import vue from '@vitejs/plugin-vue'
import vuetify from 'vite-plugin-vuetify'
import { defineConfig } from 'vitest/config'

// The API's address in development. In the cloud the API serves this app itself,
// so the browser always talks to one origin (docs/design/architecture.md §9).
const api = process.env.BI_API_URL ?? 'http://localhost:8110'

export default defineConfig({
  plugins: [vue(), vuetify({ autoImport: true })],
  server: {
    port: 5290,
    strictPort: true,
    proxy: {
      '/api': api,
      '/healthz': api,
      '/readyz': api,
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
    // a component test imports Vuetify's components, whose CSS only Vite can load
    server: { deps: { inline: ['vuetify'] } },
  },
})
