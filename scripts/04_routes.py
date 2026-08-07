"""Шаг 4. Маршруты: геометрия и порядок остановок из OSM + реестр egov.

Вход:  локальный дамп OSM (relation type=route, route=bus|trolleybus),
       reference/egov_tashkent_sched_fare.json (режим работы, интервал, длина)
Выход: data/build/routes.parquet, data/build/route_stops.parquet

Overpass из этой сети недоступен, поэтому релейшены читаются из дампа двумя
проходами: сначала состав релейшенов, потом геометрия нужных путей и узлов.

Маршрут, у которого порядок остановок восстановился, помечается quality=exact.
Остальные остаются в базе с quality=approximate и без геометрии — достраивать
их догадками нельзя, иначе в расписании появятся выдуманные перегоны.
"""

import _bootstrap  # noqa: F401

import json
import re
import time

import geopandas as gpd
import numpy as np
import osmium
import polars as pl
import shapely
from scipy.spatial import cKDTree
from shapely.geometry import LineString

from app.config import (
    BOUNDARY_GEOJSON,
    EGOV_ROUTES_JSON,
    OSM_PBF,
    ROUTE_STOPS_PARQUET,
    ROUTES_PARQUET,
    STOP_DEDUP_M,
    STOPS_PARQUET,
)

# роли членов релейшена, которыми размечены точки посадки
STOP_ROLES = frozenset(
    {"stop", "platform", "stop_entry_only", "stop_exit_only",
     "platform_entry_only", "platform_exit_only"}
)
ROUTE_KINDS = frozenset({"bus", "trolleybus"})
# поля реестра egov (узбекские имена колонок)
EGOV_FIELDS = {
    "num": "Yonalishraqami",
    "name": "Yonalishnomi",
    "length_km": "Yonalishmasofasi",
    "work_weekday": "Ishtartibiishkuni",
    "work_saturday": "Ishtartibishanba",
    "work_sunday": "Ishtartibiyakshanba",
    "headway": "Ortachaoraliqinterval",
}
TIME_RANGE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")
NUMBER_RANGE = re.compile(r"(\d+(?:[.,]\d+)?)")


