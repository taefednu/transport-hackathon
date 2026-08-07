/** Клиент расчётного ядра. Типы сняты с живых ответов, не с догадок. */

export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8025'

export type Weekday = 'fri' | 'sat' | 'sun'
export type Direction = 'fwd' | 'bwd'
export type Quality = 'exact' | 'approximate'

export interface Meta {
  constants: {
    walk_limit_m: number
    frequent_headway_min: number
    h3_resolution: number
    walk_speed_kmh: number
    dwell_sec: number
    layover_min: number
  }
  size: { stops: number; hexes: number; stop_hex_pairs: number; walk_graph_nodes: number }
  sources: { name: string; detail: string; license: string }[]
  not_built_yet: string[]
}

export interface StopProps {
  stop_id: string
  name: string
  kind: 'bus' | 'metro' | 'minibus'
  source: 'yandex' | 'osm'
  n_routes: number
}

export interface StopFeature {
  type: 'Feature'
  geometry: { type: 'Point'; coordinates: [number, number] }
  properties: StopProps
}

export interface StopsCollection {
  type: 'FeatureCollection'
  features: StopFeature[]
}

export interface RouteSummary {
  route_num: string
  directions: Direction[]
  name: string
  planned_headway_min: number | null
  length_km: number
  n_stops: number
  quality: Quality
  in_egov: boolean
}

export interface GeometryFeature {
  type: 'Feature'
  geometry: { type: 'LineString'; coordinates: [number, number][] }
  properties: {
    route_num: string
    direction: Direction
    quality: Quality
    /** Номер куска: трасса приходит разрезанной по разрывам. */
    piece: number
    /** Сколько разрывов у этого направления всего. */
    gaps: number
  }
}

export interface NetworkGeometry {
  type: 'FeatureCollection'
  count: number
  simplified: boolean
  tolerance_deg: number | null
  directions_total: number
  /** У скольких направлений трасса разорвана. */
  directions_with_gaps: number
  gap_near_m: number
  gap_far_m: number
  features: GeometryFeature[]
}

/** k — порядковый номер маршрута на перегоне, n — сколько их всего (§3.2). */
export interface ParallelSegment {
  segment_key: string
  route_num: string
  direction: Direction
  seq: number
  k: number
  n: number
}

export interface BaselineHex {
  h3: string
  pop: number
  covered: boolean
  /** Покрыт остановкой, у которой хотя бы один маршрут ходит чаще 15 минут. */
  frequent: boolean
  walk_min: number
  walk_source: string
  nearest_stop_id: string | null
  lat: number
  lon: number
}

export interface Baseline {
  weekday: Weekday
  hour: number
  population_total: number
  pnt500: { people: number; share: number; people_outside: number }
  pnft15: { people: number; share: number }
  pnft15_unavailable_reason: string | null
  t_median_min: number
  served_stops: number
  physical_stops: number
  pnt500_all_physical_stops: number
  hexes: BaselineHex[]
}

export interface Warning {
  code: string
  message: string
  severity: 'info' | 'warning' | 'error'
  route_num?: string
  seq?: number
  stop_id?: string
  /** Перегон «остановка|остановка»: у правил про дублирование место — не точка. */
  segment_key?: string
}

export interface RouteStop {
  seq: number
  stop_id: string
  name: string | null
  lat: number | null
  lon: number | null
  kind: string | null
}

export interface SegmentTime {
  seq_from: number
  seq_to: number
  hour: number
  travel_sec: number
  length_m: number
  traffic_share: number
  source: 'traffic' | 'fallback' | string
}

export interface ActualHeadway {
  hour: number
  actual_headway_min: number | null
  n_vehicles: number | null
  n_boardings: number | null
}

export interface RouteDetail {
  route_num: string
  direction: Direction
  weekday: Weekday
  name: string
  quality: Quality
  planned_headway_min: number | null
  length_km: number
  work_start: string | null
  work_end: string | null
  geometry: { type: 'LineString'; coordinates: [number, number][] } | null
  /** Индексы рёбер-швов: ребро i идёт от точки i к точке i+1. */
  geometry_gap_indices: number[]
  geometry_gaps: number
  stops: RouteStop[]
  segment_times: SegmentTime[]
  actual_headway: ActualHeadway[]
  warnings: Warning[]
}

export interface ScheduleStop {
  seq: number
  stop_id: string
  name: string | null
  arrivals: string[]
}

export interface RouteSchedule {
  available: boolean
  reason?: string | null
  stops: ScheduleStop[]
  trips: number
  first_departure: string
  headway_min: number
  one_way_min: number
  cycle_time_min: number
  required_vehicles: number
  n_vehicles: number | null
  first_arrival_last_stop: string | null
  last_arrival_last_stop: string | null
  route_num: string
  direction: Direction
  weekday: Weekday
  warnings: Warning[]
}

