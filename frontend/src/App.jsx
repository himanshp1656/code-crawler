import { BrowserRouter, Navigate, Route, Routes, useParams } from 'react-router-dom'
import { AuthProvider, useAuth } from './hooks/useAuth'

import Login from './pages/Login'
import Signup from './pages/Signup'
import Dashboard from './pages/Dashboard'
import LineageUI from './pages/LineageUI'
import Asset from './pages/Asset'
import Changes from './pages/Changes'
import BranchCompare from './pages/BranchCompare'
import Profile from './pages/Profile'
import AdminLogin from './pages/AdminLogin'
import AdminDashboard from './pages/AdminDashboard'

// Guards login + ensures the :tenant in the URL matches the logged-in user.
function TenantRoute({ children }) {
  const { user } = useAuth()
  const { tenant } = useParams()

  if (user === undefined) return <div className="loading">Loading…</div>
  if (!user) return <Navigate to="/login" replace />
  // If someone visits another tenant's URL, redirect to their own.
  if (user.tenant_id !== tenant) return <Navigate to={`/${user.tenant_id}/dashboard`} replace />
  return children
}

// Redirect / to the logged-in user's dashboard, or /login if not authed.
function RootRedirect() {
  const { user } = useAuth()
  if (user === undefined) return <div className="loading">Loading…</div>
  if (!user) return <Navigate to="/login" replace />
  return <Navigate to={`/${user.tenant_id}/dashboard`} replace />
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<RootRedirect />} />
          <Route path="/login" element={<Login />} />
          <Route path="/signup" element={<Signup />} />

          {/* Tenant-scoped pages */}
          <Route path="/:tenant/dashboard"     element={<TenantRoute><Dashboard /></TenantRoute>} />
          <Route path="/:tenant/lineage"       element={<TenantRoute><LineageUI /></TenantRoute>} />
          <Route path="/:tenant/asset"         element={<TenantRoute><Asset /></TenantRoute>} />
          <Route path="/:tenant/changes"       element={<TenantRoute><Changes /></TenantRoute>} />
          <Route path="/:tenant/compare"       element={<TenantRoute><BranchCompare /></TenantRoute>} />

          <Route path="/admin/login" element={<AdminLogin />} />
          <Route path="/admin"       element={<AdminDashboard />} />

          {/* Public profile — must be last */}
          <Route path="/:handle" element={<Profile />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
