import { defineConfig } from "@rsbuild/core";
import { pluginReact } from "@rsbuild/plugin-react";

// The React app talks to a running PiFire instance. Point PUBLIC_PIFIRE_URL at
// that host (default http://localhost:5000). In dev we proxy /socket.io and
// /api so the browser connects same-origin without CORS. Port pinned to 5173
// (playwright.config.ts webServer expects it).
const target = process.env.PUBLIC_PIFIRE_URL || "http://localhost:5000";

export default defineConfig({
  plugins: [pluginReact({ reactCompiler: true })],
  html: { template: "./index.html" },
  source: { entry: { index: "./src/main.tsx" } },
  server: {
    port: 5173,
    proxy: {
      "/socket.io": { target, ws: true, changeOrigin: true },
      "/api": { target, changeOrigin: true },
    },
  },
});
