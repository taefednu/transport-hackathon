#!/usr/bin/env bash
# Готовит каталог для выкладки на хостинг.
#
# Копируется ровно то, что нужно работающему серверу. Выданный датасет, дамп
# OSM, слой Kontur, ключи и виртуальное окружение сюда не попадают — не потому,
# что «мы аккуратные», а потому, что каталог собирается перечислением, а не
# исключением: чего нет в списке, того нет в выкладке.
#
#   bash backend/scripts/make_deploy.sh <куда>
#
# Фронтенд должен быть собран заранее:
#   cd frontend && VITE_API_BASE= npm run build

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${1:?укажи каталог назначения}"

# артефакты, которые сервер читает при старте (app/store.py). Остальное из
# data/build — промежуточные шаги пайплайна, в рантайме они не нужны
ARTIFACTS=(
  stops.parquet
  stop_hexes.parquet
  hex_access.parquet
  hexes.parquet
  hexes_buildings.parquet
  routes.parquet
  route_stops.parquet
  segment_time.parquet
  city_speed_fallback.parquet
  headway_actual.parquet
  holes.parquet
  segment_routes.parquet
  buildings.parquet
  walk_graph.pkl
  tashkent_boundary.geojson
)

if [ ! -d "$ROOT/frontend/dist" ]; then
  echo "нет frontend/dist — собери фронтенд: cd frontend && VITE_API_BASE= npm run build" >&2
  exit 1
fi

rm -rf "$OUT"
mkdir -p "$OUT/backend/app" "$OUT/backend/data/build" "$OUT/frontend"

cp "$ROOT/Dockerfile" "$OUT/Dockerfile"
cp "$ROOT/backend/requirements.txt" "$OUT/backend/requirements.txt"
cp "$ROOT/backend"/app/*.py "$OUT/backend/app/"
cp -r "$ROOT/frontend/dist" "$OUT/frontend/dist"

for name in "${ARTIFACTS[@]}"; do
  src="$ROOT/backend/data/build/$name"
  if [ ! -f "$src" ]; then
    echo "нет артефакта пайплайна: $src" >&2
    exit 1
  fi
  cp "$src" "$OUT/backend/data/build/$name"
done

# Проверка, а не надежда: в выкладке не должно быть ни ключей, ни симлинка на
# датасет, ни тяжёлых внешних источников
if find "$OUT" \( -name '.env' -o -name '*.pbf' -o -name '*.gpkg' -o -name '*.csv' -o -type l \) -print -quit | grep -q .; then
  echo "в выкладке оказалось лишнее — смотри вывод find выше" >&2
  find "$OUT" \( -name '.env' -o -name '*.pbf' -o -name '*.gpkg' -o -name '*.csv' -o -type l \)
  exit 1
fi

echo "готово: $OUT ($(du -sh "$OUT" | cut -f1))"
