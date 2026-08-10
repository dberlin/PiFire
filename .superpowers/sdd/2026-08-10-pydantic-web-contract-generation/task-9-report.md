# Task 9 Report

## Result

Implemented the executable Python inventory, an independent checked-in frontend endpoint inventory, and TypeScript AST ownership gates. Removed the remaining handwritten Python-owned helper declarations, eliminated duplicate generated public exports, and verified all eight registered schema/artifact bundles.

Implementation commit: `f8a37b966edfd0e18a98b7c6938022396c6a661b` (`pqrmouvlnmzrrtplpwookzrxkrlkotnw`) — `test(contracts): enforce Pydantic web ownership`.

Review-fix commit: `bebd9c25689213f2d6781740697ccf3d6a35486c` (`qqxprkxlvwmtxplytyskmotvqsvpnkxt`) — `fix(contracts): close ownership enforcement gaps`.

## Ownership enforcement

- Added `JSON_WEB_CONTRACT_INVENTORY` with HTTP and Socket.IO request/response model ownership across `content`, `control`, `controller`, `core`, `learning`, `operations`, `settings`, and `wizard`.
- Added the four-entry `NON_JSON_WEB_TRANSPORTS` allowlist for browser file handles, downloaded bytes, multipart `FormData`, and text/range streams. Every entry has an explicit transport and reason.
- Added Python checks for unique inventory entries, exact equality with the independently checked-in frontend endpoint list, exact one-bundle model registration, the exact eight-bundle set, committed schemas and generated TypeScript, manifest ownership, registered-model exports, duplicate registered titles, retired paths, and generation drift.
- Added TypeScript Compiler API AST scans that exclude generated files, preserve local view/state/result/query/decoder declarations, reject same-name and renamed/inline Python-owned wire mirrors, and prove every generated public declaration has exactly one artifact owner.
- Added deterministic schema export-ownership metadata. The TypeScript emitter retains only declarations reachable from each artifact's owned public surface, so transitive shared definitions remain internal and cannot create duplicate generated exports.

## RED evidence

Before `common/web_contracts/inventory.py` existed:

```text
uv run pytest -q tests/unit/common/web_contracts/test_inventory.py
ModuleNotFoundError: No module named 'common.web_contracts.inventory'
1 error in 1.62s
EXIT=1
```

The first structural run found seven real residual mirrors:

```text
admin/adminApi.ts:AdminEnvelope
command.ts:GrillMode
command.ts:ManualOutput
command.ts:SystemCmd
logs/logsApi.ts:LogsMetadataEnvelope
tuner/tunerApi.ts:TunerEnvelope
update/updateApi.ts:UpdateEnvelope
```

A representative `AdminState` mirror was then injected as `src/helpers/__contract_residual_probe.ts`; the clean-tree structural gate failed exactly as intended:

```text
Python-owned contract mirrors:
__contract_residual_probe.ts:AdminState
Test Files 1 failed
Tests 1 failed | 1 skipped
EXIT=1
```

The injected file was removed before GREEN verification and is not committed.

Review then strengthened the gates and reproduced two additional failures before the fixes:

```text
exports every schema definition from its committed generated artifact
expected [] to deeply equal [
  "CommandResponseData: .../core.gen.ts, .../learning.gen.ts",
  "Probe: .../operations.gen.ts, .../wizard.gen.ts"
]
Tests 1 failed | 4 passed
EXIT=1
```

```text
keeps migrated helpers free of Python-owned interface and type declarations
Python-owned contract mirrors:
metrics/metricsApi.ts:<inline-envelope>
Tests 1 failed | 3 passed | 1 skipped
EXIT=1
```

The duplicate-export gate now inspects committed generated artifacts, the residual gate checks the complete helper tree, and both representative failures are covered by permanent tests.

## Removed declarations and retired paths

Removed handwritten declarations:

- `AdminEnvelope<T>`
- `LogsMetadataEnvelope`
- `TunerEnvelope<T>`
- `UpdateEnvelope<T>`
- `GrillMode`
- `ManualOutput`
- `SystemCmd`

The direct consumers now import `ApiEnvelope`, `GrillMode`, `ManualOutput`, and `SystemCommand` from generated modules. LSP references were checked before removing the exported command symbols; the only external reference was `dashboard/buttonsForMode.ts`, which now imports `ManualOutput` directly from `control.gen.ts`.

The inventory verifies these previously retired paths remain absent; Task 9 did not recreate or replace any of them:

- `web-react/scripts/emitControllerTypes.ts`
- `web-react/src/helpers/modelEvidence/types.ts`
- `web-react/src/helpers/pidSpLearning/types.ts`
- `web-react/src/helpers/pellets/pelletTypes.ts`
- `web-react/src/helpers/files/recipeTypes.ts`
- `web-react/src/helpers/wizard/probeTypes.ts`

Stale comments naming retired `pelletTypes.ts` and `recipeTypes.ts` were updated to point to the generated/Pydantic ownership paths. The reviewed fix regenerated schemas with explicit export-ownership metadata and regenerated TypeScript with one public owner per declaration.

## GREEN evidence

```text
uv run pytest -q tests/unit/common/web_contracts/test_inventory.py
9 passed
EXIT=0
```

```text
uv run pytest -q tests/unit/common/web_contracts
76 passed in 2.80s
EXIT=0
```

```text
cd web-react
bunx rstest run tests/unit/helpers/generatedContracts.test.ts
Test Files 1 passed
Tests 5 passed
EXIT=0
```

```text
bun run gen:types:check
Pydantic web contract artifacts are up to date.
src/helpers/settings/settingsDefaults.gen.ts is up to date.
Generated web contract TypeScript is up to date.
EXIT=0
```

```text
bun run typecheck
EXIT=0
```

## Intentional exclusions preserved

The gate does not reject frontend-owned view/state, normalized result wrappers, query/path helpers, `FormData` and browser file handling, byte downloads, log `Range`/`Content-Range` logic, or targeted runtime decoders. `NON_JSON_WEB_TRANSPORTS` remains the explicit four-category allowlist with reasons; the frontend endpoint inventory names each excluded transport instance. The AST gate rejects same-name generated declarations and renamed/inline envelope mirrors while leaving unrelated local types legal.

## Concerns

None.
