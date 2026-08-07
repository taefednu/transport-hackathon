#!/usr/bin/env bash
# Обновляет открытый репозиторий копией этого, без данных.
#
# Правило простое: **весь код приватного репозитория уезжает наружу как есть**.
# Не уезжает ровно два класса файлов, и оба перечислены ниже явно:
#
#   1. данные — посчитанные артефакты пайплайна;
#   2. внутренние документы команды — база знаний, ТЗ, спека карты, отчёты.
#
# Раньше список работал наоборот: перечислялось то, что публикуется. Это было
# безопаснее, но означало, что новый файл кода наружу не попадёт, пока о нём
# не вспомнят. Требование «публичный повторяет код приватного один в один»
# такого молчания не допускает, поэтому список стал списком исключений — а
# чтобы он не стал дырой, в конце стоит сверка: состав публичного обязан
# совпасть с составом приватного минус исключения, иначе публикация падает.
#
#   bash backend/scripts/publish_public.sh ["сообщение коммита"]

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${QATNOV_PUBLIC_DIR:-$HOME/projects/qatnov-public}"
REMOTE="${QATNOV_PUBLIC_REMOTE:-git@github.com:taefednu/transport-hackathon.git}"
MESSAGE="${1:-обновление кода}"

# Что наружу не уезжает. Шаблоны сверяются с путём файла от корня репозитория.
# frontend-ref.png — референс чужого дизайна, происхождение неизвестно: это не
# наш материал, и в репозиторий под своей лицензией он попадать не должен.
EXCLUDE_REGEX='^(backend/data/|knowledge/|CLAUDE\.md$|NIGHT_REPORT\.md$|.*_tz\.md$|.*_spec\.md$|frontend-ref\.png$)'

cd "$ROOT"
# берём именно отслеживаемые файлы: в публикацию не может попасть ничего, чего
# нет в приватном репозитории — ни временного, ни забытого в рабочем каталоге
mapfile -t PUBLISH < <(git ls-files | grep -Ev "$EXCLUDE_REGEX")
if [ "${#PUBLISH[@]}" -eq 0 ]; then
  echo "нечего публиковать: список файлов пуст" >&2
  exit 1
fi

# --- собираем копию ------------------------------------------------------

if [ -d "$WORK/.git" ]; then
  git -C "$WORK" fetch -q origin main
  git -C "$WORK" checkout -q main 2>/dev/null || git -C "$WORK" checkout -q -b main origin/main
  git -C "$WORK" reset -q --hard origin/main
else
  rm -rf "$WORK"
  git clone -q "$REMOTE" "$WORK"
  git -C "$WORK" checkout -q main
fi

# всё, кроме .git, вычищается: удалённый в приватном файл обязан исчезнуть и здесь
find "$WORK" -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +

for path in "${PUBLISH[@]}"; do
  mkdir -p "$WORK/$(dirname "$path")"
  cp "$ROOT/$path" "$WORK/$path"
done

# --- проверка перед публикацией -----------------------------------------
#
# Две проверки, и они про разное. Первая — что наружу не уехало запрещённое.
# Вторая — что наружу уехало всё остальное: список исключений мог оказаться
# шире, чем задумано, и тогда публичный тихо отстанет от приватного.

FORBIDDEN=$(cd "$WORK" && find . -path ./.git -prune -o \
  \( -name '.env' -o -name '*.parquet' -o -name '*.pkl' -o -name '*.csv' \
     -o -name '*.pbf' -o -name '*.gpkg' -o -type l \) -print)
if [ -n "$FORBIDDEN" ]; then
  echo "в публикацию попало то, чего там быть не должно:" >&2
  echo "$FORBIDDEN" >&2
  exit 1
fi

MISSING=$(comm -23 \
  <(printf '%s\n' "${PUBLISH[@]}" | sort) \
  <(cd "$WORK" && find . -path ./.git -prune -o -type f -print | sed 's|^\./||' | sort))
if [ -n "$MISSING" ]; then
  echo "до публичного не доехали файлы приватного:" >&2
  echo "$MISSING" >&2
  exit 1
fi

# --- коммит --------------------------------------------------------------

cd "$WORK"
git add -A
if git diff --cached --quiet; then
  echo "открытый репозиторий уже совпадает с приватным — публиковать нечего"
  exit 0
fi
git commit -q -m "$MESSAGE"
git push -q origin main
echo "опубликовано: $(git ls-files | wc -l) файлов в $REMOTE"
