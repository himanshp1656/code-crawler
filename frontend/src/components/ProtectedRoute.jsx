import { Navigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

export function ProtectedRoute({ children }) {
  const { user } = useAuth()
  if (user === undefined) return <div className="loading">Loading…</div>
  if (!user) return <Navigate to="/login" replace />
  return children
}
