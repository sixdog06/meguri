import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  // maplibre-gl 的 Web Worker 用 new URL('./maplibre-gl-worker.mjs', import.meta.url)
  // 定位；一旦被预打包进 .vite/deps，worker 文件不会被拷贝过去导致 404、
  // 地图整片空白。排除预打包，让它从 node_modules 原路径加载。
  optimizeDeps: {
    exclude: ['maplibre-gl'],
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
