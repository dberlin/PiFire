# React Migration — Backlog

Durable backlog for the `web-react/` replacement of the Flask/Jinja UI.

Two sibling backlogs cover everything this one does not: `backend-backlog.md`
(the Python server — blueprints, control loop, datastore and schema layer,
updater) and `display-backlog.md` (the display process and its drivers).

## How to read this file

Earlier revisions accumulated three different kinds of date on the same entry —
when it was opened, when somebody swept it, and when it was fixed — none of them
labelled. An entry could read `SHIPPED 2026-07-28 … still open … 2026-07-29` and
mean any of them. **One convention now, applied throughout:**

| | |
|---|---|
| **DONE** | The work landed. Any date on the line is **when it landed**, never when someone noticed. |
| **OPEN** | Not done. **Carries no date** — a date beside unfinished work is exactly what used to read as a completion. |
| **WON'T DO** | Decided against. The ruling that decided it is named. |

Rules that follow from that, and that the next editor has to keep:

- **The status word comes first**, before the prose, on every entry.
- **Never add a date to an OPEN entry.** If you want to record when you checked
  it, say so in the reconciliation log below — that is what it is for.
- **Never date a heading with the day you swept it.** Item 8's heading briefly
  read `DONE 2026-08-02` when nothing was migrated that day; every page under it
  had shipped a week earlier.
- Struck-through (`~~text~~`) means the claim was wrong or has been overtaken.
  The correction follows it. Nothing is deleted outright, so a reader who
  remembers the old claim can see what happened to it.

## Reconciliation log

Dates here are when someone *checked*, which is why they are quarantined in this
one section instead of being sprinkled through the entries.

- **2026-07-25** — first pass against live code and a real browser. Previous
  revisions listed several items as open that had shipped and, more
  importantly, listed nothing that only a running browser could reveal, because
  until then nothing had been run in one.
- **2026-07-26, 2026-07-28, 2026-07-29** — three partial sweeps of item 10's
  deferred-work inventory. Each is written up inside that item.
- **2026-08-02** — full pass over the OPEN section. Three items carrying a
  caveat (1, 2, 5) were signed off, and **roughly two dozen entries were
  stale** — each true when written and overtaken without the entry being
  revisited. The date convention above was introduced by this pass.

  The single largest cause of staleness was the **Flask-retirement pass, which
  landed 2026-07-29**. Six groups of deferrals were phrased as "waits for the
  general pass (ruling 5)"; that pass then shipped and none of them were
  struck. `blueprints/{admin, cookfile, dash, events, history, logs, manifest,
  manual, metrics, pellets, probeconfig, recipes, settings, update}/` hold no
  source file today — only a stale `__pycache__` — and `templates/` is down to
  `server_error.html`. Anything describing a Flask page, template, macro or
  characterization test as live was describing something that no longer exists.

  **Lesson for the next slice that defers on a future pass:** a deferral written
  as "until X happens" is a promise that someone re-reads this file when X
  happens. Nobody did. Name the item the pass has to close, so the pass's own
  checklist carries it.

This file previously lived in `.superpowers/sdd/`, which is gitignored scratch
that `git clean -fdx` destroys. It is tracked now. Three audit documents cite
it by path and line number; those citations point here.

---

## SHIPPED

### Pages and chrome

- **Dashboard** (`/`) — live socket data.
- **App shell / global navigation** — navbar with seven destinations
  (Dashboard, Recipes, History, Pellets, Events, Settings, Admin), every one of
  them a real link since 2026-07-28. Events was the last disabled entry; with
  it ported, the `to: null` case and the disabled span it rendered are
  gone from NavBar entirely (TypeScript narrowed that branch to `never`), along
  with their stylesheet rule. A future unported destination has to reintroduce
  the mechanism deliberately.

  **Four routes are deliberately NOT in the navbar**, and adding them would be
  a regression, not a fix: `/metrics` (reached from /history, matching Flask,
  whose `base.html` has never carried a Metrics entry), `/tuner` (reached from
  Settings > Probes — Flask's navbar has a Tuner entry, but the tuner opens a
  live tuning session that moves the grill into Monitor, which belongs behind
  the probe config, not on the global nav), `/cookfiles/:filename` and
  `/recipes/:filename` (detail views, reached from their lists). The one navbar
  entry with no Flask counterpart is Pellets — see the note there.

  Also: shared layout
  route, timer bar + modal, and the `Banners` alert strip hoisted out of
  Dashboard. The shell owns the single socket subscription and passes it down
  through Outlet context; a structural test enforces the one-call rule, because
  a second socket fails no other test — every component test mocks the hook.
