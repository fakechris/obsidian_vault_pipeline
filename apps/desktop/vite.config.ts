import { defineConfig } from "vite";

// The Tauri window's REAL UI is the console-ui portal served in-process by
// ovp-server; this tiny frontend is only the boot splash + first-run screen
// shown until the backend navigates the window to the local server.
const host = process.env.TAURI_DEV_HOST;

export default defineConfig({
  clearScreen: false,
  server: {
    port: 1421,
    strictPort: true,
    host: host || false,
    watch: { ignored: ["**/src-tauri/**"] },
  },
  envPrefix: ["VITE_", "TAURI_"],
  build: {
    target: "esnext",
    minify: !process.env.TAURI_DEBUG ? "esbuild" : false,
    sourcemap: !!process.env.TAURI_DEBUG,
  },
});
