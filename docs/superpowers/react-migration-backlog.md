# React Migration — Backlog

Durable backlog for the `web-react/` replacement of the Flask/Jinja UI.

**Reconciled 2026-07-25** against live code and a real browser. Previous
revisions listed several items as open that had shipped, and — more
importantly — listed nothing that only a running browser could reveal, because
until this date nothing had been run in one.

This file previously lived in `.superpowers/sdd/`, which is gitignored scratch
that `git clean -fdx` destroys. It is tracked now. Three audit documents cite
it by path and line number; those citations point here.

---

## SHIPPED

### Pages and chrome

- **Dashboard** (`/`) — live socket data.
- **App shell / global navigation** — navbar with all six destinations
  (Recipes, Events, Admin rendered disabled, being unported), shared layout
  route, timer bar + modal, and the `Banners` alert strip hoisted out of
  Dashboard. The shell owns the single socket subscription and passes it down
  through Outlet context; a structural test enforces the one-call rule, because
  a second socket fails no other test — every component test mocks the hook.
- **History page** (`/history`) — uPlot chart, minutes window, drag-zoom,
  reset, cursor tooltip, CSV export link, **plus the saved-cook list** (2026-07-26:
  pagination, sort, per-page, upload, download, delete), which Flask also renders
  on this page rather than on one of its own.
- **Cook-file browser** (`/history` list + `/cookfiles/:filename`) — SHIPPED
  2026-07-26 (`plans/2026-07-26-react-recipes-cookfile.md`, plan 1, 14 tasks).
  The detail route carries the chart with mode-change annotations, the events
  table with totals and per-event detail, both CSV exports, title and probe-label
  editing, comments with attached photos and a lightbox, the media grid
  (upload/delete/thumbnail), and the Attempt Conversion / Attempt Repair prompt
  for an archive that will not open. Backed by a new `blueprints/api_files/`
  surface (16 endpoints) that resolves every client-supplied filename through one
  realpath-containment helper — the legacy `/cookfile` blueprint takes a
  filesystem path from the client and uses it unvalidated, which is why none of
  its routes were reused.
- **Settings** — 12 tabs: General, Units, Pellets, Safety, WorkMode, Pwm,
  Startup, Controller, History, Notifications, Platform (read-only), plus the
  shared `SaveBar`.
- **Pellets manager** (`/pellets`) — the pellet inventory manager: current
  load-out, hopper level, usage, brand/wood/rating/size vocab tables, profile
  add/edit/delete, and the pellet log. The eight actions that existed only as
  Socket.IO handlers were extracted to `common/pellets_actions.py` so both
  transports share one implementation, and new `GET`/`POST /api/pellets`
  endpoints were added — no path read or wrote the pellet archive over REST
  before. The page is socket-driven off `socket_pellet_data`, which
  `useLiveState` now subscribes to, so it needs no polling and no refetch after
  a write.
- **Probe configuration** (`/settings/probes`) — the live probe map: devices,
  ports, all five hardware-discovery flows. The editing surface is the wizard's
  shipped `probeReducer` + `DevicesCard`/`PortsCard`/`DeviceForm`/`PortForm` and
  every discovery picker, reused **in place** — no move, no rename, no new
  editing logic. What was new is the delivery path. Two REST endpoints were
  added because none existed with the right semantics: `GET /api/probe_modules`
  (the probes slice of `wizard_manifest.json` plus a per-module
  `requires_install` flag) and `POST /api/probe_map`, which applies a whole map
  behind four guards — shape (400), `mode == Stop` (409), a module whose
  dependencies only the wizard's installer can install (422), and a full
  cross-subsystem I2C bus-kind check against LIVE settings (422). On success it
  regenerates `history_page.probe_config` exactly as `wizard.py:230` does and
  raises a new `probe_map_update` control flag, which makes the running
  controller rebuild its probe devices in place through
  `ProbesMain.update_probe_map()` — an existing method with zero callers until
  now. Deliberately no `restart_scripts()` anywhere: an in-process rebuild
  keeps the whole `os.system`/`subprocess` neutralization burden out of every
  test that can reach a settings tab. `wizard.css`'s probe-editing vocabulary
  was extracted to `components/wizard/probes/probes.css`, imported by the two
  cards so it travels to any surface that renders them.
- **Wizard** (`/wizard`) — all steps functional: welcome, grill platform,
  probes (devices + ports), display, distance, finish, install-progress
  polling, and an Exit control. Functionally complete; styling is being
  finished — see open item 1.

### Behaviour and correctness

- **LTTB downsampling** (`file_mgmt/downsample.py`) — replaces the naive every-Nth
  decimation in `prepare_chartdata`, which could drop a lid-open dip or an
  overshoot spike entirely at the default budget. Benefits the Flask UI too.
  Empty history now emits an empty series rather than a fabricated zero.
- **Save-failure surfacing** — `useSaveSettings` returns a `SaveStatus` rather
  than a bare boolean; all nine saving tabs render errors inline and withhold
  the success marker. Its e2e witness became unreachable when the guards sweep
  clamped the very value the spec raised to provoke a rejection. **Decided
  2026-07-25: the `PwmTab` unit test is the accepted coverage** — no
  fault-injection e2e. See the note at the top of
  `plans/2026-07-25-react-save-failure-surfacing.md`.
- **Per-probe notifications (slice 1)** — bell per probe card, target modal,
  arm/disable, single-write non-clobber against the other two notify entries
  sharing each label.
- **Notify writes are addressed per entry** (2026-07-26) — closes the one
  regression the control-write delta conversion flagged and accepted. Posting a
  whole `notify_data` array is applied as `notify.replace`, so a client that
  built it from a queue-blind read reverts every entry it did not mean to touch
  — a timer armed from the shell while a target modal was open, most visibly. A
  whole array cannot say WHICH fields its writer meant, so the drain cannot tell
  an intentional deletion from an omission. `POST /api/control` now also takes
  `notify_updates`: a list of `{label, type, fields}` mapped 1:1 onto the
  `notify.set` ops the drain applies against live state, shared by the REST and
  Socket.IO doors via `notify_ops_from_post()` so they cannot drift, with a 400
  naming a malformed payload instead of the generic 201. Every in-repo client
  converted: `saveTargetEdit` (was GET-edit-POST, now ONE post and no read),
  `dash_default.js` + `dash_basic.js` `setNotify`/`cancelNotify`, and
  `socket_io.py::_update_notify_data` — which already received per-entry intent
  from the mobile app and threw it away by rebuilding the array.
- **Settings guards sweep** — `NumberField` bounds enforced on blur, `dc_fan`
  gating, PWM min/max guard with dependent clamps, Startup conditional
  structure, monotonic range boundaries, delete confirmations.
- **Timer** — server-computed end time, so a skewed browser clock cannot arm an
  already-expired timer; options and start sent as one control write. The
  expiry action is one choice, not two flags: the backend runs
  `if shutdown … elif keep_warm`, so offering both silently dropped keep-warm.
