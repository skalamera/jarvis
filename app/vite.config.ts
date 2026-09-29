import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  server: { port: 5199, strictPort: true },
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 2000 },
  test: { environment: "node" },
} as any);
