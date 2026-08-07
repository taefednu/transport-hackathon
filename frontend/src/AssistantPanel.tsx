/**
 * Ассистент. Вопрос словами уходит в POST /api/assistant, ядро вызывает свои
 * инструменты и возвращает текст вместе со структурными действиями.
 *
 * Действия делятся надвое. Те, что меняют только вид — выбрать маршрут,
 * перевести камеру, подсветить дыры — применяются сразу: они ничего не портят
 * и человек всё равно за ними следит. Сценарий не применяется никогда: он
 * приходит готовым для POST /api/scenario и ждёт кнопки. Решение о правке
 * сети принимает планировщик, а не модель.
 */

import { useRef, useState } from 'react'
import type { AssistantAction, AssistantAnswer } from './api'
import { Panel } from './Panel'

export interface AssistantTurn {
  id: number
  question: string
  answer: AssistantAnswer | null
  error: string | null
}

export interface AssistantPanelProps {
  open: boolean
  onToggle: () => void
  busy: boolean
  log: AssistantTurn[]
  /** Ключи уже применённых сценариев: кнопка не должна звать дважды. */
  applied: Set<string>
  onAsk: (text: string) => void
  onAction: (action: AssistantAction, key: string) => void
}

export function AssistantPanel({
  open,
  onToggle,
  busy,
  log,
  applied,
  onAsk,
  onAction,
}: AssistantPanelProps): React.JSX.Element {
  const [text, setText] = useState('')
  const input = useRef<HTMLInputElement>(null)

  const send = () => {
    const question = text.trim()
    if (!question || busy) return
    onAsk(question)
    setText('')
  }

  return (
    <Panel
      title="ассистент"
      aside={busy ? <span className="as-note">думает…</span> : null}
      open={open}
      onToggle={onToggle}
      foot={
        <div className="as-ask">
          <input
            ref={input}
            className="as-input"
            value={text}
            placeholder="спросите про сеть"
            disabled={busy}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                send()
              }
            }}
          />
          <button className="as-send" disabled={busy || !text.trim()} onClick={send}>
            спросить
          </button>
        </div>
      }
    >
      <div className="as-body">
        {log.length === 0 && !busy && (
          <div className="as-note">
            «какие маршруты требуют внимания», «где дыры покрытия», «что можно сделать с маршрутом 29»
          </div>
        )}

        {log.map((turn) => (
          <div key={turn.id}>
            <div className="as-you">{turn.question}</div>
            {turn.error && <div className="cons-note error">ассистент не ответил: {turn.error}</div>}
            {turn.answer && (
              <>
                <p className="as-text">{turn.answer.text}</p>
                <Actions turn={turn} applied={applied} onAction={onAction} />
                {turn.answer.reason && <div className="as-note">{turn.answer.reason}</div>}
                <div className="as-note">
                  {turn.answer.source === 'model' ? 'пересказано моделью, числа сверены с расчётом' : 'собрано ядром без модели'}
                </div>
              </>
            )}
          </div>
        ))}

        {busy && (
          <div className="as-wait">
            <span className="as-dot" />
            ядро считает и формулирует ответ
          </div>
        )}
      </div>
    </Panel>
  )
}

function Actions({
  turn,
  applied,
  onAction,
}: {
  turn: AssistantTurn
  applied: Set<string>
  onAction: (action: AssistantAction, key: string) => void
}): React.JSX.Element | null {
  const actions = turn.answer?.actions ?? []
  // Кнопкой показываем только сценарии: остальное уже применено к карте,
  // и вторая кнопка «выбрать маршрут 29» рядом с выбранным маршрутом врёт.
  const scenarios = actions.filter((a) => a.type === 'apply_scenario')
  if (scenarios.length === 0) return null

  return (
    <div className="as-actions">
      {scenarios.map((action, index) => {
        const key = `${turn.id}:${index}`
        const done = applied.has(key)
        return (
          <button
            key={key}
            className={`as-apply${done ? ' as-applied' : ''}`}
            disabled={done}
            onClick={() => onAction(action, key)}
          >
            {done ? '✓ ' : ''}
            применить: {action.type === 'apply_scenario' ? action.label : ''}
          </button>
        )
      })}
    </div>
  )
}
