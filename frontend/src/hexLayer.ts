/**
 * Слой населения (§7). Гексагоны H3 r8 из `/api/baseline`, границы ячеек
 * берутся у самой h3, а не рисуются похожими шестиугольниками: приближение
 * дало бы щели и нахлёсты на стыках, то есть враньё про геометрию.
 *
 * Кодировка проверена ΔE в OKLab, а не подобрана на глаз. Каналы разведены
 * по задачам: заливка — сколько людей, контур — состояние ячейки.
 *
 * Шкала плотности пересчитана после перехода слоя населения на застройку.
 * Прежнее распределение (медиана 1 395, максимум 40 673) было длиннохвостым,
 * новое — плотное и с потолком: у покрытых ячеек p10 = 1 152, медиана 5 885,
 * p85 = 8 566, максимум 15 918. Прежняя шкала считалась по всем ячейкам сразу,
 * включая пустую периферию, поэтому медиана уезжала вниз, а весь город
 * оказывался в верхней трети рампы: край-в-край ΔE 10.1 — города не видно.
 *
 * Две правки. Первая: якоря берутся по покрытым ячейкам — красим только их,
 * по ним и надо считать. Вторая: плотность кодируется цветом, а не одной
 * прозрачностью; один тон при малой альфе физически не даёт диапазона.
 * Рампа `#CDE7E3 → #6FBFB6 → #00796C` при alpha 0.55 даёт край-в-край ΔE 22.9
 * и 9.4 / 13.6 между соседними ступенями.
 *
 * Контур частой сети раньше был `#009B8A` — тот же тон, что заливка: ΔE 10.5
 * при нормальном зрении и 8.2 при протанопии, то есть контур сливался с
 * заливкой. Взят спековый `#E9A93C`: против заливки 18.7 / 13.2 / 17.8,
 * против непокрытых `#C2563F` — 21.9 / 24.1 / 19.7 (норма / протан / дейтер),
 * минимум по всем шести проверкам 13.2. Тёмный тил `#00524A` разводится с
 * заливкой лучше, но при протанопии сходится с красным до 10.7 — отвергнут.
 * Заливкой янтарный по-прежнему быть не может: при alpha 0.3 он давал 4.9.
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
  /** 10-й процентиль по покрытым ячейкам: ниже него бледнее уже не красим. */
  low: number
  /** Медиана населения покрытой ячейки. */
  mid: number
  /** 85-й процентиль: выше него плотнее уже не красим. */
  top: number
}

/** Ступени плотности: бледная, средняя, плотная. Проверены ΔE, см. шапку. */
export const DENSITY_RAMP = ['#CDE7E3', '#6FBFB6', '#00796C'] as const
/** Прозрачность заливки: одна на всю рампу, диапазон держит цвет. */
const FILL_ALPHA = 0.55

/**
 * Якоря шкалы — по покрытым ячейкам: заливка есть только у них, и считать
 * шкалу по всему городу вместе с пустой периферией значит занижать медиану.
 */
export function hexScaleOf(hexes: BaselineHex[]): HexScale {
  const sorted = hexes
    .filter((h) => h.covered)
    .map((h) => h.pop)
    .sort((a, b) => a - b)
  const at = (q: number) => sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * q))] || 1
  const low = at(0.1)
  const mid = Math.max(at(0.5), low + 1)
  return { low, mid, top: Math.max(at(0.85), mid + 1) }
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

  return { cells: { type: 'FeatureCollection', features }, scale: hexScaleOf(hexes) }
}

/** Цвет по плотности: три ступени рампы на трёх якорях распределения. */
function fillColor(scale: HexScale): ExpressionSpecification {
  return [
    'interpolate',
    ['linear'],
    ['get', 'pop'],
    scale.low,
    DENSITY_RAMP[0],
    scale.mid,
    DENSITY_RAMP[1],
    scale.top,
    DENSITY_RAMP[2],
  ]
}

/** Прозрачность одна на всю рампу: диапазон держит цвет, а не альфа. */
function fillOpacity(factor = 1): number {
  return FILL_ALPHA * factor
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
      paint: { 'fill-color': fillColor(scale), 'fill-opacity': fillOpacity() },
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
      paint: { 'line-color': C.frequent, 'line-width': 1.1, 'line-opacity': 0.85 },
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

/** Шкалу запоминаем: она нужна и легенде, и пересчёту при приглушении. */
let hexScale: HexScale = { low: 1, mid: 2, top: 3 }

export function currentHexScale(): HexScale {
  return hexScale
}

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
    map.setPaintProperty(HEX_LYR.fill, 'fill-opacity', fillOpacity(factor))
  }
  if (map.getLayer(HEX_LYR.frequent)) {
    map.setPaintProperty(HEX_LYR.frequent, 'line-opacity', dim ? 0.38 : 0.85)
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
