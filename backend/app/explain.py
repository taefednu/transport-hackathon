"""Результат сценария → абзац на языке служебной записки.

Модель здесь только пересказывает. Гарантия того, что она не начнёт считать
сама, встроена в код, а не в промпт: из готового текста вытаскиваются все числа
и сверяются с числами входных данных. Появилось лишнее — текст модели
выбрасывается целиком и отдаётся шаблонный, собранный из тех же значений.

Тот же шаблонный путь работает, когда модели нет вообще.
"""

from __future__ import annotations

import json
import re
import time

from app import config, llm, prompts
from app.store import Store

# сколько предупреждений пересказывать: их бывают десятки, в записку идут первые
MAX_WARNINGS = 6


# число с разделителями разрядов и десятичной частью: «2 362 141», «8.9», «3,25»
NUMBER_RE = re.compile(r"\d[\d\s  ]*(?:[.,]\d+)?")

_FALLBACK_SHARE: float | None = None


def fallback_share(store: Store) -> float | None:
    """Доля строк времени хода, посчитанных по медиане города, а не по трафику."""
    global _FALLBACK_SHARE
    if _FALLBACK_SHARE is None and store.segment_time is not None:
        _FALLBACK_SHARE = float((store.segment_time["source"] == "fallback").mean())
    return _FALLBACK_SHARE


def _people(value: float | int | None) -> int | None:
    return None if value is None else int(round(float(value)))


def _minutes(value: float | int | None) -> float | None:
    return None if value is None else round(float(value), 2)


def _fmt(value: float | int) -> str:
    if isinstance(value, int) or float(value).is_integer():
        return f"{int(value):,}".replace(",", " ")
    return f"{value:g}"


def build_facts(store: Store, body: dict) -> dict:
    """Плоский набор значений. Всё, что попадёт в текст, должно лежать здесь."""
    result = body.get("result") if isinstance(body.get("result"), dict) else body
    sources = body.get("sources") or {}

    share = sources.get("fallback_share")
    share_from = "запрос"
    if share is None:
        share = fallback_share(store)
        share_from = "данные сервера"
    population_date = sources.get("population_layer_date")
    if not population_date:
        population_date = config.ACTIVE_POPULATION_DATE

    changes: list[str] = []
    for route in result.get("affected_routes") or []:
        num = route.get("route_num")
        direction = route.get("direction")
        if route.get("n_stops_before") is not None:
            changes.append(
                f"маршрут {num} ({direction}): остановок было {route['n_stops_before']}, "
                f"стало {route['n_stops_after']}"
            )
        if route.get("headway_after") is not None:
            piece = (
                f"маршрут {num} ({direction}): интервал "
                f"{_fmt(route.get('headway_before'))} → {_fmt(route['headway_after'])} мин"
            )
            if route.get("cycle_time_after") is not None:
                piece += (
                    f", время оборота {_minutes(route.get('cycle_time_before'))} → "
                    f"{_minutes(route['cycle_time_after'])} мин"
                )
            if route.get("required_vehicles_after") is not None:
                piece += f", требуется машин {route['required_vehicles_after']}"
            changes.append(piece)

    # валидатор вешает одно и то же предупреждение на каждый перегон: в записке
    # повтор одной фразы шесть раз — шум, а не информация
    warnings = list(
        dict.fromkeys(
            w.get("message", "") for w in (result.get("warnings") or []) if w.get("message")
        )
    )
    pnft = result.get("pnft15_after") or {}

    facts = {
        "день": config.WEEKDAY_NAMES.get(result.get("weekday"), result.get("weekday")),
        "час": f"{int(result.get('hour', 0))}:00",
        "изменения": changes,
        "получили_доступ_человек": _people(result.get("gained")),
        "потеряли_доступ_человек": _people(result.get("lost")),
        "чистое_изменение_человек": _people(result.get("net")),
        "в_пешей_доступности_было_человек": _people(result.get("pnt500_before")),
        "в_пешей_доступности_стало_человек": _people(result.get("pnt500_after")),
        "время_до_остановки_было_мин": _minutes(result.get("t_median_before")),
        "время_до_остановки_стало_мин": _minutes(result.get("t_median_after")),
        "порог_пешей_доступности_м": int(config.WALK_LIMIT_M),
        "предупреждения": warnings[:MAX_WARNINGS],
        "предупреждений_всего": len(warnings),
        "оговорка": {
            "доля_перегонов_по_медиане_города_процент": (
                None if share is None else round(float(share) * 100, 1)
            ),
            "дата_слоя_населения": population_date,
        },
    }
    if pnft.get("people") is not None:
        facts["маршрут_с_интервалом_15_мин_рядом_человек"] = _people(pnft["people"])
    facts["_источник_оговорки"] = share_from
    return facts


