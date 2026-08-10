# Pydantic-Owned Web Contract Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Pydantic models the sole source of truth for every JSON interface shared by Python and `web-react`, export their JSON Schemas deterministically, and generate all corresponding TypeScript with `json-schema-to-typescript`.

**Architecture:** Add strict, domain-split wire models under `common/web_contracts/` and an explicit registry that exports deterministic per-domain JSON Schema bundles. Python HTTP and Socket.IO boundaries validate/serialize those models; `web-react/scripts/gen-types.ts` invokes the Python exporter and compiles every bundle into committed `*.gen.ts` modules. Frontend boundary adapters keep their existing transport/error behavior and targeted runtime checks, but import generated wire types instead of redeclaring Python-owned shapes.

**Tech Stack:** Python 3.14, Pydantic 2.13+, Flask/Flask-SocketIO, Bun, TypeScript 7, `json-schema-to-typescript` 15, pytest, Rstest, Playwright, Ruff, Biome, ESLint.

## Global Constraints

- Pydantic is authoritative for every JSON request or response exchanged between Python and `web-react`, including Socket.IO payloads.
- Generated TypeScript is compile-time typing only. Do not add Ajv or another frontend runtime-schema dependency.
- Keep targeted strict frontend decoders for authority- or safety-sensitive payloads; they must return generated types and must not redeclare wire interfaces.
- Exclude frontend-only view/state types, client-normalized result wrappers, URL/query helpers, `FormData`, downloads, and text/range streams. They are not shared JSON interfaces.
- Preserve existing input coercion and rejection semantics per endpoint. Strict models are the default; use explicit pre-validators only where a focused legacy contract proves that Python currently accepts a coercible representation.
- Internal controller/runtime dataclasses may remain internal. Convert them to Pydantic wire models at the HTTP/socket boundary rather than coupling domain code to frontend concerns.
- `extra="forbid"`, frozen models, strict scalar validation, and finite floats are the default. Use `extra="allow"` only for payloads that intentionally carry driver/plugin-defined members, and pin each exception with a test.
- Preserve JSON member names and omission/null semantics exactly during migration. This is a source-of-truth change, not an API redesign.
- Preserve all existing HTTP statuses, generic envelope semantics, polling, cancellation, socket invalidation, control authority, and stale-response fencing.
- The generated JSON Schema and TypeScript artifacts remain committed so production installs do not need Python code generation at runtime.
- `bun run gen:types` is the single write command. `bun run gen:types:check` is the non-mutating drift gate.
- Remove each handwritten TypeScript wire declaration in the same task that switches its final consumer. No aliases, compatibility re-exports, or second source of truth.
- Use Jujutsu only. Run LSP references before changing exported TypeScript symbols.
- Follow strict RED/GREEN TDD. Every task ends with focused Python and frontend tests, generator drift checks, and a path-limited commit.
- The current working copy includes the interrupted PID-SP panel commits through `492ffe97`. Preserve them. The known `ControllerModelStore.load_strict()` cache-bypass finding must be fixed in Task 4 before aggregate completion.

## Contract Boundary

### Generated from Pydantic

| Domain | Current frontend declarations or consumers | Python producer/consumer |
|---|---|---|
| Common envelopes and live state | `helpers/types.ts`, JSON body casts in `command.ts`, `useLiveState.ts` | `common.app.api_response`, `blueprints/api/routes.py`, `blueprints/mobile/socket_io.py` |
| Settings and controller metadata | `settingsTypes.gen.ts`, `controllerTypes.gen.ts`, `settingsApi.ts` wire declarations | `common/settings_schema.py`, `controller/controllers.json`, settings/controller API routes |
| MPC and PID-SP learning | `helpers/modelEvidence/types.ts`, `helpers/pidSpLearning/types.ts`, their API adapters | `controller/model_learning/report.py`, activation/calibration contracts, `controller/pid_sp_learning.py`, API routes |
| Pellets, notify, and command/control JSON | `pelletTypes.ts`, `pelletsApi.ts`, `notifyApi.ts`, `wledApi.ts`, JSON request bodies in `command.ts` | pellet schema/actions, control delta/commands, WLED routes, socket pellet payload |
| Wizard and probes | `wizardTypes.ts`, `probeTypes.ts`, `i2cBusTypes.ts`, `probeMapTypes.ts` | `blueprints/api_wizard/routes.py`, probe-map routes/actions, wizard manifest |
| Files, recipes, history, and metrics | `fileTypes.ts`, `recipeTypes.ts`, cookfile JSON types, `historyApi.ts`, `metricsTypes.ts` | `blueprints/api_files`, `blueprints/api_history`, `blueprints/api_metrics` |
| Admin, update, tuner, logs metadata | `adminTypes.ts`, `updateTypes.ts`, `tunerTypes.ts`, `LogFamily` | `blueprints/api_admin`, `blueprints/api_update`, `blueprints/api_tuner` |

### Intentionally handwritten

- `DashView`, `ProbeCardView`, component props/state, hooks, reducers, and other browser-derived view models.
- `CommandClient`, `CommandResult`, `AdminResult<T>`, `UpdateResult<T>`, `MetricsResult`, `TunerResult<T>`, and similar browser-normalized success/error results that Python never emits.
- Query option/path types, route builders, `SettingsPath`, draft state, and generic TypeScript utilities.
- `FormData` upload bodies, downloaded bytes, log text/range deltas, `Content-Range` parsing, and browser file handles.
- CSS/design token unions such as `AccentName`.

