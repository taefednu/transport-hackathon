"""Разовый подбор сценария для трёхминутной защиты.

Ищет продление, которое выглядит проектным решением, а не растягиванием маршрута
через весь город: короткий хвост к остановке, про которую мы уверенно знаем,
что её никто не обслуживает, и умеренная цена в машинах.

Ничего не меняет: гоняет продления через тот же `scenario.run`, что и API.
В пайплайн не входит, в `run_all.sh` не добавлен.

Запуск: `.venv/bin/python scripts/pick_demo.py`
"""

import _bootstrap  # noqa: F401

import numpy as np
import polars as pl

from app import scenario
from app.store import load

WEEKDAY = "fri"
HOUR = 8
# сколько ближайших кандидатов пробовать на каждое направление
CANDIDATES_PER_ROUTE = 5
# хвост длиннее четверти маршрута — это уже другой маршрут, а не продление
MAX_LENGTH_SHARE = 0.25
# больше двух дополнительных машин на защите не объяснить
MAX_EXTRA_VEHICLES = 2
TOP = 10


def confidently_unserved(store) -> pl.DataFrame:
    """195 остановок: счётчик Яндекса равен нулю и ни в одной цепочке маршрута нет.

    Критерий из knowledge/decisions.md. У остановок OSM счётчика не существует,
    их ноль означает «не знаем», поэтому они сюда не попадают.
    """
    in_chain = set(store.route_stops["stop_id"].to_list())
    return store.stops.filter(
        (pl.col("n_routes") == 0)
        & (pl.col("source") == "yandex")
        & ~pl.col("stop_id").is_in(list(in_chain))
    )


def main() -> None:
    store = load()
    index = {stop_id: i for i, stop_id in enumerate(store.stops["stop_id"].to_list())}
    names = dict(zip(store.stops["stop_id"].to_list(), store.stops["name"].to_list()))
    # координаты цели продления: по ним конечную проверяют по спутнику
    coords = {
        r["stop_id"]: (r["lat"], r["lon"]) for r in store.stops.select("stop_id", "lat", "lon").to_dicts()
    }

    candidates = confidently_unserved(store)
    candidate_ids = candidates["stop_id"].to_list()
    candidate_xy = np.array([store.stop_xy[index[s]] for s in candidate_ids])
    print(f"уверенно необслуживаемых остановок: {len(candidate_ids)}")

    directions = store.routes.filter(
        pl.col("geometry_wkt").is_not_null()
        & (pl.col("geometry_wkt") != "")
        & (pl.col("quality") == "exact")
    ).select("route_num", "direction", "length_km").to_dicts()
    print(f"направлений с геометрией и точным порядком остановок: {len(directions)}")

    rows, tried, failed = [], 0, 0
    for route in directions:
        route_num, direction = route["route_num"], route["direction"]
        length_km = route["length_km"]
        if not length_km:
            continue
        try:
            sequence = scenario._route_sequence(store, route_num, direction)
        except scenario.ScenarioError:
            continue
        terminus = sequence[-1]
        if terminus not in index:
            continue

        distances = np.hypot(*(candidate_xy - store.stop_xy[index[terminus]]).T) / 1000.0
        order = np.argsort(distances)[:CANDIDATES_PER_ROUTE]
        for position in order:
            tail_km = float(distances[position])
            # длинный хвост отбрасываем до пересчёта: он и так не пройдёт отбор
            if tail_km > MAX_LENGTH_SHARE * length_km:
                continue
            stop_id = candidate_ids[position]
            tried += 1
            try:
                result = scenario.run(
                    store,
                    WEEKDAY,
                    HOUR,
                    [
                        {
                            "type": "extend_route",
                            "route_num": route_num,
                            "direction": direction,
                            "stops": [stop_id],
                        }
                    ],
                )
            except scenario.ScenarioError:
                failed += 1
                continue

            affected = result["affected_routes"][0]
            if affected.get("required_vehicles_after") is None:
                failed += 1
                continue
            extra = affected["required_vehicles_after"] - affected["required_vehicles_before"]
            if extra > MAX_EXTRA_VEHICLES or result["gained"] <= 0:
                continue

            segments = max(1, affected["n_stops_after"] - 1)
            rows.append(
                {
                    "route": f"{route_num} ({direction})",
                    "stop": names.get(stop_id) or stop_id,
                    "stop_id": stop_id,
                    "lat": coords[stop_id][0],
                    "lon": coords[stop_id][1],
                    "added_stops": affected["n_stops_after"] - affected["n_stops_before"],
                    "tail_km": tail_km,
                    "length_km": length_km,
                    "gained": result["gained"],
                    "cycle_before": affected["cycle_time_before"],
                    "cycle_after": affected["cycle_time_after"],
                    "veh_before": affected["required_vehicles_before"],
                    "veh_after": affected["required_vehicles_after"],
                    "extra": extra,
                    "city_share": affected["segments_at_city_speed"] / segments,
                    # людей на одну дополнительную машину. Ноль новых машин не значит
                    # «бесплатно»: оборот всё равно растёт, просто прибавка умещается
                    # в текущий выпуск за счёт округления ceil(оборот / интервал)
                    "per_vehicle": result["gained"] / extra if extra > 0 else float("inf"),
                }
            )

    rows.sort(key=lambda r: (-r["per_vehicle"], -r["gained"]))
    print(f"прогнано продлений: {tried}, не посчиталось: {failed}, прошло отбор: {len(rows)}\n")

    header = (
        f"{'маршрут':>12} {'до остановки':<26} {'id':<12} {'координаты':<21} {'ост':>3} "
        f"{'хвост':>7} {'+людей':>8} {'оборот, мин':>15} {'машин':>9} {'на машину':>11}"
    )
    print(header)
    print("-" * len(header))
    for row in rows[:TOP]:
        per = "в выпуск" if row["per_vehicle"] == float("inf") else f"{row['per_vehicle']:,.0f}"
        print(
            f"{row['route']:>12} {row['stop'][:26]:<26} {row['stop_id']:<12} "
            f"{row['lat']:.5f}, {row['lon']:.5f}  {row['added_stops']:>3} "
            f"{row['tail_km']:>6.2f}к {row['gained']:>8,.0f} "
            f"{row['cycle_before']:>6.1f}→{row['cycle_after']:<8.1f} "
            f"{row['veh_before']:>3}→{row['veh_after']:<5} {per:>11}"
        )

    if rows:
        best = rows[0]
        print(
            f"\nЛучший по цене: маршрут {best['route']} до «{best['stop']}» — "
            f"+{best['gained']:,.0f} человек, хвост {best['tail_km']:.2f} км "
            f"при длине маршрута {best['length_km']:.1f} км, оборот "
            f"{best['cycle_before']:.1f}→{best['cycle_after']:.1f} мин умещается "
            f"в текущий выпуск {best['veh_before']} машин."
        )


if __name__ == "__main__":
    main()
