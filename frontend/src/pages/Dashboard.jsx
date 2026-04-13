import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { NavBar } from '../components/NavBar'
import { useAuth } from '../hooks/useAuth'
import { Spinner } from '../components/Spinner'
import { EmptyState } from '../components/EmptyState'
import { Alert } from '../components/Alert'

const EXCLUDED_DIRS = new Set([
  '.venv', 'venv', 'env', '.git', '__pycache__',
  '.pytest_cache', '.idea', '.vscode', 'node_modules',
])

function isExcluded(name) {
  return EXCLUDED_DIRS.has(name) || name.startsWith('.')
}

// Recursively collect .py files from a FileSystemEntry, skipping excluded dirs
async function collectPyFiles(entry, prefix = '') {
  if (entry.isFile) {
    if (!entry.name.endsWith('.py')) return []
    return new Promise((resolve) => {
      entry.file(f => resolve([{ file: f, path: prefix + entry.name }]))
    })
  }
  if (entry.isDirectory) {
    if (isExcluded(entry.name)) return []
    const reader = entry.createReader()
    const all = []
    while (true) {
      const batch = await new Promise((res, rej) => reader.readEntries(res, rej))
      if (batch.length === 0) break
      for (const child of batch) {
        const collected = await collectPyFiles(child, prefix + entry.name + '/')
        all.push(...collected)
      }
    }
    return all
  }
  return []
}

