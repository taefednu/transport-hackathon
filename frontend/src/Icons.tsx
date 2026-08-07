/**
 * Иконки дока. Нарисованы здесь, а не взяты из набора: набор тянет за собой
 * чужую сетку и чужую толщину линии, а тут их всего восемь.
 *
 * Все — 16×16, штрих 1.4, цвет наследуется от кнопки.
 */

const box = {
  width: 16,
  height: 16,
  viewBox: '0 0 16 16',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.4,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
}

/** Выбор: обычный курсор. */
export function IconSelect(): React.JSX.Element {
  return (
    <svg {...box}>
      <path d="M3 2.4l8.2 5.2-3.6.7-1.5 3.4z" />
    </svg>
  )
}

/** Правка трассы: линия с ручками на концах. */
export function IconEdit(): React.JSX.Element {
  return (
    <svg {...box}>
      <path d="M4.4 11.6c1.6-.4 1.4-3 3.2-3.6s2.4 1 4-1.6" />
      <circle cx="3" cy="12.2" r="1.6" />
      <circle cx="13" cy="4.4" r="1.6" />
    </svg>
  )
}

/** Вставка остановки: точка на линии с плюсом. */
export function IconInsert(): React.JSX.Element {
  return (
    <svg {...box}>
      <path d="M2 11.5h3.2M10.8 11.5H14" />
      <circle cx="8" cy="11.5" r="1.7" />
      <path d="M8 1.8v4M6 3.8h4" />
    </svg>
  )
}

/** Слой населения: гексагон. */
export function IconHex(): React.JSX.Element {
  return (
    <svg {...box}>
      <path d="M8 1.8l5 2.9v5.8L8 13.4 3 10.5V4.7z" />
    </svg>
  )
}

/** Дыры покрытия: пунктирный контур — то, чего нет. */
export function IconHoles(): React.JSX.Element {
  return (
    <svg {...box} strokeDasharray="2.2 2">
      <circle cx="8" cy="8" r="5.4" />
    </svg>
  )
}

export function IconUndo(): React.JSX.Element {
  return (
    <svg {...box}>
      <path d="M3 7.2h7.2a3 3 0 010 6H6.4" />
      <path d="M5.6 4.4L2.8 7.2l2.8 2.8" />
    </svg>
  )
}

export function IconRedo(): React.JSX.Element {
  return (
    <svg {...box}>
      <path d="M13 7.2H5.8a3 3 0 000 6h3.8" />
      <path d="M10.4 4.4l2.8 2.8-2.8 2.8" />
    </svg>
  )
}

/** Режим демонстрации: стрелки в углы. */
export function IconExpand(): React.JSX.Element {
  return (
    <svg {...box}>
      <path d="M6.2 2.6H2.6v3.6M9.8 2.6h3.6v3.6M13.4 9.8v3.6H9.8M2.6 9.8v3.6h3.6" />
    </svg>
  )
}
