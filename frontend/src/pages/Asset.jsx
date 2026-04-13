import { useEffect, useRef, useState, useCallback } from 'react'
import { useSearchParams, useNavigate, useParams, Link } from 'react-router-dom'
import { api } from '../lib/api'
import { Spinner } from '../components/Spinner'
import { RunFunctionPanel } from '../components/RunFunctionPanel'
import { CodeBlock, highlightPython } from '../components/CodeBlock'

const STORE_KEY = 'code-crawler-changes'

function pn(name) {
  const p = (name || '').split('.')
  return { f: p[p.length - 1], m: p.slice(0, -1).join('.') }
}

function getChanges() {
  try { return JSON.parse(sessionStorage.getItem(STORE_KEY)) || {} } catch { return {} }
}
function setChanges(obj) {
  sessionStorage.setItem(STORE_KEY, JSON.stringify(obj))
}

// Build a GitHub file URL pointing to a specific line, or null if not a GitHub repo.
// repo is a slug (e.g. "sample-repo") — look up the original URL from localStorage.
function buildGithubUrl(repo, branch, file, lineno) {
  const stored = localStorage.getItem(`cc-repo-url:${repo}`) || ''
  let base = (stored || repo || '').replace(/\.git$/, '').replace(/\/$/, '')
  if (!base.includes('github.com')) return null
  if (!base.startsWith('http')) base = 'https://' + base
  const cleanFile = (file || '').replace(/^\//, '')
  return `${base}/blob/${branch}/${cleanFile}#L${lineno}`
}

// Bezier curve between two boxes
function bezierPath(x1, y1, x2, y2) {
  const cx = (x1 + x2) / 2
  return `M ${x1} ${y1} C ${cx} ${y1}, ${cx} ${y2}, ${x2} ${y2}`
}

export default function Asset() {
  const [qp] = useSearchParams()
  const repo = qp.get('repo') || ''
  const branch = qp.get('branch') || 'main'
  const assetId = qp.get('asset_id') || ''
  const nav = useNavigate()
  const { tenant } = useParams()

  const [rootData, setRootData] = useState(null)  // { node, upstream, downstream, methods }
  const [nodeCache, setNodeCache] = useState({})
  const nodeCacheRef = useRef({})  // always-current mirror of nodeCache for use in callbacks
  // Maps node id → { repo, branch } for cross-repo nodes — useRef avoids re-renders
  const nodeRepoMapRef = useRef({})
  const [expanded, setExpanded] = useState({})   // { [id]: { left: bool, right: bool } }
  const [selectedId, setSelectedId] = useState(null)
  const [selectedEdge, setSelectedEdge] = useState(null)  // key "fromId→toId"
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [sidebarNode, setSidebarNode] = useState(null)  // resolved node data
  const [editMode, setEditMode] = useState(false)
  const [editedSource, setEditedSource] = useState('')
  const [runPanelOpen, setRunPanelOpen] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [colPages, setColPages] = useState({})  // { "up_1": 10, "down_2": 20, ... }

  const showMore = (key) => setColPages(prev => ({ ...prev, [key]: (prev[key] || 10) + 10 }))

  // Test cases
  const [tcList, setTcList] = useState([])
  const [tcResults, setTcResults] = useState(null)
  const [runningTc, setRunningTc] = useState(false)
  const [tcDelId, setTcDelId] = useState(null)
  const [tcDelAll, setTcDelAll] = useState(false)
  const [tcSel, setTcSel] = useState({})
  const [tcBulkOpen, setTcBulkOpen] = useState(false)
  const [tcBulkJson, setTcBulkJson] = useState('')
  const [tcGenLoading, setTcGenLoading] = useState(false)

  // Sidebar resize (horizontal)
  const [sidebarWidth, setSidebarWidth] = useState(500)
  const sidebarDragging = useRef(false)
  const sidebarDragStart = useRef(0)
  const sidebarWidthStart = useRef(0)

  // Run panel resize (vertical split inside sidebar)
  const [runPanelHeight, setRunPanelHeight] = useState(220)
  const runDragging = useRef(false)
  const runDragStart = useRef(0)
  const runHeightStart = useRef(0)

  // Pan/zoom
  const edgesRef = useRef([])   // all graph edges for SVG line drawing
  const vpRef = useRef(null)
  const planeRef = useRef(null)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [zoom, setZoom] = useState(1)
  const panRef = useRef({ x: 0, y: 0 })
  const zoomRef = useRef(1)
  const isPanning = useRef(false)
  const startMouse = useRef({ x: 0, y: 0 })
  const startPan = useRef({ x: 0, y: 0 })

  const editHighlightRef = useRef(null)
  const editGutterRef = useRef(null)
  const editTextareaRef = useRef(null)

  const changesCount = Object.keys(getChanges()).length

  // Keep nodeCacheRef in sync so callbacks always see latest cache
  useEffect(() => { nodeCacheRef.current = nodeCache }, [nodeCache])

  // ── Test case handlers ───────────────────────────────────────────────────────
  const tcFn = sidebarNode ? sidebarNode.name : ''

  const reloadTc = async () => {
    const r = await api.testCases(repo, tcFn).catch(() => [])
    setTcList(r || [])
  }

  const runTc = async () => {
    setRunningTc(true); setTcResults({ running: true }); setTcSel({})
    try {
      const rows = await api.runTestCases({
        repo, branch, function_name: tcFn,
        edited_source: editedSource !== sidebarNode?.source ? editedSource : undefined,
      })
      setTcResults({ rows: rows || [] })
    } catch (e) { setTcResults({ error: e.message }) }
    finally { setRunningTc(false) }
  }

  const deleteTc = async (id) => {
    await api.deleteTestCase(id).catch(e => alert(e.message))
    setTcDelId(null); await reloadTc()
  }

  const deleteAllTc = async () => {
    await Promise.all(tcList.map(tc => api.deleteTestCase(tc.id))).catch(e => alert(e.message))
    setTcDelAll(false); await reloadTc()
  }

  const assertTc = async () => {
    const ids = Object.keys(tcSel)
    if (!ids.length) return
    await Promise.all(ids.map(id => {
      const r = tcResults.rows.find(r => String(r.id) === id)
      return api.patchTestCaseExpected(id, r?.result)
    })).catch(e => alert(e.message))
    setTcSel({})
    setTcResults(prev => prev?.rows ? { ...prev, rows: prev.rows.map(r => tcSel[String(r.id)] ? { ...r, passed: true } : r) } : prev)
  }

  const generateTc = async () => {
    const src = sidebarNode?.source
    if (!src) return
    setTcGenLoading(true)
    try {
      const d = await api.generateTestCases({ source: src })
      if (d.error) { alert(d.error); return }
      setTcBulkJson(JSON.stringify(d.cases, null, 2)); setTcBulkOpen(true)
    } catch (e) { alert(e.message) }
    finally { setTcGenLoading(false) }
  }

  const bulkImportTc = async () => {
    let cases
    try { cases = JSON.parse(tcBulkJson) } catch { alert('Invalid JSON'); return }
    if (!Array.isArray(cases)) { alert('Must be a JSON array'); return }
    await api.bulkCreateTestCases({ repo, function_name: tcFn, cases }).catch(e => alert(e.message))
    setTcBulkJson(''); setTcBulkOpen(false); await reloadTc()
  }

  // SVG lines state
  const [lines, setLines] = useState([])   // [{x1,y1,x2,y2,side,key}]

  const fetchNode = useCallback(async (id) => {
    if (nodeCacheRef.current[id]) return nodeCacheRef.current[id]
    const nr = nodeRepoMapRef.current[id]
    const nodeRepo = nr ? nr.repo : repo
    const nodeBranch = nr ? nr.branch : branch
    const data = await api.lineageNode(nodeRepo, nodeBranch, id)
    setNodeCache(prev => ({ ...prev, [id]: data }))
    registerCrossRepoNodes([...(data.upstream || []), ...(data.downstream || [])], nodeRepo, nodeBranch)
    return data
  }, [repo, branch])

  // Load test cases when sidebar node changes
  useEffect(() => {
    if (!sidebarNode) { setTcList([]); setTcResults(null); setTcSel({}); return }
    const fn = pn(sidebarNode.name).f
    api.testCases(repo, fn).then(r => setTcList(r || [])).catch(() => setTcList([]))
    setTcResults(null); setTcSel({})
  }, [sidebarNode?.id])

  // Register nodes into the ref so fetchNode/toggleExpand use the right repo context.
  // contextRepo/contextBranch: the repo used for the fetch that produced these nodes.
  // Any node fetched from a non-page repo must be registered so nested expansions work.
  const registerCrossRepoNodes = (nodes, contextRepo, contextBranch) => {
    for (const n of (nodes || [])) {
      if (n.is_cross_repo && n.repo_url) {
        // Explicitly cross-repo node with its own repo URL
        nodeRepoMapRef.current[n.id] = { repo: n.repo_url, branch: n.branch }
      } else if (contextRepo && contextRepo !== repo && !nodeRepoMapRef.current[n.id]) {
        // Node is "same-repo" from the API's perspective but was fetched from a
        // different repo than the current page — inherit the fetch context so
        // further expansions hit the right repo.
        nodeRepoMapRef.current[n.id] = { repo: contextRepo, branch: contextBranch || branch }
      }
    }
  }

  // ── Initial load ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (!repo || !assetId) return
    setNodeCache({}); setExpanded({}); setRootData(null)
    setSelectedId(null); setSelectedEdge(null); setSidebarOpen(false)
    setLoading(true); setError(false); setColPages({})
    nodeRepoMapRef.current = {}; nodeCacheRef.current = {}

    api.lineageNode(repo, branch, assetId).then(data => {
      const cache = { [assetId]: data }
      setNodeCache(cache)
      nodeCacheRef.current = cache
      setRootData(data)
      setLoading(false)
      registerCrossRepoNodes([...(data.upstream || []), ...(data.downstream || [])], repo, branch)
      // Auto-expand L1 so neighbors are visible immediately
      setExpanded({
        [assetId]: {
          left: (data.upstream || []).length > 0,
          right: (data.downstream || []).length > 0,
        }
      })
    }).catch(() => { setLoading(false); setError(true) })
  }, [repo, branch, assetId])

  // ── Expand / collapse a node's left or right side ───────────────────────
  const ex = (id) => expanded[id] || { left: false, right: false }

  const toggleExpand = useCallback((id, side) => {
    // 1. Toggle the expanded flag
    setExpanded(prev => {
      const cur = prev[id] || { left: false, right: false }
      return { ...prev, [id]: { ...cur, [side]: !cur[side] } }
    })
    // 2. Fetch this node's neighbors if we don't have them yet
    if (!nodeCacheRef.current[id]) {
      const nr = nodeRepoMapRef.current[id]
      const r = nr ? nr.repo : repo
      const b = nr ? nr.branch : branch
      api.lineageNode(r, b, id).then(data => {
        setNodeCache(prev => ({ ...prev, [id]: data }))
        registerCrossRepoNodes([...(data.upstream || []), ...(data.downstream || [])], r, b)
      })
    }
  }, [repo, branch])

  // Open sidebar for a node
  const openSidebar = useCallback(async (id) => {
    setSidebarOpen(true)
    setSelectedId(id)
    setEditMode(false)
    setRunPanelOpen(false)

    // Get node data (use ref for latest cache, cross-repo context if available)
    let data = nodeCacheRef.current[id]
    let nodeRepo = repo, nodeBranch = branch
    if (!data) {
      const nr = nodeRepoMapRef.current[id]
      nodeRepo = nr ? nr.repo : repo
      nodeBranch = nr ? nr.branch : branch
      data = await api.lineageNode(nodeRepo, nodeBranch, id)
      setNodeCache(prev => ({ ...prev, [id]: data }))
    }
    setSidebarNode(data.node || data)
    registerCrossRepoNodes([...(data.upstream || []), ...(data.downstream || [])], nodeRepo, nodeBranch)

    // Load edited source if saved
    const changes = getChanges()
    if (changes[id]) setEditedSource(changes[id].edited)
    else setEditedSource(data.node?.source || data.source || '')
  }, [repo, branch])

  const closeSidebar = () => {
    setSidebarOpen(false)
    setSelectedId(null)
    setRunPanelOpen(false)
    setEditMode(false)
  }

  const saveEdit = () => {
    if (!sidebarNode) return
    const changes = getChanges()
    const id = sidebarNode.id
    changes[id] = {
      name: sidebarNode.name,
      file: sidebarNode.file,
      lineno: sidebarNode.lineno,
      repo,
      branch,
      original: sidebarNode.source || '',
      edited: editedSource,
    }
    setChanges(changes)
    setEditMode(false)
  }

  const copySrc = () => {
    const src = editMode ? editedSource : (sidebarNode?.source || '')
    navigator.clipboard.writeText(src).catch(() => {})
  }

  // Pan events
  const onVpMouseDown = useCallback((e) => {
    if (e.button !== 0) return
    if (e.target.closest('.node-card') || e.target.closest('.center-card') || e.target.closest('.expand-btn')) return
    isPanning.current = true
    startMouse.current = { x: e.clientX, y: e.clientY }
    startPan.current = { ...panRef.current }
  }, [])

  const onMouseMove = useCallback((e) => {
    // Pan
    if (isPanning.current) {
      const dx = e.clientX - startMouse.current.x
      const dy = e.clientY - startMouse.current.y
      panRef.current = { x: startPan.current.x + dx, y: startPan.current.y + dy }
      setPan({ ...panRef.current })
    }
    // Sidebar width drag (drag handle on left edge)
    if (sidebarDragging.current) {
      const dx = sidebarDragStart.current - e.clientX
      const newW = Math.max(320, Math.min(window.innerWidth * 0.9, sidebarWidthStart.current + dx))
      setSidebarWidth(newW)
    }
    // Run panel height drag
    if (runDragging.current) {
      const dy = e.clientY - runDragStart.current
      const newH = Math.max(80, Math.min(window.innerHeight * 0.8, runHeightStart.current - dy))
      setRunPanelHeight(newH)
    }
  }, [])

  const onMouseUp = useCallback(() => {
    isPanning.current = false
    sidebarDragging.current = false
    runDragging.current = false
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }, [])

  const onWheel = useCallback((e) => {
    e.preventDefault()
    const delta = e.deltaY > 0 ? -0.08 : 0.08
    const newZoom = Math.max(0.3, Math.min(2, zoomRef.current + delta))
    const vp = vpRef.current
    if (!vp) return
    const rect = vp.getBoundingClientRect()
    const cx = e.clientX - rect.left
    const cy = e.clientY - rect.top
    panRef.current = {
      x: cx - (cx - panRef.current.x) * (newZoom / zoomRef.current),
      y: cy - (cy - panRef.current.y) * (newZoom / zoomRef.current),
    }
    zoomRef.current = newZoom
    setPan({ ...panRef.current })
    setZoom(newZoom)
  }, [])

  useEffect(() => {
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
    const vp = vpRef.current
    if (vp) vp.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup', onMouseUp)
      if (vp) vp.removeEventListener('wheel', onWheel)
    }
  }, [onMouseMove, onMouseUp, onWheel])

  const centerView = useCallback(() => {
    const vp = vpRef.current
    const plane = planeRef.current
    if (!vp || !plane) return
    const vpR = vp.getBoundingClientRect()
    const pw = plane.scrollWidth * zoomRef.current
    const ph = plane.scrollHeight * zoomRef.current
    panRef.current = {
      x: (vpR.width - pw) / 2,
      y: Math.max(20, (vpR.height - ph) / 2),
    }
    setPan({ ...panRef.current })
  }, [])

  // Draw SVG lines for all edges in edgesRef.
  // Lines start/end at expand button circles, or card edge center if no button.
  const drawLines = useCallback(() => {
    const plane = planeRef.current
    if (!plane) return
    const planeRect = plane.getBoundingClientRect()
    const z = zoomRef.current

    setLines(edgesRef.current.map(edge => {
      const fromEl = document.querySelector(`[data-node-id="${edge.fromId}"]`)
      const toEl   = document.querySelector(`[data-node-id="${edge.toId}"]`)
      if (!fromEl || !toEl) return null

      // Source: right expand button or card right-edge center
      const rightBtn = document.querySelector(`[data-expand-right="${edge.fromId}"]`)
      let sx, sy
      if (rightBtn) {
        const br = rightBtn.getBoundingClientRect()
        sx = br.left + br.width / 2
        sy = br.top + br.height / 2
      } else {
        const fr = fromEl.getBoundingClientRect()
        sx = fr.right
        sy = fr.top + fr.height / 2
      }

      // Target: left expand button or card left-edge center
      const leftBtn = document.querySelector(`[data-expand-left="${edge.toId}"]`)
      let ex, ey
      if (leftBtn) {
        const br = leftBtn.getBoundingClientRect()
        ex = br.left + br.width / 2
        ey = br.top + br.height / 2
      } else {
        const tr = toEl.getBoundingClientRect()
        ex = tr.left
        ey = tr.top + tr.height / 2
      }

      return {
        x1: (sx - planeRect.left) / z,
        y1: (sy - planeRect.top) / z,
        x2: (ex - planeRect.left) / z,
        y2: (ey - planeRect.top) / z,
        side: edge.side,
        key: `${edge.fromId}→${edge.toId}`,
        fromId: edge.fromId, toId: edge.toId,
      }
    }).filter(Boolean))
  }, [])

  // Redraw lines after render
  useEffect(() => {
    requestAnimationFrame(() => requestAnimationFrame(drawLines))
  }, [rootData, expanded, nodeCache, drawLines, pan, zoom])

  // Center on initial load
  useEffect(() => {
    if (rootData && vpRef.current) {
      setTimeout(centerView, 100)
    }
  }, [rootData, centerView])

  if (!assetId) return null

  const data = nodeCache[assetId]
  const n = data?.node
  const upList = data?.upstream || []
  const downList = data?.downstream || []
  const info = n ? pn(n.name) : {}

  // Nodes touched by the selected edge
  const selLine = selectedEdge ? lines.find(l => l.key === selectedEdge) : null
  const highlightedIds = selLine ? new Set([selLine.fromId, selLine.toId]) : new Set()

  // ── Build columns via BFS ──────────────────────────────────────────────
  // BFS from center (pos 0). Left expansion = pos-1, right = pos+1.
  // pos 0 nodes (cross-direction of direct neighbors) render below center.
  const upstreamCols = []
  const downstreamCols = []
  const centerExtra = []     // pos 0 — shown below center card
  const allEdges = []
  const nodeSide = {}        // id → 'upstream' | 'downstream' (for styling)
  let totalUpstream = 0
  let totalDownstream = 0

  if (data) {
    const seen = new Set([assetId])
    const colMap = {}
    const edgeSeen = new Set()
    const queue = [{ id: assetId, pos: 0 }]

    while (queue.length) {
      const { id, pos } = queue.shift()
      const { left, right } = expanded[id] || {}
      const c = nodeCache[id]

      if (left && c?.upstream) {
        for (const nd of c.upstream) {
          const ek = `${nd.id}→${id}`
          if (!edgeSeen.has(ek)) { edgeSeen.add(ek); allEdges.push({ fromId: nd.id, toId: id, side: 'upstream' }) }
          if (!seen.has(nd.id)) {
            seen.add(nd.id)
            nodeSide[nd.id] = 'upstream'
            const p = pos - 1
            ;(colMap[p] ??= []).push(nd)
            queue.push({ id: nd.id, pos: p })
          }
        }
      }

      if (right && c?.downstream) {
        for (const nd of c.downstream) {
          const ek = `${id}→${nd.id}`
          if (!edgeSeen.has(ek)) { edgeSeen.add(ek); allEdges.push({ fromId: id, toId: nd.id, side: 'downstream' }) }
          if (!seen.has(nd.id)) {
            seen.add(nd.id)
            nodeSide[nd.id] = 'downstream'
            const p = pos + 1
            ;(colMap[p] ??= []).push(nd)
            queue.push({ id: nd.id, pos: p })
          }
        }
      }
    }

    for (const [k, nodes] of Object.entries(colMap)) {
      const p = Number(k)
      if (p === 0) centerExtra.push(...nodes)
      else if (p < 0) { upstreamCols.push({ pos: p, nodes }); totalUpstream += nodes.length }
      else { downstreamCols.push({ pos: p, nodes }); totalDownstream += nodes.length }
    }
    upstreamCols.sort((a, b) => a.pos - b.pos)
    downstreamCols.sort((a, b) => a.pos - b.pos)
  }
  // Store for drawLines (called after render via useEffect)
  edgesRef.current = allEdges

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      {/* Navbar */}
      <nav className="navbar">
        <div className="navbar-brand">
          <Link to={`/${tenant}/dashboard`} style={{ color: 'var(--text-dim)', textDecoration: 'none', fontSize: 13, display: 'flex', alignItems: 'center', gap: 4 }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="m15 18-6-6 6-6"/>
            </svg>
            Dashboard
          </Link>
          <span style={{ color: 'var(--border)' }}>/</span>
          <Link to={`/${tenant}/lineage?repo=${encodeURIComponent(repo)}&branch=${encodeURIComponent(branch)}`} style={{ color: 'var(--text-dim)', textDecoration: 'none', fontSize: 13 }}>
            Lineage
          </Link>
          <span style={{ color: 'var(--border)' }}>/</span>
          Asset Lineage
        </div>
        <div className="navbar-right">
          {changesCount > 0 && (
            <Link
              to={`/${tenant}/changes?repo=${encodeURIComponent(repo)}&branch=${encodeURIComponent(branch)}`}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: '4px 10px', border: '1px solid var(--border)',
                borderRadius: 6, fontSize: 11, fontFamily: "'JetBrains Mono',monospace",
                color: 'var(--text)', textDecoration: 'none',
                background: 'transparent',
              }}
            >
              <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: 'var(--green)' }} />
              {changesCount} Changes
            </Link>
          )}
        </div>
      </nav>

      {/* Info bar */}
      {n && (
        <div style={{
          padding: '8px 24px', borderBottom: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', gap: 14,
          background: 'rgba(17,24,39,0.5)', backdropFilter: 'blur(8px)', fontSize: 13,
        }}>
          <span style={{ fontFamily: "'JetBrains Mono',monospace", fontWeight: 600, color: 'var(--blue)' }}>
            {info.f}
          </span>
          <span style={{ color: 'var(--border)' }}>|</span>
          <span style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 12, color: 'var(--cyan)' }}>
            ↑ {upList.length} upstream
          </span>
          <span style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 12, color: 'var(--purple)' }}>
            ↓ {downList.length} downstream
          </span>
          <span style={{ color: 'var(--border)' }}>|</span>
          <span style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: 'var(--text-dim)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
            {n.file}:{n.lineno}
          </span>
          <button
            onClick={centerView}
            style={{
              background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-dim)',
              padding: '4px 10px', borderRadius: 6, fontSize: 11, fontFamily: "'JetBrains Mono',monospace",
              cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4, transition: 'all 0.15s',
            }}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <circle cx="12" cy="12" r="3"/>
              <path d="M12 2v4m0 12v4M2 12h4m12 0h4"/>
            </svg>
            Recenter
          </button>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, padding: '80px 20px', color: 'var(--text-dim)', fontSize: 13 }}>
          <Spinner />
          Loading lineage...
        </div>
      )}

      {/* Error */}
      {error && (
        <div style={{ textAlign: 'center', padding: '80px 20px', color: 'var(--text-dim)' }}>
          Node not found. <Link to={`/${tenant}/lineage?repo=${encodeURIComponent(repo)}&branch=${encodeURIComponent(branch)}`}>Back to lineage</Link>
        </div>
      )}

      {/* Graph viewport */}
      {!loading && !error && (
        <div
          ref={vpRef}
          onMouseDown={onVpMouseDown}
          onClick={() => setSelectedEdge(null)}
          style={{
            flex: 1,
            overflow: 'hidden',
            position: 'relative',
            cursor: isPanning.current ? 'grabbing' : 'grab',
            userSelect: 'none',
            background: 'radial-gradient(circle at 1px 1px, rgba(255,255,255,0.18) 1.5px, transparent 0)',
            backgroundSize: '24px 24px',
          }}
        >
          {/* Graph plane */}
          <div
            ref={planeRef}
            style={{
              position: 'absolute',
              top: 0, left: 0,
              minWidth: '100%', minHeight: '100%',
              display: 'flex',
              alignItems: 'flex-start',
              justifyContent: 'center',
              padding: '48px 40px',
              gap: 0,
              transform: `translate(${pan.x}px,${pan.y}px) scale(${zoom})`,
              transformOrigin: '0 0',
              transition: 'transform 0.05s ease-out',
            }}
          >
            {/* SVG for lines */}
            <svg style={{
              position: 'absolute', top: 0, left: 0,
              width: '100%', height: '100%',
              pointerEvents: 'none', zIndex: 10,
              overflow: 'visible',
            }}>
              <style>{`
                @keyframes flowDash { to { stroke-dashoffset: -10; } }
              `}</style>
              {lines.map(l => {
                const isSel = l.key === selectedEdge
                const color = l.side === 'upstream' ? 'var(--cyan)' : 'var(--purple)'
                const d = bezierPath(l.x1, l.y1, l.x2, l.y2)
                return (
                  <g
                    key={l.key}
                    onClick={e => { e.stopPropagation(); setSelectedEdge(isSel ? null : l.key) }}
                    style={{ cursor: 'pointer', pointerEvents: 'auto' }}
                  >
                    {/* Wide invisible hit area */}
                    <path d={d} fill="none" stroke="transparent" strokeWidth={14} />
                    {/* Visible line */}
                    <path
                      d={d}
                      fill="none"
                      stroke={color}
                      strokeWidth={isSel ? 2.5 : 1.5}
                      strokeDasharray={isSel ? 'none' : '6 4'}
                      opacity={isSel ? 1 : 0.75}
                      style={isSel ? {} : { animation: 'flowDash 0.8s linear infinite' }}
                    />
                    {/* Glow for selected */}
                    {isSel && (
                      <path d={d} fill="none" stroke={color} strokeWidth={6} opacity={0.18} />
                    )}
                  </g>
                )
              })}
            </svg>

            {/* Upstream group — one label, multiple position-based columns */}
            {upstreamCols.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', flexShrink: 0, marginRight: 56, zIndex: 2, position: 'relative' }}>
                <div style={{
                  fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '1.2px',
                  padding: '4px 12px', borderRadius: 20, textAlign: 'center', marginBottom: 6,
                  color: 'var(--cyan)', background: 'rgba(6,182,212,0.08)', border: '1px solid rgba(6,182,212,0.15)',
                  alignSelf: 'center',
                }}>
                  Upstream ({totalUpstream})
                </div>
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 56 }}>
                  {upstreamCols.map(col => {
                    const pageKey = `col_${col.pos}`
                    const limit = colPages[pageKey] || 10
                    const visible = col.nodes.slice(0, limit)
                    const remaining = col.nodes.length - limit
                    return (
                      <div key={col.pos} style={{ display: 'flex', flexDirection: 'column', gap: 8, width: 240 }}>
                        {visible.map(nd => {
                          const upCount = nodeCache[nd.id]?.upstream?.length ?? nd.upstream_count ?? 0
                          const downCount = nodeCache[nd.id]?.downstream?.length ?? nd.downstream_count ?? 0
                          return (
                            <NodeCard
                              key={nd.id}
                              node={nd}
                              side="upstream"
                              selected={selectedId === nd.id}
                              highlighted={highlightedIds.has(nd.id)}
                              onClick={() => { setSelectedEdge(null); openSidebar(nd.id) }}
                              nodeId={nd.id}
                              onExpandLeft={upCount !== 0 ? () => toggleExpand(nd.id, 'left') : undefined}
                              expandedLeft={ex(nd.id).left}
                              leftCount={upCount}
                              onExpandRight={downCount !== 0 ? () => toggleExpand(nd.id, 'right') : undefined}
                              expandedRight={ex(nd.id).right}
                              rightCount={downCount}
                            />
                          )
                        })}
                        {remaining > 0 && (
                          <button
                            onClick={() => showMore(pageKey)}
                            style={{
                              marginTop: 2, padding: '7px 0', width: '100%',
                              background: 'transparent', border: '1px dashed rgba(6,182,212,0.3)',
                              borderRadius: 'var(--radius)', color: 'var(--cyan)',
                              fontFamily: "'JetBrains Mono',monospace", fontSize: 11,
                              cursor: 'pointer', transition: 'all 0.15s',
                            }}
                            onMouseEnter={e => e.currentTarget.style.background = 'rgba(6,182,212,0.06)'}
                            onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                          >
                            +{Math.min(10, remaining)} more ({remaining} left)
                          </button>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            {/* Center node */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, flexShrink: 0, width: 320, alignItems: 'center', zIndex: 2, position: 'relative' }}>
              {n && (
                <div
                  id="center-node-el"
                  data-node-id={assetId}
                  className="center-card"
                  onClick={() => { setSelectedEdge(null); openSidebar(assetId) }}
                  style={{
                    background: 'linear-gradient(135deg, rgba(59,130,246,0.06) 0%, var(--surface) 100%)',
                    border: `2px solid ${highlightedIds.has(assetId) ? 'var(--cyan)' : 'var(--blue)'}`,
                    borderRadius: 'var(--radius)',
                    padding: '20px 24px',
                    textAlign: 'center',
                    position: 'relative',
                    boxShadow: highlightedIds.has(assetId)
                      ? '0 0 0 3px rgba(6,182,212,0.35), 0 0 32px rgba(6,182,212,0.2)'
                      : '0 0 40px -10px rgba(59,130,246,0.3), 0 0 0 1px rgba(59,130,246,0.08)',
                    cursor: 'pointer',
                    width: '100%',
                    transition: 'box-shadow 0.2s, border-color 0.2s',
                  }}
                >
                  {/* Expand left button */}
                  {upList.length > 0 && (
                    <button
                      className="expand-btn"
                      data-expand-left={assetId}
                      onClick={e => { e.stopPropagation(); toggleExpand(assetId, 'left') }}
                      style={{
                        position: 'absolute', top: '50%', left: -17,
                        transform: 'translateY(-50%)',
                        width: 34, height: 34, borderRadius: '50%',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        border: `2px solid ${ex(assetId).left ? 'var(--cyan)' : 'var(--border)'}`,
                        background: ex(assetId).left ? 'rgba(6,182,212,0.12)' : 'var(--bg)',
                        cursor: 'pointer', transition: 'all 0.15s', fontSize: 14, zIndex: 10,
                        color: 'var(--cyan)',
                      }}
                    >
                      ←
                      <span style={{
                        position: 'absolute', top: -5, right: -5,
                        background: 'var(--surface)', border: '1.5px solid var(--cyan)',
                        borderRadius: 8, fontSize: 9, fontWeight: 700, color: 'var(--cyan)',
                        padding: '0 5px', lineHeight: '16px',
                      }}>{upList.length}</span>
                    </button>
                  )}

                  <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 16, fontWeight: 700, color: 'var(--blue)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {info.f}
                  </div>
                  {info.m && (
                    <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: 'var(--text-dim)', marginTop: 3, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {info.m}
                    </div>
                  )}
                  <div style={{ fontSize: 10, color: 'hsl(215,20%,50%)', fontFamily: "'JetBrains Mono',monospace", marginTop: 8 }}>
                    {n.file}:{n.lineno}
                  </div>
                  <div style={{ display: 'inline-flex', gap: 12, marginTop: 10, fontSize: 11, fontFamily: "'JetBrains Mono',monospace" }}>
                    <span style={{ color: upList.length ? 'var(--cyan)' : 'var(--text-dim)', opacity: upList.length ? 1 : 0.4 }}>
                      ↑ {upList.length}
                    </span>
                    <span style={{ color: downList.length ? 'var(--purple)' : 'var(--text-dim)', opacity: downList.length ? 1 : 0.4 }}>
                      ↓ {downList.length}
                    </span>
                  </div>

                  {/* Expand right button */}
                  {downList.length > 0 && (
                    <button
                      className="expand-btn"
                      data-expand-right={assetId}
                      onClick={e => { e.stopPropagation(); toggleExpand(assetId, 'right') }}
                      style={{
                        position: 'absolute', top: '50%', right: -17,
                        transform: 'translateY(-50%)',
                        width: 34, height: 34, borderRadius: '50%',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        border: `2px solid ${ex(assetId).right ? 'var(--purple)' : 'var(--border)'}`,
                        background: ex(assetId).right ? 'rgba(168,85,247,0.12)' : 'var(--bg)',
                        cursor: 'pointer', transition: 'all 0.15s', fontSize: 14, zIndex: 10,
                        color: 'var(--purple)',
                      }}
                    >
                      →
                      <span style={{
                        position: 'absolute', top: -5, right: -5,
                        background: 'var(--surface)', border: '1.5px solid var(--purple)',
                        borderRadius: 8, fontSize: 9, fontWeight: 700, color: 'var(--purple)',
                        padding: '0 5px', lineHeight: '16px',
                      }}>{downList.length}</span>
                    </button>
                  )}
                </div>
              )}

              {/* Cross-direction of direct neighbors — rendered below center */}
              {centerExtra.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, width: 240 }}>
                  {centerExtra.map(nd => {
                    const upCount = nodeCache[nd.id]?.upstream?.length ?? nd.upstream_count ?? 0
                    const downCount = nodeCache[nd.id]?.downstream?.length ?? nd.downstream_count ?? 0
                    const side = nodeSide[nd.id] || 'upstream'
                    return (
                      <NodeCard
                        key={nd.id}
                        node={nd}
                        side={side}
                        selected={selectedId === nd.id}
                        highlighted={highlightedIds.has(nd.id)}
                        onClick={() => { setSelectedEdge(null); openSidebar(nd.id) }}
                        nodeId={nd.id}
                        onExpandLeft={upCount !== 0 ? () => toggleExpand(nd.id, 'left') : undefined}
                        expandedLeft={ex(nd.id).left}
                        leftCount={upCount}
                        onExpandRight={downCount !== 0 ? () => toggleExpand(nd.id, 'right') : undefined}
                        expandedRight={ex(nd.id).right}
                        rightCount={downCount}
                      />
                    )
                  })}
                </div>
              )}
            </div>

            {/* Downstream group — one label, multiple position-based columns */}
            {downstreamCols.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', flexShrink: 0, marginLeft: 56, zIndex: 2, position: 'relative' }}>
                <div style={{
                  fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '1.2px',
                  padding: '4px 12px', borderRadius: 20, textAlign: 'center', marginBottom: 6,
                  color: 'var(--purple)', background: 'rgba(168,85,247,0.08)', border: '1px solid rgba(168,85,247,0.15)',
                  alignSelf: 'center',
                }}>
                  Downstream ({totalDownstream})
                </div>
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 56 }}>
                  {downstreamCols.map(col => {
                    const pageKey = `col_${col.pos}`
                    const limit = colPages[pageKey] || 10
                    const visible = col.nodes.slice(0, limit)
                    const remaining = col.nodes.length - limit
                    return (
                      <div key={col.pos} style={{ display: 'flex', flexDirection: 'column', gap: 8, width: 240 }}>
                        {visible.map(nd => {
                          const upCount = nodeCache[nd.id]?.upstream?.length ?? nd.upstream_count ?? 0
                          const downCount = nodeCache[nd.id]?.downstream?.length ?? nd.downstream_count ?? 0
                          return (
                            <NodeCard
                              key={nd.id}
                              node={nd}
                              side="downstream"
                              selected={selectedId === nd.id}
                              highlighted={highlightedIds.has(nd.id)}
                              onClick={() => { setSelectedEdge(null); openSidebar(nd.id) }}
                              nodeId={nd.id}
                              onExpandLeft={upCount !== 0 ? () => toggleExpand(nd.id, 'left') : undefined}
                              expandedLeft={ex(nd.id).left}
                              leftCount={upCount}
                              onExpandRight={downCount !== 0 ? () => toggleExpand(nd.id, 'right') : undefined}
                              expandedRight={ex(nd.id).right}
                              rightCount={downCount}
                            />
                          )
                        })}
                        {remaining > 0 && (
                          <button
                            onClick={() => showMore(pageKey)}
                            style={{
                              marginTop: 2, padding: '7px 0', width: '100%',
                              background: 'transparent', border: '1px dashed rgba(168,85,247,0.3)',
                              borderRadius: 'var(--radius)', color: 'var(--purple)',
                              fontFamily: "'JetBrains Mono',monospace", fontSize: 11,
                              cursor: 'pointer', transition: 'all 0.15s',
                            }}
                            onMouseEnter={e => e.currentTarget.style.background = 'rgba(168,85,247,0.06)'}
                            onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                          >
                            +{Math.min(10, remaining)} more ({remaining} left)
                          </button>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
          </div>

          {/* Zoom controls */}
          <div style={{ position: 'absolute', bottom: 16, left: 16, display: 'flex', flexDirection: 'column', gap: 2, zIndex: 100 }}>
            <button
              onClick={() => { zoomRef.current = Math.min(2, zoomRef.current + 0.15); setZoom(zoomRef.current) }}
              style={{ width: 32, height: 32, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text-dim)', fontSize: 16, fontWeight: 600, cursor: 'pointer', borderRadius: '8px 8px 0 0', transition: 'all 0.15s' }}
            >+</button>
            <button
              onClick={() => { zoomRef.current = Math.max(0.3, zoomRef.current - 0.15); setZoom(zoomRef.current) }}
              style={{ width: 32, height: 32, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text-dim)', fontSize: 16, fontWeight: 600, cursor: 'pointer', borderRadius: '0 0 8px 8px', transition: 'all 0.15s' }}
            >−</button>
          </div>
          <div style={{ position: 'absolute', bottom: 90, left: 16, fontSize: 9, color: 'var(--text-dim)', textAlign: 'center', fontFamily: "'JetBrains Mono',monospace" }}>
            {Math.round(zoom * 100)}%
          </div>
        </div>
      )}

      {/* Sidebar overlay */}
      {sidebarOpen && (
        <div
          onClick={closeSidebar}
          style={{
            position: 'fixed', inset: 0,
            background: 'rgba(0,0,0,0.35)',
            zIndex: 500,
          }}
        />
      )}

      {/* Sidebar */}
      <div style={{
        position: 'fixed', top: 0, right: 0,
        width: sidebarWidth, height: '100vh',
        background: 'var(--bg)', borderLeft: '1px solid var(--border)',
        zIndex: 600, display: 'flex', flexDirection: 'column',
        transform: sidebarOpen ? 'translateX(0)' : 'translateX(100%)',
        transition: sidebarDragging.current ? 'none' : 'transform 0.25s ease',
        boxShadow: '-8px 0 32px rgba(0,0,0,0.5)',
      }}>
        {/* Left-edge resize handle */}
        <div
          onMouseDown={e => {
            e.preventDefault()
            sidebarDragging.current = true
            sidebarDragStart.current = e.clientX
            sidebarWidthStart.current = sidebarWidth
            document.body.style.cursor = 'col-resize'
            document.body.style.userSelect = 'none'
          }}
          style={{
            position: 'absolute', left: 0, top: 0, width: 6, height: '100%',
            cursor: 'col-resize', zIndex: 10,
            background: 'transparent',
            transition: 'background 0.15s',
          }}
          onMouseEnter={e => e.currentTarget.style.background = 'rgba(59,130,246,0.25)'}
          onMouseLeave={e => { if (!sidebarDragging.current) e.currentTarget.style.background = 'transparent' }}
        />
        {sidebarNode && (
          <>
            {/* Header */}
            <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
              <span style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 15, fontWeight: 700, color: 'var(--blue)', flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {pn(sidebarNode.name).f}
              </span>
              <button onClick={closeSidebar} style={{ width: 28, height: 28, border: '1px solid var(--border)', borderRadius: 6, background: 'transparent', color: 'var(--text-dim)', fontSize: 16, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', transition: 'all 0.15s', flexShrink: 0 }}>
                ×
              </button>
            </div>

            {/* Meta */}
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', fontFamily: "'JetBrains Mono',monospace", fontSize: 12, display: 'flex', flexDirection: 'column', gap: 6, flexShrink: 0, background: 'rgba(17,24,39,0.3)' }}>
              {[
                ['file', sidebarNode.file],
                ['line', sidebarNode.lineno],
                ['upstream', <span style={{ color: 'var(--cyan)' }}>{(sidebarNode.downstream_ids || []).length}</span>],
              ].map(([label, value]) => (
                <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ color: 'var(--text-dim)', minWidth: 70, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.5px' }}>{label}</span>
                  <span style={{ color: 'var(--text)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{value}</span>
                </div>
              ))}
              <div style={{ display: 'flex', gap: 8, marginTop: 4, flexWrap: 'wrap' }}>
                <button
                  onClick={() => {
                    const nr = nodeRepoMapRef.current[sidebarNode.id]
                    const nodeRepo = nr ? nr.repo : repo
                    const nodeBranch = nr ? nr.branch : branch
                    nav(`/${tenant}/asset?repo=${encodeURIComponent(nodeRepo)}&branch=${encodeURIComponent(nodeBranch)}&asset_id=${encodeURIComponent(sidebarNode.id)}`)
                  }}
                  style={{ padding: '5px 12px', fontSize: 11, fontFamily: "'JetBrains Mono',monospace", background: 'transparent', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--blue)', cursor: 'pointer', transition: 'all 0.15s' }}
                >
                  View lineage →
                </button>
                {(() => {
                  const ghUrl = buildGithubUrl(repo, branch, sidebarNode.file, sidebarNode.lineno)
                  if (!ghUrl) return null
                  return (
                    <a
                      href={ghUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{
                        display: 'inline-flex', alignItems: 'center', gap: 5,
                        padding: '5px 12px', fontSize: 11,
                        fontFamily: "'JetBrains Mono',monospace",
                        background: 'transparent',
                        border: '1px solid var(--border)',
                        borderRadius: 6, color: 'var(--text-dim)',
                        textDecoration: 'none', transition: 'all 0.15s',
                      }}
                      onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent)'; e.currentTarget.style.color = 'var(--accent)' }}
                      onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.color = 'var(--text-dim)' }}
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z"/>
                      </svg>
                      Open in GitHub
                    </a>
                  )
                })()}</div>
            </div>

            {/* Methods list — only for class nodes */}
            {sidebarNode.node_type === 'class' && (() => {
              const methods = nodeCache[sidebarNode.id]?.methods || rootData?.methods || []
              if (!methods.length) return null
              return (
                <div style={{ borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
                  <div style={{ padding: '8px 20px 6px', fontFamily: "'JetBrains Mono',monospace", fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--text-dim)' }}>
                    Methods ({methods.length})
                  </div>
                  <div style={{ maxHeight: 220, overflowY: 'auto' }}>
                    {methods.map(m => {
                      const shortName = m.name.split('.').pop()
                      return (
                        <div
                          key={m.id}
                          onClick={() => nav(`/${tenant}/asset?repo=${encodeURIComponent(repo)}&branch=${encodeURIComponent(branch)}&asset_id=${encodeURIComponent(m.id)}`)}
                          style={{
                            padding: '6px 20px',
                            cursor: 'pointer',
                            display: 'flex',
                            alignItems: 'center',
                            gap: 8,
                            transition: 'background 0.12s',
                          }}
                          onMouseEnter={e => e.currentTarget.style.background = 'rgba(129,140,248,0.07)'}
                          onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                        >
                          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                            <path d="M8 6l-6 6 6 6"/><path d="M16 6l6 6-6 6"/>
                          </svg>
                          <span style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {shortName}
                          </span>
                          <span style={{ marginLeft: 'auto', fontFamily: "'JetBrains Mono',monospace", fontSize: 10, color: 'var(--text-dim)', flexShrink: 0 }}>
                            :{m.lineno}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )
            })()}

            {/* Code header */}
            <div style={{ padding: '8px 20px', display: 'flex', alignItems: 'center', gap: 8, borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
              <span style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--text-dim)', flex: 1 }}>Source code</span>
              <button onClick={copySrc} style={sbCodeBtnStyle}>Copy</button>
              {sidebarNode.node_type !== 'class' && (
                <>
                  <button
                    onClick={() => setEditMode(v => !v)}
                    style={{ ...sbCodeBtnStyle, ...(editMode ? { borderColor: 'var(--blue)', color: 'var(--blue)' } : {}) }}
                  >Edit</button>
                  {editMode && (
                    <button onClick={saveEdit} style={{ ...sbCodeBtnStyle, borderColor: 'var(--green)', color: 'var(--green)' }}>Save</button>
                  )}
                  <button
                    onClick={() => setRunPanelOpen(v => !v)}
                    style={{ ...sbCodeBtnStyle, borderColor: 'var(--cyan)', color: 'var(--cyan)', ...(runPanelOpen ? { background: 'rgba(6,182,212,0.12)' } : {}) }}
                  >▶ Run</button>
                </>
              )}
            </div>

            {/* Code area — takes all remaining flex space; run panel has fixed height below */}
            <div style={{ flex: 1, overflow: 'auto', position: 'relative', minHeight: 80 }}>
              {editMode ? (
                <div style={{ display: 'flex', height: '100%' }}>
                  <div ref={editGutterRef} style={{
                    flexShrink: 0, padding: '16px 10px 16px 14px', textAlign: 'right',
                    fontFamily: "'JetBrains Mono',monospace", fontSize: 12, lineHeight: 1.6,
                    color: '#858585', userSelect: 'none', borderRight: '1px solid var(--border)',
                    background: 'rgba(17,24,39,0.4)', overflowY: 'hidden',
                  }}>
                    {editedSource.split('\n').map((_, i) => <div key={i}>{i + 1}</div>)}
                  </div>
                  <div style={{ position: 'relative', flex: 1, minWidth: 0 }}>
                    {/* Highlighted layer (behind) */}
                    <pre
                      ref={editHighlightRef}
                      style={{
                        position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
                        margin: 0, padding: '16px 16px',
                        fontFamily: "'JetBrains Mono',monospace", fontSize: 12, lineHeight: 1.6,
                        color: '#d4d4d4', whiteSpace: 'pre', tabSize: 4,
                        overflow: 'hidden', pointerEvents: 'none',
                        borderLeft: '2px solid var(--blue)',
                      }}
                      dangerouslySetInnerHTML={{ __html: highlightPython(editedSource) }}
                    />
                    {/* Transparent textarea on top for editing */}
                    <textarea
                      ref={editTextareaRef}
                      value={editedSource}
                      onChange={e => setEditedSource(e.target.value)}
                      onScroll={e => {
                        if (editHighlightRef.current) {
                          editHighlightRef.current.scrollTop = e.target.scrollTop
                          editHighlightRef.current.scrollLeft = e.target.scrollLeft
                        }
                        if (editGutterRef.current) {
                          editGutterRef.current.scrollTop = e.target.scrollTop
                        }
                      }}
                      onKeyDown={e => {
                        if (e.key === 'Tab') {
                          e.preventDefault()
                          const start = e.target.selectionStart
                          const end = e.target.selectionEnd
                          const newVal = editedSource.slice(0, start) + '    ' + editedSource.slice(end)
                          setEditedSource(newVal)
                          setTimeout(() => {
                            if (editTextareaRef.current) {
                              editTextareaRef.current.selectionStart = start + 4
                              editTextareaRef.current.selectionEnd = start + 4
                            }
                          }, 0)
                        }
                      }}
                      spellCheck={false}
                      style={{
                        position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
                        margin: 0, padding: '16px 16px',
                        fontFamily: "'JetBrains Mono',monospace", fontSize: 12, lineHeight: 1.6,
                        whiteSpace: 'pre', tabSize: 4,
                        background: 'transparent', color: 'transparent', caretColor: '#fff',
                        border: 'none', outline: 'none', resize: 'none',
                        width: '100%', height: '100%', boxSizing: 'border-box',
                        overflow: 'auto',
                      }}
                    />
                  </div>
                </div>
              ) : (
                <CodeBlock source={editedSource || sidebarNode.source} style={{ height: '100%' }} />
              )}
            </div>

            {/* Run panel */}
            {runPanelOpen && (
              <>
                {/* Vertical drag handle */}
                <div
                  onMouseDown={e => {
                    e.preventDefault()
                    runDragging.current = true
                    runDragStart.current = e.clientY
                    runHeightStart.current = runPanelHeight
                    document.body.style.cursor = 'row-resize'
                    document.body.style.userSelect = 'none'
                  }}
                  style={{ height: 6, flexShrink: 0, background: 'var(--border)', cursor: 'row-resize', position: 'relative', transition: 'background 0.15s' }}
                  onMouseEnter={e => e.currentTarget.style.background = 'rgba(59,130,246,0.35)'}
                  onMouseLeave={e => { if (!runDragging.current) e.currentTarget.style.background = 'var(--border)' }}
                >
                  <div style={{ position: 'absolute', left: '50%', top: '50%', transform: 'translate(-50%,-50%)', width: 44, height: 2, borderRadius: 1, background: 'rgba(255,255,255,0.2)' }} />
                </div>
                <div style={{ height: runPanelHeight, flexShrink: 0, overflow: 'auto' }}>
                  <RunFunctionPanel
                    assetId={sidebarNode.id}
                    source={sidebarNode.source || ''}
                    editedSource={editedSource !== sidebarNode.source ? editedSource : undefined}
                    funcname={sidebarNode.name}
                    repo={repo}
                    branch={branch}
                  />

                  {/* ── Test Cases — inside the same scrollable panel ── */}
                  <div style={{ borderTop: '1px solid var(--border)' }}>
                    <div style={{ padding: '8px 16px', display: 'flex', alignItems: 'center', gap: 8, background: 'rgba(17,24,39,0.3)' }}>
                      <span style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--text-dim)', flex: 1 }}>
                        Test Cases{tcList.length > 0 && <span style={{ color: 'var(--text)', marginLeft: 5 }}>({tcList.length})</span>}
                      </span>
                      <button onClick={runTc} disabled={runningTc} style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, padding: '3px 10px', border: '1px solid var(--blue)', background: 'rgba(59,130,246,0.1)', color: 'var(--blue)', borderRadius: 4, cursor: 'pointer' }}>
                        {runningTc ? '...' : '▶ Run all'}
                      </button>
                    </div>

                    <div style={{ padding: '0 16px' }}>
                      {tcList.length === 0 ? (
                        <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: 'var(--text-dim)', fontStyle: 'italic', padding: '8px 0' }}>No test cases yet.</div>
                      ) : tcList.map(tc => (
                        <div key={tc.id} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '6px 0', borderBottom: '1px solid rgba(100,116,139,0.12)' }}>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, fontWeight: 600, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{tc.label}</div>
                            <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, color: 'var(--text-dim)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{JSON.stringify(tc.args)}</div>
                          </div>
                          {tcDelId === tc.id ? (
                            <span style={{ display: 'inline-flex', gap: 3, flexShrink: 0 }}>
                              <button onClick={() => deleteTc(tc.id)} style={{ fontSize: 10, padding: '2px 6px', border: '1px solid rgba(239,68,68,0.6)', background: 'rgba(239,68,68,0.1)', color: '#ef4444', borderRadius: 3, cursor: 'pointer' }}>Yes</button>
                              <button onClick={() => setTcDelId(null)} style={{ fontSize: 10, padding: '2px 6px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-dim)', borderRadius: 3, cursor: 'pointer' }}>No</button>
                            </span>
                          ) : (
                            <button onClick={() => setTcDelId(tc.id)} style={{ fontSize: 11, padding: '1px 6px', border: '1px solid rgba(239,68,68,0.35)', background: 'transparent', color: '#ef4444', borderRadius: 3, cursor: 'pointer', flexShrink: 0 }}>×</button>
                          )}
                        </div>
                      ))}

                      {/* Action toolbar */}
                      <div style={{ paddingTop: 8, paddingBottom: 6, display: 'flex', gap: 6, flexWrap: 'wrap', borderTop: tcList.length > 0 ? '1px solid rgba(100,116,139,0.12)' : 'none', marginTop: tcList.length > 0 ? 2 : 0 }}>
                        <button onClick={generateTc} disabled={tcGenLoading} style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, padding: '3px 8px', border: '1px solid var(--purple)', background: 'rgba(168,85,247,0.08)', color: 'var(--purple)', borderRadius: 4, cursor: 'pointer' }}>
                          {tcGenLoading ? 'Generating...' : '✦ Generate'}
                        </button>
                        <button onClick={() => setTcBulkOpen(v => !v)} style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, padding: '3px 8px', border: `1px solid ${tcBulkOpen ? 'var(--blue)' : 'var(--border)'}`, background: tcBulkOpen ? 'rgba(59,130,246,0.08)' : 'transparent', color: tcBulkOpen ? 'var(--blue)' : 'var(--text-dim)', borderRadius: 4, cursor: 'pointer' }}>↑ Bulk</button>
                        {tcList.length > 0 && (tcDelAll ? (
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                            <span style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, color: 'var(--text-dim)' }}>Delete all?</span>
                            <button onClick={deleteAllTc} style={{ fontSize: 10, padding: '2px 6px', border: '1px solid rgba(239,68,68,0.6)', background: 'rgba(239,68,68,0.1)', color: '#ef4444', borderRadius: 3, cursor: 'pointer' }}>Yes</button>
                            <button onClick={() => setTcDelAll(false)} style={{ fontSize: 10, padding: '2px 6px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-dim)', borderRadius: 3, cursor: 'pointer' }}>No</button>
                          </span>
                        ) : (
                          <button onClick={() => setTcDelAll(true)} style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, padding: '3px 8px', border: '1px solid rgba(239,68,68,0.4)', background: 'transparent', color: '#ef4444', borderRadius: 4, cursor: 'pointer' }}>Delete all</button>
                        ))}
                      </div>

                      {/* Bulk import */}
                      {tcBulkOpen && (
                        <div style={{ marginBottom: 8, padding: 8, background: 'rgba(0,0,0,0.15)', borderRadius: 5, border: '1px solid var(--border)' }}>
                          <textarea value={tcBulkJson} onChange={e => setTcBulkJson(e.target.value)} rows={4}
                            placeholder={'[\n  {"label":"basic","args":{"x":1},"expected":2}\n]'}
                            style={{ width: '100%', background: 'var(--input-bg)', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text)', fontFamily: "'JetBrains Mono',monospace", fontSize: 11, padding: 6, resize: 'vertical', outline: 'none', boxSizing: 'border-box' }}
                          />
                          <button onClick={bulkImportTc} style={{ marginTop: 4, fontFamily: "'JetBrains Mono',monospace", fontSize: 10, padding: '3px 10px', border: '1px solid var(--blue)', background: 'rgba(59,130,246,0.1)', color: 'var(--blue)', borderRadius: 4, cursor: 'pointer' }}>Import</button>
                        </div>
                      )}

                      {/* Results */}
                      {tcResults && !tcResults.running && !tcResults.error && tcResults.rows && (
                        <div style={{ paddingBottom: 10 }}>
                          <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, textTransform: 'uppercase', color: 'var(--text-dim)', letterSpacing: '0.5px', marginBottom: 6, marginTop: 4 }}>Results</div>
                          {tcResults.rows.map(r => {
                            const b = r.passed === true ? { label: 'PASS', color: 'var(--green)', bg: 'rgba(34,197,94,0.1)' }
                              : r.passed === false ? { label: 'FAIL', color: '#ef4444', bg: 'rgba(239,68,68,0.1)' }
                              : !r.ok ? { label: 'ERR', color: '#f97316', bg: 'rgba(249,115,22,0.1)' }
                              : { label: 'RAN', color: 'var(--text-dim)', bg: 'rgba(100,116,139,0.1)' }
                            return (
                              <div key={r.id} style={{ display: 'flex', alignItems: 'flex-start', gap: 6, padding: '5px 0', borderBottom: '1px solid rgba(100,116,139,0.12)' }}>
                                <div style={{ flex: 1, minWidth: 0 }}>
                                  <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, fontWeight: 600, color: 'var(--text)' }}>{r.label}</div>
                                  <span style={{ display: 'inline-block', padding: '1px 6px', borderRadius: 10, fontSize: 10, background: b.bg, color: b.color, marginTop: 2 }}>{b.label}</span>
                                  {r.ok && <span style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, color: 'var(--text-dim)', marginLeft: 5, wordBreak: 'break-all' }}>{JSON.stringify(r.result)}</span>}
                                  {r.passed === false && <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, color: '#ef4444', marginTop: 2, wordBreak: 'break-all' }}>exp: {JSON.stringify(r.expected)}</div>}
                                </div>
                                {r.ok && (
                                  <button
                                    onClick={async () => {
                                      await api.patchTestCaseExpected(r.id, r.result).catch(e => alert(e.message))
                                      setTcResults(prev => prev?.rows ? { ...prev, rows: prev.rows.map(x => x.id === r.id ? { ...x, passed: true } : x) } : prev)
                                    }}
                                    style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 9, padding: '2px 7px', border: '1px solid rgba(34,197,94,0.5)', background: r.passed === true ? 'rgba(34,197,94,0.15)' : 'transparent', color: 'var(--green)', borderRadius: 3, cursor: 'pointer', flexShrink: 0, marginTop: 2 }}
                                  >{r.passed === true ? '✓' : 'Assert'}</button>
                                )}
                              </div>
                            )
                          })}
                        </div>
                      )}
                      {tcResults?.error && (
                        <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: '#ef4444', padding: '6px 0' }}>{tcResults.error}</div>
                      )}
                    </div>
                  </div>
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}

