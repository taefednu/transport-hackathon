/**
 * Два слоя поверх сети: позиции машин (§5) и щитки номеров маршрута (§3.4).
 *
 * Оба живут только у выбранного маршрута и оба пересобираются целиком —
 * это десятки объектов, а не тысячи.
 */

import type { GeoJSONSource, Map as MlMap } from 'maplibre-gl'
import type { Feature, FeatureCollection } from 'geojson'
import type { BusPosition } from './buses'
import type { LngLat } from './geo'
import { C } from './tokens'

export const OVERLAY_SRC = {
  buses: 'buses',
  shields: 'shields',
  warnings: 'warnings',
  terminals: 'terminals',
  walkZone: 'walk-zone',
} as const
export const OVERLAY_LYR = {
  buses: 'bus-chevrons',
  shields: 'route-shields',
  warnings: 'warning-badges',
  terminals: 'stop-terminals',
  walkZone: 'walk-zone-lines',
} as const

const empty = (): FeatureCollection => ({ type: 'FeatureCollection', features: [] })

/** §5 — шеврон 8 px, заливка синяя, белая обводка. */
function chevronImage(size: number): ImageData {
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')!
  const s = size
  ctx.beginPath()
  ctx.moveTo(s * 0.5, s * 0.12)
  ctx.lineTo(s * 0.86, s * 0.84)
  ctx.lineTo(s * 0.5, s * 0.66)
  ctx.lineTo(s * 0.14, s * 0.84)
  ctx.closePath()
  ctx.fillStyle = C.selected
  ctx.fill()
  ctx.lineWidth = s * 0.08
  ctx.strokeStyle = '#FFFFFF'
  ctx.stroke()
  return ctx.getImageData(0, 0, size, size)
}

/**
 * §3.4 — щиток как дорожный знак: прямоугольник со скруглением 3 px.
 * Картинка чёрно-белая и помечена sdf, чтобы её можно было красить
 * по состоянию линии и растягивать под длину номера.
 */
function shieldImage(size: number): ImageData {
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')!
  ctx.beginPath()
  ctx.roundRect(0, 0, size, size, size * 0.18)
  ctx.fillStyle = '#000000'
  ctx.fill()
  return ctx.getImageData(0, 0, size, size)
}

/** §4.1 — конечная маршрута: горизонтальная черта внутри круга остановки. */
function terminalBar(size: number): ImageData {
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')!
  ctx.fillStyle = '#FFFFFF'
  ctx.fillRect(size * 0.22, size * 0.42, size * 0.56, size * 0.16)
  return ctx.getImageData(0, 0, size, size)
}

/** §9 — бейдж: треугольник с белым восклицательным знаком. */
function badgeImage(size: number, fill: string = C.warn): ImageData {
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')!
  const s = size
  ctx.beginPath()
  ctx.moveTo(s * 0.5, s * 0.08)
  ctx.lineTo(s * 0.95, s * 0.88)
  ctx.lineTo(s * 0.05, s * 0.88)
  ctx.closePath()
  ctx.fillStyle = fill
  ctx.fill()
  ctx.lineWidth = s * 0.07
  ctx.strokeStyle = '#FFFFFF'
  ctx.stroke()
  ctx.fillStyle = '#FFFFFF'
  ctx.fillRect(s * 0.45, s * 0.34, s * 0.1, s * 0.3)
  ctx.fillRect(s * 0.45, s * 0.7, s * 0.1, s * 0.1)
  return ctx.getImageData(0, 0, size, size)
}

