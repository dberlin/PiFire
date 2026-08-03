# React/Flask Divergence Audit — Triage

**Date:** 2026-07-25
**Input:** `docs/superpowers/audits/2026-07-25-react-vs-flask-ui-divergences.md` (9 CRITICAL, 18 IMPORTANT, 18 MINOR)
plus the 4 WAVE-2 items in `docs/superpowers/plans/2026-07-24-flush-accessor-rename.md:169-199`.
**Output:** every remaining finding assigned to a sized, ordered slice.

## Excluded — already in flight, not triaged here

| Finding | Where it is being handled |
|---|---|
| C0 — NavBar/TimerBar built but mounted nowhere; no global navigation | `docs/superpowers/plans/2026-07-24-react-app-shell.md` (landing now) |
| C1 — per-probe notifications / temperature alerts / safety actions | plan being written in parallel |
| C2 — settings save failures are silent | `docs/superpowers/plans/2026-07-25-react-save-failure-surfacing.md` (written 2026-07-25) |

**C0 re-verified as still true in the working checkout at triage time:** `App.tsx:47-79`
is a flat route list with no layout route and no `NavBar`/`TimerBar` import. That is
expected — the shell slice has not landed — but it means every slice below must assume
the shell exists and must not re-derive navigation.

**Note on the C2 exclusion:** the audit says "eleven beneficiaries". It is **nine**.
`UnitsTab` does not use `useSaveSettings` (it goes through `createCommand().setUnits()`,
`UnitsTab.tsx:38`) and `PlatformTab` is read-only by recorded decision. Corrected in the
save-failure plan.

---

## Verification pass — what I re-checked and what survived

The audit discloses that its coverage was uneven (`2026-07-25-react-vs-flask-ui-divergences.md:685-707`):
nothing was executed in a browser, the shell components got a structural pass only, and
the `Basic` dashboard was never compared. So I re-derived every CRITICAL from live code
before triaging.

**All 9 CRITICALs spot-checked. 0 collapsed. 1 needs its framing corrected (C8).**

| # | Claim | Verdict | Evidence I ran |
|---|---|---|---|
| C0 | shell built, mounted nowhere | **HOLDS** | `App.tsx:47-79` — flat routes, no `NavBar`/`TimerBar` import anywhere outside tests |
| C1 | no per-probe notify anywhere | **HOLDS** | `helpers/command.ts` has no notify method; `types.ts:25` `hasNotifications` has fixture+type hits only |
| C2 | `r.message` discarded | **HOLDS** | `useSaveSettings.ts:14-17` returns `r.ok`; `settingsApi.ts:66-84` already returns `{ok,message,data}` |
| C3 | cook time derived from client mount | **HOLDS** | `Dashboard.tsx:51` `useState(() => …)` seeds `cookStart`; `:59` renders `now - cookStart`. `startupTimestamp`/`modeStartTime` appear only in `types.ts:53-54` and `fixture.ts:25-26` |
| C4 | wizard has no exit | **HOLDS** | zero `cancel` hits in `components/wizard/` outside `InstallProgress.tsx`'s local `cancelled` effect flag; **zero `cancel` routes in `blueprints/api_wizard/`**, while Flask has one (`blueprints/wizard/routes.py:71`, dispatched `:265`) |
| C5 | I2C bus is an empty dropdown | **HOLDS — re-verified programmatically** | walked `wizard/wizard_manifest.json`: **all 8** `*i2c_bus_num` dependencies have no `options` key (custom, pcb_2.00a, pcb_3.01a, pcb_pwm, pcb_4.x.x, x86_numato ×2, ft232h_relay) |
| C6 | vendor photos 404 | **HOLDS** | bare filename used as `src` at `ModuleCard.tsx:116`, `probes/DeviceForm.tsx:23`, `probes/DevicesCard.tsx:118` |
| C7 | destructive wizard deletes have no confirm | **HOLDS** | `probes/DevicesCard.tsx:127` and `probes/PortsCard.tsx:99` call delete directly from `onClick` |
| C8 | fixed 1280×720 stage, unusable on a phone | **HOLDS on the facts; framing needs correcting** | `helpers/dashboard/hooks.ts:15-24` `Math.min(innerWidth/w, innerHeight/h)`, applied as one `scale()` at `Dashboard.tsx:67`. **BUT** `hooks.ts:13-14` carries the comment *"The dashboard is authored at a fixed 1280x720 (the on-device touchscreen)"* — so the audit's "not disclosed" is too strong: the intent **is** written down, just in a code comment rather than a spec. This is a **recorded-but-unratified decision**, not silent drift |

