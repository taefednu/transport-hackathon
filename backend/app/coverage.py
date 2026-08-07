"""Метрики покрытия: PNT-500, PNFT-15, T-median и дельта по сценарию.

Гексагон считается покрытым, если до него пешком по сети за WALK_LIMIT_M доходит
хотя бы одна **обслуживаемая** остановка. Остановка с `route_count = 0` — это
объект без обслуживания («остановка-призрак»), доступа к транспорту она не даёт,
и именно её появление в маршруте даёт прирост в сценарии.

Расчёт — операции над множествами гексагонов, поэтому сценарий считается по
разнице, а не прогоном по всему городу.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from app.config import FREQUENT_HEADWAY_MIN, WALK_SPEED_KMH
from app.store import Store

METERS_PER_MINUTE_WALKING = WALK_SPEED_KMH * 1000.0 / 60.0


def walk_minutes(meters):
    return meters / METERS_PER_MINUTE_WALKING


def served_stop_ids(store: Store) -> set[str]:
    """Остановки, которые кто-то обслуживает — по счётчику маршрутов из данных Яндекса."""
    return set(store.stops.filter(pl.col("n_routes") > 0)["stop_id"].to_list())


def covered_hexes(store: Store, stop_ids: set[str] | None = None) -> set[str]:
    df = store.stop_hexes
    if stop_ids is not None:
        df = df.filter(pl.col("stop_id").is_in(list(stop_ids)))
    return set(df["h3_id"].to_list())


def hex_distance_from(store: Store, stop_ids: set[str]) -> dict[str, float]:
    """Расстояние до ближайшей из указанных остановок по каждому гексагону в зоне 500 м."""
    df = (
        store.stop_hexes.filter(pl.col("stop_id").is_in(list(stop_ids)))
        .group_by("h3_id")
        .agg(pl.col("walk_m").min().alias("walk_m"))
    )
    return dict(zip(df["h3_id"].to_list(), df["walk_m"].to_list()))


def hex_table(store: Store, stop_ids: set[str] | None = None) -> pl.DataFrame:
    """Гексагоны с населением, признаком покрытия и временем пешком до остановки."""
    served = served_stop_ids(store) if stop_ids is None else stop_ids
    covered = covered_hexes(store, served)
    near = hex_distance_from(store, served)

    access = store.hex_access.select(
        "h3_id",
        pl.col("walk_m_served").alias("walk_m_base"),
        pl.col("nearest_stop_served").alias("nearest_stop_id"),
    )
    hexes = store.hexes.join(access, on="h3_id", how="left")

    h3_ids = hexes["h3_id"].to_list()
    base = hexes["walk_m_base"].to_list()
    walk_m, source = [], []
    for cell, fallback_m in zip(h3_ids, base):
        if cell in near:
            walk_m.append(near[cell])
            source.append("network_in_zone")
        elif fallback_m is not None:
            walk_m.append(fallback_m)
            source.append("network_beyond_zone")
        else:
            walk_m.append(None)
            source.append("no_walk_network")

    return hexes.with_columns(
        pl.Series("walk_m", walk_m, dtype=pl.Float64),
        pl.Series("walk_source", source),
        pl.Series("covered", [c in covered for c in h3_ids]),
    ).with_columns((pl.col("walk_m") / METERS_PER_MINUTE_WALKING).alias("walk_min"))


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float | None:
    mask = np.isfinite(values) & (weights > 0)
    values, weights = values[mask], weights[mask]
    if values.size == 0:
        return None
    order = np.argsort(values)
    values, weights = values[order], weights[order]
    return float(values[np.searchsorted(np.cumsum(weights), weights.sum() / 2.0)])


def frequent_stop_ids(store: Store, weekday: str, hour: int) -> set[str] | None:
    """Остановки, у которых хотя бы один маршрут ходит чаще FREQUENT_HEADWAY_MIN.

    None — если фактические интервалы ещё не посчитаны: PNFT-15 без них не определён,
    и приравнивать его к PNT-500 нельзя.
    """
    if store.headway_actual is None or store.route_stops is None:
        return None
    frequent_routes = (
        store.headway_actual.filter(
            (pl.col("weekday_type") == weekday)
            & (pl.col("hour") == hour)
            & (pl.col("actual_headway_min") <= FREQUENT_HEADWAY_MIN)
        )["route_num"]
        .unique()
        .to_list()
    )
    return set(
        store.route_stops.filter(pl.col("route_num").is_in(frequent_routes))["stop_id"].to_list()
    )


def summary(store: Store, hexes: pl.DataFrame, weekday: str, hour: int) -> dict:
    total = float(hexes["population"].sum())
    pnt500 = float(hexes.filter(pl.col("covered"))["population"].sum())

    frequent_stops = frequent_stop_ids(store, weekday, hour)
    if frequent_stops is None:
        pnft15 = None
    else:
        frequent_cells = covered_hexes(store, frequent_stops)
        pnft15 = float(
            hexes.filter(pl.col("h3_id").is_in(list(frequent_cells)))["population"].sum()
        )

    return {
        "population_total": total,
        "pnt500": {
            "people": pnt500,
            "share": pnt500 / total if total else None,
            "people_outside": total - pnt500,
        },
        "pnft15": (
            None if pnft15 is None else {"people": pnft15, "share": pnft15 / total if total else None}
        ),
        "pnft15_unavailable_reason": (
            None if pnft15 is not None else "нет data/build/headway_actual.parquet (шаг 7 пайплайна)"
        ),
        "t_median_min": weighted_median(
            hexes["walk_min"].to_numpy(), hexes["population"].to_numpy()
        ),
    }


def baseline(store: Store, weekday: str, hour: int) -> dict:
    served = served_stop_ids(store)
    hexes = hex_table(store, served)
    result = summary(store, hexes, weekday, hour)

    # для прозрачности: сколько накрывают вообще все физические остановки,
    # включая те, которые никто не обслуживает
    all_covered = covered_hexes(store)
    physical = float(
        store.hexes.filter(pl.col("h3_id").is_in(list(all_covered)))["population"].sum()
    )

    return {
        "weekday": weekday,
        "hour": hour,
        **result,
        "served_stops": len(served),
        "physical_stops": store.stops.height,
        "pnt500_all_physical_stops": physical,
        "hexes": hexes.select(
            pl.col("h3_id").alias("h3"),
            pl.col("population").alias("pop"),
            "covered",
            "walk_min",
            "walk_source",
            "nearest_stop_id",
            "lat",
            "lon",
        ).to_dicts(),
    }
