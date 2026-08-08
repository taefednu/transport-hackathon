"""Сценарий: список операций поверх базовой сети и пересчёт последствий.

Базовая сеть неизменяема, сервер stateless — сценарий приходит целиком в теле
запроса. Пересчёт идёт по разнице множеств: меняются только те гексагоны, до
которых достают появившиеся или отпавшие остановки.

Новые остановки не поддерживаются намеренно: вставить можно только существующую.
Остановка без физической инфраструктуры — фантазия, и на защите это защищается.
"""

from __future__ import annotations

import time

import polars as pl
import shapely
from shapely.ops import substring

from app import config, coverage, schedule, validation
from app.store import Store

OPS_WITH_STOPS = ("extend_route", "trim_route", "insert_stop", "remove_stop")


class ScenarioError(ValueError):
    pass


def _route_sequence(store: Store, route_num: str, direction: str) -> list[str]:
    if store.route_stops is None:
        raise ScenarioError("нет data/build/route_stops.parquet (шаг 4 пайплайна)")
    seq = store.route_stops.filter(
        (pl.col("route_num") == route_num) & (pl.col("direction") == direction)
    ).sort("seq")
    if seq.is_empty():
        raise ScenarioError(
            f"у маршрута {route_num} ({direction}) не восстановлен порядок остановок "
            "(quality=approximate), операции над ним не определены"
        )
    return seq["stop_id"].to_list()


def _required(op: dict, field: str):
    """Обязательное поле операции. Нет его — это ошибка запроса, а не сбой.

    Раньше поле бралось как `op["until_seq"]`, и запрос без него давал
    KeyError, то есть 500 «Internal Server Error». Снаружи это выглядит как
    поломка сервера, хотя сервер цел, а неверен запрос — и причину, по которой
    его отвергли, никто не видит.
    """
    if field not in op or op[field] is None:
        raise ScenarioError(f"в операции «{op.get('type')}» не задано поле {field}")
    return op[field]


def _required_int(op: dict, field: str) -> int:
    value = _required(op, field)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ScenarioError(
            f"поле {field} операции «{op.get('type')}» должно быть числом, а пришло {value!r}"
        ) from None


def apply_ops(store: Store, ops: list[dict]) -> tuple[dict[tuple[str, str], list[str]], dict]:
    """Возвращает изменённые последовательности остановок и параметры расписаний."""
    sequences: dict[tuple[str, str], list[str]] = {}
    schedules: dict[str, dict] = {}
    known_stops = set(store.stops["stop_id"].to_list())

    for op in ops:
        kind = op.get("type")
        if kind == "set_schedule":
            # ноль здесь — не «не указано». Ключ приходит всегда (модель обязана
            # заполнять все поля схемы), а дальше по коду ноль ложным значением
            # молча подменился бы интервалом из реестра. Лучше сказать вслух.
            headway = op.get("headway_min")
            if headway is not None:
                value = float(headway)
                if value <= 0:
                    raise ScenarioError("интервал должен быть больше нуля")
                # положительный, но крошечный интервал не абсурд на вид, а
                # миллион рейсов в расписании и смерть процесса по памяти
                if value < config.MIN_HEADWAY_MIN:
                    raise ScenarioError(
                        f"интервал не может быть меньше {config.MIN_HEADWAY_MIN:.0f} мин"
                    )
            vehicles = op.get("n_vehicles")
            if vehicles is not None and int(vehicles) < 0:
                raise ScenarioError("число машин на линии не может быть отрицательным")
            schedules[_required(op, "route_num")] = {
                "first_departure": op.get("first_departure"),
                "headway_min": headway,
                "n_vehicles": vehicles,
            }
            continue

        if kind not in OPS_WITH_STOPS:
            raise ScenarioError(f"неизвестная операция: {kind}")

        route_num = _required(op, "route_num")
        direction = op.get("direction", "fwd")
        key = (route_num, direction)
        if key not in sequences:
            sequences[key] = _route_sequence(store, route_num, direction)
        seq = sequences[key]

        if kind == "extend_route":
            for stop_id in _required(op, "stops"):
                if stop_id not in known_stops:
                    raise ScenarioError(
                        f"остановки {stop_id} нет в базе; создавать новые остановки нельзя"
                    )
                seq.append(stop_id)
        elif kind == "trim_route":
            until = _required_int(op, "until_seq")
            if not 0 <= until < len(seq):
                raise ScenarioError(f"until_seq={until} вне маршрута длиной {len(seq)}")
            sequences[key] = seq[: until + 1]
        elif kind == "insert_stop":
            stop_id = _required(op, "stop_id")
            if stop_id not in known_stops:
                raise ScenarioError(f"остановки {stop_id} нет в базе")
            after = _required_int(op, "after_seq")
            # у обрезки и удаления границы проверяются, у вставки не
            # проверялись — а list.insert к границам не придирается: after_seq
            # 999 молча клал остановку в конец, −50 в начало, и ответ выглядел
            # посчитанным по тому месту, которое просили
            if not 0 <= after < len(seq):
                raise ScenarioError(f"after_seq={after} вне маршрута длиной {len(seq)}")
            seq.insert(after + 1, stop_id)
        elif kind == "remove_stop":
            index = _required_int(op, "seq")
            if not 0 <= index < len(seq):
                raise ScenarioError(f"seq={index} вне маршрута длиной {len(seq)}")
            seq.pop(index)

    return sequences, schedules


