import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// EPIPE / ECONNRESET hit the proxy whenever uvicorn --reload bounces the
// backend mid-WS-frame. The frontend reconnects on its own; just don't spam
// stderr. Anything else still surfaces.
const RELOAD_NOISE = new Set(["EPIPE", "ECONNRESET", "ECONNREFUSED"]);

// Proxy target: local Core, or Jetson for mic-on-localhost debugging, e.g.
//   set REBOT_PROXY_TARGET=http://192.168.0.103:1882 && npm run dev
const PROXY_TARGET = process.env.REBOT_PROXY_TARGET || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: PROXY_TARGET,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on("error", (err) => {
            const code = (err as NodeJS.ErrnoException).code;
            if (!code || !RELOAD_NOISE.has(code)) console.error("[api proxy]", err);
          });
        },
      },
      "/ws": {
        target: PROXY_TARGET.replace(/^http/, "ws"),
        ws: true,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on("error", (err) => {
            const code = (err as NodeJS.ErrnoException).code;
            if (!code || !RELOAD_NOISE.has(code)) console.error("[ws proxy]", err);
          });
        },
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
