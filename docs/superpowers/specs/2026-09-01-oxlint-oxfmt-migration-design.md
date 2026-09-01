# Lint/Format Migration — Biome + ESLint → oxlint + oxfmt — Design Spec

**Date:** 2026-09-01
**Status:** Approved (design), pending implementation plan
**Verified against:** `8f6e7760` (`massive-reworks-and-new-ui`) — every version,
config, and count below was re-checked against this base, not carried over from
an earlier draft.
**Scope:** Repo-wide. Replaces both Biome configs (`biome.jsonc`,
`web-react/biome.jsonc`) and `web-react/eslint.config.js`. No product-code behavior
changes.

## Context

The repo currently runs two linters and one formatter across three configs:

- `biome.jsonc` (root) — Biome 2.5.11, **formatter only**, JSON everywhere outside
  `web-react/`. `"linter": { "enabled": false }`, and
  `"vcs": { "useIgnoreFile": true }`.
- `web-react/biome.jsonc` (`"root": false`) — formatter + `recommended` linter over
  `web-react`'s JS/TS/CSS/JSON, plus the two `packages/pifire-core` generated-code
  trees the Pydantic exporter writes into.
- `web-react/eslint.config.js` — ESLint 10 carrying **only** what Biome could not:
  `eslint-plugin-react-hooks@7` (React Compiler diagnostics, notably the house
  `set-state-in-effect` rule) and `eslint-plugin-react-refresh`.

That two-linter arrangement was a deliberate 2026-07-22 decision, taken because
Biome had no React Compiler rules. **That premise expired on 2026-08-18**, when
oxlint shipped 22 React Compiler-powered rules in its `react` plugin, driven by the
compiler's own validation passes. With the reason for the second linter gone, the
whole stack collapses to one linter and one formatter.

### Capability findings (verified, not assumed)

These were established by running the real binaries and reading current docs, not
from prior knowledge. They are the load-bearing facts of this design.

**oxfmt 0.65.0 formats JSON and CSS, not just JS/TS/JSX.** Verified by round-tripping
scratch `.css` and `.json` files through `oxfmt --write`. This is what lets a single
tool replace *both* Biome configs.

**oxfmt's defaults already equal the current Biome formatter settings**, so the
migration is not a restyling:

| Setting | `biome.jsonc` today | oxfmt default |
|---|---|---|
| line width | `lineWidth: 100` | `printWidth: 100` |
| indent | 2, spaces | `tabWidth: 2`, `useTabs: false` |
| quotes | `quoteStyle: "double"` | `singleQuote: false` |
| semicolons | `semicolons: "always"` | `semi: true` |
| trailing commas | `trailingCommas: "all"` | `trailingComma: "all"` |

oxfmt also supports nested configs (nearest config wins, walking up the tree) —
the same arrangement `"root": false` gives today — and `--stdin-filepath`, which
`emitWebContracts.ts` depends on.

**oxlint 1.80.0 rule parity** for everything currently enforced:

| Enforced today | oxlint replacement | Category |
|---|---|---|
| `eslint-plugin-react-hooks` React Compiler diagnostics | 22 `react` plugin rules (`set-state-in-effect`, `purity`, `immutability`, `refs`, `preserve-manual-memoization`, `static-components`, `use-memo`, …) | mostly `correctness`; `unsupported-syntax` is `restriction` |
| `react-hooks/exhaustive-deps` | `react/exhaustive-deps` | `correctness` |
| `react-hooks/rules-of-hooks` | `react/rules-of-hooks` | `pedantic` — needs explicit enable |
| `react-refresh/only-export-components` | `react/only-export-components` | `restriction` — needs explicit enable |
| Biome `recommended` generic lint | `eslint` / `typescript` / `unicorn` / `oxc` defaults | `correctness` |
| Biome `a11y` group | `jsx-a11y` plugin | `correctness` (plugin off by default) |
| `security/noDangerouslySetInnerHtml` | `react/no-danger` | `restriction` |

Two React Compiler rules from the upstream ESLint preset are **not implemented** in
oxlint: `config` (oxlint uses fixed, valid compiler options) and `gating` (oxlint
does not expose compiler gating). Neither is configured or relied on here.

