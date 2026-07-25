# React Per-Probe Notifications — Slice 1: Target Temperature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the Flask dashboard's per-probe **Target Temperature Notification** — set a target on any probe, request a notification, and choose what happens when it is reached (Shutdown PiFire / Start Keep Warm) — into the React dashboard, plus the ETA readout that accompanies it.

**Architecture:** A per-probe modal opened from the probe card (and from the primary-probe gauge column), seeded from the live socket payload, saved as **one** `POST /api/control` carrying only the `notify_data` key. All mutation logic is pure and lives in `helpers/notify/`.

**Tech Stack:** React 19 + react-router, TS7, rsbuild, Biome, @rstest/core, Playwright, bun.

## Why this exists

Per-probe notifications are **write-absent** from the React UI. Precisely:

- `web-react/src/helpers/command.ts` (74 lines, read in full) has **zero** notify/target/limit methods. There is no write path of any kind.
- The read side is *partially* present, and the audit that triggered this plan slightly overstated the gap: `helpers/dashboard/deriveView.ts:96-104` **does** consume `target` and `targetReq` to render `→ 225°` / `AMBIENT` and the progress bar on food-probe cards. What is missing is (a) any way to *set* it, (b) the primary probe entirely (the gauge column shows no target/notify affordance at all), (c) `targetShutdown` / `targetKeepWarm` / `eta`, which have no consumer anywhere, and (d) the whole high/low limit feature.
- `SafetyTab.tsx` is unrelated — it is the `settings.safety.maxstartuptemp` family, a *settings* concern, not `control["notify_data"]`.

This is safety-adjacent: on the Flask UI a user can say "shut the grill down when the brisket hits 203". In React they cannot, and nothing in the UI indicates the feature exists.

## Scope — this is genuinely two slices, and this plan is Slice 1 only

The Flask notify modal (`blueprints/dash/templates/default/_macro_dash_default.html:140-322`) is a three-card accordion, and the backend backs each card with a **separate `notify_data` entry** (`common/defaults.py:512-538` appends three objects per probe: `type: "probe"`, `"probe_limit_high"`, `"probe_limit_low"`):

| Accordion card | notify_data type | This plan |
|---|---|---|
| Target Temperature Notification | `probe` | **Slice 1 — planned here** |
| High Limit Temperature Alert | `probe_limit_high` | Slice 2 |
| Low Limit Temperature Alert | `probe_limit_low` | Slice 2 |

They are separable because `applyTargetEdit` (Task 2) touches only `type === "probe"` entries and leaves the limit entries byte-identical, so Slice 2 lands as a second pure reducer + a second accordion card with no rework of Slice 1.

They are **not** one slice, because the limits carry machinery the target does not: a `triggered` latch that the client must pre-arm to avoid an instant false alarm, a `reignite` action, per-type asymmetric controls, and at least one suspected backend bug (see "Slice 2 groundwork" at the bottom). Bundling them would produce a shallow plan for both.

## Global Constraints

- Test runner is **@rstest/core** (`rs.fn`, `rs.mock`, `rs.stubGlobal`) — `vi` does NOT exist. `.test.tsx` → jsdom, `.test.ts` → node.
- **bun**, never npm.
- **No suppressions**: no `biome-ignore`, no `@ts-expect-error`, no `eslint-disable`.
- **No `setState` in `useEffect` for derived state** (React Compiler). Render-phase adjustment — copy the pattern in `dashboard/SetpointEntry.tsx:13-23` and `settings/tabs/SafetyTab.tsx`.
- `react-refresh/only-export-components`: non-components go in their own module. All pure logic here goes in `helpers/notify/`, never in a `.tsx`.
- Reuse the existing `pf-*` class vocabulary (`pf-modal-scrim`, `pf-modal`, `pf-modal-title`, `pf-modal-actions`, `pf-modal-btn`, `pf-modal-btn.accent`, `pf-step`, `pf-setpoint-val`, `pf-setpoint-slider` — all defined in `components/dashboard/dashboard.css:165-240`). Do not introduce a second visual language.
- Gate: `bun run typecheck && bun run lint && bun run test && bun run build && bun run gen:types:check`.

---

## THE CRITICAL CONSTRAINT — you get exactly ONE write, and it must be `POST /api/control`

There are **three** backend write paths for notify data. Two of them are traps.

### Why `/api/set/notify/{label}/{field}/{value}` cannot be used

The REST grammar exists and looks perfect (`common/api_commands.py:420-476`, dispatched at `:720-722`). It is nonetheless unusable for this feature, for two independent reasons.

**Reason 1 — the merge queue clobbers sequential writes.** Trace it:

1. `blueprints/api/routes.py:295-305` → `process_command()` → `common/api_commands.py:751` `control = read_control()`.
2. `common/datastore_accessors.py:55-61` — `read_control()` reads **only** the `control:general` blob. It does not see anything still sitting in the write queue.
3. `_cmd_set_notify` mutates one field and calls `write_control(control, WriteKind.MERGE, ...)` (`api_commands.py:473`) — note it **hard-codes MERGE**, ignoring the caller's `kind`.
4. `datastore_accessors.py:75-77` — MERGE pushes the **entire control dict** onto `queue_control_write`.
5. `datastore_accessors.py:120-122` — the drain applies each queued dict with SQLite `json_patch()`, i.e. RFC 7396 merge-patch. **RFC 7396 replaces arrays wholesale.** `notify_data` is an array.

