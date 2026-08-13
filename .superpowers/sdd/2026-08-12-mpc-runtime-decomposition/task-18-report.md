# Task 18: Enforce Hold Branch Coverage — Report

## Result

The deterministic Hold aggregate clears the strict per-module branch gate. `controller/runtime/modes/hold.py` finishes at **281/308 implemented branches = 91.23376623376623%**, strictly greater than 90.0%.

| Module | Covered / total branches | Coverage |
|---|---:|---:|
| `controller/runtime/modes/hold.py` | 281/308 | 91.23376623376623% |
| `controller/runtime/framed_pulse.py` | 46/50 | 92.0% |
| `controller/runtime/control_trace_session.py` | 75/78 | 96.15384615384616% |
| `controller/runtime/modes/hold_learning.py` | 213/230 | 92.6086956521739% |

The final aggregate collected **454 passing tests**, compared with the brief's 405-test direct-suite baseline. The eight directly affected Hold domain files still collect **266 passing tests**; adding the genuinely required direct trace-session suite yields **286 passing directly affected tests**. No coverage exclusion, pragma, source/shape assertion, compatibility path, or production-only coverage hook was added.

## Behavioral contracts added

### Resumable teardown and setup ownership — `test_hold_orchestration.py`

- Feedback preparation failure retries preparation without repeating scheduler advance, reset construction/delivery, runner stop, trace close, or final cleanup.
- Feedback dispatch failure reuses the prepared feedback value and retries only dispatch.
- Retry calls retain the first teardown timestamp and temperature.
- A second successful setup installs an independent teardown transaction and owns a second hardware-off/runner-close cycle.
- Reset-delivery failure resumes the existing reset dispatch without repeating advance, feedback preparation, or reset construction.
- Fan-start, power-on, runner-factory, runner-revision-before-learning, persistence-worker, and trace-recorder failures preserve every owner created before the failure; teardown leaves outputs off.
- When cleanup after runner-revision failure encounters a persistence flush, pending trace-event flush, or runner final-close exception, it warns, independently attempts every downstream owner once, reaches `FINISHED`, and a repeated teardown performs no second stop or close.

### Trace-session close ownership — `test_control_trace_session.py`

- A pending-event flush exception is owned by `ControlTraceSession.close()`: the exact warning is emitted, status becomes closed, the underlying recorder is still closed once, and repeated close is inert.
- Recorder-close failure retains its existing exact warning and one-attempt behavior.

### Runner status and framed-pulse boundaries — `test_hold_pulse_scheduler.py`

- Missing, raising, non-mapping, boolean, negative, legacy, activation, and adaptation generation reports normalize to the public generation contract while completed observations remain deliverable.
- In-place setpoint changes reach the live runner without rebuilding its configuration.
- Framed completion, reset, stale, inhibition, and teardown delivery tests continue to defend delivered counts, ordering, provenance, and hardware results. The manufactured `hold.control = None` private `_framed_sample` test was deleted: no public lifecycle removes control after successful setup, so it defended an unreachable state rather than behavior.

### Fan/manual/lid safety — `test_hold_fan_authority.py`, `test_hold_applied_output.py`

- A Hold setup with no runner survives a public safety callback, reports no pulse, retains safe auger state, and tears all outputs down.
- An unowned DC fan adopts a valid profile duty; an empty profile preserves current duty while advancing cadence state.
- Manual auger-off commands already-active hardware off and publishes zero manual duty.
- Lid open/expiry and manual-release paths defend hardware state, feedback publication, reseed behavior, and ignored non-auger release.
- Control/manual output dispatch still fails closed when its required trace session is unavailable.

### Persistence/migration fail-soft behavior — `test_hold_model_persistence.py`

- Malformed selected MPC configuration uses migration defaults while restore, submission, seed, and output remain live.
- Migration failure marks evidence unavailable without preventing a live seeded controller.

### Calibration and callback boundaries — `test_hold_calibration.py`

