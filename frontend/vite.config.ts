import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => ({
  server: {
    port: 5173,
    proxy: { '/api': { target: loadEnv(mode, '.', 'RESCUETWIN_').RESCUETWIN_API_URL || 'http://127.0.0.1:8000', changeOrigin: true } },
  },
}));
