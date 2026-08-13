# Task 12 Report: Control Trace Session Extraction

## Status

Implemented the clean-cutover `ControlTraceSession` collaborator and migrated Hold plus every direct private trace caller to its typed public surface. `HoldMode` retains hardware, runner dispatch, persistence, observation/evidence reconciliation, and lifecycle orchestration. The session owns only trace recorder access, identity, typed payload/envelope construction, pending model-event FIFO, warning/recovery state, model authority, update state, applied-interval state, flushing, rotation, and idempotent close.

No compatibility alias, forwarding helper, `Any`, `object`, untyped mutable payload dictionary, generic callback bag, hardware access, runner dispatch, persistence access, or duplicate wire schema was added to `controller/runtime/control_trace_session.py`.

## RED/GREEN evidence

The wished-for direct suite was written before the production module. The first command was:

```text
uv run pytest -q -n0 tests/unit/runtime/test_control_trace_session.py
```

The expected collection RED was observed:

```text
ImportError while importing test module tests/unit/runtime/test_control_trace_session.py
ModuleNotFoundError: No module named 'controller.runtime.control_trace_session'
1 error in 0.17s
exit 2
```

After implementation and coverage-driven edge-contract completion, the direct suite and strict branch gate are GREEN:

```text
uv run coverage run --branch --source=controller.runtime.control_trace_session \
  -m pytest -q -n0 tests/unit/runtime/test_control_trace_session.py
19 passed in 0.08s

uv run coverage json -o /tmp/task12-coverage.json
uv run python scripts/check_branch_coverage.py \
  --coverage /tmp/task12-coverage.json --minimum 90 \
  controller/runtime/control_trace_session.py
PASS controller/runtime/control_trace_session.py: 93.58974358974359% (73/78) > 90.0%
```

Direct contracts cover valid and invalid identity, stable UUID/cook/controller/generation binding, sorted recursive settings flattening, explicit model authority and fallback policy, invalid/bool/negative revision rejection, warning once and recovery, a warning callback that itself fails, pending FIFO failure/resume without loss or reorder, recorder record/flush/close failures, PID/PID-SP/MPC update payloads, allocation ordering, stale same-revision replay, zero/old/duplicate rejection, lifecycle FIFO, safety/model/frame/gap/calibration typed records, seed coalescing/promotion, measured and terminal applied intervals, periodic flush, full controller reconfiguration without pending loss, historical identity rotation without live update/applied/FIFO loss, repeated open, and close before/after open.

### Review fix round 1

Two reviewer findings were reproduced with wished-for tests before their fixes:

```text
uv run pytest -q -n0 --tb=short \
  tests/unit/runtime/test_control_trace_session.py::test_identity_rotation_preserves_live_update_applied_and_pending_state \
  tests/unit/runtime/test_hold_control_trace.py::test_historical_evidence_rotation_preserves_live_applied_interval \
  tests/unit/runtime/test_hold_orchestration.py::test_teardown_retry_completes_cleanup_after_pre_cleanup_failure
3 failed in 0.39s
```

The exact REDs were an absent `rotate_identity` API, a final applied interval lost after historical multi-generation evidence rotation, and a retry skipped after a pre-cleanup teardown exception. A distinct typed identity/model-authority rotation now preserves live update/applied/FIFO state, while full `rotate` remains the real controller-reconfiguration reset. Teardown marks itself complete only at the successful end of cleanup, so a pre-cleanup failure remains retryable while a completed cleanup remains idempotent.

The same three cases are GREEN:

```text
3 passed in 0.34s
```

## LSP migration inventory

Before production edits, LSP definition/reference queries mapped the existing trace schemas and recorder API, all production callers in `controller/runtime/modes/hold.py`, and direct test seams. After cutover:

- `ControlTraceSession` has 17 LSP references across Hold and direct tests.
- `ensure_open` has 17 LSP references covering runner-generation rotation, initial/session restoration paths, and direct contracts.
- LSP resolves Hold's `ControlTraceSession` construction to `controller/runtime/control_trace_session.py`.
- New production and direct-test files report `OK` diagnostics.
- The long-lived Hold/control-trace/calibration/refit/threaded files retain their pre-existing diagnostics. Changed Task 12 regions were mechanically filtered by line/range and session-symbol blocks; no Task 12-added error remains.

