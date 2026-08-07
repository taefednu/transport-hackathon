"""Проверки решений планировщика. Инструмент обязан спорить, а не только считать.

Каждое предупреждение возвращается с привязкой (маршрут, seq, остановка),
чтобы фронт мог повесить бейдж в нужное место.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from app.config import (
    DUPLICATION_ROUTE_COUNT,
    FALLBACK_SHARE_WARN,
    MAX_DUPLICATION_WARNINGS,
    MAX_ROUTE_LENGTH_KM,
    MIN_STOP_SPACING_M,
    SAME_POINT_SPACING_M,
)
from app.store import Store


def _warn(code, message, severity, **anchor):
    return {"code": code, "message": message, "severity": severity, **anchor}


def route_warnings(store: Store, route_num: str, direction: str, weekday: str) -> list[dict]:
    out: list[dict] = []
    if store.routes is None:
        return out

    row = store.routes.filter(
        (pl.col("route_num") == route_num) & (pl.col("direction") == direction)
    )
    if row.is_empty():
        return out
    route = row.to_dicts()[0]

    if route["quality"] == "approximate":
        out.append(
            _warn(
                "approximate_geometry",
                "Порядок остановок восстановлен частично",
                "info",
                route_num=route_num,
            )
        )

    length_km = route.get("length_km")
    if length_km is not None and length_km > MAX_ROUTE_LENGTH_KM:
        out.append(
            _warn(
                "route_too_long",
                f"Длина {length_km:.1f} км при пороге {MAX_ROUTE_LENGTH_KM:.0f} км",
                "warning",
                route_num=route_num,
            )
        )

    if store.segment_time is not None:
        seg = store.segment_time.filter(
            (pl.col("route_num") == route_num)
            & (pl.col("direction") == direction)
            & (pl.col("weekday_type") == weekday)
        )
        if not seg.is_empty():
            per_segment = seg.group_by("seq_from").agg(pl.col("source").first())
            share = (per_segment["source"] == "fallback").mean()
            if share > FALLBACK_SHARE_WARN:
                out.append(
                    _warn(
                        "fallback_speed",
                        f"Скорости по {share:.0%} перегонов взяты по медиане города",
                        "info",
                        route_num=route_num,
                    )
                )

    out.extend(stop_spacing_warnings(store, route_num, direction))
    out.extend(duplication_warnings(store, route_num, direction))
    return out


def stop_spacing_warnings(store: Store, route_num: str, direction: str) -> list[dict]:
    if store.route_stops is None:
        return []
    seq = (
        store.route_stops.filter(
            (pl.col("route_num") == route_num) & (pl.col("direction") == direction)
        )
        .sort("seq")
        .join(store.stops.select("stop_id", "name", "walk_node_id"), on="stop_id", how="left")
    )
    if seq.height < 2:
        return []
    nodes = seq["walk_node_id"].to_numpy()
    x, y = store.walk_graph.x[nodes], store.walk_graph.y[nodes]
    gaps = np.hypot(np.diff(x), np.diff(y))
    out = []
    for i, gap in enumerate(gaps):
        if gap < SAME_POINT_SPACING_M:
            # Ноль метров — не решение планировщика и не сбой счёта: в OSM
            # платформа и место посадки размечены разными узлами с одной
            # координатой. Формулировка говорит про данные, а не про ошибку.
            out.append(
                _warn(
                    "stops_same_point",
                    "Два узла одного остановочного пункта: в OSM платформа "
                    "и место посадки размечены отдельно, координата у них одна",
                    "info",
                    route_num=route_num,
                    seq=i + 1,
                    stop_id=seq["stop_id"][i + 1],
                )
            )
        elif gap < MIN_STOP_SPACING_M:
            out.append(
                _warn(
                    "stops_too_close",
                    f"Остановки в {gap:.0f} м друг от друга — ближе {MIN_STOP_SPACING_M:.0f} м",
                    "info",
                    route_num=route_num,
                    seq=i + 1,
                    stop_id=seq["stop_id"][i + 1],
                )
            )
    return out


def duplication_warnings(store: Store, route_num: str, direction: str) -> list[dict]:
    if store.segment_routes is None:
        return []
    mine = store.segment_routes.filter(
        (pl.col("route_num") == route_num) & (pl.col("direction") == direction)
    )
    if mine.is_empty():
        return []
    crowded = mine.filter(pl.col("n") >= DUPLICATION_ROUTE_COUNT)
    return [
        _warn(
            "duplication",
            f"На этом перегоне {row['n']} маршрутов",
            "warning",
            route_num=route_num,
            segment_key=row["segment_key"],
        )
        for row in crowded.head(MAX_DUPLICATION_WARNINGS).iter_rows(named=True)
    ]


def schedule_warnings(schedule: dict, route: dict, weekday: str) -> list[dict]:
    """Предупреждения, которые видны только после прогона расписания."""
    out: list[dict] = []
    if not schedule.get("available"):
        return out

    required = schedule.get("required_vehicles")
    have = schedule.get("n_vehicles")
    if required is not None and have is not None and required > have:
        out.append(
            _warn(
                "vehicles_short",
                f"Интервал {schedule['headway_min']:g} мин требует {required} машин, "
                f"на линии {have}",
                "error",
                route_num=route["route_num"],
            )
        )

    work_end = route.get(f"work_end_{weekday}")
    last_sec = schedule.get("last_arrival_last_stop_sec")
    if work_end and last_sec is not None:
        from app.schedule import SECONDS_PER_DAY, parse_hhmm

        limit = parse_hhmm(work_end)
        if limit < parse_hhmm(schedule["first_departure"]):
            limit += SECONDS_PER_DAY
        if last_sec > limit:
            out.append(
                _warn(
                    "beyond_work_hours",
                    f"Последний рейс возвращается в {schedule['last_arrival_last_stop']}, "
                    f"парк работает до {work_end}",
                    "warning",
                    route_num=route["route_num"],
                )
            )
    return out
