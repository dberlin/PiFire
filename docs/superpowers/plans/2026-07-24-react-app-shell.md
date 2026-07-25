# React App Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `templates/base.html` — the navbar, the timer bar/modal, and the global alert strip — so the React app has an app shell and every page is reachable by clicking.

**Architecture:** A layout route wraps the top-level pages and owns the single live-data subscription; child routes consume it via `Outlet` context. `/wizard` stays outside the layout.

**Tech Stack:** React 19 + react-router, TS7, rsbuild, Biome, @rstest/core, Playwright, bun.

## Why this exists

The React app has **no global navigation at all**. `/settings` and `/history` are reachable only by typing a URL; the only in-app links anywhere are `PlatformTab.tsx:47` (→ `/wizard`) and `InstallProgress.tsx:69,72`. This was never in the backlog — every prior plan was scoped to one surface and stopped at "register the route", so nav had no slot to live in. It went unnoticed because `app.py` does not serve `web-react/dist`; every page has been reached by a URL or by a Playwright `page.goto()`.

## Global Constraints

- Test runner is **@rstest/core** (`rs.fn`, `rs.mock`) — `vi` does NOT exist. `.test.tsx` → jsdom, `.test.ts` → node.
- **bun**, never npm.
- **No suppressions**: no `biome-ignore`, no `@ts-expect-error`, no `eslint-disable`.
- **No `setState` in `useEffect` for derived state** (React Compiler). Render-phase adjustment — see `settings/tabs/SafetyTab.tsx`, `dashboard/SetpointEntry.tsx`.
- `react-refresh/only-export-components`: non-components go in their own module.
- Gate: `bun run typecheck && bun run lint && bun run test && bun run build`. Lint exits 0 (2 known pre-existing warnings; add none).
- Reuse the existing `pf-*` class vocabulary and theme tokens. Do not introduce a second visual language.

## THE CRITICAL CONSTRAINT — one socket, not two

`web-react/src/helpers/useDashData.ts:22` opens **its own socket.io connection on every call** (`io(TARGET_URL, …)` inside its effect). The shell needs live data (timer state, errors/warnings) and `Dashboard` already calls it. **Calling the hook in both places opens two sockets and doubles the `listen_app_data` stream.**

So the shell owns the single subscription and passes it down. `SettingsShell.tsx` + the settings tabs already establish the house pattern for this (`useOutletContext`). Follow it.

**CORRECTION (2026-07-24, found during Task 0 — this plan was wrong):** the
existing call site is **`DashboardRoute.tsx:12`**, not `Dashboard.tsx`.
`Dashboard` is already a pure props-driven component. Task 5 is therefore
*easier* than described, but its Step 4 must target `DashboardRoute.tsx` and
must **preserve that file's `first_time_setup` redirect effect**. The
`<Banners>` half of Step 4 still applies to `Dashboard.tsx:85`.

## THE NAME — `useDashData` is wrong once the shell owns it

The hook stops being "the dashboard's data" the moment it feeds the navbar, the
timer bar and the global alert strip. It becomes the app's single live-state
subscription. Renamed in Task 0, before anything else, so no task builds against
the old name and nothing lands half-renamed:

| Old | New |
|---|---|
| `helpers/useDashData.ts` | `helpers/useLiveState.ts` |
| `useDashData()` | `useLiveState()` |
| type `DashData` | type `LiveState` |
| returned field `dash` | `live` |

`socket_dash_data` is the **backend wire event name** — leave it exactly as it
is. Renaming it would be a protocol change, not a refactor.

## Verified facts (checked against live code — do not re-derive, do not guess)

**Nav** (`templates/base.html:63-82`): Dashboard `/`, Recipes `/recipes`, History `/history`, Events `/events`, Settings `/settings`, Admin `/admin`. Active state via `request.path`. Brand (`:41-49`) = launcher icon + "PiFire" + grill name, links `/`. Right side (`:50-61`) = stopwatch toggle + hamburger.

**Decision:** show all 6; **Recipes, Events and Admin render disabled** (not links) since they are not ported. Do not link them to the Flask pages.

