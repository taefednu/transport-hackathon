/**
 * Пересчёт сценария. Ходит на сервер не на каждый кадр, а через 200 мс после
 * того, как список операций перестал меняться (§8.2). Пока ответ не пришёл,
 * прошлые числа остаются на месте — они гаснут, но не исчезают.
 */

import { useEffect, useRef, useState } from 'react'
import { api, type ScenarioOp, type ScenarioResult, type Weekday } from './api'

/** §8.2 — задержка после остановки курсора. */
const DEBOUNCE_MS = 200
/** §16 — после этого говорим, что пересчёт затянулся. */
const SLOW_MS = 3000

export interface ScenarioComputation {
  result: ScenarioResult | null
  pending: boolean
  slow: boolean
  error: string | null
}

export function useScenarioResult(
  weekday: Weekday,
  hour: number,
  ops: ScenarioOp[],
): ScenarioComputation {
  const [state, setState] = useState<ScenarioComputation>({
    result: null,
    pending: false,
    slow: false,
    error: null,
  })
  const abort = useRef<AbortController | null>(null)
  const key = JSON.stringify(ops)

  useEffect(() => {
    const current = JSON.parse(key) as ScenarioOp[]
    if (current.length === 0) {
      abort.current?.abort()
      setState({ result: null, pending: false, slow: false, error: null })
      return
    }

    setState((s) => ({ ...s, pending: true, slow: false, error: null }))
    const slowTimer = setTimeout(() => setState((s) => (s.pending ? { ...s, slow: true } : s)), SLOW_MS)

    const timer = setTimeout(() => {
      abort.current?.abort()
      const controller = new AbortController()
      abort.current = controller
      api
        .scenario(weekday, hour, current, controller.signal)
        .then((result) => setState({ result, pending: false, slow: false, error: null }))
        .catch((err: unknown) => {
          if (err instanceof DOMException && err.name === 'AbortError') return
          setState((s) => ({
            ...s,
            pending: false,
            slow: false,
            error: err instanceof Error ? err.message : String(err),
          }))
        })
    }, DEBOUNCE_MS)

    return () => {
      clearTimeout(timer)
      clearTimeout(slowTimer)
    }
  }, [key, weekday, hour])

  return state
}
