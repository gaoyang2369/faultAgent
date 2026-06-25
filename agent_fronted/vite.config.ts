import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import {resolve} from "path";

const backendProxyTarget = process.env.VITE_BACKEND_PROXY_TARGET || 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  publicDir: false,
  server: {
    port: 9005,
    hmr: true,
    open:true,
    proxy: {
      '/api/todos': {
        target: backendProxyTarget,
        changeOrigin: true
      },
      '/api': {
        target: backendProxyTarget, // 后端实际端口
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, '') // 将 /api 前缀移除，转发到后端
      },
      // 静态资源代理：将前端 /images/** 代理到后端静态目录
      '/images': {
        target: backendProxyTarget,
        changeOrigin: true
      },
      // 兼容可能出现的 /public/images/** 路径，重写为 /images/**
      '/public/images': {
        target: backendProxyTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/public/, '')
      },
      // 可选：报告等静态文件
      '/reports': {
        target: backendProxyTarget,
        changeOrigin: true
      }
    }
  },
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src')//可直接使用相对路径
    },
  },
})
