/** Оболочка плавающей карточки (§12): 320 px, слева, выезд 160 мс. */

export interface CardProps {
  title: React.ReactNode
  subtitle?: React.ReactNode
  onClose: () => void
  children: React.ReactNode
}

export function Card({ title, subtitle, onClose, children }: CardProps): React.JSX.Element {
  return (
    <div className="card" role="dialog" aria-label={typeof title === 'string' ? title : undefined}>
      <div className="card-head">
        <div className="card-title">{title}</div>
        <button className="card-close" onClick={onClose} aria-label="закрыть карточку">
          ✕
        </button>
      </div>
      {subtitle && <div className="card-sub">{subtitle}</div>}
      <div className="card-body">{children}</div>
    </div>
  )
}

export function Rows({ items }: { items: [string, React.ReactNode][] }): React.JSX.Element {
  return (
    <dl className="rows">
      {items.map(([label, value]) => (
        <div className="row" key={label}>
          <dt>{label}</dt>
          <dd className="num">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

/** Оговорка о том, чего в данных нет. Не извиняется, а говорит, что случилось. */
export function Caveat({ children }: { children: React.ReactNode }): React.JSX.Element {
  return <p className="caveat">{children}</p>
}
