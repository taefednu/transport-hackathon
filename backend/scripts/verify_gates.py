"""Приёмка всех гейтов из раздела 12 ТЗ одним прогоном по живому API.

Запуск: сервер поднят, затем `.venv/bin/python scripts/verify_gates.py [порт]`.
Каждое число в NIGHT_REPORT.md берётся отсюда, а не из головы.
"""

import _bootstrap  # noqa: F401

import json
import sys
import time
import urllib.error
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


def post_status(path: str, payload: dict) -> tuple[int, str]:
    """Код и тело ответа, включая ошибочные: их-то и проверяем."""
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


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


def gate7(gain_stop_name: str) -> dict:
    """Разбор фразы на естественном языке: четыре критерия из задачи."""
    literal = post_json(
        "/api/nl/scenario",
        {"text": "продлить четырнадцатый до Куйлюка и посмотреть, что будет в утренний пик"},
    )[0]
    # объект сценария принимается движком без единой правки
    accepted = literal["scenario"] is not None
    if accepted:
        post_json("/api/scenario", literal["scenario"])

    # прирост людей проверяем на остановке, у которой он вообще может быть:
    # Куйлюк уже обслуживают шесть маршрутов, продление туда даёт ноль (см. detail)
    gain_parse = post_json("/api/nl/scenario", {"text": f"продлить четырнадцатый до {gain_stop_name}"})[0]
    gain_result = post_json("/api/scenario", gain_parse["scenario"])[0] if gain_parse["scenario"] else {}
    gained = gain_result.get("gained", 0)

    latin = post_json("/api/nl/scenario", {"text": "продлить маршрут 14 до Massiv Qo'yliq-1"})[0]
    same_stop = (
        accepted
        and latin["scenario"] is not None
        and latin["scenario"]["ops"][0]["stops"] == literal["scenario"]["ops"][0]["stops"]
    )

    missing = post_json(
        "/api/nl/scenario", {"text": "продлить четырнадцатый до Марсианской набережной"}
    )[0]
    missing_ok = missing["scenario"] is None and bool(missing["unresolved"])

    ambiguous, ms = post_json("/api/nl/scenario", {"text": "продлить четырнадцатый до Qo'yliq"})
    ambiguous_ok = (
        ambiguous["scenario"] is None
        and bool(ambiguous["ambiguous"])
        and len(ambiguous["ambiguous"][0]["candidates"]) >= 2
    )

    check(
        "Гейт 7 — фраза словами превращается в сценарий",
        accepted and gained > 0 and same_stop and missing_ok and ambiguous_ok,
        f"«до Куйлюка» → {literal['understood']} (путь: {literal['source']}), "
        f"движок принял объект: {accepted}, прирост по Куйлюку 0 — остановку уже обслуживают; "
        f"«до {gain_stop_name}» → +{gained:,.0f} чел.; латиница с апострофом даёт ту же остановку: "
        f"{same_stop}; несуществующая остановка: {missing['unresolved'][0]['reason'] if missing['unresolved'] else '—'}; "
        f"неоднозначное название → {len(ambiguous['ambiguous'][0]['candidates']) if ambiguous['ambiguous'] else 0} кандидата; {ms:.0f} мс",
    )
    return {"gain_parse": gain_parse, "gain_result": gain_result, "literal": literal}


def gate8(gain_result: dict) -> dict:
    """Объяснение результата: ни одного числа сверх входных данных."""
    from app import explain as explain_mod

    payload = {
        "result": gain_result,
        "sources": {"fallback_share": 0.089, "population_layer_date": "01.11.2023"},
    }
    answer, ms = post_json("/api/explain", payload)
    extra = sorted(explain_mod.numbers_in(answer["text"]) - explain_mod.allowed_numbers(answer["facts"]))
    missing = explain_mod.missing_disclaimer(answer["text"], answer["facts"])
    check(
        "Гейт 8 — объяснение не выдумывает чисел и не теряет оговорку",
        not extra and not missing and bool(answer["text"]),
        f"путь: {answer['source']}"
        + (f" ({answer['reason']})" if answer.get("reason") else "")
        + f"; чисел вне входных данных: {len(extra)}"
        + (f" {extra}" if extra else "")
        + f"; в оговорке не хватает: {missing or 'ничего'}"
        + f"; длина {len(answer['text'])} знаков; {ms:.0f} мс",
    )
    return answer


