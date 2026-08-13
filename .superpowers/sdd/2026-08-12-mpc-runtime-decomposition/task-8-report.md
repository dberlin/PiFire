# Task 8 Report: Thin MPC Controller Composition Root

## Status

**DONE_WITH_CONCERNS**

The Task 8 implementation and brief-focused behavioral verification are complete. The only concerns are the parent-owned final Ruff/LSP/coverage/reviewer gates, which this worker was explicitly instructed not to run. No known behavioral failure remains.

- Baseline commit: `3348ce15a5b0`
- Jujutsu change ID: `xvwzkkuztvmr`
- Description: `refactor(mpc): compose the controller from focused runtimes`
- The final content-addressed commit ID is returned after opening the required fresh empty working-copy commit; it cannot be embedded in the commit whose content determines that ID.

## Change summary

`controller.mpc.Controller` is now an explicit composition root over:

1. normalized configuration and fixed scalar settings;
2. `MpcCalibrationRuntime`;
3. `MpcPairFactory` and one configured, authorized initial `OwnedMpcPair`;
4. one `ModelPersistenceWorker`;
5. `ActivationRuntime`;
6. `GreyLearningRuntime`.

The pair factory receives named model-authority and policy-failure callbacks. Grey receives named public-boundary callbacks for the active pair, active numerical components, isolated configuration, isolated snapshot parameters/history, configuration synchronization, and trace append. Controller retains only public API orchestration, fixed public settings, required trace-result caches, and top-level closed state.

Constructor ownership remains local until transfer succeeds. If construction fails before `ActivationRuntime` owns the pair and persistence worker, Controller calls persistence `flush_and_stop()` and then closes the initial pair. If a later Grey construction/start boundary fails, Controller closes `ActivationRuntime`. Every cleanup failure is attached as a note while a bare re-raise preserves the original construction exception.

Top-level `close()` marks Controller closed before cleanup, attempts Grey first and Activation second, collects every failure, raises one `BaseExceptionGroup` only after all attempts, and makes every repeated call a no-op. Activation remains the sole persistence/native-pair cleanup owner after transfer.

Public numerical orchestration now obtains the current core only through `active_control_pair.core`: target updates core then calibration, output feedback updates core then calibration, and `update()` executes only core and caches diagnostics plus baseline/final allocation. Status uses current immutable owner projections instead of Controller shadow aliases.

Public activation orchestration synchronizes the Controller configuration and Grey role generation only after successful ownership transitions. `advance_activation()` compares the public role generation before and after the call, so its successful no-op result does not rotate learning identity or configuration. Restore uses exact generation synchronization. Confidence submission preserves the Task 7 optional `preceding_evidence=()` FIFO passthrough.

## Strict RED/GREEN evidence

Initial focused RED command:

```text
uv run pytest -q tests/unit/mpc/test_mpc_composition.py
6 failed, 1 passed in 2.12s
```

Exact REDs:

1. `test_activation_construction_failure_reverse_closes_untransferred_owners` — only the activation constructor event occurred; persistence and initial pair were leaked.
2. `test_grey_construction_failure_preserves_original_after_activation_cleanup_failure` — `activation-cleanup` replaced the original `grey-construction` exception.
3. `test_noop_activation_advance_does_not_rotate_learning_or_configuration` — Controller called Grey synchronization on a no-op advance.
4. `test_successful_authorization_synchronizes_generation_and_public_configuration` — Grey generation synchronized but public `cfg` remained the incumbent configuration.
5. `test_activation_confidence_preserves_preceding_fifo_evidence` — Controller rejected the `preceding_evidence` keyword with `TypeError`.
6. `test_close_attempts_grey_then_activation_and_aggregates_failures_once` — Controller raised a single chained `RuntimeError` instead of aggregating both close failures.

The already-correct initial-pair boundary passed: an initial pair build failure did not construct, close, or take ownership of injected persistence.

Focused composition GREEN:

```text
uv run pytest -q tests/unit/mpc/test_mpc_composition.py
8 passed in 2.06s
```

The added composition contracts cover explicit construction order, pair/persistence unwind, Grey failure unwind with cleanup failure, initial-pair failure, successful/no-op activation synchronization, Task 7 confidence FIFO delegation, close order, multi-failure aggregation, and repeated close.

## Focused verification

Final brief-focused aggregate:

```text
uv run pytest -q \
  tests/unit/mpc/test_mpc_composition.py \
  tests/unit/mpc/test_mpc_public_contract.py \
  tests/unit/mpc/test_mpc_controller.py \
  tests/unit/mpc/test_mpc_core.py \
  tests/unit/mpc/test_mpc_factory.py \
  tests/unit/mpc/test_mpc_calibration_runtime_unit.py \
  tests/unit/mpc/test_mpc_calibration_runtime.py \
  tests/unit/mpc/test_mpc_calibration.py \
  tests/unit/mpc/test_activation_runtime.py \
  tests/unit/mpc/test_model_activation.py \
  tests/unit/mpc/test_grey_learning_runtime.py \
  tests/unit/mpc/test_grey_online_learning.py \
  tests/unit/mpc/test_grey_learning_snapshot_migration.py \
  tests/unit/mpc/test_mpc_model_snapshot.py \
  tests/unit/mpc/test_mpc_refit.py \
  tests/unit/mpc/test_mpc_cook_history.py \
  tests/unit/mpc/test_update_mpc.py \
  tests/unit/mpc/test_model_evidence_report.py \
  tests/unit/mpc/test_mpc_closed_loop.py \
  tests/unit/mpc/test_mpc_integration.py \
  tests/unit/runtime/test_sync_runner.py \
  tests/unit/runtime/test_threaded_runner.py \
  tests/unit/runtime/test_hold_model_persistence.py \
  tests/unit/runtime/test_hold_control_trace.py \
  tests/unit/runtime/test_hold_calibration.py \
  tests/unit/runtime/test_hold_refit_trigger.py \
  tests/unit/runtime/test_controller_build_failure.py \
  tests/unit/runtime/test_fake_runner_signature_parity.py
757 passed in 24.88s
```

Actual Hold smoke:

```text
uv run python tools/smoke_acados_hold.py
acados Hold smoke passed: frames=2 samples=7 refit=insufficient revision=1
```

The smoke tool now counts its own successful calls through Controller's public `observe_frame` boundary. It does not reinterpret checkpoint `evidence.eligible`, whose semantics differ from completed-frame delivery.

## Removed symbol and callsite inventory

LSP references were collected before every affected Controller method/property edit. Important retained public reference counts included `estimator` 9, `mpc` 18, `set_target` 29, `install_candidate_pair_inert` 9, `authorize_candidate_pair` 8, `compensate_candidate_pair` 3, `submit_activation_confidence` 3, `restore_activation` 3, `activation_runtime_failure` 4, `rollback_activation` 2, `get_status` 6, `set_output` 10, `update` 32, and `close` 30. Their callers and signatures were preserved, with the Task 7 confidence keyword restored.

Removed symbol reference counts before migration (counts include declarations and internal references):

| Removed Controller symbol | LSP references before removal | External migration |
|---|---:|---|
| `_core` | 27 | none; internal uses moved to `active_control_pair.core` |
| `_set_point_c` | 3 | none |
| `_last_combustion_load` | 8 | four controller tests and one cook-history test now use public status |
| `_applied_combustion_load` | 3 | cook-history test now uses public status |
| `_x_hat` | 3 | none |
| `_last_raw_combustion_load` | 2 | none |
| `_last_equilibrium_load` | 2 | none |
| `_last_residual_load` | 2 | none |
| `_last_feasibility` | 3 | none |
| `_consecutive_policy_failures` | 2 | none |
| `_history` | 5 | bounded-history setup uses public active-pair/core history; Hold smoke uses public `cook_history()` |
| `_native_failure_diagnostics` | 1 | none; unused alias |
| `_normalized_forecast_failure` | 1 | none; unused wrapper |
| `_core_model_authority` | 2 | replaced by the named construction callback |
| `_handle_core_policy_failure` | 2 | replaced by the named construction callback |
| `_activation_configuration` | one definition, no reads | removed shadow copy |

Post-migration regex residue checks found zero references to these Controller aliases in `controller/mpc.py`, MPC tests, or tools. AST import residue found only the public `Controller` import from `controller.mpc`; no moved/private helper import remains. Controller contains no collaborator-private attribute access, `getattr`/`hasattr` dispatch, `Any`, broad `object`, casts, Flask import, activation-service import, compatibility alias, or duplicate owner.

## LOC and ownership outcome