---

### Task 1: Build the deterministic Pydantic-to-TypeScript pipeline

**Files:**
- Create: `common/web_contracts/__init__.py`
- Create: `common/web_contracts/base.py`
- Create: `common/web_contracts/registry.py`
- Create: `common/web_contracts/export.py`
- Create: `tests/unit/common/web_contracts/test_export.py`
- Create: `web-react/scripts/emitWebContracts.ts`
- Modify: `web-react/scripts/gen-types.ts`
- Modify: `web-react/package.json`
- Create generated directory: `web-react/schema/contracts/`
- Create generated directory: `web-react/src/helpers/contracts/`

**Interfaces:**
- Produces `WireModel`, `ExtensibleWireModel`, `FiniteFloat`, `ContractBundle`, `WEB_CONTRACT_BUNDLES`, `render_contract_artifacts()`, and `python -m common.web_contracts.export --write|--check`.
- Produces a committed `web-react/schema/contracts/manifest.json` that maps each schema bundle to one `*.gen.ts` output.
- Extends `bun run gen:types` and `bun run gen:types:check`; later tasks only register models and consume generated artifacts.

- [ ] **Step 1: Write failing exporter determinism and drift tests**

Create tests that use two tiny local Pydantic models and assert:

```python
class Child(WireModel):
    count: int

class Parent(WireModel):
    child: Child
    mode: Literal["one", "two"]

bundle = ContractBundle("test", (Parent, Child), "test.gen.ts")
first = render_bundle_schema(bundle)
second = render_bundle_schema(bundle)
assert first == second
assert first.endswith("\n")
assert '"additionalProperties": false' in first
```

Also assert `--check` reports a missing, changed, and unexpected generated file without modifying any file.

- [ ] **Step 2: Run the exporter tests and observe RED**

Run:

```bash
uv run pytest -q tests/unit/common/web_contracts/test_export.py
```

Expected: import failure for `common.web_contracts`.

- [ ] **Step 3: Implement strict base models and the explicit bundle registry**

Use these exact base contracts:

```python
FiniteFloat = Annotated[float, Field(allow_inf_nan=False, strict=True)]

class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

class ExtensibleWireModel(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True, strict=True)

@dataclass(frozen=True, slots=True)
class ContractBundle:
    name: str
    models: tuple[type[BaseModel], ...]
    typescript_output: str
```

`WEB_CONTRACT_BUNDLES` is an explicit tuple, sorted by bundle name. Do not discover models by walking modules; explicit registration makes additions reviewable.

- [ ] **Step 4: Implement deterministic schema export**

Use Pydantic serialization schemas so generated TypeScript describes emitted JSON:

```python
def render_bundle_schema(bundle: ContractBundle) -> str:
    _, schema = models_json_schema(
        [(model, "serialization") for model in bundle.models],
        title=f"PiFire {bundle.name} web contracts",
    )
    return json.dumps(schema, indent=2, sort_keys=True, allow_nan=False) + "\n"
```

Write schemas and `manifest.json` atomically. `--check` must compare bytes and reject missing, stale, or unexpected files under both generated directories.

- [ ] **Step 5: Add the TypeScript compiler stage**

`emitWebContracts.ts` reads the generated manifest and compiles each schema with:

```ts
await compileFromFile(schemaPath, {
  additionalProperties: false,
  bannerComment: "/* eslint-disable */\n// GENERATED from Pydantic web contracts — do not edit. Regenerate: bun run gen:types",
  declareExternallyReferenced: true,
  strictIndexSignatures: true,
  unknownAny: true,
  unreachableDefinitions: true,
});
```

The script must produce stable LF-terminated bytes and support write/check modes. `gen-types.ts` first invokes the Python exporter from the repository root, then generates settings defaults and every TypeScript artifact. Do not duplicate the bundle list in TypeScript.

- [ ] **Step 6: Prove write and check behavior end to end**

Run:

```bash
cd web-react
bun run gen:types
bun run gen:types:check
bun run typecheck
```

Expected: all commands exit 0; a deliberate temporary schema-byte mutation makes `gen:types:check` fail and restoring/regenerating makes it pass.

- [ ] **Step 7: Commit the pipeline**

```bash
jj commit -m "build(contracts): generate web types from Pydantic"
```

---

### Task 2: Generate common envelopes and Socket.IO live-state contracts

**Files:**
- Create: `common/web_contracts/core.py`
- Modify: `common/web_contracts/registry.py`
- Modify: `blueprints/api/routes.py`
- Modify: `blueprints/mobile/socket_io.py`
- Modify: `tests/web/test_socketio_app_data.py`
- Modify: `tests/web/test_socket_dash_payload_fields.py`
- Modify: `tests/web/test_socket_probe_staleness.py`
- Modify: `web-react/src/helpers/types.ts`
- Modify: `web-react/src/helpers/useLiveState.ts`
- Modify: `web-react/src/helpers/command.ts`
- Modify: `web-react/src/helpers/useWebUiBuild.ts`
- Modify: `web-react/src/helpers/dashboard/controlHealth.ts`
- Modify: `web-react/src/helpers/shell/warningsApi.ts`
- Modify: `web-react/tests/unit/helpers/types.test.ts`
- Modify: `web-react/tests/unit/helpers/command.test.ts`
- Create: `web-react/tests/unit/helpers/useWebUiBuild.test.ts`
- Modify: `web-react/tests/unit/helpers/dashboard/health.test.ts`
- Modify: `web-react/tests/unit/helpers/shell/warningsApi.test.ts`
- Generate: `web-react/schema/contracts/core.schema.json`
- Generate: `web-react/src/helpers/contracts/core.gen.ts`

