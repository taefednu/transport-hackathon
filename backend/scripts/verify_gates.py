"""Приёмка всех гейтов из раздела 12 ТЗ одним прогоном по живому API.

Запуск: сервер поднят, затем `.venv/bin/python scripts/verify_gates.py [порт]`.
Каждое число в NIGHT_REPORT.md берётся отсюда, а не из головы.
"""

import _bootstrap  # noqa: F401

import json
import sys
import time
import urllib.parse
import urllib.request

BASE = f"http://127.0.0.1:{sys.argv[1] if len(sys.argv) > 1 else 8023}"
results: list[tuple[str, bool, str]] = []


def get(path: str):
    started = time.perf_counter()
    with urllib.request.urlopen(BASE + path) as response:
        body = response.read()
    return body, (time.perf_counter() - started) * 1000


def get_json(path: str):
    body, ms = get(path)
    return json.loads(body), ms


def post_json(path: str, payload: dict):
    started = time.perf_counter()
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        body = response.read()
    return json.loads(body), (time.perf_counter() - started) * 1000


def check(name: str, passed: bool, detail: str) -> None:
    results.append((name, passed, detail))
    print(f"[{'OK ' if passed else 'FAIL'}] {name}: {detail}")


def gate1():
    d, ms = get_json("/api/baseline?weekday=fri&hour=8")
    p = d["pnt500"]["people"]
    check(
        "Гейт 1 — PNT-500 в границах 1.5–3.2 млн",
        1_500_000 <= p <= 3_200_000,
        f"население {d['population_total']:,.0f}; PNT-500 {p:,.0f} ({d['pnt500']['share']:.1%}); "
        f"вне доступа {d['pnt500']['people_outside']:,.0f}; PNFT-15 {d['pnft15']['people']:,.0f} "
        f"({d['pnft15']['share']:.1%}); T-median {d['t_median_min']:.2f} мин; {ms:.0f} мс",
    )


def gate2():
    d, ms = get_json("/api/routes/14?direction=fwd&weekday=fri")
    st = d["segment_times"]
    t6 = sum(x["travel_sec"] for x in st if x["hour"] == 6) / 60
    t8 = sum(x["travel_sec"] for x in st if x["hour"] == 8) / 60
    traffic = sum(1 for x in st if x["source"] == "traffic") / len(st)
    check(
        "Гейт 2 — время хода растёт к часу пик",
        t8 > t6,
        f"маршрут 14: {len(d['stops'])} остановок, {d['length_km']:.2f} км; "
        f"весь маршрут 6:00 {t6:.1f} мин против 8:00 {t8:.1f} мин; "
        f"по трафику {traffic:.1%} строк; {ms:.0f} мс",
    )


def gate3():
    a, _ = get_json(
        "/api/routes/14/schedule?direction=fwd&weekday=fri&headway_min=9&first_departure=06:00"
    )
    b, ms = get_json(
        "/api/routes/14/schedule?direction=fwd&weekday=fri&headway_min=9&first_departure=07:00"
    )
    gap = (b["stops"][-1]["arrivals_sec"][0] - a["stops"][-1]["arrivals_sec"][0]) / 60
    e, _ = get_json(
        "/api/routes/14/schedule?direction=fwd&weekday=fri&headway_min=9&first_departure=18:00"
    )
    f, _ = get_json(
        "/api/routes/14/schedule?direction=fwd&weekday=fri&headway_min=9&first_departure=19:00"
    )
    gap_evening = (f["stops"][-1]["arrivals_sec"][0] - e["stops"][-1]["arrivals_sec"][0]) / 60
    check(
        "Гейт 3 — сдвиг выезда на час меняет прибытие не на час",
        abs(gap - 60) > 0.5,
        f"06:00 в пути {a['one_way_min']:.1f} мин, 07:00 в пути {b['one_way_min']:.1f} мин; "
        f"разрыв прибытия {gap:.2f} мин ({gap - 60:+.2f} к сдвигу); "
        f"вечером 18:00→19:00 разрыв {gap_evening:.2f} мин ({gap_evening - 60:+.2f}); {ms:.0f} мс",
    )