Every direct use of the deleted Hold private seams was migrated in:

- `tests/unit/runtime/test_hold_control_trace.py`
- `tests/unit/runtime/test_hold_calibration.py`
- `tests/unit/runtime/test_hold_controller_advisories.py`
- `tests/unit/runtime/test_hold_pulse_scheduler.py`
- `tests/unit/runtime/test_hold_refit_trigger.py`
- `tests/unit/runtime/test_threaded_runner.py`

A repository text residue scan across `controller` and `tests` found no attribute use of any deleted moved name.

## Deleted Hold ownership

The following fields/helpers are absent from `HoldMode` and have no compatibility seam:

- `_trace_recorder`
- `_trace_session_id`
- `_trace_cook_id`
- `_trace_warning_active`
- `_trace_pending_model_events`
- `_trace_closed`
- `_trace_session_model_snapshot`
- `_trace_session_model_provenance`
- `_trace_last_update_payload`
- `_trace_runner_snapshot_fallback_safe`
- `_trace_settings`
- `_trace_record`
- `_flush_pending_model_events`
- `_queue_model_event`
- `_ensure_trace_session`
- `_trace_safety`
- `_trace_model`
- `_trace_update`
- `_trace_complete_applied_interval`
- `_trace_terminal_framed_output`

The remaining `_trace_warning` method is the deliberately injected logging callback, not trace-session state or a forwarding trace operation.

## Ownership and ordering matrix

| Concern | Owner after Task 12 | Ordering/behavior preserved |
|---|---|---|
| Recorder construction | Hold setup | Existing constructor and best-effort failure warning |
| Recorder record/flush/close | ControlTraceSession | Exact `ControlTraceRecord` envelope and event timestamps; close once |
| Session/cook/controller/generation identity | ControlTraceSession | Session record succeeds before identity is returned/bound |
| Runner evidence bind/retire | Hold | Uses returned immutable identity; no runner access in session |
| Model authority/fallback | ControlTraceSession | Invalid revision clears authority; fallback explicitly safe only for synchronous windows |
| Pending model events | ControlTraceSession | FIFO; first failure stops flush; retry resumes once without loss/reorder |
| Update payload/replay state | ControlTraceSession | PID/PID-SP/MPC exact payloads; update then lifecycle then allocation ordering |
| Applied interval state | ControlTraceSession | Seed coalescing, boundaries, result/calibration identity, load/source/completeness preserved |
| Framed pulse scheduling/results | FramedPulseRuntime | Session consumes immutable completion/feedback only |
| `runner.set_output` | Hold | Session prepares typed `AppliedOutput`; Hold dispatches it |
| Hardware actuation | Hold | No grill dependency in session |
| Observation/evidence queues | Hold/runner | Session exposes identity and typed record calls only |
| Checkpoint/model persistence | Hold/workers | No SQL/store/worker access in session |
| Reconfigure lifecycle | Hold | Full session rotation resets live controller state after old evidence retirement; pending trace events survive |
| Historical evidence rotation | Hold/session | Identity/model authority rotate for reserved generations while live update/applied state and pending FIFO survive |
| Teardown lifecycle | Hold | Applied interval and FIFO complete, session closes before runner `finish_teardown`; completed teardown is idempotent and a pre-cleanup failure remains retryable |

## Focused verification

The brief names `tests/unit/runtime/test_control_trace_replay.py`, but the authoritative repository path is `tests/unit/controller/test_control_trace_replay.py`; the actual file was used.

```text
uv run pytest -q -n0 --tb=no \
  tests/unit/runtime/test_control_trace_session.py \
  tests/unit/runtime/test_hold_control_trace.py \
  tests/unit/controller/test_control_trace_replay.py \
  tests/unit/runtime/test_hold_pulse_scheduler.py \
  tests/unit/runtime/test_hold_applied_output.py \
  tests/unit/runtime/test_hold_orchestration.py \
  tests/unit/runtime/test_threaded_runner.py
8 failed, 220 passed in 5.97s
```

