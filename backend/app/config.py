"""Единственное место, где живут пути и доменные константы.

Никаких чисел в коде расчёта: всё, что имеет физический смысл, объявлено здесь
и снабжено источником. Пути переопределяются переменными окружения — репозиторий
переезжает между машинами, а симлинк на выданный датасет у каждого свой.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ключи модели лежат в .env рядом с репозиторием и в git не попадают
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # без dotenv читаем только настоящее окружение
    pass


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
HEX_ACCESS_PARQUET = DATA_BUILD / "hex_access.parquet"
WALK_GRAPH_PKL = DATA_BUILD / "walk_graph.pkl"
HEXES_PARQUET = DATA_BUILD / "hexes.parquet"
# альтернативный слой населения: контрольная численность, разложенная по
# застройке OSM (scripts/03b_population_buildings.py)
HEXES_BUILDINGS_PARQUET = DATA_BUILD / "hexes_buildings.parquet"
BUILDINGS_PARQUET = DATA_BUILD / "buildings.parquet"
ROUTES_PARQUET = DATA_BUILD / "routes.parquet"
ROUTE_STOPS_PARQUET = DATA_BUILD / "route_stops.parquet"
SEGMENT_SPEED_PARQUET = DATA_BUILD / "segment_speed.parquet"
CITY_SPEED_FALLBACK_PARQUET = DATA_BUILD / "city_speed_fallback.parquet"
SEGMENT_TIME_PARQUET = DATA_BUILD / "segment_time.parquet"
HEADWAY_ACTUAL_PARQUET = DATA_BUILD / "headway_actual.parquet"
SEGMENT_ROUTES_PARQUET = DATA_BUILD / "segment_routes.parquet"
HOLES_PARQUET = DATA_BUILD / "holes.parquet"
ROAD_GRAPH_PKL = DATA_BUILD / "road_graph.pkl"

# --- системы координат --------------------------------------------------
# входные данные и весь обмен по API — в широте/долготе; для расстояний в метрах
# берётся местная UTM-зона, она вычисляется от границы города
WGS84 = "EPSG:4326"

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
# длина куска, на которые режется перегон перед поиском своей скорости:
# короче — дороже счёт, длиннее — перегон едет по скорости одной случайной точки
SPEED_CHUNK_M = 100.0
# доля длины перегона, покрытая реальными наблюдениями, начиная с которой
# перегон считается посчитанным по трафику, а не по медиане города
TRAFFIC_SOURCE_SHARE = 0.5
TRIP_GAP_MIN = 12.0  # ТЗ шаг 7: разрыв между транзакциями, разделяющий рейсы

# --- пороги валидации (ТЗ р. 7) -----------------------------------------
MIN_STOP_SPACING_M = 150.0
# Ниже этого расстояния два узла — один остановочный пункт, а не две остановки.
# В OSM платформа и место посадки бывают разными узлами с одной координатой:
# из 3 777 перегонов ровно 99 короче полуметра, следующий по длине — 5 м.
# Это особенность разметки, а не ошибка расчёта, и говорить о ней надо иначе.
SAME_POINT_SPACING_M = 5.0
MAX_ROUTE_LENGTH_KM = 45.0
DUPLICATION_ROUTE_COUNT = 5
FALLBACK_SHARE_WARN = 0.30
# сколько предупреждений о дублировании отдавать на маршрут: их бывают сотни,
# фронту нужен признак и примеры, а не полный список
MAX_DUPLICATION_WARNINGS = 20

# --- выгрузка геометрии всей сети ---------------------------------------
# бюджет одной загрузки при старте приложения: больше — фронт ждёт линии
ROUTE_GEOMETRY_MAX_BYTES = 1_500_000
# допуск Дугласа-Пекера в градусах: 1e-5 ≈ 1.1 м, меньше ширины самой линии
# на городском масштабе. Полная выгрузка 1.6 МБ, с этим допуском 0.5 МБ
ROUTE_SIMPLIFY_TOLERANCE_DEG = 1e-5
# знаков после запятой в координатах: 6 ≈ 11 см, точнее не имеет смысла
COORD_PRECISION = 6

# --- разрывы трассы ------------------------------------------------------
# Релейшен маршрута собран из путей; если пути нет, в геометрии остаётся
# прямая от конца одного куска до начала другого. Пороги подобраны по
# распределению длин рёбер (медиана 18 м, p99 217 м, p99.9 525 м) — см. app/trace.py.
# Длинное ребро считается швом, только если ломает направление: рёбра
# 150–400 м почти всегда настоящие прямые улицы с редкими узлами.
GEOMETRY_GAP_NEAR_M = 300.0
GEOMETRY_GAP_TURN_DEG = 40.0
# такой длины ребро — шов при любом направлении
GEOMETRY_GAP_FAR_M = 800.0
# начиная со скольких разрывов трасса называется неполной
INCOMPLETE_GEOMETRY_GAPS = 3

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
# как день недели называется в тексте («сценарий на пятницу», «пересчёт на субботу»)
WEEKDAY_NAMES = {"fri": "пятницу", "sat": "субботу", "sun": "воскресенье"}
TRANSACTION_DATE_TO_WEEKDAY = {
    "2026-05-01": "fri",
    "2026-05-02": "sat",
    "2026-05-03": "sun",
}

# дата среза слоя населения: называть цифру без даты нельзя, слой отстаёт от
# официальной статистики примерно на 18% (knowledge/facts.md §7)
POPULATION_LAYER_DATE = "01.11.2023"

# --- какой слой населения читает сервер ---------------------------------
# "kontur" — выданный Kontur Population как есть;
# "buildings" — контрольная численность, разложенная по застройке OSM.
# Kontur внутри города распределён неправдоподобно (facts.md §9), поэтому
# альтернатива существует; переключение — только этой константой.
POPULATION_SOURCE = os.environ.get("QATNOV_POPULATION_SOURCE", "buildings").strip().lower()
ACTIVE_HEXES_PARQUET = (
    HEXES_BUILDINGS_PARQUET if POPULATION_SOURCE == "buildings" else HEXES_PARQUET
)
# контрольная численность для слоя по застройке: постоянное население
# г. Ташкент на 01.01.2026, Нацкомстат (knowledge/facts.md §3)
POPULATION_CONTROL = float(os.environ.get("QATNOV_POPULATION_CONTROL", "3178100"))
POPULATION_CONTROL_DATE = "01.01.2026"
# корзины площади пятна здания для восстановления этажности: `building:levels`
# проставлен у 93% многоквартирных домов и лишь у 6% индивидуальных, причём
# у крупных. Внутри корзины размеченное и неразмеченное здание сопоставимы.
BUILDING_AREA_BINS_M2 = (80.0, 120.0, 180.0, 250.0, 400.0, 700.0, 1200.0, 3000.0)
# чем меряется жилая ёмкость гексагона в слое по застройке:
# "floor_area"  — сумма площади пола (пятно × этажность). От типа здания
#                 не зависит, поэтому не ломается там, где многоэтажку
#                 разметили как building=yes;
# "two_weight"  — многоквартирные × вес + индивидуальные × 1. Проще для
#                 объяснения, но расходится с первой на 45.5% населения.
BUILDING_CAPACITY_MODEL = os.environ.get(
    "QATNOV_BUILDING_CAPACITY_MODEL", "floor_area"
).strip().lower()
# дата, которую сервер обязан называть в оговорке об источниках. Она зависит
# от активного слоя: у Kontur это дата среза, у раскладки по застройке — дата
# контрольной численности. Подставлять дату Kontur под слой по застройке —
# это заявление, которое не соответствует данным.
ACTIVE_POPULATION_DATE = (
    POPULATION_LAYER_DATE if POPULATION_SOURCE == "kontur" else POPULATION_CONTROL_DATE
)

# --- YandexGPT ----------------------------------------------------------
# Yandex AI Studio, Foundation Models v1. Адрес и имена полей сверены с
# официальными proto yandex-cloud/cloudapi (см. knowledge/decisions.md).
# Ключи — только из окружения, в репозиторий не коммитятся.
LLM_ENDPOINT = os.environ.get(
    "QATNOV_YC_ENDPOINT", "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
)
LLM_MODEL = os.environ.get("QATNOV_YC_MODEL", "yandexgpt/latest")
LLM_API_KEY = os.environ.get("QATNOV_YC_API_KEY", "").strip()
LLM_FOLDER_ID = os.environ.get("QATNOV_YC_FOLDER_ID", "").strip()
# рубильник для демонстрации запасного пути и для прогона гейтов без сети
LLM_DISABLED = os.environ.get("QATNOV_LLM_DISABLED", "").strip().lower() in {"1", "true", "yes"}
# строгий JSON: "schema" — jsonSchema из proto, "object" — jsonObject
LLM_JSON_MODE = os.environ.get("QATNOV_YC_JSON_MODE", "schema").strip().lower()
# зал показа, связь ненадёжна: ждём модель ровно столько и уходим на запасной путь
LLM_TIMEOUT_SEC = float(os.environ.get("QATNOV_LLM_TIMEOUT_SEC", "5"))
# разбор фразы — без творчества; пересказ чисел — почти без него
LLM_TEMPERATURE_PARSE = 0.0
LLM_TEMPERATURE_EXPLAIN = 0.2
LLM_MAX_TOKENS_PARSE = 400
LLM_MAX_TOKENS_EXPLAIN = 700
# кэш ответов на время демонстрации: одна фраза не должна стоить двух запросов
LLM_CACHE_SIZE = 256
# ассистент: температура пересказа та же, что у объяснения, а выбор инструмента —
# без творчества, как разбор фразы
LLM_MAX_TOKENS_TOOL = 300
# Замер 08.08: YandexGPT пишет примерно 200 знаков в секунду, и время ответа
# растёт линейно с его длиной — 346 знаков это 1.6 с, 893 знака уже 4.6 с при
# таймауте 5 с. Размер промпта на время почти не влияет. Поэтому ограничение
# стоит на длине ответа, а не на объёме входа: 260 токенов — это около 600
# знаков русского текста, то есть 2–3 секунды.
# Потолок стоит с запасом над той длиной, которую просим словами: ответ,
# обрезанный на середине фразы, человеку не отдаётся, и модель, чуть
# превысившая просьбу, не должна из-за этого терять весь ответ.
LLM_MAX_TOKENS_ANSWER = 340
# длина, которую просим соблюдать: около трёх секунд генерации
ANSWER_MAX_CHARS = 600

# --- ассистент ----------------------------------------------------------
# сколько раз подряд ассистент имеет право дёрнуть инструмент. Ответ обязан
# появиться в любом случае, поэтому цикл ограничен числом, а не условием выхода
ASSISTANT_MAX_STEPS = 3
# общий бюджет одного вопроса. Проверяется перед каждым обращением к модели:
# не укладываемся — дальше идём детерминированным путём и всё равно отвечаем
# Порог сравнивается с остатком перед вызовом, поэтому худший случай — вызов,
# начатый за мгновение до порога: бюджет минус таймаут плюс таймаут. При 8 и
# таймауте 5 это 8 секунд, что укладывается в требование «меньше десяти».
ASSISTANT_BUDGET_SEC = float(os.environ.get("QATNOV_ASSISTANT_BUDGET_SEC", "8"))
# сколько строк отдавать в ответах инструментов. Больше — модель не переварит,
# а охрана чисел разрешит слишком много
ASSISTANT_ROUTES_LIMIT = 5
# сколько знаков уже посчитанного показывать модели при выборе следующего шага
ASSISTANT_DONE_PROMPT_CHARS = 4000
ASSISTANT_HOLES_LIMIT = 5
ASSISTANT_OPTIONS_LIMIT = 3

# --- признаки маршрута, требующего внимания -----------------------------
# фактический интервал хуже планового во столько раз — маршрут не держит план
ATTENTION_HEADWAY_RATIO = 1.5
# доля перегонов маршрута, которые он делит с DUPLICATION_ROUTE_COUNT и более
# маршрутами: выше этой — маршрут в основном дублирует чужие линии
ATTENTION_DUPLICATION_SHARE = 0.5
# вес признака в общей оценке. Веса разные, потому что признаки разной тяжести:
# невыполненный интервал бьёт по пассажиру сегодня, дублирование — это стоимость
ATTENTION_WEIGHTS = {
    "headway_gap": 1.0,
    "vehicles_short": 0.8,
    "duplication": 0.6,
    "route_too_long": 0.5,
    "stops_too_close": 0.3,
}
# --- границы физически возможного ---------------------------------------
# Значения за этими границами — дефекты исходных данных, а не свойства сети.
# Каждый порог посажен на измеренное распределение (замеры 08.08, fri):
# длина маршрута — медиана 18.8 км, реальный максимум 32.6 км;
# расстояние между соседними остановками — медиана 450 м, p99.9 2472 м;
# время хода в один конец — медиана 48 мин, реальный максимум 72 мин;
# фактический интервал — от 2.5 до 60 мин; машин на линии — от 1 до 17.
# Записи не удаляются, а помечаются: данные остаются, доверие к ним — нет.

# поперечник города по административной границе ~40 км, норматив предупреждения
# 45 км (MAX_ROUTE_LENGTH_KM). Свыше 60 км в один конец — дефект трассы
IMPOSSIBLE_ROUTE_KM = 60.0
# длина трассы против суммы её же перегонов. Медиана отношения 1.00,
# межквартильный размах 0.95–1.04: расхождение вдвое — это уже не неточность
# трассы, а несоответствие геометрии цепочке остановок
GEOMETRY_CHAIN_RATIO_MIN = 0.5
GEOMETRY_CHAIN_RATIO_MAX = 2.0
# соседние остановки дальше этого — в цепочке пропущены остановки.
# Норматив расстановки 400–600 м, p99.9 наблюдений 2472 м
IMPOSSIBLE_STOP_GAP_M = 3000.0
# то же на перегонах времени хода: перегон длиннее — трасса ушла не туда
IMPOSSIBLE_SEGMENT_M = 5000.0
# время хода в один конец. Оборот при 120 мин — больше четырёх часов,
# городской маршрут так не работает
IMPOSSIBLE_ONE_WAY_MIN = 120.0
# интервал: ноль ломает ceil(оборот / интервал), свыше двух часов городской
# маршрут не ходит (наблюдаемый максимум факта — 60 мин)
IMPOSSIBLE_HEADWAY_MIN = 120.0
# машин на линии: наблюдаемый максимум 17
IMPOSSIBLE_VEHICLES = 100

# --- остановка в стороне от застройки -----------------------------------
# Радиус вокруг остановки, в котором ищется жильё: половина норматива пешей
# доступности. Порог посажен на действующую сеть: у 99.5% обслуживаемых
# остановок в этом радиусе не меньше 16 жилых зданий (1-й процентиль).
# Ниже порога прирост населения даёт не остановка, а дальний край гексагона:
# ячейка H3 r8 — 0.88 км², и люди в ней могут жить в 400 м от остановки.
HOUSING_RADIUS_M = 300.0
MIN_HOUSING_BUILDINGS = 10

# продление к остановке дальше этой доли длины маршрута — уже другой маршрут
IMPROVEMENT_MAX_LENGTH_SHARE = 0.25
# больше двух дополнительных машин на одно продление не объяснить
IMPROVEMENT_MAX_EXTRA_VEHICLES = 2
# сколько ближайших к конечной необслуживаемых остановок пробовать
IMPROVEMENT_CANDIDATES = 5

# --- доступ из браузера -------------------------------------------------
# фронтенд живёт на своём порту, поэтому запросы к ядру — кросс-доменные.
# Список задаётся переменной окружения через запятую; по умолчанию — dev-сервер Vite.
CORS_ORIGINS = tuple(
    origin.strip()
    for origin in os.environ.get(
        "QATNOV_CORS_ORIGINS", "http://localhost:5175,http://127.0.0.1:5175"
    ).split(",")
    if origin.strip()
)

# --- собранный фронтенд -------------------------------------------------
# На хостинге интерфейс и ядро живут одним адресом: собранный фронтенд лежит
# рядом и раздаётся тем же сервером. Каталога нет (обычная разработка, где
# фронт поднят Vite на своём порту) — раздача просто не включается.
STATIC_DIR = _path("QATNOV_STATIC_DIR", ROOT / "static")

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