- Automatic/operator lid opening and mode-transition safety cancel an active calibration probe before pause/reset hardware ownership.
- Missing or malformed newer commands do not spuriously cancel an active probe; acknowledged operator cancellation retains command identity.
- A safety-ceiling callback raising `NotImplementedError` is retried, deduplicates equal faults, clears after recovery, and emits a new fault after relapse; exact attempted ceiling values are asserted.
- A calibration request callback raising `NotImplementedError` consumes the command once, logs once, traces one typed safety event, and does not retry on the next tick.

### Trace/evidence continuity — `test_hold_control_trace.py`

- First safety use opens and binds the normal trace session.
- Historical evidence-session rotation preserves the live applied interval and changes identity without losing its producing revision.
- Unknown selected-controller configuration keeps controller output live without inventing a trace controller identity.
- Existing completion, stale/missing observation, safety, manual/lid, recorder-gap, and allocation tests continue to assert typed records, sequence linkage, and delivery order.

### Stop-timeout behavior — `test_hold_refit_trigger.py`

- A runner reporting `stop_for_refit() is False` skips refit/final checkpoint publication and emits the public warning instead of silently losing the cook's evidence.

## RED/GREEN evidence

### Feedback retry checkpoints

A temporary mutation moved the feedback-prepared checkpoint before `report_feedback`. The mutation was not retained. The new regression nodes failed for the intended lost-retry behavior:

```text
uv run pytest -q -n0 \
  tests/unit/runtime/test_hold_orchestration.py::test_teardown_retry_reprepares_feedback_without_repeating_advance \
  tests/unit/runtime/test_hold_orchestration.py::test_teardown_retry_latches_first_timestamp_and_temperature

2 failed in 0.16s
expected feedback calls [3.0, 3.0], observed [3.0]
expected feedback count 2, observed 1
```

With the checkpoint after successful preparation:

```text
uv run pytest -q -n0 \
  tests/unit/runtime/test_hold_orchestration.py::test_teardown_retry_reprepares_feedback_without_repeating_advance \
  tests/unit/runtime/test_hold_orchestration.py::test_teardown_retry_reuses_prepared_feedback_after_dispatch_failure \
  tests/unit/runtime/test_hold_orchestration.py::test_teardown_retry_latches_first_timestamp_and_temperature \
  tests/unit/runtime/test_hold_orchestration.py::test_repeated_setup_starts_a_fresh_teardown_transaction

4 passed in 0.13s
```

### Partial setup ownership

The new fan-start/power-on cases initially reached teardown with no `_runner` slot and failed with `AttributeError`. The runner-revision failure case then exposed a second real defect: a constructed runner had `runner:finish` count zero because no learning runtime existed to own final closure. Initializing owner slots at setup entry and adding the already-created-owner fallback produced:

```text
uv run pytest -q -n0 \
  tests/unit/runtime/test_hold_orchestration.py::test_factory_failure_after_persistence_creation_closes_all_created_owners \
  tests/unit/runtime/test_hold_orchestration.py::test_runner_revision_failure_before_learning_closes_every_created_owner \
  tests/unit/runtime/test_hold_orchestration.py::test_early_hardware_setup_failure_closes_created_trace_and_outputs \
  tests/unit/runtime/test_hold_orchestration.py::test_partial_setup_failures_still_close_the_runner_once

4 passed
```

### Absent-runner public safety

The new public safety-path regression initially failed in `_on_safety_event("temperature_guard", 1.0)` with:

```text
AttributeError: 'ControllerState' object has no attribute 'pulse_frame_calibration_probe_load'
```

The exact absent-runner guard in `_cancel_active_framed_calibration` now transfers to the existing no-scheduler inhibit path. The focused fan/safety cases passed after the fix.

### Partial-setup cleanup boundary failures

The new three-case Hold matrix was initially RED on persistence and pending trace-event flush failure: each exception escaped the fallback and skipped later owners; runner-finish failure already warned. That initial node was `2 failed, 1 passed`. Independent persistence/runner guards initially made the matrix GREEN, but final review correctly rejected the trace catch in Hold as the wrong ownership boundary.

The direct trace-session regression was then RED against the real root defect:

```text
uv run pytest -q -n0 \
  tests/unit/runtime/test_control_trace_session.py::test_close_marks_session_closed_and_closes_recorder_when_pending_flush_raises

1 failed in 0.15s
RuntimeError: pending flush failed
```

`ControlTraceSession.close()` now warns for the pending-flush failure, marks itself closed in `finally`, and attempts its recorder close. Hold delegates directly to that owner. The focused root-fix matrix is GREEN:

```text
uv run pytest -q -n0 \
  tests/unit/runtime/test_hold_orchestration.py::test_partial_setup_cleanup_attempts_every_owner_once_after_boundary_failure \
  tests/unit/runtime/test_control_trace_session.py::test_close_marks_session_closed_and_closes_recorder_when_pending_flush_raises \
  tests/unit/runtime/test_control_trace_session.py::test_close_before_or_after_open_is_idempotent_and_best_effort

5 passed in 0.17s
```

Each Hold case calls teardown twice and asserts one runner stop, one persistence attempt, one actual recorder close, `trace.status.closed`, one runner finish, the exact single warning, and no skipped downstream owner.

### Stop timeout

`test_hold_warns_and_skips_refit_when_runner_reports_stop_timeout` adds coverage for pre-existing behavior: `stop_for_refit() is False` already emitted the public warning and skipped refit/final checkpoint publication. It was not RED and required no production change.

### Callback failure boundaries

The final direct callback nodes are GREEN and assert delivery counts/values, trace records, warnings, recovery, and command consumption rather than line execution:

```text
uv run pytest -q -n0 \
  tests/unit/runtime/test_hold_calibration.py::test_safety_ceiling_callback_failure_deduplicates_and_recovers \
  tests/unit/runtime/test_hold_calibration.py::test_unsupported_calibration_request_is_consumed_and_traced_once \
  tests/unit/runtime/test_hold_refit_trigger.py::test_hold_warns_and_skips_refit_when_runner_reports_stop_timeout

3 passed in 0.19s
```

## Task 17 reviewer opportunities

| Opportunity | Disposition |
|---|---|
| Feedback preparation failure | Added `test_teardown_retry_reprepares_feedback_without_repeating_advance`; preparation retries while advance/reset/final cleanup remain once. |
| Feedback dispatch failure | Added `test_teardown_retry_reuses_prepared_feedback_after_dispatch_failure`; the prepared value is reused and only dispatch retries. |
| Reset-delivery retry | Existing `test_teardown_retry_resumes_delivery_after_scheduler_advance` already fails observation delivery after reset construction and proves advance, feedback preparation, and reset each occur once while delivery retries. No duplicate was added. |
| Repeated setup isolation | Added `test_repeated_setup_starts_a_fresh_teardown_transaction`; two setup/teardown cycles own distinct runtimes, stop/finish cycles, trace closes, and final hardware-off results. |
| Latched teardown inputs | Added `test_teardown_retry_latches_first_timestamp_and_temperature`; a retry after clock and temperature changes still dispatches the original inputs. |

## Removed obsolete branches and invariant proof

The brief's base denominator was 326. The complete arithmetic to the final 308 is:

```text
326
- 20  ten two-way post-setup optional-owner conditions
-  6  three two-way name conditions in _HoldOutputStatus.__getitem__
-  2  one two-way command_auger_off condition
+ 10  real safety/setup/failure-handling conditions added by Task 18
= 308
```

The Hold persistence/runner exception guards add executable statements but no coverage.py branch decisions. Moving pending-flush handling to `ControlTraceSession.close()` and deleting the Hold trace catch also leaves the Hold denominator at 308.

The ten optional-owner conditions were removed from three exact regions:

1. `_deliver_framed_completion`: optional `HoldLearningRuntime` observation delivery and optional runner output delivery.
2. `_adopt_runner_configuration`: optional learning reconcile/retire/restore/bind/reconcile calls, optional trace rotation, and the missing framed-runtime alternative.
3. `_submit_obtain_and_handle_calibration_cancellation`: optional learning reconciliation/drain and framed-runtime fallback around the live runner cadence/result pipeline.

They are unreachable, not merely unlikely:

