# Expo Mobile App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Tooling requirement for every dispatched subagent:** use **serena** (`find_symbol`, `get_symbols_overview`, `find_referencing_symbols`) to locate code and **context-mode** (`ctx_batch_execute`, `ctx_execute`, `ctx_execute_file`) for command output and file analysis. Do not sweep the repository with bare `grep`/`cat` into context. LSP tools are available for diagnostics.

**Goal:** Ship a native iOS and Android cook-companion app for PiFire, built with Expo, sharing one implementation of the API contract with the existing web UI.

**Architecture:** The repository becomes a bun workspace. Logic both clients must agree on — generated contract types, the REST command grammar, the SocketIO live connection, and pure display math — moves into `packages/pifire-core` (`@pifire/core`), which is platform-free TypeScript with no React and no DOM. `web-react` is migrated onto it with its test suites green at every step, then a new `mobile/` Expo app consumes the same package. The backend gains no endpoints and no auth; two small Python changes follow the moved files.

**Tech Stack:** bun workspaces, TypeScript, Expo SDK (expo-router, expo-notifications, react-native-svg, react-native-reanimated), socket.io-client, rstest (shared + web), jest-expo + @testing-library/react-native (mobile), EAS Build.

**Spec:** `docs/superpowers/specs/2026-08-18-expo-mobile-app-design.md`

## Global Constraints

- **Version control is jj, not git.** Never run `git add`, `git commit`, `git checkout`, or `git stash`. jj snapshots the working copy automatically — there is no staging step. End a task with `jj commit -m "..."`, which describes the current change and starts a fresh empty one.
- **Package manager is bun**, never npm or yarn. The lockfile is committed.
- **`@pifire/core` is platform-free**: no `react`, no `react-dom`, no `react-native`, no DOM globals (`window`, `document`, `localStorage`), no `import.meta.env`. Its only runtime dependency is `socket.io-client`. A hook belongs in a client, never in core.
- **`packages/pifire-core/src/contracts/` contains only Python-generated files.** `common/web_contracts/export.py` treats every file below its `TYPESCRIPT_DIRECTORY` as its own output; a hand-written file there will be reported as a stray.
- **The grill's build path is not modified.** `pifire_build_web_ui()` in `auto-install/pifire-install-common.sh` keeps its `cd web-react && bun install --frozen-lockfile && bun run build`. Do not add `--filter` — that is a deliberate deferred decision recorded in the spec.
- **web-react stays green throughout Phase 1.** Every task in Phase 1 ends with `bun run typecheck` and `bun run test` **passing** in `web-react`, and `bun run lint` introducing **no new** errors. These suites are the proof that moving code changed no behavior. **Lint baseline: biome reports 12 errors and 1 warning in web-react before any of this work** (verified against a tree that never went through Task 1). Do not fix those; do not let the count grow. A lint error in a file your task touched is yours regardless of the count.
- **Module naming rules from `web-react/README.md` apply to `packages/pifire-core` too**: `camelCase.ts` for non-component logic, no case-folded sibling collisions.
- **No public store submission.** Distribution is EAS `development` and `preview` profiles only.

---

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `package.json` (root) | Workspace declaration and cross-package scripts. No dependencies of its own. |
| `packages/pifire-core/package.json` | `@pifire/core` manifest; subpath exports; `socket.io-client` dependency. |
| `packages/pifire-core/tsconfig.json` | Strict, DOM-free (`lib: ["ES2022"]`) — the compiler is what enforces "platform-free". |
| `packages/pifire-core/rstest.config.ts` | Node-environment test project for the shared suites. |
| `packages/pifire-core/src/contracts/*.gen.ts` | Python-generated contract types (moved, not written by hand). |
| `packages/pifire-core/src/command.ts` | REST command grammar (moved). |
| `packages/pifire-core/src/postControl.ts` | The one `notifyApi` function `command.ts` depends on (moved). |
| `packages/pifire-core/src/liveConnection.ts` | Framework-free SocketIO connection; the shared half of `useLiveState`. |
| `packages/pifire-core/src/gaugeMath.ts` | 270° gauge geometry (moved). |
| `packages/pifire-core/src/dashboard/*.ts` | `deriveView`, `buttonsForMode`, `health` (moved). |
| `packages/pifire-core/src/demoData.ts`, `src/fixture.ts` | Offline cook simulator and captured payload (moved). |
| `mobile/` | The Expo app. Screen-per-file under `app/`, logic under `src/`. |

**Modified:**

| Path | Change |
|---|---|
| `web-react/package.json` | Adds `@pifire/core: workspace:*`; loses `socket.io-client` (now core's). |
| `web-react/src/**`, `web-react/tests/**` | Import rewrites; `useLiveState` rebuilt on `createLiveConnection`. |
| `common/web_contracts/export.py` | `TYPESCRIPT_DIRECTORY` retargeted to the shared package. |
| `common/web_ui_build.py` | `newest_source_mtime` also walks the shared package. |
| `tests/unit/common/web_contracts/test_export.py`, `tests/unit/updater/test_web_ui_build.py` | Follow the two changes above. |

---

# Phase 1 — Workspace and shared core

## Task 1: Turn the repository into a bun workspace

**Files:**
- Create: `package.json` (repo root)
- Move: `web-react/bun.lock` → `bun.lock`
- Modify: `.gitignore`

**Interfaces:**
- Produces: a root workspace whose members are `web-react`, `mobile`, and `packages/*`. Later tasks add members by creating directories with a `package.json`; no root edit is needed per member.

- [ ] **Step 1: Create the root manifest**

`package.json`:

```json
{
  "name": "pifire",
  "private": true,
  "workspaces": ["web-react", "mobile", "packages/*"],
  "scripts": {
    "typecheck": "bun run --filter '*' typecheck",
    "test": "bun run --filter '*' test",
    "lint": "bun run --filter '*' lint"
  }
}
```

- [ ] **Step 2: Move the lockfile to the root**

```bash
mv web-react/bun.lock bun.lock
```

- [ ] **Step 3: Ignore the root install output**

Append to the repository-root `.gitignore`:

```
/node_modules/
```

- [ ] **Step 4: Reinstall and verify web-react is unchanged**

```bash
bun install
cd web-react && bun run typecheck && bun run lint && bun run test
```

Expected: install succeeds writing the root `bun.lock`; all three web-react gates pass exactly as before. `web-react/node_modules` still exists (bun's isolated linker gives each member its own).

- [ ] **Step 5: Verify the grill's build path still works untouched**

```bash
cd web-react && bun install --frozen-lockfile && bun run build
```

Expected: PASS — this is verbatim what `pifire_build_web_ui()` runs. It must succeed with the lockfile at the root and no script change. If it fails, stop: the constraint that the grill build path is unmodified has been broken, and the failure must be fixed here rather than by editing the install script.

- [ ] **Step 6: Commit**

```bash
jj commit -m "build: make the repository a bun workspace

Moves bun.lock to the root and declares web-react, mobile, and packages/*
as workspace members. web-react's own build, typecheck, lint, and test
paths are unchanged, including the frozen-lockfile install the grill runs."
```

---

## Task 2: Create `@pifire/core` and move `gaugeMath`

`gaugeMath` is the smallest genuinely shared module — 41 lines, zero dependencies, already pure. Moving it first proves the whole wiring (exports map, TS resolution, rsbuild transpilation of workspace source, rstest) with one file at risk.

**Files:**
- Create: `packages/pifire-core/package.json`, `packages/pifire-core/tsconfig.json`, `packages/pifire-core/rstest.config.ts`
- Move: `web-react/src/helpers/gaugeMath.ts` → `packages/pifire-core/src/gaugeMath.ts`
- Move: `web-react/tests/unit/helpers/gaugeMath.test.ts` → `packages/pifire-core/tests/gaugeMath.test.ts`
- Modify: `web-react/package.json`, `web-react/src/components/dashboard/GrillGauge.tsx`

**Interfaces:**
- Produces: `@pifire/core` with subpath exports — `import { describeArc } from "@pifire/core/gaugeMath"` resolves to `packages/pifire-core/src/gaugeMath.ts`. Every later module in this package is imported the same way: `@pifire/core/<path under src, without extension>`.

- [ ] **Step 1: Write the package manifest**

`packages/pifire-core/package.json`:

```json
{
  "name": "@pifire/core",
  "version": "0.0.0",
  "private": true,
  "type": "module",
  "exports": {
    "./*": "./src/*.ts"
  },
  "scripts": {
    "typecheck": "node node_modules/typescript7/bin/tsc -b",
    "test": "rstest run"
  },
  "dependencies": {
    "socket.io-client": "^4.8.3"
  },
  "devDependencies": {
    "@rstest/core": "0.11.6",
    "typescript7": "npm:typescript@7.0.2"
  }
}
```

- [ ] **Step 2: Write the compiler config that enforces "platform-free"**

`packages/pifire-core/tsconfig.json` — note `lib` has no `DOM`, which is what makes a stray `window` reference a compile error rather than a code-review catch:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "skipLibCheck": true
  },
  "include": ["src", "tests"]
}
```

- [ ] **Step 3: Write the test config**

`packages/pifire-core/rstest.config.ts`:

```ts
import { defineConfig } from "@rstest/core";