**Interfaces:**
- Produces `ApiEnvelope`, `ProbeStatusPayload`, `ProbeDataPayload`, `TimerPayload`, `OutputPayload`, `RecipeStatusPayload`, `DashSocketPayload`, `PelletSocketPayload`, `WebUiBuildResponse`, `ControlHealthResponse`, `DismissWarningsRequest`, `DismissWarningsResponse`, and concrete command response payloads.
- All frontend consumers import these generated names directly. Do not re-export them from `helpers/types.ts`; that file retains only frontend-owned `AccentName`.

- [ ] **Step 1: Add failing Python wire-model parity tests**

Build real payloads through `_get_dash_data`, `_get_probe_structure`, and the pellet socket producer, then require:

```python
validated = DashSocketPayload.model_validate(payload, strict=True)
assert validated.model_dump(mode="json", by_alias=True, exclude_none=False) == payload
```

Pin every current member, omission/null behavior, finite numeric handling, and camelCase alias. Add an explicit test that plugin-specific probe-status members survive; this is the justified `extra="allow"` exception.

- [ ] **Step 2: Add failing frontend generated-type consumption tests**

Update fixtures to use `satisfies DashSocketPayload`; remove the handwritten `LiveState`, `ProbeData`, and `ProbeStatus` bodies. Keep `AccentName` handwritten.

Run the focused Python and Rstest files and observe missing generated symbols.

- [ ] **Step 3: Implement and register core Pydantic models**

Use aliases for the existing camelCase socket wire. Model fixed nested objects rather than anonymous `dict`s. `ProbeStatusPayload` and `ProbeDataPayload` use `ExtensibleWireModel`; every other core model uses `WireModel`.

`ApiEnvelope[T]` models the server-emitted envelope only:

```python
class ApiEnvelope(WireModel, Generic[T]):
    result: Literal["OK", "ERROR"]
    message: str = ""
    data: T | None = None
```

Do not generate `CommandResult`; it is a browser-normalized result.

- [ ] **Step 4: Validate serialization at producers**

At each socket emit boundary, construct the Pydantic model once and call `model_dump(mode="json", by_alias=True, exclude_none=False)`. Do not validate the same payload again in `useLiveState`; preserve current socket performance and behavior.

- [ ] **Step 5: Regenerate and remove handwritten mirrors**

Run `bun run gen:types`. Import generated DTOs in `types.ts`, `useLiveState.ts`, fixtures, and command body parsing. Delete the mirrored interfaces and anonymous response casts now covered by concrete generated envelopes.

- [ ] **Step 6: Verify core contracts**

```bash
uv run pytest -q tests/web/test_socketio_app_data.py tests/web/test_socket_dash_payload_fields.py tests/web/test_socket_probe_staleness.py
cd web-react
bunx rstest run \
  tests/unit/helpers/types.test.ts \
  tests/unit/helpers/command.test.ts \
  tests/unit/helpers/useWebUiBuild.test.ts \
  tests/unit/helpers/dashboard/health.test.ts \
  tests/unit/helpers/shell/warningsApi.test.ts
bun run gen:types:check
bun run typecheck
```

- [ ] **Step 7: Commit**

```bash
jj commit -m "refactor(contracts): generate live-state wire types"
```

---

### Task 3: Unify settings and dynamic controller metadata generation

**Files:**
- Create: `common/web_contracts/settings.py`
- Modify: `common/settings_schema.py`
- Modify: `common/web_contracts/registry.py`
- Modify: `tests/unit/common/test_settings_schema.py`
- Modify: `tests/unit/controller/test_controller_catalog.py`
- Modify: `web-react/scripts/gen-types.ts`
- Modify: `web-react/scripts/emitSettingsDefaults.ts`
- Delete: `web-react/scripts/emitControllerTypes.ts`
- Modify: `web-react/src/helpers/settings/settingsApi.ts`
- Modify: `web-react/tests/unit/helpers/settings/settingsApi.test.ts`
- Modify: `web-react/tests/unit/helpers/settings/controllerMetadataFixture.test.ts`
- Regenerate in place: `web-react/schema/settings.schema.json`
- Generate: `web-react/schema/contracts/controller.schema.json`
- Regenerate in place: `web-react/src/helpers/settings/settingsTypes.gen.ts`
- Regenerate in place: `web-react/src/helpers/settings/controllerTypes.gen.ts`
- Regenerate in place: `web-react/src/helpers/settings/settingsDefaults.gen.ts`

**Interfaces:**
- Reuses `SettingsSchema` as the canonical `SettingsResponse.settings` model and preserves the existing `settings.schema.json` / `settingsTypes.gen.ts` artifact paths.
- Produces `SettingsResponse`, `SettingsFlag`, `SettingsUpdateRequest`, `SettingsUpdateResponse`, `SaveFieldError`, `ModeResponse`, `ControllerOption`, `ControllerDefinition`, `ControllerMetadata`, `ControllerCatalog`, and manifest-derived `PidConfig`, `PidSpConfig`, `MpcConfig`, `ControllerConfigs` through Pydantic `create_model()`.

