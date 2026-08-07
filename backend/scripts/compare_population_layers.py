"""Разовое сравнение двух слоёв населения: выданный Kontur против раскладки
по застройке OSM (scripts/03b_population_buildings.py).

Ничего не меняет и в пайплайн не входит. Печатает оба набора чисел рядом,
чтобы решение о переключении принималось по цифрам, а не по описанию метода.

Запуск: `.venv/bin/python scripts/compare_population_layers.py`
"""

import _bootstrap  # noqa: F401

import h3
import numpy as np
import polars as pl

from app import config, coverage, scenario
from app.store import load

WEEKDAY, HOUR = "fri", 8
DEMO_ROUTE, DEMO_DIRECTION = "93", "fwd"
# сколько ближайших необслуживаемых остановок пробовать как цель продления
DEMO_CANDIDATES = 8


def rescale(hexes: pl.DataFrame, target: float) -> pl.DataFrame:
    factor = target / float(hexes["population"].sum())
    return hexes.with_columns(pl.col("population") * factor)


def holes_table(store, hexes: pl.DataFrame) -> pl.DataFrame:
    served = coverage.served_stop_ids(store)
    covered = coverage.covered_hexes(store, served)
    return (
        hexes.filter(~pl.col("h3_id").is_in(list(covered)) & (pl.col("population") > 0))
        .join(
            store.hex_access.select(
                "h3_id",
                pl.col("walk_m_served").alias("walk_m"),
                pl.col("nearest_stop_served").alias("stop_id"),
            ),
            on="h3_id",
            how="left",
        )
        .sort("population", descending=True)
    )


def demo_candidate(store) -> tuple[str | None, list[tuple[str, float, float]]]:
    """Кандидат на продление демонстрационного маршрута.

    Берётся не просто ближайшая уверенно необслуживаемая остановка: ближайшая
    может стоять внутри уже покрытого гексагона и не дать ничего. Пробуются
    несколько ближайших, выбирается первая с ненулевой прибавкой.
    """
    in_chain = set(store.route_stops["stop_id"].to_list())
    candidates = store.stops.filter(
        (pl.col("n_routes") == 0)
        & (pl.col("source") == "yandex")
        & ~pl.col("stop_id").is_in(list(in_chain))
    )["stop_id"].to_list()
    index = {s: i for i, s in enumerate(store.stops["stop_id"].to_list())}
    sequence = scenario._route_sequence(store, DEMO_ROUTE, DEMO_DIRECTION)
    terminus = store.stop_xy[index[sequence[-1]]]
    xy = np.array([store.stop_xy[index[s]] for s in candidates])
    distance = np.hypot(*(xy - terminus).T)

    tried: list[tuple[str, float, float]] = []
    best = None
    for position in np.argsort(distance)[:DEMO_CANDIDATES]:
        stop_id = candidates[int(position)]
        try:
            result = scenario.run(
                store, WEEKDAY, HOUR,
                [{"type": "extend_route", "route_num": DEMO_ROUTE,
                  "direction": DEMO_DIRECTION, "stops": [stop_id]}],
            )
        except scenario.ScenarioError:
            continue
        tried.append((stop_id, float(distance[position]) / 1000.0, result["gained"]))
        if best is None and result["gained"] > 0:
            best = stop_id
    return best, tried


def metrics(store, hexes: pl.DataFrame, stop_id: str | None) -> dict:
    store.hexes = hexes
    base = coverage.baseline(store, WEEKDAY, HOUR)
    out = {
        "население": base["population_total"],
        "PNT-500 человек": base["pnt500"]["people"],
        "PNT-500 доля": base["pnt500"]["share"],
        "вне доступности": base["pnt500"]["people_outside"],
        "PNFT-15 человек": (base["pnft15"] or {}).get("people"),
        "PNFT-15 доля": (base["pnft15"] or {}).get("share"),
        "T-median, мин": base["t_median_min"],
    }
    if stop_id is not None:
        result = scenario.run(
            store, WEEKDAY, HOUR,
            [{"type": "extend_route", "route_num": DEMO_ROUTE,
              "direction": DEMO_DIRECTION, "stops": [stop_id]}],
        )
        out["сценарий: прибавка"] = result["gained"]
        out["сценарий: потери"] = result["lost"]
    return out


