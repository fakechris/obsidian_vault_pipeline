import { defineConfig } from 'vitest/config';

// Renderer unit tests run in plain node — assertions walk the ReactNode
// tree, no DOM. A separate config (vitest prefers this file over
// vite.config.ts) so tests skip the react/tailwind build plugins.
export default defineConfig({
  esbuild: { jsx: 'automatic' },
  test: {
    environment: 'node',
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