- [ ] **Step 1: Write failing settings/catalog generation tests**

Assert the production `controller/controllers.json` validates as `ControllerCatalog`, and generated config models contain exactly each option name with the option's declared scalar/list type and required/default semantics. Assert settings JSON and controller metadata API responses validate against their concrete response models.

- [ ] **Step 2: Observe RED**

Run:

```bash
uv run pytest -q tests/unit/common/test_settings_schema.py tests/unit/controller/test_controller_catalog.py
```

Expected: missing web-contract catalog models and registry artifact.

- [ ] **Step 3: Model controller metadata and build dynamic Pydantic config models**

Define discriminated option models for `float`, `int`, `bool`, `list`, and `string`. Build each controller config model with `create_model()` from the validated manifest; use the manifest default as the Pydantic default and preserve list literals. Build `ControllerConfigs` from the resulting model classes. The Pydantic-generated schema replaces the custom TypeScript emitter.

- [ ] **Step 4: Register existing settings models without duplication**

Keep `SettingsSchema.model_json_schema()` as the direct root of `web-react/schema/settings.schema.json`; add it to the generated manifest rather than wrapping it in a second settings bundle. Register `SettingsResponse`, the catalog models, and dynamic controller config models in `controller.schema.json`. Do not copy settings fields into `common/web_contracts/settings.py`.

- [ ] **Step 5: Regenerate and cut over frontend imports**

Delete `emitControllerTypes.ts` and its tests. Generate the existing `settingsTypes.gen.ts` and `controllerTypes.gen.ts` paths from the two Pydantic schemas. Update `emitSettingsDefaults.ts` only as needed to read the direct `SettingsSchema` schema unchanged. Keep `SettingsPath`, drafts, defaults, and client save/error state handwritten.

- [ ] **Step 6: Verify settings/controller parity**

```bash
uv run pytest -q tests/unit/common/test_settings_schema.py tests/unit/controller/test_controller_catalog.py tests/web/test_api_settings_update.py tests/web/test_api_settings_controller_gate.py
cd web-react
bunx rstest run tests/unit/helpers/settings
bun run gen:types:check
bun run typecheck
```

- [ ] **Step 7: Commit**

```bash
jj commit -m "refactor(settings): generate web contracts from Pydantic"
```

---

### Task 4: Replace MPC and PID-SP handwritten learning DTOs

**Files:**
- Create: `common/web_contracts/learning.py`
- Modify: `common/web_contracts/registry.py`
- Modify: `controller/model_learning/report.py`
- Modify: `controller/model_learning/activation.py`
- Modify: `controller/pid_sp_learning.py`
- Modify: `common/controller_model_state.py`
- Modify: `blueprints/api/routes.py`
- Modify: `tests/unit/controller/test_learning_report.py`
- Modify: `tests/unit/controller/test_pid_sp_learning.py`
- Modify: `tests/unit/common/test_controller_model_state.py`
- Modify: `tests/web/test_api_model_evidence.py`
- Modify: `tests/web/test_api_pid_sp_learning.py`
- Delete: `web-react/src/helpers/modelEvidence/types.ts`
- Delete: `web-react/src/helpers/pidSpLearning/types.ts`
- Modify: `web-react/src/helpers/modelEvidence/modelEvidenceApi.ts`
- Modify: `web-react/src/helpers/pidSpLearning/pidSpLearningApi.ts`
- Modify affected learning component/tests imports
- Generate: `web-react/schema/contracts/learning.schema.json`
- Generate: `web-react/src/helpers/contracts/learning.gen.ts`

**Interfaces:**
- Produces all current MPC report/action and PID-SP report DTOs, including distinct durable checkpoint and live predictor model shapes.
- Targeted frontend decoders remain and return generated `ModelEvidenceReport` / `PidSpLearningReport`.

- [ ] **Step 1: Write failing Pydantic serialization parity tests**

For every current MPC/PID-SP status and action response, validate the existing real producer output and require byte/member parity after `model_dump(mode="json")`. Include idle, collecting, insufficient excitation, evaluating, active, fallback, structured failure, FOPDT, IPDT, null checkpoint, and malformed checkpoint cases.

- [ ] **Step 2: Pin and reproduce the interrupted strict-load defect**

Add a regression in `tests/unit/common/test_controller_model_state.py`:

```python
store.save("pid_sp", valid_snapshot)
# Simulate persistence failure/corruption while the shared latest cache remains populated.
write_persisted_pid_sp_bytes(malformed_bytes)
with pytest.raises(ControllerModelStateError):
    store.load_strict("pid_sp")
```

Expected current behavior: cached snapshot is returned. Fix `load_strict()` so strict reads always validate persisted state; leave legacy `load()` cache behavior unchanged. Confirm malformed persistence reaches the PID-SP report as structured failure/HTTP 422 while true absence remains `checkpoint: null`/HTTP 200.

- [ ] **Step 3: Implement learning wire models**

Move wire-facing dataclass shapes into `common/web_contracts/learning.py` as `WireModel`s. Keep internal evidence/event dataclasses where they are not sent directly. Define separate discriminated unions:

```python
PidSpCheckpointModel = Annotated[
    FopdtPidSpCheckpoint | IpdtPidSpCheckpoint,
    Field(discriminator="form"),
]
PidSpPredictorModel = Annotated[
    FopdtPidSpPredictor | IpdtPidSpPredictor,
    Field(discriminator="form"),
]
```

