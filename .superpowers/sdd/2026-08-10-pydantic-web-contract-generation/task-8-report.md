# Task 8 Report

## Result

DONE — source and tests implemented; shared registration, generation, and command execution remain deferred exactly as required for the concurrent wave.

Implementation commit: `cff915caeba5112bda99a4de79a83555c5b9d7b6` (`tyvpuyssktxwkxsswtntzqrpsqksvpol`)

Report change: `qxmwwvxquoqytltsnytkypzlqqotwklw`

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

## Verification intentionally not run

Per the concurrent-wave constraint, Task 8 did not run tests, formatters, linters, builds, typecheck, Playwright, or generation. The brief's focused commands remain unrun verbatim:

```bash
uv run pytest -q tests/web/test_api_admin_system.py tests/web/test_api_admin_backups.py tests/web/test_api_admin_maintenance.py tests/web/test_api_admin_log_families.py tests/web/test_api_update.py tests/web/test_api_tuner.py tests/web/test_api_tuner_auto.py
cd web-react
bunx rstest run tests/unit/helpers/admin tests/unit/helpers/update tests/unit/helpers/tuner tests/unit/helpers/logs
bunx playwright test tests/e2e/admin.spec.ts tests/e2e/update.spec.ts tests/e2e/tuner.spec.ts tests/e2e/events.spec.ts
bun run gen:types:check
bun run typecheck
```

The new direct model suite must also be run after registration/generation:

```bash
uv run pytest -q tests/unit/common/web_contracts/test_operations.py
```

Static LSP diagnostics were clean for `common/web_contracts/operations.py` and the modified admin/update/tuner Python routes, aside from pre-existing informational hints about `os.system` and an intentionally ignored tuner chart-label return. Frontend execution/type diagnostics are intentionally deferred because the imported `operations.gen.ts` module does not exist until the serialized integration wave.

## Concerns

- Expected integration blocker: frontend compilation remains unavailable until the operations registry entry and generated files above are produced.
- Behavioral verification is deliberately outstanding because execution was prohibited in this wave; the listed focused suites must be the integration wave's acceptance gate.
- No other known Task 8 concern remains.
