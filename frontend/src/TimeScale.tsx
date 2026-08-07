/**
 * Выбор часа. Час меняет всё, что считается по часам.
 *
 * Ленты 05–23 во всю ширину экрана больше нет: она занимала полосу, которая
 * нужнее карте. Осталось два шага и число; вся сетка раскрывается по клику
 * по числу — и там же видно, что доступны именно эти девятнадцать часов.
 */

import { useEffect, useRef, useState } from 'react'
import { hourLabel } from './format'

/** Границы взяты из данных: раньше пяти и позже двадцати трёх интервалов нет. */
export const FIRST_HOUR = 5
export const LAST_HOUR = 23

export const HOURS = Array.from({ length: LAST_HOUR - FIRST_HOUR + 1 }, (_, i) => FIRST_HOUR + i)

export function HourPicker({
  hour,
  onHour,
}: {
  hour: number
  onHour: (hour: number) => void
}): React.JSX.Element {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false)
    }
    window.addEventListener('mousedown', onDown)
    return () => window.removeEventListener('mousedown', onDown)
  }, [open])

  return (
    <div className="hourpick" ref={box} role="group" aria-label="час">
      <button
        className="hour-step"
        disabled={hour <= FIRST_HOUR}
        title="час назад (←)"
        onClick={() => onHour(Math.max(FIRST_HOUR, hour - 1))}
      >
        ‹
      </button>
      <button className="hour-now num" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {hourLabel(hour)}:00
      </button>
      <button
        className="hour-step"
        disabled={hour >= LAST_HOUR}
        title="час вперёд (→)"
        onClick={() => onHour(Math.min(LAST_HOUR, hour + 1))}
      >
        ›
      </button>

      {open && (
        <div className="hour-grid">
          {HOURS.map((h) => (
            <button
              key={h}
              className="hour num"
              aria-pressed={h === hour}
              onClick={() => {
                onHour(h)
                setOpen(false)
              }}
            >
              {hourLabel(h)}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
