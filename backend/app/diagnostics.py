"""Какие маршруты требуют внимания и по каким признакам.

Считается по тем же артефактам, что и всё остальное: плановый интервал из
реестра, фактический — из транзакций, дублирование — из перегонов, длина и
расстояния между остановками — из цепочки. Никаких оценок «хорошо/плохо»
здесь нет: есть числа и пороги, объявленные в `config`.

Ранжирование — взвешенная сумма нормированных превышений порога, а не
количество сработавших признаков: маршрут, который не держит интервал вдвое,
важнее маршрута, у которого две остановки стоят слишком близко.

Считается один раз на (день, час) и держится в памяти процесса: артефакты за
время жизни сервера не меняются, а вопрос про проблемные маршруты на показе
задают несколько раз подряд.
"""

from __future__ import annotations

import math

import numpy as np
import polars as pl

from app import config, dataquality
from app.store import Store

_CACHE: dict[tuple[str, int], list[dict]] = {}


def _fallback_share_by_route(store: Store, weekday: str) -> dict[str, float]:
    """Доля перегонов маршрута, посчитанных по медиане скорости города."""
    if store.segment_time is None:
        return {}
    per_segment = (
        store.segment_time.filter(pl.col("weekday_type") == weekday)
        .group_by("route_num", "direction", "seq_from")
        .agg(pl.col("source").first())
    )
    grouped = per_segment.group_by("route_num").agg(
        (pl.col("source") == "fallback").mean().alias("share")
    )
    return dict(zip(grouped["route_num"].to_list(), grouped["share"].to_list()))


def _one_way_min_by_route(store: Store, weekday: str, hour: int) -> dict[str, float]:
    """Время хода в один конец в этот час, минуты.

    Это оценка при постоянном часе, а не прогон рейса через движок расписания:
    там час берётся по факту доезда до перегона, и на длинном маршруте это
    другое число. Для ранжирования разница несущественна, но называть её
    расписанием нельзя — поле так и называется, `one_way_min_at_hour`.
    """
    if store.segment_time is None:
        return {}
    grouped = (
        store.segment_time.filter(
            (pl.col("weekday_type") == weekday) & (pl.col("hour") == hour)
        )
        .group_by("route_num", "direction")
        .agg(pl.col("travel_sec").sum().alias("sec"))
        .group_by("route_num")
        .agg(pl.col("sec").max().alias("sec"))
    )
    return {
        num: sec / 60.0
        for num, sec in zip(grouped["route_num"].to_list(), grouped["sec"].to_list())
    }


def _duplication_share(store: Store) -> dict[str, float]:
    if store.segment_routes is None:
        return {}
    grouped = store.segment_routes.group_by("route_num").agg(
        (pl.col("n") >= config.DUPLICATION_ROUTE_COUNT).mean().alias("share")
    )
    return dict(zip(grouped["route_num"].to_list(), grouped["share"].to_list()))


def _close_stops_share(store: Store) -> dict[str, tuple]:
    """Доля перегонов, где остановки стоят слишком близко друг к другу.

    Перегоны короче SAME_POINT_SPACING_M сюда не входят. Это не две остановки
    в паре метров, а один остановочный пункт: в OSM платформа и место посадки
    размечены разными узлами с одной координатой. Таких пар 99 из 3 777, и
    считать их решением планировщика нельзя — иначе маршрут попадает в список
    проблемных за особенность чужой разметки. Их число возвращается отдельно,
    чтобы объяснение могло назвать и его.
    """
    if store.route_stops is None:
        return {}
    index = {stop_id: i for i, stop_id in enumerate(store.stops["stop_id"].to_list())}
    rows = store.route_stops.sort("route_num", "direction", "seq")
    positions = np.array(
        [index.get(s, -1) for s in rows["stop_id"].to_list()], dtype=np.int64
    )
    known = positions >= 0
    xy = np.full((len(positions), 2), np.nan)
    xy[known] = store.stop_xy[positions[known]]

    table = rows.with_columns(
        pl.Series("x", xy[:, 0]), pl.Series("y", xy[:, 1])
    ).with_columns(
        (
            (pl.col("x") - pl.col("x").shift(1).over("route_num", "direction")).pow(2)
            + (pl.col("y") - pl.col("y").shift(1).over("route_num", "direction")).pow(2)
        )
        .sqrt()
        .alias("gap_m")
    )
    close = (pl.col("gap_m") >= config.SAME_POINT_SPACING_M) & (
        pl.col("gap_m") < config.MIN_STOP_SPACING_M
    )
    grouped = table.drop_nulls("gap_m").group_by("route_num").agg(
        close.mean().alias("share"),
        close.sum().alias("n"),
        (pl.col("gap_m") < config.SAME_POINT_SPACING_M).sum().alias("n_same_point"),
    )
    return {
        num: (share, int(n), int(same))
        for num, share, n, same in zip(
            grouped["route_num"].to_list(),
            grouped["share"].to_list(),
            grouped["n"].to_list(),
            grouped["n_same_point"].to_list(),
        )
    }