## Goals

1. One linter (oxlint) and one formatter (oxfmt); Biome and ESLint leave the tree
   entirely, along with every config and dependency that existed only to serve them.
2. React Compiler diagnostics keep running — this is the capability the second
   linter existed for, and it must not silently disappear.
3. Formatting coverage does not shrink: JS/TS/JSX, CSS, and JSON all stay formatted.
4. Every gate (`lint`, `typecheck`, `test`) green, with the lint gate proven to
   actually *fire* rather than merely exit zero.

## Non-Goals

- No product-code behavior changes. Source edits are mechanical: suppression-comment
  syntax and whatever pure reflow `oxfmt --write` produces.
- **CSS linting is dropped.** oxlint does not lint CSS. Of Biome's CSS rules only
  `noDescendingSpecificity` is live (`noImportantStyles` is already `off` for the
  reduce-motion override). Adding stylelint to preserve one rule was considered and
  rejected — it trades a two-linter stack for a different two-linter stack.
- No Tailwind class sorting. oxfmt's `sortTailwindcss` is a genuinely new capability
  and a large independent diff; it is a follow-up, not part of a migration.
- Lint scope stays `web-react/` only. `packages/pifire-core` and `mobile` get
  formatting but not linting, exactly as today.
- No version pinning (see Decisions).

## Decisions

**Rule surface: parity, not a cleanup.** Enable `correctness` + `suspicious` plus the
`react` and `jsx-a11y` plugins, and explicitly enable `react/rules-of-hooks` and
`react/only-export-components` (which sit outside those categories). The intent is
green on day one with no source edits.

Because oxlint's categories are narrower than Biome's `recommended`, **most of the
documented deferrals in `biome.jsonc` turn out not to need porting at all** — the
rules they disable are not in the enabled categories:

| `biome.jsonc` deferral | oxlint counterpart | Needs explicit `off`? |
|---|---|---|
| `a11y/useKeyWithClickEvents` | `jsx-a11y/click-events-have-key-events` (correctness) | **Yes** |
| `a11y/noStaticElementInteractions` | `jsx-a11y/no-static-element-interactions` (correctness) | **Yes** |
| `a11y/useButtonType` | `react/button-has-type` (restriction) | No — not enabled |
| `style/noNonNullAssertion` | `typescript/no-non-null-assertion` (style) | No — not enabled |
| `suspicious/noArrayIndexKey` | `react/no-array-index-key` (perf) | No — not enabled |
| `security/noDangerouslySetInnerHtml` (PortForm override) | `react/no-danger` (restriction) | No — not enabled |
| `a11y/noSvgWithoutTitle`, `a11y/useGenericFontNames`, `complexity/noImportantStyles` | no equivalent / CSS-only | No |

This mapping is a **prediction to be verified by running oxlint**, not a claim. The
implementation must confirm each row rather than assume it; any deferral that does
fire gets an explicit `off` carrying the *same rationale comment* as today, so the
reasons survive the migration rather than being re-derived later.

**Two oxfmt defaults are overridden for parity:**

- `sortPackageJson: false` — on by default; would reorder hand-maintained
  `package.json` files, which is churn this migration did not ask for.
- `sortImports` **enabled** — Biome's `assist.organizeImports` is on today under the
  `recommended` preset, so leaving oxfmt's default (off) would be a *silent
  regression*, not a neutral choice.

**No version pinning.** Caret ranges, matching the rest of `devDependencies`. oxfmt is
pre-1.0 and its output may shift on minor bumps; the user explicitly accepted silent
reformats on upgrade rather than carrying pinned versions.

## Design

### Config topology

| Today | After |
|---|---|
| `biome.jsonc` | `.oxfmtrc.json` (root) |
| `web-react/biome.jsonc` | `web-react/.oxfmtrc.json` |
| `web-react/eslint.config.js` | `web-react/.oxlintrc.json` |

The root `.oxfmtrc.json` is mostly `ignorePatterns`, carried over verbatim from the
two Biome configs — including the exclusions that exist for **byte-level contracts
rather than style**: `tests/characterization/fixtures/**` (digest-pinned by
`test_process_command_golden.py`), `schema/**`, and
`web-react/src/helpers/settings/settingsDefaults.gen.ts`, plus `static/`, `htmlcov/`,
`docs/`, `.superpowers/`, `dist`, `coverage`, `node_modules`.

