# React Dashboard Slice — Guards, Truth, Lost Controls, and the Reflow

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close Slice 3 of `docs/superpowers/audits/2026-07-25-audit-triage.md` — the dashboard's missing safety guard, its sticky control lockout, its fabricated cook time, its lost readouts and controls — **and** reverse the fixed 1280×720 uniformly-scaled stage so the dashboard reflows, **without changing how it looks at 1280×720**.

**Architecture:** Two halves in one plan because they edit the same six files.

1. **Behaviour** (Tasks 2–10): pure logic goes to `helpers/dashboard/*`, presentation stays in `components/dashboard/*`, every new control routes through the existing `buttonsForMode` → `ButtonAction` dispatch.
2. **Reflow** (Tasks 11–14): inline styles → CSS classes → size custom properties → media queries. **Every responsive rule lives inside a `@media` query whose condition is false at 1280×720**, so desktop fidelity is preserved *by construction*, not by care. A committed landmark-geometry baseline (Task 1) proves it commit by commit.

**Tech Stack:** React 19 + react-router 8, TS7 (`typescript7`/tsgo), rsbuild, Biome + eslint, @rstest/core, Playwright, bun.

---

## Ratified decisions this plan is built on

| # | Decision | Effect here |
|---|---|---|
| **C8** | **The dashboard must REFLOW.** `helpers/dashboard/hooks.ts:13-27`'s fixed 1280×720 stage and `useFitScale` are reversed. **Binding constraint (user's words): "does not have to be pixel perfect, but very close" at 1280×720.** | Tasks 1, 11, 12, 13, 14. 1280×720 is a **regression target, not a free variable** — see *The Fidelity Contract* below. |
| **M5** | **One dashboard forever.** No dashboard picker, no `hidden_cards`, no `touch_screen_mode`, no port of `Basic`. | See *What M5 removes from this slice* below. |

### What M5 removes from this slice

**Nothing in Slice 3 assumed dashboard selection existed** — I checked each of the nine findings against it. What M5 does change is the *reflow*:

- **No per-card hide/show.** Flask's `hidden_cards` (`blueprints/dash/templates/default/dash_default.html:25-27, 42-44, 58-60, 66-68`) server-renders `display:none` on individual columns. The React reflow therefore has **one** layout to keep correct per breakpoint, not 2ⁿ.
- **No `touch_screen_mode` branch.** Verified zero hits in `web-react/src`. The reflow does not need a separate touchscreen mode; the small-viewport branch serves the 800×480 on-device panel too.
- **`Basic`'s click-to-toggle manual outputs are reassigned to the un-migrated *manual* page backlog item. Do not plan or build them here.** For the record, and so nobody re-litigates it: React **already ships** those toggles — `helpers/dashboard/buttonsForMode.ts:56-70` returns Power / Igniter / Auger / Fan / Fan % / Stop in `Manual` mode, per decision D1 in `docs/superpowers/plans/2026-07-24-react-manual-control.md`. The reassignment is very nearly a no-op. Still not this slice's business.
- **`_get_probe_max_temp` already hardcodes `settings["dashboard"]["dashboards"]["Default"]["config"]`** (`blueprints/mobile/socket_io.py:892-904`), so the backend is already single-dashboard on the path React reads. M5 ratifies what the wire already does.

---

## THE FIDELITY CONTRACT — what "very close" means, numerically

This is the hard part of the plan. Read it before touching any layout file.

### What is pinned

**The dashboard's own 1280×720 layout, measured in stage-local coordinates**, is the thing that may not change. Not the on-screen pixel size at a 1280×720 browser window.

That distinction is not a dodge — it is forced by what the code does today, and a reviewer should understand why:

- Inside the app shell, `.pf-fit` is the area **below the navbar** (`components/shell/shell.css:28-30` makes it `position: absolute` inside `.pf-shell-main`), and `useFitScale` measures that box (`helpers/dashboard/hooks.ts:36-41`). At a 1280×720 window the navbar consumes ~50–56 px, so the stage renders today at **scale ≈ 0.92, not 1:1**.
- Worse, that number **is not stable**: the shell inserts `<TimerBar>` when the timer strip is toggled (`components/shell/AppShell.tsx:39`) and `<Banners>` whenever an error or warning arrives (`:40-44`). Each appearance shortens `.pf-fit` and re-scales the whole dashboard — a `ResizeObserver` is wired specifically to catch it (`hooks.ts:44-48`).

So "how it looks at a 1280×720 window" is already a moving target that changes when a warning appears. The stable, authored thing — and the thing a human means by "how the dashboard looks" — is the **1280×720 layout**. That is what we pin.

**The literal check is still provided**, as a human artifact: clock-frozen PNGs of the stage at a literal 1280×720 window, before and after. See mechanism (B).

### (A) The gate — landmark geometry, committed, automated

`web-react/tests/e2e/dashboard-fidelity.spec.ts` measures ~17 named landmarks (elements carrying `data-pf="…"`) and compares them to a **committed baseline JSON**, `web-react/tests/e2e/dashboard-layout-1280x720.json`.

Coordinates are **stage-relative and scale-normalised**: for each landmark, `x = round((rect.left - stageRect.left) / s)` where `s = stageRect.width / 1280`. This is exactly the authored layout number regardless of any uniform `scale()`, so the same spec is meaningful before the reflow (where `s ≈ 0.92`) and after it (where `s === 1`).

**Tolerance — two tiers:**

| Tier | Landmarks | Tolerance |
|---|---|---|
| **Authored constants** | `stage` 1280×720; `header` h58; `probeCol` w298; `rightCol` w300; `controls` h82; `cookRow` h52; `pills` h64 | **±0.5 px** — these are literals in the source (`Dashboard.tsx:93,195,234,294,308`, `ControlButtons.tsx:53`), so anything other than the literal is a real change |
| **Everything else** | all `x` / `y` / `w` / `h` of every landmark | **±2 px** |
| **Typography** | `font-size` and `font-weight` of every landmark | **exact string equality** |

**Why ±2 px:** at 1280 px wide a 2 px shift is 0.16 % of the layout, which is below the threshold at which flipping between two screenshots shows movement; it absorbs the sub-pixel remainder of flex distribution and `gap` rounding and nothing else. A 3 px shift is a design change and must be argued for, not absorbed.

**Why font sizes are exact:** the whole visible character of this dashboard is its typography (66 px probe temps, 112 px gauge number, 25 px buttons). A font size that drifts by 1 px is the single most visible thing that can go wrong in a reflow, and there is no rounding source that would produce one accidentally.

**What a reviewer does when it drifts.** The baseline is committed, so drift shows up as a **JSON diff naming the landmark**. The standing rule:

> **A reflow commit (Tasks 11–14) may not edit `dashboard-layout-1280x720.json`.** If the gate fails, the reflow is wrong — fix the CSS, do not re-baseline.
>
> **A behaviour commit (Tasks 2–10) may edit it**, in a commit that does nothing else to layout, and the commit message must name each changed landmark with its old and new value and one sentence of why. Reviewer flips the two PNGs from (B) and either accepts or bounces it.

### (B) The artifact — clock-frozen screenshots, human, not a gate

The same spec writes `web-react/tests/e2e/artifacts/dashboard-1280x720.png` (gitignored) on every run: `.pf-stage` only, `animations: "disabled"`, and **`page.clock.pauseAt(...)` before navigation** so `useClock`, `helpers/clock.ts`'s shared interval and `demoDashAt(elapsed)` all freeze — with the demo data source that makes the rendered content fully deterministic.

**It is deliberately not a `toHaveScreenshot()` gate.** Two reasons, both verified:

1. `web-react/index.html:7-11` loads Barlow from `fonts.googleapis.com` (audit M3). Font rendering therefore depends on the network and on the host's font stack, so a pixel gate would fail on any machine that renders Barlow a hair differently — and would fail *hardest* on exactly the typography we care most about.
2. Masking the volatile regions to stabilise it would mask the clock, the cook-time value and the probe temperatures — i.e. it would blind the gate to the type scale it exists to protect.

Task 1 keeps a `before.png` copy; every later task overwrites `after.png`. Reviewer flips between them.

### (C) The reflow assertion — a second viewport

`@media` queries prove nothing on their own. Task 14 adds a **390×844 Playwright project** asserting the reflow actually happened: no horizontal page overflow, probe temperature computed font-size ≥ 36 px, every control button ≥ 44 px tall, and stage width == viewport width (not letterboxed). This is the triage's Slice 10 item 3, pulled in here because C8 is what it was waiting on.

### What cannot be verified in unit tests — say it plainly

**jsdom does no layout.** `getBoundingClientRect()` returns all zeros, `offsetWidth`/`offsetHeight` are 0, and `getComputedStyle()` does not resolve `calc()`, custom properties, flex distribution or media queries. `helpers/dashboard/hooks.ts:25-27` already documents this ("jsdom never lays anything out"), and `fitScale.test.tsx` exists only because of the `window.innerWidth` fallback that behaviour forces.

Therefore: **every assertion in this plan about reflow, box geometry, font size or breakpoint behaviour is a Playwright assertion or a human looking at a PNG.** `bun run test` (rstest/jsdom) can and does assert:

- which class names are applied to which element,
- that no layout-bearing `style={{...}}` remains on the elements Task 11 converts (a source-text assertion, in the style of `src/structure.test.ts`),
- that `dashboard.css` contains the expected `@media` blocks and the expected custom-property names (a file-read assertion),
- all pure logic (countdowns, button sets, badge derivation).

It **cannot** assert that anything reflowed. Do not write a jsdom test that claims to.

---

## Global Constraints

- Test runner is **@rstest/core** (`rs.fn`, `rs.mock`, `rs.stubGlobal`) — `vi` does **not** exist. `.test.tsx` → jsdom, `.test.ts` → node.
- **bun**, never npm.
- **No suppressions**: no `biome-ignore`, no `@ts-expect-error`, no `eslint-disable`.
- **No `setState` in `useEffect` for derived state** (React Compiler). Render-phase adjustment — copy `components/settings/tabs/SafetyTab.tsx` and `components/dashboard/SetpointEntry.tsx:13-23`.
- `react-refresh/only-export-components`: non-components live in their own module. All pure logic goes under `helpers/dashboard/`, never in a `.tsx`.
- Reuse the `pf-*` class vocabulary and theme tokens (`src/theme.css`, `components/dashboard/dashboard.css:117-241`, `components/shell/shell.css`). Do not introduce a second visual language.
- **Exactly one `useLiveState()` call site.** `src/structure.test.ts:100-115` fails the build if any file other than `components/shell/AppShell.tsx` imports it. Nothing in this plan may add a second — the dashboard reads live state from props, which `components/DashboardRoute.tsx:15` gets from `useShellState()`.
- Gate: `bun run typecheck && bun run lint && bun run test && bun run build && bun run gen:types:check`. Plus `bun run test:e2e` for any task touching Playwright.
- `bun run lint` exits 0 today (2 known pre-existing warnings). Add none.

