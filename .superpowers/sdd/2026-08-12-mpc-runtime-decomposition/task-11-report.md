# Task 11 Report: Framed Pulse Runtime Extraction

## Status

Implemented the clean-cutover `FramedPulseRuntime` collaborator. `HoldMode` retains grill hardware commands plus trace/session, persistence, calibration-safety orchestration, and model lifecycle ownership. The runtime owns scheduler configuration and latching, pulse-local delivery accounting, frame completion/observation construction, applied feedback construction, calibration identity, and duplicate frame suppression.

No compatibility alias, forwarding helper, generic callback bag, `Any`, `object`, trace write, persistence write, or grill access was added to `controller/runtime/framed_pulse.py`.

## RED/GREEN evidence

The wished-for direct suite was created before the production module. The RED command was:

```text
uv run pytest -q -n0 tests/unit/runtime/test_framed_pulse_runtime.py
```

Expected RED was observed during collection because `controller.runtime.framed_pulse` did not exist (`ModuleNotFoundError`).

After implementation, the direct suite and strict branch gate are GREEN:

```text
uv run coverage run --branch --source=controller.runtime.framed_pulse \
  -m pytest -q -n0 tests/unit/runtime/test_framed_pulse_runtime.py
16 passed in 0.06s

uv run coverage json -o /tmp/task11-coverage.json
uv run python scripts/check_branch_coverage.py \
  --coverage /tmp/task11-coverage.json --minimum 90 \
  controller/runtime/framed_pulse.py
PASS controller/runtime/framed_pulse.py: 92.0% (46/50) > 90.0%
```

Direct contracts cover unconfigured/configured behavior, scheduler presence, latch/advance transitions, completed frames, delivery accounting, maximum duty and inverse realized load, calibration identity and cancellation, duplicate identity suppression, reset with and without terminal feedback, missing observations, and stale/manual/lid/safety source and disposition.

## LSP callsite and caller migration inventory

Before editing, LSP symbol/reference inventory confirmed that every production caller of the moved pulse helpers was inside `controller/runtime/modes/hold.py`; there was no external production caller. Definitions/references were also inspected for `PulseScheduler`, `PulseDecision`, `PulseFrameResult`, `ControllerState`, `ControllerRunner`, `ActuationMode`, and the direct private test callers.

Clean-cutover migrations:

- `HoldMode.setup`, runner reconfiguration, `on_tick`, manual/lid/safety inhibition, and teardown now compose or call `FramedPulseRuntime` directly.
- `test_hold_pulse_scheduler.py` direct scheduler/advance/reset/observation callers now use the runtime public surface and typed result values.
- `test_hold_control_trace.py` and `test_hold_calibration.py` direct completion callers now use runtime completion construction while Hold continues trace/evidence dispatch.
- `test_threaded_runner.py`'s focused `HoldMode.__new__` teardown fixture now initializes `_framed_pulse`, not the deleted `_pulse_scheduler` field.

The following moved private names/fields have no remaining definition or caller in Hold production/tests: `_configure_pulse_scheduler`, `_pulse_scheduler`, `_latch_pulse_frame`, `_advance_framed_pulse`, `_reset_framed_pulse`, `_record_pulse_delivery`, `_report_framed_feedback`, `_build_completed_pulse_observation`, `_record_terminal_framed_output`, `_observe_completed_pulse_frame`, `_pulse_observation_last_frame_key`, and `_pulse_observation_sequence`. The only text match is the descriptive test name `test_every_production_controller_builds_one_pulse_scheduler_and_starts_off`.

## Ownership and behavioral preservation matrix

| Behavior | Owner after Task 11 | Preservation evidence |
|---|---|---|
| scheduler create/present/absent semantics | `FramedPulseRuntime` | direct unconfigured/configured tests; controller matrix integration |
| request, maximum duty, fan and calibration latch | `FramedPulseRuntime` | direct latch/completion identity tests; migrated calibration suite |
| bounded scheduler advance/reset and delivered-on delta | `FramedPulseRuntime` | direct advance/reset tests; pulse scheduler integration |
| physical auger transition/off | `HoldMode` | Task 10 frame-boundary and three inhibit-order contracts GREEN |
| completed observation construction and duplicate key/sequence | `FramedPulseRuntime` | direct duplicate/missing/observation tests; migrated Hold callers |
| terminal/progress `AppliedOutput` and inverse load | `FramedPulseRuntime` | direct feedback/completion tests; applied-output/trace integration |
| runner feedback/observation dispatch | `HoldMode` | frame boundary retains hardware, terminal feedback, observation order |
| trace/session reconciliation and seed interval cutover | `HoldMode` | normal tick, contiguous frame coverage, manual and stale replay contracts GREEN |
| trace/persistence/model/calibration lifecycle | `HoldMode` and existing collaborators | not extracted; later-task RED inventory unchanged |

