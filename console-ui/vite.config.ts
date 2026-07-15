import { defineConfig } from 'vite';
import { resolve } from 'path';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// Single-entry React SPA — the OVP2 portal (portal v2, B1). Served at the
// site root: ovp-server serves dist/index.html for `/` and every client
// route (/library, /search, …); legacy generated pages stay reachable by
// exact filename. Deploy = `ovp2 serve --viz-dir console-ui/dist` (overlay)
// or copy dist/ to <vault>/.ovp/console/app/.
// Live build keeps base '/'. The static publish build passes VITE_OVP_BASE
// (e.g. '/blog/') so the SPA and its `<base>/api/*.json` fetches resolve under
// a GitHub-Pages sub-path.
export default defineConfig({
  base: process.env.VITE_OVP_BASE ?? '/',
  plugins: [react(), tailwindcss()],
  build: {
    outDir: resolve(__dirname, 'dist'),
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
