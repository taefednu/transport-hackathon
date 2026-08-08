"""Какие маршруты стоит продлить и что это даёт в людях.

Панель «требуют внимания» ранжирует по отклонению от плана. Это другой
вопрос: сколько человек получит доступ, если маршрут дотянуть. Маршрут 32
даёт +2 875 человек и стоит в той панели 33-м из 35 — при двенадцати
видимых строках до него не долистать.

Второго движка здесь нет: считает тот же `tools.route_options`, который
отвечает по клику на маршрут. Числа обязаны совпасть, откуда бы человек
ни пришёл.

Дорого только это: 0.7 с на маршрут, 165 маршрутов — две минуты. Поэтому
сначала отсекаем по потолку прироста операциями над множествами, и полный
прогон идёт по тем, у кого потолок ненулевой — на замере 08.08 их 9, 13.3 с.
"""

from __future__ import annotations

import threading
import traceback

import numpy as np
import polars as pl

from app import config, dataquality, schedule, scenario as scenario_mod, tools
from app.store import Store


def routes_worth_sweeping(store: Store) -> list[str]:
    """Маршруты, у которых продление вообще способно кого-то добавить.

    Потолок маршрута — максимум `tools._candidate_potential` по остановкам,
    до которых дотягивается хвост допустимой длины. Это верхняя граница, а не
    прирост: `gained_people` варианта — население гексагонов, которые приносит
    только новая остановка, а они подмножество тех, по которым считан потолок.
    Значит нулевой потолок означает нулевой прирост, и полный прогон по такому
    маршруту — потраченные впустую 0.7 с.
    """
    if store.routes is None or store.route_stops is None:
        return []
    candidates, _, _ = tools._unserved_candidates(store)
    potential = tools._candidate_potential(store)
    useful = [s for s in candidates if potential.get(s, 0.0) > 0]
    if not useful:
        return []

    stop_index = {s: i for i, s in enumerate(store.stops["stop_id"].to_list())}
    useful_xy = np.array([store.stop_xy[stop_index[s]] for s in useful])
    marked = dataquality.flags(store)

    worth: dict[str, None] = {}
    for row in store.routes.select(
        "route_num", "direction", "length_km"
    ).iter_rows(named=True):
        num = row["route_num"]
        if num in marked or num in worth:
            continue
        length_km = row["length_km"]
        if not length_km:
            continue
        try:
            sequence = scenario_mod._route_sequence(store, num, row["direction"])
        except scenario_mod.ScenarioError:
            continue
        terminus = sequence[-1]
        if terminus not in stop_index:
            continue
        limit_km = config.IMPROVEMENT_MAX_LENGTH_SHARE * float(length_km)
        distances = (
            np.hypot(*(useful_xy - store.stop_xy[stop_index[terminus]]).T) / 1000.0
        )
        if bool((distances <= limit_km).any()):
            worth[num] = None
    return list(worth)


def rank(rows: list[dict]) -> list[dict]:
    """Порядок строк и пометка повторной цели.

    Без пометки верх списка был бы из нескольких строк с одинаковым числом:
    до одной остановки дотягиваются несколько маршрутов (facts.md §11 — до
    Minerva City дотягивались четыре). Это разные действия с одинаковым
    результатом, и прятать их нельзя, а не сказать про повтор — обман.
    """
    ordered = sorted(
        rows,
        key=lambda r: (-r["gained_people"], r["extra_vehicles"], r["route_num"]),
    )
    first_for_stop: dict[str, str] = {}
    for row in ordered:
        row["same_stop_as"] = first_for_stop.get(row["stop_id"])
        first_for_stop.setdefault(row["stop_id"], row["route_num"])
    return ordered


def _route_names(store: Store) -> dict[tuple[str, str], str]:
    """Имя маршрута по паре (номер, направление).

    У одного маршрута направления могут называться по-разному — группировка
    только по номеру взяла бы имя первого попавшегося направления и
    приписала его строке про другое. Ключ по паре убирает эту двусмысленность.
    """
    rows = store.routes.select("route_num", "direction", "name")
    return {
        (row["route_num"], row["direction"]): row["name"]
        for row in rows.iter_rows(named=True)
    }


