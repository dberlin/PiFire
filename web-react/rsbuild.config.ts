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
      // PiFire's own static assets -- currently just the wizard's board photos
      // under /static/img/wizard/. Scoped to /static/img and NOT bare /static:
      // rsbuild emits THIS app's bundles under /static/js and /static/css (see
      // web-react/dist), so a blanket /static proxy would hand every script and
      // stylesheet to Flask. rsbuild's default asset dirs are js/css/font/wasm/
      // image/media -- "img" is not one of them, so this prefix cannot collide.
      "/static/img": { target, changeOrigin: true },
    },
  },
});
