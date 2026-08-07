/**
 * Подбор вариантов из ядра: по маршруту и по дыре покрытия.
 *
 * Запрос идёт при выборе объекта, а не по кнопке: перебор занимает около
 * секунды, и ждать её один раз дешевле, чем не узнать, что вариант есть.
 * Пока считается, видно, что считается.
 */

import { useEffect, useState } from 'react'
import { api, type HoleOptions, type RouteOptions, type Weekday } from './api'

export interface Loaded<T> {
  data: T | null
  loading: boolean
  error: string | null
}

const IDLE = { data: null, loading: false, error: null }

function useLoaded<T>(
  key: string | null,
  fetcher: (signal: AbortSignal) => Promise<T>,
): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>(IDLE)

  useEffect(() => {
    if (!key) {
      setState(IDLE)
      return
    }
    const controller = new AbortController()
    setState({ data: null, loading: true, error: null })
    fetcher(controller.signal)
      .then((data) => setState({ data, loading: false, error: null }))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setState({
          data: null,
          loading: false,
          error: err instanceof Error ? err.message : String(err),
        })
      })
    return () => controller.abort()
    // ключ содержит всё, от чего зависит запрос
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return state
}

export function useRouteOptions(
  routeNum: string | null,
  weekday: Weekday,
  hour: number,
): Loaded<RouteOptions> {
  return useLoaded(routeNum && `${routeNum}:${weekday}:${hour}`, (signal) =>
    api.routeOptions(routeNum!, weekday, hour, signal),
  )
}

export function useHoleOptions(
  h3: string | null,
  weekday: Weekday,
  hour: number,
): Loaded<HoleOptions> {
  return useLoaded(h3 && `${h3}:${weekday}:${hour}`, (signal) =>
    api.holeOptions(h3!, weekday, hour, signal),
  )
}
