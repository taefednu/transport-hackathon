"""Фраза планировщика → объект сценария для POST /api/scenario.

Разделение обязанностей здесь принципиальное. Модель извлекает **намерение и
сырые строки** ровно так, как их написал человек. Превращение строк в
`route_num` и `stop_id` делает поиск с транслитерацией (`search.py`), который
работает по настоящей базе. Модель базы не знает, поэтому не может выдумать
остановку, которой нет: всё, чего нет в базе, возвращается как нерезолвленное,
и сценарий не собирается.

Модель здесь необязательна. Если её нет, тот же поток проходит разбор по
ключевым словам и номерам; ответ помечается `source="deterministic"`.
"""

from __future__ import annotations

import json
import re
import time

import polars as pl

from app import config, llm, prompts, search as search_mod
from app.store import Store

# сколько кандидатов показывать при неоднозначности: человеку нужен выбор,
# а не весь список совпадений
MAX_CANDIDATES = 5
# сколько слов подряд считать названием остановки после предлога
MAX_NAME_WORDS = 4

OPS = ("extend_route", "trim_route", "insert_stop", "remove_stop", "set_schedule")

# --- словари детерминированного разбора ---------------------------------

OP_PATTERNS = (
    (re.compile(r"продл|дотян|довед|доведи|удлин"), "extend_route"),
    (re.compile(r"обрез|укорот|сократ"), "trim_route"),
    (re.compile(r"вставь|вставит|встав"), "insert_stop"),
    (re.compile(r"убра|убер|удал|снят|снять|исключ"), "remove_stop"),
    (re.compile(r"интервал|выезд|машин|расписан|отправлен"), "set_schedule"),
    (re.compile(r"добав"), "insert_stop"),
)

# номер маршрута словом. Сравнение по началу слова, потому что человек пишет
# «восьмёрке», «четырнадцатый», «на девятке» — падежей у нас нет, есть основы.
# Порядок проверки — от длинной основы к короткой, иначе «пятнадцатый» станет пятым.
ROUTE_STEMS = {
    "перв": "1", "втор": "2", "двойк": "2", "трет": "3", "тройк": "3",
    "четверт": "4", "четвёрт": "4", "четверк": "4", "четвёрк": "4",
    "пятерк": "5", "пятёрк": "5", "шестерк": "6", "шестёрк": "6",
    "семерк": "7", "семёрк": "7", "восьмерк": "8", "восьмёрк": "8",
    "девятк": "9", "десятк": "10",
    "одиннадцат": "11", "двенадцат": "12", "тринадцат": "13", "четырнадцат": "14",
    "пятнадцат": "15", "шестнадцат": "16", "семнадцат": "17", "восемнадцат": "18",
    "девятнадцат": "19", "двадцат": "20",
    "пят": "5", "шест": "6", "седьм": "7", "восьм": "8", "девят": "9", "десят": "10",
}
ROUTE_STEMS_BY_LENGTH = sorted(ROUTE_STEMS, key=len, reverse=True)


def route_from_word(word: str) -> str | None:
    key = word.lower()
    for stem in ROUTE_STEMS_BY_LENGTH:
        if key.startswith(stem):
            return ROUTE_STEMS[stem]
    return None

HOUR_WORDS = {
    "утром": 8, "утренний": 8, "утро": 8, "пик": 8, "днем": 13, "днём": 13,
    "день": 13, "вечером": 18, "вечерний": 18, "вечер": 18, "ночью": 23,
    "час": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
    "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
    "двенадцать": 12,
}

WEEKDAY_PATTERNS = (
    (re.compile(r"воскрес"), "sun"),
    (re.compile(r"выходн|суббот"), "sat"),
    (re.compile(r"будн|пятниц|рабоч"), "fri"),
)

