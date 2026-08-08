# Settings field contract, missing UI chrome, and the two dead cycle settings

**Date:** 2026-08-08
**Status:** approved, ready for planning

Six items from `backlogs/react-migration-backlog.md`, plus one retirement the
research turned up. Each backlog claim below was verified against live code
before it was designed for; the backlog's line numbers have drifted (it cites
`HistoryPage.tsx:156` in a 143-line file), so the citations here are the
verified ones.

| # | Item | Backlog source |
|---|---|---|
| A | Settings save-error placement + per-setting descriptions | item 16, UI parity |
| B | `ui_hash` reload prompt has no counterpart | UI parity |
| C | No PWA manifest | Shipping and deployment gaps |
| D | Controller "use recommended value" buttons | UI parity |
| E | Discovery results lost Refresh/Close | UI parity |
| F | Retire `cycle_data.u_min` and `cycle_data.HoldCycleTime` | found here |

---

## A. One field contract for errors and descriptions

Items 16 and the "per-setting Description dropped" parity line are the same
surface, so they land as one change.

### Where it stands

`NumberField` is the only field with an `error` slot; `NumberField` and
`Toggle` are the only ones with a `hint`. `TextField`, `ColorField`,
`StringListField`, `SecretField` and `Select` have neither.
`wizard/ConfigOptionField` takes exactly `{option, value, onChange}` and never
renders `option.option_description`, which `controllers.json` supplies on every
option. `NumberField` and `Toggle` each hand-roll their own `aria-describedby`
id-list assembly.

Only `StartupTab` is wired for errors, via a hand-maintained `CLAIMED_PATHS`
array and a guard test. Nine of the twelve tabs render a `SaveBar` at all
(`PlatformTab` is read-only by ruling; `ProbesTab` and `UnitsTab` save by other
routes).

### The design

**`components/settings/fields/Field.tsx`** owns the `.pf-field` label markup,
the hint span, the error span, and the `aria-describedby` + `aria-invalid`
wiring. Every settings field composes onto it, as does
`wizard/ConfigOptionField`. Writing the id juggling once is the point: today it
exists twice and is absent six times.

**`SettingsFieldErrors` context** carries the last save's `SaveFieldError[]`
plus a `claim(path)` registration. A `Field` given a `path` reads its own error
from context and registers that path **on mount** — not when an error exists.
The claimed set is therefore "paths with a slot actually on screen", by
construction. `SaveBar` renders the unclaimed remainder from the same context.

`CLAIMED_PATHS`, the `paths`/`errors` props on `SaveBar`, and the guard test are
deleted. `errorFor` survives as the lookup; `unmatchedErrors` becomes
context-driven.

**`RangeProfileTable`** (`components/settings/RangeProfileTable.tsx`) is not a
`Field` — it is a table — so it takes an error slot of its own and claims its
path through the same context.

**Descriptions ride the `hint` slot.** `ControllerTab` passes each option's
`option_description` as that option's hint; `ConfigOptionField` renders it too.

### The consequence worth stating

A field hidden behind a toggle is not mounted, so its path is unclaimed and its
error surfaces in the `SaveBar` rather than nowhere. That is correct, and it is
the case a declared list gets wrong — which is exactly the defect the Task 8 fix
round hit, where the claimed list named paths no widget could display.

### Testing

- An error for a path whose field is conditionally hidden appears in `SaveBar`
  and not inline; an error for a mounted field appears inline and not in
  `SaveBar`. Both directions, mutating the registration to prove each fails.
- `aria-describedby` references only ids that render, for a field with a hint
  alone, an error alone, and both.

---

## B. `ui_hash` becomes a silent settings refetch

### What Flask did, and why it does not port

`common/app.py:59` is `hash(json.dumps(settings["probe_settings"]["probe_map"]
["probe_info"]))` — it tracks the probe map and nothing else. Flask's clients
compared it per frame and opened `#serverReloadModal`: *"A server side change
was detected (probably some probes got reconfigured) and needs to reload this
page."* A full-page reload was the right answer there because Flask
server-rendered the probe cards and chart series into the DOM.

React does not have that problem. Probe readings arrive on `socket_dash_data`
and the cards rebuild themselves. What does **not** refresh is
`Dashboard.tsx:114`'s one-shot `queryClient.fetchQuery(queryKeys.settings)` —
and `set_probe_map()` rebuilds `history_page.probe_config`,
`settings["recipe"]["probe_map"]`, `dashboard[*].custom.hidden_cards` and
`control["notify_data"]` off probe labels. Those are what go stale.

### The design

`ui_hash` is on `/api/get/status` (`blueprints/api/routes.py:72`) and **not** in
the socket payload — `_get_dash_data` (`blueprints/mobile/socket_io.py:279`)
does not carry it. Rather than add a poll for a value that changes almost never,
add it to the socket frame, which already pushes at 1 Hz and already holds
`settings`. `create_ui_hash()` does its own `read_settings()`, so it takes an
optional settings argument rather than costing a datastore read per second.

`AppShell` watches the value and calls
`invalidateQueries({queryKey: queryKeys.settings})` on a change. No modal, no
reload.

### The property to accept deliberately

Python salts `hash()` for strings, so the value changes on every server restart
even when the probe map has not — `tests/characterization/
test_process_command_golden.py:120` records this and the golden strips it. Under
a modal design that is a spurious reload prompt; under a silent refetch it is one
wasted settings read. This is an argument for the chosen shape, not a defect to
fix.

### Testing

