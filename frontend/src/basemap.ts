/**
 * Подложка (§2). Берём светлый векторный стиль и давим его до почти бумажного:
 * обводки дорог убираем совсем, POI выключаем, из подписей оставляем районы,
 * махалли и — с зума 15 — улицы.
 *
 * Проверка из спеки: скриншот без слоя данных должен выглядеть почти пустым.
 */

import type { StyleSpecification, LayerSpecification } from 'maplibre-gl'
import { C } from './tokens'

const BASE_STYLE_URL = 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json'

/** Ташкент. */
export const CITY_CENTER: [number, number] = [69.2797, 41.3111]
export const CITY_ZOOM = 11.2
export const CITY_MAX_BOUNDS: [[number, number], [number, number]] = [
  [68.9, 41.0],
  [69.7, 41.55],
]

const DROP_EXACT = new Set([
  'housenumber',
  'waterway_label',
  'aeroway-runway',
  'aeroway-taxiway',
  'boundary_county',
  'boundary_state',
  'boundary_country_outline',
  'boundary_country_inner',
  'rail_dash',
  'tunnel_rail_dash',
])

const DROP_PREFIX = ['poi_', 'watername_']

/** Подписи мест: оставляем только районы и махалли, остальное — шум внутри города. */
const KEEP_PLACE = new Set(['place_hamlet', 'place_suburbs'])

const MAJOR_ROAD = /_(mot|trunk|pri)_fill/

function isCasing(id: string): boolean {
  return id.includes('_case')
}

function muteLayer(layer: LayerSpecification): LayerSpecification | null {
  const id = layer.id
  if (DROP_EXACT.has(id)) return null
  if (DROP_PREFIX.some((p) => id.startsWith(p))) return null
  if (isCasing(id)) return null // §2: обводки дорог убраны совсем
  if (id.startsWith('place_') && !KEEP_PLACE.has(id)) return null

  const out = { ...layer } as LayerSpecification & {
    paint?: Record<string, unknown>
    layout?: Record<string, unknown>
    minzoom?: number
  }
  out.paint = { ...(out.paint as Record<string, unknown> | undefined) }
  out.layout = { ...(out.layout as Record<string, unknown> | undefined) }

  if (out.type === 'background') {
    out.paint['background-color'] = C.bg
    return out
  }

  const sourceLayer = (layer as { 'source-layer'?: string })['source-layer']

  if (out.type === 'fill') {
    if (sourceLayer === 'water') out.paint['fill-color'] = C.water
    else if (sourceLayer === 'park' || sourceLayer === 'landcover') out.paint['fill-color'] = C.green
    else if (sourceLayer === 'building') {
      out.paint['fill-color'] = C.building
      out.paint['fill-outline-color'] = C.building // §2: здания без обводки
      out.minzoom = 16
    } else out.paint['fill-color'] = C.built
    out.paint['fill-opacity'] = 1
    return out
  }

  if (out.type === 'line') {
    if (sourceLayer === 'waterway') {
      out.paint['line-color'] = C.water
      return out
    }
    out.paint['line-color'] = MAJOR_ROAD.test(id) ? C.roadMajor : C.roadMinor
    out.paint['line-opacity'] = 1
    return out
  }

  if (out.type === 'symbol') {
    out.paint['text-color'] = C.baseLabel
    out.paint['text-halo-color'] = C.bg
    out.paint['text-halo-width'] = 1
    out.layout['text-size'] = 11
    delete out.layout['icon-image'] // §2: значки подложки не нужны совсем
    if (sourceLayer === 'transportation_name') out.minzoom = Math.max(15, out.minzoom ?? 0)
    return out
  }

  return out
}

/** Стиль на случай, когда до подложки не достучались: карта данных работает и так. */
export function blankStyle(): StyleSpecification {
  return {
    version: 8,
    glyphs: 'https://tiles.basemaps.cartocdn.com/fonts/{fontstack}/{range}.pbf',
    sources: {},
    layers: [{ id: 'background', type: 'background', paint: { 'background-color': C.bg } }],
  }
}

export async function loadMutedStyle(): Promise<{ style: StyleSpecification; muted: boolean }> {
  try {
    const res = await fetch(BASE_STYLE_URL)
    if (!res.ok) throw new Error(String(res.status))
    const style = (await res.json()) as StyleSpecification
    const layers = style.layers.map(muteLayer).filter((l): l is LayerSpecification => l !== null)
    return { style: { ...style, layers }, muted: true }
  } catch {
    return { style: blankStyle(), muted: false }
  }
}
