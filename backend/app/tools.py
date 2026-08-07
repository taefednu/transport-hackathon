"""Инструменты ассистента: то же, что считают эндпоинты, вызванное напрямую.

Каждый инструмент — обычная функция `(store, index, params) -> dict`. Считает
движок, а не модель: сюда приходят уже разобранные параметры, отсюда уходят
числа. Результат должен быть компактным — он целиком уезжает в модель и целиком
же становится списком чисел, которые модели разрешено называть.

Ничего не применяется и никуда не записывается: сервер без состояния, «сценарий»
здесь — это расчёт последствий, а решение принимает человек.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from app import (
    config,
    coverage,
    diagnostics,
    explain as explain_mod,
    nlparse,
    scenario as scenario_mod,
    schedule as schedule_mod,
    search as search_mod,
    toolspecs,
    validation,
)
from app.store import Store

# сколько изменившихся гексагонов возвращать сценарием: фронту нужен список
# для подсветки, а не весь город
MAX_CHANGED_HEXES = 40

# часы, по которым показывается маршрут: утренний пик, дневной провал, вечерний
# пик и тот час, о котором спросили. Все 24 отдавать нельзя — пересказывая
# таблицу за сутки, модель берёт из неё значение не того часа, и охрана чисел
# этого не видит: число настоящее, час другой
REFERENCE_HOURS = (6, 8, 13, 18)


class ToolError(ValueError):
    """Инструмент не может посчитать — с причиной, которую можно сказать человеку."""


def _route_exists(store: Store, route_num: str) -> bool:
    return (
        store.routes is not None
        and not store.routes.filter(pl.col("route_num") == route_num).is_empty()
    )


def _require_route(store: Store, params: dict) -> str:
    route_num = str(params.get("route_num") or "").strip()
    if not route_num:
        raise ToolError("в вопросе не назван номер маршрута")
    if not _route_exists(store, route_num):
        raise ToolError(f"маршрута {route_num} нет в базе")
    return route_num


def _directions(store: Store, route_num: str, direction: str | None) -> list[str]:
    rows = store.routes.filter(pl.col("route_num") == route_num)
    available = rows["direction"].to_list()
    if direction and direction in available:
        return [direction]
    return available


# --- инструменты --------------------------------------------------------


def routes_attention(store: Store, index: list, params: dict) -> dict:
    if store.routes is None:
        raise ToolError("нет data/build/routes.parquet (шаг 4 пайплайна)")
    limit = int(params.get("limit") or config.ASSISTANT_ROUTES_LIMIT)
    return diagnostics.attention(
        store, params["weekday"], params["hour"], max(1, min(limit, 20))
    )


def route_profile(store: Store, index: list, params: dict) -> dict:
    route_num = _require_route(store, params)
    weekday, hour = params["weekday"], params["hour"]
    direction = _directions(store, route_num, params.get("direction"))[0]

    route = store.routes.filter(
        (pl.col("route_num") == route_num) & (pl.col("direction") == direction)
    ).to_dicts()[0]
    shown_hours = sorted({*REFERENCE_HOURS, hour})

    travel_by_hour: list[dict] = []
    fallback_share = None
    if store.segment_time is not None:
        segments = store.segment_time.filter(
            (pl.col("route_num") == route_num)
            & (pl.col("direction") == direction)
            & (pl.col("weekday_type") == weekday)
        )
        if not segments.is_empty():
            grouped = segments.group_by("hour").agg(
                (pl.col("travel_sec").sum() / 60.0).alias("travel_min")
            ).sort("hour")
            travel_by_hour = [
                {"hour": int(h), "travel_min": round(float(m), 1)}
                for h, m in zip(grouped["hour"].to_list(), grouped["travel_min"].to_list())
                if int(h) in shown_hours
            ]
            per_segment = segments.group_by("seq_from").agg(pl.col("source").first())
            fallback_share = round(
                float((per_segment["source"] == "fallback").mean()) * 100, 1
            )

    actual = schedule_mod.actual_headway_by_hour(store, route_num, weekday) or []
    actual_now = next((row for row in actual if int(row["hour"]) == hour), None)
    warnings = validation.route_warnings(store, route_num, direction, weekday)

    return {
        "route_num": route_num,
        "direction": direction,
        "directions_available": _directions(store, route_num, None),
        "weekday": weekday,
        "hour": hour,
        # час строкой кладётся рядом с числом намеренно: в тексте он пишется
        # как «8:00», и ноль из этой записи обязан быть числом инструмента
        "hour_label": f"{hour}:00",
        "name": route["name"],
        "quality": route["quality"],
        "length_km": None if route["length_km"] is None else round(float(route["length_km"]), 1),
        "n_stops": route["n_stops"],
        "work_start": route.get(f"work_start_{weekday}"),
        "work_end": route.get(f"work_end_{weekday}"),
        "planned_headway_min": (
            None
            if route["planned_headway_min"] is None
            else round(float(route["planned_headway_min"]), 1)
        ),
        "actual_headway_min_at_hour": (
            None if actual_now is None else round(float(actual_now["actual_headway_min"]), 1)
        ),
        "vehicles_on_line_at_hour": None if actual_now is None else actual_now["n_vehicles"],
        "boardings_at_hour": None if actual_now is None else actual_now["n_boardings"],
        "actual_headway_by_hour": [
            {
                "hour": int(row["hour"]),
                "headway_min": round(float(row["actual_headway_min"]), 1),
                "vehicles": row["n_vehicles"],
            }
            for row in actual
            if int(row["hour"]) in shown_hours
        ],
        "hours_shown": shown_hours,
        "travel_min_by_hour": travel_by_hour,
        # доля перегонов, для которых реального трафика не нашлось
        "segments_at_city_speed_percent": fallback_share,
        "warnings": list(dict.fromkeys(w["message"] for w in warnings)),
        "attention": next(
            (
                {"score": r["score"], "reasons": diagnostics.reasons(r)}
                for r in diagnostics.compute(store, weekday, hour)
                if r["route_num"] == route_num
            ),
            None,
        ),
    }


def _unserved_candidates(store: Store) -> list[str]:
    """Остановки, про которые известно, что их никто не обслуживает.

    Критерий тот же, что в knowledge/decisions.md: счётчик маршрутов Яндекса
    равен нулю и остановки нет ни в одной цепочке. У остановок OSM счётчика
    не существует, их ноль означает «не знаем», поэтому они сюда не попадают.
    """
    if store.route_stops is None:
        return []
    in_chain = set(store.route_stops["stop_id"].to_list())
    return (
        store.stops.filter(
            (pl.col("n_routes") == 0)
            & (pl.col("source") == "yandex")
            & ~pl.col("stop_id").is_in(list(in_chain))
        )["stop_id"]
        .to_list()
    )


def route_options(store: Store, index: list, params: dict) -> dict:
    """Продления маршрута к необслуживаемым остановкам, посчитанные движком."""
    route_num = _require_route(store, params)
    weekday, hour = params["weekday"], params["hour"]

    candidates = _unserved_candidates(store)
    if not candidates:
        raise ToolError("нет остановок, про которые известно, что их никто не обслуживает")

    stop_index = {s: i for i, s in enumerate(store.stops["stop_id"].to_list())}
    names = dict(zip(store.stops["stop_id"].to_list(), store.stops["name"].to_list()))
    coords = {
        row["stop_id"]: (row["lat"], row["lon"])
        for row in store.stops.select("stop_id", "lat", "lon").iter_rows(named=True)
    }
    candidate_xy = np.array([store.stop_xy[stop_index[s]] for s in candidates])

    options, skipped = [], []
    for direction in _directions(store, route_num, params.get("direction")):
        try:
            sequence = scenario_mod._route_sequence(store, route_num, direction)
        except scenario_mod.ScenarioError as exc:
            skipped.append({"direction": direction, "reason": str(exc)})
            continue
        row = store.routes.filter(
            (pl.col("route_num") == route_num) & (pl.col("direction") == direction)
        ).to_dicts()[0]
        length_km = row["length_km"]
        terminus = sequence[-1]
        if terminus not in stop_index or not length_km:
            continue

        distances = (
            np.hypot(*(candidate_xy - store.stop_xy[stop_index[terminus]]).T) / 1000.0
        )
        for position in np.argsort(distances)[: config.IMPROVEMENT_CANDIDATES]:
            tail_km = float(distances[position])
            if tail_km > config.IMPROVEMENT_MAX_LENGTH_SHARE * float(length_km):
                continue
            stop_id = candidates[position]
            body = {
                "weekday": weekday,
                "hour": hour,
                "ops": [
                    {
                        "type": "extend_route",
                        "route_num": route_num,
                        "direction": direction,
                        "stops": [stop_id],
                    }
                ],
            }
            try:
                result = scenario_mod.run(store, weekday, hour, body["ops"])
            except scenario_mod.ScenarioError as exc:
                skipped.append({"direction": direction, "reason": str(exc)})
                continue
            affected = result["affected_routes"][0]
            before = affected.get("required_vehicles_before")
            after = affected.get("required_vehicles_after")
            if before is None or after is None or result["gained"] <= 0:
                continue
            if after - before > config.IMPROVEMENT_MAX_EXTRA_VEHICLES:
                continue
            options.append(
                {
                    "route_num": route_num,
                    "direction": direction,
                    "action": "продлить до остановки",
                    "stop_id": stop_id,
                    "stop_name": names.get(stop_id) or stop_id,
                    "lat": coords[stop_id][0],
                    "lon": coords[stop_id][1],
                    "tail_km": round(tail_km, 2),
                    "gained_people": int(round(result["gained"])),
                    "lost_people": int(round(result["lost"])),
                    "cycle_time_before_min": round(affected["cycle_time_before"], 1),
                    "cycle_time_after_min": round(affected["cycle_time_after"], 1),
                    "required_vehicles_before": before,
                    "required_vehicles_after": after,
                    "extra_vehicles": after - before,
                    "scenario": body,
                }
            )

    options.sort(key=lambda o: (-o["gained_people"], o["extra_vehicles"]))
    return {
        "route_num": route_num,
        "weekday": weekday,
        "hour": hour,
        "hour_label": f"{hour}:00",
        "options": options[: config.ASSISTANT_OPTIONS_LIMIT],
        "options_found": len(options),
        "candidates_checked": len(candidates),
        "max_extra_vehicles": config.IMPROVEMENT_MAX_EXTRA_VEHICLES,
        "skipped": skipped,
        "note": (
            "варианты — продления до остановок, которые сейчас никто не обслуживает; "
            "хвост до новой остановки считается по прямой и по медианной скорости города"
        ),
    }


def coverage_holes(store: Store, index: list, params: dict) -> dict:
    if store.holes is None:
        raise ToolError("нет data/build/holes.parquet (шаг 9 пайплайна)")
    limit = max(1, min(int(params.get("limit") or config.ASSISTANT_HOLES_LIMIT), 20))
    rows = store.holes.head(limit).to_dicts()
    return {
        "holes_total": store.holes.height,
        "people_total": int(round(float(store.holes["population"].sum()))),
        "walk_limit_m": int(config.WALK_LIMIT_M),
        "holes": [
            {
                "h3": row["h3_id"],
                "people": int(round(float(row["population"]))),
                "lat": round(float(row["lat"]), 5),
                "lon": round(float(row["lon"]), 5),
                "nearest_served_stop": row["nearest_stop_name"] or row["nearest_stop_id"],
                # None бывает: до гексагона не доходит пешеходная сеть
                "walk_distance_m": (
                    None
                    if row["walk_distance_m"] is None
                    else int(round(float(row["walk_distance_m"])))
                ),
            }
            for row in rows
        ],
    }


def coverage_summary(store: Store, index: list, params: dict) -> dict:
    """Метрики города без списка гексагонов: в ответ модели он не помещается."""
    base = coverage.baseline(store, params["weekday"], params["hour"])
    pnt, pnft = base["pnt500"], base["pnft15"]
    return {
        "weekday": base["weekday"],
        "hour": base["hour"],
        "hour_label": f"{base['hour']}:00",
        "population_total": int(round(base["population_total"])),
        "pnt500_people": int(round(pnt["people"])),
        "pnt500_percent": round(pnt["share"] * 100, 1) if pnt["share"] else None,
        "people_outside": int(round(pnt["people_outside"])),
        "pnft15_people": None if pnft is None else int(round(pnft["people"])),
        "pnft15_percent": (
            None if pnft is None or not pnft["share"] else round(pnft["share"] * 100, 1)
        ),
        "t_median_min": (
            None if base["t_median_min"] is None else round(base["t_median_min"], 2)
        ),
        "served_stops": base["served_stops"],
        "physical_stops": base["physical_stops"],
        "walk_limit_m": int(config.WALK_LIMIT_M),
        "frequent_headway_min": int(config.FREQUENT_HEADWAY_MIN),
    }


def scenario_from_text(store: Store, index: list, params: dict) -> dict:
    text = str(params.get("text") or "").strip()
    if not text:
        raise ToolError("нечего разбирать: в вопросе нет фразы про изменение маршрута")
    parsed = nlparse.parse(store, index, text)
    return {
        "understood": parsed["understood"],
        "scenario": parsed["scenario"],
        "parse_source": parsed["source"],
        "ambiguous": parsed["ambiguous"],
        "unresolved": parsed["unresolved"],
    }


def scenario_effect(store: Store, index: list, params: dict) -> dict:
    """Фраза → сценарий → последствия. Ничего не применяет."""
    parsed = scenario_from_text(store, index, params)
    body = parsed["scenario"]
    if body is None:
        return {**parsed, "result": None, "reason": "сценарий из фразы не собрался"}

    try:
        result = scenario_mod.run(store, body["weekday"], body["hour"], body["ops"])
    except scenario_mod.ScenarioError as exc:
        raise ToolError(str(exc)) from exc

    facts = explain_mod.build_facts(store, {"result": result})
    return {
        **parsed,
        "result": {
            "gained_people": int(round(result["gained"])),
            "lost_people": int(round(result["lost"])),
            "net_people": int(round(result["net"])),
            "pnt500_before": int(round(result["pnt500_before"])),
            "pnt500_after": int(round(result["pnt500_after"])),
            "t_median_before_min": round(result["t_median_before"], 2),
            "t_median_after_min": round(result["t_median_after"], 2),
            "affected_routes": result["affected_routes"],
            "warnings": list(dict.fromkeys(w["message"] for w in result["warnings"])),
            "changed_hexes": result["changed_hexes"][:MAX_CHANGED_HEXES],
            "changed_hexes_total": len(result["changed_hexes"]),
        },
        # тот же абзац, что отдаёт POST /api/explain без модели: числа в нём
        # собраны движком, а не пересказаны. Оговорку об источниках ассистент
        # добавляет один раз на весь ответ, поэтому здесь она не нужна
        "paragraph": explain_mod.render(facts, with_sources=False),
    }


def find(store: Store, index: list, params: dict) -> dict:
    query = str(params.get("text") or "").strip()
    if not query:
        raise ToolError("нечего искать: в вопросе нет названия")
    found = search_mod.search(index, query, limit=5)
    return {
        "query": found["query"],
        "routes": [{"route_num": r["id"], "title": r["title"]} for r in found["routes"]],
        "stops": [
            {"stop_id": s["id"], "name": s["title"], "lat": s["lat"], "lon": s["lon"]}
            for s in found["stops"]
        ],
    }


REGISTRY = {
    "routes_attention": routes_attention,
    "route_profile": route_profile,
    "route_options": route_options,
    "coverage_holes": coverage_holes,
    "coverage_summary": coverage_summary,
    "scenario_from_text": scenario_from_text,
    "scenario_effect": scenario_effect,
    "find": find,
}

# описание и реализация обязаны совпадать: описанный, но не реализованный
# инструмент модель выберет, а вызвать его будет нечем
_declared, _implemented = set(toolspecs.TOOL_NAMES), set(REGISTRY)
if _declared != _implemented:
    raise RuntimeError(
        "список инструментов разошёлся: описано без реализации "
        f"{sorted(_declared - _implemented)}, реализовано без описания "
        f"{sorted(_implemented - _declared)}"
    )