This is a cross-process seam, so it is pinned at both ends: a backend test that
the emitted frame carries the field, and a client test that a changed value
invalidates the settings query while an unchanged one does not.

---

## C. PWA manifest

Flask's `blueprints/manifest/` served a `manifest.json` naming
`launcher-icon-{2x,3x,4x}.png` at 96/144/192. All four `launcher-icon-*.png`
are still in `static/img/`.

Copy the values verbatim: `short_name: PiFire`, `name: PiFire - Pellet Smoker
Controller`, `start_url: /`, `display: standalone`, `orientation: portrait`,
`background_color: #FFFFFF`, `theme_color: #3b3b3b`.

Icons are referenced from `/static/img/`, not bundled. That tree falls through
to Flask's default static handler — `blueprints/spa/routes.py` deliberately
shadows only `/static/js`, `/static/css` and `/static/font` — so one href
resolves in production and through the dev proxy alike, exactly as the favicon
already does.

The manifest itself needs its own spa route. Without one, `/manifest.webmanifest`
reaches the SPA catch-all and is served `index.html`.

### Testing

Mirror `test_spa.py::test_favicon_is_declared_and_the_declared_path_is_served`:
read the href out of the shipped shell, fetch exactly that, and assert each
declared icon path is served.

---

## D. One recommended-value button

`_macro_settings.html:104/120/136` shows Flask offered three buttons — wired to
`#holdcycletime`, `#u_min` and `#u_max`, each labelled with the value, titled
"Click to Use Recommended Value.", setting the input without saving.

Two of those three settings are being retired (section F), so **one button
ships**: `u_max`, on `WorkModeTab`, staging into that tab's draft without
saving. `WorkModeTab` reads the selected controller's `recommendations` from
controller metadata; `getControllerMetadata` fails open with `null`, so the
button does not render when metadata is unavailable.

Note what this is: nothing reads `controllers.json`'s `recommendations` block
today. This button is its first consumer.

---

## E. Discovery Refresh/Close

`wizard/DiscoveryPanel.tsx` renders one button per discovered item and nothing
else. It gains **Refresh**, which re-runs the scan, and **Close**, which
dismisses the results without picking. The Flask original went with the
retirement pass, so there is no markup left to match; this is the minimal
honest pair.

---

## F. Retire `u_min` and `HoldCycleTime`

### The finding

`hold.py:145` takes its frame from `scheduler.timing.frame_s`. `PulseScheduler`
has "no clock, hardware, controller, or settings dependencies" by its own
docstring and defaults to `AUGER_TIMING` — `AugerTiming(pulse_s=2, frame_s=20)`,
a frozen dataclass in `grillplat/actuator_capabilities.py`. **Hold's cycle is a
code constant.**

That explains both settings:

- **`u_min` has no production reader.** The duty floor is now `pulse_s/frame_s`
  = 0.1 — the same number `u_min` defaults to, arrived at from the pulse
  geometry instead. The only non-test mentions left are `fan_assist_times`
  (`controller/runtime/logic/fan.py:41`), whose only callers are its own tests,
  and two comments asserting a clamp that does not happen
  (`applied_output.py:11`, `hold.py:102`).
- **`HoldCycleTime` does not set the hold cycle.** Its one production reader is
  `pid_sp.py:99`. `pid.py` takes `cycle_data` and reads nothing from it;
  `mpc.py` reads only `u_max`; Smoke and Startup use `SmokeOnCycleTime` /
  `SmokeOffCycleTime`.

### The change

- Delete both from `settings_schema.py` (`u_min` at :97) and `defaults.py`
  (:128), and their `WorkModeTab` controls.
- Delete `fan_assist_times` and `tests/unit/runtime/test_logic_fan.py`.
- Correct the two comments so they describe the clamp that exists.
- Repoint `pid_sp.py:99` at `AUGER_TIMING.frame_s`.
  `controller/runtime/logic/pulse.py` already imports from
  `grillplat.actuator_capabilities`, so the layering has precedent.
- Drop `cycle_time` and `cycle_ratio_min` from all three `recommendations.cycle`
  blocks in `controllers.json`, leaving `cycle_ratio_max`.

**No settings migration.** Both keys shed through `validate_settings_tree()`'s
repair pass on the next validated write — the path `global_control_panel` and
`bootstrap_page_theme` took.

### This changes controller behaviour

`pid_sp` uses `cycle_time * 3` twice: an integral reset when a new target has
not reached halfway (`:282`), and a `STARTUP_REDUCTION = 0.65` ease-off for the
first three cycles after a setpoint change (`:314`). At the shipped default of
25 s those windows are 75 s; at `frame_s = 20` they become 60 s. Anyone running
`pid_sp` gets a real change in setpoint-change behaviour.

**This requires a before/after on GrillSim**, not an assertion that 20 is more
correct than 25. The measurement must state in advance what it decides — the
bar is that setpoint-change overshoot and settling do not regress — rather than
being run open-ended. It sequences last so the five UI items are not blocked
behind it.

---

## Fidelity baselines

Descriptions change page height, so committed baselines will break. The order is
**human checkpoint first, recapture second**. Recapturing first bakes in
whatever the reviewer was about to object to, and a green gate then becomes the
evidence that it was fine — the lesson item 5 of the backlog already paid for.

## Sequencing

A, B, C, D and E are independent of each other and of F. F sequences last
because of the GrillSim validation. A is the largest: it touches every settings
tab that renders a field, which is eleven of the twelve — `PlatformTab` uses no
field components at all. C and E are small and self-contained.