STOP_MARKERS = ("остановку", "остановке", "остановки", "до", "к", "через")
ANCHOR_MARKERS = ("после",)
# слова, на которых название остановки заведомо кончилось
NAME_STOPWORDS = {
    "и", "а", "но", "чтобы", "что", "будет", "посмотреть", "посмотрим", "в", "во",
    "на", "при", "с", "со", "для", "от", "по", "утром", "вечером", "днем", "днём",
    "ночью", "утренний", "вечерний", "пик", "пике", "маршрут", "маршрута", "маршруте",
    "остановку", "остановки", "остановке", "выходной", "будни", "субботу",
    "воскресенье", "пятницу", "интервал", "машин", "минут", "если", "потом",
}

WORD_RE = re.compile(r"[\w'‘’ʻʼ-]+", re.UNICODE)


# --- сырое намерение ----------------------------------------------------


def _empty_intent() -> dict:
    return {
        "op": "unknown",
        "route": "",
        "stops": [],
        "anchor_stop": "",
        "hour": 8,
        "weekday": config.WEEKDAY_TYPES[0],
        "headway_min": 0,
        "first_departure": "",
        "n_vehicles": 0,
    }


def _coerce_intent(data: dict) -> dict | None:
    """Приводит ответ модели к нашей структуре. None — ответ непригоден."""
    if not isinstance(data, dict):
        return None
    intent = _empty_intent()
    op = str(data.get("op", "")).strip()
    if op not in OPS:
        return None
    intent["op"] = op
    intent["route"] = str(data.get("route", "") or "").strip()
    stops = data.get("stops") or []
    if isinstance(stops, str):
        stops = [stops]
    intent["stops"] = [str(s).strip() for s in stops if str(s).strip()]
    intent["anchor_stop"] = str(data.get("anchor_stop", "") or "").strip()
    try:
        hour = int(data.get("hour", 8))
    except (TypeError, ValueError):
        hour = 8
    intent["hour"] = min(23, max(0, hour))
    weekday = str(data.get("weekday", "") or "").strip()
    intent["weekday"] = weekday if weekday in config.WEEKDAY_TYPES else config.WEEKDAY_TYPES[0]
    try:
        intent["headway_min"] = float(data.get("headway_min", 0) or 0)
    except (TypeError, ValueError):
        intent["headway_min"] = 0
    intent["first_departure"] = str(data.get("first_departure", "") or "").strip()
    try:
        intent["n_vehicles"] = int(data.get("n_vehicles", 0) or 0)
    except (TypeError, ValueError):
        intent["n_vehicles"] = 0
    return intent


def intent_from_model(text: str) -> tuple[dict | None, str | None, str]:
    """Намерение, ошибка и путь: `model` или `cache`.

    Путь возвращается наружу, потому что на показе разница видна: живой вызов
    идёт две секунды, попадание в кэш — миллисекунды, и в ответе должно быть
    написано, что именно произошло.
    """
    answer = llm.complete(
        prompts.PARSE_SYSTEM,
        prompts.parse_user(text),
        temperature=config.LLM_TEMPERATURE_PARSE,
        max_tokens=config.LLM_MAX_TOKENS_PARSE,
        json_schema=prompts.PARSE_SCHEMA,
    )
    if answer.text is None:
        return None, answer.error, answer.source
    raw = answer.text.strip()
    if raw.startswith("```"):  # модель иногда заворачивает JSON в блок кода
        raw = raw.strip("`")
        raw = raw[raw.find("{") :]
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None, "в ответе модели нет JSON-объекта", answer.source
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        return None, f"JSON модели не разбирается: {exc}", answer.source
    intent = _coerce_intent(data)
    if intent is None:
        return None, "структура ответа модели не подходит", answer.source
    return intent, None, answer.source


def _words(text: str) -> list[str]:
    return WORD_RE.findall(text)


def _phrase_after(words: list[str], markers: tuple[str, ...]) -> str:
    lowered = [w.lower() for w in words]
    for i, word in enumerate(lowered):
        if word in markers:
            picked = []
            for candidate, original in zip(lowered[i + 1 :], words[i + 1 :]):
                if candidate in NAME_STOPWORDS or candidate in markers:
                    break
                picked.append(original)
                if len(picked) >= MAX_NAME_WORDS:
                    break
            if picked:
                return " ".join(picked)
    return ""


