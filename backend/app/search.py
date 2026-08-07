"""Поиск по маршрутам и остановкам с узбекской транслитерацией.

Индекс держится в памяти: несколько тысяч строк. Сравнение — точное совпадение,
затем префиксное, затем расстояние Левенштейна с порогом 2 (ТЗ р. 10).
Порог нужен именно потому, что `Куйлюк` и `Qo'yliq` — разные исходные написания
одного места и после нормализации дают близкие, но не равные строки.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from app.store import Store
from app.textnorm import levenshtein, normalize

LEVENSHTEIN_LIMIT = 2
DEFAULT_LIMIT = 10


@dataclass
class Entry:
    kind: str
    id: str
    title: str
    norm: str
    lat: float | None
    lon: float | None
    detail: str | None = None


def build_index(store: Store) -> list[Entry]:
    entries: list[Entry] = []
    for row in store.stops.select("stop_id", "name", "name_norm", "lat", "lon", "kind").iter_rows(
        named=True
    ):
        entries.append(
            Entry(
                kind="stop",
                id=row["stop_id"],
                title=row["name"] or row["stop_id"],
                norm=row["name_norm"] or "",
                lat=row["lat"],
                lon=row["lon"],
                detail=row["kind"],
            )
        )

    if store.routes is not None:
        seen = set()
        for row in store.routes.select("route_num", "name", "geometry_wkt").iter_rows(named=True):
            if row["route_num"] in seen:
                continue
            seen.add(row["route_num"])
            lat = lon = None
            if store.route_stops is not None:
                first = store.route_stops.filter(
                    pl.col("route_num") == row["route_num"]
                ).sort("seq").head(1)
                if not first.is_empty():
                    stop = store.stops.filter(pl.col("stop_id") == first["stop_id"][0])
                    if not stop.is_empty():
                        lat, lon = float(stop["lat"][0]), float(stop["lon"][0])
            entries.append(
                Entry(
                    kind="route",
                    id=row["route_num"],
                    title=f"№ {row['route_num']}"
                    + (f" — {row['name']}" if row["name"] else ""),
                    norm=normalize(f"{row['route_num']} {row['name'] or ''}"),
                    lat=lat,
                    lon=lon,
                    detail=row["name"],
                )
            )
    return entries


MATCH_NAMES = ("exact", "prefix", "fuzzy")


def rank_entries(index: list[Entry], query: str, kind: str | None = None) -> list[tuple[int, int, Entry]]:
    """Отранжированные совпадения `(rank, score, entry)`, лучшие в начале.

    Вынесено из `search`, потому что этим же ранжированием разбор фразы на
    естественном языке отличает однозначное совпадение от неоднозначного:
    ему нужен не список, а признак «лучший кандидат один или их несколько».
    """
    needle = normalize(query)
    if not needle:
        return []

    scored: list[tuple[int, int, Entry]] = []
    for entry in index:
        if not entry.norm or (kind is not None and entry.kind != kind):
            continue
        if entry.norm == needle:
            rank, score = 0, 0
        elif entry.norm.startswith(needle) or needle in entry.norm:
            rank, score = 1, len(entry.norm) - len(needle)
        else:
            distance = levenshtein(needle, entry.norm, LEVENSHTEIN_LIMIT)
            if distance > LEVENSHTEIN_LIMIT:
                continue
            rank, score = 2, distance
        scored.append((rank, score, entry))

    scored.sort(key=lambda item: (item[0], item[1], item[2].title))
    return scored


def pack_entry(entry: Entry, rank: int) -> dict:
    return {
        "id": entry.id,
        "title": entry.title,
        "detail": entry.detail,
        "lat": entry.lat,
        "lon": entry.lon,
        "match": MATCH_NAMES[rank],
    }


def search(index: list[Entry], query: str, limit: int = DEFAULT_LIMIT) -> dict:
    needle = normalize(query)
    if not needle:
        return {"query": query, "normalized": needle, "routes": [], "stops": []}

    scored = rank_entries(index, query)

    def pack(kind: str) -> list[dict]:
        return [pack_entry(e, rank) for rank, _, e in scored if e.kind == kind][:limit]

    return {
        "query": query,
        "normalized": needle,
        "routes": pack("route"),
        "stops": pack("stop"),
    }