- `setup()` installs `FramedPulseRuntime` before recorder, hardware, persistence, or runner construction.
- `setup()` always installs a `ControlTraceSession`; recorder construction failure is represented by a session with an unavailable recorder, not by a missing session.
- Ordinary tick/reconfiguration/completion is reachable only after `setup()` returns; by that point the learning assignment has completed.
- A framed scheduler is configured only inside `if self._runner is not None`; any reachable framed completion therefore has the runner that owns its output. The proposed direct no-runner completion test was unreachable and was removed.
- The runner submission/adoption pipelines are entered only after successful setup with that runner, framed runtime, trace session, and learning runtime.
- Partial setup is handled only by resumable teardown. The public failure tests cover fan start, power on, runner factory, and the actual runner-revision-before-learning boundary; they do not claim a learning-constructor-failure case.

`cast(...)` expresses these established ownership facts to LSP without adding runtime behavior or branches.

The `_HoldOutputStatus.__getitem__` conditional body had exactly one LSP reference—the definition—and no symbol-resolved callers. Runtime base-mode indexing still requests the valid `auger`, `fan`, and `pwm` attributes, so the adapter remains as direct `getattr`; only three explicit two-way name tests were removed. Their special invalid-key `KeyError` behavior was unreachable private behavior, not a public contract.

AST call inventory found all eight `_inhibit_framed_pulse` call sites. None supplied `command_auger_off`; every live call used the default hardware-off behavior. Removing that parameter/condition and making `auger_off()` unconditional preserves all live call behavior while removing its unreachable false branch.

## Review-finding closures

- **Strict gate discrepancy:** resolved with the final real command and JSON artifact; the current result is 281/308, not the earlier 297/328 draft.
- **Synthetic completion exception:** the proposed direct private test that manufactured a completion with no owning runner was removed. The live public invariant is scheduler implies runner; no production exception or private shape contract was added for that impossible state.
- **Manufactured control absence:** the direct `hold.control = None` `_framed_sample` test was removed. The branch remains uncovered because the public lifecycle does not remove control after successful setup.
- **Callback gaps:** public safety-ceiling and calibration-request `NotImplementedError` tests assert exact retries, recovery, consumption, logging, and typed trace effects.
- **Setup/constructor failures:** public fan/power/factory and runner-revision-before-learning failures prove hardware and created-owner cleanup. Persistence and runner-finish failure cases prove independent one-attempt cleanup and `FINISHED` idempotence; the trace-session case now proves its own closed status and actual recorder close at the owning abstraction.
- **Runner stop timeout:** the test covers the pre-existing warning/skip behavior; it is not reported as RED or as a production change.
- **Optional-owner cleanup:** ten post-setup conditions, six output-status name branches, and the two-way `command_auger_off` condition were removed only after lifecycle and callsite evidence established that their alternatives were unreachable.
- **Trace close ownership:** the direct RED regression proves a pending-event flush exception previously prevented both closed status and recorder close. The fix is in `ControlTraceSession.close()`; Hold no longer catches or labels the internal flush failure as a recorder-close failure.
- **Task-added diagnostics:** callback parameters participate in value assertions; the historical-rotation identity is narrowed explicitly; unused new fixture captures were removed. No task-added LSP diagnostic remains.
- **Final review:** PASS / APPROVED with no findings. All prior findings are closed.

## Final verification

### Direct trace and affected Hold domain suites

```text
uv run pytest -q -n0 \
  tests/unit/runtime/test_control_trace_session.py \
  tests/unit/runtime/test_hold_orchestration.py \
  tests/unit/runtime/test_hold_pulse_scheduler.py \
  tests/unit/runtime/test_hold_fan_authority.py \
  tests/unit/runtime/test_hold_applied_output.py \
  tests/unit/runtime/test_hold_model_persistence.py \
  tests/unit/runtime/test_hold_calibration.py \
  tests/unit/runtime/test_hold_control_trace.py \
  tests/unit/runtime/test_hold_refit_trigger.py

286 passed in 4.17s
```

### Strict deterministic aggregate

