"""Всё, что сервер держит в памяти. Загружается один раз при старте.

Тяжёлый счёт живёт в scripts/, сюда попадают только готовые parquet. Артефакты,
которых ещё нет, не подменяются заглушками: соответствующая метрика честно
отдаётся как недоступная, и причина видна в /api/meta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import polars as pl
from scipy.spatial import cKDTree

from app import config
from app.walkgraph import WalkGraph


def _read(path: Path) -> pl.DataFrame | None:
    return pl.read_parquet(path) if path.exists() else None


@dataclass
class Store:
    stops: pl.DataFrame
    stop_hexes: pl.DataFrame
    hex_access: pl.DataFrame
    hexes: pl.DataFrame
    walk_graph: WalkGraph
    routes: pl.DataFrame | None = None
    route_stops: pl.DataFrame | None = None
    segment_time: pl.DataFrame | None = None
    city_speed: pl.DataFrame | None = None
    headway_actual: pl.DataFrame | None = None
    holes: pl.DataFrame | None = None
    segment_routes: pl.DataFrame | None = None

    stop_tree: cKDTree = field(init=False)
    stop_xy: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        node = self.stops["walk_node_id"].to_numpy()
        self.stop_xy = np.column_stack([self.walk_graph.x[node], self.walk_graph.y[node]])
        self.stop_tree = cKDTree(self.stop_xy)

    @property
    def missing(self) -> list[str]:
        """Чего ещё нет в data/build — чтобы API не притворялся полным."""
        absent = []
        for name, value in (
            ("routes", self.routes),
            ("route_stops", self.route_stops),
            ("segment_time", self.segment_time),
            ("city_speed", self.city_speed),
            ("headway_actual", self.headway_actual),
            ("holes", self.holes),
            ("segment_routes", self.segment_routes),
        ):
            if value is None:
                absent.append(name)
        return absent


def load() -> Store:
    for required in (
        config.STOPS_PARQUET,
        config.STOP_HEXES_PARQUET,
        config.HEX_ACCESS_PARQUET,
        config.ACTIVE_HEXES_PARQUET,
    ):
        if not required.exists():
            raise RuntimeError(f"нет артефакта пайплайна: {required}")

    return Store(
        stops=pl.read_parquet(config.STOPS_PARQUET),
        stop_hexes=pl.read_parquet(config.STOP_HEXES_PARQUET),
        hex_access=pl.read_parquet(config.HEX_ACCESS_PARQUET),
        hexes=pl.read_parquet(config.ACTIVE_HEXES_PARQUET),
        walk_graph=WalkGraph.load(config.WALK_GRAPH_PKL),
        routes=_read(config.ROUTES_PARQUET),
        route_stops=_read(config.ROUTE_STOPS_PARQUET),
        segment_time=_read(config.SEGMENT_TIME_PARQUET),
        city_speed=_read(config.CITY_SPEED_FALLBACK_PARQUET),
        headway_actual=_read(config.HEADWAY_ACTUAL_PARQUET),
        holes=_read(config.HOLES_PARQUET),
        segment_routes=_read(config.SEGMENT_ROUTES_PARQUET),
    )
