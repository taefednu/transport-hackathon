/**
 * Слой населения (§7). Гексагоны H3 r8 из `/api/baseline`, границы ячеек
 * берутся у самой h3, а не рисуются похожими шестиугольниками: приближение
 * дало бы щели и нахлёсты на стыках, то есть враньё про геометрию.
 *
 * Кодировка проверена валидатором палитры, а не подобрана на глаз.
 * Существенное: на прозрачной заливке оттенок не работает. Спековые
 * `#2FA8A0` (покрыт) и `#E9A93C` (частая сеть), сведённые над фоном
 * `#F7F6F3` при alpha 0.3, дают ΔE 7.9 при нормальном зрении и 4.9 при
 * протанопии — при пороге 15. Два состояния становятся одним цветом.
 *
 * Поэтому каналы разведены по задачам:
 *   заливка — сколько людей (один тон, прозрачность по плотности);
 *   контур  — состояние (рисуется в полную силу и прозрачности не боится).
 * Пара контуров `#009B8A` (частая сеть) и `#C2563F` (не покрыт) проходит все
 * шесть проверок: ΔE 24.7 при нормальном зрении, 10.2 при дейтеранопии,
 * контраст обоих к фону выше 3:1.
 */

import { cellToBoundary } from 'h3-js'
import type { GeoJSONSource, ExpressionSpecification, Map as MlMap } from 'maplibre-gl'
import type { Feature, FeatureCollection, Polygon } from 'geojson'
import type { BaselineHex, Hole, ScenarioResult } from './api'
import { C, O } from './tokens'

export const HEX_SRC = { cells: 'hexes', changed: 'hexes-changed' } as const
export const HEX_LYR = {
  fill: 'hex-fill',
  separator: 'hex-separator',
  frequent: 'hex-frequent',
  uncovered: 'hex-uncovered',
  hole: 'hex-hole',
  changed: 'hex-changed',
} as const

export interface HexProps {
  h3: string
  pop: number
  covered: boolean
  frequent: boolean
  walk_min: number
  hole: boolean
}

function polygon(h3: string, props: HexProps): Feature<Polygon, HexProps> {
  // true — порядок [lng, lat], как в GeoJSON
  const ring = cellToBoundary(h3, true)
  ring.push(ring[0])
  return { type: 'Feature', geometry: { type: 'Polygon', coordinates: [ring] }, properties: props }
}

export interface HexScale {
  /** Медиана населения ячейки. */
  mid: number
  /** 85-й процентиль: выше него плотнее уже не красим. */
  top: number
}

export function buildHexes(hexes: BaselineHex[], holes: Hole[]): {
  cells: FeatureCollection
  scale: HexScale
} {
  const holeCells = new Set(holes.map((h) => h.h3_id))
  const features: Feature[] = hexes.map((h) =>
    polygon(h.h3, {
      h3: h.h3,
      pop: h.pop,
      covered: h.covered,
      frequent: h.frequent,
      walk_min: h.walk_min,
      hole: holeCells.has(h.h3),
    }),
  )

  // Хвост распределения длинный: максимум (40 673) в тридцать раз больше
  // медианы (1 395), и шкала по максимуму красит весь город одинаково бледным.
  const sorted = hexes.map((h) => h.pop).sort((a, b) => a - b)
  const at = (q: number) => sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * q))] || 1

  return {
    cells: { type: 'FeatureCollection', features },
    scale: { mid: at(0.5), top: Math.max(at(0.85), at(0.5) + 1) },
  }
}

/**
 * Прозрачность по плотности. Слой лежит под маршрутами и перекрывать сеть
 * не должен, поэтому верх шкалы низкий — плотнее 0.22 заливка не становится.
 */
function fillOpacity(scale: HexScale, factor = 1): ExpressionSpecification {
  return [
    'interpolate',
    ['linear'],
    ['get', 'pop'],
    0,
    0.06 * factor,
    scale.mid,
    0.18 * factor,
    scale.top,
    0.3 * factor,
  ]
}

