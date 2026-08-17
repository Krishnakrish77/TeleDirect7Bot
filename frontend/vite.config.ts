/// <reference types="vitest/config" />

import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'node:path';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  const backend = env.VITE_BACKEND_ORIGIN || 'https://olympic-lorianne-kksoftsolutions-87c05347.koyeb.app';

  return {
    base: '/static/app/',
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src')
      }
    },
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
        '/book': backend,
        '/hls': backend,
        '/sub': backend,
        '^/[A-Za-z0-9_-]+\\d+$': backend
      }
    },
    build: {
      outDir: '../main/server/static/app',
      emptyOutDir: true,
      sourcemap: false,
      manifest: true,
      rollupOptions: {
        output: {
          // PDF.js looks up its WebAssembly decoders by their fixed filenames
          // (for example, `${wasmUrl}openjpeg.wasm`). Preserve those filenames
          // in one isolated directory while every other asset stays content-hashed.
          assetFileNames: (assetInfo) => {
            const pdfWasmFiles = new Set(['openjpeg.wasm', 'jbig2.wasm', 'qcms_bg.wasm']);
            return assetInfo.names.some((name) => pdfWasmFiles.has(name)) ? 'assets/pdfjs/[name][extname]' : 'assets/[name]-[hash][extname]';
          }
        }
      }
    },
    test: {
      environment: 'jsdom',
      setupFiles: './src/test/setup.ts',
      globals: false
    }
  };
});