def gate4(extend_stops: list[str], trim_route: str, trim_direction: str, trim_seq: int):
    a, _ = post_json(
        "/api/scenario",
        {
            "weekday": "fri",
            "hour": 8,
            "ops": [
                {
                    "type": "extend_route",
                    "route_num": "14",
                    "direction": "fwd",
                    "stops": extend_stops,
                }
            ],
        },
    )
    b, _ = post_json(
        "/api/scenario",
        {
            "weekday": "fri",
            "hour": 8,
            "ops": [
                {
                    "type": "trim_route",
                    "route_num": trim_route,
                    "direction": trim_direction,
                    "until_seq": trim_seq,
                }
            ],
        },
    )
    check(
        "Гейт 4 — сценарий считает и приобретения, и потери",
        a["gained"] > 0 and a["lost"] == 0 and a["took_ms"] < 800 and b["lost"] > 0,
        f"продление 14 на 3 остановки: +{a['gained']:,.0f} чел., −{a['lost']:,.0f}, "
        f"{a['took_ms']:.0f} мс; обрезка {trim_route} ({trim_direction}) до seq={trim_seq}: "
        f"+{b['gained']:,.0f}, −{b['lost']:,.0f}, {b['took_ms']:.0f} мс",
    )


def gate5():
    d, ms = get_json("/api/routes/8?direction=fwd&weekday=fri")
    by_hour = {x["hour"]: x for x in d["actual_headway"]}
    h8, h22 = by_hour[8], by_hour[22]
    holes, _ = get_json("/api/holes?limit=1")
    parallel, _ = get_json("/api/segments/parallel?min_routes=5")
    check(
        "Гейт 5 — фактический интервал утром меньше вечернего",
        h8["actual_headway_min"] < h22["actual_headway_min"],
        f"маршрут 8: 8:00 {h8['actual_headway_min']:.2f} мин на {h8['n_vehicles']} машинах, "
        f"22:00 {h22['actual_headway_min']:.2f} мин на {h22['n_vehicles']}; "
        f"дыр {holes['count']} на {holes['people_total']:,.0f} чел.; "
        f"перегонов с 5+ маршрутами {parallel['count']}; {ms:.0f} мс",
    )


def gate6():
    found = {}
    for query in ("куйлюк", "Qo'yliq", "чилонзор", "Chilonzor"):
        d, _ = get_json("/api/search?q=" + urllib.parse.quote(query) + "&limit=5")
        found[query] = [s["title"] for s in d["stops"]]
    cyrillic_finds_latin = any("liq" in t or "yliq" in t for t in found["куйлюк"])
    csv_body, ms = get(
        "/api/export/schedule?route_num=14&direction=fwd&weekday=fri"
        "&first_departure=06:00&headway_min=9"
    )
    geo, _ = get_json("/api/export/route?route_num=14&direction=fwd")
    check(
        "Гейт 6 — поиск через транслитерацию и экспорт",
        cyrillic_finds_latin and len(csv_body.splitlines()) > 1 and len(geo["features"]) > 1,
        f"«куйлюк» находит {found['куйлюк'][:2]}; «чилонзор» находит {found['чилонзор'][:2]}; "
        f"CSV {len(csv_body.splitlines())} строк; GeoJSON {len(geo['features'])} фич; {ms:.0f} мс",
    )


def main() -> None:
    import polars as pl

    from app import coverage
    from app.store import load

    store = load()
    served = coverage.served_stop_ids(store)
    covered = coverage.covered_hexes(store, served)
    population = dict(zip(store.hexes["h3_id"].to_list(), store.hexes["population"].to_list()))

    # три необслуживаемые остановки, рядом с которыми больше всего людей без покрытия
    unserved = set(store.stops.filter(pl.col("n_routes") == 0)["stop_id"].to_list())
    gain: dict[str, float] = {}
    for stop_id, cell in zip(
        store.stop_hexes["stop_id"].to_list(), store.stop_hexes["h3_id"].to_list()
    ):
        if stop_id in unserved and cell not in covered:
            gain[stop_id] = gain.get(stop_id, 0.0) + population.get(cell, 0.0)
    extend_stops = [s for s, _ in sorted(gain.items(), key=lambda kv: -kv[1])[:3]]

    # маршрут, у которого хвостовая остановка — единственная, кто держит свой гексагон
    counts = store.stop_hexes.filter(pl.col("stop_id").is_in(list(served))).group_by("h3_id").agg(
        pl.len().alias("n"), pl.col("stop_id").first().alias("only_stop")
    )
    solo = (
        counts.filter(pl.col("n") == 1)
        .join(store.hexes.select("h3_id", "population"), on="h3_id")
        .filter(pl.col("population") > 0)
        .sort("population", descending=True)
    )
    trim = store.route_stops.filter(pl.col("stop_id") == solo["only_stop"][0]).to_dicts()[0]

    gate1()
    gate2()
    gate3()
    gate4(extend_stops, trim["route_num"], trim["direction"], max(0, int(trim["seq"]) - 1))
    gate5()
    gate6()

    passed = sum(1 for _, ok, _ in results if ok)
    print()
    print(f"пройдено гейтов: {passed} из {len(results)}")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
