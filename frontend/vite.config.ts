import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: { assetsInlineLimit: 0 },   // brand SVGs are used as CSS masks and must stay real files
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
});
