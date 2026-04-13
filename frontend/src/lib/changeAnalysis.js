/**
 * changeAnalysis.js
 * Ported from templates/changes.html script section.
 * All analysis functions for function signature/type change impact.
 */

/* ── Line diff (LCS) ── */
export function lineDiff(oldText, newText) {
  const oldL = oldText.split('\n'), newL = newText.split('\n')
  const m = oldL.length, n = newL.length
  if (m + n > 2000) return simpleDiff(oldL, newL)

  const dp = []
  for (let i = 0; i <= m; i++) {
    dp[i] = new Array(n + 1)
    for (let j = 0; j <= n; j++) {
      if (i === 0 || j === 0) dp[i][j] = 0
      else if (oldL[i-1] === newL[j-1]) dp[i][j] = dp[i-1][j-1] + 1
      else dp[i][j] = Math.max(dp[i-1][j], dp[i][j-1])
    }
  }
  const ops = []
  let i = m, j = n
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && oldL[i-1] === newL[j-1]) { ops.push({ type: 'ctx', text: oldL[--i] }); j-- }
    else if (j > 0 && (i === 0 || dp[i][j-1] >= dp[i-1][j])) { ops.push({ type: 'add', text: newL[--j] }) }
    else { ops.push({ type: 'del', text: oldL[--i] }) }
  }
  ops.reverse()

  const keep = []
  for (let k = 0; k < ops.length; k++) {
    if (ops[k].type !== 'ctx') {
      for (let c = Math.max(0, k-3); c <= Math.min(ops.length-1, k+3); c++) keep[c] = true
    }
  }
  const result = []
  let prev = false
  for (let k = 0; k < ops.length; k++) {
    if (keep[k]) { result.push(ops[k]); prev = true }
    else if (prev) { result.push({ type: 'sep' }); prev = false }
  }
  return result
}

function simpleDiff(a, b) {
  const r = []
  const max = Math.max(a.length, b.length)
  for (let i = 0; i < max; i++) {
    const o = i < a.length ? a[i] : undefined
    const n = i < b.length ? b[i] : undefined
    if (o === n) r.push({ type: 'ctx', text: o })
    else {
      if (o !== undefined) r.push({ type: 'del', text: o })
      if (n !== undefined) r.push({ type: 'add', text: n })
    }
  }
  return r
}

/**
 * splitArgs(raw)
 * Splits a comma-separated argument string while respecting nested parens/brackets/braces.
 */
