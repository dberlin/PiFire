# Tailwind v4 Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put `web-react/`'s seven hand-written stylesheets onto Tailwind CSS v4 through the Rsbuild integration — Tailwind owning the design tokens via `@theme`, the existing semantic `pf-*` class names surviving and re-authored with `@apply` — **without changing how any page looks**, proven by a mechanical before/after gate rather than by inspection.

**Architecture:** The "token bridge", ratified in `docs/superpowers/specs/2026-07-25-tailwind-v4-migration-design.md`. JSX is not touched. `src/theme.css` becomes the one place Tailwind is imported and the one place tokens are declared (`@theme static`); the six component stylesheets gain a `@reference "../../theme.css";` line and convert their declarations to `@apply` utilities rule by rule, smallest file first. Everything is held in place by a generalised fidelity harness: for every page, at two viewports, a committed JSON baseline of each landmark's geometry **and** its computed visual style, captured from the pre-Tailwind tree and asserted after every single step.

**Tech Stack:** React 19, TypeScript (typecheck via the `typescript7` alias), Rsbuild 2.1.8 + Rspack (Lightning CSS, **no PostCSS**), rstest 0.11.4, Playwright 1.62, Biome 2.5.5, ESLint 10, Tailwind CSS 4.3.x via `@rsbuild/plugin-tailwindcss` 2.0.3. Package manager: **bun**.

---

## Global Constraints

Copied verbatim from the ratified design and from the verified toolchain facts. Every task's requirements implicitly include this section.

- **Package manager is bun, never bare npm.** `bun install`, `bun run <script>`, `bun add -d <pkg>`. Commit `bun.lock`.
- **Gates every task must pass, from `web-react/`:** `bun run typecheck`, `bun run lint`, `bun run test`, `bun run gen:types:check`. Never `bun test` (that is bun's own runner, not rstest).
- **`bun run test` is rstest**, not vitest. It only globs `src/**/*.test.ts`, `src/**/*.test.tsx` and `*.test.ts` at the package root. A test placed anywhere else is silently never run.
- **`typescript` is pinned to `^5.9.3` for the `@typescript-eslint/parser` only.** Typechecking runs against the `typescript7` alias (`node node_modules/typescript7/bin/tsc -b`). **Do not propose bumping `typescript`.**
- **There is no PostCSS decision to make.** Rsbuild transforms CSS with Lightning CSS by default through Rspack's built-in `lightningcss-loader`, and registers `postcss-loader` only when the project has a `postcss.config.*` or sets `tools.postcss`. This repo has neither. Tailwind v4 also uses Lightning CSS internally. See <https://rsbuild.rs/guide/styling/tailwindcss>. **Do not add a PostCSS config, `@tailwindcss/postcss`, autoprefixer, postcss-preset-env, postcss-nesting, or cssnano.** Minification comes from `LightningCssMinimizerRspackPlugin`.
- **Fidelity gate:** visually identical before/after on **every** page — dashboard, all 11 settings tabs, all 6 wizard steps, history, pellets, shell chrome — at **1280×720 AND 390×844**, except where the "before" is clearly broken.
- **A baseline update that is not accompanied by a stated, reviewed reason is a defect, not a result.** Any intended difference must be listed explicitly (Task 15's table), with a before/after artifact, and the corresponding baseline entry updated in the same commit as the change that causes it.
- **It is deliberately NOT a `toHaveScreenshot()` gate and must not be "upgraded" into one.** `index.html` loads Barlow from `fonts.googleapis.com`, so pixels depend on the network and the host font stack; masking the volatile regions would mask exactly the typography the gate exists to protect. Committed PNGs are human artifacts, never assertions.
- **Token values are shared with the Qt UI** (`display/qml/Theme.qml`). Moving them into `@theme` must preserve them exactly. A drift here changes the Qt dashboard's appearance by implication.
- **Out of scope:** converting `pf-*` rules to inline utilities in JSX; changing any token value, spacing scale or type scale; light theming (the app is dark-only by design); the Flask/Jinja UI's stylesheets.
- **Repo is jj-colocated with git** on branch `massive-reworks-and-new-ui`. `jj commit` sweeps the **entire** working copy, so every task must run in an isolated jj workspace (see Parallelization). Commit messages containing backticks must be passed through a quoted heredoc, never a double-quoted `-m` string — zsh eats them.
- **Run `ruff format` on any changed Python file before committing.** This plan changes no Python, but the standing repo rule applies if that changes.

---

## Corrections to the design spec

The spec's numbers are stale: the wizard-styling, dashboard-reflow and pellets-page slices all merged after it was written. Everything below was re-measured on the live tree at `2e3ff9ae`. **Where the spec and the live code disagree, this plan follows the live code and says so.**

### Stylesheet inventory — re-measured with `wc -l`

| File | Spec says | **Live (`wc -l`)** |
|---|---:|---:|
| `web-react/src/components/dashboard/dashboard.css` | 1,149 | **1,188** |
| `web-react/src/components/wizard/wizard.css` | 624 | **628** |
| `web-react/src/components/settings/settings.css` | 344 | **344** |
| `web-react/src/components/shell/shell.css` | 315 | **315** |
| `web-react/src/theme.css` | 110 | **158** (was 110 before the 2026-07-26 palette reconciliation) |
| `web-react/src/components/history/historyChart.css` | 61 | **61** |
| `web-react/src/components/pellets/pellets.css` | *(not in the spec)* | **137** |
| **Total** | **2,603 over 6 files** | **2,831 over 7 files** |

Re-measure before starting; if these have moved again, use your own numbers and say so.

```bash
cd /home/dannyb/sources/PiFire/web-react && find src -name '*.css' | sort | xargs wc -l
```

### Other places the spec contradicts the tree

1. **`pellets.css` (137 lines) and the whole `/pellets` page do not appear in the spec at all.** They landed in commits `170322ab`…`29dc08e3`, after it was written. `/pellets` is in scope for the fidelity gate and gets its own conversion task (Task 12) and its own capture treatment (Task 5) — it is the one surface whose data arrives over the socket rather than REST, so it cannot be stubbed the way every other page can.
2. **"71 `--pf-*` custom properties of its own" (dashboard.css) is wrong.** Live: **15 distinct** `--pf-*` names, in **30** declaration lines (15 desktop defaults plus their redeclarations inside the two breakpoints). `dashboardStyles.test.tsx` pins all 15 names, their exact desktop values, and asserts `outside.size` is exactly 15 — so this number is load-bearing and must not move.
3. **"settings-`<tab>` … 12 tabs" is wrong.** `SettingsShell.tsx`'s `SETTINGS_TABS` has **11**: general, work-mode, controller, pwm, startup, safety, pellets, history, notifications, units, platform. (The `pwm` pill is hidden on an AC-fan build but the route stays registered, so the baseline still covers it.)
4. **"wizard-`<step>` … 7 steps" is wrong.** `WizardShell.tsx`'s `STEPS` has **6**: welcome, grillplatform, probes, display, distance, finish.
5. **"Implementation is blocked until the wizard-styling and dashboard-reflow slices merge" no longer holds.** Both merged (`a4821358`…`60d6597d`), and a third slice (the pellets page) merged on top. The tree is unblocked; the baseline captured by Task 4 is the reference the spec's step 2 asks for.
6. **The palette was reconciled with `display/qml/Theme.qml` on 2026-07-26, and Theme.qml is the source of truth.** This item previously said `--text-dim: #a89a86` had drifted from Qt's `dim: "#8a7f70"`, forbade fixing it, and asked Task 8 for a test that RECORDED the divergence. **That is overruled and no longer describes the tree.** A separate change (commits `refactor(web-react): route Qt-matching colour literals through theme tokens`, `fix(web-react): reconcile the palette with display/qml/Theme.qml`, `test(web-react): guard the palette against drifting from Theme.qml`) landed BEFORE this migration starts and:
    - corrected `--text-dim` to `#8a7f70`, `--glow` to Qt's opaque `glowColor`, and `--accent-1`/`--accent-2` to Qt's `arcStop2`/`arcStop0`, and added `--accent-mid` (Qt's `arcStop1`);
    - added the rest of the Qt palette as tokens: `--card-border`, `--label`, `--probe-label`, `--setpoint`, `--ok`, `--warn`, `--danger`, `--track`, `--cooking`, `--igniter`, `--icon-idle`, `--dot-idle`, `--row-label`, and the fixed `--accent-ember`/`--accent-ice`/`--accent-crimson` constants;
    - created `web-react/src/themeTokens.test.ts`, which PARSES `Theme.qml` and fails on any divergence, plus on any raw literal in `src/` that equals a Qt token value.

    Consequences for this plan: **`theme.css` is bigger than the 31 lines Task 8 assumes** (re-measure before editing it), **`themeTokens.test.ts` already exists** so Task 8 EXTENDS it rather than creating it, and **Task 4's baseline must be captured from a tree that already contains these three commits** — capturing before them pins colours the human has already ruled wrong. The values quoted throughout Task 8 have been updated in place; re-read them, do not trust memory of this file.
7. **"~257 class sites" is low.** Live: **650** `pf-*` token occurrences across `.tsx` files, and **238 distinct** `pf-*` classes declared across the seven stylesheets. This does not change the approach (JSX is untouched either way) but it does change how badly a full utility rewrite would have gone.
8. **"A test asserts that every `pf-*` class used in JSX is declared in CSS" overstates what exists.** That guard lives only in `src/components/wizard/wizardStyles.test.ts` and only walks `src/components/wizard/`. There is **no repo-wide guard**. Task 6 builds one.
9. **The guard as written cannot survive `@apply` — measured.** `declaredClasses()` counts a rule only when its body `.includes(":")`. An `@apply`-only body (`.pf-card { @apply bg-card rounded-card; }`) contains no colon, so **every rule converted in Tasks 9–14 would silently stop counting as declared** and the guard would go green while proving nothing. Task 6 fixes this before the first conversion, with a mutation test.
10. **"Confirm early that Biome's CSS parser accepts `@theme`, `@apply`" — it does not, by default.** Measured on Biome 2.5.5:

    ```
    × Tailwind-specific syntax is disabled.
    i Enable `tailwindDirectives` in the css parser options, or remove this if you are not using Tailwind CSS.
    ```

    The fix is one config key, verified working with `@theme`, `@apply`, `@reference`, `@source`, `@utility` and `@custom-variant`, producing zero lint findings and zero formatting changes: `"css": { "parser": { "tailwindDirectives": true } }` in `biome.jsonc`. Task 7 lands it. No scoped override and no lint-arrangement change is needed.
11. **"Pages that cannot run under demo mode … need a seeded `PIFIRE_DB_PATH` fixture" describes a means, not a decision, and there is a cheaper one.** `PUBLIC_DEMO=1` only affects `helpers/useLiveState.ts`; every settings/wizard/history loader fetches over REST on **both** servers. So `page.route()` with committed fixtures makes those pages deterministic on any machine **and** stops the capture mutating the shared backend (stepping through the wizard normally flushes a draft that `wizard-layout.spec.ts` has to clean up). Task 3 does that. `PIFIRE_DB_PATH` seeding is not needed. The one page it cannot help is `/pellets`, which is socket-fed — Task 5 gives that a fingerprint gate instead.
12. **The spec's gate under-specifies what "visually identical" means.** `layoutBaseline.ts` records only `{x, y, w, h, fontSize, fontWeight}`. That cannot see colour, border, radius, shadow, padding, gap or flex direction — precisely the declarations `@apply` rewrites. A wrong `@apply bg-card` passes it silently. Task 2 therefore adds a **computed-style baseline** alongside the geometry one. This is an addition to the spec's mechanism, not a replacement: `BOX_TOL`/`EXACT` are kept exactly as they are.

---

## The gate, and why the first task is not a Tailwind task

**Tasks 1–6 add no Tailwind dependency at all.** They pin the current appearance. **Precondition:** the palette reconciliation described in Corrections item 6 must already be in the tree. It is a deliberate, authorised visual change, and a baseline captured before it would pin colours that have been ruled wrong. Task 4 captures the reference baseline from the pre-migration tree; **once Tailwind is installed in Task 7 the reference can never be captured again**, because there would be nothing left to capture it from. Capturing after a Tailwind change, or re-capturing to make a red gate green, makes the whole exercise worthless — it rewrites the gate into agreement with whatever it was supposed to be catching. Capture is therefore behind an explicit `PF_CAPTURE=1` flag on a separate `bun run baseline:capture` script and is never automatic.

**How the existing harness works, and what is kept.** `tests/e2e/layoutBaseline.ts` measures every `[data-pf]` element on the dashboard, converts screen pixels back into the authored 1280-wide coordinate space by dividing by the live stage scale, and records `{x, y, w, h, fontSize, fontWeight}`. `compareToBaseline` allows `BOX_TOL = 2`px on x/y/w/h — "2px at 1280 wide is 0.16% of the layout, below the threshold at which flipping two screenshots shows movement" — except for the seven entries in the `EXACT` table (`stage`, `header`, `probeCol`, `rightCol`, `controls`, `cookRow`, `pills`), which are literals in the source and get `EXACT_TOL = 0.5`. `fontSize` and `fontWeight` must match exactly. All of that is **kept verbatim**; Task 2 generalises around it rather than replacing it.

---

## File Structure

### Created

| Path | Responsibility |
|---|---|
| `web-react/tests/e2e/pageSpecs.ts` | The page catalogue: one `PageSpec` per surface (route, ready selector, measurement root, landmark selectors, wizard step clicks, per-page `EXACT` table). The single place a new page is added to the gate. |
| `web-react/tests/e2e/apiFixtures.ts` | `stubApi(page)` — installs `page.route()` handlers that serve every REST read from a committed fixture and acknowledge every write without forwarding it. |
| `web-react/tests/e2e/fixtures/settings.json` | Captured `GET /api/settings` body. |
| `web-react/tests/e2e/fixtures/controller-metadata.json` | Captured `GET /api/controller_metadata` body. |
| `web-react/tests/e2e/fixtures/mode.json` | Captured `GET /api/get/mode` body. |
| `web-react/tests/e2e/fixtures/wizard-state.json` | Captured `GET /api/wizard/state` body. |
| `web-react/tests/e2e/fixtures/history-chart.json` | Captured `GET /api/history/chart?minutes=30` body. |
| `web-react/tests/e2e/pages-fidelity.spec.ts` | The gate for every REST-fed surface: dashboard, shell, 11 settings tabs, 6 wizard steps, history — both viewports, demo server. |
| `web-react/tests/e2e/chrome-fidelity.spec.ts` | Synthetic computed-style probes for chrome that never renders under a fixed fixture: the three banner kinds, the timer bar, the wizard modals. |
| `web-react/tests/e2e/pellets-fidelity.spec.ts` | The `/pellets` gate. App server, live backend, content-fingerprint guard. |
| `web-react/tests/e2e/baselines/*.json` | ~42 committed baseline files, `<page>-<W>x<H>.json`. The reference. |
| `web-react/tsconfig.e2e.json` | Typechecks `tests/e2e/`, `ports.ts` and `playwright.config.ts`, which the app `tsconfig.json` (`include: ["src"]`) does not cover. |
| `web-react/src/helpers/cssCoverage.ts` | `classesUsedIn(dir)`, `declaredClasses(css)`, `allStylesheets()` — the class-coverage guard's engine, understanding `@apply`-only rule bodies. |
| `web-react/src/helpers/cssCoverage.test.ts` | Unit tests for the engine, including the mutation tests that guard the guard. |
| `web-react/src/styleCoverage.test.ts` | Repo-wide guard: every `pf-*` class used in any `.tsx` has a non-empty CSS rule somewhere. |
| `web-react/src/themeTokens.test.ts` | Pins every token value in `theme.css`, and pins its relationship to `display/qml/Theme.qml` including the one known drift. |

### Modified

| Path | Change |
|---|---|
| `web-react/package.json` | `browserslist` key (Task 1); `tailwindcss` + `@rsbuild/plugin-tailwindcss` devDeps (Task 7); `baseline:capture`, `test:e2e:fidelity`, `typecheck:e2e` scripts. |
| `web-react/bun.lock` | Regenerated by `bun add -d`. Commit it. |
| `web-react/tests/e2e/layoutBaseline.ts` | Generalised: `measureSelectors`, `measureProbes`, `STYLE_PROPS`, `PageSpec`, `ExactTable`, `baselinePath`, `requireBaseline`, `CAPTURING`. `measureLandmarks`/`measureStageScale` and the `BOX_TOL`/`EXACT` semantics are untouched. |
| `web-react/tests/e2e/dashboard-fidelity.spec.ts` | Recording-run auto-write replaced by the explicit `PF_CAPTURE` gate. No change to what it measures. |
| `web-react/playwright.config.ts` | Three new projects; `app`'s `testIgnore` widened. |
| `web-react/biome.jsonc` | `css.parser.tailwindDirectives: true`. |
| `web-react/rsbuild.config.ts` | `pluginTailwindcss()` added to `plugins`. |
| `web-react/src/theme.css` | Tailwind import + `@theme static` tokens + legacy-name aliases + accent overrides. |
| `web-react/src/components/history/historyChart.css` | `@reference` + `@apply` conversion. |
| `web-react/src/components/shell/shell.css` | `@reference` + `@apply` conversion. |
| `web-react/src/components/settings/settings.css` | `@reference` + `@apply` conversion. Also styles `/history`. |
| `web-react/src/components/pellets/pellets.css` | `@reference` + `@apply` conversion. |
| `web-react/src/components/wizard/wizard.css` | `@reference` + `@apply` conversion. |
| `web-react/src/components/dashboard/dashboard.css` | `@reference` + `@apply` conversion. |
| `web-react/src/components/wizard/wizardStyles.test.ts` | Re-pointed at `src/helpers/cssCoverage.ts`; its own assertions unchanged. |
| `docs/superpowers/audits/2026-07-26-tailwind-migration-diffs.md` | Created in Task 15 only if any intended difference is accepted. |

---

## Parallelization

**Isolated jj workspaces per concurrent task. Disjoint file lists are necessary but not sufficient** — two agents in the same checkout race regardless of which files they name, and in this repo `jj commit` sweeps the entire working copy, so a second agent's half-finished edit lands inside the first agent's commit. Create workspaces with `jj workspace add ../pifire-tw-<n>`; each new workspace also needs `cp /home/dannyb/sources/PiFire/.lsp.json ../pifire-tw-<n>/` (gitignored, and its absence is the usual cause of "LSP unavailable") and `cd ../pifire-tw-<n>/web-react && bun install`.

- **Wave 0 — Task 1 alone.** It pins `browserslist`, which changes Lightning CSS's output for every stylesheet. Nothing may be measured before it lands.
- **Wave 1 — Task 2 ∥ Task 6.** Task 2 is `tests/e2e/layoutBaseline.ts`; Task 6 is `src/helpers/cssCoverage.*` + `src/styleCoverage.test.ts` + `wizardStyles.test.ts`. No shared file, no shared gate.
- **Wave 2 — Task 3 alone** (needs Task 2's `PageSpec` type).
- **Wave 3 — Task 4 alone.** It captures the reference and must see a settled tree. **Do not run anything else concurrently with the capture**: a second agent's dev server on the same port, or a stray edit to any stylesheet, poisons the reference silently.
- **Wave 4 — Task 5 alone** (adds specs beside Task 4's, and needs a live backend).
- **Wave 5 — Task 7 alone**, then **Task 8 alone.** Both touch `theme.css` and both re-assert every baseline.
- **Wave 6 — Tasks 9, 10, 11, 12, 13, 14 are file-disjoint and can run concurrently in six workspaces**, with two caveats. (a) Task 11 (`settings.css`) also restyles `/history`, and Task 9 (`historyChart.css`) covers the chart inside that page — run 9 before 11, or accept that whichever lands second re-runs the `history-*` baselines. (b) Task 14 (`dashboard.css`) owns the shared `.pf-btn` rule that the wizard and settings surfaces both consume; if Task 14 runs concurrently with 11 or 13, the merged tree must re-run the **full** fidelity suite before Task 15, not just the touched pages. In practice the safest ordering is the spec's: 9 → 10 → 11 → 12 → 13 → 14, sequential, six small commits.
- **Wave 7 — Task 15 alone.** A human. Nothing runs beside it, and nothing merges before it signs off.

**Chromium caveat:** subagent worktrees without a Chromium install SKIP Playwright specs. Every task from 4 onward is meaningless without Chromium, and Task 5 additionally needs `control.py` + gunicorn running. Re-run the touched e2e specs in the main checkout before merging.

---

## Task 1: Pin `browserslist` before anything is measured

**Files:**
- Modify: `web-react/package.json`

**Interfaces:**
- Consumes: nothing.
- Produces: a fixed Lightning CSS target set, so every baseline captured from Task 4 onward is reproducible. No new symbols.

**Why this is first.** The repo pins no browserslist — no `.browserslistrc`, no `browserslist` key — so Lightning CSS is working from Rsbuild's default target list, which is much older than Tailwind v4 requires (v4 needs Safari 16.4+, Chrome 111+, Firefox 128+, and cascade layers in particular). Two consequences: an unpinned list means the emitted CSS can change under you when a dependency updates, which the fidelity gate would then report as a regression with no cause in the diff; and raising the targets **changes Lightning CSS's output today**, so it has to happen before the reference is captured, not after. The existing `dashboard-layout-1280x720.json` baseline is the proof that it changed nothing.

The targets below are not a guess about what users have — the app **already** requires them. `settings.css`, `shell.css` and `wizard.css` use `color-mix(in srgb, var(--accent) …)` in ten places. Lightning CSS can only precompute `color-mix()` when both operands are literal; with a `var()` operand it passes the function straight through, so the shipped CSS already demands a browser with native `color-mix` — Chrome 111, Safari 16.2.

- [ ] **Step 1: Record the current CSS output as the thing that must not change**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run build
ls -l dist/static/css/
```

Expected: one file, roughly `37.2 kB` raw / `7.8 kB` gzip (the build banner prints both). Write the exact byte count down; Step 4 compares against it.

- [ ] **Step 2: Add the `browserslist` key**

In `web-react/package.json`, after the `"description"` field, add:

```json
  "browserslist": [
    "chrome >= 111",
    "edge >= 111",
    "firefox >= 128",
    "safari >= 16.4"
  ],
```

Then add this comment to `rsbuild.config.ts`, immediately above `export default defineConfig({`, because a bare version list in `package.json` tells the next reader nothing about why:

```ts
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
```

- [ ] **Step 3: Rebuild and diff the emitted CSS**

```bash
cd /home/dannyb/sources/PiFire/web-react
rm -rf dist && bun run build
ls -l dist/static/css/
```

Expected: the size is within a few hundred bytes of Step 1. A large drop means Lightning CSS stopped emitting vendor prefixes it had been emitting — that is fine and expected; a large *rise* is not, and must be understood before continuing.

- [ ] **Step 4: Prove nothing moved, using the gate that already exists**

The dashboard already has a committed landmark baseline. It is the only "before" available at this point in the plan, and it is enough to catch a targets change that altered layout.

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test:e2e --project=fidelity --project=reflow --project=panel
```

Expected: all specs pass, and `dashboard-layout-1280x720.json` is unchanged (`git diff --stat tests/e2e/` prints nothing).

- [ ] **Step 5: Run the four gates**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run typecheck && bun run lint && bun run test && bun run gen:types:check
```

Expected: typecheck silent; `Checked 265 files … No fixes applied.` plus the two pre-existing `react-refresh/only-export-components` warnings (App.tsx:49, WizardShell.tsx:14) and exit 0; rstest `115` test files / `1010` tests, 0 failed; `settingsTypes.gen.ts is up to date.`

- [ ] **Step 6: Commit**

```bash
cd /home/dannyb/sources/PiFire
jj commit -m 'build(web-react): pin browserslist to the targets the CSS already requires'
```

---

## Task 2: Generalise the fidelity harness

**Files:**
- Modify: `web-react/tests/e2e/layoutBaseline.ts`
- Modify: `web-react/tests/e2e/dashboard-fidelity.spec.ts:54-61`
- Create: `web-react/tsconfig.e2e.json`
- Modify: `web-react/package.json` (one script)

**Interfaces:**
- Consumes: `Landmark`, `LandmarkMap`, `measureLandmarks`, `measureStageScale`, `readBaseline`, `writeBaseline`, `compareToBaseline` (all already exported from `layoutBaseline.ts`).
- Produces, all from `web-react/tests/e2e/layoutBaseline.ts`:
  - `export interface PageSpec { name: string; path: string; ready: string; root: string; landmarks: string[]; clicks?: string[]; exact?: ExactTable }`
  - `export type ExactTable = Record<string, Partial<Record<"w" | "h", number>>>`
  - `export const DASHBOARD_EXACT: ExactTable` (the existing table, renamed from the private `EXACT`)
  - `export const BOX_TOL: number` (2), `export const EXACT_TOL: number` (0.5) — values unchanged
  - `export const STYLE_PROPS: readonly string[]`
  - `export async function measureSelectors(page: Page, spec: PageSpec): Promise<LandmarkMap>`
  - `export async function measureProbes(page: Page, host: string, probes: StyleProbe[]): Promise<StyleMap>`
  - `export interface StyleProbe { name: string; className: string }`, `export type StyleMap = Record<string, Record<string, string>>`
  - `export function compareStyles(actual: StyleMap, baseline: StyleMap): string[]`
  - `export function compareToBaseline(actual: LandmarkMap, baseline: LandmarkMap, exact?: ExactTable): string[]` (third parameter is new, defaults to `DASHBOARD_EXACT`, so existing call sites are unchanged)
  - `export function baselinePath(name: string, viewport: { width: number; height: number }): string`
  - `export function requireBaseline(path: string): LandmarkMap`
  - `export const CAPTURING: boolean`
  - `export async function waitForBarlow(page: Page): Promise<void>` (was private)

**Design notes the implementer needs.**

1. `measureLandmarks` is **not** touched. It measures `[data-pf]` elements, which only the dashboard has (six components, 18 attributes; nothing else in `src/` carries one), and it divides by the stage scale to convert back into authored coordinates. Its committed baseline stays byte-valid.
2. `measureSelectors` is the new, selector-driven path for every other surface. No scale normalisation: nothing outside the dashboard has a `transform: scale()`.
3. **Naming rule:** when a selector matches exactly one element the baseline key is the selector itself; when it matches several the keys are `sel#0`, `sel#1`, …. That means a list growing from one item to two reports the old key `MISSING` and two keys `NEW` — loud and correct, which is what you want from a gate.
4. **`root` and `landmarks` run inside `page.evaluate` and must be plain CSS.** `ready` is evaluated by Playwright and may use its selector engines (`h2:text-is("Probes")`). Getting this backwards throws `Failed to execute 'querySelector'` at capture time.
5. **`styles` is the point of this task.** Geometry alone cannot see a wrong colour, a lost border, a changed radius or a dropped shadow — exactly what `@apply` rewrites. Style comparison is exact, no tolerance: both sides run in the same Chromium.

- [ ] **Step 1: Replace the private `FACES` list with a superset covering every face the app loads**

In `web-react/tests/e2e/layoutBaseline.ts`, replace the `FACES` constant (lines 27-35) with:

```ts
// Every (weight, family) pair index.html's <link> actually requests: Barlow
// 400;500;600;700 and Barlow Semi Condensed 500;600;700;800.
//
// The size in a font shorthand does not select a face -- family, weight and
// style do -- so a single nominal 16px covers every size the app renders at,
// and loading a superset of any one page's faces is harmless: it can make the
// wait longer, never the measurement different. The previous list was the
// dashboard's six exact pairs, which would have under-waited on the settings
// and wizard surfaces this harness now also measures.
const FACES = [
  "400 16px Barlow",
  "500 16px Barlow",
  "600 16px Barlow",
  "700 16px Barlow",
  "500 16px 'Barlow Semi Condensed'",
  "600 16px 'Barlow Semi Condensed'",
  "700 16px 'Barlow Semi Condensed'",
  "800 16px 'Barlow Semi Condensed'",
];
```

- [ ] **Step 2: Export `waitForBarlow`**

Change its declaration (line 53) from `async function waitForBarlow` to `export async function waitForBarlow`. Leave the body and its docblock alone — that comment records a real flake (7-25px width drift when the webfont was still in flight) and why `document.fonts.check()` cannot detect it.

- [ ] **Step 3: Add the style-property list and the extended `Landmark` shape**

Extend the `Landmark` interface (lines 16-23) and add the property list immediately after it:

```ts
export interface Landmark {
  x: number;
  y: number;
  w: number;
  h: number;
  fontSize: string;
  fontWeight: string;
  /** Computed visual style. Absent on the legacy [data-pf] dashboard baseline,
   *  which predates it; compareToBaseline skips the check when the BASELINE
   *  omits it, so that file stays valid without regeneration. */
  styles?: Record<string, string>;
}

// What "visually identical" actually means, beyond a box in the right place.
//
// The geometry above is blind to colour, border, radius, shadow, padding, gap
// and flex direction -- which is the entire set of declarations an @apply
// rewrite touches. A `.pf-card { @apply bg-inset }` where the rule said
// var(--card) moves nothing and passes a geometry-only gate in silence. These
// are compared EXACTLY: both sides run in the same Chromium, so there is no
// rounding to absorb.
export const STYLE_PROPS = [
  "color",
  "background-color",
  "background-image",
  "border-top-width",
  "border-top-color",
  "border-top-style",
  "border-top-left-radius",
  "box-shadow",
  "opacity",
  "font-family",
  "line-height",
  "letter-spacing",
  "text-transform",
  "text-align",
  "padding-top",
  "padding-right",
  "padding-bottom",
  "padding-left",
  "margin-top",
  "margin-right",
  "margin-bottom",
  "margin-left",
  "display",
  "flex-direction",
  "flex-wrap",
  "gap",
  "justify-content",
  "align-items",
  "position",
  "z-index",
  "overflow",
  "transform",
] as const;
```

- [ ] **Step 4: Rename `EXACT` to `DASHBOARD_EXACT`, export it and the two tolerances**

Replace lines 106-122 with:

```ts
export type ExactTable = Record<string, Partial<Record<"w" | "h", number>>>;

// Authored constants: literals in the dashboard source, so a deviation here is
// never a rounding artefact. Keyed by data-pf name, which is why it is the
// DASHBOARD table specifically -- a selector-keyed page passes its own via
// PageSpec.exact, and must not inherit these by accident (".pf-nav" has no
// business being held to the stage's 1280px).
export const DASHBOARD_EXACT: ExactTable = {
  stage: { w: 1280, h: 720 },
  header: { h: 58 },
  probeCol: { w: 298 },
  rightCol: { w: 300 },
  controls: { h: 82 },
  cookRow: { h: 52 },
  pills: { h: 64 },
};
export const EXACT_TOL = 0.5;
// 2px at 1280 wide is 0.16% of the layout -- below the threshold at which
// flipping two screenshots shows movement. It absorbs the sub-pixel remainder
// of flex distribution and gap rounding and nothing else. A 3px shift is a
// design change and must be argued for.
export const BOX_TOL = 2;
```

- [ ] **Step 5: Parameterise `compareToBaseline` and teach it about `styles`**

Replace the body of `compareToBaseline` (lines 124-147) with:

```ts
export function compareToBaseline(
  actual: LandmarkMap,
  baseline: LandmarkMap,
  exact: ExactTable = DASHBOARD_EXACT,
): string[] {
  const problems: string[] = [];
  for (const name of Object.keys(baseline)) {
    const a = actual[name];
    const b = baseline[name];
    if (a === undefined) {
      problems.push(`${name}: MISSING from the page`);
      continue;
    }
    for (const k of ["x", "y", "w", "h"] as const) {
      const tol = exact[name]?.[k as "w" | "h"] !== undefined ? EXACT_TOL : BOX_TOL;
      if (Math.abs(a[k] - b[k]) > tol) {
        problems.push(`${name}.${k}: ${b[k]} -> ${a[k]} (tolerance ${tol})`);
      }
    }
    for (const k of ["fontSize", "fontWeight"] as const) {
      if (a[k] !== b[k]) problems.push(`${name}.${k}: ${b[k]} -> ${a[k]} (must be exact)`);
    }
    // Driven off the BASELINE's keys, not STYLE_PROPS: a baseline recorded
    // before a property was added to STYLE_PROPS stays comparable on the
    // properties it does carry, instead of failing on every landmark at once.
    for (const [prop, want] of Object.entries(b.styles ?? {})) {
      const got = a.styles?.[prop];
      if (got !== want) problems.push(`${name} { ${prop}: ${want} } -> ${got} (must be exact)`);
    }
  }
  for (const name of Object.keys(actual)) {
    if (baseline[name] === undefined) problems.push(`${name}: NEW landmark, not in the baseline`);
  }
  return problems;
}
```

- [ ] **Step 6: Add `PageSpec` and `measureSelectors`**

Append to `layoutBaseline.ts`:

```ts
/** One measurable surface. tests/e2e/pageSpecs.ts holds the catalogue. */
export interface PageSpec {
  /** Baseline file stem, e.g. "settings-general". */
  name: string;
  /** Route to open, relative to the project's baseURL. */
  path: string;
  /** Playwright selector -- MAY use the text engines -- that must be visible
   *  before anything is measured. */
  ready: string;
  /** PLAIN CSS. The measurement origin; every landmark is recorded relative to
   *  this box, so a page that scrolls does not shift its whole baseline. */
  root: string;
  /** PLAIN CSS. Each match becomes one baseline entry. */
  landmarks: string[];
  /** Playwright selectors clicked in order, after `ready`, before measuring.
   *  How the wizard reaches step N. */
  clicks?: string[];
  /** Dimensions that must not move at all, keyed by baseline entry name. */
  exact?: ExactTable;
}

export async function measureSelectors(page: Page, spec: PageSpec): Promise<LandmarkMap> {
  await waitForBarlow(page);
  // Belt and braces: .pf-module-image boxes the wizard's vendor photos to a
  // fixed 132px square with object-fit: contain, so intrinsic size cannot move
  // the layout -- but a half-decoded image can still delay paint.
  await page.waitForFunction(() => [...document.images].every((i) => i.complete));
  return (await page.evaluate(
    ({ root, landmarks, props }) => {
      const rootEl = document.querySelector<HTMLElement>(root);
      if (rootEl === null) throw new Error(`no ${root} on the page`);
      const rr = rootEl.getBoundingClientRect();
      const out: Record<string, unknown> = {};
      for (const sel of landmarks) {
        const els = [...document.querySelectorAll<HTMLElement>(sel)];
        els.forEach((el, i) => {
          const r = el.getBoundingClientRect();
          const cs = getComputedStyle(el);
          out[els.length === 1 ? sel : `${sel}#${i}`] = {
            x: Math.round(r.left - rr.left),
            y: Math.round(r.top - rr.top),
            w: Math.round(r.width),
            h: Math.round(r.height),
            fontSize: cs.fontSize,
            fontWeight: cs.fontWeight,
            styles: Object.fromEntries(props.map((p) => [p, cs.getPropertyValue(p)])),
          };
        });
      }
      return out;
    },
    { root: spec.root, landmarks: spec.landmarks, props: [...STYLE_PROPS] },
  )) as LandmarkMap;
}
```

- [ ] **Step 7: Add the synthetic style probe**

Some chrome never renders under a fixed fixture: `Banners` returns `null` when there are no errors or warnings, `TimerBar` renders only while a timer is visible, and the wizard's three `role="dialog"` surfaces need a running grill or a finished installer. `wizard-layout.spec.ts` already probes rules this way — attach a detached element carrying the class, read the computed style, remove it — and this generalises that. It proves the rules resolve and to what; it proves nothing about how they look in situ, which is Task 15's job.

Append:

```ts
export interface StyleProbe {
  /** Baseline key. */
  name: string;
  /** The class list to put on the probe element. */
  className: string;
}
export type StyleMap = Record<string, Record<string, string>>;

