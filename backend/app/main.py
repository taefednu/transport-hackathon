"""FastAPI QATNOV. Сервер stateless: читает parquet из памяти и считает по запросу."""

from __future__ import annotations

import csv
import io
import json
from contextlib import asynccontextmanager

import polars as pl
import shapely
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import (
    assistant as assistant_mod,
    config,
    coverage,
    dataquality,
    diagnostics,
    explain as explain_mod,
    llm,
    nlparse,
    scenario,
    schedule,
    search as search_mod,
    tools,
    validation,
)
from app import trace
from app.store import Store, load

STATE: dict[str, Store] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    STATE["store"] = load()
    STATE["search_index"] = search_mod.build_index(STATE["store"])
    # то, что иначе посчитается внутри первого же запроса и попадёт в его время:
    # пометки качества данных, застройка вокруг остановок, диагностика на
    # утренний пик и потолок прироста по кандидатам. Вместе это около 0.5 с —
    # на старте они бесплатны, в ответе ассистента они заметны
    dataquality.flags(STATE["store"])
    dataquality.housing_near_stops(STATE["store"])
    diagnostics.compute(STATE["store"], config.WEEKDAY_TYPES[0], 8)
    tools.warm(STATE["store"])
    yield
    STATE.clear()


app = FastAPI(title="QATNOV", lifespan=lifespan)

# фронтенд ходит с другого порта: без этого браузер режет любой запрос к ядру
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(config.CORS_ORIGINS),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def store() -> Store:
    return STATE["store"]


def check_weekday(weekday: str) -> str:
    """День недели сверяется со справочником на входе в каждый эндпоинт.

    Незнакомый день не бросается сам собой: он уходит в фильтр по колонке
    `weekday_type`, тот не находит ни одной строки, и ответ приходит пустым —
    сценарий «ничего не изменилось», расписание из одних прочерков. Пустота
    выглядит как ответ, а это опечатка в запросе.
    """
    if weekday not in config.WEEKDAY_TYPES:
        raise HTTPException(422, f"weekday должен быть одним из {config.WEEKDAY_TYPES}")
    return weekday


def check_hour(value: object) -> int:
    """Час из тела запроса. В строке запроса его проверяет FastAPI, в теле — никто.

    `int("вчера")` бросало ValueError и превращалось в 500 «Internal Server
    Error», а час 99 доезжал до расчёта и возвращался в ответе как настоящий.
    """
    try:
        hour = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise HTTPException(422, f"час должен быть числом от 0 до 23, а пришло {value!r}") from None
    if not 0 <= hour <= 23:
        raise HTTPException(422, f"час должен быть от 0 до 23, а пришло {hour}")
    return hour


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
                "license": "предоставлено организаторами хакатона",
            },
            {
                "name": "OpenStreetMap",
                "detail": "локальный дамп Geofabrik uzbekistan-latest",
                "license": "ODbL",
            },
            # источник населения зависит от config.POPULATION_SOURCE: объявлять
            # Kontur, когда сервер читает раскладку по застройке, нельзя
            {
                "name": "Kontur Population",
                "detail": f"H3 r8, срез {config.POPULATION_LAYER_DATE}",
                "license": "CC BY",
            }
            if config.POPULATION_SOURCE == "kontur"
            else {
                "name": "Население по застройке OSM",
                "detail": (
                    f"численность Нацкомстата на {config.POPULATION_CONTROL_DATE} "
                    f"({config.POPULATION_CONTROL:,.0f}), разложенная по зданиям; "
                    f"модель ёмкости — {config.BUILDING_CAPACITY_MODEL}"
                ),
                "license": "ODbL (геометрия) + официальная статистика",
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


@app.get("/api/network/geometry")
def network_geometry() -> Response:
    """Линии всей сети одной коллекцией: фронт грузит их один раз при старте.

    Только геометрия — без остановок и без времени хода. Отдаются те 125
    направлений из 223, у которых геометрия вообще есть; у остальных
    `quality=approximate` и линии в базе нет.

    Путь намеренно не `/api/routes/geometry`: этот сегмент занят параметром
    `{route_num}`, а номера маршрутов бывают буквенными («13Т»), то есть сузить
    параметр нечем. Отдельный сегмент снимает зависимость от порядка регистрации
    маршрутов — чинить такое порядком ненадёжно.

    Полная выгрузка весит 1.6 МБ, это больше бюджета одной загрузки, поэтому
    линии упрощаются по Дугласу-Пекеру и в ответе стоит признак `simplified`.
    """
    st = store()
    if st.routes is None:
        raise HTTPException(503, "нет data/build/routes.parquet (шаг 4 пайплайна)")

    rows = st.routes.filter(
        pl.col("geometry_wkt").is_not_null() & (pl.col("geometry_wkt") != "")
    ).select("route_num", "direction", "quality", "geometry_wkt").to_dicts()

    def collection(tolerance: float | None) -> dict:
        """Куски трассы без швов.

        Разрывы ищутся до упрощения: Дуглас-Пекер на прямой улице оставляет
        две точки в сотнях метров друг от друга, и по упрощённой линии шов
        от настоящей прямой уже не отличить.
        """
        features = []
        directions_with_gaps = 0
        for row in rows:
            pieces, gaps = trace.split_at_gaps(shapely.from_wkt(row["geometry_wkt"]))
            if gaps:
                directions_with_gaps += 1
            for index, piece in enumerate(pieces):
                if tolerance:
                    piece = piece.simplify(tolerance, preserve_topology=False)
                if len(piece.coords) < 2:
                    continue
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [
                                [round(x, config.COORD_PRECISION), round(y, config.COORD_PRECISION)]
                                for x, y in piece.coords
                            ],
                        },
                        "properties": {
                            "route_num": row["route_num"],
                            "direction": row["direction"],
                            "quality": row["quality"],
                            "piece": index,
                            "gaps": gaps,
                        },
                    }
                )
        return {
            "type": "FeatureCollection",
            "count": len(features),
            "simplified": tolerance is not None,
            "tolerance_deg": tolerance,
            "directions_total": len(rows),
            "directions_with_gaps": directions_with_gaps,
            "gap_near_m": config.GEOMETRY_GAP_NEAR_M,
            "gap_far_m": config.GEOMETRY_GAP_FAR_M,
            "features": features,
        }

    body = json.dumps(collection(None), ensure_ascii=False)
    if len(body.encode()) > config.ROUTE_GEOMETRY_MAX_BYTES:
        body = json.dumps(
            collection(config.ROUTE_SIMPLIFY_TOLERANCE_DEG), ensure_ascii=False
        )
    return Response(content=body, media_type="application/json; charset=utf-8")


