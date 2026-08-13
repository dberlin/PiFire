# Task 13 Report: Visible Hold Tick Orchestration

## Status

Implementation is ready for parent validation and review. `HoldMode.on_tick` is now a short, explicit sequence of the seven required responsibilities; the final trace/reconciliation responsibility is split into visible framed-dispatch, Hold lid/fan effects, and flush calls so no trace-named phase hides hardware work. The inline tick implementation was cleanly moved into named Hold methods; no compatibility seam, duplicate implementation, generic pipeline abstraction, callback registry, event bus, or production module was added.

The strengthened public `on_tick` integration contract is GREEN. The parent reports the three frozen-status PWM regressions GREEN, the focused contract set at 7 intentional RED / 205 pass, the aggregate at 7 intentional RED / 254 pass, and focused Ruff clean. The only Task 13-added LSP error was narrowed at the calibration trace schema boundary; the final diagnostic refresh reports 83 pre-existing file errors and no Task 13-added diagnostics.

## Test-first evidence

No structure-only test was added. Task 1's ordered characterization tests already protect the observable reconfigure, actuator/feedback/safety, frame-boundary, trace, checkpoint, and lifecycle order needed for this refactor.

The parent confirmed the existing genuine Task 13 RED before production edits:

```text
tests/unit/runtime/test_hold_applied_output.py::test_manual_release_reseeds_before_fresh_controller_authority
```

The failure showed that manual release produced no `OutputSource.SEED` feedback before `runner.latest`. This is an observable manual-release -> runner-result -> hardware-authority boundary, not an assertion about private helper structure. The minimal GREEN change now revokes manual auger authority by commanding the auger OFF, observes that resulting actuator state, then seeds from the observed OFF state plus the current cycle ratio and lid state before a subsequent tick can obtain a fresh controller result. Control-plane reseeding is gated only on runner availability; a trace-less path dispatches directly to the runner. No test assertion was weakened or removed.

Earlier callback-version GREEN evidence: `tests/unit/runtime/test_hold_applied_output.py::test_manual_release_reseeds_before_fresh_controller_authority` passed while the test still invoked `_on_manual_release` directly.

### Manual-release GREEN correction

The parent's first focused rerun after the initial seed-only change still reported the same RED. The seed was present before `runner.latest`, but manual release had left the auger ON; the scheduler therefore observed desired ON equal to actual ON and emitted no fresh controller hardware transition. The corrected callback revokes manual hardware authority OFF before observing and seeding the released state. The parent confirmed only this direct-callback version GREEN before the public integration test was strengthened.

The callback-only test was then strengthened to leave a nonzero expired override for the public `on_tick` callback to discover. With phase integration temporarily removed, the parent observed the exact integration RED: one failure at the ordering assertion because no `OutputSource.SEED` feedback existed before `runner.latest`. The explicit `_release_expired_manual_auger` phase restored that public path; the final focused suite confirms the strengthened integration contract GREEN.

## Phase ownership

| Ordered phase in `on_tick` | Hold phase method | Ownership and explicit values |
|---|---|---|
| 1. Adopt configuration/session context | `_adopt_tick_configuration_and_session` | Adopts installed runner revisions, retires/replaces framed state through existing Hold lifecycle calls, opens/binds trace identity, reconciles activation, and handles controller-update reconfiguration. It returns frozen `_HoldTickContext` with `now`, `ptemp`, explicit immutable auger/fan/PWM status, trace session, calibration-reset fact, and whether adoption already seeded the runner. |
| 1a. Release expired manual authority | `_release_expired_manual_auger` | Visibly detects a nonzero expired auger override before runner submission, invokes the Hold callback to command OFF and seed the observed released state, and returns an updated frozen context. If runner adoption already commanded OFF and seeded that installed generation at the same timestamp, release records/clears without duplicating the seed. |
| 2. Publish safety/calibration inputs | `_publish_safety_ceiling_and_consume_calibration` | Retargets the installed controller, publishes the grill safety ceiling, and consumes the calibration command in the original order. |
| 3. Submit/obtain runner result and handle calibration cancellation | `_submit_obtain_and_handle_calibration_cancellation` | Submits fresh temperature every tick, reconciles prior observations, preserves runner/framed cadence, and obtains only when due. A due probe cancellation visibly remains in this phase because its established reset, auger-OFF, terminal feedback/safety trace, and runner notification must precede result adoption. It returns frozen `_HoldRunnerResult` with the typed result, interval, and cancellation reason. |
| 4. Decide safety/manual/lid inhibition | `_decide_safety_manual_lid_inhibition` | Applies the obtained result to Hold control state, preserves cancellation/stale and fan-authority decisions, and returns frozen inhibition facts without commanding hardware. |
| 5. Advance/reset framed pulse | `_advance_or_reset_framed_pulse` | Applies stale reset through the public runtime reset path, preserving actuator-off-before-terminal-feedback order, then retains trace update/calibration/checkpoint timing and advances the framed runtime when uninhibited. |
| 6. Command grill hardware | `_command_grill_hardware` | Executes framed auger transitions or lid-opening auger-off in Hold before any terminal framed dispatch. No collaborator gained grill access. |
| 7. Trace/feedback/reconcile/flush | `_dispatch_framed_trace_and_feedback`, `_apply_hold_lid_fan_hardware_and_state`, `_flush_tick_trace` | Makes the existing sequential order explicit: dispatch framed completion/feedback after its hardware edge; then apply Hold-owned lid/fan hardware, state, and control persistence in their established order; finally flush trace at the original end-of-tick point. No flush/trace-named helper hides grill commands. |

