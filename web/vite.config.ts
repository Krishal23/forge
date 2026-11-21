import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies the API so the browser sees one origin. Both stay on loopback.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8765", changeOrigin: false } },
  },
  build: { outDir: "dist", sourcemap: false },
});
