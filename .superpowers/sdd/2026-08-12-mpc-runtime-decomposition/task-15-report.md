# Task 15: Move Hold Model Lifecycle — Report

## Result

`HoldLearningRuntime` is now the concrete owner of Hold's model restore, activation reconciliation, activation-event persistence, online checkpointing, one-shot refit, final checkpoint publication, and post-stop persistence/trace/runner cleanup. `HoldMode` retains hardware shutdown, framed-pulse terminalization, the runner `stop_for_refit()` call, and a small phased teardown gate.

The seven frozen Task 15 teardown REDs are closed in the implementer-focused run.

## RED/GREEN evidence

### Required first RED

Selected node:

```text
tests/unit/runtime/test_hold_learning_runtime.py::test_restore_model_clears_stale_authority_before_absent_checkpoint_noop
```

Command:

```text
uv run pytest -q tests/unit/runtime/test_hold_learning_runtime.py::test_restore_model_clears_stale_authority_before_absent_checkpoint_noop
```

Observed result: exit 1, one failed. Construction failed with `TypeError` because the pre-Task-15 `HoldLearningRuntime` did not accept the planned `model_store` lifecycle collaborator/API. The parent independently reproduced this RED before implementation approval.

### Implementer GREEN evidence

- Initial direct lifecycle file: `59 passed`.
- Initial direct lifecycle file plus the exact seven frozen nodes: `66 passed`, exit 0.
- Exact seven frozen nodes alone: `7 passed`, exit 0.
- Migrated model-persistence/refit/calibration suites: `81 passed`, exit 0.
- After round-one regression and branch-matrix additions, direct lifecycle file: `73 passed`, exit 0.
- Final implementer affected set (73 direct + four parent regressions + seven frozen nodes): `84 passed`, exit 0.

### Parent gate round one

The first parent gate reported four failures (`303 passed` focused; the same four with `316 passed` aggregate), direct `59 passed`, and strict branch coverage `180/224 = 80.357%`; Ruff was clean.

Root causes and fixes:

1. The accepted-refit trace fixture returned legacy `SimpleNamespace(accepted=True)`. The typed runtime correctly rejected that malformed refit result. The fixture now returns public `TeardownRefitResult` values.
2. Reserved runner generations were rotated only when `stop_for_refit()` did not return `False`, so stop-timeout terminal drops were not reconciled before evidence sinks closed. Rotation now follows every non-exceptional stop result; refit/checkpoint remain skipped for a stop timeout.
3. The threaded teardown test constructed `HoldMode` with `__new__` and omitted the required grill/runtime lifecycle shape. It now uses the real `hold_cycle` setup and still verifies one threaded runner shutdown.
4. A function-local controller-evaluation test constructed `HoldLearningRuntime` with an incomplete legacy persistence namespace and omitted its new required collaborators. It now supplies narrow typed persistence/logger collaborators, `model_store`, and `controller_name`.

The four exact regression nodes were first reproduced as `4 failed`, then passed as `4 passed`. Coverage JSON showed 44 missing branches. Behavior-focused direct tests were added for partial lifecycle collaborators, invalid status/settings boundaries, controller-name restore ownership, identical checkpoint success/failure, terminal checkpoint refusal, malformed authoritative retry, and retirement/trace-flush exception containment. Per parent instruction, the implementer did not rerun the parent-owned coverage gate.

### Review round two

The spec review found three uncovered ownership/retry contracts. Each was reproduced with a public behavior RED before its fix:

1. An accepted `pid_sp` online checkpoint followed by `restore_model(controller_name="mpc")` incorrectly deduplicated an identical final MPC snapshot. The exact test failed with one `pid_sp` submission instead of the required `pid_sp` and `mpc` submissions. An actual controller-name change now clears checkpoint dedupe state.
2. Mutating the nested `progress` mapping returned by `status_fragment()` changed the runner-owned source mapping. The runtime now deep-copies the learning status fragment while preserving its wire schema.
3. A framed reset mutated scheduler state and then observation delivery raised. Retry repeated `advance`, feedback reporting, and reset (`2/2/2` calls instead of `1/1/1`). Hold now retains each immutable `FramedPulseResult` and a teardown-local delivery cursor; retry resumes only unfinished delivery, trace, feedback, and reset work without advancing the scheduler again.

Exact GREEN evidence:

