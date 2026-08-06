"""Шаг 3. Слой населения: Kontur Population, обрезанный по границе Ташкента.

Вход:  data/external/kontur_population_UZ.gpkg (H3 r8, лицензия CC BY)
Выход: data/build/hexes.parquet — h3_id, population, lat, lon

Kontur поставляется сразу в H3 r8 — той же сетке, в которой считает движок,
поэтому ресемплинга нет и лишней ошибки в главной метрике тоже нет.
Приёмка: сумма населения по городу должна лечь около официальных 3,18 млн.
"""

import _bootstrap  # noqa: F401

import geopandas as gpd
import h3
import polars as pl

from app.config import BOUNDARY_GEOJSON, H3_RESOLUTION, HEXES_PARQUET, KONTUR_GPKG


def main() -> None:
    boundary = gpd.read_file(BOUNDARY_GEOJSON).geometry.iloc[0]

    kontur = gpd.read_file(KONTUR_GPKG, engine="pyogrio", columns=["h3", "population"])
    print(f"Kontur по Узбекистану: {len(kontur)} гексагонов")

    resolutions = {h3.get_resolution(c) for c in kontur["h3"].head(1000)}
    if resolutions != {H3_RESOLUTION}:
        raise SystemExit(f"ожидалось разрешение {H3_RESOLUTION}, в файле {resolutions}")

    # гексагон, наполовину лежащий за городской чертой, приносит половину своих
    # жителей: иначе на границе либо теряем людей, либо забираем население области
    kontur = kontur.to_crs("EPSG:4326")
    touching = kontur[kontur.intersects(boundary)].copy()
    print(f"пересекают границу: {len(touching)} гексагонов")

    metric_crs = gpd.GeoSeries([boundary], crs="EPSG:4326").estimate_utm_crs()
    geom_m = touching.geometry.to_crs(metric_crs)
    boundary_m = gpd.GeoSeries([boundary], crs="EPSG:4326").to_crs(metric_crs).iloc[0]
    share = (geom_m.intersection(boundary_m).area / geom_m.area).clip(0.0, 1.0)

    centroids = [h3.cell_to_latlng(c) for c in touching["h3"]]

    hexes = pl.DataFrame(
        {
            "h3_id": touching["h3"].to_numpy(),
            "population": touching["population"].to_numpy() * share.to_numpy(),
            "population_full": touching["population"].to_numpy(),
            "city_share": share.to_numpy(),
            "lat": [c[0] for c in centroids],
            "lon": [c[1] for c in centroids],
        }
    ).filter(pl.col("city_share") > 0)

    hexes.write_parquet(HEXES_PARQUET)
    print(f"в слое: {hexes.height} гексагонов")
    print(f"сумма населения (с весом по площади): {hexes['population'].sum():,.0f}")
    print(f"без веса, все пересекающие: {hexes['population_full'].sum():,.0f}")
    print(f"записано: {HEXES_PARQUET}")


if __name__ == "__main__":
    main()
