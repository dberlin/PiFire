import { defineConfig } from "vite";
import react, { reactCompilerPreset } from "@vitejs/plugin-react";
import babel from "@rolldown/plugin-babel";
// The React POC talks to a running PiFire instance over its existing
// Flask-SocketIO endpoint. Point VITE_PIFIRE_URL at that host (default
// http://localhost:5000). In dev we proxy /socket.io so the browser can
// connect same-origin without CORS.
const target = process.env.VITE_PIFIRE_URL || "http://localhost:5000";

export default defineConfig({
  plugins: [
    react(),
    babel({
      presets: [reactCompilerPreset()],
    }),
  ],
  server: {
    proxy: {
      "/socket.io": { target, ws: true, changeOrigin: true },
      "/api": { target, changeOrigin: true },
    },
  },
});
