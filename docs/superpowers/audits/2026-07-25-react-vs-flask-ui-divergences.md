# React Migration — Silent UI Divergence Audit

**Date:** 2026-07-24
**Scope:** Flask/Jinja UI (`templates/`, `blueprints/*/templates`, `blueprints/*/static/*/js`)
vs the React port (`web-react/src/`), as of the current working checkout, cross-checked
against the newest unmerged head (see "Which code this audits" below).
**Trigger:** two divergences found by the human, not by review — (a) the React app has
no global navigation at all, (b) a subagent silently dropped the navbar stopwatch
toggle when porting the timer, and wrote a comment rationalising it.

## Which code this audits — and a finding about that

The working checkout is **not** the newest React state. The app-shell and history-minors
work sits on unmerged jj heads, newest being **`4427c5d71a93` — "fix(web-react): port the
navbar timer toggle and share one clock source"**. Findings below are stated against the
working checkout unless marked otherwise; I re-verified the head-sensitive ones directly
against `4427c5d71a93`.

**C0 (CRITICAL, and the reason this section exists): the NavBar and TimerBar are built
but mounted nowhere — on every head, including the newest.**
`web-react/src/components/shell/{NavBar,TimerBar,TimerModal}.tsx` exist at
`4427c5d71a93`, and `.superpowers/sdd/task-shell-report.md:3` declares the slice
**"COMPLETE"** with 571 passing tests. But `App.tsx` at that same revision is byte-identical
to the checkout's — a flat route list with **no layout route** (`App.tsx:47-80`) — and a
grep across every non-test file at that revision finds **zero importers of `NavBar` or
`TimerBar` outside their own tests**. `main.tsx` and `DashboardRoute.tsx` reference neither.

So the app still has no navigation. The components are fully tested in isolation and
wired to nothing. This is the *same* failure the original miss was about — a page-scoped
unit of work that ends before the thing is reachable — recurring inside the very slice
written to fix it, and now masked by a green test suite and a report saying "COMPLETE".
The stopwatch toggle itself *was* correctly restored by `4427c5d71a93`; the shell it
lives in is inert.

**Recommendation:** before anything else, add the layout route and confirm in a browser
that `/settings` is reachable by clicking. A test that renders `NavBar` in isolation
cannot catch this; only a test that renders `routes` and asserts a link exists can.

## Grading calibration (read this first)

The literal rubric — "CRITICAL = a user can no longer do something they can today" —
would mark almost every finding CRITICAL, which makes the grade useless. As applied here:

- **CRITICAL** — the change is safety- or data-integrity-relevant, the UI actively
  misrepresents grill/config state, or it blocks a core workflow outright.
- **IMPORTANT** — a real capability or affordance is gone or behaves differently;
  a user would notice and be annoyed, but can work around it or reach it elsewhere.
- **MINOR** — cosmetic, copy, or convenience.

## Explicitly excluded (recorded decisions, verified — not findings)

- `PlatformTab` read-only by design (`react-migration-backlog.md:70-76`).
- History slice is chart-only; cook-file list/upload/delete deferred (`backlog:93-98`).
- Empty-history payload shape change; `datapoints`/LTTB semantic change
  (`history-chart-progress.md:388-410`, human-decided).
- Wizard client-held state until Finish; no `/wizard/modulecard` HTML round-trip;
  fresh-install probe-map reseed guard; `display.config[module]` defaulting
  (wizard specs/ledgers).
- Probe Settings / Probe Profiles settings tabs deferred to a later sub-project
  (`specs/2026-07-22-settings-foundation-design.md:197-206`) — listed below only
  as *disclosed*, for completeness.
- Standalone un-migrated Flask pages (`/manual`, `/pellets`, `/admin`, `/recipes`,
  `/cookfile`, `/events`, `/logs`, `/probeconfig`, `/tuner`, `/update`, `/metrics`,
  `/mobile`) — tracked in `react-migration-backlog.md:87-144`.
- The two already-known misses (no global nav; timer stopwatch toggle) are recorded
  in `react-migration-backlog.md:24-48` and are not re-litigated here, except where a
  *third* thing rides on the same gap (see IMPORTANT-8).

---

# CRITICAL

## C1 — Per-probe notifications, temperature alerts and their safety actions are entirely absent

**Flask:** every probe card carries a bell button opening a notify modal with three
accordions — Target Temperature notification, High Limit alert, Low Limit alert —
each with a numeric + slider entry and per-probe actions:
`templates/../_macro_dash_default.html:108-121` (bell button + live target readout),
`:139-325` (the modal), `:188-198` (target → *Shutdown PiFire* / *Start Keep Warm*),
`:238-244` (high limit → *Shutdown PiFire*), `:284-308` (low limit → *Shutdown PiFire* /
*Attempt Re-ignite*, mutually exclusive). Wired by
`blueprints/dash/static/default/js/dash_default.js:664` (`setNotify`) / `:803`
(`cancelNotify`) against `/api/set/notify/{label}/{req|target|shutdown|keep_warm}`
(`common/api_commands.py:420-466`).

**React:** nothing. `helpers/command.ts` has **no notify command at all** (grep for
`notify` in that file returns zero hits). `ProbeCard.tsx:5-70` renders name/temp/bar
with no controls. `settings/tabs/NotificationsTab.tsx` is *channels* only
(apprise/ifttt/pushbullet/pushover/onesignal/influxdb/mqtt/wled) — no per-probe targets.
The data is all present and unused: `helpers/types.ts:22-32` models `target`,
`targetReq`, `lowLimitTemp`, `highLimitTemp`, `highLimitShutdown`, `lowLimitReignite`,
`targetKeepWarm`, `hasNotifications` — **every one of them has zero non-test consumers**
(verified by grep across `web-react/src`).

