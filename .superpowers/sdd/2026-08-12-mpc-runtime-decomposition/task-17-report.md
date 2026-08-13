# Task 17: Reduce HoldMode Teardown State — Report

## Result

`HoldMode` now retains its resumable teardown transaction in one private, slotted `_HoldTeardownState`. A fresh state is installed at the start of every `setup`. The state is data-only: it contains no grill, runner, trace, persistence, callback, or service reference.

The dispatch transaction shared by ordinary and teardown result delivery is now named `_FramedDispatchState`. The rename was performed as one clean LSP cutover with no alias or re-export. No existing test was modified and no private-shape/source-text test was added.

## Direct guard evidence

The required direct command was identical at baseline, after the coherent migration, and at final verification:

```text
pytest -q tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once tests/unit/runtime/test_hold_orchestration.py::test_teardown_retry_completes_cleanup_after_pre_cleanup_failure tests/unit/runtime/test_hold_orchestration.py::test_teardown_retry_resumes_delivery_after_scheduler_advance tests/unit/runtime/test_hold_orchestration.py::test_partial_setup_failures_still_close_the_runner_once tests/unit/runtime/test_hold_pulse_scheduler.py::test_safety_manual_lid_and_teardown_reset_credit tests/unit/runtime/test_hold_pulse_scheduler.py::test_teardown_reports_final_observed_pulse_delivery_before_reset tests/unit/runtime/test_hold_pulse_scheduler.py::test_teardown_turns_auger_off_before_dispatching_final_frame_progress tests/unit/runtime/test_threaded_runner.py::test_hold_teardown_stops_threaded_runner
```

Results:

- Baseline before production edits: `13 passed in 3.05s`.
- After the coherent state/type migration: `13 passed in 3.13s`.
- Fresh final direct guard: `13 passed in 3.05s`.

The selected orchestration node includes all collected parameterized outcomes. The three teardown-specific pulse-scheduler nodes and the threaded-runner teardown node are included explicitly.

## Before/after field and type inventory

### Before

The resumable transaction was split across these ten `HoldMode` fields:

| Field | Type/default |
|---|---|
| `_teardown_phase` | `int = 0` |
| `_teardown_auger_on` | `bool = False` |
| `_teardown_now` | `float | None = None` |
| `_teardown_ptemp` | `float | None = None` |
| `_teardown_prior_output_source` | `OutputSource | None = None` |
| `_teardown_advance_dispatch` | `_TeardownFramedDispatch | None = None` |
| `_teardown_feedback_prepared` | `bool = False` |
| `_teardown_feedback` | `FramedPulseFeedback | None = None` |
| `_teardown_feedback_dispatched` | `bool = False` |
| `_teardown_reset_dispatch` | `_TeardownFramedDispatch | None = None` |

`_TeardownFramedDispatch` held the immutable result/configuration plus five delivery cursors: `delivered_recorded`, `completion_delivery_index`, `scheduler_reset_recorded`, `completion_trace_index`, and `feedback_dispatched`.

### After

`HoldMode` has one declaration for the transaction:

```text
_teardown: _HoldTeardownState
```

`_HoldTeardownState` is `@dataclass(slots=True)` and contains exactly:

| Field | Type/default | Meaning |
|---|---|---|
| `phase` | `_TeardownPhase = ACTIVE` | resumable teardown phase |
| `now` | `float | None = None` | latched teardown timestamp |
| `ptemp` | `float | None = None` | latched teardown temperature |
| `auger_on` | `bool = False` | sampled pre-off auger state |
| `prior_output_source` | `OutputSource | None = None` | source captured before final advance |
| `advance_dispatch` | `_FramedDispatchState | None = None` | cached final-advance result and cursors |
| `feedback_prepared` | `bool = False` | feedback preparation checkpoint |
| `feedback` | `FramedPulseFeedback | None = None` | cached feedback value |
| `feedback_dispatched` | `bool = False` | feedback delivery checkpoint |
| `reset_dispatch` | `_FramedDispatchState | None = None` | cached mode-change reset result and cursors |

`_last_tick_s` remains directly on `HoldMode`; it is also ordinary tick state. `_FramedDispatchState` preserves the exact former dispatch payload and cursor types. One fresh `_HoldTeardownState()` is assigned before the rest of every `setup` body.

## Phase and retry mapping

`_TeardownPhase(IntEnum)` replaces all numeric teardown phase comparisons and assignments:

| Phase | Value | Completed boundary and retry behavior |
|---|---:|---|
| `ACTIVE` | 0 | Timestamp and temperature are latched once. Pre-off auger state is sampled, then auger, fan, igniter, and power are commanded off in the existing order. The phase advances only after all four commands return. |
| `HARDWARE_OFF` | 1 | Later calls do not repeat completed hardware-off work. A failure before `runtime.advance()` returns leaves `advance_dispatch` empty, so advance may be retried. Once advance returns, its immutable result is cached before delivery. A delivery failure resumes using the cached result and its five cursors without a second advance. Feedback preparation, cached feedback delivery, reset construction, and reset delivery retain their existing checkpoints. |
| `FRAMED_FINALIZED` | 2 | Final advance/feedback/mode-change reset delivery has completed. Runner stop, permitted refit/final checkpoint, reserved-generation rotation, final applied interval, learning teardown, persistence/trace closure, and runner finish retain their existing nesting and order. |
| `FINISHED` | 3 | Set in the innermost cleanup `finally`, including when runner stop raises. A later teardown call is a no-op. The original runner-stop exception is re-raised after finalization. |

