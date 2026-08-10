# Parallel Task 5–8 Integration Report

## Result

DONE. Tasks 5–8 are registered and generated together, canonical pellet ownership is confined to `control`, all deferred focused gates pass on the integrated tree, and the generated artifacts are deterministic.

## Commits

Source-wave commits integrated:

- Task 5: `0e16235661e16e2f952b0c044eacc08d773115f3`
- Task 6: `1fb4d14491b1351765f22c73b56c8723ed8d8b6e`, review fix `055868c60780f4d631ed0b47a5f583da0fe1768a`
- Task 7: `1f8b4412` (the source report records the abbreviated commit ID)
- Task 8: `cff915caeba5112bda99a4de79a83555c5b9d7b6`, review fix `3698076cc53a813d88b828a12294d038421b1cb6`

Serialized integration/fix commit:

- `5f956d2b44cb4c10722c85699cec38ac1e409607` — `fix(web-contracts): integrate parallel contract bundles`

The report commit is recorded in the final response because a commit cannot contain its own final immutable ID.

## Registration and generated ownership

`WEB_CONTRACT_BUNDLES` is explicit and sorted by bundle name: `content`, `control`, `controller`, `core`, `learning`, `operations`, `settings`, `wizard`. The exact Task 5–8 model tuples and outputs are registered as `content.gen.ts`, `control.gen.ts`, `operations.gen.ts`, and `wizard.gen.ts`.

The Task 5 temporary core pellet definitions and registry entries were retired. Canonical pellet DTOs live in `common.web_contracts.control`; `PelletSocketPayload` references those canonical control models. A registry/manifest ownership audit reported no multiply registered model and no pellet definition in `core`, `content`, `operations`, or `wizard`.

One `bun run gen:types` invocation wrote all registered schema, TypeScript, defaults, and manifest outputs. The final drift check reported every artifact up to date.

## Failures found and fixes applied

- Replaced the registry's deliberately stale temporary core-pellet imports with the exact Task 5–8 imports and sorted bundle tuples, eliminating duplicate model registration and missing generated modules.
- Kept pellet persistence versioning independent from the settings version, updated the canonical pellet digest, added the explicit v3 finite-usage migration, and repaired the migration/digest regression tests. This fixed the expected shape-gate failure without weakening finite-number validation.
- Corrected Task 5 command-union test parametrization so the full serialized command grammar is exercised.
- Made generated recursive JSON value aliases safe at the Pydantic source/emitter boundary. Strict index signatures remain enabled globally; only the recursive `control` and `wizard` schema compilations disable them. No alias, cast, re-export, duplicate DTO, or handwritten replacement contract was added.
- Tightened wizard probe-device defaults and normalization, preserved independently valid probe-map halves, aligned the canonical `I2CBusValue` name, and narrowed mutable wizard state at its generated boundary.
- Corrected content event empty-string literals and concurrent upload staging assertions so the tests enforce process-private temporary ownership rather than a predictable `/tmp` name.
- Preserved generated optional dictionary semantics in content/operations consumers with source-level narrowing and explicit finite-entry filtering.
- Corrected operations coefficient validation to require High/Medium/Low exactly once and made Pydantic error locations map to the public response field.
- The first browser attempts were invalid because no live PiFire backend was running. The canonical README commands (`uv run python control.py` and the gunicorn app) were started, `/api/current` was observed, and the complete 53-test focused invocation then passed. This was an environment prerequisite, not a product-code failure.

## Exact verification evidence

### Focused Python

- `uv run pytest -q tests/unit/common/web_contracts/test_control.py tests/unit/common/test_pellets_schema.py tests/unit/common/test_pellets_migration_v2.py tests/unit/common/test_pellets_shape_digest.py tests/unit/datastore/test_pellets_shape_migration.py tests/web/test_api_pellets.py tests/web/test_api_cmd_requires_post.py tests/web/test_page_api.py tests/web/test_socketio_app_data.py tests/web/test_socket_warnings_payload.py` — **240 passed**.
- `uv run pytest -q tests/web/test_api_wizard.py tests/web/test_api_probe_map.py` — **89 passed**.
- `python -m pytest -q tests/web/test_api_files_listing.py tests/web/test_api_files_cookfile_read.py tests/web/test_api_files_cookfile_write.py tests/web/test_api_files_cookfile_comments.py tests/web/test_api_files_cookfile_assets.py tests/web/test_api_files_recipes_read.py tests/web/test_api_files_recipes_write.py tests/web/test_api_files_recipes_assets.py tests/web/test_api_history.py tests/web/test_api_metrics.py` — **246 passed**.
- `python -m pytest -q tests/unit/common/web_contracts/test_content.py` — **5 passed**.
- `uv run pytest -q tests/web/test_api_admin_system.py tests/web/test_api_admin_backups.py tests/web/test_api_admin_maintenance.py tests/web/test_api_admin_log_families.py tests/web/test_api_update.py tests/web/test_api_tuner.py tests/web/test_api_tuner_auto.py` — **148 passed**.
- `uv run pytest -q tests/unit/common/web_contracts/test_operations.py` — **11 passed**.

Total executed focused Python assertions: **739 passed**.

### Focused Rstest

