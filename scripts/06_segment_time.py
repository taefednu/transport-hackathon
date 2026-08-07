"""Шаг 6. Время хода по перегонам с разбивкой по часам — ядро продукта.

Вход:  routes.parquet (геометрия, quality=exact), route_stops.parquet,
       stops.parquet, segment_speed.parquet, city_speed_fallback.parquet
Выход: data/build/segment_time.parquet
       route_num, direction, seq_from, seq_to, weekday_type, hour,
       travel_sec, length_m, traffic_share, source

Путь между соседними остановками берётся как участок трассы самого маршрута
(остановки проецируются на линию релейшена), а не строится заново по дорожному
графу: трасса уже задана OSM, и повторная прокладка только добавила бы
расхождение с реальным маршрутом.

Участок режется на куски по SPEED_CHUNK_M метров, каждый кусок едет со скоростью
ближайшей точки наблюдения Яндекса в радиусе TRAFFIC_MATCH_M. Куска без
наблюдения нет — вместо него берётся медиана по городу за тот же час, и это
отражается в поле `source`, потому что честность расчёта показывается в интерфейсе.
"""

import _bootstrap  # noqa: F401

import time

import geopandas as gpd
import numpy as np
import polars as pl
import shapely
from scipy.spatial import cKDTree
from shapely.ops import substring

from app.config import (
    BOUNDARY_GEOJSON,
    CITY_SPEED_FALLBACK_PARQUET,
    DWELL_SEC,
    ROUTE_STOPS_PARQUET,
    ROUTES_PARQUET,
    SEGMENT_SPEED_PARQUET,
    SEGMENT_TIME_PARQUET,
    SPEED_CHUNK_M,
    STOPS_PARQUET,
    TRAFFIC_MATCH_M,
    TRAFFIC_SOURCE_SHARE,
)

HOURS = tuple(range(24))


def chunk_midpoints(line, step: float):
    """Середины кусков длиной step вдоль линии и длина каждого куска."""
    total = line.length
    if total <= 0:
        return np.empty((0, 2)), np.empty(0)
    n = max(1, int(np.ceil(total / step)))
    edges = np.linspace(0.0, total, n + 1)
    mids = (edges[:-1] + edges[1:]) / 2.0
    lengths = np.diff(edges)
    pts = np.array([line.interpolate(float(m)).coords[0] for m in mids])
    return pts, lengths


