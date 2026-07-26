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
- **Pellets manager** (`/pellets`) — the pellet inventory manager: current
  load-out, hopper level, usage, brand/wood/rating/size vocab tables, profile
  add/edit/delete, and the pellet log. The eight actions that existed only as
  Socket.IO handlers were extracted to `common/pellets_actions.py` so both
  transports share one implementation, and new `GET`/`POST /api/pellets`
  endpoints were added — no path read or wrote the pellet archive over REST
  before. The page is socket-driven off `socket_pellet_data`, which
  `useLiveState` now subscribes to, so it needs no polling and no refetch after
  a write.
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

### 4. The errors blob is write-only from the web tier

`read_errors()` (`common/datastore_accessors.py:126-132`) is a plain,
non-destructive JSON read — unlike `warnings` on the very same payload, which
drains and self-heals frame to frame. Its only clearer, `flush_errors()`, has
exactly one production caller: `control.py:107-109`, at boot. So once
`_check_control_status` (`blueprints/mobile/socket_io.py:1009-1019`) appends
"The control process did not respond…", that string is on every
`socket_dash_data` frame until `control.py` restarts. No HTTP route, socket
action or API command clears it.

Worse, it can be written on a **healthy** system: `get_system_command_output`
(`common/app.py:31-44`) pops the shared `queue_systemo` and discards every entry
whose command does not match, so any of its seven consumers can eat the
`check_alive` reply, the 1 s timeout expires, and the sticky error lands anyway.
Same class as triage Slice 9 item 1, which already owns
`get_system_command_output`.

The frontend has done what it can: it no longer withholds Stop/Shutdown on this
signal, and offers a **Recheck** that asks `GET /api/sys/check_alive` directly
(`web-react/src/helpers/dashboard/controlHealth.ts`). The backend half — an
endpoint that clears the error, or a liveness signal that is not sticky — is
still open.

### 5. Tailwind v4 migration — SPEC WRITTEN, UNBLOCKED

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

### 6. Remaining audit findings

`docs/superpowers/audits/2026-07-25-audit-triage.md` — roughly 40 findings in 10
slices. Slices for save-failure, notifications, guards, and the dashboard have
been written and executed or planned; the rest are untouched.

### 7. Accessor rename WAVE 2

Four remaining items, including `read_warnings()` → `drain_warnings()`. That one
is a genuine cross-consumer bug, not just a naming problem: the dash routes and
socketio both call it, so whichever polls first consumes the other's warnings.

### 8. Un-migrated Flask pages

Roughly ordered by daily-use value:

- [x] **pellets** — SHIPPED 2026-07-25 (`plans/2026-07-25-react-pellets-page.md`,
      13 tasks). Listed here as open until 2026-07-26 purely because this entry
      was never struck; see the SHIPPED section for what landed.
- [ ] **admin** — restart/reboot/shutdown, backups. Every action shells out, so
      the tests for it MUST neutralize `os.system`/`subprocess` before anything
      runs — an `is_real_hardware()` flag is not enough, and this repo has
      really rebooted the developer's machine twice that way.
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
