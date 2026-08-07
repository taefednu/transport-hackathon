/**
 * Позиции машин на маршруте в выбранный час (§5).
 *
 * Это не анимация и не GPS. Машины расставлены по фактическому интервалу:
 * борт, вышедший k интервалов назад, к началу часа проехал k·h минут пути,
 * а сколько это в метрах — считается по времени хода перегонов за тот же час
 * из `segment_times`. Метод помечается в карточке словом «по интервалу»,
 * потому что выдавать это за реальные засечки нельзя.
 *
 * Ползунка внутри часа нет намеренно: минутной точности в данных не существует.
 */

import type { RouteDetail } from './api'
import { cumulative, projectOnLine, type LngLat } from './geo'

export interface BusPosition {
  /** Номер борта: 1 — тот, что ближе всех к конечной. */
  index: number
  coord: LngLat
  /** Куда смотрит шеврон, градусы по часовой от севера. */
  bearing: number
  /** Через сколько минут после начала часа он придёт на конечную. */
  arrivesInMin: number
}

export interface BusesResult {
  buses: BusPosition[]
  /** Как называется конечная: §5 просит её в подсказке по имени. */
  lastStopName: string | null
  /** Время в одну сторону в этот час, минуты. */
  oneWayMin: number
  /** Интервал, по которому расставлены борта. */
  headwayMin: number
  /** Почему бортов нет, если их нет. */
  reason: string | null
}

/** Точка на ломаной по расстоянию от начала плюс направление в ней. */
function atMeasure(line: LngLat[], cum: number[], measure: number): { coord: LngLat; bearing: number } {
  const total = cum[cum.length - 1]
  const m = Math.max(0, Math.min(measure, total))
  let i = 1
  while (i < cum.length - 1 && cum[i] < m) i++
  const segLen = cum[i] - cum[i - 1]
  const t = segLen > 0 ? (m - cum[i - 1]) / segLen : 0
  const a = line[i - 1]
  const b = line[i]
  const coord: LngLat = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]
  const bearing = (Math.atan2(b[0] - a[0], b[1] - a[1]) * 180) / Math.PI
  return { coord, bearing }
}

/** Расстояния вдоль трассы, попадающие на шов: там машин быть не может. */
function gapSpans(cum: number[], indices: number[]): [number, number][] {
  return indices
    .filter((i) => i + 1 < cum.length)
    .map((i) => [cum[i], cum[i + 1]] as [number, number])
}

export function computeBuses(detail: RouteDetail | null, hour: number, dwellSec: number): BusesResult {
  const empty = (reason: string): BusesResult => ({
    buses: [],
    oneWayMin: 0,
    headwayMin: 0,
    lastStopName: null,
    reason,
  })
  if (!detail) return empty('маршрут не загружен')
  if (!detail.geometry) return empty('трасса не восстановлена — ставить машины некуда')
  if (detail.stops.length < 2) return empty('порядок остановок не восстановлен')

  const headway = detail.actual_headway.find((h) => h.hour === hour)?.actual_headway_min
  if (!headway || headway <= 0) return empty(`в ${hour}:00 фактического интервала в данных нет`)

  const times = detail.segment_times.filter((s) => s.hour === hour)
  if (times.length === 0) return empty(`в ${hour}:00 времени хода по перегонам нет`)

  const bySeq = new Map<number, number>()
  for (const t of times) bySeq.set(t.seq_from, t.travel_sec)

  const line = detail.geometry.coordinates as LngLat[]
  const cum = cumulative(line)

  // остановки на трассе: их проекции задают, какому времени какое расстояние
  const measures: number[] = []
  let previous = 0
  for (const stop of detail.stops) {
    if (stop.lat == null || stop.lon == null) {
      measures.push(previous)
      continue
    }
    const m = projectOnLine(line, cum, [stop.lon, stop.lat]).measure
    // маршрут возвращается по той же улице — проекции обязаны идти вперёд
    previous = Math.max(previous, m)
    measures.push(previous)
  }

  // накопленное время до каждой остановки, секунды
  const elapsed: number[] = [0]
  for (let i = 0; i + 1 < detail.stops.length; i++) {
    const travel = bySeq.get(i)
    if (travel == null) return empty(`в ${hour}:00 нет времени хода на перегоне ${i + 1}`)
    elapsed.push(elapsed[i] + travel + dwellSec)
  }

  const oneWaySec = elapsed[elapsed.length - 1]
  const headwaySec = headway * 60
  const buses: BusPosition[] = []
  // на шве трассы нет — машину туда ставить нельзя
  const spans = gapSpans(cum, detail.geometry_gap_indices ?? [])

  // борт, вышедший k интервалов назад, проехал k·h минут
  for (let k = 0; k * headwaySec < oneWaySec; k++) {
    const e = k * headwaySec
    let seg = 0
    while (seg + 1 < elapsed.length - 1 && elapsed[seg + 1] <= e) seg++
    const span = elapsed[seg + 1] - elapsed[seg]
    const share = span > 0 ? (e - elapsed[seg]) / span : 0
    const measure = measures[seg] + (measures[seg + 1] - measures[seg]) * share
    if (spans.some(([from, to]) => measure > from && measure < to)) continue
    const { coord, bearing } = atMeasure(line, cum, measure)
    buses.push({
      index: k + 1,
      coord,
      bearing,
      arrivesInMin: (oneWaySec - e) / 60,
    })
  }

  return {
    buses,
    oneWayMin: oneWaySec / 60,
    headwayMin: headway,
    lastStopName: detail.stops[detail.stops.length - 1]?.name ?? null,
    reason: null,
  }
}
