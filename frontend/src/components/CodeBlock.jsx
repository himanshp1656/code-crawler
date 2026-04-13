/**
 * CodeBlock — syntax-highlighted Python source with line numbers.
 * Uses VS Code Dark+ inspired color classes (matches lineage.html).
 */

export function highlightPython(code) {
  // Escape HTML first
  let out = code
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')

  // Use placeholders to prevent later regexes from matching inside already-wrapped spans.
  // Null byte (\x00) is safe as a delimiter — it never appears in source code.
  const slots = []
  const protect = (html) => { const i = slots.length; slots.push(html); return `\x00${i}\x00` }

  // 1. Strings (triple-quoted first so they don't get split by the single-line pass)
  out = out.replace(/("""[\s\S]*?"""|'''[\s\S]*?'''|"[^"\\]*(?:\\.[^"\\]*)*"|'[^'\\]*(?:\\.[^'\\]*)*')/g,
    (m) => protect(`<span class="hl-str">${m}</span>`))

  // 2. Comments (rest of line after #, must come after strings so #-in-string is already protected)
  out = out.replace(/(#[^\n]*)/gm,
    (m) => protect(`<span class="hl-cm">${m}</span>`))

  // 3. Decorators
  out = out.replace(/^(\s*)(@\w+)/gm,
    (_, ws, dec) => `${ws}${protect(`<span class="hl-dec">${dec}</span>`)}`)

  // 4. Keywords
  out = out.replace(/\b(def|class|return|import|from|as|if|elif|else|for|while|try|except|finally|with|raise|pass|break|continue|yield|lambda|in|not|and|or|is|None|True|False|async|await|global|nonlocal|del|assert)\b/g,
    (m) => protect(`<span class="hl-kw">${m}</span>`))

  // 5. Builtins (includes type names used as values — print, len, list(), etc.)
  out = out.replace(/\b(print|len|range|enumerate|zip|map|filter|sorted|type|isinstance|hasattr|getattr|setattr|open|super|property|staticmethod|classmethod|abs|max|min|sum|any|all|next|iter|repr|id|hash|callable)\b/g,
    (m) => protect(`<span class="hl-bi">${m}</span>`))

  // 6. Type annotations (str, int, float, bool, list, dict, set, tuple, Optional, etc.)
  out = out.replace(/\b(str|int|float|bool|list|dict|set|tuple|Optional|List|Dict|Set|Tuple|Union|Any|Callable|Generator|Iterator|Sequence|Mapping)\b/g,
    (m) => protect(`<span class="hl-th">${m}</span>`))

  // Restore all placeholders BEFORE applying numbers — the number regex would
  // otherwise match the digit indices inside \x00N\x00 and corrupt restoration.
  out = out.replace(/\x00(\d+)\x00/g, (_, i) => slots[+i])

  // 7. Numbers — applied last, after restoration, so they safely skip anything
  // already inside a <span> (strings, comments, keywords are now full HTML).
  // We only match digits that are NOT inside an existing span by checking we're
  // not inside a tag. Simple heuristic: split on tags, highlight text nodes only.
  out = out.replace(/(<[^>]+>)|(\b\d+\.?\d*\b)/g, (m, tag) => {
    if (tag) return tag  // pass HTML tags through unchanged
    return `<span class="hl-num">${m}</span>`
  })

  return out
}

export function CodeBlock({ source, startLine = 1, style }) {
  if (!source) {
    return (
      <div style={{
        padding: '16px',
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 12,
        color: 'var(--text-dim)',
        fontStyle: 'italic',
        ...style,
      }}>
        No source available
      </div>
    )
  }

  const lines = source.split('\n')

  return (
    <div style={{
      display: 'flex',
      overflow: 'auto',
      background: 'var(--input-bg)',
      borderRadius: 'var(--radius-sm)',
      ...style,
    }}>
      {/* gutter */}
      <div style={{
        flexShrink: 0,
        padding: '16px 10px 16px 14px',
        textAlign: 'right',
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 12,
        lineHeight: 1.6,
        color: '#858585',
        userSelect: 'none',
        borderRight: '1px solid var(--border)',
        background: 'rgba(17,24,39,0.4)',
      }}>
        {lines.map((_, i) => (
          <div key={i}>{startLine + i}</div>
        ))}
      </div>
      {/* code */}
      <pre style={{
        margin: 0,
        padding: '16px 16px',
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 12,
        lineHeight: 1.6,
        color: '#d4d4d4',
        whiteSpace: 'pre',
        tabSize: 4,
        flex: 1,
        minWidth: 0,
        overflow: 'auto',
      }}
        dangerouslySetInnerHTML={{ __html: highlightPython(source) }}
      />
    </div>
  )
}
