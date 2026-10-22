import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Frontend unit tests (vitest + jsdom + Testing Library).
//
// This configuration is separate from vite.config.ts (the live dev server)
// and from vite.config.test.ts (the isolated rehearsal stack), so running the
// tests can never touch a running frontend.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
    restoreMocks: true,
    clearMocks: true,
  },
})
