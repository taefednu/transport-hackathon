"""Ассистент: вопрос словами → вызовы инструментов → ответ по посчитанному.

Модель здесь не источник знания, а две узкие роли: выбрать инструмент и
пересказать то, что вернул движок. Числа она не производит — готовый текст
проверяется тем же способом, что и абзац служебной записки (`explain.py`):
из ответа вытаскиваются все числа и сверяются с числами результатов вызовов.
Появилось лишнее — текст модели выбрасывается целиком и отдаётся собранный
кодом.

Цикл ограничен с двух сторон: числом шагов и общим бюджетом времени. Ответ
приходит в любом случае — кончился бюджет, отвалилась сеть, не нашёлся
инструмент — меняется только путь и пометка `source`.

Модель необязательна. Без неё намерение определяется по ключевым словам,
вызываются те же инструменты, и ответ собирается шаблонами.
"""

from __future__ import annotations

import json
import re
import time

import polars as pl

from app import config, explain as explain_mod, llm, nlparse, toolspecs, tools
from app.store import Store

# тот же формат чисел, что в служебной записке: «3 043 073», «9.5»
fmt = explain_mod._fmt

# сколько последних результатов показывать модели при выборе следующего шага
MAX_DONE_IN_PROMPT = 2
# час по умолчанию — утренний пик, тот же, что у /api/baseline
DEFAULT_HOUR = 8
# чем должен заканчиваться законченный ответ
SENTENCE_END = (".", "!", "?", "»", ":")
# номер пункта в начале строки — это разметка списка, а не число из расчёта.
# Без этого «1.», «2.» в перечислении читаются охраной как выдуманные числа и
# отправляют в шаблон совершенно правильный ответ
LIST_MARKER = re.compile(r"^[ \t]*\d{1,2}[.)](?=\s)", re.MULTILINE)

# --- определение намерения без модели -----------------------------------

INTENT_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (
        re.compile(
            r"требу\w* внимани|проблемн|плох\w* маршрут|худш|с чего начать|"
            r"что не так|какие маршруты|список маршрутов"
        ),
        "routes_attention",
    ),
    (
        re.compile(r"что (?:можно )?сделать|что делать|улучш|предлож|вариант|оптимиз|исправ"),
        "route_options",
    ),
    (
        re.compile(r"дыр|без транспорта|не охвач|не покрыт|где нет|далеко до остановки|вне доступ"),
        "coverage_holes",
    ),
    (
        re.compile(r"покрыти|сколько людей|доступност|pnt|пнт|метрик"),
        "coverage_summary",
    ),
    # вопрос о полноте данных — это вопрос к системе, а не мимо неё. Стоит выше
    # профиля маршрута: «цельные маршруты» иначе поймается словом «маршрут»
    (
        re.compile(
            r"цельн|целостн|полные данные|данные полн|полнота|достоверн|"
            r"можно ли доверять|насколько точн|чего не хватает|что известно|"
            r"качество данных|откуда данные|источник данных"
        ),
        "data_summary",
    ),
    (
        re.compile(r"расскажи|покажи маршрут|что с маршрутом|про маршрут|интервал|время хода"),
        "route_profile",
    ),
    (re.compile(r"где остановка|найди|поищи|есть ли остановка|есть ли маршрут"), "find"),
)

# слова вопроса, которые не являются названием: «где остановка Себзар» ищется
# по «Себзар», а не по всей фразе — поиск сравнивает нормализованные названия
FIND_NOISE = re.compile(
    r"\b(где|найди|поищи|покажи|есть|ли|такое|такой|в|на|у|остановка|остановку|"
    r"остановки|остановке|маршрут|маршрута|маршруте|городе|города)\b",
    re.IGNORECASE,
)


def find_query(question: str) -> str:
    """Что именно искать: вопрос без вопросительных слов."""
    return " ".join(FIND_NOISE.sub(" ", question).split()).strip(" ?.!,")


def out_of_scope_topic(question: str) -> dict | None:
    lowered = question.lower()
    for topic in toolspecs.OUT_OF_SCOPE:
        if any(pattern in lowered for pattern in topic["patterns"]):
            return topic
    return None


