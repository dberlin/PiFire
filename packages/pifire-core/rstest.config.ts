import { defineConfig } from "@rstest/core";

// Everything here is pure logic with no DOM: one node project, no jsdom.
export default defineConfig({
  include: ["tests/**/*.test.ts"],
  testEnvironment: "node",
});
