/** Карта: подложка, сеть, остановки, наведение, выбор и режим редактирования. */

import { useEffect, useRef, useState } from 'react'
import { Map as MlMap, type MapMouseEvent, type Point, type PointLike } from 'maplibre-gl'
import type {
  LineString as GeoLineString,
  MultiLineString as GeoMultiLineString,
  Point as GeoPoint,
} from 'geojson'
import type { Direction, StopsCollection } from './api'
import { CITY_CENTER, CITY_MAX_BOUNDS, CITY_ZOOM, loadMutedStyle } from './basemap'
import type { NetworkIndex, SegmentFeature } from './network'
import { addDataLayers, applySelection, LYR, segmentPropsById, SRC, type Selection } from './mapLayers'
import {
  addEditLayers,
  EDIT_LYR,
  EDIT_SRC,
  setChangedGeometry,
  setDraft,
  setGhostInsert,
  setHandles,
  type HandlePoint,
} from './editLayer'
import { addHexLayers, buildHexes, HEX_LYR, setChangedHexes, setHexVisibility } from './hexLayer'
import {
  addOverlayLayers,
  addWarningLayer,
  OVERLAY_LYR,
  setBuses,
  setShields,
  setTerminals,
  setWalkZone,
  setWarnings,
  type WarningPoint,
} from './overlayLayer'
import type { BusPosition } from './buses'
import { cumulative, projectOnLine, pointAtMeasure } from './geo'
import type { BaselineHex, Hole, ScenarioResult } from './api'
import type { LngLat } from './geo'
import { HIT } from './tokens'

export interface MapViewProps {
  network: NetworkIndex
  stops: StopsCollection
  selection: Selection | null
  /** §3.1 — второй выбранный маршрут в режиме сравнения. */
  compare: Selection | null
  selectedStop: string | null
  editing: boolean
  /** Текущая цепочка выбранного маршрута с учётом правок. */
  chain: HandlePoint[]
  pickedSeq: number | null
  changedGeometry: LngLat[] | null
  hexes: BaselineHex[]
  holes: Hole[]
  showHexes: boolean
  showHoles: boolean
  scenarioResult: ScenarioResult | null
  /** §5 — позиции машин на выбранный час. */
  buses: BusPosition[]
  /** §3.4 — трасса выбранного маршрута, вдоль неё расставляются щитки. */
  selectedLine: LngLat[]
  /** Рёбра-швы выбранной трассы: на них ничего не ставим. */
  selectedGaps: number[]
  onBusHover: (info: { index: number; total: number; arrives: number; x: number; y: number } | null) => void
  /** §12 — зона пешей доступности выбранной остановки. */
  walkZone: { edges: { coords: [number, number][]; d: number }[]; limit_m: number } | null
  /** §4.1 — концы выбранного маршрута: у них своя форма. */
  terminals: LngLat[]
  /** §9 — метки сработавших правил на карте. */
  warnings: WarningPoint[]
  onWarnHover: (info: { messages: string[]; x: number; y: number } | null) => void
  onHexHover: (info: HexHover | null) => void
  /** Клик по слою населения: точка, по которой считается ячейка H3. */
  onHexClick: (at: LngLat) => void
  onSelectRoute: (routeNum: string, direction: Direction) => void
  onSelectStop: (stopId: string | null) => void
  onClearSelection: () => void
  onExtend: (stopId: string) => void
  onExtendPreview: (stopId: string | null) => void
  onTrim: (seq: number) => void
  onPickHandle: (seq: number | null) => void
  onInsertAt: (afterSeq: number, at: LngLat) => void
  onContextMenu: (info: ContextTarget) => void
  onBasemapMissing: () => void
  onReady: (map: MlMap) => void
}

type HoverRef = { source: string; id: string | number } | null

export interface ContextTarget {
  x: number
  y: number
  stopId: string | null
  routeNum: string | null
  direction: Direction | null
}

export interface HexHover {
  pop: number
  walkMin: number
  covered: boolean
  frequent: boolean
  x: number
  y: number
}

/** §8.2 — курсор притягивается к остановке в этом радиусе. */
const SNAP_PX = 30

