import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

const CodeIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="16 18 22 12 16 6"/>
    <polyline points="8 6 2 12 8 18"/>
  </svg>
)

const ShieldIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
  </svg>
)

export function NavBar({
  admin = false,
  repo,
  branch,
  changesCount = 0,
  breadcrumbs,
  right,
  customBrand,
}) {
  const { user, logout } = useAuth()
  const nav = useNavigate()
  const tenant = user?.tenant_id || ''

  const handleLogout = async () => {
    await logout()
    nav('/login')
  }

  if (admin) {
    return (
      <nav className="navbar">
        <div className="navbar-brand">
          <ShieldIcon />
          Admin Portal
        </div>
        <div className="navbar-right">
          <Link to="/login" className="btn btn-ghost" style={{ fontSize: 12 }}>User portal</Link>
          <button className="btn btn-ghost" onClick={handleLogout}>Logout</button>
        </div>
      </nav>
    )
  }

  const dashboardPath = `/${tenant}/dashboard`

  return (
    <nav className="navbar">
      <div className="navbar-brand">
        {customBrand ? customBrand : breadcrumbs ? (
          <>
            <Link to={dashboardPath} style={{ color: 'var(--text-dim)', textDecoration: 'none', fontSize: 13, display: 'flex', alignItems: 'center', gap: 4 }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ verticalAlign: -2 }}>
                <path d="m15 18-6-6 6-6"/>
              </svg>
              Dashboard
            </Link>
            {breadcrumbs.map((b, i) => (
              <span key={i} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ color: 'var(--border)' }}>/</span>
                {b.to
                  ? <Link to={b.to} style={{ color: 'var(--text-dim)', textDecoration: 'none', fontSize: 13 }}>{b.label}</Link>
                  : <span style={{ fontSize: 14 }}>{b.label}</span>
                }
              </span>
            ))}
          </>
        ) : (
          <>
            <CodeIcon />
            <Link to={dashboardPath} style={{ color: 'var(--accent)', textDecoration: 'none' }}>Code Crawler</Link>
            {tenant && (
              <span className="mono" style={{ fontSize: 12, color: 'var(--text-dim)', fontWeight: 400 }}>
                {tenant}
              </span>
            )}
          </>
        )}
      </div>
      <div className="navbar-right">
        {right}
        {repo && branch && changesCount > 0 && (
          <Link
            to={`/${tenant}/changes?repo=${encodeURIComponent(repo)}&branch=${encodeURIComponent(branch)}`}
            className="btn-ghost"
            style={{
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 12,
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '4px 10px',
              border: '1px solid var(--border)',
              borderRadius: 6,
              color: 'var(--text)',
              textDecoration: 'none',
            }}
          >
            <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: 'var(--accent)' }} />
            {changesCount} Changes
          </Link>
        )}
        {user && (
          <>
            <Link to={`/${tenant}`} className="btn btn-ghost" style={{ fontSize: 12 }}>Profile</Link>
            <button className="btn btn-ghost" onClick={handleLogout}>Logout</button>
          </>
        )}
      </div>
    </nav>
  )
}