- **History page** (`/history`) — uPlot chart, minutes window, drag-zoom,
  reset, cursor tooltip, CSV export link, a Metrics link (2026-07-28 — the only
  route into `/metrics` in either UI), **plus the saved-cook list** (2026-07-26:
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
- **Recipes** (`/recipes`, `/recipes/:filename`) — SHIPPED 2026-07-27
  (`plans/2026-07-27-react-recipes.md`, 18 tasks). Browse, view, run and edit
  `.pfrecipe` archives. Thirteen new `/api/files/recipes/*` routes in
  `blueprints/api_files/recipes_api.py`, every one resolving a **bare filename**
  through `resolve_managed_file` — the legacy `blueprints/recipes/` surface,
  which concatenates `RECIPE_FOLDER + filename` unvalidated, is untouched and
  stays live until the general retirement pass (ruling 5). `require_file` now
  takes its folder explicitly, since two archive kinds share it and a default
  would let a new route resolve a recipe against the history folder.

  Run status needs no polling endpoint: `socket_dash_data` has always published
  `recipeStatus` (`socket_io.py:330-336`) on every frame and on connect. The
  plan's own outline proposed `GET /run-status`; reading the socket instead
  deleted that task.

  Two structural invariants are ported rather than reinvented, each pinned by a
  test: changing `food_probes` reshapes `trigger_temps.food` on **every** step,
  and renaming an ingredient rewrites that name inside **every** instruction
  that used it (`instructions[].ingredients` holds names, not indices). The
  editors refetch the whole detail after any write rather than reconciling
  locally, because that cascade changes rows the user is not looking at.

  **`0` is the disabled sentinel** for `hold_temp` and both `trigger_temps`
  members throughout. The step editor derives each enable switch from
  `value > 0` so the switch and the value cannot disagree, and the view renders
  `0` as "—" rather than as a temperature.
- **Admin** (`/admin`) — SHIPPED 2026-07-27
  (`plans/2026-07-27-react-admin.md`, 15 tasks in two slices). System readings,
  reboot/shutdown/restart, factory reset, the four clears, the two toggles,
  backups and logs. A new `blueprints/api_admin/` answers all of it; the whole
  page is built from ONE read, `GET /api/admin/state`, because that read calls
  `gather_system_info()`, which probes the platform and writes the readings back
  into control.

  **No route here accepts a path**, in either direction: backups are named by
  bare filename and resolved through `resolve_managed_file`, the log endpoints
  take no name at all, and responses carry basenames only. Nothing in
  `blueprints/api_admin/` reaches a shell — the two `os.system` calls in Flask's
  admin blueprint (`rm ./logs/events.log`, `rm logs/*.log`, the second inside a
  bare `except:` where a failure was indistinguishable from success) are not
  inherited. `POST /logs/delete` globs server-side and answers with the names
  that actually went, and the card renders that list rather than its own.

  Three pre-existing hazards were closed on the way, each with a regression
  test:

  - **`/api/cmd/*` answered a bare GET**, so any link or prefetch was enough to
    power the box off. It now requires POST. No in-repo client used GET.
  - **Flask's backup restore built `backup_path + request.form["localfile"]`**
    by concatenation at four sites. Since a restore reads a file and writes it
    over live settings, that was an arbitrary-file-LOAD, not merely a read. All
    four now go through `resolve_managed_file`.
  - **`restart_control()` and `restart_webapp()` ran unconditionally**; they are
    now gated on `is_real_hardware()`.

  Deliberate divergence from Flask: every destructive endpoint refuses with
  **409 `not_stopped`** unless the grill is stopped, matching the guard
  `POST /api/probe_map` and `POST /api/files/recipes/run` already apply. Flask
  offered them from any mode. The maintenance clears are deliberately NOT gated,
  also matching Flask — clearing a pellet log mid-cook is recoverable.

  Deliberately left alone: **the Socket.IO admin door**
  (`blueprints/mobile/socket_io.py`), which is the mobile app's API, carries its
  own duplicate `os.system` fallbacks, and is independently tested — Task 4's
  gate is the only thing that reaches it; and **the wizard's hard links to
  Flask's `/admin/reboot` and `/admin/restart`**, which still point at the old
  blueprint. `blueprints/admin/` stays live until the general retirement pass
  (ruling 5).

  **A payload assumption that only a live read could disprove:** `os_info` is
  `/etc/os-release` as a MAP, and the RAM, core-count and frequency readings are
  NUMBERS. `gather_system_info()`'s fallback literals are all strings
  (`"Unknown"`), and typing the payload from those alone produced a page that
  crashed React on contact with a real machine. Every unit test passed, because
  the fixture had been written from the same wrong assumption as the type. The
  live-backend `tests/e2e/admin.spec.ts` is what found it; both fixtures are now
  copied from an actual response.
- **Events + Logs** (`/events`) — SHIPPED 2026-07-28
  (`plans/2026-07-28-react-events-logs.md`, 14 tasks in two slices). ONE route
  with two tabs, not Flask's two pages: both tabs are the same virtualized
  viewer (`@melloware/react-logviewer`) over a different log family, and as two
  separate pages in Flask they had already diverged — only one of them could
  reach a rotated file.

  **One endpoint answers both tabs**: `GET /api/admin/logs/view?log=<stem>`,
  which `send_file`s the stitched family from a `BytesIO` with
  `conditional=True`. That gives real `Range` support, so the live tail asks for
  `bytes=<cursor>-` and appends a delta instead of re-downloading — for the
  events family on this machine, 1.7 MB a poll otherwise.

  **The parameter is a family STEM, never a filename and never a path.** It is
  looked up in a server-built dict rather than joined onto a directory, so there
  is no concatenation for a `../` to land in. That is what makes the two holes
  in `blueprints/logs/routes.py` (`send_file` and `read_log_file`, both joining
  a request field onto the logs folder) unreachable here rather than merely
  unlikely.

  **Rotation was a blind spot in three separate places**, all fixed:
  `delete_logs` and `build_log_archive` each globbed `*.log` only — so "Delete
  All" reported success while leaving `events.log.1`-`.3` on disk and the viewer
  still showing content, and the support ZIP shipped without the history it
  existed to carry. The listing had the same gap. `list_logs()` itself is
  deliberately UNCHANGED; the rotation-aware view is a new `families` member
  alongside it, because the shipped admin LogsCard is built against the flat one.

  **A clear-path bug the questions surfaced:** clearing events removed
  `./logs/events.log` via `os.system` while the `logs` TABLE kept everything —
  every logger `create_logger` builds writes to a `RotatingFileHandler` AND a
  `SqliteLogHandler`, so clearing one sink left the other holding exactly what
  the user asked to be rid of. It now clears both.

  **The `logs` table was unbounded** — nothing had ever deleted a row. Bounded
  now by a SQLite `AFTER INSERT` trigger keeping 20 000 rows per logger, fired
  every 1 000 inserts. A trigger rather than an emit counter (the user's
  suggestion) because the counter could not see writes from other processes.

  **The suite had been appending to the operator's real `./logs/`** for the
  whole project. That is why the live `events.log` carried fixture strings
  ("Admin: Shutdown failed: boom", a WLED connection to 127.0.0.1:1) in the
  content the log viewer shows a user, and why the files disagreed with the
  table: tests already used a temporary database, but not temporary log files.
  `PIFIRE_LOG_DIR` is now resolved at import like `PIFIRE_DB_PATH`, and
  `config.py`'s `LOGS_FOLDER` is derived from it rather than being a second
  independent literal.

  **What only the live e2e could find:** LazyLog's `height` prop runs through
  `Number()` for anything but `"auto"`, so the `"60vh"` it was given was `NaN`
  and the viewer rendered its search bar over zero rows. jsdom cannot see this —
  virtua discards every measurement whose target has no `offsetParent`, and in
  jsdom that is every element, so the unit tests mount no rows either way and
  passed throughout. Height comes from `.pf-log-frame` now.
- **Metrics** (`/metrics`) — SHIPPED 2026-07-28
  (`plans/2026-07-28-react-metrics-page.md`, 9 tasks). One card per metrics
  record, behind a new **read-only** `blueprints/api_metrics`: two GETs, the
  listing and the CSV, and no POST registered at all rather than one that
  refuses. `GET /api/metrics` had to be taken back from `blueprints/api`'s
  `/api/<action>` catch-all, which was answering that literal path; the
  resolved endpoint is pinned by name, not by status, because a 200 from the
  catch-all would satisfy a status check while returning a different body.

  **`process_metrics()` stays the only definition of the derived columns.** The
  endpoint returns its output rather than raw rows, so `"1 m 30 s"` and
  `"30 grams"` are computed once, in Python, and the page and its own CSV
  cannot disagree. The cost is one sharp edge the type had to carry:
  `endtime_c` is a `"%H:%M:%S"` STRING for a finished mode and the NUMBER `0`
  for a running one, so it is typed `string | number` and pinned on both ends.

  **Three live defects fixed on the way.** (1) `metrics_page` never passed
  `settings["globals"]["augerrate"]`, so every pellet-usage estimate on the
  Flask page was computed at the 0.3 g/s default — wrong for any tuned grill.
  Fixed in both routes, and the rate is now stated in the React page's header,
  because an estimate whose constant is invisible is a number nobody can check.
  (2) `_macro_metrics.html`'s Hold card reads `metric['grill_settemp']`, which
  matches **no column** in the metrics table — the column is `primary_setpoint`
  — so Jinja has always rendered that row blank. Fixed on the React surface
  only; the template is the legacy UI and `test_page_smallpages.py`
  characterizes it. (3) The Flask export names its file
  `-PiFire-Metrics-Export` **twice**: the route appends the suffix that
  `prepare_metrics_csv` appends itself.

  **No navbar entry, deliberately.** `templates/base.html` has never had one;
  `history/index.html:47` is the only link into `/metrics` in the Flask tree.
  That link was dropped by the first history port and is now restored as a
  `<Link>` — an `<a href>` would reload the SPA and drop the shell's socket.
- **Tuner** (`/tuner`, MANUAL flow) — SHIPPED 2026-07-28
  (`plans/2026-07-28-react-tuner-manual.md`, slice 1 of 2, 11 tasks), behind a
  new `blueprints/api_tuner`. The auto flow is slice 2.

  **The one structural change from Flask: the SESSION is split from the
  READING.** `/tuner`'s `read_tr` command both enabled tuning mode AND returned
  a value, so a page that merely polled mutated grill state on every tick and no
  request meant "stop". Now exactly two calls write control —
  `POST /api/tuner/session {open}` — and `GET /api/tuner/tr` is inert. Opening
  moves a stopped grill to Monitor and is refused with 409 from any mode that is
  neither Stop nor Monitor (Flask had no such guard); closing is idempotent and
  restores Stop only when the mode is still Monitor, so a cook started mid-session
  is left alone.

  **This is the first ported page whose own operation changes grill mode**, so
  the teardown is the load-bearing behaviour: `useTunerSession` closes on
  unmount, including when the unmount races an open still in flight. Three
  independent nets guard against a leaked Monitor session — an `autouse` pytest
  fixture, the hook's unmount tests, and the live e2e's `afterEach` control
  read — each with its own negative control.

  **The template-injection door in `blueprints/tuner` is closed.** Its fragment
  endpoint concatenated a client-supplied name into Jinja SOURCE and rendered
  it (`render_template_string`); the six names the client sends are now an
  allowlist into constant strings. Proven real: the seventh macro, defined but
  never requested, rendered a full fragment under the old code.

  **Two silent failures in the maths now have signals.**
  `calc_shh_coefficients` swallows every exception and returns `(0, 0, 0)`,
  which Flask fed straight to the save form — now a 422 `uncomputable`. And
  `calc_shh_chart` abandons the whole curve on one bad point (its own docstring
  calls this common) — now reported as `chart_ok: false` rather than drawn as
  an empty chart. `tuner.py` itself is unchanged (`test_page_tuner.py` pins its
  return shape); the endpoint interprets its output. A missing Tr reading is
  `null`, not Flask's `0`, which is indistinguishable from a shorted probe.

  **Reached from Settings > Probes, not the navbar** — Flask's navbar has a
  Tuner link, but the tuner opens a live session and belongs behind the probe
  config. The curve is an inline SVG polyline, not uPlot: twenty points need no
  library and every coordinate stays readable from the DOM.

  **AUTO flow** — SHIPPED 2026-07-28 as slice 2
  (`plans/2026-07-28-react-tuner-auto.md`, 6 tasks). A Manual/Auto toggle on
  the same page; auto mode adds a reference-probe selector and polls
  `POST /api/tuner/auto-status` once a second while the session is open. Each
  poll records one temperature/resistance sample against the reference probe
  and reports the running high/medium/low selection, until the spread is wide
  enough (`ready`). It writes only the **autotune queue, never control** — the
  two session calls remain the sole writers of grill state — and the session's
  **flush-on-open** is what makes each run start from zero (Flask flushed on
  the first poll, which was its enable-tuning moment; ours is the explicit
  open). The reference temperature is looked up across the `control:current`
  P/F/AUX groups by label, and a missing probe reads `null`, not Flask's `-1`.
  Auto's Finish sends the derived three points to the SAME
  coefficients/close/chart/save path manual uses — the maths, the session
  lifetime and the profile save are reused unchanged. The DS18B20 warm-up guard
  (skip a cold probe's leading zeros) is preserved.
- **Update** (`/update`) — SHIPPED 2026-07-29
  (`plans/2026-07-29-react-updater.md`, 7 tasks), behind a new
  `blueprints/api_update`. All four mutations (`branches/refresh`, `branch`,
  `pull`, `upgrade`) fire the underlying `updater.py` process only under
  `is_real_hardware()` (the `_fire` seam); beyond that the gates differ per
  route — only `pull` and `upgrade` refuse with 409 unless
  `control.mode == STOP`, `branch` instead returns 400 for a target outside
  the branch allowlist, and `branches/refresh` has no mode gate at all. The
  page shows current version/branch/remote, a branch switcher, the
  pull/upgrade actions, an update log viewer, and a progress panel that polls
  `GET /api/update/status` until the `101`/`142` sentinel (done vs.
  reboot-required, matching `wizard/InstallProgress.tsx` and
  `updater.py:548`). A `SystemUpdateCard` on `/admin` fetches
  `GET /api/update/check` to show the current version and commits-behind and
  links to `/update` — it has no upgrade action of its own. Not ported: the
  post-update "what's new" release-notes modal — that is app-shell chrome
  triggered by a settings flag on any route, not a control on this page; see
  "Deferred by the updater slice" below.
- **Wizard** (`/wizard`) — all steps functional: welcome, grill platform,
  probes (devices + ports), display, distance, finish, install-progress
  polling, and an Exit control. Functionally complete and styled — item 1 was
  signed off 2026-08-02.

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
- **`recipe_delete` over Socket.IO was a live command injection** (found
  2026-07-27 while planning the recipes slice). `blueprints/mobile/socket_io.py`
  ran `os.system(f"rm {filepath}")` on `request["recipes_action"]["filename"]`
  with no `secure_filename`, no containment and no shell escaping, so a payload
  of `x.pfrecipe; <anything>` executed. **Its HTTP sibling
  (`blueprints/recipes/routes.py::_recipes_json_deletefile`) had been hardened
  and regression-tested months earlier; this copy was simply missed** — which is
  the reusable lesson: when a bug class is fixed at one door, grep for the other
  doors rather than assuming the fix was global. Both attacks were demonstrated
  RED against the old code before the fix landed
  (`tests/web/test_socket_recipe_delete_safety.py`). Hardened rather than
  deleted: `post_app_data` is the mobile app's API and `recipe_start` sits
  beside it with no replacement.
- **`convert_recipe_units` raised on every call.** It iterated
  `step["settemps"]`, a key the recipe schema has never had — the shape is
  `trigger_temps: {primary, food[]}`. `controller/runtime/controller.py:140`
  calls it whenever a recipe's saved `metadata.units` differs from the live
  setting, so running such a recipe took the control-process recipe loop down
  with it. The fix converts `hold_temp` and both `trigger_temps` members, and
  **passes `0` through unconverted** — `0` is the disabled sentinel, and mapping
  0 °F to −17 °C would arm every disabled trigger on the recipe.
- **Factory reset left the whole pellet database in place.** Pre-SQLite,
  `os.system("rm pelletdb.json")` WAS the mechanism; removing that dead line
  preserved the accident it left behind. Human ruling: factory reset clears the
  pellet database. Both `_admin_setting_factorydefaults` and the Socket.IO
  `factory_defaults` (which never reset pellets at all, not even pre-SQLite) now
  call the shared `common/pellets_actions.clear_pellet_db()`. The control reseed
  is untouched — it is a deliberate explicit delta.

---

## RESOLVED

Items that were tracked as OPEN but have since shipped, been superseded, or
were otherwise closed. They keep their **original numbers** so cross-references
elsewhere in this file ("(item 4)", "item 7", "SUPERSEDED by item 10") still
resolve; the OPEN section below therefore skips these numbers by design.

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
  (Both accessors were subsequently retired by the 2026-07-29 warnings-dismiss
  slice, superseded by `read_warnings_snapshot()` and `clear_warnings_through()`.)
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

---

## OPEN

### What is actually open — read this before scheduling anything

This heading says OPEN; most of what is under it is not. The status of each
top-level item, so nobody has to read 900 lines to find that out:

| Item | Status |
|---|---|
| 1. Wizard styling | **DONE** |
| 2. Dashboard reflow | **DONE** |
| 5. Tailwind v4 migration | **DONE** |
| 8. Un-migrated Flask pages | **DONE** — nothing is un-migrated |
| 9a. Live probe-map editing | 2 of 3 **DONE**; 9a.3 **OPEN** |
| 10. Deferred-work inventory | **MIXED** — the only large open surface |
| 11. Recipes deliberate non-dos | **WON'T DO** — boundaries, not owed work |
| 12. Shutdown affordance and ordering | **OPEN** |
| 13. P-MODE/SMOKE+ visibility outside Smoke | **OPEN** — needs a ruling |
| 14. `clampSetpoint` is superseded | **OPEN** — delete it |
| 15. Probe cards intermittently read 0 | **OPEN** — live-grill report |

So the real open work is **9a.3 and the OPEN entries inside item 10.** The
biggest of those, in rough order of consequence:

1. `updated_message` is written on every upgrade and read by nothing (the
   release-notes modal) — a live writer with no reader.
2. No 404 route at all, so every unrouted URL hits react-router's error screen;
   `/manual` is one instance of it.
3. No PWA manifest.
4. Wizard has no 800×480 coverage; no e2e for Exit Setup.
5. The rest of the schema and toolchain follow-ups — none started.
6. Shutdown does not read as destructive, and Stop/Shutdown sit in opposite
   orders here and on the attached display (item 12). Note what Shutdown
   actually does before scheduling this one — it is not a styling item.
7. Probe cards intermittently read 0 while the attached display never does
   (item 15) — reported from a live grill, cause not yet established.
8. Two small ones: whether P-MODE/SMOKE+ should be Smoke-only (item 13, needs a
   ruling) and a superseded helper to delete (item 14).

Closed on 2026-08-02, listed here only because they were on this list the same
day: persisted schema versioning for both durable blobs, **including modeling,
validating and versioning `pellets:general`** — which also closed the
unvalidated admin restore path and the pellet log's silent-drop bug;
`bootstrap_page_theme` (deleted, with its context processor); and the missing
favicon (the PiFire flame, pinned end to end).

**Items 1, 2, 5 and 8 are kept here rather than moved to RESOLVED** because
their bodies carry measurement notes and ordering lessons — checkpoint before
recapture, how a reflow gate goes vacuous, how to count "undefined classes"
honestly — that are most useful next to the work they came from.

Numbering is non-contiguous by design: resolved items (3, 4, 6, 6a, 7, 9) moved
to RESOLVED above keep their numbers there. Item 10 precedes item 11.

### 1. Wizard styling — DONE

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

**Task 8, the human visual checkpoint: signed off 2026-08-02.** 8 of its 12
items were pre-screened from clean screenshots; items 10–12 and the type/colour
judgement were reviewed by the owner. Item 7 (the no-photo fallback) is
unreachable — all 62 manifest modules have images — and was not performed.

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

### 2. Dashboard reflow — DONE

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

**Signed off by the owner 2026-08-02.** The breakpoint values remain measured
floors rather than design choices — worth knowing if one ever needs moving, but
not an open question.

Also ratified: **one dashboard forever.** No picker, no `hidden_cards`, no
`touch_screen_mode`, no port of `Basic`. Consequence to carry: `Basic`'s
click-to-toggle manual outputs now have no home in React — that capability
belongs to the **manual** page item below. This does not retire the Flask
picker; it only says React will not grow one.

### 5. Tailwind v4 migration — DONE

**Closed out 2026-07-27.** All 20 tasks are shipped and the human checkpoint has
signed off. Accepted visual differences, the four defects the walkthrough found
that every gate had passed, and two deviations from the plan's own baseline
rules are recorded in
`docs/superpowers/audits/2026-07-27-tailwind-migration-diffs.md`.

The spec's status line was **not** updated, though Task 15 Step 5 asks for it:
`docs/superpowers/specs/` is treated as an immutable historical record in this
repo, and that standing rule wins over a step in a plan. This entry is the
status.

The fidelity reference was re-established after the sign-off — that order
matters, since recapturing first would have baked in the four defects the
walkthrough found. That left `bun run test:e2e:fidelity` at **105 passed, 0
failed**.

`/settings/probes` was the one surface the migration left ungated, because
ProbesTab shipped after the reference was first captured. It got its own spec
and baselines, so all twelve settings tabs are now covered at both viewports and
`test:e2e:fidelity` finished at **109 passed, 0 failed**. (105 then 109 is one
number before that addition and one after, not a disagreement.)

Spec: `docs/superpowers/specs/2026-07-25-tailwind-v4-migration-design.md`.
Ratified: token bridge (`@theme` + `@apply`, `pf-*` names and JSX survive), gate
extended to every page at 1280×720 and 390×844, implementation gated on the
wizard-styling and dashboard-reflow slices merging first.

<details>
<summary><b>The original item statement, kept as history — it is written as
instructions, and they have all been carried out.</b></summary>

Move `web-react/`'s six hand-written stylesheets (2,603 lines: `theme.css`,
`dashboard.css` 1149, `wizard.css` 624, `settings.css` 344, `shell.css` 315,
`historyChart.css` 61) onto Tailwind v4 via the Rsbuild integration
(<https://rsbuild.rs/guide/styling/tailwindcss>).

Hard requirement: **visually identical before and after**, except where the
"before" is clearly broken. The gate already exists in embryo and must be
generalised rather than reinvented — `tests/e2e/layoutBaseline.ts` +
`dashboard-layout-1280x720.json` capture a per-landmark box plus
`fontSize`/`fontWeight`, compared with `BOX_TOL = 2`px and an `EXACT` override
table. It is deliberately **not** a `toHaveScreenshot()` gate: pixels depend on
the host font stack, and masking the volatile regions would mask exactly the
typography the gate exists to protect. (This paragraph originally justified that
with "`index.html` loads Barlow from `fonts.googleapis.com`". Barlow has been
self-hosted since 2026-07-29 — the *network* half of the reason is gone, the
host-rasterisation half is not, and the conclusion is unchanged. Same correction
under *Shipping and deployment gaps*.)

**Unblocked 2026-07-26.** The wizard-styling and dashboard-reflow slices — which
were rewriting the two largest stylesheets and would have collided with this —
are both merged. Re-measure the line counts above before starting; they were
taken before those two landed.

</details>

#### DONE — tasks 1-14, landed 2026-07-27

Plan: `plans/2026-07-26-tailwind-v4-migration.md`. All fourteen implementation
tasks landed; the visual guarantee held. **The 38 pre-Tailwind baselines are
byte-identical before and after** (0 deletions across the whole diff — verified
independently in the main checkout, not just reported), and the fidelity gate is
**97 passed**. `bun run baseline:capture` was never re-run; Task 5 added 5 new
baseline files that had never existed, for 43 total. Suite 1258 → 1262; CSS
bundle 41,207 → 53,871 bytes raw / 10,555 gzip, with no duplicated theme layer.

#### DONE — tasks 16-20, landed 2026-07-27: the plan's gaps closed, preflight adopted

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

**Both DONE.** Kept because the ORDER is the reusable part, not because
anything is owed:

1. ~~**The baselines encode the pre-preflight rendering**, so
   `test:e2e:fidelity` is 47 failed / 58 passed by design.~~ Re-established
   after the sign-off, as required — **109 passed, 0 failed**. The "never
   recapture" rule was premised on preflight changing nothing; once that premise
   went, the reference had to be re-cut deliberately.
2. ~~**Task 15 human visual checkpoint.**~~ Signed off; accepted differences and
   the four defects the walkthrough found are in
   `audits/2026-07-27-tailwind-migration-diffs.md`. It carried the known 390×844
   General-tab hint wart (52px column forcing a 187px row against 36-38px
   neighbours) — pre-existing and deliberately not fixed during a conversion.

**The order is the point:** the checkpoint ran first, then the recapture.
Recapturing first would have baked in whatever the reviewer was about to object
to, and a green gate would have been the evidence that it was fine.
3. **Two things found and left alone**, both pre-existing: `.pf-settings-back`
   ("Back to history") is dim text with no affordance, and the class is shared
   with a `<button>` that should not be underlined, so it needs a decision rather
   than a patch. The cook-file table runs its Download button off the right edge
   at 390px.
4. ~~**`history-*.json` is machine-dependent.**~~ **FIXED 2026-07-28.** The
   `history` fidelity spec stubs `/api/files/cookfiles` (`stubs:
   stubCookFiles`), so the embedded saved-cooks list renders fixture rows
   instead of the developer's real cook files. Both history baselines are
   byte-deterministic across captures — verified by capturing twice. The tuner
   slice's deferral list records the same fix; this copy was left standing.

### 8. Un-migrated Flask pages — DONE, nothing left un-migrated

Roughly ordered by daily-use value:

- [x] **pellets** — SHIPPED 2026-07-25 (`plans/2026-07-25-react-pellets-page.md`,
      13 tasks). Listed here as open until 2026-07-26 purely because this entry
      was never struck; see the SHIPPED section for what landed.
- [x] **admin** — SHIPPED 2026-07-27 (`plans/2026-07-27-react-admin.md`, 15
      tasks in two slices). The warning this entry used to carry still stands
      for anything that touches these paths: the tests MUST neutralize
      `os.system`/`subprocess` before anything runs, an `is_real_hardware()`
      flag is not enough, and this repo has really rebooted the developer's
      machine three times that way. See the SHIPPED section for what landed and
      what was deliberately left alone.
- [x] **recipes** — SHIPPED 2026-07-27 (`plans/2026-07-27-react-recipes.md`, 18
      tasks in two slices). See the SHIPPED section for what landed and item 11
      for what was deliberately left out. The 17-row outline at the bottom of
      `plans/2026-07-26-react-recipes-cookfile.md` is superseded by that plan;
      it said in terms that it had to be written out in full before execution,
      and it was. Correction to what this line used to say: recipes and cookfile
      do **not** "share a data model". They share a ZIP container and a listing
      shape, and nothing else — different JSON member sets, different metadata
      keys, no page in common, and **zero** overlapping action names between the
      two dispatch tables.
- [x] **events** + **logs** — SHIPPED 2026-07-28 as the single `/events` route
      with Events and Log Files tabs (`plans/2026-07-28-react-events-logs.md`,
      14 tasks in two slices). Correction to what this line used to imply: they
      are not two pages. Flask ships them as two, and they had already diverged
      because of it — only one of the two could reach a rotated file. Both tabs
      are the same viewer over a different family. See the SHIPPED section.
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
- [x] **tuner** — SHIPPED 2026-07-28, BOTH flows, at `/tuner` behind a new
      `blueprints/api_tuner`. Manual three-point flow:
      `plans/2026-07-28-react-tuner-manual.md` (slice 1, 11 tasks). Auto
      accumulation flow: `plans/2026-07-28-react-tuner-auto.md` (slice 2, 6
      tasks). See the SHIPPED section.
- [x] **update** — SHIPPED 2026-07-29 (`plans/2026-07-29-react-updater.md`, 7
      tasks). New `blueprints/api_update` (read: state/check/log; mutations:
      branches/refresh, branch, pull, upgrade — all fire only under
      `is_real_hardware()`, but the gates differ per route: `pull`/`upgrade`
      refuse 409 unless `control.mode == STOP`, `branch` returns 400 for a
      target outside the branch allowlist, and `branches/refresh` has no mode
      gate) backs a new `/update` page (state, branch switcher, pull/upgrade
      actions, log viewer, and a progress panel that polls
      `GET /api/update/status` to the `101`/`142` done/reboot-required
      sentinel) plus a `SystemUpdateCard` on `/admin` (fetches
      `GET /api/update/check` for version/commits-behind and links to
      `/update`; no upgrade action of its own). The post-update
      "what's new" release-notes modal was deliberately left out of scope —
      see "Deferred by the updater slice" below.
- [x] **metrics** — SHIPPED 2026-07-28 as `/metrics`, behind a new read-only
      `blueprints/api_metrics` (`plans/2026-07-28-react-metrics-page.md`,
      9 tasks). Correction to what this line implied: it is not a "stats" page.
      It reports one record per MODE TRANSITION — no aggregate, no trend, no
      cross-cook total — which is also why it does not poll. See the SHIPPED
      section.
- [x] ~~**mobile**~~ — **NOT A PAGE; the entry was a category error.**
      `blueprints/mobile/` registers no HTTP route at all: it is
      `socket_io.py`, and it is the live Socket.IO feed the React app itself
      consumes. There was never a mobile page to migrate. The Flask-retirement
      pass settled its fate on 2026-07-29 — **kept, registered, untouched** —
      which also supersedes ruling 2's "`/mobile` will die". See the correction
      on that ruling.

      What the responsiveness half of this line meant is real and lives
      elsewhere: the dashboard reflow (item 2) and the 800×480 `panel` project
      are what cover a phone and the grill's own screen.

### 9a. Live probe-map editing — 2 of 3 DONE, 1 OPEN

`/settings/probes` shipped 2026-07-26 with three deliberate gaps, recorded here
rather than left in the plan document, per the standing rule below. **Two are
closed** (2 in 2026-07-26, 1 by ruling 6); only 3 survives, and in a narrower
form than it was written. The numbering is kept so the original three stay
traceable.

1. ~~**Derived blobs other than `history_page.probe_config` are not
   regenerated.**~~ **DONE** — ruling 6 was implemented, and this entry
   outlived it. `common/defaults.py::set_probe_map()` is now the single
   installer of a probe map and rebuilds *everything* keyed by a probe LABEL:
   `history_page.probe_config`, `settings["recipe"]["probe_map"]`,
   `settings["dashboard"][*]["custom"]["hidden_cards"]` and
   `control["notify_data"]`. **Both** writers go through it — the live path
   (`blueprints/api/routes.py:432`) and the installer (`wizard.py:314`) — so
   the hole ruling 6 found in `run_wizard` is closed too, not just the one in
   the React tab.

   Two mechanics worth carrying: `notify_data` is rebuilt in place but
   PERSISTED BY THE CALLER as a `notify.replace` op, because
   `common/control_delta.py:34` forbids `notify_data` under `set` (it is an
   array whose elements need addressing). And only ids the OLD map carried as
   labels are pruned from `hidden_cards`, so non-probe card ids ("hopper",
   "status") survive.

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

3. **Last write wins between the two probe editors** — still true, but the
   other editor is no longer the one this entry named. The Flask settings page
   was deleted by the retirement pass; `update_probe_config`
   (`common/app.py:358`) survives with exactly ONE caller,
   `blueprints/mobile/socket_io.py::_update_probe_config` — the mobile app's
   door, which edits individual `probe_info` entries in place while
   `POST /api/probe_map` applies a whole map. A human in the React tab and a
   phone doing the same thing, both in Stop mode, is unmitigated.

   Narrower than it was (a phone rather than a second browser tab), and not
   papered over with a `lastupdated.time` compare-and-swap: that race is
   datastore-wide and pre-existing, and a point fix here would imply a
   guarantee the rest of the store does not make.

### 10. Deferred-work inventory — MIXED, see each subsection

> **The "103 open" in this item's original title was a 2026-07-26 measurement
> and is now badly wrong.** Three reconciliation sweeps and the Flask-retirement
> pass have closed a large fraction of it, and the 2026-08-02 pass struck
> roughly two dozen more entries below. **Do not quote a count from this item.**
> Every subsection below carries its own verified disposition; those are the
> answer. The number is left in the prose beneath only as the historical
> snapshot it was.

**Swept 2026-07-26**, after Slice 2 (item 9) proved that deferred work was
landing in plan documents and nowhere else. Two agents read all 17 React slice
plans, the 7 wizard plans, 14 React/UI specs and both audits, and checked every
finding against live code rather than trusting the document.

**As measured on 2026-07-26: 225 findings, 103 open, 115 already shipped, 7
needing a ruling.** Those numbers are a snapshot of that day and nothing has
recomputed them since; the per-finding dispositions below are current, the
counts are not. The "already shipped" number is the one worth carrying: a
great deal of this had been done and the documents never said so. All 9
CRITICALs and 12 of 18 IMPORTANTs from the divergence audit are closed.

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

**Reconciled 2026-07-28 — the first 15 open findings of
`audits/2026-07-26-deferred-inventory-plans.md`** (that audit is the dated
historical snapshot; this is the current disposition). Findings are cited by
their audit number:

- **Shipped since the 2026-07-26 sweep** — **#25** cook-file list/upload/delete
  (now `web-react/src/components/cookfiles/*` + `helpers/files/cookfileApi.ts`,
  route `App.tsx`) and **#27** the disabled Recipes/Events/Admin nav entries
  (gone; `shell/NavBar.tsx` has real `to:` targets for all seven). Both are in
  the SHIPPED section.
- **Resolved by a deliberate ruling** — **#22**: `.pf-kv`/`.pf-kv-row` now have
  rules (`settings.css`), and `.pf-section-note` is *intentionally* unstyled
  (allowlisted in `styleCoverage.test.ts`, which asserts that list for EXACT
  equality — so adding a rule would fail the gate, not satisfy it). Nothing to do.
- **#9 — the `settings_update` seam is now pinned.** The React SmartStart/PWM
  table saves ride the tab's delta with `["settings_update"]` where Flask sends
  a bare write; `tests/web/test_api_settings_update.py::`
  `test_settings_update_table_save_flag_does_not_alter_stored_settings` proves a
  whole-array table save WITH the flag stores the identical settings tree as the
  same delta WITHOUT it — the flag's only effect is the queued control re-read.
- **#6 — assumption confirmed, closed.** `after_startup_mode` is exactly
  `{Smoke, Hold}`: `settings_schema.py` `Literal["Smoke","Hold"]` and Flask
  `blueprints/settings/templates/settings/index.html:808-809` offer only those.
- **Confirmed BY-DECISION won't-dos** (live code still matches the ruling):
  **#18** OneSignal devices self-register, only `friendly_name` editable
  (`NotificationsTab.tsx`); **#21** PlatformTab read-only by design, edits owned
  by the wizard; **#29** no `/manual` route — manual outputs live on the
  dashboard button row; **#30** `allowManualOutputs` deliberately unused, pinned
  by `buttonsForMode.test.ts`.
- **#19 is NOT a parity gap — now an accepted enhancement (2026-07-28).** Flask
  renders all six credential fields as `type="text"` too
  (`index.html:1515,1536,1543,1577,1714,1790`), so React already matches Flask;
  masking is net-new UX, not a port. The user has accepted it as an enhancement
  beyond Flask parity — tracked under *Enhancements accepted beyond Flask parity*
  below.
- **#26 — accepted unmonitored risk.** The Flask Chart.js history page still
  consumes `prepare_chartdata` at `data_points=10000` with LTTB's per-series
  union possibly exceeding it; no cap is warranted until the slow page is
  actually observed. No code change.
- **#17 — SHIPPED 2026-07-28.** The WLED preset/profile editor is built:
  `WledCard.tsx` (`components/settings/tabs/notifications/`) renders the two mode
  toggles, the suggested-config block, and the 12-row `profile_numbers` grid, and
  wires the three action buttons (Discover pick-list, Push Profiles, Test Profile)
  through `helpers/notify/wledApi.ts` to the existing `/api/wled_*` endpoints. Full
  parity with Flask's WLED card; `mode_presets`/`event_presets` are preserved on
  Save but not rendered (zero Flask UI — parity boundary). Spec/plan:
  `specs/2026-07-28-react-wled-editor-design.md`, `plans/2026-07-28-react-wled-editor.md`.
- **#5 — SHIPPED 2026-07-29** by the Flask-retirement pass (see the SHIPPED block
  below). Flask now serves the React build via a `spa` blueprint.
- **Sweep 2 (2026-07-28) — the rest of the audit's STILL-OPEN labels, verified
  vs live code.** Stale labels now closed: **#33** (limit shutdown fires —
  `notifications.py` fixed), **#45** (`sleep_timeout` rendered — GeneralTab),
  **#46** (drafts held on SettingsShell, survive tab nav), **#48** (= #22),
  **#57** (errors blob reads non-destructive — item 4), **#69** (`clear_pelletdb`
  no longer `os.system rm`), **#70** (HopperGauge `<Link to="/pellets">`).