export async function measureProbes(
  page: Page,
  host: string,
  probes: StyleProbe[],
): Promise<StyleMap> {
  return (await page.evaluate(
    ({ host, probes, props }) => {
      const parent = document.querySelector(host);
      if (parent === null) throw new Error(`no ${host} to host the probes`);
      const out: Record<string, Record<string, string>> = {};
      for (const p of probes) {
        const el = document.createElement("div");
        el.className = p.className;
        parent.appendChild(el);
        const cs = getComputedStyle(el);
        out[p.name] = Object.fromEntries(props.map((k) => [k, cs.getPropertyValue(k)]));
        el.remove();
      }
      return out;
    },
    { host, probes, props: [...STYLE_PROPS] },
  )) as StyleMap;
}

export function compareStyles(actual: StyleMap, baseline: StyleMap): string[] {
  const problems: string[] = [];
  for (const [name, want] of Object.entries(baseline)) {
    const got = actual[name];
    if (got === undefined) {
      problems.push(`${name}: MISSING probe`);
      continue;
    }
    for (const [prop, v] of Object.entries(want)) {
      if (got[prop] !== v) problems.push(`${name} { ${prop}: ${v} } -> ${got[prop]}`);
    }
  }
  for (const name of Object.keys(actual)) {
    if (baseline[name] === undefined) problems.push(`${name}: NEW probe, not in the baseline`);
  }
  return problems;
}
```

- [ ] **Step 8: Add the explicit capture gate**

Append:

```ts
/** Baselines are captured deliberately or not at all.
 *
 *  A gate that updates itself when it disagrees with the page is not a gate --
 *  it silently rewrites itself into agreement with whatever it was supposed to
 *  be catching. So capture is one env var on one script (`bun run
 *  baseline:capture`), and a missing baseline is a hard failure everywhere
 *  else, never a quiet recording run. */
export const CAPTURING = process.env.PF_CAPTURE === "1";

export function baselinePath(name: string, viewport: { width: number; height: number }): string {
  return `tests/e2e/baselines/${name}-${viewport.width}x${viewport.height}.json`;
}

export function requireBaseline(path: string): LandmarkMap {
  const b = readBaseline(path);
  if (b === null) {
    throw new Error(
      `${path} is missing. Run \`bun run baseline:capture\` against the tree you want ` +
        `to make the reference, review the diff by hand, and commit it. Do not capture ` +
        `to make a red gate green.`,
    );
  }
  return b;
}

export function writeStyleBaseline(path: string, map: StyleMap): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${JSON.stringify(map, null, 2)}\n`);
}

export function readStyleBaseline(path: string): StyleMap | null {
  if (!existsSync(path)) return null;
  return JSON.parse(readFileSync(path, "utf8")) as StyleMap;
}
```

- [ ] **Step 9: Close the dashboard spec's silent recording run**

The existing spec writes a fresh baseline whenever the file is absent. That is the same "update if different" hole one step removed: delete the file, run the suite, and it goes green against itself. Replace `dashboard-fidelity.spec.ts:54-61` with:

```ts
  if (CAPTURING) {
    writeBaseline(BASELINE, actual);
    console.log(`[fidelity] captured ${BASELINE} -- review it by hand before committing`);
    return;
  }
  const baseline = requireBaseline(BASELINE);
```

and update its import block at the top of the file to:

```ts
import {
  CAPTURING,
  compareToBaseline,
  measureLandmarks,
  measureStageScale,
  requireBaseline,
  writeBaseline,
} from "./layoutBaseline";
```

(`readBaseline` is no longer used here; leave it exported, `pellets-fidelity.spec.ts` uses it in Task 5.)

- [ ] **Step 10: Give the harness a typechecker**

`web-react/tsconfig.json` has `"include": ["src"]`, so `tests/e2e/` is typechecked by nothing at all — `bun run typecheck` would not have caught a single error in the ~300 lines above. Create `web-react/tsconfig.e2e.json`:

```json
{
  "extends": "./tsconfig.json",
  "compilerOptions": { "types": ["node"] },
  "include": ["tests/e2e", "ports.ts", "playwright.config.ts"]
}
```

