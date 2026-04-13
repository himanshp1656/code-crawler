import { useEffect, useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { useAuth } from '../hooks/useAuth'
import { Alert } from '../components/Alert'

export default function Signup() {
  const { user, reload } = useAuth()
  const nav = useNavigate()
  const [accountType, setAccountType] = useState('personal')
  const [handle, setHandle] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [handleStatus, setHandleStatus] = useState(null) // null | 'checking' | 'ok' | string (error)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  if (user) return <Navigate to={`/${user.tenant_id}/dashboard`} replace />

  // Live handle check with debounce
  useEffect(() => {
    const val = handle.toLowerCase().replace(/[^a-z0-9-]/g, '')
    if (val !== handle) setHandle(val)
    if (val.length < 3) { setHandleStatus(null); return }
    setHandleStatus('checking')
    const t = setTimeout(async () => {
      try {
        const res = await api.checkHandle(val)
        setHandleStatus(res.available ? 'ok' : (res.errors ? res.errors.join(' ') : 'Not available'))
      } catch {
        setHandleStatus(null)
      }
    }, 400)
    return () => clearTimeout(t)
  }, [handle])

  const submit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      await api.signup({
        handle,
        display_name: displayName,
        username,
        password,
        account_type: accountType,
      })
      await reload()
      nav(`/${handle}/dashboard`)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const handleStatusEl = () => {
    if (!handleStatus) return null
    if (handleStatus === 'checking') return <span style={{ color: 'var(--text-dim)' }}>Checking...</span>
    if (handleStatus === 'ok') return <span style={{ color: 'var(--green)' }}>Available</span>
    return <span style={{ color: 'var(--red)' }}>{handleStatus}</span>
  }

  return (
    <div className="center-page">
      <div className="card center-card">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="var(--green)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="16 18 22 12 16 6"/>
            <polyline points="8 6 2 12 8 18"/>
          </svg>
          <span style={{ fontSize: 18, fontWeight: 700, color: 'var(--green)' }}>Create your account</span>
        </div>
        <p style={{ fontSize: 13, color: 'var(--text-dim)', marginBottom: 18 }}>
          Pick a handle for your public profile URL.
        </p>

        <form onSubmit={submit}>
          {/* Account type toggle */}
          <div className="field">
            <label>Account type</label>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 6 }}>
              {/* Personal */}
              <div
                onClick={() => setAccountType('personal')}
                style={{
                  padding: 12,
                  borderRadius: 'var(--radius-sm)',
                  border: `1px solid ${accountType === 'personal' ? 'var(--green)' : 'var(--border)'}`,
                  background: accountType === 'personal' ? 'rgba(34,197,94,0.08)' : 'var(--input-bg)',
                  cursor: 'pointer',
                  textAlign: 'center',
                  transition: 'all 0.2s',
                }}
              >
                <div style={{ marginBottom: 4 }}>
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                    <circle cx="12" cy="7" r="4"/>
                  </svg>
                </div>
                <div style={{ fontSize: 13, fontWeight: 600 }}>Personal</div>
                <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 2 }}>For individual use</div>
              </div>
              {/* Organization */}
              <div
                onClick={() => setAccountType('organization')}
                style={{
                  padding: 12,
                  borderRadius: 'var(--radius-sm)',
                  border: `1px solid ${accountType === 'organization' ? 'var(--green)' : 'var(--border)'}`,
                  background: accountType === 'organization' ? 'rgba(34,197,94,0.08)' : 'var(--input-bg)',
                  cursor: 'pointer',
                  textAlign: 'center',
                  transition: 'all 0.2s',
                }}
              >
                <div style={{ marginBottom: 4 }}>
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>
                    <circle cx="9" cy="7" r="4"/>
                    <path d="M22 21v-2a4 4 0 0 0-3-3.87"/>
                    <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
                  </svg>
                </div>
                <div style={{ fontSize: 13, fontWeight: 600 }}>Organization</div>
                <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 2 }}>For teams &amp; companies</div>
              </div>
            </div>
          </div>

          {/* Handle */}
          <div className="field">
            <label htmlFor="handle">Handle</label>
            <input
              id="handle"
              value={handle}
              onChange={e => setHandle(e.target.value)}
              autoComplete="off"
              required
              placeholder="my-handle"
              minLength={3}
              maxLength={39}
            />
            <div style={{ marginTop: 6, fontSize: 12, minHeight: 18 }}>
              {handleStatusEl()}
            </div>
            {handle.length >= 3 && (
              <div style={{
                marginTop: 8,
                padding: '8px 12px',
                borderRadius: 'var(--radius-sm)',
                background: 'rgba(34,197,94,0.06)',
                border: '1px solid rgba(34,197,94,0.15)',
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: 13,
                color: 'var(--green)',
              }}>
                {window.location.origin}/{handle}
              </div>
            )}
          </div>

          <div className="field">
            <label htmlFor="display_name">Display name</label>
            <input
              id="display_name"
              value={displayName}
              onChange={e => setDisplayName(e.target.value)}
              required
              placeholder="Acme Inc"
            />
          </div>

          <div className="field">
            <label htmlFor="username">Email / Username</label>
            <input
              id="username"
              value={username}
              onChange={e => setUsername(e.target.value)}
              required
              autoComplete="username"
              placeholder="you@company.com"
            />
          </div>

          <div className="field">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              required
              autoComplete="new-password"
              minLength={8}
            />
          </div>

          <button className="btn btn-green" type="submit" disabled={loading}>
            {loading ? 'Creating...' : 'Create account'}
          </button>
        </form>

        <Alert type="error">{error}</Alert>

        <p style={{ textAlign: 'center', marginTop: 16, fontSize: 12, color: 'var(--text-dim)' }}>
          Already have an account? <Link to="/login">Sign in</Link>
        </p>
      </div>
    </div>
  )
}
