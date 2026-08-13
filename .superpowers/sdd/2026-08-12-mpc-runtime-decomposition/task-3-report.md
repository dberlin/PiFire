# Task 3 Report: MPC Pair Factory and Descriptor Boundary

## Status

Implementation completed without running tests, builds, linters, formatters, or coverage, per the worker constraint. Parent validation remains required.

## Files

- `controller/mpc_factory.py` (new): concrete `MpcPairConfiguration`, `NativeTiming`, `OwnedMpcPair`, and `MpcPairFactory` ownership boundary.
- `controller/mpc.py`: factory composition, initial build, descriptor restore, automatic candidate adoption, snapshot restore, rollback authorization, and teardown candidate migration.
- `controller/mpc_core.py`: supports governed resource and authorization ownership used by the pair boundary.
- `controller/model_learning/activation.py`: structural activation-pair protocol consumed by activation transactions.
- `controller/runtime/runner.py`: prepared transition now carries `OwnedMpcPair`.
- `blueprints/api/routes.py`: operator activation uses shared factory restore/build/dry-solve behavior; no Flask imports were introduced under `controller/`.
- `tests/unit/mpc/test_mpc_factory.py` (new): real factory branch, ownership, digest, restore, timing, and cleanup tests.
- `tests/unit/mpc/_solver_fixtures.py` and migrated activation/runtime/API tests: concrete owner fixture and callsite cutover.

## LSP inventory

LSP references were collected before/while modifying the exported `controller.mpc.Controller` (70 references), the legacy activation owner (52 references), and `MpcCore.bind_resources`. Production, unit/runtime, web, tool, and experiment callsites were reviewed. `controller/mpc.py` no longer imports or directly instantiates `MpcCore`, `GreyBoxEKF`, `GreyBoxKF`, or `AcadosGreyBoxMPC`; descriptor restoration delegates to `MpcPairFactory.restore`.

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
