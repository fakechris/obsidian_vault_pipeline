import { defineConfig } from 'vite';
import { resolve } from 'path';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// Single-entry React SPA (M33). Client routes (/viz/graph, /viz/explore, …)
// are handled by react-router; ovp-server falls back to viz/index.html for
// extensionless /viz/* paths.
export default defineConfig({
  base: '/viz/',
  plugins: [react(), tailwindcss()],
  build: {
    outDir: resolve(__dirname, '../.ovp/console/viz'),
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:9990',
        changeOrigin: true,
      },
    },
  },
});
