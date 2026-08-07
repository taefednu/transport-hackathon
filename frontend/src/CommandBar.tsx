/**
 * Командная строка (§15) — одно поле на две работы.
 *
 * Пока в поле короткий запрос, это поиск: ядро ищет по маршрутам и остановкам
 * и нечувствительно к письму — «Куйлюк», «Qo'yliq», «Kuyluk» и «қуйлиқ»
 * находят одно и то же. Как только фраза становится длиннее трёх слов, первой
 * строкой поднимается разбор фразы в сценарий: человек, который пишет
 * «продлить маршрут 1 до Куйлюка», ищет не остановку.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type SearchHit, type SearchResult } from './api'

/** С этого числа слов запрос считается фразой, а не поиском. */
const PHRASE_WORDS = 3
const DEBOUNCE_MS = 180

export interface CommandBarProps {
  busy: boolean
  /** Что ядро поняло из последней фразы; null — фразу ещё не разбирали. */
  understood: string | null
  onPickRoute: (routeNum: string) => void
  onPickStop: (stopId: string, at: [number, number] | null) => void
  onPhrase: (text: string) => void
}

export function CommandBar({
  busy,
  understood,
  onPickRoute,
  onPickStop,
  onPhrase,
}: CommandBarProps): React.JSX.Element {
  const [value, setValue] = useState('')
  const [result, setResult] = useState<SearchResult | null>(null)
  const [open, setOpen] = useState(false)
  const [cursor, setCursor] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)

  const isPhrase = value.trim().split(/\s+/).filter(Boolean).length >= PHRASE_WORDS

  useEffect(() => {
    const query = value.trim()
    if (query.length < 2) {
      setResult(null)
      return
    }
    let cancelled = false
    const timer = setTimeout(() => {
      api
        .search(query, 8)
        .then((r) => {
          if (!cancelled) {
            setResult(r)
            setError(null)
          }
        })
        .catch((err: unknown) => {
          if (!cancelled) setError(err instanceof Error ? err.message : String(err))
        })
    }, DEBOUNCE_MS)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [value])

  // §10 — «/» ставит курсор в поиск
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement
      if (e.key === '/' && !typing) {
        e.preventDefault()
        input.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  interface Row {
    key: string
    kind: 'phrase' | 'route' | 'stop'
    title: string
    detail?: string
    hit?: SearchHit
  }

  const rows = useMemo<Row[]>(() => {
    const out: Row[] = []
    const phraseRow: Row = {
      key: 'phrase',
      kind: 'phrase',
      title: 'понять фразу и пересчитать',
      detail: value.trim(),
    }
    if (isPhrase) out.push(phraseRow)
    for (const r of result?.routes ?? []) {
      // в title ядра номер уже есть; номер показываем щитком, как везде
      out.push({ key: `r:${r.id}`, kind: 'route', title: r.detail || r.title, detail: r.detail, hit: r })
    }
    for (const s of result?.stops ?? []) {
      out.push({ key: `s:${s.id}`, kind: 'stop', title: s.title, detail: s.detail, hit: s })
    }
    if (!isPhrase && value.trim().length >= 2) out.push(phraseRow)
    return out
  }, [result, isPhrase, value])

  useEffect(() => setCursor(0), [rows.length])

  const run = useCallback(
    (row: Row) => {
      if (row.kind === 'phrase') {
        onPhrase(row.detail ?? value.trim())
        setOpen(false)
        return
      }
      if (row.kind === 'route') {
        onPickRoute(row.hit!.id)
      } else {
        const { lat, lon } = row.hit!
        onPickStop(row.hit!.id, lat != null && lon != null ? [lon, lat] : null)
      }
      setOpen(false)
      setValue('')
      input.current?.blur()
    },
    [onPhrase, onPickRoute, onPickStop, value],
  )

  return (
    <div className="command">
      <input
        ref={input}
        className="command-input"
        value={value}
        placeholder="Найти маршрут, остановку или описать правку словами"
        onChange={(e) => {
          setValue(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            setOpen(false)
            input.current?.blur()
            return
          }
          if (!rows.length) return
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            setCursor((c) => (c + 1) % rows.length)
          } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setCursor((c) => (c - 1 + rows.length) % rows.length)
          } else if (e.key === 'Enter') {
            e.preventDefault()
            run(rows[cursor])
          }
        }}
      />
      {busy && <span className="command-busy">ядро разбирает фразу…</span>}

      {open && (rows.length > 0 || error) && (
        <div className="command-drop">
          {error && <div className="picker-empty">поиск не ответил: {error}</div>}
          {rows.map((row, i) => (
            <button
              key={row.key}
              className={`command-row${i === cursor ? ' is-cursor' : ''}`}
              onMouseEnter={() => setCursor(i)}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => run(row)}
            >
              {row.kind === 'route' ? (
                <span className="shield num">{row.hit!.id}</span>
              ) : (
                <span className={`command-kind kind-${row.kind}`}>
                  {row.kind === 'stop' ? '•' : '⌘'}
                </span>
              )}
              <span className="command-title">{row.title}</span>
              {row.kind !== 'phrase' && row.hit?.match === 'fuzzy' && (
                <span className="command-fuzzy">похоже</span>
              )}
            </button>
          ))}
        </div>
      )}

      {understood && !open && <div className="command-understood">{understood}</div>}
    </div>
  )
}
