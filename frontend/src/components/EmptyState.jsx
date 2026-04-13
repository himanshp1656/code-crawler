export function EmptyState({ icon, children }) {
  return (
    <div className="empty-state">
      {icon && <div style={{ marginBottom: 10 }}>{icon}</div>}
      {children}
    </div>
  )
}