I also spot-checked 15 IMPORTANT/MINOR consumer claims, since the whole audit leans on
"field is modelled but has zero consumers". **All 15 held**, by grep excluding `*.test.*`
and `settingsTypes.gen.ts`:

`setPMode` (definition + factory only, `command.ts:26,60` — I1) · `startupCheck`,
`startToHoldPrompt` (types + fixture only — I3) · `lidOpenEndTime`, `recipeStatus`,
`displayMode`, `nextMode`, `pwmControl`, `allowManualOutputs`, `lidOpenDetectEnabled`,
`hasDistanceSensor` (types/fixture/demoData only — I4) · `platform.dc_fan` (read only by
the read-only `PlatformTab.tsx:26,36` — I5) · `global_control_panel`, `sleep_timeout`,
`touch_screen_mode`, `hidden_cards`, `ui_hash` (**zero hits of any kind** in
`web-react/src` — I17, I13, M5, M17) · `page_theme` (written by `GeneralTab.tsx:19,26,31`,
never read — M2) · `connected`, `batteryPercentage` (optional fields on `types.ts:7,9`,
no reader — M6) · `annotations` (fetched into `historyApi.ts:61`, never drawn — I9).

**Conclusion: the audit's factual base is sound.** Its grading is defensible; its one
weak claim (C8's "undisclosed") is corrected above and its one arithmetic error (C2's
"eleven") is corrected in the save-failure plan. Triage below is safe to build on.

**What I did NOT verify, and neither did the audit** — carry these caveats into Slice 1:
the `Basic` dashboard (`dash/templates/basic/*`, 795 lines of JS) is still uncompared by
anyone; nothing has been run in a browser, so C8's phone rendering is arithmetic, not an
observation; and the shell components' *contents* were never diffed against
`base.html:40-90` / `_macro_timer.html`.

---

# Slices, in execution order

Ten slices, 46 findings. Sizes: **S** ≈ one session, **M** ≈ a few sessions, **L** ≈ its
own sub-project.

---

## Slice 1 — Decisions only, no code (S)

**Findings:** C8, M5, plus the audit's own uncovered-area caveats.
**Real gap or nice-to-have:** these are the two places where nobody can write a task
because nobody has decided what the answer is. Blocking, not optional.
**Plan needed?** No plan — a decision, recorded in `backlogs/react-migration-backlog.md`.

| Item | The decision |
|---|---|
| **C8** — fixed 1280×720 uniformly-scaled stage | Ratify or reverse `hooks.ts:13-14`. Either *"the React dashboard targets the on-device 1280×720 touchscreen; phones keep the Flask/mobile UI"* — in which case `backlogs/react-migration-backlog.md:143`'s *"mobile — may be obsolete if React is responsive"* is **wrong and must be corrected**, and the `/mobile` blueprint stays — or the stage reflows, which re-authors `Dashboard.tsx`, `ProbeCard.tsx`, `ControlButtons.tsx` and every fixed-pixel font in `dashboard.css`. **Confirm in a real browser at 390×844 first** — the audit's number is arithmetic. |
| **M5** — no dashboard selection; `Basic` dashboard dropped wholesale | Flask ships two selectable dashboards plus a per-card hide/show modal (`hidden_cards`, `touch_screen_mode` — all three have zero React hits). Decide whether React ships one dashboard forever. If yes, `Basic`'s click-to-toggle manual outputs must be accounted for somewhere (it overlaps the un-migrated **manual** page already in the backlog). If no, someone must first do the comparison **nobody has done** — 795 lines of `dash_basic.js`. |

