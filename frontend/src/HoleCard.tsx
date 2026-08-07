/**
 * Карточка ячейки вне пешей доступности. Открывается кликом по гексагону.
 *
 * Показывает не только «сколько людей и как далеко до остановки», но и что с
 * этим делать: какой маршрут дотянуть и какой ценой. Когда вариантов нет,
 * названа причина — у неё их две, и они разные по смыслу. Рядом может не быть
 * ни одной остановки, про которую известно, что её никто не обслуживает: из
 * 149 дыр таких 139, и продлевать там некуда — нужна новая остановка, а её
 * ядро не проектирует. Либо остановка есть, но ни один маршрут не кончается
 * достаточно близко.
 */

import type { ExtensionOption, HoleOptions } from './api'
import { Card, Caveat, Rows } from './Card'
import { int, people } from './format'
import { NoOptions, OptionsBlock } from './OptionsBlock'
import type { Loaded } from './useOptions'

export interface HoleCardProps {
  h3: string
  /** Что известно про ячейку из уже загруженного слоя — до ответа ядра. */
  hex: { pop: number; walkMin: number; covered: boolean; frequent: boolean }
  /** Ближайшая обслуживаемая остановка, если ячейка попала в список дыр. */
  nearest: { name: string | null; distanceM: number | null } | null
  options: Loaded<HoleOptions>
  appliedOptions: Set<string>
  onApplyOption: (option: ExtensionOption) => void
  onClose: () => void
}

export function HoleCard({
  h3,
  hex,
  nearest,
  options,
  appliedOptions,
  onApplyOption,
  onClose,
}: HoleCardProps): React.JSX.Element {
  const state = hex.frequent
    ? 'в доступе к частой сети'
    : hex.covered
      ? 'в пешей доступности'
      : 'вне пешей доступности'

  return (
    <Card
      title={<span className="card-name">ячейка {state}</span>}
      subtitle={<span className="muted num">H3 r8 · {h3}</span>}
      onClose={onClose}
    >
      <Rows
        items={[
          ['людей в ячейке', people(hex.pop)],
          [
            'до ближайшей остановки',
            hex.walkMin > 0 ? `${hex.walkMin.toFixed(0)} мин пешком` : '—',
            'Время по пешеходной сети до ближайшей остановки, которую обслуживает хотя бы один маршрут.',
          ],
          ...(nearest?.name
            ? ([
                [
                  'ближайшая обслуживаемая',
                  nearest.distanceM != null
                    ? `${nearest.name} · ${int(nearest.distanceM)} м`
                    : nearest.name,
                ],
              ] as [string, string][])
            : []),
        ]}
      />

      <div className="card-section">что можно сделать</div>
      {options.loading && <div className="muted">ядро подбирает маршрут…</div>}
      {options.error && <Caveat>подбор не сделан: {options.error}</Caveat>}
      {options.data &&
        (options.data.options.length > 0 ? (
          <>
            <OptionsBlock
              options={options.data.options}
              showRoute
              applied={appliedOptions}
              onApply={onApplyOption}
            />
            <div className="opt-note">
              проверено маршрутов: <span className="num">{int(options.data.routes_checked)}</span>,
              целей рядом: <span className="num">{int(options.data.targets_nearby)}</span>
            </div>
          </>
        ) : (
          <NoOptions
            checked={options.data.routes_checked}
            maxExtra={options.data.max_extra_vehicles}
            reason={options.data.reason}
          />
        ))}
    </Card>
  )
}
