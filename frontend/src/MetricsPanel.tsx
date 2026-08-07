/**
 * Блок показателей (§13). Две группы: что происходит с людьми и что при этом
 * происходит с сетью.
 *
 * Раньше здесь были только люди — и правка расписания не двигала ни одного
 * числа: на покрытие интервал не влияет, влияет на парк. Вторую группу ядро
 * возвращает в `affected_routes`, её и показываем.
 *
 * Изменившееся значение показано как «было → стало». Неизменившееся
 * приглушено, но остаётся на месте: исчезнувшая строка читается как поломка,
 * а одинаковая яркость прячет то единственное, что сдвинулось.
 *
 * «Есть на линии» — единственное число не из расчёта, а из транзакций.
 * Сценарий его не меняет и меняться оно не может: это факт о сегодняшнем дне.
 */

import type { ActualHeadway, Baseline, RouteSchedule } from './api'
import { duration, int, minutes, people, percent, plural, signed } from './format'
import { Panel } from './Panel'
import type { ScenarioComputation } from './useScenarioResult'

const NBSP = ' '

export interface MetricsPanelProps {
  baseline: Baseline
  computation: ScenarioComputation
  hasOps: boolean
  /** Выбранный маршрут: по нему показываем сеть, пока правок нет. */
  routeNum: string | null
  /** Строка фактических интервалов выбранного маршрута за текущий час. */
  atHour: ActualHeadway | null
  schedule: RouteSchedule | null
  explanation: string | null
  explaining: boolean
  onExplain: () => void
  open: boolean
  onToggle: () => void
}

export function MetricsPanel({
  baseline,
  computation,
  hasOps,
  routeNum,
  atHour,
  schedule,
  explanation,
  explaining,
  onExplain,
  open,
  onToggle,
}: MetricsPanelProps): React.JSX.Element {
  const { result, pending, slow, error } = computation
  const warnings = groupWarnings(result?.warnings ?? [])

  // без правок показываем текущее состояние сети, а не нули (§16)
  const walkNow = result ? result.pnt500_after : baseline.pnt500.people
  const walkShare = result ? result.pnt500_after / baseline.population_total : baseline.pnt500.share
  const frequentShare = result ? result.pnft15_after.share : baseline.pnft15.share
  const frequentNow = result ? result.pnft15_after.people : baseline.pnft15.people

  const affected = result?.affected_routes ?? []
  const base = schedule?.available ? schedule : null

  return (
    <Panel
      className={`metrics${pending ? ' is-pending' : ''}`}
      title="показатели"
      aside={pending ? <span className="as-note">пересчёт…</span> : null}
      open={open}
      onToggle={onToggle}
    >
      <>
        <div className="m-group">
          <div className="m-group-head">люди</div>
          <Delta label="получат доступ" value={result ? result.gained : null} kind="gain" />
          <Delta label="потеряют" value={result ? -result.lost : null} kind="loss" />
          <Pair
            label="в пешей доступности"
            before={result ? result.pnt500_before : null}
            after={walkNow}
            format={people}
          />
          <Pair label="в доступе к частой" before={null} after={frequentNow} format={people} />
        </div>

        <div className="m-group">
          <div className="m-group-head">
            сеть
            {routeNum && <span className="shield num">{routeNum}</span>}
          </div>

          {affected.length === 0 ? (
            <NetworkRows
              cycleBefore={null}
              cycleAfter={base?.cycle_time_min ?? null}
              needBefore={null}
              needAfter={base?.required_vehicles ?? null}
              onLine={atHour?.n_vehicles ?? null}
              headwayBefore={null}
              headwayAfter={base?.headway_min ?? null}
              oneWayBefore={null}
              oneWayAfter={base?.one_way_min ?? null}
            />
          ) : (
            affected.map((r) => (
              <div className="m-route" key={`${r.route_num}:${r.direction ?? ''}`}>
                {affected.length > 1 && (
                  <div className="m-group-head">
                    <span className="shield num">{r.route_num}</span>
                    {r.direction && <span>{r.direction === 'fwd' ? 'А → Б' : 'Б → А'}</span>}
                  </div>
                )}
                {r.n_stops_before !== undefined && (
                  <Pair
                    label="остановок"
                    before={r.n_stops_before}
                    after={r.n_stops_after ?? null}
                    format={(v) => int(v)}
                  />
                )}
                <NetworkRows
                  cycleBefore={r.cycle_time_before ?? null}
                  cycleAfter={r.cycle_time_after ?? base?.cycle_time_min ?? null}
                  needBefore={r.required_vehicles_before ?? null}
                  needAfter={r.required_vehicles_after ?? null}
                  onLine={atHour?.n_vehicles ?? r.n_vehicles ?? null}
                  headwayBefore={r.headway_before ?? null}
                  headwayAfter={r.headway_after ?? r.headway_min ?? null}
                  oneWayBefore={r.one_way_before_min ?? null}
                  oneWayAfter={r.one_way_after_min ?? null}
                />
                {!!r.segments_at_city_speed && (
                  <div className="as-note">
                    <span className="num">{r.segments_at_city_speed}</span>{' '}
                    {plural(r.segments_at_city_speed, [
                      'перегон посчитан',
                      'перегона посчитаны',
                      'перегонов посчитаны',
                    ])}{' '}
                    по медиане скорости города, а не по трафику
                  </div>
                )}
              </div>
            ))
          )}
        </div>

        <div className="cons-foot num">
          {hasOps
            ? `${percent(walkShare)} города в пешей доступности · ${percent(frequentShare)} к частой`
            : `база: ${people(baseline.pnt500.people)} из ${int(baseline.population_total)}`}
        </div>

        {/* §9 — система должна ругаться */}
        {warnings.length > 0 && (
          <ul className="cons-warns">
            {warnings.map((w) => (
              <li key={w.code}>
                <span className={`badge badge-${w.severity}`}>!</span>
                {w.message}
              </li>
            ))}
          </ul>
        )}

        {result && (
          <button className="cons-explain" onClick={onExplain} disabled={explaining}>
            {explaining ? 'ядро формулирует…' : explanation ? 'переформулировать' : 'объяснить словами'}
          </button>
        )}
        {explanation && <p className="cons-text">{explanation}</p>}

        {slow && <div className="cons-note">пересчёт занимает дольше обычного</div>}
        {error && <div className="cons-note error">пересчёт не прошёл: {error}</div>}

        <div className="cons-source">данные: 1–3 мая 2026</div>
      </>
    </Panel>
  )
}