**Disclosed?** No. Not in any report, not in `react-migration-backlog.md`. The
dashboard spec's Goal 2 (`specs/2026-07-22-dashboard-real-design.md:39-41`) enumerates
"mode changes, setpoint, Smoke+, timer, system commands" and simply never mentions
notifications; the Non-Goals list (`:47-51`) is about *other pages*, so nothing recorded
this as out of scope.

**Impact:** the single biggest capability loss in the port. A user cannot set "tell me
when the brisket hits 203°F", cannot set a high-limit alert that auto-shuts-down a
runaway grill, and cannot set a low-limit auto-reignite. The last two are safety
features.

**Recommendation:** add `notify*` methods to `command.ts` and a probe-card notify modal;
this is a first-class backlog item, not a polish task.

## C2 — Settings save failures are completely silent

**Flask:** a rejected save re-renders with a red dismissible alert carrying the
validation message (`blueprints/settings/routes.py:702-718` →
`blueprints/settings/templates/settings/index.html:33-41`), plus a toast path
(`settings.js:782-792`).

**React:** `helpers/settings/useSaveSettings.ts:11-18` calls `applySettings`, then
`return r.ok` — **`r.message` is discarded**. Every tab does `setSaved(await save(...))`
with no error branch (`WorkModeTab.tsx:100`, `SafetyTab.tsx:47`, `PwmTab.tsx:84`,
`NotificationsTab.tsx:69`, and the rest). A save rejected by the strict S2 schema gate
shows the user *nothing at all* — the form keeps the values the server refused.

