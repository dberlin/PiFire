# Task 8 Report

## Result

DONE — source and tests implemented; shared registration, generation, and command execution remain deferred exactly as required for the concurrent wave.

Implementation commit: `cff915caeba5112bda99a4de79a83555c5b9d7b6` (`tyvpuyssktxwkxsswtntzqrpsqksvpol`)

Review fix commit: `3698076cc53a813d88b828a12294d038421b1cb6` (`kyutnyuykmmrnxrzyzszqvsyyormoxvp`)

Branch validation fix commit: `af5f2b62137b72d836a5e4e0eba004d7b0ae7eb8` (`mnxosvtxqsmzrwxryrlzoxvqrnwyrrlp`)

Initial report change: `qxmwwvxquoqytltsnytkypzlqqotwklw`

Report update change: `uqrmrzywvsnlwlzoporumqrzyntyrpmo`

Evidence report change: `srtlxlwmswxpynmnxxkxllsllomnvzlp`

## Implemented

- Added strict operations-domain Pydantic contracts in `common/web_contracts/operations.py` for admin system information/actions/settings/backups, updater state/actions/status/build-log JSON, tuner requests/lifecycle/readings/coefficients/profiles/auto-status, and log-family metadata.
- Kept `/etc/os-release` extensibility isolated to `OsInfo`, whose extra values are still strict strings. Every other operations model forbids extra members.
- Added finite-number, nonnegative count/offset, discriminated literal, sparse omission, explicit-null, and three-distinct-segment validation.
- Strictly validate JSON requests before side effects and validate/serialize success and error response data at the admin, updater, and tuner route boundaries.
- Preserved the existing `OK`/`Error` envelope spelling, HTTP statuses, refusal tokens/details, updater launch/status behavior, admin command gating, tuner open/close idempotency and cancellation flow, absent tuner readings as `null`, and sparse admin-setting responses.
- Migrated frontend wire imports and direct consumers to the future `helpers/contracts/operations.gen.ts` module.
- Retained handwritten `AdminResult<T>`, `UpdateResult<T>`, and `TunerResult<T>` browser/transport normalizers.
- Retained handwritten `LogDelta`, byte-range parsing, UTF-8 byte offsets, rotation handling, whole-text reads, and download URLs. Only `/api/admin/logs` family metadata moved to generated wire types.
- Added contract tests for strict extras, OS-release extensibility, sparse/null settings, finite tuner values, and segment completeness, plus focused route parity tests for strict requests and log metadata without streamed text.
- Captured the combined direct-consumer import in `web-react/src/components/wizard/InstallProgress.tsx`: Task 8 owns `SystemAction` from `operations.gen.ts`; Task 7 owns `InstallStatus` from `wizard.gen.ts` and explicitly delegated this shared path to Task 8's commit.

## Deferred registry entry

Add the following imports from `common.web_contracts.operations` to `common/web_contracts/registry.py`, then add the bundle in sorted bundle-name order:

```python
from .operations import (
    AdminSettings,
    AdminSettingsUpdate,
    AdminState,
    AutoStatus,
    AutoStatusRequest,
    BackupCreateRequest,
    BackupCreated,
    BackupListing,
    BackupRestoreRequest,
    BackupRestored,
    BuildLog,
    CoefficientPoint,
    Coefficients,
    CoefficientsRequest,
    CpuInfo,
    EmptyOperationRequest,
    FactoryResetResponse,
    HardwareInfo,
    LogFamily,
    LogsDeleted,
    LogsMetadata,
    MaintenanceActionRequest,
    MaintenanceActionResponse,
    NetworkInterface,
    OsInfo,
    ProfileInput,
    SavedProfile,
    SystemActionRequest,
    SystemActionResponse,
    SystemInfo,
    TrReading,
    TunerPoint,
    TunerSession,
    TunerSessionRequest,
    UpdateBranchRequest,
    UpdateCheck,
    UpdateLog,
    UpdateStarted,
    UpdateState,
    UpdateStatus,
)
```

```python
ContractBundle(
    name="operations",
    models=(
        AdminSettings,
        AdminSettingsUpdate,
        AdminState,
        AutoStatus,
        AutoStatusRequest,
        BackupCreateRequest,
        BackupCreated,
        BackupListing,
        BackupRestoreRequest,
        BackupRestored,
        BuildLog,
        CoefficientPoint,
        Coefficients,
        CoefficientsRequest,
        CpuInfo,
        EmptyOperationRequest,
        FactoryResetResponse,
        HardwareInfo,
        LogFamily,
        LogsDeleted,
        LogsMetadata,
        MaintenanceActionRequest,
        MaintenanceActionResponse,
        NetworkInterface,
        OsInfo,
        ProfileInput,
        SavedProfile,
        SystemActionRequest,
        SystemActionResponse,
        SystemInfo,
        TrReading,
        TunerPoint,
        TunerSession,
        TunerSessionRequest,
        UpdateBranchRequest,
        UpdateCheck,
        UpdateLog,
        UpdateStarted,
        UpdateState,
        UpdateStatus,
    ),
    typescript_output="operations.gen.ts",
),
```

