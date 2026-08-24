# Final inference fix report

## Findings fixed

- `ControlMode._read_probes_with_excitation()` now publishes `probe_device_info` immediately after every fused probe read. Preflight, post-setup, and active-loop reads share the same publication path, so suspected and confirmed inferred health reaches diagnostics in the same tick.
- `ProbesMain.set_thermocouple_inference_policy("off")` now immediately rebuilds cached health and per-device projections from current hardware reports. Cached inferred reports become unmonitored, confirmed hardware remains authoritative, inference engines and stale queued transitions are cleared, and no later probe read is required.
- Inactive settings reloads now pass the controller clock into policy changes. Off-policy reprojection stamps cached fused reports at that controller epoch while leaving device-owned monotonic hardware latches untouched, so republished socket health starts current and its age advances normally instead of leaking process-local monotonic time.

## TDD evidence

- RED: the new active-cook publication cases failed because `probe_device_info` retained the seeded stale value; the no-follow-up-read policy case failed because the confirmed inferred report remained cached.
- GREEN: `python -m pytest -q tests/unit/probes/test_thermocouple_orchestration.py tests/unit/runtime/test_mode_settings_reload.py tests/unit/runtime/test_control_mode_base.py` passed with `79 passed in 3.05s`.
- RED: the inactive cached-hardware, active/stopped reload, and socket-age cases failed with a rejected `now` argument or a missing controller timestamp. GREEN: `python -m pytest -q tests/unit/probes/test_thermocouple_orchestration.py tests/unit/runtime/test_mode_settings_reload.py tests/web/test_socket_dash_payload_fields.py` passed with `52 passed in 4.01s`.

## Socket freshness clock-domain fix

- The web/mobile thermocouple-health projection now defaults to `time.monotonic()`, matching the clock used by `ProbesMain` for `observed_at`. The existing explicit `now=` injection remains unchanged for deterministic callers and tests.
- The regression test drives divergent monotonic and wall-clock values through the default production path. It proves a new report starts current, then becomes stale after the shared threshold while reporting the correct monotonic age.
- RED: `pytest -q tests/web/test_socket_dash_payload_fields.py -k 'uses_producer_monotonic_clock_and_ages_current_threshold'` failed with `1 failed in 2.62s`: expected `{"current": True, "lastReportedAgeS": 0.5}`, but the wall-clock default produced `{"current": False, "lastReportedAgeS": 1799990000.0}`.
- GREEN: the same focused regression passed with `1 passed in 2.57s`.
- Focused socket/app payload and shared projection verification: `python -m pytest -q tests/ui/test_qtbackend.py tests/ui/test_qtquick_probe_health.py tests/web/test_socket_dash_payload_fields.py tests/web/test_socketio_app_data.py tests/unit/probes/test_probe_health_aggregation.py` passed with `239 passed in 6.51s`.
