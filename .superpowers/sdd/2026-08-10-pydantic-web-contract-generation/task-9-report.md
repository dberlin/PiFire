# Task 9 Report

## Result

Implemented the executable Python inventory, a TypeScript AST extractor over actual frontend fetch/build calls, and wire-context ownership gates. Removed the remaining handwritten Python-owned helper declarations and the synchronized handwritten endpoint list, eliminated duplicate generated public exports, and verified all eight registered schema/artifact bundles.

Implementation commit: `f8a37b966edfd0e18a98b7c6938022396c6a661b` (`pqrmouvlnmzrrtplpwookzrxkrlkotnw`) — `test(contracts): enforce Pydantic web ownership`.

Review-fix commit: `bebd9c25689213f2d6781740697ccf3d6a35486c` (`qqxprkxlvwmtxplytyskmotvqsvpnkxt`) — `fix(contracts): close ownership enforcement gaps`.

Source-parity fix commit: `686aee526138be186d17a6ac742f02de1a8eedb7` (`mkuqxmnmnkruqtzmmnyluvmskpuykwnr`) — `fix(contracts): derive ownership from frontend calls`.

## Ownership enforcement

- Added `JSON_WEB_CONTRACT_INVENTORY` with HTTP and Socket.IO request/response model ownership across `content`, `control`, `controller`, `core`, `learning`, `operations`, `settings`, and `wizard`.
- Added the four-entry `NON_JSON_WEB_TRANSPORTS` allowlist for browser file handles, downloaded bytes, multipart `FormData`, and text/range streams. Every entry has an explicit transport and reason.
- Added Python checks for unique inventory entries, exact equality with endpoints extracted from actual frontend fetch/build calls, request-model/body-field parity, exact one-bundle model registration, the exact eight-bundle set, committed schemas and generated TypeScript, manifest ownership, registered-model exports, duplicate registered titles, retired paths, and generation drift.
- Added TypeScript Compiler API AST scans that exclude generated files, preserve unrelated same-name local view/state/result declarations, reject same-name declarations only when wire-bound, reject renamed/inline Python-owned wire mirrors, and prove every generated public declaration has exactly one artifact owner.
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

A second review proved the previous endpoint list and same-name classification were still insufficient:

```text
allows a same-name frontend-local view type outside wire use
expected [ "local.ts:AdminState" ] to deeply equal []
Tests 1 failed | 4 skipped
EXIT=1
```

```text
test_frontend_json_post_bodies_have_concrete_request_model_ownership
AssertionError:
POST /api/files/cookfiles/assets/delete: JSON body has no concrete request model
2 failed
```

The fix removed `src/helpers/jsonWebEndpoints.json`. `scripts/extractWebTransports.ts` now derives JSON endpoints, Socket.IO events, multipart bodies, byte downloads, text/range streams, and browser-file usage from frontend source. It catches the real `POST /api/files/cookfiles/assets/upload` path and extracts JSON body fields for parity with registered request models. A same-name `AdminState` view probe now passes outside wire use, while the same declaration fails when used as a `response.json()` body.

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

The source-parity fix also removed the handwritten `web-react/src/helpers/jsonWebEndpoints.json` mirror. Concrete Pydantic request models now own every frontend JSON POST, including `file`-key cookfile/recipe operations, cookfile mutations, empty wizard/content operations, Thermoworks discovery, and probe-map writes. Flask boundaries validate the new models and frontend calls use their generated TypeScript types.

## GREEN evidence

```text
uv run pytest -q tests/unit/common/web_contracts/test_inventory.py
10 passed in 2.85s
EXIT=0
```

```text
uv run pytest -q tests/unit/common/web_contracts
77 passed in 3.14s
EXIT=0
```

```text
cd web-react
bunx rstest run tests/unit/helpers/generatedContracts.test.ts
Test Files 1 passed
Tests 6 passed
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

```text
uv run pytest -q -n 0 \
  tests/web/test_api_files_cookfile_write.py \
  tests/web/test_api_files_cookfile_assets.py \
  tests/web/test_api_files_recipes_write.py \
  tests/web/test_api_files_recipes_assets.py
173 passed in 1.86s
EXIT=0
```

## Intentional exclusions preserved

The gate does not reject frontend-owned view/state, normalized result wrappers, query/path helpers, `FormData` and browser file handling, byte downloads, log `Range`/`Content-Range` logic, or targeted runtime decoders. `NON_JSON_WEB_TRANSPORTS` remains the explicit four-category allowlist with reasons; the source extractor discovers each excluded transport instance from the frontend. The AST gate rejects same-name generated declarations only in wire-bound use and rejects renamed/inline envelope mirrors while leaving unrelated same-name local types legal.

## Concerns

None.