const sbCodeBtnStyle = {
  padding: '3px 10px', fontSize: 10, fontFamily: "'JetBrains Mono',monospace",
  background: 'transparent', border: '1px solid var(--border)', borderRadius: 4,
  color: 'var(--text-dim)', cursor: 'pointer', transition: 'all 0.15s',
}

const REL_STYLES = {
  extends:     { label: 'extends',     color: '#f59e0b', bg: 'rgba(245,158,11,0.1)',  border: 'rgba(245,158,11,0.3)'  },
  extended_by: { label: 'extended by', color: '#22c55e', bg: 'rgba(34,197,94,0.1)',   border: 'rgba(34,197,94,0.3)'   },
  calls:       { label: 'calls',       color: '#818cf8', bg: 'rgba(129,140,248,0.1)', border: 'rgba(129,140,248,0.3)' },
}

function NodeCard({
  node, side, selected, highlighted, onClick, nodeId,
  // Expand left (further upstream)
  onExpandLeft, expandedLeft, leftCount,
  // Expand right (further downstream)
  onExpandRight, expandedRight, rightCount,
}) {
  const info = pn(node.name)
  const [hov, setHov] = useState(false)

  const rel = node.relationship_type ? REL_STYLES[node.relationship_type] : null
  const color = rel ? rel.color : (side === 'upstream' ? 'var(--cyan)' : 'var(--purple)')
  const rgba  = rel ? rel.bg : (side === 'upstream' ? 'rgba(6,182,212,0.15)' : 'rgba(168,85,247,0.15)')

  const ExpandBtn = ({ dir, count, expanded, onExpand }) => {
    const isLeft = dir === 'left'
    const btnColor = isLeft ? 'var(--cyan)' : 'var(--purple)'
    // Unknown depth (null count) means cross-repo/unloaded — show as active color
    const hasUnknown = count === null
    const active = expanded || hasUnknown
    return (
      <button
        className="expand-btn"
        {...{[isLeft ? 'data-expand-left' : 'data-expand-right']: nodeId}}
        onClick={e => { e.stopPropagation(); onExpand() }}
        style={{
          position: 'absolute',
          top: '50%',
          [isLeft ? 'left' : 'right']: -17,
          transform: 'translateY(-50%)',
          width: 30, height: 30, borderRadius: '50%',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          border: `2px solid ${active ? btnColor : 'rgba(100,116,139,0.5)'}`,
          background: expanded ? (isLeft ? 'rgba(6,182,212,0.12)' : 'rgba(168,85,247,0.12)') : 'var(--bg)',
          cursor: 'pointer', transition: 'all 0.15s', zIndex: 10,
          color: active ? btnColor : 'var(--text-dim)',
          fontSize: 10, fontFamily: "'JetBrains Mono',monospace", fontWeight: 700,
        }}
      >
        {count !== null ? count : (isLeft ? '←' : '→')}
      </button>
    )
  }

  return (
    <div style={{ position: 'relative' }}>
      {onExpandLeft && (
        <ExpandBtn dir="left" count={leftCount} expanded={expandedLeft} onExpand={onExpandLeft} />
      )}

      <div
        data-node-id={nodeId}
        onClick={onClick}
        onMouseEnter={() => setHov(true)}
        onMouseLeave={() => setHov(false)}
        className="node-card"
        style={{
          background: highlighted
            ? (side === 'upstream' ? 'hsl(190,40%,10%)' : 'hsl(270,30%,11%)')
            : 'hsl(224,50%,8%)',
          overflow: 'hidden',
          maxWidth: 220,
          border: `1px solid ${highlighted ? color : selected ? 'var(--blue)' : hov ? color : 'var(--border)'}`,
          borderLeft: `3px solid ${color}`,
          borderRadius: 'var(--radius)',
          padding: '12px 14px',
          transition: 'all 0.18s',
          cursor: 'pointer',
          transform: hov ? 'translateY(-2px)' : 'none',
          boxShadow: highlighted
            ? `0 0 0 2px ${color}, 0 0 20px ${rgba}`
            : selected
            ? '0 0 0 2px var(--blue), 0 4px 20px rgba(59,130,246,0.2)'
            : hov ? `0 4px 20px ${rgba}` : 'none',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 1 }}>
          <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 12, fontWeight: 600, color: 'var(--text)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', flex: 1 }}>
            {info.f}
          </div>
          {rel && (
            <span style={{
              flexShrink: 0, fontSize: 9, padding: '1px 6px', borderRadius: 10,
              background: rel.bg, border: `1px solid ${rel.border}`, color: rel.color,
              fontFamily: "'JetBrains Mono',monospace", fontWeight: 600,
              textTransform: 'uppercase', letterSpacing: '0.5px',
            }}>
              {rel.label}
            </span>
          )}
        </div>
        <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, color: 'var(--text-dim)', marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {info.m}
        </div>
        <div style={{ fontSize: 9, color: 'hsl(215,20%,40%)', fontFamily: "'JetBrains Mono',monospace", marginTop: 4, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {node.file}:{node.lineno}
        </div>
        {node.is_cross_repo && node.repo && (
          <div style={{ fontSize: 9, color: '#f59e0b', fontFamily: "'JetBrains Mono',monospace", marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', opacity: 0.85 }}>
            ⬡ {node.repo}
          </div>
        )}
      </div>

      {onExpandRight && (
        <ExpandBtn dir="right" count={rightCount} expanded={expandedRight} onExpand={onExpandRight} />
      )}
    </div>
  )
}