export function MapView(props: MapViewProps): React.JSX.Element {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MlMap | null>(null)
  const readyRef = useRef(false)
  // Готовность держим и состоянием: ref не вызывает перерисовку, и эффекты
  // слоёв, отработавшие до загрузки карты, больше никогда не повторялись —
  // борта и щитки молча не появлялись, если данные пришли раньше стиля.
  const [ready, setReady] = useState(false)
  const recentered = useRef<Set<number>>(new Set())
  const markedStops = useRef<Set<string>>(new Set())
  const propsById = useRef<Map<number, SegmentFeature['properties']>>(new Map())
  const hovered = useRef<HoverRef>(null)
  const dragging = useRef<{ from: LngLat; snapped: string | null } | null>(null)
  // колбэки и данные меняются каждый рендер, а обработчики вешаются один раз
  const latest = useRef(props)
  latest.current = props

  useEffect(() => {
    let cancelled = false
    const el = container.current
    if (!el) return

    void (async () => {
      const { style, muted } = await loadMutedStyle()
      if (cancelled) return
      if (!muted) latest.current.onBasemapMissing()

      const map = new MlMap({
        container: el,
        style,
        center: CITY_CENTER,
        zoom: CITY_ZOOM,
        maxBounds: CITY_MAX_BOUNDS,
        minZoom: 10,
        maxZoom: 18,
        pitchWithRotate: false,
        dragRotate: false,
        touchZoomRotate: false,
        attributionControl: { compact: true },
      })
      mapRef.current = map
      if (import.meta.env.DEV) (window as unknown as { __map: MlMap }).__map = map
      map.on('error', (e) => console.error('[map]', (e as { error?: Error }).error ?? e))
      map.dragRotate.disable()
      map.touchZoomRotate.disableRotation()

      map.on('load', () => {
        if (cancelled) return
        // §1 — гексагоны лежат под трассами: слои добавляются перед сетью
        const { cells, scale } = buildHexes(latest.current.hexes, latest.current.holes)
        addDataLayers(map, latest.current.network, latest.current.stops)
        addHexLayers(map, cells, scale, LYR.routesIdle)
        addEditLayers(map)
        // §1 — борта под подписями, щитки среди подписей, бейджи поверх всего
        addOverlayLayers(map, LYR.stopLabels)
        addWarningLayer(map)
        propsById.current = segmentPropsById(latest.current.network)
        readyRef.current = true
        setReady(true)
        applySelection(
          map,
          latest.current.network,
          propsById.current,
          latest.current.selection,
          recentered.current,
          markedStops.current,
          latest.current.compare,
        )
        latest.current.onReady(map)
      })

      const box = (point: Point, r: number): [PointLike, PointLike] => [
        [point.x - r, point.y - r],
        [point.x + r, point.y + r],
      ]

      const handleAt = (point: Point) => {
        if (!map.getLayer(EDIT_LYR.handles)) return null
        return map.queryRenderedFeatures(box(point, HIT.stop), { layers: [EDIT_LYR.handles] })[0] ?? null
      }

      const stopAt = (point: Point, radius: number = HIT.stop) => {
        const layers = [LYR.stops, LYR.stopsMetro].filter((l) => map.getLayer(l))
        return map.queryRenderedFeatures(box(point, radius), { layers })[0] ?? null
      }

      const pickAt = (point: Point) => {
        // §1: клик идёт сверху вниз, остановка перехватывает раньше линии
        const stop = stopAt(point)
        if (stop) return { kind: 'stop' as const, feature: stop }
        const line = map.queryRenderedFeatures(box(point, HIT.line), {
          layers: [LYR.routesIdle, LYR.routesSelected, LYR.routesAlt].filter((l) => map.getLayer(l)),
        })[0]
        if (line) return { kind: 'route' as const, feature: line }
        return null
      }

      // §9 — наведение на бейдж: текст правила, которое сработало
      const reportWarning = (e: MapMouseEvent) => {
        if (!map.getLayer(OVERLAY_LYR.warnings)) return false
        const hit = map.queryRenderedFeatures(box(e.point, HIT.stop), { layers: [OVERLAY_LYR.warnings] })[0]
        if (!hit) {
          latest.current.onWarnHover(null)
          return false
        }
        latest.current.onWarnHover({
          messages: String(hit.properties?.messages ?? '').split('\n').filter(Boolean),
          x: e.point.x,
          y: e.point.y,
        })
        return true
      }

      // §5 — наведение на борт: какой он по счёту и когда придёт на конечную
      const reportBus = (e: MapMouseEvent) => {
        if (!map.getLayer(OVERLAY_LYR.buses)) return false
        const hit = map.queryRenderedFeatures(box(e.point, HIT.stop), { layers: [OVERLAY_LYR.buses] })[0]
        if (!hit) {
          latest.current.onBusHover(null)
          return false
        }
        latest.current.onBusHover({
          index: Number(hit.properties?.index ?? 0),
          total: latest.current.buses.length,
          arrives: Number(hit.properties?.arrives ?? 0),
          x: e.point.x,
          y: e.point.y,
        })
        return true
      }

      // §7 — подсказка по гексагону: сколько людей и сколько идти до остановки
      const reportHex = (e: MapMouseEvent) => {
        if (!latest.current.showHexes) return latest.current.onHexHover(null)
        // берём и заливку, и контуры: у непокрытых ячеек заливки нет
        const layers = [HEX_LYR.fill, HEX_LYR.uncovered, HEX_LYR.frequent].filter((l) =>
          map.getLayer(l),
        )
        const hex = map.queryRenderedFeatures(e.point, { layers })[0]
        if (!hex) return latest.current.onHexHover(null)
        latest.current.onHexHover({
          pop: Number(hex.properties?.pop ?? 0),
          walkMin: Number(hex.properties?.walk_min ?? 0),
          covered: Boolean(hex.properties?.covered),
          frequent: Boolean(hex.properties?.frequent),
          x: e.point.x,
          y: e.point.y,
        })
      }

      const clearHover = () => {
        if (hovered.current) {
          map.removeFeatureState(hovered.current, 'hover')
          hovered.current = null
        }
      }

      const setHover = (source: string, id: string | number) => {
        if (hovered.current && hovered.current.id === id && hovered.current.source === source) return
        clearHover()
        hovered.current = { source, id }
        map.setFeatureState(hovered.current, { hover: true })
      }

      map.on('mousedown', (e: MapMouseEvent) => {
        if (!readyRef.current || !latest.current.editing) return
        const handle = handleAt(e.point)
        if (!handle?.properties?.tail) return
        e.preventDefault()
        map.dragPan.disable()
        dragging.current = { from: (handle.geometry as GeoPoint).coordinates as LngLat, snapped: null }
        map.getCanvas().style.cursor = 'grabbing'
      })

      map.on('mousemove', (e: MapMouseEvent) => {
        if (!readyRef.current) return

        if (dragging.current) {
          const stop = stopAt(e.point, SNAP_PX)
          const snappedId = stop ? String(stop.properties?.stop_id ?? '') : null
          const snapCoord = stop ? ((stop.geometry as GeoPoint).coordinates as LngLat) : null
          const cursor: LngLat = [e.lngLat.lng, e.lngLat.lat]
          setDraft(map, [dragging.current.from, snapCoord ?? cursor], snapCoord)
          if (snappedId !== dragging.current.snapped) {
            dragging.current.snapped = snappedId
            latest.current.onExtendPreview(snappedId)
          }
          return
        }

        if (latest.current.editing) {
          const handle = handleAt(e.point)
          if (handle && handle.id !== undefined) {
            setHover(EDIT_SRC.handles, handle.id)
            map.getCanvas().style.cursor = handle.properties?.tail ? 'grab' : 'pointer'
            setGhostInsert(map, null)
            return
          }
          clearHover()

          // §8.4 — над участком линии показываем призрачную точку: сюда
          // встанет остановка, если кликнуть. Точка садится на саму линию,
          // а не под курсор, иначе она обещает место, которого на трассе нет.
          const onLine = map.queryRenderedFeatures(box(e.point, HIT.line), {
            layers: [LYR.routesSelected, EDIT_LYR.changed].filter((l) => map.getLayer(l)),
          })[0]
          if (onLine) {
            const geometry = onLine.geometry as GeoLineString | GeoMultiLineString
            const parts: LngLat[][] =
              geometry.type === 'LineString'
                ? [geometry.coordinates as LngLat[]]
                : (geometry.coordinates as LngLat[][])
            const cursor: LngLat = [e.lngLat.lng, e.lngLat.lat]
            let best: { coord: LngLat; offset: number } | null = null
            for (const part of parts) {
              if (part.length < 2) continue
              const cum = cumulative(part)
              const hit = projectOnLine(part, cum, cursor)
              if (!best || hit.offset < best.offset) {
                best = { coord: pointAtMeasure(part, cum, hit.measure), offset: hit.offset }
              }
            }
            setGhostInsert(map, best?.coord ?? null)
            map.getCanvas().style.cursor = 'copy'
            return
          }

          setGhostInsert(map, null)
          map.getCanvas().style.cursor = 'crosshair'
          return
        }

        if (reportWarning(e)) {
          clearHover()
          map.getCanvas().style.cursor = 'pointer'
          return
        }

        if (reportBus(e)) {
          clearHover()
          map.getCanvas().style.cursor = 'pointer'
          return
        }

        const hit = pickAt(e.point)
        if (!hit || hit.feature.id === undefined) {
          clearHover()
          map.getCanvas().style.cursor = ''
          reportHex(e)
          return
        }
        map.getCanvas().style.cursor = 'pointer'
        setHover(hit.kind === 'stop' ? SRC.stops : SRC.network, hit.feature.id)
        latest.current.onHexHover(null)
      })

      map.on('mouseup', () => {
        if (!dragging.current) return
        const snapped = dragging.current.snapped
        dragging.current = null
        map.dragPan.enable()
        setDraft(map, null, null)
        map.getCanvas().style.cursor = 'crosshair'
        // §8.2: отпускание вне остановки — правка не применяется, без модалок
        if (snapped) latest.current.onExtend(snapped)
        else latest.current.onExtendPreview(null)
      })

      map.on('mouseout', () => {
        clearHover()
        if (dragging.current) {
          dragging.current = null
          map.dragPan.enable()
          setDraft(map, null, null)
          latest.current.onExtendPreview(null)
        }
      })

      // §8.3 — правая кнопка называет словами то, что иначе делается Shift'ом
      map.on('contextmenu', (e: MapMouseEvent) => {
        if (!readyRef.current) return
        e.preventDefault()
        const stop = stopAt(e.point)
        const line = map.queryRenderedFeatures(box(e.point, HIT.line), {
          layers: [LYR.routesIdle, LYR.routesSelected, LYR.routesAlt].filter((l) => map.getLayer(l)),
        })[0]
        const props = line?.properties as { route_num?: string; direction?: Direction } | undefined
        latest.current.onContextMenu({
          x: e.point.x,
          y: e.point.y,
          stopId: stop ? String(stop.properties?.stop_id ?? '') : null,
          routeNum: props?.route_num ?? null,
          direction: props?.direction ?? null,
        })
      })

      map.on('click', (e: MapMouseEvent) => {
        if (!readyRef.current) return

        if (latest.current.editing) {
          const handle = handleAt(e.point)
          if (handle) {
            const seq = Number(handle.properties?.seq ?? -1)
            if (seq < 0) return
            // §8.3 — Shift обрезает маршрут до этой остановки
            if (e.originalEvent.shiftKey) latest.current.onTrim(seq)
            else latest.current.onPickHandle(seq)
            return
          }
          // §8.4 — клик по участку линии предлагает вставить остановку.
          // Требуем попадания в саму линию, иначе клик по пустому месту
          // открывал бы список на другом конце города.
          const onLine = map.queryRenderedFeatures(box(e.point, HIT.line), {
            layers: [LYR.routesSelected, EDIT_LYR.changed].filter((l) => map.getLayer(l)),
          })[0]
          if (onLine) {
            const after = nearestChainSegment(latest.current.chain, [e.lngLat.lng, e.lngLat.lat])
            if (after !== null) {
              latest.current.onInsertAt(after, [e.lngLat.lng, e.lngLat.lat])
              return
            }
          }
          latest.current.onPickHandle(null)
          return
        }

        const hit = pickAt(e.point)
        if (!hit) {
          // §7 — клик по ячейке слоя населения открывает её карточку.
          //
          // Ячейка ищется по координате, а не по отрисованным слоям: у
          // непокрытых ячеек заливки нет по требованию §7, есть только контур,
          // и `queryRenderedFeatures` в середине такой ячейки не попадает
          // никуда. То есть кликнуть было нельзя ровно по дырам покрытия —
          // по тем самым, ради которых карточка и нужна.
          if (latest.current.showHexes) {
            latest.current.onHexClick([e.lngLat.lng, e.lngLat.lat])
            return
          }
          latest.current.onClearSelection()
          return
        }
        if (hit.kind === 'stop') {
          latest.current.onSelectStop(String(hit.feature.properties?.stop_id ?? ''))
          return
        }
        const p = hit.feature.properties as { route_num?: string; direction?: Direction } | undefined
        if (p?.route_num) latest.current.onSelectRoute(p.route_num, p.direction ?? 'fwd')
      })
    })()

    return () => {
      cancelled = true
      mapRef.current?.remove()
      mapRef.current = null
      readyRef.current = false
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !readyRef.current) return
    applySelection(
      map,
      props.network,
      propsById.current,
      props.selection,
      recentered.current,
      markedStops.current,
      props.compare,
    )
  }, [ready, props.selection, props.compare, props.network])

  // выделенная остановка — тоже состояние объекта, а не перерисовка слоя
  const pickedStop = useRef<string | null>(null)
  useEffect(() => {
    const map = mapRef.current
    if (!map || !readyRef.current) return
    if (pickedStop.current) {
      map.removeFeatureState({ source: SRC.stops, id: pickedStop.current }, 'selected')
      pickedStop.current = null
    }
    if (props.selectedStop) {
      map.setFeatureState({ source: SRC.stops, id: props.selectedStop }, { selected: true })
      pickedStop.current = props.selectedStop
    }
  }, [ready, props.selectedStop])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !readyRef.current) return
    setHandles(map, props.editing ? props.chain : [])
    if (!props.editing) setGhostInsert(map, null)
    map.getCanvas().style.cursor = props.editing ? 'crosshair' : ''
    // Рамочное увеличение включается по Shift и съедает событие click вместе
    // с ним — из-за этого «Shift+клик по ручке — обрезать» (§8.3) не доходил
    // до обработчика вовсе. В правке рамка не нужна, а обрезка нужна.
    if (props.editing) map.boxZoom.disable()
    else map.boxZoom.enable()
    // §8: в режиме редактирования остальная сеть гаснет сильнее обычного,
    // а на выходе выражение прозрачности возвращает правило приглушения
    if (props.editing) map.setPaintProperty(LYR.routesIdle, 'line-opacity', 0.08)
    else {
      applySelection(
        map,
        props.network,
        propsById.current,
        props.selection,
        recentered.current,
        markedStops.current,
        props.compare,
      )
    }
  }, [ready, props.editing, props.chain, props.network, props.selection, props.compare])

  const markedSeq = useRef<number | null>(null)
  useEffect(() => {
    const map = mapRef.current
    if (!map || !readyRef.current) return
    if (markedSeq.current !== null) {
      map.removeFeatureState({ source: EDIT_SRC.handles, id: markedSeq.current }, 'picked')
      markedSeq.current = null
    }
    if (props.pickedSeq !== null) {
      map.setFeatureState({ source: EDIT_SRC.handles, id: props.pickedSeq }, { picked: true })
      markedSeq.current = props.pickedSeq
    }
  }, [ready, props.pickedSeq, props.chain])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !readyRef.current) return
    setChangedGeometry(map, props.changedGeometry)
  }, [ready, props.changedGeometry])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !readyRef.current) return
    setHexVisibility(map, props.showHexes, props.showHoles, props.selection !== null)
  }, [ready, props.showHexes, props.showHoles, props.selection])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !readyRef.current) return
    setChangedHexes(map, props.scenarioResult)
  }, [ready, props.scenarioResult])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !readyRef.current) return
    setBuses(map, props.buses)
  }, [ready, props.buses])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !readyRef.current) return
    setWarnings(map, props.warnings)
  }, [ready, props.warnings])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !readyRef.current) return
    setTerminals(map, props.terminals)
  }, [ready, props.terminals])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !readyRef.current) return
    setWalkZone(map, props.walkZone)
  }, [ready, props.walkZone])

  // щитки пересчитываются и при зуме: шаг между ними задан в пикселях экрана
  useEffect(() => {
    const map = mapRef.current
    if (!map || !readyRef.current) return
    const line = props.selectedLine
    const draw = () =>
      setShields(
        map,
        props.selection?.routeNum ?? null,
        line,
        line.length > 1 ? cumulative(line) : [],
        props.selectedGaps,
      )
    draw()
    map.on('zoomend', draw)
    return () => {
      map.off('zoomend', draw)
    }
  }, [ready, props.selectedLine, props.selectedGaps, props.selection])

  return <div id="map" ref={container} />
}

/** Индекс остановки, после которой лежит точка клика на цепочке. */
function nearestChainSegment(chain: HandlePoint[], point: LngLat): number | null {
  if (chain.length < 2) return null
  let bestIndex: number | null = null
  let bestDistance = Infinity
  for (let i = 1; i < chain.length; i++) {
    const d = distanceToSegment(point, chain[i - 1].coord, chain[i].coord)
    if (d < bestDistance) {
      bestDistance = d
      bestIndex = i - 1
    }
  }
  return bestIndex
}

function distanceToSegment(p: LngLat, a: LngLat, b: LngLat): number {
  const dx = b[0] - a[0]
  const dy = b[1] - a[1]
  const len2 = dx * dx + dy * dy
  const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2))
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))
}
