/** Карточка маршрута (§12). Показывает только то, что реально есть в данных. */

import { API_BASE, type Direction, type RouteSummary } from './api'
import { Card, Caveat, Rows } from './Card'
import { duration, hourLabel, km, minutes, plural } from './format'

/** Столько разрывов ядро считает границей «неполной трассы». */
const INCOMPLETE_GEOMETRY_GAPS = 3
import type { RouteData } from './routeData'

export interface RouteCardProps {
  summary: RouteSummary | null
  data: RouteData
  routeNum: string
  direction: Direction
  hour: number
  editing: boolean
  /** Цепочка с учётом правок сценария: её и показываем, а не исходную. */
  chainView: { stopId: string; name: string; seq: number; added: boolean }[]
  /** У маршрута есть правки — значит время прибытия из реестра уже не про него. */
  edited: boolean
  /** Хвост продления нарисован прямой — трассы для него не существует. */
  tailIsStraight: boolean
  /** §5 — сколько бортов нарисовано на карте и почему их может не быть. */
  buses: { count: number; reason: string | null }
  onDirection: (direction: Direction) => void
  onSelectStop: (stopId: string) => void
  onEdit: () => void
  onSchedule: () => void
  onClose: () => void
}

export function RouteCard({
  summary,
  data,
  routeNum,
  direction,
  hour,
  editing,
  chainView,
  edited,
  tailIsStraight,
  buses,
  onDirection,
  onSelectStop,
  onEdit,
  onSchedule,
  onClose,
}: RouteCardProps): React.JSX.Element {
  const { detail, schedule, loading, error } = data
  const bothDirections = (summary?.directions.length ?? 1) > 1
  const atHour = detail?.actual_headway.find((h) => h.hour === hour) ?? null

  // Прибытия выровнены по номеру рейса: берём первый рейс, вышедший в этот час
  // или позже, и дальше идём по одному и тому же рейсу, а не по разным.
  const trip = tripAtHour(schedule?.available ? schedule.stops[0]?.arrivals : undefined, hour)
  const rows = collapseChain(chainView)
  const arrivals = new Map<number, string | undefined>()
  if (schedule?.available && trip !== null) {
    for (const s of schedule.stops) arrivals.set(s.seq, s.arrivals[trip])
  }

  return (
    <Card
      title={
        <>
          <span className="shield num">{routeNum}</span>
          <span className="card-name">{detail?.name ?? summary?.name ?? 'маршрут'}</span>
        </>
      }
      subtitle={
        bothDirections ? (
          <div className="seg">
            <button
              className="seg-btn"
              aria-pressed={direction === 'fwd'}
              onClick={() => onDirection('fwd')}
            >
              А → Б
            </button>
            <button
              className="seg-btn"
              aria-pressed={direction === 'bwd'}
              onClick={() => onDirection('bwd')}
            >
              Б → А
            </button>
          </div>
        ) : (
          <span className="muted">одно направление в данных</span>
        )
      }
      onClose={onClose}
    >
      {error && <Caveat>маршрут не отдался: {error}</Caveat>}

      <Rows
        items={[
          [
            'остановок',
            detail ? chainView.length || detail.stops.length || summary?.n_stops || '—' : '…',
          ],
          ['длина', km(detail?.length_km ?? summary?.length_km)],
          ['плановый интервал', minutes(detail?.planned_headway_min ?? summary?.planned_headway_min)],
          [
            `фактический интервал в ${hourLabel(hour)}:00`,
            atHour?.actual_headway_min != null ? minutes(atHour.actual_headway_min) : '—',
          ],
          [`машин на линии в ${hourLabel(hour)}:00`, atHour?.n_vehicles ?? '—'],
          [`посадок в ${hourLabel(hour)}:00`, atHour?.n_boardings ?? '—'],
          [
            'бортов на карте',
            buses.reason ? '—' : `${buses.count} · расчёт по интервалу`,
          ],
          ['время оборота по реестру', duration(schedule?.available ? schedule.cycle_time_min : null)],
          [
            'режим работы',
            detail?.work_start && detail.work_end ? `${detail.work_start}–${detail.work_end}` : '—',
          ],
        ]}
      />

      {detail && detail.geometry_gaps > INCOMPLETE_GEOMETRY_GAPS && (
        <Caveat>
          трасса неполная: в геометрии <span className="num">{detail.geometry_gaps}</span>{' '}
          {plural(detail.geometry_gaps, ['разрыв', 'разрыва', 'разрывов'])} — маршрут нарисован кусками,
          между ними в OSM нет ни одного пути
        </Caveat>
      )}

      {detail && detail.quality !== 'exact' && (
        <Caveat>
          {detail.stops.length > 0
            ? 'трасса не восстановлена, доступны интервалы и число остановок'
            : 'трасса и порядок остановок не восстановлены; доступны интервалы, длина и режим работы'}
        </Caveat>
      )}
      {schedule && !schedule.available && schedule.reason && <Caveat>{schedule.reason}</Caveat>}

      {rows.length > 0 && (
        <>
          <div className="card-section">
            цепочка остановок
            {!edited && schedule?.available && trip !== null && (
              <span className="muted"> · рейс {schedule.stops[0]?.arrivals[trip]}</span>
            )}
            {edited && <span className="muted"> · с правками</span>}
          </div>
          <ol className="chain">
            {rows.map((s, index) => (
              <li key={`${s.seq}-${s.stopId}`}>
                <button
                  className={`chain-stop${s.added ? ' is-added' : ''}`}
                  onClick={() => onSelectStop(s.stopId)}
                >
                  {/* номер по порядку в списке, а не индекс узла: после
                      схлопывания индексы прыгают и читаются как потеря строк */}
                  <span className="chain-seq num">{index + 1}</span>
                  <span className="chain-name">
                    {s.name}
                    {s.nodes > 1 && (
                      <span className="chain-nodes num" title="узлов остановочного пункта в OSM">
                        ×{s.nodes}
                      </span>
                    )}
                  </span>
                  <span className="chain-time num">{edited ? '' : (arrivals.get(s.seq) ?? '')}</span>
                </button>
              </li>
            ))}
          </ol>
          {edited && (
            <Caveat>
              время прибытия показано по реестру и после правки уже не про этот маршрут: ядро не
              пересчитывает расписание по изменённой цепочке
            </Caveat>
          )}
        </>
      )}

      {loading && <div className="muted">данные маршрута грузятся…</div>}

      {detail && detail.warnings.length > 0 && (
        <>
          <div className="card-section">предупреждения · {detail.warnings.length}</div>
          <ul className="warns">
            {dedupeWarnings(detail.warnings).map((w) => (
              <li key={w.key}>
                <span className={`badge badge-${w.severity}`}>!</span>
                {w.message}
                {w.count > 1 && <span className="muted num"> ×{w.count}</span>}
              </li>
            ))}
          </ul>
        </>
      )}

      {buses.reason && <Caveat>машины на карте не показаны: {buses.reason}</Caveat>}

      {/* Два числа про машины приходят из разных мест: одно — расстановка по
          интервалу в одну сторону, другое — из данных по маршруту целиком.
          Молча показывать оба рядом нельзя, разница бросается в глаза. */}
      {!buses.reason && atHour?.n_vehicles != null && buses.count > atHour.n_vehicles * 1.25 && (
        <Caveat>
          на карте <span className="num">{buses.count}</span> бортов — это расстановка по интервалу{' '}
          <span className="num">{minutes(atHour.actual_headway_min)}</span> в одну сторону. В данных по
          маршруту <span className="num">{atHour.n_vehicles}</span> машин: там считается маршрут целиком,
          а не направление.
        </Caveat>
      )}

      {tailIsStraight && (
        <Caveat>хвост продления нарисован прямой: трассы по улицам для него в данных нет</Caveat>
      )}

      <div className="card-actions">
        <button
          className="btn"
          aria-pressed={editing}
          disabled={!detail || detail.stops.length === 0}
          onClick={onEdit}
        >
          {editing ? 'закончить правку' : 'редактировать'}
        </button>
        <button className="btn" disabled={!schedule?.available} onClick={onSchedule}>
          расписание
        </button>
        <a
          className="btn"
          href={`${API_BASE}/api/export/route?route_num=${encodeURIComponent(routeNum)}&direction=${direction}`}
          target="_blank"
          rel="noreferrer"
        >
          экспорт
        </a>
      </div>
    </Card>
  )
}