**Timer data** already arrives in the dash payload — `helpers/types.ts:71`,
`blueprints/mobile/socket_io.py:249-255`:
`timer: { start, paused, end, keepWarm, shutdown }`, epoch **seconds**, `math.trunc`'d.

Derived states: `start == 0` → stopped; `start > 0 && paused == 0` → running; `paused > 0` → paused. Remaining = `end - now`, or `end - paused` while paused.

**Timer commands — REST, verified in `common/api_commands.py:526-600`:**

| Path | Behaviour |
|---|---|
| `/api/set/timer/start/{seconds}` | new timer when `paused == 0`; **when `paused != 0` this is UNPAUSE and `{seconds}` is ignored** |
| `/api/set/timer/pause` | pauses — **but if `start == 0` it CLEARS everything instead** (logs "Timer cleared") |
| `/api/set/timer/stop` | clears timer **and resets `shutdown`/`keep_warm` to False** |
| `/api/set/timer/shutdown/{true\|false}` | sets the flag only |
| `/api/set/timer/keep_warm/{true\|false}` | sets the flag only |

Consequences to design around:
- `start` does **not** touch the flags, so "set flags, then start" is safe. But `stop` resets them, so after a stop the modal must re-send them.
- A non-numeric `{seconds}` silently defaults to **60 seconds**.
- `command.ts:28-30,63-65` currently has only `timerStart/timerPause/timerStop`. **`timerShutdown` and `timerKeepWarm` do not exist and must be added.**

**Modal** (`templates/_macro_timer.html:31-60`): hours slider 0–23, minutes slider 0–59, two checkboxes — "Shutdown Grill", "Start Keep Warm".

**Alerts:** `components/dashboard/Banners.tsx` already exists and is correct (errors / warnings / criticalError). It is imported **only** by `Dashboard.tsx:8,85`. This is a *move*, not new work.

---

## File Structure

- Create `web-react/src/components/shell/AppShell.tsx` — layout route: navbar + timer bar + banners + `<Outlet>`, owns `useLiveState()`.
- Create `web-react/src/components/shell/NavBar.tsx` — brand, 6 items, active state, mobile collapse.
- Create `web-react/src/components/shell/TimerBar.tsx` — status/controls strip.
- Create `web-react/src/components/shell/TimerModal.tsx` — set-duration modal.
- Create `web-react/src/helpers/timer/timerState.ts` — **pure**: derive state + remaining seconds + formatting.
- Create `web-react/src/components/shell/shell.css`.
- Move `components/dashboard/Banners.tsx` → `components/shell/Banners.tsx`.
- Modify `web-react/src/components/App.tsx` — layout route wrapping `/`, `/history`, `/settings`; `/wizard` stays outside.
- Modify `web-react/src/components/dashboard/Dashboard.tsx` — consume context instead of calling `useLiveState()`.
- Modify `web-react/src/helpers/command.ts` — add `timerShutdown`, `timerKeepWarm`.

---

### Task 0: Rename the live-state hook

**Files:** Rename `web-react/src/helpers/useDashData.ts` → `useLiveState.ts` (and its `.test.tsx`); update every consumer.

**Interfaces:** Produces `useLiveState(): { live: LiveState; phase: ConnectionPhase; controlAlive: boolean; targetUrl: string; command: CommandClient }` and the exported type `LiveState`.

Pure rename, no behaviour change. 57 references across 18 files (counted, not estimated).

- [ ] **Step 1: Find every reference with the LSP tool** — `ToolSearch("select:LSP")`, then `findReferences` on the `useDashData` definition and on `DashData`. Do NOT grep; this repo has already produced grep-vs-LSP misses today (16 found vs 41 real).
- [ ] **Step 2: Rename** the file, the hook, the type, and the returned `dash` field to `live`. Leave the `socket_dash_data` string literal alone — it is the backend event name.
- [ ] **Step 3: Run the full gate.** Every failure is a missed reference, not a behaviour change. `bun run typecheck` will catch most of them; do not stop at the first green.
- [ ] **Step 4: Commit.** This must be its own commit — a rename mixed into a feature commit is unreviewable.

