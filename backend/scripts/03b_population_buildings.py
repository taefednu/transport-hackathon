"""Шаг 3б. Альтернативный слой населения: контрольная численность, разложенная
по застройке OSM.

Зачем: выданный слой Kontur внутри города распределён неправдоподобно —
корреляция с числом зданий 0.116, с числом многоквартирных домов −0.061,
и у него плато на 40 673 (knowledge/facts.md §9). Сумма по городу примерно
верна, поштучные значения по гексагонам — нет.

Метод: население = контрольная численность × доля жилой ёмкости гексагона.
Ёмкость = число многоквартирных домов × вес + число индивидуальных × 1.
Вес не выдуман: он равен отношению медианной жилой площади (площадь пятна ×
этажность) многоквартирного дома к индивидуальному. Этажность там, где её нет
в OSM, восстанавливается по корзинам площади пятна, иначе медиана считалась бы
по смещённой подвыборке: `building:levels` проставляют у крупных зданий.

Абсолютный масштаб задаёт контрольная численность, поэтому калибровать нужно
только одно число — отношение весов.

Ограничение метода измерено: раскладка по площади пола (то же, но без деления
на классы) расходится с двухвесовой на 45% населения, потому что многоэтажки,
размеченные как `building=yes`, получают вес обычного дома. Числа рядом —
`scripts/compare_population_layers.py`.

Вход:  data/external/uzbekistan-latest.osm.pbf, data/build/tashkent_boundary.geojson
Выход: data/build/hexes_buildings.parquet     — тот же формат, что hexes.parquet
       data/build/buildings.parquet           — сырьё для аудита метода

Продукт не переключается сам: сервер читает config.ACTIVE_HEXES_PARQUET,
которая выбирается константой QATNOV_POPULATION_SOURCE (kontur | buildings).

Запуск: `.venv/bin/python scripts/03b_population_buildings.py`
"""

import _bootstrap  # noqa: F401

import math

import geopandas as gpd
import h3
import osmium
import polars as pl
from shapely.geometry import Point, Polygon
from shapely.prepared import prep

from app.config import (
    BOUNDARY_GEOJSON,
    BUILDING_AREA_BINS_M2,
    BUILDINGS_PARQUET,
    H3_RESOLUTION,
    HEXES_BUILDINGS_PARQUET,
    OSM_PBF,
    POPULATION_CONTROL,
    WGS84,
)

# building=*, которые точно не жильё. Всё остальное, включая `yes`, считаем
# потенциально жилым: в Ташкенте `yes` — основная масса разметки махаллей.
NON_RESIDENTIAL = frozenset(
    {
        "garage", "garages", "carport", "shed", "hut", "roof", "canopy", "greenhouse",
        "industrial", "warehouse", "manufacture", "factory", "hangar", "silo", "storage_tank",
        "retail", "commercial", "office", "supermarket", "kiosk", "shop", "hotel",
        "school", "university", "college", "kindergarten", "hospital", "clinic",
        "mosque", "church", "cathedral", "temple", "synagogue", "chapel", "shrine",
        "train_station", "transportation", "toilets", "service", "bunker", "ruins",
        "construction", "farm_auxiliary", "barn", "stable", "cowshed", "sty",
        "civic", "government", "public", "stadium", "sports_hall", "sports_centre",
        "parking", "bridge", "water_tower", "pumping_station",
    }
)
# многоквартирная застройка
APARTMENT_TYPES = frozenset({"apartments", "residential", "dormitory", "terrace"})

# метры на градус на широте Ташкента: слой нужен только для площади пятна,
# точная проекция тут избыточна
LAT_M_PER_DEG = 111_132.0


def _levels(tags) -> float | None:
    raw = tags.get("building:levels")
    if raw is None:
        return None
    try:
        value = float(str(raw).split(";")[0].replace(",", "."))
    except ValueError:
        return None
    return value if 0 < value <= 60 else None