The root Biome config sets `"vcs": { "useIgnoreFile": true }`. oxfmt reads
`.gitignore` by default (`--ignore-path` overrides), so this behavior carries over
without configuration.

The `web-react/.oxfmtrc.json` nested config keeps the two out-of-tree generated
directories reachable (`packages/pifire-core/src/contracts/**`,
`packages/pifire-core/src/settings/**`), preserving the arrangement whose reason is
documented in `biome.jsonc`: `emitWebContracts.ts` formats its output through that
config.

Biome's `css.parser.tailwindDirectives` escape hatch has no successor and needs none —
it existed because Biome 2.5 treated `@theme` / `@apply` / `@reference` as parse
errors. oxfmt parses Tailwind v4 directives without configuration (to be verified
against all 15 stylesheets).

### Dependency changes

Removed from `web-react/devDependencies`: `@biomejs/biome`, `eslint`,
`eslint-plugin-react-hooks`, `eslint-plugin-react-refresh`, `@typescript-eslint/parser`,
and **`typescript@^5.9.3`**. That last one is not incidental cleanup — the current
`eslint.config.js` header states it exists *solely* because
`@typescript-eslint/parser` needs the classic TS JS API that TypeScript 7 no longer
ships. Removing the parser removes its only consumer. The `typescript7` alias stays;
it remains the real typecheck gate.

Added: `oxlint`, `oxfmt`. Installed with `bun`; `bun.lock` committed.

### Script changes

`web-react/package.json`:

```
"lint":   "oxlint && oxfmt --check .",
"format": "oxfmt ."
```

This also repairs a latent defect in the current gate: `biome check . && eslint .`
short-circuits, so a Biome failure hides ESLint's state entirely — a known trap that
has previously required running `bunx eslint .` separately to see the truth.

### Call sites beyond config

- `web-react/scripts/emitWebContracts.ts` — `BIOME_EXECUTABLE` / `BIOME_CONFIG`
  constants and the `biome format --stdin-file-path` invocation become oxfmt
  equivalents. `BIOME_HEADER`
  (`// biome-ignore-all lint/suspicious/noEmptyInterface: …`) is **deleted, not
  ported**: `typescript/no-empty-interface` is a `style` rule and therefore not
  enabled, so any ported header would be a no-op. Deleting it also removes the
  `generated.replace(/^\/\* eslint-disable \*\/\n/, "")` dance at line 163.
- `web-react/scripts/emitSettingsDefaults.ts` — emits an `/* eslint-disable */`
  header into generated output; becomes `/* oxlint-disable */`.

### Suppression comment inventory (audited)

A full audit across `web-react/src`, `web-react/tests`, `web-react/scripts`,
`packages`, and `mobile` found 17 `biome-ignore` and 8 `eslint-disable` occurrences.
Crucially, **only two of them need porting** — the rest suppress rules that are not
in the enabled categories, and per the rule above are deleted rather than translated
into no-ops that would rot:

| Suppression | Count | oxlint rule | Category | Enabled? | Action |
|---|---|---|---|---|---|
| `lint/suspicious/noExplicitAny` | 8 | `typescript/no-explicit-any` | restriction | No | **Delete** |
| `lint/suspicious/noEmptyInterface` (`biome-ignore-all` header in 6 `contracts/*.gen.ts`, plus the emitter literal) | 7 | `typescript/no-empty-interface` | style | No | **Delete**, and drop `BIOME_HEADER` from `emitWebContracts.ts` entirely rather than porting it |
| `lint/correctness/useExhaustiveDependencies` | 1 | `react/exhaustive-deps` | correctness | **Yes** | **Port**, keeping its rationale comment |
| `react-refresh/only-export-components` (`AppPrefs.tsx`) | 1 | `react/only-export-components` | restriction, explicitly enabled | **Yes** | **Port**, keeping its rationale comment |
| `lint/style/noDescendingSpecificity` (CSS) | 1 | — CSS lint dropped | — | No | **Delete** |
| `/* eslint-disable */` generated-file headers | 3 files (`settingsDefaults.gen.ts`, `settingsTypes.gen.ts`, `controllerTypes.gen.ts`) + 2 emitter literals | `/* oxlint-disable */` | — | — | Rewrite in both emitters, then regenerate and confirm the output matches |
| `react-hooks/exhaustive-deps` in `mobile/` | 1 | — `mobile` is not linted | — | No | **Leave untouched** (already inert today) |

