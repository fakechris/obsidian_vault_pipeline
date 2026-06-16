import { defineConfig } from 'vite';
import { resolve } from 'path';

export default defineConfig({
  base: '/viz/',
  build: {
    outDir: resolve(__dirname, '../.ovp/console/viz'),
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        graph: resolve(__dirname, 'graph.html'),
        flow: resolve(__dirname, 'flow.html'),
        explore: resolve(__dirname, 'explore.html'),
        monitor: resolve(__dirname, 'monitor.html'),
      },
    },
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
