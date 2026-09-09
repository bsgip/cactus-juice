import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Forwards API calls to the FastAPI backend during local dev so the frontend can just use
      // relative '/api/...' paths - identical to how a production deployment would serve both
      // behind the same origin/reverse proxy.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
