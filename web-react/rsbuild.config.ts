import { defineConfig } from "@rsbuild/core";
import { pluginReact } from "@rsbuild/plugin-react";
import { pluginTailwindcss } from "@rsbuild/plugin-tailwindcss";
import { ports } from "./ports";

// The React app talks to a running PiFire instance. In dev we proxy /socket.io
// and /api so the browser connects same-origin without CORS. Which instance,
// and which port this server binds, come from ./ports -- see that file for how
// to run several checkouts at once.
const target = ports.pifireUrl;

// Browser targets are pinned in package.json's `browserslist`, not left to
// Rsbuild's defaults. Two reasons, and the second is the load-bearing one:
//
//   - Tailwind v4 requires Cascade Layers, @property and color-mix(), i.e.
//     Safari 16.4 / Chrome 111 / Firefox 128. Rsbuild's default target set is
//     older than that, so the default would be quietly wrong.
//   - An unpinned list means Lightning CSS's output changes whenever a
//     dependency updates. tests/e2e/baselines/ would report that as a visual
//     regression with no cause anywhere in the diff.
//
// This raises nothing in practice: the stylesheets already use
// color-mix(in srgb, var(--accent) ...), which Lightning CSS cannot precompute
// through a var() and passes through verbatim, so the shipped CSS already
// required Chrome 111 / Safari 16.2 before this key existed.
export default defineConfig({
  // @rsbuild/plugin-tailwindcss wraps @tailwindcss/webpack. Deliberately NOT
  // @tailwindcss/postcss: Rsbuild transforms CSS with Lightning CSS through
  // Rspack's built-in lightningcss-loader and registers postcss-loader only
  // when a postcss.config.* exists or tools.postcss is set -- neither of which
  // this repo has. Tailwind v4 uses Lightning CSS internally too, so the PostCSS
  // route would INTRODUCE a PostCSS pass into a pipeline that has none, for no
  // benefit. See https://rsbuild.rs/guide/styling/tailwindcss.
  //
  // It follows that autoprefixer, postcss-preset-env, postcss-nesting and
  // cssnano are all not needed and must not be added: Lightning CSS already
  // reads package.json's browserslist, prefixes, and downlevels nesting, and
  // minification comes from LightningCssMinimizerRspackPlugin.
  plugins: [pluginReact({ reactCompiler: true }), pluginTailwindcss()],
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