- **Manual output control** — power / igniter / auger / fan relays plus DC-fan
  duty. It is NOT a page: `buttonsForMode` gains a `Manual` branch that turns
  the dashboard's mode-button row into the output panel, with a `PwmEntry`
  overlay for duty cycle. That also settles the loose end the one-dashboard
  decision created — `Basic`'s click-to-toggle outputs now have a home. Driven
  end to end against real relays by `roundtrip.spec.ts`. This sat in the
  un-migrated list until 2026-07-25 purely because its plan's checkboxes were
  never ticked; the code had shipped.

### Bugs found and fixed this cycle

- **Socket delivered no initial payload to late-connecting clients.**
  `handle_connect` passes `force=True`, but `listen_app_data` honours it only on
  the call that starts the broadcast thread; the loop then re-emits only on
  change. A client connecting while the thread already ran got nothing, and on
  an idle grill "nothing" was unbounded. The Jinja pages hid this by rendering
  current values into the HTML. Fixed with a direct `to=<sid>` emit on connect.
- **Notify writes went to the wrong origin.** `targetUrl` is
  `PUBLIC_PIFIRE_URL || "http://localhost:5000"` — absolute, so `ConnectionStatus`
  has something readable to display. Every other module uses
  `PUBLIC_PIFIRE_URL || ""`, which stays same-origin. Notify was the first
  feature to *fetch* with `targetUrl`: CORS-blocked in dev, and in production
  aimed at localhost on the viewer's own machine.
- **`smartstart.augerontime` ceiling was 60**, against Flask's 1–1000
  (`settings/index.html:944`). An existing install with a legal value above 60
  failed schema validation, and the React field silently clamped it.
- **Dashboard fallback was non-deterministic across installs** — `os.listdir()`
  ordering decided which dashboard an invalid `current` fell back to. Now falls
  back to the named `Default`, with a sorted listing on top.
- **"Attempt Re-ignite" was unreachable from BOTH Flask dashboards** (found
  2026-07-26 while converting `setNotify` to per-entry writes). `setNotify` read
  the `_low_limit_shutdown` checkbox for the `reignite` flag as well as for
  `shutdown`, in `dash_default.js` and `dash_basic.js` alike. The two boxes are
  mutually exclusive (each unchecks the other), so the bug broke the feature in
  both directions: ticking "Attempt Re-ignite" sent `reignite:false`, and ticking
  "Shutdown PiFire" armed `reignite` as a side effect — where it was then dead
  anyway, because `notify/notifications.py:141-167` runs
  `if shutdown … elif keep_warm … elif reignite`. Witnessed by two Playwright
  tests in `tests/web/test_page_dashboard.py` that drive the real modal; both
  were confirmed RED against the old selector before the fix landed.
- **High/low-limit "Shutdown PiFire" could never fire.** `check_notify`'s
  shutdown/keep-warm tail gated on `not notify_data[index]["req"]`, and only the
  `probe` branch ever clears `req` — reaching a target is one-shot. A
  `probe_limit_high`/`probe_limit_low` entry stays armed for the whole cook and
  re-arms via `triggered`, so the gate was permanently False and the checkbox
  rendered beside every limit alert did nothing. Fixed by asking `triggered` for
  limit entries and `not req` for the one-shot ones — deliberately NOT by
  copying the probe branch's `req = False`, which would disarm the alarm for the
  rest of the cook. `reignite` already used `triggered`, which is why it worked.
- **`read_cookfile`'s version gate compared semver components independently**
  (`file[0] >= min[0] and file[1] >= min[1] and …`), so against the shipped
  minimum of 1.5.0 a file written as 2.4.0 failed the `4 >= 5` term and was
  reported as an OLDER format — routing a NEWER file to the repair/upgrade
  prompt that rewrites it backwards. Now uses `semantic_ver_is_lower()`, which
  was already in `common/common.py`.
- **`prepare_csv`/`prepare_metrics_csv` were broken under any non-default
  history folder.** Both did `filename.replace("./history/", "")` then
  `"/tmp/" + …`; the replace only matched the default `HISTORY_FOLDER`, so any
  other folder produced a path under a directory that does not exist and
  `open()` raised. That is why the three legacy `dl_cookfile`/`dl_eventfile`/
  `dl_graphfile` branches had never had a single test — every fixture uses a
  temp folder, i.e. the broken case. One `os.path.basename()`-based helper now
  serves both, which also stops a `../..` form value escaping `/tmp` (the new
  traversal test was writing `/etc/passwd-Pifire-Export.csv` before the fix and
  failed only on permissions). All three `dl_*` branches now have coverage.
- **`thumbSelected` wrote any string it was handed** into `metadata.thumbnail`,
  so a stale tab could leave a permanently broken `<img>` in the cook-file list
  with no UI path back — the picker only ever offers assets that exist.
  `/api/files/cookfiles/thumbnail` already validated this; the check moved down
  to `file_mgmt/media.py` as `set_thumbnail_checked()` and both doors share it.
- **The comment-asset toggle inferred its direction from a client-sent `state`
  string.** With a stale modal both arms failed their guard, nothing was
  written, and the handler still answered `OK` — which `cookfile.js` took as
  licence to flip the thumbnail locally, leaving the view showing the opposite
  of what was stored. The server now decides from presence in
  `comment["assets"]` and returns the authoritative `selected`, which the JS
  renders instead of guessing. An unknown `commentid` reports `ERROR` rather
  than a false success.
- **Factory reset left the whole pellet database in place.** Pre-SQLite,
  `os.system("rm pelletdb.json")` WAS the mechanism; removing that dead line
  preserved the accident it left behind. Human ruling: factory reset clears the
  pellet database. Both `_admin_setting_factorydefaults` and the Socket.IO
  `factory_defaults` (which never reset pellets at all, not even pre-SQLite) now
  call the shared `common/pellets_actions.clear_pellet_db()`. The control reseed
  is untouched — it is a deliberate explicit delta.

---

## OPEN

### 1. Wizard styling — DONE (one human checkpoint outstanding)

**This item's original text was wrong by the time anyone read it.** It claimed
"there is no `wizard.css`". There is: `web-react/src/components/wizard/wizard.css`,
624 lines, committed 2026-07-25. It was written during the same day the backlog
was reconciled, so the entry described a state that had already passed. Do not
trust a count in this file without re-measuring — see the measurement note below.

What was true: the wizard shipped functionally complete and entirely unstyled,
every `pf-*` class it used resolved to nothing, and at 1280×720 it rendered as
raw HTML with step names running together as
`WelcomeGrill PlatformProbesDisplayDistance / HopperFinish`. Every wizard unit
test and all four wizard e2e specs passed against that, because they assert on
text and roles, never on whether a class is defined.

Executed from `docs/superpowers/plans/2026-07-25-react-wizard-styling.md`
(8 tasks). Tasks 1–7 had in fact already been implemented before that run began;
the verification pass that followed found two real defects. One: the
class-coverage guard had been **silently disarmed** — its regex treated CSS
comment text as selector text, so a class counted as "declared" because prose in
a comment mentioned it, and deleting both `.pf-module-notes` rules left the
guard green. Fixed, plus a test that guards the guard. Two: duplicate
`[role="alert"]` rules in descending specificity.

**Still outstanding: Task 8, the human visual checkpoint.** 8 of its 12 items
were pre-screened from clean screenshots; items 10–12 and the type/colour
judgement need a person. Item 7 (the no-photo fallback) is unreachable — all 62
manifest modules have images — and cannot be performed as written.