def intent_from_keywords(text: str) -> dict:
    """Запасной разбор: ключевые слова и числа. Работает всегда и без сети."""
    intent = _empty_intent()
    lowered = text.lower()
    words = _words(text)

    for pattern, op in OP_PATTERNS:
        if pattern.search(lowered):
            intent["op"] = op
            break

    match = re.search(r"(?:маршрут\w*|№|номер)\s*(\d+[а-яa-z]?)", lowered)
    if match:
        intent["route"] = match.group(1)
    else:
        for word in words:
            number = route_from_word(word)
            if number:
                intent["route"] = number
                break
        else:
            # свободное число: не час, не интервал, не число машин, не часть времени
            for candidate in re.finditer(r"\b(\d+)\b(?!\s*[:.]\d)", lowered):
                tail = lowered[candidate.end() : candidate.end() + 12]
                head = lowered[max(0, candidate.start() - 4) : candidate.start()]
                if re.match(r"\s*(мин|машин|автобус|час)", tail):
                    continue
                if re.search(r"[:.]\s*$", head) or re.search(r"\bв\s*$", head):
                    continue
                intent["route"] = candidate.group(1)
                break

    anchor = _phrase_after(words, ANCHOR_MARKERS)
    if anchor:
        intent["anchor_stop"] = anchor
    name = _phrase_after(words, STOP_MARKERS)
    if name:
        intent["stops"] = [name]

    time_match = re.search(r"\b(\d{1,2}):(\d{2})\b", lowered)
    if time_match:
        intent["first_departure"] = f"{int(time_match.group(1)):02d}:{time_match.group(2)}"
        intent["hour"] = min(23, int(time_match.group(1)))
    else:
        hour_match = re.search(r"\bв\s+(\d{1,2})\b", lowered)
        if hour_match:
            intent["hour"] = min(23, int(hour_match.group(1)))
        else:
            for word in words:
                key = word.lower()
                if key in HOUR_WORDS:
                    intent["hour"] = HOUR_WORDS[key]
                    break

    for pattern, weekday in WEEKDAY_PATTERNS:
        if pattern.search(lowered):
            intent["weekday"] = weekday
            break

    headway = re.search(r"интервал\D{0,12}(\d+(?:[.,]\d+)?)", lowered) or re.search(
        r"(\d+(?:[.,]\d+)?)\s*минут", lowered
    )
    if headway:
        intent["headway_min"] = float(headway.group(1).replace(",", "."))
    vehicles = re.search(r"(\d+)\s*(?:машин|автобус)", lowered)
    if vehicles:
        intent["n_vehicles"] = int(vehicles.group(1))

    return intent


# --- резолв сырых строк по базе -----------------------------------------


def _variants(query: str) -> list[str]:
    """Название в косвенном падеже («до Куйлюка») — та же остановка.

    Морфологии не тянем: срезаем одну и две последние буквы. Этого хватает
    русским окончаниям и не ломает нормализацию, которая и так нечёткая.
    """
    query = query.strip()
    out = [query]
    if len(query) > 4:
        out.append(query[:-1])
    if len(query) > 5:
        out.append(query[:-2])
    return out


def _resolve(index: list, query: str, kind: str) -> tuple[object | None, list[dict]]:
    """Возвращает (единственная запись, кандидаты). Обе пустые — не найдено."""
    ranked: list = []
    for attempt in _variants(query):
        ranked = search_mod.rank_entries(index, attempt, kind)
        if ranked:
            break
    if not ranked:
        return None, []

    best_rank, best_score, _ = ranked[0]
    tied = [item for item in ranked if (item[0], item[1]) == (best_rank, best_score)]
    unique_ids = {entry.id for _, _, entry in tied}
    if len(unique_ids) == 1:
        return tied[0][2], []
    return None, [search_mod.pack_entry(entry, rank) for rank, _, entry in tied[:MAX_CANDIDATES]]


