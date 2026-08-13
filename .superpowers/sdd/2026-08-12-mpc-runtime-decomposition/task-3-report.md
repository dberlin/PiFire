# Task 3 Report: MPC Pair Factory and Descriptor Boundary

## Status

Implementation completed without running tests, builds, linters, formatters, or coverage, per the worker constraint. Parent validation remains required.

## Files

- `controller/mpc_factory.py` (new): concrete `MpcPairConfiguration`, `NativeTiming`, `OwnedMpcPair`, and `MpcPairFactory` ownership boundary.
- `controller/mpc.py`: factory composition, initial build, descriptor restore, automatic candidate adoption, snapshot restore, rollback authorization, and teardown candidate migration.
- `controller/mpc_core.py`: supports governed resource and authorization ownership used by the pair boundary.
- `controller/model_learning/activation.py`: concrete pair annotations, complete descriptor ownership identity, and persisted activation-record legacy migration.
- `controller/mpc_snapshot.py`: explicit legacy pair-descriptor migration at the validated v4 decode boundary.
- `controller/runtime/runner.py`: prepared transition now carries `OwnedMpcPair`.
- `blueprints/api/routes.py`: operator activation uses shared factory restore/build/dry-solve behavior and migrates raw checkpoint descriptors; no Flask imports were introduced under `controller/`.
- `tests/unit/mpc/test_mpc_factory.py` (new): real factory branch, ownership, digest, restore, timing, and cleanup tests.
- `tests/unit/mpc/_solver_fixtures.py`, `tests/unit/mpc/test_model_activation.py`, `tests/unit/mpc/test_mpc_model_snapshot.py`, `tests/unit/mpc/test_mpc_refit.py`, runtime tests, and `tests/web/test_api_model_evidence.py`: concrete owner fixture, ownership lifecycle, durable roundtrip, raw legacy migration, and callsite cutover coverage.

## LSP inventory

LSP references were collected before/while modifying the exported `controller.mpc.Controller` (69 current references), `GreyControlPairDescriptor` (119 current references), the removed legacy activation owner (52 historical references), and `MpcCore.bind_resources`. Production, unit/runtime, web, tool, and experiment callsites were reviewed. The LSP retained a stale factory snapshot after reload, so the apparent empty `MpcPairFactory` result was rejected and its import/callsite inventory was confirmed with repository search. `controller/mpc.py` no longer imports or directly instantiates `MpcCore`, `GreyBoxEKF`, `GreyBoxKF`, or `AcadosGreyBoxMPC`; descriptor restoration delegates to `MpcPairFactory.restore`.

## Ownership state transitions

1. `MpcPairFactory.build` creates an initially unauthorized gate, constructs the numerical core, derives and validates the native descriptor/digest, creates one `OwnedMpcPair`, then optionally authorizes it.
2. `MpcPairFactory.adopt` accepts an already request-owned typed estimator/solver pair, binds it into one core, validates the descriptor, and transfers ownership only on success.
3. `MpcPairFactory.restore` reconstructs typed native configuration from the descriptor, builds unauthorized, and rejects/tears down if the rebuilt descriptor differs byte-for-byte in value.
4. `OwnedMpcPair.authorize_output` and `revoke_output` mutate the pair-owned gate; `close` first revokes and then closes the core exactly once.
5. Controller installation revokes the incumbent, installs an inert candidate, authorizes only after the durable activation phase, retains the incumbent as rollback owner, and closes displaced owners only after ownership transfer.

## Failure cleanup matrix

| Failure point | Request-owned cleanup | Incumbent |
| --- | --- | --- |
| estimator construction | no resource created | untouched/usable |
| native construction after estimator | estimator closed once | untouched/usable |
| core/adopt construction | solver then estimator closed once | untouched/usable |
| descriptor/native configuration mismatch | complete candidate closed solver then estimator once | untouched/usable |
| restore descriptor mismatch | reconstructed pair closed once | active pair unchanged |
| dry solve/finite validation/timing exception | request-owned pair closed once | untouched/usable |
| candidate install rejection | candidate closed; incumbent remains active | usable |
| activation failure after inert install | failed candidate closed; retained rollback reauthorized | restored/usable |
| repeated owner close | no-op after first close | unrelated |

