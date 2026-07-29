import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// FORGE web UI (cahier §10.1 / descope §2 — React, not Streamlit). A separate build
// from the Python package; in dev it proxies /v1 to the FastAPI surface on :8000 so the
// SPA and the API share an origin (the SSE stream needs same-origin or CORS, and CORS
// is already configured on the API — this just keeps dev simple).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/v1': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
