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
    dataquality,
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
        # маршрут с невозможными исходными значениями открывается и показывает
        # свои числа, но обязан сказать, чему в них нельзя доверять
        "data_flags": list(
            dict.fromkeys(
                item["message"] for item in dataquality.flags(store).get(route_num, [])
            )
        ),
        "attention": next(
            (
                {"score": r["score"], "reasons": diagnostics.reasons(r)}
                for r in diagnostics.compute(store, weekday, hour)
                if r["route_num"] == route_num
            ),
            None,
        ),
    }


def _unserved_candidates(store: Store) -> tuple[list[str], dict[str, str], list[str]]:
    """Цели продления, их уровень уверенности и отсеянные без застройки.

    Уверенность разная, и её нельзя прятать:

    - `yandex_confirmed` — счётчик маршрутов Яндекса равен нулю и остановки нет
      ни в одной цепочке (критерий из knowledge/decisions.md). Про такую
      остановку мы знаем, что её никто не обслуживает;
    - `osm_only` — остановка есть в OSM и её нет ни в одной восстановленной
      цепочке, но счётчика Яндекса по ней не существует. Возможно, её уже
      кто-то обслуживает: точный порядок остановок восстановлен у 117
      направлений из 223.

    Второй пул нужен не для полноты. Замер 08.08: из двенадцати остановок, чьё
    продление вообще способно добавить покрытие, одиннадцать — из OSM, а
    единственная яндексовская стоит без застройки. Оставить только первый пул —
    значит не иметь ни одной рекомендации.

    Отдельно отсеиваются остановки без жилья вокруг. Прирост считается по
    гексагонам в 500 м пешком, а ячейка H3 r8 — это 0.88 км²: остановка на
    пустыре получает людей, которые живут на дальнем краю ячейки.
    """
    if store.route_stops is None:
        return [], {}, []
    in_chain = set(store.route_stops["stop_id"].to_list())
    pool = store.stops.filter(
        (pl.col("n_routes") == 0) & ~pl.col("stop_id").is_in(list(in_chain))
    ).select("stop_id", "source")

    keep, confidence, dropped = [], {}, []
    for row in pool.iter_rows(named=True):
        stop_id = row["stop_id"]
        if dataquality.stop_is_off_housing(store, stop_id):
            dropped.append(stop_id)
            continue
        keep.append(stop_id)
        confidence[stop_id] = (
            "yandex_confirmed" if row["source"] == "yandex" else "osm_only"
        )
    return keep, confidence, dropped


def _chain_baseline(store: Store, route_num: str, direction: str, sequence: list[str]) -> float:
    """Сколько людей получает доступ от одного пересчёта цепочки маршрута.

    Движок считает обслуживаемыми все остановки изменённого маршрута. Если в
    цепочке есть остановки, у которых счётчик Яндекса показывает ноль, они
    становятся обслуживаемыми при **любом** сценарии по этому маршруту — и их
    людей нельзя приписывать продлению. У маршрута 1 таких остановок 15, и они
    давали одинаковые «+595 человек» трём разным целям продления.
    """
    served, before, population = _coverage_base(store)
    after = coverage.covered_hexes(store, served | set(sequence))
    return sum(population.get(cell, 0.0) for cell in after - before)


_BASE: dict[int, tuple[set[str], set[str], dict[str, float]]] = {}


def _coverage_base(store: Store):
    """Обслуживаемые остановки, покрытые гексагоны и население — один раз."""
    key = id(store)
    if key not in _BASE:
        served = coverage.served_stop_ids(store)
        _BASE[key] = (
            served,
            coverage.covered_hexes(store, served),
            dict(zip(store.hexes["h3_id"].to_list(), store.hexes["population"].to_list())),
        )
    return _BASE[key]


_POTENTIAL: dict[int, dict[str, float]] = {}