**How the original gap was missed:** no test in the project asserted that a class
it uses is defined anywhere, and until 2026-07-25 nobody had opened the wizard in
a browser.

**Measuring "undefined classes" honestly.** A naive
`grep -o 'pf-[a-z0-9-]*'` over `.tsx` and `.css` overcounts badly — as of this
writing it reports 257 used / 215 defined / 44 undefined, but the "undefined"
list is mostly noise: CSS custom properties (`--pf-btn-h`, `--pf-col-w`,
`--pf-gauge-num`) match the same pattern, and template-literal prefixes
(`pf-badge-`, `pf-banner--`) are never whole class names. Any real number has to
exclude `--`-prefixed tokens and resolve dynamic class construction.

### 2. Dashboard reflow — DONE (unvalidated on real hardware)

Plan: `docs/superpowers/plans/2026-07-25-react-dashboard-slice.md` (14 tasks),
all implemented. The fixed 1280×720 uniformly-scaled stage is reversed and the
dashboard is responsive; the committed landmark baseline
(`tests/e2e/dashboard-layout-1280x720.json`) is unedited and still passes, which
is the evidence that desktop rendering did not move. Stage scale 0.9222.

The verification pass found that **3 of the 5 reflow gates were vacuous** —
forced back onto the pre-change scaled layout at 390px, they passed against the
very thing they exist to reject, because `getBoundingClientRect()` is
post-transform and `getComputedStyle().fontSize` reports the authored 66px while
the user sees ~20px. Now 5 of 5 bind.

It also found the **800×480 panel band had no coverage at all** — the width a
real PiFire device renders. Columns never wrapped (centre column crushed to
71px, a 320px gauge into a 69px SVG), the control row overflowed so the Stop
button showed nine pixels of itself, the hopper level bar collapsed to 2px, and
"Shutdown" spilled outside its button. The last two were invisible to every
geometric assertion — only reading the screenshot found them. A new `panel`
Playwright project gates all four.

**Nobody has held this on a real panel or phone.** The values are measured
floors, not design choices.

Also ratified: **one dashboard forever.** No picker, no `hidden_cards`, no
`touch_screen_mode`, no port of `Basic`. Consequence to carry: `Basic`'s
click-to-toggle manual outputs now have no home in React — that capability
belongs to the **manual** page item below. This does not retire the Flask
picker; it only says React will not grow one.

### 3. Timer clobber — DONE (closed at the source by control-write deltas)

Every writer now queues an intent DELTA — named members, or an ordered OP the
drain evaluates against live state — instead of the whole control snapshot it
happened to read (`common/control_delta.py`). `control["timer"]` and
`control["notify_data"]` are expressible ONLY as ops, which is what stopped the
drain guessing: `timer.start_or_resume`, `timer.clear`, `notify.set`.

- stop-then-pause in one cycle leaves the timer stopped, and a flag write after
  a start no longer zeroes it (`test_control_delta_seam.py`,
  `test_process_command_golden.py`).
- `TimerBar`'s client-side guard against that pair is deleted — the workaround
  went with the fix.
- The whole-`notify_data` clobber is closed too, on both UIs: see
  "Notify writes are addressed per entry" under SHIPPED.

Remaining: `POST /api/control` still ACCEPTS a whole `notify_data` array and
applies it as `notify.replace`, because third-party clients speak it. No
in-repo client does. Documented as lossy at both doors.

### 4. The errors blob is write-only from the web tier — DONE 2026-07-26

**Fixed as a non-sticky liveness signal, not a clearing endpoint.**

The bug: `_check_control_status` (`blueprints/mobile/socket_io.py`) appended
"The control process did not respond…" to the errors blob. `read_errors()` is a
plain non-destructive read — unlike `warnings` on the very same payload, which
drains and self-heals frame to frame — and its only clearer, `flush_errors()`,
has exactly one production caller, `control.py` at boot. So one missed answer
rode every `socket_dash_data` frame until the control process restarted, and no
route, socket action or API command could clear it.

**Which fix, and why.** The blob's other writers decide it. Every one of them
(`controller/runtime/devices.py`, `runner.py`, `controller.py`,
`common/extra_installer.py`) is the control process or one of its subprocesses,
and each records a failure that already happened and cannot un-happen — a
display that would not load, a dependency install that failed. The
`flush_errors()`-at-control-boot lifecycle matches exactly that: "errors
accumulated since the control process started." Liveness is the opposite kind of
fact: about right now, observed by the web process, false the moment control
answers again. It was misfiled, and once filed correctly there is nothing
durable left to clear, so no clearing endpoint is needed.

The verdict now lives in `socket_io._control_alive` — process-local, in memory,
overwritten by every check in both directions — and `_get_dash_data` composes
`common/app.py::CONTROL_DOWN_ERROR` into each payload from it. Deliberately not
persisted anywhere: persisting a statement about "right now" is what made this
sticky. `dash_page` already worked this way (it appends to the local list it
hands the template); the duplicated user-visible string is now one constant,
because the React dashboard identifies the condition by matching a substring of
it (`web-react/src/helpers/dashboard/health.ts`).

Consequence worth keeping: **the errors blob is now read-only from the web
tier**, giving it a single owner and removing a cross-process read-modify-write.

Pinned by `tests/web/test_control_liveness_not_sticky.py` (6 tests, driving the
real consumers), including that a durable control-process error is untouched in
both directions — a guard against "fixing" this by deleting from the blob.

The Recheck control in `controlHealth.ts` is **kept**: the payload can still be
up to one 30 s poll interval stale, which is an independent reason for an
on-demand probe. Its comment, which described the stickiness and the
`get_system_command_output` queue race as live problems, is corrected.

*The queue race is not a separate open item.* `get_system_command_output` no
longer discards non-matching entries — it peeks and pushes back what is not
its own (triage Slice 9 item 1, `33135e4aed48`,
`tests/unit/common/test_system_command_output_queue.py`). The "can be written on
a healthy system" clause of this entry was already stale when it was written.

### 5. Tailwind v4 migration — DONE 2026-07-27, one recapture outstanding

**Closed out 2026-07-27.** All 20 tasks are shipped and the human checkpoint has
signed off. Accepted visual differences, the four defects the walkthrough found
that every gate had passed, and two deviations from the plan's own baseline
rules are recorded in
`docs/superpowers/audits/2026-07-27-tailwind-migration-diffs.md`.

The spec's status line was **not** updated, though Task 15 Step 5 asks for it:
`docs/superpowers/specs/` is treated as an immutable historical record in this
repo, and that standing rule wins over a step in a plan. This entry is the
status.

**Outstanding:** `bun run baseline:capture`. The reference still describes the
pre-preflight tree, so `test:e2e:fidelity` is 47 failed / 58 passed by design.
Recapturing was gated on the sign-off, which it now has.

Spec: `docs/superpowers/specs/2026-07-25-tailwind-v4-migration-design.md`.
Ratified: token bridge (`@theme` + `@apply`, `pf-*` names and JSX survive), gate
extended to every page at 1280×720 and 390×844, implementation gated on the
wizard-styling and dashboard-reflow slices merging first.


