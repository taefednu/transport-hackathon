"""Шаг 9. Дыры в покрытии: где люди есть, а обслуживаемой остановки в 500 м нет.

Вход:  data/build/hexes.parquet, stop_hexes.parquet, hex_access.parquet, stops.parquet
Выход: data/build/holes.parquet — h3_id, population, lat, lon, nearest_stop_id,
       walk_distance_m, nearest_stop_name

Это то, на что опирается предложение сценариев: список мест, отсортированный по
числу людей, которые сейчас вне доступности.
"""

import _bootstrap  # noqa: F401

import polars as pl

from app.config import (
    ACTIVE_HEXES_PARQUET,
    HEX_ACCESS_PARQUET,
    HOLES_PARQUET,
    STOP_HEXES_PARQUET,
    STOPS_PARQUET,
)


def main() -> None:
    # слой населения выбирается константой POPULATION_SOURCE: дыры обязаны
    # считаться по тому же слою, что и метрики, иначе /api/holes расходится с /api/metrics
    hexes = pl.read_parquet(ACTIVE_HEXES_PARQUET)
    stop_hexes = pl.read_parquet(STOP_HEXES_PARQUET)
    access = pl.read_parquet(HEX_ACCESS_PARQUET)
    stops = pl.read_parquet(STOPS_PARQUET)

    served = stops.filter(pl.col("n_routes") > 0)["stop_id"]
    covered = set(
        stop_hexes.filter(pl.col("stop_id").is_in(served.to_list()))["h3_id"].to_list()
    )

    holes = (
        hexes.filter(~pl.col("h3_id").is_in(list(covered)) & (pl.col("population") > 0))
        .join(
            access.select(
                "h3_id",
                pl.col("walk_m_served").alias("walk_distance_m"),
                pl.col("nearest_stop_served").alias("nearest_stop_id"),
            ),
            on="h3_id",
            how="left",
        )
        .join(
            stops.select(pl.col("stop_id").alias("nearest_stop_id"), pl.col("name").alias("nearest_stop_name")),
            on="nearest_stop_id",
            how="left",
        )
        .select(
            "h3_id", "population", "lat", "lon",
            "nearest_stop_id", "nearest_stop_name", "walk_distance_m",
        )
        .sort("population", descending=True)
    )

    holes.write_parquet(HOLES_PARQUET)
    print(f"гексагонов без обслуживаемой остановки в 500 м: {holes.height}")
    print(f"людей в них: {holes['population'].sum():,.0f}")
    print(f"записано: {HOLES_PARQUET}")
    print("верх списка:")
    print(
        holes.head(8)
        .select("population", "nearest_stop_name", "walk_distance_m")
        .to_pandas()
        .to_string(index=False)
    )


if __name__ == "__main__":
    main()
