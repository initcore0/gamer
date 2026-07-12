/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies the JSON API + legacy status/static to the running
// FastAPI app (default :8080), so the browser talks same-origin and CORS stays
// off. `npm run build` emits a self-contained bundle into ./dist, which the
// backend serves in production (see src/gamer/api/spa.py).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8080",
      "/static": "http://localhost:8080",
      "/status": "http://localhost:8080",
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    css: false,
    restoreMocks: true,
  },
});