The predictor variants intentionally omit durable `revision` and `identified_at_f`; do not weaken the checkpoint model to accommodate them.

- [ ] **Step 4: Validate request and response boundaries**

Use Pydantic models for GET report output and MPC calibration/activation/rollback JSON input/output. Convert Pydantic validation failures into the existing 422 envelopes. Preserve report canonical revision computation over the exact emitted JSON.

- [ ] **Step 5: Regenerate frontend types and keep strict decoders**

Delete both handwritten `types.ts` files. Import generated types in API adapters and views. Keep decoder logic for non-finite values, boolean-as-number rejection, schema version, discriminated model forms, and live/non-live coherence. Decoder helper return annotations must be generated types.

- [ ] **Step 6: Verify learning contracts and browser behavior**

```bash
uv run pytest -q tests/unit/common/test_controller_model_state.py tests/unit/controller/test_learning_report.py tests/unit/controller/test_pid_sp_learning.py tests/web/test_api_model_evidence.py tests/web/test_api_pid_sp_learning.py
cd web-react
bunx rstest run tests/unit/helpers/pidSpLearningApi.test.ts tests/unit/components/dashboard/MpcLearningView.test.tsx tests/unit/components/dashboard/PidSpLearningView.test.tsx tests/unit/components/dashboard/LearningPanel.test.tsx
bunx playwright test --project=panel tests/e2e/dashboard-panel.spec.ts
bun run gen:types:check
bun run typecheck
```

- [ ] **Step 7: Commit**

```bash
jj commit -m "refactor(learning): generate shared wire contracts"
```

---

### Task 5: Generate control, pellet, notify, and WLED JSON contracts

**Files:**
- Create: `common/web_contracts/control.py`
- Modify: `common/web_contracts/registry.py`
- Modify: `common/api_commands.py`
- Modify: `common/control_delta.py`
- Modify: `common/pellets_schema.py`
- Modify: `common/pellets_actions.py`
- Modify: `blueprints/api/routes.py`
- Modify: `blueprints/mobile/socket_io.py`
- Modify relevant Python web tests
- Modify: `web-react/src/helpers/command.ts`
- Modify: `web-react/src/helpers/pellets/pelletTypes.ts`
- Modify: `web-react/src/helpers/pellets/pelletsApi.ts`
- Modify: `web-react/src/helpers/notify/notifyApi.ts`
- Modify: `web-react/src/helpers/notify/wledApi.ts`
- Modify focused frontend tests
- Generate: `web-react/schema/contracts/control.schema.json`
- Generate: `web-react/src/helpers/contracts/control.gen.ts`

**Interfaces:**
- Produces concrete command request/response models, `TimerOptionsPayload`, `NotifyEntry`, `NotifyUpdate`, `PelletProfile`, `PelletLogEntry`, `PelletCurrent`, `PelletDbSchema`, `PelletProfileFields`, pellet action requests, `WledDevice`, `WledDiscoverResponse`, `WledActionResponse`, and socket/REST pellet wrappers.
- Keeps `CommandClient`, `CommandResult`, `PelletActionResult`, browser-normalized result wrappers, and notify edit/view state handwritten.

- [ ] **Step 1: Add failing request/response parity tests**

Cover every JSON command used by `CommandClient`, every pellet action used by `pelletsApi.ts`, notify set operations, WLED discover/test/push, and socket pellet payload. Assert booleans are not accepted as numeric fields and non-finite floats cannot serialize.

- [ ] **Step 2: Implement and register the control bundle**

Use discriminated unions for command/action names. Reuse `PelletDbSchema`, `PelletProfile`, `PelletCurrent`, and `PelletLogEntry` rather than duplicating them. Model intentional open-ended notify `fields` as `dict[str, JsonValue]`; do not use `Any`.

- [ ] **Step 3: Validate Python boundaries without changing dispatch behavior**

Validate incoming JSON once before existing dispatch and emit concrete response models. Preserve RFC 7396/control-delta semantics, FIFO ordering, HTTP status codes, and device-specific WLED fields.

- [ ] **Step 4: Regenerate and remove handwritten shared DTOs**

Import generated wire types in the four helper areas. Keep local action/result unions that describe browser control flow rather than server JSON.

- [ ] **Step 5: Verify**

```bash
uv run pytest -q tests/unit/common/test_pellets_schema.py tests/web/test_api_pellets.py tests/web/test_api_cmd_requires_post.py tests/web/test_socketio_app_data.py tests/web/test_socket_warnings_payload.py
cd web-react
bunx rstest run tests/unit/helpers/command.test.ts tests/unit/helpers/pellets tests/unit/helpers/notify
bunx playwright test tests/e2e/pellets.spec.ts tests/e2e/notify.spec.ts tests/e2e/wled-editor.spec.ts
bun run gen:types:check
bun run typecheck
```

- [ ] **Step 6: Commit**

```bash
jj commit -m "refactor(control): generate command and pellet contracts"
```

---

### Task 6: Generate wizard and probe-map contracts

