import { configDefaults, defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Playwright owns tests/e2e — vitest must not import those specs.
    exclude: [...configDefaults.exclude, "tests/e2e/**"],
    setupFiles: ["./src/test-setup.ts"],
  },
});
