# Deferred-work inventory — wizard plans, React/UI specs, audits

Scope: the 7 wizard plans, 14 React/UI specs, and 2 audits assigned. Every item below is
something the source document says it will NOT do. Status verified against live code in
`/home/dannyb/sources/PiFire` on 2026-07-26 (paths absolute where cited).

**Totals: 150 findings — 64 STILL-OPEN, 84 ALREADY-DONE, 2 UNCLEAR-NEEDS-HUMAN.**

Per document (open / done):
divergence audit 28/30 · triage 7/10 · wizard-critical-fixes 3/4 · wizard-styling 3/4 ·
optional-reboot 2/2 · module-config-display-first 0/5 · probes-config plan 2/3 ·
grillplatform plan 0/5 · display-distance plan 0/4 · dashboard-real spec 3/1 ·
settings-foundation 1/2 · settings-2b1 0/3 · settings-2b2 1/2 · toolchain 0/1 ·
schema-scoping 3/0 · schema-S1 0/3 · schema-S2 3/3 · notifications-tab 3/2 · tailwind-v4 5/0.
(The 2 UNCLEAR items are counted inside the divergence-audit and triage open columns.)

Legend: **[O]** STILL-OPEN · **[D]** ALREADY-DONE · **[?]** UNCLEAR-NEEDS-HUMAN

---

# audits/2026-07-25-react-vs-flask-ui-divergences.md

45 graded findings (C0–C8, I1–I18, M1–M18) + 9 disclosed-but-unanswered rows + 5 coverage
caveats. Enumerated individually below.

## STILL-OPEN

**[O] I7 — "Send Test Notification" and all three WLED action buttons never ported**
Source: `# IMPORTANT / I7`. Flask ships Test Notification (`GET /api/set/notify/Test/req/true`),
WLED **Find Devices** (mDNS discovery), **Push Profiles to WLED**, **Test Profile**. Without
discovery there is no way to learn a WLED device's address. Backend already has
`notify/wled_profiles.py` + `/api/wled_push_profiles`.
Checked: `/home/dannyb/sources/PiFire/web-react/src/components/settings/tabs/NotificationsTab.tsx:315-330`
— WLED section is three plain fields (enabled / device_address / notify_duration); zero
"Test"/"Discover"/"Push" controls anywhere in the tab.

**[O] I8 — History page-level Stream toggle still absent; 5 s poll vs Flask's ~1 s; socket append never built**
Source: `# IMPORTANT / I8`. Flask has a Stream ON/OFF button on the page
(`history/index.html:41-45`, default "on"); its removal was justified as "a UI addition
nobody asked for", which is false. The plan's own architecture sentence promised a socket
append; polling was substituted silently.
Checked: `/home/dannyb/sources/PiFire/web-react/src/components/history/HistoryPage.tsx:25`
(`REFRESH_MS = 5000`), `:98-110` (autorefresh read once from settings on mount), `:163-181`
(actions row is Export CSV + Reset zoom only — no Stream toggle). No socket import in the file.

**[O] I9 — Chart annotations fetched but never drawn; disabled probes silently dropped**
Source: `# IMPORTANT / I9` (re-graded from MINOR).
Checked: `/home/dannyb/sources/PiFire/web-react/src/helpers/history/historyApi.ts:61` still the
only `annotations` reference in non-test source;
`/home/dannyb/sources/PiFire/web-react/src/components/history/historyAdapter.ts:59-60` filters
`ds.hidden` out with a comment saying HistoryChart has no per-series visibility toggle.

**[O] I10a — Wizard Finish has no confirmation summary**
Source: `# IMPORTANT / I10` bullet 1. Flask's finish tab lists the four selected modules, live
updated (`wizard.html:249-274`). Disclosed in `wizard-family-inventory.md` §7, never built.
Checked: `/home/dannyb/sources/PiFire/web-react/src/components/wizard/WizardShell.tsx:148` —
still literally "Review your selections, then finish setup…" with nothing rendered to review.

**[O] I10b — Install output (`status.output`) never rendered; a failed install leaves the user blind**
Source: `# IMPORTANT / I10` bullet 2 (also re-raised at
`plans/2026-07-25-react-wizard-styling.md:108` — "Not built… Record as backlog"). Flask has a
"Show Output" toggle + scrolling textarea (`wizard-finish.html:22-35`).
Checked: `/home/dannyb/sources/PiFire/web-react/src/components/wizard/InstallProgress.tsx:25` —
`output: ""` is an initial-state field and the only occurrence; nothing renders it.

**[O] I10c — Finish error detail thrown away (422 `detail`, 400 `sections`)**
Source: `# IMPORTANT / I10` bullet 3. The user is not told *which* I2C bus conflicts or *which*
section is unset.
Checked: `/home/dannyb/sources/PiFire/web-react/src/components/wizard/WizardShell.tsx:39-48`
`finishErrorMessage()` switches on status only and emits generic sentences; `FinishResult`
(`:24-28`) carries `{ok, status, message}` — no `detail`/`sections` field exists to carry it.

**[O] I11 — "System is active" warning still only at the last step, not on wizard entry**
Source: `# IMPORTANT / I11`. Flask warns on page entry (`wizard.html:320-339`); React lets you
configure everything then disables Finish.
Checked: `/home/dannyb/sources/PiFire/web-react/src/components/wizard/WizardShell.tsx:74`
(`canFinish = state.control_mode === "Stop"`) is consumed only inside `renderFinishStep()`
(`:149-153`); the welcome step (`:184-194`) shows no warning.

**[O] I13 — `display.sleep_timeout` (Screen Power Save, "0 = never") has no React control**
Source: `# IMPORTANT / I13`. The only web control for blanking the attached display.
Note the triage says to coordinate with the Qt display DPMS/sway work and that General is the
likely home (PlatformTab is read-only by decision).
Checked: zero hits for `sleep_timeout` across `/home/dannyb/sources/PiFire/web-react/src`
outside `settingsTypes.gen.ts`.

**[O] I14 — Controller "use recommended value" buttons + controller metadata card dropped**
Source: `# IMPORTANT / I14`. Flask offers one-click recommended Cycle Time / Min Cycle Ratio /
Max Cycle Ratio per controller (`metadata[sel].recommendations.cycle`), plus image, author,
homepage, contributors, attributions.
Checked: zero hits for `recommendation` in `/home/dannyb/sources/PiFire/web-react/src`;
`/home/dannyb/sources/PiFire/web-react/src/components/settings/tabs/ControllerTab.tsx` renders
`description` and the config options only.