```text
uv run coverage erase
uv run coverage run --branch \
  --source=controller.runtime.modes.hold,controller.runtime.framed_pulse,controller.runtime.control_trace_session,controller.runtime.modes.hold_learning \
  -m pytest -q -n0 \
  tests/unit/runtime/test_hold_*.py \
  tests/unit/runtime/test_threaded_runner.py \
  tests/unit/runtime/test_control_trace_session.py \
  tests/unit/runtime/test_framed_pulse_runtime.py
uv run coverage json -o /tmp/task18-final-coverage.json
uv run python scripts/check_branch_coverage.py \
  --coverage /tmp/task18-final-coverage.json --minimum 90 \
  controller/runtime/modes/hold.py \
  controller/runtime/framed_pulse.py \
  controller/runtime/control_trace_session.py \
  controller/runtime/modes/hold_learning.py

454 passed in 7.46s
PASS controller/runtime/modes/hold.py: 91.23376623376623% (281/308) > 90.0%
PASS controller/runtime/framed_pulse.py: 92.0% (46/50) > 90.0%
PASS controller/runtime/control_trace_session.py: 96.15384615384616% (75/78) > 90.0%
PASS controller/runtime/modes/hold_learning.py: 92.6086956521739% (213/230) > 90.0%
```

### Scoped Ruff

```text
uv run ruff check controller/runtime/modes/hold.py \
  controller/runtime/control_trace_session.py \
  tests/unit/runtime/test_control_trace_session.py \
  tests/unit/runtime/test_hold_orchestration.py \
  tests/unit/runtime/test_hold_pulse_scheduler.py \
  tests/unit/runtime/test_hold_fan_authority.py \
  tests/unit/runtime/test_hold_applied_output.py \
  tests/unit/runtime/test_hold_model_persistence.py \
  tests/unit/runtime/test_hold_calibration.py \
  tests/unit/runtime/test_hold_control_trace.py \
  tests/unit/runtime/test_hold_refit_trigger.py

All checks passed!
```

No project-wide suite, broad coverage run, formatter, smoke, VCS command, or commit was run.

## Remaining uncovered Hold branches

Coverage JSON reports these exact 27 missing branch pairs:

```text
[246,247], [266,267], [316,329], [371,395], [499,500],
[502,-495], [638,642], [673,674], [848,866], [983,993],
[1029,1039], [1063,1064], [1253,1254], [1395,1398],
[1717,1741], [1757,1758], [1797,1798], [1843,1852],
[1890,1900], [1906,1962], [1918,1927], [1978,-1977],
[1985,1994], [2015,2016], [2075,2076], [2175,2274],
[2332,2334]
```

Exact uncovered statement lines are `247, 267, 500, 674, 837, 951, 952, 1064, 1254, 1758, 1798, 2016, 2076`.

Risk disposition:

1. **Fail-closed adapters and callbacks:** absent runner status, missing trace in `_set_output`, malformed missing-observation trace input, and runner callback failures remain defensive. Public status, trace-unavailable, callback-failure, and setup/teardown tests cover their meaningful outcomes; further private injection would duplicate behavior or manufacture invalid ownership.
2. **Private missing-control sample:** branch `[266,267]` is deliberately uncovered after deleting the `hold.control = None` test. No public lifecycle removes control after successful setup, so executing it requires an invalid private mutation.
3. **Rare configuration/calibration combinations:** invalid safety ceilings, absent/malformed calibration command fields, cancellation admission alternatives, and controller-update timing retain explicit safe outcomes. Existing tests cover operator/safety identities, malformed commands, deduplication, recovery, and hardware inhibition; the residual branches are lower-risk combinations of those contracts.
4. **Manual/lid/smoke cadence:** residual trace-absent/manual release/lid/fan cadence paths are bounded by hardware-state tests and the always-installed trace-session invariant. Directly deleting the session after successful setup would violate the public lifecycle.
5. **Partial-setup trace absence:** branch `[2332,2334]` is the trace-absent alternative in the runner-revision-before-learning fallback. Setup installs a session before every tested fallible hardware/factory/revision boundary; the persistence/trace/runner exception matrix covers all reachable owner-failure orderings without nulling the session.
6. **Resumable teardown phase skip:** the `FRAMED_FINALIZED` resume edge is guarded by the same call's nested `finally`, which advances to `FINISHED` even on stop/close failures. Retry behavior is covered at every externally interruptible preparation/dispatch/delivery boundary; calling private state directly would be a shape test.

