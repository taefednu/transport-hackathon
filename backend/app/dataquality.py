"""Что в исходных данных физически невозможно и чему нельзя доверять.

Правило одно: **помечаем, а не удаляем**. Маршрут с невозможной длиной
открывается и показывает свои числа, но выпадает из ранжирования диагностики и
из подбора рекомендаций — иначе дефект геометрии OSM выглядит как худший
маршрут города, а продление к пустырю — как решение.

Пороги живут в `config` и посажены на измеренные распределения, а не на
интуицию. Проверки двух видов:

- абсолютные: значение само по себе невозможно (длина 2602 км);
- самосогласованность: длина трассы против суммы её же перегонов. Она ловит
  то, что абсолютным порогом не поймать: маршрут 8 объявляет 171 км при
  16 км собственной цепочки, маршрут 16 — наоборот, 49 км перегонов при
  16.8 км трассы.

Здесь же живёт признак «остановка стоит в стороне от застройки»: он из тех же
данных (здания OSM) и нужен ровно затем же — чтобы рекомендация не вела туда,
где никто не живёт.
"""

from __future__ import annotations

import numpy as np
import polars as pl
from scipy.spatial import cKDTree

from app import config
from app.store import Store


def route_sort_key(route_num: str) -> tuple[int, str]:
    """Порядок номеров такой, каким его читает человек: 8 раньше 122.

    Номер — строка, и сортировка строк ставит «122» перед «8». В списке на
    экране это выглядит как случайная россыпь. Буквенный хвост («13Т») идёт
    после голого числа, номера без цифр — в конец.
    """
    digits = ""
    for char in route_num:
        if not char.isdigit():
            break
        digits += char
    if not digits:
        return (10**9, route_num)
    return (int(digits), route_num[len(digits) :])


_FLAGS: dict[int, dict[str, list[dict]]] = {}
_HOUSING: dict[int, dict[str, int]] = {}

# метры на градус в широте Ташкента. Для радиуса в сотни метров равнопромежуточной
# развёртки достаточно: ошибка на 300 м меньше метра
METERS_PER_DEG_LAT = 111_320.0
TASHKENT_LAT = 41.3
METERS_PER_DEG_LON = METERS_PER_DEG_LAT * np.cos(np.radians(TASHKENT_LAT))


def _flag(code: str, message: str, **values) -> dict:
    return {"code": code, "message": message, **values}


def _length_flags(store: Store) -> dict[str, list[dict]]:
    """Длина трассы: сама по себе и против суммы перегонов."""
    out: dict[str, list[dict]] = {}
    # сумма длин перегонов берётся на одном срезе (день и час), а не по всем
    # строкам: время хода лежит по 24 часа на перегон, длина в них повторяется
    chain = None
    if store.segment_time is not None:
        chain = (
            store.segment_time.filter(
                (pl.col("weekday_type") == config.WEEKDAY_TYPES[0]) & (pl.col("hour") == 8)
            )
            .group_by("route_num", "direction")
            .agg(pl.col("length_m").sum().alias("chain_m"))
        )

    table = store.routes.select("route_num", "direction", "length_km", "n_stops")
    if chain is not None:
        table = table.join(chain, on=["route_num", "direction"], how="left")

    for row in table.iter_rows(named=True):
        num, length_km = row["route_num"], row["length_km"]
        found: list[dict] = []
        if length_km is not None and length_km > config.IMPOSSIBLE_ROUTE_KM:
            found.append(
                _flag(
                    "route_length_impossible",
                    f"длина {length_km:.0f} км при пределе возможного "
                    f"{config.IMPOSSIBLE_ROUTE_KM:.0f} км",
                    direction=row["direction"],
                    length_km=round(float(length_km), 1),
                )
            )
        chain_m = row.get("chain_m")
        if length_km and chain_m:
            ratio = float(length_km) * 1000.0 / float(chain_m)
            if not config.GEOMETRY_CHAIN_RATIO_MIN <= ratio <= config.GEOMETRY_CHAIN_RATIO_MAX:
                # Отношение меньше единицы человек читает как «расхождения почти
                # нет», хотя трасса втрое короче своих же перегонов. Поэтому во
                # фразе всегда кратность больше единицы и слово, куда она.
                fold = ratio if ratio >= 1.0 else 1.0 / ratio
                where = "длиннее" if ratio >= 1.0 else "короче"
                found.append(
                    _flag(
                        "geometry_chain_mismatch",
                        f"трасса {length_km:.1f} км против {chain_m / 1000:.1f} км "
                        f"по собственным перегонам, {where} в {fold:.1f} раза",
                        direction=row["direction"],
                        ratio=round(ratio, 2),
                    )
                )
        if found:
            out.setdefault(num, []).extend(found)
    return out


