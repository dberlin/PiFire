import { defineConfig } from "@rsbuild/core";
import { pluginReact } from "@rsbuild/plugin-react";
import { ports } from "./ports";

// The React app talks to a running PiFire instance. In dev we proxy /socket.io
// and /api so the browser connects same-origin without CORS. Which instance,
// and which port this server binds, come from ./ports -- see that file for how
// to run several checkouts at once.
const target = ports.pifireUrl;

export default defineConfig({
  plugins: [pluginReact({ reactCompiler: true })],
  html: { template: "./index.html" },
  source: {
    entry: { index: "./src/main.tsx" },
    // DISPLAY ONLY -- never a fetch base. The backend origin is deliberately
    // kept out of the bundle (see ./ports) so requests stay same-origin and go
    // through the proxy above. But ConnectionStatus has to be able to NAME the
    // backend it is waiting on, and without this it would print a hardcoded
    // localhost:5000 while actually talking to whatever `target` is -- a
    // diagnostic that misleads precisely when someone is debugging why nothing
    // connects.
    define: { "import.meta.env.PUBLIC_PIFIRE_TARGET": JSON.stringify(target) },
  },
  server: {
    port: ports.appPort,
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
