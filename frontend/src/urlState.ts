/** Состояние экрана живёт в адресной строке: ссылка восстанавливает вид. */

import type { Direction, Weekday } from './api'

export interface UrlState {
  routeNum: string | null
  direction: Direction
  hour: number
  weekday: Weekday
}

const WEEKDAYS: Weekday[] = ['fri', 'sat', 'sun']
/** Утренний пик: с него осмысленно начинать разговор о доступности. */
export const DEFAULT_HOUR = 8

export function readUrl(): UrlState {
  const q = new URLSearchParams(window.location.search)
  const dir = q.get('dir')
  const rawHour = q.get('hour')
  const hour = rawHour === null ? Number.NaN : Number(rawHour)
  const weekday = q.get('day')
  return {
    routeNum: q.get('route'),
    direction: dir === 'bwd' ? 'bwd' : 'fwd',
    hour: Number.isInteger(hour) && hour >= 0 && hour <= 23 ? hour : DEFAULT_HOUR,
    weekday: WEEKDAYS.includes(weekday as Weekday) ? (weekday as Weekday) : 'fri',
  }
}

export function writeUrl(state: UrlState): void {
  const q = new URLSearchParams()
  if (state.routeNum) {
    q.set('route', state.routeNum)
    q.set('dir', state.direction)
  }
  q.set('hour', String(state.hour))
  if (state.weekday !== 'fri') q.set('day', state.weekday)
  const next = `${window.location.pathname}?${q}`
  if (next !== window.location.pathname + window.location.search) {
    window.history.replaceState(null, '', next)
  }
}