/** Пять чисел про сеть — всегда в одном порядке, чтобы взгляд их искал на месте. */
function NetworkRows(props: {
  cycleBefore: number | null
  cycleAfter: number | null
  needBefore: number | null
  needAfter: number | null
  onLine: number | null
  headwayBefore: number | null
  headwayAfter: number | null
  oneWayBefore: number | null
  oneWayAfter: number | null
}): React.JSX.Element {
  return (
    <>
      <Pair label="время оборота" before={props.cycleBefore} after={props.cycleAfter} format={duration} />
      <Pair label="требуется машин" before={props.needBefore} after={props.needAfter} format={(v) => int(v)} />
      <Pair label="есть на линии" before={null} after={props.onLine} format={(v) => int(v)} />
      <Pair label="интервал" before={props.headwayBefore} after={props.headwayAfter} format={(v) => minutes(v)} />
      <Pair label="время в пути" before={props.oneWayBefore} after={props.oneWayAfter} format={duration} />
    </>
  )
}

/** Строка «было → стало». Совпало или нечего сравнивать — одно значение, глухо. */
function Pair({
  label,
  before,
  after,
  format,
}: {
  label: string
  before: number | null
  after: number | null
  format: (value: number) => string
}): React.JSX.Element {
  const changed = before != null && after != null && Math.abs(before - after) > 1e-6
  return (
    <div className={`m-row${changed ? '' : ' is-same'}`}>
      <span className="m-label">{label}</span>
      <span className="m-val num">
        {changed && (
          <>
            <span className="m-was">{format(before)}</span>
            <span className="m-arrow">→</span>
          </>
        )}
        {after == null ? '—' : format(after)}
      </span>
    </div>
  )
}

/** Строка-дельта: у неё нет «было», она сама разница. */
function Delta({
  label,
  value,
  kind,
}: {
  label: string
  value: number | null
  kind: 'gain' | 'loss'
}): React.JSX.Element {
  return (
    <div className={`m-row${value ? '' : ' is-same'}`}>
      <span className="m-label">{label}</span>
      <span className={`m-val num ${value ? kind : ''}`}>
        {value == null ? '—' : `${signed(value)}${NBSP}чел.`}
      </span>
    </div>
  )
}

interface PanelWarning {
  code: string
  message: string
  severity: string
}

/** Не более трёх правил: блок узкий, а список повторов бесполезен. */
function groupWarnings(all: { code: string; message: string; severity: string }[]): PanelWarning[] {
  const byCode = new Map<string, PanelWarning>()
  const rank: Record<string, number> = { error: 0, warning: 1, info: 2 }
  for (const w of all) if (!byCode.has(w.code)) byCode.set(w.code, w)
  return [...byCode.values()]
    .sort((a, b) => (rank[a.severity] ?? 3) - (rank[b.severity] ?? 3))
    .slice(0, 3)
}
