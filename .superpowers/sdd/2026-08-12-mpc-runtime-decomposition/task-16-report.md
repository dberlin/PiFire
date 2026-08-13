# Task 16: Unify Hold Calibration and Safety Handoff — Report

## Result

`HoldLearningRuntime` now owns the typed projection and trace handoff from the public `CalibrationDecision`. `HoldMode` retains command admission, safety/manual/lid decisions, runner dispatch, and grill hardware, but no longer interprets terminal calibration outcomes or builds calibration trace payloads.

One frozen `_CalibrationCancellation` now carries an admitted cancellation's reason, pulse reset/inhibit classification, cancellation command identity, runner-notification policy, terminal-feedback policy, safety trace identity, and current calibration identity/projection. Direct-result and framed callback cancellations route through the same reset/off/delivery/notification path. A callback-completed cancellation fences a matching in-flight active result by exact framed calibration identity, so the result restores the exact baseline without a second reset, notification, or evidence record.

## RED/GREEN evidence

### Required first RED

Selected node:

```text
tests/unit/runtime/test_hold_learning_runtime.py::test_calibration_handoff_projects_public_terminal_decision
```

Command:

```text
pytest -q tests/unit/runtime/test_hold_learning_runtime.py::test_calibration_handoff_projects_public_terminal_decision
```

Observed result: `1 failed in 2.96s`. The exact failure was:

```text
AttributeError: 'HoldLearningRuntime' object has no attribute 'handoff_calibration'
```

The test successfully constructed the public `CalibrationDecision`; collection, fixture construction, and calibration typing all succeeded. The parent independently reproduced this RED and approved production work.

### Incremental RED/GREEN matrix

| Contract | RED | GREEN |
|---|---|---|
| Public absent/active/terminal/event/unknown projection | 8 failures because `CalibrationHandoff` lacked frame projection fields | `8 passed` |
| Frozen result | Mutation RED: `DID NOT RAISE FrozenInstanceError` when `frozen=False` | `1 passed` after restoring the frozen dataclass |
| Ordered calibration trace and unknown ignore | `[]` instead of two calibration records | `1 passed` with exact `CalibrationTracePayload` values |
| Manual callback handoff | cancelled frame reason was `manual`, not `manual_override` | `1 passed` |
| Manual release handoff | no cancelled observation was produced | `1 passed` through the same typed path |
| Lid-toggle and safety callback handoff | lid reason was `lid`; safety callback did not notify runner | `2 passed` |
| Operator pause/stop/reset-progress identity | Mutation RED: all three stamped revision `0` / `safety-cancel` | `3 passed` with exact newer revision/action |
| Fahrenheit/Celsius ceiling | Mutation RED: `500.0` C obtained instead of `260.0` C | `2 passed` |
| Fault dedupe and recovery | Mutation RED: only two faults after recovery instead of three | passed after restoring recovery clear |
| Malformed boolean ceiling | Mutation RED: three faults instead of four because `True` was accepted as `1.0` | passed after restoring explicit bool rejection |

### Review-fix RED/GREEN evidence

The first scoped review found three load-bearing exactly-once gaps. Each was independently reproduced before its fix:

1. A command-aware synchronous runner changed its public decision to zero/inactive as soon as Hold forwarded a newer `pause`, `stop`, or `reset-progress`. All three exact nodes failed with `IndexError` because no cancelled observation existed. Hold now admits and stamps the operator cancellation from the exact currently latched active nonzero framed identity before forwarding the command; the command is then forwarded once. GREEN: `3 passed`.
2. An admitted `stale_result` cancellation called `FramedPulseRuntime.reset` once, then the generic stale-command branch called it again. The exact reset-count node failed with two `SAFETY` resets instead of one. The generic branch now runs only when the runner result did not already carry a cancellation. GREEN: `1 passed`.
3. Active-probe manual release routed a `MANUAL_RELEASE` safety event and then recorded the generic release event again. The exact trace-count node failed with two records instead of one. `_on_manual_release` now skips only the generic duplicate after a routed calibration cancellation; inactive and active zero-probe releases still record exactly one generic event. GREEN: `1 passed` active plus `2 passed` inactive/zero-probe.

The second scoped review found that cancellation handling was not carried across the control-period and repeated-result boundaries:

