import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 后端 HTTP API 端口：默认 8002，可 MES_API_PORT=xxx npm run dev 覆盖
const apiPort = process.env.MES_API_PORT || '8602'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // 前端请求 /api 转发到本地 mcp-mes HTTP API
      '/api': { target: `http://localhost:${apiPort}`, changeOrigin: true }
    }
  }
})
