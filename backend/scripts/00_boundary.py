"""Шаг 0. Административная граница Ташкента из локального дампа OSM.

Границей обрезается всё остальное: население, остановки, графы. Без неё PNT-500
считается от населения области (ТЗ р. 13).

Выход: data/build/tashkent_boundary.geojson
"""

import _bootstrap  # noqa: F401
import json

import geopandas as gpd
import osmium
import shapely
from shapely.geometry import mapping

from app.config import BOUNDARY_GEOJSON, CITY_ADMIN_LEVEL, CITY_NAMES, DATA_BUILD, OSM_PBF
from app.config import WGS84


def main() -> None:
    if not OSM_PBF.exists():
        raise SystemExit(f"нет дампа OSM: {OSM_PBF}")

    wkbfab = osmium.geom.WKBFactory()
    candidates = []

    processor = (
        osmium.FileProcessor(str(OSM_PBF))
        .with_areas()
        .with_filter(osmium.filter.KeyFilter("boundary"))
    )
    for obj in processor:
        if not isinstance(obj, osmium.osm.Area):
            continue
        tags = obj.tags
        if tags.get("boundary") != "administrative":
            continue
        if tags.get("admin_level") != CITY_ADMIN_LEVEL:
            continue
        names = {
            tags.get("name"),
            tags.get("name:en"),
            tags.get("name:ru"),
            tags.get("name:uz"),
            tags.get("official_name"),
        }
        if not (names & set(CITY_NAMES)):
            continue
        try:
            geom = shapely.from_wkb(bytes.fromhex(wkbfab.create_multipolygon(obj)))
        except RuntimeError:
            continue
        if geom.is_empty:
            continue
        candidates.append((tags.get("name"), geom))

    if not candidates:
        raise SystemExit(
            "граница не найдена: проверь QATNOV_CITY_NAMES / QATNOV_CITY_ADMIN_LEVEL"
        )

    # если совпало несколько релейшенов (город и одноимённая область) — берём меньший
    name, geom = min(candidates, key=lambda item: item[1].area)

    DATA_BUILD.mkdir(parents=True, exist_ok=True)
    BOUNDARY_GEOJSON.write_text(
        json.dumps(
            {
                "type": "Feature",
                "properties": {"name": name, "admin_level": CITY_ADMIN_LEVEL},
                "geometry": mapping(geom),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    minx, miny, maxx, maxy = geom.bounds
    area_km2 = (
        gpd.GeoSeries([geom], crs=WGS84)
        .to_crs(gpd.GeoSeries([geom], crs=WGS84).estimate_utm_crs())
        .area.iloc[0]
        / 1e6
    )
    print(f"кандидатов: {len(candidates)}")
    print(f"выбран: {name}")
    print(f"bbox: {minx:.5f} {miny:.5f} {maxx:.5f} {maxy:.5f}")
    print(f"площадь: {area_km2:.1f} км²")
    print(f"записано: {BOUNDARY_GEOJSON}")


if __name__ == "__main__":
    main()
