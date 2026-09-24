import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 6363,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:6364',
        changeOrigin: true,
      }
    }
  }
})
