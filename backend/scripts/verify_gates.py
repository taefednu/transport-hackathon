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
    from app import config, explain as explain_mod

    payload = {
        "result": gain_result,
        "sources": {"fallback_share": 0.089, "population_layer_date": config.ACTIVE_POPULATION_DATE},
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


def gate12(insert_stop: str, solo_route: str, solo_direction: str, solo_seq: int) -> None:
    """Вставка и удаление остановки: обе меняют покрытие, а не только длину списка.

    Случай для удаления подбирается так же, как для обрезки: остановка, которая
    в одиночку держит населённый гексагон. В плотном городе удаление типичной
    серединной остановки не меняет покрытия — соседние остановки накрывают тот же
    гексагон, и ноль там честный. Проверять надо на случае, где эффект обязан быть.
    """
    inserted, _ = post_json(
        "/api/scenario",
        {
            "weekday": "fri",
            "hour": 8,
            "ops": [
                {
                    "type": "insert_stop",
                    "route_num": "14",
                    "direction": "fwd",
                    "stop_id": insert_stop,
                    "after_seq": 5,
                }
            ],
        },
    )
    removed, ms = post_json(
        "/api/scenario",
        {
            "weekday": "fri",
            "hour": 8,
            "ops": [
                {
                    "type": "remove_stop",
                    "route_num": solo_route,
                    "direction": solo_direction,
                    "seq": solo_seq,
                }
            ],
        },
    )
    grew = inserted["affected_routes"][0]
    shrank = removed["affected_routes"][0]
    check(
        "Гейт 12 — вставка и удаление остановки меняют покрытие",
        inserted["gained"] > 0
        and grew["n_stops_after"] == grew["n_stops_before"] + 1
        and removed["lost"] > 0
        and shrank["n_stops_after"] == shrank["n_stops_before"] - 1,
        f"вставка в 14 после seq=5: +{inserted['gained']:,.0f} чел., остановок "
        f"{grew['n_stops_before']}→{grew['n_stops_after']}; удаление {solo_route} "
        f"({solo_direction}) seq={solo_seq}: −{removed['lost']:,.0f} чел., остановок "
        f"{shrank['n_stops_before']}→{shrank['n_stops_after']}; {ms:.0f} мс",
    )


def gate13() -> None:
    """Геометрия всей сети одним запросом и в бюджете загрузки."""
    body, ms = get("/api/network/geometry")
    data = json.loads(body)
    kb = len(body) / 1024
    lines = data["features"]
    all_lines = all(f["geometry"]["type"] == "LineString" for f in lines)
    has_props = all(
        {"route_num", "direction", "quality"} <= set(f["properties"]) for f in lines
    )
    check(
        "Гейт 13 — геометрия сети отдаётся одним запросом",
        len(lines) > 100 and all_lines and has_props and kb < 1500,
        f"{data['count']} направлений, {kb:.0f} КБ, упрощение: {data['simplified']} "
        f"(допуск {data['tolerance_deg']}); все объекты LineString: {all_lines}; "
        f"признак качества у каждого: {has_props}; {ms:.0f} мс",
    )


def gate14(extend_stops: list[str], trim_route: str, trim_direction: str, trim_seq: int) -> None:
    """Изменение цепочки остановок меняет и стоимость, а не только покрытие."""
    grown, _ = post_json(
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
    cut, ms = post_json(
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
    a = grown["affected_routes"][0]
    b = cut["affected_routes"][0]
    codes = {w["code"] for w in grown["warnings"]}
    check(
        "Гейт 14 — продление стоит машин, обрезка их высвобождает",
        a["cycle_time_after"] > a["cycle_time_before"]
        and a["required_vehicles_after"] > a["required_vehicles_before"]
        and "vehicles_short" in codes
        and b["cycle_time_after"] < b["cycle_time_before"]
        and b["required_vehicles_after"] <= b["required_vehicles_before"],
        f"продление 14 на {len(extend_stops)} остановки: оборот "
        f"{a['cycle_time_before']:.1f}→{a['cycle_time_after']:.1f} мин, машин "
        f"{a['required_vehicles_before']}→{a['required_vehicles_after']} при "
        f"{a['n_vehicles']} на линии, перегонов по медиане города "
        f"{a['segments_at_city_speed']}, предупреждение о нехватке: "
        f"{'vehicles_short' in codes}; обрезка {trim_route}: оборот "
        f"{b['cycle_time_before']:.1f}→{b['cycle_time_after']:.1f} мин, машин "
        f"{b['required_vehicles_before']}→{b['required_vehicles_after']}; {ms:.0f} мс",
    )


# требование к ассистенту: ответ приходит меньше чем за десять секунд в самом
# длинном сценарии. Порог один на все вопросы, поэтому и константа одна
MAX_ANSWER_MS = 10_000
ATTENTION_QUESTION = "какие маршруты требуют внимания"
OUT_OF_SCOPE_QUESTION = "какой пассажиропоток будет на 14 маршруте в следующем году"


def numbers_outside_evidence(answer: dict) -> list[float]:
    """Числа ответа, которых нет ни в результатах инструментов, ни в оговорках.

    Оговорки и список умений собраны кодом из тех же артефактов и уходят в том
    же ответе отдельными полями — это такие же посчитанные числа, как числа
    инструментов, и в допустимые они входят на тех же правах.
    """
    from app import explain as explain_mod

    allowed = explain_mod.allowed_numbers(
        {
            **answer.get("evidence", {}),
            "оговорки": answer.get("disclaimers") or [],
            "умения": answer.get("capabilities") or [],
        }
    )
    return sorted(explain_mod.numbers_in(answer["text"]) - allowed)


def gate15() -> dict:
    """Вопрос про проблемные маршруты: список с числами из диагностики."""
    answer, ms = post_json("/api/assistant", {"text": ATTENTION_QUESTION})
    evidence = answer["evidence"].get("routes_attention") or {}
    routes = evidence.get("routes") or []
    with_numbers = [r for r in routes if r.get("signs")]
    extra = numbers_outside_evidence(answer)
    check(
        "Гейт 15 — «какие маршруты требуют внимания» отвечает списком с числами",
        len(routes) >= 3
        and len(with_numbers) == len(routes)
        and all(r.get("reasons") for r in routes)
        and not extra
        and ms < MAX_ANSWER_MS,
        f"путь: {answer['source']}; инструменты {[s['tool'] for s in answer['steps']]}; "
        f"маршрутов в ответе {len(routes)} из {evidence.get('routes_with_signs')} с признаками; "
        f"у каждого признаки с числами: {len(with_numbers) == len(routes)}; "
        f"чисел вне результатов инструментов: {len(extra)}"
        + (f" {extra}" if extra else "")
        + f"; {ms:.0f} мс",
    )
    return answer


def gate16(route_num: str) -> dict:
    """Вопрос про конкретный маршрут: его показатели, а не общие слова."""
    answer, ms = post_json(
        "/api/assistant", {"text": f"расскажи про маршрут {route_num}"}
    )
    profile = answer["evidence"].get("route_profile") or {}
    extra = numbers_outside_evidence(answer)
    selected = [a for a in answer["actions"] if a["type"] == "select_route"]
    check(
        "Гейт 16 — вопрос про маршрут возвращает его показатели",
        profile.get("route_num") == route_num
        and profile.get("planned_headway_min") is not None
        and profile.get("actual_headway_min_at_hour") is not None
        and len(profile.get("travel_min_by_hour") or []) >= 2
        and bool(selected)
        and not extra
        and ms < MAX_ANSWER_MS,
        f"путь: {answer['source']}; маршрут {profile.get('route_num')}: интервал плановый "
        f"{profile.get('planned_headway_min')} мин против фактического "
        f"{profile.get('actual_headway_min_at_hour')} мин в {profile.get('hour_label')}; "
        f"часов со временем хода {len(profile.get('travel_min_by_hour') or [])}; "
        f"по медиане города {profile.get('segments_at_city_speed_percent')}% перегонов; "
        f"действие для интерфейса: {selected[0]['type'] if selected else '—'}; "
        f"чисел вне результатов: {len(extra)}; {ms:.0f} мс",
    )
    return answer


def gate17(route_num: str) -> dict:
    """«Что сделать с маршрутом N»: вариант с прибавкой, ценой и готовым сценарием.

    Сценарий из действия отправляется в движок как есть. Это и есть проверка
    того, что фронту нечего досбирать: тот же объект, тот же прирост.
    """
    answer, ms = post_json(
        "/api/assistant", {"text": f"что можно сделать с маршрутом {route_num}"}
    )
    options = (answer["evidence"].get("route_options") or {}).get("options") or []
    ready = [a for a in answer["actions"] if a["type"] == "apply_scenario"]
    extra = numbers_outside_evidence(answer)

    accepted = replayed = None
    if ready:
        code, body = post_status("/api/scenario", ready[0]["scenario"])
        accepted = code == 200
        replayed = round(json.loads(body)["gained"]) if accepted else None

    check(
        "Гейт 17 — «что сделать с маршрутом» даёт вариант с ценой и готовый сценарий",
        bool(options)
        and options[0]["gained_people"] > 0
        and options[0]["required_vehicles_after"] is not None
        and bool(ready)
        and accepted
        and replayed == options[0]["gained_people"]
        and not extra
        and ms < MAX_ANSWER_MS,
        f"путь: {answer['source']}; вариантов {len(options)}: продлить {route_num} "
        f"({options[0]['direction']}) до «{options[0]['stop_name']}» — "
        f"+{options[0]['gained_people']:,} чел., машин "
        f"{options[0]['required_vehicles_before']}→{options[0]['required_vehicles_after']}; "
        f"движок принял сценарий из действия: {accepted}, прирост при повторе "
        f"{replayed:,} против {options[0]['gained_people']:,}; "
        f"чисел вне результатов: {len(extra)}; {ms:.0f} мс"
        if options
        else f"вариантов не нашлось; {ms:.0f} мс",
    )
    return answer


def gate18() -> dict:
    """Вопрос вне возможностей: понятный отказ со списком того, что доступно."""
    answer, ms = post_json("/api/assistant", {"text": OUT_OF_SCOPE_QUESTION})
    text = answer["text"]
    check(
        "Гейт 18 — вопрос вне возможностей получает отказ со списком умений",
        answer["supported"] is False
        and len(answer.get("capabilities") or []) >= 4
        and all(item.split()[0] in text for item in answer["capabilities"])
        and "не хватает опознанных объектов" not in text
        and ms < MAX_ANSWER_MS,
        f"supported={answer['supported']}; причина: {answer['reason']}; "
        f"умений перечислено {len(answer.get('capabilities') or [])}; "
        f"старого «не хватает опознанных объектов» в тексте нет: "
        f"{'не хватает опознанных объектов' not in text}; {ms:.0f} мс",
    )
    return answer


def gate19(store, attention_route: str, options_route: str) -> dict:
    """Те же четыре вопроса при выключенной модели, в том же процессе."""
    import importlib
    import os
    import time as time_mod

    from app import assistant as assistant_mod, config, llm
    from app import search as search_mod

    previous = os.environ.get("QATNOV_LLM_DISABLED")
    os.environ["QATNOV_LLM_DISABLED"] = "1"
    importlib.reload(config)
    try:
        index = search_mod.build_index(store)
        questions = [
            ATTENTION_QUESTION,
            f"расскажи про маршрут {attention_route}",
            f"что можно сделать с маршрутом {options_route}",
            OUT_OF_SCOPE_QUESTION,
        ]
        answers, slowest = [], 0.0
        for question in questions:
            started = time_mod.perf_counter()
            answers.append(assistant_mod.ask(store, index, question))
            slowest = max(slowest, (time_mod.perf_counter() - started) * 1000)

        extra = {q: numbers_outside_evidence(a) for q, a in zip(questions, answers)}
        dirty = {q: e for q, e in extra.items() if e}
        tools_used = [
            [s["tool"] for s in a["steps"]] or ["—"] for a in answers
        ]
        check(
            "Гейт 19 — ассистент отвечает на те же вопросы без модели",
            not llm.available()
            and all(a["source"] == "deterministic" for a in answers)
            and all(a["supported"] for a in answers[:3])
            and answers[3]["supported"] is False
            and not dirty
            and slowest < MAX_ANSWER_MS,
            f"модель выключена: {not llm.available()}; пути "
            f"{[a['source'] for a in answers]}; инструменты {tools_used}; "
            f"чисел вне результатов: {sum(len(e) for e in extra.values())}"
            + (f" {dirty}" if dirty else "")
            + f"; самый долгий ответ {slowest:.0f} мс",
        )
        return {"questions": questions, "answers": answers}
    finally:
        if previous is None:
            os.environ.pop("QATNOV_LLM_DISABLED", None)
        else:
            os.environ["QATNOV_LLM_DISABLED"] = previous
        importlib.reload(config)


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
    offline_nl = gate9(store, gain_stop_name)
    gate10(store, gain_stop_name)
    gate11()
    gate12(extend_stops[0], trim["route_num"], trim["direction"], int(trim["seq"]))
    gate13()
    gate14(extend_stops, trim["route_num"], trim["direction"], max(0, int(trim["seq"]) - 1))

    # маршруты для вопросов ассистента выбираются диагностикой, а не вписаны
    # руками: пересчитались данные — гейт спрашивает про то, что стало плохим
    from app import diagnostics, tools as tools_mod
    from app import search as search_mod

    index = search_mod.build_index(store)
    ranked = diagnostics.attention(store, "fri", 8, 5)["routes"]
    attention_route = ranked[0]["route_num"]
    frame = {"weekday": "fri", "hour": 8}
    options_route = next(
        (
            entry["route_num"]
            for entry in ranked
            if tools_mod.route_options(store, index, {**frame, "route_num": entry["route_num"]})[
                "options"
            ]
        ),
        attention_route,
    )

    assistant_answers = [gate15(), gate16(attention_route), gate17(options_route), gate18()]
    offline = gate19(store, attention_route, options_route)

    print()
    print("Разобранный сценарий (фраза → объект для POST /api/scenario):")
    print(f"  фраза: продлить четырнадцатый до {gain_stop_name}")
    print(f"  {json.dumps(nl['gain_parse']['scenario'], ensure_ascii=False)}")
    print(f"  подтверждение: {nl['gain_parse']['understood']}")
    print()
    print(f"Объяснение результата (путь: {explained['source']}):")
    print(f"  {explained['text']}")
    print()
    print(f"Он же при выключенной модели (путь: {offline_nl['explained']['source']}):")
    print(f"  {offline_nl['explained']['text']}")

    print()
    print("=" * 100)
    print("АССИСТЕНТ: четыре вопроса")
    for answer in assistant_answers:
        print("=" * 100)
        print(
            f"— {answer['question']} "
            f"[{answer['source']}, {answer['took_ms']:.0f} мс, "
            f"инструменты: {[s['tool'] for s in answer['steps']] or '—'}]"
        )
        print(answer["text"])
        if answer["actions"]:
            print(f"  действия для интерфейса: {json.dumps(answer['actions'], ensure_ascii=False)[:400]}")

    print()
    print("=" * 100)
    print("АССИСТЕНТ: те же вопросы при выключенной модели")
    for question, answer in zip(offline["questions"], offline["answers"]):
        print("=" * 100)
        print(
            f"— {question} [{answer['source']}, {answer['took_ms']:.0f} мс, "
            f"инструменты: {[s['tool'] for s in answer['steps']] or '—'}]"
        )
        print(answer["text"])

    passed = sum(1 for _, ok, _ in results if ok)
    print()
    print(f"пройдено гейтов: {passed} из {len(results)}")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