Move `web-react/`'s six hand-written stylesheets (2,603 lines: `theme.css`,
`dashboard.css` 1149, `wizard.css` 624, `settings.css` 344, `shell.css` 315,
`historyChart.css` 61) onto Tailwind v4 via the Rsbuild integration
(<https://rsbuild.rs/guide/styling/tailwindcss>).

Hard requirement: **visually identical before and after**, except where the
"before" is clearly broken. The gate already exists in embryo and must be
generalised rather than reinvented — `tests/e2e/layoutBaseline.ts` +
`dashboard-layout-1280x720.json` capture a per-landmark box plus
`fontSize`/`fontWeight`, compared with `BOX_TOL = 2`px and an `EXACT` override
table. It is deliberately **not** a `toHaveScreenshot()` gate: `index.html`
loads Barlow from `fonts.googleapis.com`, so pixels depend on the network and
the host font stack, and masking the volatile regions would mask exactly the
typography the gate exists to protect.

**Unblocked 2026-07-26.** The wizard-styling and dashboard-reflow slices — which
were rewriting the two largest stylesheets and would have collided with this —
are both merged. Re-measure the line counts above before starting; they were
taken before those two landed.

#### DONE 2026-07-27 — Tasks 1-14 shipped, Task 15 (human) outstanding

Plan: `plans/2026-07-26-tailwind-v4-migration.md`. All fourteen implementation
tasks landed; the visual guarantee held. **The 38 pre-Tailwind baselines are
byte-identical before and after** (0 deletions across the whole diff — verified
independently in the main checkout, not just reported), and the fidelity gate is
**97 passed**. `bun run baseline:capture` was never re-run; Task 5 added 5 new
baseline files that had never existed, for 43 total. Suite 1258 → 1262; CSS
bundle 41,207 → 53,871 bytes raw / 10,555 gzip, with no duplicated theme layer.

#### Tasks 16-20, 2026-07-27 — the plan's gaps closed, and preflight adopted

Five follow-on tasks, none of them in the plan.

- **16 — cook-file fidelity baseline** (`e27f70bb`). `cookfiles.css` styled a
  page absent from `pageSpecs.ts`, so it had no gate. Two new specs
  (`cookfile-list` on /history, `cookfile-detail`) and four new baseline files,
  captured while the emitted CSS still matched the pre-Tailwind reference. A
  per-spec `PageSpec.stubs?` hook keeps the list fixture out of the shared
  `stubApi`, so the older `history` baselines stay untouched.
- **17 — Tailwind preflight adopted** (`08d299bc`). The reset applies for real;
  the app's own rules now declare the type and spacing they had been taking from
  the UA. An earlier attempt (`88efe5da`) neutralised preflight with a
  `@layer base` shim of `revert` declarations to keep the baselines matching, and
  was rejected: a revert that protects nothing but a computed string is not worth
  having. Five surfaces needed real fixes — cook-file thumbnails, the Platform
  tab's hardware list, device-table buttons, comment thumbnails, and link/button
  text-decoration. Everything else moved 5-30px and was deliberately left.
- **20 — static inline styles moved into the style layer** (`0071f55d`). The
  repeated download-link and visually-hidden-input treatments became named `pf-*`
  rules; one-offs became utility classes; dynamic styles stayed inline. Also
  dropped the gauge arc's drop-shadow — `Gauge.qml`'s only glow is the pulsing
  disc behind the arc, which `.pf-dash-gauge-glow` already draws.
- **18 / 19 — the last two stylesheets** (`05c749ef`, `560e0f4f`). `probes.css`
  and `cookfiles.css` authored with `@apply`. No stylesheet in the app is
  hand-written now.

**Outstanding:**

1. **The baselines now encode the pre-preflight rendering**, so
   `bun run test:e2e:fidelity` is 47 failed / 58 passed by design. The "never
   recapture" rule was premised on preflight changing nothing; that premise is
   gone. The reference has to be re-established deliberately, **after** the human
   visual checkpoint — recapturing first would bake in whatever they would have
   objected to.
2. **Task 15 human visual checkpoint.** Evidence pack is in the SDD ledger, and
   the preflight before/after screenshots are the substantive input to it.
   Carries the known 390×844 General-tab hint wart (52px column forcing a 187px
   row against 36-38px neighbours) — pre-existing and deliberately not fixed
   during a conversion.
3. **Two things found and left alone**, both pre-existing: `.pf-settings-back`
   ("Back to history") is dim text with no affordance, and the class is shared
   with a `<button>` that should not be underlined, so it needs a decision rather
   than a patch. The cook-file table runs its Download button off the right edge
   at 390px.
4. **`history-*.json` is machine-dependent.** `/history` embeds the cook-file
   list, and the older `history` spec measures the developer's real cook files
   through the demo server's `/api` proxy. It is the one baseline of the 43 that
   would not reproduce on another machine.

### 6. Remaining audit findings — SUPERSEDED by item 10

`audits/2026-07-25-audit-triage.md` and
`audits/2026-07-25-react-vs-flask-ui-divergences.md`. This item used to say
"roughly 40 findings … the rest are untouched", which was guesswork. The
2026-07-26 sweep read both audits item by item and checked each against live
code: **all 9 CRITICALs and 12 of 18 IMPORTANTs are done.** The genuinely
remaining findings are enumerated in item 10 rather than counted in the
abstract here.

### 6a. Hopper card should link to /pellets — DONE 2026-07-26

Bootstrap's hopper card carries a "Manager" link. The dashboard slice asserted
there must be **no** such link in React; the pellets plan recorded the shortcut
as owed and assigned it to the dashboard slice. The two plans contradicted each
other and neither shipped it. **Ruling: the link exists, because it exists in
Bootstrap.** `/pellets` shipped 2026-07-25, so the target is real. Small,
self-contained, unblocked.

- [x] Shipped 2026-07-26: a router `<Link to="/pellets">Manager</Link>` in
      `HopperGauge`'s footer (`.pf-dash-hopper-link`, existing `pf-*` tokens, no
      new colour); the dashboard slice's "offers no link out" assertion was
      flipped to assert the link exists and points at `/pellets`, plus one
      pinning that it navigates in-app rather than reloading the document.

### 7. Accessor rename WAVE 2 — DONE 2026-07-26

This entry was stale. It claimed four remaining items; three were already
finished when it was written, by task ACC (`.superpowers/sdd/task-acc-report.md`).
Checked against live code, not the docs:

- `read_warnings()` → `drain_warnings()` — **already done.** Both exist in
  `common/datastore_accessors.py`, `dash_page` is `drain_warnings()`'s only
  caller, and `tests/web/test_warnings_cross_consumer.py` pins it. The
  cross-consumer bug this entry described as open had already been fixed.
- `get_system_command_output()` discarding other consumers' queue entries —
  **already done.** It peeks and pushes back;
  `tests/unit/common/test_system_command_output_queue.py`. (Backlog item 4 also
  cited this as a live cause; that too was stale.)
- `read_settings_file(init=True)` — **already done**, docstring only and
  deliberately so: `init` defaults to False, all three production callers pass
  it explicitly, and a rename would churn 24 references for no behavioural gain.
