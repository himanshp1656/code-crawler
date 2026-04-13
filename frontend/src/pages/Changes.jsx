import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import { useSearchParams, useParams, Link } from 'react-router-dom'
import { api } from '../lib/api'
import { Spinner } from '../components/Spinner'
import { CodeBlock } from '../components/CodeBlock'
import { RunFunctionPanel } from '../components/RunFunctionPanel'
import {
  lineDiff,
  analyzeChange,
  detectTypeChanges,
  buildTransitiveImpact,
  detectChangedCallees,
} from '../lib/changeAnalysis'

const STORE_KEY = 'code-crawler-changes'

function loadChanges() {
  try { return JSON.parse(sessionStorage.getItem(STORE_KEY)) || {} } catch { return {} }
}
function saveChanges(obj) { sessionStorage.setItem(STORE_KEY, JSON.stringify(obj)) }

function pn(name) {
  const p = (name || '').split('.')
  return { f: p[p.length - 1], m: p.slice(0, -1).join('.') }
}

function bezierPath(x1, y1, x2, y2) {
  const cx = (x1 + x2) / 2
  return `M ${x1} ${y1} C ${cx} ${y1}, ${cx} ${y2}, ${x2} ${y2}`
}

function SevTag({ severity }) {
  const colors = {
    deleted: { bg: 'rgba(239,68,68,0.2)', color: '#f87171', border: 'rgba(239,68,68,0.55)' },
    breaking: { bg: 'rgba(239,68,68,0.12)', color: '#ef4444', border: 'rgba(239,68,68,0.35)' },
    modified: { bg: 'rgba(99,102,241,0.1)', color: '#818cf8', border: 'rgba(99,102,241,0.3)' },
  }
  const c = colors[severity] || colors.modified
  return (
    <span style={{
      display: 'inline-block',
      fontFamily: "'JetBrains Mono',monospace",
      fontSize: 8, fontWeight: 700, letterSpacing: '0.5px',
      padding: '1px 5px', borderRadius: 3, marginLeft: 7,
      verticalAlign: 'middle', border: `1px solid ${c.border}`,
      background: c.bg, color: c.color, flexShrink: 0,
    }}>
      {severity.toUpperCase()}
    </span>
  )
}

function DiffView({ original, edited }) {
  const ops = lineDiff(original || '', edited || '')
  if (!ops.length) return null

  let oLn = 1, nLn = 1
  return (
    <div style={{
      margin: '0 20px 12px',
      border: '1px solid var(--border)',
      borderRadius: 6, overflow: 'hidden',
      fontFamily: "'JetBrains Mono',monospace",
      fontSize: 11, lineHeight: 1.5,
      maxHeight: 400, overflowY: 'auto',
    }}>
      {ops.map((op, i) => {
        if (op.type === 'sep') return (
          <div key={i} style={{ padding: '0 10px', whiteSpace: 'pre', color: 'var(--border)', display: 'flex' }}>
            <span style={{ minWidth: 34, textAlign: 'right', paddingRight: 7, color: 'rgba(255,255,255,0.18)', fontSize: 10, userSelect: 'none' }}></span>
            @@ ...
          </div>
        )
        const bg = op.type === 'add' ? 'rgba(34,197,94,0.1)' : op.type === 'del' ? 'rgba(239,68,68,0.1)' : 'transparent'
        const color = op.type === 'add' ? '#a8d8a8' : op.type === 'del' ? '#e8a0a0' : 'var(--text-dim)'
        let lnLabel
        if (op.type === 'add') { lnLabel = '+' + nLn; nLn++ }
        else if (op.type === 'del') { lnLabel = '-' + oLn; oLn++ }
        else { lnLabel = ' ' + nLn; oLn++; nLn++ }
        return (
          <div key={i} style={{ padding: '0 10px', whiteSpace: 'pre', background: bg, color, display: 'flex', alignItems: 'baseline' }}>
            <span style={{ minWidth: 34, textAlign: 'right', paddingRight: 7, color: 'rgba(255,255,255,0.18)', fontSize: 10, userSelect: 'none', flexShrink: 0 }}>{lnLabel}</span>
            {op.text}
          </div>
        )
      })}
    </div>
  )
}