def _spacing_flags(store: Store) -> dict[str, list[dict]]:
    """Разрывы в цепочке остановок и в перегонах времени хода."""
    out: dict[str, list[dict]] = {}
    if store.route_stops is not None:
        index = {stop_id: i for i, stop_id in enumerate(store.stops["stop_id"].to_list())}
        rows = store.route_stops.sort("route_num", "direction", "seq")
        positions = np.array([index.get(s, -1) for s in rows["stop_id"].to_list()])
        xy = np.full((len(positions), 2), np.nan)
        known = positions >= 0
        xy[known] = store.stop_xy[positions[known]]
        gaps = (
            rows.with_columns(pl.Series("x", xy[:, 0]), pl.Series("y", xy[:, 1]))
            .with_columns(
                (
                    (pl.col("x") - pl.col("x").shift(1).over("route_num", "direction")).pow(2)
                    + (pl.col("y") - pl.col("y").shift(1).over("route_num", "direction")).pow(2)
                )
                .sqrt()
                .alias("gap_m")
            )
            .filter(pl.col("gap_m") > config.IMPOSSIBLE_STOP_GAP_M)
            .group_by("route_num")
            .agg(pl.col("gap_m").max().alias("worst"), pl.len().alias("n"))
        )
        for row in gaps.iter_rows(named=True):
            out.setdefault(row["route_num"], []).append(
                _flag(
                    "stop_gap_impossible",
                    f"между соседними остановками {row['worst'] / 1000:.1f} км "
                    f"(таких пар {row['n']}) — в цепочке пропущены остановки",
                    worst_gap_m=int(row["worst"]),
                    pairs=row["n"],
                )
            )

    if store.segment_time is not None:
        long_segments = (
            store.segment_time.filter(pl.col("length_m") > config.IMPOSSIBLE_SEGMENT_M)
            .group_by("route_num")
            .agg(pl.col("length_m").max().alias("worst"))
        )
        for row in long_segments.iter_rows(named=True):
            out.setdefault(row["route_num"], []).append(
                _flag(
                    "segment_length_impossible",
                    f"перегон длиной {row['worst'] / 1000:.1f} км — трасса между "
                    "остановками проложена не туда",
                    worst_segment_m=int(row["worst"]),
                )
            )
    return out


def _time_flags(store: Store) -> dict[str, list[dict]]:
    """Время хода, интервал и число машин."""
    out: dict[str, list[dict]] = {}
    if store.segment_time is not None:
        one_way = (
            store.segment_time.group_by("route_num", "direction", "weekday_type", "hour")
            .agg((pl.col("travel_sec").sum() / 60.0).alias("one_way_min"))
            .group_by("route_num")
            .agg(pl.col("one_way_min").max().alias("worst"))
            .filter(pl.col("worst") > config.IMPOSSIBLE_ONE_WAY_MIN)
        )
        for row in one_way.iter_rows(named=True):
            out.setdefault(row["route_num"], []).append(
                _flag(
                    "travel_time_impossible",
                    f"время хода в один конец доходит до {row['worst']:.0f} мин "
                    f"при пределе возможного {config.IMPOSSIBLE_ONE_WAY_MIN:.0f} мин",
                    worst_one_way_min=round(float(row["worst"]), 1),
                )
            )

    planned = store.routes.filter(
        (pl.col("planned_headway_min") <= 0)
        | (pl.col("planned_headway_min") > config.IMPOSSIBLE_HEADWAY_MIN)
    )
    for row in planned.iter_rows(named=True):
        out.setdefault(row["route_num"], []).append(
            _flag(
                "planned_headway_impossible",
                f"плановый интервал {row['planned_headway_min']} мин",
                planned_headway_min=row["planned_headway_min"],
            )
        )

    if store.headway_actual is not None:
        bad = store.headway_actual.filter(
            (pl.col("actual_headway_min") <= 0)
            | (pl.col("actual_headway_min") > config.IMPOSSIBLE_HEADWAY_MIN)
            | (pl.col("n_vehicles") <= 0)
            | (pl.col("n_vehicles") > config.IMPOSSIBLE_VEHICLES)
        ).group_by("route_num").agg(pl.len().alias("rows"))
        for row in bad.iter_rows(named=True):
            out.setdefault(row["route_num"], []).append(
                _flag(
                    "actual_headway_impossible",
                    f"часов с невозможным интервалом или числом машин: {row['rows']}",
                    rows=row["rows"],
                )
            )
    return out


