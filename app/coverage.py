"""Метрики покрытия: PNT-500, PNFT-15, T-median и дельта по сценарию.

Гексагон считается покрытым, если хотя бы одна остановка достаёт до него пешком
за WALK_LIMIT_M по сети. Расчёт — операции над множествами гексагонов, поэтому
сценарий пересчитывается по разнице, а не прогоном по всему городу.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from app.config import FREQUENT_HEADWAY_MIN, WALK_SPEED_KMH
from app.store import Store

METERS_PER_MINUTE_WALKING = WALK_SPEED_KMH * 1000.0 / 60.0


def walk_minutes(meters: float | np.ndarray):
    return meters / METERS_PER_MINUTE_WALKING


def covered_hexes(store: Store, stop_ids: set[str] | None = None) -> set[str]:
    """Гексагоны, до которых достаёт хотя бы одна из указанных остановок."""
    df = store.stop_hexes
    if stop_ids is not None:
        df = df.filter(pl.col("stop_id").is_in(list(stop_ids)))
    return set(df["h3_id"].to_list())


def nearest_stop_distance(store: Store, lat: np.ndarray, lon: np.ndarray):
    """Расстояние по прямой до ближайшей остановки — для гексагонов вне зоны.

    По сети такие расстояния не считаются: обход с отсечкой 500 м до них
    не доходит. Прямая — нижняя оценка, и она помечается в ответе как таковая.
    """
    from pyproj import Transformer

    transformer = Transformer.from_crs("EPSG:4326", store.walk_graph.crs, always_xy=True)
    x, y = transformer.transform(lon, lat)
    dist, idx = store.stop_tree.query(np.column_stack([np.asarray(x), np.asarray(y)]))
    return dist, store.stops["stop_id"].to_numpy()[idx]


def hex_walk_distance(store: Store) -> pl.DataFrame:
    """Для каждого гексагона — расстояние до ближайшей остановки и его происхождение.

    Берётся медиана по вершинам пешеходной сети внутри гексагона, а не минимум:
    минимум почти всегда ноль, потому что сама остановка стоит на одной из вершин,
    и «время до остановки» тогда вырождается.
    """
    by_network = store.hex_access.select(
        "h3_id",
        pl.col("walk_m_median").alias("walk_m"),
        "nearest_stop_id",
    )
    hexes = store.hexes.join(by_network, on="h3_id", how="left")

    missing = hexes.filter(pl.col("walk_m").is_null())
    if missing.height:
        dist, stop_id = nearest_stop_distance(
            store, missing["lat"].to_numpy(), missing["lon"].to_numpy()
        )
        filled = missing.with_columns(
            pl.Series("walk_m", dist),
            pl.Series("nearest_stop_id", stop_id),
            pl.lit("straightline").alias("walk_source"),
        )
        hexes = pl.concat(
            [
                hexes.filter(pl.col("walk_m").is_not_null()).with_columns(
                    pl.lit("network").alias("walk_source")
                ),
                filled,
            ],
            how="vertical_relaxed",
        )
    else:
        hexes = hexes.with_columns(pl.lit("network").alias("walk_source"))

    return hexes.with_columns(
        (pl.col("walk_m") / METERS_PER_MINUTE_WALKING).alias("walk_min")
    )


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float | None:
    """Медиана, взвешенная по населению: половина жителей ближе этого значения."""
    mask = weights > 0
    values, weights = values[mask], weights[mask]
    if values.size == 0:
        return None
    order = np.argsort(values)
    values, weights = values[order], weights[order]
    cutoff = weights.sum() / 2.0
    return float(values[np.searchsorted(np.cumsum(weights), cutoff)])


def frequent_stop_ids(store: Store, weekday: str, hour: int) -> set[str] | None:
    """Остановки, у которых хотя бы один маршрут ходит чаще FREQUENT_HEADWAY_MIN.

    Возвращает None, если фактические интервалы ещё не посчитаны: PNFT-15 без них
    не определён, и притворяться, что он равен PNT-500, нельзя.
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


def baseline(store: Store, weekday: str, hour: int) -> dict:
    hexes = hex_walk_distance(store)
    covered = covered_hexes(store)

    hexes = hexes.with_columns(pl.col("h3_id").is_in(list(covered)).alias("covered"))

    frequent_stops = frequent_stop_ids(store, weekday, hour)
    if frequent_stops is None:
        hexes = hexes.with_columns(pl.lit(None, dtype=pl.Boolean).alias("frequent"))
        pnft15 = None
    else:
        frequent_cells = covered_hexes(store, frequent_stops)
        hexes = hexes.with_columns(pl.col("h3_id").is_in(list(frequent_cells)).alias("frequent"))
        pnft15 = float(hexes.filter(pl.col("frequent"))["population"].sum())

    total = float(hexes["population"].sum())
    pnt500 = float(hexes.filter(pl.col("covered"))["population"].sum())

    return {
        "weekday": weekday,
        "hour": hour,
        "population_total": total,
        "pnt500": {
            "people": pnt500,
            "share": pnt500 / total if total else None,
            "people_outside": total - pnt500,
        },
        "pnft15": (
            None
            if pnft15 is None
            else {"people": pnft15, "share": pnft15 / total if total else None}
        ),
        "pnft15_unavailable_reason": (
            None if pnft15 is not None else "нет data/build/headway_actual.parquet (шаг 7 пайплайна)"
        ),
        "t_median_min": weighted_median(
            hexes["walk_min"].to_numpy(), hexes["population"].to_numpy()
        ),
        "hexes": hexes.select(
            pl.col("h3_id").alias("h3"),
            pl.col("population").alias("pop"),
            "covered",
            "frequent",
            "walk_min",
            "walk_source",
            "nearest_stop_id",
        ).to_dicts(),
    }