export function splitArgs(raw) {
  const args = []
  let depth = 0, cur = '', inStr = false, strChar = ''
  for (let i = 0; i < raw.length; i++) {
    const c = raw[i]
    if (inStr) {
      cur += c
      if (c === strChar && raw[i-1] !== '\\') inStr = false
    } else if (c === '"' || c === "'") {
      inStr = true; strChar = c; cur += c
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

/**
 * parseSignature(src)
 * Returns array of parameter names (excluding self/cls), or null if no def line.
 */
export function parseSignature(src) {
  const m = src.match(/^\s*(?:async\s+)?def\s+\w+\s*\(([^)]*)\)/m)
  if (!m) return null
  const raw = m[1].trim()
  if (!raw) return []
  return raw.split(',').map(p => p.trim().replace(/^\*+/, '').split(/[=:]/)[0].trim())
    .filter(p => p && p !== 'self' && p !== 'cls')
}

/**
 * parseSignatureDetailed(src)
 * Returns [{name, hasDefault}] or null.
 */
export function parseSignatureDetailed(src) {
  const m = src.match(/^\s*(?:async\s+)?def\s+\w+\s*\(([^)]*)\)/m)
  if (!m) return null
  const raw = m[1].trim()
  if (!raw) return []
  return splitArgs(raw).map(p => {
    const stripped = p.trim().replace(/^\*+/, '').trim()
    const hasDefault = stripped.indexOf('=') >= 0
    const name = stripped.split(/[=:]/)[0].trim()
    return { name, hasDefault }
  }).filter(p => p.name && p.name !== 'self' && p.name !== 'cls')
}

/**
 * analyzeChange(id, changes)
 * Returns { severity, label, detail, removed, addedRequired, addedOptional }
 */
export function analyzeChange(id, changes) {
  const ch = changes[id]
  if (!ch) return { severity: 'modified', label: 'MODIFIED', detail: '', removed: [], addedRequired: [], addedOptional: [] }

  const origSig = parseSignatureDetailed(ch.original)
  const newSig  = parseSignatureDetailed(ch.edited)

  if (origSig !== null && newSig === null) {
    return {
      severity: 'deleted', label: 'DELETED',
      detail: 'Function definition removed — all callers will fail',
      removed: [], addedRequired: [], addedOptional: [],
    }
  }

  if (origSig !== null && newSig !== null) {
    const origMap = {}, newMap = {}
    origSig.forEach(p => { origMap[p.name] = p })
    newSig.forEach(p => { newMap[p.name] = p })

    const removed = origSig.filter(p => !newMap[p.name])
    const added = newSig.filter(p => !origMap[p.name])
    const addedRequired = added.filter(p => !p.hasDefault)
    const addedOptional = added.filter(p => p.hasDefault)

    const removedNames = removed.map(p => p.name)
    const addedRequiredNames = addedRequired.map(p => p.name)
    const addedOptionalNames = addedOptional.map(p => p.name)

    if (removedNames.length || addedRequiredNames.length) {
      const parts = []
      if (removedNames.length) parts.push('removed: ' + removedNames.join(', '))
      if (addedRequiredNames.length) parts.push('added (required): ' + addedRequiredNames.join(', '))
      return {
        severity: 'breaking', label: 'BREAKING',
        detail: 'Signature changed — ' + parts.join('; '),
        removed: removedNames, addedRequired: addedRequiredNames, addedOptional: addedOptionalNames,
      }
    }

    if (addedOptionalNames.length) {
      return {
        severity: 'modified', label: 'MODIFIED',
        detail: 'Added optional param(s) with defaults — backward compatible: ' + addedOptionalNames.join(', '),
        removed: [], addedRequired: [], addedOptional: addedOptionalNames,
      }
    }
  }

  return {
    severity: 'modified', label: 'MODIFIED',
    detail: 'Implementation changed, signature intact',
    removed: [], addedRequired: [], addedOptional: [],
  }
}

/**
 * inferLiteralType(val)
 */
export function inferLiteralType(val) {
  val = (val || '').trim()
  if (!val || val === 'None') return 'None'
  if (val === 'True' || val === 'False') return 'bool'
  if (/^-?\d+$/.test(val)) return 'int'
  if (/^-?\d*\.\d+$/.test(val)) return 'float'
  if (/^[fFbBrRuU]{0,2}["']/.test(val)) return 'str'
  if (val[0] === '[') return 'list'
  if (val[0] === '{' && !val.includes(':')) return 'set'
  if (val[0] === '{') return 'dict'
  if (val[0] === '(') return 'tuple'
  return null
}

/**
 * typesCompatible(passed, expected)
 */
export function typesCompatible(passed, expected) {
  if (!passed || !expected) return true
  const exp = expected.replace(/\s/g, '')
  const optM = exp.match(/^Optional\[(.+)\]$/)
  if (optM) return passed === 'None' || typesCompatible(passed, optM[1])
  const uniM = exp.match(/^Union\[(.+)\]$/)
  if (uniM) return splitArgs(uniM[1]).some(t => typesCompatible(passed, t))
  if (passed === exp) return true
  if (passed === 'bool' && (exp === 'int' || exp === 'float')) return true
  if (passed === 'int' && exp === 'float') return true
  if (passed === 'list' && /^[Ll]ist/.test(exp)) return true
  if (passed === 'dict' && /^[Dd]ict/.test(exp)) return true
  if (passed === 'tuple' && /^[Tt]uple/.test(exp)) return true
  if (passed === 'set' && /^[Ss]et/.test(exp)) return true
  return false
}

/**
 * parseTypeAnnotations(src)
 * Returns { name, params: [{name, type}], returnType } or null.
 */
export function parseTypeAnnotations(src) {
  if (!src) return null
  const defMatch = src.match(/^\s*(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)\s*(?:->\s*(.+?))?\s*:/m)
  if (!defMatch) return null
  const returnType = defMatch[3] ? defMatch[3].trim() : null
  const params = splitArgs(defMatch[2] || '').map(p => {
    p = p.trim().replace(/^\*+/, '')
    const name = p.split(/[:=]/)[0].trim()
    if (!name || name === 'self' || name === 'cls') return null
    const colonIdx = p.indexOf(':')
    const eqIdx = p.indexOf('=')
    let typeStr = null
    if (colonIdx > -1) {
      typeStr = (eqIdx > colonIdx ? p.slice(colonIdx+1, eqIdx) : p.slice(colonIdx+1)).trim()
    }
    return { name, type: typeStr }
  }).filter(Boolean)
  return { name: defMatch[1], params, returnType }
}

/**
 * inferReturnTypes(src)
 * Returns array of inferred return type strings from literal return statements.
 */
export function inferReturnTypes(src) {
  if (!src) return []
  const types = {}
  ;(src.match(/^\s*return\s+(.+)/mg) || []).forEach(m => {
    const val = m.replace(/^\s*return\s+/, '').replace(/\s*#.*$/, '').trim()
    const t = inferLiteralType(val)
    if (t && t !== 'None') types[t] = true
  })
  return Object.keys(types)
}

/**
 * detectTypeChanges(id, changes)
 * Returns array of { level, kind, from, to, detail }
 */
export function detectTypeChanges(id, changes) {
  const ch = changes[id]
  if (!ch) return []
  const issues = []

  const origAnnot = parseTypeAnnotations(ch.original)
  const newAnnot = parseTypeAnnotations(ch.edited)

  if (origAnnot && newAnnot) {
    if (origAnnot.returnType && newAnnot.returnType && origAnnot.returnType !== newAnnot.returnType) {
      issues.push({
        level: 1, kind: 'return-type',
        from: origAnnot.returnType, to: newAnnot.returnType,
        detail: 'return ' + origAnnot.returnType + ' → ' + newAnnot.returnType,
      })
    }
    const origPMap = {}
    origAnnot.params.forEach(p => { origPMap[p.name] = p })
    newAnnot.params.forEach(p => {
      const orig = origPMap[p.name]
      if (orig && orig.type && p.type && orig.type !== p.type) {
        issues.push({
          level: 1, kind: 'param-type', param: p.name,
          from: orig.type, to: p.type,
          detail: '"' + p.name + '": ' + orig.type + ' → ' + p.type,
        })
      }
    })
  }

  const hasReturnAnnotChange = issues.some(i => i.kind === 'return-type')
  if (!hasReturnAnnotChange) {
    const origR = inferReturnTypes(ch.original)
    const newR = inferReturnTypes(ch.edited)
    const lost = origR.filter(t => newR.indexOf(t) < 0)
    const gained = newR.filter(t => origR.indexOf(t) < 0)
    if (lost.length && gained.length) {
      issues.push({
        level: 2, kind: 'inferred-return',
        from: origR.join('|'), to: newR.join('|'),
        detail: 'inferred return: ' + origR.join('|') + ' → ' + newR.join('|'),
      })
    }
  }

  return issues
}

/**
 * checkCallSite(callerSrc, funcName, newAnnot)
 * Returns array of type mismatch strings found in caller source.
 */
export function checkCallSite(callerSrc, funcName, newAnnot) {
  if (!callerSrc || !newAnnot || !newAnnot.params.length) return []
  const issues = []
  const callRegex = new RegExp('\\b' + funcName + '\\s*\\(([^)]*?)\\)', 'g')
  let m
  while ((m = callRegex.exec(callerSrc)) !== null) {
    const argStr = m[1]
    const args = splitArgs(argStr)
    newAnnot.params.forEach((param, idx) => {
      if (!param.type || idx >= args.length) return
      const argVal = args[idx]
      if (!argVal || argVal.includes('=')) return
      const inferredType = inferLiteralType(argVal.trim())
      if (inferredType && !typesCompatible(inferredType, param.type)) {
        issues.push('"' + param.name + '" expected ' + param.type + ', got ' + inferredType)
      }
    })
  }
  return issues
}

/**
 * checkCallSiteArgs(callerSrc, funcName, analysis)
 * Higher-level wrapper using analyzeChange result.
 */
export function checkCallSiteArgs(callerSrc, funcName, analysis) {
  // Check if caller uses removed params or missing required params
  if (!callerSrc || !funcName) return []
  const issues = []
  const callRegex = new RegExp('\\b' + funcName + '\\s*\\(([^)]*?)\\)', 'g')
  let m
  while ((m = callRegex.exec(callerSrc)) !== null) {
    const argStr = m[1]
    analysis.removed.forEach(paramName => {
      if (argStr.includes(paramName + '=')) {
        issues.push('passes removed param "' + paramName + '"')
      }
    })
  }
  return issues
}

/**
 * analyzeFullImpact(changedIds, allNodes, changes)
 * Returns { broken, breaking, affected, typeIssues }
 * Note: needs nodeCache for downstream_ids traversal.
 * Returns the set of impacted IDs categorized by severity.
 */
export function analyzeFullImpact(changedIds, nodeIndex, changes) {
  const broken = new Set()
  const breakingImpact = new Set()
  const affectedHops = {}  // id → min hop

  changedIds.forEach(id => {
    const analysis = analyzeChange(id, changes)
    if (analysis.severity === 'deleted' || analysis.severity === 'breaking') {
      // Direct callers (downstream) are broken/at-risk
      const node = nodeIndex[id]
      if (node && node.downstream_ids) {
        node.downstream_ids.forEach(callerId => {
          if (!changedIds.includes(callerId)) {
            if (analysis.severity === 'deleted') broken.add(callerId)
            else breakingImpact.add(callerId)
          }
        })
      }
    }
  })

  return { broken, breakingImpact, affectedHops }
}

/**
 * buildTransitiveImpact(rootIds, nodeCache, changes, MAX_HOPS, MAX_NODES)
 * BFS through nodeCache to find transitively affected nodes.
 * Returns Map<id, {hop, severity}>
 */
export function buildTransitiveImpact(rootIds, nodeCache, MAX_HOPS = 4, MAX_NODES = 60) {
  const visited = new Map()   // id → hop
  const queue = []

  rootIds.forEach(id => {
    const data = nodeCache[id]
    if (!data) return
    // downstream callers
    ;(data.downstream || []).forEach(nd => {
      if (!visited.has(nd.id)) {
        visited.set(nd.id, 1)
        queue.push({ id: nd.id, hop: 1 })
      }
    })
  })

  while (queue.length && visited.size < MAX_NODES) {
    const { id, hop } = queue.shift()
    if (hop >= MAX_HOPS) continue
    const data = nodeCache[id]
    if (!data) continue
    ;(data.downstream || []).forEach(nd => {
      if (!visited.has(nd.id)) {
        visited.set(nd.id, hop + 1)
        queue.push({ id: nd.id, hop: hop + 1 })
      }
    })
  }

  return visited
}

/**
 * detectChangedCallees(original, edited)
 * Compares function calls between original and edited source.
 * Returns a Set of function names whose call-site argument count changed.
 */
export function detectChangedCallees(original, edited) {
  if (!original || !edited) return new Set()

  // Extract { funcName → Set<argCount> } from a source string
  function extractCalls(src) {
    const map = {}
    const re = /\b([a-zA-Z_]\w*)\s*\(/g
    let m
    while ((m = re.exec(src)) !== null) {
      const name = m[1]
      // Walk forward to find the matching closing paren, counting commas at depth 1
      let depth = 1, i = m.index + m[0].length, commas = 0, inStr = false, strCh = ''
      while (i < src.length && depth > 0) {
        const c = src[i]
        if (inStr) {
          if (c === strCh && src[i - 1] !== '\\') inStr = false
        } else if (c === '"' || c === "'" || c === '`') {
          inStr = true; strCh = c
        } else if (c === '(') { depth++ }
        else if (c === ')') { depth--; if (depth === 0) break }
        else if (c === ',' && depth === 1) { commas++ }
        i++
      }
      const argsStr = src.slice(m.index + m[0].length, i).trim()
      const argc = argsStr === '' ? 0 : commas + 1
      if (!map[name]) map[name] = new Set()
      map[name].add(argc)
    }
    return map
  }

  const origCalls = extractCalls(original)
  const editedCalls = extractCalls(edited)
  const changed = new Set()

  for (const [name, argCounts] of Object.entries(editedCalls)) {
    if (!origCalls[name]) continue  // new call site — not a changed callee
    const origCounts = origCalls[name]
    // If any arg count in edited differs from all arg counts in original, it changed
    for (const c of argCounts) {
      if (!origCounts.has(c)) { changed.add(name); break }
    }
  }
  return changed
}