`rstest.config.ts` is deliberately excluded: it has one pre-existing error (`TS2769` — `coverage.all` is not in `@rstest/core`'s `CoverageOptions` type, though it is honoured at runtime) that is not this plan's to fix.

Add to `package.json`'s `scripts`:

```json
    "typecheck:e2e": "node node_modules/typescript7/bin/tsc -p tsconfig.e2e.json --noEmit",
```

- [ ] **Step 11: Run both typecheckers**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run typecheck && bun run typecheck:e2e
```

Expected: both silent, exit 0. (Measured on the pre-change tree, `typecheck:e2e` over the existing specs is already clean, so any error here is yours.)

- [ ] **Step 12: Prove the dashboard gate still behaves identically**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test:e2e --project=fidelity --project=reflow --project=panel
git diff --stat tests/e2e/dashboard-layout-1280x720.json
```

Expected: all specs pass; the `git diff --stat` prints nothing. The committed baseline has no `styles` key and `compareToBaseline` skips the style check when the baseline omits it — that is the compatibility this step is confirming.

- [ ] **Step 13: Prove the capture gate actually gates**

```bash
cd /home/dannyb/sources/PiFire/web-react
mv tests/e2e/dashboard-layout-1280x720.json /tmp/db.json
bun run test:e2e --project=fidelity 2>&1 | grep -c 'Run `bun run baseline:capture`'
mv /tmp/db.json tests/e2e/dashboard-layout-1280x720.json
```

Expected: `1` — the spec fails loudly with the capture instruction instead of writing a fresh file. Confirm the file is restored with `git status --short tests/e2e/`, which must print nothing.

- [ ] **Step 14: Run the four gates and commit**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run typecheck && bun run typecheck:e2e && bun run lint && bun run test && bun run gen:types:check
cd /home/dannyb/sources/PiFire
jj commit -m 'test(web-react): generalise the fidelity harness to any page, and pin computed style'
```

---

## Task 3: API fixtures and the page catalogue

**Files:**
- Create: `web-react/tests/e2e/apiFixtures.ts`
- Create: `web-react/tests/e2e/fixtures/settings.json`
- Create: `web-react/tests/e2e/fixtures/controller-metadata.json`
- Create: `web-react/tests/e2e/fixtures/mode.json`
- Create: `web-react/tests/e2e/fixtures/wizard-state.json`
- Create: `web-react/tests/e2e/fixtures/history-chart.json`
- Create: `web-react/tests/e2e/pageSpecs.ts`

**Interfaces:**
- Consumes: `PageSpec`, `ExactTable` from `./layoutBaseline`.
- Produces:
  - `web-react/tests/e2e/apiFixtures.ts`: `export async function stubApi(page: Page): Promise<void>`
  - `web-react/tests/e2e/pageSpecs.ts`: `export const DESKTOP = { width: 1280, height: 720 }`, `export const PHONE = { width: 390, height: 844 }`, `export const PAGE_SPECS: PageSpec[]`, `export const PELLETS_SPEC: PageSpec`, `export const CHROME_PROBES: StyleProbe[]`

**Why stubbing, and why this is not the spec's `PIFIRE_DB_PATH` fixture.** `PUBLIC_DEMO=1` is read in exactly one place, `helpers/useLiveState.ts:29`, and its only effect is that no socket is opened and `demoDashAt()` supplies the dashboard state. Every settings, wizard and history loader still fetches over REST on the demo server exactly as on the app server. So the demo server alone does not make those pages deterministic; `page.route()` does, and it buys two more things a seeded database does not: the capture stops depending on which machine it runs on, and it stops **mutating** the backend — stepping the wizard forward normally flushes a draft, which `wizard-layout.spec.ts` has to `POST /api/wizard/draft {clear:true}` afterwards to undo.

- [ ] **Step 1: Start the backend and capture the five fixture bodies**

From the repo root, in two terminals (this is the documented prototype launch; `python app.py` trips Werkzeug's production guard):

```bash
cd /home/dannyb/sources/PiFire && uv run python control.py
cd /home/dannyb/sources/PiFire && uv run gunicorn -k gthread --threads 25 -b 0.0.0.0:5000 -w 1 app:app
```

Then:

```bash
cd /home/dannyb/sources/PiFire/web-react
mkdir -p tests/e2e/fixtures
curl -sf http://localhost:5000/api/settings              -o tests/e2e/fixtures/settings.json
curl -sf http://localhost:5000/api/controller_metadata   -o tests/e2e/fixtures/controller-metadata.json
curl -sf http://localhost:5000/api/get/mode              -o tests/e2e/fixtures/mode.json
curl -sf http://localhost:5000/api/wizard/state          -o tests/e2e/fixtures/wizard-state.json
curl -sf 'http://localhost:5000/api/history/chart?minutes=30' -o tests/e2e/fixtures/history-chart.json
for f in tests/e2e/fixtures/*.json; do python3 -m json.tool "$f" > "$f.fmt" && mv "$f.fmt" "$f"; done
wc -c tests/e2e/fixtures/*.json
```

Expected: five non-empty files; `settings.json` is the largest by far (the whole settings tree). If any `curl` exits non-zero the backend is not up — do not hand-write a fixture, the shapes are large and a wrong one produces a plausible-looking but meaningless baseline.

- [ ] **Step 2: Sanity-check the two shapes the loaders unwrap**

```bash
cd /home/dannyb/sources/PiFire/web-react
python3 -c "import json;d=json.load(open('tests/e2e/fixtures/settings.json'));print(sorted(d)[:5])"
python3 -c "import json;d=json.load(open('tests/e2e/fixtures/mode.json'));print(d)"
```

Expected: the first prints a list beginning with the top-level settings keys, and the response either has a `settings` key or is the settings object itself — `settingsApi.ts:18` handles both (`body.settings ?? (body as Settings)`). The second prints something shaped like `{'data': {'mode': 'Stop'}}`.

- [ ] **Step 3: Write the stub installer**

Create `web-react/tests/e2e/apiFixtures.ts`:

```ts
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Page, Route } from "@playwright/test";

const DIR = join("tests", "e2e", "fixtures");
const body = (file: string): string => readFileSync(join(DIR, file), "utf8");

const json = (route: Route, text: string): Promise<void> =>
  route.fulfill({ status: 200, contentType: "application/json", body: text });

/**
 * Serve every REST read the styled pages make from a committed fixture, and
 * acknowledge every write without letting it reach the backend.
 *
 * Two things this buys, both load-bearing for a fidelity baseline:
 *
 *   - The DOM shape stops depending on the machine's pifire.db. A settings tab
 *     renders a different number of probe cards, range rows and device rows on
 *     every install, and a landmark baseline is a list of boxes -- so without
 *     this the reference is only meaningful on the machine that captured it.
 *   - The capture stops MUTATING shared state. Stepping the wizard forward
 *     POSTs a draft (helpers/wizard/wizardApi.ts saveDraft), which
 *     wizard-layout.spec.ts has to clear afterwards. Here it never lands.
 *
 * Demo mode does NOT cover this: PUBLIC_DEMO is read only by
 * helpers/useLiveState.ts and only suppresses the socket. Every loader below
 * fetches over REST on the demo server exactly as on the app server.
 */
export async function stubApi(page: Page): Promise<void> {
  await page.route("**/api/settings", (r) => json(r, body("settings.json")));
  await page.route("**/api/controller_metadata", (r) => json(r, body("controller-metadata.json")));
  await page.route("**/api/get/mode", (r) => json(r, body("mode.json")));
  await page.route("**/api/wizard/state", (r) => json(r, body("wizard-state.json")));
  await page.route("**/api/history/chart*", (r) => json(r, body("history-chart.json")));
  // Writes: acknowledged, never forwarded. `**` globs match the query string
  // too, so /api/wizard/draft and /api/settings_update are covered whatever the
  // caller appends.
  await page.route("**/api/wizard/draft", (r) => json(r, '{"ok":true}'));
  await page.route("**/api/settings_update", (r) => json(r, '{"ok":true}'));
  await page.route("**/api/wizard/module-values", (r) => json(r, '{"values":{}}'));
}
```

- [ ] **Step 4: Write the page catalogue — viewports and shared landmark lists**

Create `web-react/tests/e2e/pageSpecs.ts`:

```ts
import type { PageSpec, StyleProbe } from "./layoutBaseline";

// The two viewports the fidelity gate is defined at. 1280x720 is the desktop
// regression target the dashboard was authored for; 390x844 is the phone the
// reflow slice introduced. (800x480 -- the grill's own screen -- keeps its own
// dedicated `panel` project; this gate does not duplicate it.)
export const DESKTOP = { width: 1280, height: 720 };
export const PHONE = { width: 390, height: 844 };

// The app shell wraps every page except /wizard, so these ride along on each
// spec below rather than getting a page of their own.
const SHELL = [
  ".pf-shell",
  ".pf-nav",
  ".pf-nav-brand",
  ".pf-nav-mark",
  ".pf-nav-grill",
  ".pf-nav-list",
  ".pf-nav-item",
  ".pf-nav-link",
  ".pf-nav-actions",
  ".pf-nav-timer",
  ".pf-shell-main",
];

// Every structural rule settings.css owns. /history reuses .pf-settings,
// .pf-section and .pf-settings-actions, which is why Task 11 has to re-run the
// history baselines too.
const SETTINGS = [
  ".pf-settings",
  ".pf-settings-nav",
  ".pf-settings-back",
  ".pf-settings-title",
  ".pf-settings-link",
  ".pf-settings-content",
  ".pf-settings-actions",
  ".pf-settings-hint",
  ".pf-section",
  ".pf-section-title",
  ".pf-section-body",
  ".pf-field",
  ".pf-field-label",
  ".pf-field-control",
  ".pf-field-hint",
  ".pf-input",
  ".pf-switch",
];

const WIZARD = [
  ".pf-wizard",
  ".pf-wizard-header",
  ".pf-wizard-title",
  ".pf-wizard-steps",
  ".pf-wizard-step-indicator",
  ".pf-wizard-exit",
  ".pf-wizard-content",
  ".pf-wizard-step",
  ".pf-wizard-step-title",
  ".pf-wizard-footer",
  ".pf-btn",
];
```

- [ ] **Step 5: Add the dashboard, shell, history and settings specs**

Append to `pageSpecs.ts`:

```ts
const SETTINGS_TABS: { path: string; label: string }[] = [
  { path: "general", label: "General" },
  { path: "work-mode", label: "Work Mode" },
  { path: "controller", label: "Controller" },
  { path: "pwm", label: "PWM Fan" },
  { path: "startup", label: "Startup / Shutdown" },
  { path: "safety", label: "Safety" },
  { path: "pellets", label: "Pellet Levels" },
  { path: "history", label: "History" },
  { path: "notifications", label: "Notifications" },
  { path: "units", label: "Units" },
  { path: "platform", label: "Platform" },
];

export const PAGE_SPECS: PageSpec[] = [
  // The dashboard, measured by SELECTOR rather than by data-pf. This does not
  // replace dashboard-fidelity.spec.ts -- that one still runs, still
  // scale-normalises, still holds the seven EXACT constants. This one adds the
  // computed-style half, which is the half an @apply rewrite can break.
  {
    name: "dashboard",
    path: "/",
    ready: '[data-pf="stage"]',
    root: ".pf-shell",
    landmarks: [
      ...SHELL,
      ".pf-dash",
      ".pf-dash-header",
      ".pf-dash-brand",
      ".pf-dash-clock",
      ".pf-dash-body",
      ".pf-dash-probecol",
      ".pf-dash-centercol",
      ".pf-dash-rightcol",
      ".pf-dash-card",
      ".pf-dash-gauge",
      ".pf-dash-probecard",
      ".pf-dash-system",
      ".pf-dash-hopper",
      ".pf-dash-cookrow",
      ".pf-dash-pills",
      ".pf-dash-controls",
      ".pf-btn",
    ],
  },
  {
    name: "history",
    path: "/history",
    ready: ".pf-history-chart",
    root: ".pf-shell",
    landmarks: [...SHELL, ".pf-settings", ".pf-section", ".pf-section-title", ".pf-section-body", ".pf-settings-actions", ".pf-history-chart"],
  },
  ...SETTINGS_TABS.map(
    (t): PageSpec => ({
      name: `settings-${t.path}`,
      path: `/settings/${t.path}`,
      ready: ".pf-settings-content .pf-section",
      root: ".pf-shell",
      landmarks: [...SHELL, ...SETTINGS],
    }),
  ),
];
```

- [ ] **Step 6: Add the six wizard specs**

The wizard is a linear flow with no routes of its own — `STEPS` in `WizardShell.tsx` is `["welcome","grillplatform","probes","display","distance","finish"]` and the only way in is the Next button. **Never click Finish: it fires the real installer.** `ready` uses Playwright's text engine (allowed — it is evaluated Playwright-side, unlike `root`/`landmarks`).

Append to `pageSpecs.ts`:

```ts
const NEXT = 'button:text-is("Next")';
const WIZARD_STEPS: { name: string; heading: string }[] = [
  { name: "welcome", heading: "Welcome" },
  { name: "grillplatform", heading: "Grill Platform" },
  { name: "probes", heading: "Probes" },
  { name: "display", heading: "Display" },
  { name: "distance", heading: "Distance / Hopper" },
  { name: "finish", heading: "Finish" },
];

PAGE_SPECS.push(
  ...WIZARD_STEPS.map(
    (s, i): PageSpec => ({
      name: `wizard-${s.name}`,
      path: "/wizard",
      // Reached by clicking Next i times from Welcome. Finish is NEVER clicked:
      // it fires the real installer.
      clicks: Array.from({ length: i }, () => NEXT),
      ready: `h2:text-is("${s.heading}")`,
      root: ".pf-wizard",
      landmarks: [
        ...WIZARD,
        ".pf-module-card",
        ".pf-module-details",
        ".pf-module-image",
        ".pf-probes-card",
        ".pf-btn-primary",
      ],
    }),
  ),
);
```

- [ ] **Step 7: Add the pellets spec and the chrome probes**

Append to `pageSpecs.ts`:

```ts
/** /pellets is the one surface stubApi cannot make deterministic: its data
 *  arrives on the socket_pellet_data channel, not over REST, and the demo
 *  server opens no socket at all -- PelletsPage then renders nothing but its
 *  "Loading pellet database..." branch. So it runs against the APP server and
 *  the live backend, and pellets-fidelity.spec.ts fingerprints the store
 *  before trusting the numbers. See Task 5. */
export const PELLETS_SPEC: PageSpec = {
  name: "pellets",
  path: "/pellets",
  ready: '[role="region"][aria-label="Brands"]',
  root: ".pf-shell",
  landmarks: [
    ...SHELL,
    ".pf-pellets",
    ".pf-pellets-card",
    ".pf-pellets-card-title",
    ".pf-pellets-scroll",
    ".pf-pellets-meter",
    ".pf-pellets-level-text",
    ".pf-pellets-usage",
    ".pf-pellets-actions",
    ".pf-pellets-profile",
  ],
};

/** Chrome that never renders under a fixed fixture, probed synthetically.
 *
 *  Banners returns null with no errors and no warnings; TimerBar renders only
 *  while a timer is visible; the wizard's three role="dialog" surfaces need a
 *  running grill or a finished installer. This proves the rules resolve and to
 *  WHAT -- it proves nothing about how they look in situ, which is the human
 *  checkpoint's job. tests/e2e/wizard-layout.spec.ts already probes two rules
 *  this way; this is the same technique with a committed baseline. */
export const CHROME_PROBES: StyleProbe[] = [
  { name: "banner-error", className: "pf-banner pf-banner--error" },
  { name: "banner-warning", className: "pf-banner pf-banner--warning" },
  { name: "banner-critical", className: "pf-banner pf-banner--critical" },
  { name: "banners", className: "pf-banners" },
  { name: "timer-bar", className: "pf-timer-bar" },
  { name: "timer-readout", className: "pf-timer-readout" },
  { name: "timer-btn", className: "pf-timer-btn" },
  { name: "modal-scrim", className: "pf-modal-scrim-fixed" },
  { name: "wizard-modal", className: "pf-wizard-modal pf-wizard-system-active-modal" },
  { name: "install-bar", className: "pf-install-progress-bar" },
  { name: "pellets-meter-ok", className: "pf-pellets-meter pf-pellets-meter--ok" },
  { name: "pellets-meter-warn", className: "pf-pellets-meter pf-pellets-meter--warn" },
  { name: "pellets-meter-low", className: "pf-pellets-meter pf-pellets-meter--low" },
  // Runtime-constructed names (ProbeCard.tsx:35,40 build `pf-badge-${tone}`).
  // Tailwind's content scanner cannot see these. Harmless while they are
  // hand-written rules; a silently-missing style the moment anyone converts
  // them to utilities in JSX -- which this migration does not do.
  { name: "badge-ok", className: "pf-badge pf-badge-ok" },
  { name: "badge-warn", className: "pf-badge pf-badge-warn" },
];
```

- [ ] **Step 8: Typecheck and lint the new files**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run typecheck:e2e && bun run lint
```

Expected: both clean. Biome's `files.includes` covers `tests/**`, so the new files are formatted and linted; ESLint ignores `tests/e2e` (see `eslint.config.js`'s `ignores`), which is why `typecheck:e2e` from Task 2 matters.

- [ ] **Step 9: Verify the class names in the catalogue actually exist**

A selector list full of typos produces an empty, cheerful baseline. Check every one against the stylesheets:

```bash
cd /home/dannyb/sources/PiFire/web-react
grep -ohE '"\.pf-[a-z0-9-]+"' tests/e2e/pageSpecs.ts | tr -d '".' | sort -u > /tmp/want.txt
grep -rhoE '\.pf-[a-z0-9-]+' src --include='*.css' | tr -d '.' | sort -u > /tmp/have.txt
comm -23 /tmp/want.txt /tmp/have.txt
```

Expected: **no output**. Any line printed is a selector in the catalogue that no stylesheet declares — fix the catalogue, not the stylesheet.

- [ ] **Step 10: Commit**

```bash
cd /home/dannyb/sources/PiFire
jj commit -m 'test(web-react): committed API fixtures and the fidelity page catalogue'
```

---

## Task 4: Capture the reference baseline — THE point of no return

**Before capturing:** confirm `git log`/`jj log` shows the 2026-07-26 palette reconciliation (Corrections item 6) as an ancestor. `web-react/src/theme.css` must read `--text-dim: #8a7f70` and `--glow: #ff7a1a`. If it does not, stop — you are about to freeze colours that have already been corrected.

**Files:**
- Create: `web-react/tests/e2e/pages-fidelity.spec.ts`
- Create: `web-react/tests/e2e/baselines/*.json` (38 files: 19 pages × 2 viewports)
- Modify: `web-react/playwright.config.ts`
- Modify: `web-react/package.json` (two scripts)

**Interfaces:**
- Consumes: `PageSpec`, `measureSelectors`, `compareToBaseline`, `writeBaseline`, `requireBaseline`, `baselinePath`, `CAPTURING` from `./layoutBaseline`; `PAGE_SPECS`, `DESKTOP`, `PHONE` from `./pageSpecs`; `stubApi` from `./apiFixtures`.
- Produces: `tests/e2e/baselines/<name>-<W>x<H>.json` for all 19 specs in `PAGE_SPECS` at both viewports. Every task from 7 onward asserts against these files and may not regenerate them.

**Read this before running anything.** This task captures the reference for the entire migration **from the pre-Tailwind tree**. There is exactly one chance: once Task 7 installs Tailwind there is no unmodified tree left to capture from, and a baseline captured afterwards would be a photograph of the result being compared against itself. Do not run `baseline:capture` in any later task. If a later task's gate goes red, the answer is to change the CSS until it goes green or to get the difference accepted in Task 15 — never to recapture.

Verify the tree is clean before capturing: `jj status` in this workspace must show only this task's own new files.

- [ ] **Step 1: Add the three Playwright projects**

In `web-react/playwright.config.ts`, widen the `app` project's `testIgnore` and append three projects to the `projects` array:

```ts
    {
      name: "app",
      testIgnore: /dashboard-(fidelity|reflow|panel)\.spec\.ts|(pages|chrome|pellets)-fidelity\.spec\.ts/,
      use: { baseURL: ports.appUrl },
    },
```

```ts
    // The generalised fidelity gate. Demo server for the same reason the
    // dashboard one uses it -- useLiveState's demo branch opens no socket, so
    // these specs cannot be raced by the shared PiFire instance the rest of the
    // suite mutates -- plus stubApi(), which makes the REST-fed pages identical
    // on every machine and stops the wizard capture flushing a draft.
    //
    // The viewport is set per-describe with test.use() rather than per-project:
    // the same spec file measures both 1280x720 and 390x844, and one project per
    // viewport would double the config for nothing.
    {
      name: "fidelity-pages",
      testMatch: /pages-fidelity\.spec\.ts/,
      use: { baseURL: ports.demoUrl },
    },
    {
      name: "fidelity-chrome",
      testMatch: /chrome-fidelity\.spec\.ts/,
      use: { baseURL: ports.demoUrl },
    },
    // /pellets is socket-fed, so it cannot run on the demo server (no socket =
    // no pellet database = the "Loading..." branch) and it cannot be stubbed
    // over REST. App server, live backend, fingerprint guard. See Task 5.
    {
      name: "fidelity-pellets",
      testMatch: /pellets-fidelity\.spec\.ts/,
      use: { baseURL: ports.appUrl },
    },
```

- [ ] **Step 2: Add the capture and run scripts**

In `web-react/package.json`'s `scripts`:

```json
    "test:e2e:fidelity": "playwright test --project=fidelity-pages --project=fidelity-chrome --project=fidelity-pellets --project=fidelity --project=reflow --project=panel",
    "baseline:capture": "PF_CAPTURE=1 playwright test --project=fidelity-pages --project=fidelity-chrome --project=fidelity-pellets --project=fidelity",
```

- [ ] **Step 3: Write the spec**

Create `web-react/tests/e2e/pages-fidelity.spec.ts`:

```ts
import { expect, test } from "@playwright/test";
import { stubApi } from "./apiFixtures";
import {
  baselinePath,
  CAPTURING,
  compareToBaseline,
  measureSelectors,
  type PageSpec,
  requireBaseline,
  writeBaseline,
} from "./layoutBaseline";
import { DESKTOP, PAGE_SPECS, PHONE } from "./pageSpecs";

// The fidelity gate for every REST-fed surface, at both viewports.
//
// Deliberately NOT a toHaveScreenshot() gate, and it must not be "upgraded"
// into one: index.html loads Barlow from fonts.googleapis.com, so pixels depend
// on the network and the host font stack, and masking the volatile regions
// would mask exactly the typography the gate exists to protect.
//
// What it records instead, per landmark: the box (x/y/w/h relative to the
// page's root, +/-2px), the type (fontSize/fontWeight, exact), and 32 computed
// visual properties (colour, border, radius, shadow, padding, margin, flex,
// position -- exact). Geometry alone cannot see a wrong @apply bg-*, which is
// the entire class of defect this migration can introduce.

for (const viewport of [DESKTOP, PHONE]) {
  test.describe(`${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport });

    for (const spec of PAGE_SPECS) {
      test(`${spec.name} matches the committed baseline`, async ({ page }) => {
        // Freeze the clock BEFORE navigating so helpers/clock.ts's shared
        // interval, useClock and demoDashAt's elapsed-seconds argument are all
        // pinned. Without this the dashboard header's clock changes width
        // between runs and the gate is flaky on its own reference.
        await page.clock.install({ time: new Date("2026-07-25T12:00:00Z") });
        await page.clock.pauseAt(new Date("2026-07-25T12:00:00Z"));
        await stubApi(page);
        await page.goto(spec.path);
        await expect(page.locator(spec.ready).first()).toBeVisible({ timeout: 15000 });

        for (const click of spec.clicks ?? []) {
          await page.locator(click).first().click();
        }
        // Re-assert after the clicks: `ready` names the step's own heading, so
        // this is what proves the wizard actually advanced before measuring.
        await expect(page.locator(spec.ready).first()).toBeVisible({ timeout: 15000 });

        const actual = await measureSelectors(page, spec);
        // A spec whose selectors match nothing produces an empty, cheerful
        // baseline. Refuse to record one.
        expect(
          Object.keys(actual).length,
          `${spec.name} measured no landmarks -- check pageSpecs.ts`,
        ).toBeGreaterThan(5);

        const path = baselinePath(spec.name, viewport);
        if (CAPTURING) {
          writeBaseline(path, actual);
          console.log(`[fidelity] captured ${path} (${Object.keys(actual).length} landmarks)`);
          return;
        }
        const problems = compareToBaseline(actual, requireBaseline(path), spec.exact);
        expect(problems, `${spec.name} @ ${viewport.width}x${viewport.height}\n${problems.join("\n")}`).toEqual([]);
      });
    }
  });
}