**Why first:** C8 gates how Slice 3 is authored. Doing dashboard work before this decision
means either wasted responsive work or a second re-author later.

---

## Slice 2 — Wizard: unblock the hardware-config path (M)

**Findings:** C5, C6, C7, C4, I12.
**Real gap:** yes, all five. This is the only slice where a user can be *stuck*: on a
fresh install `DashboardRoute.tsx:27-35` redirects to `/wizard`, and the wizard has no
exit (C4) and a control you cannot fill in (C5).
**Plan needed?** **Yes, its own plan** — it needs a backend endpoint, so it is not a
pure-frontend slice.

| # | Work | Size |
|---|---|---|
| C5 + I12 | **Same fix, do them together.** Replace the `<select>` in `fields/I2cBusPicker.tsx:25,41` and `fields/UsbSerialPicker.tsx:35` with a free-text input + Discover, matching `_macro_probes_config.html:542-552, 587-597`. Same shape at `probes/DeviceConfigField.tsx:103-112`. Verified: no manifest entry supplies `options` for either, so both render empty/truncated today | S |
| C6 | Prefix module images with the PiFire origin + `/static/img/wizard/` at the three `src` sites. **One-line-ish**, but it restores the component-identification mechanism | XS |
| C7 | Confirm dialogs on device/probe delete carrying the cascade text *"All probes associated with this device will also be deleted"*. Reuse `components/dashboard/ConfirmAction.tsx` — already used by `UnitsTab.tsx:62` | S |
| C4 | Cancel affordance + `POST /api/wizard/cancel`. Flask's `_wizard_cancel` (`blueprints/wizard/routes.py:71-74`) clears `first_time_setup` and redirects; port it into `blueprints/api_wizard/`. **Also fix `WizardShell.tsx:159-160`**, which currently tells the user *"You can leave at any point"* — that sentence is false today | S |

**Ordering inside the slice:** C6 → C5/I12 → C7 → C4. C4 last because it is the only one
that touches Python and wants its own review.

---

## Slice 3 — Dashboard: guards, truth, and lost controls (M)

