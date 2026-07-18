import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": "/src" } },
  server: {
    proxy: {
      "/api": "http://localhost:5999",
      "/sendTask": "http://localhost:5999",
      "/health": "http://localhost:5999",
    },
  },
  build: { outDir: "out", emptyOutDir: true, sourcemap: false },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    globals: true,
  },
});
