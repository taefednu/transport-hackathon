"""Шаг 1. Единая база остановок: Яндекс + OSM, дедупликация по расстоянию.

Вход:  data/raw/data/yandex/stations.csv (2 872 строки, с изохронами)
       локальный дамп OSM (highway=bus_stop, public_transport=platform)
Выход: data/build/stops.parquet — stop_id, name, name_norm, lat, lon, kind,
       source, n_routes, walk_node_id (заполняется шагом 2)

Приоритет при склейке отдаётся Яндексу: у его остановок есть число маршрутов.
"""

import _bootstrap  # noqa: F401

import geopandas as gpd
import numpy as np
import osmium
import polars as pl
import shapely
from scipy.spatial import cKDTree

from app.config import (
    BOUNDARY_GEOJSON,
    DATA_BUILD,
    OSM_PBF,
    STATIONS_CSV,
    STOP_DEDUP_M,
    STOPS_PARQUET,
)
from app.textnorm import normalize

# теги OSM, которыми размечены остановки наземного транспорта
OSM_STOP_TAGS = (
    ("highway", "bus_stop"),
    ("public_transport", "platform"),
    ("public_transport", "stop_position"),
    ("railway", "tram_stop"),
)
# как транспорт называется в выданном файле → как называем мы
YANDEX_KIND_MAP = {"underground": "metro", "bus": "bus", "minibus": "minibus"}


def load_boundary() -> shapely.geometry.base.BaseGeometry:
    return gpd.read_file(BOUNDARY_GEOJSON).geometry.iloc[0]


def yandex_stops() -> pl.DataFrame:
    df = pl.read_csv(STATIONS_CSV)
    kinds = []
    for types in df["transport_types"].to_list():
        if not types:
            kinds.append("bus")  # остановка-призрак: обслуживания нет, объект есть
            continue
        first = types.split("|")[0]
        kinds.append(YANDEX_KIND_MAP.get(first, first))
    return df.select(
        pl.Series("stop_id", [f"Y{i:05d}" for i in range(df.height)]),
        pl.col("station_name").alias("name"),
        pl.col("lat"),
        pl.col("lon"),
        pl.Series("kind", kinds),
        pl.lit("yandex").alias("source"),
        pl.col("route_count").alias("n_routes"),
    )


def osm_stops(boundary: shapely.geometry.base.BaseGeometry) -> pl.DataFrame:
    minx, miny, maxx, maxy = boundary.bounds
    rows = []
    processor = osmium.FileProcessor(str(OSM_PBF)).with_locations().with_filter(
        osmium.filter.EntityFilter(osmium.osm.NODE)
    )
    for node in processor:
        tags = node.tags
        if not any(tags.get(k) == v for k, v in OSM_STOP_TAGS):
            continue
        lon, lat = node.location.lon, node.location.lat
        if not (minx <= lon <= maxx and miny <= lat <= maxy):
            continue
        kind = "metro" if tags.get("station") == "subway" else "bus"
        rows.append(
            {
                "stop_id": f"N{node.id}",
                "name": tags.get("name") or tags.get("name:uz") or "",
                "lat": lat,
                "lon": lon,
                "kind": kind,
                "source": "osm",
                "n_routes": 0,
            }
        )
    df = pl.DataFrame(rows)
    if df.is_empty():
        return df
    pts = gpd.GeoSeries(gpd.points_from_xy(df["lon"], df["lat"]), crs="EPSG:4326")
    inside = pts.within(boundary).to_numpy()
    return df.filter(pl.Series(inside))


def dedup(base: pl.DataFrame, extra: pl.DataFrame, metric_crs) -> pl.DataFrame:
    """Выбрасывает из extra всё, что ближе STOP_DEDUP_M к какой-либо точке base."""
    if extra.is_empty():
        return base
    base_xy = to_metric(base, metric_crs)
    extra_xy = to_metric(extra, metric_crs)
    tree = cKDTree(base_xy)
    near = tree.query_ball_point(extra_xy, r=STOP_DEDUP_M)
    keep = np.array([len(hits) == 0 for hits in near])
    return pl.concat([base, extra.filter(pl.Series(keep))], how="vertical")


def to_metric(df: pl.DataFrame, metric_crs) -> np.ndarray:
    pts = gpd.GeoSeries(
        gpd.points_from_xy(df["lon"], df["lat"]), crs="EPSG:4326"
    ).to_crs(metric_crs)
    return np.column_stack([pts.x.to_numpy(), pts.y.to_numpy()])


def main() -> None:
    boundary = load_boundary()
    metric_crs = gpd.GeoSeries([boundary], crs="EPSG:4326").estimate_utm_crs()

    ya = yandex_stops()
    print(f"Яндекс: {ya.height}")

    osm = osm_stops(boundary)
    print(f"OSM в границе: {osm.height}")

    merged = dedup(ya, osm, metric_crs)
    print(f"после дедупликации {STOP_DEDUP_M:.0f} м: {merged.height}")

    merged = merged.with_columns(
        pl.col("name")
        .map_elements(normalize, return_dtype=pl.String)
        .alias("name_norm"),
        pl.lit(None, dtype=pl.Int64).alias("walk_node_id"),
    ).select(
        "stop_id", "name", "name_norm", "lat", "lon", "kind", "source", "n_routes",
        "walk_node_id",
    )

    DATA_BUILD.mkdir(parents=True, exist_ok=True)
    merged.write_parquet(STOPS_PARQUET)
    print(merged["kind"].value_counts(sort=True))
    print(f"записано строк: {merged.height} → {STOPS_PARQUET}")


if __name__ == "__main__":
    main()
