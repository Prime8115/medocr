import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Base path is env-driven so the app can be hosted under a subpath (e.g. /admin/)
// without passing a CLI flag (which Git Bash mangles via MSYS path conversion).
// https://vite.dev/config/
// VITE_BASE is passed slash-free (e.g. "admin") to dodge Git-Bash path mangling;
// normalize it here to "/admin/". Empty => root "/".
const rawBase = (process.env.VITE_BASE || '').replace(/^\/+|\/+$/g, '')
const base = rawBase ? `/${rawBase}/` : '/'

export default defineConfig({
  base,
  plugins: [react()],
})