**[O] I17 — `global_control_panel` ("Show Control Panel on Most Pages") neither read nor offered**
Source: `# IMPORTANT / I17`. In Flask the full control panel is fixed-bottom chrome on every
page; in React `/settings`, `/history`, `/pellets` and `/wizard` have no way to stop the grill.
Triage Slice 7 says it needs its own item and its own decision (honour the setting, or always
show the panel outside the dashboard).
Checked: zero hits for `global_control_panel` in `web-react/src`;
`/home/dannyb/sources/PiFire/web-react/src/components/dashboard/ControlButtons.tsx` is imported
only by `Dashboard.tsx:13`. `AppShell.tsx:31-49` mounts NavBar + TimerBar + Banners only.

**[?] I18b — SmartStart/PWM table edits are lost on tab switch; which model wins is undecided**
Source: `# IMPORTANT / I18` (second half) + triage Slice 4. Flask persists every add/edit/delete
immediately; React defers everything to the tab's Save. The monotonic-range half is fixed.
Checked: `/home/dannyb/sources/PiFire/web-react/src/components/settings/RangeProfileTable.tsx:21`
now enforces "the strictly-monotonic window a single boundary may occupy", but no comment or
doc records a decision on the deferred-vs-immediate persistence model. **Needs a human call.**

**[O] M1 — No page title, no favicon, no PWA manifest**
Source: `# MINOR / M1`. Flask sets `<title>{page} | {grill_name}</title>`, a favicon and a
`<link rel="manifest">` (installable to a home screen).
Checked: `/home/dannyb/sources/PiFire/web-react/index.html:6` is still the hardcoded
`PiFire · React UI (POC)`; no favicon or manifest link in the file.

**[O] M2 — `globals.page_theme` is settable but inert**
Source: `# MINOR / M2`. Triage calls it "highest-value item in Slice 7 and the cheapest".
Checked: `/home/dannyb/sources/PiFire/web-react/src/components/settings/tabs/GeneralTab.tsx:20,26,31`
read and write it; `/home/dannyb/sources/PiFire/web-react/src/components/AppPrefs.tsx:15-17`
still handles `data-accent` only. Nothing else in `web-react/src` reads `page_theme`.