1. A newer operator command admitted inside the controller-period gate reset the active frame, but the pre-result boolean was discarded and the same tick called `advance()` once. Exact RED: `assert advances == 0`, actual `1`. Hold now carries immutable per-tick `calibration_handled` and `calibration_pending` values through the interval-return/result pipeline, using the existing persistent `pulse_frame_calibration_status`/projection as the cross-tick fence. The pending tick does not advance; the command is still forwarded exactly once; the same-revision inactive post-command result force-adopts its exact baseline/projection and then allows a baseline frame without restarting the probe.
2. A repeated identical stale active result matched the cancelled frame, but lost the handled marker before generic stale processing. Exact RED: `assert resets == 1`, actual `2`. The typed handled result now suppresses the generic stale reset, force-adopts the equal-revision baseline, and keeps advancement fenced while the repeated cancelled active result awaits replacement.
3. The same equal-revision adoption contract is frozen for the manual callback path: one `manual_override` cancellation is retained, the inactive exact baseline/projection is adopted, and the next framed advance is an inactive zero-probe baseline rather than a probe restart.

Exact second-review GREEN: the three new nodes passed together (`3 passed in 2.81s`); direct Task 16 passed `124 passed in 3.16s`; the affected six-file Hold set passed `117 passed in 5.20s`.

The final scoped review found three identity/evidence boundary gaps:

1. The carried cancellation fence was identity-blind for active results. With an old cancelled identity pending, a distinct active revision/action/generation was stripped to inactive. Exact RED: expected `pulse_calibration_status == "active"`, actual `"inactive"`. The carried fence now applies broadly only to an inactive acknowledgement; active results are handled only when their public command revision/action/generation exactly match the cancelled framed identity. GREEN: the distinct revision 2 / `resume` / generation 3 probe proceeds active and advances normally.
2. A newly admitted current active result was being assigned to controller request state and latched retroactively during cancellation. That invented a current-result interval which had never run and allowed stale cancellation to emit evidence under the new identity; mutation showed the resulting completion had a synthetic zero-duration frame. Exact RED: expected no revision-2 observation/trace/evidence, but one appeared. Hold now stages only the cancellation identity/status needed for the asynchronous fence, while `FramedPulseRuntime.reset()` completes physical work from its immutable prior latch. The current stale result produces no revision-2 observation or calibration summary; the prior framed request/load identity remains intact; cancellation identity and safety trace remain current. GREEN: `1 passed`.
3. At an exact scheduler boundary, cancellation reset can return both the naturally completed prior frame and a zero-duration boundary-reset artifact. The runtime now has a direct behavioral guard proving the prior completion retains its immutable revision/requested-load/requested-duty and all six seconds of delivery, while the zero-duration reset remains the current identity and has no observation. Together with the running-interval mutation guard, this freezes physical interval ownership without fabricated evidence.

Exact final-review interval-ownership GREEN: the three regression nodes passed together (`3 passed in 2.89s`); direct learning/calibration/framed-pulse passed `144 passed in 3.25s`; the affected six-file Hold set passed `117 passed in 5.25s`.

One intermediate trace-test fixture used reasons on `PROBE_CHANGED`, which the public trace schema correctly rejects. It was corrected to the valid reason-bearing `STAGE_TIMEOUT` event before the trace GREEN; the contract RED remained the prior empty-record failure.

## Public API and ownership

### `CalibrationHandoff`

`controller.runtime.modes.hold_learning.CalibrationHandoff` is a frozen, slotted result with:

- `status`: `inactive`, `active`, `accepted`, `rejected`, or `cancelled`;
- `reason`: joined terminal outcome reasons or `None`;
- `probe_load` and active `stage`;
- command revision, action, and generation;
- copy-owned immutable completed-stage tuple.

`HoldLearningRuntime.handoff_calibration(decision, result_revision=..., timestamp_ms=...)`:

1. records each recognized public calibration event in source order through its existing `ControlTraceSession`;
2. ignores unknown future event kinds;
3. derives the established external status/reason solely from `CalibrationDecision`; and
4. returns the immutable frame projection consumed by Hold.

LSP references show one production consumer (`HoldMode`) and the direct public behavior tests. `CalibrationHandoff` has no mutable runtime state and exposes neither `Any`, callbacks, nor `HoldMode`.

### Ownership before/after

