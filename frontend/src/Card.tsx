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

/**
 * Строки «подпись — значение». Третьим элементом можно дать пояснение: оно
 * появляется при наведении, а подпись получает пунктир — иначе о подсказке
 * никто не узнает.
 */
export type Row = [string, React.ReactNode] | [string, React.ReactNode, string]

export function Rows({ items }: { items: Row[] }): React.JSX.Element {
  return (
    <dl className="rows">
      {items.map(([label, value, hint]) => (
        <div className="row" key={label}>
          <dt>
            {hint ? (
              <span className="hinted" title={hint}>
                {label}
              </span>
            ) : (
              label
            )}
          </dt>
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
