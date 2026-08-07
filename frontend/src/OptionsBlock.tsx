/**
 * Варианты продления из перебора ядра. Один блок на два места: карточку
 * маршрута и карточку дыры покрытия — числа там и там означают одно и то же,
 * потому что считает их один и тот же `_evaluate_extension`.
 *
 * Ничего не применяется само. Кнопка кладёт готовый сценарий в историю правок,
 * дальше человек правит руками.
 *
 * Уровень уверенности в цели не прячется (п. 4 задачи). Про остановку из
 * OpenStreetMap без счётчика Яндекса мы не знаем, обслуживает её кто-нибудь
 * или нет: точный порядок остановок восстановлен у 117 направлений из 223.
 * Прирост по такой цели может оказаться завышенным, и об этом надо сказать
 * до того, как человек нажмёт «применить», а не после.
 */

import type { ExtensionOption, OptionConfidence } from './api'
import { Caveat } from './Card'
import { duration, int, km, people } from './format'

const CONFIDENCE_NOTE: Record<OptionConfidence, string> = {
  yandex_confirmed:
    'счётчик маршрутов Яндекса по этой остановке равен нулю и её нет ни в одной ' +
    'восстановленной цепочке — её действительно никто не обслуживает',
  osm_only:
    'остановка из OpenStreetMap, счётчика маршрутов по ней нет. Возможно, её уже ' +
    'кто-то обслуживает: точный порядок остановок восстановлен у 117 направлений из 223. ' +
    'Тогда прирост окажется меньше посчитанного',
}

const CONFIDENCE_LABEL: Record<OptionConfidence, string> = {
  yandex_confirmed: 'никем не обслуживается',
  osm_only: 'OSM, обслуживание неизвестно',
}

export interface OptionsBlockProps {
  options: ExtensionOption[]
  /** Показывать номер маршрута в строке: в карточке дыры маршруты разные. */
  showRoute: boolean
  applied: Set<string>
  onApply: (option: ExtensionOption) => void
}

export function OptionsBlock({
  options,
  showRoute,
  applied,
  onApply,
}: OptionsBlockProps): React.JSX.Element {
  return (
    <ul className="opts">
      {options.map((option) => {
        const key = optionKey(option)
        const done = applied.has(key)
        const fits = option.extra_vehicles <= 0
        return (
          <li className="opt" key={key}>
            <div className="opt-head">
              {showRoute && <span className="shield num">{option.route_num}</span>}
              <span className="opt-target">
                продлить до «{option.stop_name}»
                <span className="opt-dir muted">
                  {' '}
                  · {option.direction === 'fwd' ? 'А → Б' : 'Б → А'} · хвост{' '}
                  <span className="num">{km(option.tail_km)}</span>
                </span>
              </span>
            </div>

            <dl className="opt-rows">
              <div className="opt-row">
                <dt>получат доступ</dt>
                <dd className="num gain">+{people(option.gained_people)}</dd>
              </div>
              <div className="opt-row">
                <dt>время оборота</dt>
                <dd className="num">
                  {duration(option.cycle_time_before_min)}
                  <span className="m-arrow">→</span>
                  {duration(option.cycle_time_after_min)}
                </dd>
              </div>
              <div className="opt-row">
                <dt>требуется машин</dt>
                <dd className="num">
                  {int(option.required_vehicles_before)}
                  <span className="m-arrow">→</span>
                  {int(option.required_vehicles_after)}
                </dd>
              </div>
              <div className="opt-row">
                <dt>выпуск</dt>
                <dd className={fits ? 'num gain' : 'num loss'}>
                  {fits
                    ? 'умещается'
                    : `нужно ещё ${int(option.extra_vehicles)} ${plural(option.extra_vehicles)}`}
                </dd>
              </div>
            </dl>

            {option.confidence && (
              <div className={`opt-conf opt-conf-${option.confidence}`} title={CONFIDENCE_NOTE[option.confidence]}>
                <span className="hinted">{CONFIDENCE_LABEL[option.confidence]}</span>
              </div>
            )}

            {option.chain_recount_people > 0 && (
              <div className="opt-note">
                сверх этого пересчёт цепочки даёт ещё{' '}
                <span className="num">{people(option.chain_recount_people)}</span> — их приносит не
                продление, а то, что ядро считает обслуженными все остановки правленого маршрута
              </div>
            )}

            <button
              className={`btn btn-primary opt-apply${done ? ' is-applied' : ''}`}
              disabled={done}
              onClick={() => onApply(option)}
            >
              {done ? '✓ добавлено в сценарий' : 'применить как сценарий'}
            </button>
          </li>
        )
      })}
    </ul>
  )
}

export function optionKey(option: ExtensionOption): string {
  return `${option.route_num}:${option.direction}:${option.stop_id}`
}

/** Пустой результат — тоже результат: сказать, что проверено и почему ничего. */
export function NoOptions({
  checked,
  offHousing,
  radiusM,
  minBuildings,
  maxExtra,
  reason,
}: {
  checked: number
  offHousing?: number
  radiusM?: number
  minBuildings?: number
  maxExtra: number
  reason?: string | null
}): React.JSX.Element {
  return (
    <Caveat>
      {reason ? (
        <>{reason}.</>
      ) : (
        <>
          вариантов нет: проверено <span className="num">{int(checked)}</span> остановок, которые
          сейчас никто не обслуживает. Ни одно продление не добавляет людей сверх пересчёта цепочки
          или требует больше <span className="num">{int(maxExtra)}</span> машин сверх нынешнего
          выпуска
        </>
      )}
      {offHousing != null && offHousing > 0 && radiusM != null && (
        <>
          {' '}
          Ещё <span className="num">{int(offHousing)}</span> остановок отсеяно: вокруг них меньше{' '}
          <span className="num">{int(minBuildings ?? 0)}</span> жилых домов в{' '}
          <span className="num">{int(radiusM)}</span> м.
        </>
      )}
    </Caveat>
  )
}

function plural(count: number): string {
  const n = Math.abs(count) % 100
  const n1 = n % 10
  if (n > 10 && n < 20) return 'машин'
  if (n1 > 1 && n1 < 5) return 'машины'
  if (n1 === 1) return 'машину'
  return 'машин'
}