Detailed retry checkpoints are unchanged:

1. `now` and `ptemp` are assigned only while `now is None`.
2. Hardware-off phase is recorded only after every hardware command returns.
3. `prior_output_source` is captured before a first advance attempt.
4. `advance_dispatch` is assigned only after advance returns; its delivery cursors advance only after each corresponding operation returns.
5. Feedback is cached before `feedback_prepared` is set; dispatch is marked only after dispatch returns.
6. `reset_dispatch` is assigned only after reset returns; the same dispatch cursors resume partial delivery.
7. `FRAMED_FINALIZED` is assigned only after reset delivery returns.
8. The nested finalizers always finish learning/trace resources and assign `FINISHED`, while preserving runner-stop exception propagation.

Partial-setup paths retain the existing `None` guards for runner, framed runtime/scheduler, trace, learning, and persistence-owned resources.

## Successful order preserved

The implementation still performs:

1. one-time time/temperature latch;
2. pre-off auger sampling;
3. auger, fan, igniter, and power off;
4. final scheduler advance and resumable completion delivery;
5. final feedback preparation/delivery;
6. mode-change reset and resumable reset delivery;
7. runner stop;
8. permitted refit and final checkpoint;
9. reserved-generation rotation;
10. final applied interval;
11. learning/persistence/trace/runner finish.

No I/O moved into either state dataclass.

## Caller and rename evidence

Before renaming, LSP references for `_TeardownFramedDispatch` returned exactly seven same-file references:

1. the type definition;
2. the advance-cache annotation;
3. the reset-cache annotation;
4. `_resume_framed_dispatch`'s parameter annotation;
5. ordinary `_dispatch_framed_result` construction;
6. teardown final-advance construction;
7. teardown mode-change-reset construction.

There were no external callers. LSP rename applied all seven edits to `controller/runtime/modes/hold.py` in one operation.

The final repository-wide caller inventory for `_FramedDispatchState` is the same seven roles, all in `controller/runtime/modes/hold.py`. A repository-wide residue search found no `_TeardownFramedDispatch` reference and no superseded `_teardown_*` field reference.

The implementer session LSP retained its pre-edit document overlay after the subsequent surgical state migration, even after file/all-server reload attempts, so that output was not accepted as final evidence. Parent fresh-session LSP references subsequently confirmed exactly seven `_FramedDispatchState` references, all in `controller/runtime/modes/hold.py`, with no external caller.

## Deleted symbols

Deleted without aliases, re-exports, or wrappers:

- `_TeardownFramedDispatch`
- `_teardown_phase`
- `_teardown_auger_on`
- `_teardown_now`
- `_teardown_ptemp`
- `_teardown_prior_output_source`
- `_teardown_advance_dispatch`
- `_teardown_feedback_prepared`
- `_teardown_feedback`
- `_teardown_feedback_dispatched`
- `_teardown_reset_dispatch`

## Review

Self-review compared every teardown read/write and checkpoint against the pre-edit method. No I/O call, condition, exception boundary, or success/retry ordering was moved. The only review fix was restoring the standard blank-line separation between the two top-level dataclasses; the final direct guard was rerun afterward.

No Hold test needed strengthening because the existing observable event-order, count, identity, timestamp, exception, retry, and closure guards remained green. No tests, schemas, APIs, collaborators, protocols, modules, compatibility paths, type suppressions, or coverage exclusions were added.

Parent scoped code review: **PASS / APPROVED**, with no findings. The reviewer recorded five nonblocking behavioral coverage opportunities—feedback-preparation failure, feedback-dispatch failure, reset-delivery retry, repeated-setup isolation, and latched teardown inputs—for possible Task 18 assessment; none is a Task 17 implementation defect or reason to add a private-shape/coverage-only test here.

## Parent-supplied gates

Task 16 supplied the incoming baseline of `261 passed` focused, `386 passed` aggregate, clean Ruff, and `28 errors, 2 hints` in `hold.py`'s established optional-owned-boundary and `_ErrorLogger` LSP categories.

Task 17 parent gates:

- full `test_hold_orchestration.py`: `15 passed in 0.16s`;
- focused affected Hold learning/calibration/pulse/framed/threaded set: `325 passed in 6.50s`;
- aggregate Hold/threaded set: `386 passed in 7.07s`;
- Ruff: clean;
- fresh `hold.py` LSP: exactly `28 errors, 2 hints`, unchanged from Task 16 and confined to the same established categories;
- fresh LSP references: exactly seven `_FramedDispatchState` references, all in `controller/runtime/modes/hold.py`.

The implementer did not run broad suites, coverage, formatters, linters, Ruff, VCS commands, or commits.

## Changed paths

- `controller/runtime/modes/hold.py`
- `.superpowers/sdd/2026-08-12-mpc-runtime-decomposition/task-17-report.md`

No test file changed.

## Concerns

No known behavioral, verification, or review concern remains: implementer baseline, migration, and final direct guards each passed all 13 collected cases; parent orchestration, focused affected, aggregate, Ruff, LSP diagnostic, and LSP reference gates are green/unchanged as recorded; and scoped review is **PASS / APPROVED** with no findings. Five nonblocking observable-coverage opportunities are recorded in the review section for possible Task 18 assessment. The implementer-session stale LSP overlay was a tooling-only issue resolved by the parent's fresh LSP evidence.
