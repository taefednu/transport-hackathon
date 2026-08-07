/**
 * Разведение параллельных маршрутов (§3.2).
 *
 * Бэкенд отдаёт `k` и `n` на перегон «остановка—остановка», а геометрию — одной
 * ломаной на направление. Чтобы смещение можно было применить, ломаная режется
 * по проекциям остановок на неё, и каждый кусок получает своё смещение.
 * Считается один раз при загрузке, дальше живёт как атрибут перегона.
 */

import type { Direction, GeometryFeature, ParallelSegment, StopsCollection } from './api'
import { cumulative, projectOnLine, sliceLine, type LngLat } from './geo'
import { PARALLEL_STEP_PX } from './tokens'

/** Дальше этого от линии остановка считается чужой: в данных такие есть. */
const MAX_STOP_OFFSET_M = 800
/** Куски короче этого не рисуем: на экране это ноль пикселей. */
const MIN_PIECE_M = 8

export interface SegmentProps {
  route_num: string
  direction: Direction
  quality: string
  segment_key: string
  seq: number
  k: number
  n: number
  /** (k − (n−1)/2) · 3.5 — §3.2. */
  offset: number
}

export interface SegmentFeature {
  type: 'Feature'
  id: number
  geometry: { type: 'LineString'; coordinates: LngLat[] }
  properties: SegmentProps
}

export interface NetworkIndex {
  collection: { type: 'FeatureCollection'; features: SegmentFeature[] }
  /** segment_key → id кусков всех маршрутов, идущих по нему. Для пересчёта смещений при выборе. */
  bySegmentKey: Map<string, number[]>
  /** `${route_num}:${direction}` → id кусков маршрута. */
  byRoute: Map<string, number[]>
  /** `${route_num}:${direction}` → остановки маршрута (без порядка, из ключей перегонов). */
  stopsByRoute: Map<string, Set<string>>
  /** Направления, у которых геометрия есть. */
  drawnDirections: Set<string>
}

export function routeKey(routeNum: string, direction: Direction): string {
  return `${routeNum}:${direction}`
}

export function offsetFor(k: number, n: number): number {
  return (k - (n - 1) / 2) * PARALLEL_STEP_PX
}

function stopPair(segmentKey: string): [string, string] {
  const i = segmentKey.indexOf('|')
  return [segmentKey.slice(0, i), segmentKey.slice(i + 1)]
}

interface Span {
  lo: number
  hi: number
  seg: ParallelSegment
}

export function buildNetwork(
  geometry: GeometryFeature[],
  segments: ParallelSegment[],
  stops: StopsCollection,
): NetworkIndex {
  const stopXY = new Map<string, LngLat>()
  for (const f of stops.features) stopXY.set(f.properties.stop_id, f.geometry.coordinates)

  const segsByRoute = new Map<string, ParallelSegment[]>()
  const stopsByRoute = new Map<string, Set<string>>()
  for (const s of segments) {
    const key = routeKey(s.route_num, s.direction)
    const list = segsByRoute.get(key)
    if (list) list.push(s)
    else segsByRoute.set(key, [s])

    const [a, b] = stopPair(s.segment_key)
    let set = stopsByRoute.get(key)
    if (!set) {
      set = new Set<string>()
      stopsByRoute.set(key, set)
    }
    set.add(a)
    set.add(b)
  }

  const features: SegmentFeature[] = []
  const bySegmentKey = new Map<string, number[]>()
  const byRoute = new Map<string, number[]>()
  const drawnDirections = new Set<string>()
  let nextId = 1

  const push = (
    coords: LngLat[],
    props: SegmentProps,
    key: string,
  ): void => {
    const id = nextId++
    features.push({ type: 'Feature', id, geometry: { type: 'LineString', coordinates: coords }, properties: props })
    const byKey = bySegmentKey.get(props.segment_key)
    if (byKey) byKey.push(id)
    else if (props.segment_key) bySegmentKey.set(props.segment_key, [id])
    const byR = byRoute.get(key)
    if (byR) byR.push(id)
    else byRoute.set(key, [id])
  }

  for (const feature of geometry) {
    const { route_num, direction, quality } = feature.properties
    const key = routeKey(route_num, direction)
    drawnDirections.add(key)

    const line = feature.geometry.coordinates as LngLat[]
    if (line.length < 2) continue
    const cum = cumulative(line)
    const total = cum[cum.length - 1]

    const spans: Span[] = []
    for (const seg of segsByRoute.get(key) ?? []) {
      const [a, b] = stopPair(seg.segment_key)
      const pa = stopXY.get(a)
      const pb = stopXY.get(b)
      if (!pa || !pb) continue
      const ma = projectOnLine(line, cum, pa)
      const mb = projectOnLine(line, cum, pb)
      if (ma.offset > MAX_STOP_OFFSET_M || mb.offset > MAX_STOP_OFFSET_M) continue
      const lo = Math.min(ma.measure, mb.measure)
      const hi = Math.max(ma.measure, mb.measure)
      if (hi - lo < MIN_PIECE_M) continue
      spans.push({ lo, hi, seg })
    }

    // Ни одного пригодного перегона — рисуем линию целиком, без смещения.
    if (spans.length === 0) {
      push(line, { route_num, direction, quality, segment_key: '', seq: -1, k: 0, n: 1, offset: 0 }, key)
      continue
    }

    const cuts = new Set<number>([0, total])
    for (const s of spans) {
      cuts.add(s.lo)
      cuts.add(s.hi)
    }
    const sorted = [...cuts].sort((x, y) => x - y)

    for (let i = 1; i < sorted.length; i++) {
      const from = sorted[i - 1]
      const to = sorted[i]
      if (to - from < MIN_PIECE_M) continue
      const mid = (from + to) / 2

      // Перегонов, накрывающих середину куска, может быть несколько (маршрут
      // возвращается по той же улице) — берём самый короткий, он точнее.
      let best: Span | null = null
      for (const s of spans) {
        if (s.lo <= mid && mid <= s.hi && (best === null || s.hi - s.lo < best.hi - best.lo)) best = s
      }

      const coords = sliceLine(line, cum, from, to)
      if (coords.length < 2) continue
      push(
        coords,
        best
          ? {
              route_num,
              direction,
              quality,
              segment_key: best.seg.segment_key,
              seq: best.seg.seq,
              k: best.seg.k,
              n: best.seg.n,
              offset: offsetFor(best.seg.k, best.seg.n),
            }
          : { route_num, direction, quality, segment_key: '', seq: -1, k: 0, n: 1, offset: 0 },
        key,
      )
    }
  }

  return {
    collection: { type: 'FeatureCollection', features },
    bySegmentKey,
    byRoute,
    stopsByRoute,
    drawnDirections,
  }
}
