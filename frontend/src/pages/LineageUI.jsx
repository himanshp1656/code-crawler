import { useEffect, useState, useMemo, useRef, useCallback } from 'react'
import { useSearchParams, useNavigate, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import { NavBar } from '../components/NavBar'
import { Spinner } from '../components/Spinner'

const PAGE_SIZE = 100

export default function LineageUI() {
  const [params] = useSearchParams()
  const repo = params.get('repo') || ''
  const branch = params.get('branch') || 'main'
  const nav = useNavigate()
  const { tenant } = useParams()

  const [view, setView] = useState('functions') // 'functions' | 'classes'

  // --- Function view state (server-paginated) ---
  const [nodes, setNodes] = useState(null)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('connected')
  const [sort, setSort] = useState('connections')
  const [error, setError] = useState('')
  const [stats, setStats] = useState({ total: 0, connected: 0, isolated: 0 })
  const [filteredTotal, setFilteredTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const nodesRef = useRef(null)

  // --- Class view state ---
  const [classData, setClassData] = useState(null)
  const [classError, setClassError] = useState('')
  const [classQuery, setClassQuery] = useState('')

  const changesCount = (() => {
    try {
      const raw = sessionStorage.getItem('code-crawler-changes')
      if (!raw) return 0
      const all = JSON.parse(raw)
      return Object.values(all).filter(ch => {
        const chRepo   = ch.repo   || ''
        const chBranch = ch.branch || 'main'
        return chRepo === repo && chBranch === branch
      }).length
    } catch { return 0 }
  })()

  // Debounced search value
  const [debouncedQuery, setDebouncedQuery] = useState('')
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query), 300)
    return () => clearTimeout(t)
  }, [query])

  // Single effect: fetch page 1 whenever any filter param changes
  useEffect(() => {
    if (!repo) return
    let cancelled = false
    nodesRef.current = null
    setNodes(null)
    setLoading(true)
    api.lineageData(repo, branch, { offset: 0, limit: PAGE_SIZE, search: debouncedQuery, filter, sort })
      .then(data => {
        if (cancelled) return
        const ns = data.nodes || []
        nodesRef.current = ns
        setNodes(ns)
        setStats({ total: data.total, connected: data.connected, isolated: data.isolated })
        setFilteredTotal(data.filtered_total)
        setLoading(false)
      })
      .catch(() => { if (!cancelled) { setError('Failed to load lineage data.'); setLoading(false) } })
    return () => { cancelled = true }
  }, [repo, branch, filter, sort, debouncedQuery])

  // Load next page (append)
  const fetchPage = useCallback(() => {
    if (!repo || loading) return
    const currentOffset = nodesRef.current?.length || 0
    setLoading(true)
    api.lineageData(repo, branch, { offset: currentOffset, limit: PAGE_SIZE, search: debouncedQuery, filter, sort })
      .then(data => {
        const ns = data.nodes || []
        const merged = [...(nodesRef.current || []), ...ns]
        nodesRef.current = merged
        setNodes(merged)
        setStats({ total: data.total, connected: data.connected, isolated: data.isolated })
        setFilteredTotal(data.filtered_total)
        setLoading(false)
      })
      .catch(() => { setError('Failed to load lineage data.'); setLoading(false) })
  }, [repo, branch, debouncedQuery, filter, sort, loading])

  useEffect(() => {
    if (!repo || classData) return
    if (view !== 'classes') return
    api.classLineageData(repo, branch)
      .then(setClassData)
      .catch(() => setClassError('Failed to load class lineage.'))
  }, [repo, branch, view])

  const maxConnections = useMemo(() => {
    if (!nodes?.length) return 1
    return Math.max(1, ...nodes.map(n => (n.upstream_count || 0) + (n.downstream_count || 0)))
  }, [nodes])

  const repoLabel = repo.replace(/^https?:\/\/github\.com\//, '').replace(/\.git$/, '')

  // --- Class view derived data ---
  const classStats = useMemo(() => {
    if (!classData) return { total: 0, connected: 0, isolated: 0 }
    const connectedIds = new Set()
    ;(classData.edges || []).forEach(e => { connectedIds.add(e.source); connectedIds.add(e.target) })
    const total = (classData.nodes || []).length
    const connected = connectedIds.size
    return { total, connected, isolated: total - connected }
  }, [classData])

  const classIncoming = useMemo(() => {
    const calls = {}, extends_ = {}
    ;(classData?.edges || []).forEach(e => {
      if (e.edge_type === 'extends') extends_[e.target] = (extends_[e.target] || 0) + 1
      else calls[e.target] = (calls[e.target] || 0) + 1
    })
    return { calls, extends: extends_ }
  }, [classData])

  const classOutgoing = useMemo(() => {
    const calls = {}, extends_ = {}
    ;(classData?.edges || []).forEach(e => {
      if (e.edge_type === 'extends') extends_[e.source] = (extends_[e.source] || 0) + 1
      else calls[e.source] = (calls[e.source] || 0) + 1
    })
    return { calls, extends: extends_ }
  }, [classData])

  const filteredClasses = useMemo(() => {
    if (!classData) return []
    const q = classQuery.toLowerCase()
    return (classData.nodes || []).filter(c =>
      !q || c.name.toLowerCase().includes(q) || c.file.toLowerCase().includes(q)
    ).sort((a, b) => {
      const aT = (classOutgoing.calls[a.id] || 0) + (classOutgoing.extends[a.id] || 0) + (classIncoming.calls[a.id] || 0) + (classIncoming.extends[a.id] || 0)
      const bT = (classOutgoing.calls[b.id] || 0) + (classOutgoing.extends[b.id] || 0) + (classIncoming.calls[b.id] || 0) + (classIncoming.extends[b.id] || 0)
      return bT - aT || a.name.localeCompare(b.name)
    })
  }, [classData, classQuery, classOutgoing, classIncoming])

  return (
    <>
      <NavBar
        breadcrumbs={[{ label: view === 'classes' ? 'Class Lineage' : 'Function Lineage' }]}
        repo={repo}
        branch={branch}
        changesCount={changesCount}
        right={null}
      />

      {/* Page header */}
      <div style={{
        padding: '20px 28px 0',
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        flexWrap: 'wrap',
      }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 2 }}>
            <h1 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>
              {view === 'classes' ? 'Class Lineage' : 'Function Lineage'}
            </h1>
            <span style={{
              fontSize: 11, padding: '2px 8px', borderRadius: 20,
              background: 'rgba(129,140,248,0.1)',
              border: '1px solid rgba(129,140,248,0.25)',
              color: 'var(--accent)',
              fontFamily: "'JetBrains Mono',monospace",
            }}>{branch}</span>
          </div>
          <div style={{
            fontSize: 12, color: 'var(--text-dim)',
            fontFamily: "'JetBrains Mono',monospace",
          }}>{repoLabel}</div>
        </div>

        {/* View toggle */}
        <div style={{
          display: 'flex', gap: 2,
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 9, padding: 3,
        }}>
          {[
            { val: 'functions', label: 'Functions' },
            { val: 'classes', label: 'Classes' },
          ].map(opt => (
            <button key={opt.val} onClick={() => setView(opt.val)} style={{
              padding: '6px 16px',
              borderRadius: 7,
              border: 'none',
              cursor: 'pointer',
              fontSize: 12,
              fontFamily: "'JetBrains Mono',monospace",
              fontWeight: view === opt.val ? 600 : 400,
              background: view === opt.val ? 'rgba(129,140,248,0.18)' : 'transparent',
              color: view === opt.val ? 'var(--accent)' : 'var(--text-dim)',
              transition: 'all 0.15s',
            }}>{opt.label}</button>
          ))}
        </div>
      </div>

      {/* Stats cards */}
      <div style={{ padding: '16px 28px', display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        {view === 'functions' && stats.total > 0 && (() => {
          const items = [
            {
              label: 'Total functions', value: stats.total,
              color: 'var(--accent)', bg: 'rgba(129,140,248,0.07)', border: 'rgba(129,140,248,0.18)',
              icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M8 6l-6 6 6 6"/><path d="M16 6l6 6-6 6"/></svg>,
            },
            {
              label: 'Connected', value: stats.connected,
              color: 'var(--green)', bg: 'rgba(34,197,94,0.07)', border: 'rgba(34,197,94,0.18)',
              icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg>,
            },
            {
              label: 'Isolated', value: stats.isolated,
              color: 'var(--text-dim)', bg: 'rgba(255,255,255,0.025)', border: 'rgba(255,255,255,0.07)',
              icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/></svg>,
            },
          ]
          return items.map(s => (
            <div key={s.label} style={{
              display: 'flex', alignItems: 'center', gap: 14,
              padding: '12px 20px',
              background: s.bg, border: `1px solid ${s.border}`,
              borderRadius: 10, minWidth: 160, flex: '0 0 auto',
            }}>
              <div style={{ color: s.color, opacity: 0.8 }}>{s.icon}</div>
              <div>
                <div style={{ fontSize: 22, fontWeight: 700, color: s.color, fontFamily: "'JetBrains Mono',monospace", lineHeight: 1 }}>{s.value}</div>
                <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 3 }}>{s.label}</div>
              </div>
            </div>
          ))
        })()}

        {view === 'classes' && classData && (() => {
          const items = [
            {
              label: 'Total classes', value: classStats.total,
              color: 'var(--accent)', bg: 'rgba(129,140,248,0.07)', border: 'rgba(129,140,248,0.18)',
              icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>,
            },
            {
              label: 'Inter-connected', value: classStats.connected,
              color: 'var(--green)', bg: 'rgba(34,197,94,0.07)', border: 'rgba(34,197,94,0.18)',
              icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg>,
            },
            {
              label: 'No relations', value: classStats.isolated,
              color: 'var(--text-dim)', bg: 'rgba(255,255,255,0.025)', border: 'rgba(255,255,255,0.07)',
              icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/></svg>,
            },
          ]
          return items.map(s => (
            <div key={s.label} style={{
              display: 'flex', alignItems: 'center', gap: 14,
              padding: '12px 20px',
              background: s.bg, border: `1px solid ${s.border}`,
              borderRadius: 10, minWidth: 160, flex: '0 0 auto',
            }}>
              <div style={{ color: s.color, opacity: 0.8 }}>{s.icon}</div>
              <div>
                <div style={{ fontSize: 22, fontWeight: 700, color: s.color, fontFamily: "'JetBrains Mono',monospace", lineHeight: 1 }}>{s.value}</div>
                <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 3 }}>{s.label}</div>
              </div>
            </div>
          ))
        })()}
      </div>

      {/* Toolbar */}
      <div style={{ padding: '0 28px 16px', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        {view === 'functions' && (
          <>
            {/* Search */}
            <div style={{ position: 'relative', flex: '1 1 240px', maxWidth: 340 }}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-dim)', pointerEvents: 'none' }}>
                <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>
              </svg>
              <input
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder="Search functions or files..."
                style={{
                  width: '100%', padding: '8px 12px 8px 32px',
                  background: 'var(--surface)', border: '1px solid var(--border)',
                  borderRadius: 8, color: 'var(--text)', fontSize: 13,
                  fontFamily: "'JetBrains Mono',monospace", outline: 'none', marginTop: 0, transition: 'border-color 0.15s',
                }}
                onFocus={e => e.target.style.borderColor = 'rgba(129,140,248,0.5)'}
                onBlur={e => e.target.style.borderColor = 'var(--border)'}
              />
            </div>

            {/* Filter toggle pills */}
            <div style={{ display: 'flex', gap: 4, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: 3 }}>
              {[
                { val: 'connected', label: 'Connected' },
                { val: 'all', label: 'All' },
                { val: 'upstream', label: '↑ Up' },
                { val: 'downstream', label: '↓ Down' },
              ].map(opt => (
                <button key={opt.val} onClick={() => setFilter(opt.val)} style={{
                  padding: '5px 12px', borderRadius: 6, border: 'none', cursor: 'pointer', fontSize: 12,
                  fontFamily: "'JetBrains Mono',monospace",
                  fontWeight: filter === opt.val ? 600 : 400,
                  background: filter === opt.val ? 'rgba(129,140,248,0.15)' : 'transparent',
                  color: filter === opt.val ? 'var(--accent)' : 'var(--text-dim)',
                  transition: 'all 0.15s',
                }}>{opt.label}</button>
              ))}
            </div>

            {/* Sort */}
            <select
              value={sort}
              onChange={e => setSort(e.target.value)}
              style={{
                background: 'var(--surface)', border: '1px solid var(--border)',
                padding: '8px 12px', borderRadius: 8, color: 'var(--text-dim)', fontSize: 12,
                fontFamily: "'JetBrains Mono',monospace", outline: 'none', cursor: 'pointer', marginTop: 0,
              }}
            >
              <option value="connections">Most connected</option>
              <option value="name">Name A–Z</option>
              <option value="file">File path</option>
            </select>

            {/* Count */}
            {nodes && nodes.length > 0 && (
              <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-dim)', fontFamily: "'JetBrains Mono',monospace" }}>
                {nodes.length} / {filteredTotal}
              </span>
            )}
          </>
        )}

        {view === 'classes' && (
          <>
            {/* Class search */}
            <div style={{ position: 'relative', flex: '1 1 240px', maxWidth: 340 }}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-dim)', pointerEvents: 'none' }}>
                <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>
              </svg>
              <input
                value={classQuery}
                onChange={e => setClassQuery(e.target.value)}
                placeholder="Search classes or files..."
                style={{
                  width: '100%', padding: '8px 12px 8px 32px',
                  background: 'var(--surface)', border: '1px solid var(--border)',
                  borderRadius: 8, color: 'var(--text)', fontSize: 13,
                  fontFamily: "'JetBrains Mono',monospace", outline: 'none', marginTop: 0, transition: 'border-color 0.15s',
                }}
                onFocus={e => e.target.style.borderColor = 'rgba(129,140,248,0.5)'}
                onBlur={e => e.target.style.borderColor = 'var(--border)'}
              />
            </div>
            {classData && (
              <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-dim)', fontFamily: "'JetBrains Mono',monospace" }}>
                {filteredClasses.length} classes
              </span>
            )}
          </>
        )}
      </div>

      {/* Divider */}
      <div style={{ height: 1, background: 'var(--border)', margin: '0 0 20px' }} />

      {/* ── FUNCTIONS VIEW ── */}
      {view === 'functions' && (
        <>
          {loading && (!nodes || nodes.length === 0) && !error && (
            <div style={{ padding: 80, textAlign: 'center' }}>
              <Spinner />
              <div style={{ marginTop: 14, color: 'var(--text-dim)', fontSize: 13 }}>Loading lineage data...</div>
            </div>
          )}

          {error && (
            <div className="empty-state" style={{ padding: '80px 20px' }}>
              {error} <a href={`/${tenant}/dashboard`}>Go back</a>.
            </div>
          )}

          {!error && nodes && !(loading && nodes.length === 0) && (
            <div style={{ padding: '0 24px 32px', display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 10 }}>
              {nodes.length === 0 && !loading && (
                <div className="empty-state" style={{ gridColumn: '1 / -1', padding: '80px 20px' }}>
                  No functions match your filters.
                </div>
              )}

              {nodes.map(n => {
                const parts = n.name.split('.')
                const funcName = parts[parts.length - 1]
                const modulePath = parts.slice(0, -1).join('.')
                const upCount = n.upstream_count || 0
                const downCount = n.downstream_count || 0
                const isolated = upCount === 0 && downCount === 0
                const totalConn = upCount + downCount
                const connPct = Math.round((totalConn / maxConnections) * 100)

                return (
                  <div
                    key={n.id}
                    onClick={() => nav(`/${tenant}/asset?repo=${encodeURIComponent(repo)}&branch=${encodeURIComponent(branch)}&asset_id=${encodeURIComponent(n.id)}`)}
                    style={{
                      background: 'var(--surface)', border: '1px solid var(--border)',
                      borderRadius: 10, cursor: 'pointer',
                      transition: 'border-color 0.15s, box-shadow 0.15s, opacity 0.15s',
                      overflow: 'hidden', opacity: isolated ? 0.4 : 1, display: 'flex',
                    }}
                    onMouseEnter={e => { e.currentTarget.style.borderColor = 'rgba(129,140,248,0.45)'; e.currentTarget.style.boxShadow = '0 4px 20px rgba(129,140,248,0.1)'; e.currentTarget.style.opacity = '1' }}
                    onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.boxShadow = 'none'; e.currentTarget.style.opacity = isolated ? '0.4' : '1' }}
                  >
                    <div style={{ width: 3, flexShrink: 0, background: isolated ? 'var(--border)' : 'linear-gradient(180deg, var(--accent) 0%, var(--purple) 100%)' }} />
                    <div style={{ padding: '13px 14px', flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 600, color: 'var(--text)', fontSize: 13, fontFamily: "'JetBrains Mono',monospace", whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', marginBottom: 2 }}>{funcName}</div>
                      {modulePath && (
                        <div style={{ fontSize: 11, color: 'var(--text-dim)', fontFamily: "'JetBrains Mono',monospace", whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{modulePath}</div>
                      )}
                      <div style={{ fontSize: 10, color: 'hsl(220,10%,36%)', fontFamily: "'JetBrains Mono',monospace", marginTop: 3, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', display: 'flex', alignItems: 'center', gap: 4 }}>
                        <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0, opacity: 0.5 }}>
                          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                          <polyline points="14 2 14 8 20 8"/>
                        </svg>
                        {n.file}:{n.lineno}
                      </div>
                      <div style={{ display: 'flex', gap: 5, marginTop: 10, alignItems: 'center' }}>
                        <span style={{ padding: '2px 8px', borderRadius: 20, fontSize: 10, fontFamily: "'JetBrains Mono',monospace", background: upCount > 0 ? 'rgba(6,182,212,0.1)' : 'rgba(255,255,255,0.04)', border: `1px solid ${upCount > 0 ? 'rgba(6,182,212,0.25)' : 'rgba(255,255,255,0.07)'}`, color: upCount > 0 ? 'var(--cyan)' : 'var(--text-dim)' }}>↑ {upCount}</span>
                        <span style={{ padding: '2px 8px', borderRadius: 20, fontSize: 10, fontFamily: "'JetBrains Mono',monospace", background: downCount > 0 ? 'rgba(168,85,247,0.1)' : 'rgba(255,255,255,0.04)', border: `1px solid ${downCount > 0 ? 'rgba(168,85,247,0.25)' : 'rgba(255,255,255,0.07)'}`, color: downCount > 0 ? 'var(--purple)' : 'var(--text-dim)' }}>↓ {downCount}</span>
                        {!isolated && (
                          <div style={{ flex: 1, height: 3, background: 'rgba(255,255,255,0.06)', borderRadius: 2, overflow: 'hidden', marginLeft: 4 }}>
                            <div style={{ height: '100%', width: `${connPct}%`, background: 'linear-gradient(90deg, var(--accent), var(--purple))', borderRadius: 2, opacity: 0.7 }} />
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                )
              })}

              {nodes.length < filteredTotal && (
                <div style={{ gridColumn: '1 / -1', textAlign: 'center', padding: '12px 0 4px' }}>
                  <button
                    onClick={fetchPage}
                    disabled={loading}
                    style={{ background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-dim)', padding: '9px 28px', borderRadius: 8, cursor: loading ? 'default' : 'pointer', fontSize: 12, fontFamily: "'JetBrains Mono',monospace", transition: 'all 0.15s', opacity: loading ? 0.5 : 1 }}
                    onMouseEnter={e => { if (!loading) { e.currentTarget.style.borderColor = 'rgba(129,140,248,0.4)'; e.currentTarget.style.color = 'var(--accent)' } }}
                    onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.color = 'var(--text-dim)' }}
                  >
                    {loading ? 'Loading...' : `Load ${Math.min(PAGE_SIZE, filteredTotal - nodes.length)} more`}
                    <span style={{ opacity: 0.5, marginLeft: 6 }}>({filteredTotal - nodes.length} remaining)</span>
                  </button>
                </div>
              )}
            </div>
          )}
        </>
      )}

      {/* ── CLASSES VIEW ── */}
      {view === 'classes' && (
        <>
          {!classData && !classError && (
            <div style={{ padding: 80, textAlign: 'center' }}>
              <Spinner />
              <div style={{ marginTop: 14, color: 'var(--text-dim)', fontSize: 13 }}>Loading class lineage...</div>
            </div>
          )}

          {classError && (
            <div className="empty-state" style={{ padding: '80px 20px' }}>
              {classError} <a href={`/${tenant}/dashboard`}>Go back</a>.
            </div>
          )}

          {classData && (
            <div style={{ padding: '0 24px 32px', display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 10 }}>
              {filteredClasses.length === 0 && (
                <div className="empty-state" style={{ gridColumn: '1 / -1', padding: '80px 20px' }}>
                  No classes found{classQuery ? ' matching your search' : ''}.
                </div>
              )}

              {filteredClasses.map(c => {
                const outCalls = classOutgoing.calls[c.id] || 0
                const outExtends = classOutgoing.extends[c.id] || 0
                const inCalls = classIncoming.calls[c.id] || 0
                const inExtends = classIncoming.extends[c.id] || 0
                const isolated = outCalls + outExtends + inCalls + inExtends === 0
                const hasInheritance = outExtends > 0
                const isCrossRepo = !!c.is_cross_repo
                const cardRepo = isCrossRepo ? (c.repo_url || c.repo) : repo
                const cardBranch = isCrossRepo ? (c.branch || branch) : branch

                return (
                  <div
                    key={c.id}
                    onClick={() => nav(`/${tenant}/asset?repo=${encodeURIComponent(cardRepo)}&branch=${encodeURIComponent(cardBranch)}&asset_id=${encodeURIComponent(c.id)}`)}
                    style={{
                      background: 'var(--surface)',
                      border: `1px solid ${isCrossRepo ? 'rgba(245,158,11,0.3)' : 'var(--border)'}`,
                      borderRadius: 10, cursor: 'pointer',
                      transition: 'border-color 0.15s, box-shadow 0.15s, opacity 0.15s',
                      overflow: 'hidden', opacity: isolated ? 0.45 : 1, display: 'flex',
                    }}
                    onMouseEnter={e => { e.currentTarget.style.borderColor = isCrossRepo ? 'rgba(245,158,11,0.6)' : 'rgba(129,140,248,0.45)'; e.currentTarget.style.boxShadow = '0 4px 20px rgba(129,140,248,0.1)'; e.currentTarget.style.opacity = '1' }}
                    onMouseLeave={e => { e.currentTarget.style.borderColor = isCrossRepo ? 'rgba(245,158,11,0.3)' : 'var(--border)'; e.currentTarget.style.boxShadow = 'none'; e.currentTarget.style.opacity = isolated ? '0.45' : '1' }}
                  >
                    {/* Left accent bar — amber for cross-repo, teal for same-repo */}
                    <div style={{ width: 3, flexShrink: 0, background: isolated ? 'var(--border)' : isCrossRepo ? '#f59e0b' : 'linear-gradient(180deg, var(--cyan) 0%, var(--accent) 100%)' }} />

                    <div style={{ padding: '13px 14px', flex: 1, minWidth: 0 }}>
                      {/* Class name + badges */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                        <div style={{ fontWeight: 600, color: 'var(--text)', fontSize: 13, fontFamily: "'JetBrains Mono',monospace", whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', flex: 1 }}>
                          {c.name}
                        </div>
                        {hasInheritance && (
                          <span style={{ flexShrink: 0, fontSize: 9, padding: '1px 6px', borderRadius: 20, background: 'rgba(6,182,212,0.1)', border: '1px solid rgba(6,182,212,0.2)', color: 'var(--cyan)', fontFamily: "'JetBrains Mono',monospace" }}>
                            extends
                          </span>
                        )}
                        {isCrossRepo && (
                          <span style={{ flexShrink: 0, fontSize: 9, padding: '1px 6px', borderRadius: 20, background: 'rgba(245,158,11,0.1)', border: '1px solid rgba(245,158,11,0.3)', color: '#f59e0b', fontFamily: "'JetBrains Mono',monospace" }}>
                            external
                          </span>
                        )}
                      </div>

                      {/* File path */}
                      <div style={{ fontSize: 10, color: 'hsl(220,10%,36%)', fontFamily: "'JetBrains Mono',monospace", marginTop: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', display: 'flex', alignItems: 'center', gap: 4 }}>
                        <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0, opacity: 0.5 }}>
                          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                          <polyline points="14 2 14 8 20 8"/>
                        </svg>
                        {c.file}:{c.lineno}
                      </div>
                      {isCrossRepo && (
                        <div style={{ fontSize: 9, color: '#f59e0b', fontFamily: "'JetBrains Mono',monospace", marginTop: 2, opacity: 0.8, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          ⬡ {c.repo}
                        </div>
                      )}

                      {/* Stats row */}
                      <div style={{ display: 'flex', gap: 5, marginTop: 10, alignItems: 'center' }}>
                        {/* Methods count */}
                        <span style={{ padding: '2px 8px', borderRadius: 20, fontSize: 10, fontFamily: "'JetBrains Mono',monospace", background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.08)', color: 'var(--text-dim)' }}>
                          {c.method_count} {c.method_count === 1 ? 'method' : 'methods'}
                        </span>
                        {/* Outgoing calls */}
                        {outCalls > 0 && (
                          <span style={{ padding: '2px 8px', borderRadius: 20, fontSize: 10, fontFamily: "'JetBrains Mono',monospace", background: 'rgba(168,85,247,0.1)', border: '1px solid rgba(168,85,247,0.25)', color: 'var(--purple)' }}>
                            ↓ {outCalls} calls
                          </span>
                        )}
                        {/* Extends (outgoing inheritance) */}
                        {outExtends > 0 && (
                          <span style={{ padding: '2px 8px', borderRadius: 20, fontSize: 10, fontFamily: "'JetBrains Mono',monospace", background: 'rgba(245,158,11,0.1)', border: '1px solid rgba(245,158,11,0.25)', color: '#f59e0b' }}>
                            ↑ {outExtends} extends
                          </span>
                        )}
                        {/* Incoming calls */}
                        {inCalls > 0 && (
                          <span style={{ padding: '2px 8px', borderRadius: 20, fontSize: 10, fontFamily: "'JetBrains Mono',monospace", background: 'rgba(6,182,212,0.1)', border: '1px solid rgba(6,182,212,0.25)', color: 'var(--cyan)' }}>
                            ↑ {inCalls} callers
                          </span>
                        )}
                        {/* Subclasses (incoming inheritance) */}
                        {inExtends > 0 && (
                          <span style={{ padding: '2px 8px', borderRadius: 20, fontSize: 10, fontFamily: "'JetBrains Mono',monospace", background: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.25)', color: 'var(--green)' }}>
                            ↓ {inExtends} subclasses
                          </span>
                        )}
                        {/* Isolated placeholder */}
                        {isolated && (
                          <span style={{ padding: '2px 8px', borderRadius: 20, fontSize: 10, fontFamily: "'JetBrains Mono',monospace", background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.07)', color: 'var(--text-dim)' }}>
                            no relations
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}
    </>
  )
}
