/**
 * §3.1 — сравнение двух маршрутов. Одна карточка на двоих: спека разрешает
 * держать открытой только одну, а две колонки рядом читаются лучше, чем
 * переключение между карточками.
 *
 * Числа берутся те же, что и в карточке маршрута, и теми же словами — иначе
 * сравнение превращается в сравнение формулировок.
 */

import type { Direction, RouteSummary } from './api'
import { Card, Caveat } from './Card'
import { duration, hourLabel, km, minutes } from './format'
import type { RouteData } from './routeData'

export interface CompareCardProps {
  hour: number
  left: { routeNum: string; direction: Direction; summary: RouteSummary | null; data: RouteData }
  right: { routeNum: string; direction: Direction; summary: RouteSummary | null; data: RouteData }
  onClose: () => void
  onDropCompare: () => void
}

export function CompareCard({ hour, left, right, onClose, onDropCompare }: CompareCardProps): React.JSX.Element {
  const rows = buildRows(hour, left, right)

  return (
    <Card
      title={
        <>
          <span className="shield num">{left.routeNum}</span>
          <span className="muted">против</span>
          <span className="shield shield-compare num">{right.routeNum}</span>
        </>
      }
      subtitle={<span className="muted">сравнение в {hourLabel(hour)}:00</span>}
      onClose={onClose}
    >
      <table className="compare">
        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <td className="compare-label">{row.label}</td>
              <td className="num compare-left">{row.left}</td>
              <td className="num compare-right">{row.right}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {(left.data.loading || right.data.loading) && <div className="muted">данные маршрутов грузятся…</div>}
      {left.data.detail?.quality !== 'exact' && left.data.detail && (
        <Caveat>у маршрута {left.routeNum} трасса не восстановлена — сравнивать можно только числа</Caveat>
      )}
      {right.data.detail?.quality !== 'exact' && right.data.detail && (
        <Caveat>у маршрута {right.routeNum} трасса не восстановлена — сравнивать можно только числа</Caveat>
      )}

      <div className="card-actions">
        <button className="btn" onClick={onDropCompare}>
          убрать второй маршрут
        </button>
      </div>
    </Card>
  )
}

interface Row {
  label: string
  left: React.ReactNode
  right: React.ReactNode
}

function buildRows(hour: number, left: CompareCardProps['left'], right: CompareCardProps['right']): Row[] {
  const side = (s: CompareCardProps['left']) => {
    const detail = s.data.detail
    const atHour = detail?.actual_headway.find((h) => h.hour === hour) ?? null
    return {
      stops: detail ? detail.stops.length || s.summary?.n_stops || '—' : '…',
      length: km(detail?.length_km ?? s.summary?.length_km),
      planned: minutes(detail?.planned_headway_min ?? s.summary?.planned_headway_min),
      actual: atHour?.actual_headway_min != null ? minutes(atHour.actual_headway_min) : '—',
      vehicles: atHour?.n_vehicles ?? '—',
      boardings: atHour?.n_boardings ?? '—',
      cycle: duration(s.data.schedule?.available ? s.data.schedule.cycle_time_min : null),
      work: detail?.work_start && detail.work_end ? `${detail.work_start}–${detail.work_end}` : '—',
    }
  }
  const a = side(left)
  const b = side(right)
  return [
    { label: 'остановок', left: a.stops, right: b.stops },
    { label: 'длина', left: a.length, right: b.length },
    { label: 'плановый интервал', left: a.planned, right: b.planned },
    { label: `фактический в ${hourLabel(hour)}:00`, left: a.actual, right: b.actual },
    { label: `машин в ${hourLabel(hour)}:00`, left: a.vehicles, right: b.vehicles },
    { label: `посадок в ${hourLabel(hour)}:00`, left: a.boardings, right: b.boardings },
    { label: 'время оборота', left: a.cycle, right: b.cycle },
    { label: 'режим работы', left: a.work, right: b.work },
  ]
}
