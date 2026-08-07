/** Карта: подложка, сеть, остановки, наведение, выбор и режим редактирования. */

import { useEffect, useRef, useState } from 'react'
import { Map as MlMap, type MapMouseEvent, type PointLike } from 'maplibre-gl'
import type {
  LineString as GeoLineString,
  MultiLineString as GeoMultiLineString,
  Point as GeoPoint,
} from 'geojson'
import type { Direction, StopsCollection } from './api'
import { CITY_CENTER, CITY_MAX_BOUNDS, CITY_ZOOM, loadMutedStyle } from './basemap'
import type { NetworkIndex, SegmentFeature } from './network'
import {
  addDataLayers,
  applySelection,
  LYR,
  segmentPropsById,
  setRegistryTraceVisible,
  SRC,
  type Selection,
} from './mapLayers'
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
  /**
   * Инструмент карты. Раньше сюда приходило одно `editing: boolean`, и правка
   * трассы со вставкой остановки жили в одном режиме — клик доставался тому,
   * кто первым его поймает. Теперь у каждого свой.
   */
  tool: 'select' | 'edit' | 'insert'
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

/**
 * Экранная точка. Своя, а не maplibre-вская: во время перетаскивания она
 * собирается из события окна, и тащить ради этого класс Point незачем.
 */
interface ScreenPoint {
  x: number
  y: number
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

/**
 * §8.2 — курсор притягивается к остановке в этом радиусе.
 * Тридцати пикселей не хватало: попасть в остановку при перетаскивании
 * получалось не с первого раза, а промах молча отменяет правку.
 */
const SNAP_PX = 64
/**
 * Shift+клик обрезает маршрут. Радиус больше обычного попадания по ручке:
 * промах мимо ручки раньше проваливался во вставку остановки, то есть
 * неточное движение делало не то, что просили.
 */
const TRIM_PX = 26
/** Ближе этого к краю окна карта подкручивается сама во время перетаскивания. */
const EDGE_PX = 70
/** Максимальная скорость автопрокрутки, пикселей за кадр. */
const EDGE_SPEED_PX = 14

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
  const dragging = useRef<{ from: LngLat; snapped: string | null; point: ScreenPoint } | null>(null)
  /** Кадр автопрокрутки к краю окна во время перетаскивания. */
  const edgePan = useRef<number | null>(null)
  const endDragRef = useRef<(() => void) | null>(null)
  const windowMoveRef = useRef<((ev: MouseEvent) => void) | null>(null)
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

      const box = (point: ScreenPoint, r: number): [PointLike, PointLike] => [
        [point.x - r, point.y - r],
        [point.x + r, point.y + r],
      ]

      const handleAt = (point: ScreenPoint, radius: number = HIT.stop) => {
        if (!map.getLayer(EDIT_LYR.handles)) return null
        return map.queryRenderedFeatures(box(point, radius), { layers: [EDIT_LYR.handles] })[0] ?? null
      }

      const stopAt = (point: ScreenPoint, radius: number = HIT.stop) => {
        const layers = [LYR.stops, LYR.stopsMetro].filter((l) => map.getLayer(l))
        return map.queryRenderedFeatures(box(point, radius), { layers })[0] ?? null
      }

