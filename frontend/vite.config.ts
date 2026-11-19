/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build ciktisi FastAPI'nin sundugu klasore gider (src/tradecrm/api/static).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/tradecrm/api/static",
    emptyOutDir: true,
    chunkSizeWarningLimit: 900,
  },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
