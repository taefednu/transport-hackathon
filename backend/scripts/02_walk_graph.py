"""Шаг 2. Пешеходный граф Ташкента и зоны доступности остановок.

Вход:  локальный дамп OSM, граница города, data/build/stops.parquet
Выход: data/build/walk_graph.pkl — граф для рантайма
       data/build/stop_hexes.parquet — stop_id, h3_id (гексагоны в зоне 500 м)
       stops.parquet дополняется колонкой walk_node_id

Зона доступности строится по сети, а не буфером: 500 м пешком по улицам — это
не 500 м по прямой, и разница как раз и есть то, что инструмент показывает.
Порог — СНиП 2.07.01-89 п. 6.29 (константа WALK_LIMIT_M).
"""

import _bootstrap  # noqa: F401

import time

import geopandas as gpd
import h3
import numpy as np
import osmium
import polars as pl
from pyproj import Transformer
from scipy.spatial import cKDTree

from app.config import (
    BOUNDARY_GEOJSON,
    H3_RESOLUTION,
    HEX_ACCESS_PARQUET,
    OSM_PBF,
    STOP_HEXES_PARQUET,
    STOPS_PARQUET,
    WALK_GRAPH_PKL,
    WALK_HIGHWAY_TYPES,
    WALK_LIMIT_M,
)
from app.config import WGS84
from app.walkgraph import WalkGraph


class WayCollector(osmium.SimpleHandler):
    """Собирает рёбра пешеходной сети внутри bbox города."""

    def __init__(self, bbox):
        super().__init__()
        self.minx, self.miny, self.maxx, self.maxy = bbox
        self.edges: list[tuple[int, int]] = []
        self.coords: dict[int, tuple[float, float]] = {}

    def way(self, w):
        if w.tags.get("highway") not in WALK_HIGHWAY_TYPES:
            return
        if w.tags.get("access") in ("private", "no"):
            return
        try:
            pts = [(n.ref, n.location.lat, n.location.lon) for n in w.nodes if n.location.valid()]
        except osmium.InvalidLocationError:
            return
        inside = [
            (ref, lat, lon)
            for ref, lat, lon in pts
            if self.minx <= lon <= self.maxx and self.miny <= lat <= self.maxy
        ]
        if len(inside) < 2:
            return
        for ref, lat, lon in inside:
            self.coords[ref] = (lat, lon)
        for (a, _, _), (b, _, _) in zip(inside, inside[1:]):
            self.edges.append((a, b))


