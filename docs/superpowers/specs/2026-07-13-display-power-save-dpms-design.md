# Display power-save: cage output DPMS driven by the cook-aware idle machine

## Problem

On this hardware (`x86_numato`, display module `qtquick_dsi_1280x720t`) the panel is a
DisplayPort monitor with **no** sysfs backlight — `/sys/class/backlight/` is empty. The
existing screen-sleep machine can therefore never actually power the screen down:

- The Qt backend already has a cook-aware idle machine (`qtbackend._update_idle`): after
  `TIMEOUT` seconds of no interaction *in Stop mode* it sets `asleep = True`; a cook keeps
  the screen awake; touch wakes it (`registerInteraction`).
- On `asleepChanged`, `qtapp.run_app` toggles a **backlight** via `rpi_backlight`
  (`/sys/class/backlight/<dev>`). With no backlight device, `_make_backlight()` returns a
  `DummyBacklight` no-op, so sleep only paints the black `sleepOverlay` — the monitor stays
  fully powered.

Additional defects surfaced while diagnosing:

- `TIMEOUT = 10` is a hardcoded debug value in three places (`qtbackend.py:124`,
  `base_flex.py:81`, and the DSI pygame path via the flex base). Not user-configurable.
- The guard `Path('/sys/class/backlight/').exists()` returns `True` for an empty directory,
  so the code believes it has a backlight when it does not.

While cage holds the DRM master, writing `/sys/class/drm/<conn>/dpms` does nothing — the
compositor owns KMS state. Screen-off must go through the compositor.

## Goal

Make the idle machine actually power the monitor off/on under cage, and make the idle
timeout a user setting. Keep PiFire's cook-aware policy (never sleep during a cook) as the
single source of truth for *when* to sleep; the compositor only provides the *mechanism*.

Non-goals: moving to weston; implementing screen-off for non-wayland display kinds (SPI /
pygame-SDL keep their existing backlight behavior); changing how any display renders;
touching the QML.

## Why cage, not weston

cage implements `zwlr_output_manager_v1`, so `wlr-randr --output <name> --off/--on` disables
/ re-enables the DP output (cutting the video signal → the monitor enters standby). cage
does **not** implement `wlr-output-power-management`, so `wlopm` is not an option; `wlr-randr`
is. Because PiFire's cook-aware policy already lives in the Qt idle machine, the app itself is
the idle daemon — no `swayidle` is needed, and moving to weston (whose native `idle-time`
DPMS is not cook-aware) would mean rebuilding that policy via idle-inhibit plumbing. Driving
`wlr-randr` from the existing `asleep` transition is the smallest change that keeps the
policy and turns the screen truly off.

## Architecture

### 1. Global setting — `settings['display']['sleep_timeout']`

Integer seconds; `0` = never sleep. Default **300**.

- Added to the display settings block in `common/common.py` (the `settings['display'] = {...}`
  default construction around L217, sibling to `selected` / `config`) and to settings
  migration/upgrade defaults so existing installs gain the key.
- Surfaced on the settings page (`blueprints/settings/`) as a numeric field labelled e.g.
  "Screen sleep timeout (seconds, 0 = never)" in a display power-save row.
- Read by both idle machines to replace the hardcoded `TIMEOUT = 10`:
  - `display/qtbackend.py` — the Qt/cage path (primary target of this work).
  - `display/base_flex.py` — the pygame flex path (keeps its own backlight mechanism; only
    the timeout source changes).
  A `sleep_timeout` of `0` short-circuits `_update_idle` so `asleep` is never set true.

**Apply timing — live, ~1 Hz.** The Qt backend already re-reads the accent theme every ~1 s
in `poll()` (`_last_accent_check`). `sleep_timeout` is re-read on the same cadence and applied
to `self.TIMEOUT`, so changing it in the UI takes effect without restarting the display. The
read reuses the existing settings accessor already imported for the accent lookup.

### 2. New mechanism — `display/screen_power.py` : `ScreenPowerController`

A small strategy object that maps a display *kind* to a screen-power mechanism. No Qt import;
fully unit-testable via an injected runner.

```
class ScreenPowerController:
    def __init__(self, display_kind, run=subprocess.run):
        # display_kind in {'wayland', 'sdl', 'framebuffer', ...}
    def resolve_output(self):
        # wayland: run wlr-randr, parse the connected/enabled output name, cache it
        # other kinds: return None  (unimplemented seam)
    def set_output_power(self, on):
        # wayland: wlr-randr --output <resolved> --on | --off
        # other kinds: no-op
```