The eight failures are the exact later-task REDs listed below; the Task 12 direct/session/trace/replay/pulse/threaded cases pass.

Additional direct affected suites are GREEN:

```text
uv run pytest -q -n0 \
  tests/unit/runtime/test_hold_calibration.py \
  tests/unit/runtime/test_hold_controller_advisories.py \
  tests/unit/runtime/test_hold_refit_trigger.py
56 passed in 0.33s
```

Final migrated-seam smoke verification after diagnostic cleanup:

```text
uv run pytest -q -n0 \
  tests/unit/runtime/test_control_trace_session.py \
  tests/unit/runtime/test_hold_refit_trigger.py::test_online_adaptation_without_identification_checkpoints_before_trace_close \
  tests/unit/runtime/test_threaded_runner.py::test_hold_publishes_controller_evaluation_even_when_grey_observation_is_not_trace_valid
20 passed in 0.29s
```

Focused Ruff:

```text
uv run ruff check controller/runtime/control_trace_session.py \
  controller/runtime/modes/hold.py \
  tests/unit/runtime/test_control_trace_session.py \
  tests/unit/runtime/test_hold_calibration.py \
  tests/unit/runtime/test_hold_control_trace.py \
  tests/unit/runtime/test_hold_controller_advisories.py \
  tests/unit/runtime/test_hold_pulse_scheduler.py \
  tests/unit/runtime/test_hold_refit_trigger.py \
  tests/unit/runtime/test_threaded_runner.py
All checks passed!
```

## Full Hold aggregate and mechanical RED reconciliation

Command:

```text
uv run pytest -q -n0 --tb=no \
  tests/unit/runtime/test_hold_*.py tests/unit/runtime/test_threaded_runner.py
8 failed, 253 passed in 6.56s
```

A programmatic set difference compared the collected `FAILED ...` node IDs against Task 11's exact 15-ID baseline.

Added IDs:

```text
[]
```

Removed IDs:

1. `tests/unit/runtime/test_hold_orchestration.py::test_activation_lifecycle_evidence_keeps_fifo_ahead_of_checkpoint_and_trace_closure`
2. `tests/unit/runtime/test_hold_refit_trigger.py::test_teardown_emits_one_final_checkpoint_for_every_refit_outcome[False-None-None-disabled]`
3. `tests/unit/runtime/test_hold_refit_trigger.py::test_teardown_emits_one_final_checkpoint_for_every_refit_outcome[True-None-error5-failed]`
4. `tests/unit/runtime/test_hold_refit_trigger.py::test_teardown_emits_one_final_checkpoint_for_every_refit_outcome[True-result1-None-insufficient]`
5. `tests/unit/runtime/test_hold_refit_trigger.py::test_teardown_emits_one_final_checkpoint_for_every_refit_outcome[True-result2-None-rejected]`
6. `tests/unit/runtime/test_hold_refit_trigger.py::test_teardown_emits_one_final_checkpoint_for_every_refit_outcome[True-result3-None-ready-for-review]`
7. `tests/unit/runtime/test_hold_refit_trigger.py::test_teardown_emits_one_final_checkpoint_for_every_refit_outcome[True-result4-None-accepted-next-cook]`

Exact remaining later-task REDs:

1. `tests/unit/runtime/test_hold_applied_output.py::test_manual_release_reseeds_before_fresh_controller_authority`
2. `tests/unit/runtime/test_hold_orchestration.py::test_partial_setup_failures_still_close_the_runner_once`
3. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[checkpoint]`
4. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[persistence-flush]`
5. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[refit]`
6. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[runner-stop]`
7. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[success]`
8. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[trace-close]`

The activation trace-close-order RED and repeated final-refit idempotence REDs are closed by the session/lifecycle cutover. The manual-release, partial scheduler teardown, and broader teardown ordering matrix remain intentionally owned by later tasks. No assertion was weakened or deleted.

## Concerns

No Task 12 functional blocker remains. The full Hold aggregate is intentionally RED only for the exact eight later-task node IDs above. No project-wide build or test suite was run.
