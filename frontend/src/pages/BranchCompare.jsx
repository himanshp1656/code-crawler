import { useEffect, useState, useRef, useCallback } from 'react'
import { useSearchParams, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import { NavBar } from '../components/NavBar'
import { Spinner } from '../components/Spinner'
import { CodeBlock } from '../components/CodeBlock'

// ── Utility helpers ───────────────────────────────────────────────────────────

function splitArgs(raw) {
  const args = []
  let cur = '', depth = 0, inStr = false, strCh = ''
  for (let i = 0; i < raw.length; i++) {
    const c = raw[i]
    if (inStr) {
      cur += c
      if (c === strCh && raw[i - 1] !== '\\') inStr = false
    } else if (c === '"' || c === "'") {
      inStr = true; strCh = c; cur += c
    } else if (c === '(' || c === '[' || c === '{') {
      depth++; cur += c
    } else if (c === ')' || c === ']' || c === '}') {
      depth--; cur += c
    } else if (c === ',' && depth === 0) {
      args.push(cur.trim()); cur = ''
    } else {
      cur += c
    }
  }
  if (cur.trim()) args.push(cur.trim())
  return args
}

function parseSignature(src) {
  if (!src) return null
  const defMatch = src.match(/^[\s\S]*?(?:async\s+)?def\s+\w+\s*\(/m)
  if (!defMatch) return null
  let start = defMatch[0].length, depth = 1, i = start, inStr = false, strCh = ''
  while (i < src.length && depth > 0) {
    const c = src[i]
    if (inStr) {
      if (c === strCh && src[i - 1] !== '\\') inStr = false
    } else if (c === '"' || c === "'") {
      inStr = true; strCh = c
    } else if (c === '(' || c === '[' || c === '{') {
      depth++
    } else if (c === ')' || c === ']' || c === '}') {
      depth--
    }
    i++
  }
  const raw = src.slice(start, i - 1).trim()
  if (!raw) return []
  return splitArgs(raw).map(p => {
    const stripped = p.trim().replace(/^\*+/, '').trim()
    const hasDefault = stripped.includes('=')
    const name = stripped.split(/[=:]/)[0].trim()
    return { name, hasDefault }
  }).filter(p => p.name && p.name !== 'self' && p.name !== 'cls')
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SaveTcForm({ show, onToggle, lastResult, onSave }) {
  const [label, setLabel] = useState('')
  const [expected, setExpected] = useState('')

  if (!show) return null

  const handleSave = () => {
    if (!label.trim()) { alert('Please enter a test case name.'); return }
    let expectedVal = null
    if (expected.trim()) {
      try { expectedVal = JSON.parse(expected.trim()) }
      catch (e) { alert('Expected output is not valid JSON: ' + e.message); return }
    }
    onSave(label.trim(), expectedVal)
    setLabel(''); setExpected('')
    onToggle()
  }

  return (
    <div style={{
      marginTop: 10, padding: 10, border: '1px solid var(--border)',
      borderRadius: 'var(--radius-sm)', background: 'rgba(0,0,0,0.1)',
    }}>
      <input
        value={label}
        onChange={e => setLabel(e.target.value)}
        placeholder="Test case name (e.g. empty list, large input)"
        style={{
          width: '100%', background: 'var(--input-bg)', border: '1px solid var(--border)',
          borderRadius: 'var(--radius-sm)', color: 'var(--text)',
          fontFamily: "'JetBrains Mono',monospace", fontSize: 12, padding: '5px 8px',
          outline: 'none', marginBottom: 6, boxSizing: 'border-box',
        }}
        onFocus={e => { e.target.style.borderColor = 'var(--blue)' }}
        onBlur={e => { e.target.style.borderColor = 'var(--border)' }}
      />
      <input
        value={expected}
        onChange={e => setExpected(e.target.value)}
        placeholder="Expected output JSON (optional — leave blank to skip assertion)"
        style={{
          width: '100%', background: 'var(--input-bg)', border: '1px solid var(--border)',
          borderRadius: 'var(--radius-sm)', color: 'var(--text)',
          fontFamily: "'JetBrains Mono',monospace", fontSize: 12, padding: '5px 8px',
          outline: 'none', marginBottom: 6, boxSizing: 'border-box',
        }}
        onFocus={e => { e.target.style.borderColor = 'var(--blue)' }}
        onBlur={e => { e.target.style.borderColor = 'var(--border)' }}
      />
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <button
          onClick={() => {
            if (lastResult === undefined) { alert('No result captured yet — run the function first.'); return }
            setExpected(JSON.stringify(lastResult))
          }}
          style={{
            fontFamily: "'JetBrains Mono',monospace", fontSize: 11, padding: '4px 10px',
            border: '1px solid var(--border)', background: 'var(--input-bg)',
            color: 'var(--text-dim)', borderRadius: 4, cursor: 'pointer',
          }}
        >↑ Use last result</button>
        <button
          onClick={handleSave}
          style={{
            fontFamily: "'JetBrains Mono',monospace", fontSize: 12, padding: '5px 12px',
            border: '1px solid var(--green)', background: 'rgba(34,197,94,0.1)',
            color: 'var(--green)', borderRadius: 4, cursor: 'pointer',
          }}
        >Save</button>
        <button
          onClick={onToggle}
          style={{
            fontFamily: "'JetBrains Mono',monospace", fontSize: 12, padding: '5px 10px',
            border: '1px solid var(--border)', background: 'transparent',
            color: 'var(--text-dim)', borderRadius: 4, cursor: 'pointer',
          }}
        >Cancel</button>
      </div>
    </div>
  )
}

function ResultBox({ result, onAnalyseError }) {
  if (!result) return <div style={{ ...resultBoxStyle, minHeight: 80 }}>—</div>
  if (result.running) return <div style={{ ...resultBoxStyle, minHeight: 80 }}>Running...</div>
  if (result.notFound) return <div style={{ ...resultBoxStyle, minHeight: 80, borderColor: 'rgba(239,68,68,0.35)' }}>Function not found in this branch</div>
  if (result.ok) {
    let out = 'Return value:\n' + JSON.stringify(result.result, null, 2)
    if (result.stdout) out += '\n\nStdout:\n' + result.stdout
    return <div style={{ ...resultBoxStyle, borderColor: 'rgba(34,197,94,0.35)' }}>{out}</div>
  }
  // error
  const errText = 'Error: ' + result.error + (result.traceback ? '\n\n' + result.traceback : '')
  return (
    <div style={{ ...resultBoxStyle, borderColor: 'rgba(239,68,68,0.35)' }}>
      <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{errText}</pre>
      {onAnalyseError && (
        <div style={{ paddingTop: 8 }}>
          <button
            onClick={onAnalyseError}
            style={{
              fontFamily: "'JetBrains Mono',monospace", fontSize: 12, padding: '5px 12px',
              border: '1px solid #f97316', background: 'rgba(249,115,22,0.08)',
              color: '#f97316', borderRadius: 4, cursor: 'pointer',
            }}
          >Analyse error &amp; suggest fix</button>
        </div>
      )}
    </div>
  )
}

const resultBoxStyle = {
  background: 'var(--input-bg)', border: '1px solid var(--border)',
  borderRadius: 'var(--radius-sm)', padding: 12,
  fontFamily: "'JetBrains Mono',monospace", fontSize: 12,
  minHeight: 80, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
  resize: 'vertical', overflow: 'auto',
}

const valueStyle = { fontSize: 10, fontFamily: "'JetBrains Mono',monospace", wordBreak: 'break-all', whiteSpace: 'pre-wrap', display: 'block', marginTop: 3, maxWidth: '100%' }

function TcResultBadge({ r }) {
  if (!r.ok) {
    const errShort = (r.error || 'error').split('\n')[0].slice(0, 120)
    return (
      <>
        <span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 20, fontSize: 11, background: 'rgba(249,115,22,0.1)', border: '1px solid rgba(249,115,22,0.3)', color: '#f97316' }}>ERROR</span>
        <span style={{ ...valueStyle, color: '#f97316' }}>{errShort}</span>
      </>
    )
  }
  const resultStr = JSON.stringify(r.result)
  if (r.passed === true) return (
    <>
      <span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 20, fontSize: 11, background: 'rgba(34,197,94,0.12)', border: '1px solid rgba(34,197,94,0.3)', color: 'var(--green)' }}>PASS</span>
      <span style={{ ...valueStyle, color: 'var(--text-dim)' }}>{resultStr}</span>
    </>
  )
  if (r.passed === false) return (
    <>
      <span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 20, fontSize: 11, background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#ef4444' }}>FAIL</span>
      <span style={{ ...valueStyle, color: 'var(--text-dim)' }}>got: {resultStr}</span>
      <span style={{ ...valueStyle, color: 'rgba(239,68,68,0.8)' }}>exp: {JSON.stringify(r.expected)}</span>
    </>
  )
  return (
    <>
      <span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 20, fontSize: 11, background: 'rgba(100,116,139,0.12)', border: '1px solid rgba(100,116,139,0.3)', color: 'var(--text-dim)' }}>RAN</span>
      <span style={{ ...valueStyle, color: 'var(--text-dim)' }}>{resultStr}</span>
    </>
  )
}

