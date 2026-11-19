import { defineConfig, mergeConfig } from 'vite'
import local from './vite.config.ts'

export default mergeConfig(local, defineConfig({
  base: '/',
  publicDir: false,
  build: {
    outDir: '../.run/public-site',
    emptyOutDir: true,
  },
}))
