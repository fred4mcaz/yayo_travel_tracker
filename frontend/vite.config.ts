/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // Keeps the served asset paths aligned with the /assets mount in main.py.
    assetsDir: "assets",
  },
  server: {
    // PORT lets a second dev server (e.g. another Claude session) come up on an
    // assigned port instead of colliding with 5173.
    port: Number(process.env.PORT) || 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  test: {
    // Component tests need a DOM; the pure helpers run fine under it too.
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
