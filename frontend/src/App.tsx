import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Map as MlMap } from 'maplibre-gl'
import {
  api,
  type AssistantAction,
  type Baseline,
  type BaselineHex,
  type Direction,
  type HourHeadway,
  type Meta,
  type NetworkGeometry,
  type ParallelSegment,
  type Hole,
  type RouteSummary,
  type ScenarioOp,
  type StopFeature,
  type StopsCollection,
  type WalkZone,
  type Weekday,
} from './api'
import { buildNetwork, type NetworkIndex } from './network'
import { latLngToCell } from 'h3-js'
import { MapView, type ContextTarget, type HexHover } from './MapView'
import { hexScaleOf } from './hexLayer'
import { AttentionPanel } from './AttentionPanel'
import { HoleCard } from './HoleCard'
import { optionKey } from './OptionsBlock'
import { useHoleOptions, useRouteOptions } from './useOptions'
import type { Attention, ExtensionOption } from './api'
import { ContextMenu, type MenuItem } from './ContextMenu'
import type { Selection } from './mapLayers'
import { readUrl, writeUrl } from './urlState'
import { RouteCard } from './RouteCard'
import { StopCard } from './StopCard'
import { CompareCard } from './CompareCard'
import { ScheduleCard } from './ScheduleCard'
import { InsertPicker } from './InsertPicker'
import { HistoryPanel } from './HistoryPanel'
import { MetricsPanel } from './MetricsPanel'
import { AssistantPanel, type AssistantTurn } from './AssistantPanel'
import { RouteList } from './RouteList'
import { Dock, type Mode, type Tool } from './Dock'
import { CommandBar } from './CommandBar'
import { FIRST_HOUR, LAST_HOUR } from './TimeScale'
import { useRouteData } from './routeData'
import { useScenarioResult } from './useScenarioResult'
import { distanceM, type LngLat } from './geo'
import { computeBuses } from './buses'
import type { WarningPoint } from './overlayLayer'
import { clockAfter, int, minutes } from './format'
import {
  applyOps,
  describe,
  EMPTY_SCENARIO,
  ops as opsOf,
  push,
  redo,
  rollbackTo,
  scheduleOverride,
  setNet,
  undo,
  type ScenarioState,
} from './scenario'
import type { HandlePoint } from './editLayer'

interface Bootstrapped {
  meta: Meta
  stops: StopsCollection
  routes: RouteSummary[]
  network: NetworkIndex
  baseline: Baseline
  holes: Hole[]
  /** У скольких направлений трасса разорвана (§ швы в геометрии OSM). */
  directionsWithGaps: number
}

type BootState =
  | { phase: 'loading'; step: string }
  | { phase: 'failed'; error: string }
  | { phase: 'ready'; data: Bootstrapped }

/** §8.4 — радиус, в котором предлагаем существующие остановки для вставки. */
const INSERT_RADIUS_M = 200

async function bootstrap(setStep: (s: string) => void, hour: number): Promise<Bootstrapped> {
  setStep('расчётное ядро')
  const meta = await api.meta()

  setStep('остановки, маршруты, трассы')
  const [stops, routes, geometry, parallel, baseline, holes] = await Promise.all([
    api.stops(),
    api.routes(),
    api.networkGeometry(),
    api.parallelSegments(1),
    api.baseline('fri', hour),
    api.holes(500),
  ])

  setStep('разведение параллельных линий')
  const network = buildNetwork(
    (geometry as NetworkGeometry).features,
    (parallel.segments as ParallelSegment[]) ?? [],
    stops,
  )
  return {
    meta,
    stops,
    routes: routes.routes,
    network,
    baseline,
    holes: holes.holes,
    directionsWithGaps: geometry.directions_with_gaps,
  }
}

