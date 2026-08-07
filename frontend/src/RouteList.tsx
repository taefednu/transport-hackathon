/**
 * Список маршрутов под поиском. Свёрнут по умолчанию: 165 строк — это
 * половина экрана, а карта важнее.
 *
 * Интервал в строке — фактический, из транзакций за выбранный час. Там, где
 * за этот час рейсов не было, интервала не существует: подставляем плановый
 * из реестра и подписываем, чтобы одно не выдавалось за другое.
 */

import { useMemo, useState } from 'react'
import type { Direction, HourHeadway, RouteSummary } from './api'
import { Panel } from './Panel'
import { minutes } from './format'

export interface RouteListProps {
  routes: RouteSummary[]
  /** Фактические интервалы за текущий час; null — ещё не пришли. */
  headways: Record<string, HourHeadway> | null
  selected: { routeNum: string; direction: Direction } | null
  open: boolean
  onToggle: () => void
  onPick: (routeNum: string) => void
}

export function RouteList({
  routes,
  headways,
  selected,
  open,
  onToggle,
  onPick,
}: RouteListProps): React.JSX.Element {
  const [filter, setFilter] = useState('')

  const shown = useMemo(() => {
    const query = filter.trim().toLowerCase()
    const list = query
      ? routes.filter(
          (r) => r.route_num.toLowerCase().includes(query) || r.name.toLowerCase().includes(query),
        )
      : routes
    return [...list].sort((a, b) => a.route_num.localeCompare(b.route_num, 'ru', { numeric: true }))
  }, [routes, filter])

  return (
    <Panel
      title="маршруты"
      aside={<span className="num panel-head-num">{routes.length}</span>}
      open={open}
      onToggle={onToggle}
    >
      <input
        className="route-filter"
        value={filter}
        placeholder="номер или название"
        onChange={(e) => setFilter(e.target.value)}
      />
      {shown.length === 0 ? (
        <div className="route-empty">ничего не нашлось</div>
      ) : (
        shown.map((route) => {
          const actual = headways?.[route.route_num]?.actual_headway_min ?? null
          const isOn = selected?.routeNum === route.route_num
          return (
            <button
              key={route.route_num}
              className={`route-row${isOn ? ' is-on' : ''}`}
              aria-pressed={isOn}
              onClick={() => onPick(route.route_num)}
            >
              <span className="shield num">{route.route_num}</span>
              <span className="route-main">
                <span className="route-name">{route.name}</span>
                <span className="route-sub num">
                  {route.directions.length > 1 ? 'два направления' : 'одно направление'}
                </span>
              </span>
              <span className="route-hw num">
                {actual != null ? minutes(actual) : minutes(route.planned_headway_min)}
                <span className="route-hw-sub">{actual != null ? 'факт' : 'план'}</span>
              </span>
            </button>
          )
        })
      )}
    </Panel>
  )
}
