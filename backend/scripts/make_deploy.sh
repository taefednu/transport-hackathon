#!/usr/bin/env bash
# Готовит каталог для выкладки на хостинг.
#
# Копируется ровно то, что нужно образу. Выданный датасет, дамп OSM, слой
# Kontur, ключи, node_modules и виртуальное окружение сюда не попадают — не
# потому, что «мы аккуратные», а потому, что каталог собирается перечислением,
# а не исключением: чего нет в списке, того нет в выкладке. Это важно вдвойне,
# потому что содержимое каталога уезжает в git репозитория деплоя.
#
#   bash backend/scripts/make_deploy.sh <куда>
#
# Фронтенд собирать заранее не надо: его собирает первая стадия Dockerfile.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${1:?укажи каталог назначения}"

# артефакты, которые сервер читает при старте (app/store.py). Остальное из
# data/build — промежуточные шаги пайплайна, в рантайме они не нужны и в
# репозиторий деплоя не едут
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

# исходники фронтенда, из которых собирается интерфейс внутри образа
FRONTEND_FILES=(
  package.json
  package-lock.json
  index.html
  tsconfig.json
  vite.config.ts
)

mkdir -p "$OUT"
# .git репозитория деплоя переживает пересборку, остальное — нет
find "$OUT" -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +
mkdir -p "$OUT/backend/app" "$OUT/backend/data/build" "$OUT/frontend/src"

cp "$ROOT/Dockerfile" "$OUT/Dockerfile"
cp "$ROOT/backend/requirements.txt" "$OUT/backend/requirements.txt"
cp "$ROOT/backend"/app/*.py "$OUT/backend/app/"

for name in "${FRONTEND_FILES[@]}"; do
  cp "$ROOT/frontend/$name" "$OUT/frontend/$name"
done
# исходники интерфейса целиком: .env фронта сюда не попадает, он лежит уровнем выше
cp "$ROOT/frontend"/src/* "$OUT/frontend/src/"

for name in "${ARTIFACTS[@]}"; do
  src="$ROOT/backend/data/build/$name"
  if [ ! -f "$src" ]; then
    echo "нет артефакта пайплайна: $src" >&2
    exit 1
  fi
  cp "$src" "$OUT/backend/data/build/$name"
done

# Проверка, а не надежда: в выкладке не должно быть ни ключей, ни симлинка на
# датасет, ни сырых источников. Проверяется до того, как каталог уедет в git.
LEFTOVER=$(find "$OUT" -path "$OUT/.git" -prune -o \
  \( -name '.env' -o -name '*.pbf' -o -name '*.gpkg' -o -name '*.csv' -o -type l \) -print)
if [ -n "$LEFTOVER" ]; then
  echo "в выкладке оказалось лишнее:" >&2
  echo "$LEFTOVER" >&2
  exit 1
fi

echo "готово: $OUT ($(du -sh --exclude=.git "$OUT" | cut -f1))"