def hints(store: Store, index: list, question: str) -> dict:
    """Что удалось опознать в вопросе по базе до всякой модели.

    Номер маршрута резолвится тем же разбором, что и фразы сценариев: модель не
    должна угадывать «двадцать девятый» и не должна выдумывать номера, которых
    нет в реестре.
    """
    intent = nlparse.intent_from_keywords(question)
    found: dict = {"op": intent["op"]}
    # разбор по ключевым словам возвращает час и день всегда, даже когда в
    # вопросе их нет. Молча подставить его умолчание поверх того, что пришло
    # в запросе, — значит посчитать не тот час, о котором спросили
    if intent["hour"] != DEFAULT_HOUR:
        found["hour"] = intent["hour"]
    if intent["weekday"] != config.WEEKDAY_TYPES[0]:
        found["weekday"] = intent["weekday"]
    if intent["route"]:
        route_num, candidates = nlparse.resolve_route(store, index, intent["route"])
        if route_num:
            found["route_num"] = route_num
        elif candidates:
            found["route_candidates"] = [c["id"] for c in candidates]
        else:
            found["route_not_found"] = intent["route"]
    if intent["stops"]:
        found["stops_mentioned"] = intent["stops"]
    return found


def plan_without_model(question: str, found: dict) -> tuple[str | None, dict]:
    """Инструмент и параметры по ключевым словам. None — не поняли вопрос."""
    lowered = question.lower()
    params: dict = {"text": question}
    if found.get("route_num"):
        params["route_num"] = found["route_num"]

    # явный глагол операции («продлить», «обрезать») — это сценарий, а не вопрос.
    # Но «продлить маршрут 8» без названия остановки — это не сценарий, а просьба
    # предложить, куда продлить: собирать из этого пустой разбор и отвечать
    # «не хватает опознанных объектов» бессмысленно
    if found.get("op") != "unknown":
        if (
            found["op"] == "extend_route"
            and not found.get("stops_mentioned")
            and params.get("route_num")
        ):
            return "route_options", params
        return "scenario_effect", params

    for pattern, tool in INTENT_PATTERNS:
        if pattern.search(lowered):
            if tool == "find":
                return "find", {"text": find_query(question)}
            if tool in ("route_options", "route_profile") and not params.get("route_num"):
                # «что можно сделать» без маршрута — это вопрос про сеть целиком
                return ("routes_attention" if tool == "route_options" else None), params
            return tool, params

    if params.get("route_num"):
        return "route_profile", params
    if found.get("stops_mentioned"):
        return "find", {"text": " ".join(found["stops_mentioned"])}
    return None, params


# --- вызов инструментов -------------------------------------------------


def _normalize(params: dict, weekday: str, hour: int, question: str) -> dict:
    """Рамка расчёта проставляется здесь и только здесь.

    День и час приходят из запроса и из слов вопроса, а не от модели: рамку,
    выбранную моделью, охрана чисел не поймает — числа будут настоящие, просто
    посчитанные не про тот час, о котором спросили.
    """
    out = dict(params)
    out["weekday"], out["hour"] = weekday, hour
    if not str(out.get("text") or "").strip():
        out["text"] = question
    if not str(out.get("route_num") or "").strip():
        out.pop("route_num", None)
    if out.get("direction") not in ("fwd", "bwd"):
        out.pop("direction", None)
    return out


