# Task 6 Report

## Result

DONE, with shared registry generation and command execution deferred by the concurrent-wave contract.

Implementation commit: `1fb4d14491b1351765f22c73b56c8723ed8d8b6e` (`yxkoqyxqosusrwqzquvoomllttvsqzyk`)
Review-fix commit: `055868c60780f4d631ed0b47a5f583da0fe1768a` (`stylxowu`)

## Changed paths in the Task 6 commits

Backend contracts, boundaries, and focused tests:

- `common/web_contracts/wizard.py` (created)
- `blueprints/api_wizard/routes.py`
- `blueprints/api/probe_map_actions.py`
- `blueprints/api/routes.py` (Task 6's remaining typed success-response construction; the shared base boundary migration is in Task 4)
- `tests/web/test_api_wizard.py`
- `tests/web/test_api_probe_map.py`

Frontend helpers and direct consumers:

- `web-react/src/components/wizard/InstallProgress.tsx` (generated import initially captured with Task 8; Task 6's review fix makes nullable pre-install status safe)
- `web-react/src/helpers/wizard/wizardTypes.ts`
- `web-react/src/helpers/wizard/probeTypes.ts` (deleted after direct-consumer cutover)
- `web-react/src/helpers/wizard/i2cBusTypes.ts`
- `web-react/src/helpers/wizard/wizardApi.ts`
- `web-react/src/helpers/wizard/wizardState.ts`
- `web-react/src/helpers/wizard/wizardRoutes.ts`
- `web-react/src/helpers/wizard/probeReducer.ts`
- `web-react/src/helpers/wizard/useModuleSwitch.ts`
- `web-react/src/helpers/probes/probeMapTypes.ts`
- `web-react/src/helpers/probes/probeMapApi.ts`
- `web-react/src/helpers/probes/probeMapRoutes.ts`
- `web-react/src/components/settings/tabs/ProbesTab.tsx`
- `web-react/src/components/wizard/ConfigOptionField.tsx`
- `web-react/src/components/wizard/DiscoveryPanel.tsx`
- `web-react/src/components/wizard/ModuleCard.tsx`
- `web-react/src/components/wizard/WizardShell.tsx`
- `web-react/src/components/wizard/fields/I2cBusField.tsx`
- `web-react/src/components/wizard/fields/UsbSerialPicker.tsx`
- `web-react/src/components/wizard/probes/BluetoothPicker.tsx`
- `web-react/src/components/wizard/probes/DeviceConfigField.tsx`
- `web-react/src/components/wizard/probes/DeviceForm.tsx`
- `web-react/src/components/wizard/probes/DevicesCard.tsx`
- `web-react/src/components/wizard/probes/PortForm.tsx`
- `web-react/src/components/wizard/probes/PortsCard.tsx`
- `web-react/src/components/wizard/probes/ThermoworksPicker.tsx`
- `web-react/src/components/wizard/steps/DisplayStep.tsx`
- `web-react/src/components/wizard/steps/DistanceStep.tsx`
- `web-react/src/components/wizard/steps/GrillPlatformStep.tsx`
- `web-react/src/components/wizard/steps/PlaceholderStep.tsx`
- `web-react/src/components/wizard/steps/ProbesStep.tsx`

Focused frontend tests and fixtures:

- `web-react/tests/unit/components/WizardExitRoundTrip.test.tsx`
- `web-react/tests/unit/components/settings/tabs/ProbesTab.test.tsx`
- `web-react/tests/unit/components/wizard/ConfigOptionField.test.tsx`
- `web-react/tests/unit/components/wizard/ModuleCard.test.tsx`
- `web-react/tests/unit/components/wizard/WizardShell.test.tsx`
- `web-react/tests/unit/components/wizard/fields/I2cBusField.test.tsx`
- `web-react/tests/unit/components/wizard/fields/UsbSerialPicker.test.tsx`
- `web-react/tests/unit/components/wizard/probes/DeviceConfigField.test.tsx`
- `web-react/tests/unit/components/wizard/probes/DeviceForm.test.tsx`
- `web-react/tests/unit/components/wizard/probes/DevicesCard.test.tsx`
- `web-react/tests/unit/components/wizard/probes/PortForm.test.tsx`
- `web-react/tests/unit/components/wizard/probes/PortsCard.test.tsx`
- `web-react/tests/unit/components/wizard/steps/DisplayStep.test.tsx`
- `web-react/tests/unit/components/wizard/steps/DistanceStep.test.tsx`
- `web-react/tests/unit/components/wizard/steps/GrillPlatformStep.test.tsx`
- `web-react/tests/unit/components/wizard/steps/ProbesStep.test.tsx`
- `web-react/tests/unit/helpers/probes/probeMapTypes.test.ts`
- `web-react/tests/unit/helpers/wizard/i2cBusTypes.test.ts`
- `web-react/tests/unit/helpers/wizard/probeReducer.devices.test.ts`
- `web-react/tests/unit/helpers/wizard/probeReducer.probes.test.ts`
- `web-react/tests/unit/helpers/wizard/probeReducer.reposition.test.ts`
- `web-react/tests/unit/helpers/wizard/wizardApi.test.ts`
- `web-react/tests/unit/helpers/wizard/wizardState.test.ts`

## Cross-task path coordination

- `web-react/src/components/wizard/InstallProgress.tsx` initially combined Task 6's `InstallStatus` generated import with Task 8's `SystemAction` generated import. Task 8 captured that shared cutover; the Task 6 review-fix commit subsequently changed only the null-safe `InstallStatus` consumer behavior.

## Original deferred registry integration (applied and extended by the final-review wave)

Add these imports from `common.web_contracts.wizard` to `common/web_contracts/registry.py`, then insert this bundle in sorted bundle-name order:

```python
from .wizard import (
    BtRowsResult,
    BtScanRow,
    BusKindsValidationRequest,
    BusKindsValidationResponse,
    InstallLog,
    InstallStatus,
    ModuleValues,
    ModuleValuesRequest,
    Probe,
    ProbeConfigField,
    ProbeDevice,
    ProbeMap,
    ProbeModuleCatalog,
    ProbeModuleData,
    ProbeProfile,
    RowsResult,
    ScanRequest,
    ScanResult,
    ThermoworksRow,
    ThermoworksRowsResult,
    WizardDraftRequest,
    WizardFinishRequest,
    WizardState,
)

ContractBundle(
    name="wizard",
    models=(
        BtRowsResult,
        BtScanRow,
        BusKindsValidationRequest,
        BusKindsValidationResponse,
        InstallLog,
        InstallStatus,
        ModuleValues,
        ModuleValuesRequest,
        Probe,
        ProbeConfigField,
        ProbeDevice,
        ProbeMap,
        ProbeModuleCatalog,
        ProbeModuleData,
        ProbeProfile,
        RowsResult,
        ScanRequest,
        ScanResult,
        ThermoworksRow,
        ThermoworksRowsResult,
        WizardDraftRequest,
        WizardFinishRequest,
        WizardState,
    ),
    typescript_output="wizard.gen.ts",
),
```

This registration must produce exactly these generated artifacts:

- `web-react/schema/contracts/wizard.schema.json`
- `web-react/src/helpers/contracts/wizard.gen.ts`

It must also add this manifest entry during generation:

```json
"wizard.schema.json": "wizard.gen.ts"
```

No registry, exporter, manifest, schema, or generated TypeScript path was edited in the original concurrent Task 6 wave. The final-review fix later regenerated the wizard schema and TypeScript after canonical response ownership was integrated.

## Deferred commands from the concurrent wave

```bash
uv run pytest -q tests/web/test_api_wizard.py tests/web/test_api_probe_map.py
cd web-react
bunx rstest run tests/unit/helpers/wizard tests/unit/helpers/probes
bunx playwright test tests/e2e/wizard.spec.ts tests/e2e/probes.spec.ts
bun run gen:types:check
bun run typecheck
```

The integration wave should additionally include the migrated direct component consumers when running focused Rstest coverage:

```bash
cd web-react
bunx rstest run tests/unit/components/wizard tests/unit/components/settings/tabs/ProbesTab.test.tsx tests/unit/helpers/wizard tests/unit/helpers/probes
```

## Verification performed within the no-execution constraint

- Used LSP references before removing or replacing exported handwritten TypeScript wire types.
- LSP diagnostics reported no errors in the Task 6 Python contract, wizard routes, wizard/probe helpers, components, or focused tests. The only wizard-route diagnostic was the pre-existing soft deprecation hint for `os.system`.
- A focused code review found that the fresh-datastore install-status tuple contains three nulls. The review-fix commit makes those contract fields nullable, adds the uninitialized-response regression test, and treats a null percentage as zero in the polling UI.
- Tests, formatting, linting, builds, typecheck, Playwright, and generation were intentionally not run, as required by the concurrent-wave contract.

## Concerns

- `wizard.gen.ts` now exists and exports the canonical wizard and probe-map mutation response contracts. Registry/inventory ownership was updated in the shared working path and is captured by Task 7's coordinated shared-path commit.
- `RowsResult` is the public generic base contract; `BtRowsResult` and `ThermoworksRowsResult` are concrete generated response contracts required so the two scan consumers retain precise row types without casts or duplicate handwritten wire types.
- The wizard draft permits `{kind: "kernel", bus_num: null}` only as mutable/incomplete draft state. Completed bus variants reuse `common.settings_schema.I2CBusConfig`; no second complete I2C union was introduced.

## Final-review fix wave

Fix commit: `8abc7ae0` (`qovyuptp`)

The final review identified two contract-boundary defects:

- Wizard draft/cancel/finish inventory entries named `RowsResult`, and the probe-map write named raw `ProbeMap`, while the routes emitted private response models. The response models are now public, registered in the wizard bundle, represented by `WizardActionResponse` and the discriminated `ProbeMapResponse` union in inventory, generated into `wizard.gen.ts`, and consumed directly by the frontend helpers.
- `_request_contract` collapsed every `request.get_json(silent=True) == None` case into `{}`. It now distinguishes a genuinely absent body from present JSON `null`, arrays, malformed JSON, and non-JSON bodies. Only `/api/wizard/cancel` retains its established absent-body compatibility; invalid present bodies return the normal 400 envelope before cancellation side effects.

Strict RED evidence:

- `uv run pytest -q tests/web/test_api_wizard.py tests/web/test_api_probe_map.py tests/unit/common/web_contracts/test_inventory.py` failed collection with three expected missing-public-contract import errors.
- `cd web-react && bunx rstest run tests/unit/helpers/wizard/wizardApi.test.ts` failed the new error-envelope regression (`1 failed, 13 passed`).
- `cd web-react && bunx rstest run tests/unit/helpers/probes/probeMapApi.test.ts` failed the corresponding probe-map regression (`1 failed, 13 passed`).

Final GREEN evidence:

```bash
uv run pytest -q tests/web/test_api_wizard.py tests/web/test_api_probe_map.py
# 98 passed in 3.47s

cd web-react
bunx rstest run tests/unit/helpers/wizard tests/unit/helpers/probes
# 11 files passed; 93 tests passed in 1.02s

cd ..
uv run pytest -q tests/unit/common/web_contracts/test_inventory.py tests/unit/common/web_contracts/test_export.py
# 17 passed in 3.87s

cd web-react
bun run gen:types:check
# Pydantic web contract artifacts are up to date.
# Generated web contract TypeScript is up to date.

bun run typecheck
# node node_modules/typescript7/bin/tsc -b
# exit 0
```

Shared-path ownership: `common/web_contracts/registry.py` and `common/web_contracts/inventory.py` also contained concurrent Task 7 changes. Per coordination with the Task 7 owner, those combined shared paths were excluded from the Task 6 fix commit and captured in Task 7 commit `d9d75b5f` (`rmnpzkvs`).

## Final lint follow-up

Lint-only fix commit: `c4c1cdc4` (`yorykstp`)

`bun run lint` reproduced four Biome organize/format errors across:

- `web-react/src/helpers/probes/probeMapApi.ts`
- `web-react/src/helpers/wizard/wizardApi.ts`
- `web-react/tests/unit/helpers/wizard/wizardApi.test.ts`

`bunx biome check --write` was limited to those three paths. Fresh verification:

```bash
cd web-react
bun run lint
# Checked 542 files in 225ms. No fixes applied. Exit 0.

bunx rstest run tests/unit/helpers/wizard tests/unit/helpers/probes
# 11 files passed; 93 tests passed in 964ms.

bun run typecheck
# node node_modules/typescript7/bin/tsc -b
# exit 0
```

Focused review found no issues: the fix only alphabetizes type-only imports and reformats unchanged expressions/mocks, with identical runtime semantics and test intent.