- `get_os_info(persist=True)` — **the one that really was outstanding**, and the
  only one closed by this pass. Task ACC skipped it as "already fixed" because
  the old CWD-relative `os_info.json` write was gone, reasoning that no caller
  just wants to read. `board-config.py::rpi_config_write` is that caller: it
  reads `VERSION_ID` to choose a config.txt path and took the `persist=True`
  default. Split into `probe_os_info()` + `refresh_os_info()` as the plan
  specified, which also let a dead `tests/conftest.py` workaround go.

Details and per-item commits: `plans/2026-07-24-flush-accessor-rename.md`
"WAVE 2", now all checked.

**Lesson, since it keeps recurring:** three of four items here were closed
before the entry describing them as open was read. Verify against live code
first; the docs drift.

### 8. Un-migrated Flask pages

Roughly ordered by daily-use value:

- [x] **pellets** — SHIPPED 2026-07-25 (`plans/2026-07-25-react-pellets-page.md`,
      13 tasks). Listed here as open until 2026-07-26 purely because this entry
      was never struck; see the SHIPPED section for what landed.
- [ ] **admin** — restart/reboot/shutdown, backups. Every action shells out, so
      the tests for it MUST neutralize `os.system`/`subprocess` before anything
      runs — an `is_real_hardware()` flag is not enough, and this repo has
      really rebooted the developer's machine twice that way.
- [ ] **recipes** — recipe editor. **PLANNED 2026-07-26** as plan 2 of
      `plans/2026-07-26-react-recipes-cookfile.md` (outline only, 17 tasks); the
      **cookfile** half of this entry SHIPPED the same day — see the SHIPPED
      section. Correction to what this line used to say: recipes and cookfile do
      **not** "share a data model". They share a ZIP container and a listing
      shape, and nothing else — different JSON member sets, different metadata
      keys, no page in common, and **zero** overlapping action names between the
      two dispatch tables. The listing endpoint this line said "does not exist
      yet" now does: `GET /api/files/recipes` shipped alongside
      `GET /api/files/cookfiles` from one handler, so the recipe browser's data
      layer is already built.
- [ ] **events** + **logs** — event feed and log viewer
- [x] **probeconfig** — SHIPPED 2026-07-26 as the `/settings/probes` tab
      (`plans/2026-07-26-react-probeconfig-page.md`, 9 tasks). Both corrections
      this line already carried held up against live code: it is **not a
      standalone page** (the Flask route never calls `render_template`, only
      `render_template_string` over two macros, and its only consumer is the
      wizard loading those fragments by AJAX), and the reuse really is lopsided
      — 100% of the *editing* behaviour was reused verbatim, unmoved and
      unrenamed, while ~0% of the *delivery* path existed. What was missing was
      never the editor; it was a way to edit the **LIVE** probe map without
      re-running the wizard. `blueprints/probeconfig/` is untouched and stays:
      it is load-bearing for the Flask wizard, still the only installer UI, and
      `tests/web/test_page_probeconfig.py` remains its characterization net.
      See the SHIPPED section for what landed.
- [ ] **tuner** — probe tuning tool
- [ ] **update** — software updater (shells out; `is_real_hardware()`-gated)
- [ ] **metrics** — metrics/stats page
- [ ] **mobile** — may be obsolete once the dashboard reflows. Responsiveness is
      necessary, not sufficient; confirm before building, and do not delete the
      blueprint yet.

### 9. Per-probe notifications SLICE 2 — high/low limit alerts

**SHIPPED 2026-07-26.** Both limit alerts are in the React dashboard: the probe
notify modal now carries all three of Flask's accordion cards, and one Set
writes all three entries as three addressed `notify_updates` in a single POST.
Model in `web-react/src/helpers/notify/notifyState.ts` (`LimitEdit`,
`LimitAction`, `NotifyEdit`, `limitEditFields`, `saveNotifyEdit`), UI in
`ProbeNotifyModal.tsx`.

**This item existed nowhere in this backlog until 2026-07-26.** Slice 1 shipped
and the groundwork for slice 2 was written up carefully — but only inside
`plans/2026-07-25-react-probe-notifications.md`, under a "Slice 2 groundwork"
heading. A reader of the backlog alone would have concluded per-probe
notifications were finished; they were not. That is why this entry exists, and
why the answers below are recorded here rather than left in the plan.

What the slice settled:

- **The suspected second backend bug was REAL, and is fixed.** Resolved by
  running the code, as this item demanded, not by re-reading it: restoring the
  original gate (`fired = not control["notify_data"][index]["req"]`) turns
  `tests/unit/notify/test_notifications.py::test_check_notify_limit_shutdown_*`
  red — a `probe_limit_high` entry with `shutdown: True` and the temperature
  310°F past its 300°F limit leaves `control["mode"] == Hold`. Only the `probe`
  branch ever clears `req`; a limit entry stays armed for the whole cook, so the
  gate was permanently False and "Shutdown PiFire" beside every high/low limit
  was dead. Fixed in `6c42611d` — `fired` is `triggered` for a limit entry (the
  flag the neighbouring `reignite` branch already used) and `not req` for the
  one-shot probe/timer/test entries. Copying the probe branch's `req = False`
  would have been the WRONG fix: it disarms the alert for the rest of the cook.
- **RULING on the Flask asymmetry: ported, not flattened.** The limit
  temperatures render for every probe; the limit ACTIONS render for the Primary
  probe only. "Shutdown PiFire" on a high limit is a runaway-heat cutoff and
  "Attempt Re-ignite" on a low limit is a fire-out response — both are
  statements about the FIRE, and only the primary probe measures the fire. A
  food probe reading low means cold meat, not a dead fire: arming a re-ignite
  there fires the moment cold food goes on the grate, and pre-arming `triggered`
  cannot help, because the temperature genuinely leaves the range and comes
  back. The two halves also complement each other — the target action set is
  food-probe-only, the limit action set is primary-only — so every probe is
  offered exactly one action set.
- **`triggered` is pre-armed by the client, on the comparison the backend FIRES
  on.** `>=` for `equal_above`, `<=` for `equal_below`. Flask uses a strict
  `>` / `<` (`dash_default.js:724, :766`), so at exactly the limit it writes
  `triggered: false` for a condition that already holds and the alarm sounds
  immediately — the one boundary pre-arming exists to cover. This is also why
  the per-field REST grammar is unusable here: `/api/set/limit_high|limit_low/…`
  accepts `req`/`shutdown`/`keep_warm`/`reignite`/`target` and **cannot set
  `triggered`** (`common/api_commands.py:544-551`).
- **One `LimitAction`, not two booleans** — `"none" | "shutdown" | "reignite"`,
  the same shape as slice 1's `TargetAction`, so the UI cannot express the
  shutdown-plus-reignite state the backend silently collapses. No keep-warm on a
  limit: no UI in either app has ever offered it and the socket payload
  publishes no flag to read it back from. No re-ignite on the HIGH limit for the
  same reason — there is no `highLimitReignite` on the wire.

Two consequences worth knowing:

1. **The modal owns all three entries, so Set rewrites all three** — including
   re-arming each limit's `triggered` from the probe's live reading, which is
   what the backend itself would compute on its next pass. `limitEditFields`
   also states every input to the backend's action tail (`shutdown`,
   `keep_warm`, `reignite`) rather than only the control it shows, so an action
   armed by the mobile DTO or by `/api/set/limit_*` cannot act on an alert whose
   UI displays none. It names `condition` too: `notify.set` APPENDS an entry it
   cannot find, and `check_notify` reads `item["condition"]` unguarded.
2. **The bell now means "any notification", not "a target"** — it reads
   `hasNotifications`, which the backend already sets when any of the label's
   three entries is armed. The ETA readout is still target-only: the backend
   computes `eta` for `probe` entries alone.

**Not done, deliberately:** no cross-validation that the low limit sits below
the high limit. Flask allows the overlap and the backend fires both alarms
happily; forbidding it here would be a new rule, not a port.

### 9a. Live probe-map editing — three gaps disclosed by the probeconfig slice

`/settings/probes` shipped 2026-07-26. Three things it deliberately does NOT do,
recorded here rather than left in the plan document, per the standing rule below.

1. **Derived blobs other than `history_page.probe_config` are not regenerated.**
   `apply_probe_map` regenerates the history chart config, matching
   `wizard.py:230`, but leaves `control["notify_data"]` and
   `settings["recipe"]["probe_map"]` alone — because `run_wizard` leaves them
   alone too (`wizard.py:227-231` regenerates only the one). Matching the
   installer exactly was the conservative choice; diverging from it is a
   deliberate decision that deserves its own change. **Consequence: renaming a
   probe here leaves a stale notify entry pointing at the old label.**

2. ~~**Rebuilding probe devices does not close the old ones.**~~ **FIXED
   2026-07-26.** `ProbeInterface.close()` is now an explicit teardown hook and
   `ProbesMain._close_probe_devices()` runs it on every previous instance
   before the new list is bound, isolating failures (one device raising is
   logged and stepped over — the rebuild is the recovery path). Both original
   mitigations survive: the endpoint still gates on `mode == Stop`, and an
   unimportable module still degrades to `probes.disabled`.

   The survey behind it, worth not repeating: only six modules own a
   per-instance resource — `max31865` (spidev fd), `ads1115` (smbus2 fd, on the
   extended bus kind only), `ds18b20` and `thermoworks_cloud` (background
   threads), and `bt_ibbq` / `bt_meater` / `bt_meater_exp` (a BLE connection
   plus two non-daemon threads each). For the Bluetooth modules the old
   behavior was worse than "released at GC": the threads' own reference to the
   device meant it was never collected at all, and their `while True` setup
   loops reconnected to the same probe forever. Everything else deliberately
   has no `close()` — the Adafruit I2C/SPI modules are handed a process-cached
   bus (`common.i2c_bus.open_i2c_bus`, `probes.base.resolve_mcp2210`) shared by
   every device on that physical bus, which no single probe may close.

   **Still deliberately not done:** `ProbesMain` has no public `close()` for
   process shutdown — nothing would call one today, and the control process
   exits by termination.

   **Adjacent, found while fixing it, NOT fixed:** `_setup_probe_devices`
   degrades to `probes.disabled` on an *import* failure only. The
   `newmodule.ReadProbes(...)` construction sits outside that `try`, so a
   device whose `_init_device` fails all its retries propagates out of the
   rebuild (and out of `update_probe_map`) instead of degrading, leaving a
   partially built `probe_device_list`. The earlier text of this item claimed
   the fallback covered construct failures too; it never has.

3. **Last write wins between the two probe editors.** The Flask settings page
   still edits individual `probe_info` entries in place via
   `update_probe_config` (`common/app.py:346-390`). Two humans editing probes
   simultaneously there and in the React tab, both in Stop mode, within one
   page lifetime, is unmitigated. Not papered over with a `lastupdated.time`
   compare-and-swap: that race is datastore-wide and pre-existing, and a
   point fix here would imply a guarantee the rest of the store does not make.

### 10. Deferred-work inventory — 103 open items pulled out of plan documents

**Swept 2026-07-26**, after Slice 2 (item 9) proved that deferred work was
landing in plan documents and nowhere else. Two agents read all 17 React slice
plans, the 7 wizard plans, 14 React/UI specs and both audits, and checked every
finding against live code rather than trusting the document.

**225 findings. 103 still open, 115 already shipped, 7 need a ruling.** The
"already shipped" number is the important one: a great deal of this had been
done and the documents never said so. All 9 CRITICALs and 12 of 18 IMPORTANTs
from the divergence audit are closed.

Per-item detail — source document, exactly what was deferred, and the
`file:line` that decided each status — is in
`audits/2026-07-26-deferred-inventory-plans.md` and
`audits/2026-07-26-deferred-inventory-specs-audits.md`. The grouping below is
so that nobody has to open those to know the work exists.

**Do not re-raise two things:** the `augerontime` bound the guards sweep left
blocked on a decision was decided (1000, live in `settings_schema.py` and
`StartupTab.tsx`), and the hopper→`/pellets` link was ruled 2026-07-26 to
**exist**, matching Bootstrap (this resolves the direct contradiction between
the dashboard slice, which asserted there must be none, and the pellets plan,
which said it was owed).

#### Whole surfaces never built

- **Probe config as a React surface** — the single most-deferred item in the
  project: it appears five separate times across specs and plans as "next" and
  was never started. Now planned (see item 8).
- Recipes, Events, Admin — rendered disabled in the navbar; three whole Flask
  pages unported. Recipes/cookfile now planned.
- Cook-file list / upload / delete (D4) — History shipped the chart only.
- Recipe unpause payload not ported — a paused recipe cannot be resumed.
- `global_control_panel` neither read nor offered: no way to stop the grill
  from anywhere but the dashboard.
- WLED preset/profile grids (backend and schema are already ready).
- OneSignal: no "add device"; `uuid`/`app_id` not editable.
- "Send Test Notification" and all three WLED action buttons.
- PlatformTab is read-only — no React editor for `platform.*`.
- QML kiosk screens (Splash, Menu, Keypad, Hold/Notify overlays, QR, Sleep).
  **These may be intentionally dead** — the project pivoted from cloning the Qt
  kiosk to replacing the Flask web UI. Needs a one-line ruling either way.

#### Shipping and deployment gaps

- **Flask never serves the React app.** No SPA catch-all, no
  `send_from_directory` for a build output — so `/settings/*` deep links do not
  resolve and there is currently no deployment path at all.
- No page title, no favicon, no PWA manifest.
- `index.html` loads Barlow from `fonts.googleapis.com`: an offline PiFire
  silently falls back to a different typeface. This is also why the visual
  fidelity gate cannot be screenshot-based.
