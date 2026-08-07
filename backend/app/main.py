"""FastAPI QATNOV. Сервер stateless: читает parquet из памяти и считает по запросу."""

from __future__ import annotations

import csv
import io
from contextlib import asynccontextmanager

import polars as pl
import shapely
from fastapi import FastAPI, HTTPException, Query, Response

from app import (
    config,
    coverage,
    explain as explain_mod,
    llm,
    nlparse,
    scenario,
    schedule,
    search as search_mod,
    validation,
)
from app.store import Store, load

STATE: dict[str, Store] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    STATE["store"] = load()
    STATE["search_index"] = search_mod.build_index(STATE["store"])
    yield
    STATE.clear()


app = FastAPI(title="QATNOV", lifespan=lifespan)


def store() -> Store:
    return STATE["store"]


@app.get("/api/meta")
def meta() -> dict:
    st = store()
    return {
        "constants": {
            "walk_limit_m": config.WALK_LIMIT_M,
            "frequent_headway_min": config.FREQUENT_HEADWAY_MIN,
            "h3_resolution": config.H3_RESOLUTION,
            "walk_speed_kmh": config.WALK_SPEED_KMH,
            "dwell_sec": config.DWELL_SEC,
            "layover_min": config.LAYOVER_MIN,
        },
        "size": {
            "stops": st.stops.height,
            "hexes": st.hexes.height,
            "stop_hex_pairs": st.stop_hexes.height,
            "walk_graph_nodes": st.walk_graph.n_nodes,
        },
        "sources": [
            {
                "name": "Yandex stop accessibility",
                "detail": "stations.csv, срез 30.09.2025",
                "license": "предоставлено организаторами трека 3",
            },
            {
                "name": "OpenStreetMap",
                "detail": "локальный дамп Geofabrik uzbekistan-latest",
                "license": "ODbL",
            },
            {
                "name": "Kontur Population",
                "detail": "H3 r8, срез 01.11.2023",
                "license": "CC BY",
            },
        ],
        "not_built_yet": st.missing,
    }


@app.get("/api/stops")
def stops() -> dict:
    st = store()
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [row["lon"], row["lat"]]},
            "properties": {
                "stop_id": row["stop_id"],
                "name": row["name"],
                "kind": row["kind"],
                "source": row["source"],
                "n_routes": row["n_routes"],
            },
        }
        for row in st.stops.iter_rows(named=True)
    ]
    return {"type": "FeatureCollection", "features": features}


@app.get("/api/routes")
def routes() -> dict:
    st = store()
    if st.routes is None:
        raise HTTPException(503, "нет data/build/routes.parquet (шаг 4 пайплайна)")
    grouped = (
        st.routes.group_by("route_num")
        .agg(
            pl.col("direction").alias("directions"),
            pl.col("name").first(),
            pl.col("planned_headway_min").first(),
            pl.col("length_km").max(),
            pl.col("n_stops").max(),
            pl.col("quality").min(),  # exact < approximate по алфавиту
            pl.col("in_egov").first(),
        )
        .sort("route_num")
    )
    return {"count": grouped.height, "routes": grouped.to_dicts()}


