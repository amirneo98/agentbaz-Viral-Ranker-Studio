import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const proxy = {
  '/api': { target: process.env.VITE_PROXY_TARGET || 'http://localhost:8000', changeOrigin: true },
  '/media': { target: process.env.VITE_PROXY_TARGET || 'http://localhost:8000', changeOrigin: true }
}

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy
  },
  preview: {
    port: 4173,
    strictPort: true,
    proxy
  }
})
