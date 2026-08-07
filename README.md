# Цифровой двойник маршрутной сети — песочница «что если»

Веб-инструмент, в котором изменение маршрутной сети Ташкента пересчитывается в людей:
продлить маршрут, добавить остановку, снять дублирование — и увидеть, сколько человек
получает доступ к транспорту, сколько теряет и как меняется время до ближайшей остановки.

Проект National Transport Hackathon 2026, трек 3 (данные Минтранса и Яндекса).
Полная формулировка — [`knowledge/product.md`](knowledge/product.md).

## Раскладка

```
CLAUDE.md          инструкции для Claude Code — читать первым
knowledge/         база знаний, единственный источник правды
  facts.md         проверенные факты: хакатон, данные, город, цифры для питча
  product.md       формулировка продукта, объём MVP, границы
  data.md          справочник данных: файлы, ловушки, ключи связи, разрывы
  architecture.md  устройство кода (пока пусто)
  decisions.md     лог решений
  open-questions.md что не решено
  glossary.md      термины
  lessons.md       классы ошибок, которые уже стоили времени
qatnov_backend_tz.md  ТЗ на бэкенд
NIGHT_REPORT.md       отчёт о сборке бэкенда: гейты, числа, что не сделано
data/
  raw/             СИМЛИНК на выданный датасет. Read-only, не коммитится
  external/        открытые источники: OSM PBF, Kontur Population. Не коммитится
  build/           артефакты пайплайна, parquet. Не коммитится
app/               FastAPI и расчёты
scripts/           пайплайн (00..09), run_all.sh, verify_gates.py
```

## Запуск

```bash
.venv/bin/python -m uvicorn app.main:app --port 8024   # сервер
.venv/bin/python scripts/verify_gates.py 8024          # приёмка всех гейтов ТЗ
bash scripts/run_all.sh                                # пересобрать пайплайн (~5 мин)
```

Внешние источники кладутся в `data/external/`: `uzbekistan-latest.osm.pbf` (Geofabrik) и
`kontur_population_UZ.gpkg` (HDX). Overpass API из этой сети недоступен — всё из локального дампа.

## Данные

`data/raw` указывает на `/mnt/d/Transport Hackathon/track3_dataset`. Если симлинк битый
(другая машина, другой путь) — пересоздать:

```bash
ln -sfn "<путь до track3_dataset>" data/raw
```

Датасет выдан **только для трека 3**: не распространять вне команды, не публиковать,
не пытаться связать `pan_hash`/`masked_pan` с людьми. `.gitignore` закрывает весь `data/`
и все табличные/геоформаты — если понадобится закоммитить маленький выходной `.geojson`,
добавлять только через `git add -f` и только осознанно, убедившись, что это агрегат.

Разбор данных уже сделан: [`knowledge/data.md`](knowledge/data.md) и `data/raw/track3_analysis.md`.
Читать разбор до того, как открывать файлы.

## Начало сессии

Claude читает `CLAUDE.md`, затем `knowledge/facts.md` → `product.md` → `data.md` →
`open-questions.md`. Стек ещё не подтверждён — см. `decisions.md`.
