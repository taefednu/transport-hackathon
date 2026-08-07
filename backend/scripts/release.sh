#!/usr/bin/env bash
# Выкладка: сначала стенд, потом открытый репозиторий.
#
# Порядок не косметический. Открытый репозиторий — витрина: код в нём должен
# быть тем, который уже работает. Поэтому публикация происходит только после
# того, как стенд собрал и поднял именно этот коммит. Упала сборка — открытый
# репозиторий не трогается вовсе.
#
#   bash backend/scripts/release.sh ["сообщение коммита"]
#
# Переменные:
#   QATNOV_RELEASE_TIMEOUT  сколько ждать сборку, секунд (по умолчанию 900)
#   QATNOV_SKIP_PUBLIC=1    обновить только стенд, публикацию пропустить

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TIMEOUT="${QATNOV_RELEASE_TIMEOUT:-900}"
SERVICE="${QATNOV_RAILWAY_SERVICE:-qatnov}"
MESSAGE="${1:-выкладка}"

cd "$ROOT"

# --- проверка типов ------------------------------------------------------
#
# Фронтенд собирается внутри образа, и сборка начинается с проверки типов.
# Значит правка, которая не компилируется, роняет стенд через две минуты после
# пуша. Проверяем здесь: те же секунды, но до выкладки и с понятной причиной.
if [ -d "$ROOT/frontend/node_modules" ]; then
  echo "→ проверка типов фронтенда"
  if ! (cd "$ROOT/frontend" && npm run typecheck --silent); then
    echo "" >&2
    echo "фронтенд не проходит проверку типов — выкладка остановлена." >&2
    exit 1
  fi
fi

# --- шаг 1: стенд --------------------------------------------------------
#
# Стенд собирается из коммитов приватного репозитория, поэтому незакоммиченная
# правка на него не попадёт. Коммитим сами, но вслух: молча уносить в выкладку
# то, чего человек не видел, нельзя.
echo "→ шаг 1 из 2: стенд"
if [ -n "$(git status --porcelain)" ]; then
  echo "   в выкладку идут изменения:"
  git status --porcelain | sed 's/^/     /'
  git add -A
  git commit -q -m "$MESSAGE"
fi

git push -q origin HEAD
SHA="$(git rev-parse HEAD)"
echo "   коммит выкладки: ${SHA:0:7}"

# --- шаг 2: ждём именно этот коммит --------------------------------------
#
# Ждём не «какую-нибудь удачную сборку», а сборку этого коммита. Иначе проверка
# была бы пустой: сразу после пуша последней удачной числится предыдущая, и
# публикация уехала бы, ничего не проверив.
echo "→ жду, пока стенд соберёт ${SHA:0:7} (до $((TIMEOUT / 60)) мин)"
DEADLINE=$(( SECONDS + TIMEOUT ))
STATUS=""
while [ $SECONDS -lt $DEADLINE ]; do
  STATUS="$(railway deployment list --service "$SERVICE" --json 2>/dev/null \
    | SHA="$SHA" python3 -c '
import json, os, sys
sha = os.environ["SHA"]
try:
    rows = json.load(sys.stdin)
except Exception:
    print(""); sys.exit()
rows = rows if isinstance(rows, list) else rows.get("deployments", [])
for row in rows:
    if (row.get("meta") or {}).get("commitHash") == sha:
        print(row.get("status") or ""); break
else:
    print("")
')"
  case "$STATUS" in
    SUCCESS) echo "   сборка ${SHA:0:7}: SUCCESS"; break ;;
    FAILED|CRASHED)
      echo "" >&2
      echo "стенд не принял ${SHA:0:7}: $STATUS" >&2
      echo "открытый репозиторий не тронут. Логи: railway logs --service $SERVICE" >&2
      exit 1 ;;
    *) printf '   %s...\n' "${STATUS:-ждём}"; sleep 15 ;;
  esac
done

if [ "$STATUS" != "SUCCESS" ]; then
  echo "" >&2
  echo "не дождался сборки ${SHA:0:7} за $((TIMEOUT / 60)) мин. Открытый репозиторий не тронут." >&2
  exit 1
fi

# --- шаг 3: открытый репозиторий -----------------------------------------

if [ "${QATNOV_SKIP_PUBLIC:-}" = "1" ]; then
  echo "→ публикация пропущена (QATNOV_SKIP_PUBLIC=1)"
  exit 0
fi

echo "→ шаг 2 из 2: публикация копии без данных"
bash "$ROOT/backend/scripts/publish_public.sh" "$MESSAGE" | sed 's/^/   /'
echo "готово: стенд обновлён, код опубликован"
