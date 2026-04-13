export function Alert({ type = 'error', children }) {
  if (!children) return null
  return (
    <div className={type === 'success' ? 'alert-success' : 'alert-error'}>
      {children}
    </div>
  )
}