**Files:**
- Create: `common/web_contracts/wizard.py`
- Modify: `common/web_contracts/registry.py`
- Modify: `blueprints/api_wizard/routes.py`
- Modify: `blueprints/api/probe_map_actions.py`
- Modify relevant Python wizard/probe tests
- Modify: `web-react/src/helpers/wizard/wizardTypes.ts`
- Modify: `web-react/src/helpers/wizard/probeTypes.ts`
- Modify: `web-react/src/helpers/wizard/i2cBusTypes.ts`
- Modify: `web-react/src/helpers/wizard/wizardApi.ts`
- Modify: `web-react/src/helpers/probes/probeMapTypes.ts`
- Modify: `web-react/src/helpers/probes/probeMapApi.ts`
- Modify focused frontend wizard/probe tests
- Generate: `web-react/schema/contracts/wizard.schema.json`
- Generate: `web-react/src/helpers/contracts/wizard.gen.ts`

**Interfaces:**
- Produces `WizardState`, `WizardDraftRequest`, `WizardFinishRequest`, `ModuleValuesRequest`, `ModuleValues`, `ScanRequest`, `ScanResult`, `InstallStatus`, `InstallLog`, `RowsResult`, `BtScanRow`, `ThermoworksRow`, `BusKindsValidationRequest`, `BusKindsValidationResponse`, `ProbeProfile`, `ProbeDevice`, `Probe`, `ProbeMap`, `ProbeConfigField`, `ProbeModuleData`, and `ProbeModuleCatalog`.
- The local mutable `WizardWorking` state remains handwritten, but `saveDraft` and `finishWizard` construct generated `WizardDraftRequest` / `WizardFinishRequest` values from it.

- [ ] **Step 1: Add failing endpoint parity tests**

Use every wizard route and probe-map route to validate both successful and rejected JSON. Pin all I2C bus discriminator variants and the distinction between absent, null, and empty values.

- [ ] **Step 2: Implement static Pydantic models plus manifest metadata models**

Reuse the settings schema's I2C bus union instead of defining a second one. Model plugin/module metadata with explicit known members and an intentional extension map only where manifests demonstrably carry plugin-specific config.

- [ ] **Step 3: Validate route inputs/outputs and regenerate**

Switch route bodies and responses to Pydantic. Replace shared TS declarations with imports from `wizard.gen.ts`. Keep reducer results, hook state, and UI-only scan grouping logic handwritten. `WizardWorking` is local; the JSON submitted by `saveDraft` and `finishWizard` is typed as the generated request model.

- [ ] **Step 4: Verify**

```bash
uv run pytest -q tests/web/test_api_wizard.py tests/web/test_api_probe_map.py
cd web-react
bunx rstest run tests/unit/helpers/wizard tests/unit/helpers/probes
bunx playwright test tests/e2e/wizard.spec.ts tests/e2e/probes.spec.ts
bun run gen:types:check
bun run typecheck
```

- [ ] **Step 5: Commit**

```bash
jj commit -m "refactor(wizard): generate probe and setup contracts"
```

---

### Task 7: Generate files, recipes, history, and metrics contracts

**Files:**
- Create: `common/web_contracts/content.py`
- Modify: `common/web_contracts/registry.py`
- Modify: `blueprints/api_files/routes.py`
- Modify: `blueprints/api_files/cookfile_api.py`
- Modify: `blueprints/api_files/recipes_api.py`
- Modify: `blueprints/api_history/routes.py`
- Modify: `blueprints/api_metrics/routes.py`
- Modify relevant Python web tests
- Modify: `web-react/src/helpers/files/fileTypes.ts`
- Modify: `web-react/src/helpers/files/cookfileApi.ts`
- Modify: `web-react/src/helpers/files/recipeTypes.ts`
- Modify: `web-react/src/helpers/files/recipeApi.ts`
- Modify: `web-react/src/helpers/history/historyApi.ts`
- Modify: `web-react/src/helpers/metrics/metricsTypes.ts`
- Modify: `web-react/src/helpers/metrics/metricsApi.ts`
- Modify focused frontend tests
- Generate: `web-react/schema/contracts/content.schema.json`
- Generate: `web-react/src/helpers/contracts/content.gen.ts`

**Interfaces:**
- Produces `FileListItem`, `FileListing`, `FileErrorDetail`, `CookFileMetadata`, `CookFileEvent`, `CookFileTotals`, `CookFileComment`, `CookFileAsset`, `CookFileLabels`, `CookFileDetail`, `RecipeMetadata`, `Ingredient`, `Instruction`, `RecipeStep`, `RecipeAsset`, `RecipeDetail`, concrete recipe JSON mutation requests, `HistoryPoint`, `HistoryDataset`, `HistoryProbeMapper`, `HistoryGraphLabels`, `HistoryAnnotation`, `HistoryChartData`, `MetricRecord`, and `MetricsPayload`.
- Keeps listing query options, `FormData`, download bytes, file handles, and browser-normalized error/result wrappers handwritten.

- [ ] **Step 1: Add failing JSON endpoint parity tests**

Cover file listings, cookfile detail/comments/assets metadata, recipe read/write JSON sections, history empty/non-empty datasets and annotations, metrics empty/non-empty payloads, and error envelopes. Do not model multipart bytes as JSON.

- [ ] **Step 2: Implement and register content models**

Use explicit nested models for graph labels, probe maps, events, totals, recipe steps, ingredients, instructions, assets, history points/datasets/annotations, and metric records. Use `JsonValue` only for fields whose server intentionally forwards arbitrary stored JSON.

- [ ] **Step 3: Validate route JSON and regenerate frontend types**

Replace response casts and handwritten shared declarations. Keep client parsing of headers, download URLs, chart adaptation, and normalized failures local.

