# Task 14: Extract Observation and Evidence Reconciliation — Implementation Report

## Status

Implementation, focused integration, parent aggregate gates, strict coverage, Ruff, targeted LSP/reference gates, and scoped review fixes are complete. The exact seven intentional later-task REDs remain. No commit was created; commit remains parent-owned.

## Strict RED/GREEN handshake

### RED

Created only `tests/unit/runtime/test_hold_learning_runtime.py`, importing the required absent public module. Main confirmed the exact collection RED:

```text
uv run pytest -q -n0 tests/unit/runtime/test_hold_learning_runtime.py
exit 2 during collection
ModuleNotFoundError: No module named 'controller.runtime.modes.hold_learning'
at tests/unit/runtime/test_hold_learning_runtime.py:32
```

No production file was created or modified before that confirmation.

### GREEN

Direct runtime test:

```text
uv run pytest -q -n0 tests/unit/runtime/test_hold_learning_runtime.py
23 passed in 0.12s
```

Final focused affected set:

```text
uv run pytest -q -n0 \
  tests/unit/runtime/test_hold_learning_runtime.py \
  tests/unit/runtime/test_hold_control_trace.py \
  tests/unit/runtime/test_hold_calibration.py \
  tests/unit/runtime/test_hold_pulse_scheduler.py \
  tests/unit/runtime/test_hold_model_persistence.py \
  tests/unit/runtime/test_threaded_runner.py
223 passed in 6.27s
```

## Files

Created:

- `controller/runtime/modes/hold_learning.py`
- `tests/unit/runtime/test_hold_learning_runtime.py`
- `.superpowers/sdd/2026-08-12-mpc-runtime-decomposition/task-14-report.md`

Modified for clean cutover/direct-private-caller migration:

- `controller/runtime/modes/hold.py`
- `tests/unit/runtime/test_hold_calibration.py`
- `tests/unit/runtime/test_hold_control_trace.py`
- `tests/unit/runtime/test_hold_model_persistence.py`
- `tests/unit/runtime/test_hold_pulse_scheduler.py`
- `tests/unit/runtime/test_threaded_runner.py`

No Task 15 lifecycle, Task 16 safety/calibration command orchestration, or Task 17 general cleanup was moved.

## Public API and state ownership

`HoldLearningRuntime` provides:

- `submit_completed_observation`
- `reconcile_outcomes`
- `record_gap`
- `bind_generation`
- `retire_generation`
- `persist_evidence`
- read-only `evidence_available`
- `mark_evidence_unavailable` for the remaining Task 15 lifecycle bridge

The collaborator exclusively owns:

- the bounded pending-observation table;
- completed-frame learner submission and exact feedback/observation join;
- accepted, evicted, terminal-drop, malformed, stale/retired, missing-identity, and generation-mismatch reconciliation;
- rejected-observation and recorder-gap creation/publication;
- compact calibration evidence;
- ordinary evidence batches and confidence-decision split-channel routing;
- runner generation evidence bind/retire calls;
- learning-evidence availability.

Its collaborators are explicit typed protocols for the runner and evidence persistence plus `ControlTraceSession`; pending entries are frozen dataclasses containing frame key, observation, trace identity, generation, and immutable trace-record tuples. It accepts neither `HoldMode`, `Any`, callback bags, service locators, nor optional `getattr` dispatch.

`HoldMode` retains tick/hardware/safety/session-rotation/lifecycle orchestration and delegates only through the runtime boundary. It has no forwarding aliases and cannot inspect the pending table.

## Observation/evidence ordering matrix

| Case | Preserved effect |
|---|---|
| Accepted outcome | Original submission sequence/generation/observation and applied feedback stay joined; trace emits observation, evaluation, then lifecycle in FIFO order. |
| Runner self-eviction | Evicted pending frame retires immediately and publishes the existing `runner-observation-evicted` gap. |
| Terminal drop | Matching pending frame retires once with the runner-provided reason. |
| Dropped-sequence accounting | Drain accounting is consumed once without duplicating terminal effects. |
| Local capacity overflow | Oldest insertion retires with `pending-observation-overflow`; later frames retain FIFO. |
| Missing identity / identity or generation mismatch | Outcome becomes the same visible rejected observation and cannot attach to a new session/generation. |
| Retired generation / late outcome | Retirement fences the old pending frame; late drains cannot reopen it. |
| Trace append refusal | Remaining record suffix stays queued and preserves global FIFO for the next reconciliation. |
| Invalid probe | Calibration evidence may persist, learner submission is skipped, and one bounded synthetic rejected observation remains trace-visible. |
| Evidence routing | Confidence decisions go individually to the runner FIFO in encounter order; non-confidence evidence stays one atomic ordered persistence batch. |
| Cross-channel refusal | Refusal does not skip the other channel; any refused receipt/batch or blocked worker marks availability false. |
| Recorder gap | One matching trace gap and compact gap record retain frame/session/cook/timestamp identity. |

## Direct behavioral matrix

The 23 direct tests cover:

- accepted outcome identity and reconciliation idempotence;
- submission eviction, terminal drop, dropped-sequence consumption, and malformed submission identity;
- pending-capacity overflow, multiple invalid-probe entries, and FIFO retention;
- missing trace identity, missing collaborators, and generation mismatch;
- retired-generation late-outcome fencing and matching-only generation binding;
- split-channel ordering, absent-runner confidence behavior, confidence/evaluation evidence, and duplicate reconciliation;
- confidence, ordinary, and calibration persistence refusals;
- blocked persistence and gap publication;
- learner submission exception propagation without partial acceptance;
- valid and invalid calibration evidence;
- trace refusal retention and public reconciliation retry;
- allocation-join, invalid-probe, role-generation, gate, and malformed-outcome rejection;
- lifecycle payload validation and immutable `TraceSetting` copying;
- trace plus compact recorder-gap publication;
- typed public runner generation bind/retire calls;
- no-submission behavior without exposing mutable pending state.

## LSP caller inventory

Before implementation (definition included in totals):

- `_deliver_completed_pulse_observation`: 3 references (definition plus two Hold dispatch callers).
- `_reconcile_model_observation_outcomes`: 8 references (definition, five Hold production callers, two threaded-runner test callers).
- `_bind_runner_evidence_context`: 6 references (definition plus Hold callers).
- `_retire_runner_evidence_context`: 4 references (definition plus three Hold callers).
- `_pending_model_observations`: 30 references across production ownership and direct test setup/assertions.
- `_learning_evidence_available`: 16 references.

Final symbol/reference checks:

- LSP workspace symbol queries return no symbols for all six old moved names/fields.
- A final text residue check across `controller/runtime/modes/hold.py` and `tests/unit/runtime` also returns no occurrences of the old delivery/reconcile/bind/retire/gap/bound/flush/persist names or old fields.
- `submit_completed_observation`: one Hold production caller plus direct public-runtime tests.
- `reconcile_outcomes`: five Hold production callers plus migrated/direct tests.
- `bind_generation`: four Hold production callers plus direct test coverage.
- `retire_generation`: three Hold production callers plus direct test coverage.
- `controller/runtime/modes/hold_learning.py` LSP diagnostics: `OK`.
- `tests/unit/runtime/test_hold_learning_runtime.py` LSP diagnostics: zero errors, 47 unused-local hints.
- `controller/runtime/modes/hold.py` reports the parent-confirmed 39 pre-existing errors and 2 hints; the new integration adds none.

## Removed ownership/residue

Deleted from `HoldMode` rather than forwarded:

- pending gap publication, retirement, and capacity bounding;
- allocation and calibration evidence transforms;
- completed observation delivery;
- rejected observation construction/queueing;
- pending trace suffix flushing;
- controller evidence persistence split;
- outcome reconciliation;
- runner generation evidence binding and retirement;
- `_pending_model_observations` and `_learning_evidence_available`.

Existing tests now assert public collaborator effects (trace records, persistence batches, runner drains/submissions/bindings/retirements) rather than directly constructing or inspecting Hold's deleted table.

## Parent gate evidence and review fixes

Main supplied the final post-fix parent gates:

- focused set: 223 passed;
- aggregate: the exact seven intentional nodes failed and 277 passed;
- strict branch coverage: 95/100;
- Ruff: clean;
- `hold_learning.py` LSP: OK;
- direct tests: zero errors and 47 hints;
- workspace symbol queries for `_reconcile_model_observation_outcomes` and `_pending_model_observations`: empty;
- Hold diagnostics: 39 pre-existing errors and 2 hints, with no new integration diagnostic.

Review fix round 1:

1. Invalid-probe synthetic entries now carry the runtime's owning runner generation rather than sentinel generation `-1`. The generation is initialized explicitly and advances only through `bind_generation`. A direct test observed the old failure (`remaining == (-1,)`) before the fix and now proves trace refusal followed by generation retirement/rotation cannot publish the old invalid frame into the new session; the runner still receives no learner submission.
2. Removed cross-module imports of underscored `_OutcomeValue` and `_model_lifecycle_payload`. Raw MPC lifecycle diagnostics now enter through the deliberate public typed `parse_model_lifecycle_payload` boundary at the learning/trace boundary, and the migrated control-trace test uses that public boundary.
3. Updated this report with parent final evidence and fresh review-fix verification.

Fresh review-fix evidence:

```text
direct: 23 passed in 0.12s
focused affected: 223 passed in 6.27s
branch gate: 23 passed in 0.07s; PASS 95.0% (95/100) > 90.0%
Ruff changed review-fix files: All checks passed
hold_learning.py LSP: OK
direct test LSP: zero errors, 47 hints
Hold LSP: 39 pre-existing errors, 2 hints; no integration error
old _reconcile_model_observation_outcomes query: empty
old _pending_model_observations query: empty
```

Parent owns commit.

The seven intentional later-task REDs remain exactly:

1. `tests/unit/runtime/test_hold_orchestration.py::test_partial_setup_failures_still_close_the_runner_once`
2. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[checkpoint]`
3. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[persistence-flush]`
4. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[refit]`
5. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[runner-stop]`
6. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[success]`
7. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[trace-close]`

## Concerns

- Parent owns commit.
- No compatibility aliases, coverage exclusions, source-text assertions, schema changes, persistence SQL changes, controller math changes, hardware behavior changes, mode registry changes, or commits were introduced.
