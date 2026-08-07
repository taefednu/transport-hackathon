/**
 * Маршруты, требующие внимания. Диагностика ядра, разложенная в список.
 *
 * Живёт рядом со списком маршрутов и не зависит от модели: ассистент считает
 * то же самое теми же инструментами, но продукт обязан работать без сети.
 *
 * Исключённые маршруты не прячутся. У девяти маршрутов исходные значения
 * невозможны — длина в 2 602 км из дефекта релейшена OSM, — и в ранжировании
 * они заняли бы первые строки как «худшие в городе». Их число и причины видны
 * отдельной строкой: это факт о данных, а не о маршрутах.
 */

import { useState } from 'react'
import type { Attention } from './api'
import { Panel } from './Panel'
import { int, km, minutes } from './format'

export interface AttentionPanelProps {
  data: Attention | null
  loading: boolean
  error: string | null
  selected: string | null
  open: boolean
  onToggle: () => void
  onPick: (routeNum: string) => void
}

export function AttentionPanel({
  data,
  loading,
  error,
  selected,
  open,
  onToggle,
  onPick,
}: AttentionPanelProps): React.JSX.Element {
  const [showExcluded, setShowExcluded] = useState(false)

  return (
    <Panel
      title="требуют внимания"
      aside={
        data ? <span className="num panel-head-num">{data.routes_with_signs}</span> : null
      }
      open={open}
      onToggle={onToggle}
    >
      {loading && <div className="route-empty">ядро считает диагностику…</div>}
      {error && <div className="cons-note error">диагностика не пришла: {error}</div>}

      {data?.routes.map((route) => (
        <button
          key={route.route_num}
          className={`att-row${selected === route.route_num ? ' is-on' : ''}`}
          onClick={() => onPick(route.route_num)}
        >
          <span className="att-head">
            <span className="shield num">{route.route_num}</span>
            <span className="att-name">{route.name}</span>
            {/* Оценка тяжести — сумма признаков. Одна цифра ничего не значит
                без шкалы, поэтому рядом с ней стоит полоска. */}
            <span className="att-score num" title="оценка тяжести: сумма признаков маршрута">
              {route.score.toFixed(2).replace('.', ',')}
            </span>
          </span>
          <span className="att-bar">
            <span
              className="att-bar-fill"
              style={{ width: `${Math.min(100, Math.round((route.score / 2) * 100))}%` }}
            />
          </span>
          <span className="att-facts num">
            {route.actual_headway_min != null && (
              <span title="фактический интервал против планового">
                {minutes(route.actual_headway_min)} / {minutes(route.planned_headway_min)}
              </span>
            )}
            {route.n_stops != null && <span>{int(route.n_stops)} ост.</span>}
            {route.length_km != null && <span>{km(route.length_km)}</span>}
          </span>
          <span className="att-reasons">
            {route.reasons.map((reason) => (
              <span key={reason}>{reason}</span>
            ))}
          </span>
        </button>
      ))}

      {data && data.routes.length === 0 && !loading && (
        <div className="route-empty">
          ни у одного маршрута не сработал ни один признак — за этот час и день
        </div>
      )}

      {data && data.excluded_count > 0 && (
        <div className="att-excluded">
          <button className="att-excluded-btn" onClick={() => setShowExcluded((v) => !v)}>
            <span className="num">{data.excluded_count}</span> маршрутов не ранжируются:
            исходные значения невозможны {showExcluded ? '▴' : '▾'}
          </button>
          {showExcluded && (
            <ul className="att-excluded-list">
              {data.excluded_unreliable.map((item) => (
                <li key={item.route_num}>
                  <button className="att-excluded-row" onClick={() => onPick(item.route_num)}>
                    <span className="shield num">{item.route_num}</span>
                    <span>{item.reasons.join('; ')}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="att-note">
            по номеру они по-прежнему открываются — исключены только из списка
          </div>
        </div>
      )}
    </Panel>
  )
}