- Controller-switch and nested-status nodes: `2 passed`, exit 0.
- Resumable teardown-delivery node: `1 passed`, exit 0.
- Direct lifecycle plus the entire orchestration file: `88 passed`, exit 0.
- Four round-one parent regression nodes: `4 passed`, exit 0.
- Affected model-persistence/refit/calibration suites: `81 passed`, exit 0.
- Ruff on the changed Python set: clean, exit 0.
- Production LSP remains `hold_learning.py: OK`; `hold.py: 30 errors, 2 hints`, with only the pre-existing owned-boundary `_ErrorLogger` mismatch outside the Task 15 delta.

Parent-owned aggregate, coverage, Ruff, final diagnostics, review, and commit gates are recorded below for the parent to fill with their exact outputs.

## Public lifecycle API and ownership

`HoldLearningRuntime` now exposes:

- `restore_model(timestamp_ms=..., controller_name=...)`
- `reconcile_activation()`
- `drain_activation_events()`
- `status_fragment()`
- `submit_online_checkpoint(snapshot)`
- `refit_once(settings)` returning immutable `HoldRefitResult`
- `publish_final_checkpoint_once(result, timestamp_ms=...)`
- `finish_teardown(generation=...)`

Typed collaborators are the public `ModelLifecycleRunner` lifecycle surface plus the established observation/snapshot/status/refit runner operations, a narrow model-store load surface, the existing persistence worker boundary, `ControlTraceSession`, and a narrow logger protocol.

### Ownership before/after

| Concern | Before | After |
|---|---|---|
| Stored checkpoint restore | Hold private helper/state | `HoldLearningRuntime.restore_model` |
| Durable activation identity and lifecycle evidence high-water | Hold fields/private helpers | Runtime-owned state and `reconcile_activation` |
| Runner activation-event drain and atomic evidence batch | Hold private helper | `drain_activation_events` |
| Tick checkpoint submission | Hold private helper/worker field | `submit_online_checkpoint` |
| Refit result and one-shot gate | Hold fields/private helpers | Immutable `HoldRefitResult` and `refit_once` |
| Finalization/checkpoint attempt, outcome, retry, idempotence | Hold fields/private helper | `publish_final_checkpoint_once` |
| Evidence/lifecycle availability | Hold field | Runtime property/state |
| Persistence flush, trace flush/close, retained runner finish | Hold teardown body | `finish_teardown` |
| Hardware commands, framed terminalization, runner stop/join | Hold teardown | Remains in Hold |

Hold no longer owns or forwards `_restore_model`, `_activation_identity`, `_pair_activation_lifecycle`, `_reconcile_activation_state`, `_drain_activation_events`, `_refit_model`, `_refit_model_once`, `_publish_final_checkpoint_once`, or `_checkpoint_model`. Lifecycle-owned fields were removed from Hold; no compatibility aliases were added.

## Behavior matrices

### Stored model restore

| Durable checkpoint | Runner result | Observable behavior |
|---|---|---|
| Absent | N/A | Clear trace authority; no restore; no log event |
| Valid, synchronous accept | Accepted | Restore once; `restore_submitted` provenance; accepted trace/log |
| Valid, asynchronous accept | Accepted | Same immutable submission provenance; asynchronous acceptance wording retained |
| Valid | Rejected | Start fresh; rejection trace/log retained |
| Invalid/malformed | Store/runner contract rejects or raises | Existing invalid-checkpoint ownership and exception behavior retained |

### Activation reconciliation

| Durable state/evidence | Result |
|---|---|
| Absent | No restore |
| New prepared/active/aborted identity | Restore exactly once with ordered evidence |
| Same identity, no later lifecycle evidence | No duplicate restore |
| Retired schema identity | Reject and mark evidence unavailable |
| Later rollback/fallback evidence ID | Apply once; advance lifecycle evidence high-water |
| Read/schema failure | Mark unavailable and emit existing warning |

### Activation-event persistence

| Runner drain / worker result | Result |
|---|---|
| Empty | No submission |
| Ordered events, accepted | One immutable ordered atomic batch |
| Refused or missing worker | Mark evidence unavailable; retain warning; no synchronous wait |

### Refit and final checkpoint

| Refit condition | Typed outcome |
|---|---|
| Identification disabled | `DISABLED` |
| No/insufficient result | `INSUFFICIENT` |
| Rejected | `REJECTED` |
| Ready for review | `READY_FOR_REVIEW` |
| Accepted for next cook | `ACCEPTED_NEXT_COOK` |
| Malformed result or exception | `FAILED` |

`refit_once` caches the immutable typed result. Final publication finalizes that exact outcome before queueing its snapshot. A finalization failure transitions to `CHECKPOINT_FAILURE` without queueing the stale snapshot. A refused checkpoint performs only the established authoritative retry, then becomes terminal and idempotent. A per-tick checkpoint identical to the final authoritative snapshot is not redundantly submitted; a prior failed identical tick submission consumes the normal attempt and leaves one bounded failure-authority retry.