def _candidate_potential(store: Store) -> dict[str, float]:
    """Остановка-кандидат → сколько людей она вообще может добавить.

    Считается один раз операциями над множествами: гексагоны в пешей
    доступности остановки, которые сейчас никто не покрывает. Это потолок
    прироста, а не сам прирост — движок всё равно считает каждый вариант
    целиком. Нужно затем, чтобы перебирать не пять ближайших к конечной
    остановок, а пять самых полезных: ближайшие почти всегда стоят в уже
    покрытых кварталах, и перебор впустую тратит и время, и варианты.
    """
    key = id(store)
    if key in _POTENTIAL:
        return _POTENTIAL[key]

    _, covered, population = _coverage_base(store)
    gain: dict[str, float] = {}
    for stop_id, cell in zip(
        store.stop_hexes["stop_id"].to_list(), store.stop_hexes["h3_id"].to_list()
    ):
        if cell not in covered:
            gain[stop_id] = gain.get(stop_id, 0.0) + population.get(cell, 0.0)
    _POTENTIAL[key] = gain
    return gain


def _evaluate_extension(
    store: Store,
    *,
    route_num: str,
    direction: str,
    stop_id: str,
    tail_km: float,
    weekday: str,
    hour: int,
    baseline: float,
    names: dict,
    coords: dict,
    confidence: dict,
    skipped: list,
) -> dict | None:
    """Цена одного продления. `None` — вариант не прошёл отбор.

    Один расчёт на два входа: подбор по маршруту и подбор по дыре покрытия.
    Числа обязаны совпадать, откуда бы человек ни пришёл, поэтому фильтры
    и формулировки живут здесь, а не в двух местах.
    """
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
        return None

    affected = result["affected_routes"][0]
    before = affected.get("required_vehicles_before")
    after = affected.get("required_vehicles_after")
    if before is None or after is None:
        return None
    # прирост, который даёт именно новая остановка, а не пересчёт
    # цепочки: приписывать продлению чужих людей нельзя
    attributable = result["gained"] - baseline
    if attributable <= 0:
        return None
    if after - before > config.IMPROVEMENT_MAX_EXTRA_VEHICLES:
        return None

    return {
        "route_num": route_num,
        "direction": direction,
        "action": "продлить до остановки",
        "stop_id": stop_id,
        "stop_name": names.get(stop_id) or stop_id,
        "confidence": confidence.get(stop_id),
        "lat": coords[stop_id][0],
        "lon": coords[stop_id][1],
        "tail_km": round(tail_km, 2),
        "gained_people": int(round(attributable)),
        "gained_with_chain_recount": int(round(result["gained"])),
        "chain_recount_people": int(round(baseline)),
        "lost_people": int(round(result["lost"])),
        "cycle_time_before_min": round(affected["cycle_time_before"], 1),
        "cycle_time_after_min": round(affected["cycle_time_after"], 1),
        "required_vehicles_before": before,
        "required_vehicles_after": after,
        "extra_vehicles": after - before,
        "scenario": body,
    }