@app.get("/api/routes/{route_num}")
def route_detail(
    route_num: str,
    direction: str = Query(default="fwd"),
    weekday: str = Query(default=config.WEEKDAY_TYPES[0]),
) -> dict:
    check_weekday(weekday)
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
    gap_indices: list[int] = []
    if route.get("geometry_wkt"):
        line = shapely.from_wkt(route["geometry_wkt"])
        coords = [list(c) for c in line.coords]
        gap_indices = trace.gap_indices([(c[0], c[1]) for c in coords])
        geometry = {"type": "LineString", "coordinates": coords}

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
        # индексы рёбер-швов: ребро i идёт от точки i к точке i+1.
        # Фронт по ним рвёт линию и не ставит на шов ни щитки, ни машины.
        "geometry_gap_indices": gap_indices,
        "geometry_gaps": len(gap_indices),
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
    headway_min: float = Query(default=None, ge=config.MIN_HEADWAY_MIN),
    n_vehicles: int = Query(default=None, ge=0),
) -> dict:
    check_weekday(weekday)
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


@app.get("/api/stops/{stop_id}/walkzone")
def stop_walkzone(
    stop_id: str,
    limit_m: float = Query(default=None, gt=0, le=2000),
) -> dict:
    """Зона пешей доступности остановки — по сети, а не кругом на карте.

    Возвращает рёбра пешеходного графа, до которых дошли за `limit_m`, и
    расстояние, на котором каждое ребро достигнуто: по нему фронт рисует рост
    зоны от остановки, как требует §14 спеки. Круг радиусом 500 м здесь был бы
    враньём — пешеход ходит по улицам, а не по прямой.
    """
    st = store()
    row = st.stops.filter(pl.col("stop_id") == stop_id)
    if row.is_empty():
        raise HTTPException(404, f"остановки {stop_id} нет в базе")

    limit = float(limit_m or config.WALK_LIMIT_M)
    source = int(row["walk_node_id"][0])
    graph = st.walk_graph
    reached = graph.reachable(source, limit)

    edges = []
    for node, d_from in reached.items():
        start, end = graph.indptr[node], graph.indptr[node + 1]
        for k in range(start, end):
            neighbour = int(graph.indices[k])
            d_to = reached.get(neighbour)
            if d_to is None or d_to < d_from:
                continue
            # ребро между равноудалёнными вершинами отдаём один раз
            if d_to == d_from and neighbour < node:
                continue
            edges.append(
                {
                    "coords": [
                        [round(float(graph.lon[node]), config.COORD_PRECISION),
                         round(float(graph.lat[node]), config.COORD_PRECISION)],
                        [round(float(graph.lon[neighbour]), config.COORD_PRECISION),
                         round(float(graph.lat[neighbour]), config.COORD_PRECISION)],
                    ],
                    "d": round(d_to, 1),
                }
            )

    # население, до которого от этой остановки можно дойти пешком
    people = None
    if st.stop_hexes is not None:
        cells = st.stop_hexes.filter(
            (pl.col("stop_id") == stop_id) & (pl.col("walk_m") <= limit)
        )["h3_id"].to_list()
        if cells:
            people = float(
                st.hexes.filter(pl.col("h3_id").is_in(cells))["population"].sum()
            )

    return {
        "stop_id": stop_id,
        "limit_m": limit,
        "nodes": len(reached),
        "people": people,
        "edges": edges,
    }


