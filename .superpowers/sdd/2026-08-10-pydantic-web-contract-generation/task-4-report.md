# Task 4 Report

## Result

DONE

Implementation commit: `1b3c5e52338a` (`xztxqnxnoswo`)

## Changed paths

Backend contracts, producers, boundaries, persistence, and focused tests:

- `common/web_contracts/learning.py` (created)
- `common/web_contracts/registry.py`
- `common/web_contracts/core.py`
- `common/controller_model_state.py`
- `controller/model_learning/report.py`
- `controller/model_learning/activation.py`
- `controller/pid_sp_learning.py`
- `controller/mpc.py`
- `blueprints/api/routes.py`
- `tests/unit/common/test_controller_model_state.py`
- `tests/unit/controller/test_learning_report.py`
- `tests/unit/controller/test_pid_sp_learning.py`
- `tests/unit/mpc/test_model_activation.py`
- `tests/web/test_api_model_evidence.py`
- `tests/web/test_api_mpc_calibration.py`
- `tests/web/test_api_pid_sp_learning.py`

Generated contract artifacts and frontend clean cutover:

- `web-react/schema/contracts/core.schema.json`
- `web-react/schema/contracts/learning.schema.json` (created)
- `web-react/schema/contracts/manifest.json`
- `web-react/src/helpers/contracts/core.gen.ts`
- `web-react/src/helpers/contracts/learning.gen.ts` (created)
- `web-react/src/helpers/modelEvidence/types.ts` (deleted)
- `web-react/src/helpers/pidSpLearning/types.ts` (deleted)
- `web-react/src/helpers/modelEvidence/modelEvidenceApi.ts`
- `web-react/src/helpers/pidSpLearning/pidSpLearningApi.ts`
- `web-react/src/components/dashboard/LearningPanel.tsx`
- `web-react/src/components/dashboard/learning/MpcLearningView.tsx`
- `web-react/src/components/dashboard/learning/PidSpLearningView.tsx`
- `web-react/tests/unit/components/dashboard/Dashboard.test.tsx`
- `web-react/tests/unit/components/dashboard/LearningPanel.test.tsx`
- `web-react/tests/unit/components/dashboard/MpcLearningView.test.tsx`
- `web-react/tests/unit/components/dashboard/PidSpLearningView.test.tsx`
- `web-react/tests/unit/helpers/pidSpLearningApi.test.ts`
- `web-react/tests/e2e/dashboard-panel.spec.ts`

LSP references were collected before removing or replacing the exported handwritten DTOs and `ActivationRequest`. They proved the direct consumers listed above, plus the runtime `controller/mpc.py` and its activation tests. The final scoped LSP diagnostic request for the generated learning contract, both adapters, all three learning panel/view files, and the changed Python contract/producer/route files returned `OK`.

## Contract and behavior result

`common.web_contracts.learning` now owns the MPC evidence report, activation and rollback request/acknowledgement unions, MPC calibration command/response, PID-SP report, gates, confirmation, identifier/predictor reports, failures, and separate discriminated checkpoint and live predictor model unions. `PidSpCheckpointModel` requires durable `revision` and optionally emits `identified_at_f`; `PidSpPredictorModel` has neither durable field. The generated TypeScript exports both unions and both action acknowledgement unions by name.

MPC and PID-SP report producers validate their exact JSON projections with Pydantic before caching/serving them. GET routes independently validate and serialize those models. Activation, rollback, and calibration request/response boundaries use the same Pydantic-owned contracts and retain the existing response envelopes and authority flow. MPC report revision hashing still covers the exact emitted JSON, and PID-SP revision hashing remains canonical across every visible member.

The targeted PID-SP decoder remains runtime-strict for non-finite numbers, boolean-as-number rejection, schema version, discriminated FOPDT/IPDT forms, durable-versus-live model members, and live/coherence rules. Its return annotation and every view/test DTO import now point at `learning.gen.ts`. `LearningPanel` behavior was unchanged; only imports/types were adapted.

`ControllerModelStore.load_strict()` now always rereads and validates persistence, even when the shared latest cache is warm. Legacy `load()` keeps its cache behavior. The regression proves an interrupted/malformed stored PID-SP member raises rather than returning cached state, and the API integration proves that corruption remains HTTP 422 while true absence remains the idle HTTP 200 report with `checkpoint: null`.

## RED evidence

Warm-cache corruption reproduction:

```bash
uv run pytest -q tests/unit/common/test_controller_model_state.py::test_strict_load_revalidates_persistence_even_when_shared_cache_is_warm
```

Observed `Failed: DID NOT RAISE ValueError` because `_load()` returned `_latest_owned()` without reading persistence.

Initial contract ownership RED:

```bash
uv run pytest -q tests/unit/controller/test_learning_report.py::test_model_evidence_report_contract_preserves_the_real_canonical_projection
```

Collection failed with `ModuleNotFoundError: No module named 'common.web_contracts.learning'`.

API boundary RED:

