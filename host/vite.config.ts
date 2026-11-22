import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    // devcontainer 端口转发需要监听所有地址，且端口固定
    host: true,
    port: 5173,
    strictPort: true,
  },
})
