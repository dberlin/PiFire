# web-react Toolchain Migration — TS7 + rsbuild + Biome + rstest — Design Spec

**Date:** 2026-07-22
**Status:** Approved (design), pending implementation plan
**Scope:** `web-react/` only. Lands BEFORE settings phase 2b-2, on the just-pushed
2b-1 base (`69b900b`), so 2b-2's new components are built on the final toolchain.

## Context

`web-react/` currently builds with vite 8.1.5 (rolldown) + `@vitejs/plugin-react` 6
(React Compiler via `@rolldown/plugin-babel` + `reactCompilerPreset`), typechecks
with `tsc -b` (TypeScript 5.9.3), tests with vitest 4 (112 tests / 29 files; jsdom
opted into per-file via `// @vitest-environment jsdom` docblocks), and lints with
eslint 10 flat config (`@eslint/js` + `typescript-eslint` + `eslint-plugin-react-hooks@7`
compiler rules + `eslint-plugin-react-refresh`). No formatter is configured.
Playwright owns `tests/e2e` (8 specs across roundtrip + settings, run against the
live prototype backend). Package manager/runtime is **bun** throughout.

Decisions made in brainstorming:
- **Test runner: rstest** (full Rspack stack; not vitest-on-vite-internals).
- **Lint: Biome format + Biome lint for generic rules, slim eslint kept** solely
  for what only it has — `eslint-plugin-react-hooks` (React Compiler diagnostics,
  e.g. the `set-state-in-effect` house rule) + `eslint-plugin-react-refresh`.
- **Sequencing: toolchain first**, then 2b-2.

## Goals

1. TypeScript 7.0.2 (native/tsgo compiler) as the typechecker.
2. rsbuild 2.1.7 replaces vite for dev / build / preview, with React Compiler
   still active and verified.
3. Biome 2.5.5 as the auto-formatter and generic linter; eslint slimmed to the
   React-Compiler/hooks + react-refresh rules only.
4. rstest 0.11.3 replaces vitest; all 112 tests pass unmodified in behavior.
5. Every existing gate stays green after each stage, and the Playwright e2e
   suite still passes against the live backend at the end.

## Non-Goals

- No product-code behavior changes (pure infra; the only source edits are
  mechanical: env-var names, test-runner imports, formatting).
- No 2b-2 features.
- No change to the Playwright e2e runner or specs (beyond env-var name reads,
  if any).
- No monorepo/workspace restructuring; `web-react/` stays a standalone app.

## Approach: four staged swaps

Each stage is one reviewable unit that ends with the full gate green, so any
failure has a single cause. Order: TS7 → Biome/eslint → rsbuild → rstest — the
new test runner (highest-risk, pre-1.0) validates last, against a suite the
earlier stages kept green.

### Stage 1 — TypeScript 7

- `typescript` `^5.9.3` → `7.0.2`. No other dependency moves.
- Keep `tsc -b` if the TS7 binary supports `--build`; otherwise switch the
  `build`/typecheck invocations to `tsc --noEmit` (project has a single
  tsconfig tree; `-b` is convenience, not a hard requirement).
- **Risk:** `@typescript-eslint/parser` (needed by the slim eslint for TSX
  parsing) peer-range vs TS7. Mitigation: pin/upgrade `typescript-eslint` to
  the version that declares TS7 support; if none exists yet, pin the parser
  with an overrides entry and verify parse output on the existing files. This
  is checked in Stage 1 even though eslint slimming happens in Stage 2,
  because Stage 1 must leave `bun run lint` green.

### Stage 2 — Biome (format + generic lint) and eslint slim-down

- Add `@biomejs/biome` `2.5.5` and a `web-react/biome.json`:
  - Formatter tuned to the existing de-facto style to minimize the mechanical
    diff: 2-space indent, double quotes, semicolons, trailing commas (es5-ish —
    match what the codebase already does; confirm by dry-run diff before
    committing).
  - Linter: Biome recommended set, replacing `@eslint/js` and
    `typescript-eslint` rule coverage. Rule-level opt-outs only where Biome
    recommended conflicts with established house patterns (each opt-out
    justified in a comment).
  - `files.includes` scoped to `src/` + `tests/` + config files; ignore
    `dist/`, `node_modules/`.
- One **mechanical format-the-world commit**: `biome format --write` (or `biome
  check --write` for safe lint fixes) across the app — no hand edits mixed in.
- eslint flat config shrinks to: `eslint-plugin-react-hooks` (compiler rules,
  current config carried over verbatim) + `eslint-plugin-react-refresh` +
  `@typescript-eslint/parser` for TSX. Drop `@eslint/js`, `typescript-eslint`
  rule sets, `globals` if no longer referenced.
- Scripts: `"lint": "biome check . && eslint ."`, new `"format": "biome format
  --write ."`. CI/gate semantics: `biome check` fails on format drift AND lint.

### Stage 3 — vite → rsbuild

- `@rsbuild/core` `2.1.7` + `@rsbuild/plugin-react`. React Compiler: prefer the
  plugin-react native option if 2.x has one at implementation time; otherwise
  `@rsbuild/plugin-babel` + `babel-plugin-react-compiler` (the documented
  route). **Verification that the compiler is actually running is required**
  (see Verification).