// Not a landmark check: a page that fits in a desktop window and scrolls
// sideways on a phone is broken in a way no per-element baseline can see,
// because every element is individually where the baseline says it should be.
// dashboard-fidelity.spec.ts learned this the hard way -- the mode-button row
// was sliced across the bottom edge while the landmark gate passed
// byte-for-byte.
for (const viewport of [DESKTOP, PHONE]) {
  test.describe(`${viewport.width}x${viewport.height} overflow`, () => {
    test.use({ viewport });
    for (const spec of PAGE_SPECS) {
      test(`${spec.name} does not scroll sideways`, async ({ page }) => {
        await page.clock.install({ time: new Date("2026-07-25T12:00:00Z") });
        await page.clock.pauseAt(new Date("2026-07-25T12:00:00Z"));
        await stubApi(page);
        await page.goto(spec.path);
        await expect(page.locator(spec.ready).first()).toBeVisible({ timeout: 15000 });
        for (const click of spec.clicks ?? []) {
          await page.locator(click).first().click();
        }
        const doc = await page.evaluate(() => ({
          scrollW: document.documentElement.scrollWidth,
          innerW: window.innerWidth,
        }));
        expect(doc.scrollW, `${spec.name} scrolls sideways`).toBeLessThanOrEqual(doc.innerW);
      });
    }
  });
}
```

- [ ] **Step 4: Confirm the gate fails before it is fed**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test:e2e --project=fidelity-pages 2>&1 | tail -20
```

Expected: 38 landmark tests fail with `tests/e2e/baselines/<name>-<W>x<H>.json is missing. Run \`bun run baseline:capture\` …`, and the 38 overflow tests pass. If any landmark test *passes* here, a baseline file already exists and this task has been run before — stop and find out why.

- [ ] **Step 5: Capture**

```bash
cd /home/dannyb/sources/PiFire/web-react
PF_CAPTURE=1 playwright test --project=fidelity-pages
ls tests/e2e/baselines/ | wc -l
```

Expected: `38` files. The console prints one `[fidelity] captured …` line per file with its landmark count.

- [ ] **Step 6: Review the capture by hand — this is not optional**

A baseline is only worth what the human who reviewed it put in. Check three things:

```bash
cd /home/dannyb/sources/PiFire/web-react
# 1. Nothing is suspiciously thin. A page that failed to render still produces
#    a file, just a short one.
for f in tests/e2e/baselines/*.json; do printf '%-52s %s\n' "$(basename $f)" "$(python3 -c "import json,sys;print(len(json.load(open('$f'))))" )"; done
# 2. No landmark has a zero-sized box, which means it rendered but is empty.
python3 - <<'EOF'
import json, glob
for f in sorted(glob.glob("tests/e2e/baselines/*.json")):
    for k, v in json.load(open(f)).items():
        if v["w"] == 0 or v["h"] == 0:
            print(f, k, v["w"], v["h"])
EOF
# 3. The phone baselines actually differ from the desktop ones.
diff <(python3 -c "import json;print(sorted(json.load(open('tests/e2e/baselines/dashboard-1280x720.json'))))") \
     <(python3 -c "import json;print(sorted(json.load(open('tests/e2e/baselines/dashboard-390x844.json'))))")
```

Expected: (1) every file has at least 15 landmarks, and the wizard/settings ones are broadly similar to each other; (2) **no output** — a zero-sized landmark means the selector matched a collapsed element and belongs in a fix to `pageSpecs.ts`, not in the reference; (3) the key sets may differ (a `#0`/`#1` split that only happens at one width) but the differences must be explicable.

- [ ] **Step 7: Assert the captured baseline against itself**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test:e2e --project=fidelity-pages
```

Expected: 76 passed, 0 failed. A failure here — with nothing changed between capture and assert — means the gate is non-deterministic, and that must be fixed *now*, not diagnosed later while a thousand lines of CSS are also in motion. The usual causes, in order: an unfrozen clock (`page.clock` missing on one path), a font race (`waitForBarlow` not reached), and a `ready` selector that resolves before the page has settled.

- [ ] **Step 8: Run it twice more to prove determinism**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test:e2e --project=fidelity-pages && bun run test:e2e --project=fidelity-pages
```

Expected: green both times. Flakiness that shows up one run in three is worse than no gate, because it teaches everyone to re-run instead of to look.

- [ ] **Step 9: Run the four gates and commit**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run typecheck && bun run typecheck:e2e && bun run lint && bun run test && bun run gen:types:check
cd /home/dannyb/sources/PiFire
jj commit -m 'test(web-react): capture the pre-Tailwind fidelity reference for 19 pages at two viewports'
```

---

## Task 5: Chrome probes and the pellets gate

**Files:**
- Create: `web-react/tests/e2e/chrome-fidelity.spec.ts`
- Create: `web-react/tests/e2e/pellets-fidelity.spec.ts`
- Create: `web-react/tests/e2e/baselines/chrome-{1280x720,390x844}.json`
- Create: `web-react/tests/e2e/baselines/pellets-{1280x720,390x844}.json`
- Create: `web-react/tests/e2e/baselines/pellets-fingerprint.json`

**Interfaces:**
- Consumes: `measureProbes`, `compareStyles`, `writeStyleBaseline`, `readStyleBaseline`, `measureSelectors`, `compareToBaseline`, `writeBaseline`, `readBaseline`, `baselinePath`, `CAPTURING` from `./layoutBaseline`; `CHROME_PROBES`, `PELLETS_SPEC`, `DESKTOP`, `PHONE` from `./pageSpecs`; `stubApi` from `./apiFixtures`.
- Produces: `tests/e2e/baselines/chrome-<W>x<H>.json` (a `StyleMap`), `tests/e2e/baselines/pellets-<W>x<H>.json` (a `LandmarkMap`), `tests/e2e/baselines/pellets-fingerprint.json`.

**Preconditions:** the pellets half needs a live backend — `uv run python control.py` plus `uv run gunicorn -k gthread --threads 25 -b 0.0.0.0:5000 -w 1 app:app` — and, if you are in a jj workspace, `PIFIRE_DB_PATH` pointing at the same `pifire.db` that backend serves. `common/datastore.py` resolves `DB_PATH` relative to its own checkout, so a workspace otherwise reads a different database than the server writes.

- [ ] **Step 1: Write the chrome probe spec**

Create `web-react/tests/e2e/chrome-fidelity.spec.ts`:

```ts
import { expect, test } from "@playwright/test";
import { stubApi } from "./apiFixtures";
import {
  CAPTURING,
  compareStyles,
  measureProbes,
  readStyleBaseline,
  writeStyleBaseline,
} from "./layoutBaseline";
import { CHROME_PROBES, DESKTOP, PHONE } from "./pageSpecs";

// Chrome that never renders under a fixed fixture, pinned synthetically.
//
// Banners returns null with no errors and no warnings (shell/Banners.tsx:22);
// TimerBar renders only while useTimerVisibility says so (shell/AppShell.tsx:39);
// the wizard's three role="dialog" surfaces need a running grill or a finished
// installer. Under the demo fixture none of them exist, so a page baseline
// covers none of them -- and they are ~40 of shell.css's 315 lines.
//
// This attaches a detached element carrying the class, reads its computed
// style, and removes it. tests/e2e/wizard-layout.spec.ts already does exactly
// this for the modal and the install stripe; this generalises it and gives it a
// committed baseline. It proves the rules resolve and to WHAT. It proves
// nothing about how they look in situ -- that is Task 15's human checkpoint.

for (const viewport of [DESKTOP, PHONE]) {
  test.describe(`${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport });

    test("conditional chrome resolves to the committed styles", async ({ page }) => {
      await page.clock.install({ time: new Date("2026-07-25T12:00:00Z") });
      await page.clock.pauseAt(new Date("2026-07-25T12:00:00Z"));
      await stubApi(page);
      // The shell (not the wizard) so shell.css, dashboard.css, settings.css and
      // pellets.css are all loaded; wizard.css arrives through the /wizard
      // route's own import, so the wizard probes get their own host below.
      await page.goto("/");
      await expect(page.locator(".pf-shell")).toBeVisible({ timeout: 15000 });

      const shellProbes = CHROME_PROBES.filter((p) => !p.className.includes("pf-wizard-modal") && !p.className.includes("pf-install"));
      const shell = await measureProbes(page, ".pf-shell", shellProbes);

      await page.goto("/wizard");
      await expect(page.locator(".pf-wizard-content")).toBeVisible({ timeout: 15000 });
      const wizardProbes = CHROME_PROBES.filter((p) => p.className.includes("pf-wizard-modal") || p.className.includes("pf-install"));
      const wizard = await measureProbes(page, ".pf-wizard-content", wizardProbes);

      const actual = { ...shell, ...wizard };
      expect(Object.keys(actual).length).toBe(CHROME_PROBES.length);

      const path = `tests/e2e/baselines/chrome-${viewport.width}x${viewport.height}.json`;
      if (CAPTURING) {
        writeStyleBaseline(path, actual);
        console.log(`[chrome] captured ${path}`);
        return;
      }
      const baseline = readStyleBaseline(path);
      if (baseline === null) {
        throw new Error(`${path} is missing. Run \`bun run baseline:capture\` and review the result.`);
      }
      const problems = compareStyles(actual, baseline);
      expect(problems, problems.join("\n")).toEqual([]);
    });
  });
}
```

- [ ] **Step 2: Guard the guard — prove a probe can fail**

A synthetic probe that reads a rule which does not exist returns the browser's initial values for every property and looks perfectly healthy. Add this test to the same file, so the mechanism is proven able to say "no":

```ts
test("an unstyled probe is distinguishable from a styled one", async ({ page }) => {
  await stubApi(page);
  await page.goto("/");
  await expect(page.locator(".pf-shell")).toBeVisible({ timeout: 15000 });
  const out = await measureProbes(page, ".pf-shell", [
    { name: "real", className: "pf-banner pf-banner--error" },
    { name: "ghost", className: "pf-banner-does-not-exist" },
  ]);
  // If these two agree, the probe is measuring the browser's defaults and every
  // assertion above it is vacuous.
  expect(out.real["background-color"]).not.toBe(out.ghost["background-color"]);
});
```

- [ ] **Step 3: Write the pellets spec with its fingerprint guard**

`/pellets` reads the whole pellet database off `socket_pellet_data` — no fetch, no polling (`PelletsPage.tsx:37`). The demo server opens no socket, so `pellets` stays `null` and the page renders only `<div class="pf-pellets pf-pellets-empty">Loading pellet database…</div>`. That is a useless reference. The live backend it must use instead has a different number of brands, woods and profiles on every install, and a landmark baseline is a list of boxes.

The resolution is the discipline `pellets.spec.ts` already uses (`test.skip(!loaded, "current pelletid is not in the archive on this install")`): record the store's shape alongside the baseline, and **skip loudly** rather than fail when the machine's store differs. A skip is honest; a failure would train people to recapture.

Create `web-react/tests/e2e/pellets-fidelity.spec.ts`:

```ts
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import {
  baselinePath,
  CAPTURING,
  compareToBaseline,
  measureSelectors,
  readBaseline,
  writeBaseline,
} from "./layoutBaseline";
import { DESKTOP, PELLETS_SPEC, PHONE } from "./pageSpecs";

// PRECONDITIONS: a real backend -- control.py plus gunicorn -- and, in a jj
// workspace, PIFIRE_DB_PATH pointing at the SAME pifire.db that backend serves.
// common/datastore.py resolves DB_PATH relative to its own checkout.

const FINGERPRINT = "tests/e2e/baselines/pellets-fingerprint.json";

interface PelletDbShape {
  brands: string[];
  woods: string[];
  archive: Record<string, unknown>;
  current: { pelletid: string };
  log: unknown[];
}

/** What the layout of /pellets actually depends on: how many rows each table
 *  renders, and which profile is loaded. Not the contents -- a renamed brand
 *  moves no box, and pinning names would make the gate unusable on any machine
 *  but the one that captured it. */
function fingerprint(db: PelletDbShape): Record<string, number | string> {
  return {
    brands: db.brands.length,
    woods: db.woods.length,
    archive: Object.keys(db.archive).length,
    log: db.log.length,
    current: db.current.pelletid,
  };
}

for (const viewport of [DESKTOP, PHONE]) {
  test.describe(`${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport });

    test("pellets matches the committed baseline", async ({ page }) => {
      const res = await page.request.get("/api/pellets");
      expect(res.ok(), "GET /api/pellets failed -- is control.py + gunicorn running?").toBeTruthy();
      const db = ((await res.json()) as { data: { pellets: PelletDbShape } }).data.pellets;
      const fp = fingerprint(db);

      if (!CAPTURING) {
        const want = existsSync(FINGERPRINT)
          ? (JSON.parse(readFileSync(FINGERPRINT, "utf8")) as Record<string, number | string>)
          : null;
        expect(want, `${FINGERPRINT} is missing. Run \`bun run baseline:capture\`.`).not.toBeNull();
        // A skip, not a failure. This machine's pellet store is a different
        // shape from the one the reference was captured on, so its boxes are
        // legitimately different and there is nothing to compare. The gate is
        // silent here BY DESIGN -- which is exactly why Task 15 requires this
        // test to have RUN, not skipped, on the reviewing machine.
        test.skip(
          JSON.stringify(want) !== JSON.stringify(fp),
          `pellet store differs from the baseline's: ${JSON.stringify(want)} vs ${JSON.stringify(fp)}. ` +
            `Re-capture on this machine, or run the gate where the reference was taken.`,
        );
      }

      await page.clock.install({ time: new Date("2026-07-25T12:00:00Z") });
      await page.clock.pauseAt(new Date("2026-07-25T12:00:00Z"));
      await page.goto("/pellets");
      await expect(page.locator(PELLETS_SPEC.ready)).toBeVisible({ timeout: 20000 });

      const actual = await measureSelectors(page, PELLETS_SPEC);
      expect(Object.keys(actual).length).toBeGreaterThan(10);

      const path = baselinePath(PELLETS_SPEC.name, viewport);
      if (CAPTURING) {
        writeBaseline(path, actual);
        writeFileSync(FINGERPRINT, `${JSON.stringify(fp, null, 2)}\n`);
        console.log(`[pellets] captured ${path} at ${JSON.stringify(fp)}`);
        return;
      }
      const baseline = readBaseline(path);
      expect(baseline, `${path} is missing. Run \`bun run baseline:capture\`.`).not.toBeNull();
      const problems = compareToBaseline(actual, baseline ?? {}, PELLETS_SPEC.exact);
      expect(problems, problems.join("\n")).toEqual([]);
    });
  });
}
```

- [ ] **Step 4: Capture both**

```bash
cd /home/dannyb/sources/PiFire/web-react
PF_CAPTURE=1 playwright test --project=fidelity-chrome --project=fidelity-pellets
ls tests/e2e/baselines/
```

Expected: the 38 files from Task 4 plus `chrome-1280x720.json`, `chrome-390x844.json`, `pellets-1280x720.json`, `pellets-390x844.json`, `pellets-fingerprint.json` — 43 files.

- [ ] **Step 5: Review the two new kinds of file by hand**

```bash
cd /home/dannyb/sources/PiFire/web-react
cat tests/e2e/baselines/pellets-fingerprint.json
python3 -c "import json;d=json.load(open('tests/e2e/baselines/chrome-1280x720.json'));print(len(d));print(d['banner-error']['background-color'], d['banner-warning']['background-color'], d['banner-critical']['background-color'])"
```

Expected: the fingerprint names this machine's store; the chrome file has 15 probes and the three banner backgrounds are **three different colours**. If any two agree, the `--warning`/`--critical`/`--error` modifiers are not resolving and the probe is measuring nothing.

- [ ] **Step 6: Assert, twice**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test:e2e --project=fidelity-chrome --project=fidelity-pellets
bun run test:e2e --project=fidelity-chrome --project=fidelity-pellets
```

Expected: green both times, with **no skips** on the machine that captured. A skip here means the fingerprint changed between capture and assert — the backend mutated the store mid-run, most likely another spec, and `workers: 1` should already prevent that.

- [ ] **Step 7: Run the four gates and commit**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run typecheck && bun run typecheck:e2e && bun run lint && bun run test && bun run gen:types:check
cd /home/dannyb/sources/PiFire
jj commit -m 'test(web-react): pin conditional chrome and the pellets page against the pre-Tailwind tree'
```

---

## Task 6: A class-coverage guard that survives `@apply`

**Files:**
- Create: `web-react/src/helpers/cssCoverage.ts`
- Create: `web-react/src/helpers/cssCoverage.test.ts`
- Create: `web-react/src/styleCoverage.test.ts`
- Modify: `web-react/src/components/wizard/wizardStyles.test.ts`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces, from `web-react/src/helpers/cssCoverage.ts`:
  - `export function walk(dir: string, out?: string[]): string[]`
  - `export function stripComments(css: string): string`
  - `export function declaredClasses(css: string): Set<string>`
  - `export function classesUsedIn(dir: string): Set<string>`
  - `export function allStylesheets(root?: string): string`
  Task 8's `themeTokens.test.ts` and every conversion task (9-14) depend on `declaredClasses` counting `@apply`-only bodies.

**The defect this closes, measured.** `src/components/wizard/wizardStyles.test.ts:52-59` decides a class "has a rule" like this:

```ts
for (const [, selector, body] of stripComments(css).matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!body.includes(":")) continue;
```

`.pf-card { @apply bg-card rounded-card p-4; }` contains **no colon**. Every rule converted in Tasks 9-14 would stop counting as declared, and the guard — the only thing standing between "the stylesheet compiles" and "the classes the app actually uses exist" — would go green while proving nothing. It has already been silently disarmed once, when comment prose counted as selector text; that was fixed by `stripComments`, and this is the same hole in a new shape.

While extracting it, the guard also stops being wizard-only. Live, no other surface has one: dashboard, shell, settings, history and pellets classes could all be deleted from CSS without a single test noticing.

- [ ] **Step 1: Write the failing tests**

Create `web-react/src/helpers/cssCoverage.test.ts`:

```ts
import { describe, expect, it } from "@rstest/core";
import { classesUsedIn, declaredClasses, stripComments } from "./cssCoverage";

describe("declaredClasses", () => {
  it("counts an ordinary declaration", () => {
    expect(declaredClasses(".pf-real { color: red; }").has("pf-real")).toBe(true);
  });

  // THE @apply CASE. An @apply body has no colon in it, and the pre-extraction
  // guard required one -- so every rule this migration converts would have
  // stopped counting as declared and the guard would have gone green on an
  // empty stylesheet.
  it("counts a rule whose whole body is @apply", () => {
    expect(declaredClasses(".pf-card { @apply bg-card rounded-card p-4; }").has("pf-card")).toBe(
      true,
    );
  });

  it("counts a rule mixing @apply with raw declarations", () => {
    const css = ".pf-mix { @apply flex; background: color-mix(in srgb, var(--accent) 16%, transparent); }";
    expect(declaredClasses(css).has("pf-mix")).toBe(true);
  });

  // Guards the guard, three ways. Every assertion above is only as good as this
  // being able to say "no".
  it("does not count an empty rule", () => {
    expect(declaredClasses(".pf-hollow { }").has("pf-hollow")).toBe(false);
  });

  it("does not count a class that is only NAMED IN A COMMENT", () => {
    const css = "/* .pf-ghost is explained here */\n.pf-real { color: red; }";
    expect(declaredClasses(css).has("pf-real")).toBe(true);
    expect(declaredClasses(css).has("pf-ghost")).toBe(false);
  });

  it("does not count a class named only inside an @apply argument list", () => {
    // `@apply pf-thing` would be a Tailwind utility reference, not a rule that
    // declares .pf-thing. If this returned true, deleting .pf-thing's own rule
    // would leave the guard green.
    expect(declaredClasses(".pf-host { @apply pf-thing; }").has("pf-thing")).toBe(false);
    expect(declaredClasses(".pf-host { @apply pf-thing; }").has("pf-host")).toBe(true);
  });

  it("reaches rules nested inside @media", () => {
    const css = "@media (max-width: 719px) { .pf-phone { @apply p-[8px]; } }";
    expect(declaredClasses(css).has("pf-phone")).toBe(true);
  });

  it("ignores an @reference line", () => {
    const css = '@reference "../../theme.css";\n.pf-x { @apply flex; }';
    expect(declaredClasses(css).has("pf-x")).toBe(true);
  });
});