@app.get("/api/routes/{route_num}")
def route_detail(
    route_num: str,
    direction: str = Query(default="fwd"),
    weekday: str = Query(default=config.WEEKDAY_TYPES[0]),
) -> dict:
    st = store()
    if st.routes is None:
        raise HTTPException(503, "нет data/build/routes.parquet (шаг 4 пайплайна)")

    row = st.routes.filter(
        (pl.col("route_num") == route_num) & (pl.col("direction") == direction)
    )
    if row.is_empty():
        raise HTTPException(404, f"маршрут {route_num} направление {direction} не найден")
    route = row.to_dicts()[0]

    stops_seq = []
    if st.route_stops is not None:
        joined = (
            st.route_stops.filter(
                (pl.col("route_num") == route_num) & (pl.col("direction") == direction)
            )
            .sort("seq")
            .join(st.stops.select("stop_id", "name", "lat", "lon", "kind"), on="stop_id", how="left")
        )
        stops_seq = joined.select("seq", "stop_id", "name", "lat", "lon", "kind").to_dicts()

    segment_times = []
    if st.segment_time is not None:
        segment_times = (
            st.segment_time.filter(
                (pl.col("route_num") == route_num)
                & (pl.col("direction") == direction)
                & (pl.col("weekday_type") == weekday)
            )
            .sort(["seq_from", "hour"])
            .select("seq_from", "seq_to", "hour", "travel_sec", "length_m", "traffic_share", "source")
            .to_dicts()
        )

    geometry = None
    if route.get("geometry_wkt"):
        line = shapely.from_wkt(route["geometry_wkt"])
        geometry = {"type": "LineString", "coordinates": [list(c) for c in line.coords]}

    warnings = validation.route_warnings(st, route_num, direction, weekday)

    return {
        "route_num": route_num,
        "direction": direction,
        "weekday": weekday,
        "name": route["name"],
        "quality": route["quality"],
        "planned_headway_min": route["planned_headway_min"],
        "length_km": route["length_km"],
        "work_start": route.get(f"work_start_{weekday}"),
        "work_end": route.get(f"work_end_{weekday}"),
        "geometry": geometry,
        "stops": stops_seq,
        "segment_times": segment_times,
        "actual_headway": schedule.actual_headway_by_hour(st, route_num, weekday),
        "warnings": warnings,
    }


@app.get("/api/routes/{route_num}/schedule")
def route_schedule(
    route_num: str,
    direction: str = Query(default="fwd"),
    weekday: str = Query(default=config.WEEKDAY_TYPES[0]),
    first_departure: str = Query(default=None),
    headway_min: float = Query(default=None, gt=0),
    n_vehicles: int = Query(default=None, ge=0),
) -> dict:
    st = store()
    if st.routes is None:
        raise HTTPException(503, "нет data/build/routes.parquet (шаг 4 пайплайна)")

    row = st.routes.filter(
        (pl.col("route_num") == route_num) & (pl.col("direction") == direction)
    )
    if row.is_empty():
        raise HTTPException(404, f"маршрут {route_num} направление {direction} не найден")
    route = row.to_dicts()[0]

    # чего не задали — берём из реестра, а не из головы
    start = first_departure or route.get(f"work_start_{weekday}")
    if not start:
        raise HTTPException(
            422, f"для маршрута {route_num} нет времени начала работы в реестре, задайте first_departure"
        )
    headway = headway_min or route.get("planned_headway_min")
    if not headway:
        raise HTTPException(
            422, f"для маршрута {route_num} нет интервала в реестре, задайте headway_min"
        )

    result = schedule.build(
        st,
        route_num,
        direction,
        weekday,
        start,
        float(headway),
        n_vehicles,
        route.get(f"work_end_{weekday}"),
    )
    warnings = validation.route_warnings(st, route_num, direction, weekday)
    warnings.extend(validation.schedule_warnings(result, route, weekday))
    return {**result, "route_num": route_num, "direction": direction, "weekday": weekday,
            "warnings": warnings}


@app.get("/api/baseline")
def baseline(
    weekday: str = Query(default=config.WEEKDAY_TYPES[0]),
    hour: int = Query(default=8, ge=0, le=23),
) -> dict:
    if weekday not in config.WEEKDAY_TYPES:
        raise HTTPException(422, f"weekday должен быть одним из {config.WEEKDAY_TYPES}")
    return coverage.baseline(store(), weekday, hour)


@app.post("/api/scenario")
def post_scenario(body: dict) -> dict:
    weekday = body.get("weekday", config.WEEKDAY_TYPES[0])
    hour = int(body.get("hour", 8))
    ops = body.get("ops") or []
    if not isinstance(ops, list) or not ops:
        raise HTTPException(422, "нужен непустой список ops")
    try:
        return scenario.run(store(), weekday, hour, ops)
    except scenario.ScenarioError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/nl/scenario")
