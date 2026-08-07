# Архитектура

Собрано в ночь 07.08.2026. Числа и проверки — `NIGHT_REPORT.md`.

## Принцип

Весь тяжёлый счёт делается офлайн скриптами и складывается в parquet. Сервер при старте
загружает parquet в память и в момент запроса только читает и делает операции над множествами.
Базы данных нет, состояния нет: сценарий приходит целиком в теле запроса.

## Поток данных

```
data/raw/      выданный датасет (симлинк, read-only)
data/external/ открытые источники: OSM PBF (Geofabrik), Kontur Population (HDX)
      │
      ▼  scripts/00..09, идемпотентные, по одному на шаг
data/build/    parquet + pickle графа
      │
      ▼  app/store.py — загрузка в память один раз при старте
app/           FastAPI
```

## Шаги пайплайна

| Скрипт | Что делает | Выход |
|---|---|---|
| `00_boundary` | граница города из OSM (`admin_level=4`) | `tashkent_boundary.geojson` |
| `01_stops` | остановки Яндекса + OSM, дедуп 25 м | `stops.parquet` |
| `02_walk_graph` | пешеходный граф, зоны 500 м, расстояние до ближайшей остановки | `walk_graph.pkl`, `stop_hexes.parquet`, `hex_access.parquet` |
| `03_population` | Kontur, обрезка по границе с весом по площади | `hexes.parquet` |
| `04_routes` | релейшены OSM + реестр egov | `routes.parquet`, `route_stops.parquet` |
| `05_speeds` | скорости по часам из трафика Яндекса | `segment_speed.parquet`, `city_speed_fallback.parquet` |
| `06_segment_time` | время хода по перегонам по часам — ядро | `segment_time.parquet` |
| `07_headway` | фактический интервал из транзакций | `headway_actual.parquet` |
| `08_parallel` | сколько маршрутов на перегоне | `segment_routes.parquet` |
| `09_holes` | дыры покрытия | `holes.parquet` |

`scripts/run_all.sh` прогоняет всё по порядку. `scripts/verify_gates.py` — приёмка по живому API.

## Модули

| Модуль | Ответственность |
|---|---|
| `app/config.py` | пути и доменные константы, каждая с источником. Единственное место с числами |
| `app/walkgraph.py` | граф в CSR, Дейкстра с отсечкой и многоисточниковая |
| `app/store.py` | загрузка артефактов; отсутствующий артефакт — `None`, не заглушка |
| `app/coverage.py` | PNT-500, PNFT-15, T-median |
| `app/schedule.py` | движок расписания: час берётся по факту доезда до перегона |
| `app/scenario.py` | операции сценария, пересчёт по разнице множеств |
| `app/validation.py` | предупреждения планировщику |
| `app/textnorm.py`, `app/search.py` | транслитерация и поиск |
| `app/main.py` | эндпоинты |

## API

```
GET  /api/meta                      константы, размеры, источники, чего ещё нет
GET  /api/stops                     GeoJSON всех остановок
GET  /api/routes                    список маршрутов
GET  /api/routes/{num}              геометрия, порядок остановок, время хода по часам
GET  /api/routes/{num}/schedule     расписание прибытия, оборот, нужное число машин
GET  /api/baseline                  PNT-500, PNFT-15, T-median, гексагоны
POST /api/scenario                  дельта в людях по списку операций
GET  /api/holes                     дыры покрытия
GET  /api/segments/parallel         k и n для разведения линий на карте
GET  /api/search                    поиск с узбекской транслитерацией
GET  /api/export/schedule           CSV
GET  /api/export/route              GeoJSON
```

Не сделано (в ТЗ помечено необязательным): `/api/routes/{num}/buses`, `/api/explain`.
