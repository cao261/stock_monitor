import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue(), tailwindcss()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    // 前端 /api/* 代理到 FastAPI
    //   生产模式（start.bat）走 uvicorn 8000 + dist 静态服务，不需要 proxy
    //   开发模式（npm run dev）走 vite 5173 + proxy 到 8000 后端
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        // 把 /api/market/sentiment 改写成 /market/sentiment
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