def resolve_route(store: Store, index: list, query: str) -> tuple[str | None, list[dict]]:
    """Номер маршрута по тому, как его назвал человек. Публичная: ей же
    пользуется ассистент, чтобы не угадывать «двадцать девятый» второй раз."""
    key = route_from_word(query.strip()) or query.strip()
    if store.routes is not None and key:
        exact = store.routes.filter(pl.col("route_num") == key)
        if not exact.is_empty():
            return key, []
    entry, candidates = _resolve(index, key, "route")
    return (entry.id if entry else None), candidates


def _sequence(store: Store, route_num: str, direction: str) -> list[str]:
    if store.route_stops is None:
        return []
    rows = store.route_stops.filter(
        (pl.col("route_num") == route_num) & (pl.col("direction") == direction)
    ).sort("seq")
    return rows["stop_id"].to_list()


# --- сборка ответа ------------------------------------------------------


def _understood(intent: dict, route_num: str | None, stops: list[dict], ops: list[dict]) -> str:
    weekday = config.WEEKDAY_NAMES.get(intent["weekday"], intent["weekday"])
    when = f"{weekday}, {intent['hour']}:00"
    if not ops:
        return "Не собрал сценарий: не хватает опознанных объектов."
    names = ", ".join(f"«{s['title']}»" for s in stops)
    route = f"маршрут {route_num}"
    op = intent["op"]
    if op == "extend_route":
        head = f"продлить {route} до {names}"
    elif op == "trim_route":
        head = f"обрезать {route} до {names}"
    elif op == "insert_stop":
        head = f"вставить остановку {names} в {route}"
    elif op == "remove_stop":
        head = f"убрать остановку {names} из маршрута {route_num}"
    else:
        parts = []
        params = ops[0]
        if params.get("first_departure"):
            parts.append(f"первый выезд в {params['first_departure']}")
        if params.get("headway_min"):
            parts.append(f"интервал {params['headway_min']:g} мин")
        if params.get("n_vehicles"):
            parts.append(f"{params['n_vehicles']} машин на линии")
        head = f"задать для маршрута {route_num} " + ", ".join(parts)
    return f"Понял: {head}. Пересчёт на {when}."


