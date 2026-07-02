# EMC2301 tachometer / fan-speed (RPM) reading

## Overview

The PiFire-owned `EMC2301` driver (`grillplat/emc2301.py`) drives fan PWM and
reports the PWM duty, but does not read the fan's actual speed from the
controller's tachometer input. This change adds a read-only `fan_speed`
property that returns the measured fan speed in RPM, mirroring the Adafruit
`EMC2101` library's `fan_speed` property so both fan-controller drivers expose
the same interface.

## Goals

- Add a read-only `fan_speed` property to `EMC2301` returning fan speed in RPM
  as a `float`, matching the Adafruit `EMC2101.fan_speed` name and return type.
- Add a `poles` constructor argument (default `2`) so the driver measures a
  fan's tachometer correctly for 1–4 pole fans.
- Return `0.0` for a stopped/stalled fan (max tach count) and for a zero count,
  so a status poll never raises.

## Non-Goals

- No platform/UI wiring. Nothing in PiFire consumes fan RPM today; this change
  adds only the driver capability. Surfacing RPM through
  `grillplat/x86_numato.py`'s `get_output_status()`, status data, the display,
  or notifications is out of scope.
- No RPM-based closed-loop fan control (the `EN_ALGO` bit stays off; PiFire
  keeps driving duty directly). The tachometer is read-only here.
- No change to the EMC2101 path — the Adafruit library already exposes
  `fan_speed`.

## Background: EMC2301 tachometer