def sweep(
    store: Store,
    index: list,
    weekday: str,
    hour: int,
    on_progress=None,
) -> list[dict]:
    """Лучший вариант продления по каждому маршруту, худшие отсеяны раньше.

    `on_progress(done)` зовётся после каждого маршрута — из него живёт
    счётчик в панели.
    """
    names = _route_names(store)
    rows: list[dict] = []
    for done, num in enumerate(routes_worth_sweeping(store), start=1):
        try:
            result = tools.route_options(
                store,
                index,
                {"route_num": num, "weekday": weekday, "hour": hour, "direction": None},
            )
        except tools.ToolError:
            # маршрут с невозможной геометрией или без целей: не сбой,
            # он просто не даёт варианта
            result = None
        if result and result["options"]:
            best = max(result["options"], key=lambda o: o["gained_people"])
            # `cost_unavailable` появляется только после `_recost` на другой
            # день; здесь его ставим в None явно, чтобы ключ был в строке
            # всегда — форма ответа не должна зависеть от того, какой день
            # спросили.
            rows.append({
                **best,
                "name": names.get((best["route_num"], best["direction"]), ""),
                "cost_unavailable": None,
            })
        if on_progress is not None:
            on_progress(done)
    return rank(rows)


class _Progress:
    """Состояние фонового перебора. Пишет только поток, читают запросы.

    Блокировки нет и она не нужна: поток стартует после `tools.warm`, то есть
    все кэши, из которых он читает, уже наполнены, а `store` — неизменяемые
    фреймы polars. Наружу результат отдаётся **одним присваиванием готового
    списка**, а не дописыванием того, который кто-то читает; счётчик — это
    присваивание int. Обе операции атомарны под GIL.
    """

    def __init__(self) -> None:
        self.status = "computing"
        self.done = 0
        self.total = 0
        self.scanned = 0
        self.error: str | None = None
        self.rows: list[dict] = []
        self.weekday = ""
        self.hour = 0


_PROGRESS = _Progress()

# цена на другой день: ключ — день и длина среза, потому что пересчитываются
# ровно показанные строки; час на цену не влияет и в ключе не участвует.
# Это безопасно, только пока цена в движке действительно не зависит от часа —
# гейт 23г в verify_gates.py проверяет именно это допущение напрямую в движке.
_COST_CACHE: dict[tuple[str, int], list[dict]] = {}
_COST_LOCK = threading.Lock()


def _run(store: Store, index: list, weekday: str, hour: int) -> None:
    try:
        # `routes_worth_sweeping` зовётся здесь ради `total` и ещё раз внутри
        # `sweep`. Это доли секунды, и оно того стоит: тело перебора остаётся
        # в одном месте, а `sweep` — синхронной функцией без состояния,
        # которую можно замерить и позвать из скрипта
        _PROGRESS.total = len(routes_worth_sweeping(store))
        _PROGRESS.rows = sweep(
            store,
            index,
            weekday,
            hour,
            on_progress=lambda done: setattr(_PROGRESS, "done", done),
        )
        _PROGRESS.status = "ready"
    except Exception as exc:  # поток не должен уносить сервер с собой
        _PROGRESS.error = f"{type(exc).__name__}: {exc}"
        _PROGRESS.status = "failed"
        traceback.print_exc()


def start(store: Store, index: list, weekday: str, hour: int) -> None:
    """Запустить перебор в фоне. Возвращается сразу."""
    _PROGRESS.weekday, _PROGRESS.hour = weekday, hour
    _PROGRESS.scanned = (
        0 if store.routes is None else store.routes["route_num"].n_unique()
    )
    threading.Thread(
        target=_run,
        args=(store, index, weekday, hour),
        daemon=True,
        name="improvements",
    ).start()