| Concern | Before | After |
|---|---|---|
| Outcome/status/reason interpretation | Hold private helpers | `HoldLearningRuntime.handoff_calibration` |
| Calibration trace payload construction | Hold private helper | `HoldLearningRuntime.handoff_calibration` through existing trace session |
| Pulse calibration projection | Hold reread `CalibrationDecision` fields directly | Frozen `CalibrationHandoff` |
| Cancellation admission | Loose reason followed by downstream recomputation | Frozen `_CalibrationCancellation` returned by one admission decision |
| Callback/in-flight fencing | Callback reset followed by a later independent result cancellation | Exact framed identity recognizes the already-routed cancellation and restores baseline only |
| Calibration evidence | Existing `HoldLearningRuntime` observation/evidence path | Unchanged owner; receives one framed cancellation observation |
| Hardware and runner cadence | Hold | Unchanged |

Deleted Hold-only interpretation/trace symbols:

- `_CALIBRATION_OUTCOME_STATUS`
- `_calibration_status`
- `_calibration_reason`
- `_trace_calibration_result`
- `_calibration_cancellation_reason`
- the separate `_calibration_cancellation` builder

No compatibility aliases or forwarding wrappers were added. Text/LSP residue checks found no external test caller of the deleted private symbols.

## Behavioral matrices

### Public decision projection

| Decision | External status |
|---|---|
| absent | `inactive` |
| active | `active` |
| `start_rejected` | `rejected` |
| `safety_aborted` | `cancelled` |
| `stage_timeout` | `cancelled` |
| `stopped` | `cancelled` |
| `completed` | `accepted` |
| inactive with `start_accepted` event | `accepted` |
| unknown/inactive outcome | `inactive` |

Command revision/action/generation, active stage, completed stages, and joined outcome reasons remain exact. Known trace events retain the existing command/result/progress/probe/reason payload fields and order; unknown events are ignored.

### Cancellation admission

| Trigger | Reason | Reset/inhibit | Runner notify | Cancellation command |
|---|---|---|---|---|
| lid state/detect/operator toggle | `lid_open` | lid / lid-open | yes when active nonzero frame exists | `0`, `safety-cancel` |
| manual takeover/release path | `manual_override` | manual / manual-override | yes when active nonzero frame exists | `0`, `safety-cancel` |
| stale result | `stale_result` | safety / safety | yes | `0`, `safety-cancel` |
| controller reset/reconfiguration | `reset` | mode-change callback or safety result path | existing semantics retained | `0`, `safety-cancel` |
| safety or departure from Hold | `safety` | safety / safety | yes when active nonzero frame exists | `0`, `safety-cancel` |
| newer operator pause | `operator_pause` | safety / safety | no duplicate notify | exact newer revision, `pause` |
| newer operator stop | `operator_stop` | safety / safety | no duplicate notify | exact newer revision, `stop` |
| newer operator reset-progress | `operator_reset-progress` | safety / safety | no duplicate notify | exact newer revision, `reset-progress` |

Inactive calibration and active zero-probe dwell do not admit cancellation or fabricate evidence. A matching callback-following active result is recognized using the exact framed command revision/action/generation and is reduced to its recorded baseline allocation without rerouting the cancellation.

### Stable cancellation order

The retained framed dispatch sequence is:

1. terminalize completed old frames before the reset partial;
2. stamp the same cancellation reason and revision/action onto the framed cancellation;
3. command auger off;
4. deliver completed/reset observations, compact evidence, safety/frame trace, and terminal feedback in the existing order;
5. notify the runner when the typed decision requires it;
6. restore the consumed result's exact `baseline_allocation` and remove its probe;
7. hand the public decision to `HoldLearningRuntime` for calibration trace/projection;
8. stamp the returned projection into `PulseControllerState`.

Existing orchestration tests continue to prove actuator-off precedes terminal feedback and safety trace. Existing cancellation evidence tests continue to prove one raw and one compact record with matching identity.

### Safety ceiling and command admission

- The current `settings['safety']['maxtemp']` is read every tick.
- Fahrenheit and Celsius publish the same exact Celsius ceiling where equivalent.
- bool/malformed, nonfinite, and unknown units are rejected.
- One unchanged fault traces once; successful recovery clears the fault high-water; recurrence traces once again.
- Ceiling publication and valid/invalid command consumption remain before runner submission/result retrieval.
- Valid and invalid revisions remain one-shot; controller re-entry behavior and unsupported-controller handling are unchanged.
- Newer operator pause/stop/reset-progress cancellation is admitted from the latched active nonzero frame before the command is forwarded, so synchronous command-aware runners cannot erase the cancellation identity before `latest()`.

## Verification

Implementer commands and fresh results:

```text
pytest -q tests/unit/runtime/test_hold_learning_runtime.py tests/unit/runtime/test_hold_calibration.py
121 passed in 3.29s
```