describe("stripComments", () => {
  it("removes block comments and keeps the rules around them", () => {
    expect(stripComments("/* a */ .b { c: d; } /* e */").trim()).toBe(".b { c: d; }");
  });
});

describe("classesUsedIn", () => {
  it("finds classes in plain, template and single-quoted strings", () => {
    const used = classesUsedIn("src/components/wizard");
    // Built in a plain `const`, not a className attribute -- an attribute-only
    // scan would miss it (InstallProgress.tsx).
    expect(used.has("pf-install-progress-bar-reduced-motion")).toBe(true);
    expect(used.has("pf-wizard-header")).toBe(true);
  });

  it("skips test files", () => {
    // wizardStyles.test.ts is full of pf-* names in string literals; counting
    // them would let a test keep a deleted class alive.
    expect(classesUsedIn("src/components/wizard").has("pf-ghost")).toBe(false);
  });
});
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test 2>&1 | grep -A3 'cssCoverage'
```

Expected: the file fails to resolve `./cssCoverage` — `Cannot find module`. This is `*.test.ts`, so it runs in the `unit-node` rstest project and `node:fs` is available.

- [ ] **Step 3: Write the engine**

Create `web-react/src/helpers/cssCoverage.ts`:

```ts
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

/** Every file under `dir`, recursively. */
export function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else out.push(full);
  }
  return out;
}

/**
 * Comments are prose, not selectors.
 *
 * Stripping them is load-bearing: the `selector` capture in declaredClasses is
 * "everything since the previous brace", which includes any comment sitting
 * above a rule -- and these stylesheets' comments routinely name the very class
 * they introduce. Without this, a class counted as declared because a COMMENT
 * mentioned it, so deleting its rule left the guard green. Found by mutation:
 * removing both .pf-module-notes blocks did not turn the wizard guard red until
 * comments were stripped.
 */
export function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/**
 * The classes for which `css` contains a non-empty rule.
 *
 * "Non-empty" means the body declares something. `.foo {}` does not count -- an
 * empty rule is the original defect wearing a hat. Two ways to declare:
 *
 *   - an ordinary `prop: value` pair, detected by a colon;
 *   - an `@apply` at-rule, which has NO colon in it.
 *
 * That second clause is why this function exists as a module. The wizard guard
 * used to require a colon, so `.pf-card { @apply bg-card; }` -- the shape every
 * rule takes after the Tailwind v4 migration -- would have counted as
 * undeclared, and the guard would have reported a totally empty stylesheet as
 * fully covered.
 *
 * Only the SELECTOR is scanned for class names, never the body: `@apply
 * pf-thing` references a utility, it does not declare `.pf-thing`.
 *
 * The regex matches innermost blocks first, so rules nested inside @media are
 * captured (the outer @media prelude never matches: its body contains braces).
 */
export function declaredClasses(css: string): Set<string> {
  const out = new Set<string>();
  for (const [, selector, body] of stripComments(css).matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!body.includes(":") && !body.includes("@apply")) continue;
    for (const hit of selector.matchAll(/\.(pf-[a-z0-9]+(?:-[a-z0-9]+)*)/g)) out.add(hit[1]);
  }
  return out;
}

/**
 * Every pf-* token in ANY string literal under `dir`, not just in a className
 * attribute. InstallProgress builds its bar class in a plain `const`, so an
 * attribute-only scan would miss pf-install-progress-bar and
 * pf-install-progress-bar-reduced-motion.
 *
 * Test files are excluded: they are full of pf-* names, and counting them would
 * let a test keep a deleted class alive.
 */
export function classesUsedIn(dir: string): Set<string> {
  const found = new Set<string>();
  for (const file of walk(dir)) {
    if (!file.endsWith(".tsx") || file.endsWith(".test.tsx")) continue;
    const src = readFileSync(file, "utf8");
    for (const [, dq, tpl, sq] of src.matchAll(/"([^"\n]*)"|`([^`\n]*)`|'([^'\n]*)'/g)) {
      for (const hit of (dq ?? tpl ?? sq ?? "").matchAll(/\bpf-[a-z0-9]+(?:-[a-z0-9]+)*/g)) {
        found.add(hit[0]);
      }
    }
  }
  return found;
}

/** Every stylesheet under `root`, concatenated. */
export function allStylesheets(root = "src"): string {
  return walk(root)
    .filter((f) => f.endsWith(".css"))
    .map((f) => readFileSync(f, "utf8"))
    .join("\n");
}
```

- [ ] **Step 4: Run the tests and watch them pass**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test 2>&1 | grep -E 'cssCoverage|"tests"|failedTests'
```

Expected: all 11 new tests pass; the totals rise from `1010` to `1021`.

- [ ] **Step 5: Prove the fix was needed — a mutation check**

Temporarily revert the `@apply` clause and confirm the suite goes red, so nobody later "simplifies" it back:

```bash
cd /home/dannyb/sources/PiFire/web-react
sed -i 's/if (!body.includes(":") \&\& !body.includes("@apply")) continue;/if (!body.includes(":")) continue;/' src/helpers/cssCoverage.ts
bun run test 2>&1 | grep -E '"failedTests"'
git checkout src/helpers/cssCoverage.ts
```

Expected: `"failedTests": 3` (the three `@apply` cases), then the file is restored. Confirm with `git status --short src/` printing nothing.

- [ ] **Step 6: Add the repo-wide coverage guard**

Create `web-react/src/styleCoverage.test.ts`:

```ts
import { describe, expect, it } from "@rstest/core";
import { allStylesheets, classesUsedIn, declaredClasses } from "./helpers/cssCoverage";

// The repo-wide half of the class-coverage guard.
//
// src/components/wizard/wizardStyles.test.ts has covered the wizard directory
// for a while; nothing covered the other five surfaces, so a dashboard, shell,
// settings, history or pellets class could be deleted from CSS with a green
// suite. This is the only thing standing between "the stylesheet compiles" and
// "the classes the app actually uses exist", and the Tailwind migration rewrites
// every one of those stylesheets.

const SURFACES = [
  "src/components/dashboard",
  "src/components/shell",
  "src/components/settings",
  "src/components/history",
  "src/components/pellets",
  "src/components/wizard",
];

describe("every pf-* class the app uses has a rule", () => {
  const declared = declaredClasses(allStylesheets());

  it("finds the classes it is supposed to be checking", () => {
    const used = new Set(SURFACES.flatMap((d) => [...classesUsedIn(d)]));
    // Measured on the pre-migration tree: 238 distinct pf-* classes are
    // declared across the seven stylesheets. A floor, not an equality -- new
    // components add classes and that must not turn this red.
    expect(used.size).toBeGreaterThanOrEqual(150);
    expect(declared.size).toBeGreaterThanOrEqual(200);
  });

  for (const dir of SURFACES) {
    it(`${dir} declares every class it uses`, () => {
      const missing = [...classesUsedIn(dir)].filter((c) => !declared.has(c)).sort();
      expect(missing, `no CSS rule for: ${missing.join(", ")}`).toEqual([]);
    });
  }

  // The other end of the same data path: rules that exist but never load are
  // rules that do not exist.
  it("keeps every stylesheet imported", () => {
    const imports = [
      ["src/main.tsx", "./theme.css"],
      ["src/main.tsx", "./components/dashboard/dashboard.css"],
      ["src/main.tsx", "./components/settings/settings.css"],
      ["src/components/shell/AppShell.tsx", "./shell.css"],
      ["src/components/wizard/WizardShell.tsx", "./wizard.css"],
      ["src/components/history/HistoryChart.tsx", "./historyChart.css"],
      ["src/components/pellets/PelletsPage.tsx", "./pellets.css"],
    ];
    const { readFileSync } = require("node:fs") as typeof import("node:fs");
    for (const [file, spec] of imports) {
      expect(readFileSync(file, "utf8"), `${file} no longer imports ${spec}`).toContain(
        `import "${spec}";`,
      );
    }
  });
});
```

If Biome objects to the `require` call, hoist `import { readFileSync } from "node:fs";` to the top of the file and delete the inline line — it is only inline to keep the import list of this test short.

- [ ] **Step 7: Re-point the wizard guard at the shared engine**

In `web-react/src/components/wizard/wizardStyles.test.ts`, delete the local `walk`, `stripComments`, `declaredClasses` and `classesUsed` definitions (lines 8-59) and replace the import block at the top with:

```ts
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "@rstest/core";
import { allStylesheets, classesUsedIn, declaredClasses } from "../../helpers/cssCoverage";

const WIZARD_DIR = join("src", "components", "wizard");
const WIZARD_CSS = join(WIZARD_DIR, "wizard.css");
```

then inside the `describe`, replace the three consts with:

```ts
  const used = classesUsedIn(WIZARD_DIR);
  const anywhere = declaredClasses(allStylesheets());
  const inWizardCss = declaredClasses(readFileSync(WIZARD_CSS, "utf8"));
```

**Leave every `it(...)` in that file exactly as it is**, including `it("does not count a class that is only NAMED IN A COMMENT")` — it now tests the shared engine, which is precisely where you want it.

- [ ] **Step 8: Run all four gates**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run typecheck && bun run typecheck:e2e && bun run lint && bun run test && bun run gen:types:check
```

Expected: rstest reports `117` test files and roughly `1028` tests, 0 failed. The wizard guard's own count must be unchanged — if `wizard stylesheet coverage` loses a test, the extraction dropped one.

- [ ] **Step 9: Commit**

```bash
cd /home/dannyb/sources/PiFire
jj commit -m 'test(web-react): extract the class-coverage guard and make it survive @apply bodies'
```

---

## Task 7: Wire Tailwind in, and change nothing

**Files:**
- Modify: `web-react/package.json`, `web-react/bun.lock`
- Modify: `web-react/rsbuild.config.ts`
- Modify: `web-react/biome.jsonc`
- Modify: `web-react/src/theme.css` (one import line)

**Interfaces:**
- Consumes: every baseline in `tests/e2e/baselines/` from Tasks 4-5.
- Produces: `@import` of Tailwind available to `src/theme.css`; `pluginTailwindcss()` in the Rsbuild plugin list; Biome able to parse Tailwind at-rules. No TypeScript symbols.

**This step matters more than it looks.** If adding Tailwind changes anything before a single rule is rewritten — a reset, a layer order, a specificity shift — that must be found and understood *in isolation*, not diagnosed later while a thousand lines are also in motion. Nothing else may land in this commit.

**No PostCSS.** Rsbuild transforms CSS with Lightning CSS through Rspack's built-in `lightningcss-loader` and registers `postcss-loader` only if a `postcss.config.*` exists or `tools.postcss` is set. This repo has neither, Tailwind v4 also uses Lightning CSS, and `@rsbuild/plugin-tailwindcss` builds on `@tailwindcss/webpack`. Do not create a PostCSS config, and do not add autoprefixer, postcss-preset-env, postcss-nesting or cssnano — Lightning CSS already reads browserslist (pinned in Task 1), adds prefixes and downlevels modern syntax; minification comes from `LightningCssMinimizerRspackPlugin`.

**Preflight is the thing most likely to move something, and the decision rule is stated up front, not discovered.** Tailwind v4's `@import "tailwindcss"` pulls in three layers: `theme`, `base` (preflight) and `utilities`. Cascade Layers give unlayered author rules priority over *any* layered rule, so preflight cannot override the `pf-*` rules — but it applies everywhere they are silent, and v4's preflight sets `margin: 0; padding: 0; border: 0 solid` on `*, ::before, ::after, ::backdrop, ::file-selector-button`, strips heading sizes and list markers, and makes form controls inherit `font` and `color`. This app has bare `<p>`, `<h2>`, `<ul>`, `<table>` and `<input>` elements in the wizard, settings and pellets surfaces that rely on UA defaults. `theme.css` also already carries its own reset (`* { box-sizing: border-box }`, `html, body, #root { height: 100%; margin: 0 }`).

So: Step 4 imports the whole thing and measures. Step 6 is the reconciliation, and it is fully specified — no branch is left open.

- [ ] **Step 1: Install**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun add -d tailwindcss @rsbuild/plugin-tailwindcss
grep -E '"tailwindcss"|"@rsbuild/plugin-tailwindcss"' package.json
```

Expected: `tailwindcss` at `^4.3.x` and `@rsbuild/plugin-tailwindcss` at `^2.0.3`. The plugin peers `@rsbuild/core: ^2.0.0` and this repo pins `2.1.8`, so no resolution warning should appear. **`bun.lock` changed — it is part of this commit.**

- [ ] **Step 2: Register the plugin**

In `web-react/rsbuild.config.ts`, add the import and extend the plugin list:

```ts
import { pluginTailwindcss } from "@rsbuild/plugin-tailwindcss";
```

```ts
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
```

- [ ] **Step 3: Teach Biome the Tailwind at-rules**

Measured on Biome 2.5.5: `@theme` and `@apply` are a hard **parse error** (`× Tailwind-specific syntax is disabled.`), which also aborts formatting of the whole file. One config key fixes it, and it was verified against `@theme`, `@apply`, `@reference`, `@source`, `@utility` and `@custom-variant` — zero lint findings, zero formatting changes. In `web-react/biome.jsonc`, insert after the `"files"` block:

```jsonc
  // Biome 2.5.5 treats @theme / @apply / @reference as a PARSE ERROR without
  // this, and a parse error aborts formatting of the whole file -- so the
  // Tailwind v4 migration would have made `bun run lint` unrunnable rather than
  // merely noisy. Measured:
  //
  //   × Tailwind-specific syntax is disabled.
  //   i Enable `tailwindDirectives` in the css parser options.
  //
  // Scoped to the CSS parser only; no lint rule is relaxed. noDescendingSpecificity
  // and the rest still run over every stylesheet, and none of them fire on
  // @apply bodies -- the migration changes declarations, never selectors.
  "css": { "parser": { "tailwindDirectives": true } },
```

- [ ] **Step 4: Import Tailwind, whole, and measure**

At the very top of `web-react/src/theme.css`, above the existing `/* Design tokens ported verbatim … */` comment:

```css
@import "tailwindcss";
```

Then:

```bash
cd /home/dannyb/sources/PiFire/web-react
rm -rf dist && bun run build && ls -l dist/static/css/
bun run test:e2e:fidelity 2>&1 | tail -40
```

Record two things: the new CSS bundle size (the pre-Tailwind reference is `37.2 kB` raw / `7.8 kB` gzip from Task 1), and the **exact list of baseline entries that moved**. The failures print as `<page> @ <viewport>` followed by `name.prop: before -> after` lines.

- [ ] **Step 5: Decide, using the recorded evidence**

- **If the fidelity suite is fully green**, the two resets are compatible. Skip Step 6, note in the commit message that preflight was measured as inert, and go to Step 7.
- **If anything moved** — the expected outcome, for the reasons above — go to Step 6. Do not "fix" the moved pages by editing their stylesheets; that would be reconciling the resets by hand, one symptom at a time, and the reference exists precisely so that does not happen.

- [ ] **Step 6: Reconcile the resets by not importing preflight**

Replace the `@import "tailwindcss";` line in `web-react/src/theme.css` with:

```css
/* Tailwind v4, WITHOUT preflight.
 *
 * `@import "tailwindcss"` is three layers -- theme, base (preflight) and
 * utilities. This project wants the first and the third.
 *
 * Preflight is a reset, and this file has carried its own since before Tailwind
 * existed (the `*`/`html`/`body` rules below, ported alongside the Qt theme).
 * Cascade Layers mean preflight cannot override any pf-* rule -- unlayered
 * author declarations beat every layered one regardless of specificity -- but it
 * applies wherever those rules are silent, and v4's preflight sets
 * `margin: 0; padding: 0; border: 0 solid` on `*, ::before, ::after, ::backdrop,
 * ::file-selector-button`, drops heading sizes and list markers, and makes form
 * controls inherit font and colour. The wizard, settings and pellets surfaces
 * all render bare <p>, <h2>, <ul>, <table> and <input> elements that take UA
 * defaults today.
 *
 * The measured result is recorded in this migration's commit message. Adopting
 * preflight is a deliberate visual change to argue for on its own, not a side
 * effect of a toolchain swap.
 */