def nl_scenario(body: dict) -> dict:
    """Фраза словами → объект сценария для POST /api/scenario.

    Сценарий здесь не применяется: эндпоинт переводит язык в структуру,
    считает по-прежнему движок.
    """
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(422, "нужно поле text с фразой")
    return nlparse.parse(store(), STATE["search_index"], text)


@app.post("/api/explain")
def explain(body: dict) -> dict:
    """Результат сценария → абзац для служебной записки."""
    if not isinstance(body, dict) or not body:
        raise HTTPException(422, "нужно тело с результатом сценария")
    return explain_mod.explain(store(), body)


@app.get("/api/llm")
def llm_status() -> dict:
    """Каким путём пойдут оба текстовых эндпоинта прямо сейчас."""
    return llm.status()


@app.get("/api/holes")
def holes(limit: int = Query(default=200, ge=1, le=2000)) -> dict:
    st = store()
    if st.holes is None:
        raise HTTPException(503, "нет data/build/holes.parquet (шаг 9 пайплайна)")
    top = st.holes.head(limit)
    return {
        "count": st.holes.height,
        "people_total": float(st.holes["population"].sum()),
        "holes": top.to_dicts(),
    }


@app.get("/api/segments/parallel")
def segments_parallel(min_routes: int = Query(default=1, ge=1)) -> dict:
    st = store()
    if st.segment_routes is None:
        raise HTTPException(503, "нет data/build/segment_routes.parquet (шаг 8 пайплайна)")
    df = st.segment_routes.filter(pl.col("n") >= min_routes)
    return {"count": df.height, "segments": df.to_dicts()}


@app.get("/api/search")
def search(q: str = Query(min_length=1), limit: int = Query(default=10, ge=1, le=50)) -> dict:
    return search_mod.search(STATE["search_index"], q, limit)


@app.get("/api/export/schedule")
def export_schedule(
    route_num: str,
    direction: str = Query(default="fwd"),
    weekday: str = Query(default=config.WEEKDAY_TYPES[0]),
    first_departure: str = Query(default=None),
    headway_min: float = Query(default=None, gt=0),
) -> Response:
    payload = route_schedule(route_num, direction, weekday, first_departure, headway_min, None)
    if not payload.get("available"):
        raise HTTPException(422, payload.get("reason", "расписание недоступно"))

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["seq", "stop_id", "name", "trip_index", "arrival"])
    for stop in payload["stops"]:
        for trip_index, arrival in enumerate(stop["arrivals"]):
            writer.writerow([stop["seq"], stop["stop_id"], stop["name"], trip_index, arrival])

    filename = f"schedule_{route_num}_{direction}_{weekday}.csv"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/export/route")
def export_route(route_num: str, direction: str = Query(default="fwd")) -> dict:
    st = store()
    if st.routes is None:
        raise HTTPException(503, "нет data/build/routes.parquet (шаг 4 пайплайна)")
    row = st.routes.filter(
        (pl.col("route_num") == route_num) & (pl.col("direction") == direction)
    )
    if row.is_empty():
        raise HTTPException(404, f"маршрут {route_num} направление {direction} не найден")
    route = row.to_dicts()[0]

    features = []
    if route.get("geometry_wkt"):
        line = shapely.from_wkt(route["geometry_wkt"])
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [list(c) for c in line.coords]},
                "properties": {
                    "route_num": route_num,
                    "direction": direction,
                    "name": route["name"],
                    "quality": route["quality"],
                    "length_km": route["length_km"],
                },
            }
        )
    if st.route_stops is not None:
        seq = (
            st.route_stops.filter(
                (pl.col("route_num") == route_num) & (pl.col("direction") == direction)
            )
            .sort("seq")
            .join(st.stops.select("stop_id", "name", "lat", "lon"), on="stop_id", how="left")
        )
        for stop in seq.iter_rows(named=True):
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [stop["lon"], stop["lat"]]},
                    "properties": {
                        "seq": stop["seq"],
                        "stop_id": stop["stop_id"],
                        "name": stop["name"],
                    },
                }
            )
    return {"type": "FeatureCollection", "features": features}
