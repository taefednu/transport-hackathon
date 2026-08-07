# Образ для хостинга: ядро и интерфейс одним адресом.
#
# Фронтенд собирается заранее и кладётся в frontend/dist, артефакты пайплайна —
# в backend/data/build. В образ не входят ни выданный датасет, ни дамп OSM, ни
# ключи: сборочный контекст готовится отдельно (см. scripts/make_deploy.sh).

FROM python:3.12-slim

WORKDIR /srv

# Зависимости ставятся отдельным слоем: пересборка кода не тянет за собой
# переустановку scipy и polars
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/data/build ./data/build
COPY frontend/dist ./static

# ROOT в config.py — родитель каталога app, то есть /srv. Пути к артефактам и
# к статике складываются сами, переопределять их нечем и незачем.
ENV PYTHONUNBUFFERED=1

# Порт задаёт хостинг. Форма с оболочкой нужна, чтобы $PORT подставился.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