---

## Verified facts — checked against live code at `b2515c7b`. Do not re-derive; do not guess.

> **Reading note:** the app shell has landed on a different jj line than the docs line. Every `web-react/src` citation below is against commit **`b2515c7b`** ("fix(timer): compute the timer end server-side…"), the head of the shell line, which is the state this plan executes against. `components/shell/` does not exist in the docs-line working copy.

### Which findings survived verification

**Nine of the ten items assigned to this slice hold. Two are dropped, and neither for being false.**

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| **I3** | Startup lost its confirmation | **HOLDS** | `buttonsForMode.ts:30-34` — `STARTUP` is a bare `cmd((c) => c.setMode("startup"))`. Stop and Shutdown keep theirs (`:25-29`, `:81`). `startupCheck`, `startToHoldPrompt`, `startupGotoTemp`, `startupGotoMode` appear **only** at `types.ts:65-68` |
| **D1** | `controlAlive` can stick false | **HOLDS — and is worse than graded** | see *The `controlAlive` mechanism* below |
| **C3** | Cook time derived from client mount | **HOLDS** | `Dashboard.tsx:52-60` seeds `cookStart` from `now.getTime()`; `startupTimestamp`/`modeStartTime` appear only at `types.ts:53-54` |
| **I4** | Recipe/mode/lid readouts lost, Recipe offers breaking controls | **HOLDS** | `buttonsForMode.ts:73-83` falls through for **any** unrecognised mode. `control["mode"]` **is** the literal string `"Recipe"` (`common/modes.py:22` `RECIPE = "Recipe"`), so `dash.currentMode === "Recipe"` reaches that fallthrough today |
| **I1** | P-Mode displayed, not settable | **HOLDS** | `deriveView.ts:132-139` builds the pill; `Dashboard.tsx:308` renders it through `Pill` (`:319-356`), a plain `<div>`. `setPMode` exists at `command.ts:32,122` with no caller |
| **I2** | Prime reduced to one amount | **HOLDS** | `buttonsForMode.ts:42` — `c.prime(dash.primeAmount \|\| 10, "startup")`, no amount choice, no "prime then stop" |
| **M6** | Probe connected/battery badges dropped | **HOLDS** | `types.ts:7,9` declare `batteryPercentage?`/`connected?`; type-only, no reader |
| **M7** | Probe ETA dropped | **DROPPED — owned elsewhere** | `docs/superpowers/plans/2026-07-25-react-probe-notifications.md:180-181` **already claims it**: Task 4 adds `etaStr` to `ProbeCardView` in `deriveView.ts`, Task 5 renders it in `ProbeCard.tsx`. Building it here would be a duplicate implementation and a guaranteed merge conflict |
| **M8** | Hopper Refresh / Manager dropped | **HALF DROPPED** | Refresh **holds** and is built (Task 10). The **Manager link is dropped**: it targets `/pellets`, a Flask page (`app.py:98` `url_prefix="/pellets"`) with no React counterpart, and `docs/superpowers/plans/2026-07-24-react-app-shell.md:59` records the decision *"Do not link them to the Flask pages"* — linking out drops the live socket. Record the divergence, do not build it |
| **C8** | Fixed 1280×720 stage | **HOLDS** | `hooks.ts:13-27`, `Dashboard.tsx:46,64-69`. Now ratified as *reverse it* |

**Nothing was dropped for being factually wrong.** Three of the audit's supporting details **were** wrong and are corrected below (I3's modal count, I4's line range and field name, and the audit's implicit assumption that Flask disables its controls when the control process is down — it does not).

### Corrections to the audit — these matter to the implementation

**I3 — Flask has ONE modal with two Jinja-selected variants, and the hold-prompt variant WINS.** The audit reads as if there are two independent modals. There are not.

`templates/_macro_control_panel.html:89-90`:
```jinja
{% set startup_modal_enabled = settings['safety']['startup_check'] or (settings['startup']['start_to_mode']['start_to_hold_prompt'] and settings['startup']['start_to_mode']['after_startup_mode'] == 'Hold') %}
<button ... id="startup_btn" onclick="cpStartupCheck('{{startup_modal_enabled}}');">
```
`#startupModal` (`:207-254`) then picks its body with the *same* condition twice (`:214` title, `:224` body):

- **Hold-prompt variant** — when `after_startup_mode == 'Hold' and start_to_hold_prompt`. Title `Change Hold Temp?`, body `Change Hold Temperature after Startup?`, a numeric input + range slider seeded from `settings['startup']['start_to_mode']['primary_setpoint']`, **range 125–600 step 5 for °F (`:236`), 50–260 for °C (`:238`)**. Action `cpStartupHold(value)`.
- **Safety-check variant** — otherwise. Title `Startup Check`, body `Confirm Startup Grill?`, nothing editable, action `cpStartup()`.

**If `safety.startup_check` is true AND the hold prompt is configured, the hold variant wins and "Confirm Startup Grill?" is never rendered.** There is no path showing both.

`static/js/control_panel.js:394-401`, the stringly-typed trigger:
```js
function cpStartupCheck(enable) {
    if (enable == 'False') { cpStartup(); } else { $('#startupModal').modal('show'); };
};
```
(a Python-stringified bool compared against `'False'`; do **not** port that shape).

`control_panel.js:413-428` — `cpStartupHold` writes the setting **first**, then starts:
```js
var postdata = { 'startup': { 'start_to_mode': { 'after_startup_mode': 'Hold', 'start_to_hold_prompt': true, 'primary_setpoint': parseInt(targetTemp) } } };
cp_api_settings_post(postdata);   // POST /api/settings
cpStartup();                      // then POST /api/control {'updated':true,'mode':'Startup'}
```
Both are fire-and-forget `$.ajax` with no chaining — **a genuine race in Flask. React must `await` the settings write before setting the mode.**

Dead code, do not port: `control_panel.js:442-444` resets `$('#startupSlider')`, an id that does not exist in the template.

**I4 — line range and field name.** The mode-countdown block is `blueprints/dash/static/default/js/dash_default.js:348-368` (not 355-367) and reads **`current.status.start_time`**, which is the payload's `modeStartTime` (`blueprints/mobile/socket_io.py:235`). There is no field called `mode_start_time`. Verbatim:
```js
if (['Prime', 'Startup', 'Reignite', 'Shutdown'].includes(mode)) {
    var duration = 0;
    if (['Startup', 'Reignite'].includes(mode)) { duration = current.status.start_duration; }
    else if (mode == 'Prime') { duration = current.status.prime_duration; }
    else { duration = current.status.shutdown_duration; }
    var now = Math.floor(new Date().getTime() / 1000);
    var start_time = Math.floor(current.status.start_time);
    var countdown = Math.floor(duration - (now - start_time));
    if (countdown < 0) { countdown = 0; };
    $('#mode_timer').html(countdown);
};
```
All values are **seconds**. Rendered by `blueprints/dash/templates/default/_macro_dash_default.html:373`:
```html
<b id="mode_timer_label" style="color:green;display:none">Time Left in Mode: <span id="mode_timer"></span>s</b>
```
— i.e. literally `Time Left in Mode: 12s`. The `s` is static; only the integer is injected. **No `MM:SS`.**

Lid countdown, `dash_default.js:386-398`, field is **`lid_open_endtime`** (payload `lidOpenEndTime`, `socket_io.py:238`), shown **only in Hold**:
```js
$('#lid_open_label').html('Lid Open Detected: PID Paused ' + countdown + 's');
```

**C3 — Flask's inactive branch and its duration format.** `dash_default.js:400-412`:
```js
if (current.status.startup_timestamp != 0) { ... $('#time_elapsed_string').html(formatDuration(time_elapsed)); ... }
else { $('#time_elapsed_string').html('--'); document.getElementById('time_elapsed_string').className = 'text-secondary'; };
```
Inactive renders the literal **`--`**, not `00:00`. And `formatDuration` (`dash_default.js:599-611`) is **adaptive**: `HH:MM:SS` above an hour, `MM:SS` above a minute, `NNs` below. React's `fmtDuration` (`deriveView.ts:179-187`) has no `NNs` branch and does not zero-pad hours. See Task 3 for the decision.

`startup_timestamp` is epoch **seconds** (float), set at `controller/runtime/modes/startup.py:120`, deliberately **not** rewritten by Reignite (`controller/runtime/modes/reignite.py:17-18`), zeroed at `controller/runtime/controller.py:405,428`, and `math.trunc`'d onto the wire at `socket_io.py:234`.

### The `controlAlive` mechanism — the full trace

`helpers/dashboard/health.ts:5-9` sets `controlAlive = false` when any entry in `dash.errors` contains `"control process did not respond"`. `ControlButtons.tsx:71` then sets `disabled` on **every** button, Stop and Shutdown included.

**Producer** — `blueprints/mobile/socket_io.py:1009-1019`, run every 30 s from the emit loop (`:160-168`):
```python
def _check_control_status():
    errors = read_errors()
    process_command(action="sys", arglist=["check_alive"], origin="app-socketio")
    data = get_system_command_output(requested="check_alive")
    if data["result"] != "OK":
        error = "The control process did not respond to a request and may be stopped. ..."
        if error not in errors:
            errors.append(error)
            write_errors(errors)          # PERSISTED
```

**Why it sticks.** `common/datastore_accessors.py:126-132` — `read_errors()` is a plain, **non-destructive** JSON blob read. Compare `warnings` on the very same payload (`:163-173`), which *is* a drain (`q.list(); q.flush()`) and therefore self-heals frame to frame. The only clearer is `flush_errors()` (`:135-151`), whose **single production caller is `control.py:107-109` at boot**. There is no HTTP route, no socket action and no API command anywhere that clears the errors list.

**Consequence:** once written, the error is in every subsequent `socket_dash_data` frame for the life of that control process. `controlAlive` stays false. The only recovery is restarting `control.py`.

**And it can fire on a healthy system.** `common/app.py:31-44` — `get_system_command_output` pops from the shared `queue_systemo` and **discards every entry whose command does not match**:
```python
while system_output.length() > 0:
    data = system_output.pop()
    if data["command"][0] == requested:
        return data
```
Seven consumers share that queue (`blueprints/dash/routes.py:33`, `blueprints/mobile/socket_io.py:1013`, `blueprints/api/routes.py:309`, `blueprints/api_wizard/routes.py:425`, `blueprints/wizard/routes.py:133`, `common/app.py:24`, `common/system.py:296-335`). Any of them racing the 30 s check can eat its `check_alive` reply → the 1 s timeout expires → the sticky error is written **on a perfectly alive system**. (This is the same class of bug as triage Slice 9 item 1, which already owns `get_system_command_output`.)