### Teardown ordering and failure closure

Successful order:

1. Command auger, fan, igniter, and power off.
2. Terminalize/reset the framed pulse once, using a monotone timestamp and the pre-off auger state.
3. Deliver terminal feedback/observation once, including when persistence evidence is unavailable.
4. Call runner `stop_for_refit()` once.
5. Refit/finalize/publish final checkpoint when permitted.
6. Retire the learning generation.
7. Flush/stop persistence.
8. Flush pending trace work and close trace.
9. Finish retained/core runner resources.

| Injected failure | Preserved behavior |
|---|---|
| Pre-cleanup framed terminalization | Retryable; phase does not advance |
| Runner stop | Exception propagates after persistence/trace/runner-final cleanup is attempted once |
| Refit | Typed failure result; cleanup continues |
| Checkpoint | Bounded retry; terminal checkpoint failure; cleanup continues |
| Persistence flush | Evidence unavailable and checkpoint-failure finalization; trace/runner finish continue |
| Trace close | Warning retained; runner finish still occurs |
| Missing recorder/worker during partial setup | Runner stop/finish still occur once |
| Repeated teardown | Every owned completed operation remains at most once |

## Test and caller migrations

- Direct lifecycle tests cover restore absent/sync/async/rejected/invalid, activation phases/high-water/read failure, activation batches, recursive status-copy ownership, controller-scoped checkpoint dedupe, complete refit outcome matrix, finalization/checkpoint retries and trace authority, persistence flush outcomes, partial collaborators, exception closure, and exactly-once teardown.
- Hold model-persistence tests patch activation reads at their new owner and use public runtime calls/observable runner effects rather than deleted Hold lifecycle fields/helpers.
- Hold refit tests inject the persistence worker before runtime construction and assert public teardown effects rather than deleted Hold checkpoint fields/helpers.
- Hold calibration tests capture constructed persistence workers rather than reading a deleted Hold worker field; their direct runtime construction uses typed lifecycle collaborators.
- Orchestration boundary monkeypatches follow activation persistence reads to `hold_learning`; teardown retry coverage proves scheduler advance, terminal observation, feedback, reset, and cleanup each occur exactly once after a delivery failure.
- The stale `HoldMode._refit_model` prose reference in Grey learning runtime was updated to `HoldLearningRuntime.refit_once`.

## Frozen node inventory

Closed nodes:

1. `tests/unit/runtime/test_hold_orchestration.py::test_partial_setup_failures_still_close_the_runner_once`
2. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[checkpoint]`
3. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[persistence-flush]`
4. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[refit]`
5. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[runner-stop]`
6. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[success]`
7. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[trace-close]`

Remaining Task 15 frozen nodes in the implementer run: none.

## Diagnostics and parent gates

Implementer LSP checks:

- `controller/runtime/modes/hold_learning.py`: `OK`.
- `controller/runtime/modes/hold.py`: `30 errors, 2 hints`, improved from the Task 14 baseline of `39 errors, 2 hints`. The Task 15 unused imports and lifecycle-logger constructor mismatch are closed. The remaining `ModelPersistenceWorker` `_ErrorLogger` mismatch is pre-existing; no new Task 15 production diagnostic remains.
- `tests/unit/runtime/test_hold_learning_runtime.py`: no errors; test-fixture unused-value hints only.
- Text/AST residue check found no callers of deleted Hold lifecycle helpers/fields; the sole stale prose reference was migrated.

Parent round-two results:

- Focused gate: `322 passed`.
- Aggregate gate: `335 passed`.
- Direct lifecycle file: `73 passed`.
- Strict branch coverage for `hold_learning.py`: `203/224 = 90.625%`, above the required 90%.
- Ruff on all changed Python files: clean.
- Final production LSP: `hold_learning.py` is `OK`; `hold.py` is `30 errors, 2 hints` versus Task 14's `39 errors, 2 hints`, with unchanged diagnostic categories and only the pre-existing `_ErrorLogger` mismatch at the owned boundary.
- Scoped re-review: checkpoint dedupe, teardown retry under the exact original post-return failure, report accuracy, and nested status copy are all addressed; no new findings. Final verdict: **Spec PASS / Quality APPROVED**.
- Added RED set versus Task 14: `[]` (the parent aggregate is fully GREEN against Task 14's exact seven intentional REDs).
- Commit: parent-owned; implementer made no commit.

## Concerns

No known Task 15 behavioral or review blocker remains. Task 16 calibration/safety consolidation and Task 17 broad Hold cleanup were intentionally not performed.
