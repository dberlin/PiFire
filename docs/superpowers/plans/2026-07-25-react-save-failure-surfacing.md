# React Settings Save-Failure Surfacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a rejected settings save visible. Today the API layer already carries the
server's rejection message and the layer above it throws the message away, so a save the
backend refused looks identical to one it accepted — the refused values sit on screen with
a green "Saved ✓" absent and nothing else changed.

**Architecture:** One change at the shared layer (`useSaveSettings` returns a *status*, not
a boolean) plus one shared affordance (`SaveBar`) that replaces a byte-identical 6-line
block currently duplicated across all 9 saving tabs. No per-tab error handling.

**Tech Stack:** React 19 + react-router, TS7, rsbuild, Biome, @rstest/core, Playwright, bun.

## Why this exists

This branch made `write_settings()` **hard-strict**
(`common/datastore_accessors.py:297-315`): it calls `validate_settings_tree()` *before*
stamping `lastupdated.time` or persisting anything, so a schema violation raises
`SettingsValidationError` and the whole delta is refused with the store left untouched.
That is a good property — the write is atomic — but it is only half a feature. The other
half is telling the user, and the React UI does not.

This is not theoretical. The rejection path is reachable from the UI today: raising
`pwm.min_duty_cycle` above an existing profile duty cycle trips
`PwmSettings._check_profiles` (`common/settings_schema.py:303-316`), and `PwmTab.tsx:78-85`
neither clamps nor guards (audit finding I6). The user gets silence.