**What Flask does instead: nothing to the controls.** Verified — there is no `disabled` attribute keyed on control liveness anywhere in `blueprints/dash/templates/default/dash_default.html` or `static/js/control_panel.js`. Flask renders a **dismissible** red banner from the errors list (`templates/base.html:117-125`) and leaves every button live. Its separate "Server is Unresponsive / Offline" modal (`dash_default.html:119-142`) is about the **web** server, not the control process: it counts consecutive `/api/current` failures to 30 (`dash_default.js:4-5, 462-467`) and **clears itself on the first success** (`:151-155`). It never gates controls either.

**So the failure mode is entirely React's own making**, and Flask's behaviour is the reference. Task 2 acts on that.

**Recheck path that exists today:** `GET /api/sys/check_alive` (`blueprints/api/routes.py:299-311`) runs exactly the same probe and returns `{"result": "OK", ...}` when control replies (`grillplat/system_commands.py:79-80`). It does **not** clear the persisted error. There is no `/api/cmd/clear_errors`.

### Flask reference for each remaining finding

**I1 — P-Mode.** `_macro_dash_default.html:89-106`, inside `{% if probe_data['type'] == 'Primary' %}` (`:84`) — **primary probe card only**. Ten items: `0 - Off`, then `1`…`9`. Visibility (`dash_default.js:248-293`):

| Mode | `#pmode_group` (the control) |
|---|---|
| Prime, Shutdown, Startup, Reignite, Smoke | **shown** |
| Hold | **hidden** |
| Stop, Monitor, Recipe, Manual, Error | **hidden** (badge/icon still shown) |

Flask issues **two chained POSTs** (`dash_default.js:904-933`): `/api/settings {"cycle_data":{"PMode":n}}` then `/api/control {"settings_update": true}`. **React's single `POST /api/set/pmode/{n}` does both** — `common/api_commands.py:377-383` writes the setting *and* sets `control["settings_update"] = True`, and validates `0 <= n < 10`. Use the existing `command.setPMode` (`command.ts:122`); it is strictly better than the Flask path.

**I2 — Prime.** `templates/_macro_control_panel.html:74-87`, six items, `control_panel.js:65-73`:

| Label | Sends |
|---|---|
| Prime 10g / 25g / 50g | `{mode: 'Prime', prime_amount: N, next_mode: 'Stop'}` |
| Prime 10g / 25g / 50g & Startup | `{mode: 'Prime', prime_amount: N, next_mode: 'Startup'}` |

React's `command.prime(grams, next?)` (`command.ts:123-124`) already takes both. Flask's prime group is shown only in the inactive branch (`control_panel.js:121`; server gate `_macro_control_panel.html:72` — `Stop/Startup/Monitor/Error`), which matches React's placement in the `Stop`/`Error`/`""` branch of `buttonsForMode`.

**I4 — Recipe.** `dash_default.js:297-300` sets the status header to `Recipe | <step mode>`. The control panel hides `#active_group` **and** `#inactive_group` outright (`control_panel.js:181-182`) and shows `#recipe_group` (`_macro_control_panel.html:128-140`) — exactly four controls:

1. **Goto Next Step** — `control_panel.js:520-534`: when paused+triggered → unpause (`POST /api/control {recipe:{step_data:{...pause:false}}}`); otherwise `POST /api/control {updated:true}`. Glows when paused (`:207-217`).
2. **Step N** — an `<a href="/recipes">` (a Flask page; see M8's precedent).
3. **Mode indicator** — read-only, hidden when the step mode is Shutdown (`:197`).
4. **Shutdown** — `POST /api/control {updated:true, mode:'Shutdown'}` (`:456-463`).

Payload basis: `recipeStatus.recipeMode` = `status["recipe"]` = `control["mode"] == Mode.RECIPE` (`controller/runtime/modes/base.py:478`); `recipeStatus.mode` and `displayMode` are both `status["mode"]`, the running **sub-mode**. **Use `dash.recipeStatus.recipeMode`, not a string compare on `currentMode`** — it is the boolean the controller actually publishes.

**M6 — badges.** Connected pill (`_macro_dash_default.html:20-37`) renders **only when the `connected` key exists**; `blueprints/mobile/socket_io.py:858-859` copies it only `if "connected" in status`, and only Bluetooth drivers set it (`probes/bt_ibbq.py:361`, `probes/bt_meater.py:284`, `probes/disabled.py:60`). Icon-only with a tooltip: `Connected` (green) / `Disconnected` (light, with a slash overlay). Battery (`:39-64`, JS `:492-538`) — five states, three colours:

| Condition | Badge | Icon | Tooltip |
|---|---|---|---|
| `null` | light | empty + `?` | `Unknown` |
| `< 10` | danger | empty | `N%` |
| `< 40` | warning | half | `N%` |
| `< 90` | success | three-quarters | `N%` |
| `>= 90` | success | full | `N%` |

**Do not copy one Flask bug:** `dash_default.js:443,453` writes `battery_percentage || null`, which coerces a genuine **0 %** into `Unknown`. React must render 0 % as `0%` in the danger state.

**M8 — hopper Refresh.** `_macro_dash_default.html:358-359`, label **"Refresh Status"**. `refreshHopperStatus()` (`dash_default.js:878-897`) `POST /api/control {'hopper_check': true}`, then re-reads `GET /api/hopper` after 500 ms (`blueprints/api/routes.py:78-83`, `:114`). The whole hopper card is hidden when `settings['modules']['dist'] == 'none'` — the payload's `hasDistanceSensor` (`socket_io.py:245`) is exactly that flag, and it currently has **zero consumers**, so `HopperGauge` renders for grills with no sensor.

### Current layout system — what is fixed, what already reflows

| Thing | State | Where |
|---|---|---|
| `.pf-fit` | `position: fixed; inset: 0; display: grid; place-items: center; overflow: hidden` | `dashboard.css:85-92` |
| `.pf-fit` inside the shell | overridden to `position: absolute` so it measures below the navbar | `shell.css:28-30` |
| `.pf-stage` | **`width: 1280px; height: 720px`**, `position: absolute; top/left: 50%`, `overflow: hidden`, `transform-origin: center center` | `dashboard.css:93-111` |
| The scale | `transform: translate(-50%,-50%) scale(${scale})` set **inline** | `Dashboard.tsx:68` |
| `useFitScale` | measures **its own box** via a ref + `ResizeObserver`, falling back to `window.innerWidth/innerHeight` when the box is 0×0 (jsdom). **This is its current state — it was changed from `window.innerHeight` when the shell landed.** | `hooks.ts:28-56` |
| Everything else | **inline `style={{...}}` objects with literal px** | `Dashboard.tsx:70-355`, `ProbeCard.tsx:6-67`, `ControlButtons.tsx:46-56,60-77`, `GrillGauge.tsx`, `HopperGauge.tsx`, `SystemStatus.tsx` |
| What already reflows | **nothing.** Only `flex: 1` inside the fixed 1280×720 box | — |

**What `overflow: hidden` clips today, and why one of them must survive:**

- `.pf-stage`'s clips the decorative radial glow (`Dashboard.tsx:70-84` — `position: absolute; bottom: -160; width: 820; height: 420`). **The dashboard root must keep `overflow: hidden` (or `clip`) after the reflow or that glow bleeds into the page.**
- `.pf-fit`'s clips the letterboxed remainder of the scaled stage. That one becomes unnecessary once the scale goes.

**`.pf-fit` is shared chrome — do not repurpose it.** Five consumers: `App.tsx:30` (HydrateFallback), `DashboardRoute.tsx:42` (ConnectionStatus), `SettingsError.tsx:6`, `WizardShell.tsx:52,57`, and `Dashboard.tsx:64`. The dashboard gets a **new** root class; the other four keep `.pf-fit` exactly as it is.

**Flask's grid, for reference** (`dash_default.html:22-73`): one `.row` holding `col-lg-4 col-md-6 col-sm-12` columns — one per primary probe (`:24`), one per food probe (`:41`), status (`:57`), time-elapsed (`:65`). 3-up at ≥992 px, 2-up at 768–991, 1-up at 576–767. **Caveat worth reproducing better than Flask does:** there is no `col-12`, so below 576 px the columns fall outside the grid's control entirely. The control panel is a `navbar fixed-bottom` (`_macro_control_panel.html:64-66`).

---

## Coordination — who owns what

Four plans are in flight against `web-react/`. Ownership, stated so nobody guesses:

| Plan | Owns | Overlap with this slice |
|---|---|---|
| `2026-07-24-react-app-shell.md` | `components/shell/**`, `App.tsx`, `helpers/shellContext.ts`, `helpers/clock.ts`, `helpers/timer/**`, `structure.test.ts` | **Landed** (`b2515c7b`). This slice consumes it and must not modify any of those files. **Do not add a second `useLiveState()` call site** — `structure.test.ts:100-115` enforces it. |
| `2026-07-25-react-probe-notifications.md` (C1) | `helpers/notify/**`, `ProbeNotifyModal.tsx`, **and edits `deriveView.ts`, `ProbeCard.tsx`, `Dashboard.tsx`, `dashboard.css`** | **DIRECT COLLISION.** It owns **M7 (probe ETA)** outright — dropped here. Its Task 4 adds `label`/`notifyOn`/`etaStr` to `ProbeCardView`; this slice's Task 9 adds `connected`/`battery` to the same interface. **Task 9 and its Task 4 must not run concurrently.** Both plans thread a new `targetUrl` prop into `Dashboard` (its Task 5 Step 3; this plan's Task 4) — **whichever lands first adds the prop; the second consumes it and does not re-add it.** |
| `2026-07-25-wizard-critical-fixes.md` (Slice 2) | `components/wizard/**`, `helpers/wizard/**`, `blueprints/api_wizard/`, **and `components/dashboard/ConfirmAction.tsx`** (adds an optional `message` prop) | This slice's Task 4 **consumes** that `message` prop. If Slice 2 has not landed, Task 4 adds it — additively, same signature (`message?: string`), so the two converge. Coordinate before editing `ConfirmAction.tsx`. |
| `2026-07-25-react-settings-guards-sweep.md` (Slice 4) + `2026-07-25-react-save-failure-surfacing.md` | `components/settings/**`, `helpers/settings/useSaveSettings.ts` | **No overlap.** Task 4 *calls* `helpers/settings/settingsApi.ts`'s existing `applySettings` (`:66-83`) but does not modify it. |

**Files this slice owns exclusively:**
`helpers/dashboard/health.ts`, `buttonsForMode.ts`, `hooks.ts`, new `helpers/dashboard/cookTime.ts`, `countdowns.ts`, `probeStatus.ts`, `controlHealth.ts`; `components/dashboard/Dashboard.tsx`, `ControlButtons.tsx`, `GrillGauge.tsx`, `HopperGauge.tsx`, `SystemStatus.tsx`, `SetpointEntry.tsx`, `dashboard.css`, new `ActionMenu.tsx`; `playwright.config.ts`, `rsbuild.config.ts` (one line), `tests/e2e/dashboard-fidelity.spec.ts`.

**Shared, coordinate before touching:** `deriveView.ts`, `ProbeCard.tsx`, `ConfirmAction.tsx`, `helpers/command.ts`, `helpers/types.ts`.

