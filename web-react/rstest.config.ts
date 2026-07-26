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
// Rsbuild inlines every PUBLIC_* variable from the ambient shell at build time,
// and a dozen modules read `import.meta.env.PUBLIC_PIFIRE_URL` to choose the
// backend origin (useLiveState.ts, DashboardRoute.tsx, historyApi.ts,
// settingsRoutes.ts, wizardRoutes.ts, useSaveSettings.ts, UnitsTab.tsx). That is
// right for a dev server and wrong for a test run: a checkout that exports
// PUBLIC_PIFIRE_URL to reach its own backend -- which ./ports.ts tells every
// parallel workspace to do -- made useLiveState.test.tsx assert against that
// shell's port and the suite went red. A unit test must not depend on which
// backend the developer happens to be running, so tests always see the unset
// case and exercise the in-code fallback.
const shared = {
  setupFiles: ["./src/test-setup.ts"],
  exclude: ["**/node_modules/**", "tests/e2e/**"],
  plugins: [pluginReact()],
  source: { define: { "import.meta.env.PUBLIC_PIFIRE_URL": JSON.stringify("") } },
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
