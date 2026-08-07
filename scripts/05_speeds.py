"""Шаг 5. Скорости движения по часам из данных Яндекса.

Вход:  data/raw/data/yandex/traffic_dedup.csv
Выход: data/build/segment_speed.parquet — segment_id, lat, lon, weekday_type, hour, speed_kmh
       data/build/city_speed_fallback.parquet — weekday_type, hour, median_speed_kmh

Расхождение с ТЗ, идём за данными: в выданном файле нет геометрии участка и нет
его идентификатора — есть точка наблюдения (lat, lon) с показателями по часам.
Поэтому `segment_id` строится нами из координаты точки, а привязка к трассе
маршрута делается по расстоянию (шаг 6), а не по общему идентификатору.

Скорость берём медианную (`speed_median`), а не среднюю: средняя по участку
чувствительна к единичным выбросам, а время хода считается по типичной скорости.
"""

import _bootstrap  # noqa: F401

import polars as pl

from app.config import CITY_SPEED_FALLBACK_PARQUET, SEGMENT_SPEED_PARQUET, TRAFFIC_CSV

# как день недели назван в файле → как мы его называем
DAY_TO_WEEKDAY = {
    "monday": "mon", "tuesday": "tue", "wednesday": "wed", "thursday": "thu",
    "friday": "fri", "saturday": "sat", "sunday": "sun",
}


def main() -> None:
    traffic = pl.read_csv(TRAFFIC_CSV)
    print(f"строк трафика: {traffic.height}")

    traffic = traffic.with_columns(
        pl.col("day_of_week").replace_strict(DAY_TO_WEEKDAY, default=None).alias("weekday_type")
    ).filter(
        pl.col("weekday_type").is_not_null()
        & pl.col("speed_median").is_not_null()
        & (pl.col("speed_median") > 0)
    )
    print(f"после фильтра: {traffic.height}")
    print(traffic.group_by("weekday_type").agg(pl.len()).sort("weekday_type"))

    segments = traffic.group_by(["lat", "lon", "weekday_type", "hour"]).agg(
        pl.col("speed_median").median().alias("speed_kmh"),
        pl.len().alias("n_obs"),
    )
    # идентификатор участка — из координаты точки наблюдения, другого в данных нет
    points = (
        segments.select("lat", "lon")
        .unique()
        .sort(["lat", "lon"])
        .with_row_index("segment_id")
    )
    segments = segments.join(points, on=["lat", "lon"], how="left").select(
        "segment_id", "lat", "lon", "weekday_type", "hour", "speed_kmh", "n_obs"
    )
    segments.write_parquet(SEGMENT_SPEED_PARQUET)

    fallback = (
        traffic.group_by(["weekday_type", "hour"])
        .agg(pl.col("speed_median").median().alias("median_speed_kmh"))
        .sort(["weekday_type", "hour"])
    )
    fallback.write_parquet(CITY_SPEED_FALLBACK_PARQUET)

    print(f"точек наблюдения: {points.height}")
    print(f"строк segment_speed: {segments.height} → {SEGMENT_SPEED_PARQUET}")
    print(f"строк city_speed_fallback: {fallback.height} → {CITY_SPEED_FALLBACK_PARQUET}")
    print("медиана скорости по городу, пятница:")
    print(fallback.filter(pl.col("weekday_type") == "fri").to_pandas().to_string(index=False))


if __name__ == "__main__":
    main()