def route_options(store: Store, index: list, params: dict) -> dict:
    """Продления маршрута к необслуживаемым остановкам, посчитанные движком."""
    route_num = _require_route(store, params)
    weekday, hour = params["weekday"], params["hour"]

    marked = dataquality.flags(store).get(route_num)
    if marked:
        raise ToolError(
            f"маршрут {route_num} исключён из подбора: "
            + "; ".join(dict.fromkeys(item["message"] for item in marked))
            + ". Считать по этим данным цену продления нельзя"
        )

    candidates, confidence, off_housing = _unserved_candidates(store)
    if not candidates:
        raise ToolError("нет остановок, про которые известно, что их никто не обслуживает")

    stop_index = {s: i for i, s in enumerate(store.stops["stop_id"].to_list())}
    names = dict(zip(store.stops["stop_id"].to_list(), store.stops["name"].to_list()))
    coords = {
        row["stop_id"]: (row["lat"], row["lon"])
        for row in store.stops.select("stop_id", "lat", "lon").iter_rows(named=True)
    }
    candidate_xy = np.array([store.stop_xy[stop_index[s]] for s in candidates])
    potential = _candidate_potential(store)

    # Сколько кандидатов вообще способны кого-то добавить. Замер 08.08: из 420
    # необслуживаемых остановок таких 10 — остальные стоят в кварталах, которые
    # уже кто-то обслуживает. Без этого числа ответ «вариантов нет» выглядит
    # как поломка подбора, хотя это свойство плотной сети.
    useful = {s for s in candidates if potential.get(s, 0.0) > 0}

    options, skipped = [], []
    # ближайшая полезная остановка и допустимый хвост — по этому маршруту, а не
    # вообще: именно они объясняют, почему здесь дотянуться не до чего
    nearest_useful_km: float | None = None
    max_tail_km: float | None = None
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
        # сначала отсекаем по длине хвоста, потом берём самых полезных из
        # оставшихся: перебирать ближайших бессмысленно, они стоят в кварталах,
        # которые уже кто-то обслуживает
        limit_km = config.IMPROVEMENT_MAX_LENGTH_SHARE * float(length_km)
        max_tail_km = limit_km if max_tail_km is None else max(max_tail_km, limit_km)
        for position, stop_id in enumerate(candidates):
            if stop_id in useful and (
                nearest_useful_km is None or distances[position] < nearest_useful_km
            ):
                nearest_useful_km = float(distances[position])
        reachable = [i for i in range(len(candidates)) if distances[i] <= limit_km]
        reachable.sort(key=lambda i: -potential.get(candidates[i], 0.0))
        baseline = _chain_baseline(store, route_num, direction, sequence)
        for position in reachable[: config.IMPROVEMENT_CANDIDATES]:
            option = _evaluate_extension(
                store,
                route_num=route_num,
                direction=direction,
                stop_id=candidates[position],
                tail_km=float(distances[position]),
                weekday=weekday,
                hour=hour,
                baseline=baseline,
                names=names,
                coords=coords,
                confidence=confidence,
                skipped=skipped,
            )
            if option:
                options.append(option)

    options.sort(key=lambda o: (-o["gained_people"], o["extra_vehicles"]))

    # Вывод собирается кодом и уходит отдельным полем. Без него модель
    # пересказывает поля, а не заключение: на пустом списке вариантов она
    # написала «можно рассмотреть возможность продления», то есть ровно
    # обратное. Числа при этом настоящие, и охрана чисел такое не видит.
    if options:
        verdict = f"вариантов продления найдено: {len(options)}"
    else:
        parts = [
            f"продлить маршрут {route_num} некуда: остановок без обслуживания "
            f"в городе {len(candidates)}, но добавить людей способны только "
            f"{len(useful)} — остальные стоят в кварталах, которые уже кто-то "
            f"обслуживает"
        ]
        if nearest_useful_km is not None and max_tail_km is not None:
            parts.append(
                f"ближайшая полезная в {nearest_useful_km:.1f} км от конечной, "
                f"а продление длиннее {max_tail_km:.1f} км — это уже другой "
                f"маршрут, а не дотягивание"
            )
        parts.append("дотягиванием здесь ничего не выиграть, нужен новый маршрут")
        verdict = "; ".join(parts)

    return {
        "route_num": route_num,
        "weekday": weekday,
        "hour": hour,
        "hour_label": f"{hour}:00",
        # заключение, которое обязано прозвучать в ответе как есть
        "verdict": verdict,
        "options": options[: config.ASSISTANT_OPTIONS_LIMIT],
        "options_found": len(options),
        "candidates_checked": len(candidates),
        # из них способных добавить хоть кого-то: остальные стоят в кварталах,
        # которые уже обслуживаются, и продление к ним не меняет покрытия
        "candidates_that_can_add_people": len(useful),
        "nearest_useful_stop_km": (
            None if nearest_useful_km is None else round(nearest_useful_km, 1)
        ),
        "max_tail_km": None if max_tail_km is None else round(max_tail_km, 1),
        "candidates_off_housing": len(off_housing),
        "housing_radius_m": int(config.HOUSING_RADIUS_M),
        "min_housing_buildings": config.MIN_HOUSING_BUILDINGS,
        "max_extra_vehicles": config.IMPROVEMENT_MAX_EXTRA_VEHICLES,
        "skipped": skipped,
        "note": (
            "варианты — продления до остановок, которые сейчас никто не обслуживает и "
            "вокруг которых есть жильё; хвост до новой остановки считается по прямой "
            "и по медианной скорости города"
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


def hole_options(store: Store, index: list, params: dict) -> dict:
    """Что можно сделать с конкретной дырой покрытия.

    Обратная задача к `route_options`: там маршрут известен и ищутся цели,
    здесь известна ячейка и ищется маршрут, который до неё дотянуть. Числа
    считает тот же `_evaluate_extension`, поэтому «+N человек» с обеих
    сторон означает одно и то же.

    Пустой ответ — тоже ответ, и причина у него бывает разная: рядом может
    не быть ни одной остановки, про которую известно, что её никто не
    обслуживает; или такая остановка есть, но ни один маршрут не кончается
    достаточно близко, чтобы продление уложилось в четверть его длины.
    """
    cell = str(params.get("h3") or "").strip()
    if not cell:
        raise ToolError("нужен идентификатор ячейки h3")
    weekday, hour = params["weekday"], params["hour"]

    served, covered, population = _coverage_base(store)
    people = population.get(cell)
    if people is None:
        raise ToolError(f"ячейки {cell} нет в слое населения")

    candidates, confidence, _off_housing = _unserved_candidates(store)
    candidate_set = set(candidates)
    # остановки, с которых до этой ячейки доходят пешком, — их и надо включать
    reachers = store.stop_hexes.filter(pl.col("h3_id") == cell)["stop_id"].to_list()
    targets = [s for s in reachers if s in candidate_set]

    names = dict(zip(store.stops["stop_id"].to_list(), store.stops["name"].to_list()))
    coords = {
        row["stop_id"]: (row["lat"], row["lon"])
        for row in store.stops.select("stop_id", "lat", "lon").iter_rows(named=True)
    }
    stop_index = {s: i for i, s in enumerate(store.stops["stop_id"].to_list())}

    reason = None
    pairs: list[tuple[float, str, str, str]] = []
    if not targets:
        reason = (
            "рядом с ячейкой нет остановки, про которую известно, что её никто "
            "не обслуживает: продлевать некуда"
        )
    else:
        unreliable = dataquality.unreliable(store)
        termini = (
            store.route_stops.sort("seq")
            .group_by(["route_num", "direction"])
            .agg(pl.col("stop_id").last().alias("terminus"))
        )
        lengths = {
            (r["route_num"], r["direction"]): r["length_km"]
            for r in store.routes.select("route_num", "direction", "length_km").iter_rows(
                named=True
            )
        }
        for row in termini.iter_rows(named=True):
            key = (row["route_num"], row["direction"])
            length_km = lengths.get(key)
            terminus = row["terminus"]
            if row["route_num"] in unreliable or not length_km or terminus not in stop_index:
                continue
            limit_km = config.IMPROVEMENT_MAX_LENGTH_SHARE * float(length_km)
            for stop_id in targets:
                if stop_id not in stop_index:
                    continue
                tail_km = (
                    float(
                        np.hypot(
                            *(store.stop_xy[stop_index[stop_id]] - store.stop_xy[stop_index[terminus]])
                        )
                    )
                    / 1000.0
                )
                if tail_km <= limit_km:
                    pairs.append((tail_km, row["route_num"], row["direction"], stop_id))
        # короткий хвост дешевле: считаем сначала его, дальше упираемся в потолок
        pairs.sort()
        if not pairs:
            reason = (
                "ни один маршрут не кончается достаточно близко: продление до этой "
                f"ячейки длиннее {int(config.IMPROVEMENT_MAX_LENGTH_SHARE * 100)}% "
                "длины любого из них"
            )

    options, skipped = [], []
    checked = 0
    for tail_km, route_num, direction, stop_id in pairs[: config.IMPROVEMENT_CANDIDATES * 2]:
        try:
            sequence = scenario_mod._route_sequence(store, route_num, direction)
        except scenario_mod.ScenarioError as exc:
            skipped.append({"route_num": route_num, "direction": direction, "reason": str(exc)})
            continue
        checked += 1
        option = _evaluate_extension(
            store,
            route_num=route_num,
            direction=direction,
            stop_id=stop_id,
            tail_km=tail_km,
            weekday=weekday,
            hour=hour,
            baseline=_chain_baseline(store, route_num, direction, sequence),
            names=names,
            coords=coords,
            confidence=confidence,
            skipped=skipped,
        )
        if option:
            options.append(option)

    if pairs and not options and reason is None:
        reason = (
            "продления считались, но ни одно не прошло отбор: либо оно не добавляет "
            f"людей сверх пересчёта цепочки, либо требует больше "
            f"{config.IMPROVEMENT_MAX_EXTRA_VEHICLES} машин сверх нынешнего выпуска"
        )

    options.sort(key=lambda o: (-o["gained_people"], o["extra_vehicles"]))
    return {
        "h3": cell,
        "weekday": weekday,
        "hour": hour,
        "people": int(round(people)),
        "covered": cell in covered,
        "targets_nearby": len(targets),
        "routes_checked": checked,
        "options": options[: config.ASSISTANT_OPTIONS_LIMIT],
        "options_found": len(options),
        "reason": reason,
        "skipped": skipped,
        "max_extra_vehicles": config.IMPROVEMENT_MAX_EXTRA_VEHICLES,
        "max_length_share": config.IMPROVEMENT_MAX_LENGTH_SHARE,
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


def data_summary(store: Store, index: list, params: dict) -> dict:
    """Что система знает о сети и чего не знает.

    Вопрос «а данные-то у нас полные?» — не вне возможностей системы, а ровно
    про то, о чём она обязана говорить вслух. Раньше на него приходил отказ со
    списком умений: расчёта не было, и модель относила вопрос к чужим темам.
    """
    routes = store.routes
    total_directions = routes.height
    exact = routes.filter(pl.col("quality") == "exact").height
    with_geometry = routes.filter(
        pl.col("geometry_wkt").is_not_null() & (pl.col("geometry_wkt") != "")
    ).height

    flagged = dataquality.flags(store)
    served = int(store.stops.filter(pl.col("n_routes") > 0).height)

    traffic_share = None
    if store.segment_time is not None and store.segment_time.height:
        rows = store.segment_time.height
        by_traffic = store.segment_time.filter(pl.col("source") == "traffic").height
        traffic_share = round(by_traffic / rows * 100, 1)

    return {
        "route_numbers": int(routes["route_num"].n_unique()),
        "directions": int(total_directions),
        # «цельный маршрут» — это направление с восстановленным порядком
        # остановок: по нему считается расписание, время хода и сценарии.
        # Имена полей развёрнуты намеренно: модель пересказывает ключи, и
        # короткое `flagged_impossible` она прочла как «невозможен для проезда»
        "directions_with_restored_stop_order": int(exact),
        "directions_without_stop_order": int(total_directions - exact),
        "directions_with_trace": int(with_geometry),
        "routes_with_defective_source_data": int(len(flagged)),
        "routes_with_defective_source_data_numbers": sorted(flagged),
        "stops_total": int(store.stops.height),
        "stops_served": served,
        "segments_by_real_traffic_percent": traffic_share,
        "population_layer_date": config.ACTIVE_POPULATION_DATE,
        # то, чего нет вовсе: называется здесь же, чтобы вопрос о полноте
        # получал полный ответ, а не только приятную его половину
        "not_available": [
            "порядок остановок известен не у всех направлений, поэтому PNFT-15 — нижняя оценка",
            "GPS-треков автобусов в данных нет",
            "матрицы корреспонденций по Ташкенту нет: система оплаты регистрирует только вход",
        ],
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


def warm(store: Store) -> None:
    """Посчитать то, что иначе посчитается внутри первого запроса."""
    _coverage_base(store)
    _candidate_potential(store)
    _unserved_candidates(store)


REGISTRY = {
    "routes_attention": routes_attention,
    "route_profile": route_profile,
    "route_options": route_options,
    "coverage_holes": coverage_holes,
    "coverage_summary": coverage_summary,
    "data_summary": data_summary,
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
