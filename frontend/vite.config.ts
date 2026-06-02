/// <reference types="vitest/config" />

import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  const backend = env.VITE_BACKEND_ORIGIN || 'https://olympic-lorianne-kksoftsolutions-87c05347.koyeb.app';

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
        '/admin': backend,
        '/thumb': backend,
        '/watch': backend,
        '/hls': backend,
        '/sub': backend,
        '^/[A-Za-z0-9_-]+\\d+$': backend
      }
    },
    build: {
      outDir: '../main/server/static/app',
      emptyOutDir: true,
      sourcemap: false,
      manifest: true
    },
    test: {
      environment: 'jsdom',
      setupFiles: './src/test/setup.ts',
      globals: false
    }
  };
});