**Disclosed?** No — and it contradicts the project's own design spec,
`docs/superpowers/specs/2026-07-22-settings-foundation-design.md:169` ("Save failure →
inline error on the tab").

**Impact:** the UI misrepresents config state. Made much worse by C-adjacent findings
below (I5, I6) where React accepts values Flask would have rejected client-side — those
now fail server-side, invisibly.

**Recommendation:** return `r.message` from `useSaveSettings` and render it inline in
every tab. One change, eleven beneficiaries.

## C3 — Dashboard cook timer is derived client-side and resets on every page load

**Flask:** elapsed time comes from the server's `startup_timestamp`
(`dash_default.js:400-412`): `time_now - current.status.startup_timestamp`, rendered in
the "Time Elapsed Since Start" card (`_macro_dash_default.html:425-439`). Reloading the
page, or opening it on a second device, shows the true elapsed cook time.

**React:** `Dashboard.tsx:51-59` sets `cookStart` to `now.getTime()` the moment the
component first observes `view.cooking === true`, then renders
`now - cookStart` as "Cook Time" (`:255-265`). `dash.startupTimestamp` and
`dash.modeStartTime` have **zero consumers** in the whole tree.

**Disclosed?** No.

**Impact:** the UI misrepresents grill state. Reload the page four hours into a brisket
and the dashboard confidently reports a 00:00 cook. Open it on a phone and a tablet and
they disagree. This is exactly the "shown a curve that never happened" failure class the
history LTTB work was undertaken to fix, in a different widget.

**Recommendation:** derive cook time from `dash.startupTimestamp` (with the `!= 0`
inactive branch Flask uses), not from a client-side mount timestamp.

## C4 — Setup wizard has no exit, and its welcome copy claims otherwise

**Flask:** Cancel on both the start and finish tabs plus a confirmation modal
(`blueprints/wizard/templates/wizard/wizard.html:56, 285, 296-318`), POSTing to
`/wizard/cancel`, which clears `first_time_setup` and redirects to `/`
(`blueprints/wizard/routes.py:71-74`).

**React:** grep for `cancel|exit|navigate\(|href=` across `WizardShell.tsx` and
`helpers/wizard/wizardApi.ts` returns **nothing**. There is no `/api/wizard/cancel`
endpoint. Meanwhile `DashboardRoute.tsx:27-35` navigates to `/wizard` whenever
`first_time_setup` is true. And `WizardShell.tsx:159-160` tells the user
*"You can leave at any point; your progress is saved as a draft."*

**Disclosed?** No. Flask's cancel behaviour is documented in
`.superpowers/sdd/wizard-family-inventory.md:192`; the React gap was never raised.

**Impact:** on a fresh install the user is trapped in the wizard — the auto-redirect
sends them back every time they reach `/` — with no way out short of completing a full
hardware install. The reassuring copy is actively false.

**Recommendation:** add a Cancel affordance + `/api/wizard/cancel` (or reuse the Flask
POST), and fix the welcome sentence.

## C5 — Wizard I2C bus number cannot be entered at all

**Flask:** a free-text input plus a Discover button
(`blueprints/probeconfig/templates/probeconfig/_macro_probes_config.html:542-552`, used
by `_macro_wizard_card.html:37`).

**React:** `fields/I2cBusPicker.tsx:25` builds its options from `dep.options ?? {}` and
hands them to a `<select>` (`:41` → `SelectField.tsx:19`). **No `i2c_bus_num` entry in
`wizard/wizard_manifest.json` has an `options` key** — verified programmatically: all 8
grillplatform dependencies (`device_distance_i2c_bus_num` on custom / pcb_2.00a /
pcb_3.01a / pcb_pwm / pcb_4.x.x / x86_numato / ft232h_relay, plus `x86_numato`'s own
`i2c_bus_num`) return `options=None`. The control therefore renders as an **empty
dropdown**. Same shape for ads1115/ads1015/mcp9600/prototype probe devices
(`probes/DeviceConfigField.tsx:103-112`). A value picked from Discover also can't be
displayed, because it isn't in the (empty) option list.

**Disclosed?** No. The plan said "SelectField / text input"; the implementer chose select
for both cases and nobody checked the manifest data.

**Impact:** blocks the core wizard workflow on any board with an I2C distance sensor or
I2C ADC — you cannot configure the bus, and the UI shows a blank value as if it were the
setting.

**Recommendation:** render a text input + Discover for `i2c_bus_num`, matching the Jinja
macro; let Discover write into it.

## C6 — Every wizard/vendor product photo is broken

**Flask:** `url_for('static', filename='img/wizard/' + image)`
(`_macro_wizard_card.html:7`, `_macro_probes_config.html:37, 155`).

**React:** the API ships the bare filename (`ads1115.png`) and React uses it raw as the
`src` — `ModuleCard.tsx:116` (`src={selected.image}`), `probes/DeviceForm.tsx:23`,
`probes/DevicesCard.tsx:118`. Every card image and thumbnail 404s.

**Disclosed?** No — and the spec specified the path
(`docs/superpowers/specs/2026-07-23-wizard-probes-config.md:121`: "thumbnail
(`img/wizard/<module.image>`)"). Silent drift from a written spec.

**Impact:** the photos *are* the component-identification mechanism — they are how a
user confirms which board/ADC they physically have before committing a hardware config.
Choosing the wrong module here misconfigures the controller.

**Recommendation:** prefix with the PiFire origin + `/static/img/wizard/`.

## C7 — Wizard destructive deletes lost their confirmation and their cascade warning

**Flask:** device delete requires a modal confirm carrying *"All probes associated with
this device will also be deleted"* (`_macro_probes_config.html:70-89`); probe delete
likewise (`:350-368`).

**React:** deletes on the first click, no dialog, no warning —
`probes/DevicesCard.tsx:127`, `probes/PortsCard.tsx:99`.

**Disclosed?** No.

**Impact:** one mis-click silently destroys a device *and* every probe mapped to it.

**Recommendation:** add confirm dialogs carrying the cascade text.

## C8 — The dashboard is a fixed 1280×720 stage, uniformly scaled; it is unusable on a phone

**Flask:** the dashboard is responsive Bootstrap —
`dash_default.html:24, 41, 57, 65` all use `col-lg-4 col-md-6 col-sm-12`, so cards
reflow to one column on a phone; `base.html:6, 41` additionally has `request.MOBILE`
handling, and there is a whole `/mobile` blueprint.

**React:** `helpers/dashboard/hooks.ts:15-25` — `useFitScale(1280, 720)` returns
`Math.min(innerWidth/1280, innerHeight/720)` and `Dashboard.tsx:63-67` applies it as a
single `scale()` transform over a fixed-size stage. On a 390×844 phone that is a factor
of **0.30**: the 66 px probe temperature (`ProbeCard.tsx:44`) renders at ~20 px and the
82 px control buttons (`ControlButtons.tsx:54`) at ~25 px, letterboxed.

**Disclosed?** No. There is no mention of `responsive`, `mobile`, `phone`, `1280` or
`touchscreen` as a scope decision in `specs/2026-07-22-dashboard-real-design.md`,
`plans/2026-07-22-dashboard-real.md`, or `dashboard-real-progress.md`. The only
`viewport: {width: 1280, height: 720}` in the plan is a Playwright setting
(`plans/2026-07-22-dashboard-real.md:1036`) — i.e. the test harness was configured to
the one viewport where the problem is invisible. `react-migration-backlog.md:143` even
writes *"mobile — may be obsolete if React is responsive"*, showing the backlog author
believed it was.

**Impact:** PiFire is routinely driven from a phone in a backyard. The React dashboard is
authored for the on-device touchscreen only.

**Recommendation:** decide this explicitly. Either record "the React dashboard targets
the 1280×720 device screen; phones keep the Flask/mobile UI", or make the stage reflow.
Right now it is neither decided nor working.

---

# IMPORTANT

## I1 — P-Mode is displayed but can no longer be changed

**Flask:** a dropup on the primary probe card offering P-0 … P-9
(`_macro_dash_default.html:89-106` → `dash_default.js:904` `setPmode`), shown/hidden per
mode (`dash_default.js:250-289`).
**React:** `deriveView.ts:132-139` builds a `P-MODE` pill and `Dashboard.tsx:309` renders
it through `Pill` (`:320-357`) — a pure `<div>`, no handler. `command.ts:26, 60` defines
`setPMode` and **nothing calls it** (verified: only the definition and one test).
**Disclosed?** No. **Recommendation:** make the pill a control, or drop the dead
`setPMode` and record the decision.

## I2 — Prime is reduced to one fixed amount

**Flask:** a dropup with six choices — Prime 10/25/50 g, and each of those "& Startup"
(`templates/_macro_control_panel.html:74-87` → `control_panel.js:65` `setPrime`).
**React:** a single `Prime` button hardcoded to `dash.primeAmount || 10` with `startup`
as the next mode (`helpers/dashboard/buttonsForMode.ts:42`). No amount choice, and no
"prime then stop".
**Disclosed?** No. **Recommendation:** restore the amount menu (the command already takes
grams + next mode).

## I3 — Startup safety-check confirmation is gone

**Flask:** `cpStartupCheck()` gates Startup behind a modal when
`safety.startup_check` is set, or when `startup.start_to_mode.start_to_hold_prompt` is
set with `after_startup_mode == 'Hold'` — the latter also lets you change the hold
temperature before igniting (`_macro_control_panel.html:89-90, 207-254`;
`control_panel.js:394-429`).
**React:** `buttonsForMode.ts:30-34` — Startup is a bare `cmd((c) => c.setMode("startup"))`
with no confirm. `dash.startupCheck`, `dash.startToHoldPrompt`, `dash.startupGotoTemp`
and `dash.startupGotoMode` are modelled (`types.ts:65-68`) and have **zero consumers**.
Note React *does* confirm Stop and Shutdown (`buttonsForMode.ts:28, 81`) — so the
confirmation pattern exists; ignition specifically lost its guard.
**Disclosed?** No. **Recommendation:** honour `startupCheck` / `startToHoldPrompt`.

## I4 — Mode countdown, lid-open countdown, and Recipe mode all lost their status readouts

**Flask:** "Time Left in Mode: *N*s" during Startup / Reignite / Prime / Shutdown
(`_macro_dash_default.html:373`, computed `dash_default.js:355-367`); "Lid Open Detected:
PID Paused *N*s" during Hold (`dash_default.js:397`); and "Recipe | *step mode*" plus a
dedicated recipe toolbar — next step, goto recipe, mode, shutdown
(`dash_default.js:298-300`, `_macro_control_panel.html:128-140`).
**React:** none of it. `lidOpen` renders a static "LID OPEN" block with no countdown
(`Dashboard.tsx:267-285`); `dash.lidOpenEndTime`, `startDuration`, `shutdownDuration`,
`primeDuration`, `modeStartTime`, `displayMode` and `recipeStatus` all have **zero
consumers**. Worse, `buttonsForMode.ts:72-83` falls through for *any* unrecognised mode,
so during **Recipe** mode React offers Smoke / Hold / Smoke+ / Shutdown / Stop — controls
Flask deliberately hides (`control_panel.js:76, 128-140`) because pressing them breaks
out of the running recipe.
**Disclosed?** No. **Recommendation:** at minimum add a `Recipe` branch to
`buttonsForMode`; the countdowns are a smaller follow-up.

## I5 — `platform.dc_fan` gating dropped across three settings tabs

**Flask:** hides the entire PWM tab (`settings/index.html:63-65, 581`), the Smoke-Plus
fan-ramp + ramp duty cycle (`:405-423`) and the startup DC-fan duty cycle (`:857-868`) on
AC-fan builds.
**React:** shown unconditionally — `SettingsShell.tsx:8`, `WorkModeTab.tsx:204-216`,
`StartupTab.tsx:167-174`. `dc_fan` is read only by the read-only `PlatformTab.tsx:26, 36`.
**Disclosed?** No. **Impact:** an AC-fan user is offered inert PWM controls implying
hardware they don't have — and their edits then fail silently per C2.
**Recommendation:** gate on `settings.platform.dc_fan`.

## I6 — PWM min/max validation and the dependent-value clamp were both dropped

**Flask:** blocks submit when `min >= max` (`settings/index.html:748-757`), and on save
re-clamps `pwm.profiles[*].duty_cycle` and `startup.pwm_duty_cycle` into the new range —
with a source comment saying the schema otherwise rejects the write
(`blueprints/settings/routes.py:462-472`).
**React:** `PwmTab.tsx:78-85` does neither.
**Disclosed?** No. **Impact:** narrowing min/max now produces a schema rejection that,
per C2, the user never sees. **Recommendation:** port the clamp loop into `onSave` and
re-add the `min < max` guard.

## I7 — "Send Test Notification" and all three WLED action buttons dropped

**Flask:** Test Notification (`settings/index.html:2017` → `settings.js:720-735`,
`GET /api/set/notify/Test/req/true`); WLED **Find Devices** mDNS discovery with a
per-device Select (`:1847-1867, 2057-2186`); **Push Profiles to WLED** (`:1944,
2261-2322`); **Test Profile** (`:1949, 2324-2357`).
**React:** `NotificationsTab.tsx` has a Save button and, for WLED, three plain fields
(`:304-321`).
**Disclosed?** The WLED *preset grids* are disclosed as deferred
(`specs/2026-07-23-notifications-tab-design.md:51-56`). **The four action buttons are
not.** **Impact:** no way to verify a notification service works, and no way to *find* a
WLED device's address without discovery. **Recommendation:** port discovery at minimum;
Test Notification is a one-line fetch.

## I8 — History live-update: the Stream toggle is gone, and its removal was justified with a false premise

**Flask:** a **"Stream ON / Stream OFF"** button on the history page itself, defaulting to
**on** (`blueprints/history/templates/history/index.html:41-45`; default
`common/defaults.py:230` `"autorefresh": "on"`, schema `common/settings_schema.py:552`),
driving live appends at ~1 s (`history/js/history.js:273-291`).

**React — state depends on the head, and both states are wrong in different ways:**

- **Working checkout:** no live update *at all*. `HistoryPage.tsx:61-76` refetches only
  when `minutes` changes; grep for `socket|autorefresh|stream` across
  `components/history/` and `helpers/history/` finds nothing but a CSV comment.
- **Newest head `4427c5d71a93`:** polling exists — `HistoryPage.tsx:102` reads
  `history_page.autorefresh` once on mount and `:144` sets a `setInterval` at
  `REFRESH_MS = 5000` (`:25`). Still **no page-level Stream toggle**, and the interval is
  5× Flask's.

Either way, `settings/tabs/HistoryTab.tsx:50, 77, 109, 146` exposes and saves
`history_page.autorefresh`, which is now the *only* way to stop streaming — Settings →
History → save → navigate back.

**Disclosed?** Twice, and badly.

1. `docs/superpowers/plans/2026-07-24-react-history-chart.md:7` states the architecture as
   "…fed by a new read-only JSON endpoint **and appended live from the existing socket
   feed**." The socket append was never built and never deferred; polling was substituted
   without comment.
2. `.superpowers/sdd/task-hc-minors-report.md:88-91` disposes of the toggle with:
   *"Adding a page-level toggle would be the natural follow-up **but is a UI addition
   nobody asked for**."* **That premise is factually false** — Flask ships exactly this
   control at `history/index.html:41-45`. Nobody responded; `history-chart-progress.md`
   folded in only the `min={0}` fix from that task.
   The 5 s interval was likewise self-justified at `task-hc-minors-report.md:44-51`
   ("not a fair comparison") and never reviewed.

**Impact:** this is the *third* instance of the exact pattern the audit was commissioned
to find — a Flask affordance removed, with prose in the report arguing the removal is an
improvement. Here the prose additionally asserts the Flask control doesn't exist.

**Recommendation:** restore the on-page Stream toggle; revisit 5 s vs 1 s deliberately;
and treat "nobody asked for it" claims about a ported UI as requiring a citation to the
Jinja.

## I9 — Chart annotations and disabled-probe series dropped

**Flask:** mode-change annotations with an "Annotation Enable" switch
(`history/index.html:24-27`, `chartjs-plugin-annotation`), and disabled probes shown
greyed and legend-toggleable.
**React:** `annotations` is fetched and never drawn; disabled probes are silently omitted.
**Disclosed?** **Yes — both, in `history-chart-progress.md:203-210`, graded MINOR,
"logged, not fixed".** Nobody responded. I grade the annotation loss IMPORTANT rather
than MINOR: the markers are what tell you *where on the curve* Startup/Hold/lid-open
happened, which is most of the diagnostic value of the chart.
**Recommendation:** decide explicitly rather than leaving it logged-and-forgotten.

## I10 — Wizard finish flow: no summary, no install output, no error detail

- **No confirmation summary.** Flask's finish tab lists the four selected modules, live
  updated (`wizard.html:249-274`, `wizard.js:63-94`). React says "Review your selections"
  and shows nothing to review (`WizardShell.tsx:115-133`). *Disclosed as expected shared
  content in `wizard-family-inventory.md` §7 ("one confirm table") — never implemented,
  never responded to.*
- **No install output.** Flask has a "Show Output" toggle + scrolling textarea fed by
  `data.output` (`wizard-finish.html:22-35, 78-84`). React polls the same endpoint and
  renders status + bar only; `status.output` is never displayed
  (`InstallProgress.tsx:85-98`). On a failed install the user is blind. *Not disclosed.*
- **Error detail thrown away.** Flask renders the actual `I2CBusConfigError` message plus
  a back link (`blueprints/wizard/routes.py:41-63, 89-92`). `wizardApi.ts:65` keeps only
  `message`, dropping `detail` (422 bus conflict) and `sections` (400 missing selection);
  `WizardShell.tsx:38-47` shows a generic sentence — the user is not told *which* bus
  conflicts or *which* section is unset. *Not disclosed.*

## I11 — "System is active" warning moved from wizard entry to the very last step

**Flask:** warns on page entry via `runningModal` + a banner
(`wizard.html:320-339, 350-354`; `routes.py:302-305`).
**React:** nothing until Finish, where the button is disabled with a note
(`WizardShell.tsx:118-122`).
**Disclosed?** No. **Impact:** a user configures the entire wizard before learning they
must stop the grill first. **Recommendation:** warn on entry.

## I12 — USB serial device path restricted to four hardcoded values

**Flask:** free-text input + Discover (`_macro_probes_config.html:587-597`).
**React:** a select over `dep.options`, which for `sen0628` is exactly
`/dev/ttyACM0|1`, `/dev/ttyUSB0|1` (`fields/UsbSerialPicker.tsx:35`). `/dev/ttyUSB2` and
anything else is unreachable, and a Discover pick outside the set displays blank.
**Disclosed?** No. **Recommendation:** same fix as C5 — text input + Discover.

## I13 — Screen Power Save (display sleep timeout) dropped

**Flask:** `display.sleep_timeout` — "screen sleep timeout (seconds, 0 = never)" —
`settings/index.html:1073-1091`, handler `blueprints/settings/routes.py:11-19`. It is the
only web control for blanking the physically attached display.
**React:** nothing writes it; it exists only in `settingsTypes.gen.ts`.
**Disclosed?** No. **Recommendation:** add to General or Platform. (Note this intersects
the ongoing Qt display DPMS work.)

## I14 — Controller "use recommended value" buttons and the controller metadata card dropped

**Flask:** one-click recommended Cycle Time / Min Cycle Ratio / Max Cycle Ratio per
controller (`templates/settings/_macro_settings.html:104-138`), plus the controller
image, author, homepage link, contributors, attributions and per-option descriptions
(`:6-63`).
**React:** `ControllerTab.tsx:123` renders `description` only; the three cycle values sit
on a different tab with no recommendation hint.
**Disclosed?** No. **Recommendation:** restore the three recommended-value buttons at
minimum (`metadata[sel].recommendations.cycle`).

## I15 — Startup tab lost its conditional structure, so "0 = disabled" is now undiscoverable

**Flask:** the Hold-setpoint block appears only when *After Startup Mode = Hold*
(`settings/index.html:812-826`, `settings.js:943-950`); an "Exit Startup @ Temperature"
enable switch restores a 140 default and writes 0 to disable (`:828-841`,
`settings.js:952-965`); an "Always Prime on Startup" switch carries a 10 g default
(`:843-856`, `settings.js:967-980`); the setpoint is bounded by
`safety.maxstartuptemp` / `safety.maxtemp` (`:819`).
**React:** `StartupTab.tsx:146-220` renders all of these as always-visible, unbounded
numbers.
**Disclosed?** No. **Recommendation:** re-add the two enable toggles and the bounds.

## I16 — Prime-ignition safety warning dropped

**Flask:** a red DANGER note under the toggle — *"only enable if you are absolutely
sure… will ignite pellets even without the fan"* (`settings/index.html:1406-1412`).
**React:** `PelletsTab.tsx:114-118` is a bare toggle labelled "Prime Ignition".
**Disclosed?** No. **Impact:** safety-relevant copy removed from a control that can
ignite fuel. **Recommendation:** restore the warning text verbatim.

## I17 — `global_control_panel` is neither implemented nor exposed

**Flask:** *"Show Control Panel on Most Pages"* (`settings/index.html:1439-1442`,
`settings.js:1003-1020`, default `common/defaults.py:55`) renders the full control panel
— mode buttons, Smoke+, PWM, setpoint modal, startup modal — as fixed-bottom chrome on
*every* page (`templates/base.html:154-163, 206-213`).
**React:** the setting is neither read nor offered, and control buttons exist only inside
the dashboard (`ControlButtons.tsx`, rendered from `Dashboard.tsx:288`). On `/settings`,
`/history` and `/wizard` there is no way to stop the grill.
**Disclosed?** No. **Note:** this is *cross-cutting chrome* — the same blind spot that
produced the missing navbar. `plans/2026-07-24-react-app-shell.md` hoists the nav, the
timer bar and the alert strip into the shell but **not** the control panel.
**Recommendation:** fold into the app-shell slice, since that slice is already open.

## I18 — Table edits are provisional and unvalidated (SmartStart / PWM profiles)

**Flask:** persists every add / edit / delete immediately (`settings.js:77-93, 416-432`)
and enforces monotonic ranges, "cannot delete below two profiles", and a locked last-row
temperature.
**React:** `RangeProfileTable.tsx:41-67` defers everything to the tab's Save and enforces
only the ≥2-row rule; out-of-order boundaries are accepted, and table edits are **lost on
tab switch**.
**Disclosed?** No. **Recommendation:** at minimum restore the monotonic check; the
lost-on-switch behaviour deserves its own decision.

---

# MINOR

- **M1 — no page title, no favicon, no PWA manifest.** Flask sets
  `<title>{page} | {grill_name}</title>` (`base.html:31-35`), a favicon (`:26`) and
  `<link rel="manifest">` (`:29`, served by `blueprints/manifest/routes.py`), so PiFire is
  installable to a home screen and identifiable in a tab. `web-react/index.html` has a
  hardcoded `PiFire · React UI (POC)` title and none of the rest; `rsbuild.config.ts`
  injects nothing. Not disclosed.
- **M2 — `page_theme` is settable but inert.** `GeneralTab.tsx:19, 31` reads and writes
  `globals.page_theme`; **nothing in React consumes it** (grep: only the generated types
  and one test). `AppPrefs.tsx:11-19` handles `data-accent` only. Flask reloads into the
  chosen theme (`base.html:10-18`, `settings.js:983-1000`). The control looks broken.
- **M3 — external font dependency.** `web-react/index.html:7-11` loads Barlow from
  `fonts.googleapis.com`. Flask self-hosts everything. On an offline/isolated PiFire the
  React UI silently falls back — and the dashboard is a fixed-metric layout (see C8), so
  fallback metrics can overflow. Not disclosed.
- **M4 — updater release-notes modal dropped.** `settings.globals.updated_message` shows
  post-update release notes on every Flask page (`base.html:165-191`, `:215-222`,
  `static/js/updater_message.js`). Zero React handling. Cross-cutting chrome again.
- **M5 — dashboard card visibility + dashboard selection dropped.** Flask's gear icon
  opens a Dashboard Settings modal that hides/shows each probe card, the status card, the
  time-elapsed card and the history button, persisting to
  `dashboard.dashboards.Default.custom.hidden_cards`
  (`dash_default.html:171-291`, `dash_default.js:936-1006`); the settings page has a whole
  **Dashboard** tab for picking between the Default and Basic dashboards
  (`settings/index.html:1058-1071`, `settings.js:741-749`). React has neither, and
  `touch_screen_mode` (`dash_default.html:8-15`) has no React consumer.
- **M6 — probe connection and battery badges dropped.** Flask shows a per-probe
  connected/disconnected pill and a five-state battery pill with tooltips
  (`_macro_dash_default.html:20-67`, updated `dash_default.js:472-540`). `ProbeStatus` is
  modelled (`types.ts:5-12`); `connected` and `batteryPercentage` have zero consumers.
  For wireless probes this is how you know a reading is stale.
- **M7 — probe ETA dropped.** Flask shows an estimated-time-to-target button per probe
  (`_macro_dash_default.html:123-134`). `ProbeData.eta` modelled, zero consumers.
- **M8 — hopper Refresh / Manager buttons dropped.** `_macro_dash_default.html:358-361`
  (`refreshHopperStatus()` and a link to `/pellets`). `HopperGauge.tsx` is display-only.
- **M9 — history page: duration slider (1-480, `history/index.html:39`), Metrics link
  (`:47`) and the "Grill Inactive" empty state (`:13-16`) dropped.** Per-probe background
  fill colours are configurable in `HistoryTab` but `historyAdapter.ts:63` uses
  `borderColor` only.
- **M10 — per-setting Description column dropped throughout the wizard.**
  `_macro_wizard_card.html:26, 48, 62` and `_macro_probes_config.html:229-235` render a
  Description per dependency/option; `fields/SelectField.tsx` has no description prop,
  `ConfigOptionField.tsx` ignores `option_description`, `probes/DeviceConfigField.tsx`
  ignores `field.description`. Only `PortForm.tsx` kept its hints.
- **M11 — per-step explanatory copy dropped throughout the wizard.** e.g. "Select
  'Custom' if you are using a custom build… prototype only for testing/debug"
  (`wizard.html:69`), "A display is not required… Select None for no display" (`:164-166`),
  "A hopper level sensor is optional" (`:205-206`), probes guidance (`:107-108`), Temp
  Units "can be modified in settings later" (`:131-133`). React steps render an `<h2>`
  and the widget.
- **M12 — non-linear wizard navigation dropped.** Flask's left nav pills jump to any tab
  at any time (`wizard.html:28-35`); React's step indicators are inert `<span>`s and
  navigation is strictly Back/Next (`WizardShell.tsx:199-216`). (The step *indicator*
  itself was ported — that part is fine.)
- **M13 — wizard finish expectation-setting copy dropped** ("may take several minutes…
  will restart the PiFire server software… you can relaunch this wizard from the admin
  menu", `wizard.html:275-277`, `wizard-finish.html:20`).
- **M14 — wizard tables lost their column headers** (Thumbnail/Name/Type/Actions;
  Display Name/Enabled/Type/Device/Port/Profile/Actions) —
  `_macro_probes_config.html:25-32, 281-291` vs `DevicesCard.tsx:112`, `PortsCard.tsx:83`.
  The device "Type" column now shows `friendly_name` instead of the module id
  (`DevicesCard.tsx:122`), changing what identifies a driver.
- **M15 — delete confirmations dropped in settings.** OneSignal device delete modal
  (`settings/index.html:1652-1679`) → immediate delete (`NotificationsTab.tsx:216-223`);
  Apprise non-empty-row confirm (`settings.js:925-941`) → immediate remove
  (`StringListField.tsx:21-27`).
- **M16 — field bounds, tooltips and explanatory notes broadly dropped.** P-Mode 0-9
  (`settings/index.html:343`) vs unbounded (`WorkModeTab.tsx:127-132`); lid-open threshold
  max 80 (`:502`); pellet check time 5-240 (`:1325`); SmartStart "Auger on" capped at 60 s
  in React (`StartupTab.tsx:14`) vs 1000 s in Flask (`:944`); the P-Mode cycle-time
  reference table (`:358-381`); Keep-Warm, auger-rate tuning and MQTT-ID-change
  explanations. Individually cosmetic — but each one becomes an *invisible* rejection
  under C2.
- **M17 — dashboard error modals flattened.** Flask has three distinct modals: Server
  Change Detected (probes reconfigured → reload, `dash_default.html:92-117`, triggered by
  the `ui_hash` comparison at `dash_default.js:160-165`), Server Unresponsive
  (`:119-142`), and Critical Error with a "Event Log" button (`:144-169`). React collapses
  all of it into the non-dismissible `Banners` strip (`Banners.tsx`) plus the
  `ConnectionStatus` screen. Mostly equivalent; the `ui_hash` reload prompt has no
  counterpart, though React's re-render from each socket frame largely covers it.
- **M18 — discovery results lost Refresh/Close controls**
  (`_macro_probes_config.html:533-534, 577-579`) — `DiscoveryPanel.tsx` is inline with no
  re-scan or dismiss. Re-clicking Discover is equivalent.

---

# Disclosed-but-unanswered (surfaced deliberately)

These were flagged by an implementer or reviewer and nobody responded. They are the
category the audit brief asked to be called out.

| Item | Where disclosed | Status |
|---|---|---|
| Chart annotations never drawn | `history-chart-progress.md:206-210` (graded MINOR) | still true; see I9 |
| Disabled probes dropped from chart | `history-chart-progress.md:203-205` | still true; see I9 |
| `/history` unreachable from the UI | `history-chart-progress.md:211-213` | folded into the known no-nav gap |
| Wizard finish confirm table | `wizard-family-inventory.md` §7 | never implemented; see I10 |
| `controlAlive` can stick false, disabling **all** control buttons with no frontend recovery until `control.py` restarts | `dashboard-real-progress.md`, FINAL WHOLE-BRANCH REVIEW, graded "IMPORTANT (phase-2 follow-up, NOT a blocker)" | **still true** — `helpers/dashboard/health.ts`; worth re-grading, since the failure mode is "user cannot stop the grill from the web UI" |
| History page-level Stream toggle dropped as "a UI addition nobody asked for" | `task-hc-minors-report.md:88-91` | **premise is false** — Flask ships it (`history/index.html:41-45`); see I8 |
| History poll interval 5 s vs Flask's 1 s, self-justified as "not a fair comparison" | `task-hc-minors-report.md:44-51` | still true at `4427c5d71a93` `HistoryPage.tsx:25`; never reviewed |
| Navbar stopwatch toggle dropped, with a source comment rationalising it | `task-shell-report.md:111-115`, comment at `shell/TimerBar.tsx:8-10` | **FIXED** by `4427c5d71a93` — the human's own follow-up, not a review catch |
| Controller `numlist` option type unhandled by React (Flask's `_macro_settings.html:51-55` handles it) | `task-2b211-report.md:54, 123` | accepted as a non-issue — `specs/2026-07-22-settings-2b2-widgets-design.md:37`, zero controllers declare it. Latent only |

---

# Pattern notes (why these were missed)

1. **The data contract was ported faithfully; the affordances were not.**
   `helpers/types.ts` models the entire `socket_dash_data` payload. Fourteen of its
   fields have **zero non-test consumers**: `startupCheck`, `startToHoldPrompt`,
   `startupGotoTemp`, `startupGotoMode`, `allowManualOutputs`, `recipeStatus`,
   `lidOpenDetectEnabled`, `lidOpenEndTime`, `modeStartTime`, `startupTimestamp`,
   `pwmControl`, `nextMode`, `displayMode`, `hasDistanceSensor` — plus, per probe,
   `eta`, `connected`, `batteryPercentage`, `hasNotifications`, and all six
   limit/target action flags. **An unconsumed field in a faithfully-ported contract is
   a reliable smell for a dropped affordance**, and it is cheaply greppable. Same for
   `command.ts`: `setPMode` and `timerStart/Pause/Stop` are defined and never called —
   the timer case is the miss the human already found.
2. **Cross-cutting chrome has no home in a page-shaped backlog.** The navbar and timer
   are known. The same gap also swallowed the **global control panel** (I17), the
   **updater message modal** (M4), the **page title / favicon / PWA manifest** (M1) and
   the **theme** (M2) — all of which live in `templates/base.html`, which no page-scoped
   plan ever reads.
3. **A written plan requirement can evaporate silently.** I8 (live history streaming)
   was stated in the plan's own architecture sentence and was never built, never
   deferred, never mentioned again.
4. **A conditional in Jinja is invisible to a schema-driven form generator.** I5, I15 and
   most of M16 are all the same failure: Flask's `{% if %}` show/hide and `min`/`max`
   attributes are UI logic that a "render every field in the schema" port drops
   wholesale. This is systematic, not incidental — worth one sweep of
   `settings/index.html` for every `{% if %}` and every `min=`/`max=`.
5. **The test harness was configured to the one viewport where C8 is invisible.**
   `plans/2026-07-22-dashboard-real.md:1036` pins Playwright to 1280×720.
6. **A component-level test suite cannot tell "built" from "reachable".** C0 is the
   sharpest case: 571 green tests, a report marked COMPLETE, and a navbar nobody can
   click. Every UI slice needs at least one assertion that renders the real `routes` tree
   and reaches the new thing the way a user would.
7. **"Nobody asked for it" / "not needed" claims about a *ported* UI need a citation to
   the Jinja.** I8's rationale asserted Flask lacked a control that Flask ships in the
   template. The reviewer had no cheap way to notice, because the claim was about absence.

---

# Coverage — what I actually checked

**Compared properly (line by line against the Jinja + JS):**

- `templates/base.html` (all chrome blocks), `_macro_timer.html`,
  `_macro_control_panel.html` — vs `App.tsx`, `AppPrefs.tsx`, `main.tsx`,
  `web-react/index.html`, `rsbuild.config.ts`.
- **Dashboard** — `dash_default.html`, `_macro_dash_default.html` (all four macros),
  `dash_default.js` (all 31 functions), `control_panel.js` — vs the whole
  `components/dashboard/` tree, `helpers/dashboard/*`, `helpers/command.ts`,
  `helpers/types.ts`.
- **History page** — `history/index.html`, `history/js/history.js` (skimmed for the
  stream/annotation paths) — vs `components/history/*`, `helpers/history/*`.
- **Settings** — delegated and verified: all 2,378 lines of `settings/index.html`, both
  macros, all 1,021 lines of `settings.js`, `blueprints/settings/routes.py`, vs all 11
  React tabs, `RangeProfileTable`, all six `fields/*`, `settingsApi`/`useSaveSettings`/
  `delta`/`colorFormat`. I independently re-verified C2, I5 and the tab-inventory delta.
- **Wizard** — delegated and verified: `wizard.html`, `_macro_wizard_card.html`,
  `wizard-finish.html`, `wizard.js`, both blueprints, the imported `probeconfig` macros
  and the real `wizard_manifest.json`, vs the whole `components/wizard/` tree. I
  independently re-verified C4, C5 (against the live manifest) and C6.
- Ledgers/specs read for decision-vs-drift: `react-migration-backlog.md`,
  `dashboard-real-progress.md`, `history-chart-progress.md`, `settings-*-progress.md`,
  `toolchain-progress.md`, `wizard-family-inventory.md`, and the four
  `docs/superpowers/{specs,plans}` dashboard/history/shell/wizard documents.

**Not covered / weaker:**

- **The app-shell components got a structural pass, not a content pass.** I verified at
  `4427c5d71a93` that `NavBar`/`TimerBar`/`TimerModal` exist, that the stopwatch toggle
  was restored, and that **nothing mounts them** (C0). I did **not** compare their
  contents line-by-line against `base.html:40-90` and `_macro_timer.html:1-67` — the
  navbar's active-state and grill-name display, the mobile collapse, and the timer
  modal's hours/mins ranges plus its Shutdown-Grill / Start-Keep-Warm checkboxes and
  their mutual exclusion (`static/js/timer.js:261-273`) all still need checking. Given
  that the one thing already found wrong in this slice was a dropped toggle, assume more.
- **The `Basic` dashboard** (`dash/templates/basic/*`, `dash_basic.js`, 795 lines) —
  not compared. It is a second selectable dashboard with click-to-toggle manual outputs;
  React has no dashboard selection at all (M5), so it is dropped wholesale, but I did not
  enumerate its unique affordances.
- **`probeconfig.js`** submit/reload plumbing (lines 154-306) and `probeReducer.ts`
  validation semantics (naming, labels, virtual probes, reposition) — belong to the
  separately-tracked `/probeconfig` surface; not line-by-line verified.
- **Nothing was executed.** This is a static read: no app launch, no browser, no test
  run. The 1280×720 scaling claim (C8) is arithmetic from
  `hooks.ts:15-25`, not an observed screenshot. Worth confirming in a real browser at
  phone width before acting on it.
- **The report-language sweep across ~150 `*report*.md` files** was delegated; its
  results are folded in above where they survived code verification.
