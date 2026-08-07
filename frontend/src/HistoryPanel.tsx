/** §10 — список последних правок. Клик по строке откатывает до этого состояния. */

import { signed } from './format'
import type { ScenarioState } from './scenario'

export function HistoryPanel({
  state,
  onRollback,
  onClose,
}: {
  state: ScenarioState
  onRollback: (index: number) => void
  onClose: () => void
}): React.JSX.Element {
  return (
    <div className="history">
      <div className="history-head">
        правки
        <button className="card-close" onClick={onClose} aria-label="закрыть">
          ✕
        </button>
      </div>
      {state.entries.length === 0 ? (
        <div className="picker-empty">правок нет</div>
      ) : (
        <ol className="history-list">
          {state.entries.map((entry, i) => (
            <li key={i}>
              <button onClick={() => onRollback(i)}>
                <span className="history-label">{entry.label}</span>
                {entry.net !== null && (
                  <span className={`num ${entry.net >= 0 ? 'gain' : 'loss'}`}>{signed(entry.net)}</span>
                )}
              </button>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