`FramedPulseRuntime` still owns frame transitions, reset/completion results, applied-output feedback construction, and pulse-local accounting. `ControlTraceSession` still owns trace identity/state/records/flush. Hold still owns runner dispatch, all grill commands, settings/control mutation, safety/manual/lid decisions, observation/evidence reconciliation, checkpointing, persistence, and lifecycle orchestration.

## Ordering invariants preserved

- Installed runner reconfiguration retires the old framed pulse and evidence generation before the replacement generation is restored, bound, seeded, or queried.
- Expired manual auger authority is ordered OFF -> override clear -> MANUAL_RELEASE safety trace -> SEED before `runner.latest`; an OFF failure leaves state/trace unreleased and propagates. If runner adoption already commanded OFF and seeded the installed generation at the same timestamp, release records/clears without emitting a duplicate seed.
- Fresh temperature is submitted every tick; `latest` remains gated by the controller period or framed-pulse period fallback with the original strict `>` cadence.
- Calibration reset/cancellation and stale-revision handling retain their original relative order.
- Stale, lid, and safety paths still command auger-off before terminal feedback and safety trace.
- A framed hardware transition is commanded before completion delivery, terminal feedback, observation dispatch, and trace completion.
- Runner result normalization, controller/fan state mutation, control persistence, trace update, calibration trace, and checkpoint retain their original timestamps and ordering.
- Manual and lid timing comparisons retain the original `<`/`>=` boundaries.
- Lid detection/toggle, fan PWM authority, smoke-plus behavior, model-observation reconciliation, and end-of-tick trace flush retain their original positions.
- Exceptions still propagate; no phase catches a failure and continues with partial hardware authority.

## Moved/deleted inline blocks

The former monolithic `on_tick` blocks for configuration/session adoption, controller-update reconfiguration, safety/calibration publication, runner cadence/result normalization, inhibition decisions, framed scheduling, auger commands, framed dispatch/feedback, lid/fan work, and trace flush were moved once into the named phase methods and deleted from `on_tick`. `on_tick` contains only the public callback signature, explicit typed phase values, and the ordered phase calls.

No observation/learning extraction, activation/refit/teardown extraction, calibration consolidation, or later-task helper cleanup was performed.

## LSP caller inventory

Before production edits, LSP reported exactly two references to `HoldMode.on_tick`:

1. The public method definition in `controller/runtime/modes/hold.py`.
2. The direct smoke caller in `tools/smoke_acados_hold.py:87`, which continues to call `mode.on_tick(clock_now(), 180.0, grill.get_output_status())` without migration.

The runtime registry invokes mode callbacks through the unchanged public callback contract. Final post-edit LSP refresh retains exactly the definition and direct smoke caller above.

## Files

- Modified `controller/runtime/modes/hold.py`.
- Modified `tests/unit/runtime/test_hold_applied_output.py` so the existing observable contract leaves an expired nonzero override for public `on_tick` discovery instead of invoking the private release callback.
- Added `.superpowers/sdd/2026-08-12-mpc-runtime-decomposition/task-13-report.md`.

## Parent validation commands

The parent ran the required gates with these exact supplied outcomes:

```text
uv run pytest -q -n0 \
  tests/unit/runtime/test_hold_applied_output.py::test_manual_release_reseeds_before_fresh_controller_authority

uv run pytest -q -n0 tests/unit/runtime/test_hold_orchestration.py

uv run pytest -q -n0 \
  tests/unit/runtime/test_hold_applied_output.py \
  tests/unit/runtime/test_hold_orchestration.py \
  tests/unit/runtime/test_hold_pulse_scheduler.py \
  tests/unit/runtime/test_hold_control_trace.py \
  tests/unit/runtime/test_hold_calibration.py \
  tests/unit/runtime/test_hold_controller_advisories.py

uv run pytest -q -n0 --tb=no \
  tests/unit/runtime/test_hold_*.py tests/unit/runtime/test_threaded_runner.py

uv run ruff check \
  controller/runtime/modes/hold.py \
  tests/unit/runtime/test_hold_applied_output.py \
  tests/unit/runtime/test_hold_orchestration.py
```

Results:

```text
Frozen-status PWM regressions: 3 GREEN
Focused Hold contract set: 7 failed, 205 passed
Full Hold aggregate: 7 failed, 254 passed
Focused Ruff: clean
```

Parent LSP references retained the unchanged public `HoldMode.on_tick` callback inventory. Diagnostics initially reported one Task 13-added error at the calibration trace schema boundary in addition to 84 pre-existing file errors. `_trace_calibration_result` now narrows the validated runner action to the exact `CalibrationTracePayload` `Literal` union with an explicit local cast. The final diagnostic refresh reports 83 pre-existing file errors and no Task 13-added diagnostics.

Expected removed RED if GREEN:

```text
tests/unit/runtime/test_hold_applied_output.py::test_manual_release_reseeds_before_fresh_controller_authority
```

Expected remaining intentional later-task REDs (unchanged):

1. `tests/unit/runtime/test_hold_orchestration.py::test_partial_setup_failures_still_close_the_runner_once`
2. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[checkpoint]`
3. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[persistence-flush]`
4. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[refit]`
5. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[runner-stop]`
6. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[success]`
7. `tests/unit/runtime/test_hold_orchestration.py::test_teardown_orders_cleanup_and_owns_each_resource_at_most_once[trace-close]`

Added RED set versus Task 12: `[]`. Removed RED: `tests/unit/runtime/test_hold_applied_output.py::test_manual_release_reseeds_before_fresh_controller_authority`.

## Concerns

No known functional blocker remains. The full Hold aggregate is intentionally RED only for the exact seven later teardown/partial-setup node IDs above. The remaining 83 `hold.py` diagnostics are pre-existing and outside Task 13.
