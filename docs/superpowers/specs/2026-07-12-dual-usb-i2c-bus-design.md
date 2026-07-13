# Dual USB I2C Bus Support (FT232H + MCP2221A) — Design

**Date:** 2026-07-12
**Status:** Approved design, pending implementation plan

## Goal

Let PiFire drive two independent USB-based I2C buses in the same process at the
same time — for example, on an x86 + Numato build:

1. An **FT232H** I2C bus carrying the temperature probes and the distance sensor.
2. An **MCP2221A** I2C bus carrying the EMC2101/EMC2301 fan controller.

The assignment must be arbitrary: any I2C device (probe, distance sensor, fan
controller) can be placed on either bus. The wizard gains two new bus kinds,
`ft232h` and `mcp2221a`, alongside the existing `basic` and `extended`.

## Background

Every I2C device today opens its bus one of two ways:

- **`basic`** → `busio.I2C(board.SCL, board.SDA)`. This goes through Adafruit
  Blinka's `board` module, which is a **process-global singleton**: the first
  `import board` picks exactly one backend, selected by a `BLINKA_*` env var.
  Two USB-HID adapters therefore cannot both be "the board."
- **`extended`** → `ExtendedI2C(resolve_i2c_bus(num))`. A kernel i2c-dev bus
  (`/dev/i2c-N`, including USB-to-I2C bridges like a CP2112 that register as a
  kernel adapter). This path does **not** use `board`.

`resolve_i2c_bus` (int/numeric → `/dev/i2c-N`; otherwise adapter-name match via
`find_i2c_bus`) is currently duplicated in `probes/base.py` and
`grillplat/x86_numato.py`.

### Why two USB buses are possible

Each Blinka backend exposes its own low-level I2C class that bypasses `board`:

- **FT232H** — `adafruit_blinka.microcontroller.ftdi_mpsse.mpsse.i2c.I2C`. Each
  instance constructs its **own** `pyftdi.i2c.I2cController`, so multiple can
  coexist. The device is chosen by the `BLINKA_FT232H` env var: a value starting
  with `ftdi:` is used verbatim as the pyftdi URL, otherwise it defaults to the
  first FT232H (`ftdi://ftdi:ft232h/1`). Not a module singleton. `pyftdi` is a
  project dependency.
- **MCP2221** — `adafruit_blinka.microcontroller.mcp2221.i2c.I2C`. Uses a
  module-level singleton (`mcp2221 = MCP2221()`) that opens the **first** device
  by USB VID/PID `0x04D8/0x00DD` at import. Importing that module opens hardware
  immediately.

Neither backend touches `board`, so an FT232H bus and an MCP2221A bus coexist,
and neither collides with `basic` or `extended`.

Both backend classes expose `scan`, `writeto`, `readfrom_into`, and
`writeto_then_readfrom`, but **not** `try_lock`/`unlock`. Adafruit device
drivers (`adafruit_bus_device.I2CDevice`, used by the MCP9600, ADS1x15, VL53x,
and EMC2101 drivers) require `try_lock`/`unlock`. A thin lock-adding wrapper is
the key enabler.

### Compatibility rules

| Combination in one process | Works? |
| --- | --- |
| `ft232h` + `mcp2221a` | ✅ |
| `ft232h` + `extended` | ✅ |
| `mcp2221a` + `extended` | ✅ |
| `ft232h` + `mcp2221a` + `extended` | ✅ |
| `basic` + `extended` | ✅ |
| `basic` + `ft232h` or `mcp2221a` | ❌ |

The only unworkable case: `basic` cannot share a process with a USB-HID kind
(the `board` singleton). If a third onboard-style bus is needed alongside the
two USB ones, route it through `extended` (a Pi's onboard I2C is reachable as
extended bus `1`).

### Scope of the USB kinds

`ft232h`/`mcp2221a` only make sense for devices that open a `busio`-compatible
bus object:

- **Eligible:** `mcp9600_adafruit`, `ads1115_adafruit`, `ads1015_adafruit`
  (probes), the distance sensors (`distance/_tof_base.py`), and the EMC fan
  controller in `grillplat/x86_numato.py` and `grillplat/ft232h_relay.py`.
- **Not eligible:** `probes/ads1115.py` (the smbus2-based ADS1115) reaches
  `extended` through a kernel i2c-dev bus and cannot use a USB-HID backend. It
  keeps `basic`/`extended` only. `prototype` is a simulator and is out of scope.

## Architecture

### New shared module: `common/i2c_bus.py`

The single source of truth for opening any I2C bus. It absorbs `resolve_i2c_bus`
and `find_i2c_bus`; `probes/base.py` re-exports them so existing
`from probes.base import resolve_i2c_bus` imports keep working, and
`grillplat/x86_numato.py` imports from here instead of keeping its own copy.