def served_after(store: Store, sequences: dict[tuple[str, str], list[str]]) -> set[str]:
    """Множество обслуживаемых остановок после применения сценария."""
    served = coverage.served_stop_ids(store)
    if store.route_stops is None:
        return served

    for (route_num, direction), seq in sequences.items():
        before = set(
            store.route_stops.filter(
                (pl.col("route_num") == route_num) & (pl.col("direction") == direction)
            )["stop_id"].to_list()
        )
        after = set(seq)

        # остановка перестаёт быть обслуживаемой, только если её больше не обслуживает
        # никто: счётчик маршрутов из данных Яндекса — это все маршруты города
        for stop_id in before - after:
            n_routes = store.stops.filter(pl.col("stop_id") == stop_id)["n_routes"]
            if n_routes.len() and int(n_routes[0]) <= 1:
                served.discard(stop_id)
        served |= after
    return served


def tail_geometry(store: Store, route_num: str, direction: str, seq: list[str]) -> dict | None:
    """Геометрия изменённого маршрута.

    Хвост до добавленных остановок рисуется прямой и помечается как прямая:
    прокладывать по ней дорогу нечем, а выдавать прямую за трассу нельзя.
    """
    if store.routes is None:
        return None
    row = store.routes.filter(
        (pl.col("route_num") == route_num) & (pl.col("direction") == direction)
    )
    if row.is_empty() or not row["geometry_wkt"][0]:
        return None
    line = shapely.from_wkt(row["geometry_wkt"][0])

    base_seq = _route_sequence(store, route_num, direction)
    appended = seq[len(base_seq):] if len(seq) > len(base_seq) else []
    coords = [list(c) for c in line.coords]

    if appended:
        pos = store.stops.filter(pl.col("stop_id").is_in(appended)).select(
            "stop_id", "lat", "lon"
        )
        by_id = {r["stop_id"]: (r["lon"], r["lat"]) for r in pos.iter_rows(named=True)}
        coords.extend([list(by_id[s]) for s in appended if s in by_id])

    trimmed = len(seq) < len(base_seq)
    if trimmed:
        # обрезка: линию режем по проекции последней оставшейся остановки
        last = seq[-1]
        pos = store.stops.filter(pl.col("stop_id") == last)
        if not pos.is_empty():
            point = shapely.Point(float(pos["lon"][0]), float(pos["lat"][0]))
            cut = line.project(point)
            piece = substring(line, 0, cut)
            coords = [list(c) for c in piece.coords] if piece.length > 0 else coords

    return {
        "type": "LineString",
        "coordinates": coords,
        "tail_is_straight_line": bool(appended),
    }


def _schedule_cost(
    store: Store,
    route_num: str,
    direction: str,
    weekday: str,
    base_seq: list[str],
    new_seq: list[str],
    schedules: dict[str, dict],
    warnings: list[dict],
) -> dict:
    """Оборот и потребность в машинах до и после изменения цепочки остановок.

    Обе стороны считаются одним и тем же способом — по цепочке, а не одна по
    цепочке, а другая по заранее посчитанной матрице: иначе сравнивались бы
    числа, полученные по-разному.

    «Текущее число машин» берётся из операции `set_schedule`, если она есть,
    иначе — сколько машин требует маршрут сегодня. Именно с этим числом
    сравнивается новая потребность, поэтому продление, которому не хватает
    машин, выдаёт предупреждение.
    """
    if store.routes is None:
        return {}
    row = store.routes.filter(
        (pl.col("route_num") == route_num) & (pl.col("direction") == direction)
    )
    if row.is_empty():
        return {}
    route = row.to_dicts()[0]
    params = schedules.get(route_num) or {}

    headway = params.get("headway_min") or route.get("planned_headway_min")
    first = params.get("first_departure") or route.get(f"work_start_{weekday}")
    if not headway or not first:
        return {}

    work_end = route.get(f"work_end_{weekday}")
    before = schedule.build(
        store, route_num, direction, weekday, first, float(headway), None, work_end, base_seq
    )
    after = schedule.build(
        store, route_num, direction, weekday, first, float(headway), None, work_end, new_seq
    )
    if not before.get("available") or not after.get("available"):
        return {}

    have = params.get("n_vehicles") or before["required_vehicles"]
    warnings.extend(validation.schedule_warnings({**after, "n_vehicles": have}, route, weekday))
    return {
        "headway_min": float(headway),
        "one_way_before_min": before["one_way_min"],
        "one_way_after_min": after["one_way_min"],
        "cycle_time_before": before["cycle_time_min"],
        "cycle_time_after": after["cycle_time_min"],
        "required_vehicles_before": before["required_vehicles"],
        "required_vehicles_after": after["required_vehicles"],
        "n_vehicles": have,
        "segments_at_city_speed": after["segments_at_city_speed"],
    }


