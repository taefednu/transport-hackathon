"""Разрывы трассы: где в геометрии не хватает куска.

Релейшен маршрута в OSM собирается из отдельных путей. Если какого-то пути нет
или он не состыкован с соседями, в геометрии остаётся ребро от конца одного
куска до начала другого — прямая через полгорода. Рисовать её нельзя: она
выглядит как трасса, которой не существует.

Порог подобран по данным, а не назначен. Распределение длин рёбер по всем 125
направлениям: медиана 18 м, p99 217 м, p99.9 525 м, максимум 11.9 км. Одной
длины мало: рёбра 150–400 м почти всегда настоящие — это прямые улицы, у
которых в OSM просто мало узлов (излом с соседями больше 40° лишь у 3–8% из
них). А среди рёбер длиннее километра излом резкий у 95% — маршрут «прыгает»
и возвращается. Поэтому правило из двух частей: длинное ребро со сломом
направления, либо просто очень длинное.

Порог 300 м без проверки излома разрезал бы 251 место, в основном настоящие
улицы. С проверкой остаётся 76 разрывов у 21 направления.
"""

from __future__ import annotations

import math

from shapely.geometry import LineString

from app.config import (
    GEOMETRY_GAP_FAR_M,
    GEOMETRY_GAP_NEAR_M,
    GEOMETRY_GAP_TURN_DEG,
)

_LAT0 = 41.3
_KX = math.cos(math.radians(_LAT0)) * 6371008.8 * math.pi / 180
_KY = 6371008.8 * math.pi / 180


def _edges(coords: list[tuple[float, float]]) -> tuple[list[float], list[float]]:
    """Длины рёбер в метрах и их направления в градусах."""
    lengths: list[float] = []
    bearings: list[float] = []
    for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
        dx = (x2 - x1) * _KX
        dy = (y2 - y1) * _KY
        lengths.append(math.hypot(dx, dy))
        bearings.append(math.degrees(math.atan2(dy, dx)))
    return lengths, bearings


def gap_indices(coords: list[tuple[float, float]]) -> list[int]:
    """Индексы рёбер-швов: ребро i соединяет точки i и i+1."""
    if len(coords) < 2:
        return []
    lengths, bearings = _edges(coords)
    out: list[int] = []
    for i, length in enumerate(lengths):
        if length >= GEOMETRY_GAP_FAR_M:
            out.append(i)
            continue
        if length < GEOMETRY_GAP_NEAR_M:
            continue
        turns = []
        if i > 0:
            turns.append(abs((bearings[i] - bearings[i - 1] + 180) % 360 - 180))
        if i < len(lengths) - 1:
            turns.append(abs((bearings[i + 1] - bearings[i] + 180) % 360 - 180))
        if turns and max(turns) > GEOMETRY_GAP_TURN_DEG:
            out.append(i)
    return out


def split_at_gaps(line: LineString) -> tuple[list[LineString], int]:
    """Куски трассы без швов между ними и число выброшенных рёбер.

    Кусок из одной точки выбрасывается: рисовать нечего.
    """
    coords = list(line.coords)
    gaps = gap_indices(coords)
    if not gaps:
        return [line], 0

    pieces: list[LineString] = []
    start = 0
    for index in gaps:
        piece = coords[start : index + 1]
        if len(piece) >= 2:
            pieces.append(LineString(piece))
        start = index + 1
    tail = coords[start:]
    if len(tail) >= 2:
        pieces.append(LineString(tail))
    return pieces, len(gaps)