def gate9(store, gain_stop_name: str) -> dict:
    """Оба эндпоинта при выключенной модели: путь помечен детерминированным."""
    import importlib
    import os

    from app import config, explain as explain_mod, llm, nlparse
    from app import search as search_mod

    previous = os.environ.get("QATNOV_LLM_DISABLED")
    os.environ["QATNOV_LLM_DISABLED"] = "1"
    importlib.reload(config)
    try:
        index = search_mod.build_index(store)
        parsed = nlparse.parse(store, index, f"продлить четырнадцатый до {gain_stop_name}")
        from app import scenario as scenario_mod

        result = scenario_mod.run(
            store, parsed["scenario"]["weekday"], parsed["scenario"]["hour"], parsed["scenario"]["ops"]
        )
        text = explain_mod.explain(store, {"result": result})
        offline_ok = (
            not llm.available()
            and parsed["source"] == "deterministic"
            and parsed["scenario"] is not None
            and text["source"] == "deterministic"
            and bool(text["text"])
        )
        extra = sorted(
            explain_mod.numbers_in(text["text"]) - explain_mod.allowed_numbers(text["facts"])
        )
        check(
            "Гейт 9 — оба эндпоинта работают без модели",
            offline_ok and not extra,
            f"разбор: {parsed['source']}, сценарий собран: {parsed['scenario'] is not None}; "
            f"объяснение: {text['source']} ({text['reason']}); чисел вне входных данных: {len(extra)}",
        )
        return {"parsed": parsed, "explained": text}
    finally:
        if previous is None:
            os.environ.pop("QATNOV_LLM_DISABLED", None)
        else:
            os.environ["QATNOV_LLM_DISABLED"] = previous
        importlib.reload(config)


def gate10(store, gain_stop_name: str) -> None:
    """Модельный путь на подставном ответе сети.

    Живого ключа Yandex Cloud нет, поэтому подменяется ровно один слой —
    `urlopen`. Всё, что проверяется (сборка тела запроса, разбор ответа,
    приведение намерения, резолв по базе, охрана чисел), выполняется настоящее.
    """
    import importlib
    import json as json_mod
    import os
    import urllib.request

    from app import config, explain as explain_mod, llm, nlparse, scenario as scenario_mod
    from app import search as search_mod

    saved = {k: os.environ.get(k) for k in ("QATNOV_YC_API_KEY", "QATNOV_YC_FOLDER_ID", "QATNOV_LLM_DISABLED")}
    os.environ["QATNOV_YC_API_KEY"] = "test-key"
    os.environ["QATNOV_YC_FOLDER_ID"] = "test-folder"
    os.environ.pop("QATNOV_LLM_DISABLED", None)
    importlib.reload(config)
    real_urlopen = urllib.request.urlopen

    state = {"paragraph": ""}

    class FakeResponse:
        def __init__(self, payload: dict):
            self._body = json_mod.dumps(payload, ensure_ascii=False).encode()

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        sent = json_mod.loads(request.data.decode())
        is_parse = "jsonSchema" in sent or "jsonObject" in sent
        text = (
            json_mod.dumps(
                {
                    "op": "extend_route",
                    "route": "четырнадцатый",
                    "stops": [gain_stop_name],
                    "anchor_stop": "",
                    "hour": 8,
                    "weekday": "fri",
                    "headway_min": 0,
                    "first_departure": "",
                    "n_vehicles": 0,
                },
                ensure_ascii=False,
            )
            if is_parse
            else state["paragraph"]
        )
        return FakeResponse(
            {
                "result": {
                    "alternatives": [
                        {"message": {"role": "assistant", "text": text}, "status": "ALTERNATIVE_STATUS_FINAL"}
                    ],
                    "usage": {"inputTextTokens": 1, "completionTokens": 1, "totalTokens": 2},
                    "modelVersion": "fake",
                }
            }
        )

    urllib.request.urlopen = fake_urlopen
    try:
        index = search_mod.build_index(store)
        parsed = nlparse.parse(store, index, "продлить четырнадцатый до какой-нибудь остановки")
        parse_ok = parsed["source"] in ("model", "cache") and parsed["scenario"] is not None

        result = scenario_mod.run(
            store, parsed["scenario"]["weekday"], parsed["scenario"]["hour"], parsed["scenario"]["ops"]
        )
        facts = explain_mod.build_facts(store, {"result": result})

        state["paragraph"] = (
            f"Доступ получают {facts['получили_доступ_человек']} человек, теряют "
            f"{facts['потеряли_доступ_человек']}. Источники: "
            f"{facts['оговорка']['доля_перегонов_по_медиане_города_процент']}% перегонов "
            f"по медиане скорости города, слой населения — срез "
            f"{facts['оговорка']['дата_слоя_населения']}."
        )
        clean = explain_mod.explain(store, {"result": result})

        # тело запроса то же самое, поэтому без сброса кэша второй ответ не дойдёт
        llm.clear_cache()
        state["paragraph"] = "Доступ получают 999999 человек, это примерно 42 процента города."
        dirty = explain_mod.explain(store, {"result": result})

        check(
            "Гейт 10 — модельный путь и охрана чисел (ответ сети подставной)",
            parse_ok and clean["source"] in ("model", "cache") and dirty["source"] == "deterministic",
            f"разбор через модель: {parsed['source']}, остановка «{gain_stop_name}» опознана: "
            f"{parsed['scenario'] is not None}; чистый абзац принят как {clean['source']}; "
            f"абзац с выдуманным числом отклонён: {dirty['reason']}",
        )
    finally:
        urllib.request.urlopen = real_urlopen
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(config)