- No `/manual` route — a bookmarked Flask `/manual` URL will not resolve.
- ~~`globals.page_theme` is settable but inert.~~ — RENAMED 2026-07-27 to
  `globals.bootstrap_page_theme` and dropped from the React settings form. It
  only ever fed Bootstrap's light/dark theme on the legacy Flask pages, via
  `app.py`'s `inject_theme_and_grill_name` context processor, which still reads
  it under the new name. The React app has no light palette and never consumed
  it. **Delete the key outright when the last Flask page is retired** (item 8):
  remove it from `common/defaults.py` and `common/settings_schema.py`, drop the
  context processor's entry, and drop `page_theme` from the five templates that
  reference the injected variable.
  - No migration was written for the rename. An existing install keeps its old
    `page_theme` key, which the strict-schema repair wrapper strips on the next
    validated write, and the new key takes its `"light"` default — so a user who
    had chosen the dark Flask theme silently gets light back. Accepted because
    the key is cosmetic, applies only to pages being retired, and is scheduled
    for deletion; call it out if the Flask UI outlives this.
- The dashboard's accent swatches and General's Theme field now write the same
  key (`display.config.<module>.accent_theme`), which the Qt display reads once
  a second — so changing the accent in a browser repaints the attached screen.
  Intended, per the 2026-07-27 ruling, but it is the first setting the dashboard
  writes without a Save, so a stray click is immediately live on the appliance.

#### Backend behaviour that is broken or lying

- ~~The errors blob is write-only from the web tier, and `_check_control_status`
  can false-positive on a healthy system~~ — FIXED 2026-07-26 (item 4). The blob
  is now read-only from the web tier; liveness is a non-sticky in-memory signal
  composed into each payload. The false-positive vector (the `queue_systemo`
  race) was fixed earlier still.
- ~~`get_os_info(persist=True)` — a destructive flag still defaults to true.~~
  — FIXED 2026-07-26 (item 7): split into `probe_os_info()` + `refresh_os_info()`.
- `backup_pellet_db` is not performed on a React "Load New Pellets".
- Residual clobber window on the pellet blob — no optimistic concurrency.
- Notify targets are never converted on a temperature-units change.

#### UI parity, minor-graded

History Stream toggle and 5 s poll vs Flask's ~1 s; chart annotations fetched
but never drawn and disabled probes silently dropped; per-probe fill colours
configurable but ignored; History duration is a bare number input, not a 1–480
slider; History→Metrics link dropped; `display.sleep_timeout` has no control at
all; Controller "use recommended value" buttons and metadata card dropped;
updater release-notes modal dropped; per-setting Description dropped by
`SelectField` and `ConfigOptionField`; secret masking not ported (API keys and
tokens render as plain text); Flask's three dashboard error modals flattened and
the `ui_hash` reload prompt has no counterpart; discovery results lost their
Refresh/Close controls; `pf-section-note` and `pf-kv` have no CSS rule anywhere;
PlatformTab's markup uses classes that do not exist.

Wizard specifically: no confirmation summary at Finish; install output never
rendered, so a failed install leaves the user blind; Finish error detail (422
`detail`, 400 `sections`) thrown away; "System is active" warning still only at
the last step rather than on entry; per-step explanatory copy dropped; strictly
Back/Next with inert step indicators; tables have no column headers and device
"Type" shows `friendly_name` rather than the module id.

#### Verification gaps

- The `Basic` dashboard (795 lines) has never been compared by anyone.
- `probeconfig.js` plumbing and `probeReducer.ts` validation semantics never
  verified against each other.
- No 800×480 or phone-viewport coverage for the wizard.
- Three wizard surfaces unreachable by e2e — they rest on the human eye alone.
- Never checked: WCAG contrast, accent swaps, Barlow-unavailable rendering.
- No unconsumed-field regression check exists.
- `/scan`'s `vid`/`pid` are unwired — Flask hex-parses them, the React endpoint
  does not.
- `/admin/restart` and `/admin/reboot` are same-origin and hit the dev server
  rather than Flask.
- No e2e coverage of Exit Setup / `POST /api/wizard/cancel`.
- The reboot-modal flow has never run on real Pi hardware, and the assumption
  that `raspi-config nonint do_onewire 0` writes `dtoverlay=w1-gpio` is
  unverified.

#### Rulings — 2026-07-26

These were open questions. They are now answered; the entries they resolve are
struck from the list below rather than left to be re-asked.

1. **React ships ONE dashboard, permanently.** `hidden_cards`,
   `touch_screen_mode`, the dashboard picker and the whole `Basic` dashboard
   (795 lines) are dropped deliberately, not pending. Do not port them, and do
   not open "the Basic dashboard has never been compared" as a gap again — there
   is nothing to compare it to.
2. **`/mobile` will die.** Stop treating it as a migration target. It is not
   "may be obsolete"; it is going away. The blueprint stays only until the
   general retirement in ruling 5.
3. **Settings edits must survive a tab switch.** Today SmartStart/PWM table
   edits are lost on navigation, where Flask persisted immediately. Either
   answer is acceptable — preserve across the switch, or save immediately.
   **Preserving is the better fit**: saving immediately contradicts the per-tab
   `SaveBar` that already shipped and would make every keystroke a control
   write. Losing the edit silently is the only outcome ruled out.
4. **`display.sleep_timeout` belongs in the General tab**, and it must actually
   drive the DPMS behaviour — see [[project_qt_display_dpms_sway]]: cage's
   `wlr-randr --off` broke touch-wake on DSI+HDMI, blanking is disabled in the
   interim, and the next step there is imperative DPMS under sway/labwc. The
   React control and that work are the same feature; a control that renders but
   does nothing is worse than no control.
5. **No Flask page is retired until everything else is finished.** `pellets`,
   `probeconfig` and `cookfile` all now have shipped React replacements and all
   three Flask surfaces stay live. Retirement is one deliberate pass at the end,
   not a trailing step of each slice.
6. **Renaming a probe must not leave stale references.** This overrules item
   9a.1's conservative "match `wizard.py` exactly" choice. `apply_probe_map`
   regenerates only `history_page.probe_config`; a rename therefore leaves
   `control["notify_data"]` and `settings["recipe"]["probe_map"]` pointing at
   the old label. Fix it in `apply_probe_map`, and fix `run_wizard` too — the
   installer has the same hole, which is why matching it looked safe.
7. **Qt wins for `ok`/`warn`/`danger` everywhere — both bespoke ramps are
   gone.** Two React-only colour sets had no counterpart in `Theme.qml` and so
   were never covered by the `themeTokens` guard: a LIGHT ramp for text on
   tinted badges (`#8fe09a`/`#ffce6a`/`#ff8b82`, 24 sites in 7 stylesheets) and
   a MUTED set for the pellet meter (`#6cc070`/`#e0a44a`/`#d05a4e`, 4 sites) —
   drifting in opposite directions from the same three semantics. Every site now
   reads `var(--ok)`/`var(--warn)`/`var(--danger)`, so the guard covers them for
   free. Costs ~2 points of text contrast (all still clear AA) and removes the
   app's only sub-AA value. `HopperView.color2` went with them: it was only the
   light stop of the level bar's gradient, which `dashboard.css` now derives
   with `color-mix()` instead of carrying a second colour. **Do not reintroduce
   a hand-picked light or muted variant** — if a surface needs one, derive it
   from the token. Landed `102378d9`.
   *Leftover, deliberately not swept:* `tools/qt_dashboard_preview.qml:409,572,573`
   still hold the light ramp. It is a standalone dev preview with its own
   self-contained palette that never imports `Theme.qml`, so it was out of scope.

#### Still needing a human

Nothing. Every question in this group was answered on 2026-07-26; see the
rulings above.