      const pickAt = (point: ScreenPoint) => {
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

      /**
       * Перерисовать штриховую линию и цель под текущей точкой курсора.
       * Вынесено из mousemove, потому что то же самое нужно каждому кадру
       * автопрокрутки: карта уехала — цель под курсором стала другой.
       */
      const refreshDraft = (point: ScreenPoint) => {
        if (!dragging.current) return
        const stop = stopAt(point, SNAP_PX)
        const snappedId = stop ? String(stop.properties?.stop_id ?? '') : null
        const snapCoord = stop ? ((stop.geometry as GeoPoint).coordinates as LngLat) : null
        const cursor = map.unproject([point.x, point.y])
        setDraft(
          map,
          [dragging.current.from, snapCoord ?? [cursor.lng, cursor.lat]],
          snapCoord,
        )
        if (snappedId !== dragging.current.snapped) {
          dragging.current.snapped = snappedId
          latest.current.onExtendPreview(snappedId)
        }
      }

      /**
       * Точка курсора в координатах холста — из события окна.
       *
       * Слушать окно, а не карту, обязательно: справа и слева карту закрывают
       * панели, и как только курсор наезжает на них, MapLibre перестаёт слать
       * mousemove. До края окна при этом не добраться — а именно к краю его и
       * ведут, чтобы карта подкрутилась.
       */
      const pointFromWindow = (ev: MouseEvent): ScreenPoint => {
        const rect = map.getCanvas().getBoundingClientRect()
        return { x: ev.clientX - rect.left, y: ev.clientY - rect.top }
      }

      const onWindowMove = (ev: MouseEvent) => {
        if (!dragging.current) return
        dragging.current.point = pointFromWindow(ev)
        refreshDraft(dragging.current.point)
      }

      /** Насколько подкрутить карту: тем быстрее, чем ближе курсор к краю. */
      const edgeShift = (point: ScreenPoint): [number, number] => {
        const { clientWidth: w, clientHeight: h } = map.getCanvas()
        const push = (near: number) => Math.min(1, Math.max(0, (EDGE_PX - near) / EDGE_PX))
        const dx = push(point.x) * -EDGE_SPEED_PX + push(w - point.x) * EDGE_SPEED_PX
        const dy = push(point.y) * -EDGE_SPEED_PX + push(h - point.y) * EDGE_SPEED_PX
        return [dx, dy]
      }

      const stopEdgePan = () => {
        if (edgePan.current !== null) cancelAnimationFrame(edgePan.current)
        edgePan.current = null
      }

      // §8.2 — тянуть конец трассы за край окна: без этого продлить маршрут
      // дальше видимой области было нельзя, а зумировать одной рукой некуда
      const edgeStep = () => {
        const state = dragging.current
        if (!state) return stopEdgePan()
        const [dx, dy] = edgeShift(state.point)
        if (dx || dy) {
          // jumpTo, а не panBy: panBy идёт через easeTo, а тот применяет
          // сдвиг на следующем кадре отрисовки. Пока курсор стоит у края и
          // новых событий мыши нет, каждый следующий вызов отменял предыдущий,
          // и карта не двигалась вовсе. jumpTo меняет камеру сразу.
          const canvas = map.getCanvas()
          map.jumpTo({
            center: map.unproject([canvas.clientWidth / 2 + dx, canvas.clientHeight / 2 + dy]),
          })
          refreshDraft(state.point)
        }
        edgePan.current = requestAnimationFrame(edgeStep)
      }

      map.on('mousedown', (e: MapMouseEvent) => {
        // Тянуть можно только в режиме правки трассы. Во «вставке» перетаскивание
        // ручки означало бы продление — а человек выбрал другой инструмент.
        if (!readyRef.current || latest.current.tool !== 'edit') return
        const handle = handleAt(e.point)
        if (!handle?.properties?.tail) return
        e.preventDefault()
        map.dragPan.disable()
        dragging.current = {
          from: (handle.geometry as GeoPoint).coordinates as LngLat,
          snapped: null,
          point: e.point,
        }
        map.getCanvas().style.cursor = 'grabbing'
        stopEdgePan()
        // capture: событие ловится до того, как его успеет остановить
        // что-нибудь из панелей, лежащих поверх карты
        window.addEventListener('mousemove', onWindowMove, true)
        edgePan.current = requestAnimationFrame(edgeStep)
      })

      map.on('mousemove', (e: MapMouseEvent) => {
        if (!readyRef.current) return

        // во время перетаскивания курсор ведёт окно: см. onWindowMove
        if (dragging.current) return

        const tool = latest.current.tool
        if (tool !== 'select') {
          const handle = tool === 'edit' ? handleAt(e.point) : null
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
          const onLine =
            tool === 'insert'
              ? map.queryRenderedFeatures(box(e.point, HIT.line), {
                  layers: [LYR.routesSelected, EDIT_LYR.changed].filter((l) => map.getLayer(l)),
                })[0]
              : undefined
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

      const endDrag = () => {
        if (!dragging.current) return
        const snapped = dragging.current.snapped
        dragging.current = null
        stopEdgePan()
        window.removeEventListener('mousemove', onWindowMove, true)
        map.dragPan.enable()
        setDraft(map, null, null)
        map.getCanvas().style.cursor = 'crosshair'
        // §8.2: отпускание вне остановки — правка не применяется, без модалок
        if (snapped) latest.current.onExtend(snapped)
        else latest.current.onExtendPreview(null)
      }

      map.on('mouseup', endDrag)
      // Кнопку могут отпустить за пределами холста — при автопрокрутке курсор
      // как раз стоит у самого края. Без окна перетаскивание бы не кончилось.
      window.addEventListener('mouseup', endDrag)
      endDragRef.current = endDrag
      windowMoveRef.current = onWindowMove

      // Курсор ушёл за пределы карты — но перетаскивание не отменяем: во время
      // него он уходит к краю намеренно, чтобы карта подкрутилась под ним.
      map.on('mouseout', () => {
        clearHover()
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

        // Инструменты не спорят за клик: правка трассы работает по ручкам,
        // вставка — по линии. Раньше оба жили в одном режиме, и Shift, чуть
        // промахнувшийся мимо ручки, проваливался во вставку остановки.
        if (latest.current.tool === 'edit') {
          // §8.3 — Shift обрезает маршрут до этой остановки. Радиус больше
          // обычного: обрезка необратимее выбора, промах дороже.
          if (e.originalEvent.shiftKey) {
            const target = handleAt(e.point, TRIM_PX)
            const seq = Number(target?.properties?.seq ?? -1)
            if (target && seq >= 0) latest.current.onTrim(seq)
            return
          }
          const handle = handleAt(e.point)
          const seq = Number(handle?.properties?.seq ?? -1)
          latest.current.onPickHandle(handle && seq >= 0 ? seq : null)
          return
        }

        if (latest.current.tool === 'insert') {
          // §8.4 — клик по участку линии предлагает вставить остановку.
          // Требуем попадания в саму линию, иначе клик по пустому месту
          // открывал бы список на другом конце города.
          const onLine = map.queryRenderedFeatures(box(e.point, HIT.line), {
            layers: [LYR.routesSelected, EDIT_LYR.changed].filter((l) => map.getLayer(l)),
          })[0]
          if (onLine) {
            const after = nearestChainSegment(latest.current.chain, [e.lngLat.lng, e.lngLat.lat])
            if (after !== null) latest.current.onInsertAt(after, [e.lngLat.lng, e.lngLat.lat])
          }
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
      if (edgePan.current !== null) cancelAnimationFrame(edgePan.current)
      if (endDragRef.current) window.removeEventListener('mouseup', endDragRef.current)
      if (windowMoveRef.current) window.removeEventListener('mousemove', windowMoveRef.current, true)
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
      props.changedGeometry !== null,
    )
  }, [ready, props.selection, props.compare, props.network, props.changedGeometry])

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
    // Ручки нужны обоим режимам правки: во вставке они показывают, между
    // какими остановками встанет новая, хотя тянуть за них там нельзя.
    const editing = props.tool !== 'select'
    setHandles(map, editing ? props.chain : [])
    if (props.tool !== 'insert') setGhostInsert(map, null)
    map.getCanvas().style.cursor = editing ? 'crosshair' : ''
    // Рамочное увеличение включается по Shift и съедает событие click вместе
    // с ним — из-за этого «Shift+клик по ручке — обрезать» (§8.3) не доходил
    // до обработчика вовсе. В правке рамка не нужна, а обрезка нужна.
    if (editing) map.boxZoom.disable()
    else map.boxZoom.enable()
    // §8: в режиме редактирования остальная сеть гаснет сильнее обычного,
    // а на выходе выражение прозрачности возвращает правило приглушения
    if (editing) {
      map.setPaintProperty(LYR.routesIdle, 'line-opacity', 0.08)
      // Обрезают маршрут именно отсюда, и линия из реестра должна уйти сразу,
      // а не после выхода из режима: applySelection в правке не вызывается.
      setRegistryTraceVisible(map, props.selection, props.changedGeometry === null)
    } else {
      applySelection(
        map,
        props.network,
        propsById.current,
        props.selection,
        recentered.current,
        markedStops.current,
        props.compare,
        props.changedGeometry !== null,
      )
    }
  }, [
    ready,
    props.tool,
    props.chain,
    props.network,
    props.selection,
    props.compare,
    props.changedGeometry,
  ])

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