export function addHexLayers(
  map: MlMap,
  cells: FeatureCollection,
  scale: HexScale,
  beforeId: string,
): void {
  hexScale = scale
  map.addSource(HEX_SRC.cells, { type: 'geojson', data: cells })
  map.addSource(HEX_SRC.changed, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })

  // заливка — только у покрытых ячеек: §7 требует непокрытые без заливки
  map.addLayer(
    {
      id: HEX_LYR.fill,
      type: 'fill',
      source: HEX_SRC.cells,
      filter: ['get', 'covered'],
      layout: { visibility: 'none' },
      paint: { 'fill-color': C.covered, 'fill-opacity': fillOpacity(scale) },
    },
    beforeId,
  )

  // разделитель между соседними ячейками, иначе заливка сливается в пятно
  map.addLayer(
    {
      id: HEX_LYR.separator,
      type: 'line',
      source: HEX_SRC.cells,
      filter: ['all', ['get', 'covered'], ['!', ['get', 'frequent']]],
      layout: { visibility: 'none' },
      paint: { 'line-color': '#FFFFFF', 'line-width': 0.6, 'line-opacity': 0.35 },
    },
    beforeId,
  )

  // §7 — покрыт частой сетью: контур в полную силу, он переживает прозрачность
  map.addLayer(
    {
      id: HEX_LYR.frequent,
      type: 'line',
      source: HEX_SRC.cells,
      filter: ['get', 'frequent'],
      layout: { visibility: 'none', 'line-join': 'round' },
      paint: { 'line-color': C.covered, 'line-width': 0.9, 'line-opacity': 0.5 },
    },
    beforeId,
  )

  // не покрыт — без заливки, только контур
  map.addLayer(
    {
      id: HEX_LYR.uncovered,
      type: 'line',
      source: HEX_SRC.cells,
      filter: ['!', ['get', 'covered']],
      layout: { visibility: 'none', 'line-join': 'round' },
      paint: { 'line-color': C.removed, 'line-width': 0.9, 'line-opacity': 0.5 },
    },
    beforeId,
  )

  // §10, клавиша D — дыры покрытия: те же ячейки, но громче
  map.addLayer(
    {
      id: HEX_LYR.hole,
      type: 'line',
      source: HEX_SRC.cells,
      filter: ['get', 'hole'],
      layout: { visibility: 'none', 'line-join': 'round' },
      paint: { 'line-color': C.removed, 'line-width': 2, 'line-opacity': 0.95 },
    },
    beforeId,
  )

  // изменённые сценарием ячейки — поверх остальных
  map.addLayer(
    {
      id: HEX_LYR.changed,
      type: 'fill',
      source: HEX_SRC.changed,
      paint: {
        'fill-color': ['case', ['==', ['get', 'state'], 'gained'], C.added, C.removed],
        'fill-opacity': 0.5,
        'fill-outline-color': '#FFFFFF',
      },
    },
    beforeId,
  )
}

/** Шкалу запоминаем: она входит в выражение прозрачности при каждом пересчёте. */
let hexScale: HexScale = { mid: 1, top: 2 }

export function setHexVisibility(map: MlMap, showHexes: boolean, showHoles: boolean, dim: boolean): void {
  const on = showHexes ? 'visible' : 'none'
  for (const id of [HEX_LYR.fill, HEX_LYR.separator, HEX_LYR.frequent, HEX_LYR.uncovered]) {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', on)
  }
  if (map.getLayer(HEX_LYR.hole)) {
    map.setLayoutProperty(HEX_LYR.hole, 'visibility', showHoles ? 'visible' : 'none')
  }

  // §7 — при выборе маршрута гексагоны не приглушаются, но уходят вниз,
  // чтобы не спорить с линией
  const factor = dim ? O.hexDimmed / 0.35 : 1
  if (map.getLayer(HEX_LYR.fill)) {
    map.setPaintProperty(HEX_LYR.fill, 'fill-opacity', fillOpacity(hexScale, factor))
  }
  if (map.getLayer(HEX_LYR.frequent)) {
    map.setPaintProperty(HEX_LYR.frequent, 'line-opacity', dim ? 0.22 : 0.5)
  }
  if (map.getLayer(HEX_LYR.uncovered)) {
    map.setPaintProperty(HEX_LYR.uncovered, 'line-opacity', dim ? 0.28 : 0.5)
  }
}

export function setChangedHexes(map: MlMap, result: ScenarioResult | null): void {
  const source = map.getSource(HEX_SRC.changed) as GeoJSONSource | undefined
  if (!source) return
  const features: Feature[] = (result?.changed_hexes ?? []).map((c) => {
    const ring = cellToBoundary(c.h3, true)
    ring.push(ring[0])
    return {
      type: 'Feature',
      geometry: { type: 'Polygon', coordinates: [ring] },
      properties: { state: c.state, pop: c.pop },
    }
  })
  source.setData({ type: 'FeatureCollection', features })
}