def _routes_table(store: Store) -> list[dict]:
    """Маршрут целиком: направления сведены, длина и число остановок — максимум."""
    return (
        store.routes.group_by("route_num")
        .agg(
            pl.col("name").first().alias("name"),
            pl.col("planned_headway_min").first().alias("planned_headway_min"),
            pl.col("length_km").max().alias("length_km"),
            pl.col("n_stops").max().alias("n_stops"),
            pl.col("quality").min().alias("quality"),
            pl.col("direction").alias("directions"),
        )
        .sort("route_num")
        .to_dicts()
    )


def compute(store: Store, weekday: str, hour: int) -> list[dict]:
    """Все маршруты с признаками и оценкой, худшие в начале."""
    key = (weekday, hour)
    if key in _CACHE:
        return _CACHE[key]
    if store.routes is None:
        return []

    fallback = _fallback_share_by_route(store, weekday)
    one_way = _one_way_min_by_route(store, weekday, hour)
    duplication = _duplication_share(store)
    close_stops = _close_stops_share(store)

    actual: dict[str, dict] = {}
    if store.headway_actual is not None:
        rows = store.headway_actual.filter(
            (pl.col("weekday_type") == weekday) & (pl.col("hour") == hour)
        )
        actual = {row["route_num"]: row for row in rows.iter_rows(named=True)}

    out: list[dict] = []
    for route in _routes_table(store):
        num = route["route_num"]
        signs: dict[str, dict] = {}
        planned = route["planned_headway_min"]
        fact = actual.get(num) or {}

        fact_headway = fact.get("actual_headway_min")
        if planned and fact_headway:
            ratio = float(fact_headway) / float(planned)
            if ratio >= config.ATTENTION_HEADWAY_RATIO:
                signs["headway_gap"] = {
                    "planned_headway_min": round(float(planned), 1),
                    "actual_headway_min": round(float(fact_headway), 1),
                    "ratio": round(ratio, 2),
                    "severity": min(1.0, ratio - 1.0),
                }

        # сколько машин требует плановый интервал против того, сколько их вышло
        one_way_min = one_way.get(num)
        on_line = fact.get("n_vehicles")
        if planned and one_way_min:
            cycle = 2 * one_way_min + config.LAYOVER_MIN
            required = math.ceil(cycle / float(planned))
            if on_line is not None and required > int(on_line):
                short = required - int(on_line)
                signs["vehicles_short"] = {
                    "required_vehicles": required,
                    "vehicles_on_line": int(on_line),
                    "short": short,
                    "cycle_time_min_at_hour": round(cycle, 1),
                    "one_way_min_at_hour": round(one_way_min, 1),
                    "severity": min(1.0, short / required),
                }

        share = duplication.get(num)
        if share is not None and share >= config.ATTENTION_DUPLICATION_SHARE:
            signs["duplication"] = {
                # процент кладётся рядом с долей намеренно: в текст ответа идёт
                # процент, а охрана чисел разрешает только те числа, которые
                # лежат в результате инструмента
                "share_of_segments_percent": round(float(share) * 100, 1),
                "min_routes_on_segment": config.DUPLICATION_ROUTE_COUNT,
                "severity": float(share),
            }

        length = route["length_km"]
        if length and length > config.MAX_ROUTE_LENGTH_KM:
            signs["route_too_long"] = {
                "length_km": round(float(length), 1),
                "limit_km": config.MAX_ROUTE_LENGTH_KM,
                "severity": min(1.0, float(length) / config.MAX_ROUTE_LENGTH_KM - 1.0),
            }

        close = close_stops.get(num)
        if close and close[0] > 0:
            signs["stops_too_close"] = {
                "share_of_segments_percent": round(float(close[0]) * 100, 1),
                "n_segments": close[1],
                "n_same_point": close[2],
                "limit_m": config.MIN_STOP_SPACING_M,
                "severity": float(close[0]),
            }

        # Признак без веса в оценку не идёт: он наблюдение, а не претензия
        # к маршруту. См. комментарий у config.ATTENTION_WEIGHTS.
        score = sum(
            config.ATTENTION_WEIGHTS.get(name, 0.0) * data["severity"]
            for name, data in signs.items()
        )
        out.append(
            {
                "route_num": num,
                "name": route["name"],
                "score": round(score, 3),
                "signs": signs,
                "planned_headway_min": planned,
                "actual_headway_min": fact_headway,
                "n_vehicles": on_line,
                "n_boardings": fact.get("n_boardings"),
                "length_km": length,
                "n_stops": route["n_stops"],
                "quality": route["quality"],
                "fallback_share": (
                    None if fallback.get(num) is None else round(float(fallback[num]), 3)
                ),
            }
        )

    out.sort(key=lambda r: (-r["score"], r["route_num"]))
    _CACHE[key] = out
    return out


