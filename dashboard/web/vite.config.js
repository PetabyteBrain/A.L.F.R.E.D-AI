import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: `npm run dev` proxies the API to the Python backend (python -m tests.devserver on :8765).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    sourcemap: false,
    // Keep everything in external files so the strict CSP (script-src 'self') holds.
    modulePreload: { polyfill: false },
    assetsInlineLimit: 0,
  },
  server: {
    port: 5180,
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
});
