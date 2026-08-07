/**
 * Оболочка сворачиваемой панели. Одна на все плавающие блоки: заголовок —
 * он же кнопка, тело прокручивается само, панель никогда не выдавливает
 * соседей за край экрана.
 */

export interface PanelProps {
  /** Добавка к классу оболочки: панель со своим состоянием (пересчёт идёт). */
  className?: string
  title: React.ReactNode
  /** Правый край шапки: счётчик, статус — то, что видно и в свёрнутом виде. */
  aside?: React.ReactNode
  open: boolean
  onToggle: () => void
  children: React.ReactNode
  /** Подвал вне прокручиваемого тела: поле ввода, кнопка. */
  foot?: React.ReactNode
}

export function Panel({
  className,
  title,
  aside,
  open,
  onToggle,
  children,
  foot,
}: PanelProps): React.JSX.Element {
  return (
    <div className={`panel${open ? ' is-open' : ''}${className ? ` ${className}` : ''}`}>
      <button className="panel-head" aria-expanded={open} onClick={onToggle}>
        <span className={`panel-chevron${open ? ' is-open' : ''}`}>▶</span>
        <span className="panel-head-title">{title}</span>
        {aside}
      </button>
      {open && (
        <>
          <div className="panel-body">{children}</div>
          {foot}
        </>
      )}
    </div>
  )
}
