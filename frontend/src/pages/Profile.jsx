import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api } from '../lib/api'
import { Spinner } from '../components/Spinner'
import { EmptyState } from '../components/EmptyState'

export default function Profile() {
  const { handle } = useParams()
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api.profile(handle)
      .then(setData)
      .catch((e) => setError(e.status === 404 ? 'Profile not found.' : e.message))
  }, [handle])

  const tenant = data?.tenant
  const repos = data?.repos || []
  const isOrg = tenant?.account_type === 'organization'

  return (
    <>
      {/* Navbar */}
      <nav className="navbar">
        <div className="navbar-brand">
          <Link to="/" style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--green)', textDecoration: 'none' }}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="16 18 22 12 16 6"/>
              <polyline points="8 6 2 12 8 18"/>
            </svg>
            Code Crawler
          </Link>
        </div>
        <div className="navbar-right">
          <Link to="/login" className="btn btn-ghost">Sign in</Link>
        </div>
      </nav>

      <div style={{ maxWidth: 720, margin: '0 auto', padding: 24 }}>
        {error && (
          <div className="alert-error">{error}</div>
        )}
        {!data && !error && <Spinner />}

        {tenant && (
          <>
            {/* Profile header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 24 }}>
              <div style={{
                width: 64, height: 64, borderRadius: '50%',
                background: 'var(--surface)',
                border: '2px solid var(--border)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                flexShrink: 0,
              }}>
                {isOrg ? (
                  <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--purple)" strokeWidth="1.5" style={{ opacity: 0.7 }}>
                    <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>
                    <circle cx="9" cy="7" r="4"/>
                    <path d="M22 21v-2a4 4 0 0 0-3-3.87"/>
                    <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
                  </svg>
                ) : (
                  <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--blue)" strokeWidth="1.5" style={{ opacity: 0.7 }}>
                    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                    <circle cx="12" cy="7" r="4"/>
                  </svg>
                )}
              </div>
              <div>
                <h1 style={{ fontSize: 22, fontWeight: 700, lineHeight: 1.2 }}>{tenant.tenant_name}</h1>
                <div style={{ fontSize: 14, color: 'var(--text-dim)', fontFamily: "'JetBrains Mono',monospace" }}>
                  {tenant.tenant_id}
                </div>
                <span style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 4,
                  marginTop: 6,
                  padding: '3px 10px',
                  borderRadius: 20,
                  fontSize: 11,
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '0.5px',
                  background: isOrg ? 'rgba(168,85,247,0.12)' : 'rgba(59,130,246,0.12)',
                  color: isOrg ? 'var(--purple)' : 'var(--blue)',
                  border: `1px solid ${isOrg ? 'rgba(168,85,247,0.25)' : 'rgba(59,130,246,0.25)'}`,
                }}>
                  {isOrg ? (
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>
                      <circle cx="9" cy="7" r="4"/>
                      <path d="M22 21v-2a4 4 0 0 0-3-3.87"/>
                      <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
                    </svg>
                  ) : (
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                      <circle cx="12" cy="7" r="4"/>
                    </svg>
                  )}
                  {isOrg ? 'Organization' : 'Personal'}
                </span>
              </div>
            </div>

            <div className="card">
              <div className="card-title">Repositories</div>
              <div style={{ fontSize: 13, color: 'var(--text-dim)', marginBottom: 12 }}>
                {repos.length} {repos.length === 1 ? 'repository' : 'repositories'} crawled
              </div>

              <div className="list">
                {repos.length === 0 && (
                  <EmptyState
                    icon={
                      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--text-dim)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.5 }}>
                        <rect x="16" y="16" width="6" height="6" rx="1"/>
                        <rect x="2" y="16" width="6" height="6" rx="1"/>
                        <rect x="9" y="2" width="6" height="6" rx="1"/>
                        <path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3"/>
                        <path d="M12 12V8"/>
                      </svg>
                    }
                  >
                    No repositories crawled yet.
                  </EmptyState>
                )}

                {repos.map((r, i) => (
                  <div className="list-item" key={i}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--green)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2c-.3 1.15-.3 2.35 0 3.5A5.403 5.403 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65S8.93 17.38 9 18v4"/>
                        <path d="M9 18c-4.51 2-5-2-7-2"/>
                      </svg>
                      <div>
                        <div style={{ fontWeight: 600, color: 'var(--green)' }}>{r.repo}</div>
                        <div className="meta"><span className="mono">{r.branch}</span></div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </>
  )
}