export type ScenarioOp =
  | { type: 'extend_route'; route_num: string; direction: Direction; stops: string[] }
  | { type: 'trim_route'; route_num: string; direction: Direction; until_seq: number }
  | { type: 'insert_stop'; route_num: string; direction: Direction; stop_id: string; after_seq: number }
  | { type: 'remove_stop'; route_num: string; direction: Direction; seq: number }
  | {
      type: 'set_schedule'
      route_num: string
      first_departure?: string | null
      headway_min?: number | null
      n_vehicles?: number | null
    }

export interface ScenarioResult {
  weekday: Weekday
  hour: number
  gained: number
  lost: number
  net: number
  pnt500_before: number
  pnt500_after: number
  pnft15_after: { people: number; share: number }
  t_median_before: number
  t_median_after: number
  changed_hexes: { h3: string; state: 'gained' | 'lost'; pop: number }[]
  /** Цена правки: часть полей появляется только у операций своего вида. */
  affected_routes: {
    route_num: string
    direction?: Direction
    n_stops_before?: number
    n_stops_after?: number
    headway_before?: number
    headway_after?: number
    headway_min?: number
    one_way_before_min?: number
    one_way_after_min?: number
    cycle_time_before?: number
    cycle_time_after?: number
    required_vehicles_before?: number
    required_vehicles_after?: number
    n_vehicles?: number
    /** Сколько перегонов посчитано по медиане скорости города, а не по трафику. */
    segments_at_city_speed?: number
  }[]
  new_geometry: Record<
    string,
    { type: 'LineString'; coordinates: [number, number][]; tail_is_straight_line: boolean }
  >
  warnings: Warning[]
  took_ms: number
}

export interface SearchHit {
  id: string
  title: string
  detail: string
  lat: number | null
  lon: number | null
  match: string
}

export interface SearchResult {
  query: string
  normalized: string
  routes: SearchHit[]
  stops: SearchHit[]
}

export interface NlScenario {
  text: string
  source: string
  llm: { available: boolean; error: string | null }
  intent: Record<string, unknown>
  understood: string
  scenario: { weekday: Weekday; hour: number; ops: ScenarioOp[] } | null
  ambiguous: unknown[]
  unresolved: unknown[]
  took_ms: number
}

export interface Explanation {
  text: string
  source: string
  reason: string | null
  numbers_checked: boolean
  facts: Record<string, unknown>
}

export interface WalkZone {
  stop_id: string
  limit_m: number
  nodes: number
  /** Население, до которого от остановки можно дойти пешком. */
  people: number | null
  /** Рёбра пешеходной сети и расстояние, на котором до них дошли. */
  edges: { coords: [number, number][]; d: number }[]
}

/**
 * Диагностика и подбор. Всё считает ядро без модели: продукт обязан работать
 * без сети и без ключа, ассистент — дополнительный вход, а не единственный.
 */
export interface AttentionRoute {
  route_num: string
  name: string
  score: number
  planned_headway_min: number | null
  actual_headway_min: number | null
  n_vehicles: number | null
  n_boardings: number | null
  length_km: number | null
  n_stops: number | null
  quality: Quality
  fallback_share: number | null
  /** Готовые формулировки признаков: числа в них — из самого признака. */
  reasons: string[]
}

export interface Attention {
  weekday: Weekday
  hour: number
  routes_total: number
  routes_with_signs: number
  /** Сработало только то, что в оценку не идёт: наблюдение, а не претензия. */
  routes_informational_only: number
  routes_shown: number
  /** Маршруты с невозможными исходными значениями: в ранжирование не берутся. */
  excluded_unreliable: { route_num: string; reasons: string[] }[]
  excluded_count: number
  routes: AttentionRoute[]
}

/** Уверенность в том, что цель продления сейчас никем не обслуживается. */
export type OptionConfidence = 'yandex_confirmed' | 'osm_only'

export interface ExtensionOption {
  route_num: string
  direction: Direction
  stop_id: string
  stop_name: string
  confidence: OptionConfidence | null
  lat: number
  lon: number
  tail_km: number
  /** Прирост именно от новой остановки, без пересчёта цепочки. */
  gained_people: number
  chain_recount_people: number
  lost_people: number
  cycle_time_before_min: number
  cycle_time_after_min: number
  required_vehicles_before: number
  required_vehicles_after: number
  extra_vehicles: number
  /** Готово для POST /api/scenario — применяет человек, не ядро. */
  scenario: { weekday: Weekday; hour: number; ops: ScenarioOp[] }
}

export interface RouteOptions {
  route_num: string
  options: ExtensionOption[]
  options_found: number
  candidates_checked: number
  candidates_off_housing: number
  housing_radius_m: number
  min_housing_buildings: number
  max_extra_vehicles: number
  note: string
}

export interface HoleOptions {
  h3: string
  people: number
  covered: boolean
  /** Остановок рядом, про которые известно, что их никто не обслуживает. */
  targets_nearby: number
  routes_checked: number
  options: ExtensionOption[]
  options_found: number
  /** Почему вариантов нет. null — они есть. */
  reason: string | null
  max_extra_vehicles: number
  max_length_share: number
}

