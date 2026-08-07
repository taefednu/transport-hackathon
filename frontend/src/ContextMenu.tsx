/**
 * Контекстное меню правой кнопки.
 *
 * Здесь живут те же действия, что и на клавиатуре: §8.3 прямо требует пункт
 * «обрезать здесь» для тех, кто не знает про Shift. Ничего своего меню не
 * делает — только называет словами то, что иначе доступно горячей клавишей.
 */

export interface MenuItem {
  key: string
  label: string
  hint?: string
  disabled?: boolean
  run: () => void
}

export function ContextMenu({
  at,
  items,
  onClose,
}: {
  at: { x: number; y: number }
  items: MenuItem[]
  onClose: () => void
}): React.JSX.Element | null {
  if (items.length === 0) return null
  return (
    <div className="menu" style={{ left: at.x, top: at.y }} onMouseLeave={onClose}>
      {items.map((item) => (
        <button
          key={item.key}
          className="menu-item"
          disabled={item.disabled}
          onClick={() => {
            item.run()
            onClose()
          }}
        >
          <span className="menu-label">{item.label}</span>
          {item.hint && <span className="menu-hint num">{item.hint}</span>}
        </button>
      ))}
    </div>
  )
}