**Findings:** I3, C3, I4, I1, I2, M6, M7, M8, plus **D1** (the `controlAlive` sticky-false
item from the audit's disclosed-but-unanswered table).
**Real gap:** mostly yes. I3 and D1 are safety; C3 and M6 are the UI misreporting state;
I1/I2/I4 are lost capability; M7/M8 are convenience.
**Plan needed?** **Yes, one plan for the whole slice — not one per finding.** Every item
here edits `Dashboard.tsx`, `helpers/dashboard/buttonsForMode.ts`, `helpers/dashboard/*`
or `ProbeCard.tsx`. Splitting them across parallel workspaces guarantees collisions.
**Depends on Slice 1 (C8).**

Ordered inside the slice:

1. **I3 — startup safety-check confirmation** (`buttonsForMode.ts:30-34`). Ignition lost
   its guard while Stop and Shutdown kept theirs (`:28, :81`) — the confirm pattern
   already exists, so this is wiring `startupCheck` / `startToHoldPrompt` /
   `startupGotoTemp` / `startupGotoMode`, all four modelled and all four unconsumed.
2. **D1 — `controlAlive` can stick false**, disabling every control button with no
   frontend recovery until `control.py` restarts (`helpers/dashboard/health.ts`).
   Disclosed in `dashboard-real-progress.md` as "phase-2 follow-up, NOT a blocker"; the
   audit re-grades it and I agree — the failure mode is *"the user cannot stop the grill
   from the web UI"*. **Re-grade to CRITICAL-adjacent and fix here.**
3. **C3 — cook time from `dash.startupTimestamp`**, with Flask's `!= 0` inactive branch
   (`dash_default.js:400-412`), replacing the mount-time seed at `Dashboard.tsx:51`.
4. **I4 — Recipe-mode branch in `buttonsForMode.ts:72-83` first** (today the fallthrough
   offers Smoke/Hold/Smoke+/Shutdown/Stop during Recipe mode — controls Flask
   deliberately hides because they break out of a running recipe; that is a *behaviour
   bug*, not a missing readout). Mode/lid-open countdowns second.
5. **I1 — P-Mode pill becomes a control** (`Dashboard.tsx:309`), or delete the dead
   `setPMode` and record the decision. Do not leave it half-alive.
6. **I2 — Prime amount menu** (10/25/50 g × ±startup) instead of the hardcoded
   `dash.primeAmount || 10` at `buttonsForMode.ts:42`.
7. **M6 — probe connected/battery badges** (`types.ts:7,9`). For wireless probes this is
   the only signal that a reading is stale; higher value than its MINOR grade suggests.
8. **M7 — probe ETA**, **M8 — hopper Refresh / Manager link**. Genuine nice-to-haves.

---

## Slice 4 — Settings: the Jinja `{% if %}` / `min=` / `max=` sweep (M)

**Findings:** I5, I6, I15, I18, M16, M15.
**Real gap:** yes, and it is one systematic failure, not six. The audit's pattern note 4
names it exactly: a "render every field in the schema" port drops Flask's conditional
show/hide and its `min`/`max` attributes wholesale. **Do it as one sweep of
`settings/index.html` for every `{% if %}` and every `min=`/`max=`,** not as six tickets.
**Plan needed?** **Yes, its own plan.**
**Depends on the save-failure plan** (`2026-07-25-react-save-failure-surfacing.md`) —
every finding here currently produces an *invisible* server rejection, and fixing them
without the error surfacing means the next one is invisible too.

| # | Work |
|---|---|
| I5 | Gate on `settings.platform.dc_fan`: hide the whole PWM tab (`SettingsShell.tsx:8`), the Smoke-Plus fan ramp (`WorkModeTab.tsx:204-216`) and the startup DC-fan duty cycle (`StartupTab.tsx:167-174`) on AC-fan builds. Verified: `dc_fan` is read *only* by the read-only `PlatformTab` |
| I6 | Port the clamp loop from `blueprints/settings/routes.py:462-472` into `PwmTab.tsx:78-85` and re-add the `min < max` submit guard. **This is the rejection path the save-failure plan uses as its e2e witness** — coordinate, do not duplicate |
| I15 | Re-add the Startup tab's conditional structure: the Hold-setpoint block only when *After Startup Mode = Hold*, the "Exit Startup @ Temperature" enable switch (0 = disabled, 140 default) and "Always Prime on Startup" (10 g default), plus the `safety.maxstartuptemp`/`maxtemp` bounds |
| I18 | Restore the monotonic-range check in `RangeProfileTable.tsx:41-67` (verified: it enforces only the ≥2-row rule via `canRemove`, and `handleCellChange` clamps per-column but never checks ordering). **The "edits lost on tab switch" half is a separate decision** — Flask persists every add/edit/delete immediately; React defers to Save. Record which model wins before coding |
| M16 | The bounds pass: P-Mode 0-9, lid-open threshold ≤80, pellet check 5-240, SmartStart auger-on 60 s (React) vs 1000 s (Flask) — **note this one disagrees with the schema, which pins `augerontime` to `le=60` at `settings_schema.py:323`; resolve the direction before changing either** |
| M15 | Delete confirmations: OneSignal device (`NotificationsTab.tsx:216-223`), Apprise non-empty row (`StringListField.tsx:21-27`). Same `ConfirmAction` reuse as C7 |

---

## Slice 5 — History page parity (S–M)

**Findings:** I8, I9, M9.
**Real gap:** yes for I8 and I9; M9 is mixed.
**Plan needed?** **Fold into the existing backlog item** — `backlogs/react-migration-backlog.md`
already owns the history surface with a recorded uPlot decision. This is a follow-up
slice on that item, and it needs a plan only because I8 carries a live disagreement.

- **I8 — restore the on-page Stream toggle** (`history/index.html:41-45`, default `"on"`,
  `common/defaults.py:230`). Today the only way to stop streaming is Settings → History →
  save → navigate back. **And settle the interval:** the newest head polls at 5 s
  (`REFRESH_MS`) against Flask's ~1 s. Both the toggle's removal and the interval were
  self-justified in `task-hc-minors-report.md:88-91, 44-51` and never reviewed — and the
  toggle's stated premise ("a UI addition nobody asked for") is **factually false**.
  Also decide whether polling stays or the socket append promised in
  `plans/2026-07-24-react-history-chart.md:7` gets built.
- **I9 — chart annotations and disabled-probe series.** `annotations` is fetched into
  `historyApi.ts:61` and never drawn (verified). Mode-change markers are what tell you
  *where on the curve* Startup/Hold/lid-open happened. Graded MINOR and "logged, not
  fixed" in `history-chart-progress.md:203-210`; **agree with the audit's re-grade to
  IMPORTANT.**
- **M9 —** duration slider (1-480), Metrics link, "Grill Inactive" empty state, and the
  per-probe background fill colours that `HistoryTab` lets you configure but
  `historyAdapter.ts:63` ignores. **The last one is the real bug in this group**: a
  setting that saves and does nothing. The other three are nice-to-haves.

---

## Slice 6 — Settings: missing action affordances (S)

**Findings:** I7, I14, I13.
**Real gap:** I7 yes, I14 partly, I13 yes-but-small.
**Plan needed?** **No — fold into the settings backlog as three independent small tasks.**
They touch three different tabs and can run fully in parallel.

- **I7 — "Send Test Notification"** (`GET /api/set/notify/Test/req/true` — a one-line
  fetch) and the three WLED buttons: **Find Devices** (mDNS discovery), **Push Profiles**,
  **Test Profile**. The WLED *preset grids* are already disclosed as deferred; the four
  action buttons never were. **Discovery is the one that matters** — without it there is
  no way to find a WLED device's address. `notify/wled_profiles.py` and the
  `/api/wled_push_profiles` route (`blueprints/api/routes.py:~230`) already exist backend-side.
- **I14 — the three "use recommended value" buttons** (`metadata[sel].recommendations.cycle`).
  The controller *metadata card* (image, author, homepage, contributors, attributions) is
  a genuine nice-to-have; the recommendation buttons are not — without them the three
  cycle values sit on a different tab with no hint what to set them to.
- **I13 — `display.sleep_timeout`** ("0 = never"), the only web control for blanking the
  attached display. Nothing in React writes it (verified: zero hits). **Coordinate with
  the Qt display DPMS/sway work** before choosing General vs Platform — and note
  `PlatformTab` is read-only by decision, so General is the likely home.

---

## Slice 7 — App chrome, part 2 (S, except I17) (M for I17)

**Findings:** M1, M2, M4, M17, I17.
**Real gap:** M2 and I17 yes; M1 and M4 are nice-to-haves; M17 is near-equivalent already.
**Plan needed?** M1/M2/M4/M17 fold into the app-shell backlog item as follow-ups.
**I17 needs its own decision and probably its own plan.**

The audit's pattern note 2 is the point: *cross-cutting chrome has no home in a
page-shaped backlog*. The shell slice hoists nav, timer and alerts — and stops there.
Everything below still lives in `templates/base.html` with no React owner. **Add a
"chrome" section to `backlogs/react-migration-backlog.md` so the next one has a slot.**

- **M2 — `page_theme` is settable but inert.** Verified: `GeneralTab.tsx:19,26,31` reads
  and writes it, **nothing reads it back**; `AppPrefs.tsx` handles `data-accent` only.
  A control that visibly does nothing is worse than an absent one. **Highest-value item
  in this slice and the cheapest.**
- **I17 — `global_control_panel`** ("Show Control Panel on Most Pages"). Verified: zero
  hits in `web-react/src`, neither read nor offered. Flask renders the full control panel
  as fixed-bottom chrome on every page; in React, `/settings`, `/history` and `/wizard`
  have **no way to stop the grill**. The audit recommends folding it into the app-shell
  slice — **too late, that slice's scope is fixed and landing.** It needs its own item.
  Decide first whether React honours the setting at all or always shows the panel outside
  the dashboard.
- **M1 — page title / favicon / PWA manifest.** `web-react/index.html` has a hardcoded
  `PiFire · React UI (POC)`; Flask sets `<title>{page} | {grill_name}</title>`, a favicon,
  and a manifest that makes PiFire installable to a home screen. Cheap; do it with M2.
- **M4 — updater release-notes modal** (`settings.globals.updated_message`). Zero React
  handling. Only fires after an update; genuinely low priority.
- **M17 — dashboard error modals flattened** into `Banners` + `ConnectionStatus`. The
  audit itself calls it *"mostly equivalent"*, and `ui_hash` (verified: zero React hits)
  is largely covered by re-rendering from each socket frame. **Nice-to-have. Record the
  divergence and close it** unless the `ui_hash` reload prompt turns out to matter.

---

## Slice 8 — Wizard: content and finish flow (M)

**Findings:** I10, I11, M10, M11, M12, M13, M14, M18.
**Real gap:** I10's install-output and error-detail halves, yes. The rest is content and
convenience — but it is a *lot* of content, and the wizard is the one surface where the
user is being asked to identify physical hardware.
**Plan needed?** **Yes, its own plan**, but low priority and strictly after Slice 2.

- **I10 — three separate things, only two of which matter.** *(a)* No install output:
  React polls the same endpoint and renders status + bar, never `status.output`
  (`InstallProgress.tsx` — verified, `output` appears once, at `:25`, as an initial-state
  field that is never rendered). **On a failed install the user is blind. Fix this.**
  *(b)* Error detail thrown away: `wizardApi.ts:65` keeps only `message`, dropping the
  422 bus-conflict `detail` and the 400 missing-selection `sections`, so the user is not
  told *which* bus conflicts or *which* section is unset. **Fix this.** *(c)* No
  confirmation summary on Finish — disclosed in `wizard-family-inventory.md` §7 and never
  built. Nice-to-have.
- **I11 — "system is active" warning on wizard entry**, not at the last step. Flask warns
  on page entry; React lets you configure everything and *then* disables Finish
  (`WizardShell.tsx:118-122`). Cheap, and prevents a wasted pass through the wizard.
- **M10, M11 — per-setting Description and per-step explanatory copy.** These two are one
  job: `SelectField.tsx` has no description prop, `ConfigOptionField.tsx` ignores
  `option_description`, `probes/DeviceConfigField.tsx` ignores `field.description`. Add
  the prop once, then it is data. **Graded MINOR but I would raise it** — this is the copy
  that tells a user what "prototype" means before they pick it.
- **M14 — wizard table column headers**, plus the device "Type" column now showing
  `friendly_name` instead of the module id (`DevicesCard.tsx:122`), which changes what
  identifies a driver.
- **M12 — non-linear wizard nav** (Flask's pills jump to any tab; React is strict
  Back/Next), **M13 — finish expectation-setting copy**, **M18 — discovery Refresh/Close**
  (re-clicking Discover is equivalent). Genuine nice-to-haves.

---

## Slice 9 — Python: accessor-naming WAVE 2 (S)

**Findings:** the 4 items at `docs/superpowers/plans/2026-07-24-flush-accessor-rename.md:169-199`.
**Real gap:** one is a real bug; the rest are naming, except `read_warnings` which is a
real cross-consumer interference bug wearing a naming problem's clothes.
**Plan needed?** **No — the plan already exists and already has these as unchecked boxes.**
Just execute it.

**Fully independent of every other slice** (Python only, zero React files) — run it in
parallel with anything.

1. **`common/common.py:609` — `get_system_command_output()` pops `SqliteQueue("queue_systemo")`
   and silently discards every non-matching entry.** *Real data loss for any concurrent
   consumer.* **This is a bug, not a naming problem — do it first and independently of
   the rename.**
2. **`read_warnings()` → `drain_warnings()`.** `blueprints/dash/routes.py:22` and
   `blueprints/mobile/socket_io.py:208` both call it, so whichever polls first eats the
   other's warnings — the same cross-consumer interference that forced `workers: 1` on
   the e2e suite. 4 sites; fix as *behaviour*, not just naming.
3. **`common/system.py:144` — `get_os_info(persist=True)`.** A `get_`-named function that
   writes the datastore, with the destructive flag **defaulting to true** — worse than
   every Tier-1 case. Split into `probe_os_info()` + `refresh_os_info()`. 3 production
   call sites, all taking the default.
4. **`common/settings_migration.py:37` — `read_settings_file(init=True)`** writes files
   and a warning. Called from `common/datastore.py:279`. Smallest of the four; mostly a
   comment fix plus a rename.

---

## Slice 10 — Regression guards (S)

**Findings:** none directly — this is the audit's pattern notes 1, 5 and 6 turned into
checks. **Nice-to-have, but the cheapest insurance in the whole list**, and it is what
stops slice 11 from existing.
**Plan needed?** No. Three small tasks.

1. **An unconsumed-field check.** The audit found 14 payload fields and 4 per-probe fields
   with zero non-test consumers, and *every single one* turned out to be a dropped
   affordance. That is a perfect signal and it is cheaply greppable. Make it a script or
   a test that fails on a new unconsumed field in `helpers/types.ts` / `command.ts`,
   with an explicit allowlist for deliberate cases.
2. **A reachability assertion per UI slice.** C0 is the sharp case: 571 green tests, a
   report marked COMPLETE, and a navbar nobody could click. Every slice needs at least one
   test that renders the real `routes` tree (`App.tsx` already exports `routes` for this
   purpose, `:47`) and reaches the new thing the way a user would.
3. **A second Playwright viewport.** `plans/2026-07-22-dashboard-real.md:1036` pins
   1280×720 — the one viewport where C8 is invisible. Add a phone project once Slice 1
   decides what the phone is supposed to show.

---

# Order summary

| # | Slice | Size | Findings | Own plan? |
|---|---|---|---|---|
| 1 | Human decisions, no code | S | C8, M5 | No — record in backlog |
| 2 | Wizard: unblock hardware config | M | C5, C6, C7, C4, I12 | **Yes** |
| 3 | Dashboard: guards, truth, lost controls | M | I3, D1, C3, I4, I1, I2, M6, M7, M8 | **Yes** (one plan, not nine) |
| 4 | Settings: `{% if %}` / bounds sweep | M | I5, I6, I15, I18, M16, M15 | **Yes** |
| 5 | History page parity | S–M | I8, I9, M9 | Fold into the history backlog item |
| 6 | Settings: missing action affordances | S | I7, I14, I13 | No — 3 parallel tasks |
| 7 | App chrome, part 2 | S (+M for I17) | M2, M1, M4, M17, **I17** | Fold, except I17 |
| 8 | Wizard: content and finish flow | M | I10, I11, M10-M14, M18 | **Yes**, low priority |
| 9 | Python: accessor WAVE 2 | S | 4 items | No — plan exists, execute it |
| 10 | Regression guards | S | — | No — 3 small tasks |

**Parallelism:** Slice 9 is Python-only and runs alongside anything. Slices 2 and 4 touch
disjoint trees (`components/wizard/` vs `components/settings/`) and can run concurrently in
isolated jj workspaces. **Slice 3 must run alone** — everything in it edits the same four
dashboard files. Slice 4 must be sequenced **after** the save-failure plan lands. Slice 1
gates Slice 3.

**If only three things get done:** Slice 2 (a user can be stuck), Slice 3 items 1-3
(ignition guard, `controlAlive`, cook timer), and the save-failure plan already written —
because Slice 4 is not worth starting until rejections are visible.
