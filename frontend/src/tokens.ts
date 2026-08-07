/** Палитра и размеры из qatnov_map_spec.md. Числа руками нигде больше не пишем. */

export const C = {
  // подложка (§2)
  bg: '#F7F6F3',
  water: '#DCE5EA',
  green: '#E4E9DF',
  built: '#F0EEEA',
  roadMajor: '#E2DFD9',
  roadMinor: '#EDEBE7',
  building: '#EFEDE8',
  baseLabel: '#9A968E',

  // данные (§3, §4)
  routeIdle: '#8C8880',
  routeHover: '#5A5650',
  selected: '#0F5FA6',
  compare: '#C97A16',
  removed: '#C2563F',
  added: '#0E8A73',
  warn: '#E9A93C',
  // проверено validate_palette: с `removed` ΔE 24.7 (норма) / 10.2 (дейтеранопия)
  covered: '#009B8A',
  // контур «в доступе к частой сети»: тот же тон, что заливка плотности,
  // сливался с ней (ΔE 8.2 при протанопии) — см. шапку hexLayer.ts
  frequent: '#E9A93C',

  // интерфейс
  ink: '#3D3A35',
  inkSoft: '#6B665E',
  card: '#FFFFFF',
  hairline: '#DEDBD4',
} as const

export const W = {
  routeIdle: 1.5,
  routeHover: 2.5,
  routeSelected: 3.5,
  routeRemoved: 2,
} as const

export const O = {
  routeIdle: 0.55,
  routeDimmed: 0.15,
  routeHover: 0.9,
  routeSelected: 1,
  stopDimmed: 0.25,
  hexDimmed: 0.2,
} as const

/** §3.2: шаг разведения параллельных линий, экранные пиксели. */
export const PARALLEL_STEP_PX = 3.5
/** §3.2: ниже этого зума линии намеренно сливаются. */
export const PARALLEL_MIN_ZOOM = 14

/** §14 — длительности анимаций, мс. */
export const T = {
  hover: 120,
  dim: 140,
  card: 160,
  numbers: 220,
  hour: 150,
  flyTo: 600,
} as const

/** §1 — радиусы попадания. */
export const HIT = { stop: 12, line: 8 } as const
