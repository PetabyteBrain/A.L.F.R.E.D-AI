import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

const here = (p) => fileURLToPath(new URL(p, import.meta.url));

// Two pages: index.html = dashboard app (served at /app), home.html = public homepage (served at /).
// Dev: `npm run dev` proxies the API to the Python backend (python -m tests.devserver on :8765).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    sourcemap: false,
    // Keep everything in external files so the strict CSP (script-src 'self') holds.
    modulePreload: { polyfill: false },
    assetsInlineLimit: 0,
    rollupOptions: { input: { app: here("./index.html"), home: here("./home.html") } },
  },
  server: {
    port: 5180,
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
});