@layer theme, base, components, utilities;
@import "tailwindcss/theme.css" layer(theme);
@import "tailwindcss/utilities.css" layer(utilities);
```

Confirm those two entry points exist before assuming the paths:

```bash
cd /home/dannyb/sources/PiFire/web-react
ls node_modules/tailwindcss/*.css
```

Expected: `index.css`, `preflight.css`, `theme.css`, `utilities.css`.

Then re-run:

```bash
cd /home/dannyb/sources/PiFire/web-react
rm -rf dist && bun run build && ls -l dist/static/css/
bun run test:e2e:fidelity
```

Expected: **every baseline green, zero diff.** The declared `@layer` order line is not decorative — without it the layers are ordered by first appearance, and `utilities` must lose to unlayered `pf-*` rules but win over `theme`.

If something *still* moves after preflight is out, stop and diagnose it before going further. The only remaining candidates are the `@layer` statement itself changing the order of the existing unlayered rules (it cannot — they are unlayered) or Lightning CSS emitting differently now that a second `@import` is in the file. Do not proceed with a diff you cannot explain.

- [ ] **Step 7: Confirm the bundle did not blow up**

```bash
cd /home/dannyb/sources/PiFire/web-react
ls -l dist/static/css/
```

Expected: still in the neighbourhood of `37-45 kB` raw. Tailwind's automatic source detection scans the project and emits only the utilities it finds; with no utilities in JSX (out of scope, and Tasks 9-14 use `@apply`, which does not depend on content scanning) the emitted utility set should be near-empty. **Do not add `@source none`** to shrink it further — that would silently break the first utility anyone writes in JSX, for a few kilobytes.

- [ ] **Step 8: Confirm Biome is genuinely parsing, not skipping**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run lint
printf '.pf-broken {\n  color: ;\n}\n' >> src/theme.css
bun run lint 2>&1 | head -8
git checkout src/theme.css.orig 2>/dev/null || true
```

Then undo the appended lines by hand (or `git diff src/theme.css` and revert the last three lines). Expected: the first `lint` is clean; the second reports a CSS parse error on the broken rule. If the second is *also* clean, Biome is not reading the file at all and `tailwindDirectives` has masked a real problem.

- [ ] **Step 9: Run the four gates**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run typecheck && bun run typecheck:e2e && bun run lint && bun run test && bun run gen:types:check
```

Expected: all clean. `bun run test` is unaffected — measured: rstest imports CSS without processing it, so an `@apply` in an imported stylesheet neither errors nor resolves there. That is worth knowing for the tasks ahead: **the unit suite can never validate an `@apply`; only the browser gate can.** Do not add `pluginTailwindcss()` to `rstest.config.ts` in the belief that it is missing.

- [ ] **Step 10: Commit**

```bash
cd /home/dannyb/sources/PiFire
jj commit -m "$(cat <<'EOF'
build(web-react): add Tailwind v4 via the Rsbuild plugin, with no CSS change

Toolchain only: the plugin, the Biome CSS parser option, and the import in
theme.css. No rule is rewritten and no token has moved yet.

Preflight is deliberately not imported. Record the measured diff here:
<paste the baseline entries that moved under the full `@import "tailwindcss"`,
or "preflight measured inert, imported whole" if nothing moved>.

All 43 fidelity baselines unchanged.
EOF
)"
```

---

## Task 8: Move the tokens into `@theme`

**Files:**
- Modify: `web-react/src/theme.css` (the token block — now ~75 lines, not 31; re-measure)
- Modify: `web-react/src/themeTokens.test.ts` (already exists — the Theme.qml guard)

**Interfaces:**
- Consumes: Tailwind's `@theme` from Task 7.
- Produces the Tailwind token namespace every later `@apply` uses:
  - colours `--color-page`, `--color-card`, `--color-inset`, `--color-card-border`, `--color-text`, `--color-text-dim`, `--color-label`, `--color-probe-label`, `--color-setpoint`, `--color-ok`, `--color-warn`, `--color-danger`, `--color-track`, `--color-cooking`, `--color-igniter`, `--color-icon-idle`, `--color-dot-idle`, `--color-row-label`, `--color-accent`, `--color-accent-mid`, `--color-accent-1`, `--color-accent-2`, `--color-accent-ember`, `--color-accent-ice`, `--color-accent-crimson`, `--color-glow` → utilities `bg-page`, `text-text-dim`, `border-accent`, … (the full list is whatever `theme.css` declares — read it, this plan predates several of these)
  - radii `--radius-card`, `--radius-pill` → `rounded-card`, `rounded-pill`
  - easing `--ease-out-cubic` → `ease-out-cubic`
  - Every legacy name `theme.css` declares today (`--page`, `--card`, `--inset`, `--card-border`, `--text`, `--text-dim`, `--label`, `--probe-label`, `--setpoint`, `--ok`, `--warn`, `--danger`, `--track`, `--cooking`, `--igniter`, `--icon-idle`, `--dot-idle`, `--row-label`, `--card-radius`, `--pill-radius`, `--accent`, `--accent-mid`, `--accent-1`, `--accent-2`, `--accent-ember`, `--accent-ice`, `--accent-crimson`, `--glow`, `--anim-ms`, `--ease-out-cubic`) keeps resolving, so the `var(--…)` uses across the seven stylesheets are untouched by this task. The reconciliation raised that count well above the ~180 the spec measured; re-count rather than quoting it.

**How the accent switcher survives, and why the aliases point the way they do.** `@theme` emits its variables inside `@layer theme`. Unlayered author declarations beat every layered one regardless of specificity. So an unlayered `:root[data-accent="ice"] { --color-accent: #3cc7d0 }` overrides `@theme`'s ember value, and an unlayered `:root { --accent: var(--color-accent) }` then resolves to whatever won. Every existing `var(--accent)` in every stylesheet, and every Tailwind `bg-accent` utility (which compiles to `background-color: var(--color-accent)`), pick up the same value. Aliasing in the other direction — theme reading from legacy — would put a `var()` inside `@theme`, which Tailwind cannot resolve at build time for utilities that need a literal.

`--ease-out-cubic` needs no alias: `--ease-*` is a real Tailwind namespace, so the `@theme` name and the legacy name are the same string, and aliasing it to itself would be a cycle. `--anim-ms` has no Tailwind namespace and stays a plain custom property.

`@theme static` forces every declared variable to be emitted even if no generated utility references it. Without `static`, Tailwind tree-shakes unused theme variables — and `--color-glow` and `--color-accent-1`/`-2` are consumed only through legacy `var()` names, which Tailwind cannot see. They would vanish, the aliases would resolve to nothing, and the glow and gradient stops would silently disappear.

- [ ] **Step 1: Write the failing token test**

**`web-react/src/themeTokens.test.ts` ALREADY EXISTS** (it is the Theme.qml guard from the 2026-07-26 palette reconciliation). Do not overwrite it — its Theme.qml parser and its "no hardcoded Qt value outside theme.css" scan are the drift guard this migration relies on. Add the describes below ALONGSIDE what is there, and update its `MAPPING` so each CSS token resolves through its new `--color-*` name:

```ts
import { readFileSync } from "node:fs";
import { describe, expect, it } from "@rstest/core";

// The design tokens are shared with the Qt UI (display/qml/Theme.qml). A drift
// here changes the on-device dashboard's appearance by implication, not just the
// web one -- and neither codebase's tests would notice, because each is
// internally consistent with its own copy.

const css = readFileSync("src/theme.css", "utf8");
// rstest's cwd is web-react/, so the Qt theme is one level up.
const qml = readFileSync("../display/qml/Theme.qml", "utf8");

describe("theme.css tokens", () => {
  it("uses @theme static so unreferenced tokens are still emitted", () => {
    // Without `static`, Tailwind tree-shakes theme variables no generated
    // utility mentions. --color-glow and --color-accent-1/-2 are consumed only
    // through the legacy var(--glow) / var(--accent-1) names, which Tailwind
    // cannot see -- they would vanish and the glow would silently disappear.
    expect(css).toContain("@theme static {");
  });

  const TOKENS: Record<string, string> = {
    "--color-page": "#0c0a09",
    "--color-card": "#2c231a",
    "--color-inset": "#1c1712",
    "--color-text": "#f4ede2",
    "--color-text-dim": "#8a7f70",
    "--color-accent": "#ff8a2b",
    "--color-accent-mid": "#ff8a2b",
    "--color-accent-1": "#ffc24b",
    "--color-accent-2": "#ff5e1a",
    "--color-glow": "#ff7a1a",
    "--radius-card": "18px",
    "--radius-pill": "999px",
    "--ease-out-cubic": "cubic-bezier(0.33, 1, 0.68, 1)",
  };

  it("declares every token at its original value", () => {
    for (const [name, value] of Object.entries(TOKENS)) {
      expect(css, `${name} missing or changed`).toContain(`${name}: ${value};`);
    }
  });

  it("keeps the legacy names resolving, so the six stylesheets need no edit", () => {
    for (const [legacy, themed] of [
      ["--page", "--color-page"],
      ["--card", "--color-card"],
      ["--inset", "--color-inset"],
      ["--text", "--color-text"],
      ["--text-dim", "--color-text-dim"],
      ["--card-radius", "--radius-card"],
      ["--pill-radius", "--radius-pill"],
      ["--accent", "--color-accent"],
      ["--accent-mid", "--color-accent-mid"],
      ["--accent-1", "--color-accent-1"],
      ["--accent-2", "--color-accent-2"],
      ["--glow", "--color-glow"],
    ]) {
      expect(css, `${legacy} is not aliased to ${themed}`).toContain(`${legacy}: var(${themed});`);
    }
    // No Tailwind namespace for durations; stays a plain custom property.
    expect(css).toContain("--anim-ms: 250ms;");
    // --ease-* IS a Tailwind namespace, so the themed name and the legacy name
    // are the same string. Aliasing it to itself would be a var() cycle.
    expect(css).not.toContain("--ease-out-cubic: var(--ease-out-cubic)");
  });

  it("keeps all three accents overriding the THEMED name", () => {
    // The switcher works by attribute on :root. These rules are unlayered, so
    // they beat @theme's layered value; the --accent alias then follows.
    for (const [attr, accent, a1, a2, glow] of [
      ["ice", "#3cc7d0", "#7ef0d2", "#1f9fb8", "#2ec5d3"],
      ["crimson", "#ff6a5a", "#ff9f43", "#e11d48", "#ff5a4d"],
    ]) {
      const at = css.indexOf(`:root[data-accent="${attr}"]`);
      expect(at, `no rule for the ${attr} accent`).toBeGreaterThan(-1);
      const block = css.slice(at, css.indexOf("}", at));
      expect(block).toContain(`--color-accent: ${accent};`);
      expect(block).toContain(`--color-accent-1: ${a1};`);
      expect(block).toContain(`--color-accent-2: ${a2};`);
      expect(block).toContain(`--color-glow: ${glow};`);
    }
  });
});

describe("parity with display/qml/Theme.qml", () => {
  it("shares the seven values that are genuinely shared", () => {
    for (const [cssToken, qmlValue] of [
      ["--color-page", "#0c0a09"],
      ["--color-card", "#2c231a"],
      ["--color-inset", "#1c1712"],
      ["--color-text", "#f4ede2"],
      ["--color-accent", "#ff8a2b"],
    ]) {
      expect(css, `${cssToken} drifted from the Qt theme`).toContain(`${cssToken}: ${qmlValue};`);
      expect(qml, `${qmlValue} is no longer in Theme.qml`).toContain(`"${qmlValue}"`);
    }
    // The accent switcher's other two, from Theme.qml:32.
    expect(qml).toContain('"#3cc7d0"');
    expect(qml).toContain('"#ff6a5a"');
  });

  // RESOLVED 2026-07-26. Theme.qml is the source of truth and theme.css was
  // corrected to match it: --text-dim is #8a7f70, Qt's dim. The old assertion
  // here RECORDED the divergence and told nobody to fix it; that ruling was
  // reversed before this migration began.
  //
  // The live themeTokens.test.ts already parses Theme.qml and compares the
  // whole palette per accent, so this describe does not restate values. Keep
  // that file's checks working under the --color-* rename instead: its MAPPING
  // is CSS-token -> Qt-property, so each entry gains the themed name.
  it("keeps --text-dim equal to Qt's dim", () => {
    expect(css).toContain("--color-text-dim: #8a7f70;");
    expect(qml).toContain('dim:');
    expect(qml).toContain('"#8a7f70"');
  });
});
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test 2>&1 | grep -E 'themeTokens|"failedTests"'
```

Expected: the `theme.css tokens` describe fails on every assertion (there is no `@theme` block yet); the `parity` describe passes except for the `--color-*` names.

- [ ] **Step 3: Rewrite the token block**

Replace `web-react/src/theme.css` lines 1-31 (everything from the opening comment down to and including the `crimson` rule; **keep the `*`/`html`/`body` reset and everything below it untouched**) with:

```css
/* Tailwind v4, WITHOUT preflight -- see the migration commit for the measured
 * reason. The @layer statement is not decorative: without it the layers order
 * by first appearance, and `utilities` must lose to the unlayered pf-* rules
 * while still beating `theme`. */
@layer theme, base, components, utilities;
@import "tailwindcss/theme.css" layer(theme);
@import "tailwindcss/utilities.css" layer(utilities);

/* Design tokens. Values are shared with the Qt UI (display/qml/Theme.qml) and
 * are NOT ours to change -- a drift here alters the on-device dashboard by
 * implication. The palette was reconciled with Theme.qml on 2026-07-26 and
 * src/themeTokens.test.ts parses Theme.qml and fails on any divergence.
 *
 * `static` forces every variable to be emitted. Without it Tailwind
 * tree-shakes theme variables that no generated utility references, and
 * --color-glow / --color-accent-1 / --color-accent-2 are reached only through
 * the legacy var() names below -- which Tailwind cannot see. They would vanish
 * and the glow and gradient stops would silently disappear.
 *
 * Namespaces matter: --color-* generates bg-/text-/border-, --radius-*
 * generates rounded-, --ease-* generates ease-. A token in no namespace
 * generates no utility, which is why --anim-ms stays a plain property below. */
@theme static {
  --color-page: #0c0a09;
  --color-card: #2c231a;
  --color-inset: #1c1712;
  --color-text: #f4ede2;
  --color-text-dim: #8a7f70;

  /* Ember (default). Overridden per accent below. The full Qt palette
     (--color-card-border, --color-label, --color-probe-label, --color-setpoint,
     --color-ok, --color-warn, --color-danger, --color-track, --color-cooking,
     --color-igniter, --color-icon-idle, --color-dot-idle, --color-row-label and
     the three fixed --color-accent-ember/-ice/-crimson) belongs here too --
     re-read the live theme.css, which has them all. */
  --color-accent: #ff8a2b;
  --color-accent-mid: #ff8a2b;
  --color-accent-1: #ffc24b;
  --color-accent-2: #ff5e1a;
  --color-glow: #ff7a1a;

  --radius-card: 18px;
  --radius-pill: 999px;

  --ease-out-cubic: cubic-bezier(0.33, 1, 0.68, 1);
}

/* The bridge. Six stylesheets reference the ORIGINAL names in ~180 places and
 * are not rewritten by this change; the accent switcher still works by
 * attribute. Both keep working because these rules are UNLAYERED, and an
 * unlayered declaration beats anything in @layer theme whatever its
 * specificity -- so [data-accent="ice"] below wins over @theme's --color-accent,
 * and every var(--accent) resolves through this alias to it.
 *
 * The direction is deliberate: theme -> legacy, never legacy -> theme. Putting
 * var(--page) inside @theme would leave Tailwind without a literal for the
 * utilities that need one.
 *
 * --ease-out-cubic is absent from this list on purpose: --ease-* is a real
 * Tailwind namespace, so the themed name and the legacy name are the same
 * string and @theme already declares it. Aliasing it here would be a cycle. */
:root {
  --page: var(--color-page);
  --card: var(--color-card);
  --inset: var(--color-inset);
  --text: var(--color-text);
  --text-dim: var(--color-text-dim);
  --card-radius: var(--radius-card);
  --pill-radius: var(--radius-pill);
  --accent: var(--color-accent);
  --accent-mid: var(--color-accent-mid);
  --accent-1: var(--color-accent-1);
  --accent-2: var(--color-accent-2);
  --glow: var(--color-glow);

  /* No Tailwind namespace for durations; a plain custom property. */
  --anim-ms: 250ms;
}
:root[data-accent="ice"] {
  --color-accent: #3cc7d0;
  --color-accent-mid: #35c7d0;
  --color-accent-1: #7ef0d2;
  --color-accent-2: #1f9fb8;
  --color-glow: #2ec5d3;
}
:root[data-accent="crimson"] {
  --color-accent: #ff6a5a;
  --color-accent-mid: #ff5a4d;
  --color-accent-1: #ff9f43;
  --color-accent-2: #e11d48;
  --color-glow: #ff5a4d;
}
```

- [ ] **Step 4: Run the token test**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test 2>&1 | grep -E 'themeTokens|"failedTests"'
```

Expected: all `themeTokens` tests pass; `"failedTests": 0`.

- [ ] **Step 5: Prove `@theme static` actually emitted the unreferenced tokens**

This is the failure mode the test above can only assert *intent* about — the text says `static`, but the build has to honour it.

```bash
cd /home/dannyb/sources/PiFire/web-react
rm -rf dist && bun run build
grep -c -- '--color-glow' dist/static/css/*.css
grep -c -- '--color-accent-2' dist/static/css/*.css
```

Expected: `1` or more for each. **A `0` means `static` was not honoured** — in that case add the ember defaults explicitly to the unlayered `:root` block (`--color-accent: #ff8a2b; --color-accent-mid: #ff8a2b; --color-accent-1: #ffc24b; --color-accent-2: #ff5e1a; --color-glow: #ff7a1a;`), extend `themeTokens.test.ts` to require them there, and note the duplication in the commit message so the next person knows there are now two places holding these five values.

- [ ] **Step 6: Assert every baseline, on the default accent**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test:e2e:fidelity
```

Expected: green. Any colour that moved shows up as a `background-color` or `color` line in the style diff — this is precisely what the computed-style half of the baseline was added for, and a geometry-only gate would have passed a broken alias in silence.

- [ ] **Step 7: Assert all three accents**

The baselines were captured on Ember. The other two accents are switched by `[data-accent]` on `:root` (`AppPrefs.tsx`), so they can be checked by setting the attribute and re-reading the resolved variables rather than by capturing two more sets of baselines. Add this test to the end of `web-react/tests/e2e/chrome-fidelity.spec.ts`:

```ts
// The accent switcher, end to end. @theme declares --color-accent once; the two
// [data-accent] rules override it; the legacy --accent alias follows; every
// stylesheet's var(--accent) and every Tailwind bg-accent resolve to the same
// value. Four links, and a break in any of them is invisible on the default
// accent -- which is the only one the baselines cover.
test("all three accents resolve through the token bridge", async ({ page }) => {
  await stubApi(page);
  await page.goto("/");
  await expect(page.locator(".pf-shell")).toBeVisible({ timeout: 15000 });

  const seen = await page.evaluate(() => {
    const out: Record<string, { themed: string; legacy: string; glow: string }> = {};
    for (const accent of ["ember", "ice", "crimson"]) {
      document.documentElement.setAttribute("data-accent", accent);
      const cs = getComputedStyle(document.documentElement);
      out[accent] = {
        themed: cs.getPropertyValue("--color-accent").trim(),
        legacy: cs.getPropertyValue("--accent").trim(),
        glow: cs.getPropertyValue("--glow").trim(),
      };
    }
    document.documentElement.setAttribute("data-accent", "ember");
    return out;
  });

  expect(seen.ember.themed).toBe("#ff8a2b");
  expect(seen.ice.themed).toBe("#3cc7d0");
  expect(seen.crimson.themed).toBe("#ff6a5a");
  // The alias follows the override, which is the whole mechanism.
  for (const a of ["ember", "ice", "crimson"]) {
    expect(seen[a].legacy, `--accent did not follow --color-accent on ${a}`).toBe(seen[a].themed);
    expect(seen[a].glow, `--glow is empty on ${a} -- @theme static did not emit it`).not.toBe("");
  }
  // Three distinct accents, not one repeated.
  expect(new Set(["ember", "ice", "crimson"].map((a) => seen[a].legacy)).size).toBe(3);
});
```

Run it:

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test:e2e --project=fidelity-chrome
```

Expected: green, including the new test.

- [ ] **Step 8: Run the four gates and commit**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run typecheck && bun run typecheck:e2e && bun run lint && bun run test && bun run gen:types:check
cd /home/dannyb/sources/PiFire
jj commit -m "$(cat <<'EOF'
refactor(web-react): make @theme the definition of the design tokens

Tailwind now owns the token namespace (--color-*, --radius-*, --ease-*). The
original names survive as unlayered :root aliases pointing AT the themed ones,
so the six stylesheets' ~180 var() references and the [data-accent] switcher are
untouched.

Values are byte-identical with the reconciled palette (see the 2026-07-26
Theme.qml reconciliation). src/themeTokens.test.ts, which already parses
Theme.qml, is extended to cover the new --color-* names.

All 43 fidelity baselines unchanged; all three accents asserted.
EOF
)"
```

---

## The conversion rules (Tasks 9-14)

Read this once; every conversion task refers back to it.

### `@reference` is mandatory in every file except `theme.css`

The six component stylesheets are imported from `.tsx` (`import "./shell.css";`), so Rspack hands each to the Tailwind loader as its own module. A file that does not import Tailwind has no theme, and `@apply` in it fails the build with `Cannot apply unknown utility class`. Every converted file therefore starts with:

```css
@reference "../../theme.css";
```

All six live at `src/components/<surface>/`, so the path is `../../theme.css` in all six. `@reference` loads the file **for its theme only and emits no CSS**, so this does not duplicate Tailwind's output into six bundles.

### The safe-utility table — the only substitutions permitted

Use a utility **only when its expansion is exactly the declarations being replaced**. Everything else stays raw CSS; the end state is deliberately not "pure Tailwind" and a stylesheet layer remains.

| Declaration | Utility |
|---|---|
| `display: flex \| grid \| block \| inline-block \| none` | `flex` / `grid` / `block` / `inline-block` / `hidden` |
| `flex-direction: column \| row` | `flex-col` / `flex-row` |
| `flex-wrap: wrap \| nowrap` | `flex-wrap` / `flex-nowrap` |
| `align-items: center \| flex-start \| flex-end \| stretch \| baseline` | `items-center` / `items-start` / `items-end` / `items-stretch` / `items-baseline` |
| `justify-content: center \| space-between \| flex-start \| flex-end` | `justify-center` / `justify-between` / `justify-start` / `justify-end` |
| `position: relative \| absolute \| fixed \| sticky \| static` | `relative` / `absolute` / `fixed` / `sticky` / `static` |
| `inset: 0` | `inset-0` |
| `overflow: hidden \| auto \| scroll \| visible` | `overflow-hidden` / `overflow-auto` / `overflow-scroll` / `overflow-visible` |
| `width: 100%` / `height: 100%` | `w-full` / `h-full` |
| `font-weight: 400\|500\|600\|700\|800` | `font-normal` / `font-medium` / `font-semibold` / `font-bold` / `font-extrabold` |
| `pointer-events: none` | `pointer-events-none` |
| `white-space: nowrap` | `whitespace-nowrap` |
| `text-align: center \| left \| right` | `text-center` / `text-left` / `text-right` |
| `cursor: pointer` | `cursor-pointer` |
| `z-index: <n>` | `z-<n>` |
| `background: var(--card)` (and the other five colour tokens) | `bg-card`, `bg-page`, `bg-inset`, `bg-accent`, `bg-accent-1`, `bg-accent-2`, `bg-glow` |
| `color: var(--text)` / `var(--text-dim)` / `var(--accent)` | `text-text` / `text-text-dim` / `text-accent` |
| `border-radius: var(--card-radius)` / `var(--pill-radius)` | `rounded-card` / `rounded-pill` |
| Any literal length | an **arbitrary value**: `gap: 14px` → `gap-[14px]`, `padding: 8px 10px` → `px-[10px] py-[8px]`, `border-radius: 10px` → `rounded-[10px]`, `min-width: 140px` → `min-w-[140px]` |
| `font-size: 12px` | `text-[12px]` — **never a named size** |

### The five things that are forbidden, and why

1. **Never a named `text-*` size** (`text-xs`, `text-sm`, `text-lg`, …). In v4 those emit `font-size` **and** `line-height` together. The stylesheets set `font-size` alone in dozens of places and take `line-height` from an ancestor; adding one silently reflows the text inside a box whose outer geometry never moves. `text-[12px]` emits font-size only.
2. **Never bare `border`.** It emits `border-width: 1px` and relies on preflight's `border-style: solid` — which is not imported. Use `border-[1px] border-solid border-[color]`, or leave the declaration as raw CSS.
3. **Never named `rounded-*`** other than the two theme radii (`rounded-card`, `rounded-pill`). `rounded-lg` is 8px in v4 and 10px in v3; an arbitrary value cannot drift.
4. **Never `space-x-*` / `space-y-*` / `divide-*`.** They generate `> * + *` child selectors, which changes the rule's *selector*, not its declarations, and can trip `noDescendingSpecificity` besides.
5. **Never touch a `color-mix()`, a `var(--pf-*)` size token, a `@keyframes`, a `transition` or a `box-shadow`.** Leave them as raw CSS. There is no utility whose expansion is exactly `background: color-mix(in srgb, var(--accent) 16%, transparent)`, and the 15 `--pf-*` size tokens are pinned to the character by `dashboardStyles.test.tsx`.

### Facts about `@apply` the implementer will otherwise learn the hard way

- `@apply` **inlines the utility's declarations at the rule's own position**, so specificity and source order are unchanged. That is what makes the conversion invisible to the cascade.
- `@apply` works inside `@media` blocks.
- `@apply` does **not** depend on Tailwind's content scanning. It works whether or not any JSX ever mentions the utility.
- **The unit suite cannot validate an `@apply`.** Measured: rstest imports CSS without processing it, so a stylesheet full of `@apply` neither errors nor resolves under `bun run test`. Only `bun run test:e2e:fidelity` can tell you whether a conversion was correct.

### The per-task loop

Every conversion task is the same five moves, and the deliverable is always "the file is converted and all 43 baselines are byte-identical":

1. Add the `@reference` line.
2. Convert rules **top to bottom in one pass**, applying only the table above.
3. `bun run build` — a build failure here is `Cannot apply unknown utility class`, meaning either the `@reference` is missing or the utility is not real.
4. `bun run test && bun run test:e2e:fidelity` — the class-coverage guard (which now understands `@apply` bodies, Task 6) plus the full fidelity gate.
5. `git diff --stat tests/e2e/baselines/` must print **nothing**. If it does not, revert the specific rule that moved and leave it as raw CSS; a baseline is never edited to accommodate a conversion.

---

## Task 9: Convert `historyChart.css` (61 lines)

**Files:**
- Modify: `web-react/src/components/history/historyChart.css`

**Interfaces:**
- Consumes: `--color-*`, `--radius-*` from Task 8's `@theme static`.
- Produces: nothing new. `.pf-history-chart` and `.pf-history-tip` keep their names and their rules' positions.

The smallest file, and the one that proves the loop works before anything expensive is at risk. Two `pf-*` classes and five descendant rules. Note that `.pf-history-tip`'s children are single-letter element classes (`.t`, `.r`, `.r i`, `.r b`) written by `tooltipPlugin.ts` — `HistoryChart.test.tsx:101` pins that DOM structure, so the selectors must not change.

- [ ] **Step 1: Record the starting point**

```bash
cd /home/dannyb/sources/PiFire/web-react
wc -l src/components/history/historyChart.css
```

Expected: `61`. If it is not, re-read the file before converting — someone has changed it since this plan was written.

- [ ] **Step 2: Convert the file**

Replace `web-react/src/components/history/historyChart.css` in full:

```css
@reference "../../theme.css";