**[O] M3 — External Google-Fonts dependency; offline PiFire silently falls back**
Source: `# MINOR / M3`. Flask self-hosts fonts. Also named at
`plans/2026-07-25-react-wizard-styling.md:178` ("rendering with Barlow unavailable… offline
first-boot is a plausible real scenario for this exact screen and is **not** covered") and it
is the stated reason the Tailwind spec forbids a pixel screenshot gate.
Checked: `/home/dannyb/sources/PiFire/web-react/index.html:7-12` still preconnects and loads
Barlow from `fonts.googleapis.com`.

**[O] M4 — Updater release-notes modal (`settings.globals.updated_message`) dropped**
Source: `# MINOR / M4`. Cross-cutting `base.html` chrome; fires only after an update.
Checked: zero hits for `updated_message` in `/home/dannyb/sources/PiFire/web-react/src`.

**[O] M5 — No dashboard selection, no card hide/show, `Basic` dashboard dropped wholesale**
Source: `# MINOR / M5` + triage Slice 1 (a *decision*, not code). Flask has a Dashboard Settings
modal persisting `dashboard.dashboards.Default.custom.hidden_cards`, a `touch_screen_mode`
branch, and a settings Dashboard tab selecting Default vs Basic. `Basic`'s click-to-toggle
manual outputs must be accounted for if React ships one dashboard forever.
Checked: zero hits for `hidden_cards` or `touch_screen_mode` in `web-react/src`; no Dashboard
settings tab in `/home/dannyb/sources/PiFire/web-react/src/components/App.tsx:74-87` (11 tabs).

**[O] M8b — Hopper "Manager" link (to the pellet manager) still not on the hopper card**
Source: `# MINOR / M8`. The Refresh half is resolved by decision (see ALREADY-DONE below).
Checked: `/home/dannyb/sources/PiFire/web-react/src/components/shell/NavBar.tsx:11-15` — a
`/pellets` nav entry exists *instead*, with an explicit note that "a hopper-card shortcut
belongs to the dashboard-reflow plan, not here".

**[O] M9a — History duration control is a bare number input, not Flask's 1–480 slider**
Source: `# MINOR / M9`.
Checked: `/home/dannyb/sources/PiFire/web-react/src/components/history/HistoryPage.tsx:156-162`
— `NumberField` with `min={1}` and no `max`.

**[O] M9b — History page Metrics link dropped**
Source: `# MINOR / M9` (`history/index.html:47`). Checked: no metrics link in `HistoryPage.tsx`;
`/metrics` is an un-migrated Flask page.

**[O] M9d — Per-probe background fill colours configurable in HistoryTab but ignored by the chart**
Source: `# MINOR / M9`, called out as "the real bug in this group" — a setting that saves and
does nothing.
Checked: `/home/dannyb/sources/PiFire/web-react/src/components/history/historyAdapter.ts:63`
still uses `ds.borderColor` only; no `backgroundColor`/`fill` handling.

**[O] M10 — Per-setting Description dropped for the two remaining widget types**
Source: `# MINOR / M10`. Partially fixed: `I2cBusPicker.tsx:74` and `UsbSerialPicker.tsx:62` now
render `dep.description`. Still missing:
`/home/dannyb/sources/PiFire/web-react/src/components/wizard/fields/SelectField.tsx:6-12` has no
description prop at all, and `components/wizard/ConfigOptionField.tsx` still ignores
`option_description` (declared at `helpers/wizard/wizardTypes.ts:17`).

**[O] M11 — Per-step explanatory copy dropped throughout the wizard**
Source: `# MINOR / M11`. e.g. "Select 'Custom' if you are using a custom build… prototype only
for testing/debug", "A display is not required… Select None", "A hopper level sensor is
optional", Temp Units "can be modified in settings later".
Checked: `steps/DisplayStep.tsx:37-38`, `steps/DistanceStep.tsx:31-32`,
`steps/GrillPlatformStep.tsx:49-50`, `steps/ProbesStep.tsx:17` — each renders an `<h2>`, an
error slot, and the widget. Only the welcome step (`WizardShell.tsx:188-192`) has prose.

**[O] M12 — Wizard navigation is still strictly Back/Next; step indicators are inert**
Source: `# MINOR / M12`. Flask's left nav pills jump to any tab at any time.
Checked: `/home/dannyb/sources/PiFire/web-react/src/components/wizard/WizardShell.tsx:230-234` —
`<span className="pf-wizard-step-indicator">`, no handler.

**[O] M13 — Wizard finish expectation-setting copy dropped**
Source: `# MINOR / M13`. Flask: "may take several minutes… will restart the PiFire server
software… you can relaunch this wizard from the admin menu".
Checked: `WizardShell.tsx:148` carries one sentence; none of that copy is present.

**[O] M14 — Wizard tables have no column headers; device "Type" shows friendly_name not module id**
Source: `# MINOR / M14`.
Checked: `/home/dannyb/sources/PiFire/web-react/src/components/wizard/probes/DevicesCard.tsx:115-142`
and `probes/PortsCard.tsx:85-108` — `<table>` with `<tr>/<td>` and no `<thead>`/`<th>` anywhere.
`DevicesCard.tsx:130` renders `modules[d.module]?.friendly_name ?? d.module`.

**[O] M17 — Three distinct Flask dashboard error modals flattened; `ui_hash` reload prompt has no counterpart**
Source: `# MINOR / M17`. The audit itself calls it "mostly equivalent" and the triage says
"record the divergence and close it" unless `ui_hash` turns out to matter — i.e. this is a
disposition to record, not necessarily code.
Checked: zero hits for `ui_hash` in `/home/dannyb/sources/PiFire/web-react/src`.

**[O] M18 — Discovery results lost Refresh/Close controls**
Source: `# MINOR / M18`. Audit notes re-clicking Discover is equivalent.
Checked: `/home/dannyb/sources/PiFire/web-react/src/components/wizard/DiscoveryPanel.tsx` — 
error/empty/rows rendering only, no re-scan or dismiss button.

**[O] COV-1 — The `Basic` dashboard (`dash/templates/basic/*`, 795 lines of `dash_basic.js`) has never been compared**
Source: `# Coverage — Not covered / weaker`. Nobody has enumerated its unique affordances
(notably click-to-toggle manual outputs). Blocks a proper M5 decision.
Checked: no React dashboard-selection surface exists (see M5).

**[O] COV-2 — `probeconfig.js` submit/reload plumbing (lines 154-306) and `probeReducer.ts` validation semantics not line-by-line verified**
Source: `# Coverage — Not covered / weaker`. Naming, labels, virtual probes, reposition. Belongs
to the separately-tracked `/probeconfig` surface.
Checked: `/home/dannyb/sources/PiFire/web-react/src/helpers/wizard/probeReducer.ts` exists and is
tested, but no cross-check against `probeconfig.js` was ever recorded.

## ALREADY-DONE

**[D] C0 — NavBar/TimerBar mounted nowhere.** Fixed: `web-react/src/components/App.tsx:54-56`
now has a pathless `AppShell` layout route wrapping `/`, `/history`, `/pellets`, `/settings`;
`components/shell/AppShell.tsx:33-44` mounts NavBar + TimerBar + Banners. (`/wizard` is
deliberately outside it — documented at `App.tsx:91-94`.)

**[D] C1 — Per-probe notifications / temperature alerts / safety actions.** Shipped:
`components/dashboard/NotifyBell.tsx`, `components/dashboard/ProbeNotifyModal.tsx`,
`helpers/notify/notifyApi.ts`, `helpers/notify/notifyState.ts`, e2e at `tests/e2e/notify.spec.ts`.

**[D] C2 — Silent settings save failures.** Fixed: `helpers/settings/useSaveSettings.ts:7`
returns a `SaveStatus` union with `{kind:"error", message}` and `normalizeSaveError()` at `:19`;
`components/settings/SaveBar.tsx` renders it.

**[D] C3 — Cook timer derived from client mount.** Fixed: `helpers/dashboard/cookTime.ts:20`
`cookElapsed(startupTimestamp, nowSeconds)` with the `=== 0` inactive branch, used at
`components/dashboard/Dashboard.tsx:90`.

**[D] C4 — Wizard has no exit and lies about it.** Fixed: `POST /api/wizard/cancel`
(`blueprints/api_wizard/routes.py:189`), `WizardShell.tsx:96-121` `handleExit()` (save draft →
cancel → navigate), Exit Setup button at `:242-251`, honest copy at `:188-192`.

**[D] C5 / I12 — I2C bus + USB serial pickers were empty/truncated selects.** Fixed: both are
now free-text + Discover — `components/wizard/fields/I2cBusPicker.tsx`,
`fields/UsbSerialPicker.tsx`, with the manifest `default` carried through
`probes/DeviceConfigField.tsx:50-56`.

**[D] C6 — Vendor photos 404.** Fixed: `helpers/wizard/wizardAssets.ts` builds the URL against
the PiFire origin; `rsbuild.config.ts:36` adds a scoped `/static/img` dev proxy.

**[D] C7 — Wizard destructive deletes had no confirm.** Fixed: `ConfirmAction` gained an
optional `message`; used in `probes/DevicesCard.tsx` and `probes/PortsCard.tsx`; e2e asserts the
two-click flow at `tests/e2e/wizard.spec.ts:70-80`.

**[D] C8 — Fixed 1280×720 scaled stage.** Decided *and* implemented: the dashboard reflows below
1280px and keeps scale-to-fit above it (commits `a4821358`, `db374b73`), with
`tests/e2e/dashboard-reflow.spec.ts` at 390×844 and `dashboard-panel.spec.ts` at 800×480.
Note the triage's follow-on chore — correcting the backlog's "mobile — may be obsolete if React
is responsive" line and deciding the `/mobile` blueprint's fate — is backlog-owned, not code.

**[D] I1 — P-Mode pill is now a control** (`92e8fb3b`, in the modes Flask allows it).
**[D] I2 — Six-way Prime amount menu restored** (`helpers/dashboard/buttonsForMode.ts:41-62`).
**[D] I3 — Startup safety-check confirmation restored** (`buttonsForMode.ts:80`, gating on
`startupCheck || (startToHoldPrompt && startupGotoMode === "Hold")`).
**[D] I4 — Mode/lid-open countdowns and a dedicated Recipe branch** (`buttonsForMode.ts:88-105`,
`helpers/dashboard/countdowns.ts`).
**[D] I5 — `platform.dc_fan` gating** across PWM tab / Smoke-Plus ramp / startup duty cycle
(`helpers/settings/platform.ts`, e2e `e93bcf3d`).
**[D] I6 — PWM min/max guard + dependent-value clamp** (`762ec435`).
**[D] I15 — Startup tab conditional structure + bounds** (`6ac24dea`;
`components/settings/tabs/StartupTab.tsx:201,211,264`).
**[D] I16 — Prime-ignition DANGER copy restored verbatim**
(`components/settings/tabs/PelletsTab.tsx:128-131`).
**[D] I18a — Monotonic range enforcement** (`RangeProfileTable.tsx:21`).
**[D] M6 — Probe connected/battery badges** (`5d05756e`; `helpers/dashboard/probeStatus.ts`).
**[D] M7 — Probe ETA** (`helpers/dashboard/deriveView.ts:141` → `ProbeCard.tsx:66`).
**[D] M8a — Hopper "Refresh Status"** — *resolved by root-cause fix, not port*: the control loop
now refreshes the hopper non-blockingly and the socket pushes it, so the button was deliberately
removed. Rationale recorded in `components/dashboard/HopperGauge.tsx:7-11`.
**[D] M9c — "Grill Inactive" empty state** — equivalent shipped at `HistoryPage.tsx:190-192`
("No history yet — start a cook to see the chart").
**[D] M15 — Delete confirmations in settings** (OneSignal `NotificationsTab.tsx:336`, Apprise
`StringListField.tsx:56`).
**[D] M16 — Field bounds sweep** (`a6f64867`). The SmartStart auger-on disagreement was resolved
in the schema's favour: `common/settings_schema.py:323` `augerontime: Field(ge=1, le=1000)` and
`StartupTab.tsx:16` `max: 1000` now agree.
**[D] D1 — `controlAlive` can stick false** (disclosed-but-unanswered table). Fixed by
`ff822125`; `helpers/dashboard/controlHealth.ts`.
**[D] Timer stopwatch toggle** (disclosed table) — already marked FIXED in the audit.
**[D] COV-3 — "Nothing was executed"** — a real Playwright suite now runs in Chromium:
`web-react/tests/e2e/{roundtrip,settings,history,notify,pellets,wizard,wizard-layout,dashboard-fidelity,dashboard-reflow,dashboard-panel}.spec.ts`.
**[D] COV-4 — Shell components' *contents* never diffed against `base.html`/`_macro_timer.html`.**
Now covered: `components/shell/TimerModal.tsx:6` documents the port (hours 0-23, minutes 0-59),
`:11-16` implements Shutdown-Grill / Start-Keep-Warm as mutually-exclusive radio options.
**[D] Disclosed table: "/history unreachable from the UI"** — resolved by the shell (`App.tsx:60`
+ NavBar `/history` entry).
**[D] Disclosed table: controller `numlist` option type** — accepted non-issue by recorded
decision (`specs/2026-07-22-settings-2b2-widgets-design.md:37-38`); zero controllers declare it.

---

# audits/2026-07-25-audit-triage.md

~46 findings across 10 slices. The C/I/M findings are dispositioned above; listed here are the
triage's **own** contributions — slice-level decisions, its corrections, its caveats, and the two
slices (9, 10) whose contents exist nowhere else.

## STILL-OPEN

**[O] T-S1a — Slice 1 decision: does React ship one dashboard forever? (M5)**
Source: `## Slice 1 — Decisions only, no code`, M5 row. "Decisions only, no code… blocking, not
optional." If yes, `Basic`'s click-to-toggle manual outputs must be accounted for (overlaps the
un-migrated `/manual` page); if no, someone must first do the 795-line comparison nobody has
done. Status: still undecided — see M5/COV-1 above.