- **Sweep-2 correction (2026-07-29).** Two of sweep 2's "genuinely still open"
  labels were already closed and one was fixed this session:
  - **#31 / #32 / #34 — SHIPPED** by a concurrent session (`08071c7e` model
    high/low limit alert edits, `21f2d977` the modal UI), wired in
    `Dashboard.tsx` (`readNotifyEdit` / `saveNotifyEdit`). #32's `triggered`
    pre-arm lives in `notifyState.ts::limitEditFields`; #34's Flask asymmetry
    was *decided* (limit temperatures for every probe, Shutdown/Re-ignite
    actions Primary-only — `ProbeNotifyModal.tsx:158-168`), not blindly ported.
    Covered by `notifyState.test.ts`, `ProbeNotifyModal.test.tsx`,
    `Dashboard.test.tsx`, `deriveView.test.ts`, `notify.spec.ts`. The sweep-2
    label was stale — the code landed while the reconciliation was being written.
  - **#35 — FIXED 2026-07-29** (`fix(api_commands): convert notify targets on a
    units change`). `_cmd_set_units` now converts every armed
    `control["notify_data"]` target via addressed `notify.set` ops, gated on a
    real unit change, skipping the `target: 0` off-sentinel. Helper
    `common/common.py::notify_target_conversion_ops`; tests in
    `tests/unit/common/test_set_units_notify_conversion.py`.
  - Fixed 2026-07-29: **#44** (Barlow self-hosted via `@fontsource`, no CDN),
    **#58** (recipe *unpause* — `recipeUnpause` posts the minimal
    `{recipe:{step_data:{pause:false}}}`; `buttonsForMode` branches the Next Step
    button paused→unpause), **#67** (`pellets_load_profile` now calls
    `backup_pellet_db`). **#5** and **#71** were the last two — both SHIPPED
    2026-07-29 by the Flask-retirement pass (see its SHIPPED block below). Full
    disposition of every finding (incl. accepted divergences and
    UNCLEAR/browser-only items) is in the audit's *sweep 2* block.

#### DONE — Flask-retirement pass, landed 2026-07-29 (ruling 5: #5 + #71)

Spec/plan: `specs/2026-07-29-flask-retirement-design.md`,
`plans/2026-07-29-flask-retirement.md`. Executed subagent-driven; final gate green
(Python `tests/web` 622 + `tests/unit` 1707; web-react typecheck/lint/build + 1623
tests).

- **#5 — SHIPPED.** New `blueprints/spa/` (registered last) serves
  `web-react/dist/index.html` for `/` and every client-side deep link, and serves
  the React bundle's `/static/{js,css,font}` via rules that shadow Flask's default
  static. Unknown `/api` and `/mobile` paths return a JSON 404, not the SPA shell.
  `app.py`'s `index()` redirect was removed (React owns first-run routing).
  **Load-bearing subtlety:** Flask's `static_folder` was deliberately NOT repointed
  — `/static/img/**` stays on the default handler because it is a KEPT tree
  (`api_files` uploads under `static/img/tmp` + React vendor images
  `/static/img/wizard/*`, `/static/img/pifire-cf-thumb.png`). Regression-pinned by
  `test_spa.py::test_static_img_still_served_by_flask_default`.
- **#71 — SHIPPED.** Deregistered AND deleted the 14 legacy page blueprints
  (admin, events, logs, history, metrics, dash, pellets, cookfile, probeconfig,
  recipes, settings, update, manual, manifest) plus the `wizard`/`tuner` page
  routes/templates — the `wizard.py`/`tuner.py` helper modules survive because
  `api_wizard`/`api_tuner` import them. Deleted the dead shared templates
  (`base.html`, the `_macro_*`/`_log_list` partials, `shutdown.html`) and the
  legacy `static/{css,font,js}`. Every retired page's characterization/straggler
  test was deleted or (for mixed files) surgically trimmed, coverage confirmed on
  the kept surface first — including the admin-restore **path-traversal
  containment** security guarantee (preserved on `test_api_admin_backups.py`).
- **"Still reachable until ruling 5" items — now CLOSED.** The traversal /
  template-injection doors on the retired pages (e.g. the logs-folder request-field
  path, the tuner `render_template_string` fragment) are unreachable because the
  routes no longer exist. `shutdown.html` (admin reboot/shutdown/restart splash) is
  covered in React by `SystemCard` → `api_admin` `_ADMIN_DISPATCH` (same
  `common/system.py` calls) + an inline `role="status"` notice.
- **/mobile — KEPT, fate settled.** `blueprints/mobile/socket_io.py` is Socket.IO
  only (no HTTP routes) and is React's live backend feed; it stays registered and
  untouched. It was never a page to retire. (The separate "mobile responsiveness"
  line below remains its own open concern, unrelated to the socket.)
