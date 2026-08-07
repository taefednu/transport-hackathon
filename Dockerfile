# Образ для хостинга: ядро и интерфейс одним адресом.
#
# Фронтенд собирается здесь же, из исходников. Так пуш в ветку деплоя обновляет
# интерфейс сам — иначе выложить можно было бы код без пересборки фронта и не
# заметить, что на стенде висит старая сборка.
#
# Артефакты пайплайна (backend/data/build) в образ приходят из репозитория
# деплоя. Выданного датасета, дампа OSM и ключей там нет — каталог собирается
# перечислением, см. backend/scripts/make_deploy.sh.

FROM node:20-slim AS ui

WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# пустой адрес ядра = тот же origin: интерфейс и API раздаёт один сервер
ENV VITE_API_BASE=""
RUN npm run build


FROM python:3.12-slim

WORKDIR /srv

# Зависимости — отдельным слоем: правка кода не тянет переустановку scipy и polars
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/data/build ./data/build
COPY --from=ui /ui/dist ./static

# ROOT в config.py — родитель каталога app, то есть /srv. Пути к артефактам и
# к статике складываются сами, переопределять их нечем и незачем.
ENV PYTHONUNBUFFERED=1

# Порт задаёт хостинг. Форма с оболочкой нужна, чтобы $PORT подставился.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