export function App(): React.JSX.Element {
  const initial = useRef(readUrl()).current
  const [boot, setBoot] = useState<BootState>({ phase: 'loading', step: 'соединение' })
  const [selection, setSelection] = useState<Selection | null>(
    initial.routeNum ? { routeNum: initial.routeNum, direction: initial.direction } : null,
  )
  const [selectedStop, setSelectedStop] = useState<string | null>(null)
  /** §10, клавиши 1…4 — что показывает экран. */
  const [mode, setMode] = useState<Mode>('scenario')
  const [compare, setCompare] = useState<Selection | null>(null)
  const [basemapMissing, setBasemapMissing] = useState(false)
  const [hour, setHour] = useState(initial.hour)
  const [weekday, setWeekday] = useState<Weekday>(initial.weekday)
  const [scenario, setScenario] = useState<ScenarioState>(EMPTY_SCENARIO)
  /**
   * Инструмент карты. «Вставка» — тот же режим правки: у карты нет отдельного
   * состояния под вставку, остановка встаёт кликом по линии. Разными их держит
   * только подсказка, зато искать команду больше не нужно.
   */
  const [tool, setTool] = useState<Tool>('select')
  const editing = tool !== 'select'
  const setEditing = useCallback((next: boolean | ((v: boolean) => boolean)) => {
    setTool((current) => {
      const want = typeof next === 'function' ? next(current !== 'select') : next
      if (!want) return 'select'
      return current === 'select' ? 'edit' : current
    })
  }, [])
  const [pickedSeq, setPickedSeq] = useState<number | null>(null)
  const [extendPreview, setExtendPreview] = useState<string | null>(null)
  const [insertRequest, setInsertRequest] = useState<{ afterSeq: number; at: LngLat } | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const [showSchedule, setShowSchedule] = useState(false)
  const [showHexes, setShowHexes] = useState(false)
  const [showHoles, setShowHoles] = useState(false)
  const [hexHover, setHexHover] = useState<HexHover | null>(null)
  const [menu, setMenu] = useState<ContextTarget | null>(null)
  const [walkZone, setWalkZone] = useState<WalkZone | null>(null)
  const [zoneLoading, setZoneLoading] = useState(false)
  const [warnHover, setWarnHover] = useState<{ messages: string[]; x: number; y: number } | null>(null)
  const [busHover, setBusHover] = useState<{ index: number; total: number; arrives: number; x: number; y: number } | null>(null)
  const [nlBusy, setNlBusy] = useState(false)
  const [understood, setUnderstood] = useState<string | null>(null)
  const [explanation, setExplanation] = useState<string | null>(null)
  const [explaining, setExplaining] = useState(false)
  /** Показатели покрытия за выбранный день: подменяют загруженные при старте. */
  const [baselineNow, setBaselineNow] = useState<Baseline | null>(null)
  /** Фактические интервалы всех маршрутов за текущий час — для списка слева. */
  const [headways, setHeadways] = useState<Record<string, HourHeadway> | null>(null)
  const [askLog, setAskLog] = useState<AssistantTurn[]>([])
  const [asking, setAsking] = useState(false)
  const [applied, setApplied] = useState<Set<string>>(() => new Set())
  /** Диагностика: грузится при старте, чтобы список был виден сразу. */
  const [attention, setAttention] = useState<Attention | null>(null)
  const [attentionLoading, setAttentionLoading] = useState(true)
  const [attentionError, setAttentionError] = useState<string | null>(null)
  const [attentionOpen, setAttentionOpen] = useState(true)
  /** Выбранная ячейка слоя населения: её карточка живёт в правой колонке. */
  const [hexCell, setHexCell] = useState<BaselineHex | null>(null)
  /** Уже применённые варианты продления: кнопка не должна звать дважды. */
  const [appliedOptions, setAppliedOptions] = useState<Set<string>>(() => new Set())
  const [routesOpen, setRoutesOpen] = useState(false)
  const [metricsOpen, setMetricsOpen] = useState(true)
  const [assistantOpen, setAssistantOpen] = useState(false)
  /** Режим демонстрации: панели убраны, карта на весь экран. */
  const [demo, setDemo] = useState(false)
  /** Инструкция к правке: висит до первого действия, потом уходит. */
  const [hintDone, setHintDone] = useState(false)
  const mapRef = useRef<MlMap | null>(null)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const data = await bootstrap((step) => {
          if (!cancelled) setBoot({ phase: 'loading', step })
        }, initial.hour)
        if (!cancelled) setBoot({ phase: 'ready', data })
      } catch (err) {
        if (!cancelled) setBoot({ phase: 'failed', error: err instanceof Error ? err.message : String(err) })
      }
    })()
    return () => {
      cancelled = true
    }
  }, [initial.hour])

  useEffect(() => {
    writeUrl({
      routeNum: selection?.routeNum ?? null,
      direction: selection?.direction ?? 'fwd',
      hour,
      weekday,
    })
  }, [selection, hour, weekday])

  // Тяжёлый слой покрытия загружен при старте; день недели меняют редко,
  // и тогда его приходится перезапрашивать — прежние числа держатся на месте,
  // пока не придут новые.
  useEffect(() => {
    if (weekday === initial.weekday && baselineNow === null) return
    let cancelled = false
    api
      .baseline(weekday, hour)
      .then((b) => {
        if (!cancelled) setBaselineNow(b)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
    // час меняется часто, а слой от него почти не зависит — следим за днём
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [weekday, initial.weekday])

  // Диагностика без модели: список проблемных маршрутов должен быть на экране
  // сразу после загрузки, а не после вопроса ассистенту.
  useEffect(() => {
    let cancelled = false
    setAttentionLoading(true)
    api
      .attention(weekday, hour)
      .then((a) => {
        if (!cancelled) {
          setAttention(a)
          setAttentionError(null)
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setAttentionError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setAttentionLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [weekday, hour])

  // Фактические интервалы — маленький ответ на 169 маршрутов, его можно
  // тянуть при каждом сдвиге часа: без него в списке слева нечего показывать.
  useEffect(() => {
    let cancelled = false
    api
      .headways(weekday, hour)
      .then((h) => {
        if (!cancelled) setHeadways(h.routes)
      })
      .catch(() => {
        if (!cancelled) setHeadways(null)
      })
    return () => {
      cancelled = true
    }
  }, [weekday, hour])

  const data = boot.phase === 'ready' ? boot.data : null
  const baseline = baselineNow ?? data?.baseline ?? null
  /** Границы шкалы плотности — те же, по которым красится слой. */
  const hexScale = useMemo(() => hexScaleOf(baseline?.hexes ?? []), [baseline])
  const routeData = useRouteData(selection?.routeNum ?? null, selection?.direction ?? 'fwd', weekday)
  const compareData = useRouteData(compare?.routeNum ?? null, compare?.direction ?? 'fwd', weekday)
  const routeOptions = useRouteOptions(selection?.routeNum ?? null, weekday, hour)
  const holeOptions = useHoleOptions(hexCell?.h3 ?? null, weekday, hour)

  const stopById = useMemo(() => {
    const map = new Map<string, StopFeature>()
    for (const f of data?.stops.features ?? []) map.set(f.properties.stop_id, f)
    return map
  }, [data])

  const stopName = useCallback(
    (id: string) => stopById.get(id)?.properties.name ?? id,
    [stopById],
  )

  const committedOps = useMemo(() => opsOf(scenario), [scenario])

  /** Предпросмотр во время перетаскивания идёт поверх принятых правок. */
  const effectiveOps = useMemo<ScenarioOp[]>(() => {
    if (!extendPreview || !selection) return committedOps
    return [
      ...committedOps,
      {
        type: 'extend_route',
        route_num: selection.routeNum,
        direction: selection.direction,
        stops: [extendPreview],
      },
    ]
  }, [committedOps, extendPreview, selection])

  const computation = useScenarioResult(weekday, hour, effectiveOps)

  // чистое изменение прилипает к последней правке, но только когда
  // посчитано именно принятое состояние, а не предпросмотр под курсором
  useEffect(() => {
    if (extendPreview || !computation.result) return
    setScenario((s) => {
      const last = s.entries[s.entries.length - 1]
      if (!last || last.net !== null) return s
      return setNet(s, computation.result!.net)
    })
  }, [computation.result, extendPreview])

  const baseChain = useMemo(
    () => (routeData.detail?.stops ?? []).map((s) => s.stop_id),
    [routeData.detail],
  )

  const chainIds = useMemo(() => {
    if (!selection) return []
    return applyOps(baseChain, effectiveOps, selection.routeNum, selection.direction)
  }, [baseChain, effectiveOps, selection])

  const chain = useMemo<HandlePoint[]>(() => {
    return chainIds
      .map((stopId, index) => {
        const feature = stopById.get(stopId)
        if (!feature) return null
        return {
          stopId,
          seq: index,
          tail: index === chainIds.length - 1,
          head: index === 0,
          coord: feature.geometry.coordinates,
        }
      })
      .filter((p): p is HandlePoint => p !== null)
  }, [chainIds, stopById])

  /** Цепочка для карточки: имена и пометка «добавлена сценарием». */
  const chainView = useMemo(() => {
    const inBase = new Set(baseChain)
    return chainIds.map((stopId, index) => ({
      stopId,
      name: stopName(stopId),
      seq: index,
      added: !inBase.has(stopId),
    }))
  }, [chainIds, baseChain, stopName])

  const editedRoute = useMemo(() => {
    if (!selection) return false
    return effectiveOps.some(
      (op) => op.type !== 'set_schedule' && op.route_num === selection.routeNum && op.direction === selection.direction,
    )
  }, [effectiveOps, selection])

  /** §5 — борта считаются по фактическому интервалу и времени хода за этот час. */
  const buses = useMemo(() => {
    const dwell = data?.meta.constants.dwell_sec ?? 20
    return computeBuses(routeData.detail, hour, dwell)
  }, [routeData.detail, hour, data])

  const changedGeometry = useMemo<LngLat[] | null>(() => {
    // §10, режим 1 — базовая сеть: правки временно не рисуются
    if (mode === 'base') return null
    if (!selection || !computation.result) return null
    const key = `${selection.routeNum}:${selection.direction}`
    return (computation.result.new_geometry[key]?.coordinates as LngLat[] | undefined) ?? null
  }, [computation.result, selection, mode])

  /** Трасса выбранного маршрута под щитки: с правками, если они есть. */
  const selectedLine = useMemo<LngLat[]>(() => {
    if (changedGeometry) return changedGeometry
    return (routeData.detail?.geometry?.coordinates as LngLat[] | undefined) ?? []
  }, [changedGeometry, routeData.detail])

  /**
   * §9 — метки правил на карте. Место есть только у части правил: одни
   * привязаны к остановке, другие к перегону. Правила про маршрут целиком
   * (не хватает машин, рейс за режимом парка) остаются в карточке — ставить
   * их на карту некуда, и врать координатой нельзя.
   */
  const warningPoints = useMemo<WarningPoint[]>(() => {
    const all = [...(routeData.detail?.warnings ?? []), ...(computation.result?.warnings ?? [])]
    const rank: Record<string, number> = { error: 0, warning: 1, info: 2 }
    const byPlace = new Map<string, WarningPoint>()

    for (const w of all) {
      let coord: LngLat | null = null
      if (w.stop_id) {
        coord = stopById.get(w.stop_id)?.geometry.coordinates ?? null
      } else if (w.segment_key) {
        const [a, b] = w.segment_key.split('|')
        const pa = stopById.get(a)?.geometry.coordinates
        const pb = stopById.get(b)?.geometry.coordinates
        if (pa && pb) coord = [(pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2]
      }
      if (!coord) continue

      const key = `${coord[0].toFixed(6)}:${coord[1].toFixed(6)}`
      const existing = byPlace.get(key)
      if (existing) {
        if (!existing.messages.includes(w.message)) existing.messages.push(w.message)
        if ((rank[w.severity] ?? 3) < (rank[existing.severity] ?? 3)) existing.severity = w.severity
      } else {
        byPlace.set(key, { id: byPlace.size + 1, coord, severity: w.severity, messages: [w.message] })
      }
    }
    return [...byPlace.values()]
  }, [routeData.detail, computation.result, stopById])

  /** §4.1 — конечные выбранного маршрута: первая и последняя в цепочке. */
  const terminals = useMemo<LngLat[]>(() => {
    if (chain.length < 2) return []
    return [chain[0].coord, chain[chain.length - 1].coord]
  }, [chain])

  const selectedGaps = useMemo<number[]>(
    () => (changedGeometry ? [] : (routeData.detail?.geometry_gap_indices ?? [])),
    [changedGeometry, routeData.detail],
  )

  const tailIsStraight = useMemo(() => {
    if (!selection || !computation.result) return false
    return computation.result.new_geometry[`${selection.routeNum}:${selection.direction}`]?.tail_is_straight_line ?? false
  }, [computation.result, selection])

  const commit = useCallback(
    (op: ScenarioOp) => {
      // первое действие сделано — инструкция больше не нужна
      setHintDone(true)
      setScenario((s) => push(s, { op, label: describe(op, stopName), net: null }))
    },
    [stopName],
  )

  // Каждый вход в правку показывает инструкцию заново: набор действий у
  // «править» и «вставить» разный, и человек редактирует не каждый день.
  // Заодно снимается список вставки: он принадлежит инструменту вставки и
  // после переключения висел над картой, перехватывая клики.
  useEffect(() => {
    if (tool !== 'select') setHintDone(false)
    setInsertRequest(null)
  }, [tool])

  const onSelectRoute = useCallback(
    (routeNum: string, direction: Direction) => {
      setSelectedStop(null)
      setHexCell(null)
      setEditing(false)
      setPickedSeq(null)
      setShowSchedule(false)

      // §3.1 — в режиме сравнения первый клик задаёт основной маршрут,
      // следующий кладёт второй; повторный клик по нему его снимает.
      // Вызывать setCompare внутри апдейтера setSelection нельзя: апдейтер
      // обязан быть чистым, и в StrictMode он выполняется дважды.
      const sameAsMain =
        selection?.routeNum === routeNum && selection?.direction === direction
      if (mode === 'compare' && selection && !sameAsMain) {
        setCompare((prev) =>
          prev && prev.routeNum === routeNum && prev.direction === direction
            ? null
            : { routeNum, direction },
        )
        return
      }
      setSelection({ routeNum, direction })
    },
    [mode, selection],
  )

  const onClearSelection = useCallback(() => {
    setWalkZone(null)
    setCompare(null)
    setSelection(null)
    setSelectedStop(null)
    setHexCell(null)
    setEditing(false)
    setPickedSeq(null)
    setShowSchedule(false)
  }, [])

  const onExtend = useCallback(
    (stopId: string) => {
      if (!selection) return
      setExtendPreview(null)
      commit({
        type: 'extend_route',
        route_num: selection.routeNum,
        direction: selection.direction,
        stops: [stopId],
      })
    },
    [commit, selection],
  )

  const onTrim = useCallback(
    (seq: number) => {
      if (!selection || seq >= chainIds.length - 1) return
      commit({
        type: 'trim_route',
        route_num: selection.routeNum,
        direction: selection.direction,
        until_seq: seq,
      })
      setPickedSeq(null)
    },
    [chainIds.length, commit, selection],
  )

  const onRemove = useCallback(() => {
    if (!selection || pickedSeq === null) return
    commit({
      type: 'remove_stop',
      route_num: selection.routeNum,
      direction: selection.direction,
      seq: pickedSeq,
    })
    setPickedSeq(null)
  }, [commit, pickedSeq, selection])

  const insertCandidates = useMemo(() => {
    if (!insertRequest || !data) return []
    const inChain = new Set(chainIds)
    const out: { stop: StopFeature; distance: number }[] = []
    for (const f of data.stops.features) {
      if (inChain.has(f.properties.stop_id)) continue
      const d = distanceM(insertRequest.at, f.geometry.coordinates)
      if (d <= INSERT_RADIUS_M) out.push({ stop: f, distance: d })
    }
    return out.sort((a, b) => a.distance - b.distance).slice(0, 8)
  }, [insertRequest, data, chainIds])

  const insertScreen = useMemo(() => {
    if (!insertRequest || !mapRef.current) return { x: 100, y: 100 }
    const p = mapRef.current.project(insertRequest.at)
    return { x: p.x, y: p.y }
  }, [insertRequest])

  const routesByNum = useMemo(() => {
    const map = new Map<string, RouteSummary>()
    for (const r of data?.routes ?? []) map.set(r.route_num, r)
    return map
  }, [data])

  /** §14 — полёт камеры к объекту из поиска: 600 мс, ease-in-out. */
  const flyTo = useCallback((center: LngLat, zoom: number) => {
    const map = mapRef.current
    if (!map) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    map.flyTo({ center, zoom, duration: reduced ? 0 : 600, essential: true })
  }, [])

  const onPickRoute = useCallback(
    (routeNum: string) => {
      const map = mapRef.current
      const summary = routesByNum.get(routeNum)
      const direction: Direction = summary?.directions[0] ?? 'fwd'
      onSelectRoute(routeNum, direction)
      if (!map || !data) return
      const ids = data.network.byRoute.get(`${routeNum}:${direction}`)
      if (!ids?.length) return
      const byId = new Map(data.network.collection.features.map((f) => [f.id, f]))
      let west = 180
      let south = 90
      let east = -180
      let north = -90
      for (const id of ids) {
        for (const [lon, lat] of byId.get(id)?.geometry.coordinates ?? []) {
          if (lon < west) west = lon
          if (lon > east) east = lon
          if (lat < south) south = lat
          if (lat > north) north = lat
        }
      }
      if (west > east) return
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      map.fitBounds(
        [
          [west, south],
          [east, north],
        ],
        { padding: { top: 60, bottom: 100, left: 360, right: 60 }, duration: reduced ? 0 : 600 },
      )
    },
    [data, onSelectRoute, routesByNum],
  )

  /**
   * Вариант продления из перебора. Ядро ничего не применяет: сюда приходит
   * готовый сценарий, и он ложится в историю правок обычной операцией —
   * дальше его можно отменить, дополнить или откатить, как ручную.
   */
  const onApplyOption = useCallback(
    (option: ExtensionOption) => {
      onSelectRoute(option.route_num, option.direction)
      for (const op of option.scenario.ops) commit(op)
      setAppliedOptions((s) => new Set(s).add(optionKey(option)))
    },
    [commit, onSelectRoute],
  )

  /**
   * Клик по слою населения. Ячейка считается по координате, а не берётся из
   * отрисованных слоёв: у непокрытых ячеек нет заливки, и клик в середину
   * дыры не попадал бы ни во что. Разрешение сетки берём у ядра, а не пишем
   * восьмёрку руками.
   */
  const onHexClick = useCallback(
    (at: LngLat) => {
      if (!data || !baseline) return
      const cell = latLngToCell(at[1], at[0], data.meta.constants.h3_resolution)
      const hex = baseline.hexes.find((h) => h.h3 === cell)
      if (!hex) {
        // за пределами слоя населения клик по пустому месту снимает выбор
        onClearSelection()
        return
      }
      setSelectedStop(null)
      setWalkZone(null)
      setHexCell(hex)
    },
    [baseline, data, onClearSelection],
  )

  const onPickStop = useCallback(
    (stopId: string, at: LngLat | null) => {
      // зона относится к конкретной остановке: сменили остановку — сняли зону
      setWalkZone(null)
      setHexCell(null)
      setSelectedStop(stopId)
      const coord = at ?? stopById.get(stopId)?.geometry.coordinates ?? null
      if (coord) flyTo(coord, 15.5)
    },
    [flyTo, stopById],
  )

  /** §12 — зона пешей доступности: ядро обходит пешеходную сеть от остановки. */
  const onShowZone = useCallback(() => {
    if (!selectedStop) return
    setZoneLoading(true)
    api
      .walkZone(selectedStop)
      .then(setWalkZone)
      .catch(() => setWalkZone(null))
      .finally(() => setZoneLoading(false))
  }, [selectedStop])

  /** Фраза на естественном языке: ядро разбирает её в операции сценария. */
  const onPhrase = useCallback(
    (text: string) => {
      setNlBusy(true)
      setUnderstood(null)
      api
        .nlScenario(text, weekday, hour)
        .then((res) => {
          setUnderstood(res.understood || 'ядро не поняло фразу')
          const ops = res.scenario?.ops ?? []
          if (res.scenario && Number.isInteger(res.scenario.hour)) setHour(res.scenario.hour)
          for (const op of ops) {
            if (op.type !== 'set_schedule') onSelectRoute(op.route_num, op.direction)
            commit(op)
          }
        })
        .catch((err: unknown) => {
          setUnderstood(`ядро не разобрало фразу: ${err instanceof Error ? err.message : String(err)}`)
        })
        .finally(() => setNlBusy(false))
    },
    [commit, hour, weekday, onSelectRoute],
  )

  /**
   * Ассистент. Вид меняем сразу — выбор маршрута, камера, подсветка дыр
   * ничего не портят. Сценарий не применяем: он ждёт кнопки в панели.
   */
  const runAction = useCallback(
    (action: AssistantAction, key: string) => {
      if (action.type === 'select_route') {
        onSelectRoute(action.route_num, action.direction ?? 'fwd')
      } else if (action.type === 'focus_map') {
        flyTo([action.lon, action.lat], 14.5)
      } else if (action.type === 'highlight_holes') {
        setShowHexes(true)
        setShowHoles(true)
      } else if (action.type === 'apply_scenario') {
        if (Number.isInteger(action.scenario.hour)) setHour(action.scenario.hour)
        for (const op of action.scenario.ops) {
          if (op.type !== 'set_schedule') onSelectRoute(op.route_num, op.direction)
          commit(op)
        }
        setApplied((s) => new Set(s).add(key))
      }
    },
    [commit, flyTo, onSelectRoute],
  )

  const onAsk = useCallback(
    (question: string) => {
      const id = Date.now()
      setAskLog((log) => [...log, { id, question, answer: null, error: null }])
      setAsking(true)
      api
        .assistant(question, weekday, hour)
        .then((answer) => {
          setAskLog((log) => log.map((t) => (t.id === id ? { ...t, answer } : t)))
          for (const action of answer.actions ?? []) {
            if (action.type !== 'apply_scenario') runAction(action, `${id}:auto`)
          }
        })
        .catch((err: unknown) =>
          setAskLog((log) =>
            log.map((t) =>
              t.id === id ? { ...t, error: err instanceof Error ? err.message : String(err) } : t,
            ),
          ),
        )
        .finally(() => setAsking(false))
    },
    [hour, runAction, weekday],
  )

  /** §13 — объяснение результата словами, по кнопке в плашке. */
  const onExplain = useCallback(() => {
    if (!computation.result) return
    setExplaining(true)
    api
      .explain(computation.result)
      .then((res) => setExplanation(res.text))
      .catch((err: unknown) =>
        setExplanation(`ядро не собрало объяснение: ${err instanceof Error ? err.message : String(err)}`),
      )
      .finally(() => setExplaining(false))
  }, [computation.result])

  // результат изменился — старое объяснение больше не про него
  useEffect(() => setExplanation(null), [computation.result])

  const onReady = useCallback((map: MlMap) => {
    mapRef.current = map
    if (import.meta.env.DEV) (window as unknown as { __map: MlMap }).__map = map
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement
      if (typing) return

      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        setScenario((s) => (e.shiftKey ? redo(s) : undo(s)))
        return
      }
      if (e.key === 'Escape') {
        if (menu) setMenu(null)
        else if (insertRequest) setInsertRequest(null)
        else if (editing) setEditing(false)
        else if (selectedStop) setSelectedStop(null)
        else onClearSelection()
      } else if (e.key === 'ArrowLeft') setHour((h) => Math.max(FIRST_HOUR, h - 1))
      else if (e.key === 'ArrowRight') setHour((h) => Math.min(LAST_HOUR, h + 1))
      else if (e.key === 'Delete' || e.key === 'Backspace') onRemove()
      else if (e.key.toLowerCase() === 'e' || e.key === 'у') {
        if (selection) setEditing((v) => !v)
      } else if (e.key.toLowerCase() === 'h' || e.key === 'р') setShowHexes((v) => !v)
      else if (e.key.toLowerCase() === 'd' || e.key === 'в') setShowHoles((v) => !v)
      else if (e.key === '1') setMode('base')
      else if (e.key === '2') setMode('scenario')
      else if (e.key === '3') setMode('compare')
      else if (e.key === '4') {
        setMode('holes')
        setShowHexes(true)
        setShowHoles(true)
      } else if (e.key.toLowerCase() === 'f' || e.key === 'а') setDemo((v) => !v)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [editing, insertRequest, menu, onClearSelection, onRemove, selectedStop, selection])

  const routesByStop = useMemo(() => {
    const map = new Map<string, { routeNum: string; direction: Direction }[]>()
    if (!data) return map
    for (const [key, stopIds] of data.network.stopsByRoute) {
      const [routeNum, direction] = key.split(':') as [string, Direction]
      for (const stopId of stopIds) {
        const list = map.get(stopId)
        if (list) list.push({ routeNum, direction })
        else map.set(stopId, [{ routeNum, direction }])
      }
    }
    return map
  }, [data])

  const stopProps = selectedStop ? (stopById.get(selectedStop)?.properties ?? null) : null

  /** Ближайшая обслуживаемая остановка к выбранной ячейке — из списка дыр. */
  const nearestForCell = useMemo(() => {
    if (!hexCell || !data) return null
    const hole = data.holes.find((h) => h.h3_id === hexCell.h3)
    if (!hole) return null
    return { name: hole.nearest_stop_name, distanceM: hole.walk_distance_m }
  }, [hexCell, data])

  const serving = useMemo(() => {
    if (!selectedStop) return []
    return (routesByStop.get(selectedStop) ?? [])
      .map(({ routeNum, direction }) => {
        const route = routesByNum.get(routeNum)
        return route ? { route, direction } : null
      })
      .filter((x): x is { route: RouteSummary; direction: Direction } => x !== null)
      .sort((a, b) => a.route.route_num.localeCompare(b.route.route_num, 'ru', { numeric: true }))
  }, [selectedStop, routesByStop, routesByNum])

  const override = selection ? scheduleOverride(committedOps, selection.routeNum) : null

  /** §8.3 — меню правой кнопки: только те действия, что реально доступны здесь. */
  const menuItems = useMemo<MenuItem[]>(() => {
    if (!menu) return []
    const items: MenuItem[] = []
    const seqInChain = menu.stopId ? chainIds.indexOf(menu.stopId) : -1

    if (menu.stopId) {
      items.push({
        key: 'stop',
        label: `остановка ${stopName(menu.stopId)}`,
        run: () => setSelectedStop(menu.stopId),
      })
    }
    if (selection && seqInChain >= 0 && seqInChain < chainIds.length - 1) {
      items.push({
        key: 'trim',
        label: 'обрезать маршрут здесь',
        hint: 'Shift',
        run: () => {
          setEditing(true)
          onTrim(seqInChain)
        },
      })
    }
    if (selection && seqInChain >= 0) {
      items.push({
        key: 'remove',
        label: 'убрать остановку из маршрута',
        hint: 'Delete',
        run: () => {
          if (!selection) return
          commit({
            type: 'remove_stop',
            route_num: selection.routeNum,
            direction: selection.direction,
            seq: seqInChain,
          })
        },
      })
    }
    if (menu.routeNum) {
      const isCurrent = selection?.routeNum === menu.routeNum
      items.push({
        key: 'route',
        label: isCurrent ? `маршрут ${menu.routeNum} · выбран` : `выбрать маршрут ${menu.routeNum}`,
        disabled: isCurrent,
        run: () => onSelectRoute(menu.routeNum!, menu.direction ?? 'fwd'),
      })
      if (isCurrent) {
        items.push({
          key: 'edit',
          label: editing ? 'закончить правку' : 'редактировать маршрут',
          hint: 'E',
          run: () => setEditing((v) => !v),
        })
        items.push({
          key: 'schedule',
          label: 'расписание',
          disabled: !routeData.schedule?.available,
          run: () => setShowSchedule(true),
        })
      }
    }
    if (!menu.stopId && !menu.routeNum) {
      if (selection || selectedStop) {
        items.push({ key: 'clear', label: 'снять выбор', hint: 'Esc', run: onClearSelection })
      }
      items.push({
        key: 'hex',
        label: showHexes ? 'скрыть население' : 'показать население',
        hint: 'H',
        run: () => setShowHexes((v) => !v),
      })
      items.push({
        key: 'holes',
        label: showHoles ? 'скрыть дыры покрытия' : 'подсветить дыры покрытия',
        hint: 'D',
        run: () => setShowHoles((v) => !v),
      })
    }
    return items
  }, [
    chainIds,
    commit,
    editing,
    menu,
    onClearSelection,
    onSelectRoute,
    onTrim,
    routeData.schedule,
    selectedStop,
    selection,
    showHexes,
    showHoles,
    stopName,
  ])

  return (
    <>
      {data && (
        <MapView
          network={data.network}
          stops={data.stops}
          selection={selection}
          selectedStop={selectedStop}
          tool={tool}
          chain={chain}
          pickedSeq={pickedSeq}
          changedGeometry={changedGeometry}
          hexes={(baselineNow ?? data.baseline).hexes}
          holes={data.holes}
          showHexes={showHexes}
          showHoles={showHoles}
          scenarioResult={mode === 'base' ? null : computation.result}
          compare={compare}
          onHexHover={setHexHover}
          onHexClick={onHexClick}
          buses={buses.buses}
          selectedLine={selectedLine}
          selectedGaps={selectedGaps}
          walkZone={walkZone}
          terminals={terminals}
          warnings={warningPoints}
          onWarnHover={setWarnHover}
          onBusHover={setBusHover}
          onSelectRoute={onSelectRoute}
          onSelectStop={(stopId) => {
            setWalkZone(null)
            setSelectedStop(stopId)
          }}
          onClearSelection={onClearSelection}
          onExtend={onExtend}
          onExtendPreview={setExtendPreview}
          onTrim={onTrim}
          onPickHandle={setPickedSeq}
          onInsertAt={(afterSeq, at) => setInsertRequest({ afterSeq, at })}
          onContextMenu={setMenu}
          onBasemapMissing={() => setBasemapMissing(true)}
          onReady={onReady}
        />
      )}

      {basemapMissing && data && <div className="offline-bar">нет подложки — карта работает без неё</div>}

      {boot.phase !== 'ready' && (
        <div className="boot">
          <div className="boot-inner">
            <div className="boot-title">QATNOV</div>
            {boot.phase === 'loading' ? (
              <div className="boot-step">{boot.step}…</div>
            ) : (
              <div className="boot-step" style={{ color: 'var(--removed)' }}>
                нет связи с расчётным ядром: {boot.error}
              </div>
            )}
          </div>
        </div>
      )}

      {data && baseline && (
        <div className="hud">
          {/* Слева сверху — поиск и список маршрутов, внизу колонки сводка
              по данным. Справа — инспектор, показатели и ассистент. Колонки
              не пересекаются и обе кончаются выше дока. */}
          {!demo && (
            <>
          <div className="col-left">
            <CommandBar
              busy={nlBusy}
              understood={understood}
              onPickRoute={onPickRoute}
              onPickStop={onPickStop}
              onPhrase={onPhrase}
            />

            {/* Диагностика ядра, а не ответ модели: список виден сразу и
                работает без сети. */}
            <AttentionPanel
              data={attention}
              loading={attentionLoading}
              error={attentionError}
              selected={selection?.routeNum ?? null}
              open={attentionOpen}
              onToggle={() => setAttentionOpen((v) => !v)}
              onPick={onPickRoute}
            />

            <RouteList
              routes={data.routes}
              headways={headways}
              selected={selection}
              open={routesOpen}
              onToggle={() => setRoutesOpen((v) => !v)}
              onPick={onPickRoute}
            />

            <div className="col-foot">
              {/* Состояние не должно держаться на одном цвете (§9 и правило
                  доступности): рядом с цветом всегда есть слово. */}
              {showHexes && (
                <div className="hex-legend">
                  <div className="hex-legend-row">
                    <span className="sw-ramp">
                      <span className="sw sw-d1" />
                      <span className="sw sw-d2" />
                      <span className="sw sw-d3" />
                    </span>
                    в пешей доступности, цвет — людей в ячейке
                  </div>
                  <div className="hex-legend-scale num">
                    от {int(hexScale.low)} до {int(hexScale.top)} чел.
                  </div>
                  <div className="hex-legend-row">
                    <span className="sw sw-frequent" /> в доступе к частой сети
                  </div>
                  <div className="hex-legend-row">
                    <span className="sw sw-uncovered" /> вне пешей доступности
                  </div>
                </div>
              )}

              <div className="legend">
                <div>
                  <b className="num">{data.routes.length}</b> маршрутов · трасса восстановлена у{' '}
                  <b className="num">{data.network.drawnDirections.size}</b> направлений из{' '}
                  <b className="num">{data.routes.reduce((acc, r) => acc + r.directions.length, 0)}</b>
                </div>
                {data.directionsWithGaps > 0 && (
                  <div className="muted">
                    у <b className="num">{data.directionsWithGaps}</b> из них трасса разорвана —
                    рисуется кусками
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="col-right">
          {/* §12: открыта может быть только одна карточка */}
          {hexCell ? (
            <HoleCard
              h3={hexCell.h3}
              hex={{
                pop: hexCell.pop,
                walkMin: hexCell.walk_min,
                covered: hexCell.covered,
                frequent: hexCell.frequent,
              }}
              nearest={nearestForCell}
              options={holeOptions}
              appliedOptions={appliedOptions}
              onApplyOption={onApplyOption}
              onClose={() => setHexCell(null)}
            />
          ) : stopProps ? (
            <StopCard
              stop={stopProps}
              serving={serving}
              onSelectRoute={onSelectRoute}
              zone={walkZone}
              zoneLoading={zoneLoading}
              onShowZone={onShowZone}
              onHideZone={() => setWalkZone(null)}
              onClose={() => {
                setWalkZone(null)
                setSelectedStop(null)
              }}
            />
          ) : mode === 'compare' && selection && compare ? (
            <CompareCard
              hour={hour}
              left={{
                routeNum: selection.routeNum,
                direction: selection.direction,
                summary: routesByNum.get(selection.routeNum) ?? null,
                data: routeData,
              }}
              right={{
                routeNum: compare.routeNum,
                direction: compare.direction,
                summary: routesByNum.get(compare.routeNum) ?? null,
                data: compareData,
              }}
              onClose={onClearSelection}
              onDropCompare={() => setCompare(null)}
            />
          ) : selection && showSchedule ? (
            <ScheduleCard
              routeNum={selection.routeNum}
              direction={selection.direction}
              weekday={weekday}
              base={routeData.schedule}
              applied={override}
              onApply={(params) =>
                commit({ type: 'set_schedule', route_num: selection.routeNum, ...params })
              }
              onClose={() => setShowSchedule(false)}
            />
          ) : (
            selection && (
              <RouteCard
                summary={routesByNum.get(selection.routeNum) ?? null}
                data={routeData}
                routeNum={selection.routeNum}
                direction={selection.direction}
                hour={hour}
                editing={editing}
                buses={{ count: buses.buses.length, reason: buses.reason }}
                options={routeOptions}
                appliedOptions={appliedOptions}
                onApplyOption={onApplyOption}
                chainView={chainView}
                edited={editedRoute}
                tailIsStraight={tailIsStraight}
                onDirection={(direction) => setSelection({ routeNum: selection.routeNum, direction })}
                onSelectStop={(stopId) => {
                  setWalkZone(null)
                  setSelectedStop(stopId)
                }}
                onEdit={() => setEditing((v) => !v)}
                onSchedule={() => setShowSchedule(true)}
                onClose={onClearSelection}
              />
            )
          )}

            <MetricsPanel
              baseline={baseline}
              computation={computation}
              hasOps={effectiveOps.length > 0}
              routeNum={selection?.routeNum ?? null}
              atHour={routeData.detail?.actual_headway.find((h) => h.hour === hour) ?? null}
              schedule={routeData.schedule}
              explanation={explanation}
              explaining={explaining}
              onExplain={onExplain}
              open={metricsOpen}
              onToggle={() => setMetricsOpen((v) => !v)}
            />

            <AssistantPanel
              open={assistantOpen}
              onToggle={() => setAssistantOpen((v) => !v)}
              busy={asking}
              log={askLog}
              applied={applied}
              onAsk={onAsk}
              onAction={runAction}
            />
          </div>

          {showHistory && (
            <HistoryPanel
              state={scenario}
              onRollback={(index) => setScenario((s) => rollbackTo(s, index))}
              onClose={() => setShowHistory(false)}
            />
          )}

          <Dock
            tool={tool}
            onTool={setTool}
            canEdit={!!selection}
            mode={mode}
            onMode={setMode}
            showHexes={showHexes}
            onHexes={() => setShowHexes((v) => !v)}
            showHoles={showHoles}
            onHoles={() => setShowHoles((v) => !v)}
            undoCount={scenario.entries.length}
            redoCount={scenario.redo.length}
            onUndo={() => setScenario(undo)}
            onRedo={() => setScenario(redo)}
            historyOpen={showHistory}
            onHistory={() => setShowHistory((v) => !v)}
            weekday={weekday}
            onWeekday={setWeekday}
            hour={hour}
            onHour={setHour}
            onDemo={() => setDemo(true)}
            direction={selection?.direction ?? null}
          />
            </>
          )}

          {demo && (
            <button className="demo-restore" onClick={() => setDemo(false)}>
              показать панели <span className="num">F</span>
            </button>
          )}

          {/* Выбор остановки для вставки стоит на карте, а не в колонке:
              он привязан к точке, по которой кликнули. */}
          {insertRequest && (
            <InsertPicker
              at={insertScreen}
              afterSeq={insertRequest.afterSeq}
              candidates={insertCandidates}
              onPick={(stopId) => {
                if (selection) {
                  commit({
                    type: 'insert_stop',
                    route_num: selection.routeNum,
                    direction: selection.direction,
                    stop_id: stopId,
                    after_seq: insertRequest.afterSeq,
                  })
                }
                setInsertRequest(null)
              }}
              onClose={() => setInsertRequest(null)}
            />
          )}

          {warnHover && (
            <div className="hex-tip warn-tip" style={{ left: warnHover.x + 14, top: warnHover.y + 14 }}>
              {warnHover.messages.map((m) => (
                <div key={m}>{m}</div>
              ))}
            </div>
          )}

          {busHover && (
            <div className="hex-tip" style={{ left: busHover.x + 14, top: busHover.y + 14 }}>
              борт <span className="num">{busHover.index}</span> из{' '}
              <span className="num">{busHover.total}</span>
              <span className="hex-tip-state">
                {buses.lastStopName ? `прибытие на ${buses.lastStopName} в ` : 'на конечной в '}
                <span className="num">{clockAfter(hour, busHover.arrives)}</span> · расчёт по интервалу
              </span>
            </div>
          )}

          {hexHover && showHexes && (
            <div className="hex-tip" style={{ left: hexHover.x + 14, top: hexHover.y + 14 }}>
              <span className="num">≈{int(hexHover.pop)}</span> человек, до ближайшей остановки{' '}
              <span className="num">{minutes(hexHover.walkMin)}</span>
              <span className="hex-tip-state">
                {hexHover.frequent
                  ? 'в доступе к частой сети'
                  : hexHover.covered
                    ? 'в пешей доступности'
                    : 'вне пешей доступности'}
              </span>
            </div>
          )}

          {menu && (
            <ContextMenu at={{ x: menu.x, y: menu.y }} items={menuItems} onClose={() => setMenu(null)} />
          )}

          {mode === 'compare' && selection && !compare && (
            <div className="edit-hint">кликни по второму маршруту — он станет оранжевым и встанет рядом в таблицу</div>
          )}

          {/* Инструкция к правке. Висит до первого действия и уходит сама:
              пока человек ничего не сделал, он и не знает, что делать. */}
          {editing && !hintDone && (
            <div className="edit-guide">
              <div className="edit-guide-head">
                {tool === 'insert' ? 'вставка остановки' : 'правка маршрута'}
                <button
                  className="card-close"
                  aria-label="скрыть подсказку"
                  onClick={() => setHintDone(true)}
                >
                  ✕
                </button>
              </div>
              {tool === 'insert' ? (
                <ol className="edit-guide-list">
                  <li>
                    кликните по линии маршрута там, где нужна остановка — ядро предложит те, что
                    рядом
                  </li>
                  <li>
                    <b>Esc</b> — выйти
                  </li>
                </ol>
              ) : (
                <ol className="edit-guide-list">
                  <li>
                    <span className="guide-dot guide-dot-tail" /> конец маршрута — крупная ручка с
                    ореолом: <b>потяните её мышкой</b>, чтобы продлить
                  </li>
                  <li>
                    <span className="guide-dot guide-dot-head" /> начало закрашено и не тянется:
                    ядро продлевает только с конца
                  </li>
                  <li>
                    <b>Shift + клик</b> по любой ручке — обрезать маршрут до неё
                  </li>
                  <li>
                    <b>клик по линии</b> — вставить остановку, <b>Delete</b> — убрать выбранную
                  </li>
                </ol>
              )}
            </div>
          )}
        </div>
      )}
    </>
  )
}