Two facts worth recording, because they cut against the design and should not be
discovered later:

- The single live `noDescendingSpecificity` suppression is **proof that rule actually
  fires in this codebase**. Dropping CSS linting is not dropping a theoretical rule;
  it is dropping one with a demonstrated hit. The user accepted this trade knowingly;
  it is recorded here so a future reader does not mistake it for an oversight.
- The 8 `noExplicitAny` suppressions mean this codebase has at least 8 deliberate
  `any` uses that go **unguarded** after the migration, since
  `typescript/no-explicit-any` is `restriction` and not enabled. This is a real (if
  minor) reduction in enforcement, not a pure like-for-like swap. Enabling that single
  rule is available as a follow-up if the loss is unwanted.

## Verification

`bun run lint`, `bun run typecheck`, and `bun run test` must all pass. That is
necessary but **not sufficient** — a lint config that fails to load a plugin exits
zero and looks exactly like a clean tree.

**Negative control (required).** For each capability this migration claims to
preserve, write a deliberate violation into a scratch file inside `web-react/src`
and confirm oxlint reports it, then delete it:

| Rule | Violation to plant |
|---|---|
| `react/set-state-in-effect` | `useEffect(() => { setX(1); }, [])` |
| `react/exhaustive-deps` | effect reading a prop with `[]` deps |
| `react/rules-of-hooks` | `useState` inside an `if` |
| `react/only-export-components` | component file also exporting a plain constant |
| `react/immutability` | mutating a `useState` value directly |

A green run only counts as evidence of parity if these first go red. Additionally,
run with `--report-unused-disable-directives` to catch any ported suppression whose
rule name no longer resolves — a misspelled disable is invisible otherwise.

Final verification runs in the **main checkout**, not an agent worktree. `node_modules`
is gitignored, so `jj workspace add` does not populate it; a worktree that has not had
`bun install` run in it cannot execute `oxlint` or `oxfmt` at all, and a lint step that
fails to launch must not be mistaken for a lint step that passed.

## Risks

- **Reflow diff hides real changes in review.** `oxfmt` output will not be
  byte-identical to Biome's, producing a wide mechanical diff. Mitigated by
  sequencing (below) — the reflow lands as its own commit, reviewable as "trust the
  formatter", so it cannot conceal a substantive hunk.
- **`suspicious` category may surface findings Biome missed.** The user's decision
  was parity with no new source edits, so any such findings are **brought back for a
  decision** rather than fixed or suppressed unilaterally. The size of this set is
  unknown until oxlint runs; it may be zero.
- **oxfmt is pre-1.0.** Accepted explicitly; see Decisions.

## Sequencing

Three commits, deliberately separated:

1. Configs, dependencies, scripts, suppression-comment rewrites — **no reformatting**.
2. Pure `oxfmt --write .` reflow across the repo.
3. Any source fixes the negative control or lint run surfaces, if the user approves
   them.

Repo is jj; commits via `jj describe --stdin`, with `jj new` before writing. `default`
is currently the only workspace (`.worktrees/learning-tests` was removed on
2026-09-01, having held no unique commits), but concurrent sessions do commit to this
branch, so each commit is scoped to this migration's files rather than to the whole
working copy.

## Parallelization

Task 1 (root `.oxfmtrc.json` + dependency/script changes) and Task 2
(`web-react/.oxlintrc.json` + suppression-comment rewrites) touch disjoint files and
could run concurrently, but Task 2 cannot be *verified* until Task 1 has installed the
binaries — so concurrency here buys nothing real. **Recommend serial execution.**
Tasks 3 (reflow) and 4 (verification) are strictly ordered after both, since the
reflow's output depends on the final config.

If run concurrently anyway, isolated jj workspaces are required; disjoint file sets
alone are not sufficient in this repo.