// Everything here is pure logic with no DOM: one node project, no jsdom.
export default defineConfig({
  include: ["tests/**/*.test.ts"],
  testEnvironment: "node",
});
```

- [ ] **Step 4: Move the module and its test**

```bash
mkdir -p packages/pifire-core/src packages/pifire-core/tests
mv web-react/src/helpers/gaugeMath.ts packages/pifire-core/src/gaugeMath.ts
mv web-react/tests/unit/helpers/gaugeMath.test.ts packages/pifire-core/tests/gaugeMath.test.ts
```

Then fix the moved test's import — it becomes `from "../src/gaugeMath"`.

- [ ] **Step 5: Point web-react at the package**

Add to `web-react/package.json` dependencies:

```json
"@pifire/core": "workspace:*"
```

In `web-react/src/components/dashboard/GrillGauge.tsx`, change the first import from `"../../helpers/gaugeMath"` to `"@pifire/core/gaugeMath"`.

- [ ] **Step 6: Install and run both test suites**

```bash
bun install
cd packages/pifire-core && bun run test
cd ../../web-react && bun run typecheck && bun run test
```

Expected: PASS in both. The gauge tests now run from the shared package; web-react's `GrillGauge` tests still pass against the imported geometry.

- [ ] **Step 7: Verify the web bundle actually builds from workspace source**

```bash
cd web-react && bun run build
```

Expected: PASS. rsbuild resolves the workspace symlink to the real path under `packages/`, which is outside `node_modules`, so its SWC transform applies normally.

**If and only if this step fails** with an error about unexpected TypeScript syntax in `packages/pifire-core`, add to `web-react/rsbuild.config.ts` inside the existing `source: {` block:

```ts
    include: [{ and: [/packages[\\/]pifire-core/, { not: /node_modules/ }] }],
```

Then re-run the build and record in the commit message that it was needed.

- [ ] **Step 8: Commit**

```bash
jj commit -m "refactor(core): add @pifire/core and move gaugeMath into it

First module in the shared package: 41 lines of pure gauge geometry,
chosen to prove the exports map, TypeScript resolution, rsbuild
transpilation of workspace source, and the rstest project with minimal
code at risk. The package's tsconfig omits DOM from lib, so a
platform-specific import fails to compile rather than passing review."
```

---

## Task 3: Move contract type generation into the shared package

201 import sites reference the generated contracts across 189 files. They all match one regex regardless of directory depth, so this is a single mechanical rewrite verified by the type checker.

**Files:**
- Modify: `common/web_contracts/export.py:16` (`TYPESCRIPT_DIRECTORY`)
- Modify: `tests/unit/common/web_contracts/test_export.py`
- Move: `web-react/src/helpers/contracts/*.gen.ts` → `packages/pifire-core/src/contracts/`
- Modify (do NOT move): `web-react/tests/unit/helpers/generatedContracts.test.ts`
- Modify: 189 files under `web-react/src` and `web-react/tests`

**Interfaces:**
- Consumes: `@pifire/core` subpath exports from Task 2.
- Produces: `@pifire/core/contracts/<bundle>` for bundles `content`, `control`, `core`, `learning`, `operations`, `wizard`. Example: `import type { DashSocketPayload } from "@pifire/core/contracts/core"`.

- [ ] **Step 1: Write the failing Python test**

In `tests/unit/common/web_contracts/test_export.py`, the existing tests assert paths beginning `web-react/schema/contracts/...`. Add this test beside them:

```python
def test_typescript_artifacts_are_written_to_the_shared_package():
    """The contract types are consumed by both clients, so they live in
    @pifire/core rather than inside one client's source tree."""
    from common.web_contracts.export import TYPESCRIPT_DIRECTORY

    assert TYPESCRIPT_DIRECTORY == Path("packages/pifire-core/src/contracts")
```

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest tests/unit/common/web_contracts/test_export.py::test_typescript_artifacts_are_written_to_the_shared_package -v
```

Expected: FAIL — `assert PosixPath('web-react/src/helpers/contracts') == PosixPath('packages/pifire-core/src/contracts')`.

- [ ] **Step 3: Retarget the exporter**

`common/web_contracts/export.py:16`:

```python
TYPESCRIPT_DIRECTORY = Path("packages/pifire-core/src/contracts")
```

Leave `SCHEMA_DIRECTORY` at `web-react/schema/contracts` — that is deliberate per the spec, since no client reads raw schemas.

- [ ] **Step 4: Move the generated files and regenerate**

```bash
mkdir -p packages/pifire-core/src/contracts
mv web-react/src/helpers/contracts/*.gen.ts packages/pifire-core/src/contracts/
rmdir web-react/src/helpers/contracts
uv run python -m common.web_contracts.export --write
uv run pytest tests/unit/common/web_contracts/ -v
```

Expected: PASS, and `--write` reports no changes beyond the new location — the file contents are identical, only the directory moved.

- [ ] **Step 5: Rewrite every import site**

Every specifier ends in `contracts/<name>.gen`, at depths from `./contracts/` to `../../../../../src/helpers/contracts/`. One regex covers all of them:

```bash
cd web-react
grep -rlE '"[^"]*contracts/[a-z]+\.gen"' src tests --include=*.ts --include=*.tsx \
  | xargs sed -i '' -E 's#"[^"]*contracts/([a-z]+)\.gen"#"@pifire/core/contracts/\1"#g'
```

The `\.gen` in the pattern is load-bearing: it is what keeps the JSON schema paths (`schema/contracts/manifest.json`) untouched.

- [ ] **Step 6: Repoint the generated-contract guard test — it STAYS in web-react**

**Do not move this test.** It imports `scripts/extractWebTransports` from
web-react and the `typescript` package, neither of which `@pifire/core` has.
Moving it would make the shared package depend on a client's scripts, inverting
the one dependency direction this whole design rests on. It stays at
`web-react/tests/unit/helpers/generatedContracts.test.ts`, where both its imports
already resolve, and is repointed at the contracts' new home.

Its `WEB_ROOT` is the web-react root and stays as it is; the schemas stayed in
`web-react` and the TypeScript did not, so add a repository root beside it:

```ts
const REPO_ROOT = join(WEB_ROOT, "..");
```

`MANIFEST_PATH` and `schemaRoot` are unchanged (still under `WEB_ROOT`). Inside
`generatedArtifacts()`, only the TypeScript root moves:

```ts
  const typescriptRoot = join(REPO_ROOT, "packages/pifire-core/src/contracts");
```

`HELPERS_ROOT` — the root scanned for hand-written types that duplicate generated
ones — becomes both trees, so the guard still catches a duplicate written in
either place:

```ts
const SCANNED_ROOTS = [
  HELPERS_ROOT,
  join(REPO_ROOT, "packages/pifire-core/src"),
];
```

and `filesBelow` is called once per entry in `SCANNED_ROOTS`, concatenating the
results. The `extractWebTransports` import is unchanged.

- [ ] **Step 7: Verify everything**

```bash
cd packages/pifire-core && bun run test
cd ../../web-react && bun run typecheck && bun run lint && bun run test
```

Expected: PASS. The type checker visits all 189 rewritten files; a missed or malformed specifier cannot survive this step.

- [ ] **Step 8: Commit**

```bash
jj commit -m "refactor(core): generate contract types into @pifire/core

The Pydantic exporter now writes TypeScript to
packages/pifire-core/src/contracts, and all 201 import sites across
web-react are rewritten to @pifire/core/contracts/<bundle>. The JSON
schemas stay under web-react/schema: clients consume generated types,
never raw schemas, so moving those too would churn the exporter's tests
for no consumer's benefit."
```

---

## Task 4: Move the command grammar

**Files:**
- Move: `web-react/src/helpers/command.ts` → `packages/pifire-core/src/command.ts`
- Move: `web-react/tests/unit/helpers/command.test.ts` → `packages/pifire-core/tests/command.test.ts`
- Create: `packages/pifire-core/src/postControl.ts`
- Modify: `web-react/src/helpers/notify/notifyApi.ts`, plus the 9 source files importing `command`

**Interfaces:**
- Consumes: `@pifire/core/contracts/control`, `@pifire/core/contracts/core`.
- Produces: `@pifire/core/command` exporting `createCommand(apiBase: string): CommandClient`, plus types `CommandClient` and `CommandResult`. `CommandResult` is `{ ok: boolean; message: string; data?: unknown }`.

- [ ] **Step 1: Extract `postControl` into core**

`command.ts` imports `postControl` from `./notify/notifyApi`. Only that one function needs to move. Read `web-react/src/helpers/notify/notifyApi.ts` (51 lines), move the `postControl` function verbatim into `packages/pifire-core/src/postControl.ts`, exporting it unchanged, and have `notifyApi.ts` re-export it for its existing consumers:

```ts
export { postControl } from "@pifire/core/postControl";
```

It uses `fetch`, which exists on React Native, so it needs no adaptation.

- [ ] **Step 2: Move the command module and its test**

```bash
mv web-react/src/helpers/command.ts packages/pifire-core/src/command.ts
mv web-react/tests/unit/helpers/command.test.ts packages/pifire-core/tests/command.test.ts
```

In the moved `command.ts`, the import of `postControl` becomes `from "./postControl"`. Its contract imports are already `@pifire/core/contracts/...` from Task 3. In the moved test, the import of the module under test becomes `from "../src/command"`.

- [ ] **Step 3: Repoint web-react's importers**

```bash
cd web-react
grep -rlE '"[^"]*helpers/command"|"\.\./command"|"\./command"' src tests \
  | xargs sed -i '' -E 's#"[^"]*/command"#"@pifire/core/command"#g'
```

Then read the diff and confirm every rewritten line is a `command` import — this regex is looser than Task 3's, so it is checked by eye as well as by the compiler:

```bash
jj --no-pager diff --git | grep -n '^[+-].*command'
```

- [ ] **Step 4: Run both suites**

```bash
cd packages/pifire-core && bun run test
cd ../../web-react && bun run typecheck && bun run lint && bun run test
```

Expected: PASS. `command.test.ts` is the substantive proof — it covers the timer grammar's non-obvious semantics documented in that file's header comment.

- [ ] **Step 5: Commit**

```bash
jj commit -m "refactor(core): move the REST command grammar into @pifire/core

createCommand and its timer/mode/prime semantics are now shared by both
clients, along with the postControl helper it needs. notifyApi re-exports
postControl so its existing callers are untouched."
```

---

## Task 5: Extract the live connection

The socket wiring is shared; the React hook around it is not. Web keeps a hook that re-renders on payloads; mobile needs one that also reconnects when the app foregrounds. Core owns the part they agree on.

**Files:**
- Create: `packages/pifire-core/src/liveConnection.ts`, `packages/pifire-core/tests/liveConnection.test.ts`
- Move: `web-react/src/helpers/dashboard/health.ts` → `packages/pifire-core/src/dashboard/health.ts` (with its test)
- Modify: `web-react/src/helpers/useLiveState.ts`
- Modify: `web-react/package.json` (drops `socket.io-client`)

**Interfaces:**
- Consumes: `@pifire/core/contracts/core`, `@pifire/core/contracts/control`. Not `command` — the command client is built by the client that owns the hook, not by the connection.
- Produces: `@pifire/core/liveConnection` exporting:

```ts
export type ConnectionPhase = "connecting" | "live" | "unreachable" | "demo";

export interface LiveConnectionHandlers {
  onDash(payload: DashSocketPayload): void;
  onPellets(pellets: PelletSocketPayload["pellets"]): void;
  onPhase(phase: ConnectionPhase): void;
}

export interface LiveConnection {
  /** Force a reconnect — mobile calls this when the app returns to the
   *  foreground and iOS has torn the socket down underneath it. */
  reconnect(): void;
  close(): void;
}

export function createLiveConnection(
  url: string,
  handlers: LiveConnectionHandlers,
): LiveConnection;
```

- [ ] **Step 1: Write the failing test**

`packages/pifire-core/tests/liveConnection.test.ts`. The point of the shared module is the event-to-phase mapping, so that is what is tested — with an injected fake socket factory rather than a real server:

```ts
import { expect, it } from "@rstest/core";
import { createLiveConnection, type ConnectionPhase } from "../src/liveConnection";

function fakeSocket() {
  const handlers: Record<string, (payload?: unknown) => void> = {};
  return {
    socket: {
      on(event: string, handler: (payload?: unknown) => void) {
        handlers[event] = handler;
      },
      emit() {},
      close() {},
      connect() {},
    },
    fire(event: string, payload?: unknown) {
      handlers[event]?.(payload);
    },
  };
}

it("reports live on connect and unreachable on disconnect", () => {
  const phases: ConnectionPhase[] = [];
  const fake = fakeSocket();
  createLiveConnection("http://pi.local:5000", {
    onDash() {},
    onPellets() {},
    onPhase: (phase) => phases.push(phase),
    // @ts-expect-error -- test-only injection seam, see step 3
    createSocket: () => fake.socket,
  });
  fake.fire("connect");
  fake.fire("disconnect");
  expect(phases).toEqual(["live", "unreachable"]);
});

it("does not let a pellet payload claim the dash feed is healthy", () => {
  const phases: ConnectionPhase[] = [];
  const fake = fakeSocket();
  createLiveConnection("http://pi.local:5000", {
    onDash() {},
    onPellets() {},
    onPhase: (phase) => phases.push(phase),
    // @ts-expect-error -- test-only injection seam, see step 3
    createSocket: () => fake.socket,
  });
  fake.fire("socket_pellet_data", { pellets: {} });
  expect(phases).toEqual([]);
});
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd packages/pifire-core && bun run test liveConnection
```

Expected: FAIL — cannot resolve `../src/liveConnection`.

- [ ] **Step 3: Implement it**

`packages/pifire-core/src/liveConnection.ts`. Port the socket body from `web-react/src/helpers/useLiveState.ts:46-70` verbatim — the same events, the same `listen_app_data` emit on connect, and the same deliberate omission of a phase change on `socket_pellet_data`. Replace the `@ts-expect-error` seam above with a real optional fourth field on the handlers object:

```ts
/** Injection seam for tests. Production passes nothing and gets socket.io. */
createSocket?: (url: string) => SocketLike;
```

typed against a minimal `SocketLike` interface (`on`, `emit`, `close`, `connect`) that `socket.io-client`'s `Socket` structurally satisfies. Once the field exists, delete the two `@ts-expect-error` comments from the test.

`reconnect()` closes the current socket and creates a fresh one through the same factory.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd packages/pifire-core && bun run test liveConnection
```

Expected: PASS (2 tests).

- [ ] **Step 5: Move `health.ts` and rebuild web's hook on the shared connection**

```bash
mv web-react/src/helpers/dashboard/health.ts packages/pifire-core/src/dashboard/health.ts
mv web-react/tests/unit/helpers/dashboard/health.test.ts packages/pifire-core/tests/health.test.ts
```

Then rewrite `web-react/src/helpers/useLiveState.ts` so its effect calls `createLiveConnection(TARGET_URL, {...})` with handlers that call the existing `setLive` / `setPellets` / `setPhase`, and its cleanup calls `connection.close()`. Everything else in that file — the demo branch, `TARGET_URL` from `import.meta.env`, the `targetUrl` display fallback, the `LiveStateResult` shape — stays exactly as it is. The build-time env read stays in web-react; core never sees it.

Remove `socket.io-client` from `web-react/package.json` — it is core's dependency now and reaches web-react transitively.

- [ ] **Step 6: Verify**

```bash
bun install
cd packages/pifire-core && bun run test
cd ../../web-react && bun run typecheck && bun run lint && bun run test
```

Expected: PASS, including the existing `tests/unit/helpers/useLiveState.test.tsx`, which is the regression proof that the rewrite preserved the hook's behavior.

- [ ] **Step 7: Commit**

```bash
jj commit -m "refactor(core): extract the SocketIO live connection

createLiveConnection owns the event-to-phase mapping both clients share,
behind an injectable socket factory so it is testable without a server.
useLiveState keeps every React and build-time-env concern, and gains
nothing but a delegate. reconnect() exists for mobile, where iOS tears
the socket down on backgrounding."
```

---

## Task 6: Move the remaining pure display logic

**Files:**
- Move: `deriveView.ts`, `buttonsForMode.ts`, `probeStatus.ts` → `packages/pifire-core/src/dashboard/`
- Move: `demoData.ts`, `fixture.ts` → `packages/pifire-core/src/`
- Move: the five corresponding test files → `packages/pifire-core/tests/`
- Modify: importers in `web-react/src` and `web-react/tests`

**`probeStatus.ts` moves because `deriveView.ts` imports it** (`batteryBadge`,
`connectionBadge`, `probeStatus.ts:1-7`). Nothing else in web-react imports it
except its own test, and it imports nothing but contracts, so it moves cleanly
and no web-react component needs repointing for it. Moving `deriveView` without
it would leave the shared package importing back into a client.

**Interfaces:**
- Produces: `@pifire/core/dashboard/deriveView`, `@pifire/core/dashboard/buttonsForMode`, `@pifire/core/demoData`, `@pifire/core/fixture`. Exported names are unchanged by the move.

- [ ] **Step 1: Move the four modules and their tests**

```bash
mv web-react/src/helpers/dashboard/deriveView.ts \
   web-react/src/helpers/dashboard/buttonsForMode.ts \
   web-react/src/helpers/dashboard/probeStatus.ts \
   packages/pifire-core/src/dashboard/
mv web-react/src/helpers/demoData.ts web-react/src/helpers/fixture.ts packages/pifire-core/src/
mv web-react/tests/unit/helpers/dashboard/deriveView.test.ts \
   web-react/tests/unit/helpers/dashboard/buttonsForMode.test.ts \
   web-react/tests/unit/helpers/dashboard/probeStatus.test.ts \
   web-react/tests/unit/helpers/demoData.test.ts packages/pifire-core/tests/
```

- [ ] **Step 2: Fix the moved files' internal imports**

Within the moved modules, sibling imports become `./` paths inside core (`buttonsForMode` imports `command`, which is now `@pifire/core/command` or the relative `../command` — use the relative form inside the package). The moved tests import from `../src/...`.

`demoData.ts` must not reach for DOM APIs. Its `Date.now()` usage is fine; if the compiler flags anything DOM-shaped, replace it with a `globalThis` equivalent rather than widening the package's `lib`.

- [ ] **Step 3: Repoint web-react's importers**

```bash
cd web-react
grep -rl 'helpers/dashboard/deriveView\|helpers/dashboard/buttonsForMode\|helpers/demoData\|helpers/fixture\|"\.\./deriveView"\|"\./deriveView"' src tests
```

Rewrite each hit by hand to the matching `@pifire/core/...` specifier — this set is small enough (roughly a dozen files) that a hand edit is safer than a regex, and the type checker confirms completeness.

- [ ] **Step 4: Verify**

```bash
cd packages/pifire-core && bun run test
cd ../../web-react && bun run typecheck && bun run lint && bun run test && bun run build
```

Expected: PASS. `bun run build` is included because `fixture.ts` and `demoData.ts` participate in the demo bundle.

- [ ] **Step 5: Confirm the package is genuinely platform-free**

```bash
cd packages/pifire-core
grep -rnE '\b(window|document|localStorage|navigator)\b|import\.meta\.env|from "react' src && echo "FOUND — must be empty" || echo "clean"
bun run typecheck
```

Expected: `clean`, and typecheck passes. The DOM-free `lib` in the tsconfig is the real enforcement; this grep is a readable second opinion.

- [ ] **Step 6: Commit**

```bash
jj commit -m "refactor(core): move deriveView, buttonsForMode, demoData, fixture

Completes the shared surface: both clients now derive display values,
decide which controls a mode offers, and run offline against the same
code. The package compiles without DOM in its lib, so platform leakage
fails the build."
```

---

## Task 7: Teach the grill's staleness check about the shared package

Without this, editing a shared module and updating a grill leaves it serving a bundle built from older sources, with nothing reporting a problem.

**Files:**
- Modify: `common/web_ui_build.py`
- Modify: `tests/unit/updater/test_web_ui_build.py`

**Interfaces:**
- Produces: `source_dirs(repo_root) -> list[str]`, the directories `newest_source_mtime` walks.

- [ ] **Step 1: Write the failing test**

In `tests/unit/updater/test_web_ui_build.py`, beside the existing staleness tests (which use the `repo` fixture and the `write(path, text, mtime)` helper):

```python
def test_a_shared_package_source_touched_after_the_build_triggers_a_rebuild(repo):
    """web-react imports @pifire/core, so a bundle built before a change to
    that package is as stale as one built before a change to web-react."""
    write(os.path.join(repo, "packages/pifire-core/src/command.ts"), mtime=NEWER)
    assert web_ui_needs_rebuild(repo)


def test_the_shared_package_node_modules_does_not_make_the_bundle_look_stale(repo):
    write(os.path.join(repo, "packages/pifire-core/node_modules/dep/index.js"), mtime=NEWER)
    assert not web_ui_needs_rebuild(repo)
```

Reuse whatever constant the neighbouring tests use for a post-build mtime; if they inline a literal, inline the same literal rather than introducing `NEWER`.

- [ ] **Step 2: Run them to verify they fail**

```bash
uv run pytest tests/unit/updater/test_web_ui_build.py -k shared -v
```

Expected: the first FAILS (the walk never sees the file), the second PASSES already (vacuously — nothing under `packages/` is walked yet). Both must pass after the change.

- [ ] **Step 3: Implement**

In `common/web_ui_build.py`, add beside `web_dir`:

```python
def shared_package_dir(repo_root):
    """web-react imports @pifire/core, so that package's sources are the
    bundle's sources too -- an edit there must trigger a rebuild."""
    return os.path.join(repo_root, "packages", "pifire-core")


def source_dirs(repo_root):
    return [web_dir(repo_root), shared_package_dir(repo_root)]
```

and change `newest_source_mtime` to walk each directory in `source_dirs(repo_root)`, keeping the existing `SKIP_DIRS` filtering and the `OSError` tolerance exactly as they are.

- [ ] **Step 4: Run the full updater suite**

```bash
uv run pytest tests/unit/updater/test_web_ui_build.py -v
```

Expected: PASS, all tests including the pre-existing ones.

- [ ] **Step 5: Commit**

```bash
jj commit -m "fix(updater): rebuild the web UI when shared package sources change

newest_source_mtime walked only web-react. With the contract types,
command grammar, and live connection now in packages/pifire-core, a
change there would leave a grill serving a bundle built from older
sources with nothing reporting it."
```

---

## Task 8: Phase 1 regression gate

**Files:** none — this task changes nothing and exists to prove the extraction was behavior-preserving before any mobile work starts.

- [ ] **Step 1: Run every suite the extraction could have broken**

```bash
cd /Users/dannyb/sources/PiFire
uv run pytest tests/unit/common/web_contracts/ tests/unit/updater/ -v
cd packages/pifire-core && bun run typecheck && bun run test
cd ../../web-react && bun run typecheck && bun run lint && bun run test && bun run build
```

Expected: PASS throughout, with `lint` at or below its 12-error baseline.

- [ ] **Step 1b: Verify contract drift detection end to end**

`bun run gen:types:check` shells to a bare `uv run`, which on macOS fails while
building `bluepy` before any contract logic executes — a pre-existing environment
gap, unrelated to this work. Run the two halves explicitly instead, which
together cover the same ground:

```bash
cd /Users/dannyb/sources/PiFire/web-react && bun scripts/emitWebContracts.ts --check
uv run --no-sync python -m common.web_contracts.export --check
```

Expected: both report up to date. If a machine is available where a bare
`uv run` succeeds (Linux, or macOS once `bluepy` carries a platform marker),
run `bun run gen:types:check` there as well and record the result.

- [ ] **Step 2: Run the browser end-to-end suite**

```bash
cd web-react && bun run test:e2e
```

Expected: PASS. These are the only tests that exercise the real bundle in a browser, so they are the last word on whether the moved modules still behave. If a fidelity screenshot test fails, inspect the artifact before assuming a real regression — those baselines are host-dependent and documented as human references.

- [ ] **Step 3: Confirm the grill's exact build invocation**

```bash
cd web-react && bun install --frozen-lockfile && bun run build && test -f dist/index.html && echo OK
```

Expected: `OK`.

- [ ] **Step 4: Record the gate**

No commit is needed if nothing changed. If any fix was required, commit it with a message describing the regression it repaired.

---

# Phase 2 — The Expo app

## Task 9: Scaffold the Expo app

**Files:**
- Create: `mobile/package.json`, `mobile/app.json`, `mobile/tsconfig.json`, `mobile/metro.config.js`, `mobile/jest.config.js`, `mobile/app/_layout.tsx`, `mobile/app/index.tsx`
- Create: `mobile/src/theme.ts`, `mobile/tests/theme.test.ts`
- Modify: `package.json` (repo root) — **restore `mobile` to the `workspaces` array**

**Task 1 removed `mobile` from the root `workspaces` array** because bun errors on
a literal workspace path that does not exist. Now that `mobile/package.json`
exists, add it back and re-run `bun install` from the repository root. Until you
do, `@pifire/core` will not resolve from `mobile/` and Step 8 cannot pass.

**Interfaces:**
- Produces: a running Expo app; `mobile/src/theme.ts` exporting `THEME` with accent tokens keyed `ember | ice | crimson`, each `{ accent, background, surface, text, danger }`.

- [ ] **Step 1: Scaffold**

```bash
cd /Users/dannyb/sources/PiFire
bunx create-expo-app@latest mobile --template blank-typescript
cd mobile && bunx expo install expo-router react-native-safe-area-context react-native-screens \
  react-native-svg react-native-reanimated expo-notifications @react-native-async-storage/async-storage
bun add @pifire/core@workspace:*
bun add -d jest jest-expo @testing-library/react-native @types/jest
```

- [ ] **Step 2: Configure Metro for the workspace**

`mobile/metro.config.js` — without this, Metro cannot follow the symlink out of `mobile/` into `packages/pifire-core`:

```js
const { getDefaultConfig } = require("expo/metro-config");
const path = require("node:path");

const projectRoot = __dirname;
const workspaceRoot = path.resolve(projectRoot, "..");

const config = getDefaultConfig(projectRoot);
// Metro must watch the whole workspace: @pifire/core lives outside mobile/.
config.watchFolders = [workspaceRoot];
config.resolver.nodeModulesPaths = [
  path.resolve(projectRoot, "node_modules"),
  path.resolve(workspaceRoot, "node_modules"),
];
config.resolver.unstable_enablePackageExports = true;

module.exports = config;
```

- [ ] **Step 3: Configure the test runner**

`mobile/jest.config.js`:

```js
module.exports = {
  preset: "jest-expo",
  setupFilesAfterEnv: ["@testing-library/react-native/extend-expect"],
  // @pifire/core ships TypeScript source, and Expo's preset does not
  // transform node_modules by default.
  transformIgnorePatterns: [
    "node_modules/(?!((jest-)?react-native|@react-native(-community)?|expo(nent)?|@expo(nent)?/.*|@pifire/.*|react-navigation|@react-navigation/.*|@unimodules/.*|unimodules|sentry-expo|native-base|react-native-svg))",
  ],
};
```

Add `"test": "jest"` to `mobile/package.json` scripts.

- [ ] **Step 4: Write the failing theme test**

`mobile/tests/theme.test.ts`:

```ts
import { THEME } from "../src/theme";

it("carries the three PiFire accents", () => {
  expect(Object.keys(THEME)).toEqual(["ember", "ice", "crimson"]);
});

it("gives every accent a full token set", () => {
  for (const tokens of Object.values(THEME)) {
    expect(tokens).toEqual(
      expect.objectContaining({
        accent: expect.stringMatching(/^#/),
        background: expect.stringMatching(/^#/),
        surface: expect.stringMatching(/^#/),
        text: expect.stringMatching(/^#/),
        danger: expect.stringMatching(/^#/),
      }),
    );
  }
});
```

- [ ] **Step 5: Run it to verify it fails**

```bash
cd mobile && bun run test
```

Expected: FAIL — cannot resolve `../src/theme`. The runner itself must start; if jest does not run at all, fix the config from Step 3 before continuing.

- [ ] **Step 6: Implement the theme**

`mobile/src/theme.ts` — port the literal values from `web-react/src/theme.css`'s accent blocks. Read that file and copy the hex values; do not invent colors.

- [ ] **Step 7: Verify the test passes and the app runs**

```bash
cd mobile && bun run test
bunx expo start
```

Expected: tests PASS; the app opens in the iOS simulator or Android emulator showing the default screen.

- [ ] **Step 8: Verify the shared package actually imports on-device**

Add to `mobile/app/index.tsx` a temporary render of a value derived from core:

```tsx
import { valueAngle } from "@pifire/core/gaugeMath";
// ...
<Text>{String(valueAngle(225, 600))}</Text>
```

Reload the app and confirm a number renders. This is the one check that Metro resolution, package exports, and TypeScript source transpilation all work together on a device. Remove the temporary render once confirmed.

- [ ] **Step 9: Commit**

```bash
jj commit -m "feat(mobile): scaffold the Expo app

expo-router, react-native-svg, Reanimated, notifications, and async
storage, with Metro configured to resolve @pifire/core out of the
workspace root. The PiFire accent tokens are ported from theme.css."
```

---

## Task 10: The Connect screen

**Files:**
- Create: `mobile/src/host.ts`, `mobile/tests/host.test.ts`, `mobile/app/connect.tsx`

**Interfaces:**
- Produces: `normalizeHost(input: string): string | null` — returns a URL with scheme and no trailing slash, or `null` when the input cannot be a host. `loadHosts(): Promise<string[]>` and `rememberHost(url: string): Promise<string[]>` over AsyncStorage, most-recent first, capped at 5. The head of that list is the active host.

**No service discovery.** PiFire advertises no mDNS service — the repository's
only zeroconf code (`notify/wled_discovery.py`) is a client that finds WLED
devices. Do not add a discovery library. The field defaults to `pifire.local`
and the OS resolves the hostname avahi publishes on Raspberry Pi OS.

- [ ] **Step 1: Write the failing test**

`mobile/tests/host.test.ts`:

```ts
import { normalizeHost } from "../src/host";

it("adds the default scheme and port to a bare host", () => {
  expect(normalizeHost("pifire.local")).toBe("http://pifire.local:5000");
});

it("keeps an explicit scheme and port", () => {
  expect(normalizeHost("https://grill.example:8443")).toBe("https://grill.example:8443");
});

it("strips a trailing slash so the API base never doubles it", () => {
  expect(normalizeHost("http://10.0.0.5:5000/")).toBe("http://10.0.0.5:5000");
});

it("rejects input that cannot be a host", () => {
  expect(normalizeHost("   ")).toBeNull();
  expect(normalizeHost("http://")).toBeNull();
});

it("remembers most-recent-first without duplicates", async () => {
  await rememberHost("http://a.local:5000");
  await rememberHost("http://b.local:5000");
  expect(await rememberHost("http://a.local:5000")).toEqual([
    "http://a.local:5000",
    "http://b.local:5000",
  ]);
});
```

Import `rememberHost` alongside `normalizeHost`, and mock
`@react-native-async-storage/async-storage` with its official jest mock
(`jest.mock("@react-native-async-storage/async-storage", () => require("@react-native-async-storage/async-storage/jest/async-storage-mock"))`).

- [ ] **Step 2: Run it to verify it fails**

```bash
cd mobile && bun run test host
```

Expected: FAIL — cannot resolve `../src/host`.

- [ ] **Step 3: Implement `mobile/src/host.ts`**

`normalizeHost` as specified above; port 5000 is the default because that is what `gunicorn` binds in `auto-install/supervisor/webapp.conf`. `loadHosts`/`rememberHost` wrap AsyncStorage under the key `pifire.hosts`, storing a JSON array, most-recent first, deduplicated, capped at 5.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd mobile && bun run test host
```

Expected: PASS (5 tests).

- [ ] **Step 5: Build the screen**

`mobile/app/connect.tsx`: a text input defaulting to `pifire.local` (or the most recent stored host when one exists), the remembered hosts listed as one-tap options beneath it, a Connect button that normalizes and remembers, and an inline error when `normalizeHost` returns null. On success it routes to the dashboard. This screen is also where an unreachable connection sends the user back to, with the reason shown.

- [ ] **Step 6: Verify by hand against a real backend**

Start the backend per `web-react/README.md`:

```bash
cd /Users/dannyb/sources/PiFire
uv run python control.py &
uv run gunicorn -k gthread --threads 25 -b 0.0.0.0:5000 -w 1 app:app &
curl -s http://localhost:5000/api/current | head -c 80
```

Expected: JSON with a `current` object. Then enter the machine's LAN IP in the app and confirm it advances past Connect. A simulator cannot reach `localhost` on the host machine by that name — use the actual LAN address.

- [ ] **Step 7: Commit**

```bash
jj commit -m "feat(mobile): host entry and connection screen

normalizeHost is the single place a typed host becomes an API base:
default scheme, default port 5000 to match the gunicorn bind, and no
trailing slash so the command client never builds a doubled path."
```

---

## Task 11: Live data on the phone

**Files:**
- Create: `mobile/src/useLive.ts`, `mobile/tests/useLive.test.tsx`
- Modify: `mobile/app/_layout.tsx`

**Interfaces:**
- Consumes: `@pifire/core/liveConnection`, `@pifire/core/command`, `@pifire/core/dashboard/health`.
- Produces: `useLive(host: string)` returning `{ live, phase, controlAlive, pellets, command, lastPayloadAt }`, where `lastPayloadAt` is an epoch-millisecond timestamp used to render staleness.

- [ ] **Step 1: Write the failing test**

`mobile/tests/useLive.test.tsx` — the mobile-specific behavior is foregrounding, so that is what is tested:

```tsx
import { AppState } from "react-native";
import { act, renderHook } from "@testing-library/react-native";
import { useLive } from "../src/useLive";

jest.mock("@pifire/core/liveConnection", () => ({
  createLiveConnection: jest.fn(() => ({ reconnect: jest.fn(), close: jest.fn() })),
}));

it("reconnects when the app returns to the foreground", () => {
  const { createLiveConnection } = require("@pifire/core/liveConnection");
  renderHook(() => useLive("http://pifire.local:5000"));
  const connection = createLiveConnection.mock.results[0].value;

  act(() => {
    AppState.emit("change", "background");
    AppState.emit("change", "active");
  });

  expect(connection.reconnect).toHaveBeenCalledTimes(1);
});
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd mobile && bun run test useLive
```

Expected: FAIL — cannot resolve `../src/useLive`.

- [ ] **Step 3: Implement `mobile/src/useLive.ts`**

One effect creates the connection via `createLiveConnection(host, handlers)` and closes it on unmount. A second effect subscribes to `AppState` and calls `connection.reconnect()` on the transition into `active` — iOS suspends the socket while backgrounded and does not always surface a `disconnect`. Handlers set state and stamp `lastPayloadAt` on each dash payload. `controlAlive` comes from `deriveControlAlive`.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd mobile && bun run test useLive
```

Expected: PASS.

- [ ] **Step 5: Wire it into the layout and show connection state**

`mobile/app/_layout.tsx` provides the `useLive` result through React context to the screens. Render a persistent status strip: `live`, `connecting`, or `unreachable`, and when the newest payload is older than 30 seconds, the age in seconds. Never render a temperature without that staleness marker when the feed is stale.

- [ ] **Step 6: Verify against the running backend**

With the backend from Task 10 still running, open the app and confirm live values arrive and update. Then background the app for a minute and foreground it: the status must return to `live` without a manual reload.

- [ ] **Step 7: Commit**

```bash
jj commit -m "feat(mobile): live dashboard data with foreground reconnect

useLive wraps the shared connection with the one thing the phone needs
and the browser does not: iOS suspends the socket on background, so the
hook reconnects on the AppState transition to active and surfaces the
age of the newest payload rather than presenting stale readings as
current."
```

---

## Task 12: The gauge

**Files:**
- Create: `mobile/src/components/GrillGauge.tsx`, `mobile/tests/GrillGauge.test.tsx`

**Interfaces:**
- Consumes: `@pifire/core/gaugeMath` — `describeArc`, `arcLength`, `valueAngle`, `polarToCartesian`, `clampFraction`.
- Produces: `<GrillGauge temp stale setpoint maxTemp frac hasSetpoint modeLabel units cooking animate />` — the same prop set as the web component (`web-react/src/components/dashboard/GrillGauge.tsx:1-18`), so `deriveView`'s output feeds it unchanged. `stale` is the staleness marker string, or null.

- [ ] **Step 1: Write the failing test**

`mobile/tests/GrillGauge.test.tsx`:

```tsx
import { render } from "@testing-library/react-native";
import { GrillGauge } from "../src/components/GrillGauge";

it("renders the temperature and mode", () => {
  const { getByText } = render(
    <GrillGauge temp={225} stale={null} setpoint={225} maxTemp={600} frac={0.375}
      hasSetpoint modeLabel="Hold" units="F" cooking animate={false} />,
  );
  expect(getByText("225")).toBeTruthy();
  expect(getByText("Hold")).toBeTruthy();
});

it("marks a carried-over reading as stale", () => {
  const { getByText } = render(
    <GrillGauge temp={225} stale="last data 47s ago" setpoint={225} maxTemp={600}
      frac={0.375} hasSetpoint modeLabel="Hold" units="F" cooking={false} animate={false} />,
  );
  expect(getByText("last data 47s ago")).toBeTruthy();
});
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd mobile && bun run test GrillGauge
```

Expected: FAIL — cannot resolve the component.

- [ ] **Step 3: Implement with `react-native-svg`**

Port `web-react/src/components/dashboard/GrillGauge.tsx` structurally: same `CX`/`CY`/`R` constants, same `describeArc(CX, CY, R, -135, 135)` track, same `arcLength`-and-offset value arc via `strokeDasharray`/`strokeDashoffset`, same setpoint tick from `valueAngle`. Swap `<svg>/<path>/<circle>` for the `react-native-svg` equivalents and the CSS transition for a Reanimated `withTiming(frac, { duration: 250, easing: Easing.out(Easing.cubic) })` driving the dash offset. The glow pulse is a looped opacity animation on the arc, run only when `cooking && animate`.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd mobile && bun run test GrillGauge
```

Expected: PASS (2 tests).

- [ ] **Step 5: Compare against the web gauge by eye**

Run the app beside `bun run demo` in `web-react` and confirm the sweep direction, the 270° span with its bottom gap, the setpoint tick position, and the ease timing read as the same instrument.

- [ ] **Step 6: Commit**

```bash
jj commit -m "feat(mobile): the 270-degree grill gauge

Geometry comes from @pifire/core/gaugeMath, so web and mobile cannot
disagree about where a temperature sits on the arc. Only the rendering
and animation are platform code: react-native-svg for the arcs and
Reanimated for the 250ms OutCubic value sweep and the glow pulse."
```

---

## Task 13: The dashboard

**Files:**
- Create: `mobile/app/(tabs)/index.tsx`, `mobile/src/components/ProbeCard.tsx`, `mobile/src/components/ControlRow.tsx`, `mobile/src/components/SetpointModal.tsx`
- Create: `mobile/tests/ControlRow.test.tsx`, `mobile/tests/ProbeCard.test.tsx`

**Interfaces:**
- Consumes: `@pifire/core/dashboard/deriveView`, `@pifire/core/dashboard/buttonsForMode` (`buttonsForMode(dash)` returning the `ControlButton[]` and `MenuItem[]` the web dashboard uses), `@pifire/core/command`.
- Produces: the app's primary screen.

- [ ] **Step 1: Write the failing control-row test**

`mobile/tests/ControlRow.test.tsx`:

```tsx
import { fireEvent, render } from "@testing-library/react-native";
import { ControlRow } from "../src/components/ControlRow";

const command = { setMode: jest.fn().mockResolvedValue({ ok: true, message: "" }) };

it("sends the mode a pressed button names", async () => {
  const { getByText } = render(
    <ControlRow dash={dashInMode("Stop")} command={command as never} disabled={false} />,
  );
  fireEvent.press(getByText("Startup"));
  expect(command.setMode).toHaveBeenCalledWith("Startup");
});
```

Build `dashInMode` on top of `@pifire/core/fixture`'s `FIXTURE_DASH`, overriding only the mode — that fixture is a real captured `socket_dash_data` payload, so the test exercises the real shape.

- [ ] **Step 2: Run it to verify it fails**

```bash
cd mobile && bun run test ControlRow
```

Expected: FAIL — cannot resolve the component.

- [ ] **Step 3: Implement the control row**

It renders exactly what `buttonsForMode` returns — the mode-to-controls decision is shared logic and must not be re-derived here. Destructive actions (`Stop`, `Shutdown`) confirm before dispatching. Buttons disable while `phase !== "live"`.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd mobile && bun run test ControlRow
```

Expected: PASS.

- [ ] **Step 5: Write the failing probe-card test, then implement it**

`mobile/tests/ProbeCard.test.tsx` asserts a probe renders its name, temperature, and target, and that a carried-over reading shows its staleness marker:

```tsx
import { render } from "@testing-library/react-native";
import { ProbeCard } from "../src/components/ProbeCard";

it("shows the probe name, reading, and target", () => {
  const { getByText } = render(
    <ProbeCard name="Brisket" temp={165} target={203} units="F" stale={null} />,
  );
  expect(getByText("Brisket")).toBeTruthy();
  expect(getByText("165")).toBeTruthy();
  expect(getByText(/203/)).toBeTruthy();
});

it("marks a stale reading", () => {
  const { getByText } = render(
    <ProbeCard name="Brisket" temp={165} target={203} units="F" stale="last data 47s ago" />,
  );
  expect(getByText("last data 47s ago")).toBeTruthy();
});
```

Run it (`bun run test ProbeCard`), watch it fail, then implement the component and watch it pass.

- [ ] **Step 6: Assemble the screen**

`app/(tabs)/index.tsx`: gauge, probe cards, control row, hopper level, and a setpoint modal that calls `command.hold(tempF)` with the same floor and ceiling the web modal enforces (`HOLD_PROMPT_MIN` of 125°F / 50°C, ceiling from the grill's shutdown limit).

- [ ] **Step 7: Verify against the running backend**

Drive a real mode change from the phone — Startup, then Hold at a setpoint — and confirm the web UI reflects it. This is the proof that the shared command grammar works unmodified from a native client.

- [ ] **Step 8: Commit**

```bash
jj commit -m "feat(mobile): the dashboard screen

Gauge, probe cards, hopper, and the mode-driven control row. Which
buttons a mode offers comes from @pifire/core/buttonsForMode and the
writes go through the shared command client, so a native press and a
browser click reach the backend as the same request."
```

---

## Task 14: History

**Files:**
- Create: `mobile/app/(tabs)/history.tsx`, `mobile/src/components/HistoryChart.tsx`, `mobile/tests/HistoryChart.test.tsx`

**Interfaces:**
- Consumes: `web-react/src/components/history/historyAdapter.ts`'s shaping logic. If it is pure, move it to `packages/pifire-core/src/history/historyAdapter.ts` as part of this task, updating web's import and running web's suite. If it turns out to touch uPlot types, leave it and port only the shaping it performs.

- [ ] **Step 1: Determine whether the adapter is portable**

```bash
cd /Users/dannyb/sources/PiFire/web-react
grep -nE "uplot|uPlot|document|window" src/components/history/historyAdapter.ts
```

If clean, move it into core and repoint web's import; if not, keep it in place. Record which branch was taken in the commit message.

- [ ] **Step 2: Write the failing chart test**

`mobile/tests/HistoryChart.test.tsx` asserts that a series of readings produces one path per probe and that an empty series renders an explicit empty state rather than a blank box:

```tsx
import { render } from "@testing-library/react-native";
import { HistoryChart } from "../src/components/HistoryChart";

it("renders one line per series", () => {
  const { getAllByTestId } = render(
    <HistoryChart series={[{ label: "Grill", points: [[0, 200], [60, 225]] },
                           { label: "Brisket", points: [[0, 40], [60, 55]] }]} />,
  );
  expect(getAllByTestId("history-line")).toHaveLength(2);
});

it("says so when there is nothing to plot", () => {
  const { getByText } = render(<HistoryChart series={[]} />);
  expect(getByText(/no history/i)).toBeTruthy();
});
```

- [ ] **Step 3: Run it to verify it fails**

```bash
cd mobile && bun run test HistoryChart
```

Expected: FAIL.

- [ ] **Step 4: Implement the chart**

A `react-native-svg` line chart: scale each series to the view box, render one `<Path testID="history-line">` per series, axis labels for time and temperature, and a legend. Pan and zoom are explicitly out of scope for v1 per the spec — do not add them.

- [ ] **Step 5: Verify tests and appearance**

```bash
cd mobile && bun run test HistoryChart
```

Expected: PASS. Then view a real cook's history in the app against the same range in the web UI and confirm the curves agree.

- [ ] **Step 6: Commit**

```bash
jj commit -m "feat(mobile): history chart

uPlot is DOM-only, so the chart itself is a react-native-svg
reimplementation rather than a port. v1 targets a readable chart, not
uPlot's interaction model."
```

---

## Task 15: Events and local alerts

**Files:**
- Create: `mobile/app/(tabs)/events.tsx`, `mobile/src/alerts.ts`, `mobile/tests/alerts.test.ts`

**Interfaces:**
- Produces: `alertsFor(previous, next): Alert[]` — a pure function comparing two dash payloads and returning the alerts the transition warrants. `Alert` is `{ id: string; title: string; body: string }`.

- [ ] **Step 1: Write the failing test**

`mobile/tests/alerts.test.ts` — purity is what makes this testable, so the diffing lives in a function, not in an effect:

```ts
import { alertsFor } from "../src/alerts";
import { FIXTURE_DASH } from "@pifire/core/fixture";

it("alerts once when a probe reaches its target", () => {
  const before = withProbeTemp(FIXTURE_DASH, "Brisket", 200);
  const after = withProbeTemp(before, "Brisket", 204);
  const alerts = alertsFor(before, after);
  expect(alerts).toHaveLength(1);
  expect(alerts[0].title).toMatch(/Brisket/);
});

it("does not re-alert while the probe stays at target", () => {
  const at = withProbeTemp(FIXTURE_DASH, "Brisket", 204);
  expect(alertsFor(at, withProbeTemp(at, "Brisket", 205))).toEqual([]);
});

it("alerts on a grill error", () => {
  const alerts = alertsFor(FIXTURE_DASH, withError(FIXTURE_DASH, "GRILL_ERROR_01"));
  expect(alerts.map((a) => a.id)).toContain("GRILL_ERROR_01");
});
```

Write `withProbeTemp` and `withError` as local helpers that clone the fixture and set one field.

- [ ] **Step 2: Run it to verify it fails**

```bash
cd mobile && bun run test alerts
```

Expected: FAIL.

- [ ] **Step 3: Implement `alertsFor` and wire it to notifications**

`alertsFor` covers probe-target-reached, timer expiry, and grill errors, keyed by a stable id so a reconnect that replays state raises nothing. A thin effect in the layout calls it on each payload and hands each alert to `expo-notifications` — the effect contains no decision logic.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd mobile && bun run test alerts
```

Expected: PASS (3 tests).

- [ ] **Step 5: Build the events screen and state the limitation**

`app/(tabs)/events.tsx` lists recent events from the existing events API. On it, and on the preferences screen from Task 16, state plainly that alerts arrive only while the app is running, and that PiFire's server-side notification services remain the reliable path.

- [ ] **Step 6: Verify on a device**

Trigger a test notification from PiFire and confirm a local notification appears while the app is foregrounded, and that no duplicate arrives after backgrounding and returning.

- [ ] **Step 7: Commit**

```bash
jj commit -m "feat(mobile): event list and local notifications

alertsFor is a pure diff of two dash payloads, keyed by stable event ids
so a reconnect that replays state raises nothing. The app says plainly
that this only works while it is running: server-side apprise and
pushover remain the reliable path."
```

---

## Task 16: Preferences

**Files:**
- Create: `mobile/app/(tabs)/settings.tsx`, `mobile/src/prefs.ts`, `mobile/tests/prefs.test.ts`

**Interfaces:**
- Produces: `loadPrefs(): Promise<Prefs>` / `savePrefs(p: Prefs): Promise<void>`, `Prefs` being `{ host: string | null; accent: "ember" | "ice" | "crimson"; alerts: boolean }`.

- [ ] **Step 1: Write the failing test**

```ts
import { defaultPrefs, mergePrefs } from "../src/prefs";

it("falls back to defaults for anything missing or unknown", () => {
  expect(mergePrefs({ accent: "nonsense" })).toEqual(defaultPrefs);
});

it("keeps a valid stored accent", () => {
  expect(mergePrefs({ accent: "ice" }).accent).toBe("ice");
});
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd mobile && bun run test prefs
```

Expected: FAIL.

- [ ] **Step 3: Implement and pass**

`mergePrefs` validates each field against the allowed set rather than trusting stored JSON — an app updated past a renamed accent must not render an undefined theme.

- [ ] **Step 4: Build the screen**

Host (reusing `normalizeHost`, with a "change grill" flow back to Connect), accent picker applying live, alert toggle, and a short note that grill configuration lives in the web UI.

- [ ] **Step 5: Verify**

```bash
cd mobile && bun run test
```

Expected: PASS, whole mobile suite. Then change the accent in the app and confirm the gauge and controls recolor immediately.

- [ ] **Step 6: Commit**

```bash
jj commit -m "feat(mobile): preferences screen

Host, accent, and alert toggle, with stored values validated on read so
an app updated past a renamed token cannot render an undefined theme."
```

---

## Task 17: Builds and documentation

**Files:**
- Create: `mobile/eas.json`, `mobile/README.md`
- Modify: `README.md` (repo root), `web-react/README.md`

**Interfaces:** none — this task ships the app and records how to work on it.

- [ ] **Step 1: Write the EAS profiles**

`mobile/eas.json`:

```json
{
  "cli": { "version": ">= 5.0.0" },
  "build": {
    "development": {
      "developmentClient": true,
      "distribution": "internal"
    },
    "preview": {
      "distribution": "internal",
      "android": { "buildType": "apk" }
    }
  }
}
```

No `production` profile: store submission is explicitly out of scope.

- [ ] **Step 2: Produce an Android build**

```bash
cd mobile && bunx eas build --platform android --profile preview
```

Expected: a downloadable APK. Install it on a phone on the same LAN as the grill and run a full cook interaction: connect, view live data, change mode, receive an alert.

- [ ] **Step 3: Attempt the iOS build**

```bash
cd mobile && bunx eas build --platform ios --profile preview
```

This requires an Apple Developer account. If none is available, stop here and record it: the Android artifact plus a simulator-verified iOS build is the completed state of this task, and the spec names this as a known blocker rather than a defect.

- [ ] **Step 4: Write `mobile/README.md`**

Cover: `bun install` from the repo root; `bunx expo start`; running against a real PiFire versus the shared demo simulator; how Metro resolves `@pifire/core`; `bun run test`; the two EAS profiles; and the Apple account prerequisite.

- [ ] **Step 5: Update the surrounding documentation**

In the root `README.md`, add the mobile app to the feature list. In `web-react/README.md`, correct the sections that describe the app as standalone: `bun install` now runs from the repository root, and the shared modules live in `@pifire/core`.

- [ ] **Step 6: Final full verification**

```bash
cd /Users/dannyb/sources/PiFire
uv run pytest tests/unit/common/web_contracts/ tests/unit/updater/ -v
cd packages/pifire-core && bun run typecheck && bun run test
cd ../../web-react && bun run typecheck && bun run lint && bun run test && bun run build
cd ../mobile && bun run test
```

Expected: PASS throughout.

- [ ] **Step 7: Commit and bookmark**

```bash
jj commit -m "docs(mobile): build profiles and documentation

EAS development and preview profiles only -- no production profile,
since store submission is out of scope. Documents the Apple Developer
account prerequisite for durable iOS installs and corrects web-react's
README where it described itself as a standalone project."
jj bookmark create expo-mobile-app -r @-
```

Leave pushing to the user.

---

## Verification Summary

| Gate | Command |
|---|---|
| Shared package | `cd packages/pifire-core && bun run typecheck && bun run test` |
| Web client | `cd web-react && bun run typecheck && bun run lint && bun run test && bun run build` |
| Web end-to-end | `cd web-react && bun run test:e2e` |
| Mobile | `cd mobile && bun run test` |
| Python | `uv run pytest tests/unit/common/web_contracts/ tests/unit/updater/` |
| Grill build path | `cd web-react && bun install --frozen-lockfile && bun run build && test -f dist/index.html` |
