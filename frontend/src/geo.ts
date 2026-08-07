/** Геометрия на плоскости. Город маленький, равнопромежуточной проекции хватает. */

export type LngLat = [number, number]

const R = 6371008.8
const DEG = Math.PI / 180
/** Опорная широта Ташкента: масштаб по долготе берём в ней и дальше не пересчитываем. */
const LAT0 = 41.3
const KX = Math.cos(LAT0 * DEG) * R * DEG
const KY = R * DEG

export function toXY([lon, lat]: LngLat): [number, number] {
  return [lon * KX, lat * KY]
}

export function distanceM(a: LngLat, b: LngLat): number {
  const [ax, ay] = toXY(a)
  const [bx, by] = toXY(b)
  return Math.hypot(ax - bx, ay - by)
}

/** Накопленная длина по вершинам, метры. Длина массива равна числу вершин. */
export function cumulative(line: LngLat[]): number[] {
  const out = new Array<number>(line.length)
  out[0] = 0
  for (let i = 1; i < line.length; i++) out[i] = out[i - 1] + distanceM(line[i - 1], line[i])
  return out
}

export interface Projection {
  /** Расстояние от начала линии до проекции точки, метры. */
  measure: number
  /** Насколько точка в стороне от линии, метры. */
  offset: number
}

/** Проекция точки на ломаную. Перебор всех звеньев: линий мало, считается один раз. */
export function projectOnLine(line: LngLat[], cum: number[], point: LngLat): Projection {
  const [px, py] = toXY(point)
  let best: Projection = { measure: 0, offset: Infinity }

  for (let i = 1; i < line.length; i++) {
    const [ax, ay] = toXY(line[i - 1])
    const [bx, by] = toXY(line[i])
    const dx = bx - ax
    const dy = by - ay
    const len2 = dx * dx + dy * dy
    let t = len2 === 0 ? 0 : ((px - ax) * dx + (py - ay) * dy) / len2
    t = Math.max(0, Math.min(1, t))
    const cx = ax + t * dx
    const cy = ay + t * dy
    const off = Math.hypot(px - cx, py - cy)
    if (off < best.offset) {
      best = { measure: cum[i - 1] + t * Math.sqrt(len2), offset: off }
    }
  }
  return best
}

function interpolate(a: LngLat, b: LngLat, t: number): LngLat {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]
}

/** Кусок ломаной между двумя расстояниями от начала. Концы попадают точно. */
export function sliceLine(line: LngLat[], cum: number[], from: number, to: number): LngLat[] {
  const total = cum[cum.length - 1]
  const start = Math.max(0, Math.min(from, total))
  const end = Math.max(start, Math.min(to, total))
  if (end - start < 1e-6) return []

  const out: LngLat[] = []
  for (let i = 1; i < line.length; i++) {
    const segStart = cum[i - 1]
    const segEnd = cum[i]
    if (segEnd < start || segStart > end) continue
    const segLen = segEnd - segStart
    const t0 = segLen === 0 ? 0 : (Math.max(start, segStart) - segStart) / segLen
    const t1 = segLen === 0 ? 0 : (Math.min(end, segEnd) - segStart) / segLen
    const p0 = interpolate(line[i - 1], line[i], t0)
    const p1 = interpolate(line[i - 1], line[i], t1)
    if (out.length === 0) out.push(p0)
    else if (out[out.length - 1][0] !== p0[0] || out[out.length - 1][1] !== p0[1]) out.push(p0)
    out.push(p1)
  }
  return out.length >= 2 ? out : []
}

/** Точка на ломаной по расстоянию от её начала. */
export function pointAtMeasure(line: LngLat[], cum: number[], measure: number): LngLat {
  const total = cum[cum.length - 1]
  const m = Math.max(0, Math.min(measure, total))
  let i = 1
  while (i < cum.length - 1 && cum[i] < m) i++
  const segLen = cum[i] - cum[i - 1]
  const t = segLen > 0 ? (m - cum[i - 1]) / segLen : 0
  return interpolate(line[i - 1], line[i], t)
}

export interface Bounds {
  west: number
  south: number
  east: number
  north: number
}

export function boundsOf(coords: LngLat[]): Bounds | null {
  if (coords.length === 0) return null
  let west = Infinity
  let south = Infinity
  let east = -Infinity
  let north = -Infinity
  for (const [lon, lat] of coords) {
    if (lon < west) west = lon
    if (lon > east) east = lon
    if (lat < south) south = lat
    if (lat > north) north = lat
  }
  return { west, south, east, north }
}
