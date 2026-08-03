# Deferred-work inventory — `docs/superpowers/plans/` (React migration slice plans)

Scope: the 17 plan docs assigned. Wizard plans, specs and audits are covered by another agent.
Each item: title / source / what was deferred / status verified against live code at 2026-07-26.

Status legend: **STILL-OPEN** = not in live code, needs doing. **ALREADY-DONE** = shipped or
superseded since the plan was written. **UNCLEAR** = needs a human decision or a live observation.

Totals: **75 findings — 39 STILL-OPEN, 31 ALREADY-DONE, 5 UNCLEAR.**

**Reconciled 2026-07-28:** 11 of the first-15 open findings are closed and
moved to the RESOLVED section below — #1, #2, #6, #9, #18, #21, #22, #25, #27,
#29, #30. Still open from that set: #5, #17 (each its own slice), #19
(matches Flask — masking is a new feature, not a port), #26 (accepted risk).

**Reconciled 2026-07-28 — sweep 2 (the rest of the STILL-OPEN labels vs live
code).** The per-finding `Status:` lines below were verified at 2026-07-26 and
several have since gone stale. Current disposition of every remaining finding:

- **SHIPPED since the sweep / stale STILL-OPEN labels — nothing to do:**
  - **#17** WLED preset/profile editor — BUILT 2026-07-28 (`WledCard.tsx` +
    `helpers/notify/wledApi.ts`, all three action buttons). See the backlog.
  - **#33** limit "Shutdown PiFire" — FIXED: `notify/notifications.py` now gates
    a limit's shutdown on `triggered` (not `not req`); the dead-checkbox bug the
    audit confirmed is gone, and the code comment documents it.
  - **#45** `display.sleep_timeout` — RENDERED: `GeneralTab.tsx` shows "Screen
    Sleep Timeout", bound and saved via `display.sleep_timeout` (ruling 4;
    seam-pinned by `test_settings_update_sleep_timeout_*`).
  - **#46** per-tab Save losing unsaved edits — FIXED: drafts are held on
    `SettingsShell` (`useSettingsDraftStore`, passed via Outlet context), so an
    edit survives a trip to another tab. (The Flask-persists-immediately
    *divergence* is a separate deliberate choice, not a gap.)
  - **#48** `pf-section-note` / `pf-kv` unstyled — RESOLVED with #22: `pf-kv`
    rules shipped; `pf-section-note` is intentionally unstyled (allowlisted).
  - **#57** errors blob write-only / `_check_control_status` false-positive —
    FIXED 2026-07-26 (item 4): reads are non-destructive; liveness is a
    non-sticky in-memory signal.
  - **#69** `clear_pelletdb` shelling `os.system("rm pelletdb.json")` — FIXED:
    it calls `clear_pellet_db()`; `test_page_admin.py:366` /
    `test_socketio_app_data.py:349` assert the `os.system` rm is gone.
  - **#70** dashboard hopper → `/pellets` shortcut — SHIPPED: `HopperGauge.tsx`
    renders `<Link to="/pellets">` (the 2026-07-26 ruling).
- **Sweep-2 CORRECTION (2026-07-29) — three of the labels below were wrong.**
  **#31 / #32 / #34 are SHIPPED**, not open: a concurrent session landed Slice 2
  (`08071c7e`, `21f2d977`) while this reconciliation was being written — full
  high/low limit alerts in `ProbeNotifyModal`, wired in `Dashboard.tsx`, with
  #32's `triggered` pre-arm and #34's asymmetry *decided* (limit temps for every
  probe; Shutdown/Re-ignite actions Primary-only). See items 31/32/34 below.
  **#35 is FIXED** (2026-07-29): `_cmd_set_units` now converts armed
  `control["notify_data"]` targets via addressed `notify.set` ops.
- **Fixed 2026-07-29:** **#44** Barlow self-hosted via `@fontsource` (no CDN),
  **#58** recipe *unpause* ported (`recipeUnpause` minimal patch), **#67**
  `backup_pellet_db` now called on the React load path. See items 44/58/67.
- **Confirmed STILL-OPEN after the correction (genuine, re-verified):** **#5**
  (SPA serve — deferred to the Flask-retirement pass by request), **#71** every
  legacy Flask blueprint still registered (`app.py:68-93` — the general
  Flask-retirement pass, ruling 5).
- **Accepted divergences / by-decision / won't-do — already dispositioned, no
  code action:** #26, #36, #37, #47, #50, #54, #56, #59, #60, #63, #68, #72, #73.
- **UNCLEAR — needs a browser look or a live observation, not a code read:**
  #49, #55, #61, #62, #74, #75.
- **Enhancement beyond Flask parity (accepted, tracked in the backlog):** #19.

---

## RESOLVED 2026-07-28 (reconciliation)

Findings closed after the 2026-07-26 sweep, re-verified against live code on
2026-07-28 (reconciliation of the first 15 open findings; see
`backlogs/react-migration-backlog.md` item 10 and ruling 8). Each keeps its original
number and its original 2026-07-26 assessment below a RESOLVED header, for
provenance.

**→ RESOLVED 2026-07-28 — not a web-react target -- the QML kiosk is the on-device touchscreen UI and stays; React only borrowed its visual language (Theme.qml -> theme.css). Backlog ruling 8 (2026-07-28).**

1. **QML kiosk screens never built: Splash, MenuScreen, Keypad, Hold/Notify overlays, QR, Sleep**
   - Source: "## Phasing" steps 4–5; "Explicitly deferred in the spike" (line 81).
   - Deferred: `MenuScreen` (from `Menus.js`), numeric `Keypad`, `HoldInput`/`NotifyInput` overlays,
     `SplashScreen`, `QrCodeScreen`, `SleepOverlay`, and `Actions.js` routing.
   - Status: STILL-OPEN — zero hits for splash/sleep/qrcode/keypad in `web-react/src` (only a
     generated-types match in `web-react/src/helpers/settings/settingsTypes.gen.ts`). Note the project
     pivoted from "clone the QML kiosk" to "replace the Flask web UI", so these may be *intentionally*
     dead. Needs a one-line ruling in the backlog either way.

