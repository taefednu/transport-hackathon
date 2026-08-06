"""Единственное место, где живут пути и доменные константы.

Никаких чисел в коде расчёта: всё, что имеет физический смысл, объявлено здесь
и снабжено источником. Пути переопределяются переменными окружения — репозиторий
переезжает между машинами, а симлинк на выданный датасет у каждого свой.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _path(env_name: str, default: Path) -> Path:
    raw = os.environ.get(env_name)
    return Path(raw).expanduser().resolve() if raw else default


# --- пути ---------------------------------------------------------------
DATA_RAW = _path("QATNOV_DATA_RAW", ROOT / "data" / "raw")
DATA_EXTERNAL = _path("QATNOV_DATA_EXTERNAL", ROOT / "data" / "external")
DATA_BUILD = _path("QATNOV_DATA_BUILD", ROOT / "data" / "build")

# файлы выданного датасета (структура: data/raw/data/<город|источник>/)
STATIONS_CSV = DATA_RAW / "data" / "yandex" / "stations.csv"
TRAFFIC_CSV = DATA_RAW / "data" / "yandex" / "traffic_dedup.csv"
TRANSACTIONS_CSV = DATA_RAW / "data" / "tashkent" / "RFC_167_tash_hackaton.csv"
EGOV_ROUTES_JSON = DATA_RAW / "reference" / "egov_tashkent_sched_fare.json"

# внешние открытые источники
OSM_PBF = DATA_EXTERNAL / "uzbekistan-latest.osm.pbf"
KONTUR_GPKG = DATA_EXTERNAL / "kontur_population_UZ.gpkg"

# артефакты пайплайна
BOUNDARY_GEOJSON = DATA_BUILD / "tashkent_boundary.geojson"
STOPS_PARQUET = DATA_BUILD / "stops.parquet"
STOP_HEXES_PARQUET = DATA_BUILD / "stop_hexes.parquet"
WALK_GRAPH_PKL = DATA_BUILD / "walk_graph.pkl"
HEXES_PARQUET = DATA_BUILD / "hexes.parquet"
ROUTES_PARQUET = DATA_BUILD / "routes.parquet"
ROUTE_STOPS_PARQUET = DATA_BUILD / "route_stops.parquet"
SEGMENT_SPEED_PARQUET = DATA_BUILD / "segment_speed.parquet"
CITY_SPEED_FALLBACK_PARQUET = DATA_BUILD / "city_speed_fallback.parquet"
SEGMENT_TIME_PARQUET = DATA_BUILD / "segment_time.parquet"
HEADWAY_ACTUAL_PARQUET = DATA_BUILD / "headway_actual.parquet"
SEGMENT_ROUTES_PARQUET = DATA_BUILD / "segment_routes.parquet"
HOLES_PARQUET = DATA_BUILD / "holes.parquet"
ROAD_GRAPH_PKL = DATA_BUILD / "road_graph.pkl"

# --- доменные константы -------------------------------------------------
# порог пешей доступности остановки
WALK_LIMIT_M = 500.0  # СНиП 2.07.01-89 п. 6.29
# интервал, начиная с которого маршрут считается «частым»
FREQUENT_HEADWAY_MIN = 15.0  # методика People Near Transit
# разрешение гексагональной сетки: в нём же поставляется Kontur Population
H3_RESOLUTION = 8
# пешеходная скорость для пересчёта расстояния в минуты
WALK_SPEED_KMH = 4.8  # ТЗ р. 5
# стоянка на остановке
DWELL_SEC = 20  # ТЗ р. 3, шаг 6
# отстой на конечной
LAYOVER_MIN = 5  # ТЗ р. 4

# --- пороги пайплайна ---------------------------------------------------
STOP_DEDUP_M = 25.0  # ТЗ шаг 1: две остановки ближе этого — одна и та же
TRAFFIC_MATCH_M = 100.0  # ТЗ шаг 6: радиус поиска участка трафика
TRIP_GAP_MIN = 12.0  # ТЗ шаг 7: разрыв между транзакциями, разделяющий рейсы

# --- пороги валидации (ТЗ р. 7) -----------------------------------------
MIN_STOP_SPACING_M = 150.0
MAX_ROUTE_LENGTH_KM = 45.0
DUPLICATION_ROUTE_COUNT = 5
FALLBACK_SHARE_WARN = 0.30

# --- идентификация города ----------------------------------------------
# граница берётся из OSM: relation boundary=administrative нужного уровня
CITY_ADMIN_LEVEL = os.environ.get("QATNOV_CITY_ADMIN_LEVEL", "4")
CITY_NAMES = tuple(
    n.strip()
    for n in os.environ.get(
        "QATNOV_CITY_NAMES", "Toshkent shahri|Tashkent|Ташкент|Тошкент шаҳри"
    ).split("|")
    if n.strip()
)

# --- окно данных --------------------------------------------------------
# соответствие дат выданных транзакций дням недели (ТЗ шаг 7)
WEEKDAY_TYPES = ("fri", "sat", "sun")
TRANSACTION_DATE_TO_WEEKDAY = {
    "2026-05-01": "fri",
    "2026-05-02": "sat",
    "2026-05-03": "sun",
}

# --- сеть -------------------------------------------------------------
# типы дорог, по которым ходит пешеход (ТЗ шаг 2)
WALK_HIGHWAY_TYPES = frozenset(
    {
        "footway",
        "path",
        "pedestrian",
        "living_street",
        "residential",
        "service",
        "steps",
        "track",
        "unclassified",
        "tertiary",
        "secondary",
    }
)
# типы дорог, по которым едет автобус
ROAD_HIGHWAY_TYPES = frozenset(
    {
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
        "unclassified",
        "residential",
        "living_street",
        "service",
    }
)