/** Фактический интервал за один час: приходит только по маршрутам с рейсами. */
export interface HourHeadway {
  route_num: string
  actual_headway_min: number | null
  n_vehicles: number | null
  n_boardings: number | null
}

export interface Headways {
  weekday: Weekday
  hour: number
  count: number
  routes: Record<string, HourHeadway>
}

/**
 * Действия ассистента. Ядро отдаёт их уже собранными — интерфейс ничего
 * не досчитывает. Сценарий приходит в том виде, который принимает
 * POST /api/scenario, но сам ассистент его не применяет: это делает человек.
 */
export type AssistantAction =
  | { type: 'select_route'; route_num: string; direction?: Direction }
  | { type: 'focus_map'; lat: number; lon: number }
  | { type: 'highlight_holes'; h3: string[] }
  | {
      type: 'apply_scenario'
      label: string
      scenario: { weekday: Weekday; hour: number; ops: ScenarioOp[] }
    }

export interface AssistantAnswer {
  text: string
  source: string
  reason: string | null
  supported: boolean
  actions: AssistantAction[]
  steps?: { tool: string; took_ms: number; error: string | null }[]
  disclaimers?: string[]
}

export interface Hole {
  h3_id: string
  population: number
  lat: number
  lon: number
  nearest_stop_id: string | null
  nearest_stop_name: string | null
  walk_distance_m: number
}

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message)
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(API_BASE + path, init)
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    let detail = body
    try {
      detail = (JSON.parse(body) as { detail?: string }).detail ?? body
    } catch {
      /* тело не json — берём как есть */
    }
    throw new ApiError(res.status, detail || `${res.status} ${res.statusText}`)
  }
  return (await res.json()) as T
}

function post<T>(path: string, body: unknown): Promise<T> {
  return req<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export const api = {
  meta: () => req<Meta>('/api/meta'),
  stops: () => req<StopsCollection>('/api/stops'),
  routes: () => req<{ count: number; routes: RouteSummary[] }>('/api/routes'),
  networkGeometry: () => req<NetworkGeometry>('/api/network/geometry'),
  parallelSegments: (minRoutes = 1) =>
    req<{ count: number; segments: ParallelSegment[] }>(`/api/segments/parallel?min_routes=${minRoutes}`),
  baseline: (weekday: Weekday, hour: number) =>
    req<Baseline>(`/api/baseline?weekday=${weekday}&hour=${hour}`),
  routeDetail: (routeNum: string, direction: Direction, weekday: Weekday) =>
    req<RouteDetail>(
      `/api/routes/${encodeURIComponent(routeNum)}?direction=${direction}&weekday=${weekday}`,
    ),
  routeSchedule: (
    routeNum: string,
    direction: Direction,
    weekday: Weekday,
    params: { first_departure?: string; headway_min?: number; n_vehicles?: number } = {},
  ) => {
    const q = new URLSearchParams({ direction, weekday })
    if (params.first_departure) q.set('first_departure', params.first_departure)
    if (params.headway_min != null) q.set('headway_min', String(params.headway_min))
    if (params.n_vehicles != null) q.set('n_vehicles', String(params.n_vehicles))
    return req<RouteSchedule>(`/api/routes/${encodeURIComponent(routeNum)}/schedule?${q}`)
  },
  scenario: (weekday: Weekday, hour: number, ops: ScenarioOp[], signal?: AbortSignal) =>
    req<ScenarioResult>('/api/scenario', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ weekday, hour, ops }),
      signal,
    }),
  nlScenario: (text: string, weekday: Weekday, hour: number) =>
    post<NlScenario>('/api/nl/scenario', { text, weekday, hour }),
  headways: (weekday: Weekday, hour: number) =>
    req<Headways>(`/api/headways?weekday=${weekday}&hour=${hour}`),
  attention: (weekday: Weekday, hour: number, limit = 12) =>
    req<Attention>(`/api/diagnostics/attention?weekday=${weekday}&hour=${hour}&limit=${limit}`),
  routeOptions: (routeNum: string, weekday: Weekday, hour: number, signal?: AbortSignal) =>
    req<RouteOptions>(
      `/api/routes/${encodeURIComponent(routeNum)}/options?weekday=${weekday}&hour=${hour}`,
      { signal },
    ),
  holeOptions: (h3: string, weekday: Weekday, hour: number, signal?: AbortSignal) =>
    req<HoleOptions>(
      `/api/holes/${encodeURIComponent(h3)}/options?weekday=${weekday}&hour=${hour}`,
      { signal },
    ),
  assistant: (text: string, weekday: Weekday, hour: number) =>
    post<AssistantAnswer>('/api/assistant', { text, weekday, hour }),
  explain: (result: ScenarioResult) => post<Explanation>('/api/explain', result),
  search: (q: string, limit = 10) =>
    req<SearchResult>(`/api/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  walkZone: (stopId: string) => req<WalkZone>(`/api/stops/${encodeURIComponent(stopId)}/walkzone`),
  holes: (limit = 200) => req<{ count: number; people_total: number; holes: Hole[] }>(`/api/holes?limit=${limit}`),
}
