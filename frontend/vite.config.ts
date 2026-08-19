import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API is same-origin through the nginx reverse proxy in production.
// In dev, proxy /api and /health to the local backend.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