```bash
uv run pytest -q \
  tests/web/test_api_model_evidence.py::test_report_route_rejects_a_producer_projection_outside_the_pydantic_contract \
  tests/web/test_api_model_evidence.py::test_calibration_route_uses_the_existing_error_envelope_for_pydantic_request_failures \
  tests/web/test_api_model_evidence.py::test_activate_route_treats_typed_contract_failures_as_unprocessable
```

Observed three failures: malformed report returned 200 instead of 422, invalid calibration returned 400 instead of the Pydantic 422 envelope, and an invalid digest returned 409 instead of 422.

PID-SP GET boundary RED:

```bash
uv run pytest -q tests/web/test_api_pid_sp_learning.py::test_report_route_rejects_a_projection_outside_the_pydantic_contract
```

Observed malformed schema version returned 200 instead of 422. The companion warm-cache API regression passed only after the strict-load fix.

Generated drift RED:

```bash
cd web-react
bun run gen:types:check
```

Observed changed core/manifest artifacts and missing `learning.schema.json` and `learning.gen.ts`; exit 1.

## GREEN evidence

Required focused backend command:

```bash
uv run pytest -q tests/unit/common/test_controller_model_state.py tests/unit/controller/test_learning_report.py tests/unit/controller/test_pid_sp_learning.py tests/web/test_api_model_evidence.py tests/web/test_api_pid_sp_learning.py
```

```text
155 passed in 4.19s
EXIT=0
```

Supplemental directly changed MPC action boundaries:

```bash
uv run pytest -q tests/web/test_api_mpc_calibration.py tests/unit/mpc/test_model_activation.py
```

```text
32 passed in 4.64s
EXIT=0
```

Required focused frontend command:

```bash
cd web-react
bunx rstest run tests/unit/helpers/pidSpLearningApi.test.ts tests/unit/components/dashboard/MpcLearningView.test.tsx tests/unit/components/dashboard/PidSpLearningView.test.tsx tests/unit/components/dashboard/LearningPanel.test.tsx
```

```text
4 files passed
101 tests passed in 2.70s
EXIT=0
```

Panel browser verification:

```bash
bunx playwright test --project=panel tests/e2e/dashboard-panel.spec.ts
```

```text
14 passed (13.4s)
EXIT=0
```

The browser command emitted the pre-existing proxy errors caused by no PiFire backend at `http://localhost:5000`; mocked panel scenarios still completed successfully.

Generated drift:

```bash
bun run gen:types:check
```

```text
Pydantic web contract artifacts are up to date.
src/helpers/settings/settingsDefaults.gen.ts is up to date.
Generated web contract TypeScript is up to date.
EXIT=0
```

## Typecheck integration deferral

The full `bun run typecheck` was executed. It reported only concurrent Task 8 missing-export errors in admin/logs/tuner/update helper types and their consumers. The output contained no `learning.gen.ts`, `modelEvidence`, `pidSpLearning`, `LearningPanel`, `MpcLearningView`, or `PidSpLearningView` error. Per Main's instruction, those unrelated Task 8 files were not changed; full typecheck is deferred to the serialized integration gate after that concurrent wave lands. Scoped LSP diagnostics for every Task 4 TypeScript file returned `OK`.

## Self-review

The handwritten learning DTO files are deleted, all direct consumers import generated contracts, and no aliases, re-exports, duplicate wire declarations, PID-SP mutation API, or MPC/PID-SP authority changes were introduced. The PID-SP canonical report wrapper remains internal and immutable; only its validated wire payload is public. Durable checkpoint and live predictor shapes cannot substitute for one another. Existing calibration, activation, rollback, report revision, persistence, structured failure, and HTTP absence/corruption semantics are covered by the focused tests above.

## Concerns

No known Critical or Important Task 4 defect remains. Full repository TypeScript typecheck is intentionally deferred solely for the concurrent Task 8 integration described above.

## Fix round 1: absent PID-SP checkpoint provenance

Implementation commit: `427a255c` (`tqqnlynp`)

RED:

```bash
uv run pytest -q tests/web/test_api_pid_sp_learning.py::test_report_route_omits_absent_checkpoint_provenance
```

```text
FAILED: response checkpoint contained {"identified_at_f": None}
1 failed in 3.51s
```

GREEN, run from a clean Jujutsu workspace at the fix commit because the shared working copy contained the intentionally incomplete concurrent registry/core integration:

```bash
uv run pytest -q tests/unit/controller/test_pid_sp_learning.py tests/web/test_api_pid_sp_learning.py
```

```text
77 passed in 6.44s
```

Strict frontend decoder regression:

```bash
bunx rstest run tests/unit/helpers/pidSpLearningApi.test.ts
```

```text
Test Files 1 passed
Tests 36 passed
Duration 248ms
```

The backend now omits `identified_at_f` when a valid legacy/current checkpoint has no provenance. The retained strict decoder continues to reject a present `identified_at_f: null`.

## Fix round 2: restore PID-SP status type import