So four sequential calls (`target`, `req`, `shutdown`, `keep_warm`) each queue a full snapshot of `notify_data` taken from the same stale base blob, and the **last one wins outright** — three of the four edits are silently discarded. The drain happens at `controller/runtime/modes/base.py:637` (every loop iteration, `ctx.clock.sleep(0.05)` at `:744`) and `controller/runtime/controller.py:282`; a ~50 ms window is comfortably wider than four awaited localhost round-trips. This is intermittent, silent, and would present as "the shutdown checkbox doesn't stick sometimes".

**Reason 2 — a full-control MERGE patch can revert the controller.** Because the queued patch is the *whole* control dict read at request time, it also carries stale `mode`, `primary_setpoint`, `s_plus`, etc. If the controller `OVERWRITE`s control in between (e.g. `notify/notifications.py:169`, `controller.py:342`), the next drain patches the stale values back over it. `POST /api/control` avoids this entirely by sending a patch containing **only** `notify_data`.

### Why the socket `notify_action` path is not used either

`blueprints/mobile/socket_io.py:735-743` → `_update_notify_data` at `:958-1002` does update all three entries atomically and is a legitimate option. It is rejected here because: the React socket is currently read-only (`helpers/useDashData.ts:41-50` only listens), adding an emit path is new plumbing for one feature, and the DTO subscripts `notify_dto["target_shutdown"]` / `["target_keep_warm"]` / `["target_req"]` **unconditionally** whenever `target_temp` is present (`:964-969`) — a partial DTO is a `KeyError`, not an error envelope.

### The path this plan uses

Exactly what the Flask dashboard does (`blueprints/dash/static/default/js/dash_default.js:784-799` and `:816-829`):

```
GET  /api/get/notify          -> { result: "OK", data: NotifyEntry[] }
POST /api/control  body: { "notify_data": [ ...the whole array, one entry edited... ] }
```

One request, minimal patch, array replaced atomically. `_api_post_control` (`blueprints/api/routes.py:204-213`) just does `write_control(request_json, WriteKind.MERGE, origin="app")`.

**Landmine inside the landmine:** that endpoint answers `{"control": "success", "result": "success", ...}` with HTTP **201** — `result` is lowercase `"success"`, **not** the `"OK"` that `common/app.py`'s `api_response` envelope uses everywhere else. `command.ts:49` tests `body.result === "OK"`. **Do not reuse `command.ts`'s `post()` helper for this write** — it would report failure on every successful save.

---

## Verified facts (checked against live code — do not re-derive, do not guess)

### The Flask UI being ported

- The notify modal is rendered by `render_probe_card` (`_macro_dash_default.html:1`), which `dash_default.html:36` and `:53` invoke for **every** probe in `probe_status['P']` *and* `probe_status['F']` — so the **primary probe gets the modal too**, not just food probes.
- Probe card affordance (`_macro_dash_default.html:108-121`): a single bell button. Filled `btn-primary` showing `{{ target }}°F` when `notify_info['req']`; outline `btn-outline-primary` with `fa-bell-slash` when not. Live-updated by `dash_default.js:614-661` (`updateNotificationCard`).
- ETA button (`_macro_dash_default.html:123-134`): shown **only** while the probe notification is requested; hidden otherwise. Content set at `dash_default.js:632-636` — `formatDuration(eta)` when `eta != null`, a spinner when it is null.
- Target card (`:151-201`): a master switch `{label}_notify_temp` (**rendered `checked` by default in the raw HTML**, then corrected from live data by `initTarget`, `dash_default.js:565-597`), a numeric text input + a range slider bound to each other, and — **only when `probe_data['type'] != 'Primary'`** (`:188-198`) — two checkboxes, "Shutdown PiFire" (`{label}_shutdown`) and "Start Keep Warm" (`{label}_keepWarm`).
- Slider ranges are hard-coded in the template (`:174-186`), and are **not** the dash payload's `maxTemp`:

  | | F | C |
  |---|---|---|
  | Primary | 0–600 | 0–300 |
  | Food | 0–300 | 0–225 |

- Footer (`:317-319`): "Cancel" (`btn-warning`) → `cancelNotify(label)`, "Set" → `setNotify(label)`. **"Cancel" is not "close without saving"** — `dash_default.js:803-831` posts a write that clears `req`, `shutdown`, `keep_warm` and `target` for **every** entry matching the label, i.e. it wipes the limits too. It is a *Disable All* button wearing a Cancel label.
- `setNotify` (`dash_default.js:664-702` for the target card): when the master switch is on → `target = parseInt(sliderValue)`, `shutdown`, `keep_warm`, `req = true`. When off → `req = false` only (target/shutdown/keep_warm left as they were).

### Backend behaviour once the flags are set

`notify/notifications.py:77-171`, per control loop pass, for each entry with `req == true`:

- ETA is recomputed for `type == "probe"` entries (`:81-99`) from 20 minutes of history and written back to `notify_data[i]["eta"]` — seconds, or `None`.
- `_check_condition(item["condition"], current, target)` (`:101`, defined `:724-743`). Default `condition` for a probe entry is `"equal_above"` (`common/defaults.py:524`) → fires at `current >= target`.
- On fire (`:109-111`): **the backend clears the request itself** — `req = False`, `target = 0`, `eta = None`. A target notification is **one-shot and self-clearing**; the UI must not treat a vanished target as an error.
- Shutdown / keep-warm (`:141-159`) run in the *same* pass, gated on `not control["notify_data"][index]["req"]` — i.e. only once `req` has just been cleared. `shutdown` wins over `keep_warm` (`if` / `elif`), and each flag is reset to `False` after it fires. So **checking both boxes means shutdown**; the Flask UI lets you check both and does not say so.