**Not touched, by decision:** `components/shell/**`, `App.tsx`, `components/settings/**`, `components/wizard/**`, `structure.test.ts`, `blueprints/mobile/socket_io.py` (every field this slice needs is already on the wire — verified at `socket_io.py:216-276`).

---

## File Structure

**Create**
- `web-react/src/helpers/dashboard/cookTime.ts` + `.test.ts` — pure C3 derivation.
- `web-react/src/helpers/dashboard/countdowns.ts` + `.test.ts` — pure I4 mode/lid countdowns + recipe label.
- `web-react/src/helpers/dashboard/probeStatus.ts` + `.test.ts` — pure M6 badge derivation.
- `web-react/src/helpers/dashboard/controlHealth.ts` + `.test.tsx` — D1 recheck hook (`useControlHealth`).
- `web-react/src/components/dashboard/ActionMenu.tsx` + `.test.tsx` — the shared dropup used by I1 and I2.
- `web-react/tests/e2e/dashboard-fidelity.spec.ts` — the 1280×720 gate.
- `web-react/tests/e2e/layoutBaseline.ts` — measure / compare / report helper (non-spec module).
- `web-react/tests/e2e/dashboard-layout-1280x720.json` — **committed** baseline.
- `web-react/tests/e2e/dashboard-reflow.spec.ts` — the 390×844 assertions.