RED:

```text
uv run ruff check controller/pid_sp_learning.py
F821 Undefined name `PidSpLearningStatus`
```

GREEN:

```text
uv run ruff check controller/pid_sp_learning.py
All checks passed!

uv run pytest -q tests/unit/controller/test_pid_sp_learning.py
60 passed in 1.90s
```

## Fix round 3: complete the activation report contract

Source commit: `5f9260b6e7ed` (`lvkptwzr`)

Generated artifacts commit: `b9cdbd0b03a1` (`lswvwuqm`)

RED:

```text
uv run pytest -q tests/unit/mpc/test_model_evidence_report.py
32 failed: ActivationReport rejected decision_id and incumbent_digest as extra fields
```

GREEN:

```text
uv run pytest -q tests/unit/mpc/test_model_evidence_report.py
42 passed in 2.42s

bun run gen:types:check
Pydantic web contract artifacts are up to date.
src/helpers/settings/settingsDefaults.gen.ts is up to date.
Generated web contract TypeScript is up to date.
```

`ActivationReport` now owns the `decision_id` and `incumbent_digest` members already emitted by the authoritative model-evidence projection. The generated learning schema and TypeScript contract were regenerated from that corrected Pydantic source.

## Fix round 4: discard cached authority after schema-invalid success

RED:

```text
bunx rstest run tests/unit/components/dashboard/MpcLearningView.test.tsx -t "drops cached authority"
1 failed: the prior "Activate exact model" action remained in the document after a malformed HTTP 200 refresh
```

GREEN:

```text
bunx rstest run tests/unit/helpers/modelEvidenceApi.test.ts \
  tests/unit/components/dashboard/MpcLearningView.test.tsx \
  tests/unit/components/dashboard/LearningPanel.test.tsx
Test Files 3 passed
Tests 48 passed
Duration 2.31s

bun run typecheck
EXIT=0

bunx biome check src/components/dashboard/learning/MpcLearningView.tsx \
  tests/unit/components/dashboard/MpcLearningView.test.tsx
Checked 2 files. No fixes applied.

bunx playwright test --project=panel tests/e2e/dashboard-panel.spec.ts
14 passed (12.1s)
```

The query view now distinguishes a schema-invalid successful response from a transport/server refresh failure. A schema-invalid success ignores cached report authority, removes candidate/activation/rollback content, and disables its actions. The established network failure regression continues to retain prior report context while presenting the stale-data error and retry controls.

## Fix round 5: persist schema invalidation across failed polls

RED:

```text
bunx rstest run tests/unit/components/dashboard/MpcLearningView.test.tsx \
  -t "keeps schema-invalidated authority absent"
1 failed: a following HTTP 503 replaced the schema-error marker and exposed the cached activation action again
```

GREEN:

```text
bunx rstest run tests/unit/helpers/modelEvidenceApi.test.ts \
  tests/unit/components/dashboard/MpcLearningView.test.tsx \
  tests/unit/components/dashboard/LearningPanel.test.tsx
Test Files 3 passed
Tests 48 passed
Duration 2.43s

bun run typecheck
EXIT=0

bunx biome check src/components/dashboard/learning/MpcLearningView.tsx \
  tests/unit/components/dashboard/MpcLearningView.test.tsx
Checked 2 files. No fixes applied.

bunx playwright test --project=panel tests/e2e/dashboard-panel.spec.ts
14 passed (12.2s)
```

Once a decoded success violates the schema, the mounted MPC view retains a schema-invalidated state across subsequent network/server refresh failures. Cached candidate, activation, and rollback authority remain suppressed until a newly decoded valid report succeeds. The independent valid-to-network regression still preserves stale context when no schema violation preceded it.

## Fix round 6: enforce nonblank PID-SP failure fields

RED:

```text
bunx rstest run tests/unit/helpers/pidSpLearningApi.test.ts \
  -t "rejects schema mismatch"
2 failed: empty failure.code and failure.detail were accepted as successful reports
```

GREEN:

```text
bunx rstest run tests/unit/helpers/pidSpLearningApi.test.ts \
  tests/unit/helpers/modelEvidenceApi.test.ts \
  tests/unit/components/dashboard/PidSpLearningView.test.tsx \
  tests/unit/components/dashboard/MpcLearningView.test.tsx \
  tests/unit/components/dashboard/LearningPanel.test.tsx
Test Files 5 passed
Tests 107 passed
Duration 2.46s

bun run typecheck
EXIT=0

bunx biome check src/helpers/pidSpLearning/pidSpLearningApi.ts \
  tests/unit/helpers/pidSpLearningApi.test.ts
Checked 2 files. No fixes applied.

bunx playwright test --project=panel tests/e2e/dashboard-panel.spec.ts
14 passed (12.0s)
```

The strict PID-SP decoder now enforces the generated `NonBlankString` contract for both failure members. Valid structured error reports are unchanged; malformed successful responses still fail closed with `ok: false` and `data: null`.