The integration order is: runtime decides; Hold commands the grill; Hold dispatches terminal applied feedback and observation; Hold records safety/frame/feedback trace events in replay-valid order. Reset trace coverage distinguishes learner-discarded terminal feedback from complete physical applied-output evidence without changing the typed feedback disposition.

## Focused verification

Required focused command:

```text
uv run pytest -q -n0 \
  tests/unit/runtime/test_framed_pulse_runtime.py \
  tests/unit/runtime/test_hold_pulse_scheduler.py \
  tests/unit/runtime/test_hold_applied_output.py \
  tests/unit/runtime/test_hold_control_trace.py \
  tests/unit/runtime/test_hold_calibration.py \
  tests/unit/runtime/test_hold_orchestration.py
9 failed, 133 passed in 3.15s
```

All nine failures are intentional later-task contracts: manual-release reseed (1), activation close ordering (1), teardown success/failure matrix (6), and partial-setup repeated teardown (1). No Task 11 pulse-owned failure remains.

Focused Ruff:

```text
uv run ruff check \
  controller/runtime/framed_pulse.py \
  controller/runtime/modes/hold.py \
  tests/unit/runtime/test_framed_pulse_runtime.py \
  tests/unit/runtime/test_hold_pulse_scheduler.py \
  tests/unit/runtime/test_hold_control_trace.py \
  tests/unit/runtime/test_hold_calibration.py \
  tests/unit/runtime/test_threaded_runner.py
All checks passed!
```

LSP diagnostics are clean for both new files. Migrated `test_hold_pulse_scheduler.py` has no errors. The long-lived Hold, control-trace, calibration, and threaded suites retain their pre-existing diagnostics; Task 11 introduced no unresolved diagnostic. The new runtime initially exposed unused-local/unreachable hints and its test exposed one optional dataclass error; all were corrected before this report.

## Full Hold aggregate and exact remaining inventory

Baseline:

```text
uv run pytest -q -n0 tests/unit/runtime/test_hold_*.py tests/unit/runtime/test_threaded_runner.py
257 collected; 21 failed, 236 passed in 6.49s
```

Task 11 result:

```text
uv run pytest -q -n0 tests/unit/runtime/test_hold_*.py tests/unit/runtime/test_threaded_runner.py
15 failed, 242 passed in 6.61s
```

Exact remaining intentional REDs:

1. `tests/unit/runtime/test_hold_applied_output.py::test_manual_release_reseeds_before_fresh_controller_authority`
2. `tests/unit/runtime/test_hold_orchestration.py::test_activation_lifecycle_evidence_keeps_fifo_ahead_of_checkpoint_and_trace_closure`
3. `tests/unit/runtime/test_hold_orchestration.py::test_partial_setup_failures_still_close_the_runner_once`
4. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[checkpoint]`
5. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[persistence-flush]`
6. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[refit]`
7. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[runner-stop]`
8. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[success]`
9. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[trace-close]`
10. `tests/unit/runtime/test_hold_refit_trigger.py::test_teardown_emits_one_final_checkpoint_for_every_refit_outcome[False-None-None-disabled]`
11. `tests/unit/runtime/test_hold_refit_trigger.py::test_teardown_emits_one_final_checkpoint_for_every_refit_outcome[True-None-error5-failed]`
12. `tests/unit/runtime/test_hold_refit_trigger.py::test_teardown_emits_one_final_checkpoint_for_every_refit_outcome[True-result1-None-insufficient]`
13. `tests/unit/runtime/test_hold_refit_trigger.py::test_teardown_emits_one_final_checkpoint_for_every_refit_outcome[True-result2-None-rejected]`
14. `tests/unit/runtime/test_hold_refit_trigger.py::test_teardown_emits_one_final_checkpoint_for_every_refit_outcome[True-result3-None-ready-for-review]`
15. `tests/unit/runtime/test_hold_refit_trigger.py::test_teardown_emits_one_final_checkpoint_for_every_refit_outcome[True-result4-None-accepted-next-cook]`

Relative to Task 10, the six pulse-owned REDs are closed: normal tick order (1), frame-boundary order (1), framed reset metadata (1), and lid/safety/stale inhibit order/terminalization (3). The remaining 15 are the exact later-task categories and form a strict subset of Task 10's 21.

## Concerns

No functional blocker remains for Task 11. The aggregate remains intentionally RED for Tasks 12-18; those assertions were neither weakened nor deleted. No project-wide suite or build was run.