function GraphNode({ label, file, type, onClick, highlighted, nodeId }) {
  const [hov, setHov] = useState(false)
  const borderColors = {
    changed: 'var(--green)',
    unstaged: '#eab308',
    breaking: '#f97316',
    deleted: '#ef4444',
    broken: '#ef4444',
    affected: 'rgba(249,115,22,0.55)',
    'affected-h2': 'rgba(239,68,68,0.32)',
    upstream: 'var(--cyan)',
  }
  const border = borderColors[type] || 'var(--green)'
  return (
    <div
      data-node-id={nodeId}
      onClick={onClick}
      onMouseEnter={() => setHov(true)}
      onMouseLeave={() => setHov(false)}
      style={{
        background: 'var(--surface)',
        border: `1.5px solid ${border}`,
        borderStyle: type === 'affected' || type === 'affected-h2' || type === 'upstream' ? 'dashed' : 'solid',
        borderRadius: 'var(--radius)',
        padding: '12px 14px',
        cursor: 'pointer',
        transition: 'all 0.15s',
        position: 'relative',
        transform: hov ? 'translateY(-2px)' : 'none',
        boxShadow: highlighted ? '0 0 0 2px var(--blue), 0 4px 20px rgba(59,130,246,0.2)' : 'none',
        opacity: type === 'affected' ? 0.85 : type === 'affected-h2' ? 0.6 : 1,
        zIndex: 2,
      }}
    >
      <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 12, fontWeight: 600, color: 'var(--text)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {pn(label).f}
      </div>
      <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 9, color: 'var(--text-dim)', marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {file}
      </div>
    </div>
  )
}