def main() -> None:
    t0 = time.time()
    boundary = gpd.read_file(BOUNDARY_GEOJSON).geometry.iloc[0]
    metric_crs = gpd.GeoSeries([boundary], crs="EPSG:4326").estimate_utm_crs()

    routes = pl.read_parquet(ROUTES_PARQUET).filter(pl.col("quality") == "exact")
    route_stops = pl.read_parquet(ROUTE_STOPS_PARQUET)
    stops = pl.read_parquet(STOPS_PARQUET)
    speeds = pl.read_parquet(SEGMENT_SPEED_PARQUET)
    fallback = pl.read_parquet(CITY_SPEED_FALLBACK_PARQUET)

    weekdays = sorted(speeds["weekday_type"].unique().to_list())
    weekday_index = {w: i for i, w in enumerate(weekdays)}
    print(f"дни недели в трафике: {weekdays}")

    # скорость точки наблюдения: [точка][день][час]
    points = speeds.select("segment_id", "lat", "lon").unique().sort("segment_id")
    speed_grid = np.full((points.height, len(weekdays), len(HOURS)), np.nan)
    for seg, wd, hour, kmh in zip(
        speeds["segment_id"], speeds["weekday_type"], speeds["hour"], speeds["speed_kmh"]
    ):
        speed_grid[seg, weekday_index[wd], hour] = kmh

    fallback_grid = np.full((len(weekdays), len(HOURS)), np.nan)
    for wd, hour, kmh in zip(
        fallback["weekday_type"], fallback["hour"], fallback["median_speed_kmh"]
    ):
        fallback_grid[weekday_index[wd], hour] = kmh
    if np.isnan(fallback_grid).any():
        raise SystemExit("в медиане по городу есть пустые часы — считать нечем")

    point_xy = gpd.GeoSeries(
        gpd.points_from_xy(points["lon"], points["lat"]), crs="EPSG:4326"
    ).to_crs(metric_crs)
    point_tree = cKDTree(np.column_stack([point_xy.x.to_numpy(), point_xy.y.to_numpy()]))

    stop_xy = gpd.GeoSeries(
        gpd.points_from_xy(stops["lon"], stops["lat"]), crs="EPSG:4326"
    ).to_crs(metric_crs)
    stop_pos = {
        sid: (float(x), float(y))
        for sid, x, y in zip(stops["stop_id"], stop_xy.x.to_numpy(), stop_xy.y.to_numpy())
    }

    rows = []
    skipped_no_geom = 0
    reversed_lines = 0

    for route in routes.iter_rows(named=True):
        num, direction = route["route_num"], route["direction"]
        seq = route_stops.filter(
            (pl.col("route_num") == num) & (pl.col("direction") == direction)
        ).sort("seq")
        if seq.height < 2 or not route["geometry_wkt"]:
            skipped_no_geom += 1
            continue

        line = (
            gpd.GeoSeries([shapely.from_wkt(route["geometry_wkt"])], crs="EPSG:4326")
            .to_crs(metric_crs)
            .iloc[0]
        )
        stop_ids = seq["stop_id"].to_list()
        along = np.array(
            [line.project(shapely.Point(*stop_pos[s])) for s in stop_ids if s in stop_pos]
        )
        if len(along) < 2:
            skipped_no_geom += 1
            continue

        # трасса релейшена может быть склеена в обратную сторону относительно
        # порядка остановок — тогда разворачиваем линию, а не порядок остановок
        if np.sum(np.diff(along) < 0) > np.sum(np.diff(along) > 0):
            line = shapely.reverse(line)
            along = np.array([line.project(shapely.Point(*stop_pos[s])) for s in stop_ids])
            reversed_lines += 1

        along = np.maximum.accumulate(along)

        for i in range(len(along) - 1):
            start, end = float(along[i]), float(along[i + 1])
            if end - start < 1.0:
                # остановки спроецировались в одну точку: берём прямую между ними
                a, b = stop_pos[stop_ids[i]], stop_pos[stop_ids[i + 1]]
                seg_len = float(np.hypot(a[0] - b[0], a[1] - b[1]))
                mids = np.array([[(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]])
                lengths = np.array([seg_len])
            else:
                piece = substring(line, start, end)
                mids, lengths = chunk_midpoints(piece, SPEED_CHUNK_M)
                seg_len = float(piece.length)
            if seg_len <= 0 or len(lengths) == 0:
                continue

            dist, idx = point_tree.query(mids, distance_upper_bound=TRAFFIC_MATCH_M)
            matched = np.isfinite(dist)
            idx = np.where(matched, idx, 0)

            for wd in weekdays:
                w = weekday_index[wd]
                for hour in HOURS:
                    speed = np.where(matched, speed_grid[idx, w, hour], np.nan)
                    have = ~np.isnan(speed)
                    speed = np.where(have, speed, fallback_grid[w, hour])
                    travel_sec = float(np.sum(lengths / (speed * 1000.0 / 3600.0))) + DWELL_SEC
                    share = float(lengths[have].sum() / seg_len) if seg_len else 0.0
                    rows.append(
                        {
                            "route_num": num,
                            "direction": direction,
                            "seq_from": i,
                            "seq_to": i + 1,
                            "weekday_type": wd,
                            "hour": hour,
                            "travel_sec": travel_sec,
                            "length_m": seg_len,
                            "traffic_share": share,
                            "source": "traffic" if share >= TRAFFIC_SOURCE_SHARE else "fallback",
                        }
                    )

    segment_time = pl.DataFrame(rows)
    segment_time.write_parquet(SEGMENT_TIME_PARQUET)

    print(f"направлений посчитано: {routes.height - skipped_no_geom}, пропущено: {skipped_no_geom}")
    print(f"развёрнутых трасс: {reversed_lines}")
    print(f"строк segment_time: {segment_time.height} → {SEGMENT_TIME_PARQUET}")
    print(segment_time.group_by("source").agg(pl.len()).sort("source"))
    print(f"время: {time.time() - t0:.1f} с")


if __name__ == "__main__":
    main()