export default function Dashboard() {
  const { user } = useAuth()
  const tenant = user?.tenant_id || ''
  const [data, setData] = useState(null)
  const [repoUrl, setRepoUrl] = useState('')
  const [branch, setBranch] = useState('main')
  const [crawling, setCrawling] = useState(false)
  const [crawlMsg, setCrawlMsg] = useState('')
  const [crawlError, setCrawlError] = useState('')
  const [error, setError] = useState('')
  // key: "repo::branch" → 'idle' | 'loading' | 'done' | 'error'
  const [recrawlState, setRecrawlState] = useState({})

  // key: repo → default branch (optimistic local state)
  const [defaultBranches, setDefaultBranches] = useState({})

  const setDefaultBranch = async (repo, branch) => {
    setDefaultBranches(prev => ({ ...prev, [repo]: branch }))
    try { await api.setDefaultBranch(repo, branch) } catch { /* best effort */ }
  }

  // Local folder state
  const [crawlMode, setCrawlMode] = useState('github') // 'github' | 'local'
  const [isDragging, setIsDragging] = useState(false)
  const [localFiles, setLocalFiles] = useState([])   // [{file, path}]
  const [localFolderName, setLocalFolderName] = useState('')
  const [localBranch, setLocalBranch] = useState('local')
  const folderInputRef = useRef(null)

  useEffect(() => {
    api.dashboard().then(d => {
      setData(d)
      // Seed default branches from DB
      const seed = {}
      for (const r of d.repos || []) {
        if (r.default_branch) seed[r.repo] = r.default_branch
      }
      setDefaultBranches(seed)
    }).catch((e) => setError(e.message))
  }, [])

  const recrawl = async (repo, branchName, repoFullUrl) => {
    const key = `${repo}::${branchName}`
    setRecrawlState(prev => ({ ...prev, [key]: 'loading' }))
    try {
      await api.crawl(repoFullUrl, branchName)
      setRecrawlState(prev => ({ ...prev, [key]: 'done' }))
      setTimeout(() => setRecrawlState(prev => ({ ...prev, [key]: 'idle' })), 3000)
    } catch {
      setRecrawlState(prev => ({ ...prev, [key]: 'error' }))
      setTimeout(() => setRecrawlState(prev => ({ ...prev, [key]: 'idle' })), 3000)
    }
  }

  const startCrawl = async (e) => {
    e.preventDefault()
    setCrawling(true)
    setCrawlMsg('')
    setCrawlError('')
    try {
      const res = await api.crawl(repoUrl, branch)
      // Persist original URL so Asset page can build GitHub deep-links
      const slug = repoUrl.replace(/\.git$/, '').split('/').pop()
      let cleanUrl = repoUrl.replace(/\.git$/, '').replace(/\/$/, '')
      if (!cleanUrl.startsWith('http')) cleanUrl = 'https://' + cleanUrl
      localStorage.setItem(`cc-repo-url:${slug}`, cleanUrl)
      setCrawlMsg(`Workflow started: ${res.workflow_id || 'queued'}`)
      setTimeout(() => api.dashboard().then(setData), 3000)
    } catch (err) {
      setCrawlError(err.message)
    } finally {
      setCrawling(false)
    }
  }

  const handleDrop = async (e) => {
    e.preventDefault()
    setIsDragging(false)
    const items = Array.from(e.dataTransfer.items)
    const entry = items[0]?.webkitGetAsEntry?.()
    if (!entry) return
    const folderName = entry.isDirectory ? entry.name : entry.name.replace('.py', '')
    const collected = await collectPyFiles(entry, '')
    setLocalFolderName(folderName)
    setLocalFiles(collected)
  }

  const handleFolderInput = (e) => {
    const files = Array.from(e.target.files).filter(f => {
      if (!f.name.endsWith('.py')) return false
      // webkitRelativePath = "folder/sub/dir/file.py" — check every path segment
      const segments = f.webkitRelativePath.split('/')
      return !segments.some(seg => isExcluded(seg))
    })
    if (!files.length) return
    const folderName = files[0].webkitRelativePath.split('/')[0]
    setLocalFolderName(folderName)
    setLocalFiles(files.map(f => ({ file: f, path: f.webkitRelativePath })))
  }

  const startLocalCrawl = async (e) => {
    e.preventDefault()
    if (!localFiles.length) return
    setCrawling(true)
    setCrawlMsg('')
    setCrawlError('')
    try {
      const fd = new FormData()
      fd.append('folder_name', localFolderName)
      fd.append('branch', localBranch)
      for (const { file, path } of localFiles) {
        fd.append('files', file, path)
      }
      const res = await api.crawlLocal(fd)
      setCrawlMsg(`Workflow started: ${res.workflow_id || 'queued'}`)
      setTimeout(() => api.dashboard().then(setData), 3000)
    } catch (err) {
      setCrawlError(err.message)
    } finally {
      setCrawling(false)
    }
  }

  return (
    <>
      <NavBar />

      {/* Page header */}
      <div style={{
        padding: '28px 28px 0',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}>
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 700, marginBottom: 2 }}>Dashboard</h1>
          <p style={{ fontSize: 13, color: 'var(--text-dim)' }}>
            Crawl a Python repo to build its function-level lineage graph.
          </p>
        </div>
      </div>

      <div className="page-grid" style={{ paddingTop: 20 }}>
        {/* Left: Start a crawl */}
        <div className="card" style={{ position: 'relative', overflow: 'hidden' }}>
          {/* Accent gradient strip */}
          <div style={{
            position: 'absolute', top: 0, left: 0, right: 0, height: 3,
            background: crawlMode === 'github'
              ? 'linear-gradient(90deg, var(--accent), var(--purple))'
              : 'linear-gradient(90deg, var(--green), var(--cyan))',
          }} />

          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14, marginTop: 8 }}>
            <div style={{
              width: 32, height: 32, borderRadius: 8,
              background: crawlMode === 'github' ? 'rgba(129,140,248,0.1)' : 'rgba(34,197,94,0.1)',
              border: crawlMode === 'github' ? '1px solid rgba(129,140,248,0.2)' : '1px solid rgba(34,197,94,0.2)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              flexShrink: 0,
            }}>
              {crawlMode === 'github' ? (
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/>
                </svg>
              ) : (
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--green)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                </svg>
              )}
            </div>
            <div style={{ flex: 1 }}>
              <div className="card-title" style={{ marginBottom: 0 }}>Start a crawl</div>
              <div className="card-hint" style={{ marginBottom: 0, marginTop: 1 }}>Python only</div>
            </div>

            {/* Mode toggle */}
            <div style={{
              display: 'flex', borderRadius: 8, overflow: 'hidden',
              border: '1px solid var(--border)', flexShrink: 0,
            }}>
              {[['github', 'GitHub'], ['local', 'Local Folder']].map(([mode, label]) => (
                <button
                  key={mode}
                  onClick={() => { setCrawlMode(mode); setCrawlMsg(''); setCrawlError('') }}
                  style={{
                    padding: '5px 12px', fontSize: 11, fontWeight: 600, cursor: 'pointer', border: 'none',
                    background: crawlMode === mode ? 'rgba(129,140,248,0.18)' : 'transparent',
                    color: crawlMode === mode ? 'var(--accent)' : 'var(--text-dim)',
                    transition: 'background 0.15s, color 0.15s',
                    fontFamily: "'JetBrains Mono',monospace",
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* GitHub mode */}
          {crawlMode === 'github' && (
            <form onSubmit={startCrawl}>
              <div className="field">
                <label htmlFor="repo-url">Repository URL</label>
                <input
                  id="repo-url"
                  value={repoUrl}
                  onChange={e => setRepoUrl(e.target.value)}
                  placeholder="https://github.com/org/repo.git"
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="branch">Branch</label>
                <input
                  id="branch"
                  value={branch}
                  onChange={e => setBranch(e.target.value)}
                />
              </div>
              <button className="btn btn-primary" type="submit" disabled={crawling}>
                {crawling ? (
                  <><div className="spinner" style={{ width: 15, height: 15, margin: 0, borderWidth: 2, borderTopColor: '#fff' }} />Crawling...</>
                ) : (
                  <><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>Crawl &amp; Build Lineage</>
                )}
              </button>
            </form>
          )}

          {/* Local folder mode */}
          {crawlMode === 'local' && (
            <form onSubmit={startLocalCrawl}>
              {/* Drop zone */}
              <div
                onDragOver={e => { e.preventDefault(); setIsDragging(true) }}
                onDragLeave={() => setIsDragging(false)}
                onDrop={handleDrop}
                onClick={() => folderInputRef.current?.click()}
                style={{
                  border: `2px dashed ${isDragging ? 'var(--green)' : localFiles.length ? 'rgba(34,197,94,0.4)' : 'var(--border)'}`,
                  borderRadius: 10,
                  padding: '24px 16px',
                  textAlign: 'center',
                  cursor: 'pointer',
                  background: isDragging ? 'rgba(34,197,94,0.05)' : localFiles.length ? 'rgba(34,197,94,0.03)' : 'transparent',
                  transition: 'all 0.2s',
                  marginBottom: 14,
                }}
              >
                {localFiles.length > 0 ? (
                  <>
                    <div style={{ fontSize: 28, marginBottom: 6 }}>📁</div>
                    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--green)', fontFamily: "'JetBrains Mono',monospace" }}>
                      {localFolderName}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 4 }}>
                      {localFiles.length} Python {localFiles.length === 1 ? 'file' : 'files'} found
                    </div>
                    <button
                      type="button"
                      onClick={e => { e.stopPropagation(); setLocalFiles([]); setLocalFolderName('') }}
                      style={{ marginTop: 8, fontSize: 11, color: 'var(--text-dim)', background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline' }}
                    >
                      Clear
                    </button>
                  </>
                ) : (
                  <>
                    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke={isDragging ? 'var(--green)' : 'var(--text-dim)'} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.5, marginBottom: 8 }}>
                      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                    </svg>
                    <div style={{ fontSize: 13, color: isDragging ? 'var(--green)' : 'var(--text-dim)', fontWeight: 500 }}>
                      {isDragging ? 'Drop folder here' : 'Drop a Python folder here'}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 4, opacity: 0.6 }}>
                      or click to browse
                    </div>
                  </>
                )}
              </div>
              <input
                ref={folderInputRef}
                type="file"
                webkitdirectory=""
                multiple
                style={{ display: 'none' }}
                onChange={handleFolderInput}
              />

              <div className="field">
                <label htmlFor="local-branch">Branch label</label>
                <input
                  id="local-branch"
                  value={localBranch}
                  onChange={e => setLocalBranch(e.target.value)}
                  placeholder="local"
                />
              </div>

              <button className="btn btn-primary" type="submit" disabled={crawling || !localFiles.length}
                style={{ background: localFiles.length ? undefined : undefined, opacity: localFiles.length ? 1 : 0.5 }}>
                {crawling ? (
                  <><div className="spinner" style={{ width: 15, height: 15, margin: 0, borderWidth: 2, borderTopColor: '#fff' }} />Uploading &amp; Crawling...</>
                ) : (
                  <><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>Crawl &amp; Build Lineage</>
                )}
              </button>
            </form>
          )}

          {crawlMsg && (
            <div style={{
              marginTop: 12, fontSize: 12, color: 'var(--accent)',
              fontFamily: "'JetBrains Mono',monospace",
              padding: '8px 12px',
              background: 'rgba(129,140,248,0.07)',
              border: '1px solid rgba(129,140,248,0.2)',
              borderRadius: 'var(--radius-sm)',
            }}>
              {crawlMsg}
            </div>
          )}
          <Alert type="error">{crawlError}</Alert>
        </div>

        {/* Right: Your lineage */}
        <div className="card" style={{ position: 'relative', overflow: 'hidden' }}>
          <div style={{
            position: 'absolute', top: 0, left: 0, right: 0, height: 3,
            background: 'linear-gradient(90deg, var(--cyan), var(--accent))',
          }} />

          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16, marginTop: 8 }}>
            <div style={{
              width: 32, height: 32, borderRadius: 8,
              background: 'rgba(6,182,212,0.1)',
              border: '1px solid rgba(6,182,212,0.2)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              flexShrink: 0,
            }}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--cyan)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="16" y="16" width="6" height="6" rx="1"/>
                <rect x="2" y="16" width="6" height="6" rx="1"/>
                <rect x="9" y="2" width="6" height="6" rx="1"/>
                <path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3"/>
                <path d="M12 12V8"/>
              </svg>
            </div>
            <div>
              <div className="card-title" style={{ marginBottom: 0 }}>Your lineage</div>
              <div className="card-hint" style={{ marginBottom: 0, marginTop: 1 }}>Repos crawled for this tenant</div>
            </div>
          </div>

          {error && <Alert type="error">{error}</Alert>}
          {!data && !error && <Spinner />}

          {data && (
            <div className="list">
              {data.repos.length === 0 && (
                <EmptyState
                  icon={
                    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--text-dim)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.4 }}>
                      <rect x="16" y="16" width="6" height="6" rx="1"/>
                      <rect x="2" y="16" width="6" height="6" rx="1"/>
                      <rect x="9" y="2" width="6" height="6" rx="1"/>
                      <path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3"/>
                      <path d="M12 12V8"/>
                    </svg>
                  }
                >
                  No lineage yet — start a crawl to get going.
                </EmptyState>
              )}

              {data.repos.map((r) => (
                <div className="list-item" key={r.repo}>
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10,
                  }}>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                      <path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/>
                    </svg>
                    <span style={{
                      fontWeight: 600, fontSize: 13,
                      fontFamily: "'JetBrains Mono',monospace",
                      color: 'var(--text)',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>{r.repo}</span>
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
                    {r.branches.map((b) => {
                      const bName = typeof b === 'string' ? b : b.branch
                      const bQ = encodeURIComponent(bName)
                      const rcKey = `${r.repo}::${bName}`
                      const rcStatus = recrawlState[rcKey] || 'idle'
                      const fullUrl = r.repo_url || localStorage.getItem(`cc-repo-url:${r.repo}`) || `https://github.com/${r.repo}`
                      const isDefault = (defaultBranches[r.repo] || r.default_branch) === bName
                      return (
                        <div key={bName} style={{ display: 'inline-flex', alignItems: 'center', gap: 0 }}>
                          <Link
                            to={`/${tenant}/lineage?repo=${encodeURIComponent(r.repo)}&branch=${bQ}`}
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: 5,
                              padding: '4px 10px',
                              borderRadius: '20px 0 0 20px',
                              fontSize: 11,
                              fontFamily: "'JetBrains Mono',monospace",
                              background: 'rgba(129,140,248,0.08)',
                              border: '1px solid rgba(129,140,248,0.22)',
                              borderRight: 'none',
                              color: 'var(--accent)',
                              textDecoration: 'none',
                              transition: 'all 0.2s',
                            }}
                          >
                            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                              <line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>
                            </svg>
                            {bName}
                          </Link>
                          <button
                            title="Recrawl this branch"
                            disabled={rcStatus === 'loading'}
                            onClick={() => recrawl(r.repo, bName, fullUrl)}
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              padding: '4px 8px',
                              borderRadius: '0 20px 20px 0',
                              fontSize: 11,
                              fontFamily: "'JetBrains Mono',monospace",
                              background: rcStatus === 'done'
                                ? 'rgba(34,197,94,0.12)'
                                : rcStatus === 'error'
                                ? 'rgba(239,68,68,0.1)'
                                : 'rgba(129,140,248,0.06)',
                              border: `1px solid ${
                                rcStatus === 'done' ? 'rgba(34,197,94,0.35)'
                                : rcStatus === 'error' ? 'rgba(239,68,68,0.3)'
                                : 'rgba(129,140,248,0.22)'
                              }`,
                              color: rcStatus === 'done'
                                ? 'var(--green)'
                                : rcStatus === 'error'
                                ? '#ef4444'
                                : 'var(--text-dim)',
                              cursor: rcStatus === 'loading' ? 'not-allowed' : 'pointer',
                              transition: 'all 0.2s',
                              opacity: rcStatus === 'loading' ? 0.6 : 1,
                            }}
                          >
                            {rcStatus === 'loading' ? (
                              <div style={{ width: 10, height: 10, border: '1.5px solid currentColor', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 0.7s linear infinite' }} />
                            ) : rcStatus === 'done' ? (
                              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                            ) : rcStatus === 'error' ? (
                              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                            ) : (
                              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
                              </svg>
                            )}
                          </button>
                          {/* Default branch star */}
                          <button
                            title={isDefault ? 'Default branch for cross-repo lineage' : 'Set as default branch'}
                            onClick={() => setDefaultBranch(r.repo, bName)}
                            style={{
                              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                              padding: '4px 7px', marginLeft: 3, borderRadius: 20, fontSize: 11,
                              background: isDefault ? 'rgba(245,158,11,0.12)' : 'transparent',
                              border: isDefault ? '1px solid rgba(245,158,11,0.3)' : '1px solid transparent',
                              color: isDefault ? '#f59e0b' : 'var(--text-dim)',
                              cursor: 'pointer', transition: 'all 0.15s',
                            }}
                            onMouseEnter={e => { if (!isDefault) { e.currentTarget.style.borderColor = 'rgba(245,158,11,0.2)'; e.currentTarget.style.color = '#f59e0b' } }}
                            onMouseLeave={e => { if (!isDefault) { e.currentTarget.style.borderColor = 'transparent'; e.currentTarget.style.color = 'var(--text-dim)' } }}
                          >
                            <svg width="10" height="10" viewBox="0 0 24 24" fill={isDefault ? '#f59e0b' : 'none'} stroke={isDefault ? '#f59e0b' : 'currentColor'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
                            </svg>
                          </button>
                        </div>
                      )
                    })}
                    {r.branches.length > 1 && (
                      <Link
                        to={`/${tenant}/compare?repo=${encodeURIComponent(r.repo)}`}
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 4,
                          padding: '4px 10px',
                          borderRadius: 20,
                          fontSize: 11,
                          background: 'rgba(168,85,247,0.08)',
                          border: '1px solid rgba(168,85,247,0.22)',
                          color: 'var(--purple)',
                          textDecoration: 'none',
                        }}
                      >
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                          <path d="M18 20V10"/>
                          <path d="M12 20V4"/>
                          <path d="M6 20v-6"/>
                        </svg>
                        Compare branches
                      </Link>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  )
}
