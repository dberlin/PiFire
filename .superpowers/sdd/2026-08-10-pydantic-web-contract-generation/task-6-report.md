# Task 6 Report

## Result

DONE, with shared registry generation and command execution deferred by the concurrent-wave contract.

Implementation commit: `1fb4d14491b1351765f22c73b56c8723ed8d8b6e` (`yxkoqyxqosusrwqzquvoomllttvsqzyk`)

## Changed paths in the Task 6 implementation commit

Backend contracts, boundaries, and focused tests:

- `common/web_contracts/wizard.py` (created)
- `blueprints/api_wizard/routes.py`
- `blueprints/api/probe_map_actions.py`
- `blueprints/api/routes.py` (Task 6's remaining typed success-response construction; the shared base boundary migration is in Task 4)
- `tests/web/test_api_wizard.py`
- `tests/web/test_api_probe_map.py`

Frontend helpers and direct consumers:

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

## Shared path deliberately excluded from the Task 6 commit

- `web-react/src/components/wizard/InstallProgress.tsx`: contains the Task 6 `InstallStatus` generated import alongside Task 8's `SystemAction` generated import and was captured by the Task 8 owner.

## Exact deferred registry integration

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

No registry, exporter, manifest, schema, or generated TypeScript path was edited in Task 6.

## Deferred commands (not run in this concurrent wave)

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
- Tests, formatting, linting, builds, typecheck, Playwright, and generation were intentionally not run, as required by the concurrent-wave contract.

## Concerns

- Direct frontend imports intentionally target the absent future `wizard.gen.ts`; compilation and generated-name confirmation belong to the serialized registry/generation wave.
- `RowsResult` is the public generic base contract; `BtRowsResult` and `ThermoworksRowsResult` are concrete generated response contracts required so the two scan consumers retain precise row types without casts or duplicate handwritten wire types.
- The wizard draft permits `{kind: "kernel", bus_num: null}` only as mutable/incomplete draft state. Completed bus variants reuse `common.settings_schema.I2CBusConfig`; no second complete I2C union was introduced.