### Task 1: Timer command client

**Files:** Modify `web-react/src/helpers/command.ts`; Test `web-react/src/helpers/command.test.ts`

**Interfaces:** Produces `timerShutdown(on: boolean)`, `timerKeepWarm(on: boolean)` on `CommandClient`.

- [ ] **Step 1: Write failing tests** asserting the exact URLs — `set/timer/shutdown/true`, `set/timer/keep_warm/false` — following the existing timer-command tests in that file verbatim in style.
- [ ] **Step 2: Run, confirm they fail** (`bun run test src/helpers/command.test.ts`).
- [ ] **Step 3: Add both methods** to the interface and the factory, mirroring `timerStart`'s shape (`command.ts:63-65`). Booleans serialize as the literal strings `true`/`false` — the backend compares `arglist[2] == "true"`.
- [ ] **Step 4: Run, confirm pass. Commit.**

### Task 2: Pure timer state

**Files:** Create `web-react/src/helpers/timer/timerState.ts` + `.test.ts`

**Interfaces:** Produces `deriveTimer(timer, nowSeconds) → { state: "stopped"|"running"|"paused"; remaining: number }` and `formatRemaining(seconds) → "HH:MM:SS"`.

- [ ] **Step 1: Write failing tests** covering all three states, remaining while paused (`end - paused`, frozen), an expired timer (`end < now` → clamp to 0, do not go negative), and `formatRemaining` at 0, 59s, 1h, 23h59m.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.** Pure functions only — no clock reads inside; `nowSeconds` is a parameter so tests are deterministic.
- [ ] **Step 4: Run, confirm pass. Commit.**

### Task 3: NavBar

**Files:** Create `web-react/src/components/shell/NavBar.tsx` + `.test.tsx`, `shell.css`

- [ ] **Step 1: Write failing tests** — all 6 labels render; Dashboard/History/Settings are links to `/`, `/history`, `/settings`; Recipes/Events/Admin are **not** links and are marked disabled (`aria-disabled`); the current route's item is marked active; the grill name renders when provided and is absent when empty.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.** `NavLink` with `isActive` for the three live items (see `SettingsShell.tsx:33` for the established pattern). Disabled items are `<span aria-disabled="true">`, never `<a>`. Brand links `/`. Mobile collapse via a toggle button.
- [ ] **Step 4: Run, confirm pass. Commit.**

### Task 4: TimerBar + TimerModal

**Files:** Create `TimerBar.tsx` + `.test.tsx`, `TimerModal.tsx` + `.test.tsx`

**Interfaces:** Consumes `deriveTimer`/`formatRemaining` (Task 2) and the command client (Task 1).

- [ ] **Step 1: Write failing tests.** TimerBar: stopped → only a start affordance; running → pause + stop, remaining ticks down; paused → resume + stop, remaining frozen. TimerModal: sliders set hours/minutes; submitting calls `timerShutdown`/`timerKeepWarm` **then** `timerStart` with `hours*3600 + minutes*60`; a 0h0m submission is rejected rather than sent (the backend would silently substitute 60s).
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.** Resume calls `timerStart` again (the backend unpauses; the seconds argument is ignored — comment this, it looks wrong otherwise). Live tick via an interval that is cleared on unmount; the *displayed* remaining is derived at render from `dash.timer` + a ticking `now`, never mirrored into state.
- [ ] **Step 4: Run, confirm pass. Commit.**

### Task 4c: FIX — the timer modal silently drops shutdown/keep-warm

**This plan's Task 4 specified a sequence that does not work.** It said to call
`timerShutdown`/`timerKeepWarm` **then** `timerStart`. That loses both flags.

Verified mechanism:
- `common/datastore_accessors.py:55-61` — `read_control()` reads only the
  persisted blob; it **never sees the pending write queue**.
- The queue drains only in the **control loop**
  (`controller/runtime/controller.py:282`, `modes/base.py:637`,
  `transitions.py:128`) — never in the web process.