def main() -> None:
    store = load()
    kontur = pl.read_parquet(config.HEXES_PARQUET)
    if not config.HEXES_BUILDINGS_PARQUET.exists():
        raise SystemExit("сначала: .venv/bin/python scripts/03b_population_buildings.py")
    buildings = pl.read_parquet(config.HEXES_BUILDINGS_PARQUET)

    kontur_total = float(kontur["population"].sum())
    targets = {
        "сумма Kontur в границе": kontur_total,
        f"Нацкомстат {config.POPULATION_CONTROL_DATE}": config.POPULATION_CONTROL,
    }

    stop_id, tried = demo_candidate(store)
    names = dict(zip(store.stops["stop_id"].to_list(), store.stops["name"].to_list()))
    print(f"кандидаты на продление маршрута {DEMO_ROUTE} ({DEMO_DIRECTION}), слой Kontur:")
    for candidate, tail_km, gained in tried:
        mark = " ← выбран" if candidate == stop_id else ""
        print(f"  {candidate}  {str(names.get(candidate))[:34]:<34} хвост {tail_km:5.2f} км  "
              f"прибавка {gained:>9,.0f}{mark}")
    if stop_id is None:
        print("  ни один кандидат не даёт прибавки — сценарий не считается")
    print()

    columns = {}
    for label, target in targets.items():
        columns[f"Kontur\n{label}"] = metrics(store, rescale(kontur, target), stop_id)
        columns[f"застройка\n{label}"] = metrics(store, rescale(buildings, target), stop_id)

    keys = list(next(iter(columns.values())).keys())
    head = f"{'метрика':<22}" + "".join(f"{c.replace(chr(10),' / '):>38}" for c in columns)
    print(head)
    print("-" * len(head))
    for key in keys:
        row = f"{key:<22}"
        for values in columns.values():
            v = values.get(key)
            if v is None:
                cell = "—"
            elif "доля" in key:
                cell = f"{v:.1%}"
            elif "мин" in key:
                cell = f"{v:.2f}"
            else:
                cell = f"{v:,.0f}"
            row += f"{cell:>38}"
        print(row)

    # --- десять крупнейших дыр в обоих слоях ------------------------------
    for label, hexes in (("Kontur", kontur), ("застройка OSM", buildings)):
        scaled = rescale(hexes, config.POPULATION_CONTROL)
        store.hexes = scaled
        holes = holes_table(store, scaled).head(10)
        total_out = holes_table(store, scaled)["population"].sum()
        print(f"\n=== десять крупнейших дыр: {label} "
              f"(контроль {config.POPULATION_CONTROL:,.0f}; всего вне доступности {total_out:,.0f}) ===")
        print(holes.select(
            "h3_id",
            pl.col("population").round(0).alias("человек"),
            pl.col("walk_m").round(0).alias("пешком_м"),
            pl.col("lat").round(4), pl.col("lon").round(4),
        ).to_pandas().to_string(index=False))

    # --- риск метода: где OSM размечен хуже -------------------------------
    print("\n=== риск метода: плотность улиц против плотности застройки ===")
    graph = store.walk_graph
    node_cells = [h3.latlng_to_cell(float(a), float(b), config.H3_RESOLUTION)
                  for a, b in zip(graph.lat, graph.lon)]
    nodes = pl.DataFrame({"h3_id": node_cells}).group_by("h3_id").agg(pl.len().alias("узлов_графа"))
    joined = (
        buildings.select("h3_id", "apartments", "individual_buildings", "capacity", "lat", "lon")
        .join(nodes, on="h3_id", how="full", coalesce=True)
        .with_columns(pl.col("узлов_графа").fill_null(0),
                      pl.col("individual_buildings").fill_null(0),
                      pl.col("apartments").fill_null(0))
        .join(kontur.select("h3_id", pl.col("population").alias("kontur")), on="h3_id", how="left")
        .filter(pl.col("kontur").is_not_null())
        .with_columns((pl.col("apartments") + pl.col("individual_buildings")).alias("зданий"))
    )
    # остановки Яндекса — единственный сигнал урбанизации, не зависящий от OSM
    stop_cells = [h3.latlng_to_cell(a, b, config.H3_RESOLUTION) for a, b in
                  zip(store.stops.filter(pl.col("source") == "yandex")["lat"].to_list(),
                      store.stops.filter(pl.col("source") == "yandex")["lon"].to_list())]
    yandex = pl.DataFrame({"h3_id": stop_cells}).group_by("h3_id").agg(
        pl.len().alias("остановок_яндекса"))
    joined = joined.join(yandex, on="h3_id", how="left").with_columns(
        pl.col("остановок_яндекса").fill_null(0))

    print(f"корреляция «здания ↔ узлы пешеходного графа»: "
          f"{float(joined.select(pl.corr('зданий', 'узлов_графа')).item()):.3f}")
    print(f"корреляция «здания ↔ остановки Яндекса» (сигнал вне OSM): "
          f"{float(joined.select(pl.corr('зданий', 'остановок_яндекса')).item()):.3f}")
    print(f"корреляция «Kontur ↔ остановки Яндекса»: "
          f"{float(joined.select(pl.corr('kontur', 'остановок_яндекса')).item()):.3f}")
    print(f"корреляция «Kontur ↔ узлы пешеходного графа»: "
          f"{float(joined.select(pl.corr('kontur', 'узлов_графа')).item()):.3f}")
    # Главный риск двухвесовой модели: вес многоквартирного дома в 25 раз больше,
    # поэтому район, где многоэтажки размечены как building=yes, теряет ёмкость.
    # Контроль — раскладка по площади пола (площадь пятна × этажность), которая
    # берётся из геометрии и от типа здания не зависит.
    raw = pl.read_parquet(config.BUILDINGS_PARQUET)
    typed = raw.filter(
        (pl.col("klass") != "non_residential") & pl.col("area_m2").is_not_null()
    ).with_columns(
        pl.col("area_m2").cut(list(config.BUILDING_AREA_BINS_M2),
                              left_closed=True).alias("area_bin")
    )
    typical = (
        typed.filter(pl.col("levels").is_not_null())
        .group_by("klass", "area_bin")
        .agg(pl.col("levels").median().alias("levels_typical"))
    )
    floor = (
        typed.join(typical, on=["klass", "area_bin"], how="left")
        .with_columns(pl.coalesce(pl.col("levels"), pl.col("levels_typical"), pl.lit(1.0)).alias("lv"))
        .with_columns((pl.col("area_m2") * pl.col("lv")).alias("floor_area"))
        .group_by("h3_id").agg(pl.col("floor_area").sum())
    )
    floor_layer = floor.with_columns(
        (pl.col("floor_area") / pl.col("floor_area").sum() * config.POPULATION_CONTROL).alias("population")
    ).join(buildings.select("h3_id", "lat", "lon"), on="h3_id", how="left")
    check = floor_layer.join(
        buildings.select("h3_id", pl.col("population").alias("двухвесовая")), on="h3_id", how="inner"
    )
    scaled_two = rescale(buildings, config.POPULATION_CONTROL).select(
        "h3_id", pl.col("population").alias("двухвесовая"))
    check = floor_layer.select("h3_id", pl.col("population").alias("по_площади_пола")).join(
        scaled_two, on="h3_id", how="inner")
    print(f"\nкорреляция «двухвесовая ↔ по площади пола»: "
          f"{float(check.select(pl.corr('двухвесовая', 'по_площади_пола')).item()):.3f}")
    diff = check.with_columns(
        (pl.col("по_площади_пола") - pl.col("двухвесовая")).abs().alias("расхождение"))
    print(f"суммарное расхождение: {float(diff['расхождение'].sum()):,.0f} человек "
          f"из {config.POPULATION_CONTROL:,.0f} "
          f"({float(diff['расхождение'].sum()) / config.POPULATION_CONTROL:.1%})")
    print("гексагоны с наибольшим расхождением:")
    print(diff.sort("расхождение", descending=True).head(8).select(
        "h3_id", pl.col("двухвесовая").round(0), pl.col("по_площади_пола").round(0),
        pl.col("расхождение").round(0)).to_pandas().to_string(index=False))

    store.hexes = floor_layer.select("h3_id", "population", "lat", "lon")
    base_floor = coverage.baseline(store, WEEKDAY, HOUR)
    print(f"\nPNT-500 по раскладке на площадь пола: {base_floor['pnt500']['share']:.1%} "
          f"(двухвесовая — 94.6%, Kontur — 90.4%)")

    suspicious = joined.filter((pl.col("узлов_графа") >= 200) & (pl.col("зданий") <= 20))
    print(f"гексагонов с плотной уличной сетью (≥200 узлов) и почти без зданий (≤20): "
          f"{suspicious.height} из {joined.height}")
    if suspicious.height:
        print(suspicious.select("h3_id", "зданий", "узлов_графа",
                                pl.col("kontur").round(0), "lat", "lon")
              .sort("узлов_графа", descending=True).head(15).to_pandas().to_string(index=False))


if __name__ == "__main__":
    main()
