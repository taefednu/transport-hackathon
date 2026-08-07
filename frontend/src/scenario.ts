/**
 * Сценарий: список операций поверх базовой сети.
 *
 * Порядок остановок повторяется на клиенте ровно так же, как его применяет
 * ядро (`app/scenario.py`), иначе индексы `after_seq` и `seq` в операциях
 * будут указывать не туда. Правила там простые и меняться не собираются:
 * продление добавляет в хвост, обрезка режет по индексу, вставка и удаление
 * работают по текущей цепочке, а не по исходной.
 *
 * Чего ядро не умеет и мы не предлагаем: добавлять остановку в начало
 * маршрута и обрезать его с головы. Обе операции определены только с хвоста.
 */

import type { Direction, ScenarioOp } from './api'

export interface HistoryEntry {
  op: ScenarioOp
  label: string
  /** Чистое изменение в людях. Появляется, когда придёт ответ ядра. */
  net: number | null
}

/** §10: стек отмены — 50 шагов. */
export const UNDO_LIMIT = 50

export interface ScenarioState {
  entries: HistoryEntry[]
  /** Снятые отменой операции: ждут возврата. */
  redo: HistoryEntry[]
}

export const EMPTY_SCENARIO: ScenarioState = { entries: [], redo: [] }

export function ops(state: ScenarioState): ScenarioOp[] {
  return state.entries.map((e) => e.op)
}

export function push(state: ScenarioState, entry: HistoryEntry): ScenarioState {
  const entries = [...state.entries, entry].slice(-UNDO_LIMIT)
  return { entries, redo: [] }
}

export function undo(state: ScenarioState): ScenarioState {
  if (state.entries.length === 0) return state
  const entries = state.entries.slice(0, -1)
  return { entries, redo: [...state.redo, state.entries[state.entries.length - 1]] }
}

export function redo(state: ScenarioState): ScenarioState {
  if (state.redo.length === 0) return state
  const entry = state.redo[state.redo.length - 1]
  return { entries: [...state.entries, entry], redo: state.redo.slice(0, -1) }
}

/** Откат до состояния после указанной правки включительно. */
export function rollbackTo(state: ScenarioState, index: number): ScenarioState {
  if (index < 0 || index >= state.entries.length) return state
  return { entries: state.entries.slice(0, index + 1), redo: [] }
}

export function setNet(state: ScenarioState, net: number): ScenarioState {
  if (state.entries.length === 0) return state
  const entries = state.entries.slice()
  entries[entries.length - 1] = { ...entries[entries.length - 1], net }
  return { ...state, entries }
}

/** Повтор логики ядра: цепочка остановок после применения операций. */
export function applyOps(base: string[], allOps: ScenarioOp[], routeNum: string, direction: Direction): string[] {
  let seq = base.slice()
  for (const op of allOps) {
    if (op.type === 'set_schedule') continue
    if (op.route_num !== routeNum || op.direction !== direction) continue
    if (op.type === 'extend_route') seq = [...seq, ...op.stops]
    else if (op.type === 'trim_route') seq = seq.slice(0, op.until_seq + 1)
    else if (op.type === 'insert_stop') {
      const next = seq.slice()
      next.splice(op.after_seq + 1, 0, op.stop_id)
      seq = next
    } else if (op.type === 'remove_stop') {
      const next = seq.slice()
      next.splice(op.seq, 1)
      seq = next
    }
  }
  return seq
}

/** Расписание, заданное операциями: последняя запись по маршруту побеждает. */
export function scheduleOverride(
  allOps: ScenarioOp[],
  routeNum: string,
): { first_departure?: string | null; headway_min?: number | null; n_vehicles?: number | null } | null {
  let found: ScenarioOp | null = null
  for (const op of allOps) if (op.type === 'set_schedule' && op.route_num === routeNum) found = op
  if (!found || found.type !== 'set_schedule') return null
  return {
    first_departure: found.first_departure,
    headway_min: found.headway_min,
    n_vehicles: found.n_vehicles,
  }
}

export function describe(op: ScenarioOp, stopName: (id: string) => string): string {
  switch (op.type) {
    case 'extend_route':
      return `продлён маршрут ${op.route_num} до ${op.stops.map(stopName).join(', ')}`
    case 'trim_route':
      return `обрезан маршрут ${op.route_num} на остановке ${op.until_seq + 1}`
    case 'insert_stop':
      return `вставлена остановка ${stopName(op.stop_id)} в маршрут ${op.route_num}`
    case 'remove_stop':
      return `убрана остановка ${op.seq + 1} из маршрута ${op.route_num}`
    case 'set_schedule': {
      const parts: string[] = []
      if (op.first_departure) parts.push(`выезд ${op.first_departure}`)
      if (op.headway_min) parts.push(`интервал ${op.headway_min} мин`)
      if (op.n_vehicles) parts.push(`машин ${op.n_vehicles}`)
      return `расписание маршрута ${op.route_num}: ${parts.join(', ') || 'по реестру'}`
    }
  }
}
