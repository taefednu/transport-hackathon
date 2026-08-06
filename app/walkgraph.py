"""Пешеходный граф: хранение и Дейкстра с отсечкой по расстоянию.

Граф лежит в памяти в формате CSR (три массива), потому что в рантайме нужен
только один запрос — «какие вершины достижимы за 500 метров от точки». Обход
с отсечкой посещает сотни вершин, а не весь город, поэтому чистого Python здесь
достаточно и не нужен ни networkx, ни матричные операции.
"""

from __future__ import annotations

import heapq
import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class WalkGraph:
    indptr: np.ndarray  # CSR: границы списка соседей
    indices: np.ndarray  # CSR: соседи
    weights: np.ndarray  # длина ребра в метрах
    lat: np.ndarray
    lon: np.ndarray
    x: np.ndarray  # проекция в метрах, для поиска ближайшей вершины
    y: np.ndarray
    crs: str

    @property
    def n_nodes(self) -> int:
        return len(self.lat)

    def reachable(self, source: int, limit_m: float) -> dict[int, float]:
        """Вершины, достижимые от source не дальше limit_m, с расстоянием до них."""
        dist = {source: 0.0}
        heap = [(0.0, source)]
        while heap:
            d, node = heapq.heappop(heap)
            if d > dist.get(node, float("inf")):
                continue
            start, end = self.indptr[node], self.indptr[node + 1]
            for k in range(start, end):
                nxt = int(self.indices[k])
                nd = d + float(self.weights[k])
                if nd > limit_m:
                    continue
                if nd < dist.get(nxt, float("inf")):
                    dist[nxt] = nd
                    heapq.heappush(heap, (nd, nxt))
        return dist

    # pickle здесь безопасен: файл — артефакт нашего же пайплайна из data/build,
    # он не приходит извне и не принимается от пользователя.
    def save(self, path: Path) -> None:
        with open(path, "wb") as fh:
            pickle.dump(self, fh, protocol=pickle.HIGHEST_PROTOCOL)

    @staticmethod
    def load(path: Path) -> "WalkGraph":
        with open(path, "rb") as fh:
            return pickle.load(fh)
