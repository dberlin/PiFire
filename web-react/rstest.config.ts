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
const shared = {
  setupFiles: ["./src/test-setup.ts"],
  exclude: ["**/node_modules/**", "tests/e2e/**"],
  plugins: [pluginReact()],
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