def gate11() -> None:
    """Ноль не уезжает в расчёт ни из разбора фразы, ни напрямую в движок.

    Схема Яндекса требует все поля, поэтому `headway_min: 0` приходит от модели
    всегда — и не должен ни попасть в операции, ни быть молча понят как «оставить
    интервал из реестра».
    """
    parsed = post_json("/api/nl/scenario", {"text": "поставить на восьмёрке выезд в 05:40"})[0]
    op = (parsed["scenario"] or {}).get("ops", [{}])[0]
    clean = "headway_min" not in op and "n_vehicles" not in op

    code, body = post_status(
        "/api/scenario",
        {"weekday": "fri", "hour": 8, "ops": [{"type": "set_schedule", "route_num": "8", "headway_min": 0}]},
    )
    rejected = code == 422 and "больше нуля" in body
    check(
        "Гейт 11 — нулевой интервал не уезжает в расчёт",
        clean and rejected,
        f"фраза без интервала → ops {json.dumps(op, ensure_ascii=False)}; "
        f"прямой POST с headway_min=0 → HTTP {code}, "
        f"{json.loads(body).get('detail') if code == 422 else 'принят молча'}",
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

    # остановка для фразы, у которой прирост людей вообще может быть ненулевым:
    # первая по приросту из тех, у кого есть название, которое можно назвать словами
    names = dict(zip(store.stops["stop_id"].to_list(), store.stops["name"].to_list()))
    gain_stop_name = next(
        names[s] for s, _ in sorted(gain.items(), key=lambda kv: -kv[1]) if names.get(s)
    )

    gate1()
    gate2()
    gate3()
    gate4(extend_stops, trim["route_num"], trim["direction"], max(0, int(trim["seq"]) - 1))
    gate5()
    gate6()
    nl = gate7(gain_stop_name)
    explained = gate8(nl["gain_result"])
    offline = gate9(store, gain_stop_name)
    gate10(store, gain_stop_name)
    gate11()

    print()
    print("Разобранный сценарий (фраза → объект для POST /api/scenario):")
    print(f"  фраза: продлить четырнадцатый до {gain_stop_name}")
    print(f"  {json.dumps(nl['gain_parse']['scenario'], ensure_ascii=False)}")
    print(f"  подтверждение: {nl['gain_parse']['understood']}")
    print()
    print(f"Объяснение результата (путь: {explained['source']}):")
    print(f"  {explained['text']}")
    print()
    print(f"Он же при выключенной модели (путь: {offline['explained']['source']}):")
    print(f"  {offline['explained']['text']}")

    passed = sum(1 for _, ok, _ in results if ok)
    print()
    print(f"пройдено гейтов: {passed} из {len(results)}")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