**→ RESOLVED 2026-07-28 — not a web-react target -- parity was against the kiosk's LOOK (theme tokens, shipped), never its screens. Backlog ruling 8 (2026-07-28).**

2. **Phase 6 parity check against the QML screens and `tests/ui/test_qtquick_*.py` never run**
   - Source: "## Phasing" step 6; "## Verification" last paragraph.
   - Deferred: cross-check React screens against `display/qml/screens/*` and the existing behaviour
     specs in `tests/ui/`.
   - Status: STILL-OPEN — `tests/ui/` exists (`test_flex_*`, `test_qtapp_power.py`, …) but nothing in
     `web-react/` references it and no parity artifact exists.

**→ RESOLVED 2026-07-28 — assumption confirmed -- after_startup_mode is exactly {Smoke, Hold} (settings_schema Literal + Flask settings/index.html:808-809).**

6. **Unverified assumption: `after_startup_mode` options are only Smoke/Hold**
   - Source: Task 9 Step 1 (line 423) — "verify against the wizard/settings template if more exist —
     default to Smoke/Hold".
   - Status: UNCLEAR/STILL-OPEN — `common/settings_schema.py` pins
     `after_startup_mode: Literal["Smoke", "Hold"]`, so the schema now agrees with the guess, but the
     Flask template was never cross-checked. Low risk; one grep closes it.

**→ RESOLVED 2026-07-28 — seam pinned -- tests/web/test_api_settings_update.py::test_settings_update_table_save_flag_does_not_alter_stored_settings (a table save WITH the flag stores the same settings as a bare write).**

9. **Accepted divergence: React sends `settings_update` on table-only saves where Flask sends a bare write**
   - Source: Task 10 "Interfaces" (line 413) — "**plan ruling: one Save per tab, existing flags kept**
     … The reviewer should verify this reasoning, not silently narrow it."
   - Deferred: SmartStart / PWM range-table edits ride in the tab's single delta with
     `["settings_update"]`, whereas Flask's separate table forms are bare writes. Judged a harmless
     superset (control loop re-reads settings) but never independently verified.
   - Status: STILL-OPEN as an unverified reasoning claim —
     `web-react/src/components/settings/tabs/StartupTab.tsx` and `PwmTab.tsx` still save with
     `["settings_update"]`. No test pins that the extra flag is harmless.

**→ RESOLVED 2026-07-28 — confirmed BY-DECISION won't-do -- OneSignal devices self-register; only friendly_name editable (NotificationsTab.tsx). Matches Flask.**

18. **OneSignal: no "add device", and `uuid`/`app_id` not editable**
    - Source: Global Constraints line 16; "Verification summary" non-goals (line 176).
    - Status: STILL-OPEN by design — `NotificationsTab.tsx:190-241` edits `friendly_name` and deletes
      rows only. Matches Flask (devices self-register), so this is a documented won't-do rather than a
      gap; recorded for completeness.

**→ RESOLVED 2026-07-28 — confirmed BY-DECISION -- PlatformTab read-only; platform.* is owned by the Setup Wizard.**

21. **PlatformTab is read-only: no React editor for `platform.*`**
    - Source: "Design decisions → **D1**" (line 13).
    - Deferred: editing `current`, `system_type`, `dc_fan`, `triggerlevel`, `standalone`, `real_hw`,
      and output pins — delegated to the Setup Wizard to avoid dual ownership.
    - Status: STILL-OPEN by decision — `tabs/PlatformTab.tsx` renders a `<dl>` + a "Configure in Setup
      Wizard" link. Ratified, but it *is* a Flask-vs-React difference worth a backlog line.

**→ RESOLVED 2026-07-28 — .pf-kv / .pf-kv-row now have rules (settings.css); .pf-section-note is intentionally unstyled (allowlisted in styleCoverage.test.ts, asserted for exact equality).**

22. **PlatformTab's markup uses CSS classes that have no rule anywhere**
    - Source: not in this plan; recorded in `2026-07-25-react-settings-guards-sweep.md` "Missing CSS
      classes" (line 327). Cross-listed here because it is PlatformTab's markup.
    - Status: STILL-OPEN — `pf-section-note` and `pf-kv`/`pf-kv-row` appear in no stylesheet
      (`rg` over `src/theme.css`, `settings.css`, `dashboard.css`, `shell.css`, `wizard.css`,
      `pellets.css`, `historyChart.css`). Only `.pf-field-hint` was added
      (`web-react/src/components/settings/settings.css:192`).