## Migrated callsites

- Controller startup pair construction.
- Startup activation recovery and rollback reconstruction.
- Persisted model snapshot restoration.
- Passive/teardown candidate adoption after off-path construction.
- Operator activation route incumbent/candidate reconstruction and dry solve.
- Runner prepared-pair transition contract.
- Unit/runtime/web pair fixtures and stale restore monkeypatch seams.

## Tests and branches

`tests/unit/mpc/test_mpc_factory.py` covers EKF/KF selection, authorized/unauthorized ownership, generated descriptor/digest equality, native-build partial cleanup, configuration mismatch cleanup order, exact restore, unsupported estimator/solver kinds, deterministic timing, solve exception, non-finite sequence/objective, short sequence, adopted resource transfer, adopt failure cleanup, malformed descriptor numerics, invalid temperature cleanup, and idempotent close. The parent must measure the required branch percentage.

## Risks

- Existing broader suites still contain Task 2-era monkeypatches of `controller.mpc_core`; parent validation should identify any fixture that must patch the composed factory instead.
- The live-learning orchestration contract still invokes typed factory component builders and timing probe callbacks before the resulting pair is adopted by the same factory. Construction is no longer direct in `Controller`, but a later cleanup may fold that orchestration callback surface into a single factory request.
- Workspace LSP reports unresolved environment imports (`numpy`, `pytest`) and substantial pre-existing diagnostics; focused semantic errors in the new factory/test were cleared apart from those environment imports.
- After concurrent worker edits, the Python LSP continued to expose a stale pre-fix `controller/mpc_factory.py` snapshot even after reload; exported factory references were therefore confirmed with the repository fallback search rather than trusted as an empty LSP result.

## Parent commands

```bash
uv run pytest -q tests/unit/mpc/test_mpc_factory.py
uv run pytest -q tests/unit/mpc/test_model_activation.py tests/unit/mpc/test_mpc_public_contract.py tests/unit/mpc/test_mpc_model_snapshot.py tests/unit/runtime/test_threaded_runner.py tests/web/test_api_model_evidence.py
uv run pytest -q --cov=controller.mpc_factory --cov-branch --cov-report=term-missing tests/unit/mpc/test_mpc_factory.py
uv run pyright controller/mpc_factory.py controller/mpc.py controller/runtime/runner.py
```

## Fix round 1

- Corrected snapshot state transfer at the factory boundary. Learned parameters now produce a descriptor through `MpcPairFactory.descriptor`; restore verifies that the durable active-pair descriptor exactly describes the merged active model before constructing resources. The restored pair's `MpcCore.config`, estimator, solver, and checkpoint identity therefore advance together.
- Migrated `_bare_mpc_pair_owner` to seed one real `_active_control_pair` and use its core directly. Activation tests no longer rely on a parallel numerical namespace or fallback authorization field.
- Replaced the structural activation-pair seam with concrete `OwnedMpcPair` annotations and runtime checks. The descriptor JSON decoder's pre-existing `Mapping[str, object]` boundary remains, while the new activation ownership path contains no `object`, cast, suppression, or compatibility alias.
- Expanded public factory behavioral coverage for every configuration identity validator, owner type validation, KF configuration/build/restore, invalid authorization, authorized adoption, descriptor reconstruction mismatch, post-construction validation failure, invalid timing, dual cleanup failures, and non-closable protocol components.
- Extracted `MpcCore.native_configuration` so numerical construction and descriptor-only mapping share the exact same native configuration transform instead of duplicating constants.
- Per parent instruction, this worker ran no tests, builds, linters, formatters, or coverage commands during the fix round.

## Fix round 2

- Removed the parallel `_next_cook_descriptor` snapshot seam. `OwnedMpcPair.descriptor` is now the single authoritative active identity and advances in the same `_adopt_model` operation as the pair-owned configuration.
- Snapshot writing serializes `active_control_pair.descriptor` directly. It no longer substitutes a separately synthesized learned descriptor.
- Added typed checkpoint metadata for origin, policy, and rollback digest/generation. Restore hydrates those fields from the validated v4 document so a write after restore preserves the durable ownership metadata exactly.
- Refit adoption supplies the factory-created descriptor and exact origin/policy while atomically advancing the active pair. Generation-only descriptor rotation remains valid because restore reconstructs using the durable descriptor generations while comparing the same model/native configuration.
- Per parent instruction, this worker ran no tests, builds, linters, formatters, or coverage commands during the fix round.