def _has_work_start(store: Store, route_num: str, direction: str, weekday: str) -> bool:
    """Есть ли у маршрута заявленный первый выезд в этот день недели.

    Отсутствие колонки `work_start_{weekday}` — это единственный факт,
    по которому можно сказать «маршрут в этот день не работает». Всё
    остальное, что мешает построить расписание, не про это, и путать
    два случая нельзя — угадывать причину по отсутствию цены запрещено.
    """
    if store.routes is None:
        return False
    match = store.routes.filter(
        (pl.col("route_num") == route_num) & (pl.col("direction") == direction)
    )
    if match.is_empty():
        return False
    return match[f"work_start_{weekday}"][0] is not None


def _schedule_gap_reason(store: Store, route_num: str, direction: str, weekday: str) -> str | None:
    """Причина отказа, которую уже посчитал `schedule.build`, если она есть.

    `scenario._schedule_cost` строит расписание тем же вызовом и получает ту
    же причину, но при неудаче отдаёт наружу пустой `{}` — `reason` в нём
    выбрасывается. Строить расписание второй раз ради одной фразы — то же
    самое чтение данных, без побочных эффектов, и дешевле, чем гадать текст
    самим: `no_travel_reason` внутри уже отличает «за этот день нет времени
    хода вообще ни у одного маршрута» от «у этого маршрута порядок остановок
    восстановлен только приблизительно».
    """
    if store.routes is None:
        return None
    match = store.routes.filter(
        (pl.col("route_num") == route_num) & (pl.col("direction") == direction)
    )
    if match.is_empty():
        return None
    route = match.to_dicts()[0]
    headway = route.get("planned_headway_min")
    first = route.get(f"work_start_{weekday}")
    if not headway or not first:
        return None
    try:
        base_seq = scenario_mod._route_sequence(store, route_num, direction)
    except scenario_mod.ScenarioError:
        return None
    result = schedule.build(
        store,
        route_num,
        direction,
        weekday,
        first,
        float(headway),
        None,
        route.get(f"work_end_{weekday}"),
        base_seq,
    )
    if result.get("available"):
        return None
    return result.get("reason") or None


def _recost(store: Store, rows: list[dict], weekday: str, hour: int) -> list[dict]:
    """Те же варианты, посчитанные на другой день.

    Прирост людей — это геометрия покрытия. От дня она в теории зависит, но
    маршрут либо есть, либо его нет весь день — не по часам. От дня зависит
    время хода, а значит оборот и число машин: `scenario._schedule_cost`
    строит расписание от первого выезда маршрута в этот день недели
    (`work_start_{weekday}`), а `hour` в него вообще не передаётся — он
    остаётся в сигнатуре только потому, что `scenario.run` его требует для
    подсчёта покрытия, и меняет он там ровно ничего (замер 08.08: 9 из 9
    маршрутов дают одно и то же число людей на любой час). Пересчитываются
    только показанные строки, и из пересчёта берётся всё, включая людей: если
    где-то они всё-таки зависят от дня, панель покажет посчитанное, а не
    сохранённое.

    Потолок в `config.IMPROVEMENT_MAX_EXTRA_VEHICLES` здесь не применяется
    заново: он уже отфильтровал перебор на день прогрева, и в панель попали
    только варианты, вписавшиеся в него тогда. Пересчёт на другой день просто
    показывает настоящую цену на этот день, не сверяя её с потолком ещё раз —
    так и задумано: строка, подорожавшая на другой день сверх потолка, была бы
    этой сверкой спрятана, а спрятать подорожавший вариант хуже, чем показать
    его цену.

    Строка без цены не выбрасывается: у части маршрутов нет расписания на
    какие-то дни (`work_start_{weekday}` пуст), и прирост людей от продления
    у них не перестаёт быть фактом только потому, что цену не посчитать.
    Вместо цены — `cost_unavailable` с конкретной причиной.
    """
    out: list[dict] = []
    for row in rows:
        ops = [
            {
                "type": "extend_route",
                "route_num": row["route_num"],
                "direction": row["direction"],
                "stops": [row["stop_id"]],
            }
        ]
        try:
            result = scenario_mod.run(store, weekday, hour, ops)
        except scenario_mod.ScenarioError:
            continue
        affected = result["affected_routes"][0]
        before = affected.get("required_vehicles_before")
        after = affected.get("required_vehicles_after")
        cost_unavailable = None
        if before is None or after is None:
            if not _has_work_start(store, row["route_num"], row["direction"], weekday):
                cost_unavailable = (
                    f"маршрут {row['route_num']} не работает в "
                    f"{config.WEEKDAY_NAMES.get(weekday, weekday)}: "
                    "первого выезда на этот день нет в расписании"
                )
            else:
                cost_unavailable = _schedule_gap_reason(
                    store, row["route_num"], row["direction"], weekday
                ) or (
                    f"расписание маршрута {row['route_num']} на "
                    f"{config.WEEKDAY_NAMES.get(weekday, weekday)} построить не удалось"
                )
        out.append(
            {
                **row,
                "gained_people": int(round(result["gained"] - row["chain_recount_people"])),
                "lost_people": int(round(result["lost"])),
                "cycle_time_before_min": None if cost_unavailable else round(affected["cycle_time_before"], 1),
                "cycle_time_after_min": None if cost_unavailable else round(affected["cycle_time_after"], 1),
                "required_vehicles_before": before,
                "required_vehicles_after": after,
                "extra_vehicles": None if cost_unavailable else after - before,
                "scenario": None if cost_unavailable else {"weekday": weekday, "hour": hour, "ops": ops},
                "cost_unavailable": cost_unavailable,
            }
        )
    return rank(out)


