import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

const backend = process.env.VITE_BACKEND_URL || 'http://127.0.0.1:8100'

// https://vite.dev/config/
export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
  server: {
    host: '127.0.0.1',
    port: Number(process.env.VITE_PORT) || 5174,
    strictPort: true,
    proxy: {
      '/api': backend,
      '/images': backend,
    },
  },
})
