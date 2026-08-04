# Manual Output Duty Status Design

**Date:** 2026-08-03

## Goal

Make every dashboard report Manual-mode auger and fan duty from the actuator state the controller actually applied. Manual auger ON reports 100% and OFF reports 0%. A Manual DC fan reports its actual hardware PWM percentage while powered and 0% while off; a relay fan reports 100% while on and 0% while off.

## Root Cause

`ControlMode._apply_manual_overrides()` directly changes actuator outputs. The status publisher later snapshots those outputs into `status_data["outpins"]`, but derives `cycle_ratio` from the automatic auger-cycle state and derives DC `fan_duty` from `control["duty_cycle"]`. Neither automatic-control value owns Manual-mode actuator state.

A reproduced DC-fan frame contained `outpins.fan=true` and `outpins.pwm=100` while publishing `fan_duty=0`. Manual auger has the equivalent mismatch because its automatic cycle ratio remains zero after the output is switched on.

## Design

Change `ControlMode._build_status_data()` in `controller/runtime/modes/base.py`. Continue taking exactly one `grill_platform.get_output_status()` snapshot per published frame.

When `self.name == Mode.MANUAL`:

- Set `cycle_ratio` to `1.0` when the snapshot's auger output is on, otherwise `0.0`.
- Set `fan_duty` to `0` when the snapshot's fan output is off.
- When a DC fan is on, set `fan_duty` to the snapshot's `pwm` percentage.
- When a relay fan is on, set `fan_duty` to `100`.

For every other mode, preserve the existing meanings: `cycle_ratio` remains the scheduled auger share, and DC `fan_duty` remains the automatic command value. This avoids changing presentation during automatic fan ramps.

## Consumers

No Socket.IO, React, or attached-display changes are required. Those layers already render `cycle_ratio` and `fan_duty`; correcting their shared source fixes all consumers consistently.

## Error Handling

Hardware platform implementations configured for DC fan support already include `pwm` in `get_output_status()`. Follow the existing status-building convention and safely default a missing or falsey PWM value to zero so a malformed platform snapshot cannot crash the control loop.

## Verification

Add characterization tests around the real Manual work cycle:

1. Auger ON publishes `cycle_ratio == 1.0`; auger OFF publishes `0.0`.
2. DC fan ON with a stale automatic `control["duty_cycle"]` publishes the actual hardware PWM value instead.
3. A manually selected DC-fan PWM percentage is reflected while the fan is on.
4. DC and relay fan OFF states publish zero.
5. Existing automatic-mode duty behavior remains unchanged.

Run the focused characterization tests, the full randomized Python suite, and a browser smoke scenario showing Manual auger/fan duty changes. Review the final revision before publication.

## Non-Goals

- Changing automatic-mode duty semantics.
- Changing actuator control behavior.
- Refactoring socket or dashboard presentation code.
- Adding new status fields or compatibility aliases.