def parse(store: Store, index: list, text: str) -> dict:
    started = time.perf_counter()

    intent, error, source = None, None, "model"
    if llm.available():
        intent, error, source = intent_from_model(text)
    else:
        error = llm.status()["reason"]
    if intent is None:
        intent, source = intent_from_keywords(text), "deterministic"

    ambiguous: list[dict] = []
    unresolved: list[dict] = []
    resolved_stops: list[dict] = []
    ops: list[dict] = []
    direction = "fwd"

    route_num, route_candidates = (None, [])
    if intent["route"]:
        route_num, route_candidates = resolve_route(store, index, intent["route"])
        if route_num is None:
            target = ambiguous if route_candidates else unresolved
            item = {"query": intent["route"], "role": "route"}
            if route_candidates:
                item["candidates"] = route_candidates
            else:
                item["reason"] = "маршрута с таким номером нет в базе"
            target.append(item)
    elif intent["op"] != "unknown":
        unresolved.append(
            {"query": "", "role": "route", "reason": "в фразе не назван номер маршрута"}
        )

    for raw_name in intent["stops"]:
        entry, candidates = _resolve(index, raw_name, "stop")
        if entry is not None:
            resolved_stops.append({"id": entry.id, "title": entry.title})
        elif candidates:
            ambiguous.append({"query": raw_name, "role": "stop", "candidates": candidates})
        else:
            unresolved.append(
                {"query": raw_name, "role": "stop", "reason": "остановки с таким названием нет в базе"}
            )

    anchor_stop = None
    if intent["anchor_stop"]:
        entry, candidates = _resolve(index, intent["anchor_stop"], "stop")
        if entry is not None:
            anchor_stop = {"id": entry.id, "title": entry.title}
        elif candidates:
            ambiguous.append(
                {"query": intent["anchor_stop"], "role": "anchor_stop", "candidates": candidates}
            )
        else:
            unresolved.append(
                {
                    "query": intent["anchor_stop"],
                    "role": "anchor_stop",
                    "reason": "остановки с таким названием нет в базе",
                }
            )

    sequence = _sequence(store, route_num, direction) if route_num else []
    if route_num and intent["op"] in ("trim_route", "insert_stop", "remove_stop") and not sequence:
        unresolved.append(
            {
                "query": route_num,
                "role": "route",
                "reason": "у маршрута не восстановлен порядок остановок, операция не определена",
            }
        )

    if intent["op"] == "unknown":
        unresolved.append(
            {"query": text, "role": "op", "reason": "не понял, какую операцию выполнить"}
        )

    can_build = bool(route_num) and not ambiguous and not unresolved
    if can_build:
        op = intent["op"]
        if op == "extend_route" and resolved_stops:
            ops.append(
                {
                    "type": "extend_route",
                    "route_num": route_num,
                    "direction": direction,
                    "stops": [s["id"] for s in resolved_stops],
                }
            )
        elif op == "trim_route" and resolved_stops:
            stop_id = resolved_stops[0]["id"]
            if stop_id in sequence:
                ops.append(
                    {
                        "type": "trim_route",
                        "route_num": route_num,
                        "direction": direction,
                        "until_seq": sequence.index(stop_id),
                    }
                )
            else:
                unresolved.append(
                    {
                        "query": resolved_stops[0]["title"],
                        "role": "stop",
                        "reason": f"остановки нет в маршруте {route_num}",
                    }
                )
        elif op == "insert_stop" and resolved_stops:
            after = sequence.index(anchor_stop["id"]) if anchor_stop and anchor_stop["id"] in sequence else None
            if after is None:
                unresolved.append(
                    {
                        "query": intent["anchor_stop"],
                        "role": "anchor_stop",
                        "reason": "не понял, после какой остановки маршрута вставлять",
                    }
                )
            else:
                ops.append(
                    {
                        "type": "insert_stop",
                        "route_num": route_num,
                        "direction": direction,
                        "stop_id": resolved_stops[0]["id"],
                        "after_seq": after,
                    }
                )
        elif op == "remove_stop" and resolved_stops:
            stop_id = resolved_stops[0]["id"]
            if stop_id in sequence:
                ops.append(
                    {
                        "type": "remove_stop",
                        "route_num": route_num,
                        "direction": direction,
                        "seq": sequence.index(stop_id),
                    }
                )
            else:
                unresolved.append(
                    {
                        "query": resolved_stops[0]["title"],
                        "role": "stop",
                        "reason": f"остановки нет в маршруте {route_num}",
                    }
                )
        elif op == "set_schedule":
            params = {"type": "set_schedule", "route_num": route_num}
            if intent["first_departure"]:
                params["first_departure"] = intent["first_departure"]
            if intent["headway_min"]:
                params["headway_min"] = intent["headway_min"]
            if intent["n_vehicles"]:
                params["n_vehicles"] = intent["n_vehicles"]
            if len(params) == 2:
                unresolved.append(
                    {
                        "query": text,
                        "role": "schedule",
                        "reason": "не назван ни интервал, ни время выезда, ни число машин",
                    }
                )
            else:
                ops.append(params)
        elif op in ("extend_route", "trim_route", "insert_stop", "remove_stop"):
            unresolved.append(
                {"query": text, "role": "stop", "reason": "в фразе не названа остановка"}
            )

    scenario_body = (
        {"weekday": intent["weekday"], "hour": intent["hour"], "ops": ops} if ops else None
    )

    return {
        "text": text,
        "source": source,
        "llm": {"available": llm.available(), "error": error},
        "intent": intent,
        "understood": _understood(intent, route_num, resolved_stops, ops),
        "scenario": scenario_body,
        "ambiguous": ambiguous,
        "unresolved": unresolved,
        "took_ms": (time.perf_counter() - started) * 1000.0,
    }