- [ ] **Step 4: Verify**

```bash
uv run pytest -q tests/web/test_api_files_listing.py tests/web/test_api_files_cookfile_read.py tests/web/test_api_files_cookfile_write.py tests/web/test_api_files_cookfile_comments.py tests/web/test_api_files_cookfile_assets.py tests/web/test_api_files_recipes_read.py tests/web/test_api_files_recipes_write.py tests/web/test_api_files_recipes_assets.py tests/web/test_api_history.py tests/web/test_api_metrics.py
cd web-react
bunx rstest run tests/unit/helpers/files tests/unit/helpers/history tests/unit/helpers/metrics
bunx playwright test tests/e2e/cookfiles.spec.ts tests/e2e/recipes.spec.ts tests/e2e/history.spec.ts tests/e2e/metrics.spec.ts
bun run gen:types:check
bun run typecheck
```

- [ ] **Step 5: Commit**

```bash
jj commit -m "refactor(content): generate file and history contracts"
```

---

### Task 8: Generate admin, updater, tuner, and log-metadata contracts

**Files:**
- Create: `common/web_contracts/operations.py`
- Modify: `common/web_contracts/registry.py`
- Modify: `blueprints/api_admin/admin_api.py`
- Modify: `blueprints/api_admin/routes.py`
- Modify: `blueprints/api_update/routes.py`
- Modify: `blueprints/api_tuner/routes.py`
- Modify relevant Python web tests
- Modify: `web-react/src/helpers/admin/adminTypes.ts`
- Modify: `web-react/src/helpers/admin/adminApi.ts`
- Modify: `web-react/src/helpers/update/updateTypes.ts`
- Modify: `web-react/src/helpers/update/updateApi.ts`
- Modify: `web-react/src/helpers/tuner/tunerTypes.ts`
- Modify: `web-react/src/helpers/tuner/tunerApi.ts`
- Modify: `web-react/src/helpers/logs/logTypes.ts`
- Modify: `web-react/src/helpers/logs/logsApi.ts`
- Modify focused frontend tests
- Generate: `web-react/schema/contracts/operations.schema.json`
- Generate: `web-react/src/helpers/contracts/operations.gen.ts`

**Interfaces:**
- Produces `NetworkInterface`, `OsInfo`, `CpuInfo`, `HardwareInfo`, `SystemInfo`, `AdminSettings`, `BackupListing`, `AdminState`, concrete admin action requests/responses, `UpdateState`, `UpdateCheck`, `UpdateStatus`, `BuildLog`, `UpdateStarted`, `TunerPoint`, `TunerSession`, `TrReading`, `Coefficients`, `ProfileInput`, `SavedProfile`, `AutoStatus`, and `LogFamily`.
- Keeps generic normalized `AdminResult<T>`, `UpdateResult<T>`, `TunerResult<T>`, log byte-range deltas, and text downloads handwritten.

- [ ] **Step 1: Add failing success/error parity tests**

Cover all admin/update/tuner GET and POST JSON endpoints consumed by the frontend, including stopped-mode refusals, field errors, absent readings, detached branches, empty logs, and tuner solve failures. For logs, generate only `/api/admin/logs` family metadata; do not model streamed log text.

- [ ] **Step 2: Implement and register operations models**

Use discriminated `Literal` action names and explicit nested system-information models. Preserve intentionally open `/etc/os-release` extras with one tested `extra="allow"` model; all other operations models remain strict.

- [ ] **Step 3: Validate route JSON and regenerate frontend types**

Replace shared wire declarations and anonymous body casts with generated types. Keep adapters' network-error normalization unchanged.

- [ ] **Step 4: Verify**

```bash
uv run pytest -q tests/web/test_api_admin_system.py tests/web/test_api_admin_backups.py tests/web/test_api_admin_maintenance.py tests/web/test_api_admin_log_families.py tests/web/test_api_update.py tests/web/test_api_tuner.py tests/web/test_api_tuner_auto.py
cd web-react
bunx rstest run tests/unit/helpers/admin tests/unit/helpers/update tests/unit/helpers/tuner tests/unit/helpers/logs
bunx playwright test tests/e2e/admin.spec.ts tests/e2e/update.spec.ts tests/e2e/tuner.spec.ts tests/e2e/events.spec.ts
bun run gen:types:check
bun run typecheck
```

- [ ] **Step 5: Commit**

```bash
jj commit -m "refactor(operations): generate admin and tuner contracts"
```

---

### Task 9: Enforce complete shared-contract ownership and delete residual mirrors

**Files:**
- Create: `common/web_contracts/inventory.py`
- Create: `tests/unit/common/web_contracts/test_inventory.py`
- Create: `web-react/tests/unit/helpers/generatedContracts.test.ts`
- Audit: `web-react/src/helpers/**/*.ts`
- Audit: `web-react/src/helpers/**/*.tsx`

**Interfaces:**
- Produces `JSON_WEB_CONTRACT_INVENTORY`, a checked-in executable mapping of every frontend-consumed Python JSON endpoint/socket event to its request/response Pydantic model and generated artifact.
- Produces `NON_JSON_WEB_TRANSPORTS`, the explicit approved exclusions with reasons.

- [ ] **Step 1: Write the inventory before the enforcement implementation**

Each entry has this exact structure:

```python
@dataclass(frozen=True, slots=True)
class JsonWebContract:
    transport: Literal["http", "socketio"]
    name: str
    request: type[BaseModel] | None
    response: type[BaseModel]
    bundle: str
```