def run(store: Store, weekday: str, hour: int, ops: list[dict]) -> dict:
    started = time.perf_counter()

    base_served = coverage.served_stop_ids(store)
    base_hexes = coverage.hex_table(store, base_served)
    base = coverage.summary(store, base_hexes, weekday, hour)

    sequences, schedules = apply_ops(store, ops)
    after_served = served_after(store, sequences)
    after_hexes = coverage.hex_table(store, after_served)
    after = coverage.summary(store, after_hexes, weekday, hour)

    before_map = dict(zip(base_hexes["h3_id"].to_list(), base_hexes["covered"].to_list()))
    changed = []
    gained = lost = 0.0
    for cell, cov, pop in zip(
        after_hexes["h3_id"].to_list(),
        after_hexes["covered"].to_list(),
        after_hexes["population"].to_list(),
    ):
        was = before_map.get(cell, False)
        if cov and not was:
            gained += pop
            changed.append({"h3": cell, "state": "gained", "pop": pop})
        elif was and not cov:
            lost += pop
            changed.append({"h3": cell, "state": "lost", "pop": pop})

    affected, warnings, geometry = [], [], {}
    for (route_num, direction), seq in sequences.items():
        base_seq = _route_sequence(store, route_num, direction)
        entry = {
            "route_num": route_num,
            "direction": direction,
            "n_stops_before": len(base_seq),
            "n_stops_after": len(seq),
        }
        # изменение цепочки остановок меняет и стоимость: время оборота и число
        # машин, которое нужно, чтобы держать интервал. Без этого продление
        # выглядит бесплатным, а правило про нехватку машин не срабатывает
        entry.update(
            _schedule_cost(store, route_num, direction, weekday, base_seq, seq, schedules, warnings)
        )
        affected.append(entry)
        geom = tail_geometry(store, route_num, direction, seq)
        if geom:
            geometry[f"{route_num}:{direction}"] = geom

    changed_routes = {num for num, _ in sequences}
    for route_num, params in schedules.items():
        # маршрут, у которого поменялась цепочка, уже посчитан выше — там же
        # учтены и параметры расписания, второй записи о нём быть не должно
        if route_num in changed_routes:
            continue
        row = store.routes.filter(pl.col("route_num") == route_num) if store.routes is not None else None
        if row is None or row.is_empty():
            continue
        route = row.to_dicts()[0]
        direction = route["direction"]
        first = params["first_departure"] or route.get(f"work_start_{weekday}")
        headway = params["headway_min"] or route.get("planned_headway_min")
        if not first or not headway:
            continue
        before = schedule.build(
            store, route_num, direction, weekday,
            route.get(f"work_start_{weekday}") or first,
            float(route.get("planned_headway_min") or headway),
            params["n_vehicles"], route.get(f"work_end_{weekday}"),
        )
        after_sched = schedule.build(
            store, route_num, direction, weekday, first, float(headway),
            params["n_vehicles"], route.get(f"work_end_{weekday}"),
        )
        # Набор полей тот же, что у правки цепочки (`_schedule_cost`): интерфейс
        # показывает одну и ту же таблицу «было → стало» независимо от того,
        # чем правку сделали. Оба расписания уже посчитаны выше, брать неоткуда
        # только «есть на линии» — это факт из данных, а не результат расчёта.
        affected.append(
            {
                "route_num": route_num,
                "direction": direction,
                "headway_before": route.get("planned_headway_min"),
                "headway_after": headway,
                "one_way_before_min": before.get("one_way_min"),
                "one_way_after_min": after_sched.get("one_way_min"),
                "cycle_time_before": before.get("cycle_time_min"),
                "cycle_time_after": after_sched.get("cycle_time_min"),
                "required_vehicles_before": before.get("required_vehicles"),
                "required_vehicles_after": after_sched.get("required_vehicles"),
                "n_vehicles": params["n_vehicles"] or before.get("required_vehicles"),
                "segments_at_city_speed": after_sched.get("segments_at_city_speed"),
            }
        )
        warnings.extend(validation.schedule_warnings(after_sched, route, weekday))

    for (route_num, direction) in sequences:
        warnings.extend(validation.route_warnings(store, route_num, direction, weekday))

    return {
        "weekday": weekday,
        "hour": hour,
        "gained": gained,
        "lost": lost,
        "net": gained - lost,
        "pnt500_before": base["pnt500"]["people"],
        "pnt500_after": after["pnt500"]["people"],
        "pnft15_after": after["pnft15"],
        "t_median_before": base["t_median_min"],
        "t_median_after": after["t_median_min"],
        "changed_hexes": changed,
        "affected_routes": affected,
        "new_geometry": geometry,
        "warnings": warnings,
        "took_ms": (time.perf_counter() - started) * 1000.0,
    }
