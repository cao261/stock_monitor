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
    //   注意：FastAPI 路由已自带 /api 前缀（app/main.py include_router prefix="/api"），
    //   代理必须原样透传，不能再 rewrite 掉 /api（旧架构遗留的坑——
    //   rewrite 后 /market/sentiment 不匹配任何 API 路由，落入 SPA fallback 返回 HTML，
    //   前端把 HTML 当 JSON 解析 → 整页渲染崩溃）
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
