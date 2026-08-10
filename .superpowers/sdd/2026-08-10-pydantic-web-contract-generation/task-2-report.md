# Task 2 Report

## Result

DONE

Implementation commit: `47fe3c5ed68b13330bf90aa12efea165f30c2f44` (`tnwzokqovrtnsqlskonrkrpwvmukoork`)
Aggregate fixture-fix commit: `901b1e8da316125b08f80f467729144bb9afac9b` (`wmzqkyoskorrzlyspsuqoysupwntvsus`)

## Changed paths

Backend and generated contract paths:

- `common/web_contracts/core.py`
- `common/web_contracts/registry.py`
- `blueprints/api/routes.py`
- `blueprints/mobile/socket_io.py`
- `blueprints/spa/routes.py`
- `tests/web/test_socketio_app_data.py`
- `tests/web/test_socket_dash_payload_fields.py`
- `tests/web/test_socket_probe_staleness.py`
- `tests/web/test_spa_caching.py`
- `tests/web/test_control_liveness_not_sticky.py`
- `web-react/schema/contracts/core.schema.json`
- `web-react/schema/contracts/manifest.json`
- `web-react/src/helpers/contracts/core.gen.ts`

Brief-owned frontend paths:

- `web-react/src/helpers/types.ts`
- `web-react/src/helpers/useLiveState.ts`
- `web-react/src/helpers/command.ts`
- `web-react/src/helpers/useWebUiBuild.ts`
- `web-react/src/helpers/dashboard/controlHealth.ts`
- `web-react/src/helpers/shell/warningsApi.ts`
- `web-react/tests/unit/helpers/types.test.ts`
- `web-react/tests/unit/helpers/command.test.ts`
- `web-react/tests/unit/helpers/useWebUiBuild.test.ts`
- `web-react/tests/unit/helpers/dashboard/health.test.ts`
- `web-react/tests/unit/helpers/shell/warningsApi.test.ts`

Expanded clean-cutover paths approved after LSP references showed direct consumers of the retired mirrors:

- `web-react/src/components/dashboard/ControlButtons.tsx`
- `web-react/src/components/dashboard/Dashboard.tsx`
- `web-react/src/components/shell/TimerBar.tsx`
- `web-react/src/components/shell/TimerModal.tsx`
- `web-react/src/helpers/dashboard/buttonsForMode.ts`
- `web-react/src/helpers/dashboard/countdowns.ts`
- `web-react/src/helpers/dashboard/deriveView.ts`
- `web-react/src/helpers/dashboard/health.ts`
- `web-react/src/helpers/dashboard/probeStatus.ts`
- `web-react/src/helpers/demoData.ts`
- `web-react/src/helpers/fixture.ts`
- `web-react/src/helpers/notify/notifyState.ts`
- `web-react/src/helpers/recipes/runStatus.ts`
- `web-react/src/helpers/timer/timerState.ts`
- `web-react/tests/unit/components/dashboard/ControlButtons.test.tsx`
- `web-react/tests/unit/components/dashboard/Dashboard.test.tsx`
- `web-react/tests/unit/components/dashboard/dashboardStyles.test.tsx`
- `web-react/tests/unit/components/recipes/RecipePage.test.tsx`
- `web-react/tests/unit/components/shell/AppShell.test.tsx`
- `web-react/tests/unit/components/shell/TimerBar.controlCycle.test.tsx`
- `web-react/tests/unit/components/shell/TimerBar.test.tsx`
- `web-react/tests/unit/components/shell/TimerModal.test.tsx`
- `web-react/tests/unit/helpers/dashboard/buttonsForMode.test.ts`
- `web-react/tests/unit/helpers/dashboard/countdowns.test.ts`
- `web-react/tests/unit/helpers/dashboard/deriveView.test.ts`
- `web-react/tests/unit/helpers/dashboard/probeStatus.test.ts`
- `web-react/tests/unit/helpers/notify/notifyState.test.ts`
- `web-react/tests/unit/helpers/timer/timerState.test.ts`
- `web-react/tests/unit/helpers/useLiveState.test.tsx`

Expanded pellet and specialized-command clean-cutover paths:

- `web-react/src/helpers/pellets/pelletTypes.ts` (removed)
- `web-react/src/components/pellets/CurrentLoadCard.tsx`
- `web-react/src/components/pellets/PelletLog.tsx`
- `web-react/src/components/pellets/ProfileEditor.tsx`
- `web-react/src/helpers/modelEvidence/modelEvidenceApi.ts`
- `web-react/tests/unit/components/pellets/CurrentLoadCard.test.tsx`
- `web-react/tests/unit/components/pellets/PelletLog.test.tsx`
- `web-react/tests/unit/components/pellets/PelletsPage.test.tsx`
- `web-react/tests/unit/components/pellets/ProfileEditor.test.tsx`

