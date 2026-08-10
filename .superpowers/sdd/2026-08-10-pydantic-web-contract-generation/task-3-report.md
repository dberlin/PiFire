# Task 3 Report

## Result

DONE

Implementation commit: `dad25ed82bf252480f271250754a5f5441012ec4` (`rzplyvqprqkyumvwosquwnrsuzvttzxv`)

Report change: `pyxwynmknmlqxuqonyzwnmqzxtlsoovn`

## Changed paths

Backend contracts, registry/export pipeline, routes, and tests:

- `common/web_contracts/settings.py` (created)
- `common/web_contracts/registry.py`
- `common/web_contracts/export.py`
- `common/settings_schema.py`
- `blueprints/api/routes.py`
- `tests/unit/common/test_settings_schema.py`
- `tests/unit/controller/test_controller_catalog.py`
- `tests/web/test_api_settings_update.py`

Generated pipeline and artifacts:

- `web-react/schema/contracts/controller.schema.json` (created)
- `web-react/schema/contracts/manifest.json`
- `web-react/scripts/emitControllerTypes.ts` (deleted)
- `web-react/tests/unit/scripts/emitControllerTypes.test.ts` (deleted)
- `web-react/scripts/emitWebContracts.ts`
- `web-react/scripts/gen-types.ts`
- `web-react/biome.jsonc`
- `web-react/src/helpers/settings/controllerTypes.gen.ts`
- `web-react/src/helpers/settings/settingsTypes.gen.ts`
- `web-react/src/helpers/contracts/core.gen.ts`

`web-react/schema/settings.schema.json` and `web-react/src/helpers/settings/settingsDefaults.gen.ts` were regenerated and verified current but remained byte-identical. `web-react/scripts/emitSettingsDefaults.ts` required no change because the registered root remains the direct `SettingsSchema.model_json_schema()` shape.

LSP-proven clean-cutover consumers:

- `web-react/src/helpers/settings/settingsApi.ts`
- `web-react/src/helpers/settings/accent.ts`
- `web-react/src/helpers/settings/controllerSelection.ts`
- `web-react/src/helpers/settings/fieldErrorContext.tsx`
- `web-react/src/helpers/settings/fieldErrors.ts`
- `web-react/src/helpers/settings/mpcFan.ts`
- `web-react/src/helpers/settings/platform.ts`
- `web-react/src/helpers/settings/settingsDrafts.ts`
- `web-react/src/helpers/settings/settingsRoutes.ts`
- `web-react/src/helpers/settings/useSaveSettings.ts`
- `web-react/src/helpers/settings/useSettings.ts`
- `web-react/src/helpers/probes/probeMapApi.ts`
- `web-react/src/components/settings/SettingsShell.tsx`
- `web-react/src/components/settings/tabs/ControllerTab.tsx`
- `web-react/src/components/settings/tabs/GeneralTab.tsx`
- `web-react/src/components/settings/tabs/HistoryTab.tsx`
- `web-react/src/components/settings/tabs/NotificationsTab.tsx`
- `web-react/src/components/settings/tabs/PelletsTab.tsx`
- `web-react/src/components/settings/tabs/PlatformTab.tsx`
- `web-react/src/components/settings/tabs/ProbesTab.tsx`
- `web-react/src/components/settings/tabs/PwmTab.tsx`
- `web-react/src/components/settings/tabs/SafetyTab.tsx`
- `web-react/src/components/settings/tabs/StartupTab.tsx`
- `web-react/src/components/settings/tabs/UnitsTab.tsx`
- `web-react/src/components/settings/tabs/WorkModeTab.tsx`
- `web-react/tests/unit/components/DashboardRoute.test.tsx`
- `web-react/tests/unit/components/settings/tabs/ControllerTab.test.tsx`
- `web-react/tests/unit/components/settings/tabs/PlatformTab.test.tsx`
- `web-react/tests/unit/components/settings/tabs/WorkModeTab.test.tsx`
- `web-react/tests/unit/helpers/settings/accent.test.ts`
- `web-react/tests/unit/helpers/settings/controllerMetadataFixture.test.ts`
- `web-react/tests/unit/helpers/settings/controllerSelection.test.ts`
- `web-react/tests/unit/helpers/settings/mpcFan.test.ts`
- `web-react/tests/unit/helpers/settings/platform.test.ts`

## RED evidence

Initial required Python command:

```bash
uv run pytest -q tests/unit/common/test_settings_schema.py tests/unit/controller/test_controller_catalog.py
```

Observed two collection errors because `common.web_contracts.settings` did not exist (`EXIT=1`). After the models existed, registry tests separately failed because the settings root registration and controller bundle were absent (`1 failed, 6 passed, 1 error`, `EXIT=1`).