## Fix round 3

- Parent validation reported 12 calibration-runtime failures from the removed private `Controller._build_for` monkeypatch; the preceding 280 tests passed.
- Migrated `tests/unit/mpc/test_mpc_calibration_runtime.py` to replace the controller's imported public `MpcPairFactory` constructor with a `functools.partial` that supplies the fixture's fake EKF/KF factories and native policy factory. The real factory still owns configuration normalization, descriptor validation, `MpcCore` construction, authorization, and pair lifecycle.
- The fake policy now retains the complete `GreyBoxMPCConfig`, allowing the real factory's descriptor/native-configuration validation to execute instead of bypassing it through a private controller wrapper.
- Searched `tests/` and `controller/`; no `_build_for` references remain.
- Parent should rerun `uv run pytest -q tests/unit/mpc/test_mpc_calibration_runtime.py` and then the broader MPC suite. Per instruction, this worker ran no tests, builds, linters, formatters, or coverage commands.

## Fix round 4

- Parent validation reported the same 12 calibration-runtime failures with 308 broader tests passing because the migrated fixture still requested the obsolete zero-delay configuration.
- Updated the fixture to request the generated controller's exact eight-delay contract. The fake estimator now emits all eight delayed-load states followed by chamber temperature and disturbance, matching the native `state_size == 10` boundary.
- The fake policy continues to size command and residual sequences from the complete validated `GreyBoxMPCConfig.horizon_steps`; no production validation was relaxed.
- Parent should rerun `uv run pytest -q tests/unit/mpc/test_mpc_calibration_runtime.py` and then the broader MPC suite. Per instruction, this worker ran no tests, builds, linters, formatters, or coverage commands.

## Fix round 5

- Parent validation isolated the remaining mechanical collection/runtime failure: the factory-fixture migration removed the still-used `dataclasses.replace` import.
- Restored that import only; unchanged calibration tests at the native-configuration mutation callsites retain their original behavior.
- Per instruction, this worker ran no tests, builds, linters, formatters, or coverage commands.

## Fix round 6

- Corrected factory dry-solve input state to eight zero delayed-load states, chamber temperature, and zero disturbance. Timing limits now derive from the owned pair's restored `control_period`, not factory startup settings.
- Expanded the durable descriptor configuration to the strict native contract plus normalized `control_period`, `est_q_temp`, `est_q_dist`, and `est_r_meas`. `model_digest` remains the native-model digest; `ownership_digest` covers the full estimator/solver identity and generations. Restore uses descriptor values exclusively and rejects missing, extra, malformed, or changed fields before authorization.
- Added explicit native-only legacy descriptor migration at all durable decode boundaries: v4 snapshots, persisted activation records, and raw operator API checkpoints. All incumbent/candidate/rollback identities are migrated before equality or restore.
- Refit acceptance now adopts the prepared estimator/solver as one complete factory-owned pair and atomically swaps its descriptor, core, configuration, origin, policy, and rollback identity. Closed or otherwise invalid candidate owners cannot replace the incumbent.
- Activation installation revokes the incumbent before the candidate becomes active, retains exactly one rollback owner, closes a displaced rollback on successive activation, and reauthorizes only the selected owner during compensation/rollback. Controller construction failure closes the complete pair through `OwnedMpcPair`.
- Snapshot restore now hydrates validated challenger, candidate, window, cook-refit, activation, failure, evidence, origin/policy, and rollback state so the next snapshot round-trips the complete durable checkpoint.
- Added focused ownership/digest/state-layout/restored-cadence/legacy migration/successive activation/refit-transfer/snapshot-roundtrip/API tests. LSP reference inventory was refreshed for `Controller` (69 references) and `GreyControlPairDescriptor` (119 references); the factory server snapshot remained stale after reload, so its production/test import inventory was confirmed with the repository fallback search and recorded as a tooling risk.
- Per instruction, this worker ran no tests, builds, linters, formatters, or coverage commands. Parent validation commands remain those above.