export function addOverlayLayers(map: MlMap, beforeId: string): void {
  map.addSource(OVERLAY_SRC.buses, { type: 'geojson', data: empty() })
  map.addSource(OVERLAY_SRC.shields, { type: 'geojson', data: empty() })

  // pixelRatio 2: картинка 48 px даёт базу 24 px, дальше размер по зуму
  map.addSource(OVERLAY_SRC.warnings, { type: 'geojson', data: empty() })
  map.addSource(OVERLAY_SRC.terminals, { type: 'geojson', data: empty() })
  map.addSource(OVERLAY_SRC.walkZone, { type: 'geojson', data: empty() })

  // §12 — зона пешей доступности: сама пешеходная сеть, а не круг на карте.
  // Кладём под трассы, чтобы маршрут остался читаемым.
  map.addLayer(
    {
      id: OVERLAY_LYR.walkZone,
      type: 'line',
      source: OVERLAY_SRC.walkZone,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': C.selected, 'line-width': 2, 'line-opacity': 0.45 },
    },
    beforeId,
  )
  map.addImage('bus-chevron', chevronImage(48), { pixelRatio: 2 })
  map.addImage('warn-badge', badgeImage(48), { pixelRatio: 3 })
  map.addImage('warn-badge-error', badgeImage(48, C.removed), { pixelRatio: 3 })
  map.addImage('stop-terminal', terminalBar(32), { pixelRatio: 4 })

  // §4.1 — черта поверх круга конечной; сама остановка уже покрашена как
  // принадлежащая маршруту, черта только добавляет ей форму
  map.addLayer(
    {
      id: OVERLAY_LYR.terminals,
      type: 'symbol',
      source: OVERLAY_SRC.terminals,
      minzoom: 13,
      layout: {
        'icon-image': 'stop-terminal',
        'icon-size': ['interpolate', ['linear'], ['zoom'], 13, 0.5, 16, 1],
        'icon-allow-overlap': true,
      },
    },
    beforeId,
  )
  map.addImage('route-shield', shieldImage(32), { pixelRatio: 2, sdf: true })

  map.addLayer(
    {
      id: OVERLAY_LYR.buses,
      type: 'symbol',
      source: OVERLAY_SRC.buses,
      minzoom: 12,
      layout: {
        'icon-image': 'bus-chevron',
        // §5 — шеврон 8 px на среднем зуме, чуть крупнее на близком
        'icon-size': ['interpolate', ['linear'], ['zoom'], 12, 0.34, 16, 0.5],
        'icon-rotate': ['get', 'bearing'],
        'icon-rotation-alignment': 'map',
        'icon-allow-overlap': true,
      },
      // §14 — при смене часа борта перескакивают за 150 мс, без промежуточного хода
      paint: { 'icon-opacity': 1, 'icon-opacity-transition': { duration: 150, delay: 0 } },
    },
    beforeId,
  )

  map.addLayer({
    id: OVERLAY_LYR.shields,
    type: 'symbol',
    source: OVERLAY_SRC.shields,
    minzoom: 12,
    layout: {
      'symbol-placement': 'point',
      'icon-image': 'route-shield',
      'icon-text-fit': 'both',
      'icon-text-fit-padding': [3, 6, 3, 6],
      'text-field': ['get', 'route_num'],
      'text-font': ['Open Sans Regular'],
      'text-size': 11,
      'text-allow-overlap': false,
      'icon-allow-overlap': false,
      // §3.4 — щитки вытесняют названия остановок
      'symbol-sort-key': 0,
    },
    paint: { 'icon-color': C.selected, 'text-color': '#FFFFFF' },
  })
}

/** §1 — бейджи лежат выше всего: их видно и поверх подписей. */
export function addWarningLayer(map: MlMap): void {
  map.addLayer({
    id: OVERLAY_LYR.warnings,
    type: 'symbol',
    source: OVERLAY_SRC.warnings,
    minzoom: 12,
    layout: {
      'icon-image': ['case', ['==', ['get', 'severity'], 'error'], 'warn-badge-error', 'warn-badge'],
      // §9 — треугольник 12 px: картинка 48 px при pixelRatio 3 даёт базу 16 px
      'icon-size': ['interpolate', ['linear'], ['zoom'], 12, 0.75, 16, 1],
      'icon-allow-overlap': true,
      'icon-offset': [0, -10],
    },
  })
}

export interface WarningPoint {
  id: number
  coord: LngLat
  severity: string
  /** Все правила, сработавшие в этой точке: одна метка — один список. */
  messages: string[]
}

export function setWarnings(map: MlMap, points: WarningPoint[]): void {
  const source = map.getSource(OVERLAY_SRC.warnings) as GeoJSONSource | undefined
  if (!source) return
  source.setData({
    type: 'FeatureCollection',
    features: points.map((p) => ({
      type: 'Feature',
      id: p.id,
      geometry: { type: 'Point', coordinates: p.coord },
      properties: { severity: p.severity, messages: p.messages.join('\n') },
    })),
  })
}

