#!/usr/bin/env bash
# Полный пайплайн с нуля. Каждый шаг идемпотентен и печатает число строк на выходе.
# Внешние источники (OSM PBF, Kontur) должны лежать в data/external — см. NIGHT_REPORT.md.
set -euo pipefail

cd "$(dirname "$0")"
PY="../.venv/bin/python"

for step in 00_boundary 01_stops 02_walk_graph 03_population \
            04_routes 05_speeds 06_segment_time \
            07_headway 08_parallel 09_holes; do
  echo "=== ${step} ==="
  "$PY" "${step}.py"
done

echo "=== пайплайн собран ==="