Route validation RED:

```bash
uv run pytest -q tests/web/test_api_settings_update.py::test_settings_update_rejects_a_non_object_request_settings_member
```

The old route returned an unscoped empty error path instead of the concrete request-model path `settings` (`1 failed`, `EXIT=1`).

Generator drift RED:

```bash
cd web-react
bun run gen:types:check
```

Observed a changed manifest and missing `web-react/schema/contracts/controller.schema.json`; the exporter exited 1.

Frontend cutover RED:

```bash
bunx rstest run tests/unit/helpers/settings
```

After the envelope-only client cutover, two accent tests still supplied the retired direct-settings response rather than `SettingsResponse`; `1 file failed, 15 passed`, `2 tests failed, 95 passed`, `EXIT=1`.

A final metadata-parity regression test also observed integer controller bounds being normalized to floats before the int option subtype overrode its bound fields (`1 failed`, `EXIT=1`).

## GREEN evidence

Final focused Python acceptance command:

```bash
uv run pytest -q tests/unit/common/test_settings_schema.py tests/unit/controller/test_controller_catalog.py tests/web/test_api_settings_update.py tests/web/test_api_settings_controller_gate.py
```

```text
99 passed in 3.29s
EXIT=0
```

Final focused frontend settings and direct controller consumer command:

```bash
cd web-react
bunx rstest run tests/unit/helpers/settings tests/unit/components/settings/tabs/ControllerTab.test.tsx tests/unit/components/settings/tabs/WorkModeTab.test.tsx
```

```text
18 files passed
135 tests passed in 1.88s
EXIT=0
```

Exporter regression check:

```text
uv run pytest -q tests/unit/common/web_contracts/test_export.py
5 passed in 1.53s
EXIT=0
```

## Generated drift and typecheck

```bash
cd web-react
bun run gen:types:check
```

```text
Pydantic web contract artifacts are up to date.
src/helpers/settings/settingsDefaults.gen.ts is up to date.
Generated web contract TypeScript is up to date.
EXIT=0
```

```bash
bun run typecheck
```

```text
$ node node_modules/typescript7/bin/tsc -b
EXIT=0
```

## Review fix round 1

Falsy flag normalization RED:

```bash
uv run pytest -q tests/web/test_api_settings_update.py::test_settings_update_normalizes_legacy_falsy_flags_to_empty
```

```text
5 failed
EXIT=1
```

Dedicated unknown-flag envelope RED:

```bash
uv run pytest -q tests/web/test_api_settings_update.py::test_settings_update_rejects_unknown_flag
```

```text
1 failed in 2.57s
EXIT=1
```

The failure showed the generic Pydantic `flags.0` error instead of `Unknown flag: mode` with an empty `errors` list.

Focused GREEN for both reviewed behaviors:

```bash
uv run pytest -q \
  tests/web/test_api_settings_update.py::test_settings_update_rejects_unknown_flag \
  tests/web/test_api_settings_update.py::test_settings_update_normalizes_legacy_falsy_flags_to_empty
```

```text
6 passed in 2.75s
EXIT=0
```

Final directly affected route/request-contract suite:

```bash
uv run pytest -q tests/web/test_api_settings_update.py tests/unit/common/test_settings_schema.py
```

```text
91 passed in 3.22s
EXIT=0
```

## Self-review

`SettingsSchema` remains the canonical direct root at the established settings schema/type paths. A registered root contract now places it in the same deterministic manifest as the controller bundle, while the manifest compiler safely resolves only paths below `schema` and `src/helpers`. The handwritten controller emitter, its tests, the old settings-schema CLI exporter, handwritten frontend settings flags/options/catalog/errors, and every alias/re-export consumer were removed.

The production controller catalog is validated before dynamic models are built. Discriminated float/int/bool/list/string option models preserve absent-versus-null metadata, list literals, defaults, bounds, and integer-versus-float option values. `PidConfig`, `PidSpConfig`, `MpcConfig`, and `ControllerConfigs` are created from validated metadata with defaults. Routes validate and serialize concrete settings, update, mode, and controller response models while retaining sparse merge-patch data, allowed flags, error envelopes, controller gates, and frontend fail-closed/fail-open behavior.

The manifest compiler's strict index signatures exposed previously implicit undefined values; direct UI consumers now narrow those values without adding aliases or compatibility layers. The regenerated core output changed only those compiler-owned strict index signatures as a consequence of eliminating the order-dependent legacy settings/controller compilation path.

## Concerns

The focused frontend suite emits the pre-existing React Router warning `No HydrateFallback element provided to render during initial hydration`; it is non-failing and unrelated to settings contracts. No known Critical or Important Task 3 concern remains.
