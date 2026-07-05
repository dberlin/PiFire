# Constant-velocity Kalman probe filter

## Problem

Probe temperatures on the dashboard/history appear to "jump" rather than move
smoothly. The root cause is the standard-deviation gate in the current smoothing
filter, `probes/temp_queue.py`.

`TempQueue` keeps a sliding window of the 10 most recent readings (~500 ms at the
50 ms control-loop rate) and averages them, but only when the window's standard
deviation is below `stdev_max` (4.75 °F / 2.25 °C). While the temperature is
genuinely ramping, the window's spread crosses that threshold, so the filter
*holds* `last_average` and stops updating — then snaps forward once the spread
settles. That hold-then-snap is the visible jump, and it also adds lag.

A simulation on a realistic profile (startup ramp at 1.5 °F/s, hold, lid-open
dip to 200 °F, recovery) with ±2 °F read noise measured:

| Filter | Steady-ramp lag | Lid-open recovery lag | Steady-hold noise |
|---|---|---|---|
| Current queue | ~608 ms | ~815 ms | ±0.67 °F |
| Kalman constant-position | ~302 ms | ~398 ms | ±0.58 °F |
| **Kalman constant-velocity** | **~51 ms** | **~122 ms** | **±0.31 °F** |
| (raw reads) | — | — | ±1.97 °F |

Corner overshoot (ramp flattening at setpoint) for constant-velocity was ~1.2 °F
— no worse than the current queue — at the tuning below.

## Decision

Replace `TempQueue` with a **constant-velocity (2-state) Kalman filter**. It
estimates both temperature and its rate of change, so it tracks ramps (startup,
lid-open recovery) with minimal lag while smoothing better than the current
filter, and it has no hold-then-snap behavior.

Decisions made during design:

- **Motion model:** constant-velocity (2-state `[temp, rate]`).
- **Outlier rejection:** innovation gate (reject reads > 5σ from the prediction)
  — preserves the spike protection that was the other half of the stdev filter's
  job, but smoothly.
- **Tuning config:** hardcoded module-level constants, matching the existing
  `stdev_max = 4.75` convention. No `settings.json` schema change, no migration.
- **Output precision:** return one decimal for both °F and °C (previously °F was
  truncated with `int()`, which itself caused 1° stepping).

## Design

### New module — `probes/kalman.py`

A single class `TempKalman` replacing `TempQueue`. State is 2-D: `[temperature,
rate]`.

```
predict:  x = F·x            F = [[1, dt],[0, 1]]
          P = F·P·Fᵀ + Q     Q = q·[[dt⁴/4, dt³/2],[dt³/2, dt²]]   (white-accel)
gate:     y = z − x[0];  S = P₀₀ + R
          if y²/S > GATE²:  reject sample, keep predicted state
update:   K = [P₀₀/S, P₁₀/S];  x += K·y;  P = (I − K·H)·P            H = [1, 0]
return round(x[0], 1)
```

Public surface (keeps `base.py` trivial):

- `TempKalman(units='F')`
- `update(reading) -> float | None` — performs predict + gate + update in one
  call and returns the filtered estimate (or `None`; see edge cases).
- `reset()` — clears state so the next valid reading re-initializes the filter.

### Integration — `probes/base.py`

- `_build_ports()` (~line 235): construct `TempKalman(units=self.units)` per
  port. Rename the holding dict `self.port_queues` → `self.port_filters` for
  honesty (it no longer holds queues).
- `read_all_ports()` (~lines 349–355): collapse the `enqueue`/`average` pair
  into a single call:
  ```python
  output_value = self.port_filters[port].update(port_values[port])
  ```
  `None` handling moves *into* the filter (see below), so the
  `if port_values[port] == None:` guard is removed.
- Delete `probes/temp_queue.py` and its import in `base.py`. `base.py` is its
  only consumer (verified by grep).

### Hardcoded tuning constants

Module-level in `probes/kalman.py`, selected by units at construction:

| Constant | °F | °C | Meaning |
|---|---|---|---|
| `R` (measurement variance) | 4.0 | 1.25 | sensor noise² (≈ ±2 °F / ±1.1 °C) |
| `q` (accel spectral density) | 0.5 | 0.15 | how fast the ramp rate may change |
| `GATE` | 5.0 | 5.0 | reject reads > 5σ from prediction |

These are the values the simulation used (~51 ms lag, ±0.31 °F noise, ~1.2 °F
corner overshoot). °C values are the °F values scaled by the °F→°C factor
(≈ 1/1.8 for R, ≈ (1/1.8)² for q).

### Edge cases

- **`dt` is measured, not assumed.** The control loop is not perfectly 50 ms
  (per-port ADC `time_delay` sleeps plus variable work), so the filter timestamps
  each update with `time.monotonic()` and uses the real elapsed `dt`, clamped to
  `[0.01, 1.0]` s. On the first update after construction or `reset()`, there is
  no prior timestamp — initialize state instead of predicting.
- **`None` reading (bad or disconnected probe):** `update(None)` returns `None`,
  preserving today's contract (downstream renders blank). A single `None` keeps
  state warm; after 3 consecutive `None`s the filter calls `reset()` so a
  reconnected probe re-initializes cleanly rather than snapping from a stale
  estimate and velocity.
- **Startup / initialization:** on the first valid reading, initialize
  `x = [z, 0]` with an inflated covariance `P` (e.g. `[[R, 0], [0, R]]`) so the
  velocity estimate adapts quickly, and return that reading immediately. This
  differs from `TempQueue`, which returned `0` for the first ~500 ms until its
  window filled. Returning the real value immediately is a minor, strictly-better
  behavior change (no half-second of "0").

### Downstream impact — one accepted behavior change

Returning float °F was verified safe across PID (`controllerCore.update`),
all threshold comparisons (`>=`/`<=`/`>`/`<`), history/current writes, MQTT,
InfluxDB, and the web API. The two safety-temp sites in `control.py` (~lines
293–296, 324) already wrap the value in `int()`, so they are unaffected.

The one exception is the notification `'equal'` condition at
`notify/notifications.py:715` (`return current == target`), which becomes
effectively unfireable once the value is a float. It is already unreliable with
noisy integer sensors, and the defaults use `equal_above` / `equal_below`.
Decision: **leave notifications untouched and document this.** An optional future
follow-up is a tolerant comparison (`abs(current - target) < 0.5`), out of scope
here.

Display truncation `str(temps[0])[:5]` now shows e.g. `"250.4"` instead of
`"250"` — cosmetic and acceptable.

## Testing — `tests/test_kalman.py`

pytest (tab-indented, matching repo convention), reusing the simulation harness:

1. Converges to a constant input (steady 250 → output → 250 within tolerance).
2. Tracks a linear ramp with steady-state lag below a bound.
3. Rejects a single 900° spike — output moves < 1°.
4. Reduces noise: output std < input std on a noisy constant.
5. `None` handling: returns `None`; resets after 3 consecutive `None`s; then
   re-initializes on the next valid reading.
6. °C path: uses scaled constants and returns one decimal.
7. Irregular `dt` (jittered timestamps) stays stable.

## Files

- New: `probes/kalman.py` — the filter.
- New: `tests/test_kalman.py` — unit tests.
- Edit: `probes/base.py` — construction + call site (~3 lines).
- Delete: `probes/temp_queue.py` — replaced.

## Out of scope

- Exposing tuning constants in `settings.json`.
- Any change to the notification `'equal'` comparison.
- Changing the 3-second history write interval or the display refresh cadence.
