import { useState } from 'react'
import { api } from '../lib/api'

/**
 * RunFunctionPanel — used in Asset/lineage sidebar and BranchCompare.
 * Props:
 *   assetId      — function's unique lineage ID (passed as asset_id to backend)
 *   source       — current source code string (sent as edited_source when in edit mode)
 *   funcname     — function name (used only for UI display + suggest mocks)
 *   repo         — repo URL
 *   branch       — branch name
 *   editedSource — optional override source (when in edit mode, pass edited text here)
 */
export function RunFunctionPanel({ assetId, source, funcname, repo, branch, editedSource }) {
  const [params, setParams] = useState({})
  const [running, setRunning] = useState(false)
  const [output, setOutput] = useState(null)
  const [suggesting, setSuggesting] = useState(false)
  const [assertLabel, setAssertLabel] = useState('')
  const [showAssert, setShowAssert] = useState(false)
  const [assertSaved, setAssertSaved] = useState(false)

  // Parse function signature params from source
  function parseParams(src) {
    if (!src) return []
    const m = src.match(/^\s*(?:async\s+)?def\s+\w+\s*\(([^)]*)\)/m)
    if (!m) return []
    const raw = m[1].trim()
    if (!raw) return []
    return raw.split(',').map(p => {
      const stripped = p.trim().replace(/^\*+/, '')
      const name = stripped.split(/[=:]/)[0].trim()
      return name
    }).filter(p => p && p !== 'self' && p !== 'cls')
  }

  const paramNames = parseParams(source)

  const handleSuggest = async () => {
    if (!source) return
    setSuggesting(true)
    try {
      const data = await api.suggestMocks({ source, callee_sources: [], free_names: [] })
      if (data.error) { alert('Claude error: ' + data.error); return }
      if (data.params) {
        setParams(prev => {
          const next = { ...prev }
          Object.keys(data.params).forEach(n => { next[n] = JSON.stringify(data.params[n]) })
          return next
        })
      }
    } catch (e) {
      alert('Suggest failed: ' + e.message)
    } finally {
      setSuggesting(false)
    }
  }

  const handleRun = async () => {
    setRunning(true)
    setOutput(null)
    setShowAssert(false)
    setAssertSaved(false)
    setAssertLabel(funcname || '')
    try {
      const args = {}
      paramNames.forEach(p => {
        const val = params[p] ?? ''
        if (val !== '') {
          try { args[p] = JSON.parse(val) }
          catch { args[p] = val }
        }
      })

      const payload = { asset_id: assetId, repo, branch, args }
      // Pass edited source when in edit mode so backend runs the modified code
      if (editedSource) payload.edited_source = editedSource

      const result = await api.runInRepo(payload)
      setOutput(result)
    } catch (err) {
      setOutput({ error: err.message })
    } finally {
      setRunning(false)
    }
  }


  return (
    <div style={{
      background: 'rgba(17,24,39,0.4)',
      borderTop: '1px solid var(--border)',
      padding: '12px 16px',
      display: 'flex',
      flexDirection: 'column',
      gap: 10,
    }}>
      {/* Parameters */}
      <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, textTransform: 'uppercase', letterSpacing: 1, color: 'var(--text-dim)' }}>
        Parameters
      </div>

      {paramNames.length === 0 ? (
        <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: 'var(--text-dim)', fontStyle: 'italic' }}>
          No parameters
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {paramNames.map(p => (
            <div key={p} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{
                fontFamily: "'JetBrains Mono',monospace",
                fontSize: 11,
                color: 'var(--cyan)',
                width: 90,
                flexShrink: 0,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}>{p}</span>
              <input
                value={params[p] ?? ''}
                onChange={e => setParams(prev => ({ ...prev, [p]: e.target.value }))}
                placeholder="JSON value"
                style={{
                  flex: 1,
                  background: 'var(--input-bg)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-sm)',
                  color: 'var(--text)',
                  fontFamily: "'JetBrains Mono',monospace",
                  fontSize: 11,
                  padding: '4px 8px',
                  outline: 'none',
                  width: 'auto',
                  marginTop: 0,
                }}
                onFocus={e => e.target.style.borderColor = 'var(--blue)'}
                onBlur={e => e.target.style.borderColor = 'var(--border)'}
              />
            </div>
          ))}
        </div>
      )}

      {/* Action buttons */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <button
          onClick={handleRun}
          disabled={running}
          style={{
            padding: '5px 14px',
            fontFamily: "'JetBrains Mono',monospace",
            fontSize: 11,
            borderRadius: 'var(--radius-sm)',
            cursor: 'pointer',
            border: '1px solid var(--green)',
            background: 'rgba(34,197,94,0.1)',
            color: 'var(--green)',
            transition: 'all 0.15s',
          }}
        >
          {running ? '⏳ Running...' : '▶ Run'}
        </button>
        {paramNames.length > 0 && (
          <button
            onClick={handleSuggest}
            disabled={suggesting}
            style={{
              padding: '5px 14px',
              fontFamily: "'JetBrains Mono',monospace",
              fontSize: 11,
              borderRadius: 'var(--radius-sm)',
              cursor: 'pointer',
              border: '1px solid var(--purple)',
              background: 'rgba(168,85,247,0.1)',
              color: 'var(--purple)',
              transition: 'all 0.15s',
              opacity: suggesting ? 0.7 : 1,
            }}
          >
            {suggesting ? 'Asking Claude...' : 'Suggest values'}
          </button>
        )}
      </div>

      {/* Output */}
      {output && (
        <div style={{
          background: 'rgba(0,0,0,0.3)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-sm)',
          overflow: 'hidden',
        }}>
          {output.error ? (
            <>
              <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, color: 'var(--text-dim)', padding: '3px 10px', borderBottom: '1px solid var(--border)', textTransform: 'uppercase', letterSpacing: 1 }}>
                Error
              </div>
              <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 12, color: 'var(--red)', padding: '8px 10px', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                {output.error}
              </div>
            </>
          ) : (
            <>
              {output.stdout && (
                <>
                  <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, color: 'var(--text-dim)', padding: '3px 10px', borderBottom: '1px solid var(--border)', textTransform: 'uppercase', letterSpacing: 1 }}>
                    stdout
                  </div>
                  <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: 'var(--text)', padding: '6px 10px', borderBottom: '1px solid var(--border)', whiteSpace: 'pre-wrap' }}>
                    {output.stdout}
                  </div>
                </>
              )}
              <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, color: 'var(--text-dim)', padding: '3px 10px', borderBottom: '1px solid var(--border)', textTransform: 'uppercase', letterSpacing: 1 }}>
                result
              </div>
              <div style={{
                fontFamily: "'JetBrains Mono',monospace",
                fontSize: 12,
                color: output.result !== undefined ? 'var(--green)' : 'var(--text-dim)',
                padding: '8px 10px',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all',
              }}>
                {output.result !== undefined ? JSON.stringify(output.result, null, 2) : 'None'}
              </div>

              {/* Assert / save as test case */}
              {output.result !== undefined && (
                <div style={{ borderTop: '1px solid var(--border)', padding: '8px 10px' }}>
                  {assertSaved ? (
                    <span style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: 'var(--green)' }}>✓ Saved as test case</span>
                  ) : showAssert ? (
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                      <input
                        value={assertLabel}
                        onChange={e => setAssertLabel(e.target.value)}
                        placeholder="Test case label"
                        style={{ flex: 1, background: 'var(--input-bg)', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text)', fontFamily: "'JetBrains Mono',monospace", fontSize: 11, padding: '3px 7px', outline: 'none' }}
                        onFocus={e => e.target.style.borderColor = 'var(--blue)'}
                        onBlur={e => e.target.style.borderColor = 'var(--border)'}
                      />
                      <button
                        onClick={async () => {
                          const label = assertLabel.trim() || (funcname + ' test')
                          const args = {}
                          paramNames.forEach(p => {
                            const val = params[p] ?? ''
                            if (val !== '') { try { args[p] = JSON.parse(val) } catch { args[p] = val } }
                          })
                          try {
                            await api.createTestCase({ repo, function_name: funcname, label, args, expected: output.result })
                            setAssertSaved(true); setShowAssert(false)
                          } catch (e) { alert('Save failed: ' + e.message) }
                        }}
                        style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, padding: '3px 10px', border: '1px solid var(--green)', background: 'rgba(34,197,94,0.1)', color: 'var(--green)', borderRadius: 4, cursor: 'pointer', whiteSpace: 'nowrap' }}
                      >Save ✓</button>
                      <button onClick={() => setShowAssert(false)} style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, padding: '3px 8px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-dim)', borderRadius: 4, cursor: 'pointer' }}>✕</button>
                    </div>
                  ) : (
                    <button
                      onClick={() => { setShowAssert(true); setAssertLabel(funcname || '') }}
                      style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, padding: '3px 10px', border: '1px solid rgba(34,197,94,0.5)', background: 'transparent', color: 'var(--green)', borderRadius: 4, cursor: 'pointer' }}
                    >Assert result</button>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
