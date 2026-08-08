/**
 * Что можно улучшить: маршруты, ранжированные по приросту людей от продления.
 *
 * Соседняя панель ранжирует по отклонению от плана — это другой вопрос, и
 * маршрут, дающий больше всех людей, стоял в ней 33-м из 35.
 *
 * Пока фоновый перебор не досчитал, здесь живой счётчик, а не пустой список:
 * пустой список читается как «улучшать нечего». Числа не показываются раньше,
 * чем они верны, поэтому частичный список не выводится — порядок в нём менялся
 * бы под пальцем.
 */

import type { Improvements } from './api'
import { Panel } from './Panel'
import { duration, int, km, people, plural } from './format'

export interface ImprovementsPanelProps {
  data: Improvements | null
  error: string | null
  selected: string | null
  open: boolean
  onToggle: () => void
  onPick: (routeNum: string) => void
}

export function ImprovementsPanel({
  data,
  error,
  selected,
  open,
  onToggle,
  onPick,
}: ImprovementsPanelProps): React.JSX.Element {
  const ready = data?.status === 'ready'

  return (
    <Panel
      title="что можно улучшить"
      aside={
        ready ? <span className="num panel-head-num">{data.routes_with_options}</span> : null
      }
      open={open}
      onToggle={onToggle}
    >
      {error && <div className="cons-note error">подбор не пришёл: {error}</div>}

      {/* Ядро может ответить успешно и при этом сообщить, что часть работы
          не удалась — например, не пересчитался день. Показываем всегда,
          когда поле непустое, а не только при status: failed. */}
      {data?.status === 'ready' && data.error && (
        <div className="cons-note error">{data.error}</div>
      )}

      {data?.status === 'computing' && (
        <div className="route-empty">
          ядро перебирает продления — <span className="num">{data.routes_done}</span> из{' '}
          <span className="num">{data.routes_total}</span>{' '}
          {plural(data.routes_total, ['маршрута', 'маршрутов', 'маршрутов'])}
        </div>
      )}

      {data?.status === 'failed' && (
        <div className="cons-note error">
          перебор не досчитал: {data.error}. По клику на маршрут подбор продлений работает
          как раньше
        </div>
      )}

      {ready &&
        data.routes.map((row) => (
          <button
            key={`${row.route_num}-${row.stop_id}`}
            className={`att-row${selected === row.route_num ? ' is-on' : ''}`}
            onClick={() => onPick(row.route_num)}
          >
            <span className="att-head">
              <span className="shield num">{row.route_num}</span>
              <span className="att-name">продлить до «{row.stop_name}»</span>
              <span className="imp-gain num">+{people(row.gained_people)}</span>
            </span>
            <span className="att-facts num">
              {row.cost_unavailable ? (
                /* Цену посчитать нечем — говорим почему, а не показываем
                   прочерки: прочерк читается как ноль машин. */
                <span>{row.cost_unavailable}</span>
              ) : (
                <>
                  <span title="сколько машин добавится на линию">
                    {row.extra_vehicles === 0
                      ? 'без новых машин'
                      : `+${int(row.extra_vehicles as number)} ${plural(row.extra_vehicles as number, ['машина', 'машины', 'машин'])}`}
                  </span>
                  <span title="оборот до и после продления">
                    {duration(row.cycle_time_before_min)} → {duration(row.cycle_time_after_min)}
                  </span>
                </>
              )}
              <span title="длина хвоста продления">{km(row.tail_km)}</span>
            </span>
            <span className="att-reasons">
              {row.same_stop_as && <span>та же остановка, что у маршрута {row.same_stop_as}</span>}
              {row.confidence === 'osm_only' && (
                <span>
                  остановка известна только по OSM: счётчика маршрутов по ней нет, её может
                  кто-то уже обслуживать
                </span>
              )}
            </span>
          </button>
        ))}

      {ready && data.routes.length === 0 && (
        <div className="route-empty">
          ни одно продление не добавляет людей: остановки без обслуживания стоят в кварталах,
          которые уже кто-то обслуживает
        </div>
      )}

      {/* Без этого числа «9 в переборе» читается как потеря: сколько всего
          рассмотрено и почему добавили людей не все — ровно то, ради чего
          ядро отдаёт routes_scanned. */}
      {ready && (
        <div className="att-note att-note-block">
          в переборе рассмотрено <span className="num">{int(data.routes_scanned)}</span>{' '}
          {plural(data.routes_scanned, ['маршрут', 'маршрута', 'маршрутов'])}: добавить кого-то
          продлением способны только <span className="num">{int(data.routes_with_options)}</span> —
          у остальных хвост дотягивается лишь до кварталов, которые уже кто-то обслуживает
        </div>
      )}

      {/* Оговорка обязательна: человек двигает час на шкале и вправе ждать,
          что цифры поедут. Движок считает оборот для рейса, выходящего в
          первый выезд маршрута, и часа просмотра не знает вовсе. */}
      {ready && data.routes.length > 0 && (
        <div className="att-note att-note-block">
          цена — на первый выезд маршрута и на выбранный день; от часа на шкале она не
          зависит. Хвост и новый оборот — оценка по прямой до остановки и по медианной
          скорости города: трассы для дороги, которой ещё нет, в данных нет
        </div>
      )}

      {ready && data.excluded_count > 0 && (
        <div className="att-note att-note-block">
          <span className="num">{data.excluded_count}</span> маршрутов в подбор не идут —
          исходные значения невозможны, считать по ним цену продления нельзя
        </div>
      )}
    </Panel>
  )
}