```python
def open_i2c_bus(bus_kind='basic', bus_selector=None):
    """Return a busio.I2C-compatible bus for the given kind.

    bus_selector is the stored i2c_bus_num value:
      - extended : a /dev/i2c-N number or adapter-name match (resolve_i2c_bus)
      - ft232h   : a pyftdi URL (ftdi://...); blank -> first FT232H
      - mcp2221a : an MCP2221 serial; blank -> first MCP2221A
      - basic    : ignored

    Buses are cached per (kind, selector) for the life of the process so every
    device on one physical bus shares one handle and one lock. Raises
    I2CBusConfigError if this open would create an unworkable combination.
    """
```

Bus construction per kind:

- `basic` → `import board, busio; busio.I2C(board.SCL, board.SDA)` (returned
  as-is; already lockable).
- `extended` → `ExtendedI2C(resolve_i2c_bus(bus_selector))` (as-is).
- `ft232h` → **transiently** set `os.environ['BLINKA_FT232H']` to the selector
  (a `ftdi:` URL) or `'1'`, construct `ftdi_mpsse.mpsse.i2c.I2C()`, then restore
  the prior value (delete it if it was unset). The backend reads the var only
  during construction (`get_ft232h_url()` in `__init__`), so restoring
  immediately afterward is safe — and it means the factory never leaves a
  board-forcing var in the environment (see "Startup Blinka-environment guard").
  Wrap in `_LockedI2C`. Constructing this I2C also sets the process-global
  `Pin.mpsse_gpio` (see "FT232H single-controller sharing") — an intentional
  side effect that lets FT232H GPIO reuse this same controller. Because the
  factory opens the controller before any relay pin is created, `Pin.__init__`
  never hits its lazy fallback, so no `BLINKA_FT232H` is needed at GPIO time.
- `mcp2221a` → construct `mcp2221.i2c.I2C()` (blank selector = first device);
  a non-blank serial opens the matching HID device, wrap in `_LockedI2C`.

Backend imports are lazy (inside each branch) so importing `common.i2c_bus`
never requires Blinka/pyftdi/hid to be present (keeps tests and non-hardware
hosts importable).

### `_LockedI2C` wrapper

In `common/i2c_bus.py`. Adds `try_lock`/`unlock` (a `threading.RLock`) and
delegates `scan`, `writeto`, `readfrom_into`, `writeto_then_readfrom`, and
`deinit` to the backend. Only `ft232h`/`mcp2221a` buses are wrapped; `basic`
and `extended` already provide locking.

### Bus cache and process model

A module-level dict keyed by `(kind, str(selector))`, guarded by a lock. PiFire's
control process owns both the grill platform and the probe complex, so one cache
covers both; the two USB adapters are distinct devices, so there is no
cross-conflict. Sharing one handle per physical bus is mandatory for FT232H (a
single MPSSE engine) and correct for the rest.

### FT232H single-controller sharing (relays + I2C)

An FT232H exposes a **single USB interface**, and only one `pyftdi` controller
can own it at a time. I2C uses that interface in **MPSSE mode**; GPIO can use a
non-MPSSE bitbang controller, but bitbang and MPSSE are mutually exclusive modes
of the same interface, so once I2C is active the *only* way to drive the spare
pins (AD3–7, AC0–7; the I2C pins AD0–AD2 are dedicated to I2C) is
`I2cController.get_gpio()` — GPIO carried over the MPSSE controller. Blinka in
fact always builds an `I2cController` and calls `get_gpio()` for FT232H GPIO,
even relay-only. Hence: on one FT232H, relays (GPIO) and I2C must share one
controller.

Blinka shares it through a process-global class attribute `Pin.mpsse_gpio`
(`ftdi_mpsse/mpsse/pin.py`): constructing the MPSSE `I2C` sets
`Pin.mpsse_gpio = controller.get_gpio()`, and `digitalio` GPIO pins reuse
whatever `Pin.mpsse_gpio` holds — or, if it is still `None`, lazily create their
own controller. Whoever creates a controller first wins; a second controller on
the same device conflicts. `ft232h/pin.py` imports the same `Pin` class the
factory's MPSSE `I2C` touches, so the attribute is genuinely shared. (Relays on
a *separate* FT232H from any I2C device would not need to share — but that is not
the `ft232h_relay` topology.)

