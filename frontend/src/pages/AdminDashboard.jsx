import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { NavBar } from '../components/NavBar'
import { Alert } from '../components/Alert'
import { Spinner } from '../components/Spinner'

export default function AdminDashboard() {
  const nav = useNavigate()
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [tenantId, setTenantId] = useState('')
  const [tenantName, setTenantName] = useState('')
  const [tenantType, setTenantType] = useState('personal')
  const [uTenantId, setUTenantId] = useState('')
  const [uUsername, setUUsername] = useState('')
  const [uPassword, setUPassword] = useState('')
  const [tenantMsg, setTenantMsg] = useState({ type: '', text: '' })
  const [userMsg, setUserMsg] = useState({ type: '', text: '' })

  const reload = () => {
    api.adminTenants()
      .then(setData)
      .catch((e) => {
        if (e.status === 401) nav('/admin/login')
        else setError(e.message)
      })
  }

  useEffect(() => { reload() }, [])

  const createTenant = async (e) => {
    e.preventDefault()
    setTenantMsg({ type: '', text: '' })
    try {
      await api.adminCreateTenant(tenantId, tenantName)
      setTenantMsg({ type: 'success', text: 'Tenant created.' })
      setTenantId('')
      setTenantName('')
      reload()
    } catch (err) {
      setTenantMsg({ type: 'error', text: err.message })
    }
  }

  const createUser = async (e) => {
    e.preventDefault()
    setUserMsg({ type: '', text: '' })
    try {
      await api.adminCreateUser(uTenantId, uUsername, uPassword)
      setUserMsg({ type: 'success', text: 'User created.' })
      setUUsername('')
      setUPassword('')
      reload()
    } catch (err) {
      setUserMsg({ type: 'error', text: err.message })
    }
  }

  const tenants = data?.tenants || []

  return (
    <>
      <NavBar admin />
      <div className="page-grid">
        {/* Left: Forms */}
        <div className="list">
          {/* Create tenant */}
          <div className="card">
            <div className="card-title">Create tenant</div>
            <div className="card-hint">Each tenant gets isolated data.</div>
            <form onSubmit={createTenant}>
              <div className="field">
                <label htmlFor="tid">Tenant ID</label>
                <input id="tid" value={tenantId} onChange={e => setTenantId(e.target.value)} placeholder="acme" required />
              </div>
              <div className="field">
                <label htmlFor="tname">Tenant name</label>
                <input id="tname" value={tenantName} onChange={e => setTenantName(e.target.value)} placeholder="Acme Inc" required />
              </div>
              <div className="field">
                <label htmlFor="atype">Account type</label>
                <select id="atype" value={tenantType} onChange={e => setTenantType(e.target.value)}>
                  <option value="personal">Personal</option>
                  <option value="organization">Organization</option>
                </select>
              </div>
              <button className="btn btn-blue" type="submit">Create tenant</button>
            </form>
            {tenantMsg.text && <Alert type={tenantMsg.type}>{tenantMsg.text}</Alert>}
          </div>

          {/* Create user */}
          <div className="card">
            <div className="card-title">Create user</div>
            <div className="card-hint">Assign a login to a tenant.</div>
            <form onSubmit={createUser}>
              <div className="field">
                <label htmlFor="utenant">Tenant</label>
                <select id="utenant" value={uTenantId} onChange={e => setUTenantId(e.target.value)} required>
                  <option value="">-- select tenant --</option>
                  {tenants.map(t => (
                    <option key={t.tenant_id} value={t.tenant_id}>
                      {t.tenant_id} — {t.tenant_name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="uname">Username</label>
                <input id="uname" value={uUsername} onChange={e => setUUsername(e.target.value)} placeholder="alice@example.com" required />
              </div>
              <div className="field">
                <label htmlFor="upass">Password</label>
                <input id="upass" type="password" value={uPassword} onChange={e => setUPassword(e.target.value)} required />
              </div>
              <button className="btn btn-blue" type="submit">Create user</button>
            </form>
            {userMsg.text && <Alert type={userMsg.type}>{userMsg.text}</Alert>}
          </div>
        </div>

        {/* Right: Tenant list */}
        <div className="list">
          {error && <Alert type="error">{error}</Alert>}
          {!data && !error && <Spinner />}

          {data && tenants.length === 0 && (
            <div className="card empty-state">No tenants yet. Create one to get started.</div>
          )}

          {tenants.map(t => {
            const users = t.users || []
            const isOrg = t.account_type === 'organization'
            return (
              <div key={t.tenant_id} style={{
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius)',
                background: 'rgba(255,255,255,0.02)',
                padding: 16,
              }}>
                <h3 className="mono" style={{ fontSize: 14, color: 'var(--green)', marginBottom: 4 }}>{t.tenant_id}</h3>
                <div className="meta">{t.tenant_name}</div>
                <span style={{
                  display: 'inline-block',
                  marginTop: 4,
                  padding: '2px 8px',
                  borderRadius: 12,
                  fontSize: 10,
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '0.5px',
                  background: isOrg ? 'rgba(168,85,247,0.12)' : 'rgba(59,130,246,0.12)',
                  color: isOrg ? 'var(--purple)' : 'var(--blue)',
                  border: `1px solid ${isOrg ? 'rgba(168,85,247,0.25)' : 'rgba(59,130,246,0.25)'}`,
                }}>
                  {t.account_type || 'personal'}
                </span>

                {users.length === 0 ? (
                  <div className="meta" style={{ marginTop: 8 }}>No users yet</div>
                ) : (
                  users.map(u => (
                    <div key={u.username} style={{
                      marginTop: 8,
                      fontSize: 13,
                      display: 'flex',
                      alignItems: 'baseline',
                      justifyContent: 'space-between',
                      gap: 10,
                      padding: '6px 0',
                      borderTop: '1px solid rgba(255,255,255,0.04)',
                    }}>
                      <span className="mono">{u.username}</span>
                      <span style={{ fontSize: 11, color: u.is_active ? 'var(--green)' : 'var(--red)' }}>
                        {u.is_active ? 'active' : 'inactive'}
                      </span>
                    </div>
                  ))
                )}
              </div>
            )
          })}
        </div>
      </div>
    </>
  )
}
