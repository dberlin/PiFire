# oxlint + oxfmt Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Biome 2.5.11 (formatter + linter) and ESLint 10 (react-hooks + react-refresh) with oxlint 1.80 and oxfmt 0.65, leaving one linter and one formatter.

**Architecture:** Both toolchains coexist for Tasks 1–3 so oxlint/oxfmt can be proven correct *against* the tool they replace, rather than after it is gone. Biome and ESLint are removed only in Task 4, once parity is demonstrated. The repo-wide reformat is deliberately quarantined in Task 5 so its wide mechanical diff cannot conceal a substantive hunk.

**Tech Stack:** bun (package manager and runner), oxlint 1.80.x, oxfmt 0.65.x, TypeScript 7 (`typescript7` alias) for typecheck, rstest for unit tests, jj for version control.

**Spec:** `docs/superpowers/specs/2026-09-01-oxlint-oxfmt-migration-design.md`

## Global Constraints

- **Package manager is `bun`, never bare `npm`.** Install with `bun add`, run scripts with `bun run`, execute one-offs with `bunx`. Commit `bun.lock`.
- **Test runner is rstest, not vitest.** `bun run test` (never `bun test` — that invokes bun's own runner and fails differently).
- **No version pinning.** Use caret ranges (`^1.80.0`, `^0.65.0`). The user explicitly accepted that a minor bump may silently reformat the tree.
- **VCS is jj, not git.** Use `jj new` before writing, `jj describe --stdin` to set messages. Never `git commit` — it silently succeeds in this colocated repo and bypasses jj. `git rev-parse HEAD` resolves to `@-`, not `@`.
- **Concurrent sessions commit to this branch.** Scope each commit to this migration's files; do not `jj squash` reflexively (edits are already in `@`).
- **Formatter settings are parity targets, exact values:** `printWidth: 100`, `tabWidth: 2`, `useTabs: false`, `semi: true`, `singleQuote: false`, `trailingComma: "all"`.
- **Lint scope is `web-react/` only.** `packages/pifire-core` and `mobile` get formatting but not linting, exactly as today.
- **Never add a suppression comment to make a gate pass.** If a rule fires on real code, stop and report it — per the spec, new findings go back to the user for a decision.
- **Verification runs in the main checkout** (`/home/dannyb/sources/PiFire`), not an agent worktree — `node_modules` is gitignored, so a worktree without `bun install` cannot execute these binaries at all.

---

## File Structure

| File | Responsibility |
|---|---|
| `.oxfmtrc.jsonc` (root, **create**) | Formats everything outside `web-react/`. Replaces `biome.jsonc`. |
| `web-react/.oxfmtrc.jsonc` (**create**) | Nested override for `web-react/` + the two `@pifire/core` generated trees. Replaces `web-react/biome.jsonc`'s formatter half. |
| `web-react/.oxlintrc.jsonc` (**create**) | The only lint config. Replaces `web-react/eslint.config.js` and `web-react/biome.jsonc`'s linter half. |
| `biome.jsonc` (**delete**, Task 4) | — |
| `web-react/biome.jsonc` (**delete**, Task 4) | — |
| `web-react/eslint.config.js` (**delete**, Task 4) | — |
| `web-react/package.json` (**modify**) | Dep swap + `lint`/`format` scripts. |
| `web-react/scripts/emitWebContracts.ts` (**modify**) | Shells out to the formatter; carries `BIOME_*` constants. |
| `web-react/scripts/emitSettingsDefaults.ts` (**modify**) | Emits an `/* eslint-disable */` banner. |

---

## Task 1: Add oxlint + oxfmt and the two formatter configs

Biome stays installed and configured. The goal of this task is a working oxfmt whose output can be *compared* against Biome's.

**Files:**
- Modify: `web-react/package.json` (devDependencies only — not scripts yet)
- Create: `.oxfmtrc.jsonc`
- Create: `web-react/.oxfmtrc.jsonc`

**Interfaces:**
- Produces: two `.oxfmtrc.jsonc` config files and the `oxfmt` / `oxlint` binaries at `web-react/node_modules/.bin/`. Task 2 consumes `oxlint`; Task 3 consumes the `web-react/.oxfmtrc.jsonc` path; Task 5 consumes both configs.

- [ ] **Step 1: Start a fresh commit BEFORE writing anything**

`@` is often the pushed tip, and in this repo concurrent sessions commit to the same branch. Do this first, not at commit time:

```bash
cd /home/dannyb/sources/PiFire
jj new
```

Recovery if this is forgotten is `jj op restore` (the operation log), **not** `jj restore`.

- [ ] **Step 2: Install the two tools**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun add -D oxlint@^1.80.0 oxfmt@^0.65.0
```

- [ ] **Step 3: Verify both binaries execute**

```bash
cd /home/dannyb/sources/PiFire/web-react
./node_modules/.bin/oxlint --version && ./node_modules/.bin/oxfmt --version
```

Expected: two version strings, `1.80.x` and `0.65.x`. If `oxlint` reports `Cannot find native binding`, the platform-specific optional dependency did not install — re-run `bun install` rather than proceeding.

- [ ] **Step 4: Create the root formatter config**

Create `/home/dannyb/sources/PiFire/.oxfmtrc.jsonc`. The `ignorePatterns` are carried over verbatim from `biome.jsonc`, and the comments explaining *why* each byte-contract exclusion exists must survive — they are the reason, not decoration.

**Two corrections found during execution — both are load-bearing:**

1. The file must be named `.oxfmtrc.jsonc`, not `.oxfmtrc.jsonc`. It contains comments, and Biome (still installed until Task 4) parses a `.json` extension strictly, producing 17 errors that fail the old gate. oxfmt discovers `.oxfmtrc.jsonc` natively.
2. Biome used an **allow-list** (`"includes": ["**/*.json", ...]`) — JSON and nothing else. oxfmt only has a deny-list, so the old scope must be reconstructed by denying every other extension. Without this, oxfmt reformats **362 files it never formatted before** (all of `mobile/`'s TS/TSX, every README, the GitHub workflows).

```jsonc
{
  "$schema": "./web-react/node_modules/oxfmt/configuration_schema.json",
  // Formats JSON everywhere outside web-react/, which has its own nested
  // config (web-react/.oxfmtrc.jsonc) covering its JS, TS, CSS and JSON
  // together.
  //
  // The Biome config this replaces used an ALLOW-list ("includes":
  // ["**/*.json", ...]), so its scope was JSON and nothing else. oxfmt only
  // has a deny-list, so that scope has to be reconstructed by denying every
  // other extension -- without this, oxfmt reformats 362 files it was never
  // formatting before (all of mobile/'s TS/TSX, every README, the GitHub
  // workflows). Widening the scope may be worth doing on purpose one day;
  // it should not happen as a side effect of changing formatters.
  //
  // oxfmt reads .gitignore by default, which is what biome.jsonc's
  // "vcs": { "useIgnoreFile": true } asked for explicitly.
  "ignorePatterns": [
    "**/node_modules/**",
    "web-react/**",
    "static/**",
    "htmlcov/**",
    "docs/**",
    ".superpowers/**",

    // Everything that is not JSON, to reproduce the old allow-list.
    "**/*.ts",
    "**/*.tsx",
    "**/*.js",
    "**/*.jsx",
    "**/*.mjs",
    "**/*.cjs",
    "**/*.md",
    "**/*.html",
    "**/*.css",
    "**/*.yml",
    "**/*.yaml",
    "**/*.toml",

    // Listed separately because it is a landmine, not a scope choice: this is
    // QML JavaScript, and its leading `.pragma library` is not valid JS. oxfmt
    // fails to parse it outright, which fails the whole run. Keep this
    // exclusion even if the JS denial above is ever lifted.
    "display/qml/**",

    // Excluded because it carries a byte-level contract that a formatter
    // breaks -- not because anyone chose its style. Digest-pinned by
    // tests/characterization/test_process_command_golden.py.
    "tests/characterization/fixtures/**"
  ],
  "sortPackageJson": false
}
```

`sortPackageJson` is `true` by default and would reorder hand-maintained `package.json` files; this migration did not ask for that churn.

Note on nested configs: oxfmt resolves the **nearest** config per file and does not merge, so these root `ignorePatterns` do not apply to anything under `web-react/` — that tree is governed entirely by its own config.

- [ ] **Step 5: Create the web-react formatter config**

Create `/home/dannyb/sources/PiFire/web-react/.oxfmtrc.jsonc`:

```jsonc
{
  "$schema": "./node_modules/oxfmt/configuration_schema.json",
  // Nested under the repo-root .oxfmtrc.jsonc. This one owns everything in
  // web-react/ -- JS, TS, CSS and JSON -- plus the two @pifire/core trees the
  // Pydantic exporter writes into. The nearest config to a file wins, so this
  // one applies to web-react/** without the root config needing to know.
  "ignorePatterns": [
    "dist/**",
    "coverage/**",
    "node_modules/**",
    "src/helpers/settings/settingsDefaults.gen.ts",
    // Generated by the registered Pydantic web-contract exporter. Formatting
    // it here would only be undone by the next `bun run gen:types`.
    "schema/**"
  ],
  // sortImports replaces Biome's assist.organizeImports, which was on by
  // default under its `recommended` preset. Leaving oxfmt's default (off)
  // would be a silent regression, not a neutral choice.
  "sortImports": {},
  "sortPackageJson": false
}
```

- [ ] **Step 6: Verify oxfmt parses every Tailwind v4 stylesheet**

Biome needed `css.parser.tailwindDirectives` or `@theme` / `@apply` / `@reference` were parse errors that aborted the whole file. Confirm oxfmt needs no such flag:

```bash
cd /home/dannyb/sources/PiFire/web-react
./node_modules/.bin/oxfmt --list-different 'src/**/*.css' 2>&1 | tail -20
```

Expected: a list of files that *would change* (or none), and **no parse errors**. If any file reports a syntax/parse error, stop — that is a blocker, not a formatting difference, and the spec's claim that Tailwind directives need no configuration is wrong.

- [ ] **Step 7: Record the oxfmt-vs-Biome delta for later comparison**

This is the baseline Task 5 will act on. Capture it now, while Biome is still installed:

```bash
cd /home/dannyb/sources/PiFire/web-react
./node_modules/.bin/oxfmt --list-different . 2>&1 | tee /tmp/oxfmt-delta-webreact.txt | wc -l
cd /home/dannyb/sources/PiFire
./web-react/node_modules/.bin/oxfmt --list-different . 2>&1 | tee /tmp/oxfmt-delta-root.txt | wc -l
```

Expected: two counts. Report both numbers — they size the Task 5 reflow. No action needed on them yet.

- [ ] **Step 8: Confirm the existing gate is still green (nothing broken yet)**

```bash
cd /home/dannyb/sources/PiFire/web-react && bun run lint && bun run typecheck
```

Expected: PASS. Biome and ESLint are untouched at this point; a failure here means the install disturbed something and must be investigated before continuing.

- [ ] **Step 9: Commit**

Edits are already in `@` (jj snapshots the working copy automatically) — describe it, never `jj squash`, which would move them into the parent:

```bash
cd /home/dannyb/sources/PiFire
jj describe --stdin <<'MSG'
Add oxlint and oxfmt alongside Biome

Both toolchains coexist so the replacement can be proven against the tool
it replaces. No scripts switched and nothing removed yet.
MSG
```

---

## Task 2: Write the lint config and prove every preserved rule actually fires

This is the load-bearing task. A lint config that silently fails to load a plugin exits zero and is indistinguishable from a clean tree, so the deliverable is not "oxlint runs" but "oxlint demonstrably catches what ESLint caught."

**Files:**
- Create: `web-react/.oxlintrc.jsonc`
- Test: negative-control scratch file at `web-react/src/__oxlint_probe.tsx` (created and deleted within this task; never committed)

**Interfaces:**
- Consumes: the `oxlint` binary from Task 1.
- Produces: `web-react/.oxlintrc.jsonc`, the sole lint config. Task 3 consumes its enabled-rule set to decide which suppression comments to port vs delete. Task 4 consumes it as the target of the new `lint` script.

- [ ] **Step 1: Write the lint config**

Create `/home/dannyb/sources/PiFire/web-react/.oxlintrc.jsonc`. Every `off` carries the rationale from `biome.jsonc` verbatim — the reasons are the point, not the switches.

```jsonc
{
  "$schema": "./node_modules/oxlint/configuration_schema.json",
  // The only lint config in the repo. Replaces both web-react/biome.jsonc's
  // linter half and eslint.config.js. The React Compiler diagnostics that
  // eslint-plugin-react-hooks used to carry alone now live in oxlint's react
  // plugin (22 rules, shipped 2026-08-18) -- which is what retired the
  // two-linter arrangement.
  "plugins": ["react", "jsx-a11y", "typescript", "unicorn", "oxc"],
  "categories": {
    "correctness": "error",
    "suspicious": "error"
  },
  "ignorePatterns": [
    "dist",
    "coverage",
    "tests/e2e",
    "*.config.js",
    "*.config.ts",
    "src/helpers/settings/settingsDefaults.gen.ts"
  ],
  "rules": {
    // Outside the enabled categories (pedantic / restriction respectively),
    // so both need enabling by name to preserve today's coverage.
    "react/rules-of-hooks": "error",
    "react/only-export-components": "warn",

    // Deferred, carried over from biome.jsonc with their original reasons.
    //
    // All findings are on existing modal/gauge widgets (click-to-dismiss
    // scrims, SVG gauge, buttons without an explicit type). Per house rules,
    // a11y widget restructuring is out of scope here -- scheduled as a
    // separate follow-up.
    "jsx-a11y/click-events-have-key-events": "off",
    "jsx-a11y/no-static-element-interactions": "off"
  }
}
```

Note what is deliberately **absent**: `react/button-has-type`, `typescript/no-non-null-assertion`, `react/no-array-index-key`, and `react/no-danger` are all outside the enabled categories, so disabling them would be a no-op that rots. Step 3 verifies that claim instead of trusting it.

- [ ] **Step 2: Run oxlint and record the baseline**

```bash
cd /home/dannyb/sources/PiFire/web-react
./node_modules/.bin/oxlint 2>&1 | tail -30
```

Expected: ideally zero errors. **If there are findings, do not fix them and do not suppress them.** Record the rule names and counts and report back — per the spec, new findings that Biome missed are a user decision, not an implementer's.

- [ ] **Step 3: Verify the "not enabled, so not disabled" claim**

Confirm the four rules omitted from the config genuinely do not fire, using `--print-config` to see the resolved rule set:

```bash
cd /home/dannyb/sources/PiFire/web-react
./node_modules/.bin/oxlint --print-config 2>&1 | grep -iE "button-has-type|no-non-null-assertion|no-array-index-key|no-danger|no-explicit-any|no-empty-interface" || echo "NONE ENABLED (expected)"
```

Expected: `NONE ENABLED (expected)`, or each listed as `off`/absent. If any resolves to `error`/`warn`, the spec's category mapping was wrong for that rule — add it to `rules` as `"off"` with the matching rationale comment from `biome.jsonc`, and note the correction.

- [ ] **Step 4: Write the negative-control probe (the failing test)**

This is the step that makes a green run meaningful. Create `/home/dannyb/sources/PiFire/web-react/src/__oxlint_probe.tsx`:

```tsx
import { useEffect, useState } from "react";

export const NOT_A_COMPONENT = 42;

export function ProbeSetStateInEffect({ value }: { value: number }) {
  const [count, setCount] = useState(0);
  // react/set-state-in-effect + react/exhaustive-deps (reads `value`, empty deps)
  useEffect(() => {
    setCount(value);
  }, []);
  return <div>{count}</div>;
}

export function ProbeRulesOfHooks({ flag }: { flag: boolean }) {
  if (flag) {
    // react/rules-of-hooks
    const [x] = useState(0);
    return <div>{x}</div>;
  }
  return null;
}

export function ProbeImmutability() {
  const [state] = useState({ a: 0 });
  // react/immutability
  state.a = 1;
  return <div>{state.a}</div>;
}
```

- [ ] **Step 5: Run oxlint on the probe and verify every rule fires**

```bash
cd /home/dannyb/sources/PiFire/web-react
./node_modules/.bin/oxlint src/__oxlint_probe.tsx 2>&1 | grep -oE "react\([a-z-]+\)|react-hooks/[a-z-]+" | sort | uniq -c
```

Expected: hits for **all five** of `set-state-in-effect`, `exhaustive-deps`, `rules-of-hooks`, `immutability`, and `only-export-components`. 

**A missing rule is a hard failure, not a note.** If any of the five does not appear, the migration is losing that rule — stop and report which one. Do not proceed to Task 4 (which deletes ESLint) until all five fire.

- [ ] **Step 6: Verify `only-export-components` accepts its option**

ESLint configured this as `["warn", { allowConstantExport: true }]`. Confirm oxlint honors an equivalent, because without it every file exporting a constant alongside a component will warn:

```bash
cd /home/dannyb/sources/PiFire/web-react
./node_modules/.bin/oxlint src/__oxlint_probe.tsx 2>&1 | grep -A3 "only-export-components"
```

If `NOT_A_COMPONENT` is flagged, add the option to `.oxlintrc.jsonc`:

```jsonc
    "react/only-export-components": ["warn", { "allowConstantExport": true }]
```

then re-run and confirm the constant is no longer flagged while the component rules still fire. If oxlint rejects that option shape, report it — the fallback is accepting the warning on the handful of affected files, which is a user decision.

- [ ] **Step 7: Delete the probe**

```bash
rm /home/dannyb/sources/PiFire/web-react/src/__oxlint_probe.tsx
```

Verify it is gone — a committed probe would fail `bun run typecheck` in CI:

```bash
test ! -e /home/dannyb/sources/PiFire/web-react/src/__oxlint_probe.tsx && echo "probe removed"
```

- [ ] **Step 8: Commit**

```bash
cd /home/dannyb/sources/PiFire
jj describe --stdin <<'MSG'
Add oxlint config with verified rule parity

Enables correctness + suspicious plus the react and jsx-a11y plugins, and
names rules-of-hooks and only-export-components explicitly since they sit
outside those categories.

Each preserved rule was proven to fire against a planted violation before
this landed; a lint config that fails to load a plugin exits zero and looks
exactly like a clean tree.
MSG
```

---

## Task 3: Port the two live suppressions, delete the dead ones, rewrite the emitters

Per the spec's audit, only 2 of the 25 suppression comments correspond to rules that are still enabled. The rest are deleted rather than translated into no-ops that would rot.

**Files:**
- Modify: `web-react/src/components/AppPrefs.tsx` (1 `eslint-disable-next-line`)
- Modify: the file carrying `biome-ignore lint/correctness/useExhaustiveDependencies` (locate in Step 1)
- Modify: `web-react/scripts/emitWebContracts.ts:16-19,155-195`
- Modify: `web-react/scripts/emitSettingsDefaults.ts:6-8`
- Delete comments in: the 8 `noExplicitAny` sites, the 1 CSS `noDescendingSpecificity` site
- Leave untouched: `mobile/src/components/SetpointModal.tsx`

**Interfaces:**
- Consumes: the enabled-rule set from Task 2's `.oxlintrc.jsonc`.
- Produces: `emitWebContracts.ts` with `OXFMT_EXECUTABLE` / `OXFMT_CONFIG` constants replacing `BIOME_EXECUTABLE` / `BIOME_CONFIG`, and `formatGeneratedTypeScript(generated: string, outputPath: string): Promise<string>` — same signature, so no caller changes.

- [ ] **Step 1: Locate the two suppressions that must be ported**

```bash
cd /home/dannyb/sources/PiFire
grep -rn 'useExhaustiveDependencies' web-react/src web-react/tests
grep -rn 'only-export-components' web-react/src
```

Record both file:line locations. These are the only two whose rules survive.

- [ ] **Step 2: Port them to oxlint syntax, keeping the rationale text**

The `biome-ignore lint/correctness/useExhaustiveDependencies: <reason>` becomes:

```ts
// oxlint-disable-next-line react/exhaustive-deps -- <same reason text, verbatim>
```

The `eslint-disable-next-line react-refresh/only-export-components -- pairs the provider with its hook, same module by design.` in `AppPrefs.tsx` becomes:

```ts
// oxlint-disable-next-line react/only-export-components -- pairs the provider with its hook, same module by design.
```

- [ ] **Step 3: Delete the dead suppressions**

```bash
cd /home/dannyb/sources/PiFire
grep -rn 'biome-ignore lint/suspicious/noExplicitAny' web-react/src web-react/tests packages
grep -rn 'biome-ignore lint/style/noDescendingSpecificity' web-react/src
```

Delete each of those comment lines (the 8 `noExplicitAny` and the 1 `noDescendingSpecificity`). Their rules are not enabled, so a ported version would suppress nothing. **Delete only the comment line, never the code it precedes.**

Leave `mobile/src/components/SetpointModal.tsx` alone — `mobile` is not linted, so its directive is already inert and touching it is out of scope.

- [ ] **Step 4: Rewrite `emitWebContracts.ts` constants**

Replace lines 16–19:

```ts
const BIOME_EXECUTABLE = join(WEB_REACT_ROOT, "node_modules/.bin/biome");
const BIOME_CONFIG = join(WEB_REACT_ROOT, "biome.jsonc");
const BIOME_HEADER =
  "// biome-ignore-all lint/suspicious/noEmptyInterface: Generated from closed empty JSON objects.";
```

with:

```ts
const OXFMT_EXECUTABLE = join(WEB_REACT_ROOT, "node_modules/.bin/oxfmt");
const OXFMT_CONFIG = join(WEB_REACT_ROOT, ".oxfmtrc.jsonc");
```

`BIOME_HEADER` is deleted outright, not ported: `typescript/no-empty-interface` is a `style` rule and is not enabled, so the header would suppress nothing.

- [ ] **Step 5: Update the generated banner**

At line ~24, in `COMPILER_OPTIONS.bannerComment`, change `/* eslint-disable */` to `/* oxlint-disable */`:

```ts
  bannerComment:
    "/* oxlint-disable */\n// GENERATED from Pydantic web contracts — do not edit. Regenerate: bun run gen:types",
```

- [ ] **Step 6: Simplify `formatGeneratedTypeScript`**

With `BIOME_HEADER` gone, the conditional prepend and its `.replace()` collapse. Replace the function body's opening and the spawn call:

```ts
async function formatGeneratedTypeScript(generated: string, outputPath: string): Promise<string> {
  // A WEB_REACT_ROOT-relative --stdin-filepath -- the same "../packages/..."
  // shape TYPESCRIPT_DIRECTORY already uses -- keeps the formatter resolving
  // this config rather than falling back to its built-in defaults for a path
  // outside web-react.
  const formatterStdinPath = relative(WEB_REACT_ROOT, outputPath);
  const formatter = Bun.spawn(
    [
      OXFMT_EXECUTABLE,
      "-c",
      OXFMT_CONFIG,
      "--stdin-filepath",
      formatterStdinPath,
    ],
    {
      cwd: WEB_REACT_ROOT,
      stdin: new Blob([generated]),
      stdout: "pipe",
      stderr: "pipe",
    },
  );
  const formattedOutput = new Response(formatter.stdout).text();
  const formatterError = new Response(formatter.stderr).text();
  const exitCode = await formatter.exited;
  if (exitCode !== 0) {
    throw new Error(`oxfmt failed to format ${outputPath}: ${await formatterError}`);
  }
  return lfTerminated(await formattedOutput);
}
```

Note the flag changes: `--config-path` → `-c`, `--stdin-file-path` → `--stdin-filepath`, and Biome's `format` subcommand is dropped (oxfmt formats by default).

- [ ] **Step 7: Rewrite `emitSettingsDefaults.ts` banner**

At lines 6–8, this:

```ts
const BANNER =
  "/* eslint-disable */\n" +
  "// GENERATED from schema/settings.schema.json — do not edit. Regenerate: bun run gen:types";
```

becomes:

```ts
const BANNER =
  "/* oxlint-disable */\n" +
  "// GENERATED from schema/settings.schema.json — do not edit. Regenerate: bun run gen:types";
```

- [ ] **Step 8: Regenerate and verify the emitters still work**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run gen:types
```

Expected: exit 0, no "failed to format" error. Then confirm the generated files carry the new banner and that indentation did not regress to tabs:

```bash
cd /home/dannyb/sources/PiFire
head -2 packages/pifire-core/src/contracts/core.gen.ts
grep -rn 'eslint-disable' packages/pifire-core/src web-react/src || echo "no eslint-disable left in generated output"
grep -Pn '^\t' packages/pifire-core/src/contracts/core.gen.ts | head -3 || echo "no tab indentation (correct)"
```

Expected: banner reads `/* oxlint-disable */`; no `eslint-disable` remains; **no tab-indented lines**. Tabs mean the `--stdin-filepath` config resolution failed and oxfmt fell back to defaults — that was a real, documented Biome bug in this exact code path, so verify rather than assume oxfmt avoids it.

- [ ] **Step 9: Run the full gate**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run lint && bun run typecheck && bun run test
```

Expected: all PASS. `bun run lint` is still `biome check . && eslint .` at this point — that is intentional. It confirms the suppression rewrites did not break the *old* gate before the old gate is removed.

**This step will likely fail on the deleted suppressions** — Biome will now report the 8 `noExplicitAny` and 1 `noDescendingSpecificity` findings whose comments you removed. That is expected and correct: those rules are still enabled in `biome.jsonc`, which Task 4 deletes. Record the failures, confirm every one of them is exactly a rule this migration is intentionally dropping, and proceed. If any *other* rule fires, stop and report.

- [ ] **Step 10: Commit**

```bash
cd /home/dannyb/sources/PiFire
jj describe --stdin <<'MSG'
Port suppressions and emitters to oxlint/oxfmt

Only 2 of 25 suppression comments map to rules that are still enabled;
the rest are deleted rather than translated into no-ops that would rot.

Drops BIOME_HEADER from the contract emitter entirely -- no-empty-interface
is a style rule and is not enabled, so a ported header would suppress
nothing.
MSG
```

---

## Task 4: Remove Biome and ESLint, switch the gate

**Files:**
- Delete: `biome.jsonc`, `web-react/biome.jsonc`, `web-react/eslint.config.js`
- Modify: `web-react/package.json` (scripts + devDependencies)

**Interfaces:**
- Consumes: `.oxlintrc.jsonc` and both `.oxfmtrc.jsonc` files from Tasks 1–2.
- Produces: `bun run lint` = `oxlint && oxfmt --check .`; `bun run format` = `oxfmt .`.

- [ ] **Step 1: Switch the scripts**

In `web-react/package.json`, replace:

```json
    "lint": "biome check . && eslint .",
    "format": "biome format --write .",
```

with:

```json
    "lint": "oxlint && oxfmt --check .",
    "format": "oxfmt .",
```

Note this also repairs a latent defect: the old `&&` chain short-circuited, so a Biome failure hid ESLint's state entirely.

- [ ] **Step 2: Remove the six dependencies**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun remove @biomejs/biome eslint eslint-plugin-react-hooks eslint-plugin-react-refresh @typescript-eslint/parser typescript
```

`typescript@^5.9.3` goes too. This is not incidental cleanup — `eslint.config.js` states it exists *solely* because `@typescript-eslint/parser` needs the classic TS JS API that TypeScript 7 no longer ships. Removing the parser removes its only consumer.

- [ ] **Step 3: Verify typecheck still works without `typescript@5`**

This is the step that catches it if something other than the ESLint parser depended on TS 5:

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run typecheck && bun run typecheck:e2e
```

Expected: both PASS. If either fails with a missing-module error for `typescript`, something else consumes TS 5 — report it and restore the dep rather than working around it.

- [ ] **Step 4: Delete the three config files**

```bash
cd /home/dannyb/sources/PiFire
rm biome.jsonc web-react/biome.jsonc web-react/eslint.config.js
```

- [ ] **Step 5: Confirm no dangling references remain**

```bash
cd /home/dannyb/sources/PiFire
grep -rn 'biome\|eslint' --include='*.json' --include='*.ts' --include='*.js' --include='*.yml' \
  web-react/package.json package.json web-react/scripts packages mobile .github 2>/dev/null \
  | grep -v node_modules || echo "no dangling references"
```

Expected: `no dangling references`. Any hit is a call site the migration missed.

- [ ] **Step 6: Run the full gate on the new toolchain**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run lint; echo "lint exit=$?"
bun run typecheck && bun run test
```

`bun run lint` is expected to **fail on formatting** here — `oxfmt --check` reports the whole unformatted tree, which Task 5 fixes. `oxlint` itself should be clean. Confirm the failure is purely `oxfmt --check` and not oxlint findings before proceeding.

- [ ] **Step 7: Commit**

```bash
cd /home/dannyb/sources/PiFire
jj describe --stdin <<'MSG'
Remove Biome and ESLint

Deletes all three configs and six dependencies, including typescript@5.9.3,
which existed solely for @typescript-eslint/parser -- the real typecheck
gate is the typescript7 alias and is unaffected.

`bun run lint` becomes `oxlint && oxfmt --check .`, which also fixes the
old chain's short-circuit that hid eslint's state behind biome failures.
MSG
```

---

## Task 5: The repo-wide reflow

Quarantined in its own commit so its wide mechanical diff cannot conceal a substantive hunk. Nothing but formatter output belongs in this commit.

**Files:** every formatted file in the repo. No hand edits.

- [ ] **Step 1: Reformat**

```bash
cd /home/dannyb/sources/PiFire
./web-react/node_modules/.bin/oxfmt .
cd web-react && ./node_modules/.bin/oxfmt .
```

- [ ] **Step 2: Verify the excluded byte-contract files were NOT touched**

The single most important check in this task — these files are digest-pinned and reformatting them breaks tests in a way that looks unrelated:

```bash
cd /home/dannyb/sources/PiFire
jj --no-pager diff --name-only | grep -E 'tests/characterization/fixtures/|schema/|settingsDefaults\.gen\.ts' \
  && echo "VIOLATION: an excluded file was reformatted" || echo "excluded files untouched (correct)"
```

Expected: `excluded files untouched (correct)`. If a byte-contract file was reformatted, the `ignorePatterns` are wrong — revert with `jj restore <path>`, fix the config, and redo this task.

- [ ] **Step 3: Confirm the gate is now fully green**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run lint && bun run typecheck && bun run test
```

Expected: all PASS, including `oxfmt --check` this time.

- [ ] **Step 4: Verify the Python side still passes**

The root config formats JSON outside `web-react/`, so Python-side fixtures and configs were in scope:

```bash
cd /home/dannyb/sources/PiFire
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q 2>&1 | tail -15
```

Expected: no *new* failures versus the pre-migration baseline. If `test_process_command_golden.py` fails, a digest-pinned fixture was reformatted despite Step 2 — treat as a blocker.

- [ ] **Step 5: Commit**

```bash
cd /home/dannyb/sources/PiFire
jj describe --stdin <<'MSG'
Reformat repository with oxfmt

Pure formatter output, no hand edits. Isolated in its own commit so the
mechanical diff cannot conceal a substantive change.
MSG
```

---

## Task 6: Final verification

**Files:** none modified. This task only runs checks.

- [ ] **Step 1: Confirm the negative control still holds post-migration**

Re-plant the probe and confirm all five rules still fire against the *final* config. This guards against a Task 4/5 change having quietly broken config resolution — the configs moved and Biome was deleted since the probe last ran.

Create `/home/dannyb/sources/PiFire/web-react/src/__oxlint_probe.tsx`:

```tsx
import { useEffect, useState } from "react";

export const NOT_A_COMPONENT = 42;

export function ProbeSetStateInEffect({ value }: { value: number }) {
  const [count, setCount] = useState(0);
  // react/set-state-in-effect + react/exhaustive-deps (reads `value`, empty deps)
  useEffect(() => {
    setCount(value);
  }, []);
  return <div>{count}</div>;
}

export function ProbeRulesOfHooks({ flag }: { flag: boolean }) {
  if (flag) {
    // react/rules-of-hooks
    const [x] = useState(0);
    return <div>{x}</div>;
  }
  return null;
}

export function ProbeImmutability() {
  const [state] = useState({ a: 0 });
  // react/immutability
  state.a = 1;
  return <div>{state.a}</div>;
}
```

Then:

```bash
cd /home/dannyb/sources/PiFire/web-react
./node_modules/.bin/oxlint src/__oxlint_probe.tsx 2>&1 | grep -oE "react\([a-z-]+\)" | sort -u
rm src/__oxlint_probe.tsx
test ! -e src/__oxlint_probe.tsx && echo "probe removed"
```

Expected: hits for all five of `set-state-in-effect`, `exhaustive-deps`, `rules-of-hooks`, `immutability`, `only-export-components`, then `probe removed`. A missing rule is a hard failure — report it rather than closing out the migration.

- [ ] **Step 2: Check for unused disable directives**

```bash
cd /home/dannyb/sources/PiFire/web-react
./node_modules/.bin/oxlint --report-unused-disable-directives 2>&1 | tail -20
```

Expected: no unused directives. A hit means a ported suppression names a rule that no longer resolves — a misspelled disable is invisible otherwise.

- [ ] **Step 3: Full gate, main checkout**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run lint && bun run typecheck && bun run typecheck:e2e && bun run test && bun run build
```

Expected: all PASS.

- [ ] **Step 4: Confirm generated types are reproducible**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run gen:types:check
```

Expected: PASS — the committed generated files match what the rewritten emitters produce.

- [ ] **Step 5: Report**

Summarize for the user: the Task 1 Step 6 delta counts vs. what Task 5 actually reformatted, any oxlint findings deferred for their decision, and confirmation that all five negative-control rules fire. Do not claim completion without pasting the actual gate output.

---

## Parallelization

**Recommend fully serial execution.** Every task depends on its predecessor's output:

- Task 2 cannot run before Task 1 installs the binaries.
- Task 3's delete-vs-port decisions depend on Task 2's verified enabled-rule set (Step 3 may add rules the spec predicted were unnecessary).
- Task 4 must not delete ESLint before Task 2 proves the five rules fire.
- Task 5's output depends on the final configs from Tasks 1–4.
- Task 6 verifies the whole chain.

Tasks 1 and 2 touch disjoint files and could nominally overlap, but Task 2 cannot be *verified* without Task 1's binaries, so concurrency buys nothing real.

If any task is nonetheless run in a separate workspace, note that `jj workspace add` does **not** populate `node_modules` (gitignored), so `bun install` must be run in the new workspace before any of these commands will execute. A worktree that skips this reports command-not-found, which must not be read as a passing gate.

## Deviation from the spec's sequencing

The spec sketches three commits; this plan uses five. The spec's actual constraint — that the reflow lands as its own reviewable commit, isolated from substantive changes — is preserved exactly (Task 5). The setup half is split into four commits instead of one because each carries an independently verifiable claim, which the single combined commit would have obscured.