- `rsbuild.config.ts` ports from `vite.config.ts`:
  - `server.proxy`: `/socket.io` (ws: true, changeOrigin) + `/api`
    (changeOrigin) → `process.env.PUBLIC_PIFIRE_URL || "http://localhost:5000"`
    (same single env var serves the client base URL and the dev proxy target,
    as `VITE_PIFIRE_URL` does today).
  - `html.template: "./index.html"` — keeps the Barlow font links and root div.
    Strip vite-specific bits from index.html (the `/src/main.tsx` module script
    becomes rsbuild's injected entry; entry configured as `src/main.tsx`).
- **Env-var rename** (rsbuild injects only `PUBLIC_*` into client code):
  - `VITE_PIFIRE_URL` → `PUBLIC_PIFIRE_URL`; `VITE_DEMO` → `PUBLIC_DEMO`.
  - Touchpoints: every `import.meta.env.VITE_*` read in `src/` (command.ts,
    UnitsTab, useDashData, settings hooks — grep is authoritative), the `demo`
    script (`PUBLIC_DEMO=1 rsbuild dev`), and any e2e/docs references.
- Scripts: `dev`/`demo` → `rsbuild dev`, `build` → `<typecheck> && rsbuild
  build`, `preview` → `rsbuild preview`.
- Remove vite, `@vitejs/plugin-react`, `@rolldown/plugin-babel` (unless the
  babel route reuses it — it does not; rsbuild has its own babel plugin).
  vitest remains installed and running until Stage 4.
  - Note: vitest 4 runs standalone in the interim (it vendors its own vite);
    `vite.config.ts`'s `test` block moves to a temporary `vitest.config.ts` in
    this stage so `vite.config.ts` can be deleted cleanly.

### Stage 4 — vitest → rstest

- `@rstest/core` `0.11.3` replaces `vitest` + `vitest.config.ts` → `rstest.config.ts`.
- **Environment split moves from per-file docblocks to glob config**: `*.test.tsx`
  → jsdom, `*.test.ts` → node. Delete every `// @vitest-environment jsdom`
  docblock (the tsx/ts naming convention already encodes the split exactly).
- Mechanical codemod across the 29 test files: `import { ... } from "vitest"`
  → the `@rstest/core` equivalents, `vi.*` mock API → rstest's mock API
  (exact names confirmed against rstest docs at implementation; the codemod is
  find/replace-able or a small script, not hand-editing).
- `src/test-setup.ts`: jest-dom matcher registration rewired from
  `@testing-library/jest-dom/vitest` to the rstest-compatible entry (generic
  `@testing-library/jest-dom` + expect.extend if no dedicated entry exists);
  global `afterEach(cleanup)` preserved.
- `exclude: tests/e2e/**` preserved so rstest never imports Playwright specs.
- Scripts: `"test": "rstest run"` (watch mode via `rstest`).
- **Fallback:** if rstest 0.11.x cannot express something load-bearing (the
  env split, setupFiles, jsdom fidelity, mock semantics), STOP and report
  rather than contorting the suite — Stage 4 reverts cleanly to vitest
  standalone (Stages 1–3 do not depend on it), and rstest waits for maturity.

## Verification (every stage unless noted)

`cd web-react && <typecheck> && bun run lint && bun run test && bun run build`
all green — 112 tests / 29 files, counts unchanged. Additionally:

- **Stage 2:** the format commit contains zero non-whitespace/quote/comma
  semantic changes (spot-check with `git diff -w --stat`); `biome check` clean.
- **Stage 3:** React Compiler verified ACTIVE in the rsbuild build — check the
  build output for compiler-memoized components (e.g. `_c` cache arrays /
  `react/compiler-runtime` import in the bundle), not just "the plugin is in
  the config". Dev-server proxy verified against the live backend (dashboard
  socket frames + a settings save round-trip). Demo mode (`bun run demo`)
  renders. Full Playwright e2e suite (roundtrip + settings specs) passes
  against the live backend — restart gunicorn first (no `--reload`).
- **Stage 4:** full 112-test suite green under rstest with per-file env
  correctness proven (a node test that would fail under jsdom and vice versa
  already exist in the suite — their passing is the proof). Console pristine.
  Playwright e2e re-run (runner untouched, but scripts/env changed in 3–4).
- **Final:** `bun.lock` committed; no `vite`/`vitest` remain in dependencies;
  `bun install --frozen-lockfile` from clean succeeds.

## Risk register

| Risk | Stage | Mitigation / fallback |
|---|---|---|
| `typescript-eslint` parser incompatible with TS7 | 1 | Pin to TS7-supporting release; overrides entry; worst case hold eslint's TS at 5.9 semantics via parser pin while `tsc` is 7 (parser only parses, doesn't typecheck) |
| `tsc -b` unsupported by TS7 binary | 1 | `tsc --noEmit` (single config tree) |
| Biome format fights established style | 2 | Tune biome.json to the de-facto style via dry-run diff before the format commit |
| React Compiler silently inactive under rsbuild | 3 | Explicit bundle-output verification is a gate; babel-plugin route is the documented fallback |
| rstest pre-1.0 gaps (env split, mocks, jsdom) | 4 | Hard fallback: stay on standalone vitest, file the gap, retry at 1.0 |
| Hidden `VITE_*` references post-rename | 3 | `grep -r "VITE_" web-react/` must return zero hits (excluding lockfile/docs history) as a step |

## Out of scope / follow-ups (unchanged from 2b-1 final review)

setTimeout→waitFor in tab tests; align `read*` fallback defaults with
`common/defaults.py`; aria-describedby on gated-toggle hint; float-vs-int
coercion audit. These are 2b-2 concerns and must NOT ride along in this
migration's diffs.