The EMC2301 continuously measures the fan tachometer on its dedicated TACH pin
whenever tach pulses arrive; the measurement runs in the direct-PWM ("fan
setting") mode PiFire already uses, with no separate enable bit. Two read-only
registers hold the result:

- **TACH Reading High Byte `0x3E`** — upper 8 bits of the count.
- **TACH Reading Low Byte `0x3F`** — lower 5 bits, left-justified in bits [7:3].

The stored value is a 13-bit count of 32.768 kHz (`f_TACH`) clock ticks spanning
a number of tach edges set by the EDGES field:

```
count = ((msb << 8) | lsb) >> 3
```

A stopped or stalled fan drives the count *near* its maximum but not reliably to
exactly `0x1FFF` — on real hardware a motionless fan was observed at `0x1FFE`.
Detecting a stopped fan by the count value is therefore fragile; see Error
handling for the correction (use the Fan Stall Status bit).

> **Correction (post-implementation):** this section originally stated the count
> reaches exactly `0x1FFF`, and the driver's first cut keyed off `count >=
> 0x1FFF`. Hardware testing showed a stopped fan reads `0x1FFE` (count 8190),
> one below the threshold, so the driver reported a phantom ~960 RPM (the RANGE
> floor). Fixed by keying off the Fan Stall Status register instead.

### RPM conversion

The Microchip EMC230x formula (DS20006532A / AN 17.4), confirmed against the
kiatAWDSA EMC2301 reference library:

```
RPM = ((n - 1) / poles) × m × f_TACH × 60 / count
```

where `f_TACH = 32768`, `m` is the RANGE multiplier, `poles` is the fan pole
count, and `n` is the number of edges measured (EDGES field). When EDGES is set
to match the pole count — `n = 2 × poles + 1` — the `(n - 1) / poles` term is
always `2`, so the formula collapses, for **every** supported pole count, to:

```
RPM = m × 3932160 / count          # 3932160 = 2 × f_TACH × 60
```

This is why `poles` does its real work at init (configuring EDGES) rather than
in the arithmetic.

### RANGE multiplier `m`

`m` comes from the RANGE bits [6:5] of the **Fan Configuration 1** register
`0x32`: `00→1, 01→2, 10→4, 11→8`. The driver never writes the RANGE bits, so
the chip keeps its power-on default `0x2B`, where RANGE = `01` → `m = 2`
(`RPM = 7864320 / count`). `fan_speed` reads `m` from `0x32` **on each call**
(no caching) so the result stays correct regardless of how RANGE is configured.

## Design

### Constructor change

`EMC2301.__init__(self, i2c_bus, address=_DEFAULT_ADDRESS, poles=2)`.

- Validate `poles` is one of `{1, 2, 3, 4}`; raise
  `ValueError('poles must be 1-4')` otherwise (EDGES only encodes 1–4 poles).
  This matches the driver's existing `manual_fan_speed` range-check style.
- Store `self.poles = poles`.
- At init, set the EDGES field (Fan Config 1 `0x32`, bits [4:3]) to
  `poles - 1` via a read-modify-write that preserves the RANGE bits and all
  other bits in the register:

  ```
  config1 = read(0x32)
  config1 = (config1 & ~0x18) | ((poles - 1) << 3)
  write(0x32, config1)
  ```

  This is additive to the existing `__init__` sequence (config `0x20`, PWM base
  `0x2D`, divide `0x31`, fan setting `0x30`); the new write can sit alongside
  them. The default `poles=2` writes EDGES = `01`, which equals the chip's
  power-on default — so existing callers (`grillplat/x86_numato.py` constructs
  `EMC2301(i2c, address=...)`) are behaviorally unchanged.

### `fan_speed` property

Read-only property returning `float` RPM:

```
@property
def fan_speed(self):
    msb = self._read_register(_REG_TACH_HIGH)   # 0x3E
    lsb = self._read_register(_REG_TACH_LOW)     # 0x3F
    count = ((msb << 8) | lsb) >> 3              # 13-bit count
    if count == 0 or count >= _TACH_STALL_COUNT:  # 0 guard / 0x1FFF stall
        return 0.0
    m = _RANGE_TO_MULTIPLIER[(self._read_register(_REG_FAN_CONFIG1) >> 5) & 0x03]
    return (m * _RPM_CONSTANT) / count           # _RPM_CONSTANT = 3932160
```

New module constants: `_REG_TACH_HIGH = 0x3E`, `_REG_TACH_LOW = 0x3F`,
`_REG_FAN_CONFIG1 = 0x32`, `_EDGES_MASK = 0x18`, `_TACH_STALL_COUNT = 0x1FFF`,
`_RPM_CONSTANT = 3932160`, and `_RANGE_TO_MULTIPLIER = {0: 1, 1: 2, 2: 4, 3: 8}`.

Rounding: return the raw quotient as a `float` (the Adafruit EMC2101 rounds to
2 dp via `round(...)`; matching that is fine but not required — pick `round(x,
2)` to stay consistent with the sibling driver).

## Error handling

- **Stopped/stalled fan** — return `0.0` when the Fan Stall Status register
  (`0x25`) bit 0 is set. This is the chip's authoritative signal and works in
  direct-PWM mode. (The original count-threshold approach was wrong; see the
  correction under "Background".)
- **Zero count** (guards divide-by-zero; not expected in normal operation):
  return `0.0`.
- I2C read failures propagate as they do for the existing register accessors —
  no new swallowing.

## Testing

Extend `tests/test_emc2301.py`, reusing its existing `FakeI2C` register-map
fake (seed registers, then read back):

- **Normal reading, default RANGE:** seed `0x32` with the power-on default
  `0x2B` (RANGE `01` → m=2) and a known tach count in `0x3E`/`0x3F`; assert
  `fan_speed == round(7864320 / count, 2)`.
- **Normal reading, RANGE m=1:** seed `0x32` with RANGE bits `00`; assert
  `fan_speed == round(3932160 / count, 2)` for the same count — proves `m` is
  read live from the register.
- **Stalled fan:** seed `0x3E`/`0x3F` encoding count `0x1FFF`; assert
  `fan_speed == 0.0`.
- **Zero count:** seed `0x3E`/`0x3F` = 0; assert `fan_speed == 0.0`.
- **EDGES set at init:** construct `EMC2301(..., poles=4)` and assert the
  Fan Config 1 register `0x32` has EDGES bits [4:3] == `poles - 1` (`0b11`)
  while the surrounding bits from the seed are preserved.
- **poles default unchanged:** construct with default `poles=2` and assert
  EDGES bits == `01` (equal to the power-on default, so existing behavior is
  preserved).
- **Invalid poles:** `EMC2301(..., poles=0)` and `poles=5` each raise
  `ValueError`.

Tach-count assembly note for tests: to seed a target 13-bit `count`, write
`msb = (count >> 5) & 0xFF` to `0x3E` and `lsb = (count << 3) & 0xF8` to `0x3F`,
the inverse of the driver's `((msb << 8) | lsb) >> 3`.
