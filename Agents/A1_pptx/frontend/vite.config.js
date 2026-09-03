import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Two terminals: `uvicorn backend.main:app --reload` on 8000, `npm run dev` here on 5173.
// The proxy means the app only ever talks to same-origin /api and /media paths, so
// nothing in src/ knows the backend's port. Set BACKEND if 8000 is already taken.
const target = process.env.BACKEND || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target, changeOrigin: true },
      '/media': { target, changeOrigin: true },
      // Uploads are served by uvicorn too. Without this line an uploaded image 404s in
      // dev and the editor shows an empty box for a file that saved and exported fine.
      '/uploads': { target, changeOrigin: true },
    },
  },
})
