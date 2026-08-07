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

from app.config import DWELL_SEC, LAYOVER_MIN
from app.store import Store

SECONDS_PER_HOUR = 3600
SECONDS_PER_DAY = 24 * SECONDS_PER_HOUR
KMH_TO_M_PER_SEC = 1000.0 / 3600.0


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


def city_speed_by_hour(store: Store, weekday: str) -> np.ndarray | None:
    """Медианная скорость города по часам, км/ч. None — если шаг 5 не посчитан."""
    if store.city_speed is None:
        return None
    rows = store.city_speed.filter(pl.col("weekday_type") == weekday)
    if rows.is_empty():
        return None
    speeds = np.full(24, np.nan)
    for hour, kmh in zip(rows["hour"], rows["median_speed_kmh"]):
        speeds[int(hour)] = float(kmh)
    return speeds


def sequence_travel_matrix(
    store: Store, route_num: str, direction: str, weekday: str, sequence: list[str]
) -> tuple[np.ndarray | None, int]:
    """Матрица [перегон][час] для произвольной цепочки остановок.

    Перегоны, которые есть в базовом маршруте, берут посчитанное время хода.
    Перегоны, которых там нет — хвост продления или новое соседство после
    вставки и удаления, — считаются по медианной скорости города за тот же час
    и прямой линии между остановками: трассы для них не существует, а выдумывать
    её нельзя. Формула та же, что на шаге 6: длина / скорость + стоянка.

    Возвращает матрицу и число перегонов, посчитанных по медиане города.
    """
    if store.route_stops is None or store.segment_time is None or len(sequence) < 2:
        return None, 0

    base = (
        store.route_stops.filter(
            (pl.col("route_num") == route_num) & (pl.col("direction") == direction)
        )
        .sort("seq")["stop_id"]
        .to_list()
    )
    known: dict[tuple[str, str], np.ndarray] = {}
    rows = store.segment_time.filter(
        (pl.col("route_num") == route_num)
        & (pl.col("direction") == direction)
        & (pl.col("weekday_type") == weekday)
    )
    for seq_from, seq_to, hour, sec in zip(
        rows["seq_from"], rows["seq_to"], rows["hour"], rows["travel_sec"]
    ):
        if seq_from < len(base) and seq_to < len(base):
            pair = (base[seq_from], base[seq_to])
            known.setdefault(pair, np.full(24, np.nan))[int(hour)] = float(sec)

    speeds = city_speed_by_hour(store, weekday)
    if speeds is None:
        return None, 0

    index = {stop_id: i for i, stop_id in enumerate(store.stops["stop_id"].to_list())}
    matrix = np.full((len(sequence) - 1, 24), np.nan)
    from_city = 0
    for i, (a, b) in enumerate(zip(sequence, sequence[1:])):
        row = known.get((a, b))
        if row is not None and not np.isnan(row).any():
            matrix[i] = row
            continue
        if a not in index or b not in index:
            return None, 0
        metres = float(np.hypot(*(store.stop_xy[index[a]] - store.stop_xy[index[b]])))
        city = metres / (speeds * KMH_TO_M_PER_SEC) + DWELL_SEC
        matrix[i] = city if row is None else np.where(np.isnan(row), city, row)
        from_city += 1

    return (None, 0) if np.isnan(matrix).any() else (matrix, from_city)


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
    sequence: list[str] | None = None,
) -> dict:
    """`sequence` — цепочка остановок после сценария. None значит базовый маршрут."""
    if sequence is None:
        matrix, segments_at_city_speed = travel_matrix(store, route_num, direction, weekday), 0
    else:
        matrix, segments_at_city_speed = sequence_travel_matrix(
            store, route_num, direction, weekday, sequence
        )
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
    if sequence is not None:
        names = dict(zip(store.stops["stop_id"].to_list(), store.stops["name"].to_list()))
        seq_stops = [
            {"seq": i, "stop_id": stop_id, "name": names.get(stop_id)}
            for i, stop_id in enumerate(sequence)
        ]
    elif store.route_stops is not None:
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
        # перегоны, для которых трассы нет и время взято по медиане скорости города
        "segments_at_city_speed": segments_at_city_speed,
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