/* HistoryChart -- uPlot host + the cursor-following tooltip plugin markup.
   Colours reuse the app's design tokens from src/theme.css rather than
   introducing new ones.

   The single-letter child classes (.t, .r, .r i, .r b) are written by
   helpers/history/tooltipPlugin.ts and pinned by HistoryChart.test.tsx --
   selectors here must not change, only declarations. */

.pf-history-chart {
  @apply relative w-full;
}

/* The font shorthand is kept RAW, not split into @apply utilities. It resets
   line-height to `normal` as part of its own expansion, and the equivalent
   Tailwind named size (text-xs) would set a line-height of its own instead --
   two different reflows of the same box. */
.pf-history-chart .u-legend {
  @apply text-text-dim;
  font:
    600 12px "Barlow",
    system-ui,
    sans-serif;
}

.pf-history-tip {
  @apply pointer-events-none absolute z-10 rounded-[10px] bg-card px-[10px] py-[8px] text-text;
  border: 1px solid rgba(255, 255, 255, 0.14);
  font:
    600 12px "Barlow",
    system-ui,
    sans-serif;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
  min-width: 140px;
}

.pf-history-tip .t {
  @apply text-[11px] text-text-dim;
  margin-bottom: 4px;
}

.pf-history-tip .r {
  @apply flex items-center gap-[6px] whitespace-nowrap;
  padding: 2px 0;
}

.pf-history-tip .r i {
  @apply inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: 0 0 auto;
}

.pf-history-tip .r b {
  @apply font-bold text-text;
  margin-left: auto;
}
```

Three deliberate non-conversions to note while reviewing: `border: 1px solid rgba(...)` stays raw (bare `border` needs preflight's `border-style`); `border-radius: 50%` stays raw (no percentage radius utility whose expansion is exactly that); `flex: 0 0 auto` stays raw (`flex-none` expands to `flex: none`, which is `0 0 auto` in the spec but a different declaration string, and the computed-style baseline compares strings).

- [ ] **Step 3: Build**

```bash
cd /home/dannyb/sources/PiFire/web-react
rm -rf dist && bun run build
```

Expected: succeeds. `Cannot apply unknown utility class` means either the `@reference` line is missing or a utility in the table above was mistyped.

- [ ] **Step 4: Run the unit suite**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test 2>&1 | grep -E '"failedTests"|HistoryChart|styleCoverage'
```

Expected: `"failedTests": 0`. The repo-wide coverage guard from Task 6 now has its first real exercise: `.pf-history-chart` and `.pf-history-tip` still count as declared even though their bodies are `@apply` plus raw declarations.

- [ ] **Step 5: Assert the full fidelity gate**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test:e2e:fidelity
git diff --stat tests/e2e/baselines/
```

Expected: green; the `git diff --stat` prints **nothing**. The history page's baselines (`history-1280x720.json`, `history-390x844.json`) include `.pf-history-chart`, and the chart's own colours and radii are in the computed-style half.

- [ ] **Step 6: Run the four gates and commit**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run typecheck && bun run typecheck:e2e && bun run lint && bun run test && bun run gen:types:check
cd /home/dannyb/sources/PiFire
jj commit -m 'refactor(web-react): author historyChart.css with @apply, no visual change'
```

---

## Task 10: Convert `shell.css` (315 lines)

**Files:**
- Modify: `web-react/src/components/shell/shell.css`

**Interfaces:**
- Consumes: `--color-*`, `--radius-*` from Task 8.
- Produces: nothing new. All 29 `pf-*` selectors keep their names, their positions and their one `@media` block.

The app chrome: `.pf-shell`, the navbar (`.pf-nav*`, 12 rules), the timer strip (`.pf-timer*`, 10 rules), the banner strip (`.pf-banner*`, 4 rules) and `.pf-modal-scrim-fixed`. Half of it never renders under the page baselines — that is what Task 5's `chrome-fidelity.spec.ts` probes are for, and they are the assertion that matters most in this task.

**Two things in this file need naming before you touch it.** `Banners.tsx:26` builds `pf-banner--${it.level}` at runtime and `NavBar.tsx:79,100,115` builds `pf-nav-timer ${on} ${running}` and `pf-nav-link ${active}`; Tailwind's content scanner cannot see any of them. That is harmless while they remain hand-written rules — `@apply` does not depend on content scanning — and it becomes a silently-missing style the moment anyone converts them to utilities in JSX, which this migration does not do. Put that in a comment where those rules live. Second, four rules use `color-mix(in srgb, var(--accent) …)`: leave every one of them raw.

- [ ] **Step 1: Record the starting point**

```bash
cd /home/dannyb/sources/PiFire/web-react
wc -l src/components/shell/shell.css
grep -c 'color-mix' src/components/shell/shell.css
grep -n '@media' src/components/shell/shell.css
```

Expected: `315` lines, `4` `color-mix` uses, one `@media` at roughly line 240. Re-measure rather than trusting these.

- [ ] **Step 2: Add the `@reference` line and the dynamic-class note**

At the very top of `web-react/src/components/shell/shell.css`:

```css
@reference "../../theme.css";
```

Immediately above the first `.pf-banner` rule:

```css
/* RUNTIME-CONSTRUCTED CLASS NAMES. Banners.tsx:26 builds
   `pf-banner pf-banner--${it.level}` and NavBar.tsx builds
   `pf-nav-timer ${on} ${running}` and `pf-nav-link ${active}` from state.
   Tailwind's content scanner cannot see any of them.

   That is harmless as long as these stay hand-written rules -- @apply resolves
   at build time from the theme and does not depend on content scanning at all.
   It becomes a silently-missing style the moment someone moves them into
   utility strings in JSX. Do not.

   tests/e2e/chrome-fidelity.spec.ts probes all three banner kinds
   synthetically, because none of them renders under the demo fixture. */
```

- [ ] **Step 3: Convert, rule by rule, top to bottom**

Apply the safe-utility table. The shapes that recur in this file, with their exact conversions:

```css
/* before */
.pf-shell {
  display: flex;
  flex-direction: column;
  height: 100%;
}
/* after */
.pf-shell {
  @apply flex h-full flex-col;
}
```

```css
/* before */
.pf-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 18px;
  background: var(--card);
}
/* after */
.pf-nav {
  @apply flex items-center justify-between gap-[12px] bg-card px-[18px] py-0;
}
```

```css
/* before */
.pf-nav-link.active {
  color: var(--accent);
  font-weight: 600;
}
/* after */
.pf-nav-link.active {
  @apply font-semibold text-accent;
}
```

```css
/* LEFT ALONE -- no utility expands to exactly this. */
.pf-nav-item.active .pf-nav-link {
  background: color-mix(in srgb, var(--accent) 16%, transparent);
}
```

Work through all 29 selectors this way. Anything not in the table stays exactly as it is.

- [ ] **Step 4: Build**

```bash
cd /home/dannyb/sources/PiFire/web-react
rm -rf dist && bun run build
```

Expected: succeeds.

- [ ] **Step 5: Assert the chrome probes first — they cover what the pages cannot**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test:e2e --project=fidelity-chrome
```

Expected: green. The three banner kinds, the timer bar, the timer readout, the timer button and the modal scrim are all in this file and in no page baseline; if this project is green and the pages are green, the file is covered.

- [ ] **Step 6: Assert the full gate**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test:e2e:fidelity
git diff --stat tests/e2e/baselines/
```

Expected: green; no baseline diff. Every page baseline carries the eleven `SHELL` landmarks, so a navbar regression fails 19 pages at once rather than one.

- [ ] **Step 7: Run the four gates and commit**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run typecheck && bun run typecheck:e2e && bun run lint && bun run test && bun run gen:types:check
cd /home/dannyb/sources/PiFire
jj commit -m 'refactor(web-react): author shell.css with @apply, no visual change'
```

---

## Task 11: Convert `settings.css` (344 lines)

**Files:**
- Modify: `web-react/src/components/settings/settings.css`

**Interfaces:**
- Consumes: `--color-*`, `--radius-*` from Task 8.
- Produces: nothing new. All 41 `pf-*` selectors keep their names and positions.

**This file styles two pages, not one.** `HistoryPage.tsx` uses `.pf-settings`, `.pf-settings-content`, `.pf-settings-actions`, `.pf-settings-hint`, `.pf-section`, `.pf-section-title` and `.pf-section-body` — all declared here, none declared in `historyChart.css`. So this task's gate is 11 settings baselines **plus** the two history ones, at both viewports: 26 baseline files.

**And one of its rules is overridden from another file.** `wizard.css` carries `.pf-wizard .pf-probes-card`, an override of the `.pf-probes-card` rule declared here (`wizardStyles.test.ts:115` pins that scoping). Changing the declarations in this file is safe; changing the *selector* would break the override silently. Leave selectors alone.

- [ ] **Step 1: Record the starting point**

```bash
cd /home/dannyb/sources/PiFire/web-react
wc -l src/components/settings/settings.css
grep -c 'color-mix' src/components/settings/settings.css
grep -n '@media' src/components/settings/settings.css
grep -n 'pf-probes-card' src/components/settings/settings.css src/components/wizard/wizard.css
```

Expected: `344` lines, `2` `color-mix` uses, one `@media`, and `.pf-probes-card` declared here with a `.pf-wizard .pf-probes-card` override in `wizard.css`.

- [ ] **Step 2: Add the `@reference` line and the cross-surface note**

At the very top of `web-react/src/components/settings/settings.css`:

```css
@reference "../../theme.css";
```

Immediately below it:

```css
/* This stylesheet is NOT settings-only.
 *
 * HistoryPage.tsx renders .pf-settings, .pf-settings-content,
 * .pf-settings-actions, .pf-settings-hint, .pf-section, .pf-section-title and
 * .pf-section-body -- every one of them declared here and nowhere else. Any
 * change to those rules moves /history too, so the gate for this file is 13
 * pages, not 11.
 *
 * .pf-probes-card is also overridden from outside, by `.pf-wizard
 * .pf-probes-card` in wizard.css (pinned by wizardStyles.test.ts). Its
 * DECLARATIONS are safe to convert; its SELECTOR is not -- a rename here breaks
 * that override with no error anywhere. */
```

- [ ] **Step 3: Convert, rule by rule, top to bottom**

The recurring shapes in this file:

```css
/* before */
.pf-settings {
  display: flex;
  height: 100%;
  overflow: hidden;
}
/* after */
.pf-settings {
  @apply flex h-full overflow-hidden;
}
```

```css
/* before */
.pf-section-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 10px;
}
/* after -- text-[15px], NEVER text-sm: a named size would add a line-height
   this rule has never had and reflow every section heading. */
.pf-section-title {
  @apply mb-[10px] text-[15px] font-bold text-text;
}
```

```css
/* before */
.pf-input {
  background: var(--inset);
  color: var(--text);
  border-radius: 8px;
  padding: 6px 10px;
}
/* after -- the border declaration, if the rule has one, stays raw: bare
   `border` depends on preflight's border-style, which is not imported. */
.pf-input {
  @apply rounded-[8px] bg-inset px-[10px] py-[6px] text-text;
}
```

```css
/* LEFT ALONE, both occurrences. */
.pf-settings-link.active {
  background: color-mix(in srgb, var(--accent) 16%, transparent);
}
```

- [ ] **Step 4: Build**

```bash
cd /home/dannyb/sources/PiFire/web-react
rm -rf dist && bun run build
```

Expected: succeeds.

- [ ] **Step 5: Assert the settings and history baselines specifically**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test:e2e --project=fidelity-pages -g 'settings-|history'
```

Expected: 26 landmark tests plus their overflow siblings, all green. Running the narrow set first makes a failure readable — 13 pages' worth of style diffs at once is not.

- [ ] **Step 6: Assert the full gate**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test:e2e:fidelity
git diff --stat tests/e2e/baselines/
```

Expected: green; no baseline diff.

- [ ] **Step 7: Run the four gates and commit**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run typecheck && bun run typecheck:e2e && bun run lint && bun run test && bun run gen:types:check
cd /home/dannyb/sources/PiFire
jj commit -m 'refactor(web-react): author settings.css with @apply, no visual change'
```

---

## Task 12: Convert `pellets.css` (137 lines)

**Files:**
- Modify: `web-react/src/components/pellets/pellets.css`

**Interfaces:**
- Consumes: `--color-*`, `--radius-*` from Task 8.
- Produces: nothing new. All 26 `pf-pellets-*` selectors keep their names and positions.

**This file is not in the design spec at all** — the `/pellets` page landed after the spec was written (commits `170322ab`…`29dc08e3`). It is in scope for the fidelity gate regardless.

**Its gate is weaker than every other file's, and you need to know how.** `/pellets` reads the whole pellet database off `socket_pellet_data`; the demo server opens no socket, so `PelletsPage.tsx:60` renders only the "Loading pellet database…" branch there. The baseline therefore runs against the **app server and the live backend**, guarded by the store fingerprint from Task 5 — and it **skips**, loudly, when the machine's store is a different shape. A skip is not a pass. Two consequences for this task: (1) confirm the pellets tests actually **ran** by reading the Playwright summary, and (2) the three meter modifiers (`--ok`, `--warn`, `--low`, built at runtime in `CurrentLoadCard.tsx:18-20`) only ever render one at a time, so `chrome-fidelity.spec.ts`'s probes are what covers the other two.

- [ ] **Step 1: Record the starting point, and check the backend is up**

```bash
cd /home/dannyb/sources/PiFire/web-react
wc -l src/components/pellets/pellets.css
grep -c 'color-mix' src/components/pellets/pellets.css
curl -sf http://localhost:5000/api/pellets >/dev/null && echo "backend up" || echo "START control.py + gunicorn"
cat tests/e2e/baselines/pellets-fingerprint.json
```

Expected: `137` lines; `backend up`; the fingerprint printed. If the fingerprint no longer matches this machine's store the pellets gate will skip and this task has no gate at all — restore the store or re-run Task 5's capture on the *pre-Tailwind* commit, never on the current tree.

- [ ] **Step 2: Add the `@reference` line and the runtime-modifier note**

At the very top of `web-react/src/components/pellets/pellets.css`:

```css
@reference "../../theme.css";
```

Immediately above the first `.pf-pellets-meter--` rule:

```css
/* RUNTIME-CONSTRUCTED CLASS NAMES. CurrentLoadCard.tsx:18-20 picks one of
   pf-pellets-meter--ok / --warn / --low from the hopper level, so only one of
   the three ever renders and a page baseline can only ever cover that one.
   Tailwind's content scanner cannot see any of them either.

   Harmless while these stay hand-written rules; a silently-missing style the
   moment they move into utility strings in JSX. tests/e2e/chrome-fidelity.spec.ts
   probes all three synthetically for exactly this reason. */
```

- [ ] **Step 3: Convert, rule by rule, top to bottom**

The recurring shapes:

```css
/* before */
.pf-pellets {
  display: grid;
  gap: 14px;
  padding: 14px;
  overflow: hidden;
  height: 100%;
}
/* after */
.pf-pellets {
  @apply grid h-full gap-[14px] overflow-hidden p-[14px];
}
```

```css
/* before */
.pf-pellets-card {
  background: var(--card);
  border-radius: var(--card-radius);
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
}
/* after -- rounded-card, not rounded-lg: the theme radius is 18px and every
   named Tailwind radius is something else. */
.pf-pellets-card {
  @apply flex flex-col rounded-card bg-card px-[14px] py-[12px];
}
```

```css
/* before */
.pf-pellets-card-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-dim);
}
/* after -- text-[13px], NEVER text-sm. */
.pf-pellets-card-title {
  @apply text-[13px] font-bold text-text-dim;
}
```

`grid-template-columns`, any `color-mix()`, and the meter's width/transition declarations stay raw.

- [ ] **Step 4: Build**

```bash
cd /home/dannyb/sources/PiFire/web-react
rm -rf dist && bun run build
```

Expected: succeeds.

- [ ] **Step 5: Assert the pellets gate, and confirm it did not skip**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test:e2e --project=fidelity-pellets --project=fidelity-chrome 2>&1 | tail -12
```

Expected: `4 passed` for the two projects' tests with **`0 skipped`**. A line reading `pellet store differs from the baseline's` means the gate is silent and this task is unverified — fix the store or the fingerprint before continuing.

- [ ] **Step 6: Assert the full gate**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test:e2e:fidelity
git diff --stat tests/e2e/baselines/
```

Expected: green, no skips, no baseline diff.

- [ ] **Step 7: Run the four gates and commit**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run typecheck && bun run typecheck:e2e && bun run lint && bun run test && bun run gen:types:check
cd /home/dannyb/sources/PiFire
jj commit -m 'refactor(web-react): author pellets.css with @apply, no visual change'
```

---

## Task 13: Convert `wizard.css` (628 lines)

**Files:**
- Modify: `web-react/src/components/wizard/wizard.css`

**Interfaces:**
- Consumes: `--color-*`, `--radius-*` from Task 8.
- Produces: nothing new. All 54 `pf-*` selectors keep their names and positions, including `.pf-wizard .pf-probes-card` and the `@media (prefers-reduced-motion: reduce)` block.

**Three assertions already constrain this file and will fail if you drift.** `wizardStyles.test.ts` requires (1) `.pf-wizard .pf-probes-card` to exist verbatim as a scoped override of `settings.css`'s rule; (2) a `@media (prefers-reduced-motion: reduce)` block containing `.pf-install-progress-bar` and the literal text `animation: none`; (3) every wizard-owned class — matching `/^pf-(wizard|module|install|discovery|port-form|device-form|form-actions|probes-table|btn-primary)/` — to be declared in *this file*, not borrowed from another surface's stylesheet. `wizard-layout.spec.ts` additionally asserts in a real engine that the reduced-motion rule *wins the cascade*, that `.pf-wizard-modal` resolves to `position: fixed` with a spread box-shadow scrim, and that `.pf-btn` gets a wizard treatment overriding `dashboard.css`'s 25px default.

The reduced-motion rule uses `animation: none !important` in `dashboard.css` — `biome.jsonc` turns `complexity/noImportantStyles` off globally for exactly that reason. Do not attempt to express `!important` through `@apply`; leave any `!important` declaration raw.

- [ ] **Step 1: Record the starting point and the constraints**

```bash
cd /home/dannyb/sources/PiFire/web-react
wc -l src/components/wizard/wizard.css
grep -c 'color-mix' src/components/wizard/wizard.css
grep -n '@media\|@keyframes\|!important\|pf-probes-card' src/components/wizard/wizard.css
```

Expected: `628` lines, `4` `color-mix` uses, one `@media (prefers-reduced-motion: reduce)`, one `@keyframes`, and the `.pf-wizard .pf-probes-card` rule.

- [ ] **Step 2: Add the `@reference` line and the constraints note**

At the very top of `web-react/src/components/wizard/wizard.css`:

```css
@reference "../../theme.css";
```

