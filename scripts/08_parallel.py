"""Шаг 8. Разведение параллельных маршрутов: сколько линий идёт по одному перегону.

Вход:  data/build/route_stops.parquet
Выход: data/build/segment_routes.parquet — segment_key, route_num, direction, k, n

Расхождение с ТЗ, идём за данными: общего идентификатора перегона дорожной сети
у нас нет (трафик Яндекса — точки, а не участки линий), поэтому перегон
определяется парой соседних остановок. Пара берётся неупорядоченной, чтобы
встречные направления считались одной линией на карте.

`k` — порядковый номер маршрута на перегоне, `n` — сколько их всего. Фронт
использует их, чтобы развести линии в пикселях; считать это в рантайме нельзя.
"""

import _bootstrap  # noqa: F401

import polars as pl

from app.config import ROUTE_STOPS_PARQUET, SEGMENT_ROUTES_PARQUET


def main() -> None:
    route_stops = pl.read_parquet(ROUTE_STOPS_PARQUET).sort(["route_num", "direction", "seq"])

    pairs = (
        route_stops.with_columns(
            pl.col("stop_id").shift(-1).over(["route_num", "direction"]).alias("next_stop_id"),
        )
        .filter(pl.col("next_stop_id").is_not_null())
        .with_columns(
            pl.min_horizontal("stop_id", "next_stop_id").alias("a"),
            pl.max_horizontal("stop_id", "next_stop_id").alias("b"),
        )
        .with_columns((pl.col("a") + "|" + pl.col("b")).alias("segment_key"))
        .select("segment_key", "route_num", "direction", "seq")
        .unique(subset=["segment_key", "route_num", "direction"])
    )

    counted = pairs.join(
        pairs.group_by("segment_key").agg(pl.len().alias("n")), on="segment_key", how="left"
    ).sort(["segment_key", "route_num", "direction"])

    counted = counted.with_columns(
        pl.int_range(pl.len()).over("segment_key").alias("k")
    ).select("segment_key", "route_num", "direction", "seq", "k", "n")

    counted.write_parquet(SEGMENT_ROUTES_PARQUET)
    print(f"перегонов (уникальных пар остановок): {counted['segment_key'].n_unique()}")
    print(f"строк segment_routes: {counted.height} → {SEGMENT_ROUTES_PARQUET}")
    print("распределение по числу маршрутов на перегоне:")
    print(
        counted.group_by("n").agg(pl.col("segment_key").n_unique().alias("перегонов")).sort("n")
    )


if __name__ == "__main__":
    main()