**→ RESOLVED 2026-07-28 — SHIPPED -- web-react/src/components/cookfiles/* + helpers/files/cookfileApi.ts (list/upload/delete), route in App.tsx.**

25. **D4 — cook-file list / upload / delete not ported**
    - Source: "Design decisions → **D4 — Chart only**" (line 16).
    - Deferred: the `/history` cook-file management UI (listing, upload, delete), parked with the
      "cookfile + recipes" backlog item because they share a data model and need a JSON listing
      endpoint that does not exist.
    - Status: STILL-OPEN — no cookfile component or helper in `web-react/src`; `blueprints/cookfile/`
      is Flask-only (`app.py:99`).

**→ RESOLVED 2026-07-28 — SHIPPED -- NavBar.tsx has real to: targets for all seven nav entries; no disabled spans or to:null remain.**

27. **Recipes, Events and Admin render disabled — three whole Flask pages unported**
    - Source: "**Decision:** show all 6; Recipes, Events and Admin render disabled (not links) since
      they are not ported. Do not link them to the Flask pages." (line 59).
    - Status: STILL-OPEN — `web-react/src/components/shell/NavBar.tsx:16-24` has
      `{ label: "Recipes", to: null }`, `{ label: "Events", to: null }`, `{ label: "Admin", to: null }`
      rendered as `<span aria-disabled="true" title="Not available in the new interface yet">`
      (`NavBar.tsx:101-108`). The no-link-out rule is cited by three later plans as precedent.

**→ RESOLVED 2026-07-28 — confirmed BY-DECISION -- no /manual route; manual outputs live on the dashboard button row (buttonsForMode.ts).**

29. **No `/manual` route — a bookmarked Flask `/manual` URL will not resolve in React**
    - Source: "Design decisions → **D1**" (line 23) — "Consequence the user accepted".
    - Status: STILL-OPEN by decision — manual outputs live on the dashboard button row
      (`helpers/dashboard/buttonsForMode.ts:130-142`); `App.tsx` registers no `/manual`.

**→ RESOLVED 2026-07-28 — confirmed BY-DECISION, test-pinned -- allowManualOutputs deliberately unused (buttonsForMode.test.ts).**

30. **`allowManualOutputs` deliberately unused; manual toggles only in Manual mode**
    - Source: "Design decisions → **D3**" (line 25).
    - Deferred: Flask's `safety.allow_manual_changes` path (which `_cmd_set_manual` honours) is not
      surfaced; parity with legacy's narrower gate was chosen instead.
    - Status: STILL-OPEN by decision, pinned by a test. Recorded so nobody "fixes" it accidentally.
      Also: the Flask `manual` blueprint is still registered (`app.py:94`) and un-retired.

---

## plans/2026-07-21-react-qtquick-ui.md

### ALREADY-DONE

3. **Spike deferrals: food-probe column, right column, responsive breakpoints** (line 81) — all shipped
   (`web-react/src/components/dashboard/ProbeCard.tsx`, `SystemStatus.tsx`, `HopperGauge.tsx`;
   `dashboard.css` has `@media (max-width: 1279px|719px)` and `@media (min-width: 1280px)`).

4. **"Open question: can we run Node/Vite tooling here?"** (line 83) — answered; the app builds under
   bun + rsbuild (`web-react/package.json`, `rsbuild.config.ts`).

---

## plans/2026-07-22-dashboard-real.md

No deferred work recorded beyond the stated non-goal "no settings/feature pages" (Self-Review,
line 1101), which later plans delivered in full. Nothing open from this plan.

---

## plans/2026-07-22-settings-foundation.md

### STILL-OPEN

5. **Flask never serves the React app / no SPA catch-all for `/settings/*` deep links**
   - Source: "Self-Review notes → **Deferred (not this slice)**" (line 1038).
   - Deferred: production Flask must serve `index.html` for `/settings/*` (and now `/history`,
     `/pellets`, `/wizard`) so deep links resolve outside the dev server.
   - Status: STILL-OPEN, and larger than recorded — `app.py:89-154` registers no React blueprint and
     no `web-react/dist` static mount at all. The React app is reachable only via the rsbuild dev
     server / Playwright `page.goto`. Called out independently at
     `plans/2026-07-24-react-app-shell.md:13`.

---

## plans/2026-07-22-settings-2b1-scalar-tabs.md

### ALREADY-DONE

7. **`return null` tab stubs left by Task 2 Step 7** (line 202) — all five tabs implemented
   (`web-react/src/components/settings/tabs/{SafetyTab,PelletsTab,WorkModeTab,StartupTab,HistoryTab}.tsx`).

8. **Non-goals "colors / tables / controller form → 2b-2"** (Self-Review line 470) — delivered by
   `2026-07-22-settings-2b2-widgets.md` (`HistoryTab` chart colours, `RangeProfileTable.tsx`,
   `ControllerTab.tsx` all exist).

---

## plans/2026-07-22-settings-2b2-widgets.md

### ALREADY-DONE

10. **Deferred surfaces from 2b-1 (chart colours, range tables, Controller tab)** — this plan is the
    delivery; all three shipped (`HistoryTab.tsx` chart-colours section,
    `web-react/src/components/settings/RangeProfileTable.tsx`, `tabs/ControllerTab.tsx`,
    `GET /api/controller_metadata` in `blueprints/api/routes.py`).

---

## plans/2026-07-23-settings-schema-s1.md

### ALREADY-DONE

11. **Constraints (min/max, cross-field) deferred to S2** (Global Constraints line 17) — delivered:
    `common/settings_schema.py` carries `Field(ge=…, le=…)`, `PwmSettings._check_profiles`,
    `SmartStart._check_profile_count`, `SettingsSchema._check_startup_pwm_duty_cycle`.

12. **`test_lax_coercion_is_pinned` explicitly punts the strictness decision to S2** (Task 1 Step 5) —
    decided in S2; strict validation is live at `write_settings`.

13. **Migration-fixture parity "consciously skipped if no fixture exists"** (Task 2 Step 3, line 176) —
    done: `tests/unit/common/test_settings_schema.py` has a `_migration_env` fixture and
    `_migrate_ancient_settings` running the real `read_settings_file()` pipeline.

14. **`NotifyServices` modelled loosely "if dynamic" — implementer's verdict deferred** (line 162) —
    resolved: per-service models exist (`MqttService.port: str` etc., cited by the notifications plan).

---

## plans/2026-07-23-settings-schema-s2.md

### ALREADY-DONE

15. **"If a writer site is genuinely unreachable in tests, document why — an explicit skip beats a fake test"**
    (Task 4 Step 1, line 243)
    - Status: no such skip was needed. `tests/characterization/test_all_writers_strict.py` covers
      admin (8), socketio (5), api_commands, history, dash, wizard, api settings/settings_update,
      `save_settings_and_flag_update`, notifications/OneSignal, display, updater (2), `run_wizard`, and
      10 migration boundaries. Only the standard `[chromium]`-skips-in-agent-envs note appears (line 22).

16. **`create_partial_model(recursive=True)` "unproven combination" fallback** (Task 1 Step 3 note,
    line 127) — `PartialSettingsSchema` is in use by the `/api/settings_update` layer-1 check
    (referenced by `plans/2026-07-25-react-save-failure-surfacing.md:87`).

---

## plans/2026-07-23-notifications-tab.md

### STILL-OPEN

17. **WLED preset grids not editable in React**
    - Source: "Verification summary" non-goals (line 176); Global Constraints line 15 ("untouched
      services/keys must survive byte-identical (WLED preset subtrees…)").
    - Deferred: the WLED per-event preset matrix that Flask edits; React only round-trips it.
    - Status: STILL-OPEN — `web-react/src/components/settings/tabs/NotificationsTab.tsx:315-330` renders
      only `enabled`, `device_address`, `notify_duration` for WLED.

19. **Secret masking not ported** ("masking" non-goal, line 176)
    - Deferred: API keys / tokens render as plain `TextField`s in React.
    - Status: STILL-OPEN — every credential field in `NotificationsTab.tsx` (IFTTT `APIKey`, Pushover
      `APIKey`/`UserKeys`, InfluxDB `token`, MQTT `password`) is a plain text input.

### ALREADY-DONE

20. **"notify targets" non-goal** (line 176) — the *target-temperature* half shipped as
    `plans/2026-07-25-react-probe-notifications.md` Slice 1 (`ProbeNotifyModal.tsx`,
    `helpers/notify/`). The limit half is finding #31 below.

---

## plans/2026-07-24-react-small-batch.md

### ALREADY-DONE

23. **D2 — no display-config repair migration** (line 14) — deliberate won't-do; the shape bug
    (`c37b1036`) never shipped, so no install is affected. Closed.

24. **Stale `App.tsx:33-46` comment saying the first_time_setup gate is "follow-up work"**
    (Integration Step, line 588) — gate is live in
    `web-react/src/components/DashboardRoute.tsx` (`getSettings` → `navigate("/wizard")`).

---

## plans/2026-07-24-react-history-chart.md

### STILL-OPEN

26. **Legacy Flask chart may become unusable on long windows after the LTTB change**
    - Source: "### Legacy performance note (raised with the user, accepted)" (line 18).
    - Deferred: above the 10 000-sample threshold the Flask/Chart.js page now receives *more* points
      than before. Mitigation if observed: cap its budget via `select_indices(max_points=…)`, not
      revert LTTB. "Log it if observed; do not pre-optimise."
    - Status: STILL-OPEN as an unmonitored risk — `prepare_chartdata` is shared and the Flask history
      page still consumes it. Nobody has observed the legacy page on a long window.

---

## plans/2026-07-24-react-app-shell.md

### ALREADY-DONE

28. **Task 4c — the timer modal's shutdown/keep-warm flags were dropped by three sequential writes**
    - Source: "### Task 4c: FIX — the timer modal silently drops shutdown/keep-warm" (line 158).
    - Status: FIXED and superseded. `web-react/src/components/shell/TimerModal.tsx:68` calls
      `command.timerStartWithOptions(seconds, {…})` — one request, server-computed end, flags carried
      with the arm. `helpers/command.ts:30-60` documents the composed-intent drain
      (`common/control_delta.py`) and explicitly says TimerBar no longer needs its guard. The plan's
      prescribed fix (post the whole `notify_data` array) is stale and was NOT what shipped.

---

## plans/2026-07-24-react-manual-control.md

*All findings (#29, #30) resolved 2026-07-28 -- moved to the RESOLVED section above.*

---

## plans/2026-07-25-react-probe-notifications.md

### STILL-OPEN

31. **Slice 2 — per-probe High/Low Limit Temperature Alerts entirely unbuilt** *(the item that triggered this sweep)*
    - Source: "## Scope — this is genuinely two slices, and this plan is Slice 1 only" (line 21) and
      the accordion table (lines 25–29).
    - Deferred: the `probe_limit_high` (`condition: equal_above`) and `probe_limit_low`
      (`equal_below`) notify entries — a second pure reducer plus two accordion cards in
      `ProbeNotifyModal`, with per-type asymmetric controls (Shutdown + Attempt Re-ignite).
    - Status: **SHIPPED** (correction 2026-07-29). A concurrent session landed Slice 2:
      `08071c7e` (the reducer — `notifyState.ts` now exports `readLimitEdit`/`readNotifyEdit`/
      `limitEditFields`/`notifyEditUpdates`/`saveNotifyEdit`) and `21f2d977` (the three-section
      `ProbeNotifyModal`), wired in `Dashboard.tsx` (`readNotifyEdit` :369, `saveNotifyEdit` :142).
      Covered by `notifyState.test.ts`, `ProbeNotifyModal.test.tsx`, `Dashboard.test.tsx`,
      `deriveView.test.ts`, `notify.spec.ts`. The STILL-OPEN label was stale on the day it was written.

32. **Slice 2 groundwork: the client must pre-arm `triggered` or the alarm fires instantly**
    - Source: "## Slice 2 groundwork" bullet 2 (line 479).
    - Deferred: on save, set `triggered = current > target` (high) / `current < target` (low), as
      `dash_default.js:721-725, 763-767` does, so `notifications.py:112` stays quiet until the
      temperature leaves and re-enters the range.
    - Status: **SHIPPED** (correction 2026-07-29, with #31). `limitEditFields`
      (`notifyState.ts`) computes `triggered = currentTemp >= target` (high) / `<= target` (low)
      at save time, using the SAME comparison the backend fires on. Pinned by
      `notifyState.test.ts` ("pre-arms at exactly the limit…") and `notify.spec.ts`
      ("a high limit the probe has already passed is saved pre-armed"). It uses the modern
      `notify_updates` op, exactly as this note predicted.

33. **Suspected live Flask/backend bug: high/low-limit "Shutdown PiFire" appears never to fire**
    - Source: "## Slice 2 groundwork → Two live Flask bugs to decide about" item 2 (line 484) —
      explicitly marked "read from source, not executed".
    - Detail: `notifications.py` gates the shutdown action on
      `not control["notify_data"][index]["req"]`, but only `type == "probe"` entries ever clear `req`
      (at `:109`). A limit entry stays `req: true`, so the branch is unreachable. `reignite` is gated
      on `triggered` instead and does work.
    - Status: STILL-OPEN and CONFIRMED by reading live code — `notify/notifications.py:141-149` still
      has `item["shutdown"] and control["mode"] in (…) and not control["notify_data"][index]["req"]`,
      and nothing clears `req` for limit entries. Needs an empirical check, then either fix the
      backend or omit the control in Slice 2.

34. **Flask asymmetry to decide on before Slice 2**
    - Source: "## Slice 2 groundwork" bullet 4 (line 481).
    - Decision needed: high/low temperature sliders render for every probe, but "Shutdown PiFire"
      (`_macro_dash_default.html:238-244, 284-289`) and "Attempt Re-ignite" (`:290-293`) render only
      for Primary, with JS-enforced mutual exclusion (`:294-308`). Port the asymmetry or normalise it?
    - Status: **DECIDED & SHIPPED** (correction 2026-07-29). The asymmetry was *normalised*, not
      blindly ported: limit *temperatures* render for every probe, but the Shutdown/Re-ignite
      *actions* are Primary-only, with a rationale in `ProbeNotifyModal.tsx:158-168` ("only the
      primary probe measures the fire"). A food probe reading low is cold meat, not a dead fire.

35. **Notify targets are never converted on a temperature-units change**
    - Source: "### Units" (line 115) — "Pre-existing; out of scope; do not 'fix' it here."
    - Detail: `_cmd_set_units` converts *settings* and raises `units_change`;
      `controller.py:346-354` stops the grill and flushes history but never touches `notify_data`.
      A 203 °F target stays the number 203 after switching to °C.
    - Status: **FIXED 2026-07-29**. `_cmd_set_units` (`common/api_commands.py`) now converts every
      armed `control["notify_data"]` target via addressed `notify.set` ops, built by
      `common/common.py::notify_target_conversion_ops`. Gated on a REAL unit change (the command
      raises `units_change` even on a redundant same-unit write, and `convert_temp` assumes its
      input is in the OTHER unit), and skips the `target: 0` off-sentinel. Tests:
      `tests/unit/common/test_set_units_notify_conversion.py`. The units golden is untouched —
      default control carries no armed target, so the writer emits no extra delta.

36. **Accepted divergence: React's modal "Cancel" closes without writing; Flask's wipes the limits**
    - Source: Landmine 7 (line 157) and Task 2 Step 1 (line 280).
    - Detail: Flask's `cancelNotify` (`dash_default.js:803-831`) is a "Disable All" button wearing a
      Cancel label — it clears `req`/`shutdown`/`keep_warm`/`target` for *every* entry with that label,
      limits included. React deliberately does not port it.
    - Status: STILL-OPEN as a recorded divergence (deliberate). No React equivalent of "disable
      everything for this probe" exists.

37. **Accepted divergence: React refuses a target of 0; Flask allows it**
    - Source: Task 3 Step 1 (line 386) — "a deliberate, documented divergence".
    - Rationale: `condition` is `equal_above`, so a 0 target fires on the next control pass.
    - Status: STILL-OPEN as a recorded divergence — `ProbeNotifyModal.tsx` sets an `invalid` state
      rather than submitting.

### ALREADY-DONE

38. **Uncertainty 1 — round-trip latency of the GET→POST→socket-echo loop, never measured**
    (line 166) — measured: `helpers/notify/notifyApi.ts:22` records "Verified live (Stop mode,
    2026-07-25): a posted edit becomes visible on the next read after ~110 ms".

39. **Uncertainty 2 — whether `POST /api/control` with only `notify_data` is validated/rejected**
    (line 167) — moot; the write was rebuilt around `notify_updates` addressed patches
    (`notifyApi.ts:39-70`) and `web-react/tests/e2e/notify.spec.ts` exercises it.

40. **Uncertainty 3 — the `execute_control_writes` null-stripping ERROR on round-tripped `eta: null`**
    (line 168) — moot for the same reason: only the changed `fields` are posted, never the whole
    entry, so `eta: null` is not re-sent.

41. **Uncertainty 4 — primary-probe bell placement was a guess** (line 169) — placed and shipped
    (`ProbeNotifyModal.tsx` + `Dashboard.tsx` wiring; `ProbeCard.tsx` bell).

42. **"Landmine 1 also invalidates a step in the app-shell plan" (timer flags)** (line 162) — fixed;
    see #28.

43. **Flask bug 1: `dash_default.js` read `_low_limit_shutdown` twice, so `reignite` mirrored
    `shutdown`** (line 483) — FIXED in both front-ends:
    `blueprints/dash/static/default/js/dash_default.js:735-744` and
    `blueprints/dash/static/basic/js/dash_basic.js:499-508` now read `_low_limit_reignite`, with a
    comment naming the old bug.

---

## plans/2026-07-25-react-settings-guards-sweep.md

### STILL-OPEN

44. **M3 — the app loads Barlow from `fonts.googleapis.com` (external network dependency)**
    - Source: "### Orphans — flagged, not silently adopted" bullet 2 (line 322) — "Not adopted. Flag it
      to whoever owns Slice 7."
    - Second consequence, recorded in `2026-07-25-react-dashboard-slice.md:81`: it is why the fidelity
      screenshot cannot be a `toHaveScreenshot()` gate.
    - Status: **FIXED 2026-07-29**. Barlow is self-hosted via `@fontsource/barlow` +
      `@fontsource/barlow-semi-condensed` (same weights: 400/500/600/700 and 500/600/700/800),
      imported in `web-react/src/main.tsx` and bundled by rsbuild (`dist/static/font/*.woff2`); the
      `fonts.googleapis.com` `<link>`/preconnects are gone from `index.html`. Offline-safe, and the
      fidelity screenshot can now become a real gate.

45. **I13 — `display.sleep_timeout` is not rendered by React at all**
    - Source: Coordination table, "Slice 6" row (line 373) — "Out of scope — the field is not rendered
      by React at all yet".
    - Status: STILL-OPEN — `sleep_timeout` appears only in the generated types
      (`web-react/src/helpers/settings/settingsTypes.gen.ts:265`); no tab renders it.

46. **I18's other half — per-tab Save loses unsaved edits on navigation (Flask persists immediately)**
    - Source: "**The 'edits lost on tab switch' half does NOT hold as a table finding — dropped**"
      (line 264) — "a divergence from a decision that was made… If someone wants it revisited, that is
      a backlog item, not a task here."
    - Detail: Flask's SmartStart/PWM tables POST on every row change
      (`settings.js:76-92`, `:416-432`); React holds everything until Save, so navigating away discards.
    - Status: STILL-OPEN as an explicitly-backlogged revisit. Live code confirms the model
      (`PwmTab.tsx`, `StartupTab.tsx` seed `useState` from loader data).

47. **Accepted divergence: React allows removing any range-profile row; Flask only the last**
    - Source: Task 5 Step 3 (line 643) — "**Do not change the removal rule to Flask's 'last row only'**
      … note it and move on".
    - Status: STILL-OPEN as a recorded divergence
      (`web-react/src/components/settings/RangeProfileTable.tsx`).

48. **Missing CSS: `pf-section-note` and `pf-kv` render unstyled**
    - Source: "### Missing CSS classes (pre-existing…)" (line 326) — only `.pf-field-hint` was added;
      "Do not restyle the others; that is not this slice."
    - Status: STILL-OPEN. Same as #22 — cross-listed because this is where it is recorded.

### UNCLEAR

49. **Task 1's core premise was never observed in a browser**
    - Source: "## Self-Review → **Could not verify**" (line 797) — "the claim that React's `min`/`max`
      are advisory is derived from the absence of any `<form>` … strong but static reasoning… **if
      [Task 1's tests] show the attributes already blocking, stop and re-scope.**"
    - Status: UNCLEAR — `clampToBounds` + the blur clamp shipped
      (`web-react/src/helpers/settings/bounds.ts`, `fields/NumberField.tsx`), so the guard exists either
      way; whether the original premise held was never reported back.

### ALREADY-DONE

50. **The `augerontime` bound disagreement — blocked on a human decision, deliberately in no task**
    - Source: "### THE ONE DISAGREEMENT — `augerontime`, and why it is circular" (line 132); Task 6
      "Do NOT touch" (line 683); Parallelization "**Blocked item**" (line 764).
    - Status: **DECIDED — option (B) was taken.** `common/settings_schema.py:323` is now
      `augerontime: int = Field(ge=1, le=1000)` and
      `web-react/src/components/settings/tabs/StartupTab.tsx:16` is `min: 1, max: 1000`, matching
      Flask's shipped `index.html:944, 990`. The circular-provenance comment should be re-checked, but
      the decision is made.

51. **`history_page.minutes` / `datapoints` bounds handed to "Slice 5"** (Verified-facts table lines
    123-124; Coordination line 372) — delivered:
    `web-react/src/components/settings/tabs/HistoryTab.tsx:134-150` has `min={1}` on Minutes and
    `min={2}` on "Downsample above (samples)", each with a sourced comment (the field was also
    re-semanticised by the LTTB change, hence 2 rather than the planned 10).

52. **Controller `option['hidden']` rows and `option_min`/`option_max`** (lines 301-310) — dropped with
    evidence (zero `hidden: true` across all 9 controllers; bounds already passed). Closed.

53. **M15 delete confirmations** (Task 7) — shipped:
    `web-react/src/components/settings/fields/StringListField.tsx:14-62` holds a `pending` index and
    renders one `<ConfirmAction>` with a consequence `message`.

---

## plans/2026-07-25-react-save-failure-surfacing.md

### STILL-OPEN

54. **Deliberately not in scope: mapping a dotted error path to the offending widget**
    - Source: Design decision 3 (line 193) and Self-Review "Not in scope, deliberately" (line 418).
    - Deferred: highlighting the field named in `safety.maxtemp: Input should be…`. Rejected because a
      `model_validator` failure reports the *section*, so the mapping is not total.
    - Status: STILL-OPEN by decision — `SaveBar.tsx` renders the server string verbatim in a
      `role="alert"`.

### UNCLEAR

55. **"If a long dotted-path message crowds the `pf-settings-actions` row, that is a real finding —
    report it, do not silently add CSS"**
    - Source: Task 2 Step 3 closing note (line 332).
    - Status: UNCLEAR — needs one look in a browser. `.pf-settings-actions` is a flex row and the
      error `<p>` sits inline beside the Save button.

### ALREADY-DONE

56. **The UI-level e2e rejection witness became unreachable; the unit test was accepted instead**
    - Source: the "> **DECIDED 2026-07-25, after this plan shipped**" block (lines 30-51).
    - Detail: the guards sweep clamped exactly the value the witness raised, so `min >= max` no longer
      reaches the server. Coverage is now `PwmTab.test.tsx`'s "surfaces a rejected save inline and
      withholds the success marker" plus the API-level `settings.spec.ts` atomicity spec. This is a
      "test replaced by weaker coverage" event, but it is decided and reasoned, not an oversight.

---

## plans/2026-07-25-react-dashboard-slice.md

### STILL-OPEN

57. **Backend follow-up: the errors blob is write-only from the web tier and `_check_control_status`
    can false-positive**
    - Source: Task 2 Step 5 (line 575) — "**Record the backend follow-up** as a one-line backlog note";
      rationale in "### The `controlAlive` mechanism" (line 210) and Task 2's "The backend half — an
      endpoint that clears the error, or a non-sticky liveness signal — is **out of scope**".
    - Detail: `read_errors()` is a non-destructive blob read; the only clearer, `flush_errors()`, runs
      once at `control.py:107-109` boot. Any of seven consumers of the shared `queue_systemo` can eat
      the `check_alive` reply (`common/app.py:31-44`), writing the sticky error on a healthy system.
    - Status: **CLOSED 2026-07-26** — fixed as a non-sticky liveness signal rather than a clearing
      endpoint, because every other writer of that blob is the control process recording a durable
      past failure; liveness was simply misfiled. `socket_io._control_alive` holds the verdict in
      memory and `_get_dash_data` composes `common/app.py::CONTROL_DOWN_ERROR` into each payload, so
      the web tier no longer writes the blob at all. The `queue_systemo` false-positive vector was
      already gone (that function stopped discarding non-matching entries in `33135e4aed48`), so this
      entry's second bullet was stale. `recheckControl` is kept — the payload can still be one 30 s
      poll interval stale. Pinned by `tests/web/test_control_liveness_not_sticky.py`.

58. **Recipe unpause payload not ported — needs verification against a live recipe**
    - Source: Task 5 Step 1 (line 664) — "**The unpause payload … is deliberately NOT ported here** …
      Record it; do not build it blind"; repeated under "Things I could not verify" (line 964):
      "Somebody must verify that reasoning against a live recipe before Slice 2 of the recipe work."
    - Detail: Flask's `#recipe_group` unpause posts `{recipe:{step_data:{…pause:false}}}`
      (`control_panel.js:382-392`), which rewrites the whole `step_data` object through an
      array-replacing `json_patch` merge.
    - Status: **FIXED 2026-07-29**. `command.ts` gains `recipeUnpause`, which posts the minimal
      `{ recipe: { step_data: { pause: false } } }` — the scalar leaf only, not Flask's whole-step_data
      repost (RFC-7396 merges it in place, so a controller-set `triggered`/`notify` in the same cycle
      is not reverted). `buttonsForMode.ts` branches the "Next Step" button exactly as Flask's does
      (`control_panel.js:520-533`): paused → `recipeUnpause`, otherwise → `recipeNextStep`. The
      controller advances the step once `pause` is false (`base.py::_handle_recipe_end`). Tests in
      `command.test.ts` and `buttonsForMode.test.ts`.

59. **M8 — the hopper "Manager" link to `/pellets` deliberately dropped**
    - Source: the findings table, M8 row (line 137) and Task 10 Step 1 (line 760) — "**Assert there is
      no link to `/pellets`**".
    - Status: PARTLY SUPERSEDED, still open as a UX gap. `/pellets` now exists in React
      (`components/pellets/PelletsPage.tsx`, route at `App.tsx:64`, nav item at `NavBar.tsx:20`), but
      the dashboard hopper card still has no shortcut to it — the pellets plan explicitly left that to
      this slice ("noted, not built", `2026-07-25-react-pellets-page.md:328`) and this slice's
      assertion forbids it. **Somebody must lift the ban now that the page exists.**

60. **`hidden_cards` / `touch_screen_mode` / dashboard picker / the `Basic` dashboard will never be ported**
    - Source: ratified decision **M5** (line 21) and "### What M5 removes from this slice" (line 24).
    - Status: STILL-OPEN as a recorded won't-do. Flask still ships both dashboards
      (`blueprints/dash/templates/{default,basic}/`) and `_get_probe_max_temp` already hardcodes
      `Default` (`socket_io.py:892-904`). Worth a backlog line so the settings that drive them get
      retired eventually.

### UNCLEAR

61. **How the reflowed layout actually looks — every breakpoint value is a guess**
    - Source: "## Things I could not verify" bullet 1 (line 961) — "**Task 13 Step 5 is a mandatory
      human checkpoint**, not a formality".
    - Status: UNCLEAR — the reflow shipped (`dashboard.css` has `@media (max-width: 1279px)`,
      `(max-width: 719px)`, `(min-width: 1280px)`), and note the **Task 13 fallback was taken**:
      `helpers/dashboard/hooks.ts` still exports `useFitScale`/`FIT_QUERY` gated to
      `(min-width: 1280px)` rather than deleting it as Task 13 Step 3 specified. Whether a human ever
      approved the visual result is not recorded.

### ALREADY-DONE

62. **Whether the demo fixture has `hasDistanceSensor: true`** (line 963)
    - Status: RESOLVED — `web-react/src/helpers/demoData.ts:30` is `hasDistanceSensor: true`;
      `fixture.ts:36` is `false`. Gate is live at `Dashboard.tsx:320`.

63. **M8 hopper "Refresh Status" button — planned in Task 10, then deliberately NOT built**
    - Status: REVERSED by a later decision, and the reversal is documented in the code:
      `components/dashboard/HopperGauge.tsx:7-11` ("There is deliberately no 'Refresh Status' button
      here … the control loop re-reads the level every ~10 s and the socket pushes hopperLevel with
      every frame") and `helpers/command.ts:201-206` ("No hopperCheck here on purpose"). A Refresh
      Status button does exist on the new pellets page
      (`components/pellets/CurrentLoadCard.tsx:102`). Plan text is stale.

64. **`backlogs/react-migration-backlog.md` does not exist** (line 965) — it does now:
    `docs/superpowers/backlogs/react-migration-backlog.md` (27 KB, modified today).

65. **M7 probe ETA "dropped — owned elsewhere"** (line 136) — delivered by the probe-notifications
    plan; `deriveView.ts` carries `etaStr` and `ProbeCard.tsx` renders it.

66. **I1/I2/I3/I4/D1/C3/M6 behaviour findings** — all shipped:
    `buttonsForMode.ts` has the six-item Prime menu (`:48-55`), the `startup` confirm action
    (`:81`), the recipe branch with `Next Step` (`:101`), and Manual (`:130-142`);
    `helpers/dashboard/{cookTime,countdowns,probeStatus,controlHealth}.ts` all exist;
    `deriveView.ts:213` computes `pModeEditable` and `Dashboard.tsx:311` makes the pill clickable.

---

## plans/2026-07-25-react-pellets-page.md

### STILL-OPEN

67. **`backup_pellet_db(action="backup")` is not performed on a React "Load New Pellets"**
    - Source: "## Out of scope, deliberately" bullet 1 (line 1514) — "**Record this in the backlog when
      the page ships** — it is a real, if small, behaviour difference between the two UIs."
    - Detail: Flask's `blueprints/pellets/routes.py:40` snapshots the pellet DB on the load-profile path
      only. Porting it means porting `common/backups.py`'s file surface, which belongs with the admin
      page.
    - Status: **FIXED 2026-07-29**. `common/pellets_actions.py::pellets_load_profile` now calls
      `backup_pellet_db(action="backup")` after `write_pellet_db`, mirroring Flask's ordering, so the
      React `POST /api/pellets` `load_profile` path leaves a restore point. Test:
      `tests/unit/common/test_pellets_load_profile_backup.py`.

68. **Residual clobber window on the pellet blob (no optimistic concurrency)**
    - Source: Hazard 1 "Residual, honestly stated" (line 261) and "Out of scope, deliberately" bullet 4
      (line 1526).
    - Detail: a user POST and a controller hopper check landing in the same millisecond can still lose
      one; closing it needs a `lastupdated.time` compare-and-swap in the datastore. Pre-existing and
      identical in Flask.
    - Status: STILL-OPEN by decision — datastore-wide, not a page concern.

69. **`clear_pelletdb` still shells out to `os.system("rm pelletdb.json")` against a retired file**
    - Source: "## Out of scope, deliberately" bullet 5 (line 1528) — "That is an admin-page finding; do
      not 'fix' it here."
    - Status: **FIXED 2026-07-26**, same day this inventory was written. Both transports now call
      `common/pellets_actions.clear_pellet_db()`, which reseeds the blob in SQLite; the `rm` is gone
      from `blueprints/admin/routes.py` and `blueprints/mobile/socket_io.py`, and the three tests
      that pinned the `os.system` call were flipped to assert the store is really cleared. It was
      worse than "a live `os.system` on a retired path": the handler logged success and did nothing
      at all.

70. **No dashboard hopper-card shortcut to `/pellets`**
    - Source: Design decision 4 (line 328) — "A hopper-card shortcut can be added by the reflow plan
      later; it is noted, not built"; and "Out of scope" bullet 3 (line 1524).
    - Status: STILL-OPEN, but UNBLOCKED — the human ruled 2026-07-26 that the link exists, because it
      exists in Bootstrap. Recorded as backlog item 6a. Was blocked by the dashboard slice's "assert there is no link
      to `/pellets`" (finding #59). Same item from the other side; needs one ruling.

71. **The Flask `blueprints/pellets/` page is still live — nothing has been deleted**
    - Source: "## Out of scope, deliberately" bullet 2 (line 1520) — "No page has been deleted on this
      migration yet and this is not the plan that starts."
    - Status: STILL-OPEN, and it generalises: `app.py:89-154` still registers every legacy blueprint
      (`manual`, `pellets`, `dash`, `settings`, `history`, `recipes`, `admin`, `events`, `cookfile`, …).
      There is no retirement plan for any ported page.

72. **Accepted divergence: duplicate brand/wood rejection is client-side in React, server-side in Flask**
    - Source: Design decision 3 (line 315) — "Recorded as a deliberate divergence in behaviour
      *location*, not in behaviour."
    - Detail: the shared socketio handler is a silent no-op on duplicates
      (`socket_io.py:563-568`); Flask's route errors (`routes.py:62-63`). React pre-checks against the
      live `brands` array and shows the Flask wording without a round trip. A non-React client hitting
      `POST /api/pellets` still gets the silent no-op.
    - Status: STILL-OPEN as a recorded divergence.

73. **Accepted divergence: estimated-usage trailing zero (`"2.0"` in Python vs `"2"` in JS)**
    - Source: Design decision 5 (line 330).
    - Status: STILL-OPEN as a recorded, test-pinned divergence
      (`web-react/src/helpers/pellets/usage.ts`).

### UNCLEAR

74. **"Could NOT verify" — nothing on the pellets page was opened in a browser**
    - Source: "## Could NOT verify" bullets 1, 2 and 5 (lines 1532-1554).
    - Detail: (a) the 1280×720 no-page-scroll fit is derived from `.pf-shell-main`'s box model, not
      observed — "if the page scrolls, fix the grid, do not relax the assertion"; (b)
      `socket_pellet_data` was never observed arriving in a browser — "verifying via `GET /api/pellets`
      is **not** verification of the socket path"; (c) Flask's `est_usage` string was never compared
      side-by-side against `formatUsage` on real data.
    - Status: UNCLEAR — `web-react/tests/e2e/pellets.spec.ts` exists, but agent worktrees skip
      `[chromium]`, so whether it has ever actually executed is not recorded here.

75. **Whether `control.notify_data` is non-empty on the test rig, which Task 3's clobber test needs**
    - Source: "## Could NOT verify" bullet 4 (line 1546) — "a rig with no probes configured will skip
      rather than prove the fix."
    - Status: UNCLEAR — the fix (minimal `{"hopper_check": True}` patch instead of a whole-control
      MERGE) is in `common/pellets_actions.py`; whether its clobber test actually asserted anything is
      not verifiable from the plan.

---

## Cross-cutting items worth one backlog line each

Reconciled 2026-07-28 — several of these closed; see the RESOLVED section above.

- **Nothing is served by Flask** (#5) — the React app has no production entry
  point at all. **Still open** (its own slice).
- ~~**Three nav items disabled** (#27) plus **cookfile** (#25)~~ — both SHIPPED;
  the nav entries are real links and the cook-file surfaces exist. **manual**
  (#29) is a confirmed by-decision won't-do — outputs live on the dashboard
  button row, not a `/manual` route.
- **No legacy page has been retired** (#71) — still true; every legacy blueprint
  stays live until the single retirement pass (backlog ruling 5).
- ~~**Two plans contradict each other on the hopper→pellets shortcut** (#59 vs
  #70)~~ — RESOLVED: the link exists, matching Bootstrap (backlog item 6a).
