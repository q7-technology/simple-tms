/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Vite runs this under Node; the app itself never sees process.
declare const process: { env: Record<string, string | undefined> };

const api = { "/v1": { target: process.env.TMS_API_URL ?? "http://127.0.0.1:8022", changeOrigin: true } };

// Served by Caddy at /driver/ (deploy/Caddyfile). `npm run dev` and
// `npm run preview` send /v1 to the API, so a phone can be pointed at either.
export default defineConfig({
  base: "/driver/",
  plugins: [react(), tailwindcss()],
  server: { port: 5175, proxy: api },
  preview: { port: 4175, proxy: api },
  build: { outDir: "dist", sourcemap: false },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    testTimeout: 20000,
  },
});