function NodePanel({ id, type, changes, nodeCache, repo, branch, onClose }) {
  const [tab, setTab] = useState(type === 'changed' ? 'diff' : 'source')
  const [panelWidth, setPanelWidth] = useState(420)
  const dragging = useRef(false)
  const dragStart = useRef(0)
  const widthStart = useRef(0)

  // Test case state
  const [tcList, setTcList] = useState([])
  const [tcResults, setTcResults] = useState(null)
  const [tcRunning, setTcRunning] = useState(false)
  const [tcDelId, setTcDelId] = useState(null)
  const [tcDelAll, setTcDelAll] = useState(false)
  const [tcBulkOpen, setTcBulkOpen] = useState(false)
  const [tcBulkJson, setTcBulkJson] = useState('')
  const [tcGenLoading, setTcGenLoading] = useState(false)

  const isChanged = type === 'changed'
  const ch = changes[id]
  const cached = nodeCache[id]
  const name = isChanged ? ch?.name : (cached?.node?.name || cached?.name || id)
  const file = isChanged ? ch?.file : (cached?.node?.file || cached?.file || '')
  const originalSource = isChanged ? (ch?.original || '') : null
  const editedSource = isChanged ? (ch?.edited || '') : null
  const source = isChanged ? (ch?.edited || ch?.original) : (cached?.node?.source || null)
  const startLine = isChanged ? (ch?.lineno || 1) : (cached?.node?.lineno || 1)

  useEffect(() => {
    setTab(type === 'changed' ? 'diff' : 'source')
    setTcList([]); setTcResults(null); setTcDelId(null)
    setTcDelAll(false); setTcBulkOpen(false); setTcBulkJson('')
  }, [id, type])

  useEffect(() => {
    if (!name || tab !== 'tests') return
    setTcList([])
    api.testCases(repo, name)
      .then(d => setTcList(Array.isArray(d) ? d : []))
      .catch(() => setTcList([]))
  }, [id, name, repo, tab])

  const reloadTc = async () => {
    const r = await api.testCases(repo, name).catch(() => [])
    setTcList(r || [])
  }

  const runTc = async () => {
    if (!name || tcRunning) return
    setTcRunning(true); setTcResults({ running: true })
    try {
      const rows = await api.runTestCases({
        repo, branch, function_name: name,
        edited_source: isChanged && editedSource !== originalSource ? editedSource : undefined,
      })
      setTcResults({ rows: rows || [] })
    } catch (e) {
      setTcResults({ error: e.message })
    } finally {
      setTcRunning(false)
    }
  }

  const deleteTc = async (tcId) => {
    await api.deleteTestCase(tcId).catch(e => alert(e.message))
    setTcDelId(null); await reloadTc()
  }

  const deleteAllTc = async () => {
    await Promise.all(tcList.map(tc => api.deleteTestCase(tc.id))).catch(e => alert(e.message))
    setTcDelAll(false); await reloadTc()
  }

  const generateTc = async () => {
    if (!source) return
    setTcGenLoading(true)
    try {
      const d = await api.generateTestCases({ source })
      if (d.error) { alert(d.error); return }
      setTcBulkJson(JSON.stringify(d.cases, null, 2)); setTcBulkOpen(true)
    } catch (e) { alert(e.message) }
    finally { setTcGenLoading(false) }
  }

  const bulkImportTc = async () => {
    let cases
    try { cases = JSON.parse(tcBulkJson) } catch { alert('Invalid JSON'); return }
    if (!Array.isArray(cases)) { alert('Must be a JSON array'); return }
    await api.bulkCreateTestCases({ repo, function_name: name, cases }).catch(e => alert(e.message))
    setTcBulkJson(''); setTcBulkOpen(false); await reloadTc()
  }

  // Drag-to-resize handle
  const onDragMouseDown = (e) => {
    e.preventDefault()
    dragging.current = true
    dragStart.current = e.clientX
    widthStart.current = panelWidth
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    const onMove = (ev) => {
      if (!dragging.current) return
      const delta = dragStart.current - ev.clientX
      setPanelWidth(Math.min(Math.max(widthStart.current + delta, 320), Math.floor(window.innerWidth * 0.7)))
    }
    const onUp = () => {
      dragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  const tabs = isChanged ? ['diff', 'source', 'run', 'tests'] : ['source', 'run', 'tests']

  const tabLabel = { diff: 'Diff', source: 'Source', run: '▶ Run', tests: '✓ Tests' }

  return (
    <div style={{
      position: 'absolute', right: 0, top: 0, bottom: 0, zIndex: 10,
      width: panelWidth, background: 'var(--surface)',
      borderLeft: '1px solid var(--border)',
      display: 'flex', flexDirection: 'column',
      boxShadow: '-4px 0 20px rgba(0,0,0,0.35)',
    }}>
      {/* Drag handle */}
      <div
        onMouseDown={onDragMouseDown}
        onMouseEnter={e => { e.currentTarget.style.background = 'rgba(59,130,246,0.25)' }}
        onMouseLeave={e => { if (!dragging.current) e.currentTarget.style.background = 'transparent' }}
        style={{
          position: 'absolute', left: 0, top: 0, bottom: 0, width: 6,
          cursor: 'col-resize', zIndex: 11, background: 'transparent', transition: 'background 0.15s',
        }}
      />

      {/* Header */}
      <div style={{ padding: '12px 14px 12px 18px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 13, fontWeight: 600, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {pn(name).f}
          </div>
          <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 9, color: 'var(--text-dim)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {file}
          </div>
        </div>
        <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--text-dim)', cursor: 'pointer', fontSize: 20, padding: '0 4px', lineHeight: 1, flexShrink: 0 }}>×</button>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
        {tabs.map(t => (
          <button key={t} onClick={() => setTab(t)} style={{
            padding: '8px 12px', border: 'none', background: 'none', cursor: 'pointer',
            fontFamily: "'JetBrains Mono',monospace", fontSize: 10, letterSpacing: '0.3px',
            color: tab === t ? 'var(--text)' : 'var(--text-dim)',
            borderBottom: tab === t ? '2px solid var(--blue)' : '2px solid transparent',
            transition: 'color 0.15s',
          }}>
            {tabLabel[t]}
          </button>
        ))}
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>

        {/* Diff tab */}
        {tab === 'diff' && isChanged && (
          <div style={{ flex: 1, overflowY: 'auto', paddingTop: 14 }}>
            <DiffView original={ch?.original} edited={ch?.edited} />
          </div>
        )}

        {/* Source tab — syntax-highlighted */}
        {tab === 'source' && (
          <div style={{ flex: 1, overflowY: 'auto' }}>
            {source ? (
              <CodeBlock source={source} startLine={startLine} />
            ) : (
              <div style={{ padding: 16, fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: 'var(--text-dim)', fontStyle: 'italic' }}>
                Source not available.
              </div>
            )}
          </div>
        )}

        {/* Run tab */}
        {tab === 'run' && (
          <div style={{ flex: 1, overflowY: 'auto' }}>
            <RunFunctionPanel
              assetId={id}
              source={originalSource || source || ''}
              editedSource={isChanged && editedSource !== originalSource ? editedSource : undefined}
              funcname={name}
              repo={repo}
              branch={branch}
            />
          </div>
        )}

        {/* Tests tab — full feature parity with Asset.jsx */}
        {tab === 'tests' && (
          <div style={{ flex: 1, overflowY: 'auto' }}>
            {/* Header row */}
            <div style={{ padding: '8px 16px', display: 'flex', alignItems: 'center', gap: 8, background: 'rgba(17,24,39,0.3)', borderBottom: '1px solid var(--border)' }}>
              <span style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--text-dim)', flex: 1 }}>
                Test Cases{tcList.length > 0 && <span style={{ color: 'var(--text)', marginLeft: 5 }}>({tcList.length})</span>}
              </span>
              <button onClick={runTc} disabled={tcRunning} style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, padding: '3px 10px', border: '1px solid var(--blue)', background: 'rgba(59,130,246,0.1)', color: 'var(--blue)', borderRadius: 4, cursor: 'pointer' }}>
                {tcRunning ? '...' : '▶ Run all'}
              </button>
            </div>

            <div style={{ padding: '0 16px' }}>
              {/* Test case list */}
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
        )}
      </div>
    </div>
  )
}

