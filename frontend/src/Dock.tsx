/**
 * Нижний док. Всё, чем управляют картой, собрано в одну панель по центру:
 * инструмент, слои, отмена, режим показа, день и час.
 *
 * «Вставка остановки» — это тот же режим правки: ядро вставляет остановку
 * кликом по линии, отдельного состояния у карты для этого нет. Кнопка меняет
 * только подсказку, потому что человек, который ищет «вставить», не должен
 * догадываться, что для этого надо сначала нажать «править».
 */

import type { Direction, Weekday } from './api'
import {
  IconEdit,
  IconExpand,
  IconHex,
  IconHoles,
  IconInsert,
  IconRedo,
  IconSelect,
  IconUndo,
} from './Icons'
import { HourPicker } from './TimeScale'

export type Tool = 'select' | 'edit' | 'insert'
export type Mode = 'base' | 'scenario' | 'compare' | 'holes'

const WEEKDAYS: [Weekday, string][] = [
  ['fri', 'пт'],
  ['sat', 'сб'],
  ['sun', 'вс'],
]

const MODES: [Mode, string, string][] = [
  ['base', '1', 'база'],
  ['scenario', '2', 'сценарий'],
  ['compare', '3', 'сравнение'],
]

export interface DockProps {
  tool: Tool
  onTool: (tool: Tool) => void
  /** Править нечего, пока не выбран маршрут с восстановленной цепочкой. */
  canEdit: boolean
  mode: Mode
  onMode: (mode: Mode) => void
  showHexes: boolean
  onHexes: () => void
  showHoles: boolean
  onHoles: () => void
  undoCount: number
  redoCount: number
  onUndo: () => void
  onRedo: () => void
  historyOpen: boolean
  onHistory: () => void
  weekday: Weekday
  onWeekday: (weekday: Weekday) => void
  hour: number
  onHour: (hour: number) => void
  onDemo: () => void
  /** Направление показываем в подсказке правки — иначе непонятно, что правим. */
  direction: Direction | null
}

export function Dock(props: DockProps): React.JSX.Element {
  return (
    <div className="dock">
      <div className="dock-group">
        <button
          className="tool"
          aria-pressed={props.tool === 'select'}
          title="выбор (Esc)"
          aria-label="выбор"
          onClick={() => props.onTool('select')}
        >
          <IconSelect />
        </button>
        <button
          className="tool"
          aria-pressed={props.tool === 'edit'}
          disabled={!props.canEdit}
          title={`редактирование маршрута${props.direction ? ` · ${props.direction === 'fwd' ? 'А → Б' : 'Б → А'}` : ''} (E)`}
          aria-label="редактирование маршрута"
          onClick={() => props.onTool('edit')}
        >
          <IconEdit />
        </button>
        <button
          className="tool"
          aria-pressed={props.tool === 'insert'}
          disabled={!props.canEdit}
          title="вставка остановки: клик по линии маршрута"
          aria-label="вставка остановки"
          onClick={() => props.onTool('insert')}
        >
          <IconInsert />
        </button>
      </div>

      <div className="dock-sep" />

      <div className="dock-group">
        <button
          className="tool"
          aria-pressed={props.showHexes}
          title="слой населения (H)"
          aria-label="слой населения"
          onClick={props.onHexes}
        >
          <IconHex />
        </button>
        <button
          className="tool"
          aria-pressed={props.showHoles}
          title="дыры покрытия (D)"
          aria-label="дыры покрытия"
          onClick={props.onHoles}
        >
          <IconHoles />
        </button>
      </div>

      <div className="dock-sep" />

      <div className="dock-group">
        <button
          className="tool"
          disabled={props.undoCount === 0}
          title="отменить (Ctrl+Z)"
          aria-label="отменить"
          onClick={props.onUndo}
        >
          <IconUndo />
        </button>
        <button
          className="tool"
          disabled={props.redoCount === 0}
          title="вернуть (Ctrl+Shift+Z)"
          aria-label="вернуть"
          onClick={props.onRedo}
        >
          <IconRedo />
        </button>
        <button
          className="tool num"
          aria-pressed={props.historyOpen}
          title="список правок"
          onClick={props.onHistory}
        >
          {props.undoCount}
        </button>
      </div>

      <div className="dock-sep" />

      <div className="dock-group">
        {MODES.map(([key, digit, label]) => (
          <button
            key={key}
            className="mode-btn"
            aria-pressed={props.mode === key}
            title={`${label} (${digit})`}
            onClick={() => props.onMode(key)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="dock-sep" />

      <div className="dock-group">
        <div className="seg">
          {WEEKDAYS.map(([key, label]) => (
            <button
              key={key}
              className="seg-btn"
              aria-pressed={props.weekday === key}
              onClick={() => props.onWeekday(key)}
            >
              {label}
            </button>
          ))}
        </div>
        <HourPicker hour={props.hour} onHour={props.onHour} />
      </div>

      <div className="dock-sep" />

      <button className="tool" title="режим демонстрации (F)" aria-label="режим демонстрации" onClick={props.onDemo}>
        <IconExpand />
      </button>
    </div>
  )
}