- **`display_kind`** selects the branch. Only `'wayland'` is implemented now; other kinds are
  safe no-ops so the controller can be constructed and called unconditionally. This is the
  seam the design deliberately leaves for future non-wayland power control.
- **`resolve_output`** runs `wlr-randr` (no args) and returns the single connected/enabled
  output name (e.g. `DP-1`), so the output is not hardcoded. Result cached; re-resolved lazily
  if a call finds it stale/absent. Robust to a missing `wlr-randr` binary (logs, returns
  None → `set_output_power` becomes a no-op rather than crashing the display).
- **`set_output_power(on)`** builds `['wlr-randr', '--output', <name>, '--on' | '--off']` and
  invokes `run`. Failures are logged, never raised into the Qt loop.
- `run` is injected (default `subprocess.run`) purely so tests assert argv without spawning
  anything.

`wlr-randr` runs inside cage's Wayland session: `WAYLAND_DISPLAY` is set by cage for its child
(`display_process.py`) and inherited by the Qt child process, so a subprocess `wlr-randr`
connects to the running compositor.

### 3. Wiring (Qt/cage path only) — `display/qtapp.py`

`run_app` already connects `backend.asleepChanged` to `_apply_backlight`. Extend that handler
(or add a sibling connected to the same signal) to also drive the controller:

```
controller = ScreenPowerController('wayland')
def _apply_power():
    controller.set_output_power(not backend.asleep)
backend.asleepChanged.connect(_apply_power)
_apply_power()
```

The dummy-backlight toggle stays (harmless, and correct on hardware that *does* have a
backlight). The black `sleepOverlay` stays as the graceful in-app cover. On wake, `wlr-randr
--on` re-enables the output; the Qt surface re-maps and the poll loop redraws. Touch wakes
because disabling the output does not disable the touch input device — cage still delivers the
tap to Qt → `registerInteraction` → `asleep = False` → `--on`.

### 4. Installers

Add `wlr-randr` to the package install lists alongside the existing cage/seatd additions in
`auto-install/install.sh` (apt), `auto-install/pifire-dietpi.sh` (apt),
`auto-install/install-fedora.sh` (dnf).

## Data flow

```
poll() every frame
  -> _update_idle(mode, now)          # TIMEOUT from settings['display']['sleep_timeout'] (0 = never)
       -> _asleep True/False
            -> asleepChanged
                 -> qtapp handler
                      -> ScreenPowerController('wayland').set_output_power(not asleep)
                           -> wlr-randr --output <resolved> --on|--off
  (~1 Hz) re-read sleep_timeout -> self.TIMEOUT
```

## Error handling

- **`wlr-randr` missing / errors**: `ScreenPowerController` logs and no-ops; the display keeps
  running (only the physical power-off is lost). A fresh install has `wlr-randr` via §4.
- **No connected output resolved**: `resolve_output` returns None → `set_output_power` no-ops.
- **`sleep_timeout` missing/invalid in settings**: treated as the default 300; `<= 0` disables
  sleeping.
- **Non-wayland display kind**: controller is a no-op; those paths retain their existing
  backlight-based sleep.

## Testing

Unit tests only; nothing spawns cage or `wlr-randr`.

- `ScreenPowerController` (`display_kind='wayland'`):
  - `resolve_output` parses a captured `wlr-randr` sample to the connected output name;
    handles the no-output and missing-binary cases.
  - `set_output_power(True/False)` invokes the injected `run` with the expected
    `wlr-randr --output <name> --on|--off` argv; caches the resolved output.
  - A non-wayland kind makes both methods no-ops (no `run` calls).
- Timeout wiring:
  - `qtbackend` reads `settings['display']['sleep_timeout']` into `TIMEOUT`; `0` makes
    `_update_idle` never set `asleep`; a positive value sleeps after that many seconds in Stop.
  - Live re-read updates `TIMEOUT` on the ~1 Hz cadence (extend existing `test_qtbackend.py`).

## Files

- `display/screen_power.py` — new `ScreenPowerController`.
- `tests/test_screen_power.py` — new; controller unit tests.
- `display/qtapp.py` — construct the controller, drive it from `asleepChanged`.
- `display/qtbackend.py` — read `sleep_timeout` into `TIMEOUT` (startup + ~1 Hz), `0`-disables.
- `display/base_flex.py` — read `sleep_timeout` into `TIMEOUT` (timeout source only).
- `common/common.py` — `settings['display']['sleep_timeout']` default (300) + migration.
- `blueprints/settings/` — settings-page field for the timeout.
- `tests/test_qtbackend.py` — extend for the timeout read + `0`-disable.
- `auto-install/install.sh`, `pifire-dietpi.sh`, `install-fedora.sh` — add `wlr-randr`.