```text
pytest -q tests/unit/runtime/test_hold_fan_authority.py tests/unit/runtime/test_hold_controller_advisories.py tests/unit/runtime/test_hold_orchestration.py tests/unit/runtime/test_hold_applied_output.py tests/unit/runtime/test_hold_pulse_scheduler.py tests/unit/runtime/test_hold_control_trace.py
117 passed in 5.42s
```

```text
ruff check controller/runtime/modes/hold.py controller/runtime/modes/hold_learning.py tests/unit/runtime/test_hold_learning_runtime.py tests/unit/runtime/test_hold_calibration.py
All checks passed!
```

Implementer LSP:

- `controller/runtime/modes/hold_learning.py`: `OK`.
- `controller/runtime/modes/hold.py`: `28 errors, 2 hints`; the remaining optional-owned-boundary and `_ErrorLogger` diagnostics are the existing Hold baseline categories. No Task 16 handoff symbol/import/call diagnostic remains after LSP reload.
- `tests/unit/runtime/test_hold_learning_runtime.py`: no errors; existing unused-fixture hints only.
- `tests/unit/runtime/test_hold_calibration.py`: two pre-existing `FakeControllerRunner.observation_outcome` assignment errors and fixture hints; no Task 16 test diagnostic.
- Final LSP references: `CalibrationHandoff` has six references; `handoff_calibration` has six references (one production call, four direct test calls, and its definition).

Parent final gate results after the interval-ownership review:

- Exact interval-ownership regression nodes: `3 passed in 0.18s`.
- Focused calibration/fan/advisory/orchestration/framed-pulse gate: `261 passed in 3.56s`.
- Full Hold/threaded/framed-pulse aggregate: `386 passed in 7.05s`.
- Direct `test_hold_learning_runtime.py`: `84 passed in 0.20s`.
- Strict branch coverage for substantial new collaborator logic: `209/230 = 90.869565%`, above 90%.
- Final Ruff: clean.
- Final production LSP: `hold_learning.py` and `framed_pulse.py` are `OK`; `hold.py` is `28 errors, 2 hints` in the same pre-existing optional-owned-boundary and `_ErrorLogger` categories.
- Final test LSP: `test_hold_calibration.py` retains two pre-existing `observation_outcome` assignment errors and 29 hints; seven added hints are unused local fixture parameters, with no new errors. The direct learning test retains hints only.
- Final LSP references: `CalibrationHandoff` has six references; `handoff_calibration` has six references with one production consumer.
- First, second, and final scoped reviews initially **FAIL**; every finding was independently reproduced RED and fixed.
- Interval-ownership review RED/GREEN: running-frame latch mutation expected revision `9` but got `10`; synthetic current-result evidence expected zero observations but got one; fixes passed the parent exact gate at `3 passed in 0.18s`.
- Parent focused gate: `261 passed in 3.56s`.
- Parent aggregate gate: `386 passed in 7.05s`.
- Parent lifecycle gate: `84 passed in 0.20s`.
- Latest production LSP: `framed_pulse.py` is `OK`; `hold.py` remains at the exact `28 errors, 2 hints` baseline. Test calibration LSP remains at the exact two pre-existing errors and 29 hints.
- Final re-review: **PASS / APPROVED** with no findings or test gaps; both interval-ownership identity findings are closed.
- Added RED set versus Task 15: `[]`.
- Commit: parent-owned; implementer made no commit.

## Changed paths

- `controller/runtime/modes/hold.py`
- `controller/runtime/framed_pulse.py`
- `controller/runtime/modes/hold_learning.py`
- `tests/unit/runtime/test_hold_learning_runtime.py`
- `tests/unit/runtime/test_hold_calibration.py`
- `tests/unit/runtime/test_framed_pulse_runtime.py`
- `.superpowers/sdd/2026-08-12-mpc-runtime-decomposition/task-16-report.md`

## Concerns

No known Task 16 behavioral or verification blocker remains. Parent final exact (`3 passed in 0.18s`), focused (`261 passed in 3.56s`), aggregate (`386 passed in 7.05s`), lifecycle (`84 passed in 0.20s`), strict branch coverage (`209/230 = 90.869565%`), Ruff, LSP, reference, and added-RED gates are green/unchanged as recorded. Final re-review is **PASS / APPROVED** with no findings or test gaps, and both interval-ownership identity findings are closed. Commit remains parent-owned. Task 17 generic Hold reduction and Task 18 aggregate branch cleanup were not performed.
