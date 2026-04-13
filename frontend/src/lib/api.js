// Central API client — all requests go through the Vite proxy to FastAPI.
// Cookies (session) are included automatically via credentials: 'include'.

async function request(method, path, body) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
  }
  if (body !== undefined) opts.body = JSON.stringify(body)
  const res = await fetch(path, opts)
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    const error = new Error(err.detail || 'Request failed')
    error.status = res.status
    throw error
  }
  return res.json()
}

const get = (path) => request('GET', path)
const post = (path, body) => request('POST', path, body)

export const api = {
  // ── Auth ──────────────────────────────────────────────────────────────
  login: (username, password) => post('/api/auth/login', { username, password }),
  logout: () => post('/api/auth/logout'),
  signup: (data) => post('/api/auth/signup', data),
  me: () => get('/api/auth/me'),
  checkHandle: (handle) => get(`/check-handle?handle=${encodeURIComponent(handle)}`),

  // ── Dashboard ─────────────────────────────────────────────────────────
  dashboard: () => get('/api/dashboard'),
  crawl: (github_repo_url, branch) => post('/api/crawl', { github_repo_url, branch }),
  setDefaultBranch: (repo, branch) => post('/api/repos/default-branch', { repo, branch }),
  crawlLocal: (formData) => fetch('/api/crawl-local', {
    method: 'POST',
    credentials: 'include',
    body: formData,
  }).then(async res => {
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      const error = new Error(err.detail || 'Upload failed')
      error.status = res.status
      throw error
    }
    return res.json()
  }),

  // ── Lineage data (existing JSON endpoints, unchanged) ─────────────────
  lineageData: (repo, branch, { offset = 0, limit = 100, search = '', filter = 'connected', sort = 'connections' } = {}) => {
    const p = new URLSearchParams({ repo, branch, offset, limit, search, filter, sort })
    return get(`/lineage-data?${p}`)
  },
  classLineageData: (repo, branch) =>
    get(`/api/lineage/classes?repo=${encodeURIComponent(repo)}&branch=${encodeURIComponent(branch)}`),
  lineageNode: (repo, branch, asset_id) =>
    get(`/lineage-node?repo=${encodeURIComponent(repo)}&branch=${encodeURIComponent(branch)}&asset_id=${encodeURIComponent(asset_id)}`),

  // ── Run / AI ──────────────────────────────────────────────────────────
  runInRepo: (data) => post('/run-in-repo', data),
  suggestMocks: (data) => post('/suggest-mocks', data),
  suggestFix: (data) => post('/suggest-fix', data),
  analyzeFunction: (data) => post('/analyze-function', data),

  // ── Test cases ────────────────────────────────────────────────────────
  testCases: (repo, function_name) =>
    get(`/test-cases?repo=${encodeURIComponent(repo)}&function_name=${encodeURIComponent(function_name)}`),
  createTestCase: (data) => post('/test-cases', data),
  runTestCases: (data) => post('/run-test-cases', data),
  generateTestCases: (data) => post('/generate-test-cases', data),
  patchTestCaseExpected: (id, expected) =>
    request('PATCH', `/test-cases/${id}/expected`, { expected }),
  deleteTestCase: (id) => request('DELETE', `/test-cases/${id}`),

  // ── Branch compare ────────────────────────────────────────────────────
  branchFunctions: (repo) =>
    get(`/api/branch-functions?repo=${encodeURIComponent(repo)}`),
  branchFunctionsForBranch: (repo, branch) =>
    get(`/api/branch-functions?repo=${encodeURIComponent(repo)}&branch=${encodeURIComponent(branch)}`),
  functionSource: (repo, branch, function_name) =>
    get(`/api/function-source?repo=${encodeURIComponent(repo)}&branch=${encodeURIComponent(branch)}&name=${encodeURIComponent(function_name)}`),
  bulkCreateTestCases: (data) => post('/test-cases/bulk', data),

  // ── Profile ───────────────────────────────────────────────────────────
  profile: (handle) => get(`/api/profile/${handle}`),

  // ── Admin ─────────────────────────────────────────────────────────────
  adminLogin: (username, password) => post('/api/admin/login', { username, password }),
  adminLogout: () => post('/api/admin/logout'),
  adminMe: () => get('/api/admin/me'),
  adminTenants: () => get('/api/admin/tenants'),
  adminCreateTenant: (tenant_id, tenant_name) =>
    post('/api/admin/tenants', { tenant_id, tenant_name }),
  adminCreateUser: (tenant_id, username, password) =>
    post('/api/admin/users', { tenant_id, username, password }),
}
