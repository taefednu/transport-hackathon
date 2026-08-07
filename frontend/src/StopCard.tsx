/**
 * Карточка остановки (§12).
 *
 * Население в пятисотметровой зоне и сама зона приходят из `/api/stops/{id}/walkzone`
 * — по пешеходной сети, а не кругом на карте. Времени первого и последнего рейса
 * по каждому маршруту здесь по-прежнему нет: его пришлось бы тянуть отдельным
 * запросом на каждый номер, и мы этого не делаем.
 */

import type { Direction, RouteSummary, StopProps, WalkZone } from './api'
import { Card, Caveat, Rows } from './Card'
import { int, minutes, people, STOP_KIND } from './format'

export interface StopCardProps {
  stop: StopProps
  /** Маршруты, у которых эта остановка есть в восстановленной цепочке. */
  serving: { route: RouteSummary; direction: Direction }[]
  onSelectRoute: (routeNum: string, direction: Direction) => void
  /** §12 — зона пешей доступности: null, пока её не запросили. */
  zone: WalkZone | null
  zoneLoading: boolean
  onShowZone: () => void
  onHideZone: () => void
  onClose: () => void
}

export function StopCard({
  stop,
  serving,
  onSelectRoute,
  zone,
  zoneLoading,
  onShowZone,
  onHideZone,
  onClose,
}: StopCardProps): React.JSX.Element {
  return (
    <Card
      title={<span className="card-name">{stop.name}</span>}
      subtitle={<span className="muted">{STOP_KIND[stop.kind] ?? stop.kind}</span>}
      onClose={onClose}
    >
      <Rows
        items={[
          ['маршрутов по данным Яндекса', stop.n_routes],
          ['из них с восстановленной цепочкой', serving.length],
          ...(zone?.people != null
            ? ([[`в зоне ${int(zone.limit_m)} м пешком`, people(zone.people)]] as [string, string][])
            : []),
          ['источник', stop.source === 'yandex' ? 'Яндекс' : 'OpenStreetMap'],
        ]}
      />

      <div className="card-actions">
        <button className="btn" disabled={zoneLoading} onClick={zone ? onHideZone : onShowZone}>
          {zoneLoading
            ? 'ядро обходит пешеходную сеть…'
            : zone
              ? 'убрать зону пешей доступности'
              : 'показать зону пешей доступности'}
        </button>
      </div>

      {serving.length > 0 ? (
        <>
          <div className="card-section">проходят через остановку</div>
          <ul className="serving">
            {serving.map(({ route, direction }) => (
              <li key={`${route.route_num}:${direction}`}>
                <button className="serving-btn" onClick={() => onSelectRoute(route.route_num, direction)}>
                  <span className="shield num">{route.route_num}</span>
                  <span className="serving-name">{route.name}</span>
                  <span className="num muted">{minutes(route.planned_headway_min)}</span>
                </button>
              </li>
            ))}
          </ul>
          <Caveat>интервал — плановый, из реестра. Фактический — в карточке маршрута.</Caveat>
        </>
      ) : (
        <Caveat>
          цепочка остановок не восстановлена ни у одного из маршрутов этой остановки — назвать их
          нечем
        </Caveat>
      )}
    </Card>
  )
}
