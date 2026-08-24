# Final inference fix report

## Findings fixed

- `ControlMode._read_probes_with_excitation()` now publishes `probe_device_info` immediately after every fused probe read. Preflight, post-setup, and active-loop reads share the same publication path, so suspected and confirmed inferred health reaches diagnostics in the same tick.
- `ProbesMain.set_thermocouple_inference_policy("off")` now immediately rebuilds cached health and per-device projections from current hardware reports. Cached inferred reports become unmonitored, confirmed hardware remains authoritative, inference engines and stale queued transitions are cleared, and no later probe read is required.

## TDD evidence

- RED: the new active-cook publication cases failed because `probe_device_info` retained the seeded stale value; the no-follow-up-read policy case failed because the confirmed inferred report remained cached.
- GREEN: `python -m pytest -q tests/unit/probes/test_thermocouple_orchestration.py tests/unit/runtime/test_mode_settings_reload.py tests/unit/runtime/test_control_mode_base.py` passed with `79 passed in 3.05s`.
