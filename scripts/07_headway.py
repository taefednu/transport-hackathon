"""Шаг 7. Фактический интервал движения из транзакций оплаты.

Вход:  data/raw/data/tashkent/RFC_167_tash_hackaton.csv (4 758 901 строка, UTF-8 с BOM)
Выход: data/build/headway_actual.parquet
       route_num, weekday_type, hour, actual_headway_min, n_vehicles, n_boardings, n_trips

Рейс восстанавливается по разрыву между транзакциями одного борта: пауза дольше
TRIP_GAP_MIN означает, что предыдущий рейс закончился. Интервал получается как
60 / (число рейсов в этот час).

Из транзакций берётся только это. Восстановление матрицы корреспонденций и
цепочек поездок в продукт не входит — там, где выход не регистрируется,
это была бы модель, а не наблюдение.
"""

import _bootstrap  # noqa: F401

import time

import polars as pl

from app.config import (
    HEADWAY_ACTUAL_PARQUET,
    TRANSACTION_DATE_TO_WEEKDAY,
    TRANSACTIONS_CSV,
    TRIP_GAP_MIN,
)

# номер маршрута в транзакциях записан как «Маршрут № 14»
ROUTE_PATTERN = r"(\d+[A-Za-zА-Яа-я]?)\s*$"


def main() -> None:
    t0 = time.time()
    lazy = pl.scan_csv(TRANSACTIONS_CSV, encoding="utf8-lossy", infer_schema_length=10_000)
    columns = lazy.collect_schema().names()
    # BOM превращает имя первой колонки в ﻿merchant
    renames = {c: c.lstrip("﻿") for c in columns if c != c.lstrip("﻿")}
    if renames:
        lazy = lazy.rename(renames)
        print(f"снят BOM с колонок: {list(renames.values())}")

    trips = (
        lazy.filter(pl.col("transport") == "bus")
        .with_columns(
            pl.col("route").str.extract(ROUTE_PATTERN, 1).alias("route_num"),
            pl.col("terminal_date").str.to_datetime("%Y-%m-%d %H:%M:%S", strict=False).alias("ts"),
        )
        .filter(pl.col("route_num").is_not_null() & pl.col("ts").is_not_null())
        .with_columns(
            pl.col("ts").dt.date().cast(pl.String).alias("date"),
            pl.col("ts").dt.hour().alias("hour"),
        )
        .sort(["route_num", "reg_num", "ts"])
        .with_columns(
            # новый рейс начинается там, где борт замолчал дольше TRIP_GAP_MIN
            (
                (
                    pl.col("ts").diff().dt.total_seconds()
                    > TRIP_GAP_MIN * 60
                )
                | pl.col("ts").diff().is_null()
                | (pl.col("reg_num") != pl.col("reg_num").shift())
                | (pl.col("route_num") != pl.col("route_num").shift())
            )
            .cum_sum()
            .alias("trip_id")
        )
        .collect(engine="streaming")
    )

    print(f"транзакций автобуса: {trips.height}")
    print(f"восстановлено рейсов: {trips['trip_id'].n_unique()}")
    print(f"маршрутов в транзакциях: {trips['route_num'].n_unique()}")

    per_hour = (
        trips.group_by(["route_num", "date", "hour"])
        .agg(
            pl.col("trip_id").n_unique().alias("n_trips"),
            pl.col("reg_num").n_unique().alias("n_vehicles"),
            pl.len().alias("n_boardings"),
        )
        .with_columns(
            pl.col("date")
            .replace_strict(TRANSACTION_DATE_TO_WEEKDAY, default=None)
            .alias("weekday_type")
        )
        .filter(pl.col("weekday_type").is_not_null())
        .with_columns((60.0 / pl.col("n_trips")).alias("actual_headway_min"))
        .select(
            "route_num", "weekday_type", "hour",
            "actual_headway_min", "n_vehicles", "n_boardings", "n_trips",
        )
        .sort(["route_num", "weekday_type", "hour"])
    )

    per_hour.write_parquet(HEADWAY_ACTUAL_PARQUET)
    print(f"строк headway_actual: {per_hour.height} → {HEADWAY_ACTUAL_PARQUET}")
    print(per_hour.group_by("weekday_type").agg(pl.len()).sort("weekday_type"))
    sample = per_hour.filter((pl.col("route_num") == "8") & (pl.col("weekday_type") == "fri"))
    print("маршрут 8, пятница:")
    print(sample.select("hour", "actual_headway_min", "n_vehicles", "n_boardings").to_pandas().to_string(index=False))
    print(f"время: {time.time() - t0:.1f} с")


if __name__ == "__main__":
    main()
