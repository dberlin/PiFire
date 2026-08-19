import tsParser from "@typescript-eslint/parser";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";

// Biome owns formatting and generic lint (biome.jsonc). eslint carries ONLY what
// Biome cannot: the React Compiler diagnostics in eslint-plugin-react-hooks
// (e.g. set-state-in-effect — the house render-phase-sync rule) + react-refresh.
//
// devDependencies carries typescript@5.9.3 SOLELY because @typescript-eslint/parser
// requires the classic TS JS API, which typescript@7 no longer ships (it's a native
// binary + a version stub). The real typecheck gate is the `typescript7` npm alias,
// invoked via `node node_modules/typescript7/bin/tsc -b` (`bun run typecheck`) — do
// not "clean up" the 5.9 dep.
export default [
  {
    ignores: [
      "dist",
      "coverage",
      "tests/e2e",
      "*.config.js",
      "*.config.ts",
      // settingsTypes.gen.ts and controllerTypes.gen.ts moved to @pifire/core
      // (packages/pifire-core/src/settings) — outside web-react's tree, so
      // they're no longer picked up by `eslint .` here at all.
      "src/helpers/settings/settingsDefaults.gen.ts",
    ],
  },
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: 2022,
      sourceType: "module",
    },
    plugins: { "react-hooks": reactHooks, "react-refresh": reactRefresh },
    rules: {
      ...reactHooks.configs["recommended-latest"].rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
    },
  },
];
