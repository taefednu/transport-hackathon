/**
 * Карточка расписания (§12). Здесь видно эффект от времени выезда: слева
 * прибытие по реестру, справа — по заданному выезду, разница подсвечена.
 */

import { useEffect, useState } from 'react'
import type { Direction, RouteSchedule, Weekday } from './api'
import { Card, Caveat, Rows } from './Card'
import { duration, minutes } from './format'
import { loadSchedule } from './routeData'

export interface ScheduleCardProps {
  routeNum: string
  direction: Direction
  weekday: Weekday
  /** Расписание по реестру: с ним сравниваем. */
  base: RouteSchedule | null
  applied: { first_departure?: string | null; headway_min?: number | null; n_vehicles?: number | null } | null
  onApply: (params: { first_departure?: string; headway_min?: number; n_vehicles?: number }) => void
  onClose: () => void
}

const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/

export function ScheduleCard({
  routeNum,
  direction,
  weekday,
  base,
  applied,
  onApply,
  onClose,
}: ScheduleCardProps): React.JSX.Element {
  const [departure, setDeparture] = useState(applied?.first_departure ?? base?.first_departure ?? '')
  const [headway, setHeadway] = useState(String(applied?.headway_min ?? base?.headway_min ?? ''))
  const [preview, setPreview] = useState<RouteSchedule | null>(null)
  const [error, setError] = useState<string | null>(null)

  const departureValid = TIME_RE.test(departure)
  const headwayValue = Number(headway.replace(',', '.'))
  const headwayValid = Number.isFinite(headwayValue) && headwayValue > 0

  useEffect(() => {
    if (!departureValid || !headwayValid) return
    let cancelled = false
    setError(null)
    loadSchedule(routeNum, direction, weekday, departure, headwayValue)
      .then((s) => {
        if (!cancelled) setPreview(s)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [routeNum, direction, weekday, departure, headwayValue, departureValid, headwayValid])

  const shown = preview ?? base

  return (
    <Card
      title={
        <>
          <span className="shield num">{routeNum}</span>
          <span className="card-name">расписание</span>
        </>
      }
      subtitle={<span className="muted">прибытие первого рейса по остановкам</span>}
      onClose={onClose}
    >
      <div className="fields">
        <label className="field">
          <span>первый выезд</span>
          <input
            className="num"
            value={departure}
            onChange={(e) => setDeparture(e.target.value)}
            placeholder="06:00"
            aria-invalid={!departureValid}
          />
        </label>
        <label className="field">
          <span>интервал, мин</span>
          <input
            className="num"
            value={headway}
            onChange={(e) => setHeadway(e.target.value)}
            placeholder="10"
            aria-invalid={!headwayValid}
          />
        </label>
      </div>

      {!departureValid && <Caveat>время задаётся как ЧЧ:ММ, например 06:00</Caveat>}
      {error && <Caveat>ядро не построило расписание: {error}</Caveat>}

      {shown && !shown.available && shown.reason && <Caveat>{shown.reason}</Caveat>}

      {shown?.available && (
        <Rows
          items={[
            ['рейсов за день', shown.trips],
            ['время в одну сторону', duration(shown.one_way_min)],
            ['время оборота', duration(shown.cycle_time_min)],
            ['машин требуется', shown.required_vehicles],
            ['интервал', minutes(shown.headway_min)],
            ['последнее прибытие', shown.last_arrival_last_stop ?? '—'],
          ]}
        />
      )}

      {shown?.available && base?.available && (
        <>
          <div className="card-section">
            остановка × прибытие
            <span className="muted"> · реестр {base.first_departure} → {shown.first_departure}</span>
          </div>
          <table className="sched">
            <tbody>
              {shown.stops.map((s, i) => {
                const now = s.arrivals[0]
                const was = base.stops[i]?.arrivals[0]
                const changed = was !== undefined && now !== was
                return (
                  <tr key={`${s.seq}-${s.stop_id}`} className={changed ? 'changed' : undefined}>
                    <td className="sched-name">{s.name ?? s.stop_id}</td>
                    <td className="num sched-was">{was ?? ''}</td>
                    <td className="num sched-now">{now ?? ''}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </>
      )}

      <div className="card-actions">
        <button
          className="btn"
          disabled={!departureValid || !headwayValid}
          onClick={() => onApply({ first_departure: departure, headway_min: headwayValue })}
        >
          применить к сценарию
        </button>
      </div>
      {applied && (
        <div className="muted" style={{ marginTop: 6 }}>
          в сценарии: выезд {applied.first_departure ?? '—'}, интервал {applied.headway_min ?? '—'} мин
        </div>
      )}
    </Card>
  )
}
