# Config wizard: dynamic, per-run reboot detection + optional reboot

## Problem

When the setup wizard finishes installing selected modules, [wizard.py](../../../wizard.py)
decides whether to reboot the Pi or just restart supervisord, based on a **static**
`reboot_required` flag baked into each module's entry in
[wizard_manifest.json](../../../wizard/wizard_manifest.json). If any selected module has
`reboot_required: true`, [wizard.py:354-367](../../../wizard.py) sets `percent = 142`;
[wizard-finish.html:66-73](../../../blueprints/wizard/templates/wizard/wizard-finish.html)
polls install status and, with **no confirmation**, redirects straight to `/admin/reboot`,
which calls `reboot_system()` and reboots the machine.

This is wrong in two ways:

1. **No user choice.** The user never gets a say — the Pi just reboots. The equivalent
   restart-only path already exists (`/admin/restart` → `restart_scripts()`, which restarts
   the `supervisor`/`supervisord` service) but is never offered as an alternative when a
   reboot is flagged.
2. **The static flag is wrong/over-broad.** It's set per module *selection*, not per actual
   system change:
   - All DSI/QtQuick DSI display modules (`ili9341f`, `dsi_800x480t`, `dsi_1024x768t`,
     `qtquick_dsi_1024x600t`, `qtquick_dsi_1280x720t`, `dsi_1024x600t`, `dsi_1280x720t`) are
     flagged `true`, but their only install command is `board-config.py -bl`
     (`set_backlight()`), which just writes a udev rule
     (`/etc/udev/rules.d/backlight-permissions.rules`) — never touches `/boot/config.txt`.
     A udev rule doesn't need a reboot to take effect.
   - `display.protoflex` is flagged `true` with an **empty** `command_list` — nothing runs
     at all, so nothing could possibly need a reboot. This is a clear copy/paste artifact.
   - Even for modules that *do* write `dtparam=`/`dtoverlay=` lines to `config.txt`
     (grillplatform PCB selections, `ds18b20`'s onewire overlay), the flag fires on every
     wizard run regardless of whether the resulting config line is actually different from
     what's already on disk. Re-running the wizard with the same PCB/pin selection forces a
     reboot even though nothing changed.

## Goal

- Replace the static per-module `reboot_required` manifest flag with a **dynamic, per-run**
  signal: the actual install commands report whether they made a change that genuinely
  requires a reboot to take effect (a `config.txt` dtoverlay/dtparam edit), not whether the
  module type is generically capable of needing one.
- If nothing selected in this run actually changed anything reboot-worthy, no reboot is
  offered or forced — matches today's `percent = 101` (auto-restart) path.
- If something *did* require a reboot, give the user a choice — reboot now, or just restart
  services (supervisord) — instead of silently rebooting.
- Fix the DSI/QtQuick "always requires reboot" bug as a side effect of the dynamic detection
  (backlight udev rule changes never count as reboot-worthy).

Non-goals: `updater.py` / `updater_manifest.json` has its own, separate `reboot_required`
flow for the software-update path. That is out of scope here — this design only touches the
setup/config wizard (`wizard.py`, `wizard/wizard_manifest.json`,
`blueprints/wizard/templates/wizard/wizard-finish.html`).

## Architecture

### 1. Sentinel protocol: commands report reboot necessity over stdout

Any command in a module's `command_list` may print a final line:

```
REBOOT_REQUIRED=true
REBOOT_REQUIRED=false
```

`wizard.py`'s command-execution loop parses this line (case-insensitive) out of each
subprocess's captured stdout and ORs the boolean into a single `reboot_required` for the
whole install run. **Absence of the line is treated as `false`** — so commands that never
need a reboot ([raspi5.sh](../../../wizard/raspi5.sh), [bluepy.sh](../../../wizard/bluepy.sh))
require no changes at all.

### 2. `board-config.py`: diff-based `changed` reporting

[rpi_config_write()](../../../board-config.py:210) currently always rewrites
`/boot/config.txt` unconditionally and returns only a human-readable message string. Change
it to:

- Snapshot `config_data` (the file's lines) before editing.
- Build the modified `config_data` as today.
- Compare before vs. after. Only write the file, and only report `changed=True`, if the
  content actually differs. This single diff check correctly handles all three cases
  uniformly: newly enabling a feature, re-enabling an already-identical feature (no-op), and
  disabling an already-disabled (commented-out) feature (no-op).
- Return `(message, changed)` instead of just `message`.

Every wrapper that calls `rpi_config_write()` — `set_pwm_gpio`, `set_onewire_gpio`,
`enable_spi`, `enable_i2c`, `set_i2c_speed`, `enable_gpio_shutdown` — is updated to thread
`changed` through and return `(message, changed)` itself.

`enable_i2c()` also calls `append_file('/etc/modules', 'i2c-dev\n')`. `append_file()` gets an
idempotency check (skip the append, report `changed=False`, if the line is already present)
so repeated wizard runs don't duplicate the line or wrongly report a change every time.

`set_backlight()` (the `-bl` flag, used by every DSI/QtQuick display module) only ever writes
a udev rule, never `config.txt`. It now **unconditionally returns `changed=False`** — this is
the concrete fix for the DSI/QtQuick over-flagging bug.

`__main__` collects every wrapper's `changed` flag and, after printing the existing
human-readable `Results:` block, prints one final line:

```python
print(f'REBOOT_REQUIRED={reboot_required}'.lower())  # -> "reboot_required=true"/"...=false"
```

(lowercased for a consistent, case-normalized sentinel regardless of caller.)

### 3. `ds18b20.sh`: same idempotency idea for the onewire overlay

Before calling `raspi-config nonint do_onewire 0`, grep the active config.txt
(`/boot/firmware/config.txt` or `/boot/config.txt`) for an existing, uncommented
`dtoverlay=w1-gpio` line. If present, skip the `raspi-config` call and print
`REBOOT_REQUIRED=false`; otherwise run it and print `REBOOT_REQUIRED=true`.

This can't be exercised end-to-end in this dev environment (no real `raspi-config`/Pi
hardware) — flagged for a manual smoke test on real hardware before merge.

### 4. `wizard.py`: consume the signal, drop the static flag, fix a line-loss bug

- Remove the manifest-based aggregation at
  [wizard.py:235-236](../../../wizard.py) (`if WizardData[...]['reboot_required']:
  reboot_required = True`).
- In the `command_list` execution loop
  ([wizard.py:330-352](../../../wizard.py)), parse each output line for the
  `REBOOT_REQUIRED=` sentinel and OR the parsed boolean into `reboot_required`.
- **Bug fix required for correctness:** the loop currently does:

  ```python
  while True:
      output = process.stdout.readline()
      if process.poll() is not None:
          break
      if output:
          ...
  ```

  This discards the last line read whenever the child process has already exited by the time
  `poll()` is checked — which is exactly what happens with our new sentinel line, since it's
  always the last thing a script prints before exiting. Reorder to process `output` first and
  only break on a genuinely empty read (true EOF):

  ```python
  while True:
      output = process.stdout.readline()
      if output:
          ...
      elif process.poll() is not None:
          break
  ```

  The same latent bug exists in the apt-dependency and pip-dependency install loops earlier
  in `wizard.py`; fixing it there too for consistency (lower stakes there since nothing reads
  a sentinel from those, but it's the same defect).

- In dev/test mode (`is_real_hardware() == False`), no subprocess ever runs, so
  `reboot_required` naturally stays `False`. This is an intentional behavior change from
  today (where the static manifest flag could still force `percent = 142` in dev/test) — it's
  more correct, since nothing real changed on a dev machine, and it makes the restart-only
  path exercisable in dev.

### 5. `wizard_manifest.json`: remove the now-unused static field

Strip `reboot_required` from all module entries (mechanical, ~80 occurrences). Confirmed via
grep this field is not read anywhere in the frontend/UI — it's wizard.py-internal only, so no
other consumer needs updating.

### 6. Reboot-optional modal on the finish screen

In [wizard-finish.html](../../../blueprints/wizard/templates/wizard/wizard-finish.html), when
the poll sees `percent == 142` (dynamically computed reboot_required), instead of immediately
redirecting to `/admin/reboot`, show a static Bootstrap modal matching the existing
`cancelModal`/`runningModal` pattern in
[wizard.html:298-335](../../../blueprints/wizard/templates/wizard/wizard.html):

- `data-backdrop="static" data-keyboard="false"`, no dismiss/X — the user must pick one.
- Body text explains that a hardware-level change (e.g. GPIO/I2C/SPI/onewire overlay) needs a
  real reboot to take effect.
- Two buttons: **"Reboot Now"** (primary/warning style, default emphasis) →
  `location.href='/admin/reboot'`; **"Restart Services Only"** (secondary) →
  `location.href='/admin/restart'`.
- No countdown/auto-reboot fallback.

When `percent == 101` (no reboot-worthy change happened), behavior is unchanged: auto-redirect
to `/admin/restart`, no modal — nothing forced a reboot in that case before, so no prompt is
needed.

No backend route changes needed — `/admin/reboot` and `/admin/restart` already exist and do
exactly the two things needed.

## Testing

Real hardware (`sudo systemctl reboot`, `sudo reboot`, `raspi-config`) must never actually run
during tests. All tests below run with `is_real_hardware()` mocked/forced `False` (or system
type forced to a non-Pi value where the code branches on `system_type`), and any `subprocess`/
`os.system` calls are mocked, not executed — per the standing rule to neutralize these before
verification (a `real_hw=False`-style flag alone is not trustworthy).

- **`board-config.py` unit tests** (new, e.g. `tests/test_board_config.py`):
  - `rpi_config_write()` on a scratch/temp config.txt file: enabling a feature not yet
    present → `changed=True` and line added; enabling a feature whose line already matches
    the desired state → `changed=False` and file untouched (mtime/content unchanged);
    disabling a feature already commented out → `changed=False`.
  - `set_backlight()` → always returns `changed=False`, regardless of whether the udev rule
    file content changed.
  - `enable_i2c()` → `append_file` idempotency: appending `i2c-dev` to a `/etc/modules` stand-in
    that already contains it does not duplicate the line and reports `changed=False`; a
    version state where the correct dtparam already exists reports overall `changed=False`.
  - `__main__`/CLI-level: invoking with flags against a temp config.txt prints a final
    `REBOOT_REQUIRED=true`/`false` line matching the aggregated `changed` state. Uses
    `subprocess`-free invocation (call the module's functions directly, or run the script with
    `sys.argv` patched) — no real system commands.

- **`wizard.py` unit tests** (new/extended, e.g. `tests/test_wizard_reboot.py`):
  - Feed the command-execution loop a fake subprocess (mock `subprocess.Popen`) whose stdout
    yields lines ending in `REBOOT_REQUIRED=true` as the very last line, with `poll()`
    reporting "exited" immediately after that line is available — asserts the line is *not*
    dropped (regression test for the readline/poll ordering bug) and `reboot_required` ends up
    `True`.
  - Same with `REBOOT_REQUIRED=false` as the last line → `reboot_required` stays `False`.
  - No sentinel line at all (simulating `raspi5.sh`/`bluepy.sh`) → `reboot_required` stays
    `False`.
  - Multiple commands in `command_list`, only one reporting `true` → aggregate is `True`
    (OR semantics).
  - `is_real_hardware() == False` path → no subprocess run, `reboot_required` is `False`,
    `percent` resolves to `101`.

- **`ds18b20.sh` test**: since this is a shell script, test it with a temp `config.txt`
  stand-in and a stubbed `raspi-config` (a fake executable earlier on `PATH` that just
  echoes/no-ops) so the real `raspi-config nonint do_onewire 0` is never invoked. Assert: when
  the temp config already has `dtoverlay=w1-gpio`, the stub is never called and
  `REBOOT_REQUIRED=false` is printed; when absent, the stub is called once and
  `REBOOT_REQUIRED=true` is printed.

- **Frontend (`wizard-finish.html`) test**: manual/JS-level check (no existing JS test
  harness in this repo for wizard templates) that the modal appears only when
  `data.percent == 142`, both buttons are wired to the correct `location.href` targets, and no
  auto-redirect fires before a button is clicked. If a lightweight DOM test is feasible given
  existing tooling, add one; otherwise this is called out explicitly as manual-verification
  scope rather than silently skipped.

## Open items / assumptions to confirm

- Assumed `raspi-config nonint do_onewire 0` writes exactly `dtoverlay=w1-gpio` (no explicit
  pin, defaults to GPIO4) to config.txt — the grep check in `ds18b20.sh` depends on this.
  Needs confirmation on real hardware since `raspi-config` isn't available in this dev
  environment.