REASON_TEXT = {
    "headway_gap": (
        "фактический интервал {actual_headway_min} мин против планового "
        "{planned_headway_min} мин, хуже плана в {ratio} раза"
    ),
    # «на линии» здесь сказать нельзя: n_vehicles — это уникальные номера
    # бортов, у которых в этот час прошла оплата. Борт без оплат в данные
    # не попадает, поэтому число — нижняя граница выпуска, а не сам выпуск.
    "vehicles_short": (
        "плановый интервал требует машин: {required_vehicles}, по оплатам "
        "видно {vehicles_on_line} — это нижняя граница, в оценку не входит"
    ),
    "duplication": (
        "{share_of_segments_percent}% перегонов маршрут делит "
        "с {min_routes_on_segment} и более маршрутами"
    ),
    "route_too_long": "длина {length_km} км при пороге {limit_km} км",
    "stops_too_close": (
        "остановки ближе {limit_m} м друг к другу: {n_segments} перегонов "
        "({share_of_segments_percent}% маршрута)"
    ),
}


def reasons(entry: dict) -> list[str]:
    """Человеческие формулировки признаков. Все числа — из самого признака."""
    return [
        REASON_TEXT[name].format(**data)
        for name, data in entry["signs"].items()
        if name in REASON_TEXT
    ]


def attention(store: Store, weekday: str, hour: int, limit: int) -> dict:
    """Верхние маршруты по оценке.

    Маршруты с невозможными исходными значениями в ранжирование не попадают:
    дефект геометрии OSM иначе занимает первые строки и выглядит как худший
    маршрут города. Они не исчезают — уходят отдельным списком с причинами,
    и по номеру по-прежнему открываются.
    """
    marked = dataquality.flags(store)
    everything = compute(store, weekday, hour)
    # В список идут только маршруты с признаком, у которого есть вес. Иначе
    # верх занимали бы те, у кого сработало одно наблюдение о данных.
    ranked = [r for r in everything if r["score"] > 0 and r["route_num"] not in marked]
    informational = [
        r
        for r in everything
        if r["signs"] and r["score"] <= 0 and r["route_num"] not in marked
    ]
    top = []
    for entry in ranked[:limit]:
        top.append({**entry, "reasons": reasons(entry)})
    excluded = [
        {
            "route_num": num,
            "reasons": list(dict.fromkeys(item["message"] for item in items)),
        }
        for num, items in sorted(marked.items(), key=lambda kv: dataquality.route_sort_key(kv[0]))
    ]
    return {
        "weekday": weekday,
        "hour": hour,
        "hour_label": f"{hour}:00",
        "routes_total": len(everything),
        "routes_with_signs": len(ranked),
        # сработало только то, что в оценку не идёт: не претензия, но и не ноль
        "routes_informational_only": len(informational),
        # что выпало из ранжирования и почему — признак качества данных,
        # который интерфейс показывает рядом со списком
        "excluded_unreliable": excluded,
        "excluded_count": len(excluded),
        # длина списка кладётся в результат намеренно: её называют в ответе, а
        # называть можно только те числа, которые вернул инструмент
        "routes_shown": len(top),
        "routes": top,
    }