export function setTerminals(map: MlMap, coords: LngLat[]): void {
  const source = map.getSource(OVERLAY_SRC.terminals) as GeoJSONSource | undefined
  if (!source) return
  source.setData({
    type: 'FeatureCollection',
    features: coords.map((coord) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: coord },
      properties: {},
    })),
  })
}

export function setBuses(map: MlMap, buses: BusPosition[]): void {
  const source = map.getSource(OVERLAY_SRC.buses) as GeoJSONSource | undefined
  if (!source) return
  source.setData({
    type: 'FeatureCollection',
    features: buses.map((b) => ({
      type: 'Feature',
      id: b.index,
      geometry: { type: 'Point', coordinates: b.coord },
      properties: { index: b.index, bearing: b.bearing, arrives: b.arrivesInMin },
    })),
  })
}

/**
 * §3.4 — щитки вдоль линии каждые 250 px экрана плюс обязательно на концах.
 * Шаг считается в метрах по текущему масштабу: `symbol-spacing` умеет только
 * повторять символ вдоль линии, а нам нужны ещё и оба конца.
 */
export function setShields(
  map: MlMap,
  routeNum: string | null,
  line: LngLat[],
  cum: number[],
  gapIndices: number[] = [],
): void {
  const source = map.getSource(OVERLAY_SRC.shields) as GeoJSONSource | undefined
  if (!source) return
  if (!routeNum || line.length < 2) {
    source.setData(empty())
    return
  }

  const total = cum[cum.length - 1]
  const metersPerPixel = metersPerPx(map)
  const step = Math.max(250 * metersPerPixel, total / 12)

  const positions: number[] = [0]
  for (let m = step; m < total - step / 2; m += step) positions.push(m)
  positions.push(total)

  // щиток на шве висел бы над пустотой, где линии нет
  const spans = gapIndices
    .filter((i) => i + 1 < cum.length)
    .map((i) => [cum[i], cum[i + 1]] as [number, number])
  const kept = positions.filter((m) => !spans.some(([from, to]) => m > from && m < to))

  const features: Feature[] = kept.map((m) => {
    let i = 1
    while (i < cum.length - 1 && cum[i] < m) i++
    const segLen = cum[i] - cum[i - 1]
    const t = segLen > 0 ? (m - cum[i - 1]) / segLen : 0
    const a = line[i - 1]
    const b = line[i]
    return {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t] },
      properties: { route_num: routeNum },
    }
  })
  source.setData({ type: 'FeatureCollection', features })
}

function metersPerPx(map: MlMap): number {
  const centre = map.getCenter()
  const scale = 40075016.686 * Math.cos((centre.lat * Math.PI) / 180)
  return scale / (512 * Math.pow(2, map.getZoom()))
}

/**
 * §14 — зона рисуется ростом по сети за 400 мс: фильтр пропускает рёбра,
 * до которых «дошли» к текущему моменту. `prefers-reduced-motion` выключает
 * рост, но не саму зону.
 */
export function setWalkZone(
  map: MlMap,
  zone: { edges: { coords: [number, number][]; d: number }[]; limit_m: number } | null,
): void {
  const source = map.getSource(OVERLAY_SRC.walkZone) as GeoJSONSource | undefined
  if (!source || !map.getLayer(OVERLAY_LYR.walkZone)) return

  if (!zone) {
    source.setData(empty())
    map.setFilter(OVERLAY_LYR.walkZone, null)
    return
  }

  source.setData({
    type: 'FeatureCollection',
    features: zone.edges.map((e) => ({
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: e.coords },
      properties: { d: e.d },
    })),
  })

  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (reduced) {
    map.setFilter(OVERLAY_LYR.walkZone, null)
    return
  }

  const started = performance.now()
  const grow = () => {
    if (!map.getLayer(OVERLAY_LYR.walkZone)) return
    const share = Math.min(1, (performance.now() - started) / 400)
    map.setFilter(OVERLAY_LYR.walkZone, ['<=', ['get', 'd'], share * zone.limit_m])
    if (share < 1) requestAnimationFrame(grow)
  }
  grow()
}