> **DECIDED 2026-07-25, after this plan shipped: the unit test is the accepted
> coverage for the UI half.**
>
> The settings guards sweep landed on top of this slice and clamped exactly the
> value the witness spec raised, so `PwmTab.onSave` now re-clamps every profile
> duty cycle into the new range and that save succeeds by design. The vector
> above is no longer reachable through the UI.
>
> A search for a replacement found none: every schema constraint is now
> client-bounded, pre-flight-guarded, clamped at save, structurally maintained,
> or on a field React does not render. The options were to accept
> `PwmTab.test.tsx`'s "surfaces a rejected save inline and withholds the success
> marker" as the coverage, or to add a fault-injection e2e whose only purpose is
> to manufacture a rejection the product can no longer produce. **The unit test
> is accepted.** Inventing a rejection path in order to test rejection would
> test the fixture, not the feature.
>
> Still covered elsewhere: the server-rejection channel itself by the API-level
> spec (`settings.spec.ts`, "invalid settings_update delta is rejected
> atomically with a dotted-path error"), and the UI-level behaviour by the
> `PwmTab` unit test. The original witness spec was re-pointed rather than
> deleted, and now proves the clamp.

It also contradicts the project's own design spec —
`docs/superpowers/specs/2026-07-22-settings-foundation-design.md:169`: *"Save failure →
inline error on the tab."*

## Global Constraints

- Test runner is **@rstest/core** (`rs.fn`, `rs.mock`, `rs.stubGlobal`) — `vi` does NOT
  exist. `.test.tsx` → jsdom, `.test.ts` → node.
- **bun**, never npm.
- **No suppressions**: no `biome-ignore`, no `@ts-expect-error`, no `eslint-disable`.
- **No `setState` in `useEffect` for derived state** (React Compiler). Render-phase
  adjustment only — see `settings/tabs/GeneralTab.tsx:22-27`, `tabs/UnitsTab.tsx:24-28`.
- `react-refresh/only-export-components`: non-components go in their own module. The
  `SaveStatus` *type* must be exported from the hook module, not from `SaveBar.tsx`.
- Gate: `bun run typecheck && bun run lint && bun run test && bun run build && bun run gen:types:check`.
- Reuse the existing `pf-*` class vocabulary. **No new CSS is needed** — see Verified facts.

## Verified facts (checked against live code — do not re-derive, do not guess)

**The information exists and is discarded exactly one layer up.**
`helpers/settings/settingsApi.ts:66-84` — `applySettings()` already returns
`{ ok: boolean; message: string; data?: Settings }` and populates `message` on all four
failure paths: non-2xx HTTP (`:74` → `"HTTP 503"`), a `result !== "success"` envelope
(`:76`), a thrown fetch (`:78`), and success (empty string).
`helpers/settings/useSaveSettings.ts:11-18` then does `return r.ok` — `r.message` and
`r.data` are dropped on the floor. **Confirmed: the fix is a one-line-of-information
plumbing change, not new data acquisition.**

**Backend response shape — `POST /api/settings_update`** (`blueprints/api/routes.py:149-201`,
dispatch registered at `:282`). Three distinct rejection paths, **all of them HTTP 200**:

| Path | Line | Body |
|---|---|---|
| Unknown control flag | `:185` | `{"result":"error","message":"Unknown flag: <flag>","data":{}}` |
| Layer 1 — field-level partial-schema validation of the *delta* | `:187-190` | `{"result":"error","message":"Settings update failed: <dotted.path: reason>[; …]","data":{}}` |
| Layer 2 — `SettingsValidationError` from `write_settings()` on the *merged tree* | `:197-199` | same envelope, same message prefix |
| Unexpected exception | `:200-201` | same envelope, `message` is `str(e)` |

**Status code is 200 on every rejection**, so `res.ok` is `true` and the discrimination is
purely `body.result === "success"` — which `applySettings` already does correctly at
`settingsApi.ts:76`. Nothing in the API layer needs changing.

**Does the message name the offending field? Yes.** `SettingsValidationError.errors` is
built by `_format_errors()` (`common/settings_schema.py:610-611`) as
`f"{'.'.join(err['loc'])}: {err['msg']}"` — a dotted path plus pydantic's reason, joined
with `"; "`. This is already pinned end-to-end: `web-react/tests/e2e/settings.spec.ts:110-131`
POSTs `{safety: {maxtemp: "nope"}}` and asserts `result === "error"`, that `message`
contains `"safety.maxtemp"`, and that the store read-back is unchanged. **So the plan can
render the server's message verbatim and the user gets the field name for free.** Note that
a `model_validator` failure (cross-field) reports the *section* as the path with the reason
naming the field — e.g. `pwm: Value error, profiles[0].duty_cycle must be within
[min_duty_cycle, max_duty_cycle]`. Still actionable; still not something to reformat.

**Flask's behaviour, for parity.** `blueprints/settings/routes.py:702-718` is a single
choke point that catches `SettingsValidationError` and routes it two ways: JSON handlers
get `{"result":"error","message":"Settings update rejected: <msg>"}`; form-POST handlers
get `event = {"type":"error","text": …}`, rendered as a red dismissible alert by
`blueprints/settings/templates/settings/index.html:33-41`. **Inline on the page, next to
the form** — not global chrome.

**The 9 saving tabs are structurally identical.** `useSaveSettings` has exactly 9 non-test
consumers, one `await save(...)` each:

| Tab | save call | flags |
|---|---|---|
| `GeneralTab.tsx:32` | `save(delta, [])` | none |
| `WorkModeTab.tsx:100` | `save(d, ["settings_update"])` | |
| `ControllerTab.tsx:109` | `save(d, ["controller_update"])` | |
| `PwmTab.tsx:84` | `save(d, ["settings_update"])` | |
| `StartupTab.tsx:121` | `save(d, ["settings_update"])` | |
| `SafetyTab.tsx:47` | `save(d, [])` | |
| `PelletsTab.tsx:69` | `save(d, flags)` | computed |
| `HistoryTab.tsx:112` | `save(d, [])` | |
| `NotificationsTab.tsx:69` | `save({notify_services: v.ns}, ["settings_update"])` | |

All nine wrap it as `setSaved(await save(...))` over a local `const [saved, setSaved] =
useState(false)`, and all nine render **the same six lines**
(`GeneralTab.tsx:39-44`, `WorkModeTab.tsx:232-237`, `ControllerTab.tsx:178-183`,
`PwmTab.tsx:134-139`, `StartupTab.tsx:221-226`, `SafetyTab.tsx:93-98`,
`PelletsTab.tsx:119-124`, `HistoryTab.tsx:206-211`, `NotificationsTab.tsx:323-328`):

```tsx
<div className="pf-settings-actions">
  <button className="pf-modal-btn accent" disabled={saving} onClick={onSave}>
    {saving ? "Saving…" : "Save"}
  </button>
  {saved && <span className="pf-settings-saved">Saved ✓</span>}
</div>
```

**The audit's "eleven beneficiaries" (C2) is wrong — it is nine.** `SettingsShell.tsx:5-17`
lists 11 tabs, but `UnitsTab` does not use `useSaveSettings` (it goes through
`createCommand(BASE_URL).setUnits()`, `UnitsTab.tsx:38`) and `PlatformTab` is read-only by
recorded decision. Do not touch either.

**An error affordance already exists — reuse it, do not invent one.** `UnitsTab.tsx:20,
43-45, 60` is the house pattern: `const [error, setError] = useState<string | null>(null)`,
set from `r.message`, rendered as `{error && <p className="pf-settings-error-text">{error}</p>}`.
The class is already defined at `components/settings/settings.css:185-188`
(`color: #ff8b82; font: 600 14px "Barlow"`). **No new CSS.**

**What NOT to reuse.** `components/settings/SettingsError.tsx` is the route
`errorElement` — a whole-page "Couldn't load settings" fallback with Retry/Dashboard
buttons (`App.tsx:62`). Wrong granularity; leave it alone.
`components/dashboard/Banners.tsx` renders `dash.errors` / `dash.warnings` /
`dash.criticalError` from the `socket_dash_data` feed — see the decision below.

---

## Design decisions (answered, with rationale)

**1. Where does the error appear — inline near Save, or the global `Banners` strip?**

**Recommendation: inline, immediately below the Save button, inside `SaveBar`.** Three
reasons, in order of weight:

- **Different data source.** `Banners.tsx` is driven entirely by the shell's single
  `socket_dash_data` subscription (`plans/2026-07-24-react-app-shell.md:90` — `AppShell`
  owns `useLiveState()` and renders `<Banners>`). Routing a settings-save rejection there
  means either cross-wiring an unrelated client-side error source into shell state, or
  inventing a second banner channel. Both are architecture changes to a slice that is
  landing right now; this one is not worth them.
- **`Banners` items are not dismissible and do not unmount on navigation.** A stale "your
  PWM save was rejected" would follow the user to the dashboard. The inline version is
  scoped to a per-tab hook instance and disappears the moment the tab unmounts (see
  decision 4).
- **Flask does it inline.** `settings/index.html:33-41` renders the alert in the settings
  page body, not in `base.html` chrome. Parity is the point of the port.

**2. Do refused values stay on screen, or revert?**

**Recommendation: they stay.** The write was atomic and the store is untouched
(`datastore_accessors.py:312-315`), so there is no drift to correct — and reverting would
destroy the user's typing at exactly the moment they need to look at it and fix it. The
existing success path already handles resync correctly: `useSaveSettings.ts:16` calls
`revalidator.revalidate()` **only when `r.ok`**, which re-runs `settingsLoader` and drives
every tab's render-phase re-sync (`GeneralTab.tsx:22-27` and siblings). Preserve that
asymmetry exactly — a failed save must still not revalidate. `useSaveSettings.test.tsx:81-93`
already pins this; do not weaken it.

**3. Should the error name the offending field?**

**Yes, and it already does — render the server's message verbatim.** The dotted path is in
the string (verified above). Do **not** attempt to parse the dotted path and highlight the
corresponding widget in this slice: the loc for a `model_validator` failure is the section,
not the field, so the mapping is not total, and a half-working highlight is worse than
a precise sentence. Strip only the `"Settings update failed: "` prefix — it is noise once
the text sits under a Save button that visibly failed. Prefix-stripping belongs in the hook
(pure, testable), not in the component.

**4. When does the message clear?**

At the start of the next save (`setStatus({kind:"idle"})` before the request), and on
unmount — which happens on every tab switch, since each tab calls `useSaveSettings()`
itself. Do **not** add per-field `onChange` clearing: that is 9 bespoke edits, which is
exactly what this plan exists to avoid. Note this also fixes a smaller existing wart —
today's `saved` flag never clears either, so a stale "Saved ✓" can sit next to unsaved
edits.

---

## File Structure

- Modify `web-react/src/helpers/settings/useSaveSettings.ts` — return `{ save, saving,
  status, baseUrl }`; export the `SaveStatus` type.
- Modify `web-react/src/helpers/settings/useSaveSettings.test.tsx` — cover the error status.
- Create `web-react/src/components/settings/SaveBar.tsx` + `SaveBar.test.tsx`.
- Modify all 9 tabs listed above — delete the local `saved` state, render `<SaveBar>`.
- Modify `web-react/tests/e2e/settings.spec.ts` — add a UI-driven rejection spec.
- **No CSS file changes.** `pf-settings-actions`, `pf-settings-saved` and
  `pf-settings-error-text` all already exist (`settings.css:170-188`).

---

### Task 1: `useSaveSettings` returns a status instead of a boolean

**Files:** Modify `web-react/src/helpers/settings/useSaveSettings.ts`; Test
`web-react/src/helpers/settings/useSaveSettings.test.tsx`

**Interfaces:** Produces the exported type

```ts
export type SaveStatus =
  | { kind: "idle" }
  | { kind: "saved" }
  | { kind: "error"; message: string };
```

and `useSaveSettings(): { save(delta: object, flags: SettingsFlag[]): Promise<boolean>;
saving: boolean; status: SaveStatus; baseUrl: string }`.

`save` keeps returning `boolean` deliberately — the nine call sites read it as
`setSaved(await save(...))` today and Task 3 changes them to bare `await save(...)`; the
*status* is what the UI reads. Keeping the return type means Task 1 lands green on its own
and nothing is half-migrated between commits.

- [ ] **Step 1: Write failing tests** in the existing file, extending the existing
      `mockApplySettings` harness (`useSaveSettings.test.tsx:6-19`; it uses `rs.mock` +
      a deferred `await import("./useSaveSettings")` — follow that verbatim, do not
      restructure it). Have `Probe` render `status.kind` and, when it is `"error"`,
      `status.message`, into new `data-testid`s. Assert:
      - initial status is `"idle"`;
      - `{ok:true, message:""}` → `"saved"`, and the loader re-runs (revalidation);
      - `{ok:false, message:"Settings update failed: safety.maxtemp: Input should be a valid integer"}`
        → `kind === "error"` and message **`"safety.maxtemp: Input should be a valid integer"`**
        (prefix stripped);
      - `{ok:false, message:"HTTP 503"}` → `kind === "error"`, message `"HTTP 503"`
        (no prefix, nothing stripped, no crash);
      - `{ok:false, message:""}` → `kind === "error"` with a non-empty fallback message
        (`"Save failed."`) — an empty red gap is a worse bug than the one being fixed;
      - status returns to `"idle"` for the duration of a second in-flight save.
- [ ] **Step 2: Run, confirm they fail** — `bun run test src/helpers/settings/useSaveSettings.test.tsx`.
- [ ] **Step 3: Implement.**
      ```ts
      const PREFIX = "Settings update failed: ";
      // Exported for the test; pure, no React.
      export function normalizeSaveError(message: string): string {
        const stripped = message.startsWith(PREFIX) ? message.slice(PREFIX.length) : message;
        return stripped.trim() || "Save failed.";
      }
      ```
      In `save`: `setStatus({ kind: "idle" })` before `applySettings`, then
      `setStatus(r.ok ? { kind: "saved" } : { kind: "error", message: normalizeSaveError(r.message) })`.
      **Leave `if (r.ok) revalidator.revalidate()` exactly as it is** (`:16`) — decision 2.
      `useSaveSettings.ts` is a hook module, so `react-refresh/only-export-components` does
      not apply; exporting the type and `normalizeSaveError` alongside the hook is fine.
- [ ] **Step 4: Run, confirm pass. Commit.**

### Task 2: `SaveBar` — the one shared affordance

**Files:** Create `web-react/src/components/settings/SaveBar.tsx` + `SaveBar.test.tsx`

**Interfaces:** Consumes `SaveStatus` (Task 1). Produces
`<SaveBar onSave={() => void | Promise<void>} saving={boolean} status={SaveStatus} />`.

This is a pure presentational component — it takes state, it does not call the hook. Each
tab already holds its own `useSaveSettings()` instance and builds its own delta; the bar
must not second-guess either.

- [ ] **Step 1: Write failing tests.** Render with each status:
      - `idle` → a "Save" button, no "Saved ✓", no error paragraph;
      - `saving` → button label "Saving…" and `disabled`;
      - `saved` → "Saved ✓" present, no error;
      - `error` → the message text is rendered, **"Saved ✓" is absent**, and the button is
        enabled again so the user can retry after fixing the value;
      - clicking the button calls `onSave` exactly once.
      Assert the error node carries `role="alert"` so a screen reader announces it — the
      user's eyes are on the field they just edited, not on the button.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement**, reusing the exact existing markup so nothing shifts visually:
      ```tsx
      import type { SaveStatus } from "../../helpers/settings/useSaveSettings";

      export function SaveBar({
        onSave,
        saving,
        status,
      }: {
        onSave: () => void | Promise<void>;
        saving: boolean;
        status: SaveStatus;
      }) {
        return (
          <div className="pf-settings-actions">
            <button className="pf-modal-btn accent" disabled={saving} onClick={onSave}>
              {saving ? "Saving…" : "Save"}
            </button>
            {status.kind === "saved" && <span className="pf-settings-saved">Saved ✓</span>}
            {status.kind === "error" && (
              <p className="pf-settings-error-text" role="alert">
                {status.message}
              </p>
            )}
          </div>
        );
      }
      ```
      `pf-settings-actions` is `display: flex; align-items: center` (`settings.css:170-175`),
      so the `<p>` sits inline next to the button. If a long dotted-path message crowds the
      row, that is a real finding — report it, do not silently add CSS outside this file's
      scope.
- [ ] **Step 4: Run, confirm pass. Commit.**

### Task 3: Migrate all 9 tabs

**Files:** Modify `GeneralTab.tsx`, `WorkModeTab.tsx`, `ControllerTab.tsx`, `PwmTab.tsx`,
`StartupTab.tsx`, `SafetyTab.tsx`, `PelletsTab.tsx`, `HistoryTab.tsx`,
`NotificationsTab.tsx` (+ their `.test.tsx` where they assert on "Saved ✓").

Mechanical and identical in every file. Three edits per tab:

1. Delete `const [saved, setSaved] = useState(false);`.
2. `setSaved(await save(d, flags));` → `await save(d, flags);` (keep each tab's existing
   delta construction and flag list **byte-for-byte** — the flags table in Verified facts
   is the checklist; a changed flag is a control-loop bug, not a refactor).
3. Replace the six-line `pf-settings-actions` block with
   `<SaveBar onSave={onSave} saving={saving} status={status} />`, and take `status` from
   `useSaveSettings()`.

- [ ] **Step 1: Do one tab first — `GeneralTab.tsx`** (the smallest, `:15-47`). Run its
      test. Whatever the existing test asserts about "Saved ✓" must still pass unchanged;
      if it does not, `SaveBar` diverges from the old markup and Task 2 is wrong. Fix Task 2,
      not the test.
- [ ] **Step 2: Apply the same three edits to the other eight.** Watch for `useState` becoming
      an unused import in a tab that had no other local state — Biome will flag it.
      `PelletsTab.tsx:69` passes a *computed* `flags`; leave that computation alone.
- [ ] **Step 3: Add one error-path test per tab? No — add exactly one**, in
      `PwmTab.test.tsx`: mock `applySettings` to reject with a dotted-path message and
      assert the message renders and "Saved ✓" does not. Nine copies of the same assertion
      would test `SaveBar` nine times; `SaveBar.test.tsx` already owns that. PwmTab is the
      chosen witness because it is the tab with a live, reachable rejection (I6).
- [ ] **Step 4: Full gate** — `bun run typecheck && bun run lint && bun run test && bun run build && bun run gen:types:check`.
      `typecheck` is the safety net here: any tab left on the old shape fails on the deleted
      `saved` binding. Do not stop at the first green file.
- [ ] **Step 5: Commit.**

### Task 4: e2e — prove it end-to-end against the real backend

**Files:** Modify `web-react/tests/e2e/settings.spec.ts`

The existing spec at `:110-131` proves the *API* rejects atomically with a dotted-path
message. It cannot see the UI. This adds the missing half.

- [ ] **Step 1: Write the spec.** Navigate to `/settings/pwm`. Read the current
      `pwm.min_duty_cycle` / `max_duty_cycle` / `profiles[*].duty_cycle` via
      `GET /api/settings` first (do not assume defaults — the e2e suite shares one store).
      Set `min_duty_cycle` above the lowest profile duty cycle so
      `PwmSettings._check_profiles` (`common/settings_schema.py:313-315`) must reject, click
      Save, and assert:
      - an alert is visible whose text mentions `duty_cycle`;
      - **"Saved ✓" is not visible**;
      - `GET /api/settings` read-back shows `min_duty_cycle` **unchanged** — the atomicity
        guarantee and the UI message have to agree.
- [ ] **Step 2: Restore state.** The store is shared and the suite runs `workers: 1`
      precisely because of cross-test interference. This test writes nothing on the failure
      path, which is the point — but if the setup step had to change any value to reach the
      rejection, restore it in a `finally`, not at the end of the happy path.
- [ ] **Step 3: Run** the e2e suite. **Chromium is unavailable in agent worktrees and
      `[chromium]` tests SKIP silently there** — if the run reports 0 executed, say so
      explicitly in the task report rather than claiming a pass; the spec must be re-run in
      the main checkout before merge.
- [ ] **Step 4: Commit.**

## Parallelization

- **Wave 0:** Task 1 alone. Task 2 imports the `SaveStatus` type it defines.
- **Wave 1:** Task 2 alone (needs Task 1's type). Small.
- **Wave 2:** Task 3 alone — it edits 9 tab files plus their tests, and it is the only task
  that touches them. Do not run it concurrently with any other settings-tab work
  (notably the audit's I5 `dc_fan` gating and I15 Startup-tab conditionals, which edit
  `WorkModeTab`/`StartupTab`/`PwmTab`). If those are in flight, serialize behind them.
- **Wave 3:** Task 4.

Isolated jj workspaces per wave; the waves are strictly sequential, so this plan does not
benefit from concurrency. Its value is that it is small — four commits — and it unblocks the
audit's I5, I6, I15 and M16 findings, every one of which currently produces an *invisible*
rejection.

## Self-Review

**Spec coverage:** "Save failure → inline error on the tab"
(`specs/2026-07-22-settings-foundation-design.md:169`) → Tasks 1-3; verified against the
real backend → Task 4. **Placeholder scan:** none — every response shape, class name and
call site is cited to live code above. **Type consistency:** `SaveStatus` is defined in
Task 1 and consumed in Tasks 2 and 3; `save`'s `Promise<boolean>` signature is deliberately
unchanged so Task 1 lands independently green. **Not in scope, deliberately:** mapping a
dotted path to a specific widget (decision 3); client-side pre-validation that would prevent
the rejection in the first place (audit I6 — a separate item, and this plan is what makes
its absence survivable); `UnitsTab` and `PlatformTab` (neither uses this hook).