export default function Changes() {
  const { tenant } = useParams()
  const [qp] = useSearchParams()
  const repo = qp.get('repo') || ''
  const branch = qp.get('branch') || 'main'

  const [changes, setChanges] = useState({})
  const [staged, setStaged] = useState({})
  const [openDiff, setOpenDiff] = useState({})
  const [nodeCache, setNodeCache] = useState({})
  const [impactMap, setImpactMap] = useState(new Map())
  const [focusedId, setFocusedId] = useState(null)
  const [loading, setLoading] = useState(true)
  const [selectedNode, setSelectedNode] = useState(null)   // { id, type }
  const [svgPaths, setSvgPaths] = useState([])
  const innerRef = useRef(null)
  const graphViewRef = useRef(null)

  useEffect(() => {
    const ch = loadChanges()
    setChanges(ch)
    const st = {}
    Object.keys(ch).forEach(id => { st[id] = ch[id].staged !== false })
    setStaged(st)
    setLoading(false)

    if (Object.keys(ch).length > 0) {
      // Only fetch nodes that belong to this page's repo+branch
      Object.keys(ch).forEach(id => {
        const chRepo   = ch[id].repo   || repo
        const chBranch = ch[id].branch || branch
        if (chRepo !== repo || chBranch !== branch) return  // skip other repos
        api.lineageNode(chRepo, chBranch, id)
          .then(data => {
            setNodeCache(prev => ({ ...prev, [id]: data }))
            ;(data.downstream || []).forEach(nd => {
              api.lineageNode(chRepo, chBranch, nd.id)
                .then(ndData => setNodeCache(prev => ({ ...prev, [nd.id]: ndData })))
                .catch(() => {})
            })
          })
          .catch(() => {})
      })
    }
  }, [repo, branch])

  // Build transitive impact whenever nodeCache updates
  useEffect(() => {
    const changedIds = Object.keys(changes)
    if (!changedIds.length) return
    const impact = buildTransitiveImpact(changedIds, nodeCache)
    setImpactMap(impact)
  }, [nodeCache, changes])

  // Fetch focused node data whenever focusedId changes and data isn't cached yet
  useEffect(() => {
    if (!focusedId || nodeCache[focusedId]) return
    const ch = changes[focusedId]
    const chRepo   = ch?.repo   || repo
    const chBranch = ch?.branch || branch
    api.lineageNode(chRepo, chBranch, focusedId)
      .then(data => setNodeCache(prev => ({ ...prev, [focusedId]: data })))
      .catch(() => {})
  }, [focusedId, repo, branch, changes]) // intentionally exclude nodeCache

  // Fetch affected node data on selection (use focused node's repo/branch)
  useEffect(() => {
    if (!selectedNode || selectedNode.type !== 'affected') return
    const id = selectedNode.id
    if (nodeCache[id]) return
    const ch = focusedId ? changes[focusedId] : null
    const chRepo   = ch?.repo   || repo
    const chBranch = ch?.branch || branch
    api.lineageNode(chRepo, chBranch, id)
      .then(data => setNodeCache(prev => ({ ...prev, [id]: data })))
      .catch(() => {})
  }, [selectedNode, repo, branch, nodeCache, focusedId, changes])

  // Only show changes that belong to this repo+branch
  const entries = useMemo(
    () => Object.entries(changes).filter(([, ch]) => {
      const chRepo   = ch.repo   || repo
      const chBranch = ch.branch || branch
      return chRepo === repo && chBranch === branch
    }),
    [changes, repo, branch]
  )

  const toggleDiff = (id) => setOpenDiff(prev => ({ ...prev, [id]: !prev[id] }))
  const stageChange = (id) => {
    setStaged(prev => {
      const next = { ...prev, [id]: true }
      const ch = { ...changes }
      ch[id] = { ...ch[id], staged: true }
      setChanges(ch); saveChanges(ch)
      return next
    })
  }
  const unstageChange = (id) => {
    setStaged(prev => {
      const next = { ...prev, [id]: false }
      const ch = { ...changes }
      ch[id] = { ...ch[id], staged: false }
      setChanges(ch); saveChanges(ch)
      return next
    })
  }
  const discardChange = (id) => {
    setChanges(prev => {
      const next = { ...prev }
      delete next[id]
      saveChanges(next)
      return next
    })
    setStaged(prev => { const n = { ...prev }; delete n[id]; return n })
  }

  const stagedCount = Object.values(staged).filter(Boolean).length
  const impactCount = impactMap.size

  const analyses = useMemo(() => {
    const a = {}
    entries.forEach(([id]) => { a[id] = analyzeChange(id, changes) })
    return a
  }, [entries, changes])
  const brokenCount   = useMemo(() => entries.filter(([id]) => analyses[id]?.severity === 'deleted').length,  [entries, analyses])
  const breakingCount = useMemo(() => entries.filter(([id]) => analyses[id]?.severity === 'breaking').length, [entries, analyses])

  // Direct upstream callees and downstream callers of the focused node
  const focusedUpstream   = useMemo(() => (focusedId ? nodeCache[focusedId]?.upstream   || [] : []), [focusedId, nodeCache])
  const focusedDownstream = useMemo(() => (focusedId ? nodeCache[focusedId]?.downstream || [] : []), [focusedId, nodeCache])

  // Which upstream callees had their call signature changed in the edited code
  const changedCallees = useMemo(() => {
    if (!focusedId) return new Set()
    const ch = changes[focusedId]
    if (!ch) return new Set()
    return detectChangedCallees(ch.original, ch.edited)
  }, [focusedId, changes])

  // Only show upstream callees that were actually called differently
  const affectedUpstream = useMemo(
    () => focusedUpstream.filter(nd => changedCallees.has(nd.name.split('.').pop())),
    [focusedUpstream, changedCallees]
  )

  // SVG edges: upstream→focused (cyan) and focused→downstream (orange)
  const svgPathsRef = useRef([])
  const computeEdges = useCallback(() => {
    if (!focusedId) {
      if (svgPathsRef.current.length) { svgPathsRef.current = []; setSvgPaths([]) }
      return
    }
    const inner = innerRef.current
    if (!inner) return
    const innerRect = inner.getBoundingClientRect()
    if (!innerRect.width) return
    const paths = []

    const addEdge = (fromId, toId, isUpstream) => {
      const fromEl = inner.querySelector(`[data-node-id="${fromId}"]`)
      const toEl   = inner.querySelector(`[data-node-id="${toId}"]`)
      if (!fromEl || !toEl) return
      const fr = fromEl.getBoundingClientRect()
      const tr = toEl.getBoundingClientRect()
      paths.push({
        id: `${fromId}→${toId}`,
        x1: fr.right - innerRect.left, y1: fr.top + fr.height / 2 - innerRect.top,
        x2: tr.left  - innerRect.left, y2: tr.top + tr.height / 2 - innerRect.top,
        isUpstream,
      })
    }

    affectedUpstream.forEach(nd => addEdge(nd.id, focusedId, true))
    focusedDownstream.forEach(nd => addEdge(focusedId, nd.id, false))

    const prev = svgPathsRef.current
    if (paths.length !== prev.length || paths.some((p, i) => p.id !== prev[i]?.id || p.x1 !== prev[i]?.x1 || p.y1 !== prev[i]?.y1)) {
      svgPathsRef.current = paths
      setSvgPaths(paths)
    }
  }, [focusedId, affectedUpstream, focusedDownstream])

  useEffect(() => {
    let rafId, timerId
    // rAF ensures DOM is painted before measuring node positions
    rafId = requestAnimationFrame(() => {
      computeEdges()
      timerId = setTimeout(computeEdges, 150)
    })
    return () => { cancelAnimationFrame(rafId); clearTimeout(timerId) }
  }, [computeEdges])

  if (loading) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
      <Spinner />
    </div>
  )

  if (entries.length === 0) return (
    <>
      <nav className="navbar">
        <div className="navbar-brand">
          <Link to={`/${tenant}/dashboard`} style={{ color: 'var(--text-dim)', textDecoration: 'none', fontSize: 13 }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ verticalAlign: -2 }}>
              <path d="m15 18-6-6 6-6"/>
            </svg>
            Dashboard
          </Link>
          <span style={{ color: 'var(--border)' }}>/</span>
          <Link to={`/${tenant}/lineage?repo=${encodeURIComponent(repo)}&branch=${encodeURIComponent(branch)}`} style={{ color: 'var(--text-dim)', textDecoration: 'none', fontSize: 13 }}>Functions</Link>
          <span style={{ color: 'var(--border)' }}>/</span>
          Changes
        </div>
      </nav>
      <div style={{ padding: '80px 20px', textAlign: 'center', fontFamily: "'JetBrains Mono',monospace", fontSize: 13, color: 'var(--text-dim)' }}>
        No changes yet.<br />
        <a href="javascript:history.back()">Go back</a>, edit a function, and click Save.
      </div>
    </>
  )

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
          <Link to={`/${tenant}/lineage?repo=${encodeURIComponent(repo)}&branch=${encodeURIComponent(branch)}`} style={{ color: 'var(--text-dim)', textDecoration: 'none', fontSize: 13 }}>Functions</Link>
          <span style={{ color: 'var(--border)' }}>/</span>
          Changes
        </div>
      </nav>

      {/* Two-column layout */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'minmax(340px, 1fr) minmax(400px, 1.4fr)',
        height: 'calc(100vh - 57px)',
        overflow: 'hidden',
      }}>
        {/* LEFT: change list */}
        <div style={{ overflowY: 'auto', borderRight: '1px solid var(--border)' }}>
          {/* Stats */}
          <div style={{
            padding: '14px 20px', borderBottom: '1px solid var(--border)',
            display: 'flex', gap: 20,
            fontFamily: "'JetBrains Mono',monospace", fontSize: 12,
            background: 'rgba(17,24,39,0.3)', flexWrap: 'wrap',
          }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--green)', display: 'inline-block' }} />
              {stagedCount} staged
            </span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#eab308', display: 'inline-block' }} />
              {entries.length - stagedCount} unstaged
            </span>
            {brokenCount > 0 && (
              <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--red)', display: 'inline-block' }} />
                {brokenCount} deleted
              </span>
            )}
            {breakingCount > 0 && (
              <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#f97316', display: 'inline-block' }} />
                {breakingCount} breaking
              </span>
            )}
            {impactCount > 0 && (
              <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#f97316', opacity: 0.5, display: 'inline-block' }} />
                {impactCount} affected
              </span>
            )}
          </div>

          {/* Staged section */}
          {entries.some(([id]) => staged[id]) && (
            <div style={{
              padding: '10px 20px 6px',
              fontFamily: "'JetBrains Mono',monospace", fontSize: 10,
              textTransform: 'uppercase', letterSpacing: 1, color: 'var(--text-dim)',
              position: 'sticky', top: 0, background: 'var(--bg)', zIndex: 5,
              borderBottom: '1px solid var(--border)',
            }}>Staged Changes</div>
          )}

          {entries.filter(([id]) => staged[id]).map(([id, ch]) => {
            const analysis = analyses[id]
            const typeChanges = detectTypeChanges(id, changes)
            return (
              <ChangeCard
                key={id}
                id={id}
                ch={ch}
                analysis={analysis}
                typeChanges={typeChanges}
                staged={true}
                open={openDiff[id]}
                highlighted={focusedId === id}
                onToggle={() => { toggleDiff(id); setFocusedId(id) }}
                onUnstage={() => unstageChange(id)}
                onDiscard={() => discardChange(id)}
              />
            )
          })}

          {/* Unstaged section */}
          {entries.some(([id]) => !staged[id]) && (
            <div style={{
              padding: '10px 20px 6px',
              fontFamily: "'JetBrains Mono',monospace", fontSize: 10,
              textTransform: 'uppercase', letterSpacing: 1, color: 'var(--text-dim)',
              position: 'sticky', top: 0, background: 'var(--bg)', zIndex: 5,
              borderBottom: '1px solid var(--border)',
            }}>Unstaged Changes</div>
          )}

          {entries.filter(([id]) => !staged[id]).map(([id, ch]) => {
            const analysis = analyses[id]
            const typeChanges = detectTypeChanges(id, changes)
            return (
              <ChangeCard
                key={id}
                id={id}
                ch={ch}
                analysis={analysis}
                typeChanges={typeChanges}
                staged={false}
                open={openDiff[id]}
                highlighted={focusedId === id}
                onToggle={() => { toggleDiff(id); setFocusedId(id) }}
                onStage={() => stageChange(id)}
                onDiscard={() => discardChange(id)}
              />
            )
          })}
        </div>

        {/* RIGHT: impact graph + node panel */}
        <div style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden', position: 'relative' }}>
          <div style={{
            padding: '12px 20px', borderBottom: '1px solid var(--border)',
            fontFamily: "'JetBrains Mono',monospace", fontSize: 12, fontWeight: 600,
            color: 'var(--text-dim)', display: 'flex', alignItems: 'center', gap: 8,
            flexShrink: 0, background: 'rgba(17,24,39,0.3)',
          }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 20v-6M6 20V10M18 20V4"/>
            </svg>
            Impact Graph — transitive blast radius
            {focusedId && (
              <span
                onClick={() => setFocusedId(null)}
                style={{
                  marginLeft: 10, fontSize: 10, padding: '2px 8px', borderRadius: 10,
                  background: 'rgba(59,130,246,0.15)', border: '1px solid rgba(59,130,246,0.3)',
                  color: 'var(--blue)', cursor: 'pointer',
                }}
              >
                focused ×
              </span>
            )}
          </div>

          {/* Legend */}
          <div style={{
            display: 'flex', gap: 14, flexWrap: 'wrap',
            fontFamily: "'JetBrains Mono',monospace", fontSize: 9, color: 'var(--text-dim)',
            padding: '8px 16px', borderBottom: '1px solid var(--border)',
            flexShrink: 0, background: 'rgba(10,15,28,0.5)',
          }}>
            {[
              { label: 'DELETED — removed', border: '#ef4444', bg: 'rgba(239,68,68,0.18)', bw: 2 },
              { label: 'BREAKING — signature changed', border: '#f97316', bg: 'rgba(249,115,22,0.08)' },
              { label: 'MODIFIED — body only', border: 'var(--green)', bg: 'rgba(34,197,94,0.08)' },
              { label: 'may be affected (hop 1+)', border: 'rgba(249,115,22,0.6)', dashed: true },
            ].map((l, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 5, whiteSpace: 'nowrap' }}>
                <span style={{
                  display: 'inline-block', width: 10, height: 10, borderRadius: 2,
                  border: `${l.bw || 1.5}px ${l.dashed ? 'dashed' : 'solid'} ${l.border}`,
                  background: l.bg || 'transparent',
                }} />
                {l.label}
              </div>
            ))}
          </div>

          {/* Graph viewport */}
          <div
            ref={graphViewRef}
            style={{
              flex: 1, position: 'relative', overflowY: 'auto', overflowX: 'auto',
              background: 'radial-gradient(circle at 1px 1px, rgba(255,255,255,0.03) 1px, transparent 0)',
              backgroundSize: '24px 24px',
            }}
          >
            <div
              ref={innerRef}
              style={{ padding: 40, display: 'flex', alignItems: 'flex-start', justifyContent: 'flex-start', gap: 80, minHeight: '100%', minWidth: 'max-content', position: 'relative' }}
            >
              {/* SVG overlay for bezier edges */}
              <svg
                style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', pointerEvents: 'none', overflow: 'visible', zIndex: 1 }}
              >
                <defs>
                  <marker id="arrowhead" markerWidth="6" markerHeight="4" refX="5" refY="2" orient="auto">
                    <polygon points="0 0, 6 2, 0 4" fill="rgba(249,115,22,0.5)" />
                  </marker>
                  <marker id="arrowhead-up" markerWidth="6" markerHeight="4" refX="5" refY="2" orient="auto">
                    <polygon points="0 0, 6 2, 0 4" fill="rgba(34,211,238,0.6)" />
                  </marker>
                </defs>
                {svgPaths.map(p => (
                  <path
                    key={p.id}
                    d={bezierPath(p.x1, p.y1, p.x2, p.y2)}
                    fill="none"
                    stroke={p.isUpstream ? 'rgba(34,211,238,0.4)' : 'rgba(249,115,22,0.45)'}
                    strokeWidth="1.5"
                    strokeDasharray="4 3"
                    markerEnd={p.isUpstream ? 'url(#arrowhead-up)' : 'url(#arrowhead)'}
                  />
                ))}
              </svg>

              {!focusedId ? (
                <div style={{ padding: '60px 20px', textAlign: 'center', fontFamily: "'JetBrains Mono',monospace", fontSize: 12, color: 'var(--text-dim)', zIndex: 2 }}>
                  Click a changed function on the left to see its lineage.
                </div>
              ) : (
                <>
                  {/* Upstream callees — only those whose call signature changed */}
                  {affectedUpstream.length > 0 && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, zIndex: 2, minWidth: 200 }}>
                      <div style={{ fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '1.2px', textAlign: 'center', fontFamily: "'JetBrains Mono',monospace", color: 'rgba(34,211,238,0.7)', marginBottom: 4 }}>
                        Upstream (call changed)
                      </div>
                      {affectedUpstream.map(nd => (
                        <GraphNode
                          key={nd.id}
                          nodeId={nd.id}
                          label={nd.name}
                          file={nd.file}
                          type="upstream"
                          highlighted={selectedNode?.id === nd.id}
                          onClick={() => setSelectedNode({ id: nd.id, type: 'affected' })}
                        />
                      ))}
                    </div>
                  )}

                  {/* The focused changed function */}
                  {(() => {
                    const ch = changes[focusedId]
                    if (!ch) return null
                    const a = analyses[focusedId]
                    const type = a?.severity === 'deleted' ? 'deleted' : a?.severity === 'breaking' ? 'breaking' : staged[focusedId] ? 'changed' : 'unstaged'
                    return (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, zIndex: 2, minWidth: 200 }}>
                        <div style={{ fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '1.2px', textAlign: 'center', fontFamily: "'JetBrains Mono',monospace", color: 'var(--text-dim)', marginBottom: 4 }}>
                          Changed
                        </div>
                        <GraphNode
                          nodeId={focusedId}
                          label={ch.name}
                          file={ch.file}
                          type={type}
                          highlighted={selectedNode?.id === focusedId}
                          onClick={() => setSelectedNode({ id: focusedId, type: 'changed' })}
                        />
                      </div>
                    )
                  })()}

                  {/* Downstream callers — functions that call the changed node */}
                  {focusedDownstream.length > 0 && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, zIndex: 2, minWidth: 200 }}>
                      <div style={{ fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '1.2px', textAlign: 'center', fontFamily: "'JetBrains Mono',monospace", color: 'var(--text-dim)', marginBottom: 4 }}>
                        Affected (callers)
                      </div>
                      {focusedDownstream.map(nd => (
                        <GraphNode
                          key={nd.id}
                          nodeId={nd.id}
                          label={nd.name}
                          file={nd.file}
                          type="affected"
                          highlighted={selectedNode?.id === nd.id}
                          onClick={() => setSelectedNode({ id: nd.id, type: 'affected' })}
                        />
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>

          {/* Node detail panel */}
          {selectedNode && (
            <NodePanel
              key={selectedNode.id}
              id={selectedNode.id}
              type={selectedNode.type}
              changes={changes}
              nodeCache={nodeCache}
              repo={repo}
              branch={branch}
              onClose={() => setSelectedNode(null)}
            />
          )}
        </div>
      </div>
    </div>
  )
}

function ChangeCard({ id, ch, analysis, typeChanges, staged, open, highlighted, onToggle, onStage, onUnstage, onDiscard }) {
  const chBtnStyle = {
    width: 26, height: 26, borderRadius: 4,
    border: '1px solid var(--border)', background: 'transparent',
    color: 'var(--text-dim)', fontSize: 14, cursor: 'pointer',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    transition: 'all 0.1s', flexShrink: 0,
  }

  return (
    <div style={{
      borderBottom: '1px solid var(--border)',
      transition: 'background 0.15s',
      background: highlighted ? 'rgba(59,130,246,0.06)' : 'transparent',
    }}>
      <div
        onClick={onToggle}
        style={{
          padding: '12px 20px 4px',
          display: 'flex', alignItems: 'center', gap: 8,
          cursor: 'pointer',
        }}
      >
        <span style={{
          fontFamily: "'JetBrains Mono',monospace",
          fontSize: 13, fontWeight: 600, color: 'var(--text)',
          flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {pn(ch.name).f}
          <SevTag severity={analysis.severity} />
        </span>
        <div style={{ display: 'flex', gap: 4 }}>
          {staged ? (
            <button onClick={e => { e.stopPropagation(); onUnstage() }} title="Unstage" style={chBtnStyle}>−</button>
          ) : (
            <button onClick={e => { e.stopPropagation(); onStage() }} title="Stage" style={chBtnStyle}>+</button>
          )}
          <button onClick={e => { e.stopPropagation(); onDiscard() }} title="Discard" style={{ ...chBtnStyle }}>×</button>
        </div>
      </div>
      <div style={{ padding: '0 20px 6px', fontFamily: "'JetBrains Mono',monospace", fontSize: 10, color: 'var(--text-dim)' }}>
        {ch.file}
      </div>
      {analysis.detail && (
        <div style={{
          padding: '0 20px 8px',
          fontFamily: "'JetBrains Mono',monospace", fontSize: 10, opacity: 0.8,
          color: analysis.severity === 'deleted' ? '#f87171' : analysis.severity === 'breaking' ? '#ef4444' : 'var(--text-dim)',
        }}>
          {analysis.detail}
        </div>
      )}
      {typeChanges.length > 0 && (
        <div style={{ padding: '0 20px 8px', display: 'flex', flexDirection: 'column', gap: 3 }}>
          {typeChanges.map((tc, i) => (
            <div key={i} style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, color: '#eab308', display: 'flex', alignItems: 'center', gap: 5 }}>
              <span style={{ fontSize: 8, fontWeight: 700, background: 'rgba(234,179,8,0.1)', border: '1px solid rgba(234,179,8,0.3)', borderRadius: 3, padding: '0 4px', color: '#eab308', flexShrink: 0 }}>
                TYPE
              </span>
              {tc.detail}
            </div>
          ))}
        </div>
      )}
      {open && (
        <DiffView original={ch.original} edited={ch.edited} />
      )}
    </div>
  )
}
