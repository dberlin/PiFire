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
  reset, cursor tooltip, CSV export link. Chart only, as scoped; the cook-file
  list/upload/delete still belongs to the un-migrated cookfile/recipes item.
- **Settings** — 12 tabs: General, Units, Pellets, Safety, WorkMode, Pwm,
  Startup, Controller, History, Notifications, Platform (read-only), plus the
  shared `SaveBar`.
- **Wizard** (`/wizard`) — all steps functional: welcome, grill platform,
  probes (devices + ports), display, distance, finish, install-progress
  polling, and an Exit control. **Functionally complete but entirely
  unstyled — see the open item below.**

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
- **Settings guards sweep** — `NumberField` bounds enforced on blur, `dc_fan`
  gating, PWM min/max guard with dependent clamps, Startup conditional
  structure, monotonic range boundaries, delete confirmations.
- **Timer** — server-computed end time, so a skewed browser clock cannot arm an
  already-expired timer; options and start sent as one control write.

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

---

## OPEN

### 1. Wizard has no CSS at all — highest priority

43 `pf-*` class names used across `web-react/src/components/wizard/**` match
**zero rules**; there is no `wizard.css`. Only `pf-btn` and `pf-fit` resolve,
from `dashboard.css`. At 1280×720 the wizard renders as raw HTML: step names run
together as `WelcomeGrill PlatformProbesDisplayDistance / HopperFinish`, buttons
stack at x=0, no padding, no card treatment, no modal chrome.

Every wizard unit test and all four wizard e2e specs pass against it, because
they assert on text and roles. This is the first-run experience.

Plan: `docs/superpowers/plans/2026-07-25-react-wizard-styling.md` (8 tasks).

**How this was missed:** no test in the project asserted that a class it uses
is defined anywhere, and until 2026-07-25 nobody had opened the wizard in a
browser.

### 2. Dashboard reflow

Plan: `docs/superpowers/plans/2026-07-25-react-dashboard-slice.md` (14 tasks).
Ratified 2026-07-25: the fixed 1280×720 uniformly-scaled stage is reversed and
the dashboard becomes responsive, **with 1280×720 as a regression target** —
"does not have to be pixel perfect, but very close." Capture reference geometry
before touching layout.

Also ratified: **one dashboard forever.** No picker, no `hidden_cards`, no
`touch_screen_mode`, no port of `Basic`. Consequence to carry: `Basic`'s
click-to-toggle manual outputs now have no home in React — that capability
belongs to the **manual** page item below. This does not retire the Flask
picker; it only says React will not grow one.

### 3. Timer clobber is only half fixed

`timerStartWithOptions` closed the arm path, but `timerShutdown` and
`timerKeepWarm` remain separate calls, and `command.ts` documents that they must
be re-sent after `timerStop`. Two writes inside one control cycle is exactly the
clobber the original fix addressed: the drain applies queued partials with
`json_patch`, which replaces arrays wholesale, so the last write wins outright.

### 4. Remaining audit findings

`docs/superpowers/audits/2026-07-25-audit-triage.md` — roughly 40 findings in 10
slices. Slices for save-failure, notifications, guards, and the dashboard have
been written and executed or planned; the rest are untouched.

### 5. Accessor rename WAVE 2

Four remaining items, including `read_warnings()` → `drain_warnings()`. That one
is a genuine cross-consumer bug, not just a naming problem: the dash routes and
socketio both call it, so whichever polls first consumes the other's warnings.

### 6. Un-migrated Flask pages

Roughly ordered by daily-use value:

- [ ] **manual** — manual output control (core function; also inherits `Basic`'s
      click-to-toggle outputs, per the one-dashboard decision)
- [ ] **pellets** — pellet inventory manager (distinct from the Pellets settings tab)
- [ ] **admin** — restart/reboot/shutdown, backups
- [ ] **recipes** + **cookfile** — recipe editor and cook-file browser (share a
      data model and need a JSON listing endpoint that does not exist yet)
- [ ] **events** + **logs** — event feed and log viewer
- [ ] **probeconfig** — standalone probe-config page; the wizard's probes step is
      done, so this can likely reuse the shipped reducer and cards
- [ ] **tuner** — probe tuning tool
- [ ] **update** — software updater (shells out; `is_real_hardware()`-gated)
- [ ] **metrics** — metrics/stats page
- [ ] **mobile** — may be obsolete once the dashboard reflows. Responsiveness is
      necessary, not sufficient; confirm before building, and do not delete the
      blueprint yet.

---

## Test-harness notes

- **Running e2e from a jj workspace needs `PIFIRE_DB_PATH`.** `common/datastore.py`
  resolves `DB_PATH` relative to its own checkout, so `history.spec.ts`'s seed
  script writes to that workspace's `pifire.db` while the backend serves the
  main checkout's. `beforeAll` now fails once with this instruction instead of
  five confusing "the chart never mounted" failures.
- **Playwright needs the main checkout or an explicit DB path**, and the suite
  runs `workers: 1` because every spec drives one shared, stateful PiFire.
- The Playwright characterization suite covers all 17 Flask blueprint pages
  (`docs/web-test-findings-2026-07-17.md`) — it is the safety net for each
  page's migration.
- `api` / `api_wizard` are the JSON backends the React app consumes, not pages
  to migrate.

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
