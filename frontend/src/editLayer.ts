/**
 * Слои режима редактирования (§8): ручки на остановках маршрута, штриховая
 * линия за курсором при продлении и трасса, изменённая сценарием.
 *
 * Данные этих слоёв маленькие (десятки точек), поэтому они пересобираются
 * целиком — в отличие от сети, где так делать нельзя.
 */

import type { GeoJSONSource, Map as MlMap } from 'maplibre-gl'
import type { Feature, FeatureCollection } from 'geojson'
import type { LngLat } from './geo'
import { C, W } from './tokens'

export const EDIT_SRC = {
  handles: 'edit-handles',
  draft: 'edit-draft',
  changed: 'edit-changed',
  ghost: 'edit-ghost',
} as const
export const EDIT_LYR = {
  changed: 'edit-changed-line',
  draft: 'edit-draft-line',
  ghost: 'edit-ghost-point',
  handles: 'edit-handle-points',
  ghostInsert: 'edit-ghost-insert',
} as const

export interface HandlePoint {
  stopId: string
  seq: number
  /** Хвостовая ручка: только за неё можно тянуть — ядро продлевает с конца. */
  tail: boolean
  coord: LngLat
}

const empty = (): FeatureCollection => ({ type: 'FeatureCollection', features: [] })

export function addEditLayers(map: MlMap): void {
  map.addSource(EDIT_SRC.changed, { type: 'geojson', data: empty() })
  map.addSource(EDIT_SRC.draft, { type: 'geojson', data: empty() })
  map.addSource(EDIT_SRC.handles, { type: 'geojson', data: empty() })
  map.addSource(EDIT_SRC.ghost, { type: 'geojson', data: empty() })

  // §3.1 — изменённый в сценарии участок: та же линия, но штрихом 6/4
  map.addLayer({
    id: EDIT_LYR.changed,
    type: 'line',
    source: EDIT_SRC.changed,
    layout: { 'line-cap': 'butt', 'line-join': 'round' },
    paint: {
      'line-color': C.selected,
      'line-width': W.routeSelected,
      'line-dasharray': [6, 4],
    },
  })

  // §8.2 — линия от последней остановки к курсору
  map.addLayer({
    id: EDIT_LYR.draft,
    type: 'line',
    source: EDIT_SRC.draft,
    filter: ['==', ['geometry-type'], 'LineString'],
    layout: { 'line-cap': 'round' },
    paint: { 'line-color': C.selected, 'line-width': 2, 'line-dasharray': [4, 4] },
  })

  // точка привязки: сплошной кружок на остановке, к которой притянуло
  map.addLayer({
    id: EDIT_LYR.ghost,
    type: 'circle',
    source: EDIT_SRC.draft,
    filter: ['==', ['geometry-type'], 'Point'],
    paint: {
      'circle-radius': 6,
      'circle-color': C.added,
      'circle-stroke-color': '#FFFFFF',
      'circle-stroke-width': 2,
    },
  })

  // §8.4 — призрачная точка под курсором: сюда встанет вставленная остановка
  map.addLayer({
    id: EDIT_LYR.ghostInsert,
    type: 'circle',
    source: EDIT_SRC.ghost,
    paint: {
      'circle-radius': 5,
      'circle-color': C.selected,
      'circle-opacity': 0.4,
      'circle-stroke-color': C.selected,
      'circle-stroke-width': 1,
      'circle-stroke-opacity': 0.5,
    },
  })

  // §8.1 — ручки: концевая 10 px, промежуточные 7 px, под курсором крупнее
  map.addLayer({
    id: EDIT_LYR.handles,
    type: 'circle',
    source: EDIT_SRC.handles,
    paint: {
      'circle-radius': [
        'case',
        ['boolean', ['feature-state', 'hover'], false],
        ['case', ['get', 'tail'], 6, 4.5],
        ['case', ['get', 'tail'], 5, 3.5],
      ],
      'circle-color': [
        'case',
        ['boolean', ['feature-state', 'hover'], false],
        C.selected,
        ['case', ['boolean', ['feature-state', 'picked'], false], C.selected, '#FFFFFF'],
      ],
      'circle-stroke-color': C.selected,
      'circle-stroke-width': 2,
    },
  })
}

export function setHandles(map: MlMap, points: HandlePoint[]): void {
  const source = map.getSource(EDIT_SRC.handles) as GeoJSONSource | undefined
  if (!source) return
  source.setData({
    type: 'FeatureCollection',
    features: points.map((p) => ({
      type: 'Feature',
      id: p.seq,
      geometry: { type: 'Point', coordinates: p.coord },
      properties: { stop_id: p.stopId, seq: p.seq, tail: p.tail },
    })),
  })
}

export function setDraft(map: MlMap, line: LngLat[] | null, snap: LngLat | null): void {
  const source = map.getSource(EDIT_SRC.draft) as GeoJSONSource | undefined
  if (!source) return
  const features: Feature[] = []
  if (line && line.length >= 2) {
    features.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: line }, properties: {} })
  }
  if (snap) {
    features.push({ type: 'Feature', geometry: { type: 'Point', coordinates: snap }, properties: {} })
  }
  source.setData({ type: 'FeatureCollection', features })
}

export function setChangedGeometry(map: MlMap, line: LngLat[] | null): void {
  const source = map.getSource(EDIT_SRC.changed) as GeoJSONSource | undefined
  if (!source) return
  source.setData(
    line && line.length >= 2
      ? {
          type: 'FeatureCollection',
          features: [{ type: 'Feature', geometry: { type: 'LineString', coordinates: line }, properties: {} }],
        }
      : { type: 'FeatureCollection', features: [] },
  )
}

export function setGhostInsert(map: MlMap, coord: LngLat | null): void {
  const source = map.getSource(EDIT_SRC.ghost) as GeoJSONSource | undefined
  if (!source) return
  source.setData(
    coord
      ? {
          type: 'FeatureCollection',
          features: [{ type: 'Feature', geometry: { type: 'Point', coordinates: coord }, properties: {} }],
        }
      : empty(),
  )
}
