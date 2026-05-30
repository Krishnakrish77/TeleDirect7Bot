import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  const backend = env.VITE_BACKEND_ORIGIN || 'http://127.0.0.1:8080';

  return {
    base: '/static/app/',
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: false,
      proxy: {
        '/api': backend,
        '/auth': backend,
        '/search': backend,
        '/thumb': backend,
        '/watch': backend,
        '/hls': backend,
        '/sub': backend
      }
    },
    build: {
      outDir: '../main/server/static/app',
      emptyOutDir: true,
      sourcemap: false,
      manifest: true
    }
  };
});