def run_tool(store: Store, index: list, name: str, params: dict) -> dict:
    started = time.perf_counter()
    step = {"tool": name, "params": params}
    try:
        step["result"] = tools.REGISTRY[name](store, index, params)
    except tools.ToolError as exc:
        step["error"] = str(exc)
    except KeyError:
        step["error"] = f"инструмента {name} не существует"
    step["took_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return step


def choose_tool(question: str, found: dict, done: list[dict]) -> tuple[dict | None, str | None]:
    """Выбор модели. None — модель не ответила или ответила непригодным."""
    answer = llm.complete(
        toolspecs.TOOL_SYSTEM.format(tools=toolspecs.tools_text()),
        toolspecs.tool_user(question, found, done),
        temperature=config.LLM_TEMPERATURE_PARSE,
        max_tokens=config.LLM_MAX_TOKENS_TOOL,
        json_schema=toolspecs.TOOL_SCHEMA,
    )
    if answer.text is None:
        return None, answer.error
    raw = answer.text.strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None, "в ответе модели нет JSON-объекта"
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        return None, f"JSON модели не разбирается: {exc}"
    if not isinstance(data, dict) or data.get("tool") not in (
        *toolspecs.TOOL_NAMES,
        "answer",
        "out_of_scope",
    ):
        return None, "модель выбрала инструмент, которого нет"
    return data, None


# --- шаблонные ответы ---------------------------------------------------


def _render_attention(result: dict) -> str:
    if not result["routes"]:
        return "Маршрутов с признаками, требующими внимания, не нашлось."
    lines = [
        f"Требуют внимания {fmt(result['routes_shown'])} маршрутов из "
        f"{fmt(result['routes_total'])} (всего с признаками — {fmt(result['routes_with_signs'])}):"
    ]
    for route in result["routes"]:
        lines.append(f"— маршрут {route['route_num']}: " + "; ".join(route["reasons"]) + ".")
    return "\n".join(lines)


def _render_profile(result: dict) -> str:
    parts = [
        f"Маршрут {result['route_num']} ({result['direction']}): {fmt(result['n_stops'])} остановок, "
        f"{fmt(result['length_km'])} км."
    ]
    planned, actual = result["planned_headway_min"], result["actual_headway_min_at_hour"]
    if planned and actual:
        parts.append(
            f"В {result['hour']}:00 фактический интервал {fmt(actual)} мин при плановом "
            f"{fmt(planned)} мин, на линии машин: {fmt(result['vehicles_on_line_at_hour'])}."
        )
    elif planned:
        parts.append(f"Плановый интервал {fmt(planned)} мин, фактический за этот час не посчитан.")

    hours = {row["hour"]: row["travel_min"] for row in result["travel_min_by_hour"]}
    if 6 in hours and 8 in hours:
        parts.append(
            f"Время хода в один конец: {fmt(hours[6])} мин в 6:00 против {fmt(hours[8])} мин в 8:00."
        )
    if result["segments_at_city_speed_percent"] is not None:
        parts.append(
            f"Скорость по медиане города взята на {fmt(result['segments_at_city_speed_percent'])}% "
            "перегонов маршрута."
        )
    if result["warnings"]:
        parts.append("Предупреждения: " + "; ".join(result["warnings"]) + ".")
    return " ".join(parts)


def _render_options(result: dict) -> str:
    if not result["options"]:
        # заключение уже собрано движком: «вариантов нет» без причины читается
        # как поломка подбора, а причина измерима и у каждого маршрута своя
        verdict = result["verdict"]
        return verdict[:1].upper() + verdict[1:] + "."
    lines = [f"Что можно сделать с маршрутом {result['route_num']}:"]
    for option in result["options"]:
        cost = (
            "укладывается в текущий выпуск"
            if option["extra_vehicles"] == 0
            else f"нужна ещё машин: {fmt(option['extra_vehicles'])}"
        )
        # уровень уверенности говорится вслух: у остановки OSM счётчика
        # маршрутов Яндекса нет, и её может кто-то уже обслуживать
        caveat = (
            " Остановка известна только по OSM, счётчика маршрутов по ней нет —"
            " возможно, её уже кто-то обслуживает."
            if option.get("confidence") == "osm_only"
            else ""
        )
        lines.append(
            f"— продлить {option['direction']} до «{option['stop_name']}» "
            f"(хвост {fmt(option['tail_km'])} км): доступ получают "
            f"{fmt(option['gained_people'])} человек, оборот "
            f"{fmt(option['cycle_time_before_min'])} → {fmt(option['cycle_time_after_min'])} мин, "
            f"машин {fmt(option['required_vehicles_before'])} → "
            f"{fmt(option['required_vehicles_after'])}, {cost}.{caveat}"
        )
    return "\n".join(lines)


def _render_holes(result: dict) -> str:
    lines = [
        f"Вне пешей доступности живут {fmt(result['people_total'])} человек — "
        f"{fmt(result['holes_total'])} мест, где до обслуживаемой остановки дальше "
        f"{fmt(result['walk_limit_m'])} м:"
    ]
    for hole in result["holes"]:
        distance = (
            "пешеходная сеть до места не доходит"
            if hole["walk_distance_m"] is None
            else f"{fmt(hole['walk_distance_m'])} м до «{hole['nearest_served_stop']}»"
        )
        lines.append(
            f"— {fmt(hole['people'])} человек, {hole['lat']}, {hole['lon']}: {distance}."
        )
    return "\n".join(lines)


def _render_coverage(result: dict) -> str:
    parts = [
        f"В {result['hour']}:00 из {fmt(result['population_total'])} жителей доступ к "
        f"обслуживаемой остановке в пределах {fmt(result['walk_limit_m'])} м пешком имеют "
        f"{fmt(result['pnt500_people'])} человек ({fmt(result['pnt500_percent'])}%), "
        f"вне доступа {fmt(result['people_outside'])}."
    ]
    if result["pnft15_people"] is not None:
        parts.append(
            f"Рядом с маршрутом, который ходит чаще {fmt(result['frequent_headway_min'])} мин, "
            f"живут {fmt(result['pnft15_people'])} человек ({fmt(result['pnft15_percent'])}%)."
        )
    if result["t_median_min"] is not None:
        parts.append(f"Медианное время до остановки {fmt(result['t_median_min'])} мин.")
    parts.append(
        f"Обслуживаемых остановок {fmt(result['served_stops'])} из "
        f"{fmt(result['physical_stops'])} физических."
    )
    return " ".join(parts)


def _render_scenario(result: dict) -> str:
    if result.get("result") is None:
        reasons = [item.get("reason", "") for item in result.get("unresolved") or []]
        tail = " ".join(r for r in reasons if r)
        return f"{result['understood']} {tail}".strip()
    return f"{result['understood']} {result['paragraph']}"


def _render_find(result: dict) -> str:
    if not result["routes"] and not result["stops"]:
        return f"По запросу «{result['query']}» в базе ничего не нашлось."
    parts = []
    if result["routes"]:
        parts.append("Маршруты: " + ", ".join(r["title"] for r in result["routes"]) + ".")
    if result["stops"]:
        parts.append("Остановки: " + ", ".join(s["name"] for s in result["stops"]) + ".")
    return " ".join(parts)


def _render_data_summary(result: dict) -> str:
    parts = [
        f"В базе {fmt(result['route_numbers'])} маршрутов, "
        f"{fmt(result['directions'])} направлений. Точный порядок остановок "
        f"восстановлен у {fmt(result['directions_with_restored_stop_order'])} направлений — "
        f"только по ним считаются расписание, время хода и сценарии; "
        f"у {fmt(result['directions_without_stop_order'])} порядка нет. "
        f"Трасса есть у {fmt(result['directions_with_trace'])}."
    ]
    if result["routes_with_defective_source_data"]:
        parts.append(
            f"Маршрутов с дефектными исходными данными: "
            f"{fmt(result['routes_with_defective_source_data'])} "
            f"({', '.join(result['routes_with_defective_source_data_numbers'])}) — они открываются, "
            f"но изъяты из ранжирования и подбора."
        )
    parts.append(
        f"Остановок {fmt(result['stops_total'])}, из них обслуживаемых "
        f"{fmt(result['stops_served'])}."
    )
    if result["segments_by_real_traffic_percent"] is not None:
        parts.append(
            f"По реальному трафику посчитано "
            f"{fmt(result['segments_by_real_traffic_percent'])}% перегонов, "
            f"остальное — по медиане скорости города."
        )
    parts.append("Чего нет: " + "; ".join(result["not_available"]) + ".")
    return " ".join(parts)


RENDERERS = {
    "routes_attention": _render_attention,
    "data_summary": _render_data_summary,
    "route_profile": _render_profile,
    "route_options": _render_options,
    "coverage_holes": _render_holes,
    "coverage_summary": _render_coverage,
    "scenario_from_text": _render_scenario,
    "scenario_effect": _render_scenario,
    "find": _render_find,
}


# что выбрасывается из результата перед отправкой в модель. Это не сокрытие
# данных: клиенту уходит полный результат, а модели — тот же результат без
# полей, которые она всё равно не пересказывает. Признаки диагностики уже
# сформулированы словами в `reasons`, гексагоны — материал для карты, а не для
# текста. Длинный промпт модель не успевает пересказать за таймаут
PROMPT_DROP = {
    # список исключённых маршрутов модели не показывается: получив его вместе
    # с ранжированием, она выдала исключённые маршруты за ответ на вопрос
    # «какие маршруты требуют внимания». Он добавляется к тексту кодом
    "routes_attention": ("signs", "excluded_unreliable"),
    "scenario_effect": ("changed_hexes",),
}


def for_prompt(evidence: dict) -> dict:
    out = {}
    for tool, result in evidence.items():
        dropped = PROMPT_DROP.get(tool)
        if not dropped or not isinstance(result, dict):
            out[tool] = result
            continue
        compact = {k: v for k, v in result.items() if k not in dropped}
        if tool == "routes_attention":
            compact["routes"] = [
                {k: v for k, v in route.items() if k not in dropped}
                for route in result.get("routes", [])
            ]
        else:
            inner = result.get("result")
            if isinstance(inner, dict):
                compact["result"] = {k: v for k, v in inner.items() if k not in dropped}
        out[tool] = compact
    return out


def render(steps: list[dict]) -> str:
    """Ответ, собранный кодом. Работает и когда модели нет, и когда она соврала."""
    parts = []
    for step in steps:
        if step.get("error"):
            # без имени инструмента: текст ошибки уже написан для человека
            # («маршрут 93 исключён из подбора, потому что…»), а приставка
            # `route_options:` превращает ответ в строку лога
            parts.append(f"{step['error'].rstrip('.')}.")
        elif step.get("result") is not None:
            parts.append(RENDERERS[step["tool"]](step["result"]))
    return "\n\n".join(parts) if parts else "Нечего показать: ни один расчёт не выполнился."


def refusal(topic: dict | None, head: str | None = None) -> str:
    """Отказ: почему именно не отвечаем и что вместо этого доступно.

    Список умений обязателен в любом отказе. Человек, которому ответили
    «не хватает опознанных объектов», решает, что продукт сломан, — а это
    граница расчёта, и её надо назвать вслух.
    """
    if head is None:
        head = (
            f"Не считаю: {topic['topic']} — {topic['reason']}."
            if topic
            else "Не понял вопрос и не нашёл расчёта, которым на него отвечают."
        )
    return head + " Что я умею:\n" + "\n".join(f"— {item}." for item in toolspecs.CAPABILITIES)


# --- действия для интерфейса --------------------------------------------


def actions_for(step: dict) -> list[dict]:
    """Структурные действия: интерфейс применяет их сам, ничего не досбирая."""
    name, result = step["tool"], step.get("result")
    if result is None:
        return []
    out: list[dict] = []

    if name == "routes_attention" and result["routes"]:
        out.append({"type": "select_route", "route_num": result["routes"][0]["route_num"]})
    elif name == "route_profile":
        out.append(
            {
                "type": "select_route",
                "route_num": result["route_num"],
                "direction": result["direction"],
            }
        )
    elif name == "route_options":
        out.append({"type": "select_route", "route_num": result["route_num"]})
        for option in result["options"]:
            out.append(
                {
                    "type": "apply_scenario",
                    "label": f"продлить {option['route_num']} до «{option['stop_name']}»",
                    "scenario": option["scenario"],
                }
            )
            out.append({"type": "focus_map", "lat": option["lat"], "lon": option["lon"]})
    elif name == "coverage_holes" and result["holes"]:
        out.append(
            {"type": "highlight_holes", "h3": [hole["h3"] for hole in result["holes"]]}
        )
        out.append(
            {"type": "focus_map", "lat": result["holes"][0]["lat"], "lon": result["holes"][0]["lon"]}
        )
    elif name in ("scenario_from_text", "scenario_effect") and result.get("scenario"):
        out.append(
            {
                "type": "apply_scenario",
                "label": result["understood"],
                "scenario": result["scenario"],
            }
        )
        changed = (result.get("result") or {}).get("changed_hexes") or []
        if changed:
            out.append(
                {"type": "highlight_holes", "h3": [cell["h3"] for cell in changed]}
            )
    elif name == "find":
        if result["stops"]:
            stop = result["stops"][0]
            out.append({"type": "focus_map", "lat": stop["lat"], "lon": stop["lon"]})
        if result["routes"]:
            out.append({"type": "select_route", "route_num": result["routes"][0]["route_num"]})
    return out


# --- оговорки -----------------------------------------------------------

_EXACT_SHARE: tuple[int, int] | None = None


def _exact_directions(store: Store) -> tuple[int, int]:
    """Сколько направлений с точным порядком остановок против всех."""
    global _EXACT_SHARE
    if _EXACT_SHARE is None and store.routes is not None:
        exact = store.routes.filter(pl.col("quality") == "exact").height
        _EXACT_SHARE = (exact, store.routes.height)
    return _EXACT_SHARE or (0, 0)


def exclusion_note(steps: list[dict]) -> str:
    """Строка про исключённые маршруты. Собирается кодом, а не моделью.

    Модели этот список не показывается (см. PROMPT_DROP), поэтому она не может
    ни выдать его за ответ, ни потерять: он приписывается к готовому тексту.
    """
    for step in steps:
        result = step.get("result") or {}
        if step["tool"] == "routes_attention" and result.get("excluded_count"):
            numbers = ", ".join(item["route_num"] for item in result["excluded_unreliable"])
            return (
                f"\n\nИз ранжирования исключены {fmt(result['excluded_count'])} маршрутов "
                f"с невозможными исходными значениями: {numbers}. Они открываются, но "
                "их числам доверять нельзя."
            )
    return ""


def disclaimers(store: Store) -> list[str]:
    """Оговорки о качестве данных. Собираются кодом и в охрану чисел не входят."""
    out = []
    share = explain_mod.fallback_share(store)
    if share is not None:
        out.append(
            f"{fmt(round(share * 100, 1))}% перегонов посчитано по медиане скорости города, "
            "а не по данным трафика"
        )
    exact, total = _exact_directions(store)
    if total:
        out.append(
            f"порядок остановок восстановлен точно у {fmt(exact)} направлений из {fmt(total)}"
        )
    out.append(f"слой населения — срез {config.ACTIVE_POPULATION_DATE}")
    return out


# --- главный цикл -------------------------------------------------------


def ask(
    store: Store,
    index: list,
    question: str,
    weekday: str | None = None,
    hour: int | None = None,
) -> dict:
    started = time.perf_counter()
    weekday = weekday if weekday in config.WEEKDAY_TYPES else config.WEEKDAY_TYPES[0]
    hour = hour if isinstance(hour, int) and 0 <= hour <= 23 else 8

    def left() -> float:
        return config.ASSISTANT_BUDGET_SEC - (time.perf_counter() - started)

    def done(payload: dict) -> dict:
        return {
            **payload,
            "question": question,
            "weekday": weekday,
            "hour": hour,
            "numbers_checked": True,
            "took_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    topic = out_of_scope_topic(question)
    if topic is not None:
        return done(
            {
                "text": refusal(topic),
                "source": "deterministic",
                "reason": f"вопрос вне возможностей системы: {topic['topic']}",
                "supported": False,
                "capabilities": list(toolspecs.CAPABILITIES),
                "steps": [],
                "evidence": {},
                "actions": [],
                "disclaimers": [],
            }
        )

    found = hints(store, index, question)
    weekday = found.get("weekday", weekday)
    hour = found.get("hour", hour)

    if found.get("route_not_found"):
        return done(
            {
                "text": refusal(
                    None, f"Маршрута {found['route_not_found']} нет в базе."
                ),
                "source": "deterministic",
                "reason": "названный маршрут не найден в реестре",
                "supported": False,
                "capabilities": list(toolspecs.CAPABILITIES),
                "steps": [],
                # номер, которого нет, — это результат резолва по базе, а не
                # выдуманное число: он лежит в доказательствах, как и всё
                # остальное, что ассистент называет
                "evidence": {"route_lookup": {"route_not_found": found["route_not_found"]}},
                "actions": [],
                "disclaimers": [],
            }
        )

    steps: list[dict] = []
    path, reason = "keywords", None
    refused_by_model = False

    # Инструмент выбирается ключевыми словами, а модель зовётся только тогда,
    # когда они не сработали. Замер 08.08: обращение к модели за выбором стоит
    # 780–1060 мс, и на понятной формулировке оно ничего не добавляет — тот же
    # инструмент выбирается по словам за доли миллисекунды. Сэкономленная
    # секунда уходит в бюджет пересказа, ради которого модель и нужна.
    tool_name, params = plan_without_model(question, found)
    if tool_name is not None:
        steps.append(
            run_tool(store, index, tool_name, _normalize(params, weekday, hour, question))
        )

    if not steps and llm.available():
        path = "model"
        while len(steps) < config.ASSISTANT_MAX_STEPS and left() > config.LLM_TIMEOUT_SEC:
            choice, error = choose_tool(
                question,
                found,
                [
                    {
                        "tool": s["tool"],
                        "result": for_prompt({s["tool"]: s.get("result")}).get(s["tool"]),
                        "error": s.get("error"),
                    }
                    for s in steps[-MAX_DONE_IN_PROMPT:]
                ],
            )
            if choice is None:
                reason = error
                break
            if choice["tool"] == "out_of_scope":
                refused_by_model = True
                break
            if choice["tool"] == "answer":
                break
            params = _normalize(choice, weekday, hour, question)
            # тот же инструмент второй раз — не новый шаг. С другими параметрами
            # он даёт второй набор чисел про то же самое (например второе
            # направление маршрута), и в пересказе они смешиваются с первым
            if any(s["tool"] == choice["tool"] for s in steps):
                break
            steps.append(run_tool(store, index, choice["tool"], params))
        if not steps and not refused_by_model:
            reason = reason or "модель не выбрала инструмент"

    if refused_by_model:
        return done(
            {
                "text": refusal(None),
                "source": "model",
                "reason": "модель отнесла вопрос к тому, что система не считает",
                "supported": False,
                "capabilities": list(toolspecs.CAPABILITIES),
                "steps": [],
                "evidence": {},
                "actions": [],
                "disclaimers": [],
            }
        )

    if not steps:
        return done(
                {
                    "text": refusal(None),
                    "source": "deterministic",
                    "reason": reason or "намерение не опознано",
                    "supported": False,
                    "capabilities": list(toolspecs.CAPABILITIES),
                    "steps": [],
                    "evidence": {},
                    "actions": [],
                    "disclaimers": [],
                }
        )

    evidence = {
        step["tool"]: step.get("result", {"error": step.get("error")}) for step in steps
    }
    notes = disclaimers(store)
    text = render(steps)
    source = "deterministic"

    # оговорки собраны кодом из тех же артефактов и уходят в ответ отдельным
    # полем, поэтому их числа — такие же посчитанные, как числа инструментов
    allowed = explain_mod.allowed_numbers({**evidence, "оговорки": notes})

    # Пересказывается и отказ инструмента: «маршрут 93 исключён из подбора,
    # потому что между остановками 11.1 км» — это ответ по существу, а не
    # техническая ошибка, и человеку он нужен фразой, а не строкой лога.
    # Числа отказа лежат в тех же доказательствах, охрана чисел их видит.
    has_something = any(
        step.get("result") is not None or step.get("error") for step in steps
    )
    if llm.available() and left() > config.LLM_TIMEOUT_SEC and has_something:
        answer = llm.complete(
            toolspecs.ANSWER_SYSTEM.format(limit=config.ANSWER_MAX_CHARS),
            toolspecs.answer_user(question, for_prompt(evidence)),
            temperature=config.LLM_TEMPERATURE_EXPLAIN,
            max_tokens=config.LLM_MAX_TOKENS_ANSWER,
        )
        if answer.text is None:
            reason = answer.error
        else:
            extra = sorted(
                explain_mod.numbers_in(LIST_MARKER.sub("", answer.text)) - allowed
            )
            if extra:
                reason = f"модель назвала числа, которых нет в результатах расчёта: {extra}"
            elif not answer.text.rstrip().endswith(SENTENCE_END):
                # потолок токенов стоит низко ради скорости, и ответ может
                # оборваться на середине фразы. Оборванный текст человеку не
                # отдаём — отдаём собранный кодом
                reason = "ответ модели оборвался на середине фразы"
            else:
                text, source, reason = answer.text, answer.source, None
    elif llm.available():
        # Причина называется настоящая. Раньше здесь всегда стоял бюджет, и на
        # отказ инструмента человек получал «не осталось бюджета времени», хотя
        # времени было сколько угодно — просто пересказывать было нечего.
        reason = reason or (
            "не осталось бюджета времени на пересказ моделью"
            if has_something
            else "инструмент ничего не вернул — пересказывать нечего"
        )

    actions = [action for step in steps for action in actions_for(step)]
    return done(
        {
            "text": text + exclusion_note(steps) + "\n\nОговорки: " + "; ".join(notes) + ".",
            "source": source,
            "reason": reason,
            "supported": True,
            "steps": [
                {
                    "tool": s["tool"],
                    "params": {k: v for k, v in s["params"].items() if v not in (None, "")},
                    "took_ms": s["took_ms"],
                    "error": s.get("error"),
                }
                for s in steps
            ],
            "evidence": evidence,
            "actions": actions,
            "disclaimers": notes,
            "path": path,
        }
    )