@app.get("/api/baseline")
def baseline(
    weekday: str = Query(default=config.WEEKDAY_TYPES[0]),
    hour: int = Query(default=8, ge=0, le=23),
) -> dict:
    check_weekday(weekday)
    return coverage.baseline(store(), weekday, hour)


@app.get("/api/diagnostics/attention")
def diagnostics_attention(
    weekday: str = Query(default=config.WEEKDAY_TYPES[0]),
    hour: int = Query(default=8, ge=0, le=23),
    limit: int = Query(default=12, ge=1, le=50),
) -> dict:
    """Маршруты, требующие внимания, — ранжированный список без модели.

    Тот же расчёт, что зовёт ассистент, но доступный напрямую: диагностика
    нужна и тогда, когда сети нет или ключа модели нет.
    """
    check_weekday(weekday)
    return diagnostics.attention(store(), weekday, hour, limit)


@app.get("/api/routes/{route_num}/options")
def route_options(
    route_num: str,
    weekday: str = Query(default=config.WEEKDAY_TYPES[0]),
    hour: int = Query(default=8, ge=0, le=23),
    direction: str | None = Query(default=None),
) -> dict:
    """Варианты продления маршрута из перебора. Ничего не применяет."""
    check_weekday(weekday)
    try:
        return tools.route_options(
            store(),
            STATE["search_index"],
            {"route_num": route_num, "weekday": weekday, "hour": hour, "direction": direction},
        )
    except tools.ToolError as exc:
        # это не сбой сервера: подбор не делается по названной причине,
        # и причину надо показать человеку, а не прятать за пятисоткой
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/holes/{h3}/options")
def hole_options(
    h3: str,
    weekday: str = Query(default=config.WEEKDAY_TYPES[0]),
    hour: int = Query(default=8, ge=0, le=23),
) -> dict:
    """Чем закрыть конкретную дыру покрытия и какой ценой."""
    check_weekday(weekday)
    try:
        return tools.hole_options(
            store(), STATE["search_index"], {"h3": h3, "weekday": weekday, "hour": hour}
        )
    except tools.ToolError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/headways")
def headways(
    weekday: str = Query(default=config.WEEKDAY_TYPES[0]),
    hour: int = Query(default=8, ge=0, le=23),
) -> dict:
    """Фактический интервал и число машин по всем маршрутам за один час.

    Отдельно от `/api/routes`, потому что список маршрутов от часа не зависит
    и грузится один раз, а эти два числа меняются при каждом сдвиге часа.
    Маршруты, по которым за этот час нет ни одного рейса в транзакциях,
    в ответе отсутствуют — их интервал неизвестен, а не равен нулю.
    """
    check_weekday(weekday)
    st = store()
    if st.headway_actual is None:
        raise HTTPException(503, "нет data/build/headway_actual.parquet (шаг 6 пайплайна)")
    rows = st.headway_actual.filter(
        (pl.col("weekday_type") == weekday) & (pl.col("hour") == hour)
    ).select("route_num", "actual_headway_min", "n_vehicles", "n_boardings")
    return {
        "weekday": weekday,
        "hour": hour,
        "count": rows.height,
        "routes": {r["route_num"]: r for r in rows.to_dicts()},
    }


@app.post("/api/scenario")
def post_scenario(body: dict) -> dict:
    weekday = body.get("weekday", config.WEEKDAY_TYPES[0])
    hour = check_hour(body.get("hour", 8))
    ops = body.get("ops") or []
    check_weekday(weekday)
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


@app.get("/api/data-quality")
def data_quality() -> dict:
    """Что в исходных данных физически невозможно и чему нельзя доверять.

    Записи не удалены: маршрут открывается и показывает свои числа. Из
    ранжирования диагностики и из подбора рекомендаций он исключён.
    """
    return dataquality.report(store())


@app.post("/api/assistant")
def assistant(body: dict) -> dict:
    """Вопрос словами → вызовы инструментов → ответ по посчитанному.

    Ассистент ничего не применяет: сценарии он возвращает действием
    `apply_scenario` в том же виде, который принимает POST /api/scenario,
    а решение принимает человек.
    """
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(422, "нужно поле text с вопросом")
    hour = body.get("hour")
    return assistant_mod.ask(
        store(),
        STATE["search_index"],
        text,
        weekday=body.get("weekday"),
        hour=None if hour is None else check_hour(hour),
    )


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
    headway_min: float = Query(default=None, ge=config.MIN_HEADWAY_MIN),
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


# Раздача собранного фронтенда — последней строкой файла и не случайно.
# Монтирование на «/» перехватывает любой путь, поэтому оно обязано быть
# зарегистрировано после всех эндпоинтов: Starlette выбирает первый
# подошедший маршрут, а этот подходит ко всему.
if config.STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="ui")