- `execute_control_writes` applies each queued partial with
  `json_patch(value, ?)` (`datastore_accessors.py:120-122`). SQLite's
  `json_patch` is RFC 7396, which **replaces arrays wholesale**.
- `write_control(..., WriteKind.MERGE)` queues the **entire control dict**.
- The timer's `shutdown`/`keep_warm` live in `control["notify_data"]` — an
  ARRAY — at `common/api_commands.py:588-599`, while `start` writes
  `control["timer"]`.

So three sequential calls inside one ~50 ms control cycle each queue a stale
full snapshot, and the last one's `notify_data` replaces the earlier ones'.
The checkboxes are dropped every time.

**Fix:** one write, not three — `POST /api/control` carrying the whole
`notify_data` array, exactly as the Flask dashboard does
(`static/js/dash_default.js:784-799`).

**Trap:** `POST /api/control` answers `{"result": "success"}` — lowercase — not
the `"OK"` that `command.ts:49` tests for. Reusing the existing `post()` helper
unchanged would report every successful write as a failure.

Pin it with a test that fails against the sequential-call version.

### Task 5: AppShell + route restructure + Banners move

**Files:** Create `AppShell.tsx` + `.test.tsx`; move `Banners.tsx`; modify `App.tsx`, `Dashboard.tsx`

**This is the risky task — the socket constraint above lives here.**

- [ ] **Step 1: Write failing tests** — shell renders nav, banners and `<Outlet>`; `/wizard` renders WITHOUT the shell; a child route receives dash data via context.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.** `AppShell` calls `useLiveState()` **once** and provides it via `<Outlet context={…}>`. Restructure `App.tsx` so `/`, `/history`, `/settings` are children of the shell route and `/wizard` is a sibling outside it. Move `Banners.tsx` into `shell/` and render it in the shell.
- [ ] **Step 4: Update `DashboardRoute.tsx`** (NOT `Dashboard.tsx` — see the correction above) to take live state from `useOutletContext()` and delete its own `useLiveState()` call. **Keep its `first_time_setup` → `/wizard` redirect effect working.** Separately, remove the `<Banners>` usage from `Dashboard.tsx:85` since the shell now renders it. **Verify by reading that exactly one `useLiveState()` call site remains in the tree.**
- [ ] **Step 5: Run the full suite.** Dashboard's existing tests mock or drive the hook; they will need their harness updated to provide context instead. Updating the harness is fine; weakening an assertion is not.
- [ ] **Step 6: Commit.**

### Task 6: e2e + gate

**Files:** Modify `web-react/tests/e2e/`

- [ ] **Step 1: Add a nav spec** — from `/`, click History → lands on `/history`; click Settings → `/settings`; the active item tracks the route; the three unported items are present but not clickable; `/wizard` shows no nav.
- [ ] **Step 2: Add a timer spec** — set 0h1m, start, assert the bar shows a running timer and `GET /api/get/timer` reflects it; stop; assert cleared. **Restore state**: the timer is real control state, so stop it in cleanup even on failure.
- [ ] **Step 3: Full gate**, both suites, plus the repo-root artifact check (`os_info.json`/`settings.json`/`pelletdb.json` absent).
- [ ] **Step 4: Commit.**

## Parallelization

- **Wave 0:** Task 0 alone (touches 18 files; nothing may run alongside it).
- **Wave 1:** Task 1 ∥ Task 2 ∥ Task 3 — disjoint files, no shared edits. Isolated jj workspaces.
- **Wave 2:** Task 4 (needs 1 + 2).
- **Wave 3:** Task 5 (needs 3 + 4) — must be alone; it restructures routing and edits Dashboard.
- **Wave 4:** Task 6.

## Self-Review

**Spec coverage:** navbar → T3; timer bar + modal → T4; global alerts → T5 (move); reachability → T5/T6. **Placeholder scan:** none — every behavioural requirement names the file and the verified backend contract. **Type consistency:** `deriveTimer`/`formatRemaining` defined in T2 and consumed in T4; `timerShutdown`/`timerKeepWarm` defined in T1 and consumed in T4; the `DashData` context shape is the existing `useLiveState` return type, unchanged.