### Units

`_cmd_set_notify` rounds by unit — `int(float(x))` for F, `float(x)` for C (`api_commands.py:463-466`). The `POST /api/control` path does no rounding at all, and `_update_notify_data` uses `int()` unconditionally (`socket_io.py:966`). **Nothing converts an existing notify target when the user changes units** (`_cmd_set_units`, `api_commands.py:298-314`, converts *settings* and sets `units_change`; `controller.py:346-354` handles that flag by stopping the grill and flushing history, and never touches `notify_data`). A 203 °F target stays the number 203 after switching to Celsius. Pre-existing; out of scope; do not "fix" it here, but do not write a step that assumes conversion happens.

### Data already flowing — do NOT re-plumb it

`blueprints/mobile/socket_io.py:823-848` flattens the `type == "probe"` notify entry onto every probe in the dash payload, and `web-react/src/helpers/types.ts:14-38` already models all of it:

| Wire field | socket_io.py | types.ts | Needed by Slice 1 |
|---|---|---|---|
| `target` | `:828` | `:21` | yes |
| `targetReq` | `:831` | `:24` | yes |
| `targetShutdown` | `:829` | `:33` | yes |
| `targetKeepWarm` | `:830` | `:34` | yes |
| `eta` | `:827` | `:17` | yes |
| `hasNotifications` | `:832-848` | `:25` | no (true if *any* of the three entries is req'd) |
| `label` | `:817` | `:16` | yes — the identity key for the write |
| `highLimit*` / `lowLimit*` | `:834-847` | `:22-32` | Slice 2 |

**Nothing new needs to arrive over the socket.** The modal seeds from these fields; the only REST read is the save-time `GET /api/get/notify` needed to build the full array.

`_get_probe_structure` (`socket_io.py:866-889`) guarantees every field is present with a falsy default even when no notify entry matches, so no optionality handling is required.

### Where it belongs in the React UI

**On the dashboard, in a per-probe modal.** Justification, not preference:

- It is *control* state (`control["notify_data"]`), not settings. The React Notifications settings tab (`settings/tabs/NotificationsTab.tsx`) edits `settings.notify_services` — which service delivers a notification (IFTTT/Pushover/Apprise/WLED/InfluxDB). It has no concept of a probe and no business owning a per-cook target.
- The Flask layout puts it exactly there: a bell on the probe card opening a modal (`_macro_dash_default.html:108-140`).
- The React dashboard already owns this shape: `ControlButtons.tsx:22,85` opens `SetpointEntry` — a `pf-modal-scrim` modal driven from dashboard state with a command client in scope. `ProbeNotifyModal` is the same pattern with a different body.
- The app shell landing right now (`docs/superpowers/plans/2026-07-24-react-app-shell.md`) is **not a dependency**: it moves `Banners` and adds nav/timer chrome. `src/components/shell/` does not exist yet (verified). This plan touches `Dashboard.tsx`'s probe column and centre column only, so it composes either way — but see Parallelization, the two must not run concurrently against `Dashboard.tsx`.

---

## Landmine table — copy this into the implementation, do not rediscover it

| # | Landmine | Where | Consequence |
|---|---|---|---|
| 1 | MERGE writes queue the **whole control dict**; the drain uses `json_patch` (RFC 7396) which **replaces arrays**; `read_control()` never sees the queue | `datastore_accessors.py:55-61,75-77,120-122` | Two `/api/set/notify/...` calls inside one ~50 ms control cycle silently discard each other. **Use one `POST /api/control`.** |
| 2 | `POST /api/control` answers `result: "success"`, not `"OK"` | `blueprints/api/routes.py:211` | `command.ts:49`'s `post()` reports every successful save as a failure. Needs its own helper. |
| 3 | Notify objects are addressed by **`label`**, and up to **three** entries share one label | `api_commands.py:441-449`, `defaults.py:512-538` | Any "find the entry for this probe" must filter on `type` too, or it edits the limits by accident. |
| 4 | The route is `/<action>/<arg0>/<arg1>/<arg2>/<arg3>` with Flask's default string converter | `blueprints/api/routes.py:289-294` | A probe label containing `/` cannot be addressed by the REST grammar at all. `buildCommandUrl` (`command.ts:37-39`) does **not** percent-encode. Another reason Slice 1 uses a JSON body. |
| 5 | Backend clears `req`/`target`/`eta` itself when the target is reached | `notifications.py:109-111` | A target that "disappears" is success, not a lost write. Do not re-assert it. |
| 6 | `shutdown` and `keep_warm` are `if`/`elif` — shutdown wins; both are reset to `False` after firing | `notifications.py:142-159` | Present them as mutually exclusive in React. Do not offer "both". |
| 7 | Flask's modal "Cancel" button *writes*, clearing target **and both limits** for that label | `dash_default.js:803-831` | Do **not** port this. React's Cancel must close without writing; disabling is the master switch. |
| 8 | Boolean args on the REST grammar compare against the literal string `"true"`; anything else is `False` | `api_commands.py:457-460` | Only relevant if a later slice adds REST calls. |
| 9 | Nothing converts notify targets on a units change | `controller.py:346-354` | Render targets in the current `tempUnits` and do not imply conversion. |
| 10 | `_cmd_set_notify` calls `write_control` even on its error branches | `api_commands.py:470-473` | A rejected target still queues a full-control MERGE. Avoided entirely by not using this endpoint. |

**Landmine 1 also invalidates a step in a sibling plan.** `2026-07-24-react-app-shell.md` Task 4 Step 1 specifies "submitting calls `timerShutdown`/`timerKeepWarm` **then** `timerStart`". `/api/set/timer/shutdown/*` writes `control["notify_data"][timer_index]["shutdown"]` (`api_commands.py:590-598`) while `/api/set/timer/start/*` writes `control["timer"]` — but *both* queue the whole control dict, so the later `start` patch replaces `notify_data` with its own stale snapshot and drops the flags. Whoever implements or reviews the timer modal needs to know; it is not this plan's job to fix, but it must be reported.

## Things I could not verify — the implementer must check these first

1. **Live round-trip latency of the `GET` → `POST` → socket-echo loop.** I read the code; I did not run a PiFire. The e2e task must poll rather than assume the dash payload reflects a write within one animation frame — the write lands in a queue drained by the control loop, and in **Stop** mode the drain lives in `controller.py:282`, whose cadence I did not measure (I only measured the active work cycle, `modes/base.py:744`, at 50 ms). **Budget a generous timeout in Task 6 and verify the real cadence there.**
2. **Whether `POST /api/control` with only `notify_data` is rejected by any validation I did not find.** `_api_post_control` has no validation and the Flask dashboard posts exactly this shape, so it should be fine — but the Flask dashboard posts a *complete* `notify_data` array read from `/api/current`, and I did not confirm that `GET /api/get/notify` and the array embedded in `/api/current` are the same object. `_cmd_get_notify` returns `control["notify_data"]` verbatim (`api_commands.py:159`), so they should be identical. **Task 1 Step 5 confirms this against a live instance before anything is built on it.**
3. **Whether the null-stripping diagnostic fires on this write.** `execute_control_writes` strips null members from MERGE partials and logs at ERROR when it does (`datastore_accessors.py:106-119`), naming `/api/control` as a suspected source. A `probe` notify entry carries `"eta": null` when idle (`defaults.py:520`), and round-tripping it through `GET` → `POST` will re-send that null. **Check `logs/control.log` after the first write in Task 6; if it logs, replace `eta: null` with the fetched value only when non-null, and say so in the commit.** Do not paper over it.
4. **Exact visual placement of the primary-probe bell.** Task 5 proposes the Cook Time row. That is a judgement call about a fixed 1280×720 stage I cannot see rendered. The *behaviour* is specified and tested by accessible name; the implementer may move the button, but must not drop it.

---

## File Structure

- Create `web-react/src/helpers/notify/notifyApi.ts` — `NotifyEntry`, `getNotifyData`, `postNotifyData`. Network only.
- Create `web-react/src/helpers/notify/notifyApi.test.ts`
- Create `web-react/src/helpers/notify/notifyState.ts` — **pure**: `TargetEdit`, `readTargetEdit`, `applyTargetEdit`, `targetRange`, `saveTargetEdit`.
- Create `web-react/src/helpers/notify/notifyState.test.ts`
- Create `web-react/src/components/dashboard/ProbeNotifyModal.tsx` + `.test.tsx`
- Modify `web-react/src/helpers/dashboard/deriveView.ts` — add `label`, `notifyOn`, `etaStr` to `ProbeCardView`.
- Modify `web-react/src/components/dashboard/ProbeCard.tsx` — bell button + ETA.
- Modify `web-react/src/components/dashboard/Dashboard.tsx` — modal state, food-probe wiring, primary-probe bell.
- Modify `web-react/src/components/dashboard/dashboard.css` — `pf-notify-*` additions.
- Create `web-react/tests/e2e/notify.spec.ts`

---

### Task 1: Notify REST client

**Files:** Create `web-react/src/helpers/notify/notifyApi.ts` + `notifyApi.test.ts`

**Interfaces:** Produces `NotifyEntry`, `getNotifyData(baseUrl): Promise<NotifyEntry[]>`, `postNotifyData(baseUrl, entries): Promise<void>`.

- [ ] **Step 1: Write failing tests.** Follow `helpers/command.test.ts:14-25` verbatim in style (`rs.fn` + `rs.stubGlobal("fetch", …)` in `beforeEach`, `rs.unstubAllGlobals()` in `afterEach`). Assert:
  - `getNotifyData("")` fetches `/api/get/notify` and returns `body.data`.
  - `getNotifyData` **throws** on `{ result: "ERROR" }`, on a non-array `data`, and on `res.ok === false`. It must never return `[]` on failure — an empty array here would be posted back as a wipe of every notification on the grill.
  - `postNotifyData("", entries)` POSTs `/api/control` with method `POST`, `Content-Type: application/json`, and body exactly `JSON.stringify({ notify_data: entries })` — assert the parsed body has **only** the `notify_data` key (`Object.keys(...)` has length 1). That single-key shape is what keeps landmine 2 from reverting the controller.
  - `postNotifyData` **resolves** on `{ result: "success" }` and **rejects** on `{ result: "OK" }`… no: rejects on `{ result: "error", message: "Settings update failed." }`, and resolves on `{ result: "success" }`. Pin the lowercase spelling explicitly — this is the whole point of the test.
- [ ] **Step 2: Run, confirm they fail.** `bun run test src/helpers/notify/notifyApi.test.ts`
- [ ] **Step 3: Implement.**

```ts
// Per-probe notification state lives in control["notify_data"] -- runtime
// control state, NOT settings. Read with GET /api/get/notify, written with a
// SINGLE POST /api/control carrying only the notify_data key.
//
// Why not /api/set/notify/{label}/{field}/{value}? Because every MERGE write
// queues the WHOLE control dict (common/datastore_accessors.py:75-77) and the
// drain applies it with SQLite json_patch (RFC 7396), which REPLACES arrays
// (:120-122). read_control() never sees the queue (:55-61). So two granular
// calls inside one control cycle silently discard each other's edit.

export interface NotifyEntry {
  label: string;
  type: string; // "probe" | "probe_limit_high" | "probe_limit_low" | "timer" | "hopper" | "test"
  req: boolean;
  shutdown: boolean;
  keep_warm?: boolean;
  reignite?: boolean;
  target?: number;
  eta?: number | null;
  condition?: string;
  triggered?: boolean;
  // Index signature: entries carry per-type extras (name, last_check, ...) that
  // MUST survive the round trip untouched -- we post the whole array back.
  [k: string]: unknown;
}

export async function getNotifyData(baseUrl: string): Promise<NotifyEntry[]> {
  const res = await fetch(`${baseUrl}/api/get/notify`);
  if (!res.ok) throw new Error(`GET /api/get/notify failed: HTTP ${res.status}`);
  const body = (await res.json()) as { result?: string; data?: unknown };
  if (body.result !== "OK" || !Array.isArray(body.data)) {
    // Deliberately throws rather than returning []: the caller posts this array
    // straight back, so an empty fallback would wipe every notification set.
    throw new Error("GET /api/get/notify returned no notify_data");
  }
  return body.data as NotifyEntry[];
}

export async function postNotifyData(baseUrl: string, entries: NotifyEntry[]): Promise<void> {
  const res = await fetch(`${baseUrl}/api/control`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // ONLY notify_data. A fuller body would queue a stale patch for keys the
    // controller owns (mode, primary_setpoint, ...) and could revert them.
    body: JSON.stringify({ notify_data: entries }),
  });
  if (!res.ok) throw new Error(`POST /api/control failed: HTTP ${res.status}`);
  // This endpoint answers { result: "success" } -- lowercase, NOT the "OK" that
  // common/app.py's api_response envelope uses. blueprints/api/routes.py:211.
  // Do NOT route this through command.ts's post(); it tests result === "OK".
  const body = (await res.json()) as { result?: string; message?: string };
  if (body.result !== "success") throw new Error(body.message ?? "control write rejected");
}
```

- [ ] **Step 4: Run, confirm pass.**
- [ ] **Step 5: Confirm the contract against a live instance** (this is uncertainty 2 above, and it gates every later task). With `uv run python control.py` and `uv run python app.py` running:
  - `curl -s localhost:5000/api/get/notify | head -c 400` — confirm `result: "OK"` and that `data` is an array whose probe entries carry `label`, `type`, `req`, `target`, `shutdown`, `keep_warm`, `eta`, `condition`.
  - Confirm the same array appears under `/api/current`'s `notify_data` (that is what the Flask dashboard round-trips).
  - **If either differs from the shapes above, stop and update this plan before continuing.**
- [ ] **Step 6: Commit.**

### Task 2: Pure target-edit reducer

**Files:** Create `web-react/src/helpers/notify/notifyState.ts` + `notifyState.test.ts`

**Interfaces:** Produces `TargetEdit`, `readTargetEdit(probe, units)`, `applyTargetEdit(entries, label, edit)`, `targetRange(isPrimary, units)`, `saveTargetEdit(baseUrl, label, edit)`.

This task holds the entire correctness surface of the feature. It is pure (except `saveTargetEdit`, which is two awaits over Task 1) and must be tested exhaustively.

- [ ] **Step 1: Write failing tests.**
  - `targetRange` returns `{min:0,max:600}` / `{min:0,max:300}` for primary F/C and `{min:0,max:300}` / `{min:0,max:225}` for food F/C — the four hard-coded template ranges (`_macro_dash_default.html:174-186`). Assert with a comment that these are **not** `probe.maxTemp` from the payload.
  - `applyTargetEdit` with `enabled: true` sets `target`, `shutdown`, `keep_warm`, `req: true` on the `type: "probe"` entry for that label.
  - **`applyTargetEdit` leaves the `probe_limit_high` and `probe_limit_low` entries for the same label deep-equal to their inputs.** This is the test that makes Slice 2 additive; write it first.
  - It leaves entries for *other* labels, and the `timer` / `hopper` / `test` entries, deep-equal.
  - It does not mutate the input array (assert the original is deep-equal to a pre-call clone) and returns a new array.
  - Unknown keys on the edited entry survive (`condition`, `name`, and any future field) — pass an entry with `foo: 1` and assert `foo === 1` on the way out.
  - `enabled: false` clears the target entry to `req: false, target: 0, shutdown: false, keep_warm: false` **and still leaves the limit entries untouched** (this deliberately diverges from Flask's `cancelNotify`, which wipes the limits too — `dash_default.js:807-813`).
  - `shutdown: true, keepWarm: true` is not representable: `TargetEdit` carries a single `action` field. Assert `action: "shutdown"` → `shutdown: true, keep_warm: false`; `"keepWarm"` → the inverse; `"none"` → both false.
  - `applyTargetEdit` rounds a fractional target to an integer.
  - `applyTargetEdit` with a label that has no `type: "probe"` entry returns the array unchanged (does not throw, does not append).
  - `readTargetEdit` maps a `ProbeData` to a `TargetEdit`: `targetReq` → `enabled`, `target` → `target`, and `targetShutdown` → `action: "shutdown"` taking precedence over `targetKeepWarm` (mirroring the backend's `if`/`elif` at `notifications.py:142-159`).
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.**

```ts
import type { ProbeData } from "../types";
import { getNotifyData, type NotifyEntry, postNotifyData } from "./notifyApi";

// The backend runs `if shutdown: ... elif keep_warm: ...`
// (notify/notifications.py:142-159), so checking both boxes means shutdown and
// silently ignores keep-warm. Model it as one choice instead of two booleans so
// the UI cannot express a state the backend will not honour.
export type TargetAction = "none" | "shutdown" | "keepWarm";

export interface TargetEdit {
  enabled: boolean;
  target: number;
  action: TargetAction;
}

// Hard-coded in blueprints/dash/templates/default/_macro_dash_default.html:174-186.
// NOT probe.maxTemp from the dash payload -- that is the gauge ceiling from
// settings.dashboard.dashboards.Default.config, a different number.
export function targetRange(isPrimary: boolean, units: "F" | "C"): { min: number; max: number } {
  if (isPrimary) return { min: 0, max: units === "F" ? 600 : 300 };
  return { min: 0, max: units === "F" ? 300 : 225 };
}

export function readTargetEdit(probe: ProbeData): TargetEdit {
  return {
    enabled: probe.targetReq,
    target: Math.round(probe.target),
    action: probe.targetShutdown ? "shutdown" : probe.targetKeepWarm ? "keepWarm" : "none",
  };
}

// Edits ONLY the `type === "probe"` entry for `label`. Up to three entries share
// a label (common/defaults.py:512-538) -- probe, probe_limit_high,
// probe_limit_low -- and the limit pair belongs to Slice 2. Everything else in
// the array is returned untouched because the caller posts the WHOLE array back.
export function applyTargetEdit(
  entries: NotifyEntry[],
  label: string,
  edit: TargetEdit,
): NotifyEntry[] {
  return entries.map((e) => {
    if (e.type !== "probe" || e.label !== label) return e;
    if (!edit.enabled) {
      return { ...e, req: false, target: 0, shutdown: false, keep_warm: false };
    }
    return {
      ...e,
      req: true,
      target: Math.round(edit.target),
      shutdown: edit.action === "shutdown",
      keep_warm: edit.action === "keepWarm",
    };
  });
}

// Read-modify-write, exactly as the Flask dashboard does
// (dash_default.js:784-799). One POST, so the array is replaced atomically.
export async function saveTargetEdit(
  baseUrl: string,
  label: string,
  edit: TargetEdit,
): Promise<void> {
  const entries = await getNotifyData(baseUrl);
  await postNotifyData(baseUrl, applyTargetEdit(entries, label, edit));
}
```

- [ ] **Step 4: Run, confirm pass. Commit.**

### Task 3: `ProbeNotifyModal`

**Files:** Create `web-react/src/components/dashboard/ProbeNotifyModal.tsx` + `.test.tsx`; modify `dashboard.css`

**Interfaces:** Consumes `TargetEdit`/`targetRange` (Task 2). Props:

```ts
interface Props {
  open: boolean;
  probeName: string;      // probe.title
  isPrimary: boolean;
  units: "F" | "C";
  initial: TargetEdit;    // from readTargetEdit(probe)
  saving: boolean;
  error: string | null;
  onSubmit(edit: TargetEdit): void;
  onCancel(): void;       // closes. Does NOT write. See landmine 7.
}
```

- [ ] **Step 1: Write failing tests.**
  - Renders nothing when `open` is false.
  - Title contains the probe name and the words "Notifications".
  - The master switch reflects `initial.enabled`; toggling it off disables/hides the target controls.
  - Slider `min`/`max` come from `targetRange` — assert 600 for primary+F and 225 for food+C.
  - The number input and the slider are bound both ways (typing 203 moves the slider; moving the slider updates the text).
  - The action control renders **only when `isPrimary` is false** (`_macro_dash_default.html:188-198`) and offers exactly three mutually exclusive choices: none / Shutdown PiFire / Start Keep Warm.
  - Submitting with the switch on and a target of 203 calls `onSubmit({ enabled: true, target: 203, action: … })`.
  - **Submitting with the switch on and target 0 does not call `onSubmit`** and shows a validation message. Rationale in a code comment: `condition` is `equal_above` (`defaults.py:524`), so a 0 target fires on the very next control pass — Flask's slider allows 0 and this is a deliberate, documented divergence.
  - Submitting with the switch **off** calls `onSubmit({ enabled: false, … })` even when target is 0 — that is how a user turns the notification off.
  - `onCancel` fires on the scrim click, on the Cancel button, and **never** calls `onSubmit`.
  - `saving` disables the submit button; `error` renders.
  - Re-seeding: changing `initial` while open re-seeds the fields via the **render-phase adjustment** pattern, not an effect.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.** Structure and classes copied from `SetpointEntry.tsx:29-64` — `pf-modal-scrim` > `pf-modal` > `pf-modal-title` / body / `pf-modal-actions` with `pf-modal-btn` and `pf-modal-btn accent`. Seed state exactly as `SetpointEntry.tsx:13-23` does:

```ts
  const [edit, setEdit] = useState<TargetEdit>(initial);
  // Re-seed from `initial` whenever the identity of what we're editing changes,
  // adjusted synchronously during render (React's recommended pattern for
  // deriving state from prop changes) rather than in an effect -- the React
  // Compiler lint forbids setState-in-effect here. Same shape as
  // SetpointEntry.tsx:13-23.
  const seedKey = `${open}|${probeName}|${initial.enabled}|${initial.target}|${initial.action}`;
  const [prevSeedKey, setPrevSeedKey] = useState(seedKey);
  if (seedKey !== prevSeedKey) {
    setPrevSeedKey(seedKey);
    if (open) setEdit(initial);
  }
  if (!open) return null;
```

  Add only the `pf-notify-*` classes you actually need to `dashboard.css`, adjacent to the existing `.pf-modal*` block at `:165-240`.
- [ ] **Step 4: Run, confirm pass. Commit.**

### Task 4: Probe-card view fields

**Files:** Modify `web-react/src/helpers/dashboard/deriveView.ts`; modify/extend its tests

**Interfaces:** `ProbeCardView` gains `label: string`, `notifyOn: boolean`, `etaStr: string | null`.

- [ ] **Step 1: Write failing tests.**
  - `probeCard` carries the probe's `label` through (needed as the write identity — landmine 3; `ProbeCardView` currently exposes only `name`, `deriveView.ts:29-37`, so there is no way to address the probe).
  - `notifyOn` is `targetReq` (**not** `hasNotifications`, which is also true when only a *limit* is armed — `socket_io.py:832-848`).
  - `etaStr` is `fmtDuration(eta)` when `targetReq && typeof eta === "number"`, and `null` otherwise (covers `eta: null`, `eta: "…"` — `types.ts:17` allows a string — and `targetReq: false`, matching the Flask ETA button's visibility at `_macro_dash_default.html:123-131`).
  - The existing `targetStr` / `tgtColor` / `barPct` assertions still pass unchanged.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement** inside `probeCard` (`deriveView.ts:95-107`). Do not change the existing fields.
- [ ] **Step 4: Run, confirm pass. Commit.**

### Task 5: Dashboard wiring

**Files:** Modify `web-react/src/components/dashboard/ProbeCard.tsx` + `.test.tsx`, `Dashboard.tsx` + `.test.tsx`

**Interfaces:** Consumes Tasks 2, 3, 4.

- [ ] **Step 1: Write failing tests.**
  - `ProbeCard` renders a button with accessible name `Notifications for {name}`; clicking it calls `onOpenNotify` with the probe's **label**. The button reflects `notifyOn` (`aria-pressed`).
  - `ProbeCard` renders `etaStr` when present and nothing when null.
  - `Dashboard` opens `ProbeNotifyModal` when a food-probe bell is clicked, seeded from that probe's `target`/`targetReq`/`targetShutdown`/`targetKeepWarm`.
  - `Dashboard` exposes a bell for the **primary** probe too, and opening it seeds from `dash.primaryProbe` with `isPrimary: true` (so the action control is absent and the range is 0–600 in F).
  - Submitting calls `saveTargetEdit` with `dash.primaryProbe.label` / the food probe's label. Stub the module with `rs.mock`.
  - A rejected save leaves the modal open and shows the error; it does **not** close optimistically. (This write is not echoed back until the control loop drains the queue — closing on failure would look like success.)
  - A successful save closes the modal. Assert **no** local mirroring of the new target into component state: the card must keep rendering from `dash`, so the value updates when the socket echoes it. Verified-facts basis: the backend clears `req`/`target` on its own when the target fires (`notifications.py:109-111`), so any local mirror would fight the truth.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.**
  - `ProbeCard` takes `onOpenNotify(label: string): void`. Bell button in the header row beside `p.targetStr` (`ProbeCard.tsx:22-34`); ETA as a small line under the progress bar.
  - `Dashboard` holds `const [notifyLabel, setNotifyLabel] = useState<string | null>(null)` plus `saving`/`error`, resolves the `ProbeData` for that label from `dash.primaryProbe`/`dash.foodProbes`, and renders one `ProbeNotifyModal`.
  - `saveTargetEdit` needs the base URL. `Dashboard` currently receives `command` but not `targetUrl` (`Dashboard.tsx:18-27`); `useDashData` already returns `targetUrl` (`useDashData.ts:60`). **Thread `targetUrl` through as a new prop** rather than importing `import.meta.env` in a component — every existing test constructs `Dashboard` explicitly and will need the new prop.
  - Primary bell: place it in the Cook Time row (`Dashboard.tsx:234-286`) as a fixed-width button beside the Cook Time card. See uncertainty 4 — placement is adjustable, presence is not.
- [ ] **Step 4: Run the full suite.** `DashboardRoute.tsx` also constructs `Dashboard`; update it for the new prop. Updating a test harness is fine; weakening an assertion is not.
- [ ] **Step 5: Commit.**

### Task 6: e2e + gate

**Files:** Create `web-react/tests/e2e/notify.spec.ts`

Requires a live backend (`uv run python control.py` + `uv run python app.py`), like every other spec here. `playwright.config.ts:24` runs `workers: 1` because the suite shares one stateful grill — respect that.

- [ ] **Step 1: Capture the baseline and register cleanup.** `GET /api/get/notify` before anything, and in a `finally` (or `afterEach`) `POST /api/control` with the captured array to restore it. **Notify state is real control state that can shut the grill down** — leaving a target armed after a test run is not acceptable. Call `ensureStopped(request)` first, as `roundtrip.spec.ts:7` does.
- [ ] **Step 2: Write the round-trip spec.** From `/`: click a food probe's bell, enable the target, set a value, choose Keep Warm, submit; then **poll `GET /api/get/notify`** until the `type === "probe"` entry for that label shows `req: true`, the target, and `keep_warm: true` — do not assume immediacy (uncertainty 1; the write is queued and drained by the control loop). Then assert the dashboard card re-renders the new target *from the socket echo*, which is the real cross-process seam this feature depends on.
- [ ] **Step 3: Pin the non-clobber property — this is the test that would have caught the landmine.** Before the run, arm `probe_limit_high` for the same probe directly via `POST /api/control` (target + `req: true`). Do the Step 2 target write through the UI. Then assert the `probe_limit_high` entry is **still armed and unchanged**. Restore in cleanup.
- [ ] **Step 4: Disable path.** Reopen the modal, turn the master switch off, submit, poll until `req: false` and `target: 0`, and assert the limit entry is *still* untouched (the deliberate divergence from Flask's `cancelNotify`).
- [ ] **Step 5: Check `logs/control.log`** for the `execute_control_writes: stripped null member(s)` ERROR (uncertainty 3). If it fires, fix the payload — do not suppress the log.
- [ ] **Step 6: Full gate** — `bun run typecheck && bun run lint && bun run test && bun run build && bun run gen:types:check`, then `bun run test:e2e`. Plus the repo-root artifact check (`os_info.json` / `settings.json` / `pelletdb.json` absent).
- [ ] **Step 7: Commit.**

---

## Parallelization

- **Wave 1:** Task 1 ∥ Task 2 ∥ Task 4 — disjoint files (`notify/notifyApi.*`, `notify/notifyState.*`, `dashboard/deriveView.ts`). Task 2 imports Task 1's `NotifyEntry` type only; write it against the type as declared here and the compile lands when Wave 1 merges, or run 1 → 2 sequentially if that is uncomfortable. Isolated jj workspaces.
- **Wave 2:** Task 3 (needs 2).
- **Wave 3:** Task 5 (needs 3 + 4) — **must be alone.**
- **Wave 4:** Task 6.

**Cross-plan serialization:** Task 5 edits `Dashboard.tsx`, and `2026-07-24-react-app-shell.md` Task 5 also edits `Dashboard.tsx` (removing `<Banners>`) and restructures routing around `DashboardRoute.tsx`. **These two tasks must not run concurrently.** Land the app shell first if it is in flight; this plan's Task 5 then adds a prop to whatever `Dashboard.tsx` looks like at that point.

## Slice 2 groundwork (do not implement here — recorded so the next plan does not re-derive it)

- Two more entries per probe, `probe_limit_high` (`condition: "equal_above"`) and `probe_limit_low` (`condition: "equal_below"`), both carrying a `triggered` boolean (`common/defaults.py:528-538`).
- The client **must pre-arm `triggered`** on save or the alarm fires instantly: `dash_default.js:721-725` sets `triggered = current > target` for high, `:763-767` sets `triggered = current < target` for low — i.e. mark it already-triggered when the condition is *already* satisfied, so `notifications.py:112` stays quiet until the temperature leaves and re-enters the range.
- `/api/set/limit_high/...` and `/api/set/limit_low/...` exist (`api_commands.py:434-437`, dispatched `:721-722`) but **cannot set `triggered`** (`:456` accepts only `req`/`shutdown`/`keep_warm`/`reignite`, plus `target`). Slice 2 is therefore also forced onto `POST /api/control`.
- Flask asymmetry to decide on: the high/low **temperature sliders** render for every probe, but the "Shutdown PiFire" checkbox renders **only for Primary** (`_macro_dash_default.html:238-244`, `:284-289`), as does "Attempt Re-ignite" (`:290-293`), which is JS-enforced mutually exclusive with shutdown (`:294-308`).
- **Two live Flask bugs to decide about rather than port:**
  1. `dash_default.js:752-757` reads `#{label}_low_limit_shutdown` **twice** — once for `shutdown`, once (copy-paste) for `reignite`. The "Attempt Re-ignite" checkbox is never read; `reignite` just mirrors `shutdown`.
  2. `notifications.py:141-149` gates the shutdown action on `not control["notify_data"][index]["req"]`, but limit entries never clear `req` (only `type == "probe"` does, `:109`). So **high/low-limit "Shutdown PiFire" appears never to fire.** `reignite` is gated on `triggered` instead (`:160-164`) and does work. I did not execute this — it is read from source and should be confirmed empirically before Slice 2 either replicates or omits the control.

## Self-Review

**Spec coverage:** target temperature → T2/T3; notify request (master switch) → T2/T3; shutdown-on-reach and keep-warm-on-reach → T2 (`TargetAction`) / T3; ETA → T4/T5; primary probe parity with Flask → T5; the write contract → T1; non-clobber of limits → T2 Step 1 + T6 Step 3. High/low limits are explicitly deferred and scoped above. **Placeholder scan:** none — every step names the file and the verified backend line. **Type consistency:** `NotifyEntry` defined in T1, consumed by T2; `TargetEdit`/`TargetAction`/`targetRange` defined in T2, consumed by T3 and T5; `ProbeCardView` extended in T4, consumed in T5. **Honesty check:** four items are listed under "Things I could not verify" with the task that must resolve each, and the suspected `probe_limit_*` shutdown bug is marked as read-not-executed rather than asserted.