List every frontend-consumed JSON route and Socket.IO event. List approved exclusions separately with `name`, `transport`, and a non-empty reason.

- [ ] **Step 2: Add failing completeness checks**

Tests must assert:

1. every inventory model is registered in exactly one schema bundle;
2. every schema bundle has a committed schema and generated TS artifact;
3. generated TS exports every registered model title;
4. no migrated helper source declares a Python-owned wire `interface`/`type`;
5. no generated file is edited by hand or stale;
6. no duplicate exported contract name exists across generated bundles;
7. the non-JSON exclusion list contains only the four approved categories.

Use a small AST-based TypeScript inventory script rather than a raw source-text substring test; type aliases for local state remain legal.

- [ ] **Step 3: Delete residual mirrors and repair imports**

Use LSP references before deleting or renaming exported symbols. Remove old settings, controller, learning, live-state, pellet, wizard, file, admin, update, tuner, metrics, history, and log wire declarations. Do not delete local view/normalized-result types from the approved exclusion list.

- [ ] **Step 4: Run the inventory and all generator checks**

```bash
uv run pytest -q tests/unit/common/web_contracts
cd web-react
bun run gen:types:check
bunx rstest run tests/unit/helpers/generatedContracts.test.ts
bun run typecheck
```

- [ ] **Step 5: Commit**

```bash
jj commit -m "test(contracts): enforce Pydantic web ownership"
```

---

### Task 10: Aggregate verification and real-browser smoke

**Files:**
- All changed files only; no opportunistic refactors.

**Interfaces:**
- Consumes every generated bundle and migrated API/socket boundary.
- Produces no new compatibility layer.
- This verification task intentionally creates no commit when clean. Any task-caused correction is returned to its owning task, reviewed, committed there, and then this full gate restarts.

- [ ] **Step 1: Prove generated artifacts are current from a clean tree**

```bash
cd web-react
bun run gen:types:check
```

Then run `bun run gen:types`, verify it changes zero tracked files, and confirm the Jujutsu working copy remains clean.

- [ ] **Step 2: Run focused Python contract suites together**

```bash
uv run pytest -q \
  tests/unit/common/web_contracts \
  tests/unit/common/test_settings_schema.py \
  tests/unit/common/test_pellets_schema.py \
  tests/unit/common/test_current_schema.py \
  tests/unit/common/test_controller_model_state.py \
  tests/unit/controller/test_learning_report.py \
  tests/unit/controller/test_pid_sp_learning.py \
  tests/web/test_api_settings_update.py \
  tests/web/test_api_model_evidence.py \
  tests/web/test_api_pid_sp_learning.py \
  tests/web/test_api_pellets.py \
  tests/web/test_api_wizard.py \
  tests/web/test_api_probe_map.py \
  tests/web/test_api_files_listing.py \
  tests/web/test_api_history.py \
  tests/web/test_api_metrics.py \
  tests/web/test_api_admin_system.py \
  tests/web/test_api_update.py \
  tests/web/test_api_tuner.py \
  tests/web/test_socketio_app_data.py \
  tests/web/test_socket_dash_payload_fields.py
```

- [ ] **Step 3: Run focused frontend contracts together**

```bash
cd web-react
bunx rstest run tests/unit/helpers tests/unit/components/dashboard
bunx playwright test --project=panel tests/e2e/dashboard-panel.spec.ts
```

- [ ] **Step 4: Run repository quality gates**

```bash
uv run ruff check .
uv run pytest -q
cd web-react
bun run typecheck
bun run test
bun run build
bun run lint
```

If a gate fails, reproduce it on the plan's parent before attributing it. Do not suppress or reformat unrelated files.

- [ ] **Step 5: Smoke-test real JSON boundaries in Chromium**

Using the normal development/Playwright stack at 800×480 and 1280×720, exercise:

1. Socket dashboard cold start and a live update.
2. PID, PID-SP, and MPC dashboard learning states.
3. Settings read/write and controller switch.
4. Wizard state/draft/module-values and probe map.
5. Pellet read/action, notify edit, and WLED response.
6. Cookfile/recipe/history/metrics pages.
7. Admin/update/tuner pages.
8. One malformed safety-sensitive learning response to confirm the retained decoder rejects it without stale data.

The smoke must use production frontend adapters and Python route/socket serializers, not direct model construction.

- [ ] **Step 6: Request final code review**

Review specifically for:

- any Python/web JSON shape without a registered Pydantic model;
- any handwritten TypeScript mirror of a Python-owned wire type;
- generated artifacts whose source is not the Pydantic registry;
- duplicate dynamic controller/config typing;
- accidental modeling of frontend-local or non-JSON transports;
- weakened strictness, finite-number handling, omission/null semantics, or extra-field policy;
- route/socket serialization overhead in the 1 Hz dashboard path;
- changed HTTP statuses, envelopes, authority, or stale-response behavior;
- the `load_strict()` persisted-state regression.

Resolve every Critical or Important finding and rerun the finding's focused contract plus Steps 1–5.

- [ ] **Step 7: Confirm clean delivery state**

Use Jujutsu-safe status inspection. The working copy must contain no uncommitted changes, stale schemas, stale generated TypeScript, obsolete wire declarations, temporary generator outputs, or generated evidence artifacts. Do not move bookmarks or push unless explicitly requested.