The referenced `Reading`, `BackupKind`, `SystemAction`, `MaintenanceAction`, `Segment`, and `FiniteNumber` aliases are emitted through the registered models; they are not separate registry entries.

## Deferred generated outputs

The serialized integration wave must generate and commit exactly:

- `web-react/schema/contracts/operations.schema.json`
- `web-react/src/helpers/contracts/operations.gen.ts`
- `web-react/schema/contracts/manifest.json` entry: `"operations.schema.json": "operations.gen.ts"`

No registry, exporter, manifest, schema, or `*.gen.ts` path was edited by Task 8.

## Static review remediation

The required post-implementation review found three Important boundary regressions. Review fix commit `3698076cc53a813d88b828a12294d038421b1cb6` resolves all three:

- Admin empty-object actions now distinguish an absent body from present malformed, falsy, or non-object JSON before factory reset or log deletion can execute.
- Updater empty-object actions make the same distinction before status mutation or process launch.
- Strict operation error details now retain the existing `log` member, preserving unknown log-family responses as JSON 404s rather than response-validation failures.

## Review fix round 2

Branch request validation RED:

```bash
uv run pytest -q tests/web/test_api_update.py::test_change_branch_rejects_invalid_json_before_update_discovery
```

```text
FFFF                                                                     [100%]
Expected {"data": null, "result": "Error", "message": "bad_request"}.
Received {"data": {"branches": ["main", "dev"]}, "result": "Error", "message": "invalid_branch"}.
4 failed in 3.37s
EXIT=1
```

The failure also recorded `get_update_data(settings)` for every invalid request,
proving malformed, non-object, and extra-bearing input reached branch discovery.

After returning the `_json_request(UpdateBranchRequest)` validation response
before reading settings or calling `get_update_data`, focused GREEN:

```bash
uv run pytest -q tests/web/test_api_update.py::test_change_branch_rejects_invalid_json_before_update_discovery
```

```text
4 passed in 3.46s
EXIT=0
```

Full focused updater route suite:

```bash
uv run pytest -q tests/web/test_api_update.py
```

```text
38 passed in 3.71s
EXIT=0
```

## Verification

The initial concurrent wave intentionally did not run tests, formatters, linters,
builds, typecheck, Playwright, or generation. Review fix round 2 subsequently
ran only the focused updater commands recorded above. The brief's combined
commands otherwise remain unrun verbatim:

```bash
uv run pytest -q tests/web/test_api_admin_system.py tests/web/test_api_admin_backups.py tests/web/test_api_admin_maintenance.py tests/web/test_api_admin_log_families.py tests/web/test_api_update.py tests/web/test_api_tuner.py tests/web/test_api_tuner_auto.py
cd web-react
bunx rstest run tests/unit/helpers/admin tests/unit/helpers/update tests/unit/helpers/tuner tests/unit/helpers/logs
bunx playwright test tests/e2e/admin.spec.ts tests/e2e/update.spec.ts tests/e2e/tuner.spec.ts tests/e2e/events.spec.ts
bun run gen:types:check
bun run typecheck
```

The new direct model suite and the strengthened log-view error regression must
also be run after registration/generation:

```bash
uv run pytest -q tests/unit/common/web_contracts/test_operations.py tests/web/test_api_admin_logs_view.py
```

Static LSP diagnostics were clean for `common/web_contracts/operations.py` and the modified admin/update/tuner Python routes, aside from pre-existing informational hints about `os.system` and an intentionally ignored tuner chart-label return. Frontend execution/type diagnostics were not run by Task 8; the serialized integration wave later supplied `operations.gen.ts` outside the Task 8 commits.

## Concerns

- No Task 8 integration blocker is currently known; registry/generated artifacts are owned and supplied by the serialized integration wave.
- Behavioral verification remains outstanding for the acceptance commands other than the now-green focused updater suite.
- No Critical or Important static-review concern from rounds 1–2 remains after fix commits `3698076cc53a813d88b828a12294d038421b1cb6` and `af5f2b62137b72d836a5e4e0eba004d7b0ae7eb8`.