Immediately below it:

```css
/* Four things in this file are asserted from outside and must not drift:
 *
 *   - `.pf-wizard .pf-probes-card` -- a SCOPED override of the rule
 *     settings.css declares. wizardStyles.test.ts matches that selector as
 *     literal text.
 *   - the `@media (prefers-reduced-motion: reduce)` block, which must contain
 *     `.pf-install-progress-bar` and the text `animation: none`.
 *     wizard-layout.spec.ts then checks in a real engine that it WINS the
 *     cascade, which a text check cannot see.
 *   - every pf-(wizard|module|install|discovery|port-form|device-form|
 *     form-actions|probes-table|btn-primary)* class, which must be declared
 *     HERE, not borrowed from another surface.
 *   - `.pf-btn`, whose wizard treatment overrides dashboard.css's 25px default.
 *
 * Declarations are free to convert. Selectors, @media preludes and !important
 * are not: @apply cannot carry !important, so leave those declarations raw. */
```

- [ ] **Step 3: Convert, rule by rule, top to bottom**

The recurring shapes:

```css
/* before */
.pf-wizard {
  position: fixed;
  inset: 0;
  display: flex;
  flex-direction: column;
  background: var(--page);
  color: var(--text);
}
/* after */
.pf-wizard {
  @apply fixed inset-0 flex flex-col bg-page text-text;
}
```

```css
/* before */
.pf-wizard-content {
  flex: 1;
  overflow: auto;
  padding: 20px 24px;
}
/* after -- `flex: 1` stays raw: Tailwind's `flex-1` expands to
   `flex: 1 1 0%`, a different declaration string, and the computed-style
   baseline compares strings. */
.pf-wizard-content {
  @apply overflow-auto px-[24px] py-[20px];
  flex: 1;
}
```

```css
/* before */
.pf-module-details {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 0 0 150px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  min-height: 146px;
}
/* after -- border-top stays raw (no preflight border-style), and the long
   comment above this rule explaining why it is a stack with a fixed gutter
   rather than a grid must be preserved verbatim. */
.pf-module-details {
  @apply relative flex flex-col gap-[10px] pt-[14px] pr-0 pb-0 pl-[150px];
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  min-height: 146px;
}
```

```css
/* LEFT ALONE. */
.pf-module-card.selected {
  background: color-mix(in srgb, var(--accent) 18%, transparent);
  border-color: color-mix(in srgb, var(--accent) 55%, transparent);
}
```

Every explanatory comment in this file is preserved. They record decisions that were paid for (the `:not(:has(.pf-module-image))` gutter rule, the modal's box-shadow scrim, the 132px `object-fit: contain` photo box) and a conversion is not a licence to drop them.

- [ ] **Step 4: Build**

```bash
cd /home/dannyb/sources/PiFire/web-react
rm -rf dist && bun run build
```

Expected: succeeds.

- [ ] **Step 5: Run the wizard's own text guards before the browser gate**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test 2>&1 | grep -E 'wizard stylesheet coverage|"failedTests"'
```

Expected: `"failedTests": 0`. This is the first task where the `@apply` fix from Task 6 is load-bearing on a large scale — 54 classes whose rules are now `@apply` bodies. If `has a non-empty CSS rule for every pf-* class the wizard uses` fails here, the guard is reading `@apply` bodies wrong, not the stylesheet.

- [ ] **Step 6: Assert the wizard baselines, then everything**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test:e2e --project=fidelity-pages -g 'wizard-'
bun run test:e2e --project=app -g 'wizard'
bun run test:e2e:fidelity
git diff --stat tests/e2e/baselines/
```

Expected: the 12 wizard baseline tests green; `wizard.spec.ts` and `wizard-layout.spec.ts` green (these run against the live backend and are the ones that check the reduced-motion rule and the modal probe in a real engine); the full gate green; no baseline diff.

- [ ] **Step 7: Run the four gates and commit**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run typecheck && bun run typecheck:e2e && bun run lint && bun run test && bun run gen:types:check
cd /home/dannyb/sources/PiFire
jj commit -m 'refactor(web-react): author wizard.css with @apply, no visual change'
```

---

## Task 14: Convert `dashboard.css` (1,188 lines)

**Files:**
- Modify: `web-react/src/components/dashboard/dashboard.css`

**Interfaces:**
- Consumes: `--color-*`, `--radius-*` from Task 8.
- Produces: nothing new. All 101 `pf-*` selectors, the 15 `--pf-*` size tokens, the six `@media` blocks and the nine `@keyframes` keep their names, values and positions.

The largest file and the most heavily constrained. `dashboardStyles.test.tsx` is 335 lines of text assertions over this one stylesheet, and every one of them can be broken by a careless conversion.

**The seven constraints, stated so they are not discovered one failure at a time.**

1. **The 15 size tokens are pinned to the character.** `--pf-header-h: 58px`, `--pf-probecol-w: 298px`, `--pf-col-w: 300px`, `--pf-gauge-size: 392px`, `--pf-gauge-ring: 360px`, `--pf-gauge-num: 112px`, `--pf-gauge-unit: 40px`, `--pf-probe-temp: 66px`, `--pf-probe-unit: 26px`, `--pf-probe-name: 15px`, `--pf-btn-font: 25px`, `--pf-btn-h: 82px`, `--pf-cook-val: 26px`, `--pf-pill-val: 24px`, `--pf-hopper-val: 34px`. The test asserts each appears as `${name}: ${value};` in the root rule, that each is consumed by a `var(${name}` somewhere outside it, and that **exactly 15** are declared outside a media query. Do not convert a token declaration, and do not convert a `var(--pf-*)` consumer into an arbitrary value.
2. **Six rules must keep a literal shape** the test matches by regex: `.pf-dash-cookrow { … height: 52px … }`, `.pf-dash-pills { … height: 64px … }`, `.pf-dash-header { … height: var(--pf-header-h) … }`, `.pf-dash-probecol { … width: var(--pf-probecol-w) … }`, `.pf-dash-rightcol { … width: var(--pf-col-w) … }`, `.pf-dash-controls { … height: var(--pf-btn-h) … }`. An `@apply h-[52px]` in place of `height: 52px` fails the regex.
3. **`.pf-btn` must keep `font-size: var(--pf-btn-font, 25px)`** — with the fallback, asserted as literal text. It is shared with the wizard and settings surfaces, which never see the dashboard root and so never see the token.
4. **No size token may sit inside a shorthand.** Two tests enforce it: no rule may combine `font:` with `font-variant-numeric`, and no line may be `font: … var(--pf-…)`. A `var()` inside a shorthand makes the whole declaration pending-substitution — it expands after the cascade and resets every longhand it owns, including `font-variant-numeric`, so the clock silently loses its tabular figures while every landmark box stays exactly where it was. Found by the fidelity screenshot, which is precisely the class of regression a landmark gate cannot see. **Never convert a font longhand back into a shorthand.**
5. **`.pf-dash` must keep `overflow: hidden` and `position: relative`**, and `.pf-fit` must keep `position: fixed` and `inset: 0` — all four matched as literal text inside their own rule.
6. **Both breakpoints must remain as literal text:** `@media (max-width: 1279px)`, `@media (max-width: 719px)`, and `@media (min-width: 1280px)` (which must equal `FIT_QUERY` in `helpers/dashboard/hooks.ts`). The phone block must set `--pf-btn-h` to at least 44px.
7. **`animation: none !important`** in the reduce-motion path stays raw; `@apply` cannot carry `!important`, and `biome.jsonc` disables `noImportantStyles` specifically for it.

- [ ] **Step 1: Record the starting point and re-read the constraints from the test**

```bash
cd /home/dannyb/sources/PiFire/web-react
wc -l src/components/dashboard/dashboard.css
grep -cE '^\s*--pf-[a-z0-9-]+\s*:' src/components/dashboard/dashboard.css
grep -oE '^\s*--pf-[a-z0-9-]+' src/components/dashboard/dashboard.css | tr -d ' ' | sort -u | wc -l
grep -n '@media\|@keyframes\|!important' src/components/dashboard/dashboard.css
```

Expected: `1188` lines; `30` token declaration lines over `15` distinct names (15 desktop defaults plus their redeclarations inside the two breakpoints — note this contradicts the design spec's claim of 71); six `@media`; nine `@keyframes`.

- [ ] **Step 2: Add the `@reference` line and the constraints note**

At the very top of `web-react/src/components/dashboard/dashboard.css`:

```css
@reference "../../theme.css";
```

Immediately below it:

```css
/* CONVERTED TO @apply WHERE IT IS SAFE, AND ONLY THERE.
 *
 * src/components/dashboard/dashboardStyles.test.tsx asserts this file as TEXT,
 * in seven ways. The ones that constrain a conversion:
 *
 *   - the 15 --pf-* size tokens must appear as `--pf-x: 58px;` in the root rule,
 *     be consumed by a `var(--pf-x` outside it, and be declared exactly ONCE
 *     outside a media query. Neither a token declaration nor a var() consumer
 *     may become a utility.
 *   - six rules are matched by regex and must keep their literal shape:
 *     cookrow height: 52px, pills height: 64px, header/probecol/rightcol/controls
 *     height|width: var(--pf-*).
 *   - .pf-btn must keep `font-size: var(--pf-btn-font, 25px)` WITH the fallback:
 *     it is shared with the wizard and settings, which never see the dashboard
 *     root and so never see the token.
 *   - NO size token may sit inside a shorthand. `font: 800 var(--x) "..."`
 *     followed by `font-variant-numeric: tabular-nums` loses the tabular
 *     figures: a var() in a shorthand is pending-substitution, expands after
 *     the cascade, and resets every longhand it owns. The digits then change
 *     width while every landmark box stays exactly where it was. Never convert
 *     a font longhand back into a shorthand.
 *   - `animation: none !important` stays raw; @apply cannot carry !important.
 *
 * RUNTIME-CONSTRUCTED CLASS NAMES: ProbeCard.tsx:35,40 build
 * `pf-badge pf-badge-${tone}`; Dashboard.tsx:201,208 build `pf-swatch ${sel}`
 * and `pf-toggle ${on}`; NotifyBell.tsx:20 builds `pf-notify-bell${on}`.
 * Tailwind's content scanner cannot see any of them -- harmless while they are
 * hand-written rules, a silently-missing style the moment they become utilities
 * in JSX. tests/e2e/chrome-fidelity.spec.ts probes the badge tones. */
```

- [ ] **Step 3: Convert in six passes, one per section, building after each**

1,188 lines is too much to convert in one go and then debug. Do it section by section — header, body columns, cards, gauge, controls, media blocks — running `bun run build` after each. The recurring shapes:

```css
/* before */
.pf-dash-body {
  display: flex;
  gap: 14px;
  padding: 0 14px 14px;
}
/* after */
.pf-dash-body {
  @apply flex gap-[14px] px-[14px] pt-0 pb-[14px];
}
```

```css
/* UNCHANGED -- pinned by regex. */
.pf-dash-header {
  height: var(--pf-header-h);
  ...
}
```

```css
/* before */
.pf-dash-card {
  background: var(--card);
  border-radius: var(--card-radius);
  position: relative;
  overflow: hidden;
}
/* after */
.pf-dash-card {
  @apply relative overflow-hidden rounded-card bg-card;
}
```

```css
/* UNCHANGED -- the fallback is asserted as literal text, and a tokenised size
   must stay a longhand. */
.pf-btn {
  font-size: var(--pf-btn-font, 25px);
  ...
}
```

```css
/* UNCHANGED -- inside a breakpoint, and the token declaration is pinned. */
@media (max-width: 719px) {
  .pf-dash {
    --pf-btn-h: 48px;
  }
}
```

- [ ] **Step 4: Build**

```bash
cd /home/dannyb/sources/PiFire/web-react
rm -rf dist && bun run build
```

Expected: succeeds.

- [ ] **Step 5: Run the dashboard's text guards — the strictest gate in the repo**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test 2>&1 | grep -E 'dashboard (layout|size|.*css)|scaled stage|responsive rule|shorthand|"failedTests"'
```

Expected: `"failedTests": 0` and every `dashboardStyles` describe green. A failure names the constraint it broke; the fix is always to revert that rule to raw CSS, never to relax the test.

- [ ] **Step 6: Assert the three dashboard viewport projects and then everything**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test:e2e --project=fidelity --project=reflow --project=panel
bun run test:e2e:fidelity
git diff --stat tests/e2e/baselines/ tests/e2e/dashboard-layout-1280x720.json
```

Expected: all green; both `git diff --stat` targets print nothing. The `panel` project at 800×480 — the width the grill's own screen runs at — is not part of this plan's two-viewport requirement but it must stay green; it is the only assertion covering the tablet branch of the reflow.

- [ ] **Step 7: Run the four gates and commit**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run typecheck && bun run typecheck:e2e && bun run lint && bun run test && bun run gen:types:check
cd /home/dannyb/sources/PiFire
jj commit -m 'refactor(web-react): author dashboard.css with @apply, no visual change'
```

---

## Task 15: The human checkpoint, and the record of what changed

**Files:**
- Create (only if any difference is accepted): `docs/superpowers/audits/2026-07-26-tailwind-migration-diffs.md`
- Modify: `docs/superpowers/specs/2026-07-25-tailwind-v4-migration-design.md` (status line)
- Modify: `docs/superpowers/react-migration-backlog.md`

**Interfaces:**
- Consumes: every commit from Tasks 1-14.
- Produces: a signed-off migration and, if anything was allowed to change, a table naming each difference with a before/after artifact.

**A green gate is not the deliverable.** The gate cannot see anything the baselines do not name: a gradient stop, a shadow on an element with no landmark, an animation's easing, a hover state, a focus ring. This task is a person looking at the app.

- [ ] **Step 1: Confirm the whole suite is green from a cold start**

```bash
cd /home/dannyb/sources/PiFire/web-react
rm -rf dist node_modules/.cache
bun install
bun run typecheck && bun run typecheck:e2e && bun run lint && bun run test && bun run gen:types:check
bun run build
bun run test:e2e
bun run test:e2e:fidelity
```

Expected: everything green. **Read the Playwright summary for skips**, not just the exit code — the pellets gate skips rather than fails when the store's fingerprint moved, and a skipped pellets gate means `/pellets` was never actually verified.

- [ ] **Step 2: Confirm no baseline was edited during the migration**

This is the audit that makes the whole exercise meaningful. Every baseline must be byte-identical to the reference captured in Tasks 4-5, before Tailwind existed in the tree.

```bash
cd /home/dannyb/sources/PiFire
CAPTURE=$(jj log -r 'description(glob:"test(web-react): capture the pre-Tailwind*")' --no-graph -T 'commit_id.short()')
echo "reference captured at $CAPTURE"
git diff --stat "$CAPTURE" -- web-react/tests/e2e/baselines/ web-react/tests/e2e/dashboard-layout-1280x720.json
```

Expected: **no output**. Any file listed is a baseline that moved during the migration and must appear in Step 4's table with a reason, or be reverted and the CSS fixed instead.

- [ ] **Step 3: Look at every surface, at both viewports**

Start the backend (`uv run python control.py`, `uv run gunicorn -k gthread --threads 25 -b 0.0.0.0:5000 -w 1 app:app`) and the dev server (`bun run dev`), then walk the list at 1280×720 and again at 390×844 with the browser's device toolbar:

- [ ] `/` — dashboard: gauge arc gradient, probe cards, hopper meter, the five mode buttons, the accent swatches, the reduce-motion toggle
- [ ] `/` with each of the three accents (Ember, Ice, Crimson) — the swatch row switches them
- [ ] `/history` — chart, legend, the cursor tooltip (hover the plot), the range controls
- [ ] `/pellets` — all six cards, the hopper meter at whatever level the store reports, the profile editor's form
- [ ] `/settings/<tab>` for all 11 tabs — section frames, field rows, inputs, the toggle switches, the range-profile table, the save bar
- [ ] `/wizard` steps 1-5 — step pills, module cards with their vendor photos, the probes table. **Do not click Finish; it fires the real installer.**
- [ ] Shell chrome: the navbar at both widths (the hamburger opens at the phone width), and the timer strip (start a timer from the navbar clock icon)
- [ ] Focus rings: tab through the settings form and the wizard footer
- [ ] Hover states: the nav links, the mode buttons, the settings tabs

- [ ] **Step 4: Record every accepted difference, or record that there were none**

If the walkthrough found nothing, note that in the commit message and skip to Step 5. If it found something that is **clearly broken in the "before"** and was fixed by this migration, or something that changed and is being accepted, create `docs/superpowers/audits/2026-07-26-tailwind-migration-diffs.md`:

```markdown
# Tailwind v4 migration — accepted visual differences

The migration's requirement was visual identity except where the "before" was
clearly broken. Every difference below was found by a human walkthrough on
2026-07-26 and accepted deliberately.

| Surface | Viewport | Before | After | Why this is not a regression | Artifact |
|---|---|---|---|---|---|
| … | … | … | … | … | `docs/superpowers/audits/img/…` |
```

Each row needs a real before/after image. `web-react/tests/e2e/artifacts/` is gitignored (it is regenerated every run and never asserted against), so copy the PNGs into `docs/superpowers/audits/img/` where they are tracked. **The corresponding baseline entry is updated in the same commit as the change that causes it** — not in a separate "fix the baselines" commit, which is how a reviewed reason gets separated from its result.

- [ ] **Step 5: Update the spec's status and the backlog**

In `docs/superpowers/specs/2026-07-25-tailwind-v4-migration-design.md`, change the status line to:

```markdown
**Status:** implemented 2026-07-26 — see `docs/superpowers/plans/2026-07-26-tailwind-v4-migration.md`.
Note: this document's stylesheet line counts, its "12 settings tabs" / "7 wizard
steps" / "71 --pf-* properties" figures, and its omission of pellets.css were all
stale by the time implementation started. The plan's "Corrections to the design
spec" section records the measured values.
```

In `docs/superpowers/react-migration-backlog.md`, mark the Tailwind item done and add the follow-up the spec identified as reversible: converting a component's `pf-*` rules to inline utilities in JSX is now a mechanical, component-at-a-time change **with the gate already in place** — which is the thing that was missing when a full utility rewrite was rejected.

- [ ] **Step 6: Commit**

```bash
cd /home/dannyb/sources/PiFire
jj commit -m "$(cat <<'EOF'
docs(tailwind): close out the v4 migration

Human walkthrough of all 20 surfaces at 1280x720 and 390x844, on all three
accents. All 43 fidelity baselines byte-identical to the pre-Tailwind reference;
no baseline was edited during the migration.

Accepted visual differences: <none | see docs/superpowers/audits/2026-07-26-tailwind-migration-diffs.md>.
EOF
)"
```

---

## Self-review

Run against the spec after the plan is written, before execution starts.

**Spec coverage.** Every section of `2026-07-25-tailwind-v4-migration-design.md` maps to a task: the `@theme` + `@apply` approach → Tasks 8-14; the Rsbuild integration and its "no PostCSS" consequence → Task 7 (and the Global Constraints); the browserslist caveat → Task 1; "v4 cannot be used with Sass/Less/Stylus" → no action, the repo is plain CSS; extending `layoutBaseline.ts` rather than inventing a mechanism → Task 2; coverage at both viewports for every page → Tasks 3-5; determinism (demo server, `page.clock`, `document.fonts.ready`, `animations: "disabled"`) → Tasks 3-5; the explicit capture path → Task 2 Step 8 and Task 4; "what counts as clearly broken" → Task 15; the class-coverage guard risk → Task 6; the Biome risk → Task 7 Step 3; the Qt token risk → Task 8; the accent-switcher risk → Task 8 Steps 3 and 7; dynamic class names → noted in Tasks 10, 12 and 14 where those rules live. The spec's ordering (plugin inert → tokens → smallest file first) is Tasks 7 → 8 → 9-14.

**Two deliberate departures**, both recorded in "Corrections" above rather than silently taken: `page.route()` fixtures instead of a seeded `PIFIRE_DB_PATH` (cheaper, machine-independent, and non-destructive), and a computed-style baseline added alongside the geometry one (the spec's gate cannot see a wrong colour, which is the defect this migration can actually introduce).

**Naming consistency.** `PageSpec`, `ExactTable`, `StyleProbe`, `StyleMap`, `DASHBOARD_EXACT`, `STYLE_PROPS`, `measureSelectors`, `measureProbes`, `compareStyles`, `baselinePath`, `requireBaseline`, `writeStyleBaseline`, `readStyleBaseline`, `CAPTURING`, `stubApi`, `PAGE_SPECS`, `PELLETS_SPEC`, `CHROME_PROBES`, `DESKTOP`, `PHONE`, `classesUsedIn`, `declaredClasses`, `allStylesheets`, `stripComments`, `walk` — each is defined in exactly one task and spelled the same way everywhere it is consumed.

**Numbers that must be re-measured, not trusted.** The line counts in Tasks 9-14 Step 1, the `1010`/`1021`/`1028` rstest totals, the `265`-file Biome count, and the `37.2 kB` CSS bundle were all measured on `2e3ff9ae`. Each appears in a step that measures it first. If your number differs, use yours and say so in the commit — this repo has a documented history of plans being confidently wrong about live code.
