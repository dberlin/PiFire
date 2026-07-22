import tsParser from "@typescript-eslint/parser";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";

// Biome owns formatting and generic lint (biome.json). eslint carries ONLY what
// Biome cannot: the React Compiler diagnostics in eslint-plugin-react-hooks
// (e.g. set-state-in-effect — the house render-phase-sync rule) + react-refresh.
export default [
  { ignores: ["dist", "tests/e2e", "*.config.js", "*.config.ts"] },
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