def render(facts: dict) -> str:
    """Шаблонный абзац. Ни одного числа сверх тех, что в facts."""
    parts: list[str] = []
    changes = facts["изменения"]
    head = "; ".join(changes) if changes else "состав маршрутов не изменился"
    parts.append(f"Сценарий на {facts['день']}, {facts['час']}: {head}.")

    gained, lost = facts["получили_доступ_человек"], facts["потеряли_доступ_человек"]
    parts.append(
        f"Доступ к обслуживаемой остановке в пределах {facts['порог_пешей_доступности_м']} м "
        f"по пешеходной сети получают {_fmt(gained)} человек, теряют {_fmt(lost)}; "
        f"чистое изменение {_fmt(facts['чистое_изменение_человек'])} человек."
    )
    parts.append(
        f"В пешей доступности было {_fmt(facts['в_пешей_доступности_было_человек'])} человек, "
        f"стало {_fmt(facts['в_пешей_доступности_стало_человек'])}; медианное время до остановки "
        f"{_fmt(facts['время_до_остановки_было_мин'])} мин против "
        f"{_fmt(facts['время_до_остановки_стало_мин'])} мин."
    )
    if facts["предупреждения"]:
        listed = "; ".join(facts["предупреждения"])
        tail = (
            f" (показаны {_fmt(len(facts['предупреждения']))} из "
            f"{_fmt(facts['предупреждений_всего'])})"
            if facts["предупреждений_всего"] > len(facts["предупреждения"])
            else ""
        )
        parts.append(f"Сработали предупреждения: {listed}{tail}.")
    else:
        parts.append("Предупреждений валидатора нет.")

    share = facts["оговорка"]["доля_перегонов_по_медиане_города_процент"]
    share_text = (
        f"{_fmt(share)}% перегонов посчитано по медиане скорости города, а не по данным трафика"
        if share is not None
        else "доля перегонов, посчитанных по медиане скорости города, не передана"
    )
    parts.append(
        f"Источники: {share_text}; слой населения — срез {facts['оговорка']['дата_слоя_населения']}."
    )
    return " ".join(parts)


def numbers_in(text: str) -> set[float]:
    out: set[float] = set()
    for chunk in NUMBER_RE.findall(text):
        cleaned = re.sub(r"[\s  ]", "", chunk).replace(",", ".")
        cleaned = cleaned.rstrip(".")
        if not cleaned:
            continue
        try:
            out.add(round(float(cleaned), 2))
        except ValueError:
            continue
    return out


def allowed_numbers(facts: dict) -> set[float]:
    """Все числа входных данных — в том виде, в каком их можно написать."""
    allowed = numbers_in(json.dumps(facts, ensure_ascii=False))
    for value in list(allowed):
        allowed.add(round(value, 1))
        allowed.add(float(int(value)))
    return allowed


def missing_disclaimer(text: str, facts: dict) -> list[str]:
    """Чего не хватает в оговорке об источниках.

    Охрана чисел ловит только лишнее. Пропуск она не видит, а модель охотно
    сокращает оговорку до одной даты — проверяем обе величины отдельно.
    """
    missing: list[str] = []
    share = facts["оговорка"]["доля_перегонов_по_медиане_города_процент"]
    if share is not None and round(float(share), 2) not in numbers_in(text):
        missing.append("доля перегонов, посчитанных по медиане скорости города")
    date = facts["оговорка"]["дата_слоя_населения"]
    if date and str(date) not in text:
        missing.append("дата слоя населения")
    return missing


def explain(store: Store, body: dict) -> dict:
    started = time.perf_counter()
    facts = build_facts(store, body)
    allowed = allowed_numbers(facts)
    fallback_text = render(facts)

    source, reason = "deterministic", llm.status()["reason"]
    text = fallback_text

    if llm.available():
        answer = llm.complete(
            prompts.EXPLAIN_SYSTEM,
            prompts.explain_user(facts),
            temperature=config.LLM_TEMPERATURE_EXPLAIN,
            max_tokens=config.LLM_MAX_TOKENS_EXPLAIN,
        )
        if answer.text is None:
            reason = answer.error
        else:
            extra = sorted(numbers_in(answer.text) - allowed)
            missing = missing_disclaimer(answer.text, facts)
            if extra:
                reason = f"модель назвала числа, которых нет во входных данных: {extra}"
            elif missing:
                reason = f"в абзаце нет обязательной оговорки: {', '.join(missing)}"
            else:
                text, source, reason = answer.text, answer.source, None

    return {
        "text": text,
        "source": source,
        "reason": reason,
        "numbers_checked": True,
        "facts": facts,
        "took_ms": (time.perf_counter() - started) * 1000.0,
    }