// ── Draggable column split ────────────────────────────────────────────────────

function useHSplit(leftRef, rightRef) {
  const dragging = useRef(false)
  const startX = useRef(0)
  const startLW = useRef(0)
  const startRW = useRef(0)

  const onMouseDown = useCallback((e) => {
    if (!leftRef.current || !rightRef.current) return
    dragging.current = true
    startX.current = e.clientX
    startLW.current = leftRef.current.getBoundingClientRect().width
    startRW.current = rightRef.current.getBoundingClientRect().width
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [leftRef, rightRef])

  useEffect(() => {
    const onMove = (e) => {
      if (!dragging.current) return
      const dx = e.clientX - startX.current
      const newL = Math.max(120, startLW.current + dx)
      const newR = Math.max(120, startRW.current - dx)
      if (leftRef.current) leftRef.current.style.flex = `0 0 ${newL}px`
      if (rightRef.current) rightRef.current.style.flex = `0 0 ${newR}px`
    }
    const onUp = () => {
      dragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
  }, [leftRef, rightRef])

  return onMouseDown
}

// ── Main component ────────────────────────────────────────────────────────────

export default function BranchCompare() {
  const { tenant } = useParams()
  const [params] = useSearchParams()
  const repo = params.get('repo') || ''

  // Branch + function selection
  const [branches, setBranches] = useState([])
  const [b1, setB1] = useState('')
  const [b2, setB2] = useState('')
  const [funcOptions, setFuncOptions] = useState([])   // {name, b1Only, b2Only}
  const [funcList1, setFuncList1] = useState([])
  const [funcList2, setFuncList2] = useState([])
  const [selectedFn, setSelectedFn] = useState('')
  const [loadingFuncs, setLoadingFuncs] = useState(false)
  const [showChangedOnly, setShowChangedOnly] = useState(false)
  const [changedNames, setChangedNames] = useState(new Set())
  const [scanning, setScanning] = useState(false)
  const [scanProgress, setScanProgress] = useState(0)
  const [fnSearch, setFnSearch] = useState('')
  const [fnDropOpen, setFnDropOpen] = useState(false)
  const fnSearchRef = useRef(null)
  const fnDropRef = useRef(null)

  // Sources
  const [source1, setSource1] = useState(null)
  const [source2, setSource2] = useState(null)
  const [panelVisible, setPanelVisible] = useState(false)

  // Run results
  const [result1, setResult1] = useState(null)
  const [result2, setResult2] = useState(null)
  const [running, setRunning] = useState(false)

  // Test cases
  const [testCases, setTestCases] = useState([])
  const [tcResults, setTcResults] = useState(null)
  const [runningTests, setRunningTests] = useState(false)
  const [showBulkImport, setShowBulkImport] = useState(false)
  const [bulkJson, setBulkJson] = useState('')
  const [bulkStatus, setBulkStatus] = useState('')
  const [genLoading, setGenLoading] = useState(false)
  const [confirmDeleteId, setConfirmDeleteId] = useState(null)
  const [confirmDeleteAll, setConfirmDeleteAll] = useState(false)
  const [assertSelections, setAssertSelections] = useState({})  // { [tcId]: 'b1' | 'b2' }

  // Params state for both sides
  const [params1, setParams1] = useState([])
  const [params2, setParams2] = useState([])
  const [argValues1, setArgValues1] = useState({})
  const [argValues2, setArgValues2] = useState({})
  const [suggesting1, setSuggesting1] = useState(false)
  const [suggesting2, setSuggesting2] = useState(false)
  const [showSave1, setShowSave1] = useState(false)
  const [showSave2, setShowSave2] = useState(false)

  // Drag handles
  const codeLeft = useRef(null)
  const codeRight = useRef(null)
  const resLeft = useRef(null)
  const resRight = useRef(null)
  const codeHandleDown = useHSplit(codeLeft, codeRight)
  const resHandleDown = useHSplit(resLeft, resRight)

  // Load branches from dashboard on mount
  useEffect(() => {
    if (!repo) return
    api.dashboard()
      .then(data => {
        const repos = data.repos || []
        const found = repos.find(r => r.repo === repo || r.repo.endsWith('/' + repo))
        if (found) {
          const bList = found.branches || [found.branch || 'main']
          setBranches(bList)
          setB1(bList[0] || 'main')
          setB2(bList[1] || bList[0] || 'main')
        }
      })
      .catch(() => {})
  }, [repo])

  // Close fn dropdown on outside click
  useEffect(() => {
    const handler = (e) => {
      if (fnDropRef.current && !fnDropRef.current.contains(e.target)) {
        setFnDropOpen(false)
        // Reset search to selected function name on blur
        setFnSearch(selectedFn)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [selectedFn])

  const loadFunctions = async () => {
    if (!b1 || !b2) return
    setLoadingFuncs(true)
    try {
      const [res1, res2] = await Promise.all([
        api.branchFunctionsForBranch(repo, b1),
        api.branchFunctionsForBranch(repo, b2),
      ])
      setFuncList1(res1 || [])
      setFuncList2(res2 || [])
      setChangedNames(new Set())
      setScanProgress(0)
      const names1 = new Set((res1 || []).map(f => f.name))
      const names2 = new Set((res2 || []).map(f => f.name))
      const all = Array.from(new Set([...names1, ...names2])).sort()
      setFuncOptions(all.map(n => ({
        name: n,
        b1Only: !names2.has(n),
        b2Only: !names1.has(n),
      })))
      setPanelVisible(true)
      if (all.length > 0) {
        setSelectedFn(all[0])
        setFnSearch(all[0])
      }
    } catch (e) {
      alert('Failed to load functions: ' + e.message)
    } finally {
      setLoadingFuncs(false)
    }
  }

  const scanAll = async () => {
    const toScan = funcOptions.filter(f => !f.b1Only && !f.b2Only)
    const changed = new Set([
      ...funcOptions.filter(f => f.b1Only || f.b2Only).map(f => f.name),
      ...changedNames,
    ])
    setScanning(true)
    setScanProgress(0)
    let done = 0
    const batchSize = 8
    for (let i = 0; i < toScan.length; i += batchSize) {
      const batch = toScan.slice(i, i + batchSize)
      await Promise.all(batch.map(async f => {
        const [r1, r2] = await Promise.all([
          api.functionSource(repo, b1, f.name).catch(() => null),
          api.functionSource(repo, b2, f.name).catch(() => null),
        ])
        if ((r1?.source || '') !== (r2?.source || '')) changed.add(f.name)
        done++
        setScanProgress(Math.round((done / toScan.length) * 100))
      }))
    }
    setChangedNames(new Set(changed))
    setScanning(false)
  }

  // Load code when function is selected
  useEffect(() => {
    if (!selectedFn || !b1 || !b2) return
    setSource1('Loading...')
    setSource2('Loading...')
    setResult1(null); setResult2(null)
    setArgValues1({}); setArgValues2({})

    Promise.all([
      api.functionSource(repo, b1, selectedFn).catch(() => null),
      api.functionSource(repo, b2, selectedFn).catch(() => null),
    ]).then(([r1, r2]) => {
      const s1 = r1?.source || (r1 ? '# no source stored' : '# not found in this branch')
      const s2 = r2?.source || (r2 ? '# no source stored' : '# not found in this branch')
      setSource1(s1)
      setSource2(s2)
      const p1 = parseSignature(s1) || []
      const p2 = parseSignature(s2) || []
      setParams1(p1); setParams2(p2)
      // Auto-tag as changed if sources differ
      if ((r1?.source || '') !== (r2?.source || '')) {
        setChangedNames(prev => new Set([...prev, selectedFn]))
      }
    })

    loadTestCasesFor(selectedFn)
  }, [selectedFn, b1, b2])

  const loadTestCasesFor = async (fn) => {
    if (!fn) return
    setTcResults(null)
    try {
      const cases = await api.testCases(repo, fn)
      setTestCases(cases || [])
    } catch (_) {
      setTestCases([])
    }
  }

  // Collect args from state
  const collectArgs = (valMap) => {
    const result = {}
    Object.entries(valMap).forEach(([k, v]) => {
      if (!v.trim()) return
      try { result[k] = JSON.parse(v.trim()) }
      catch (_) { result[k] = v.trim() }
    })
    return result
  }

  const runOnBoth = async () => {
    if (!selectedFn) return
    const node1 = funcList1.find(f => f.name === selectedFn)
    const node2 = funcList2.find(f => f.name === selectedFn)
    const args1 = collectArgs(argValues1)
    const args2 = collectArgs(argValues2)

    setRunning(true)
    setResult1({ running: true })
    setResult2({ running: true })

    const runOne = async (node, branch, args) => {
      if (!node) return { notFound: true }
      try {
        const data = await api.runInRepo({ asset_id: node.id, repo, branch, args })
        return data
      } catch (e) {
        return { ok: false, error: e.message }
      }
    }

    const [r1, r2] = await Promise.all([
      runOne(node1, b1, args1),
      runOne(node2, b2, args2),
    ])
    setResult1(r1)
    setResult2(r2)
    setRunning(false)
  }

  const handleSuggest = async (branch, source, side) => {
    if (!source || source.startsWith('#') || source === 'Loading...') return
    if (side === 1) setSuggesting1(true)
    else setSuggesting2(true)
    try {
      const data = await api.suggestMocks({ source, callee_sources: [], free_names: [] })
      if (data.error) { alert('Claude error: ' + data.error); return }
      if (data.params) {
        if (side === 1) {
          setArgValues1(prev => {
            const next = { ...prev }
            Object.keys(data.params).forEach(n => { next[n] = JSON.stringify(data.params[n]) })
            return next
          })
        } else {
          setArgValues2(prev => {
            const next = { ...prev }
            Object.keys(data.params).forEach(n => { next[n] = JSON.stringify(data.params[n]) })
            return next
          })
        }
      }
    } catch (e) {
      alert('Suggest failed: ' + e.message)
    } finally {
      if (side === 1) setSuggesting1(false)
      else setSuggesting2(false)
    }
  }

  const handleSaveTc = async (argValMap, side, label, expected) => {
    if (!selectedFn || !label.trim()) { alert('Please enter a test case name.'); return }
    const args = collectArgs(argValMap)
    try {
      await api.createTestCase({ repo, function_name: selectedFn, label: label.trim(), args, expected })
      await loadTestCasesFor(selectedFn)
    } catch (e) {
      alert('Save failed: ' + e.message)
    }
  }

  const handleDeleteTc = async (id) => {
    try {
      await api.deleteTestCase(id)
      setConfirmDeleteId(null)
      await loadTestCasesFor(selectedFn)
    } catch (e) {
      alert('Delete failed: ' + e.message)
    }
  }

  const handleDeleteAll = async () => {
    try {
      await Promise.all(testCases.map(tc => api.deleteTestCase(tc.id)))
      setConfirmDeleteAll(false)
      await loadTestCasesFor(selectedFn)
    } catch (e) {
      alert('Delete failed: ' + e.message)
    }
  }


  const runAllTestCases = async () => {
    if (!selectedFn) return
    setRunningTests(true)
    setTcResults({ running: true })
    setAssertSelections({})
    try {
      const [res1, res2] = await Promise.all([
        api.runTestCases({ repo, branch: b1, function_name: selectedFn }),
        api.runTestCases({ repo, branch: b2, function_name: selectedFn }),
      ])
      const map2 = {}
      ;(res2 || []).forEach(r => { map2[r.id] = r })
      setTcResults({ rows: res1 || [], map2, b1, b2 })
    } catch (e) {
      setTcResults({ error: e.message })
    } finally {
      setRunningTests(false)
    }
  }

  const toggleAssertSelect = (tcId, branch) => {
    setAssertSelections(prev => {
      // clicking the already-selected branch deselects; clicking the other branch switches
      if (prev[tcId] === branch) {
        const next = { ...prev }
        delete next[tcId]
        return next
      }
      return { ...prev, [tcId]: branch }
    })
  }

  const handleAssertSelected = async () => {
    const entries = Object.entries(assertSelections)
    if (!entries.length) return
    try {
      await Promise.all(entries.map(([tcId, branch]) => {
        const result = branch === 'b1'
          ? tcResults.rows.find(r => String(r.id) === tcId)?.result
          : tcResults.map2[tcId]?.result
        return api.patchTestCaseExpected(tcId, result)
      }))
      const sel = { ...assertSelections }
      setAssertSelections({})
      setTestCases(prev => prev.map(tc => {
        const branch = sel[String(tc.id)]
        if (!branch) return tc
        const result = branch === 'b1'
          ? tcResults.rows.find(r => r.id === tc.id)?.result
          : tcResults.map2[tc.id]?.result
        return { ...tc, expected: result }
      }))
      setTcResults(prev => {
        if (!prev?.rows) return prev
        return {
          ...prev,
          rows: prev.rows.map(r => sel[String(r.id)] ? { ...r, passed: true } : r),
          map2: Object.fromEntries(
            Object.entries(prev.map2).map(([k, v]) => [k, sel[String(v.id)] ? { ...v, passed: true } : v])
          ),
        }
      })
    } catch (e) {
      alert('Assert failed: ' + e.message)
    }
  }

  const handleBulkImport = async () => {
    if (!selectedFn) return
    let cases
    try { cases = JSON.parse(bulkJson.trim()) }
    catch (e) { alert('Invalid JSON: ' + e.message); return }
    if (!Array.isArray(cases)) { alert('Must be a JSON array [ {...}, {...} ]'); return }
    setBulkStatus('Saving ' + cases.length + ' test cases...')
    try {
      await api.bulkCreateTestCases({ repo, function_name: selectedFn, cases })
      setBulkJson('')
      setShowBulkImport(false)
      setBulkStatus('')
      await loadTestCasesFor(selectedFn)
    } catch (e) {
      setBulkStatus('')
      alert('Import failed: ' + e.message)
    }
  }

  const handleGenerateTc = async () => {
    if (!selectedFn) return
    let src = source1
    if (!src || src.startsWith('#')) src = source2
    if (!src || src.startsWith('#')) { alert('Load a function first.'); return }
    setGenLoading(true)
    try {
      const data = await api.generateTestCases({ source: src })
      if (data.error) { alert('AI error: ' + data.error); return }
      setBulkJson(JSON.stringify(data.cases, null, 2))
      setShowBulkImport(true)
    } catch (e) {
      alert('Failed: ' + e.message)
    } finally {
      setGenLoading(false)
    }
  }

  const changesCount = (() => {
    try {
      const raw = sessionStorage.getItem('code-crawler-changes')
      return raw ? Object.keys(JSON.parse(raw)).length : 0
    } catch { return 0 }
  })()

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <>
      <NavBar
        repo={repo}
        changesCount={changesCount}
        right={
          <a href={`/${tenant}/dashboard`} className="btn btn-ghost">Dashboard</a>
        }
        customBrand={
          <>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M18 20V10" /><path d="M12 20V4" /><path d="M6 20v-6" />
            </svg>
            Compare Branches
            <span style={{ color: 'var(--text)', fontWeight: 400 }}>&nbsp;— {repo}</span>
          </>
        }
      />

      <div style={{ padding: '16px 20px' }}>

        {/* Setup card */}
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-title">Select branches &amp; function</div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap', marginTop: 14 }}>
            <div className="field" style={{ flex: 1, minWidth: 140, marginTop: 0 }}>
              <label>Branch 1</label>
              <select
                value={b1}
                onChange={e => setB1(e.target.value)}
                style={{ background: 'var(--input-bg)', border: '1px solid var(--border)', padding: '8px 12px', borderRadius: 'var(--radius-sm)', color: 'var(--text)', fontSize: 13, fontFamily: "'JetBrains Mono',monospace", outline: 'none', width: '100%', cursor: 'pointer' }}
              >
                {branches.length === 0 && <option value="">main</option>}
                {branches.map(b => <option key={b} value={b}>{b}</option>)}
              </select>
            </div>
            <div className="field" style={{ flex: 1, minWidth: 140, marginTop: 0 }}>
              <label>Branch 2</label>
              <select
                value={b2}
                onChange={e => setB2(e.target.value)}
                style={{ background: 'var(--input-bg)', border: '1px solid var(--border)', padding: '8px 12px', borderRadius: 'var(--radius-sm)', color: 'var(--text)', fontSize: 13, fontFamily: "'JetBrains Mono',monospace", outline: 'none', width: '100%', cursor: 'pointer' }}
              >
                {branches.length === 0 && <option value="">main</option>}
                {branches.map(b => <option key={b} value={b}>{b}</option>)}
              </select>
            </div>
            <div className="field" style={{ flex: 2, minWidth: 200, marginTop: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                <label style={{ margin: 0 }}>Function</label>
                {funcOptions.length > 0 && (
                  <button
                    disabled={scanning}
                    onClick={() => {
                      const next = !showChangedOnly
                      setShowChangedOnly(next)
                      if (next) scanAll()
                    }}
                    style={{
                      display: 'inline-flex', alignItems: 'center', gap: 6,
                      padding: '3px 10px', borderRadius: 20,
                      border: `1px solid ${showChangedOnly ? '#facc15' : 'rgba(100,116,139,0.35)'}`,
                      background: showChangedOnly ? 'rgba(250,204,21,0.08)' : 'transparent',
                      color: showChangedOnly ? '#facc15' : 'var(--text-dim)',
                      fontFamily: "'JetBrains Mono',monospace", fontSize: 11,
                      cursor: scanning ? 'default' : 'pointer',
                      transition: 'all 0.18s', userSelect: 'none',
                    }}
                    onMouseEnter={e => { if (!scanning && !showChangedOnly) e.currentTarget.style.borderColor = 'rgba(250,204,21,0.4)' }}
                    onMouseLeave={e => { if (!showChangedOnly) e.currentTarget.style.borderColor = 'rgba(100,116,139,0.35)' }}
                  >
                    {scanning ? (
                      <>
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" style={{ animation: 'spin 1s linear infinite' }}>
                          <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
                        </svg>
                        {scanProgress}%
                      </>
                    ) : (
                      <>
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2v-4M9 21H5a2 2 0 0 1-2-2v-4m0 0h18"/>
                        </svg>
                        Changed only
                        {showChangedOnly && (
                          <span style={{
                            background: 'rgba(250,204,21,0.15)', border: '1px solid rgba(250,204,21,0.3)',
                            borderRadius: 10, padding: '0 5px', fontSize: 10, lineHeight: '16px',
                          }}>
                            {funcOptions.filter(f => f.b1Only || f.b2Only || changedNames.has(f.name)).length}
                          </span>
                        )}
                      </>
                    )}
                  </button>
                )}
              </div>
              {/* Custom searchable function picker */}
              <div ref={fnDropRef} style={{ position: 'relative' }}>
                <div style={{ position: 'relative' }}>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
                    style={{ position: 'absolute', left: 11, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-dim)', pointerEvents: 'none' }}>
                    <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
                  </svg>
                  <input
                    ref={fnSearchRef}
                    value={fnSearch}
                    onChange={e => { setFnSearch(e.target.value); setFnDropOpen(true) }}
                    onFocus={() => { setFnSearch(''); setFnDropOpen(true) }}
                    placeholder={funcOptions.length === 0 ? '— load functions first —' : 'Search functions…'}
                    disabled={funcOptions.length === 0}
                    style={{
                      width: '100%', padding: '8px 12px 8px 32px',
                      background: 'var(--input-bg)',
                      border: `1px solid ${fnDropOpen ? 'var(--blue)' : showChangedOnly ? 'rgba(250,204,21,0.25)' : 'var(--border)'}`,
                      borderRadius: fnDropOpen ? 'var(--radius-sm) var(--radius-sm) 0 0' : 'var(--radius-sm)',
                      color: 'var(--text)', fontSize: 13,
                      fontFamily: "'JetBrains Mono',monospace",
                      outline: 'none', transition: 'border-color 0.15s',
                    }}
                  />
                  {fnSearch && (
                    <button onClick={() => { setFnSearch(selectedFn); setFnDropOpen(false) }}
                      style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', color: 'var(--text-dim)', cursor: 'pointer', fontSize: 16, lineHeight: 1, padding: '0 2px' }}>
                      ×
                    </button>
                  )}
                </div>

                {fnDropOpen && funcOptions.length > 0 && (() => {
                  const q = fnSearch.toLowerCase()
                  const base = funcOptions.filter(f => !showChangedOnly || f.b1Only || f.b2Only || changedNames.has(f.name))
                  const matches = q ? base.filter(f => f.name.toLowerCase().includes(q)) : base
                  const rest = q ? base.filter(f => !f.name.toLowerCase().includes(q)) : []
                  const allItems = [...matches, ...rest]
                  return (
                    <div style={{
                      position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 999,
                      background: 'var(--surface)', border: '1px solid var(--blue)',
                      borderTop: 'none', borderRadius: '0 0 var(--radius-sm) var(--radius-sm)',
                      maxHeight: 280, overflowY: 'auto',
                      boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
                    }}>
                      {allItems.length === 0 ? (
                        <div style={{ padding: '10px 14px', fontFamily: "'JetBrains Mono',monospace", fontSize: 12, color: 'var(--text-dim)', fontStyle: 'italic' }}>
                          No functions match
                        </div>
                      ) : allItems.map((f, idx) => {
                        const isMatch = !q || f.name.toLowerCase().includes(q)
                        const isSelected = f.name === selectedFn
                        const tag = f.b1Only ? 'b1 only' : f.b2Only ? 'b2 only' : changedNames.has(f.name) ? 'changed' : null
                        const tagColor = f.b1Only ? 'var(--cyan)' : f.b2Only ? 'var(--purple)' : '#facc15'

                        // Highlight matching portion
                        let label
                        if (q && isMatch) {
                          const i = f.name.toLowerCase().indexOf(q)
                          label = (
                            <span>
                              {f.name.slice(0, i)}
                              <span style={{ color: 'var(--blue)', fontWeight: 700 }}>{f.name.slice(i, i + q.length)}</span>
                              {f.name.slice(i + q.length)}
                            </span>
                          )
                        } else {
                          label = f.name
                        }

                        return (
                          <div
                            key={f.name}
                            onMouseDown={e => {
                              e.preventDefault()
                              setSelectedFn(f.name)
                              setFnSearch(f.name)
                              setFnDropOpen(false)
                            }}
                            style={{
                              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                              padding: '7px 14px', cursor: 'pointer', gap: 8,
                              background: isSelected ? 'rgba(59,130,246,0.12)' : 'transparent',
                              borderLeft: isSelected ? '2px solid var(--blue)' : '2px solid transparent',
                              opacity: isMatch ? 1 : 0.35,
                              borderTop: !isMatch && idx === matches.length ? '1px solid var(--border)' : 'none',
                              transition: 'background 0.1s',
                            }}
                            onMouseEnter={e => { if (!isSelected) e.currentTarget.style.background = 'rgba(255,255,255,0.04)' }}
                            onMouseLeave={e => { if (!isSelected) e.currentTarget.style.background = 'transparent' }}
                          >
                            <span style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 12, color: isSelected ? 'var(--blue)' : 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {label}
                            </span>
                            {tag && (
                              <span style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 9, color: tagColor, border: `1px solid ${tagColor}`, borderRadius: 10, padding: '1px 6px', flexShrink: 0, opacity: 0.8 }}>
                                {tag}
                              </span>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  )
                })()}
              </div>
            </div>
            <div style={{ marginTop: 0 }}>
              <button
                className="btn btn-blue"
                style={{ width: 'auto', padding: '10px 20px', marginTop: 0, display: 'flex', alignItems: 'center', gap: 6 }}
                onClick={loadFunctions}
                disabled={loadingFuncs}
              >
                {loadingFuncs && <Spinner style={{ width: 14, height: 14, borderWidth: 2 }} />}
                Load
              </button>
            </div>
          </div>
        </div>

        {panelVisible && (
          <>
            {/* Code side by side */}
            <div style={{ marginBottom: 16 }}>
              <div style={{ display: 'flex', gap: 0, alignItems: 'stretch' }}>
                <div ref={codeLeft} className="card" style={{ flex: 1, minWidth: 180, overflow: 'hidden' }}>
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: 'var(--text-dim)' }}>
                    Branch 1: <span style={{ color: 'var(--green)' }}>{b1}</span>
                  </div>
                  <CodeBlock source={source1 || ''} style={{ height: 360, minHeight: 100, resize: 'vertical', border: '1px solid var(--border)' }} />
                </div>
                {/* Drag handle */}
                <div
                  onMouseDown={codeHandleDown}
                  style={{
                    width: 10, flexShrink: 0, cursor: 'col-resize',
                    background: 'transparent', position: 'relative', margin: '0 2px',
                    borderRadius: 4, borderLeft: '1px solid var(--border)', borderRight: '1px solid var(--border)',
                    transition: 'background 0.15s, border-color 0.15s',
                  }}
                  onMouseEnter={e => {
                    e.currentTarget.style.background = 'rgba(59,130,246,0.1)'
                    e.currentTarget.style.borderColor = 'rgba(59,130,246,0.4)'
                  }}
                  onMouseLeave={e => {
                    e.currentTarget.style.background = 'transparent'
                    e.currentTarget.style.borderColor = 'var(--border)'
                  }}
                  title="Drag to resize columns"
                >
                  <div style={{
                    position: 'absolute', top: '50%', left: '50%',
                    transform: 'translate(-50%, -50%)',
                    width: 2, height: 36, borderRadius: 1, background: 'rgba(255,255,255,0.1)',
                  }} />
                </div>
                <div ref={codeRight} className="card" style={{ flex: 1, minWidth: 180, overflow: 'hidden' }}>
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: 'var(--text-dim)' }}>
                    Branch 2: <span style={{ color: 'var(--green)' }}>{b2}</span>
                  </div>
                  <CodeBlock source={source2 || ''} style={{ height: 360, minHeight: 100, resize: 'vertical', border: '1px solid var(--border)' }} />
                </div>
              </div>
            </div>

            {/* Args + Run */}
            <div style={{ marginBottom: 16 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                {/* Branch 1 params */}
                <div className="card">
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: 'var(--text-dim)' }}>
                    Branch 1: <span style={{ color: 'var(--green)' }}>{b1}</span>
                  </div>
                  <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10 }}>Parameters</div>
                  {params1.length === 0
                    ? <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: 'var(--text-dim)', fontStyle: 'italic' }}>
                        {parseSignature(source1) === null ? 'Cannot detect parameters.' : 'No parameters.'}
                      </div>
                    : params1.map(p => (
                      <div key={p.name} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                        <span style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: 'var(--cyan)', width: 90, minWidth: 90, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</span>
                        <input
                          value={argValues1[p.name] || ''}
                          onChange={e => setArgValues1(prev => ({ ...prev, [p.name]: e.target.value }))}
                          placeholder={p.hasDefault ? 'optional — JSON' : 'JSON value'}
                          style={{ flex: 1, background: 'var(--input-bg)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', color: 'var(--text)', fontFamily: "'JetBrains Mono',monospace", fontSize: 11, padding: '4px 8px', outline: 'none' }}
                          onFocus={e => { e.target.style.borderColor = 'var(--blue)' }}
                          onBlur={e => { e.target.style.borderColor = 'var(--border)' }}
                        />
                      </div>
                    ))
                  }
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 10 }}>
                    <button
                      disabled={suggesting1}
                      onClick={() => handleSuggest(b1, source1, 1)}
                      style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 12, padding: '5px 12px', border: '1px solid var(--purple)', background: 'rgba(168,85,247,0.1)', color: 'var(--purple)', borderRadius: 4, cursor: 'pointer', opacity: suggesting1 ? 0.7 : 1 }}
                    >
                      {suggesting1 ? 'Asking Claude...' : 'Suggest values'}
                    </button>
                    <button
                      onClick={() => setShowSave1(s => !s)}
                      style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 12, padding: '5px 12px', border: '1px solid rgba(100,116,139,0.4)', background: 'rgba(100,116,139,0.08)', color: 'var(--text-dim)', borderRadius: 4, cursor: 'pointer' }}
                    >
                      Save as test case
                    </button>
                  </div>
                  <SaveTcForm
                    show={showSave1}
                    onToggle={() => setShowSave1(false)}
                    lastResult={result1?.ok ? result1.result : undefined}
                    onSave={(label, expectedVal) => handleSaveTc(argValues1, 1, label, expectedVal)}
                  />
                </div>

                {/* Branch 2 params */}
                <div className="card">
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: 'var(--text-dim)' }}>
                    Branch 2: <span style={{ color: 'var(--green)' }}>{b2}</span>
                  </div>
                  <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10 }}>Parameters</div>
                  {params2.length === 0
                    ? <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: 'var(--text-dim)', fontStyle: 'italic' }}>
                        {parseSignature(source2) === null ? 'Cannot detect parameters.' : 'No parameters.'}
                      </div>
                    : params2.map(p => (
                      <div key={p.name} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                        <span style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: 'var(--cyan)', width: 90, minWidth: 90, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</span>
                        <input
                          value={argValues2[p.name] || ''}
                          onChange={e => setArgValues2(prev => ({ ...prev, [p.name]: e.target.value }))}
                          placeholder={p.hasDefault ? 'optional — JSON' : 'JSON value'}
                          style={{ flex: 1, background: 'var(--input-bg)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', color: 'var(--text)', fontFamily: "'JetBrains Mono',monospace", fontSize: 11, padding: '4px 8px', outline: 'none' }}
                          onFocus={e => { e.target.style.borderColor = 'var(--blue)' }}
                          onBlur={e => { e.target.style.borderColor = 'var(--border)' }}
                        />
                      </div>
                    ))
                  }
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 10 }}>
                    <button
                      disabled={suggesting2}
                      onClick={() => handleSuggest(b2, source2, 2)}
                      style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 12, padding: '5px 12px', border: '1px solid var(--purple)', background: 'rgba(168,85,247,0.1)', color: 'var(--purple)', borderRadius: 4, cursor: 'pointer', opacity: suggesting2 ? 0.7 : 1 }}
                    >
                      {suggesting2 ? 'Asking Claude...' : 'Suggest values'}
                    </button>
                    <button
                      onClick={() => setShowSave2(s => !s)}
                      style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 12, padding: '5px 12px', border: '1px solid rgba(100,116,139,0.4)', background: 'rgba(100,116,139,0.08)', color: 'var(--text-dim)', borderRadius: 4, cursor: 'pointer' }}
                    >
                      Save as test case
                    </button>
                  </div>
                  <SaveTcForm
                    show={showSave2}
                    onToggle={() => setShowSave2(false)}
                    lastResult={result2?.ok ? result2.result : undefined}
                    onSave={(label, expectedVal) => handleSaveTc(argValues2, 2, label, expectedVal)}
                  />
                </div>
              </div>

              <div style={{ textAlign: 'center', marginTop: 12 }}>
                <button
                  className="btn btn-green"
                  style={{ width: 'auto', padding: '10px 28px', display: 'inline-flex', alignItems: 'center', gap: 6 }}
                  onClick={runOnBoth}
                  disabled={running}
                >
                  {running && <Spinner style={{ width: 14, height: 14, borderWidth: 2 }} />}
                  {running ? 'Running...' : '▶ Run on both branches'}
                </button>
              </div>
            </div>

            {/* Test Cases */}
            <div style={{ marginBottom: 16 }}>
              <div className="card">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                  <div className="card-title" style={{ margin: 0 }}>
                    Test Cases{' '}
                    {testCases.length > 0 && (
                      <span style={{ fontSize: 12, fontWeight: 400, color: 'var(--text-dim)' }}>({testCases.length})</span>
                    )}
                  </div>
                  <button
                    className="btn btn-blue"
                    style={{ width: 'auto', padding: '8px 16px', margin: 0, display: 'inline-flex', alignItems: 'center', gap: 6 }}
                    onClick={runAllTestCases}
                    disabled={runningTests}
                  >
                    {runningTests && <Spinner style={{ width: 14, height: 14, borderWidth: 2 }} />}
                    {runningTests ? 'Running...' : '▶ Run all on both branches'}
                  </button>
                </div>

                {testCases.length === 0 ? (
                  <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: 'var(--text-dim)', fontStyle: 'italic' }}>
                    No test cases saved yet. Use "Save as test case" after filling in parameters.
                  </div>
                ) : (
                  <div>
                    {testCases.map(tc => (
                      <div key={tc.id} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
                        <div style={{ flex: 1 }}>
                          <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>{tc.label}</div>
                          <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: 'var(--text-dim)', wordBreak: 'break-all' }}>
                            {JSON.stringify(tc.args)}
                            {tc.expected !== null && tc.expected !== undefined && (
                              <span style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: 'var(--cyan)' }}>
                                {' '}→ {JSON.stringify(tc.expected)}
                              </span>
                            )}
                          </div>
                        </div>
                        {confirmDeleteId === tc.id ? (
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontFamily: "'JetBrains Mono',monospace", fontSize: 11, whiteSpace: 'nowrap' }}>
                            <button
                              onClick={() => handleDeleteTc(tc.id)}
                              style={{ padding: '2px 8px', border: '1px solid rgba(239,68,68,0.6)', background: 'rgba(239,68,68,0.12)', color: '#ef4444', borderRadius: 4, cursor: 'pointer' }}
                            >Yes</button>
                            <button
                              onClick={() => setConfirmDeleteId(null)}
                              style={{ padding: '2px 8px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-dim)', borderRadius: 4, cursor: 'pointer' }}
                            >No</button>
                          </span>
                        ) : (
                          <button
                            onClick={() => setConfirmDeleteId(tc.id)}
                            style={{ fontSize: 11, padding: '2px 8px', border: '1px solid rgba(239,68,68,0.4)', background: 'rgba(239,68,68,0.06)', color: '#ef4444', borderRadius: 4, cursor: 'pointer', whiteSpace: 'nowrap' }}
                            onMouseEnter={e => { e.currentTarget.style.background = 'rgba(239,68,68,0.14)' }}
                            onMouseLeave={e => { e.currentTarget.style.background = 'rgba(239,68,68,0.06)' }}
                          >Delete</button>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {/* Action toolbar */}
                {(
                  <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border)', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <button
                      disabled={genLoading}
                      onClick={handleGenerateTc}
                      style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, padding: '5px 12px', border: '1px solid var(--purple)', background: 'rgba(168,85,247,0.08)', color: 'var(--purple)', borderRadius: 5, cursor: 'pointer', opacity: genLoading ? 0.7 : 1 }}
                    >
                      {genLoading ? 'Generating...' : '✦ Generate'}
                    </button>
                    <button
                      onClick={() => setShowBulkImport(s => !s)}
                      style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, padding: '5px 12px', border: `1px solid ${showBulkImport ? 'var(--blue)' : 'var(--border)'}`, background: showBulkImport ? 'rgba(59,130,246,0.08)' : 'transparent', color: showBulkImport ? 'var(--blue)' : 'var(--text-dim)', borderRadius: 5, cursor: 'pointer' }}
                    >
                      ↑ Bulk import
                    </button>
                    {testCases.length > 0 && (
                      confirmDeleteAll ? (
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: "'JetBrains Mono',monospace", fontSize: 11 }}>
                          <span style={{ color: 'var(--text-dim)' }}>Delete {testCases.length}?</span>
                          <button onClick={handleDeleteAll} style={{ padding: '3px 8px', border: '1px solid rgba(239,68,68,0.6)', background: 'rgba(239,68,68,0.12)', color: '#ef4444', borderRadius: 4, cursor: 'pointer', fontFamily: "'JetBrains Mono',monospace", fontSize: 11 }}>Yes</button>
                          <button onClick={() => setConfirmDeleteAll(false)} style={{ padding: '3px 8px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-dim)', borderRadius: 4, cursor: 'pointer', fontFamily: "'JetBrains Mono',monospace", fontSize: 11 }}>No</button>
                        </span>
                      ) : (
                        <button
                          onClick={() => setConfirmDeleteAll(true)}
                          style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, padding: '5px 12px', border: '1px solid rgba(239,68,68,0.4)', background: 'rgba(239,68,68,0.06)', color: '#ef4444', borderRadius: 5, cursor: 'pointer' }}
                          onMouseEnter={e => e.currentTarget.style.background = 'rgba(239,68,68,0.14)'}
                          onMouseLeave={e => e.currentTarget.style.background = 'rgba(239,68,68,0.06)'}
                        >
                          Delete all
                        </button>
                      )
                    )}
                    {Object.keys(assertSelections).length > 0 && (
                      <button
                        onClick={handleAssertSelected}
                        style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, padding: '5px 12px', border: '1px solid rgba(34,197,94,0.5)', background: 'rgba(34,197,94,0.1)', color: 'var(--green)', borderRadius: 5, cursor: 'pointer' }}
                        onMouseEnter={e => e.currentTarget.style.background = 'rgba(34,197,94,0.18)'}
                        onMouseLeave={e => e.currentTarget.style.background = 'rgba(34,197,94,0.1)'}
                      >
                        Assert selected ({Object.keys(assertSelections).length}) ✓
                      </button>
                    )}
                  </div>
                )}

                {/* Bulk import panel */}
                {showBulkImport && (
                  <div style={{ marginTop: 8, padding: 10, background: 'rgba(0,0,0,0.12)', borderRadius: 6, border: '1px solid var(--border)' }}>
                    <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, color: 'var(--text-dim)', marginBottom: 6 }}>
                      Paste an array of test cases. Each item:{' '}
                      <code style={{ color: 'var(--cyan)' }}>{'{'}&#34;label&#34;:&#34;name&#34;,&#34;args&#34;:{'{'}...{'}'},&#34;expected&#34;:...{'}'}</code>
                      {' '}— <code>label</code> and <code>expected</code> are optional.
                    </div>
                    <textarea
                      value={bulkJson}
                      onChange={e => setBulkJson(e.target.value)}
                      rows={5}
                      placeholder={'[\n  {"label": "basic", "args": {"x": 1}, "expected": 2},\n  {"args": {"x": 0}}\n]'}
                      style={{ width: '100%', background: 'var(--input-bg)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', color: 'var(--text)', fontFamily: "'JetBrains Mono',monospace", fontSize: 12, padding: 8, resize: 'vertical', outline: 'none', boxSizing: 'border-box' }}
                    />
                    <div style={{ display: 'flex', gap: 6, marginTop: 6, alignItems: 'center' }}>
                      <button onClick={handleBulkImport} style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, padding: '4px 12px', border: '1px solid var(--blue)', background: 'rgba(59,130,246,0.1)', color: 'var(--blue)', borderRadius: 4, cursor: 'pointer' }}>Import all</button>
                      {bulkStatus && <span style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: 'var(--text-dim)' }}>{bulkStatus}</span>}
                    </div>
                  </div>
                )}

                {/* Test results */}
                {tcResults && !tcResults.running && !tcResults.error && (
                  <div style={{ marginTop: 16 }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10 }}>Results</div>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: "'JetBrains Mono',monospace", fontSize: 12, tableLayout: 'fixed' }}>
                      <thead>
                        <tr>
                          <th style={{ textAlign: 'left', padding: '6px 10px', borderBottom: '2px solid var(--border)', color: 'var(--text-dim)', fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Test Case</th>
                          <th style={{ textAlign: 'left', padding: '6px 10px', borderBottom: '2px solid var(--border)', color: 'var(--text-dim)', fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                            {tcResults.b1}{' '}
                            <span style={{ color: 'var(--green)' }}>✓{tcResults.rows.filter(r => r.passed === true).length}</span>
                            {' '}<span style={{ color: '#ef4444' }}>✗{tcResults.rows.filter(r => r.passed === false).length}</span>
                          </th>
                          <th style={{ textAlign: 'left', padding: '6px 10px', borderBottom: '2px solid var(--border)', color: 'var(--text-dim)', fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                            {tcResults.b2}{' '}
                            <span style={{ color: 'var(--green)' }}>✓{Object.values(tcResults.map2).filter(r => r.passed === true).length}</span>
                            {' '}<span style={{ color: '#ef4444' }}>✗{Object.values(tcResults.map2).filter(r => r.passed === false).length}</span>
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {tcResults.rows.map(r1 => {
                          const r2 = tcResults.map2[r1.id] || null
                          const unasserted = r1.passed == null
                          const tcId = String(r1.id)
                          const sel = assertSelections[tcId]  // 'b1' | 'b2' | undefined

                          const Checkbox = ({ branch, result }) => {
                            const checked = sel === branch
                            const disabled = result == null
                            return (
                              <label style={{ display: 'inline-flex', alignItems: 'center', gap: 5, marginTop: 5, cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.3 : 1 }}>
                                <input
                                  type="checkbox"
                                  checked={checked}
                                  disabled={disabled}
                                  onChange={() => toggleAssertSelect(tcId, branch)}
                                  style={{ accentColor: 'var(--green)', width: 13, height: 13, cursor: disabled ? 'default' : 'pointer' }}
                                />
                                <span style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, color: checked ? 'var(--green)' : 'var(--text-dim)' }}>
                                  assert
                                </span>
                              </label>
                            )
                          }

                          return (
                            <tr key={r1.id}>
                              <td style={{ padding: '7px 10px', borderBottom: '1px solid var(--border)', verticalAlign: 'top' }}>
                                <strong>{r1.label}</strong>
                                <br /><span style={{ fontSize: 10, color: 'var(--text-dim)' }}>{JSON.stringify(r1.args)}</span>
                              </td>
                              <td style={{ padding: '7px 10px', borderBottom: '1px solid var(--border)', verticalAlign: 'top' }}>
                                <TcResultBadge r={r1} />
                                {r1.ok && <><br /><Checkbox branch="b1" result={r1.result} /></>}
                              </td>
                              <td style={{ padding: '7px 10px', borderBottom: '1px solid var(--border)', verticalAlign: 'top' }}>
                                {r2 ? (
                                  <>
                                    <TcResultBadge r={r2} />
                                    {r2.ok && <><br /><Checkbox branch="b2" result={r2.result} /></>}
                                  </>
                                ) : (
                                  <span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 20, fontSize: 11, background: 'rgba(249,115,22,0.1)', border: '1px solid rgba(249,115,22,0.3)', color: '#f97316' }}>not run</span>
                                )}
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                )}

              </div>
            </div>

            {/* Results side by side */}
            {(result1 || result2) && (
              <div style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', gap: 0, alignItems: 'stretch' }}>
                  <div ref={resLeft} className="card" style={{ flex: 1, minWidth: 180, overflow: 'hidden' }}>
                    <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: 'var(--text-dim)' }}>
                      Branch 1: <span style={{ color: 'var(--green)' }}>{b1}</span>
                    </div>
                    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10, marginTop: 8 }}>Result</div>
                    <ResultBox
                      result={result1}
                      onAnalyseError={result1 && !result1.ok && !result1.running && !result1.notFound ? async () => {
                        const node = funcList1.find(f => f.name === selectedFn)
                        if (!node) return
                        const errText = 'Error: ' + result1.error + (result1.traceback ? '\n\n' + result1.traceback : '')
                        try {
                          const data = await api.suggestFix({ source: source1, error: errText })
                          alert(data.fix || 'No suggestion available.')
                        } catch (e) { alert('Failed: ' + e.message) }
                      } : null}
                    />
                  </div>
                  {/* Drag handle */}
                  <div
                    onMouseDown={resHandleDown}
                    style={{ width: 10, flexShrink: 0, cursor: 'col-resize', background: 'transparent', position: 'relative', margin: '0 2px', borderRadius: 4, borderLeft: '1px solid var(--border)', borderRight: '1px solid var(--border)', transition: 'background 0.15s, border-color 0.15s' }}
                    onMouseEnter={e => { e.currentTarget.style.background = 'rgba(59,130,246,0.1)'; e.currentTarget.style.borderColor = 'rgba(59,130,246,0.4)' }}
                    onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.borderColor = 'var(--border)' }}
                    title="Drag to resize columns"
                  >
                    <div style={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%,-50%)', width: 2, height: 36, borderRadius: 1, background: 'rgba(255,255,255,0.1)' }} />
                  </div>
                  <div ref={resRight} className="card" style={{ flex: 1, minWidth: 180, overflow: 'hidden' }}>
                    <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: 'var(--text-dim)' }}>
                      Branch 2: <span style={{ color: 'var(--green)' }}>{b2}</span>
                    </div>
                    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10, marginTop: 8 }}>Result</div>
                    <ResultBox
                      result={result2}
                      onAnalyseError={result2 && !result2.ok && !result2.running && !result2.notFound ? async () => {
                        const errText = 'Error: ' + result2.error + (result2.traceback ? '\n\n' + result2.traceback : '')
                        try {
                          const data = await api.suggestFix({ source: source2, error: errText })
                          alert(data.fix || 'No suggestion available.')
                        } catch (e) { alert('Failed: ' + e.message) }
                      } : null}
                    />
                  </div>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </>
  )
}