- `bunx rstest run tests/unit/helpers/command.test.ts tests/unit/helpers/pellets tests/unit/helpers/notify tests/unit/components/pellets tests/unit/helpers/useLiveState.test.tsx` — **12 files, 152 tests passed**.
- `bunx rstest run tests/unit/helpers/wizard tests/unit/helpers/probes` — **11 files, 91 tests passed**.
- `bunx rstest run tests/unit/components/wizard tests/unit/components/settings/tabs/ProbesTab.test.tsx tests/unit/helpers/wizard tests/unit/helpers/probes` — **34 files, 272 tests passed**.
- `bunx rstest run tests/unit/helpers/files tests/unit/helpers/history tests/unit/helpers/metrics` — **5 files, 46 tests passed**.
- `bunx rstest run tests/unit/helpers/admin tests/unit/helpers/update tests/unit/helpers/tuner tests/unit/helpers/logs` — **7 files, 63 tests passed**.

The Task 6 helper tests are intentionally present in both exact deferred commands; counts above are per invocation rather than a claim of unique tests.

### Generation and TypeScript

- `cd web-react && bun run gen:types` — wrote the complete registered schema/manifest/TypeScript/defaults set successfully.
- `cd web-react && bun run gen:types:check` — **pass**: all Pydantic artifacts, settings defaults, and generated TypeScript are up to date.
- `cd web-react && bun run typecheck` — **pass**, zero TypeScript diagnostics.

### Focused Playwright

With the canonical live backend and control loop running:

`cd web-react && bunx playwright test tests/e2e/pellets.spec.ts tests/e2e/notify.spec.ts tests/e2e/wled-editor.spec.ts tests/e2e/wizard.spec.ts tests/e2e/probes.spec.ts tests/e2e/cookfiles.spec.ts tests/e2e/recipes.spec.ts tests/e2e/history.spec.ts tests/e2e/metrics.spec.ts tests/e2e/admin.spec.ts tests/e2e/update.spec.ts tests/e2e/tuner.spec.ts tests/e2e/events.spec.ts`

Result: **53 passed in 41.0s** using one worker.

### Focused formatting

- `uv run ruff format` was run over the 13 changed Python source/test paths: **7 reformatted, 6 unchanged**.
- `bunx biome format --write` was run over the changed handwritten frontend source/test paths: final checks reported the paths formatted; generated files were regenerated, not hand-edited.

## Changed paths in the integration/fix commit

Backend/contracts/tests:

- `blueprints/api_tuner/routes.py`
- `common/pellets_schema.py`
- `common/web_contracts/content.py`
- `common/web_contracts/control.py`
- `common/web_contracts/operations.py`
- `common/web_contracts/registry.py`
- `common/web_contracts/wizard.py`
- `tests/unit/common/test_pellets_shape_digest.py`
- `tests/unit/common/web_contracts/test_control.py`
- `tests/unit/datastore/test_pellets_shape_migration.py`
- `tests/web/test_api_files_cookfile_assets.py`
- `tests/web/test_api_files_recipes_assets.py`
- `tests/web/test_api_wizard.py`

Generated shared artifacts:

- `web-react/schema/contracts/content.schema.json`
- `web-react/schema/contracts/control.schema.json`
- `web-react/schema/contracts/core.schema.json`
- `web-react/schema/contracts/manifest.json`
- `web-react/schema/contracts/operations.schema.json`
- `web-react/schema/contracts/wizard.schema.json`
- `web-react/src/helpers/contracts/content.gen.ts`
- `web-react/src/helpers/contracts/control.gen.ts`
- `web-react/src/helpers/contracts/core.gen.ts`
- `web-react/src/helpers/contracts/operations.gen.ts`
- `web-react/src/helpers/contracts/wizard.gen.ts`

Emitter and handwritten frontend consumers/tests:

- `web-react/scripts/emitWebContracts.ts`
- `web-react/src/components/admin/AdminPage.tsx`
- `web-react/src/components/cookfiles/CookFileMeta.tsx`
- `web-react/src/components/cookfiles/EventsTable.tsx`
- `web-react/src/components/cookfiles/cookfileAdapter.ts`
- `web-react/src/components/history/historyAdapter.ts`
- `web-react/src/components/recipes/RecipeAssetManager.tsx`
- `web-react/src/components/recipes/StepsEditor.tsx`
- `web-react/src/components/settings/tabs/ProbesTab.tsx`
- `web-react/src/components/wizard/ConfigOptionField.tsx`
- `web-react/src/components/wizard/ModuleCard.tsx`
- `web-react/src/components/wizard/WizardShell.tsx`
- `web-react/src/components/wizard/fields/I2cBusField.tsx`
- `web-react/src/components/wizard/probes/DeviceConfigField.tsx`
- `web-react/src/components/wizard/probes/DeviceForm.tsx`
- `web-react/src/components/wizard/probes/DevicesCard.tsx`
- `web-react/src/components/wizard/probes/PortsCard.tsx`
- `web-react/src/helpers/notify/notifyState.ts`
- `web-react/src/helpers/probes/probeMapApi.ts`
- `web-react/src/helpers/wizard/i2cBusTypes.ts`
- `web-react/src/helpers/wizard/probeReducer.ts`
- `web-react/src/helpers/wizard/wizardState.ts`
- `web-react/src/helpers/wizard/wizardTypes.ts`
- `web-react/tests/unit/components/metrics/metricFields.test.ts`
- `web-react/tests/unit/components/wizard/ModuleCard.test.tsx`
- `web-react/tests/unit/components/wizard/fields/I2cBusField.test.tsx`
- `web-react/tests/unit/helpers/files/recipeApi.test.ts`
- `web-react/tests/unit/helpers/probes/probeMapApi.test.ts`
- `web-react/tests/unit/helpers/wizard/i2cBusTypes.test.ts`

Report path:

- `.superpowers/sdd/2026-08-10-pydantic-web-contract-generation/parallel-integration-report.md`

## Concerns

None. The temporary live PiFire processes used by Playwright were stopped after the successful gate.