interface ChainRow {
  seq: number
  stopId: string
  name: string
  added: boolean
  /** Сколько узлов OSM схлопнулось в эту строку. */
  nodes: number
}

/**
 * Подряд идущие узлы с одним названием — это платформа и остановочный пункт
 * из OSM, для планировщика одна остановка. Показываем одной строкой; время
 * берётся у первого узла, его же seq уходит в клик, чтобы индексы совпадали
 * с цепочкой, по которой ядро применяет операции.
 */
function collapseChain(chain: { stopId: string; name: string; seq: number; added: boolean }[]): ChainRow[] {
  const rows: ChainRow[] = []
  for (const stop of chain) {
    const last = rows[rows.length - 1]
    if (last && last.name === stop.name) {
      last.nodes += 1
      // добавленная сценарием остановка не должна потеряться в схлопывании
      last.added = last.added || stop.added
      continue
    }
    rows.push({ seq: stop.seq, stopId: stop.stopId, name: stop.name, added: stop.added, nodes: 1 })
  }
  return rows
}

/** Номер рейса, вышедшего с первой остановки в указанный час или позже. */
function tripAtHour(firstStopArrivals: string[] | undefined, hour: number): number | null {
  if (!firstStopArrivals?.length) return null
  const index = firstStopArrivals.findIndex((a) => Number(a.slice(0, 2)) >= hour)
  return index === -1 ? null : index
}

interface GroupedWarning {
  key: string
  message: string
  severity: string
  count: number
}

/** Одно и то же правило срабатывает по десять раз — показываем правило, а не список. */
function dedupeWarnings(warnings: { code: string; message: string; severity: string }[]): GroupedWarning[] {
  const byCode = new Map<string, GroupedWarning>()
  for (const w of warnings) {
    const existing = byCode.get(w.code)
    if (existing) existing.count += 1
    else byCode.set(w.code, { key: w.code, message: w.message, severity: w.severity, count: 1 })
  }
  return [...byCode.values()]
}
