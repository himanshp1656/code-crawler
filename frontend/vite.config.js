import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Backend URL — override with BACKEND_URL env var for Docker:
//   BACKEND_URL=http://backend:8000 npm run dev
const BACKEND = process.env.BACKEND_URL || 'http://localhost:8000'

const proxy = Object.fromEntries(
  ['/api', '/lineage-data', '/lineage-node', '/check-handle', '/run-in-repo',
   '/suggest-mocks', '/suggest-fix', '/analyze-function', '/test-cases',
   '/run-test-cases', '/generate-test-cases', '/ingest']
    .map(path => [path, { target: BACKEND, changeOrigin: true }])
)

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,   // bind to 0.0.0.0 so Docker port mapping works
    proxy,
  },
})
