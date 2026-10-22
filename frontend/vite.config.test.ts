// Test-only Vite configuration for the isolated `pharmacy_test` rehearsal.
//
// This file is ADDITIVE: it does not modify vite.config.ts (the live frontend).
// It mirrors the live config's structure and conventions exactly, with only the
// port and the API proxy target changed, so the test frontend talks to the
// isolated backend on 127.0.0.1:8001 instead of the live backend on :8000.
//
// Start it with:
//   npm run dev -- --config vite.config.test.ts
//
// strictPort makes the dev server fail loudly if 5175 is taken, instead of
// silently drifting to another port (which is what produced the stray :5174).
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5175,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: false,
      },
    },
  },
})