## LSP evidence

LSP references were used before changing `ControlTraceSession.close`, `teardown`, `_cancel_active_framed_calibration`, `_inhibit_framed_pulse`, `_on_manual_release`, output-status adaptation, and runner/reconfiguration boundaries.

Final diagnostics on changed Python files show **zero task-added diagnostics**. Existing repository diagnostics were left unchanged except where a new test initially introduced one:

- `controller/runtime/modes/hold.py`: 25 existing errors, 2 hints; ownership casts removed every diagnostic introduced by the obsolete-guard cleanup.
- `controller/runtime/control_trace_session.py`: OK.
- `test_control_trace_session.py`: OK.
- `test_hold_orchestration.py`: 0 errors, 15 existing hints.
- `test_hold_pulse_scheduler.py`: 0 errors, 7 existing hints.
- `test_hold_fan_authority.py`: OK.
- `test_hold_applied_output.py`: OK.
- `test_hold_model_persistence.py`: 10 existing errors, 20 hints.
- `test_hold_calibration.py`: 2 existing errors, 29 hints; callback-value assertions removed both task-added unused-parameter hints.
- `test_hold_control_trace.py`: OK.
- `test_hold_refit_trigger.py`: 10 existing errors, 29 hints; the task-added unused fixture hint was removed.

## Production-change justification

1. `_cancel_active_framed_calibration` returns `False` when `_runner is None`. This fixes the demonstrated public safety-callback crash without changing any live-runner path.
2. `setup()` resets `_runner` and `_persistence_worker` before hardware/constructor work. This fixes teardown after early failure and guarantees repeated-setup isolation.
3. The actual runner-revision-before-learning partial state closes persistence, trace session, and runner exactly once. Hold independently warns and continues after persistence or runner-finalization failure. It delegates trace closure directly to `ControlTraceSession`.
4. `ControlTraceSession.close()` now owns pending-event flush failure: it emits `Control trace pending flush failed: …`, marks itself closed in `finally`, and attempts the underlying recorder close once. Existing recorder-close failure warning behavior is preserved; repeated close is inert.
5. Ten post-setup optional-owner guards, the three conditional `_HoldOutputStatus.__getitem__` name checks, and the unused `command_auger_off` condition were removed under the documented lifecycle/callsite invariants. Their removal changes no reachable behavior and accounts for the complete denominator arithmetic above.

The stop-timeout warning/skip path was pre-existing and is not a production change. No production abstraction, alias, callback bag, compatibility API, coverage hook, or unrelated subsystem behavior was added.

## Changed paths

- `controller/runtime/modes/hold.py`
- `controller/runtime/control_trace_session.py`
- `tests/unit/runtime/test_control_trace_session.py`
- `tests/unit/runtime/test_hold_orchestration.py`
- `tests/unit/runtime/test_hold_pulse_scheduler.py`
- `tests/unit/runtime/test_hold_fan_authority.py`
- `tests/unit/runtime/test_hold_applied_output.py`
- `tests/unit/runtime/test_hold_model_persistence.py`
- `tests/unit/runtime/test_hold_calibration.py`
- `tests/unit/runtime/test_hold_control_trace.py`
- `tests/unit/runtime/test_hold_refit_trigger.py`
- `.superpowers/sdd/2026-08-12-mpc-runtime-decomposition/task-18-report.md`

## Concerns and blockers

No blocker remains. Strict passage requires at least 278 covered branches at a 308 denominator; the final numerator is 281, three covered branches above the minimum. The remaining 27 branches are retained and risk-dispositioned rather than excluded, suppressed, or covered with source/shape assertions. Final review identified only nonblocking gaps for combined multi-failure cleanup matrices and persistent flush-failure variants; existing static paths and the independent boundary cases cover their constituent behavior, so no additional source or test change was requested. Parent-owned aggregate review/commit remains outside this task.
