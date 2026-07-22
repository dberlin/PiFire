# web-react Toolchain Migration (TS7 + rsbuild + Biome + rstest) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `web-react/` toolchain — TypeScript 7 (native tsgo) as typechecker, rsbuild (with native React Compiler) instead of vite, Biome for formatting + generic linting with eslint slimmed to the React-Compiler rules, and rstest instead of vitest — with the full gate green after every stage.

**Architecture:** Four staged swaps, each an independently green, reviewable unit: TS7 → Biome/eslint-slim → rsbuild → rstest. The riskiest change (rstest, pre-1.0) lands last against a suite the earlier stages kept green, and reverts cleanly to standalone vitest if it can't express something load-bearing.

**Tech Stack:** bun (package manager/runner — NEVER npm), typescript 7.0.2 (via npm alias) + typescript 5.9.3 (kept for the eslint parser), @biomejs/biome 2.5.5, @rsbuild/core 2.1.7 + @rsbuild/plugin-react 2.1.0, @rstest/core 0.11.3, eslint 10 + eslint-plugin-react-hooks 7 (kept), Playwright (untouched).

## Global Constraints

- All work in `web-react/` only. NO backend (Python) changes. No product-code behavior changes — the only source edits are mechanical (env-var names, test-runner imports, formatting).
- bun for everything: `bun add -d`, `bun remove`, `bun run`, `bunx`. Commit `bun.lock` with every dependency change.
- Exact versions: `typescript7@npm:typescript@7.0.2` (alias; `typescript@^5.9.3` STAYS installed — TS7 does not ship the classic JS API that `@typescript-eslint/parser` requires; `require('typescript')` under v7 returns a version-only stub), `@biomejs/biome@2.5.5`, `@rsbuild/core@2.1.7`, `@rsbuild/plugin-react@2.1.0`, `@rstest/core@0.11.3`, `@typescript-eslint/parser@8.65.0`.
- The full gate after EVERY task: `cd web-react && bun run typecheck && bun run lint && bun run test && bun run build` — all exit 0, test counts unchanged (112 tests / 29 files) unless a task states otherwise. (Task 1 creates the `typecheck` script; before that it's `bunx tsc -b`.)
- Playwright e2e (`bun run test:e2e`) gates Tasks 4, 5, 6 — it needs the live prototype backend on :5000; **restart gunicorn first** (it has no `--reload`): find the master `pid` of `gunicorn ... app:app`, `kill` it, relaunch `uv run gunicorn -k gthread --threads 25 -b 0.0.0.0:5000 -w 1 app:app` from the repo root (control.py keeps running; do NOT touch it), confirm `curl -s -o /dev/null -w '%{http_code}' http://localhost:5000/api/current` → 201.
- Dev server port stays **5173** (playwright.config.ts `webServer` expects it; rsbuild defaults to 3000 — pin it).
- NO new `eslint-disable`, `biome-ignore`, or `useEffect`-for-state-sync anywhere.
- The 2b-1 follow-ups (waitFor conversion, fallback-default alignment, a11y hint) must NOT ride along in these diffs.

---

### Task 1: TypeScript 7 as the typechecker

**Files:**
- Modify: `web-react/package.json` (devDeps + scripts)

**Interfaces:**
- Consumes: existing `tsc -b` build script, single `web-react/tsconfig.json` (`noEmit: true`, `include: ["src"]`).
- Produces: script `"typecheck": "node node_modules/typescript7/bin/tsc -b"` — every later task's gate uses `bun run typecheck`. The `typescript` package remains 5.9.3 (eslint parser API only); TS7 lives at the `typescript7` alias.

- [ ] **Step 1: Baseline** — `cd web-react && bunx tsc -b && bun run lint && bun run test && bun run build` all green (confirms a clean start; 112 tests / 29 files).

- [ ] **Step 2: Add the TS7 alias**

```bash
cd web-react && bun add -d typescript7@npm:typescript@7.0.2
```

- [ ] **Step 3: Verify both compilers coexist**

```bash
node node_modules/typescript7/bin/tsc --version   # Expected: Version 7.0.2
bunx tsc --version                                # Expected: Version 5.9.3 (the alias must NOT shadow .bin/tsc)
```

If `bunx tsc --version` reports 7.0.2, the alias's bin shadowed the real one — remove the `node_modules/.bin/tsc` shadowing by reinstalling (`rm -rf node_modules && bun install`) and re-check; scripts below use explicit paths precisely so bin-link order never matters.

- [ ] **Step 4: Wire scripts** — in `web-react/package.json` scripts, add `typecheck` and change `build`:

```json
"typecheck": "node node_modules/typescript7/bin/tsc -b",
"build": "node node_modules/typescript7/bin/tsc -b && vite build",
```

- [ ] **Step 5: Run the TS7 typecheck** — `bun run typecheck` → exit 0, no output. If TS7 reports errors TS 5.9 didn't: fix only mechanical/trivial ones (e.g. a stricter lib signature) preserving behavior; anything semantic → STOP and report (status DONE_WITH_CONCERNS or BLOCKED).

- [ ] **Step 6: Full gate** — `bun run typecheck && bun run lint && bun run test && bun run build` → all green.

- [ ] **Step 7: Commit**

```bash
cd web-react && git add package.json bun.lock
git commit -m "build(web-react): typecheck with TypeScript 7 (tsgo) via typescript7 alias"
```

---

### Task 2: Biome — formatter + generic linter

**Files:**
- Create: `web-react/biome.json`
- Modify: `web-react/package.json` (devDep + scripts); mechanical rewrite of `src/**` + config files (format commit)

**Interfaces:**
- Consumes: nothing new.
- Produces: `bun run lint` = `biome check . && eslint .`; `bun run format` = `biome format --write .`. Task 3 slims eslint knowing Biome's recommended lint set is active.

- [ ] **Step 1: Install** — `cd web-react && bun add -d @biomejs/biome@2.5.5`

- [ ] **Step 2: Create `web-react/biome.json`** (tuned to the existing de-facto style — 2-space, double quotes, semicolons):

```json
{
  "$schema": "./node_modules/@biomejs/biome/configuration_schema.json",
  "files": {
    "includes": ["src/**", "tests/**", "*.json", "*.ts", "*.js", "index.html", "!dist/**", "!node_modules/**"]
  },
  "formatter": {
    "enabled": true,
    "indentStyle": "space",
    "indentWidth": 2,
    "lineWidth": 100
  },
  "javascript": {
    "formatter": {
      "quoteStyle": "double",
      "semicolons": "always",
      "trailingCommas": "all"
    }
  },
  "linter": {
    "enabled": true,
    "rules": {
      "recommended": true
    }
  }
}
```

(If Biome 2.5 rejects any key, fix per `bunx biome migrate` / the printed error — the schema file is in `node_modules` for reference. Keep the SETTINGS as specified; only the key spelling may differ.)

- [ ] **Step 3: Dry-run the format diff** — `bunx biome format . | head -100` (or `bunx biome check .`) and eyeball: changes must be whitespace/quotes/commas/parens only. If the diff shows semantic rewrites, stop and report.

- [ ] **Step 4: Commit the config alone**

```bash
git add biome.json package.json bun.lock && git commit -m "build(web-react): add Biome 2.5 (format + lint config)"
```

- [ ] **Step 5: Mechanical format-the-world commit** — `bunx biome format --write .` then `git add -A src tests *.ts *.js index.html && git commit -m "style(web-react): biome format (mechanical, no hand edits)"`. Verify mechanicalness: `git diff HEAD~1 --stat` (breadth is fine) and `git diff HEAD~1 -w | head -50` shows little-to-nothing beyond quote/comma lines.

- [ ] **Step 6: Resolve Biome lint findings** — `bunx biome check .`. For each finding: apply Biome's safe fix (`bunx biome check --write .`) or a minimal hand fix if it's trivially behavior-preserving; otherwise turn the specific rule off in `biome.json` under `linter.rules` with a `//`-comment-free JSON structure and record the justification in the commit message (JSON has no comments — the commit message carries the "why"). Likely candidates: `noExplicitAny` in test files (acceptable to disable for `**/*.test.*` via an `overrides` block), a11y rules on existing widgets (do NOT restructure widgets in this task — disable the specific rule with justification; a11y work is a 2b-2 concern). Zero findings remain.

- [ ] **Step 7: Wire scripts** — in `package.json`:

```json
"lint": "biome check . && eslint .",
"format": "biome format --write .",
```

- [ ] **Step 8: Full gate + commit**

```bash
bun run typecheck && bun run lint && bun run test && bun run build
git add -A && git commit -m "build(web-react): biome check joins the lint gate"
```

---

### Task 3: Slim eslint to React-Compiler + react-refresh rules

**Files:**
- Modify: `web-react/eslint.config.js` (full rewrite below), `web-react/package.json`

**Interfaces:**
- Consumes: Biome lint active (Task 2) — it now owns the generic-rule coverage `@eslint/js`/`typescript-eslint` provided.
- Produces: eslint runs ONLY `eslint-plugin-react-hooks` (React Compiler diagnostics incl. `set-state-in-effect`) + `react-refresh`, parsing TS via `@typescript-eslint/parser` (which is why `typescript@5.9.3` stays).

- [ ] **Step 1: Swap deps**

```bash
cd web-react && bun add -d @typescript-eslint/parser@8.65.0 && bun remove typescript-eslint @eslint/js globals
```

- [ ] **Step 2: Rewrite `web-react/eslint.config.js`**

```js
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tsParser from "@typescript-eslint/parser";

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
```

- [ ] **Step 3: Prove the compiler rules still fire** — create `src/lint-canary.tsx`:

```tsx
import { useEffect, useState } from "react";
export function Canary({ x }: { x: number }) {
  const [v, setV] = useState(0);
  useEffect(() => {
    setV(x); // must trigger the react-hooks set-state-in-effect diagnostic
  }, [x]);
  return <span>{v}</span>;
}
```

Run `bunx eslint src/lint-canary.tsx` → expect a nonzero exit with a react-hooks diagnostic (the set-state-in-effect family). Then `rm src/lint-canary.tsx`. If NO diagnostic fires, the slim config lost the compiler rules — STOP and fix before proceeding (check the plugin's flat-config export name).

- [ ] **Step 4: Full gate** — `bun run typecheck && bun run lint && bun run test && bun run build` → green. eslint must exit 0 on the real codebase (it did before with more rules; fewer rules cannot add findings).

- [ ] **Step 5: Commit**

```bash
git add eslint.config.js package.json bun.lock
git commit -m "build(web-react): slim eslint to react-hooks compiler rules + react-refresh"
```

---

### Task 4: vite → rsbuild (dev/build/preview + React Compiler + env rename)

**Files:**
- Create: `web-react/rsbuild.config.ts`, `web-react/vitest.config.ts` (interim), `web-react/src/env.d.ts`
- Delete: `web-react/vite.config.ts`, `web-react/src/vite-env.d.ts`
- Modify: `web-react/package.json`, `web-react/index.html`, `src/useDashData.ts`, `src/settings/useSaveSettings.ts`, `src/settings/settingsRoutes.ts`, `src/settings/tabs/UnitsTab.tsx`

**Interfaces:**
- Consumes: `typecheck` script (Task 1); biome+eslint lint gate (Tasks 2–3).
- Produces: `PUBLIC_PIFIRE_URL` / `PUBLIC_DEMO` env vars (rsbuild injects only `PUBLIC_*` into client code) — Task 5/6 and all future docs use these names; dev server on :5173; vitest running standalone via `vitest.config.ts` until Task 5 replaces it.

- [ ] **Step 1: Swap deps**

```bash
cd web-react && bun add -d @rsbuild/core@2.1.7 @rsbuild/plugin-react@2.1.0
bun remove vite @vitejs/plugin-react @rolldown/plugin-babel babel-plugin-react-compiler
```

(vitest stays; it vendors its own vite internally. If Step 6's build fails asking for `babel-plugin-react-compiler`, re-add just that package — the SWC-native path shouldn't need it.)

- [ ] **Step 2: Create `web-react/rsbuild.config.ts`**

```ts
import { defineConfig } from "@rsbuild/core";
import { pluginReact } from "@rsbuild/plugin-react";

// The React app talks to a running PiFire instance. Point PUBLIC_PIFIRE_URL at
// that host (default http://localhost:5000). In dev we proxy /socket.io and
// /api so the browser connects same-origin without CORS. Port pinned to 5173
// (playwright.config.ts webServer expects it).
const target = process.env.PUBLIC_PIFIRE_URL || "http://localhost:5000";

export default defineConfig({
  plugins: [pluginReact({ reactCompiler: true })],
  html: { template: "./index.html" },
  source: { entry: { index: "./src/main.tsx" } },
  server: {
    port: 5173,
    proxy: {
      "/socket.io": { target, ws: true, changeOrigin: true },
      "/api": { target, changeOrigin: true },
    },
  },
});
```

(If `reactCompiler: true` fails the type check or build, consult `node_modules/@rsbuild/plugin-react/dist/index.d.ts` for the exact `reactCompiler` config shape — it maps to Rspack's SWC `reactCompiler` transform config; pass the object form it wants. The babel fallback — `@rsbuild/plugin-babel` + `babel-plugin-react-compiler` — only if SWC-native fails outright; report it if so.)

- [ ] **Step 3: `index.html`** — delete the line `<script type="module" src="/src/main.tsx"></script>` (rsbuild injects the entry from `source.entry`); keep everything else (fonts, `#root`).

- [ ] **Step 4: Env rename + typing.** In the 4 source files, rename `VITE_PIFIRE_URL` → `PUBLIC_PIFIRE_URL` and `VITE_DEMO` → `PUBLIC_DEMO` (5 total `import.meta.env.` reads: `useDashData.ts:19-20`, `useSaveSettings.ts:5`, `settingsRoutes.ts:3`, `UnitsTab.tsx:9`). Delete `src/vite-env.d.ts`; create `src/env.d.ts`:

```ts
/// <reference types="@rsbuild/core/types" />

interface ImportMetaEnv {
  readonly PUBLIC_DEMO?: string;
  readonly PUBLIC_PIFIRE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
```

- [ ] **Step 5: Interim vitest config.** Move the `test` block out of the doomed `vite.config.ts`: create `web-react/vitest.config.ts`:

```ts
import { configDefaults, defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Playwright owns tests/e2e — vitest must not import those specs.
    exclude: [...configDefaults.exclude, "tests/e2e/**"],
    setupFiles: ["./src/test-setup.ts"],
  },
});
```

Then `git rm vite.config.ts`.

- [ ] **Step 6: Scripts** — in `package.json`:

```json
"dev": "rsbuild dev",
"demo": "PUBLIC_DEMO=1 rsbuild dev",
"build": "node node_modules/typescript7/bin/tsc -b && rsbuild build",
"preview": "rsbuild preview",
```

- [ ] **Step 7: Full gate** — `bun run typecheck && bun run lint && bun run test && bun run build` → green (vitest still runs the 112).

- [ ] **Step 8: Prove the React Compiler is ACTIVE in the bundle** (a configured-but-inert plugin is the named risk):

```bash
grep -rl "memo_cache_sentinel" dist/static/js/ | head -1
```

Expected: at least one hit (`react.memo_cache_sentinel` is the compiler's memo-cache marker; `react/compiler-runtime` also acceptable as the grep target). Zero hits = compiler inert → STOP, use the d.ts config shape / babel fallback from Step 2, and re-verify.

- [ ] **Step 9: Live verification** — restart gunicorn (Global Constraints recipe), then `bun run test:e2e` → all specs pass (roundtrip + settings; playwright starts `bun run dev` on :5173 itself, which also proves the dev server + proxy). Demo smoke: `bun run demo &`, `curl -s -o /dev/null -w '%{http_code}' http://localhost:5173` → 200, then kill it.

- [ ] **Step 10: No stragglers** — `grep -rn "VITE_" src/ tests/ index.html *.ts *.json 2>/dev/null` → zero hits.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "build(web-react): replace vite with rsbuild (native React Compiler, PUBLIC_* env)"
```

---

### Task 5: vitest → rstest

**Files:**
- Create: `web-react/rstest.config.ts`
- Delete: `web-react/vitest.config.ts`
- Modify: `web-react/package.json`, `web-react/src/test-setup.ts`, all 29 `src/**/*.test.ts{,x}` files (mechanical codemod)

**Interfaces:**
- Consumes: everything green under vitest (Task 4).
- Produces: `bun run test` = `rstest run`; env split by glob project (`*.test.tsx` → jsdom, `*.test.ts` → node) replacing the 21 per-file docblocks; mock API is `rs.*`.

- [ ] **Step 1: Swap deps** — `bun add -d @rstest/core@0.11.3 && bun remove vitest` (KEEP `jsdom` — rstest's jsdom environment needs it; keep `@testing-library/*`).

- [ ] **Step 2: Create `web-react/rstest.config.ts`** (field names verified against `@rstest/core` 0.11.3 d.ts: `projects`, `name`, `include`, `exclude`, `testEnvironment`, `setupFiles`):

```ts
import { defineConfig } from "@rstest/core";

// Env split by naming convention (replaces the per-file
// `// @vitest-environment jsdom` docblocks): *.test.tsx are component tests
// and get jsdom; *.test.ts are pure-function tests and stay on fast node.
const shared = {
  setupFiles: ["./src/test-setup.ts"],
  exclude: ["**/node_modules/**", "tests/e2e/**"],
};

export default defineConfig({
  projects: [
    { ...shared, name: "unit-node", include: ["src/**/*.test.ts"], testEnvironment: "node" },
    { ...shared, name: "unit-jsdom", include: ["src/**/*.test.tsx"], testEnvironment: "jsdom" },
  ],
});
```

- [ ] **Step 3: Codemod the 29 test files** — two mechanical passes, then review:

```bash
cd web-react
# 1) runner import + vi -> rs (word-boundary; vi appears only as the mock API in these files)
grep -rl 'from "vitest"' src --include="*.test.ts" --include="*.test.tsx" \
  | xargs sed -i -e 's/from "vitest"/from "@rstest\/core"/' -e 's/\bvi\b/rs/g'
# 2) drop the now-redundant env docblocks (21 files)
grep -rl '@vitest-environment' src | xargs sed -i '/@vitest-environment jsdom/d'
```

Then `git diff --stat` and skim the diff: only import lines, `vi.`→`rs.` call sites, and deleted docblock lines. Any other change = sed over-matched — fix by hand. (`rs.fn`, `rs.mock`, `rs.spyOn`, `rs.clearAllMocks` all exist in 0.11.3; `rs.mock` is hoisted like `vi.mock`.)

- [ ] **Step 4: Rewire `src/test-setup.ts`**

```ts
import * as matchers from "@testing-library/jest-dom/matchers";
import { afterEach, expect } from "@rstest/core";
import { cleanup } from "@testing-library/react";

expect.extend(matchers);
afterEach(cleanup);
```

If `bun run typecheck` then errors on jest-dom matcher calls (`.toBeInTheDocument()` etc. unknown on rstest's Assertion type): find the augmentable interface with `grep -n "interface Assertion\|interface Matchers" node_modules/@rstest/core/dist/index.d.ts` and add `src/jest-dom.d.ts` augmenting that module with `TestingLibraryMatchers` from `@testing-library/jest-dom/matchers`, e.g.:

```ts
import type { TestingLibraryMatchers } from "@testing-library/jest-dom/matchers";

declare module "@rstest/core" {
  // Name must match the interface found in @rstest/core's d.ts.
  interface Assertion<T = unknown> extends TestingLibraryMatchers<unknown, T> {}
}
```

- [ ] **Step 5: Scripts** — `"test": "rstest run"`; delete `vitest.config.ts` (`git rm vitest.config.ts`).

- [ ] **Step 6: Run the suite** — `bun run test` → **112 tests / 29 files, all pass, console pristine** (no unhandled warnings). The env split is self-proving: jsdom-dependent tests (any RTL render) fail under node and pure node tests exercise no DOM — a full pass means every file landed in the right project. **FALLBACK (from the spec): if rstest 0.11.x cannot express something load-bearing (env split, hoisted mocks, jsdom fidelity, jest-dom matchers), STOP and report BLOCKED rather than contorting the suite — this task reverts cleanly to vitest.**

- [ ] **Step 7: Full gate + e2e** — `bun run typecheck && bun run lint && bun run test && bun run build` green; restart gunicorn (Global Constraints recipe) and `bun run test:e2e` → all pass.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "test(web-react): migrate vitest to rstest (glob-project env split, rs.* mocks)"
```

---

### Task 6: Final sweep — clean-install reproducibility

**Files:**
- Modify: none expected (verification task; fixes only if a check fails)

**Interfaces:**
- Consumes: Tasks 1–5 complete.
- Produces: the branch's "done" evidence.

- [ ] **Step 1: Dependency hygiene** — `grep -E '"(vite|vitest|@vitejs/|@rolldown/|babel-plugin-react-compiler|typescript-eslint|@eslint/js|globals)"' web-react/package.json` → zero hits (they may exist transitively in `bun.lock`; that's fine).

- [ ] **Step 2: Clean-install repro**

```bash
cd web-react && rm -rf node_modules && bun install --frozen-lockfile
```

→ exits 0 with no lockfile drift.

- [ ] **Step 3: Full gate from clean** — `bun run typecheck && bun run lint && bun run test && bun run build` → green (112/29).

- [ ] **Step 4: Straggler grep (repo-wide)** — `grep -rn "VITE_" web-react/src web-react/tests web-react/index.html web-react/*.ts web-react/*.json` → zero hits.

- [ ] **Step 5: Commit (only if fixes were needed)** — otherwise nothing to commit; report the evidence.

---

## Verification summary (matches the spec)

- Per task: `typecheck` (TS7) + `lint` (biome check + slim eslint) + `test` (112/29) + `build`, all green.
- Task 4: React Compiler proven ACTIVE via bundle grep; e2e + demo smoke live.
- Task 5: suite green under rstest with the glob env split self-proving; e2e re-run.
- Task 6: frozen-lockfile clean install + full gate from scratch.
