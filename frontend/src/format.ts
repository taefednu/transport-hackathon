/**
 * Числа для интерфейса. Разряды — неразрывным пробелом, ведущих нулей нет.
 * Единица людей — «чел.»: мы считаем жителей в пешей доступности, а не поездки.
 */

const NBSP = ' '
const MINUS = '−'

function groups(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP)
}

/** Целое с разделителями разрядов. */
export function int(value: number): string {
  const rounded = Math.round(Math.abs(value))
  const sign = value < 0 ? MINUS : ''
  return sign + groups(String(rounded))
}

/** То же, но со знаком всегда: для дельт в плашке последствий. */
export function signed(value: number): string {
  const rounded = Math.round(Math.abs(value))
  if (rounded === 0) return '0'
  return (value < 0 ? MINUS : '+') + groups(String(rounded))
}

export function people(value: number): string {
  return `${int(value)}${NBSP}чел.`
}

export function percent(share: number, digits = 1): string {
  return `${(share * 100).toFixed(digits).replace('.', ',')}${NBSP}%`
}

export function minutes(value: number | null | undefined, digits = 0): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return `${value.toFixed(digits).replace('.', ',')}${NBSP}мин`
}

export function km(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return `${value.toFixed(1).replace('.', ',')}${NBSP}км`
}

/** Часы и минуты из минут: время оборота удобнее читать так. */
export function duration(min: number | null | undefined): string {
  if (min == null || !Number.isFinite(min)) return '—'
  const total = Math.round(min)
  if (total < 60) return `${total}${NBSP}мин`
  return `${Math.floor(total / 60)}${NBSP}ч${NBSP}${String(total % 60).padStart(2, '0')}${NBSP}мин`
}

export function hourLabel(hour: number): string {
  return String(hour).padStart(2, '0')
}

export const STOP_KIND: Record<string, string> = {
  bus: 'автобусная',
  metro: 'метро',
  minibus: 'маршрутка',
}

/** Русское склонение по числу: [1, 2–4, 5+]. */
export function plural(count: number, forms: [string, string, string]): string {
  const n = Math.abs(Math.round(count)) % 100
  const n1 = n % 10
  if (n > 10 && n < 20) return forms[2]
  if (n1 > 1 && n1 < 5) return forms[1]
  if (n1 === 1) return forms[0]
  return forms[2]
}

/** Время суток из часа и минут после него: «8:23». */
export function clockAfter(hour: number, minutesLater: number): string {
  const total = Math.round(hour * 60 + minutesLater)
  const hh = Math.floor(total / 60) % 24
  const mm = total % 60
  return `${hh}:${String(mm).padStart(2, '0')}`
}
