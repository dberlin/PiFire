import { pluginReact } from "@rsbuild/plugin-react";
import { defineConfig } from "@rstest/core";

// Env split by naming convention (replaces the per-file
// `// @vitest-environment jsdom` docblocks): *.test.tsx are component tests
// and get jsdom; *.test.ts are pure-function tests and stay on fast node.
//
// rstest's `plugins` field is Rsbuild-compatible (unlike vitest, which used
// esbuild's automatic JSX runtime out of the box via Vite). Without the React
// plugin, .tsx test files fail at runtime with "React is not defined" because
// nothing configures the automatic JSX transform for rstest's Rspack build.
// Rsbuild inlines every PUBLIC_* variable from the ambient shell at build time.
// Eight modules read `import.meta.env.PUBLIC_PIFIRE_URL` as their fetch base
// (useLiveState.ts, DashboardRoute.tsx, historyApi.ts, settingsRoutes.ts,
// wizardRoutes.ts, useSaveSettings.ts, UnitsTab.tsx, HistoryPage.tsx), and
// useLiveState.ts:72 falls back through PUBLIC_PIFIRE_TARGET for the origin it
// DISPLAYS. Inlining is right for a dev server and wrong for a test run: with
// either variable exported, useLiveState.test.tsx stops testing the in-code
// fallback and asserts against whatever origin the developer's shell names.
// Measured: `PUBLIC_PIFIRE_URL=http://localhost:5300 bun run test` failed with
// "expected http://localhost:5000, received http://localhost:5300".
//
// Workspaces now point the proxy with PIFIRE_BACKEND_URL, which is deliberately
// not a PUBLIC_ name and never reaches the bundle -- so that particular setup no
// longer trips this. Both PUBLIC_ names remain supported for aiming a single
// checkout at a real grill, though, and a developer using one should not get a
// red suite for it. A unit test must not depend on which backend happens to be
// running, so tests always see the unset case.
const shared = {
  setupFiles: ["./src/test-setup.ts"],
  exclude: ["**/node_modules/**", "tests/e2e/**"],
  plugins: [pluginReact()],
  source: {
    define: {
      "import.meta.env.PUBLIC_PIFIRE_URL": JSON.stringify(""),
      "import.meta.env.PUBLIC_PIFIRE_TARGET": JSON.stringify(""),
    },
  },
};

export default defineConfig({
  coverage: {
    provider: "istanbul",
    all: true,
    include: ["src/**/*.{ts,tsx}"],
    exclude: [
      "src/**/*.test.*",
      "src/main.tsx",
      "src/**/*.d.ts",
      "src/test-setup.ts",
      "src/test-utils.tsx",
    ],
    thresholds: {
      "src/**/*.{ts,tsx}": { lines: 75, perFile: true },
    },
  },
  projects: [
    { ...shared, name: "unit-node", include: ["src/**/*.test.ts"], testEnvironment: "node" },
    { ...shared, name: "unit-jsdom", include: ["src/**/*.test.tsx"], testEnvironment: "jsdom" },
    // Config modules live at the package root, not under src/, because they are
    // read by rsbuild/playwright rather than bundled into the app. Without this
    // project their tests match no glob and are silently never run.
    { ...shared, name: "unit-config", include: ["*.test.ts"], testEnvironment: "node" },
  ],
});