- **Dead-code cleanup (this pass).** Removed 4 helpers orphaned by the retirement
  (`add_line_numbers`, `get_display_info`, `is_checked`, `is_not_blank`) after
  serena-verifying zero production callers, plus a dead test and an orphaned
  import. A "think-hard" audit of every function the deleted pages called
  (`dead-function-audit.md`) found exactly ONE behavioral gap (below); everything
  else was replicated on a kept surface or pure-dead.

**Deferred by the Flask-retirement pass (recorded, not built) — both since
closed:**

- **DONE** — ~~Warnings never auto-clear in React.~~ Shipped 2026-07-29 by the
  warnings-clear slice. React now has a dismiss control: `POST /api/dismiss_warnings
  {through_id}` clears `SqliteQueue` rows `WHERE id <= through_id` — a
  high-water-mark clear, keyed to the `warningsMaxId` the socket payload
  publishes alongside `warnings`, so a warning raised after the client's
  snapshot was taken is never deleted unseen. `read_warnings()` and
  `drain_warnings()` (the read-and-burn accessors this gap was originally
  filed against) are retired, along with the Valkey-era `scenario_warnings`
  oracle fixture and its now-meaningless drain/clear-parity test.
- **DONE** — ~~Pre-existing e2e baseline drift.~~ Resolved 2026-07-29.
  `fidelity-pages`
  was 106 passed / 6 failed; it is now **112 passed**. Each failure was diagnosed
  to an *intended* change that landed after its baseline was captured — no
  regression was hiding in the set, which is why the baselines were recaptured
  rather than the code changed:
  - **`b5bf2631` self-hosted Barlow (#44)** is the newest commit of the set and the
    unifying cause of every `+14`/`+20px` height shift (`settings-probes-390`,
    `wizard-probes-390`). The baselines were captured while Barlow came over the
    network; self-hosting changed which font actually rendered, so text-driven
    boxes changed size. This is why `wizard-probes` drifted even though nothing
    under `src/components/wizard` had changed since its baseline — the cause was a
    shared font, not the page.
  - **`489a4231` added `SystemUpdateCard` to `/admin`** — the x-coordinates cycling
    `33 → 158 → 282 → 33` is one card entering a grid and shifting every sibling a
    slot.
  - **`ce3106cf` added the WLED profile grid** to the notifications tab — the
    `+522px` on `.pf-section#7` and ~2500 net-new landmark lines.

  Recapture was scoped with `--grep` so passing baselines were not silently moved.
  Two that were recaptured — `settings-probes-1280x720` and `wizard-probes-1280x720`
  — came back **byte-identical**, which is the evidence that the capture is
  deterministic and the diffs above are real signal rather than noise. Verified
  green twice, and `fidelity`/`reflow`/`panel`/`fidelity-chrome` all still pass.

  **Recapture procedure**, for the next time an intended change moves a landmark:
  `PF_CAPTURE=1 bunx playwright test --project=fidelity-pages --grep '<page names>'`,
  then confirm `jj diff --summary` lists only the baselines you expected, and that
  any recaptured-but-passing baseline is unchanged. Diagnose *before* recapturing:
  a baseline refresh that has not been traced to an intended change will happily
  bake in a regression.

  **The gate is not fully hermetic, and the one time that mattered it found a real
  bug (2026-07-29).** `stubApi` intercepts `/api/**` and `/static/img/tmp/**`, but
  NOT `/static/img/wizard/**` — the board and module photos. So those `<img>`
  elements load from the live backend when one is running and fail when one is
  not, and the two states can measure differently. `settings-probes-390x844` and
  `wizard-probes-390x844` went red with no relevant source change, purely because
  a dev-server restart put a backend in the picture; chasing it down found that
  the devices-table photo was collapsing to 0x0 whenever it actually loaded
  (Preflight's `max-width:100%`/`height:auto` beating the width/height
  attributes, circular in an auto-layout table). Pinning the size in CSS fixed
  the bug and made that table measure identically either way, so the existing
  baselines needed no recapture.

  What is still exposed: `.pf-module-image` (`width: 132px; height: auto`) on the
  wizard's grillplatform/probes steps takes its HEIGHT from the loaded photo's
  aspect ratio, so those baselines do still depend on a reachable backend.
  Stubbing the route would trade that for a different divergence — one stub image
  cannot carry every real photo's aspect ratio, so the gate would stop matching
  production. Left as a known dependence rather than papered over. **If a probes
  or wizard baseline goes red with no plausible source change, check whether a
  backend is running before you touch the baseline.**

- **WON'T DO** — #1 / #2, resolved by ruling 8: not a web-react target. The QML
  kiosk is the on-device touchscreen UI (a fullscreen Wayland kiosk on the Pi's
  attached screen) and it STAYS; the React app was never going to reimplement
  its screens. The kiosk was only a *visual* target — the React UI borrows its
  look, which shipped as `display/qml/Theme.qml` → `theme.css` (guarded by
  `themeTokens.test.ts`). So the "kiosk screens never built in React" framing is
  a category error the spike plan introduced; closed as won't-do. See ruling 8.

#### Enhancements accepted beyond Flask parity — DONE, list currently empty

Net-new UX the user has accepted even though Flask never had it. These are NOT
parity ports: no fidelity gate will catch them, and each needs its own slice
when scheduled. The one entry ever added here has shipped, so nothing is
pending — but keep the section, because this is where the next accepted
enhancement goes and it is the only place that distinguishes one from a gap.

- **DONE** — ~~Credential masking (#19).~~ Shipped 2026-07-29. `SecretField.tsx` masks
  the value and reveals it only while the user holds it open with a Show/Hide
  toggle. Field-level only: storage and transport still send these in clear,
  because hiding the value on screen is the only thing a field component can
  address.

  The six fields are the IFTTT, Pushbullet and Pushover **API keys**, the
  Pushover **user keys**, the **InfluxDB token** and the **MQTT password** —
  which is a correction to this entry's own list. It said "WLED/OneSignal keys":
  WLED's only text field is `device_address` (not a secret), and OneSignal's
  `uuid`/`app_id` are deliberately not rendered by the tab at all, so neither
  contributed a field. The count of six was right for the wrong reasons.

  Two mechanical details worth carrying forward. A wrapping `<label>` cannot hold
  the toggle — its text content would become "MQTT PasswordShow" and break every
  `getByLabelText` — so the component uses an explicit `htmlFor`/`id` pair.
  And because the toggle is named after its field (so six of them stay
  distinguishable to a screen reader), Playwright's `getByLabel` matches it too:
  locators for a masked field need `{ exact: true }`.

#### Whole surfaces never built — MIXED

- **DONE** — ~~Probe config as a React surface.~~ Shipped 2026-07-26 as the
  `/settings/probes` tab (`ProbesTab.tsx`,
  `plans/2026-07-26-react-probeconfig-page.md`, 9 tasks); see the SHIPPED entry
  at item 8. It had been the single most-deferred item in the project, named
  "next" five separate times across specs and plans before it was started.
  Corrected 2026-07-29: this line still read "was never started" three days
  after the tab shipped, and reading it cold was enough to reopen a finished
  slice.
- **DONE** — ~~Recipes~~ shipped 2026-07-27; the navbar entry is a real link now.
  ~~Admin~~ — SHIPPED 2026-07-27, same. ~~Events~~ — SHIPPED 2026-07-28, and it
  was the last one: **no navbar entry renders disabled any more**, and no whole
  Flask page in the navbar is unported.
- **DONE** — ~~Cook-file list / upload / delete (D4); History shipped the chart only.~~
  **SHIPPED 2026-07-26** (finding #25): `components/cookfiles/*` +
  `helpers/files/cookfileApi.ts`, rendered on `/history` exactly as Flask does.
  The SHIPPED section and the sweep-2 reconciliation both recorded this; this
  line was the third copy and the only one still claiming it was open.
- **DONE** — ~~Recipe unpause payload not ported.~~ Shipped 2026-07-29 (#58).
  `helpers/command.ts:210`'s `recipeUnpause` posts the minimal
  `{recipe:{step_data:{pause:false}}}`, and `buttonsForMode.ts:107-108` branches
  the single Next Step button on `recipeStatus.paused` — unpause when paused,
  advance when not — exactly as Flask's one button does. Covered by
  `command.test.ts` and `buttonsForMode.test.ts`. Corrected 2026-07-29: this
  line still claimed the gap was open while the sweep-2 reconciliation above
  recorded the fix on the same day, so the file contradicted itself.
- **DONE** — ~~`global_control_panel` neither read nor offered.~~ The SETTING IS GONE,
  deleted 2026-07-29 rather than implemented. "Show Control Panel on Most Pages"
  was a Flask-era layout switch for Jinja templates that no longer exist; no
  Python, React or QML read it and no UI in either stack ever offered a way to
  set it, so it had never been anything but a stored `False`. Removed from
  `defaults.py` and `settings_schema.py`, with the schema and generated types
  regenerated. An existing tree still carrying the key sheds it through
  `validate_settings_tree()`'s repair pass on its next validated write.
  (Stopping the grill from any page remains possible only from the dashboard —
  that was never what this flag delivered.)
- **DONE** — ~~WLED preset/profile grids (backend and schema are already
  ready).~~ Shipped 2026-07-28 (#17); see the reconciliation above.
- **WON'T DO** — ~~OneSignal: no "add device"; `uuid`/`app_id` not editable.~~
  **By decision, not a gap** (#18, confirmed against live code above). Devices self-register
  from the mobile app, so an "add device" control would have nothing to add;
  only `friendly_name` is editable, and `uuid`/`app_id` are deliberately not
  rendered. `NotificationsTab.tsx:35-38` states this at the code. Listing it
  under "never built" reads as owed work; it is not.
- **OPEN** — "Send Test Notification" (Apprise/OneSignal test) is genuinely
  still missing. (~~All three WLED action buttons~~ **DONE**, shipped
  2026-07-28 (#17): Discover, Push Profiles, Test Profile.)
- **WON'T DO** — ~~PlatformTab is read-only, no React editor for
  `platform.*`.~~ **By decision** (#21, confirmed above): platform edits are
  owned by the wizard, which is where the hardware is chosen. Same objection
  as the OneSignal line — a settled ruling filed under "never built".
- **WON'T DO** — ~~QML kiosk screens (Splash, Menu, Keypad, Hold/Notify
  overlays, QR, Sleep).~~ **Ruling 8: NOT a web-react target.** The QML kiosk
  is the
  on-device touchscreen UI and stays; React never intended to reimplement its
  screens. The kiosk was only a visual target, and that borrowing already
  shipped (`Theme.qml` → `theme.css`). Findings #1/#2 closed as won't-do.

#### Shipping and deployment gaps — MIXED

- **DONE** — ~~Flask never serves the React app; no SPA catch-all, no
  `send_from_directory` for a build output.~~ `blueprints/spa/`
  serves `web-react/dist`: asset routes for `/static/js`, `/static/css` and the
  rest, declared ahead of Flask's built-in `/static/<path:filename>` so Werkzeug
  prefers them, plus the catch-all that makes `/settings/*` deep links resolve.
  This is the deployment path, and it is what supervisor runs in production —
  the rsbuild dev server is a development convenience, not a requirement.
- **DONE** — ~~No page title.~~ `web-react/index.html` sets
  `PiFire · React UI (POC)`.
- **DONE** — ~~no favicon.~~ Shipped 2026-08-02: the PiFire flame,
  `<link rel="icon" href="/static/img/favicon.ico">`, which is the same asset
  the Flask UI used. Referenced rather than copied into the bundle, because
  `/static/img` is a kept tree Flask's default static handler serves and the
  spa blueprint deliberately does not shadow, so one href resolves in
  production and through the dev proxy alike. Pinned end to end by
  `test_spa.py::test_favicon_is_declared_and_the_declared_path_is_served`,
  which reads the href out of the shipped shell and fetches exactly that.
- **OPEN** — no PWA manifest, and `web-react/public/` does not exist. Flask's
  `base.html` used to link one from a `/manifest` route; both went with the
  retirement pass, so there is no manifest anywhere now. Split out of the
  page-title entry, which used to carry title, favicon and manifest together
  and read as done because its first third was.
- **DONE** — ~~`index.html` loads Barlow from `fonts.googleapis.com`, so an
  offline PiFire silently falls back to a different typeface.~~ Barlow is
  self-hosted via `@fontsource`, imported in `src/main.tsx` and bundled at build
  time; `index.html` carries a comment saying so. The offline Pi renders the
  right glyphs. Note the consequence recorded in `pages-fidelity.spec.ts`: the
  fidelity gate is landmark-based rather than screenshot-based because glyph
  *rasterisation* still varies by host, not because the font might not load.
- **OPEN** — no `/manual` route, and now no 404 route either. `blueprints/manual/` was
  deleted by the retirement pass, so a bookmarked `/manual` reaches the SPA
  catch-all, which serves `index.html` — and `components/App.tsx` declares no
  `path: "*"`, so react-router falls through to its default error screen.
  That is true of **every** unknown path, not just this one: the app has no
  not-found surface at all. Whether `/manual` is ported is a separate question
  from whether an unrouted URL renders something deliberate.
- **DONE** — ~~`globals.page_theme` is settable but inert.~~ Renamed
  2026-07-27 to `globals.bootstrap_page_theme`, then **deleted outright
  2026-08-02** once the Flask pages that were its only consumer were gone.
  Removed from `common/defaults.py` and `common/settings_schema.py`, and the
  whole `inject_theme_and_grill_name` context processor went with it —
  `grill_name` was equally unread, `server_error.html` being the only template
  this app renders and taking no context. That also took a `read_settings()`
  out of the 500 path, where a datastore failure would have made the error
  handler raise on exactly the fault it exists to report.

  The `globals.page_theme` -> `globals.bootstrap_page_theme` carry entry went
  too, leaving `_RENAMED_SETTINGS` empty. The carry MECHANISM is kept armed and
  its tests now install a synthetic rename, so they prove it works for the next
  rename rather than for whichever entry happened to be listed.

  **No migration is needed and none was written.** An existing install sheds
  both keys through `validate_settings_tree()`'s repair pass on its next
  validated write — verified against the live database rather than assumed: one
  no-op write, key gone, nothing else lost. The value is discarded, which is
  correct; there is no longer a surface it could affect.

- **DONE, with a consequence worth knowing** — the dashboard's accent swatches
  and General's Theme field write the same key
  (`display.config.<module>.accent_theme`), which the Qt display reads once a
  second, so changing the accent in a browser repaints the attached screen.
  Intended, per ruling 7's sibling decision. Flagged because it is the first
  setting the dashboard writes **without a Save**, so a stray click is
  immediately live on the appliance. Not an open item; a property to remember
  before adding a second such control.

#### Backend behaviour that is broken or lying — MIXED

- **DONE** — ~~The errors blob is write-only from the web tier, and
  `_check_control_status` can false-positive on a healthy system.~~ Fixed
  2026-07-26 (item 4). The blob
  is now read-only from the web tier; liveness is a non-sticky in-memory signal
  composed into each payload. The false-positive vector (the `queue_systemo`
  race) was fixed earlier still.
- **DONE** — ~~`get_os_info(persist=True)`, a destructive flag defaulting to
  true.~~ Fixed 2026-07-26 (item 7): split into `probe_os_info()` + `refresh_os_info()`.
- **DONE** — ~~`backup_pellet_db` is not performed on a React "Load New
  Pellets".~~ Fixed 2026-07-29 (#67). `common/pellets_actions.py::pellets_load_profile`
  snapshots before writing, and since that module is the shared implementation
  both transports call, the REST and Socket.IO doors get it together.
- **DONE** — ~~Notify targets are never converted on a temperature-units
  change.~~ Fixed 2026-07-29 (#35). `_cmd_set_units` converts every armed
  `control["notify_data"]` target through addressed `notify.set` ops
  (`common/common.py::notify_target_conversion_ops`), gated on a real unit
  change and skipping the `target: 0` off-sentinel.

  Both of these were recorded as fixed in the sweep-2 reconciliation above on
  the day they landed, and both were left standing here — the same entry
  claimed open and closed in one file.
- **OPEN** — residual clobber window on the pellet blob; no optimistic
  concurrency. every pellet write is a read-modify-write of the whole blob with
  nothing to detect a concurrent one.

#### Deferred by the events + logs slice — MIXED

Per the standing rule below. `plans/2026-07-28-react-events-logs.md` is the
detailed reference for each.

- **DONE** — ~~`blueprints/events/` and `blueprints/logs/` are still live, and
  still carry their two traversal doors.~~ Closed 2026-07-29 by the retirement
  pass — both blueprints were deleted, so `send_file` and `read_log_file`, and
  the request-field-joined-onto-the-logs-folder pattern they shared, are
  unreachable because the code is gone. This entry's own escape clause ("they
  go with the general Flask retirement pass") is what came true.
- **OPEN** — `datastore.read_log()` still has no PRODUCTION caller. Wording
  updated: it does have callers now and they are all tests
  (`tests/unit/datastore/test_log_retention.py`,
  `tests/web/test_api_admin_clear_events.py`). Those tests use it as an oracle
  for the retention trigger and for clear-events, which is genuinely useful, so
  "delete it or use it" is no longer the whole choice: it is a test-only reader
  of a table the product does not read. The events tab still reads FILES rather
  than the table, deliberately, per the user's ruling — only supervisord logs
  to a file without a database row. Decide whether the table is a product
  surface or a test fixture, and say so where the reader lives.
- **OPEN** — `board-config.py` carries its own duplicate `create_logger`,
  writing to
  `./logs/` directly. It is out of the web tier and was left untouched, so it is
  the one remaining writer that ignores `PIFIRE_LOG_DIR`.
- **DONE** — ~~The wizard's hard links to Flask's `/admin/reboot` and
  `/admin/restart`.~~ The wizard POSTs to the admin API instead
  of linking — `InstallProgress.tsx:74` and `WizardShell.tsx:144` both say so
  in comments that name the retired routes. It had to close: those Flask page
  routes went with the retirement pass, so a link would now be dead rather than
  merely misdirected.

#### Deferred by the metrics slice — MIXED

Per the standing rule below. `plans/2026-07-28-react-metrics-page.md` is the
detailed reference for each.

- **OPEN** — four collected columns are shown nowhere but the raw disclosure.
  `fanontime`/`fanontime_c` and `pellet_level_start`/`pellet_level_end`/
  `pellet_brand_type` are written by `control.py` and named by **no macro** in
  `_macro_metrics.html`, so the port kept parity and left them to the `Raw
  Data` panel. Two are worth promoting: `fanontime` is the only reading of fan
  duty the system has, and the pellet-level delta over a mode is a **measured**
  consumption figure sitting beside an estimate the page does display.
- **MOOT** — ~~`blueprints/metrics/` is still live~~, ~~`_macro_metrics.html`'s
  Hold card still reads the non-existent `grill_settemp`~~, ~~the legacy export
  still doubles its filename~~. All three ceased to exist on 2026-07-29. The
  blueprint, the macro and `test_page_smallpages.py` were all deleted by the
  retirement pass. Each of these deferrals existed only because touching the
  Jinja page would have moved what its characterization test pinned; there is
  no page, no macro and no test left to move. The React surface's fixes stand
  as the only implementations.
- **WON'T DO (for now)** — metrics are shown flat, not grouped by cook.
  Records chain
  Startup → Smoke/Hold → Shutdown → Stop, so a session grouping is derivable;
  it was considered and cut as scope. Order is the server's insertion order,
  oldest first, matching the CSV — a page and its own export disagreeing about
  order would make the two impossible to line up.
- **WON'T DO** — no live refresh; the page reads once on mount. A row is written when a
  mode ENDS and a mode lasts minutes to hours, so a poll would spend requests
  to show the same thing; a running mode's card does show `Active` and an em
  dash rather than a stale end time.

#### Deferred by the tuner slices — MIXED

Per the standing rule below. Both flows have shipped;
`plans/2026-07-28-react-tuner-manual.md` and `-auto.md` are the detailed
references. What remains open:

- **MOOT** — ~~`blueprints/tuner/` is still live, still renders its Jinja
  page.~~ The page ceased to exist on 2026-07-29. What survives at that path is
  `blueprints/tuner/tuner.py`, the maths module, kept deliberately because
  `blueprints/api_tuner/routes.py:34` imports `calc_auto_tune_status`,
  `calc_shh_chart` and `calc_shh_coefficients` from it. It is a library now,
  not a blueprint — `__init__.py` is three lines and registers no route.
- **OPEN** — `calc_shh_coefficients` and `temp_to_tr` still swallow every
  exception into a bare `except:`. Still true, and **this entry's reason for
  not fixing them has expired.** It said `test_page_tuner.py` pins the return shape and
  `tuner.py` has other callers; that test was deleted with the page, and the
  only callers now are `api_tuner/routes.py` and
  `tests/unit/tuner/test_calc_auto_tune_status.py`. The endpoint interpreting
  their output is the whole contract, so tightening the maths is a change to
  one caller, not to a characterized legacy page. `temp_to_tr` remains the
  documented-unreliable inverse — `chart_ok` reports when it fails; nothing yet
  makes it fail less often.
- **MOOT** — ~~`_settings_addprofile` still reports success for a profile it
  never applied.~~ Ceased to exist 2026-07-29: it lived in `blueprints/settings/`, which
  the retirement pass deleted. `POST /api/tuner/profile`, which 404s before
  storing anything, is now the only way to add a profile.
- **OPEN** — the tuner fidelity baseline captures only the pre-Start MANUAL
  screen:
  the three empty segment cards plus the Manual/Auto toggle. The Auto screen
  (reference selector + accumulation card), the curve and the save form are all
  reached only by interaction or a live session, so they are covered by unit
  tests, not the fidelity gate.
- **OPEN** — the auto flow's e2e cannot reach `ready`. A real ≥50 °F spread will not
  occur on a Stopped/Monitor grill during a test, so the live spec proves
  accumulation and the session lifecycle only; the `ready` selection path is
  covered by `test_api_tuner_auto.py`'s seeded twelve-sample test. Nothing
  drives the full converge-and-solve loop end to end against a real grill.
- **DONE** — ~~`history-390x844.json` keeps re-capturing with the tuner
  baselines.~~ Fixed 2026-07-28: the `history` fidelity spec now stubs
  `/api/files/cookfiles` (`stubs: stubCookFiles`), so the saved-cooks section
  renders fixture rows
  instead of the demo backend's live cook files. Both history baselines are now
  byte-deterministic across captures — verified by capturing twice. This closes
  the drift the metrics and both tuner slices had been absorbing.

#### Deferred by the updater slice — OPEN

The `/update` page (`specs/2026-07-29-react-updater-*`, `plans/2026-07-29-react-updater.md`)
is scoped to the updater page itself. One piece of the Flask updater experience
is **global chrome, not the page**, and is deliberately excluded:

- **OPEN** — the post-update "what's new" release-notes modal is not ported,
  and since 2026-07-29 it is **not rendered anywhere at all.** Flask used to set
  `settings["globals"]["updated_message"]` on upgrade and show a one-time modal
  on the next load of any page, from `templates/base.html:165-230`
  (`updater_message_modal` + `updater_message.js`), with a body from
  `GET /update/post-message` (`_update_get_post_message`, which read
  `./updater/post-update-message.html` and `render_template_string`d it — a
  template-injection surface the React port would drop, not reproduce). The
  retirement pass deleted `base.html`, the JS and the route.

  So the state today, verified 2026-08-02: `updated_message` is still SET —
  `_upgrade_settings` sets it on every upgrade regardless of how far the tree
  jumps, pinned by four tests in `test_settings_migration.py` — and
  `updater/post-update-message.html` still ships. Nothing reads either. **A
  writer, a payload and no reader**, which is a worse resting state than the
  deferral described: the flag now silently accumulates instead of being
  consumed. Either build the shell modal or retire the flag and the file
  together; leaving one end live is the option to rule out.

  Still **app-shell chrome triggered by a settings flag on ANY route**, not a
  control on the updater page, so it belongs with the shell. Same item as the
  one-line "updater release-notes modal dropped" under *UI parity,
  minor-graded*; this is its full disposition.

#### UI parity, minor-graded — MIXED

Re-verified 2026-08-02; five of these had closed and two were half-right.

**Still open, confirmed against live code:** History Stream toggle and 5 s poll
(`HistoryPage.tsx:27`) vs Flask's ~1 s; disabled probes silently dropped;
per-probe fill colours configurable but ignored — `line_color*` is written by
`HistoryTab` and read by nothing in `helpers/history/`; History duration is a
bare `NumberField` (`HistoryPage.tsx:156`), not a 1–480 slider; Controller "use
recommended value" buttons; updater release-notes modal dropped (full
disposition under the updater slice — it is shell chrome, not a page control);
per-setting Description dropped by `Select` and `ConfigOptionField`, neither of
which takes a description prop; the `ui_hash` reload prompt has no counterpart,
though `create_ui_hash()` is still published on `/api/status`; discovery results
lost their Refresh/Close controls.

**Half-right, corrected:**

- *Chart annotations fetched but never drawn* — an `annotationPlugin` exists and
  `CookFileChart.tsx:61,98` draws mode-change markers. What remains is narrower:
  `/history` still passes no `annotations`, which `HistoryChart.tsx:22` says in
  its own prop comment. One page, not the feature.
- *Controller metadata card dropped* — the description renders
  (`ControllerTab.tsx:127`). Only the recommended-value buttons are missing.
- *Flask's three dashboard error modals flattened* — the Flask dashboards were
  deleted 2026-07-29, so there is nothing left to be a parity gap against. If
  the flattening is wrong it is now a design question about React's own banners,
  not a port.

**Closed:**

- ~~`display.sleep_timeout` has no control at all~~ — it is in the General tab
  (`GeneralTab.tsx:78`), per ruling 4. Finding #45 recorded this on 2026-07-28
  and this line was never struck. Ruling 4's *other* half — that it must
  actually drive the DPMS behaviour — is unverified here and tracked on the
  ruling.
- ~~Secret masking not ported~~ — `SecretField.tsx` shipped 2026-07-29 across
  all six credential fields, and is written up under *Enhancements accepted
  beyond Flask parity*. Recorded there as shipped and here as open, in the same
  file.
- ~~`pf-section-note` and `pf-kv` have no CSS rule anywhere~~ — `.pf-kv` and
  `.pf-kv-row` have rules (`settings.css:383-395`); `.pf-section-note` is
  *deliberately* unstyled and allowlisted. This is finding #22's ruling, already
  written out above.
- ~~PlatformTab's markup uses classes that do not exist~~ — it uses five
  (`pf-btn`, `pf-kv`, `pf-kv-row`, `pf-section-note`, `pf-settings-tab`) and
  `tests/unit/styleCoverage.test.ts` now fails the build if any class on a
  covered surface has no rule. Its `UNSTYLED` allowlist is asserted for EXACT
  equality, and a second test fails if an entry on it is unused or has gained a
  rule — so the allowlist cannot rot into the thing this line was complaining
  about.

Wizard specifically: no confirmation summary at Finish; install output never
rendered, so a failed install leaves the user blind; Finish error detail (422
`detail`, 400 `sections`) thrown away; "System is active" warning still only at
the last step rather than on entry; per-step explanatory copy dropped; strictly
Back/Next with inert step indicators; tables have no column headers and device
"Type" shows `friendly_name` rather than the module id.

#### Verification gaps — MIXED

Re-verified 2026-08-02. Four of these described code that no longer exists.

**Still open:**

- No unconsumed-field regression check exists — nothing in the tree asserts that
  a setting the schema defines is read by some surface.
- No e2e coverage of Exit Setup / `POST /api/wizard/cancel`. Unit coverage
  exists (`WizardExitRoundTrip.test.tsx`, `WizardShell.test.tsx`,
  `wizardApi.test.ts`); no spec drives it in a browser.
- No **800×480** coverage for the wizard. The `panel` project matches
  `dashboard-panel.spec.ts` only, so the grill's own screen is gated for the
  dashboard and nothing else.
- Accent swaps and Barlow-unavailable rendering: never checked. (WCAG contrast
  is no longer in this list — see below.)
- The reboot-modal flow has never run on real Pi hardware, and the assumption
  that `raspi-config nonint do_onewire 0` writes `dtoverlay=w1-gpio` is
  unverified.

**Corrected:**

- *No phone-viewport coverage for the wizard* — there is: `pages-fidelity`
  captures the wizard at 390×844 as well as 1280×720, and
  `wizard-probes-390x844` is one of the baselines the 2026-07-29 recapture
  diagnosed. Only the 800×480 half of this line survives, above.
- *Three wizard surfaces unreachable by e2e — they rest on the human eye alone*
  — they no longer rest on the eye alone. `CHROME_PROBES` in `pageSpecs.ts`
  renders each `role="dialog"` surface synthetically against a committed
  baseline, which proves the rules resolve and to WHAT. It still proves nothing
  about how they look in situ; that remains the human checkpoint's job.
- *Never checked: WCAG contrast* — ruling 7 measured it when it collapsed the
  two bespoke ramps onto the Qt tokens: the change costs ~2 points of text
  contrast, leaves everything at clear AA, and removed the app's only sub-AA
  value. Not a systematic audit, but no longer "never checked".

**Closed — the code these describe is gone:**

- ~~The `Basic` dashboard (795 lines) has never been compared by anyone.~~
  Deleted 2026-07-29, and ruling 1 had already said in terms not to open this as
  a gap again. It was re-opened here anyway, three lines above the ruling
  forbidding it.
- ~~`probeconfig.js` plumbing vs `probeReducer.ts` validation semantics.~~
  `probeconfig.js` went with `static/js/` in the retirement pass; there is no
  second implementation left to reconcile against.
- ~~`/scan`'s `vid`/`pid` are unwired.~~ Wired: `wizardApi.ts::scan` passes them
  through from the manifest as written (`"0x2a19"`), and
  `common/usb_serial.py::discover_usb_serial_devices` coerces via `_as_usb_id`,
  which accepts an int or a hex string and raises on a malformed one. The
  comment on the client explains that typing them as `number` is precisely what
  made them get dropped before.
- ~~`/admin/restart` and `/admin/reboot` are same-origin and hit the dev server
  rather than Flask.~~ The wizard POSTs `/api/admin/system` now — see the
  events+logs deferral above, where the same item is also struck.

#### Rulings

These were open questions. They are now answered; the entries they resolve are
struck from the list below rather than left to be re-asked.

**A ruling is a DECISION, not a status.** Deciding what to build is not
building it, and three of these were quietly read as "handled" for that reason.
Each therefore carries its own implementation status: rulings 1, 2, 5, 6 and 8
need nothing further, ruling 3 is implemented, ruling 7 is implemented, and
**ruling 4 is only half satisfied**.

1. **DECIDED, nothing to build. React ships ONE dashboard, permanently.** `hidden_cards`,
   `touch_screen_mode`, the dashboard picker and the whole `Basic` dashboard
   (795 lines) are dropped deliberately, not pending. Do not port them, and do
   not open "the Basic dashboard has never been compared" as a gap again — there
   is nothing to compare it to.
2. **REVERSED — ~~`/mobile` will die~~.** Overtaken 2026-07-29. The premise
   was that `/mobile` was a page awaiting retirement. It is not a page at all:
   `blueprints/mobile/` registers no HTTP route, only Socket.IO, and it is the
   live feed the React dashboard itself consumes. The retirement pass therefore
   kept it, registered and untouched, and killing it would take React's own data
   channel with it. The half of this ruling that stands: **stop treating
   `/mobile` as a migration target** — it never was one.
3. **DECIDED and IMPLEMENTED. Settings edits must survive a tab switch**
   (finding #46): drafts are held on `SettingsShell` and survive tab navigation. The
   ruling chose preserving over saving-immediately, because saving immediately
   contradicts the per-tab `SaveBar` and would make every keystroke a control
   write. Losing the edit silently was the only outcome ruled out, and it no
   longer happens.
4. **DECIDED, HALF IMPLEMENTED. `display.sleep_timeout` belongs in the General
   tab.** The
   control shipped (`GeneralTab.tsx:78`, writing `display.sleep_timeout`). The
   ruling's other requirement, that it **actually drive the DPMS behaviour**, is
   not verified: see [[project_qt_display_dpms_sway]] — cage's `wlr-randr --off`
   broke touch-wake on DSI+HDMI, blanking is disabled in the interim, and the
   next step is imperative DPMS under sway/labwc. **A control that renders but
   does nothing is worse than no control**, so this ruling is not satisfied by
   the field existing. This is the open half of the item struck from *UI parity,
   minor-graded* above.
5. **CARRIED OUT. ~~No Flask page is retired until everything else is
   finished~~** — the pass landed 2026-07-29. The one deliberate pass this ruling called for
   happened: 14 page blueprints deregistered and deleted, the shared templates
   and legacy `static/{css,font,js}` with them. Six deferrals elsewhere in this
   file were written as "waits for the general pass (ruling 5)"; all are now
   struck. Cite this ruling only as history — nothing is waiting on it.
6. **DECIDED and IMPLEMENTED. Renaming a probe must not leave stale
   references** — more completely than the ruling asked.
   `common/defaults.py::set_probe_map()` rebuilds
   `history_page.probe_config`, `settings["recipe"]["probe_map"]`,
   `control["notify_data"]` **and** `dashboard[*].custom.hidden_cards` (a probe
   card's id IS its label), and BOTH writers go through it — the live path and
   `run_wizard`, so the installer's identical hole is closed too. See item 9a.1.
7. **DECIDED and IMPLEMENTED. Qt wins for `ok`/`warn`/`danger` everywhere —
   both bespoke ramps are gone.** Two React-only colour sets had no counterpart
   in `Theme.qml` and so
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
8. **DECIDED, nothing to build. The QML kiosk is NOT a web-react target.**
   The Qt Quick (QML) UI is a separate on-device front-end — a fullscreen
   Wayland kiosk on the Pi's
   attached touchscreen (`display/qml/`), sharing only the SQLite datastore and
   command channel with the web tier. The React app was never going to
   reimplement its screens (Splash, Menu, Keypad, Hold/Notify overlays, QR,
   Sleep); the kiosk was only a *visual* target so the web UI matches its look,
   and that borrowing shipped as `Theme.qml` → `theme.css` (guarded by
   `web-react/src/themeTokens.test.ts`). This closes deferred-inventory findings
   **#1** (kiosk screens never built) and **#2** (QML parity check) as won't-do —
   they were a category error the react spike plan introduced, not real gaps.
   The `tests/ui/test_qtquick_*.py` specs remain the kiosk's own net.

#### Still needing a human — NONE OUTSTANDING

No open questions. The design questions in this group were answered by the
rulings above, and the two visual checkpoints that outlived them — item 1's
Task 8 and item 2's responsive layout — were signed off by the owner on
2026-08-02.

Item 1's Task 8 item 7 (the no-photo fallback) was **not performed**, because
it is unreachable: all 62 manifest modules have images. That is a skipped check,
not an outstanding one — it needs a manifest without a photo before anyone can
look at it.

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

#### Schema and toolchain follow-ups — MIXED

Persisted schema versioning is **DONE** (below). All of the following are still
open; none has been started.

S3 defaults consolidation and typed deep-path `setPath` helpers; per-controller
schema generation from `controllers.json`; `<path>: <why>` save-error display;
read-path validation never scoped; mapping a dotted error path to the
offending widget; the four 2b-1 follow-ups (`waitFor`, `read*` fallback
defaults, `aria-describedby`, float-vs-int audit).

`additionalProperties` stripping is **DONE**, and was closed by S2 rather than
by this work. `_Section` is `extra="forbid"`, so every modelled section emits
`"additionalProperties": false` and generates a **closed** interface — a typo'd
field already fails `bun run typecheck`. Exactly six nodes carry
`additionalProperties: true`, all of them deliberately dynamic maps
(`dashboard.dashboards`, `display.config`, `onesignal.devices`, the
`probe_devices`/`probe_info` items, `probe_profiles`); the rest are
`dict[str, X]` value schemas, which must generate an index signature —
`history_page.probe_config` becoming `{[k: string]: ProbeChartConfig}` is
correct output, not leakage. Nothing was left to strip, and stripping the
`dict[str, X]` form would have been a regression.

**DONE 2026-08-02 for both durable blobs; the wizard manifest is deliberately
out of scope (see the corrections below).** The settings tree and the pellet
database each carry a `schema_version`, each has an ordered migration registry
run at `init()` inside the store's own transaction, and each has a committed
shape digest (`common/schema_digest.py`) that fails the suite when the modeled
shape moves without a version bump. Plan:
`plans/2026-08-02-persisted-schema-versioning.md`.

Landed alongside, because modeling the pellet database is what surfaced them:

- `pellets:general` has a pydantic model for the first time
  (`common/pellets_schema.py`), wired into `write_pellet_db()`. The admin
  restore route validated a settings backup and 400'd a bad one, then in its
  next branch wrote whatever JSON a pellet file held straight into the live
  store; it now refuses a malformed one the same way.
- **The pellet log's key-collision bug is fixed.** It was keyed by
  `str(datetime.now())[0:19]` — local time, second resolution — so two loads
  inside one second landed on the same dict key and one entry was silently
  lost. Keys are epoch milliseconds now, and a millisecond already taken is
  advanced rather than overwritten. Nothing in this backlog had recorded it.
- The ungated i2c repair is gone: it is step 1 of `_SHAPE_MIGRATIONS`, so it
  stops scanning on every connect forever.

The original statement, kept because the reasoning below is still the record of
why the wizard manifest is excluded:

**Nothing PiFire persists records the schema it was written against.**
`wizard/wizard_manifest.json` has no version field at all (top-level keys are
`boards`, `modules`, `probe_config_options`), and neither does the
`wizard:install` draft blob. The only version anywhere is
`updater_manifest.json`'s `metadata.versions`, which is the RELEASE version, and
it is demonstrably not a proxy for shape: a real grill database sat at
`1.11.0 build 71` — the code's own current version — while still holding the
pre-71 `i2c_bus_kind`/`i2c_bus_num` settings shape, because the version was
bumped without that migration existing yet.

Two things were built as substitutes rather than fixes, and both should be
replaced by real schema versioning:

- ~~The i2c settings-shape repair runs **ungated**, ignoring the version
  entirely (`common/datastore.py::_upgrade_settings_in_store`), because a
  version-gated migration was skipped by an unchecked path four separate times
  on that branch.~~ **Replaced:** it is step 1 of `_SHAPE_MIGRATIONS`, gated on
  `settings["schema_version"]`.
- **KEPT deliberately.** The wizard draft carries a **SHA-256 fingerprint of the manifest's
  `section/module/dependency` triples** (`blueprints/api_wizard/routes.py`),
  synthesized precisely because the manifest declares no identity of its own. A
  stale draft is otherwise undetectable, and a real one silently rendered
  "Basic" for a grill running on two USB-I2C bridges.

**Design approved and IMPLEMENTED 2026-08-02.** Spec:
`specs/2026-08-02-persisted-schema-versioning-design.md`.

Two corrections the spec makes to the paragraph above, recorded here so they
are not re-raised from this entry:

- **The wizard manifest should NOT get a declared version for the draft's
  sake.** Staleness is a boolean question, and a content hash answers it
  better than a declared version does — a declared version is a promise a human
  remembers to bump, and this branch's own record is that they do not (the
  same paragraph counts four skips). `_manifest_fingerprint` cannot be
  forgotten, so it stays. A manifest version for operator diagnostics is a real
  but separate requirement.
- **Only the settings tree wants an integer**, because only it has to answer
  *which migrations must run*, which is ordered. A hash says THAT something
  differs, never FROM WHAT.

The spec also notes that the mechanism already exists one layer down —
`PRAGMA user_version` at `common/datastore.py:215-250`, currently 4, with
gated ordered steps and the stamp written last. The tables have it; the blobs
stored inside them do not.

The spec also covers **`pellets:general`** — see the entry below. It was going
to be a separate item; the owner folded it in, on the grounds that modeling it
is the prerequisite for versioning it.

**DONE 2026-08-02 — `pellets:general` is modeled, validated and versioned.**
Folded INTO the schema-versioning spec above (§5) at the owner's request rather
than tracked separately, because modeling it is the prerequisite for versioning
it and the two landed together.

Every other durable blob goes through `validate_settings_tree()`'s
strict-plus-repair gate; `write_pellet_db()` calls `write_pellets_store()`
directly with no validation of any kind. It holds operator-owned data (brands,
woods, profiles, the usage log). (`PelletLevel` in `settings_schema.py:65`
models `settings["pelletlevel"]`, the hopper level — a different thing with a
confusingly similar name.)

Sharper than "no migration net", and the reason it is worth doing now: the
admin backup-restore route validates a settings backup and refuses a bad one
with a 400, then in its very next branch writes an arbitrary pellet JSON file
straight into the live store (`blueprints/api_admin/routes.py:320-337`). Same
function, one guarded path and one unguarded one.

Two live defects the spec work turned up in `common/pellets_actions.py`, both
FIXED by the version 2 migration:

- ~~**The usage log silently drops entries.**~~ It was keyed by
  `str(datetime.now())[0:19]` — local time, second resolution — so two profile
  loads in the same second collided on the dict key and one was lost. Both
  writers did it. Keys are epoch milliseconds now, and one already taken is
  advanced rather than overwritten.
- ~~**`rating` is stored unvalidated and uncoerced**~~ from the request at both
  the add and edit doors, so a client could store `"4"` or `99`. It is bounded
  `1..5` at the door and in the schema; existing values were coerced and
  clamped by the migration.

Also worth knowing before anyone models this from `defaults.py`: `est_usage` is
a FLOAT on a live grill (the defaults seed int `0`), a log value can be the
literal string `"deleted"` rather than a profile id, and `brand`/`wood` are
deliberately NOT constrained to the `brands`/`woods` lists.

~~Tailwind prerequisites, all now owned by
`plans/2026-07-26-tailwind-v4-migration.md`: no browserslist pinned; unverified
whether Biome's CSS parser accepts `@theme`/`@apply`/`@import "tailwindcss"`;
**no visual baselines exist for any page at either viewport**; dynamic class
names are invisible to Tailwind's scanner and the required safelist note does
not exist.~~ — **all four resolved by the migration shipping** (item 5,
2026-07-27), which is what "owned by the plan" was always going to mean. A
browserslist is pinned in `package.json:7`, with `rsbuild.config.ts:12` stating
outright that targets are pinned there rather than left to a default; Biome
parses the at-rules (the whole app is authored with them and `bun run lint` is a
gate); there are **61** baseline files across both viewports; and the dynamic
names are handled — `CHROME_PROBES` in `pageSpecs.ts` carries the
runtime-constructed `pf-badge-${tone}` pair with a committed baseline and a
comment explaining that Tailwind's scanner cannot see them.

### 11. Recipes slice — what it deliberately does NOT do — WON'T DO unless asked

Recorded here per the standing rule, rather than left in
`plans/2026-07-27-react-recipes.md`.

**None of these is owed work.** Each is a boundary the slice chose and can
defend; several are places where the React surface is deliberately STRICTER
than Flask. Item 10 is the exception — it describes something that has since
happened on its own.

1. **No recipe comments panel.** Human ruling, 2026-07-27, taken before a line
   was written. `comments.json` is in every `.pfrecipe` and Flask's
   `recipeassetmanager` has a `comments` branch, but **nothing in either UI has
   ever written a recipe comment** (`tests/web/test_page_recipes.py:33-38` says
   so). Building one from the schema alone would invent a feature rather than
   port one. The member is preserved byte-for-byte on every write, because each
   endpoint rewrites only the member it changed. Revisit only if someone asks
   for it as a feature.
2. **`POST /recipes/run` refuses unless `mode == Stop`** (409 `not_stopped`).
   Flask posts from any mode (`static/recipes/js/recipes.js:270-293`). This
   matches the guard `POST /api/probe_map` already applies, and it is the
   difference between a test suite that can exercise the route and one that
   cannot.
3. **Instruction writes reject an unknown ingredient name** (400,
   `data.field == "ingredients"`). Flask does not check. The React multi-select
   can only offer names that exist, so a request carrying an unknown one is a
   bug in something.
4. **Unknown metadata field names and out-of-range indices are refused**, where
   Flask writes them. A negative index is refused rather than wrapping around to
   the last element.
5. **Asset writes are whole-list**, where Flask sends `{action, asset_name}` and
   infers direction. Same reasoning that drove cook-file comments in plan 1: a
   stale client can send an `add` for something already present and be told OK.
6. **The asset lightbox carousel** (Flask's `recipeshowasset`) is not ported.
   The cook-file lightbox exists and could be generalised; it is not a blocker
   for editing a recipe.
7. **No cross-validation of a recipe's steps against the live probe map.** The
   controller remaps `trigger_temps` through
   `settings["recipe"]["probe_map"]` (`controller.py:156-163`), so a recipe
   saved against a different probe map is a real failure mode — but diagnosing
   it is its own piece of work.
8. **No migration for recipes already saved with mismatched units.** The
   conversion is fixed; existing archives are not rewritten.
9. **`get_recipefilelist_details` still reads the module constant
   `file_mgmt.recipes.RECIPE_FOLDER`**, not `current_app.config`, so a fixture
   must patch both. Nothing in the new surface depends on it; it is a trap for
   the next person.
10. ~~**The Flask `/recipes` page stays live**, along with its
    characterization suite.~~ **RETIRED 2026-07-29** with the other 13 page
    blueprints. `blueprints/recipes/` — including the unvalidated
    `RECIPE_FOLDER + filename` concatenation that item 6 above records as the
    reason none of its routes were reused — is deleted, and its
    characterization suite with it.

### 12. Shutdown does not look like what it does — OPEN

**Status:** OPEN. The Qt half is `display-backlog.md` item 2; neither UI has
both halves, and they are missing opposite ones.

**Shutdown does considerably more than end the cook.** This was filed as a
styling item and is not one:

- **Every time.** `ShutdownMode.teardown()`
  (`controller/runtime/modes/shutdown.py:25-27`) calls `grill.power_off()` once
  `shutdown_duration` elapses, opening the platform's master power relay
  (`grillplat/ft232h_relay.py:171-173`, and the equivalent on every other
  platform). Everything fed through that channel loses power.
- **Reported on a user's grill, 2026-08-02:** an FT232H-driven relay board
  disappeared entirely at the end of a shutdown — the USB bridge sits downstream
  of the relay it drives, so opening that relay removed the device from the bus.
  Note what that implies: **the action removes the means of undoing itself.**
  `power_on()` cannot re-close a relay whose controller is no longer enumerated,
  so recovery is physical. Confirm the wiring on the affected build before
  writing this up as general — but design for it.
- **When `shutdown.auto_power_off` is set** — default `False`, a plain toggle on
  the Startup settings tab — `controller/runtime/controller.py:641-643` then runs
  `os.system("sleep 3 && sudo shutdown -h now &")`, halting the host.

So a confirmation is not a nicety, and red is the least of it. The confirm text
should say what will happen rather than ask "Shut down the grill?", and should
say something different when `auto_power_off` is on, because that case takes the
web UI down with it.

**The affordances, as they stand.** Each UI has exactly one of the two:

| | Red | Confirms | Position |
|---|---|---|---|
| Web | **no** | yes — "Shut down the grill?" | Shutdown, then Stop |
| Qt | yes | **no** | Stop, then Shutdown |

Here, `Shutdown` carries no `variant`, so it renders `plain` while `Stop` beside
it is `danger` (`helpers/dashboard/buttonsForMode.ts:73-74` and `98-99` for
Stop; `157` and `195` for Shutdown). Both of the row's ways out of a cook are
destructive and only one of them says so. The confirm is already there on both
Shutdown entries, so this half is a `variant: "danger"` on two lines.

**The orders are inverted, and that is the sharper problem.** Every Qt row
containing both ends `Stop, Shutdown` (`display/qml/Menus.js:101-102, 116-117,
122-123`); every web row containing both ends `Shutdown, STOP`
(`buttonsForMode.ts:157-158, 195-196`). The last button in the row is a
different destructive action depending on which screen you are standing at.
Someone who uses the grill's own display and their phone hits the wrong one.

Whichever way it is unified, it must be unified in both files at once, and the
`ControlButtons` tests that pin row contents assert order, so they will catch a
one-sided change.

**Outstanding evidence.** Which of the two mechanisms fired on the reported
grill is not yet known, and the answer changes the confirm's wording. Its
SQLite file settles it: `settings:general` → `shutdown.auto_power_off`,
`modules.grillplat`, and the `platform` block (the output/pin map plus the
`ft232h` entry, which says whether `power` is the channel the bridge hangs off).
The `logs` table settles it independently — `eventLogger` writes "Fan OFF, Power
OFF" at teardown on every shutdown, and "Shutdown mode ended powering off grill"
only on the `auto_power_off` path.

**Not in scope here.** The admin page's Reboot/Shutdown/Restart
(`components/admin/SystemCard.tsx`) power off the Pi rather than ending a cook.
They are a different control with their own confirmation; this item is the
dashboard's mode button.

### 13. Should P-MODE and SMOKE+ show outside Smoke at all? — OPEN, needs a ruling

**Status:** OPEN. Raised 2026-08-02 while making the pills carry the actuator
duties in Hold; deliberately not decided then.

The duty-pill change switches on `mode === "Hold"` and nothing else, which is
exactly what the attached display does (`DashScreen.qml:21`,
`property bool hold: backend.mode === "Hold"`). Mirroring it was the
conservative reading of "they do not appear in hold mode in the qt UI".

But the request that prompted it was that P-mode and Smoke+ "should only appear
in **smoke** mode" — which is stricter. Under the shipped behaviour they still
appear in Stop, Monitor, Startup, Prime, Shutdown and Manual, describing a
smoke cycle that is not running in any of them.

The two readings differ only outside Hold, and the change is one condition in
`helpers/dashboard/deriveView.ts` (`holding` becomes something like
`mode !== "Smoke"`). What makes it a ruling rather than a fix: going Smoke-only
means **the web UI leads and Qt trails**, so `DashScreen.qml` has to follow or
the two diverge again — and there is a live counter-argument, that P-mode is
editable in Prime, Shutdown, Startup and Reignite (`PMODE_EDITABLE_MODES`), so
hiding the pill in those modes removes a control, not just a readout.

### 14. `clampSetpoint` is superseded — delete it — OPEN

**Status:** OPEN. Noticed 2026-08-02 while giving the setpoint modal the grill's
real ceiling; history traced the same day, which settled what to do with it.

`helpers/dashboard/health.ts` exports `clampSetpoint` and the only thing that
references it is `tests/unit/helpers/dashboard/health.test.ts`.

**It did have callers.** Added in `e466e3ac` with only a test, then wired into
the original `SetpointEntry` by `53fb83de` in three places — the open-seed, the
`bump` stepper, and the slider's `onChange`. It went dead in `4ac5aaad`, the
commit that added the startup hold prompt: that prompt needs a **wider** range
than the Hold setpoint does, so the bounds became `min`/`max` props, and a
module-level function hard-wired to `SETPOINT_RANGE` could not express
per-caller bounds. The component grew a local `clamp` closure in that same
change and never called the helper again.

So it is not merely uncalled, it is **superseded**: `setpointRange(units,
safetyMaxTemp)` plus the component's closure is the shape that does its job now,
and the ceiling it hard-codes is the fixed 500 that item 12's work removed.
Delete it with its test rather than finding it a caller. It was carried forward
(and even given the new `safetyMaxTemp` parameter) only to keep a deletion out
of a behaviour fix.

### 15. Probe cards intermittently read 0 in the web UI — OPEN

**Status:** OPEN. Reported from a live grill 2026-08-02: the web UI sometimes
shows 0 for a probe card while the attached Qt display never falters.

**Confirmed by the reporter:** the pit card is *never* 0, only the food card is,
and it happens throughout the cook, not at its start. That rules out the two
obvious explanations — the fixture seed (`useLiveState.ts:33` starts from
`FIXTURE_DASH`, in which every temp including Grill is 0) and `flush_current()`
(`common/datastore_accessors.py:668`), which zeroes the whole structure. Both
would take the pit down with the food probe.

**A `None` reading becomes a plausible-looking 0, and only on the web.** The
asymmetry is the hardware. On the reporting grill the pit is `mcp9600_adafruit`
— a wired thermocouple that always returns a number — while the food probe is
`thermoworks_cloud`, a network-polled device. The chain:

1. `thermoworks_cloud` caches per-channel readings and returns **`None`** for a
   channel whose cache is older than `poll_interval * _STALE_MULTIPLIER`
   (`probes/thermoworks_cloud.py:88`, 30 s × 3 = 90 s by default). A missed
   cloud poll is exactly the intermittent, mid-cook event described.
2. The Kalman stage passes it straight through — `output_value =
   kalman.update(raw)  # None passes through` (`probes/base.py:373`).
3. `write_current` stores `probe_history["food"]` verbatim
   (`common/datastore_accessors.py:660`), so `current["F"][label]` is `null`.
4. `_get_probe_data` copies it to the wire unchanged
   (`blueprints/mobile/socket_io.py:842`), so the frame carries `"temp": null`.
5. `probeCard()` renders `tempInt: Math.round(fp.temp)`
   (`helpers/dashboard/deriveView.ts`), and **`Math.round(null) === 0`**.

**This is a defect on its own merits, whatever the root cause turns out to be.**
A probe with no reading must not render as `0` — zero is a plausible
temperature, so it reads as data rather than as absence. It should render as
"—" (and `barPct`/`targetStr` should follow). Note also that `LiveState`'s
`ProbeData.temp` is declared `number` while the backend can put `null` there:
the type is lying, and nothing catches it because the fixture was hand-written
with `temp: 0` rather than captured from a live payload.

**CONFIRMED from the grill's control log.** It carries this line, repeating:

```
display/qml/screens/DashScreen.qml:69:7: Unable to assign [undefined] to double
```

`DashScreen.qml:69` is `temp: model.temp` in the food-probe `ProbeCard`
repeater. So the store really is handing out a null for that probe, which is
what could not be established by reading code alone — and it explains why the
two UIs disagree.

**Both UIs receive the same bad value and fail differently.** `ProbeCard.qml:12`
declares `property real temp`, a typed double. QML *refuses* to assign
`undefined` to it: the property keeps its previous value, so the card silently
goes on showing the last good reading and logs the failure every frame.
JavaScript has no such protection — `Math.round(null)` is `0` — so the web
renders a confident zero instead.

**That makes it two defects, in opposite directions:**

- **Here:** a probe with no reading renders as `0`, a plausible temperature.
- **On the display** (`display-backlog.md` item 3): a probe with no reading
  renders as *the last good value*, with nothing to say it is stale, and floods
  the log at frame rate. It only looks correct.

**Decided 2026-08-02 — what it should show.** Not a dash, and not a zero: **the
last good value, marked stale, with how old it is** — "last data 47 s ago" or
similar, in a line beneath the number. Both UIs, worded and placed the same.
A reading that is 40 s old is still worth something to someone deciding whether
to open the lid; what is unacceptable is showing it as though it were live.

Two consequences follow, and they move the work:

- **Nothing publishes per-probe freshness today.** The age cannot be computed in
  either UI. `current["TS"]` timestamps the whole blob, not a probe, and it
  keeps advancing while an individual probe is stale. The right channel already
  exists and is already on the wire: `probe["status"]`
  (`socket_io.py:873-885`), populated per probe from `probe_device_info` and
  currently carrying `connected`, `error` and the battery fields. The data
  exists at the source too — `thermoworks_cloud` caches
  `{channel: (celsius, fetched_at_utc)}` — it is simply never surfaced. So the
  producer publishes the last good reading and its timestamp; it is not a
  presentation-layer fix.
- **Do not track it client-side.** A UI that remembers "the value went null at
  T" loses that across a page reload, and the two UIs would then disagree about
  the same probe's age — which is the class of divergence this whole item is.
  Keeping the last-good value in the producer also keeps `deriveView` a pure
  function of the latest frame, which it is today and should stay.

**The fix is three layers, and the middle one is the real one.**
`ProbeData.temp` is typed `number` while the producer can put `null` there;
`thermoworks_cloud` returning `None` for a stale channel is *correct* — better
than inventing a number — so the type has to admit it (`number | null`) and both
UIs have to render absence as absence. Widening the type is what makes the
compiler find every consumer: `tempInt`, `barPct`, `done` and `targetStr` all
read `fp.temp` today.

The hand-written fixture is why nothing caught this: it carries `temp: 0` for
every probe, a value the live payload does not always produce. Capture the
replacement from a real frame.

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
  uv run watchfiles --filter python 'python control.py' \
      control.py common controller display distance file_mgmt grillplat notify probes &
  uv run gunicorn -k gthread --threads 25 -b 0.0.0.0:5100 -w 1 --reload app:app &
  ```

  **Both halves auto-reload now**, which is the point: `--reload` restarts the
  gunicorn worker when a source file it imported changes, and `watchfiles` does
  the same for `control.py`, which has no equivalent of its own. Started this
  way, the stale-worker failure below cannot happen. Plain
  `uv run python control.py` and a `--reload`-less gunicorn are still correct for
  a real appliance — a file watcher on a Pi is cost for nothing there, and
  nothing restarts a controller mid-cook.

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
- **A stale backend is now caught, not remembered.** A worker started before a
  backend change serves the old code: new endpoints return 404 and the specs
  that need them fail as if the frontend were broken. That cost five separate
  tasks — two blocked outright, the pellets merge showing two red specs because
  the worker was ~13 hours older than the `GET /api/pellets` route, and on
  2026-07-27 a whole recipes run whose failures were confidently misdiagnosed as
  browser colour drift.

  Two things close it. `--reload` (above) prevents it in dev. And
  `GET /api/get/revision` publishes the revision this process imported plus a
  `stale` flag — true when any Python it loaded has changed since it started —
  which `tests/e2e/globalSetup.ts` reads before any spec runs, aborting with the
  reload command rather than producing failures that mean something else.

  `stale` is computed from mtimes rather than by comparing revisions, because in
  this jj-colocated checkout git HEAD tracks the working copy's PARENT and does
  not move when you edit a file — a revision comparison would miss every
  uncommitted change. Absent `stale` (an older PiFire) means "cannot tell" and
  does not block a run, and an unreachable backend is not an error at all: the
  demo-server projects stub every fetch and are meant to run without PiFire.
- **Playwright needs the main checkout or an explicit DB path**, and the suite
  runs `workers: 1` because every spec drives one shared, stateful PiFire.
- ~~The Playwright characterization suite covers all 17 Flask blueprint pages
  (`docs/web-test-findings-2026-07-17.md`) — it is the safety net for each
  page's migration.~~ **The net came down with the pages, 2026-07-29.** It had
  done its job: every page it characterized has a shipped React replacement, and
  the retirement pass deleted or surgically trimmed each spec only after
  confirming coverage on the kept surface — including the admin-restore
  path-traversal containment guarantee, which moved to
  `test_api_admin_backups.py` rather than being dropped. `tests/web/` retains
  exactly one `test_page_*`: `test_page_api.py`. Read
  `docs/web-test-findings-2026-07-17.md` as a historical record of what the
  Flask UI did, not as a description of a running suite.
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
   calling the migration done, ask what lives in the app's chrome that no
   page-shaped item would ever cover.

   `templates/base.html` used to be the place to read for that answer, and it
   was deleted on 2026-07-29 — so the question now has to be asked from the
   React side (`components/shell/`) and from git history. The post-update
   release-notes modal is the one known survivor of that sweep: base.html was
   its only implementation, and deleting the file did not close the item. Assume
   there are others.
2. **Tests that assert text and roles do not assert that a page looks like
   anything.** The wizard shipped with no CSS and a full green suite.
3. **Verifying a feature by calling its API is not verifying the feature.** The
   notify round trip was confirmed working against the backend over REST, and was
   broken in every browser.
