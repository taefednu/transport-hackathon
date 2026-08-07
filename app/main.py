"""FastAPI QATNOV. Сервер stateless: читает parquet из памяти и считает по запросу."""

from __future__ import annotations

from contextlib import asynccontextmanager

import polars as pl
from fastapi import FastAPI, HTTPException, Query

from app import config, coverage
from app.store import Store, load

STATE: dict[str, Store] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    STATE["store"] = load()
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


@app.get("/api/baseline")
def baseline(
    weekday: str = Query(default=config.WEEKDAY_TYPES[0]),
    hour: int = Query(default=8, ge=0, le=23),
) -> dict:
    if weekday not in config.WEEKDAY_TYPES:
        raise HTTPException(422, f"weekday должен быть одним из {config.WEEKDAY_TYPES}")
    return coverage.baseline(store(), weekday, hour)