def flags(store: Store) -> dict[str, list[dict]]:
    """Маршрут → список пометок. Пустой список маршруты без пометок не получают."""
    key = id(store)
    if key not in _FLAGS:
        if store.routes is None:
            _FLAGS[key] = {}
        else:
            merged: dict[str, list[dict]] = {}
            for part in (_length_flags(store), _spacing_flags(store), _time_flags(store)):
                for num, items in part.items():
                    merged.setdefault(num, []).extend(items)
            _FLAGS[key] = merged
    return _FLAGS[key]


def unreliable(store: Store) -> set[str]:
    """Маршруты, которые не участвуют в ранжировании и в рекомендациях."""
    return set(flags(store))


def report(store: Store) -> dict:
    """Что отфильтровано и почему — для интерфейса как признак качества данных."""
    marked = flags(store)
    total = 0 if store.routes is None else store.routes["route_num"].n_unique()
    return {
        "routes_total": total,
        "routes_flagged": len(marked),
        "checks": [
            {"code": "route_length_impossible", "limit_km": config.IMPOSSIBLE_ROUTE_KM},
            {
                "code": "geometry_chain_mismatch",
                "limit_ratio_min": config.GEOMETRY_CHAIN_RATIO_MIN,
                "limit_ratio_max": config.GEOMETRY_CHAIN_RATIO_MAX,
            },
            {"code": "stop_gap_impossible", "limit_m": config.IMPOSSIBLE_STOP_GAP_M},
            {"code": "segment_length_impossible", "limit_m": config.IMPOSSIBLE_SEGMENT_M},
            {"code": "travel_time_impossible", "limit_min": config.IMPOSSIBLE_ONE_WAY_MIN},
            {"code": "planned_headway_impossible", "limit_min": config.IMPOSSIBLE_HEADWAY_MIN},
            {"code": "actual_headway_impossible", "limit_vehicles": config.IMPOSSIBLE_VEHICLES},
        ],
        "routes": [
            {
                "route_num": num,
                # у маршрута два направления, и абсолютная проверка срабатывает
                # на каждом: в списке для человека одна и та же фраза дважды —
                # шум, а не вторая находка
                "reasons": list(dict.fromkeys(item["message"] for item in items)),
                "codes": list(dict.fromkeys(item["code"] for item in items)),
            }
            for num, items in sorted(marked.items(), key=lambda kv: route_sort_key(kv[0]))
        ],
    }


# --- застройка вокруг остановки -----------------------------------------


def housing_near_stops(store: Store) -> dict[str, int]:
    """Остановка → сколько жилых зданий в HOUSING_RADIUS_M.

    Пустой словарь, если слоя зданий нет: тогда фильтр не применяется и об
    этом честно сообщается, а не подменяется приближением.
    """
    key = id(store)
    if key in _HOUSING:
        return _HOUSING[key]

    if store.buildings is None:
        _HOUSING[key] = {}
        return _HOUSING[key]

    residential = store.buildings.filter(pl.col("klass") != "non_residential")

    def flat(lat, lon) -> np.ndarray:
        return np.column_stack(
            [np.asarray(lon) * METERS_PER_DEG_LON, np.asarray(lat) * METERS_PER_DEG_LAT]
        )

    tree = cKDTree(flat(residential["lat"].to_numpy(), residential["lon"].to_numpy()))
    stops_xy = flat(store.stops["lat"].to_numpy(), store.stops["lon"].to_numpy())
    counts = tree.query_ball_point(stops_xy, config.HOUSING_RADIUS_M, return_length=True)
    _HOUSING[key] = dict(zip(store.stops["stop_id"].to_list(), (int(c) for c in counts)))
    return _HOUSING[key]


def stop_is_off_housing(store: Store, stop_id: str) -> bool:
    """Стоит ли остановка в стороне от жилья."""
    housing = housing_near_stops(store)
    if not housing:
        return False
    return housing.get(stop_id, 0) < config.MIN_HOUSING_BUILDINGS
