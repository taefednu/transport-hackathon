"""Движок расписания: из времени первого выезда — прибытие по каждой остановке.

Суть в одной строке: час берётся тот, в котором автобус реально доезжает до
перегона, а не час его отправления. Рейс, вышедший в 7:50, часть пути едет по
восьмичасовым скоростям, поэтому сдвиг выезда с 7:00 на 6:00 меняет прибытие
на конечную не ровно на час.
"""

from __future__ import annotations

import math

import numpy as np
import polars as pl

from app.config import LAYOVER_MIN
from app.store import Store

SECONDS_PER_HOUR = 3600
SECONDS_PER_DAY = 24 * SECONDS_PER_HOUR


def parse_hhmm(value: str) -> int:
    hh, mm = value.split(":")
    return int(hh) * SECONDS_PER_HOUR + int(mm) * 60


def format_hhmm(seconds: float) -> str:
    total = int(round(seconds))
    return f"{(total // SECONDS_PER_HOUR) % 24:02d}:{(total % SECONDS_PER_HOUR) // 60:02d}"


def travel_matrix(store: Store, route_num: str, direction: str, weekday: str) -> np.ndarray | None:
    """Матрица [перегон][час] в секундах. None — если времени хода нет."""
    if store.segment_time is None:
        return None
    df = store.segment_time.filter(
        (pl.col("route_num") == route_num)
        & (pl.col("direction") == direction)
        & (pl.col("weekday_type") == weekday)
    )
    if df.is_empty():
        return None
    n_seg = int(df["seq_from"].max()) + 1
    matrix = np.full((n_seg, 24), np.nan)
    for seq_from, hour, sec in zip(df["seq_from"], df["hour"], df["travel_sec"]):
        matrix[seq_from, hour] = sec
    return matrix


def run_trip(matrix: np.ndarray, departure_sec: float) -> list[float]:
    """Прибытия по остановкам для одного рейса, вышедшего в departure_sec."""
    arrivals = [departure_sec]
    t = departure_sec
    for seq in range(matrix.shape[0]):
        hour = int((t // SECONDS_PER_HOUR) % 24)
        t += float(matrix[seq, hour])
        arrivals.append(t)
    return arrivals


def build(
    store: Store,
    route_num: str,
    direction: str,
    weekday: str,
    first_departure: str,
    headway_min: float,
    n_vehicles: int | None,
    work_end: str | None,
) -> dict:
    matrix = travel_matrix(store, route_num, direction, weekday)
    if matrix is None:
        return {
            "available": False,
            "reason": (
                f"для маршрута {route_num} ({direction}, {weekday}) нет времени хода: "
                "порядок остановок восстановлен частично (quality=approximate)"
            ),
            "stops": [],
            "trips": 0,
        }

    start = parse_hhmm(first_departure)
    end = parse_hhmm(work_end) if work_end else SECONDS_PER_DAY
    if end <= start:  # режим работы переходит за полночь
        end += SECONDS_PER_DAY

    departures = []
    t = start
    while t <= end:
        departures.append(t)
        t += headway_min * 60

    trips = [run_trip(matrix, d) for d in departures]

    one_way_sec = trips[0][-1] - trips[0][0] if trips else 0.0
    cycle_time_min = (2 * one_way_sec) / 60.0 + LAYOVER_MIN
    required_vehicles = math.ceil(cycle_time_min / headway_min) if headway_min > 0 else None

    seq_stops = []
    if store.route_stops is not None:
        seq_stops = (
            store.route_stops.filter(
                (pl.col("route_num") == route_num) & (pl.col("direction") == direction)
            )
            .sort("seq")
            .join(store.stops.select("stop_id", "name"), on="stop_id", how="left")
            .select("seq", "stop_id", "name")
            .to_dicts()
        )

    table = []
    for i, stop in enumerate(seq_stops):
        table.append(
            {
                **stop,
                "arrivals": [format_hhmm(trip[i]) for trip in trips],
                "arrivals_sec": [trip[i] for trip in trips],
            }
        )

    return {
        "available": True,
        "stops": table,
        "trips": len(trips),
        "first_departure": first_departure,
        "headway_min": headway_min,
        "one_way_min": one_way_sec / 60.0,
        "cycle_time_min": cycle_time_min,
        "required_vehicles": required_vehicles,
        "n_vehicles": n_vehicles,
        "first_arrival_last_stop": format_hhmm(trips[0][-1]) if trips else None,
        "last_arrival_last_stop": format_hhmm(trips[-1][-1]) if trips else None,
        "last_arrival_last_stop_sec": trips[-1][-1] if trips else None,
    }


def actual_headway_by_hour(store: Store, route_num: str, weekday: str):
    """Фактический интервал по часам. None — пока не посчитан шаг 7 пайплайна."""
    if store.headway_actual is None:
        return None
    return (
        store.headway_actual.filter(
            (pl.col("route_num") == route_num) & (pl.col("weekday_type") == weekday)
        )
        .sort("hour")
        .select("hour", "actual_headway_min", "n_vehicles", "n_boardings")
        .to_dicts()
    )
