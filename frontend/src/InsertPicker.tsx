/**
 * §8.4 — список существующих остановок рядом с местом клика.
 * Ставить произвольную точку нельзя: остановки без инфраструктуры не бывает.
 */

import type { StopFeature } from './api'
import { int } from './format'

export interface InsertPickerProps {
  /** Экранная позиция клика, чтобы список появился под курсором. */
  at: { x: number; y: number }
  afterSeq: number
  candidates: { stop: StopFeature; distance: number }[]
  onPick: (stopId: string) => void
  onClose: () => void
}

export function InsertPicker({ at, afterSeq, candidates, onPick, onClose }: InsertPickerProps): React.JSX.Element {
  return (
    <div className="picker" style={{ left: at.x + 12, top: at.y + 12 }}>
      <div className="picker-head">
        вставить после остановки <span className="num">{afterSeq + 1}</span>
        <button className="card-close" onClick={onClose} aria-label="закрыть">
          ✕
        </button>
      </div>
      {candidates.length === 0 ? (
        <div className="picker-empty">в двухстах метрах отсюда нет остановок, которых ещё нет в маршруте</div>
      ) : (
        <ul className="picker-list">
          {candidates.map(({ stop, distance }) => (
            <li key={stop.properties.stop_id}>
              <button onClick={() => onPick(stop.properties.stop_id)}>
                <span className="picker-name">{stop.properties.name}</span>
                <span className="num muted">{int(distance)} м</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