`grillplat/ft232h_relay.py` uses the FT232H for **both** the relays (GPIO) and
the EMC fan controller (I2C). Today it sets `BLINKA_FT232H` itself, creates the
relay GPIO pins first (controller #1), then builds the EMC bus via `busio.I2C`
(controller #2); a new `ft232h` probe bus would add a third. All must collapse
to one.

**Unification:** the factory is the single owner of each FT232H controller
(cached by selector). `ft232h_relay` is refactored to:

1. resolve its FT232H selector (`ft232h.url`, default `'1'`),
2. call `open_i2c_bus('ft232h', url)` **first** to construct the one MPSSE `I2C`
   (which sets `Pin.mpsse_gpio`) and cache it,
3. `import board, digitalio` and create the relay GPIO pins, which now reuse the
   factory's controller via `Pin.mpsse_gpio`,
4. use that same factory bus for the EMC controller.

`ft232h_relay` no longer sets `BLINKA_FT232H` directly — the factory owns the
env/URL for the `ft232h` kind. Because the controller is cached by selector, any
probe or distance sensor later placed on the same FT232H (same selector) reuses
the one controller, with no conflict. Since the control process builds the
platform before the probe complex, the platform establishes the shared
controller first.

**Caveat:** to share one FT232H between the relays and other I2C devices, the
relay's `ft232h.url` and those devices' `ft232h` selector must resolve to the
same adapter (blank/`'1'` = first FT232H on both). Different selectors mean
different adapters — and opening two controllers on one physical FT232H fails.

Moving the EMC in `ft232h_relay` onto a *different* bus (e.g. an MCP2221A while
relays stay on the FT232H) is a straightforward later extension (give its
`fan_controller` an `i2c_bus_kind`); this design keeps the `ft232h_relay` EMC on
the FT232H bus.

### Validation

A pure function in `common/i2c_bus.py`:

```python
USB_HID_KINDS = {'ft232h', 'mcp2221a'}

def validate_bus_kinds(kinds):
    """Raise I2CBusConfigError if the set of bus kinds cannot coexist."""
    kinds = {k for k in kinds if k}
    if 'basic' in kinds and (kinds & USB_HID_KINDS):
        raise I2CBusConfigError(
            "'basic' I2C can't share a process with a USB-HID bus "
            "(ft232h/mcp2221a): Blinka's board backend is process-global. "
            "Use 'extended' for the onboard bus (a Pi's onboard I2C is "
            "reachable as extended bus 1)."
        )
```

`I2CBusConfigError(ValueError)` is defined in the same module.

Enforced at two layers:

1. **Wizard save (proactive, user-facing).** When the wizard assembles/saves the
   config, it collects every configured `i2c_bus_kind` across the probe devices,
   the distance sensor, and the platform fan controller, calls
   `validate_bus_kinds`, and on failure surfaces the message through the
   wizard's existing alert mechanism and **blocks the save**. This is the
   "trying to make a bad config" error.
2. **Runtime backstop (factory).** `open_i2c_bus` tracks the kinds actually
   opened in the process and validates the prospective set on each open, raising
   the same `I2CBusConfigError`. This catches hand-edited `settings.json`,
   migrations, and any non-wizard path with the same clear message instead of
   silently opening the wrong bus.

One rule, one message, both places.

### Startup Blinka-environment guard

The `basic` kind relies on Blinka's `board`/`busio.I2C(board.SCL, board.SDA)`,
which picks a backend from `BLINKA_*` env vars at first `import board`. A
well-meaning operator could set e.g. `BLINKA_FT232H=1` or `BLINKA_MCP2221=1` in
the shell/systemd unit to force `basic` onto a USB adapter as a workaround —
which appears to work until some unrelated `import board` elsewhere resolves to
that adapter and breaks, subtly and far from the cause. The whole point of the
`ft232h`/`mcp2221a` kinds is to make that unnecessary, so the design forbids it.

`assert_clean_blinka_env()` in `common/i2c_bus.py` inspects `os.environ` for any
**board/chip-forcing** Blinka variable and raises `I2CBusConfigError` if one is
present, with a message pointing the user at the `ft232h`/`mcp2221a` bus kinds
instead. It is called once at control-process startup, **before** any bus is
opened. Because the factory only ever sets `BLINKA_FT232H` transiently (restored
immediately), the invariant "no board-forcing `BLINKA_*` var in `os.environ`"
holds for the entire process, so `import board` can never be silently hijacked.

Forbidden (exact match): `BLINKA_FT232H`, `BLINKA_FT2232H`, `BLINKA_FT4232H`,
`BLINKA_MCP2221`, `BLINKA_U2IF`, `BLINKA_GREATFET`, `BLINKA_NOVA`,
`BLINKA_SPIDRIVER`, `BLINKA_FORCECHIP`, `BLINKA_FORCEBOARD`. Forbidden (prefix):
`BLINKA_FTX232H_`. Explicitly allowed (tuning, not board-forcing):
`BLINKA_MCP2221_HID_DELAY`, `BLINKA_MCP2221_RESET_DELAY` — so the `BLINKA_MCP2221`
check must be exact, not a prefix.

## Call-site migration

Replace each inline `if basic/extended` block with a single
`open_i2c_bus(kind, selector)` call:

- Probes: `mcp9600_adafruit.py`, `ads1115_adafruit.py`, `ads1015_adafruit.py`.
  (`ads1115.py` smbus keeps its own basic/extended handling — not a `busio`
  device.)
- Distance: `distance/_tof_base.py` (`_open_i2c_bus`).
- Platform: `grillplat/x86_numato.py` (EMC controller construction) and
  `grillplat/ft232h_relay.py` (single-controller unification — open the FT232H
  bus via the factory first, then create relay GPIO pins that reuse it, and use
  the same bus for the EMC; drop its own `BLINKA_FT232H` handling per "FT232H
  single-controller sharing").

`grillplat/x86_numato.py` drops its local `resolve_i2c_bus`/`find_i2c_bus` and
imports from `common.i2c_bus`.

## Wizard / config changes

`wizard/wizard_manifest.json`:

- Add `ft232h` and `mcp2221a` to the `i2c_bus_kind` selectors for the eligible
  surfaces only:
  - Probe `device_specific.config` `list_values`/`list_labels` for
    `mcp9600_adafruit`, `ads1115_adafruit`, `ads1015_adafruit` (leave `ads1115`
    and `prototype` at `basic`/`extended`).
  - The distance `device_distance_i2c_bus_kind` settings-dependency options
    (all platform boards that expose it).
  - The platform fan-controller `i2c_bus_kind` settings-dependency options.
- Labels: `ft232h` → "FT232H (USB)", `mcp2221a` → "MCP2221A (USB)".
- Reuse the existing `i2c_bus_num` field on each surface as the optional
  selector (FTDI URL for `ft232h`, MCP2221 serial for `mcp2221a`; blank = first
  of that kind). Update its help text to say so.

No config key renames — existing `basic`/`extended` configs are untouched and
keep working.

## Testing

The FT232H/MCP2221 backends cannot open hardware on CI or the dev box, so tests
inject fakes at the backend-import boundary (the pattern the `ft232h_relay`
tests already use for `_load_ft232h`):

- Factory selects the correct backend per kind; passes the selector through
  (sets `BLINKA_FT232H` for `ft232h`; matches serial for `mcp2221a`).
- Buses are cached per `(kind, selector)` — a second request for the same bus
  returns the same object.
- `_LockedI2C.try_lock`/`unlock` behave (exclusive, reentrant, safe double
  unlock) and I/O methods delegate to the backend.
- `validate_bus_kinds` raises for `basic` + USB-HID and passes for every
  workable combination in the table.
- Wizard save rejects an unworkable combination with an alert and does not
  persist it.
- `ft232h_relay` opens its FT232H bus through the factory before creating relay
  GPIO pins, so relays + EMC (+ any FT232H probe) share one controller: assert
  the factory is called first and that no second controller is constructed
  (fakes count controller instantiations).
- The `ft232h` factory path leaves `os.environ` unchanged after construction:
  set a sentinel value (or leave it unset) beforehand and assert it is restored,
  proving the transient set/restore.
- `assert_clean_blinka_env()` raises for each forbidden var
  (`BLINKA_FT232H`, `BLINKA_MCP2221`, `BLINKA_FORCEBOARD`, a `BLINKA_FTX232H_0`
  prefix case, …) and passes when only the allowed tuning vars
  (`BLINKA_MCP2221_HID_DELAY`) or nothing are set.
- Manifest test: the eligible selectors include `ft232h` and `mcp2221a`; the
  ineligible ones (`ads1115`, `prototype`) do not.

## Backward compatibility

- `basic` and `extended` behavior is unchanged; existing configs need no
  migration.
- `resolve_i2c_bus`/`find_i2c_bus` remain importable from `probes.base`
  (re-export) and are now also in `common.i2c_bus`.
- No new required config keys.

## Out of scope

- Selecting among multiple adapters of the **same** kind beyond the provided
  selector (URL/serial); the common case is one of each.
- Non-`busio` devices using USB-HID buses (smbus2 ADS1115, prototype).
- Combining `basic` with a USB-HID kind (validated against, not supported).
- Moving the `ft232h_relay` EMC fan controller off the FT232H onto another bus
  (a later extension; the relays are inherently on the FT232H's GPIO).
- Sharing one FT232H across two different selectors (a physical FT232H hosts a
  single controller).