**Modify**
- `web-react/src/helpers/dashboard/health.ts` (+ `.test.ts`) — D1.
- `web-react/src/helpers/dashboard/buttonsForMode.ts` (+ `.test.ts`) — I3, I4a, I2.
- `web-react/src/helpers/dashboard/hooks.ts` (+ delete `fitScale.test.tsx`) — C8: remove `useFitScale`.
- `web-react/src/helpers/dashboard/deriveView.ts` — M6 fields (**shared**).
- `web-react/src/components/dashboard/Dashboard.tsx` (+ `.test.tsx`) — nearly every task.
- `web-react/src/components/dashboard/ControlButtons.tsx` (+ `.test.tsx`) — I3, I2, D1.
- `web-react/src/components/dashboard/SetpointEntry.tsx` (+ `.test.tsx`) — I3 reuse (`title`/`submitLabel`).
- `web-react/src/components/dashboard/ConfirmAction.tsx` (+ `.test.tsx`) — I3 (`message`; **shared with Slice 2**).
- `web-react/src/components/dashboard/ProbeCard.tsx` (+ `.test.tsx`) — M6 (**shared with C1**).
- `web-react/src/components/dashboard/HopperGauge.tsx` (+ `.test.tsx`) — M8.
- `web-react/src/components/dashboard/GrillGauge.tsx`, `SystemStatus.tsx` — Task 11 CSS extraction only.
- `web-react/src/components/dashboard/dashboard.css` — Tasks 11–13.
- `web-react/src/components/DashboardRoute.tsx` (+ `.test.tsx`) — new `targetUrl` prop.
- `web-react/playwright.config.ts` — fidelity + reflow projects.
- `web-react/rsbuild.config.ts` — one line: honour `process.env.PORT`.
- `web-react/.gitignore` (or the repo root's) — `tests/e2e/artifacts/`.

---

### Task 1: The 1280×720 fidelity harness — landmarks, baseline, artifact

**This task must land before any other task in this plan.** It changes zero styles: it adds `data-pf` attributes (which cannot move a box) and new test files.

**Files:** Modify `web-react/src/components/dashboard/Dashboard.tsx`, `ProbeCard.tsx`, `ControlButtons.tsx`, `HopperGauge.tsx`, `SystemStatus.tsx`, `GrillGauge.tsx`, `web-react/playwright.config.ts`, `web-react/rsbuild.config.ts`; Create `tests/e2e/layoutBaseline.ts`, `tests/e2e/dashboard-fidelity.spec.ts`, `tests/e2e/dashboard-layout-1280x720.json`.

**Interfaces:** Produces `measureLandmarks(page): Promise<LandmarkMap>`, `compareToBaseline(actual, baseline): string[]`, and the `data-pf` landmark vocabulary every later task asserts against.

- [ ] **Step 1: Add the landmark attributes.** Attributes only — **no style object may be touched in this step.** Seventeen landmarks:

```
stage  header  brand  status  clock  body
probeCol  probeColTitle  probeCard   (probeCard repeats; index it)
centerCol  gauge  cookRow  cookCard  controls
rightCol  system  pills  hopper
```
  e.g. `Dashboard.tsx:65-69` becomes `<div className="pf-stage" data-pf="stage" …>`, `:91` `data-pf="header"`, `:192` `data-pf="probeCol"`, `:220` `data-pf="centerCol"`, `:233` `data-pf="cookRow"`, `:291` `data-pf="rightCol"`, `:307` `data-pf="pills"`; `ControlButtons.tsx:47` `data-pf="controls"`; `ProbeCard.tsx:7` `data-pf="probeCard"`.

- [ ] **Step 2: Make the dev server port configurable.** One line in `rsbuild.config.ts` so the fidelity project can run a second, demo-mode server without fighting the existing one for 5173:

```ts
server: {
  // Playwright's fidelity project starts a SECOND dev server in demo mode on
  // its own port; the default stays 5173 because playwright.config.ts's main
  // webServer pins it.
  port: Number(process.env.PORT) || 5173,
  proxy: { /* unchanged */ },
},
```

- [ ] **Step 3: Add the `fidelity` Playwright project.** Demo mode is deliberate and load-bearing: `useLiveState`'s demo branch (`useLiveState.ts:31-37`) opens **no socket at all**, so this project cannot be raced by the `workers: 1` shared-instance constraint that the rest of the suite lives under, and `demoDashAt` (`helpers/demoData.ts:8-31`) pins the structure to **mode Hold, exactly one food probe, hopper visible, no lid-open block, no banners** — a fixed DOM shape on every machine.

```ts
webServer: [
  { command: "bun run dev", url: "http://localhost:5173", reuseExistingServer: true, timeout: 60_000 },
  // Demo mode: no socket, so this server is independent of the shared PiFire
  // instance the other specs mutate. PUBLIC_DEMO is read at build time
  // (useLiveState.ts:22), which is why it needs its own server rather than a
  // query parameter.
  { command: "PORT=5174 bun run demo", url: "http://localhost:5174", reuseExistingServer: true, timeout: 60_000 },
],
projects: [
  { name: "app", testIgnore: /dashboard-(fidelity|reflow)\.spec\.ts/, use: { baseURL: "http://localhost:5173", viewport: { width: 1280, height: 720 } } },
  { name: "fidelity", testMatch: /dashboard-fidelity\.spec\.ts/, use: { baseURL: "http://localhost:5174", viewport: { width: 1280, height: 720 } } },
],
```

- [ ] **Step 4: Write `tests/e2e/layoutBaseline.ts`.** Measurement is stage-relative and scale-normalised so it means the same thing before and after the reflow:

```ts
export interface Landmark { x: number; y: number; w: number; h: number; fontSize: string; fontWeight: string }
export type LandmarkMap = Record<string, Landmark>;

export async function measureLandmarks(page: Page): Promise<LandmarkMap> {
  // Barlow is loaded from fonts.googleapis.com (index.html:7-11). If it has not
  // arrived, every text-derived box is measured against a fallback face and the
  // baseline is meaningless -- fail loudly rather than record garbage.
  await page.waitForFunction(() => document.fonts.check("700 20px Barlow"));
  return page.evaluate(() => {
    const stage = document.querySelector<HTMLElement>('[data-pf="stage"]');
    if (stage === null) throw new Error("no [data-pf=stage] on the page");
    const sr = stage.getBoundingClientRect();
    // The live fit scale. Dividing by it converts screen pixels back into the
    // authored 1280x720 coordinate space, so this measurement is invariant to
    // however much chrome the app shell happens to be showing -- and it reads
    // 1 once the scale transform is gone.
    const s = sr.width / 1280;
    const out: Record<string, unknown> = {};
    let n = 0;
    for (const el of document.querySelectorAll<HTMLElement>("[data-pf]")) {
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      const name = el.dataset.pf === "probeCard" ? `probeCard${n++}` : el.dataset.pf!;
      out[name] = {
        x: Math.round((r.left - sr.left) / s),
        y: Math.round((r.top - sr.top) / s),
        w: Math.round(r.width / s),
        h: Math.round(r.height / s),
        fontSize: cs.fontSize,
        fontWeight: cs.fontWeight,
      };
    }
    return out;
  }) as Promise<LandmarkMap>;
}
```

  and the comparison, which reports **every** violation at once rather than dying on the first:

```ts
// Authored constants: literals in the source (Dashboard.tsx:93,195,234,294,308;
// ControlButtons.tsx:53). A deviation here is never rounding.
const EXACT: Record<string, Partial<Record<"w" | "h", number>>> = {
  stage: { w: 1280, h: 720 }, header: { h: 58 }, probeCol: { w: 298 },
  rightCol: { w: 300 }, controls: { h: 82 }, cookRow: { h: 52 }, pills: { h: 64 },
};
const EXACT_TOL = 0.5;
const BOX_TOL = 2;   // see "THE FIDELITY CONTRACT" for why 2

export function compareToBaseline(actual: LandmarkMap, baseline: LandmarkMap): string[] {
  const problems: string[] = [];
  for (const name of Object.keys(baseline)) {
    const a = actual[name], b = baseline[name];
    if (a === undefined) { problems.push(`${name}: MISSING from the page`); continue; }
    for (const k of ["x", "y", "w", "h"] as const) {
      const tol = EXACT[name]?.[k as "w" | "h"] !== undefined ? EXACT_TOL : BOX_TOL;
      if (Math.abs(a[k] - b[k]) > tol) problems.push(`${name}.${k}: ${b[k]} -> ${a[k]} (tolerance ${tol})`);
    }
    for (const k of ["fontSize", "fontWeight"] as const) {
      if (a[k] !== b[k]) problems.push(`${name}.${k}: ${b[k]} -> ${a[k]} (must be exact)`);
    }
  }
  for (const name of Object.keys(actual)) {
    if (baseline[name] === undefined) problems.push(`${name}: NEW landmark, not in the baseline`);
  }
  return problems;
}
```

- [ ] **Step 5: Write `dashboard-fidelity.spec.ts`.** Freeze the clock **before** navigating so `helpers/clock.ts`'s shared interval, `useClock` and `demoDashAt`'s elapsed-seconds argument are all pinned:

```ts
test("dashboard layout at 1280x720 matches the committed baseline", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-07-25T12:00:00Z") });
  await page.clock.pauseAt(new Date("2026-07-25T12:00:00Z"));
  await page.goto("/");
  await expect(page.locator('[data-pf="stage"]')).toBeVisible();

  const actual = await measureLandmarks(page);
  await page.locator('[data-pf="stage"]').screenshot({
    path: "tests/e2e/artifacts/dashboard-1280x720.png",
    animations: "disabled",
  });

  const problems = compareToBaseline(actual, baseline);
  expect(problems, problems.join("\n")).toEqual([]);
});
```
  Also assert the authored constants directly, so a wholesale baseline replacement cannot quietly relax them:
  `expect(actual.stage.w).toBe(1280)`, `expect(actual.stage.h).toBe(720)`, `expect(actual.controls.h).toBe(82)`.

- [ ] **Step 6: Generate and commit the baseline.** Run the spec once with the baseline file absent (the helper writes it and the spec skips the comparison on that run only), inspect the JSON by hand against the source constants, then commit it. **Copy `artifacts/dashboard-1280x720.png` to `artifacts/before.png` and keep it for the whole slice** — it is the reviewer's reference for every later task. Add `web-react/tests/e2e/artifacts/` to `.gitignore`.

- [ ] **Step 7: Run the full gate** plus `bun run test:e2e --project=fidelity`. **Confirm it passes twice in a row** — a baseline that is not reproducible on the same machine is not a baseline. **Commit.**

---

### Task 2: D1 — `controlAlive` must not lock the user out of Stop

**Files:** Modify `web-react/src/helpers/dashboard/health.ts` + `.test.ts`; Create `web-react/src/helpers/dashboard/controlHealth.ts` + `.test.tsx`; Modify `ControlButtons.tsx` + `.test.tsx`, `Dashboard.tsx` + `.test.tsx`, `dashboard.css`.

**Interfaces:** Produces `useControlHealth(controlAlive, targetUrl) → { alive: boolean; stale: boolean; recheck(): Promise<void>; rechecking: boolean }` and `recheckControl(baseUrl): Promise<boolean>`.

**Why this shape.** Three verified facts drive it: the error never clears without a `control.py` restart (`common/datastore_accessors.py:126-132`; only clearer `control.py:107-109`); it can be written on a healthy system by a queue race (`common/app.py:31-44`); and **Flask never disables its controls in this state** — it shows a dismissible banner and leaves every button live (`templates/base.html:117-125`). Disabling exactly the buttons a user needs in an emergency is React's own invention. The backend half — an endpoint that clears the error, or a non-sticky liveness signal — is **out of scope**; record it as a backlog item.

- [ ] **Step 1: Write failing tests.**
  - `recheckControl` GETs `/api/sys/check_alive` and returns `true` only for `{ result: "OK" }`; `false` for any other result, a non-ok HTTP status, or a thrown fetch. Follow `helpers/command.test.ts`'s `rs.stubGlobal("fetch", …)` style verbatim.
  - `useControlHealth` returns `alive: true` when `controlAlive` is true. When `controlAlive` is false it returns `alive: false, stale: true`. After `recheck()` resolves true it returns `alive: true` **and keeps doing so on later renders where `controlAlive` is still false** — that persistence is the whole point: a live probe that just succeeded is better evidence than a blob written up to 30 s ago that nothing can clear.
  - After `recheck()` resolves false, `alive` stays false.
  - `ControlButtons`: with `disabled` true, buttons whose label is `Stop` or `Shutdown` are **not** `disabled`; every other button is. Assert by label, not by index.
  - `Dashboard`: when `controlAlive` is false and phase is `live`, a **Recheck** button renders beside the `CTRL OFFLINE` pill; clicking it calls the injected recheck.
- [ ] **Step 2: Run, confirm fail.** `bun run test src/helpers/dashboard src/components/dashboard`
- [ ] **Step 3: Implement.**

```ts
// helpers/dashboard/controlHealth.ts
//
// dash.errors NEVER clears itself. read_errors() (common/datastore_accessors.py:126-132)
// is a plain non-destructive blob read -- unlike `warnings` on the same payload,
// which drains -- and its only clearer, flush_errors(), is called from exactly
// one place in production: control.py:107-109, at boot. So once
// _check_control_status (blueprints/mobile/socket_io.py:1009-1019) writes the
// "control process did not respond" string, it is on every frame until the
// control process restarts.
//
// It can also be written on a HEALTHY system: get_system_command_output
// (common/app.py:31-44) pops the shared queue_systemo and DISCARDS non-matching
// entries, so any of its seven consumers can eat the check_alive reply, the 1s
// timeout expires, and the sticky error lands.
//
// The frontend cannot clear the blob -- there is no route that does. What it
// CAN do is ask the same question directly and believe the answer.
export async function recheckControl(baseUrl: string): Promise<boolean> {
  try {
    const res = await fetch(`${baseUrl}/api/sys/check_alive`);
    if (!res.ok) return false;
    const body = (await res.json()) as { result?: string };
    return body.result === "OK";
  } catch {
    return false;
  }
}
```
  `useControlHealth` holds one `useState<boolean>` override, set only by a successful `recheck()`. **No `useEffect`** — `alive` is `controlAlive || override`, computed at render.

  In `ControlButtons.tsx:68-82`, `disabled` becomes per-button:

```tsx
// The control process being unreachable is exactly when a user most needs to
// stop the grill, and this flag can be stuck true on a healthy system (see
// helpers/dashboard/controlHealth.ts). Flask never disables anything here --
// it shows a banner and leaves the buttons live (templates/base.html:117-125).
// We keep the dimming as a signal but never withhold the exits.
const SAFETY_LABELS = new Set(["Stop", "Shutdown"]);
const off = (disabled && !SAFETY_LABELS.has(b.label)) || busy;
```
  In `Dashboard.tsx:133-141`, next to the `CTRL OFFLINE` span, render a `pf-toggle` **Recheck** button when `!controlAlive && phase !== "demo"`, disabled while `rechecking`.

- [ ] **Step 4: Run the full gate.** Re-run `--project=fidelity`: the Recheck button lives inside the header's right-hand group, so `header.h` must still be 58 and `brand`/`clock` must not move. If a landmark moved, **fix the button's sizing** — this task has no licence to change the header's geometry.
- [ ] **Step 5: Record the backend follow-up** as a one-line backlog note: *"errors blob is write-only from the web tier; `_check_control_status` can false-positive via the shared `queue_systemo` (`common/app.py:31-44`) — see triage Slice 9 item 1."* **Commit.**

---

### Task 3: C3 — cook time from `startupTimestamp`

**Files:** Create `web-react/src/helpers/dashboard/cookTime.ts` + `.test.ts`; Modify `Dashboard.tsx` + `.test.tsx`.

**Interfaces:** Produces `cookElapsed(startupTimestamp: number, nowSeconds: number) → number | null` and `fmtElapsed(seconds: number | null) → string`.

- [ ] **Step 1: Write failing tests.**
  - `cookElapsed(0, anything)` → `null` (Flask's `!= 0` inactive branch, `dash_default.js:400,410`).
  - `cookElapsed(1700000000, 1700003600)` → `3600`.
  - A **negative** result clamps to 0. Not hypothetical: `startup_timestamp` is the *server's* `time.time()` (`controller/runtime/modes/startup.py:120`) and `nowSeconds` is the *browser's* clock, so a browser running behind the Pi produces a negative elapsed. The timer code already had to solve this class of problem server-side (`helpers/command.ts:78-103`); here clamping is enough because nothing is armed from it.
  - `fmtElapsed(null)` → `"--"`, exactly matching Flask (`dash_default.js:410`).
  - `fmtElapsed(7)` → `"07s"`; `fmtElapsed(754)` → `"12:34"`; `fmtElapsed(3723)` → `"01:02:03"` — Flask's adaptive `formatDuration` (`dash_default.js:599-611`), **including the zero-padded hour**, which `deriveView.ts`'s `fmtDuration` does not do.
  - **Decision, stated so it is not re-litigated:** match Flask exactly. `fmtElapsed` is a new function; `deriveView.ts:179-187`'s `fmtDuration` keeps its current behaviour and its current callers untouched.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement**, then rewrite `Dashboard.tsx:48-60`:

```tsx
// Elapsed cook time comes from the CONTROLLER's startup_timestamp
// (blueprints/mobile/socket_io.py:234, epoch seconds, math.trunc'd), not from
// when this browser happened to mount. Reloading four hours into a brisket used
// to report 00:00, and two devices disagreed with each other. Reignite
// deliberately does not rewrite the timestamp (controller/runtime/modes/
// reignite.py:17-18), so a reignited cook keeps counting from the original
// ignition -- which is the behaviour Flask has always had.
const cookTime = fmtElapsed(cookElapsed(dash.startupTimestamp, Math.floor(now.getTime() / 1000)));
```
  **Delete** `cookStart`, `prevCooking` and their render-phase adjustment (`:52-59`) entirely. `view.cooking` keeps its other consumers (`GrillGauge`, `liveColor`) — do not remove it from `deriveView`.

- [ ] **Step 4: Run the full gate + `--project=fidelity`.** The cook-time value's font-size is a landmark on `cookCard`; it must not change. The **string** changes (`--` vs `00:00`) and that is expected — text content is not measured.
- [ ] **Step 5: Commit.**

---

### Task 4: I3 — the startup safety-check confirmation

**Files:** Modify `buttonsForMode.ts` + `.test.ts`, `ControlButtons.tsx` + `.test.tsx`, `SetpointEntry.tsx` + `.test.tsx`, `ConfirmAction.tsx` + `.test.tsx`, `Dashboard.tsx` + `.test.tsx`, `DashboardRoute.tsx` + `.test.tsx`.

**Interfaces:** `ButtonAction` gains `{ type: "startup" }`. `SetpointEntry` gains optional `title`, `submitLabel`, `min`, `max`. `ConfirmAction` gains optional `message` (**shared with Slice 2** — if it is already there, consume it).

**The two variants and their precedence** are given verbatim in Verified Facts. Restating the one that is easy to get wrong: **when `startupCheck` is true AND (`startToHoldPrompt` && `startupGotoMode === "Hold"`), Flask shows the hold-temperature variant, not the confirmation.**

- [ ] **Step 1: Write failing tests.**
  - `buttonsForMode`: in `Stop`, the Startup button's action is `{ type: "startup" }` (not `command`) whenever `startupCheck || (startToHoldPrompt && startupGotoMode === "Hold")`, and stays a plain `command` when neither holds. Cover all four combinations.
  - `ControlButtons` given `dash.startToHoldPrompt = true, startupGotoMode = "Hold", startupGotoTemp = 225`: pressing Startup opens a `SetpointEntry` titled **"Change Hold Temp?"** with submit label **"Startup"**, seeded to 225, range **125–600 for °F** and **50–260 for °C** (`_macro_control_panel.html:236,238` — note these are *wider* than `SETPOINT_RANGE` in `health.ts:11-14`, hence the `min`/`max` props).
  - Submitting it calls `applySettings` with **exactly** `{ startup: { start_to_mode: { after_startup_mode: "Hold", start_to_hold_prompt: true, primary_setpoint: 225 } } }` and flags `["settings_update"]`, and **awaits it before** calling `setMode("startup")`. Assert the ordering with a resolution-order array — this is the fix for Flask's fire-and-forget race (`control_panel.js:421-427`).
  - A **failed** `applySettings` does **not** call `setMode` and surfaces the message. Igniting a grill that just failed to record its hold target is the wrong failure mode.
  - `dash.startupCheck = true` with no hold prompt: pressing Startup opens `ConfirmAction` titled **"Startup Check"** with message **"Confirm Startup Grill?"**; Confirm calls `setMode("startup")`; Cancel calls nothing.
  - Both flags set → the **hold** variant renders and `ConfirmAction` does not.
  - `Dashboard` accepts `targetUrl` and threads it to `ControlButtons`.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.**
  - `buttonsForMode.ts:30-34`:

```ts
// Flask gates ignition behind a modal (_macro_control_panel.html:89-90) whose
// two variants are chosen by Jinja, with the hold prompt taking precedence over
// the plain safety check when both are configured. Which one to show is a
// presentation decision, so this returns the intent and ControlButtons picks.
const startupButton = (dash: LiveState): ControlButton => ({
  label: "Startup",
  variant: "accent",
  action:
    dash.startupCheck || (dash.startToHoldPrompt && dash.startupGotoMode === "Hold")
      ? { type: "startup" }
      : cmd((c) => c.setMode("startup")),
});
```
  - `ControlButtons` holds `startupMode: "none" | "hold" | "confirm"`, derived when the button is pressed. **Do not mirror it from `dash` in an effect.**
  - `targetUrl` comes from `DashboardRoute`'s `useShellState()` (`DashboardRoute.tsx:15` already destructures it) → `Dashboard` prop → `ControlButtons` prop. **Do not import `import.meta.env` in a component**, and **do not add a `useLiveState()` call** (`structure.test.ts:100-115`).
  - Reuse `applySettings` from `helpers/settings/settingsApi.ts:66-83` unmodified.
- [ ] **Step 4: Run the full gate + `--project=fidelity`.** The Startup button's box must not change — only its `onClick` path.
- [ ] **Step 5: Commit.**

---

### Task 5: I4a — the Recipe branch (behaviour bug, do this before the readouts)

**Files:** Modify `buttonsForMode.ts` + `.test.ts`.

This half is a *bug*, not a missing readout: `buttonsForMode.ts:73-83` falls through for any unrecognised mode, so during a running recipe React offers Smoke / Hold / Smoke+ / Shutdown / Stop — the exact controls Flask hides (`control_panel.js:181-182` hides `#active_group` **and** `#inactive_group`) because pressing them breaks out of the recipe.

- [ ] **Step 1: Write failing tests.**
  - With `dash.recipeStatus.recipeMode === true`, `buttonsForMode` returns **only** the recipe controls: `Next Step`, `Shutdown`, `Stop`. It must **not** contain `Smoke`, `Hold` or `Smoke+`.
  - Gate on `recipeStatus.recipeMode`, **not** on `currentMode === "Recipe"`. Assert both: `recipeMode: true` with `currentMode: "Smoke"` still returns the recipe set. (`controller/runtime/modes/base.py:478` publishes the boolean; the string is a second-hand copy.)
  - `Next Step` sends `POST /api/control {"updated": true}` (`control_panel.js:530`).
  - When `recipeStatus.paused` is true, `Next Step` carries `variant: "accent"` (Flask's `glowbutton`, `control_panel.js:207-217`) — and still sends the same command. **The unpause payload (`{recipe:{step_data:{...pause:false}}}`, `:382-392`) is deliberately NOT ported here**: it rewrites the whole `step_data` object, and `POST /api/control` merges via SQLite `json_patch` (RFC 7396) which replaces arrays wholesale — the same landmine documented at `helpers/command.ts:78-103`. Record it; do not build it blind.
  - `Shutdown` keeps its confirm (`control_panel.js:456-463` wires it to the same handler as the normal shutdown).
  - **Do not** port `Step N` — it links to `/recipes`, a Flask page, and the app-shell decision forbids linking out (`2026-07-24-react-app-shell.md:59`). Assert its absence so nobody adds it later.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.** A `recipeMode` branch **before** the mode ladder at `buttonsForMode.ts:39`, since `recipeStatus.recipeMode` is authoritative over the mode string. Add `postControl(body)` to `CommandClient` **only if it does not already exist** — the C1 notifications plan's Task 1 introduces exactly this write path (`postNotifyData`, and landmine 2: `POST /api/control` answers `result: "success"`, lowercase, not the `"OK"` that `command.ts:72` tests for). **Coordinate; do not write a second one.**
- [ ] **Step 4: Run the full gate.** Pure logic — no layout impact, `--project=fidelity` unaffected. **Commit.**

---

### Task 6: I4b — mode countdown, lid-open countdown, recipe status readout

**Files:** Create `helpers/dashboard/countdowns.ts` + `.test.ts`; Modify `Dashboard.tsx` + `.test.tsx`.

**Interfaces:** Produces `modeCountdown(dash, nowSeconds) → number | null`, `lidCountdown(dash, nowSeconds) → number | null`, `recipeLabel(dash) → string | null`.

- [ ] **Step 1: Write failing tests.** Arithmetic copied from `dash_default.js:348-368` and `:386-398`; all fields are seconds.
  - `modeCountdown` returns `duration - (now - modeStartTime)` **only** for `Startup`, `Reignite` (→ `startDuration`), `Prime` (→ `primeDuration`), `Shutdown` (→ `shutdownDuration`); `null` for every other mode; clamped at 0, never negative.
  - **`null` during a recipe even when the step's sub-mode is Startup or Prime.** Flask keys the arithmetic off the outer `mode` var, which is `'Recipe'` during a recipe (`dash_default.js:349`), so no countdown is shown. Reproduce that rather than improving on it: the countdown's inputs are not published per-step.
  - `lidCountdown` returns `lidOpenEndTime - now`, clamped at 0, **only when `currentMode === "Hold" && lidOpenDetected`**; `null` otherwise.
  - `recipeLabel` returns `` `Recipe | ${dash.displayMode}` `` when `recipeStatus.recipeMode`, else `null` (`dash_default.js:297-300`; `displayMode` is `status["mode"]`, the step's sub-mode — `socket_io.py:225`).
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement, and render into the existing boxes — do not add rows.** This constraint is load-bearing for the fidelity gate:
  - Mode countdown: append `Time Left in Mode: {n}s` inside the **existing** `GrillGauge` mode label area, or as a second line in the cook-time card's label column. Flask's literal string, `s` suffix included (`_macro_dash_default.html:373`).
  - Lid countdown: the `LID OPEN` block already exists at `Dashboard.tsx:266-284` with a fixed `flex: 0 0 210px`. Render `LID OPEN` and `PID Paused {n}s` as two lines **inside that same 210×52 box**. Do not widen it.
  - Recipe label: into the gauge's mode label (`view.modeLabel`), replacing it while `recipeLabel` is non-null.
- [ ] **Step 4: Run the full gate + `--project=fidelity`.** Demo mode is `Hold` with no lid-open, so the demo landmarks should be **unchanged** and the baseline must not be edited. If it moved, the readout leaked out of its box.
- [ ] **Step 5: Commit.**

---

### Task 7: I2 — the Prime amount menu

**Files:** Create `components/dashboard/ActionMenu.tsx` + `.test.tsx`; Modify `buttonsForMode.ts` + `.test.ts`, `ControlButtons.tsx` + `.test.tsx`, `dashboard.css`.

**Interfaces:** `ActionMenu({ open, title, items, onPick, onCancel })` where `items: { label: string; value: string }[]`. `ButtonAction` gains `{ type: "menu"; title: string; items: MenuItem[]; run(c, value): Promise<CommandResult> }`.

- [ ] **Step 1: Write failing tests.**
  - `buttonsForMode` in `Stop` returns a `Prime` button with a `menu` action carrying **exactly six** items in Flask's order (`_macro_control_panel.html:80-85`): `Prime 10g`, `Prime 25g`, `Prime 50g`, `Prime 10g & Startup`, `Prime 25g & Startup`, `Prime 50g & Startup`.
  - Picking `Prime 25g` calls `prime(25, "stop")`; picking `Prime 50g & Startup` calls `prime(50, "startup")`. (`command.ts:123-124` already takes `(grams, next?)`; `GrillMode` at `command.ts:5-12` is lowercase.)
  - `ActionMenu` renders nothing when closed; renders one button per item; a scrim click cancels; Escape cancels.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.** `ActionMenu` reuses `pf-modal-scrim` / `pf-modal` / `pf-modal-title` / `pf-modal-btn` (`dashboard.css:165-241`) — **no new visual language**. `ControlButtons.onClick` (`:39-44`) gains the `menu` case, mirroring how `confirm` already stores `{title, run}` in state.
- [ ] **Step 4: Run the full gate + `--project=fidelity`.** Demo mode is `Hold`, so the Prime button is not on screen and the baseline must be unchanged. **Commit.**

---

### Task 8: I1 — the P-Mode pill becomes a control

**Files:** Modify `Dashboard.tsx` + `.test.tsx`, `deriveView.ts`, `dashboard.css`.

**Interfaces:** Consumes `ActionMenu` (Task 7) and the existing `command.setPMode` (`command.ts:32,122`).

**The decision the triage demanded, made:** the pill becomes a control. `setPMode` is not deleted. React's single `POST /api/set/pmode/{n}` is *better* than Flask's two chained POSTs — `common/api_commands.py:377-383` writes `cycle_data.PMode` **and** sets `control["settings_update"] = True` in one merge, and range-checks `0 <= n < 10` server-side.

- [ ] **Step 1: Write failing tests.**
  - The P-MODE pill is a `<button>`, not a `<div>`, **only in the modes Flask shows it in**: `Prime`, `Shutdown`, `Startup`, `Reignite`, `Smoke` (`dash_default.js:248-283`). In `Hold` and in `Stop`/`Monitor`/`Manual`/`Error`/recipe it renders as today's read-only pill (`:266-274`, `:284-293`).
  - Pressing it opens an `ActionMenu` titled `P-Mode` with **ten** items: `0 - Off`, then `1`…`9` (`_macro_dash_default.html:91-105`).
  - Picking `7` calls `setPMode(7)`.
  - The pill keeps rendering `P-{dash.pMode}` from the payload after the pick — **no local mirroring**. `pMode` is `settings["cycle_data"]["PMode"]` (`socket_io.py:232`) and comes back on the next frame; a mirror would fight it.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.** Add `pModeEditable: boolean` to `DashView` in `deriveView.ts` (a pure mode-set membership test) and let `Pill` (`Dashboard.tsx:319-356`) take an optional `onClick`. When present it renders a `<button>` carrying **the identical style object** — same border, background, font sizes, `flex: 1`. **A button element defaults to a different font and padding; reset both explicitly** or the fidelity gate will catch it, which is exactly what it is for.
- [ ] **Step 4: Run the full gate + `--project=fidelity`.** Demo mode is `Hold`, so the pill stays a `<div>` and the baseline is unchanged. Additionally run the spec once by hand against the live backend in `Smoke` to confirm the button variant measures the same as the div variant. **Commit.**

---

### Task 9: M6 — probe connected / battery badges

**Files:** Create `helpers/dashboard/probeStatus.ts` + `.test.ts`; Modify `deriveView.ts` (**shared — see Coordination**), `ProbeCard.tsx` + `.test.tsx` (**shared**), `dashboard.css`.

**Interfaces:** Produces `connectionBadge(status) → { label: string; tone: "ok" | "off" } | null` and `batteryBadge(status) → { text: string; tone: "unknown" | "danger" | "warn" | "ok"; level: 0|1|2|3 } | null`.

**Serialize against the C1 notifications plan's Task 4**, which adds `label`/`notifyOn`/`etaStr` to the same `ProbeCardView` interface and the same `ProbeCard` header row.

- [ ] **Step 1: Write failing tests.**
  - `connectionBadge` returns `null` when `connected` is **absent** — not "disconnected". `blueprints/mobile/socket_io.py:858-859` copies the key only `if "connected" in status`, and only Bluetooth drivers set it (`probes/bt_ibbq.py:361`, `probes/bt_meater.py:284`, `probes/disabled.py:60`). A wired ADC probe must show no pill at all, exactly as `_macro_dash_default.html:20` gates it.
  - `true` → `{ label: "Connected", tone: "ok" }`; `false` → `{ label: "Disconnected", tone: "off" }`.
  - `batteryBadge` returns `null` when `batteryPercentage` is absent; `{ text: "Unknown", tone: "unknown" }` when it is `null`.
  - Thresholds, from `_macro_dash_default.html:41-64` / `dash_default.js:506-529`: `<10` danger, `<40` warn, `<90` ok/three-quarters, `>=90` ok/full. Round and clamp to 0–100 (`dash_default.js:497-504`).
  - **`0` renders as `"0%"` in the danger tone.** Flask's `battery_percentage || null` (`:443,453`) turns a real 0 % into `Unknown`; that is a bug and we do not copy it. Pin it with an explicit test so a later "parity" pass cannot reintroduce it.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.** `deriveView.ts`'s `probeCard` (`:95-107`) gains `conn` and `battery`. `ProbeCard` renders them in the header row (`ProbeCard.tsx:22-34`), **right-aligned beside `p.targetStr`**, as small `pf-badge` spans. Fixed height, so the card's box does not change.
- [ ] **Step 4: Run the full gate + `--project=fidelity`.** The demo fixture's probe has no `connected`/`batteryPercentage` (`helpers/fixture.ts`), so **both badges are absent and `probeCard0` must be unchanged**. If it moved, the badge container is taking space when it should render nothing — return `null`, do not render an empty span.
- [ ] **Step 5: Commit.**

---

### Task 10: M8 — hopper Refresh (Manager link deliberately dropped)

**Files:** Modify `helpers/command.ts` + `.test.ts` (**shared**), `HopperGauge.tsx` + `.test.tsx`, `Dashboard.tsx` + `.test.tsx`, `dashboard.css`.

**Interfaces:** `CommandClient` gains `hopperCheck(): Promise<CommandResult>`.

- [ ] **Step 1: Write failing tests.**
  - `hopperCheck` POSTs `/api/control` with body exactly `{"hopper_check": true}` and treats `{ result: "success" }` as ok. **Landmine, verified:** `POST /api/control` answers **lowercase `"success"`**, not the `"OK"` that `command.ts:72`'s `post()` tests for (`blueprints/api/routes.py:211`) — reusing `post()` unchanged reports every successful refresh as a failure. The C1 plan hits the same wall (its landmine 2); **if it has landed, reuse its helper rather than adding a second.**
  - `HopperGauge` renders a **"Refresh Status"** button (Flask's exact label, `_macro_dash_default.html:358`) which calls the injected handler and is disabled while in flight.
  - `Dashboard` renders `HopperGauge` **only when `dash.hasDistanceSensor`** — Flask hides the whole card when `settings['modules']['dist'] == 'none'` (`:416-420`), which is exactly what that payload field is (`socket_io.py:245`). It currently has zero consumers, so React shows a hopper gauge on grills with no sensor.
  - **Assert there is no link to `/pellets`.** Dropped by decision (`2026-07-24-react-app-shell.md:59`); the assertion is what stops it coming back.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.** No 500 ms `setInterval`-as-`setTimeout` (`dash_default.js:891`): the socket pushes `hopperLevel` on its own (`socket_io.py:233`), so the refresh only needs to post the flag and let the next frame carry the answer.
- [ ] **Step 4: Run the full gate + `--project=fidelity`.** The button goes **inside** `HopperGauge`'s existing box, which is `flex: 1` in the right column — its height is derived, so a change here moves `hopper.h`. **This is the one behaviour task expected to move a landmark.** Update the baseline in a commit that does nothing else, naming the change. **Confirm `hasDistanceSensor` is `true` in the demo fixture** before concluding anything; if it is false the gauge vanishes and the baseline change is much larger — in that case set the fixture field rather than the layout.
- [ ] **Step 5: Commit.**

---

### Task 11: REFLOW 1 — extract inline layout styles to CSS. **Zero visual change.**

**Files:** Modify `Dashboard.tsx`, `ProbeCard.tsx`, `ControlButtons.tsx`, `GrillGauge.tsx`, `HopperGauge.tsx`, `SystemStatus.tsx`, `dashboard.css`, and the corresponding `.test.tsx` files.

**This is the enabling move: you cannot write a media query against an inline style object.** It is also the highest-risk task for fidelity, because it touches every box. That is why the gate exists and why this lands alone.

- [ ] **Step 1: Write the guard test first** (`dashboard.css` + a source-text assertion in the style of `structure.test.ts:56-91`): every element carrying `data-pf` has a `className` and **no `style` prop containing any of `width`, `height`, `padding`, `margin`, `gap`, `flex`, `display`, `font`**. Dynamic *values* stay inline as custom properties.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Extract.** One `pf-dash-*` class per box, values copied **verbatim**:

```css
/* Ported 1:1 from the inline style objects in Dashboard.tsx et al. Every value
   here is the literal that was inline; this file is where the media queries in
   Task 13 can reach them, which is the only reason the move happened. */
.pf-dash-header  { height: 58px; flex: 0 0 58px; display: flex; align-items: center;
                   justify-content: space-between; padding: 0 22px;
                   border-bottom: 1px solid rgba(255,255,255,0.06); position: relative; z-index: 2; }
.pf-dash-body    { flex: 1; display: flex; gap: 16px; padding: 16px 18px 18px;
                   position: relative; z-index: 1; min-height: 0; }
.pf-dash-probecol{ width: 298px; flex: 0 0 298px; display: flex; flex-direction: column;
                   gap: 12px; min-height: 0; }
.pf-dash-rightcol{ width: 300px; flex: 0 0 300px; display: flex; flex-direction: column;
                   gap: 14px; min-height: 0; }
.pf-dash-cookrow { display: flex; gap: 14px; height: 52px; flex: 0 0 52px; }
.pf-dash-pills   { display: flex; gap: 14px; height: 64px; flex: 0 0 64px; }
/* ... one rule per data-pf landmark and per inner box ... */
```
  Values that depend on `deriveView` output (`p.bg`, `p.border`, `barPct`, `h.pct`, `view.liveColor`) become custom properties set inline:
  `style={{ "--pf-bar-pct": `${p.barPct}%`, "--pf-bar-color": p.barColor } as CSSProperties}`.
  The `scale()` transform **stays inline for now** — Task 13 removes it.
- [ ] **Step 4: Run the full gate + `--project=fidelity`.** **The baseline must pass byte-for-byte. Do not edit it.** Any failure is an extraction error; the reported landmark names the box you got wrong. **This is the single most valuable use of the gate in the whole plan.**
- [ ] **Step 5: Flip `artifacts/before.png` against the fresh `dashboard-1280x720.png`** and confirm by eye. Then **commit** — alone, with nothing else in it.

---

### Task 12: REFLOW 2 — size custom properties. **Still zero visual change.**

**Files:** Modify `dashboard.css`, and the components whose font sizes are still inline.

- [ ] **Step 1: Write the guard test.** `dashboard.css` declares every name in the token list below on `.pf-dash`, and **no** `.pf-dash` rule outside a `@media` block references a raw px font-size for a tokenised element.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Introduce the tokens**, with desktop values **identical to today's literals**:

```css
/* Size tokens. Every value here is exactly the literal it replaces, so the
   desktop rendering is unchanged by construction. Task 13's media queries
   override ONLY these names -- which is what makes "1280x720 is untouched" a
   structural property of the stylesheet rather than something to be careful
   about. */
.pf-dash {
  --pf-gauge-size: 392px;   /* GrillGauge.tsx:66 svg */
  --pf-gauge-ring: 360px;   /* GrillGauge.tsx:57-58 */
  --pf-gauge-num:  112px;   /* GrillGauge.tsx:128 */
  --pf-gauge-unit:  40px;   /* GrillGauge.tsx:131 */
  --pf-probe-temp:  66px;   /* ProbeCard.tsx:44 */
  --pf-probe-unit:  26px;   /* ProbeCard.tsx:45 */
  --pf-probe-name:  15px;   /* ProbeCard.tsx:25 */
  --pf-btn-font:    25px;   /* dashboard.css:127 */
  --pf-btn-h:       82px;   /* ControlButtons.tsx:53 */
  --pf-cook-val:    26px;   /* Dashboard.tsx:258 */
  --pf-pill-val:    24px;   /* Dashboard.tsx:346 */
  --pf-hopper-val:  34px;   /* HopperGauge.tsx:33 */
  --pf-col-w:      300px;   /* Dashboard.tsx:294 */
  --pf-probecol-w: 298px;   /* Dashboard.tsx:195 */
  --pf-header-h:    58px;   /* Dashboard.tsx:93 */
}
```
  Then every corresponding rule reads `font-size: var(--pf-probe-temp)` etc.
  **Deliberately NOT a single global scale unit.** A `--pf-u: 1px` multiplier applied to everything would just be `useFitScale` rewritten in CSS — uniform scaling is the thing C8 rejects. Per-element tokens are what allow a phone to get *relatively larger* type in some places and smaller in others.
- [ ] **Step 4: Run the full gate + `--project=fidelity`.** **Baseline must pass unedited**; computed values resolve `var()`, so `font-size` still reports `66px`.
- [ ] **Step 5: Commit** alone.

---

### Task 13: REFLOW 3 — breakpoints, and the scale transform goes

**Files:** Modify `dashboard.css`, `Dashboard.tsx` + `.test.tsx`, `helpers/dashboard/hooks.ts`; **delete** `helpers/dashboard/fitScale.test.tsx`.

- [ ] **Step 1: Write failing tests** (jsdom-testable parts only — **see the "cannot be verified" note; none of these assert layout**):
  - `Dashboard` renders **no** `transform` in its root's style attribute.
  - `hooks.ts` no longer exports `useFitScale`; `useClock` is unchanged and still exported.
  - `dashboard.css` contains `@media (max-width: 1279px)` and `@media (max-width: 719px)` blocks, and **every** `--pf-*` override in the file is inside one of them (a file-read assertion — this is the mechanical guarantee that desktop is untouched).
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.**
  - **New root class, `.pf-dash-root`. `.pf-fit` is left exactly as it is** for its four other consumers (`App.tsx:30`, `DashboardRoute.tsx:42`, `SettingsError.tsx:6`, `WizardShell.tsx:52,57`).

```css
.pf-dash-root { width: 100%; display: flex; justify-content: center; background: #0c0a09; }
.pf-dash {
  width: 100%;
  max-width: 1280px;
  min-height: 720px;
  display: flex;
  flex-direction: column;
  /* KEEP. The decorative radial glow (Dashboard.tsx:70-84) is positioned at
     bottom:-160 and relies on this to stay inside the dashboard; without it the
     glow bleeds over the rest of the page. The letterbox clipping that .pf-fit
     used to do is what goes away, not this. */
  overflow: hidden;
  background: radial-gradient(120% 90% at 50% 118%, #241a12 0%, #16110d 42%, #0d0b09 100%);
  font-family: "Barlow", system-ui, sans-serif;
  color: #f4ede2;
}
```
  At ≥1280 px wide this lays out **identically** to the old fixed box: same width, same fixed row heights, same column widths.
  - **Tablet:**

```css
@media (max-width: 1279px) {
  .pf-dash-body { flex-wrap: wrap; }
  .pf-dash-probecol,
  .pf-dash-rightcol { flex: 1 1 260px; width: auto; }
  .pf-dash { --pf-gauge-size: 320px; --pf-gauge-ring: 292px; --pf-gauge-num: 88px; }
}
```
  - **Phone:**

```css
@media (max-width: 719px) {
  .pf-dash { min-height: 0;
    --pf-gauge-size: 260px; --pf-gauge-ring: 236px; --pf-gauge-num: 64px; --pf-gauge-unit: 24px;
    --pf-probe-temp: 44px;  --pf-probe-unit: 18px;
    --pf-btn-font: 17px;    --pf-btn-h: 56px;      /* >= 44px touch target */
    --pf-cook-val: 20px;    --pf-pill-val: 20px;   --pf-hopper-val: 26px; }
  .pf-dash-body { flex-direction: column; }
  /* Gauge and controls first: what you reach for on a phone is the temperature
     and the Stop button, not the probe list. */
  .pf-dash-centercol { order: 1; }
  .pf-dash-probecol  { order: 2; display: grid; grid-template-columns: 1fr 1fr; width: auto; flex: none; }
  .pf-dash-rightcol  { order: 3; width: auto; flex: none; }
  /* gridAutoFlow: column forces N equal columns; on a phone five 25px-font
     buttons in one row is unreadable, so let them wrap. */
  .pf-dash-controls { grid-auto-flow: row; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr)); height: auto; flex: none; }
  .pf-dash-header { flex-wrap: wrap; height: auto; flex: none; padding: 8px 12px; }
}
```
  - **Delete `useFitScale`** from `hooks.ts` (lines 13–56) and its `.test.tsx`; drop the `ref` and the inline `transform` from `Dashboard.tsx:46,64-69`. The shell's `.pf-shell-main { overflow: auto }` (`shell.css:17-22`) already provides the scrolling the taller-than-viewport case needs. Also delete the now-dead `.pf-shell-main .pf-fit { position: absolute }` override (`shell.css:28-30`) **only if no other `.pf-fit` consumer is inside the shell** — `DashboardRoute.tsx:42`'s ConnectionStatus **is**, so *keep it*.
- [ ] **Step 4: Run the full gate + `--project=fidelity`.** **The baseline must pass unedited.** The fidelity project's viewport is 1280 wide, so `max-width: 1279px` is false and every rule above is inert — that is the point. If it fails, the desktop branch was changed, which this task is not allowed to do.
- [ ] **Step 5: Flip `before.png` against the new PNG.** Expect a difference: the stage is no longer scaled to ~0.92, so everything is ~8 % larger and the bottom ~55 px now scrolls instead of shrinking. **This is the one visible change the plan accepts, and it must be shown to the user before Task 14.** If it is not acceptable, the fallback — keep `useFitScale` **only** in a `@media (min-width: 1280px)` branch — is a two-line change from here; the media-query structure is what makes that cheap.
- [ ] **Step 6: Commit** alone.

---

### Task 14: REFLOW 4 — the phone viewport, and the whole gate

**Files:** Create `tests/e2e/dashboard-reflow.spec.ts`; Modify `playwright.config.ts`.

- [ ] **Step 1: Add the `reflow` project** at 390×844, on the demo server (same isolation argument as Task 1):

```ts
{ name: "reflow", testMatch: /dashboard-reflow\.spec\.ts/,
  use: { baseURL: "http://localhost:5174", viewport: { width: 390, height: 844 } } },
```
- [ ] **Step 2: Write the spec.** Four assertions, each one a thing C8 said was broken:
  - **No horizontal overflow:** `document.documentElement.scrollWidth <= window.innerWidth`.
  - **Not letterboxed:** the dashboard root's rendered width is 390 (±1), not 1280 scaled down.
  - **Readable type:** the probe temperature's computed `font-size` parses to **≥ 36 px**. (At the old 0.30 scale it rendered at ~20 px — the audit's headline number.)
  - **Touch targets:** every `.pf-btn`'s `getBoundingClientRect().height >= 44`.
  Also capture `artifacts/dashboard-390x844.png` with the clock frozen, for the record.
- [ ] **Step 3: Run everything.** `bun run typecheck && bun run lint && bun run test && bun run build && bun run gen:types:check`, then `bun run test:e2e` across **all three** projects. Then the repo-root artifact check (`os_info.json` / `settings.json` / `pelletdb.json` absent).
- [ ] **Step 4: Re-run the fidelity project a second time** and confirm it is still green. A gate that passes once is a coincidence.
- [ ] **Step 5: Commit.**

---

## Parallelization

Isolated jj workspaces per concurrent task; disjoint files are necessary but **not** sufficient (see the standing rule).

- **Wave 0 — Task 1 alone.** It touches every dashboard component (attributes) and both configs. Nothing runs beside it, and nothing in this plan starts before it lands.
- **Wave 1 — Task 2 ∥ Task 3 ∥ Task 5.**
  - T2: `health.ts`, `controlHealth.ts`, `ControlButtons.tsx`, `Dashboard.tsx` header.
  - T3: `cookTime.ts`, `Dashboard.tsx` cook-time block.
  - T5: `buttonsForMode.ts` only.
  - **T2 and T3 both edit `Dashboard.tsx`** in disjoint regions (header vs cook row). If that is uncomfortable, run T3 → T2; T3 is the smaller diff.
- **Wave 2 — Task 4 ∥ Task 6 ∥ Task 9 ∥ Task 10.**
  - T4 needs T5's `ButtonAction` union to be settled.
  - T9 must **not** run concurrently with the C1 notifications plan's Task 4 (both edit `ProbeCardView` and `ProbeCard.tsx`'s header row).
  - T10 must **not** run concurrently with the C1 plan's Task 1 (both want a `POST /api/control` helper in `command.ts`).
- **Wave 3 — Task 7, then Task 8** (T8 consumes `ActionMenu`). Sequential.
- **Wave 4 — Task 11 alone.** It rewrites every style object in the surface. Nothing may run beside it, and it must land after **all** behaviour tasks, so the DOM it converts is the final one.
- **Wave 5 — Task 12 alone.** Then **Task 13 alone.** Then **Task 14.** Strictly sequential: this is the "make a regression attributable" requirement — three separate commits, each independently gated, so "which change moved the header" has exactly one candidate.

**Cross-plan serialization**

- **Slice 3 must run alone against `web-react/src/components/dashboard/`.** The only exception is the C1 notifications plan, which shares four files; if both are live, alternate waves and never overlap T9/T10 with its T1/T4/T5.
- The Slice 2 wizard plan (`components/wizard/**` + `ConfirmAction.tsx`) can run concurrently **except** with Task 4.
- The Slice 4 settings plan is fully disjoint and can run throughout.

---

## Things I could not verify

- **How the reflowed layout actually looks.** jsdom does no layout, and I did not run a browser. Every breakpoint value in Task 13 is a starting point derived from the authored constants, not an observation. **Task 13 Step 5 is a mandatory human checkpoint**, not a formality.
- **The exact fit scale at a 1280×720 window** (~0.92). It is arithmetic from the navbar's declared padding and font size (`shell.css:41,157`), not a measurement. Task 1 measures it for real — `s` is computed in `measureLandmarks` and worth logging on the first run.
- **Whether the demo fixture has `hasDistanceSensor: true`.** Task 10 Step 4 depends on it and says what to do either way.
- **Flask's `#recipe_group` unpause payload.** Task 5 records why it is not ported (an array-replacing `json_patch` merge) rather than porting it blind. Somebody must verify that reasoning against a live recipe before Slice 2 of the recipe work.
- **`backlogs/react-migration-backlog.md` does not exist.** The triage and two other docs reference it (`docs/superpowers/plans/2026-07-24-react-small-batch.md`, `docs/superpowers/specs/2026-07-23-wizard-module-config-display-first.md`), but `find docs -iname "*backlog*"` returns nothing and it is absent from every commit in this repo. The M5/M8/M7 divergence records this plan asks for therefore need a home — create the file or name a substitute.

## Self-Review

**Spec coverage:** I3 → T4; controlAlive/D1 → T2; C3 → T3; I4 → T5 (behaviour bug) + T6 (readouts); I1 → T8; I2 → T7; M6 → T9; M7 → **dropped, owned by the C1 plan**; M8 → T10 (Refresh only; Manager link dropped by the app-shell decision); C8 → T1 + T11 + T12 + T13 + T14; M5 → shrinks the reflow's problem space, documented, no task.

**Placeholder scan:** none — every step names the file, the line and the verified backend contract. The three "decisions the triage demanded" (I1 control-vs-delete, C3 duration format, D1 fix shape) are each made explicitly with a reason rather than deferred.

**Type consistency:** `ButtonAction` gains `"startup"` in T4 and `"menu"` in T7, both consumed by `ControlButtons`; `ActionMenu` defined in T7, consumed by T8; `ProbeCardView` extended in T9 (coordinated with the C1 plan's extension of the same interface); `LandmarkMap` defined in T1, consumed by T14; the `--pf-*` token names defined in T12 are the exact set overridden in T13.

**Fidelity honesty check:** the gate is stated numerically (±0.5 authored / ±2 derived / exact typography), its mechanism is committed and diffable, the reviewer's rule is written down, the one accepted visible change is called out in advance with its fallback, and the limits of what can be tested without a browser are stated rather than papered over.