- Baseline `controller/mpc.py`: 489 lines.
- Corrected Task 8 `controller/mpc.py`: 550 lines (`+61` from baseline).
- Controller methods: 53 total, 8 private.
- Remaining private methods are six named construction callbacks/projections plus two real cross-runtime ownership-transition helpers:
  - `_active_pair_for_learning`
  - `_active_learning_components`
  - `_learning_configuration`
  - `_snapshot_parameters_for_learning`
  - `_history_for_learning`
  - `_sync_learning_configuration`
  - `_synchronize_activation_transition`
  - `_activation_identity_changed`

The LOC increase is the explicit reverse constructor unwind, all-failures close aggregation, and committed-identity synchronization required at each activation wrapper. It replaces eleven shadow properties, an unused state copy, an unused wrapper, and two obsolete bound construction seams. Mutable collaborator state remains solely in `MpcCore`/`OwnedMpcPair`, `MpcCalibrationRuntime`, `ActivationRuntime`/its one persistence worker, and `GreyLearningRuntime`.

## Concerns and parent-owned gates

Per delegation constraints, this worker did not run project-wide tests, Ruff, LSP diagnostics, or an internal reviewer. The parent must run those once. Focused branch coverage was subsequently requested and is recorded below.

## Coverage gate round 1

The parent aggregate initially reported these RED gates:

- `controller/mpc.py`: 27/34 branches, 79.41%.
- `controller/model_learning/grey_runtime.py`: 198/220 branches, exactly 90.00% and therefore below the strict greater-than-90% gate.

Tests only were changed. Observable Controller composition contracts now cover four rejected transitions (`authorize_candidate_pair`, `compensate_candidate_pair`, `activation_runtime_failure`, and `rollback_activation`) plus the already-covered successful no-op `advance_activation` with unchanged role generation. Every case asserts the exact public return and that neither public configuration nor Grey generation synchronization changes.

The Grey direct public polling contract now covers an accepted confidence receipt that completes non-durably. `poll_learning_off_path()` raises `activation-confidence-not-durable`, preserves the incumbent owner, and closes the reviewed candidate during Grey cleanup. No production state or source assertion was added.

Affected direct suites:

```text
uv run pytest -q \
  tests/unit/mpc/test_mpc_composition.py \
  tests/unit/mpc/test_grey_learning_runtime.py
70 passed in 2.49s
```

The exact 28-file focused aggregate with branch coverage:

```text
762 passed, 10 warnings in 25.32s
controller/mpc.py: 31/34 branches = 91.18%
controller/model_learning/grey_runtime.py: 199/220 branches = 90.45%
```

Both brief-named modules now satisfy the strict greater-than-90% branch gate in the focused aggregate.

## Review fix: committed terminal activation transitions

The first code review found one Important defect: `ActivationRuntime` can return
`False` after publishing a new active pair or role generation. The composition
root gated configuration and Grey-generation synchronization only on the
Boolean result, leaving those projections stale after authorization lifecycle,
fallback, compensation, or restore-retirement failures.

Strict RED on the real runtime boundary:

```text
tests/unit/mpc/test_mpc_composition.py
3 failed, 9 passed in 0.51s
```

`Controller` now snapshots the public active pair and role generation before
each potentially committing call. Direct authorization, compensation, restore,
fallback, and rollback synchronize on success or a committed identity change
while preserving the delegated Boolean. `advance_activation()` intentionally
uses generation change only: its first inert-install stage changes the active
pair slot before authorization commits and must not publish candidate
configuration. Restore retains exact Grey synchronization.

The focused composition suite is now `14 passed`. The affected real-runtime
subset is `138 passed`. The exact 28-file aggregate is:

```text
764 passed, 1 deselected in 70.65s
controller/mpc.py: 32/34 branches = 94.12%
controller/mpc_core.py: 43/46 branches = 93.48%
controller/mpc_calibration.py: 70/70 branches = 100.00%
controller/model_learning/activation_runtime.py: 177/190 branches = 93.16%
controller/model_learning/grey_runtime.py: 199/220 branches = 90.45%
```

Ruff and LSP diagnostics are clean. The Hold smoke exits zero with
`frames=2 samples=7 refit=insufficient revision=1`. Fix-round review verdict:
**Spec PASS / Quality APPROVED**, with zero Critical, Important, or Minor
findings.
