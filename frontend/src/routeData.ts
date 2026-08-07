/**
 * Данные конкретного маршрута: тянутся по требованию и кэшируются на вкладке.
 * Детали маршрута весят под двести килобайт, второй раз за тем же не ходим.
 */

import { useEffect, useState } from 'react'
import { api, type Direction, type RouteDetail, type RouteSchedule, type Weekday } from './api'

const details = new Map<string, Promise<RouteDetail>>()
const schedules = new Map<string, Promise<RouteSchedule>>()

function detailKey(routeNum: string, direction: Direction, weekday: Weekday): string {
  return `${routeNum}:${direction}:${weekday}`
}

export function loadDetail(routeNum: string, direction: Direction, weekday: Weekday): Promise<RouteDetail> {
  const key = detailKey(routeNum, direction, weekday)
  let promise = details.get(key)
  if (!promise) {
    promise = api.routeDetail(routeNum, direction, weekday)
    details.set(key, promise)
    // неудачу не кэшируем: следующий выбор маршрута должен попробовать снова
    promise.catch(() => details.delete(key))
  }
  return promise
}

export function loadSchedule(
  routeNum: string,
  direction: Direction,
  weekday: Weekday,
  firstDeparture?: string,
  headwayMin?: number,
): Promise<RouteSchedule> {
  const key = `${detailKey(routeNum, direction, weekday)}:${firstDeparture ?? ''}:${headwayMin ?? ''}`
  let promise = schedules.get(key)
  if (!promise) {
    promise = api.routeSchedule(routeNum, direction, weekday, {
      first_departure: firstDeparture,
      headway_min: headwayMin,
    })
    schedules.set(key, promise)
    promise.catch(() => schedules.delete(key))
  }
  return promise
}

export interface RouteData {
  detail: RouteDetail | null
  schedule: RouteSchedule | null
  loading: boolean
  error: string | null
}

export function useRouteData(
  routeNum: string | null,
  direction: Direction,
  weekday: Weekday,
): RouteData {
  const [state, setState] = useState<RouteData>({
    detail: null,
    schedule: null,
    loading: false,
    error: null,
  })

  useEffect(() => {
    if (!routeNum) {
      setState({ detail: null, schedule: null, loading: false, error: null })
      return
    }
    let cancelled = false
    setState({ detail: null, schedule: null, loading: true, error: null })

    void (async () => {
      try {
        const detail = await loadDetail(routeNum, direction, weekday)
        if (cancelled) return
        // карточка показывается сразу по деталям; расписание догружается следом
        setState({ detail, schedule: null, loading: true, error: null })
        const schedule = await loadSchedule(routeNum, direction, weekday)
        if (cancelled) return
        setState({ detail, schedule, loading: false, error: null })
      } catch (err) {
        if (cancelled) return
        setState({
          detail: null,
          schedule: null,
          loading: false,
          error: err instanceof Error ? err.message : String(err),
        })
      }
    })()

    return () => {
      cancelled = true
    }
  }, [routeNum, direction, weekday])

  return state
}