class Collector(osmium.SimpleHandler):
    def __init__(self, keep) -> None:
        super().__init__()
        self.keep = keep
        self.rows: list[tuple] = []

    def _add(self, lat, lon, btype, levels, area):
        if not self.keep(lon, lat):
            return
        self.rows.append((h3.latlng_to_cell(lat, lon, H3_RESOLUTION), btype, levels, area, lat, lon))

    def node(self, n):
        if "building" not in n.tags or not n.location.valid():
            return
        self._add(n.location.lat, n.location.lon, n.tags["building"], _levels(n.tags), None)

    def way(self, w):
        if "building" not in w.tags:
            return
        try:
            coords = [(n.location.lon, n.location.lat) for n in w.nodes if n.location.valid()]
        except Exception:
            return
        if len(coords) < 3:
            return
        lon = sum(c[0] for c in coords) / len(coords)
        lat = sum(c[1] for c in coords) / len(coords)
        try:
            ring = Polygon(coords)
            deg2 = abs(ring.area)
        except Exception:
            return
        area = deg2 * LAT_M_PER_DEG * (LAT_M_PER_DEG * math.cos(math.radians(lat)))
        self._add(lat, lon, w.tags["building"], _levels(w.tags), area if area > 0 else None)


def main() -> None:
    boundary = gpd.read_file(BOUNDARY_GEOJSON).geometry.iloc[0]
    inside = prep(boundary)
    minx, miny, maxx, maxy = boundary.bounds

    def keep(lon: float, lat: float) -> bool:
        # bbox отсекает Узбекистан целиком за два сравнения; полигон проверяется
        # только для зданий Ташкента, иначе проход по дампу не заканчивается
        if not (minx <= lon <= maxx and miny <= lat <= maxy):
            return False
        return inside.contains(Point(lon, lat))

    if BUILDINGS_PARQUET.exists() and BUILDINGS_PARQUET.stat().st_mtime > OSM_PBF.stat().st_mtime:
        # проход по дампу занимает минуты, а калибровку хочется перебирать
        buildings = pl.read_parquet(BUILDINGS_PARQUET)
        print(f"выгрузка зданий взята из {BUILDINGS_PARQUET.name}")
    else:
        handler = Collector(keep)
        handler.apply_file(str(OSM_PBF), locations=True, idx="flex_mem")
        # схема задаётся явно: точечные здания приходят с пустой этажностью и
        # площадью, и вывод типа по первым строкам ломается на первом же контуре
        buildings = pl.DataFrame(
            handler.rows,
            schema={
                "h3_id": pl.String,
                "btype": pl.String,
                "levels": pl.Float64,
                "area_m2": pl.Float64,
                "lat": pl.Float64,
                "lon": pl.Float64,
            },
            orient="row",
        ).with_columns(
            pl.when(pl.col("btype").is_in(list(APARTMENT_TYPES)))
            .then(pl.lit("apartment"))
            .when(pl.col("btype").is_in(list(NON_RESIDENTIAL)))
            .then(pl.lit("non_residential"))
            .otherwise(pl.lit("individual"))
            .alias("klass")
        )
        buildings.write_parquet(BUILDINGS_PARQUET)

    print(f"зданий в границе города: {buildings.height:,}")
    print(buildings.group_by("klass").agg(pl.len()).sort("len", descending=True).to_pandas().to_string(index=False))

    # --- калибровка веса -------------------------------------------------
    # Жилая ёмкость ≈ площадь пятна × этажность. Этажность проставлена у 93%
    # многоквартирных домов и лишь у 6% индивидуальных, причём у индивидуальных
    # её ставят на крупных зданиях: медиана пятна по классу 164 м², а внутри
    # размеченной подвыборки 565 м². Брать медиану этажей по подвыборке нельзя —
    # это смещение отбора. Поэтому этажность восстанавливается по корзинам
    # площади: внутри корзины размеченные и неразмеченные здания сравнимы.
    residential = buildings.filter(
        (pl.col("klass") != "non_residential") & pl.col("area_m2").is_not_null()
    ).with_columns(
        pl.col("area_m2").cut(list(BUILDING_AREA_BINS_M2), left_closed=True).alias("area_bin")
    )
    imputed = (
        residential.filter(pl.col("levels").is_not_null())
        .group_by("klass", "area_bin")
        .agg(pl.col("levels").median().alias("levels_typical"), pl.len().alias("размечено"))
    )
    print("\nвосстановленная этажность по корзинам площади:")
    print(imputed.sort("klass", "area_bin").to_pandas().to_string(index=False))

    residential = residential.join(imputed, on=["klass", "area_bin"], how="left").with_columns(
        pl.coalesce(pl.col("levels"), pl.col("levels_typical"), pl.lit(1.0)).alias("levels_used")
    ).with_columns((pl.col("area_m2") * pl.col("levels_used")).alias("floor_area"))

    stats = residential.group_by("klass").agg(
        pl.len().alias("зданий"),
        pl.col("area_m2").median().round(0).alias("медиана_пятна_м2"),
        pl.col("levels_used").median().alias("медиана_этажей"),
        pl.col("floor_area").median().round(0).alias("медиана_площади_м2"),
    )
    print("\nкалибровка (этажность восстановлена, смещение снято):")
    print(stats.to_pandas().to_string(index=False))

    by_klass = {r["klass"]: r for r in stats.to_dicts()}
    if "apartment" not in by_klass or "individual" not in by_klass:
        raise SystemExit("нечем калибровать: в выгрузке нет одного из классов")
    weight_apartment = float(
        by_klass["apartment"]["медиана_площади_м2"] / by_klass["individual"]["медиана_площади_м2"]
    )
    print(f"\nвес многоквартирного дома относительно индивидуального: {weight_apartment:.2f}")

    # --- раскладка --------------------------------------------------------
    counts = (
        residential.group_by("h3_id", "klass")
        .agg(pl.len().alias("n"))
        .pivot(on="klass", index="h3_id", values="n")
        .fill_null(0)
    )
    for column in ("apartment", "individual"):
        if column not in counts.columns:
            counts = counts.with_columns(pl.lit(0).alias(column))

    counts = counts.with_columns(
        (pl.col("apartment") * weight_apartment + pl.col("individual")).alias("capacity")
    )
    total_capacity = float(counts["capacity"].sum())
    centroids = [h3.cell_to_latlng(c) for c in counts["h3_id"].to_list()]

    hexes = counts.with_columns(
        (pl.col("capacity") / total_capacity * POPULATION_CONTROL).alias("population"),
        pl.Series("lat", [c[0] for c in centroids]),
        pl.Series("lon", [c[1] for c in centroids]),
    ).with_columns(
        # схема та же, что у hexes.parquet: обрезки по границе тут нет — здания
        # уже отфильтрованы точкой внутри города, поэтому доля равна единице
        pl.col("population").alias("population_full"),
        pl.lit(1.0).alias("city_share"),
        pl.col("apartment").alias("apartments"),
        pl.col("individual").alias("individual_buildings"),
    ).select(
        "h3_id", "population", "population_full", "city_share", "lat", "lon",
        "apartments", "individual_buildings", "capacity",
    ).sort("population", descending=True)

    hexes.write_parquet(HEXES_BUILDINGS_PARQUET)
    print(f"\nгексагонов с жильём: {hexes.height:,}")
    print(f"контрольная численность: {POPULATION_CONTROL:,.0f}")
    print(f"сумма слоя: {hexes['population'].sum():,.0f}")
    print(f"записано: {HEXES_BUILDINGS_PARQUET}")
    print("\nверх списка:")
    print(hexes.head(8).select("h3_id", "population", "apartments", "individual_buildings", "lat", "lon")
          .to_pandas().to_string(index=False))


if __name__ == "__main__":
    main()