def snapshot(store: Store, weekday: str, hour: int, limit: int) -> dict:
    """Ответ эндпоинта в любом состоянии перебора."""
    head = {
        "status": _PROGRESS.status,
        "routes_done": _PROGRESS.done,
        "routes_total": _PROGRESS.total,
        # сколько маршрутов рассмотрено всего: без этого числа «31 в переборе»
        # читается как потеря, а не как отсечка
        "routes_scanned": _PROGRESS.scanned,
        "error": _PROGRESS.error,
        "weekday": weekday,
        "hour": hour,
        "hour_label": f"{hour}:00",
        "excluded_count": len(dataquality.flags(store)),
    }
    if _PROGRESS.status != "ready":
        return {**head, "routes_with_options": 0, "routes_shown": 0, "routes": []}

    rows = _PROGRESS.rows
    shown = rows[:limit]
    # Час пересчёта не вызывает: `scenario._schedule_cost` его не принимает,
    # оборот считается для рейса, выходящего в первый выезд маршрута. День
    # вызывает — от дня зависят и время хода, и окно работы маршрута.
    if weekday != _PROGRESS.weekday:
        key = (weekday, limit)
        with _COST_LOCK:
            cached = _COST_CACHE.get(key)
            if cached is None:
                # `_recost` сама гасит `ScenarioError` построчно — это
                # ожидаемый случай, вариант просто не годится для строки.
                # Сюда долетает только неожиданное, а этот эндпоинт обязан
                # отвечать в любом состоянии: 500 здесь — это пустая панель
                # в момент, когда человек двигает день на шкале времени.
                # Поэтому отдаём варианты как есть (на день прогрева, а не
                # запрошенный) и говорим об этом в `error`, а не падаем и не
                # подсовываем цену молча. Неудачу в кэш не кладём — иначе
                # она станет постоянной для этого дня.
                try:
                    cached = _recost(store, shown, weekday, hour)
                    _COST_CACHE[key] = cached
                except Exception:
                    traceback.print_exc()
                    head["error"] = (
                        "не удалось пересчитать цену на запрошенный день — "
                        "показана цена на день прогрева"
                    )
                    cached = shown
        shown = cached
    return {
        **head,
        "routes_with_options": len(rows),
        "routes_shown": len(shown),
        "routes": shown,
    }
