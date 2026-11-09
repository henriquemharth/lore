import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development Vite (5283) proxies /api to the lore server (8610).
// In normal use the lore server itself serves dist/: a single process on 127.0.0.1.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    port: 5283,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8610" },
  },
  preview: { host: "127.0.0.1", port: 5284 },
  build: { outDir: "dist", emptyOutDir: true, sourcemap: false },
});