def parse_work_hours(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    m = TIME_RANGE.match(value)
    if not m:
        return None, None
    h1, m1, h2, m2 = (int(g) for g in m.groups())
    return f"{h1:02d}:{m1:02d}", f"{h2:02d}:{m2:02d}"


def parse_headway(value: str | None) -> float | None:
    """«11-13» → 12.0. Реестр даёт вилку, планируем по её середине."""
    if not value:
        return None
    nums = [float(x.replace(",", ".")) for x in NUMBER_RANGE.findall(value)]
    return sum(nums) / len(nums) if nums else None


def read_egov() -> dict[str, dict]:
    raw = json.loads(EGOV_ROUTES_JSON.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for row in raw:
        num = str(row.get(EGOV_FIELDS["num"], "")).strip()
        if not num or num in out:  # в файле 164 полных дубля
            continue
        length = row.get(EGOV_FIELDS["length_km"])
        out[num] = {
            "name": row.get(EGOV_FIELDS["name"]),
            "length_km": float(str(length).replace(",", ".")) if length else None,
            "headway_min": parse_headway(row.get(EGOV_FIELDS["headway"])),
            "work": {
                "wed": parse_work_hours(row.get(EGOV_FIELDS["work_weekday"])),
                "fri": parse_work_hours(row.get(EGOV_FIELDS["work_weekday"])),
                "sat": parse_work_hours(row.get(EGOV_FIELDS["work_saturday"])),
                "sun": parse_work_hours(row.get(EGOV_FIELDS["work_sunday"])),
            },
        }
    return out


def read_relations() -> list[dict]:
    """Первый проход: состав маршрутных релейшенов."""
    relations = []
    processor = osmium.FileProcessor(str(OSM_PBF)).with_filter(
        osmium.filter.EntityFilter(osmium.osm.RELATION)
    )
    for rel in processor:
        tags = rel.tags
        if tags.get("type") != "route" or tags.get("route") not in ROUTE_KINDS:
            continue
        ref = (tags.get("ref") or "").strip()
        if not ref:
            continue
        ways, stop_nodes = [], []
        for member in rel.members:
            if member.type == "w" and not member.role:
                ways.append(member.ref)
            elif member.type == "n" and member.role in STOP_ROLES:
                stop_nodes.append(member.ref)
        relations.append(
            {
                "rel_id": rel.id,
                "ref": ref,
                "name": tags.get("name"),
                "from": tags.get("from"),
                "to": tags.get("to"),
                "ways": ways,
                "stop_nodes": stop_nodes,
            }
        )
    return relations


def read_geometry(way_ids: set[int], node_ids: set[int]):
    """Второй проход: координаты нужных путей и узлов-остановок."""
    way_coords: dict[int, list[tuple[float, float]]] = {}
    node_coords: dict[int, tuple[float, float]] = {}

    processor = osmium.FileProcessor(str(OSM_PBF)).with_locations()
    for obj in processor:
        if isinstance(obj, osmium.osm.Node):
            if obj.id in node_ids:
                node_coords[obj.id] = (obj.location.lat, obj.location.lon)
        elif isinstance(obj, osmium.osm.Way):
            if obj.id not in way_ids:
                continue
            try:
                pts = [(n.location.lon, n.location.lat) for n in obj.nodes if n.location.valid()]
            except osmium.InvalidLocationError:
                continue
            if len(pts) >= 2:
                way_coords[obj.id] = pts
    return way_coords, node_coords


def stitch(ways: list[int], way_coords: dict[int, list]) -> LineString | None:
    """Склеивает пути релейшена в одну линию, разворачивая те, что лежат наоборот."""
    parts = [way_coords[w] for w in ways if w in way_coords]
    if not parts:
        return None
    line = list(parts[0])
    for nxt in parts[1:]:
        if not nxt:
            continue
        if _close(line[-1], nxt[0]):
            line.extend(nxt[1:])
        elif _close(line[-1], nxt[-1]):
            line.extend(reversed(nxt[:-1]))
        elif _close(line[0], nxt[-1]):
            line = list(nxt[:-1]) + line
        elif _close(line[0], nxt[0]):
            line = list(reversed(nxt[1:])) + line
        else:
            line.extend(nxt)  # разрыв в релейшене: соединяем как есть
    return LineString(line) if len(line) >= 2 else None


def _close(a, b, eps: float = 1e-7) -> bool:
    return abs(a[0] - b[0]) < eps and abs(a[1] - b[1]) < eps


def main() -> None:
    t0 = time.time()
    boundary = gpd.read_file(BOUNDARY_GEOJSON).geometry.iloc[0]
    metric_crs = gpd.GeoSeries([boundary], crs="EPSG:4326").estimate_utm_crs()
    egov = read_egov()
    print(f"egov: {len(egov)} уникальных маршрутов")

    relations = read_relations()
    print(f"OSM: релейшенов route=bus|trolleybus по стране: {len(relations)}")

    way_ids = {w for rel in relations for w in rel["ways"]}
    node_ids = {n for rel in relations for n in rel["stop_nodes"]}
    way_coords, node_coords = read_geometry(way_ids, node_ids)
    print(f"геометрия: путей {len(way_coords)}, узлов-остановок {len(node_coords)}")

    stops = pl.read_parquet(STOPS_PARQUET)
    stop_pts = gpd.GeoSeries(
        gpd.points_from_xy(stops["lon"], stops["lat"]), crs="EPSG:4326"
    ).to_crs(metric_crs)
    stop_tree = cKDTree(np.column_stack([stop_pts.x.to_numpy(), stop_pts.y.to_numpy()]))
    stop_ids = stops["stop_id"].to_numpy()

    from pyproj import Transformer

    to_metric = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)

    by_ref: dict[str, list[dict]] = {}
    for rel in relations:
        geom = stitch(rel["ways"], way_coords)
        if geom is None or not geom.intersects(boundary):
            continue
        rel["geometry"] = geom
        by_ref.setdefault(rel["ref"], []).append(rel)

    print(f"OSM: релейшенов внутри Ташкента: {sum(len(v) for v in by_ref.values())}")
    print(f"OSM: различных номеров маршрутов: {len(by_ref)}")

    route_rows, stop_rows = [], []
    matched_refs = set()

    for ref, rels in sorted(by_ref.items()):
        # порядок направлений фиксируем детерминированно, чтобы fwd не «прыгал»
        rels = sorted(rels, key=lambda r: (r.get("from") or "", r["rel_id"]))
        for direction, rel in zip(("fwd", "bwd"), rels):
            geom = rel["geometry"]
            seq_stops = []
            for node_id in rel["stop_nodes"]:
                if node_id not in node_coords:
                    continue
                lat, lon = node_coords[node_id]
                mx, my = to_metric.transform(lon, lat)
                dist, idx = stop_tree.query([mx, my])
                if dist <= STOP_DEDUP_M:
                    seq_stops.append(str(stop_ids[idx]))
            # подряд идущие дубли — это платформа и точка остановки одного места
            deduped = [s for i, s in enumerate(seq_stops) if i == 0 or s != seq_stops[i - 1]]

            quality = "exact" if len(deduped) >= 2 else "approximate"
            if quality == "exact":
                matched_refs.add(ref)
                for seq, stop_id in enumerate(deduped):
                    stop_rows.append(
                        {"route_num": ref, "direction": direction, "seq": seq, "stop_id": stop_id}
                    )

            meta = egov.get(ref, {})
            work = meta.get("work", {})
            length_km = meta.get("length_km")
            if length_km is None:
                length_km = float(
                    gpd.GeoSeries([geom], crs="EPSG:4326").to_crs(metric_crs).length.iloc[0] / 1000
                )
            route_rows.append(
                {
                    "route_num": ref,
                    "direction": direction,
                    "osm_rel_id": rel["rel_id"],
                    "name": meta.get("name") or rel.get("name"),
                    "planned_headway_min": meta.get("headway_min"),
                    "length_km": length_km,
                    "n_stops": len(deduped),
                    "quality": quality,
                    "geometry_wkt": shapely.to_wkt(geom, rounding_precision=6),
                    "in_egov": ref in egov,
                    **{f"work_start_{d}": (work.get(d) or (None, None))[0] for d in
                       ("wed", "fri", "sat", "sun")},
                    **{f"work_end_{d}": (work.get(d) or (None, None))[1] for d in
                       ("wed", "fri", "sat", "sun")},
                }
            )

    # маршруты реестра, которых в OSM нет: остаются в базе без геометрии
    for ref, meta in sorted(egov.items()):
        if ref in by_ref:
            continue
        work = meta["work"]
        route_rows.append(
            {
                "route_num": ref,
                "direction": "fwd",
                "osm_rel_id": None,
                "name": meta["name"],
                "planned_headway_min": meta["headway_min"],
                "length_km": meta["length_km"],
                "n_stops": 0,
                "quality": "approximate",
                "geometry_wkt": None,
                "in_egov": True,
                **{f"work_start_{d}": (work.get(d) or (None, None))[0] for d in
                   ("wed", "fri", "sat", "sun")},
                **{f"work_end_{d}": (work.get(d) or (None, None))[1] for d in
                   ("wed", "fri", "sat", "sun")},
            }
        )

    routes = pl.DataFrame(route_rows)
    route_stops = pl.DataFrame(stop_rows)
    routes.write_parquet(ROUTES_PARQUET)
    route_stops.write_parquet(ROUTE_STOPS_PARQUET)

    exact = routes.filter(pl.col("quality") == "exact")
    print(f"строк routes: {routes.height} (направлений), номеров: {routes['route_num'].n_unique()}")
    print(f"quality=exact: {exact.height} направлений по {exact['route_num'].n_unique()} маршрутам")
    print(f"строк route_stops: {route_stops.height}")
    print(f"маршрутов и в OSM, и в egov: {len(matched_refs & set(egov))}")
    print(f"время: {time.time() - t0:.1f} с")


if __name__ == "__main__":
    main()