~~Whether React's client-side clamps are deleted now that the schema enforces
bounds.~~ **Ruled: the clamps stay.** The schema is authoritative and remains
so — that was never the deciding factor. What settles it is a fact recorded in
`NumberField.tsx`'s own comment: there is no `<form>` anywhere in the settings
tree, so the browser never runs constraint validation and `min`/`max` only drive
the spinner arrows and `:invalid` styling. Delete the clamp and a typed `500` in
a `max={9}` field reaches the server and returns as a save error naming a dotted
path. The clamp is not duplicating the schema; it is the only thing that makes
the bound visible at the keystroke. It clamps on **blur, not change** — clamping
on change makes a bounded field untypeable (`min={20}` turns the intermediate
"2" of "25" into "205"). Sites: `helpers/settings/bounds.ts`,
`settings/fields/NumberField.tsx:41-45`, `settings/RangeProfileTable.tsx:86,102,113`,
`settings/tabs/StartupTab.tsx:111,115`.

#### Schema and toolchain follow-ups

S3 defaults consolidation and typed deep-path `setPath` helpers; per-controller
schema generation from `controllers.json`; `additionalProperties` stripping in
TS generation; `<path>: <why>` save-error display; read-path validation never
scoped; mapping a dotted error path to the offending widget; the four 2b-1
follow-ups (`waitFor`, `read*` fallback defaults, `aria-describedby`,
float-vs-int audit).

Tailwind prerequisites, all now owned by `plans/2026-07-26-tailwind-v4-migration.md`:
no browserslist pinned; unverified whether Biome's CSS parser accepts
`@theme`/`@apply`/`@import "tailwindcss"`; **no visual baselines exist for any
page at either viewport**; dynamic class names are invisible to Tailwind's
scanner and the required safelist note does not exist.


---

## Test-harness notes

- **Running several checkouts at once needs four variables.** Ports and origins
  all come from `web-react/ports.ts`; nothing else may hardcode one. A second
  workspace needs its own dev servers *and* its own PiFire, because the suite
  is globally destructive to whichever backend it reaches:

  ```sh
  export PORT=5273 DEMO_PORT=5274                   # this checkout's dev servers
  export PIFIRE_BACKEND_URL=http://localhost:5100   # this checkout's backend
  export PIFIRE_DB_PATH="$PWD/pifire.db"            # and its own datastore
  uv run python control.py &
  uv run gunicorn -k gthread --threads 25 -b 0.0.0.0:5100 -w 1 app:app &
  ```

  **`PIFIRE_BACKEND_URL`, never `PUBLIC_PIFIRE_URL`.** rsbuild injects every
  `PUBLIC_*` variable into the browser bundle, and eight modules read
  `import.meta.env.PUBLIC_PIFIRE_URL` as their fetch base. Setting that turns
  every same-origin request into an absolute cross-origin one that skips the dev
  proxy — and Flask sends no CORS headers, so the browser blocks it and every
  loader throws. The first version of this scheme did exactly that and made the
  e2e suite unrunnable in every secondary workspace. `PUBLIC_PIFIRE_URL` remains
  correct for the other job: pointing a single checkout's browser at a real
  grill, where the browser and the proxy should agree on one absolute origin.

  Sharing any one of them reintroduces the failure this replaced: two
  workspaces both served `:5173`, and Playwright's `reuseExistingServer`
  attached to whichever started first, so a suite silently reported results for
  a tree nobody was looking at.
- **Running e2e from a jj workspace needs `PIFIRE_DB_PATH`.** `common/datastore.py`
  resolves `DB_PATH` relative to its own checkout, so `history.spec.ts`'s seed
  script writes to that workspace's `pifire.db` while the backend serves the
  main checkout's. `beforeAll` now fails once with this instruction instead of
  five confusing "the chart never mounted" failures.
- **Restart gunicorn before trusting an e2e result.** A worker started before a
  backend change serves the old code, and new endpoints return 404 while the
  specs that need them fail as if the frontend were broken. This has now cost
  three separate tasks: two were blocked outright, and the pellets merge showed
  two red specs purely because the running worker was ~13 hours older than the
  `GET /api/pellets` route it was being asked for.
- **Playwright needs the main checkout or an explicit DB path**, and the suite
  runs `workers: 1` because every spec drives one shared, stateful PiFire.
- The Playwright characterization suite covers all 17 Flask blueprint pages
  (`docs/web-test-findings-2026-07-17.md`) — it is the safety net for each
  page's migration.
- `api` / `api_wizard` are the JSON backends the React app consumes, not pages
  to migrate.

### Two dependency pins that are deliberate, not neglect

Both survived a `bun update --latest` / `uv lock --upgrade` sweep on 2026-07-26.
Bumping either is its own piece of work, not a lockfile refresh.

- **`typescript` stays on `^5.9.3`.** It exists only as
  `@typescript-eslint/parser@8.65.0`'s peer. Typechecking runs against
  `typescript7` (`npm:typescript@7.0.2`), aliased so the two coexist. `bun
  outdated` will keep offering `typescript 5.9.3 → 7.0.2`; taking it breaks the
  ESLint parser and nothing else gains.
- **`ruff` is capped `<0.16`.** 0.16 promotes new rules to the default set and
  extends the formatter to Python code blocks inside Markdown. Measured on this
  tree: 0.15.22 gives `All checks passed` and 550 files formatted; 0.16.0 gives
  **1422 errors and 64 files reformatted**, almost all of it docs and legacy
  code no current change touches. `ruff check .` is a merge gate (`ruff.toml`
  says so outright), so adopting 0.16 is a repo-wide cleanup commit. Raise the
  ceiling in that commit; the rationale is on the pin in `pyproject.toml`.

## Standing rule: a slice is not done until its deferrals are HERE

Every plan in `docs/superpowers/plans/` defers something -- a later slice, an
accepted divergence, a suspected bug it read but did not execute, a decision it
punted to a human. Until 2026-07-26 that deferred work lived only inside the
plan document, and a plan document is read once, by the person executing it,
and then never again.

That is how per-probe notification **Slice 2** -- the entire high/low limit
alert feature, carefully specified -- became invisible: the backlog said
notifications shipped, and nothing anywhere said the limits had not.

**So: before a slice is marked done, every "Slice N+1" / "out of scope" /
"could not verify" / "decide later" item in its plan gets a line in this file.**
The plan stays the detailed reference; this file is what guarantees anyone ever
opens it again. A pointer plus one sentence of what is deferred is enough --
the point is that it exists here at all.

## Lessons this backlog has already paid for

1. **A page-shaped backlog has a blind spot for things that are not pages.**
   Global navigation was missing from this file entirely for months: every plan
   was scoped to one surface and ended at "register the route in App.tsx", and
   nobody wrote the task for how a user gets from one page to another. Before
   calling the migration done, ask what lives in `templates/base.html` and in the
   app's chrome that no page-shaped item would ever cover.
2. **Tests that assert text and roles do not assert that a page looks like
   anything.** The wizard shipped with no CSS and a full green suite.
3. **Verifying a feature by calling its API is not verifying the feature.** The
   notify round trip was confirmed working against the backend over REST, and was
   broken in every browser.
