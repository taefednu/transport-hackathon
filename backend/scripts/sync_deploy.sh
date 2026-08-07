#!/usr/bin/env bash
# Отправляет текущее состояние продукта в приватный репозиторий деплоя.
#
# Репозиториев два, и это осознанно. Публичный (taefednu/transport-hackathon) —
# только код, его отдают жюри. Приватный (taefednu/qatnov-deploy) — тот же код
# плюс посчитанные артефакты пайплайна: без них сервер не поднимется, а в
# публичный они попасть не могут, потому что производны от датасета, выданного
# только для трека 3.
#
# Railway следит за веткой main приватного репозитория: пуш отсюда = выкладка.
#
#   bash backend/scripts/sync_deploy.sh ["сообщение коммита"]

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${QATNOV_DEPLOY_DIR:-$HOME/projects/qatnov-deploy}"
REMOTE="${QATNOV_DEPLOY_REMOTE:-git@github.com:taefednu/qatnov-deploy.git}"
MESSAGE="${1:-выкладка: $(cd "$ROOT" && git log -1 --format='%h %s')}"

bash "$ROOT/backend/scripts/make_deploy.sh" "$WORK"

cd "$WORK"
if [ ! -d .git ]; then
  git init -q -b main
  git remote add origin "$REMOTE"
fi

# Репозиторий деплоя собирается перечислением, поэтому .gitignore здесь не
# фильтр, а страховка: то, чего в make_deploy.sh нет, не должно попасть даже
# если окажется в каталоге случайно.
cat > .gitignore <<'IGNORE'
.env
*.pbf
*.gpkg
*.csv
node_modules/
dist/
__pycache__/
IGNORE

# Открывший этот репозиторий должен сразу понять, что он сгенерирован, и где
# лежит источник: иначе правку сделают здесь, и её сотрёт следующая пересборка.
cat > README.md <<README
# QATNOV — репозиторий выкладки

**Этот репозиторий собирается скриптом. Править здесь нечего: любая правка
будет стёрта следующей пересборкой.**

Исходники: <https://github.com/taefednu/transport-hackathon> (публичные, MIT).
Здесь тот же код плюс посчитанные артефакты пайплайна — без них сервер не
поднимется, а в публичный репозиторий они попасть не могут: они производны от
датасета, выданного только для трека 3 National Transport Hackathon 2026.

Сырых данных здесь нет: ни строки транзакций, ни одного идентификатора карты.
Только агрегаты, геометрия из OpenStreetMap и слой населения.

Railway следит за веткой \`main\`: пуш сюда = выкладка на
<https://qatnov-production.up.railway.app>.

Пересобирается из исходников командой:

\`\`\`bash
bash backend/scripts/sync_deploy.sh
\`\`\`
README

git add -A
if git diff --cached --quiet; then
  echo "изменений нет — выкладывать нечего"
  exit 0
fi
git commit -q -m "$MESSAGE"
git push -q origin main
echo "отправлено в $REMOTE (ветка main) — Railway подхватит сам"