def main() -> None:
    t0 = time.time()
    boundary = gpd.read_file(BOUNDARY_GEOJSON).geometry.iloc[0]
    metric_crs = gpd.GeoSeries([boundary], crs=WGS84).estimate_utm_crs()

    handler = WayCollector(boundary.bounds)
    handler.apply_file(str(OSM_PBF), locations=True, idx="flex_mem")
    print(f"рёбер собрано: {len(handler.edges)}, вершин: {len(handler.coords)}")

    node_ids = np.fromiter(handler.coords.keys(), dtype=np.int64)
    order = {nid: i for i, nid in enumerate(node_ids)}
    lat = np.array([handler.coords[n][0] for n in node_ids])
    lon = np.array([handler.coords[n][1] for n in node_ids])

    transformer = Transformer.from_crs(WGS84, metric_crs, always_xy=True)
    x, y = transformer.transform(lon, lat)
    x, y = np.asarray(x), np.asarray(y)

    src = np.array([order[a] for a, _ in handler.edges], dtype=np.int32)
    dst = np.array([order[b] for _, b in handler.edges], dtype=np.int32)
    length = np.hypot(x[src] - x[dst], y[src] - y[dst])

    # неориентированный граф: каждое ребро кладём в обе стороны
    all_src = np.concatenate([src, dst])
    all_dst = np.concatenate([dst, src])
    all_len = np.concatenate([length, length])

    sort_idx = np.argsort(all_src, kind="stable")
    all_src, all_dst, all_len = all_src[sort_idx], all_dst[sort_idx], all_len[sort_idx]
    indptr = np.searchsorted(all_src, np.arange(len(node_ids) + 1)).astype(np.int64)

    graph = WalkGraph(
        indptr=indptr,
        indices=all_dst.astype(np.int32),
        weights=all_len.astype(np.float32),
        lat=lat,
        lon=lon,
        x=x,
        y=y,
        crs=str(metric_crs),
    )
    graph.save(WALK_GRAPH_PKL)
    print(f"граф: {graph.n_nodes} вершин, {len(all_dst)} направленных рёбер → {WALK_GRAPH_PKL}")

    stops = pl.read_parquet(STOPS_PARQUET)
    stop_x, stop_y = transformer.transform(stops["lon"].to_numpy(), stops["lat"].to_numpy())
    tree = cKDTree(np.column_stack([x, y]))
    snap_dist, snap_idx = tree.query(np.column_stack([stop_x, stop_y]))
    print(
        f"привязка остановок к графу: медиана {np.median(snap_dist):.1f} м, "
        f"максимум {snap_dist.max():.1f} м"
    )

    rows_stop, rows_hex, rows_dist = [], [], []
    for stop_id, node in zip(stops["stop_id"].to_list(), snap_idx):
        reached = graph.reachable(int(node), WALK_LIMIT_M)
        # расстояние до гексагона — медиана по его вершинам, а не минимум:
        # минимум почти всегда ноль, потому что остановка сама стоит на вершине
        per_cell: dict[str, list[float]] = {}
        for n, dist_m in reached.items():
            cell = h3.latlng_to_cell(float(lat[n]), float(lon[n]), H3_RESOLUTION)
            per_cell.setdefault(cell, []).append(dist_m)
        best = {cell: float(np.median(v)) for cell, v in per_cell.items()}
        rows_stop.extend([stop_id] * len(best))
        rows_hex.extend(best.keys())
        rows_dist.extend(best.values())

    stop_hexes = pl.DataFrame(
        {"stop_id": rows_stop, "h3_id": rows_hex, "walk_m": rows_dist}
    ).unique(subset=["stop_id", "h3_id"])
    stop_hexes.write_parquet(STOP_HEXES_PARQUET)

    stops.with_columns(pl.Series("walk_node_id", snap_idx.astype(np.int64))).write_parquet(
        STOPS_PARQUET
    )

    print(f"пар (остановка, гексагон): {stop_hexes.height} → {STOP_HEXES_PARQUET}")
    print(f"уникальных гексагонов в зонах доступности: {stop_hexes['h3_id'].n_unique()}")

    # расстояние по сети до ближайшей остановки для каждой вершины графа:
    # без него «время до остановки» меряется до края гексагона и вырождается в ноль.
    # Считаем дважды: по всем физическим остановкам и только по обслуживаемым —
    # остановка с route_count = 0 существует, но доступа к транспорту не даёт.
    node_cells = [
        h3.latlng_to_cell(float(la), float(lo), H3_RESOLUTION) for la, lo in zip(lat, lon)
    ]
    stop_ids = stops["stop_id"].to_numpy()
    served_mask = (stops["n_routes"].to_numpy() > 0)
    print(f"обслуживаемых остановок (route_count > 0): {served_mask.sum()} из {stops.height}")

    access = None
    for label, mask in (("all", np.ones(stops.height, dtype=bool)), ("served", served_mask)):
        sources = snap_idx[mask]
        subset_ids = stop_ids[mask]
        node_dist, node_owner = graph.nearest_source(sources)
        reached = np.isfinite(node_dist)
        print(
            f"[{label}] вершин с доступом: {reached.sum()} из {graph.n_nodes} "
            f"({reached.sum() / graph.n_nodes:.1%})"
        )
        part = (
            pl.DataFrame(
                {
                    "h3_id": node_cells,
                    "walk_m": node_dist,
                    "nearest_stop_id": np.where(
                        node_owner >= 0, subset_ids[np.clip(node_owner, 0, None)], None
                    ),
                }
            )
            .filter(pl.col("walk_m").is_finite())
            .group_by("h3_id")
            .agg(
                pl.col("walk_m").median().alias(f"walk_m_{label}"),
                pl.col("nearest_stop_id").sort_by("walk_m").first().alias(f"nearest_stop_{label}"),
            )
        )
        access = part if access is None else access.join(part, on="h3_id", how="full", coalesce=True)

    access.write_parquet(HEX_ACCESS_PARQUET)
    print(f"гексагонов с расстоянием по сети: {access.height} → {HEX_ACCESS_PARQUET}")
    print(f"время: {time.time() - t0:.1f} с")


if __name__ == "__main__":
    main()