**[O] T-S1c — Slice 1 caveat: correct the backlog's "mobile — may be obsolete if React is responsive" line and settle `/mobile`'s fate**
Source: `## Slice 1`, C8 row. C8 itself is implemented (reflow), which makes the backlog line
*more* wrong-or-right in a way nobody has recorded. Backlog-owned; not code.

**[O] T-S6/I13 — Coordinate `display.sleep_timeout` with the Qt display DPMS/sway work; General vs Platform**
Source: `## Slice 6`, I13 bullet. Same open item as I13, with an added cross-project sequencing
constraint and the note that PlatformTab is read-only by decision so General is the likely home.

**[O] T-S7 — Add a "chrome" section to the migration backlog so cross-cutting `base.html` items have a home**
Source: `## Slice 7`, opening paragraph ("cross-cutting chrome has no home in a page-shaped
backlog"). Backlog-structure work; M1/M2/M4/M17/I17 are its residents.

**[D] T-S9.3 — `common/system.py get_os_info(persist=True)`: destructive flag defaults to true, `get_`-named function writes the datastore**
Source: `## Slice 9` item 3. Plan calls for splitting into `probe_os_info()` + `refresh_os_info()`;
3 production call sites all take the default.
Was: `common/system.py:145` `def get_os_info(loggername="events", persist=True)` — the *docstring*
documented the write path but the split never happened.
**Done 2026-07-26.** Split as specified. `probe_os_info()` is pure and is what
`board-config.py::rpi_config_write` calls (it reads `VERSION_ID` to choose a config.txt path and
was the caller that genuinely just wanted a value — the counterexample to task ACC's "no caller
just wants to read"). `refresh_os_info()` carries the write for `--osversion`, the `os_info` system
command and `get_display_os_info()`'s cache-miss backfill; it still skips the write on a failed
probe, matching the old behaviour. The same change deleted `tests/conftest.py`'s dead
`_os_info_cache_off_repo` fixture, which had been feeding a tmp path to `loggername` session-wide.
This closes Slice 9 / accessor-rename WAVE 2 entirely.

**[O] T-S10.1 — No unconsumed-field regression check exists**
Source: `## Slice 10` item 1. The audit found 14 payload fields + 4 per-probe fields with zero
non-test consumers and *every one* was a dropped affordance — "a perfect signal and cheaply
greppable". Proposal: a script/test that fails on a new unconsumed field in `helpers/types.ts` /
`command.ts` with an explicit allowlist.
Checked: no such script or test in `/home/dannyb/sources/PiFire/web-react` (searched `src`,
`scripts`, `tests`).

**[?] T-S10.2 — "A reachability assertion per UI slice" exists as one test, not as a policy**
Source: `## Slice 10` item 2. `App.tsx` exports `routes` for exactly this and
`/home/dannyb/sources/PiFire/web-react/src/components/App.test.tsx` renders the real tree — but
nothing enforces that *each new slice* adds one. **Needs a human call** on whether the single
test satisfies the intent or a gate is wanted.

**[O] T-VER-1 — Wizard has no 800×480 (on-device panel) or phone-viewport layout coverage**
Source: `## Slice 10` item 3 (generalised) + `plans/2026-07-25-react-wizard-styling.md:178`
("appearance on the 800×480 on-device panel… record as backlog").
Checked: `/home/dannyb/sources/PiFire/web-react/playwright.config.ts:73-77` — the `panel`
(800×480) and `reflow` (390×844) projects both `testMatch` dashboard specs only;
`wizard-layout.spec.ts` runs in the default `app` project at 1280×720.

## ALREADY-DONE

**[D] T-S1b — C8 decision (fixed stage vs reflow).** Made and implemented; see C8 above.
**[D] T-S2 — Slice 2 (wizard unblock: C5, C6, C7, C4, I12).** Fully landed; plan
`plans/2026-07-25-wizard-critical-fixes.md` executed (see its own section below).
**[D] T-S3 — Slice 3 (dashboard: I3, D1, C3, I4, I1, I2, M6, M7, M8).** Fully landed.
**[D] T-S4 — Slice 4 (settings sweep: I5, I6, I15, I18a, M16, M15).** Landed, except I18b
(decision) which stays open above. The M16 `augerontime` direction was resolved in the schema's
favour (`2221d21e`).
**[D] T-S9.1 — `get_system_command_output()` discarding other consumers' queue entries.** Fixed:
`/home/dannyb/sources/PiFire/common/common.py:621-666` now peeks, pops-and-restores, and the
docstring records the old data-loss behaviour explicitly.
**[D] T-S9.2 — `read_warnings()` cross-consumer interference.** Fixed by splitting:
`common/datastore_accessors.py:213` `read_warnings()` (non-destructive, used by
`blueprints/mobile/socket_io.py:234`) and `:233` `drain_warnings()` (one-shot, single owner
`blueprints/dash/routes.py:25`).
**[D] T-S9.4 — `read_settings_file(init=True)` destructive default.** Now
`/home/dannyb/sources/PiFire/common/settings_migration.py:37` `init=False`, with both write paths
documented in the docstring (`:39-55`). The rename itself was not done and is not needed —
behaviour is safe and disclosed.
**[D] T-S10.3 — Second Playwright viewport.** `playwright.config.ts:62-77` adds `reflow`
(390×844) and `panel` (800×480) projects.
**[D] T-EXCL — Excluded-as-in-flight rows C0 / C1 / C2.** All three landed.
**[D] T-CORR — Triage's own corrections** (C2's "eleven beneficiaries" → nine; C8's "not
disclosed" → recorded-but-unratified). Both absorbed into the shipped work.

---

# plans/2026-07-25-wizard-critical-fixes.md

## STILL-OPEN

**[O] WCF-1 — `/scan`'s `vid`/`pid` are accepted but no client passes them; Flask parses them as hex and `/api/wizard/scan` does not**
Source: `### Out of scope — flagged, not fixed` (bullet 1). Currently a no-op (the only
`usb_serial_device` dep in the manifest has `vid: null, pid: null`), but "if this is ever wired
up, that mismatch is a real bug".
Checked: `/home/dannyb/sources/PiFire/web-react/src/helpers/wizard/wizardApi.ts:46` still takes
`{kind, vid?, pid?}`; no call site supplies them; `blueprints/api_wizard/routes.py:281` passes
`payload.get("vid")` straight through with no hex parse (Flask's `blueprints/wizard/routes.py`
does `int(…, 16)`).

**[O] WCF-2 — `window.location.href = "/admin/restart"` (and `/admin/reboot`) are same-origin and hit the React dev server, not Flask**
Source: `### Out of scope — flagged, not fixed` (bullet 2) — "same class of bug as C6, different
surface".
Checked: `/home/dannyb/sources/PiFire/web-react/src/components/wizard/WizardShell.tsx:136` and
`components/wizard/InstallProgress.tsx:69,72` all use bare `/admin/...` paths; neither routes
through `BASE_URL`/`wizardAssets`.

**[O] WCF-3 — No e2e coverage of the Exit Setup / `POST /api/wizard/cancel` flow**
Source: Task 6 (the plan's e2e task) called for "confirm-dialog steps + an exit spec"
(`## File Structure`, `tests/e2e/wizard.spec.ts` line). The confirm-dialog half shipped; the exit
spec did not.
Checked: `/home/dannyb/sources/PiFire/web-react/tests/e2e/wizard.spec.ts` has 4 tests (display,
probes, grill platform, distance) — zero hits for `Exit Setup` or `first_time_setup`.

## ALREADY-DONE

**[D] WCF-4 — "The wizard has no CSS at all. Not this slice's job."** (`Out of scope` bullet 3.)
Closed by `plans/2026-07-25-react-wizard-styling.md`;
`/home/dannyb/sources/PiFire/web-react/src/components/wizard/wizard.css` exists (624 lines).
**[D] WCF-5 — "Could not verify" (a): scoped-vs-blanket `/static` dev proxy at runtime.**
Resolved: `/home/dannyb/sources/PiFire/web-react/rsbuild.config.ts:30-36` proxies `/static/img`
only, with the reasoning (rsbuild emits this app's bundles under `/static/js`, `/static/css`)
written into the file.
**[D] WCF-6 — "Could not verify" (b): `.pf-modal-scrim` placement inside the wizard.**
Superseded — `wizard.css` now owns the wizard's modal treatment (spread-shadow scrim) and
`tests/e2e/wizard-layout.spec.ts:182` probes it synthetically.
**[D] WCF-7 — "Could not verify" (c): whether `/api/settings` can write `globals.first_time_setup`
for the e2e restore.** Moot — no exit e2e was written (see WCF-3).

---

# plans/2026-07-25-react-wizard-styling.md

## STILL-OPEN

**[O] WS-1 — Wizard "Show Output" install log deliberately not built (markup change)**
Source: `### Flask reference` table, row `wizard-finish.html:22-24` — "**Not built.**
`InstallProgress.tsx` does not render `status.output` at all… Out of scope: it would be a markup
change. Record as backlog." Same item as I10b.

**[O] WS-2 — Three wizard surfaces are unreachable by the e2e gate and rest on the human eye alone**
Source: `### (2) Playwright geometry` — "**Cannot reach three surfaces at all**": the 409 "grill
is active" modal (needs a running grill) and the reboot dialog (needs the real installer to have
run). Task 7 covers only their *rules*, via a clearly-labelled synthetic probe.
Checked: `/home/dannyb/sources/PiFire/web-react/tests/e2e/wizard-layout.spec.ts:182` — the
synthetic-probe test is exactly as described.

**[O] WS-3 — Never verified: WCAG colour contrast, the `data-accent="ice"|"crimson"` swaps, Barlow-unavailable rendering, and the 800×480 on-device panel**
Source: `### What nothing here can verify` — "Record these as backlog; do not claim them." Note
the wizard has no UI that sets an accent at all. Barlow-offline overlaps M3; 800×480 overlaps
T-VER-1.

## ALREADY-DONE / ACCEPTED-BY-DECISION

**[D] WS-4 — Deliberate divergence: Flask's 3-column `Setting / Options / Description` table
becomes label-over-control `.pf-field` stacks** (`### Flask reference` table). Recorded as
intentional; rebuilding the table needs markup changes this plan does not make.
**[D] WS-5 — Deliberate divergence: Flask's left `nav-pills` rail becomes a horizontal pill strip
in the header**, forced by the fixed 720px height. Recorded as intentional.
**[D] WS-6 — Screenshots are artifacts, not gates** (`### (3)`), by ratified reasoning shared with
the dashboard slice. Not a gap.
**[D] WS-7 — Task 7 must check `playwright.config.ts`'s shape before adding a spec**
(`## Coordination`). Resolved: the config is now a `projects` array and `wizard-layout.spec.ts`
lands in the default `app` project.

---

# plans/2026-07-14-wizard-optional-reboot.md

## STILL-OPEN

**[O] OR-1 — Unverifiable hardware assumption: that `raspi-config nonint do_onewire 0` writes `dtoverlay=w1-gpio` (no explicit pin), matching the grep in `wizard/ds18b20.sh`**
Source: `## Task 7 / Step 3: Manual/hardware follow-up note (not automatable here)` (line 1552).
"This assumption can't be verified in this dev environment (no `raspi-config` binary, no real
`/boot`)." If it is wrong, the idempotency check silently mis-fires.

**[O] OR-2 — End-to-end reboot-modal flow never exercised on real Pi hardware**
Source: same section, line 1553: select a GPIO-based module → finish → see the reboot modal →
click "Restart Services Only" → confirm the hardware overlay is *not* yet active until a manual
reboot, as the modal copy warns. **Needs a human with hardware.**

## ALREADY-DONE

**[D] OR-3 — The plan itself.** Every checkbox is unticked (40 unchecked, 0 checked) but the code
shipped: `/home/dannyb/sources/PiFire/board-config.py:505-520`
`_print_results_and_reboot_flag()` emits the `REBOOT_REQUIRED=<bool>` sentinel and
`/home/dannyb/sources/PiFire/wizard.py:156-189` parses it out of subprocess stdout
(`:431-435` acts on it). The plan document is stale, not the work.
**[D] OR-4 — `updater.py` / `updater_manifest.json` out of scope** (line 17). A deliberate
boundary, not a gap; the updater has its own `reboot_required` keys.

---

# plans/2026-07-23-wizard-module-config-display-first.md

**[D] MC-1 — grillplatform / probes / distance steps shipped as navigable placeholders only**
(`## Task 12`). All three are now real: `components/wizard/steps/{GrillPlatformStep,ProbesStep,DistanceStep}.tsx`.
**[D] MC-2 — Draft clearing assumed but not designed** (`## Self-review notes` bullet 1: "if the
executor finds no clear path, extend Task 2's `/draft` to accept `{clear: true}`"). Implemented:
`/home/dannyb/sources/PiFire/blueprints/api_wizard/routes.py:161-166`.
**[D] MC-3 — `profile_selected` shape and `wizard_bus_kinds` signature provisional pending a read
of the real helpers** (`## Self-review notes` bullet 2). Resolved during execution; both
endpoints are live and tested.
**[D] MC-4 — Display double-default manifest bug.** Fixed: `wizard/wizard_manifest.json` now has
exactly one `default: true` per section (grillplatform `pcb_4.x.x`, probes `ads1115_adafruit`,
display `ili9341b`, distance `none`).
**[D] MC-5 — First-time-setup forced-wizard gate left to the implementer's judgement**
(`## Task 12 / Step 3`, "if that risks a redirect loop… gate only the index route and document
it"). Implemented as a documented non-blocking post-mount check —
`/home/dannyb/sources/PiFire/web-react/src/components/App.tsx:37-48`.

No STILL-OPEN items in this plan.

---

# plans/2026-07-23-wizard-probes-config.md

## STILL-OPEN

**[O] PC-1 — Probe-profile CRUD (add/edit/delete of temperature profiles) is Settings-page-only and has no React home**
Source: `## Self-review notes` / spec `⑤ Profiles` — "Profile CRUD stays on the Settings page
(`blueprints/settings/routes.py:226-319`) — **out of scope**".
Checked: `/home/dannyb/sources/PiFire/web-react/src/components/App.tsx:74-87` has 11 settings
tabs and none of them is Probe Settings or Probe Profiles.

**[O] PC-2 — Deleting the legacy Jinja `blueprints/probeconfig/*` surface (post-parity cleanup)**
Source: `## Self-review notes`, "Deferred (tracked, not gaps)". Gated on React parity being
proven live.
Checked: `/home/dannyb/sources/PiFire/blueprints/probeconfig/` still present and routed.

## ALREADY-DONE

**[D] PC-3 — First-time board-default `probe_map` seeding** ("depends on the grillplatform step,
still a placeholder"). Shipped in the grillplatform slice: `_build_state` ships
`board_probe_maps` and seeds a fresh install from the selected board
(`blueprints/api_wizard/routes.py`, commit `68a7ec45`); client side
`helpers/wizard/wizardState.ts` `reseedProbeMapForBoard`.
**[D] PC-4 — Virtual-port reposition branches deferred from Task 7 to Task 8** (line 1000). Task
8 executed (`e3f8aa39`).
**[D] PC-5 — "Retroactive-only" ordering invariant on new adds, deliberately not "fixed"**
(line 203). An intentional legacy-faithful behaviour, pinned by tests — record as a decision, not
a gap.

---

# plans/2026-07-24-wizard-grillplatform-config.md

**[D] GP-1 — Retrofitting `DisplayStep`/distance onto the `module-values` round-trip**
(`## Out of scope (follow-ups)`, line 226 — "the endpoint is built general so this is later just
wiring. Record in the react-migration backlog"). Done by the display/distance plan:
`helpers/wizard/useModuleSwitch.ts` is consumed by `steps/GrillPlatformStep.tsx:1`,
`steps/DisplayStep.tsx:1` and `steps/DistanceStep.tsx:1`.
**[D] GP-2 — The distance step (still a placeholder).** Shipped (`2fec5bd5`).
**[D] GP-3 — `PlatformTab`.** Shipped (`9317dad6`), read-only by decision.
**[D] GP-4 — `settings["pwm"]` excluded from grillplatform's dep set as a separate subsystem**
(line 55). Deliberate boundary; the PWM settings tab owns it.
**[D] GP-5 — "`B` is canonical (do not fix)"** (line 219) — a recorded do-not-touch, not a gap.

No STILL-OPEN items in this plan.

---

# plans/2026-07-24-wizard-display-distance-config.md

**[D] DD-1 — `ConfigOptionField` does not fall back to the manifest `option.default`, so a
never-configured display module shows no selection** (`## Out of scope (follow-ups)`, line 225).
Fixed by `809667c3` ("fall back to manifest default for unset config options").
**[D] DD-2 — `PlatformTab`** (line 229). Shipped.
**[D] DD-3 — `first_time_setup` auto-redirect to `/wizard`** (line 230). Shipped (`a8e97a27`);
implemented as a post-mount check in `DashboardRoute`.
**[D] DD-4 — `PlaceholderStep` kept although the shell no longer uses it** (line 883, explicit
instruction to keep the component + its test). Deliberate; still present at
`components/wizard/steps/PlaceholderStep.tsx`.

No STILL-OPEN items in this plan.

---

# specs/2026-07-22-dashboard-real-design.md

**[D] DR-1 — Non-Goals (phase 1): settings pages, feature pages, auth, backend changes**
(line 47-51). Settings, history, pellets and wizard have all since shipped.

## STILL-OPEN

**[O] DR-2 — Roadmap item 3: Probe config (temp profiles + assignment + hardware device/port mapping) as a React surface**
Source: `## Roadmap (subsequent phases, out of scope here)` line 194. The wizard covers
device/port mapping for first-run setup; the standalone `/probeconfig` page and probe *profiles*
are still Flask-only. Same gap as PC-1/PC-2.

**[O] DR-3 — Roadmap item 5: the remaining feature pages — recipes, cookfile, tuner, metrics/events/logs, updater, admin**
Source: same line 196. Of that list only the pellet manager and history charts have shipped
(`components/pellets/PelletsPage.tsx`, `components/history/HistoryPage.tsx`). Backlog-tracked
elsewhere, but the spec is where they were first deferred.

**[O] DR-4 — Authentication never in scope for any phase**
Source: `## Non-Goals (phase 1)` "authentication", repeated in every later settings spec as
"No auth". Never subsequently scoped anywhere.

---

# specs/2026-07-22-settings-foundation-design.md

**[D] SF-1 — Phase 2b fan-out** (`## Phase 2b (follow-on, out of scope here)`, lines 197-204):
Work Mode, Safety, Startup/Shutdown/SmartStart, History, Pellet levels, the PWM/SmartStart
profiles tables, Notifications. All shipped.
**[D] SF-2 — Save failure → inline error on the tab** (`## Error handling` line 169) — the spec
requirement that C2 found unimplemented. Now implemented.

## STILL-OPEN

**[O] SF-3 — Probe config as its own larger sub-project** (`## Phase 2b`, line 204, and the
divergence audit's "Explicitly excluded" list citing `:197-206`). Never started. Duplicate of
PC-1/DR-2 — one backlog item, three sources.

---

# specs/2026-07-22-settings-2b1-scalar-tabs-design.md

**[D] S2B1-1 — Non-goals → 2b-2:** chart colours (`ColorField`), SmartStart/PWM profile tables,
controller config form (lines 33-40). All shipped in 2b-2.
**[D] S2B1-2 — Deferred phase-2a minors folded in:** `UnitsTab` `CommandResult.ok` check, router
`HydrateFallback` (lines 113-120). Both present (`components/App.tsx:30`, `UnitsTab.tsx`).
**[D] S2B1-3 — Notifications and Probe config named as "their own later sub-projects"** (line 39).
Notifications shipped; Probe config = SF-3 above.

No new STILL-OPEN items unique to this spec.

---

# specs/2026-07-22-settings-2b2-widgets-design.md

## STILL-OPEN

**[O] S2B2-1 — The three 2b-1 follow-up nice-to-haves, explicitly barred from riding along**
Source: `## Non-Goals` line 40-41 and repeated at
`specs/2026-07-22-toolchain-rsbuild-ts7-biome-design.md:170-175`: (a) `setTimeout` → `waitFor`
in tab tests, (b) align `read*` fallback defaults with `common/defaults.py`, (c)
`aria-describedby` on the gated-toggle hint, (d) float-vs-int coercion audit.
Checked: all 10 files under
`/home/dannyb/sources/PiFire/web-react/src/components/settings/tabs/*.test.tsx` still use
`setTimeout` and **none** uses `waitFor`; zero `aria-describedby` occurrences anywhere in
`web-react/src/components`.

## ALREADY-DONE / ACCEPTED

**[D] S2B2-2 — `numlist` option type deliberately unhandled** (line 37-38) — YAGNI, zero
controllers declare it. Recorded decision; the divergence audit accepted it.
**[D] S2B2-3 — Editing probe names/types from the colors section** (line 39) — belongs to probe
config (SF-3).

---

# specs/2026-07-22-toolchain-rsbuild-ts7-biome-design.md

**[D] TC-1 — Every stage shipped.** TS7 via the `typescript7` alias, Biome 2.5, rsbuild 2.1.7,
`@rstest/core`. `typescript` remains pinned at ^5.9.3 only as the ESLint parser peer (the spec's
Stage-1 risk row, resolved exactly as its mitigation predicted).
**[O] TC-2 — see S2B2-1** — this spec's `## Out of scope / follow-ups` (lines 170-175) is the
authoritative statement of those four items and they are still open. Counted once, under S2B2-1.

No other STILL-OPEN items.

---

# specs/2026-07-22-settings-schema-scoping.md

## STILL-OPEN

**[O] SS-1 — S3: defaults consolidation (end the `defaults.py` / schema dual authority) and typed deep-path `setPath` helpers**
Source: `**S3 (optional, later)**` lines 210-213. Explicitly "nice-to-haves; not needed for the
payoff", never scheduled.
Checked: `/home/dannyb/sources/PiFire/common/defaults.py` and `common/settings_schema.py` still
carry defaults independently, held in sync only by the parity test.

**[O] SS-2 — Per-controller schema generation from `controllers.json`**
Source: line 162-164 ("a later refinement can GENERATE per-controller schema"), restated as a
non-goal in both S1 (line 86) and S2 (line 127).
Checked: `common/settings_schema.py` still models `controller.config` as a loose
`dict[str, dict[...]]`.

**[O] SS-3 — Client-side runtime validation (ajv/zod) deliberately deferred as a future decision**
Source: line 86-89 ("zod is out… Client-side pre-POST validation, if ever wanted, would be
revisited as a new decision") and line 204-207. An accepted divergence, but the "delete the
duplicated React clamps" half was later reversed by S2 ("Client clamps STAY (UX)") — the two
documents disagree and nothing records which wins.

---

# specs/2026-07-23-settings-schema-s1-design.md

**[D] S1-1 — Every S1 non-goal was S2's job and S2 shipped** (`## Non-goals (S2+)`, lines 82-86):
runtime validation, pydantic-partial, constraint enforcement.
**[D] S1-2 — Dynamic zones stay deliberately loose** (lines 32-39). Still true and still
deliberate; the per-controller half is SS-2.
**[D] S1-3 — "Pin the documented behaviour, don't fight it" on lax coercion** (line 91-93).
Superseded by S2's strict mode, which deleted that pin by design.

No STILL-OPEN items unique to this spec.

---

# specs/2026-07-23-settings-schema-s2-design.md

## STILL-OPEN

**[O] S2-1 — `additionalProperties` stripping in TS generation ("still backlog")**
Source: `## Non-goals` line 127-128, flagged in the source text as already-backlog.
Checked: `/home/dannyb/sources/PiFire/web-react/src/helpers/settings/settingsTypes.gen.ts`
contains 12 `[k: string]` index signatures, so the generated `Settings` type still admits
arbitrary keys and hides typos at compile time.

**[O] S2-2 — `<path>: <why>` error-detail display polish deliberately left out of S2**
Source: `### 5. React side` line 117-119 — "add the `<path>: <why>` display polish only if it's
already plumbed; NO new UI work in S2 (that's cosmetics for later)".
Checked: `helpers/settings/useSaveSettings.ts:19-22` `normalizeSaveError()` keeps the dotted path
verbatim, so the raw pydantic path is what the user sees — never designed for readability.

**[O] S2-3 — Read-path validation never scoped**
Source: `## Non-goals` line 128 ("any read-path validation"). A malformed store still reads
straight through to the UI.

## ALREADY-DONE

**[D] S2-4 — WLED preset dicts modelled ("the S2 deferral lands here")** (line 70-71). Done:
`common/settings_schema.py:442,461,476` define `WledProfileNumbers`, `WledModePresets`,
`WledEventPresets`.
**[D] S2-5 — Sequencing: notifications page then probe-config page.** Notifications shipped;
probe-config = SF-3.
**[D] S2-6 — Accepted risk: an unfixed writer now raises instead of persisting a malformed tree**
(lines 40-43, "just deal with it"). A ratified risk, mitigated by the writer matrix, not a gap.

---

# specs/2026-07-23-notifications-tab-design.md

## STILL-OPEN

**[O] NT-1 — WLED preset/profile grids explicitly deferred (the whole WLED profile-editor surface)**
Source: `**WLED preset grids are explicitly deferred**` lines 51-56 + `## Non-goals` line 103:
`profile_numbers` (12), `mode_presets` (7), `event_presets` (5), `suggested_config`,
`use_profiles` / `use_suggested_presets`. They rebuild untouched today.
Checked: `components/settings/tabs/NotificationsTab.tsx:315-330` edits WLED's three scalar fields
only. (Backend + schema are ready — see S2-4 — so this is UI-only work.)

**[O] NT-2 — Secret masking out of scope (tokens/passwords/API keys are plain text inputs)**
Source: line 47-49 and `## Non-goals` line 104. An accepted divergence matching legacy, worth a
recorded disposition rather than silence.

**[O] NT-3 — Sequencing: "After this: the probe-config page"**
Source: line 108. Never started — same item as SF-3/PC-1/DR-2.

## ALREADY-DONE

**[D] NT-4 — Per-probe notify TARGETS declared a non-goal here ("cook-flow, not settings")**
(line 103). Correctly routed and since shipped on the dashboard (C1).
**[D] NT-5 — OneSignal add-device deliberately absent (app-driven registration)** (line 104).
Deliberate; the empty-state hint explains it.

---

# specs/2026-07-25-tailwind-v4-migration-design.md

**Entire spec is deferred by its own status line** — "approved design, implementation blocked
until the wizard-styling and dashboard-reflow slices merge" (line 3, `## Sequencing` line 220).
Both blockers have now merged, so it is unblocked; a plan is being written separately. Its
in-document deferrals:

**[O] TW-1 — No browserslist is pinned; the target set must be established (and probably pinned) before starting**
Source: `## Integration`, first caveat (lines 111-119). Tailwind v4 requires Cascade Layers in
the target browsers, and an unpinned list means CSS output can drift under a dependency update —
"precisely the kind of silent drift the visual-identity gate would then report as a regression
with no cause in the diff".
Checked: no `.browserslistrc` and no `browserslist` key in
`/home/dannyb/sources/PiFire/web-react/package.json`.

**[O] TW-2 — Unverified: whether Biome's CSS parser accepts `@theme`, `@apply` and `@import "tailwindcss"`**
Source: `## Risks specific to this repo` (lines 196-200) — "Confirm early… Do not discover this
at the end." Not yet checked; `bun run lint` runs Biome over `.css`.

**[O] TW-3 — Visual-identity baselines do not exist yet for any page**
Source: `### Coverage` (lines 141-151) — baselines wanted for dashboard, shell, 12 settings tabs,
7 wizard steps and history, at **both** 1280×720 and 390×844.
Checked: only `/home/dannyb/sources/PiFire/web-react/tests/e2e/dashboard-layout-1280x720.json`
exists; there is no `tests/e2e/baselines/` directory.

**[O] TW-4 — Explicit out-of-scope list, deferred to a later slice**
Source: `## Out of scope` (lines 246-252): converting `pf-*` rules to inline utilities in JSX
(stated as a deliberately reversible follow-on at lines 77-81); changing any token/spacing/type
scale; light theming (dark-only by design); the Flask/Jinja stylesheets.

**[O] TW-5 — Dynamic class names (`pf-badge-${mode}`, `pf-banner--${kind}`) are invisible to Tailwind's content scanner**
Source: `## Risks specific to this repo` (lines 212-216) — harmless while they stay hand-written
rules, "becomes a silently-missing style the moment anyone converts them to utilities". The spec
asks for a note where those rules live; that note does not exist yet.