The expansion was required for a no-alias, no-re-export cutover: `helpers/types.ts` now retains only frontend-owned `AccentName`, and the deleted pellet mirror has no remaining importer.

## RED evidence

Required Python command before implementation:

```bash
uv run pytest -q tests/web/test_socketio_app_data.py tests/web/test_socket_dash_payload_fields.py tests/web/test_socket_probe_staleness.py
```

Observed:

```text
ModuleNotFoundError: No module named 'common.web_contracts.core'
3 collection errors in 3.23s
```

Required focused frontend command before generation:

```bash
cd web-react
bunx rstest run \
  tests/unit/helpers/types.test.ts \
  tests/unit/helpers/command.test.ts \
  tests/unit/helpers/useWebUiBuild.test.ts \
  tests/unit/helpers/dashboard/health.test.ts \
  tests/unit/helpers/shell/warningsApi.test.ts
```

Rstest strips type-only imports, so the runtime suite remained green (`6 files, 55 tests`). The compile-time RED was captured with:

```bash
bun run typecheck
```

```text
TS2307: Cannot find module '../../../src/helpers/contracts/core.gen'
TS2307 at all five new generated-contract imports
EXIT=1
```

Aggregate verification exposed one additional RED in a legacy liveness test fixture:

```bash
uv run pytest -q tests/web/test_control_liveness_not_sticky.py
```

```text
5 failed, 2 passed
DashSocketPayload rejected the fixture's empty probe dictionaries with 105 missing-field errors.
```

## GREEN evidence

Final required backend command:

```bash
uv run pytest -q tests/web/test_socketio_app_data.py tests/web/test_socket_dash_payload_fields.py tests/web/test_socket_probe_staleness.py
```

```text
142 passed in 3.39s
```

Final required frontend command:

```bash
cd web-react
bunx rstest run \
  tests/unit/helpers/types.test.ts \
  tests/unit/helpers/command.test.ts \
  tests/unit/helpers/useWebUiBuild.test.ts \
  tests/unit/helpers/dashboard/health.test.ts \
  tests/unit/helpers/shell/warningsApi.test.ts
```

```text
6 files passed, 55 tests passed in 719ms
```

Supplemental producer/consumer checks:

```text
uv run pytest -q tests/web/test_spa_caching.py tests/web/test_api_dismiss_warnings.py tests/web/test_api_cmd_requires_post.py
19 passed in 3.07s

uv run pytest -q tests/web/test_api_mpc_calibration.py
8 passed in 2.89s

bunx rstest run tests/unit/components/pellets/CurrentLoadCard.test.tsx tests/unit/components/pellets/PelletLog.test.tsx tests/unit/components/pellets/PelletsPage.test.tsx tests/unit/components/pellets/ProfileEditor.test.tsx tests/unit/helpers/useLiveState.test.tsx
5 files passed, 52 tests passed in 1.74s
```

The liveness fixture was corrected to build representative probe dictionaries through the real `_get_probe_structure` producer, without weakening production validation:

```bash
uv run pytest -q tests/web/test_control_liveness_not_sticky.py
```

```text
7 passed in 4.78s
```

## Generated drift and typecheck

```bash
bun run gen:types:check
```

```text
Pydantic web contract artifacts are up to date.
Generated web contract TypeScript is up to date.
```

```bash
bun run typecheck
```

```text
$ node node_modules/typescript7/bin/tsc -b
EXIT=0
```

## Self-review

LSP references were collected before renaming the exported `LiveState`, `ProbeData`, `ProbeStatus`, and pellet DTO symbols. Every direct caller was migrated to generated names and imports; no compatibility alias or re-export remains. Socket dash and pellet producers, the web-build endpoint, dismiss-warnings endpoint, control-health response, ordinary command responses, and the specialized MPC calibration response now construct/validate Pydantic models and serialize with JSON aliases. Probe extras remain allowed only on the two explicitly extensible models, absent status members remain absent while explicit nulls survive, and all numeric wire fields reject non-finite floats.

A final reviewer found two Important issues: the always-emitted nullable `modelLearningRevision` was initially generated as optional, and the specialized calibration response remained handwritten. Both were corrected, regenerated, and reverified. No Critical or Important finding remains.
Aggregate verification then found one legacy liveness mock returning invalid empty probe objects. The test now uses the real probe-structure producer; its focused RED/GREEN is recorded above and production validation remains strict.

## Concerns

Task 5 must consolidate the canonical pellet models into its control bundle and migrate imports in one clean cutover, so the generated ownership introduced here does not remain duplicated across active bundles.
