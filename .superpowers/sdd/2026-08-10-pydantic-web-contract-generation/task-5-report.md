# Task 5 Report

## Result

DONE — source and tests are committed; shared registration, generation, and validation remain intentionally deferred to the serialized integration gate.

Implementation commit: `0e16235661e16e2f952b0c044eacc08d773115f3` (`ozuqtkyxosvtqzpzxnqznkwqtzkzxmox`)

## Changed paths

Python contracts, canonical pellet ownership, boundaries, and tests:

- `common/web_contracts/control.py` (created)
- `common/web_contracts/core.py`
- `common/pellets_schema.py`
- `common/pellets_actions.py`
- `common/datastore.py`
- `common/defaults.py`
- `blueprints/api/routes.py`
- `blueprints/mobile/socket_io.py`
- `tests/unit/common/web_contracts/test_control.py` (created)
- `tests/unit/common/test_pellets_schema.py`
- `tests/unit/common/test_pellets_shape_digest.py`
- `tests/unit/common/test_pellets_migration_v2.py`
- `tests/unit/datastore/test_pellets_shape_migration.py`
- `tests/web/test_api_pellets.py`
- `tests/web/test_page_api.py`
- `tests/web/test_socketio_app_data.py`

Frontend helpers and direct consumers:

- `web-react/src/helpers/command.ts`
- `web-react/src/helpers/pellets/pelletsApi.ts`
- `web-react/src/helpers/notify/notifyApi.ts`
- `web-react/src/helpers/notify/notifyState.ts`
- `web-react/src/helpers/notify/wledApi.ts`
- `web-react/src/helpers/useLiveState.ts`
- `web-react/src/components/pellets/CurrentLoadCard.tsx`
- `web-react/src/components/pellets/PelletLog.tsx`
- `web-react/src/components/pellets/ProfileEditor.tsx`
- `web-react/src/components/pellets/PelletsPage.tsx`
- `web-react/src/components/settings/tabs/notifications/WledCard.tsx`
- `web-react/tests/unit/helpers/command.test.ts`
- `web-react/tests/unit/helpers/notify/notifyApi.test.ts`
- `web-react/tests/unit/helpers/notify/notifyState.test.ts`
- `web-react/tests/unit/components/dashboard/Dashboard.test.tsx`
- `web-react/tests/unit/components/pellets/CurrentLoadCard.test.tsx`
- `web-react/tests/unit/components/pellets/PelletLog.test.tsx`
- `web-react/tests/unit/components/pellets/PelletsPage.test.tsx`
- `web-react/tests/unit/components/pellets/ProfileEditor.test.tsx`
- `web-react/tests/unit/helpers/useLiveState.test.tsx`
- `web-react/tests/e2e/notify.spec.ts`

`web-react/src/helpers/pellets/pelletTypes.ts` was already removed by Task 2, so there was no remaining file to modify. `common/api_commands.py` and `common/control_delta.py` required no production change: the existing path dispatch and RFC 7396/FIFO op translation remain authoritative, while the new contracts validate the JSON boundaries before those functions run. Shared registry/export/generated/manifest paths were not edited.

## Implemented contract and boundary behavior

- Added an operation-discriminated `CommandRequest` root model covering every path command emitted by handwritten `CommandClient`. Task 2's `core.CommandResponse` remains the one command response contract; Task 5 does not duplicate it.
- Added `TimerOptionsPayload` with the existing browser-facing `keepWarm` alias. `CommandClient` and `CommandResult` remain handwritten.
- Added JSON-only `NotifyEntry` extras, `NotifyUpdate.fields: dict[str, JsonValue]`, `NotifyListResponse`, and sparse `ControlPatchRequest`/`ControlPatchResponse`. REST and Socket.IO control-patch boundaries strict-validate once before the existing notify-op/RFC 7396 translation. Timer patch rejection, merge semantics, op ordering, and statuses are unchanged.
- Moved the canonical `PelletProfile`, `PelletLogEntry`, `PelletCurrent`, `PelletLastUpdated`, `PelletDbSchema`, and `PELLETDB_SCHEMA_VERSION` ownership to `common/web_contracts/control.py`. `common/pellets_schema.py` now owns only migration/repair/validation mechanics and invokes the canonical model through its module namespace; no model alias or re-export remains.
- Removed Task 2's duplicate pellet storage models from `common/web_contracts/core.py`. The retained `core.PelletSocketPayload` now references canonical `PelletDbSchema`; it must be generated in the control bundle, not the core bundle.
- Added an action-discriminated `PelletActionRequest`, concrete action data/requests, `PelletActionResponse`, and REST wrapper. Both REST and Socket.IO use one strict dispatcher before the existing action handlers. Existing action names, successful envelopes, unknown-action messages, and HTTP 200 behavior remain; boolean ratings are rejected without writing.
- Added strict WLED push/test requests and discover/action responses. Browser-normalized `WledDiscoverResult`/`WledActionResult` remain handwritten. Device-specific discovery fields are preserved, omitted mock/device fields remain omitted, boolean profile numbers are rejected with HTTP 400, and success/error HTTP statuses remain unchanged.
- Frontend response decoding and request construction use generated types without `as` casts. Pellet/socket/notify/WLED direct consumers import the future `control.gen.ts`; handwritten browser control-flow result types remain local.

## Exact integration registration

In `common/web_contracts/registry.py`, import `PelletSocketPayload` from `.core`, import the remaining names from `.control`, and add the following sorted bundle before `controller`:

```python
ContractBundle(
    name="control",
    models=(
        AddPelletProfileData,
        AddPelletProfileRequest,
        CommandRequest,
        ControlPatchRequest,
        ControlPatchResponse,
        DeletePelletLogRequest,
        DeletePelletProfileRequest,
        EditPelletBrandsRequest,
        EditPelletProfileData,
        EditPelletProfileRequest,
        EditPelletWoodsRequest,
        EmptyPelletActionData,
        HopperCheckRequest,
        LoadPelletProfileRequest,
        ManualOutputCommandRequest,
        ManualPwmCommandRequest,
        NotifyEntry,
        NotifyListResponse,
        NotifyUpdate,
        PelletActionRequest,
        PelletActionResponse,
        PelletCurrent,
        PelletDbSchema,
        PelletLastUpdated,
        PelletLogEntry,
        PelletLogReference,
        PelletProfile,
        PelletProfileFields,
        PelletProfileReference,
        PelletRestData,
        PelletRestResponse,
        PelletSocketPayload,
        PelletVocabularyEdit,
        PrimeCommandRequest,
        SetModeCommandRequest,
        SetPModeCommandRequest,
        SetPrimarySetpointCommandRequest,
        SetSmokePlusCommandRequest,
        SetUnitsCommandRequest,
        SystemCommandRequest,
        TimerKeepWarmCommandRequest,
        TimerOptionsPayload,
        TimerPauseCommandRequest,
        TimerShutdownCommandRequest,
        TimerStartCommandRequest,
        TimerStartWithOptionsCommandRequest,
        TimerStopCommandRequest,
        WledActionResponse,
        WledDevice,
        WledDiscoverResponse,
        WledPushProfilesRequest,
        WledTestProfileRequest,
    ),
    typescript_output="control.gen.ts",
),
```

At the same gate, remove these retired names from the `.core` registry import and core bundle tuple:

- `PelletCurrentPayload`
- `PelletDatabasePayload`
- `PelletLastUpdatedPayload`
- `PelletLogEntryPayload`
- `PelletProfilePayload`
- `PelletSocketPayload` (remove only from the **core tuple**; import the retained Python wrapper for the control tuple)

Generate/update exactly:

- `web-react/schema/contracts/control.schema.json`
- `web-react/schema/contracts/manifest.json`
- `web-react/src/helpers/contracts/control.gen.ts`
- regenerated `web-react/schema/contracts/core.schema.json`
- regenerated `web-react/src/helpers/contracts/core.gen.ts`

The regenerated core artifacts must no longer contain the retired pellet storage definitions. No export-pipeline code change is required.

## Deferred validation

Per the concurrent-wave constraint, no pytest, Rstest, Playwright, formatter, linter, build, generator, or TypeScript typecheck command was run. LSP references were collected before exported pellet/notify/WLED symbol cutovers, and targeted Python LSP diagnostics are clean for `common/web_contracts/control.py`, `common/web_contracts/core.py`, and `tests/unit/common/web_contracts/test_control.py`. Frontend diagnostics are expected to report only the intentionally absent `control.gen.ts` until integration generates it.

Run after registry integration and generation:

```bash
uv run pytest -q \
  tests/unit/common/web_contracts/test_control.py \
  tests/unit/common/test_pellets_schema.py \
  tests/unit/common/test_pellets_migration_v2.py \
  tests/unit/common/test_pellets_shape_digest.py \
  tests/unit/datastore/test_pellets_shape_migration.py \
  tests/web/test_api_pellets.py \
  tests/web/test_api_cmd_requires_post.py \
  tests/web/test_page_api.py \
  tests/web/test_socketio_app_data.py \
  tests/web/test_socket_warnings_payload.py

cd web-react
bunx rstest run \
  tests/unit/helpers/command.test.ts \
  tests/unit/helpers/pellets \
  tests/unit/helpers/notify \
  tests/unit/components/pellets \
  tests/unit/helpers/useLiveState.test.tsx
bunx playwright test \
  tests/e2e/pellets.spec.ts \
  tests/e2e/notify.spec.ts \
  tests/e2e/wled-editor.spec.ts
bun run gen:types:check
bun run typecheck
```

## Concerns

- Until the integration gate updates `registry.py`, importing the registry is expected to fail because it still imports the removed temporary core pellet classes. This is deliberate rather than leaving aliases or duplicate ownership.
- Frontend files intentionally point at the not-yet-generated `control.gen.ts`; they cannot typecheck before the serialized generator gate.
- The integration gate should confirm the TypeScript emitter preserves the RootModel names `CommandRequest` and `PelletActionRequest` as discriminated unions and the alias `TimerOptionsPayload.keepWarm`. If the emitter chooses a structurally equivalent unexpected name, fix generation/registration rather than adding frontend aliases or casts.

## Post-integration review closure

Review-fix commit: `364e04b656124c6bc3ae94f96e10f8d6c4d4c2bc`
(`vwnwxoqzywqrttxmtmluzwoptsvqnvzn`)

The serialized integration gate registered and generated the control bundle, so
the deferred-state notes and concerns above describe only the original
concurrent-wave handoff. The review fix:

- narrows `PrimeCommandRequest.next_mode` and `CommandClient.prime` to the two
  backend-recognized follow-on modes, `startup` and `monitor`; the Prime-and-Stop
  UI now omits the optional segment so the backend selects its default `Stop`
  outcome rather than claiming that `stop`, `smoke`, or `manual` is recognized;
- models successful WLED profile pushes with concrete
  `WledProfileItem {name, number, description}` objects, matching
  `WLEDProfileManager.push_all_profiles()` and the route envelope;
- registers `WledProfileItem` in the control bundle and regenerates
  `control.schema.json` and `control.gen.ts`.

Strict RED evidence was captured before production edits:

- `uv run pytest -q tests/unit/common/web_contracts/test_control.py`:
  3 expected failures (the model accepted `smoke` and `manual`, and rejected
  real WLED profile objects), 42 passed;
- `cd web-react && bun run typecheck`: the two new prime rejection checks
  reported unused `@ts-expect-error` directives before the signature was
  narrowed.

Fresh GREEN evidence after regeneration:

- focused Python boundary suite: 226 passed in 6.20s;
- command/pellet/notify helper suite: 94 passed in 326ms;
- command plus both direct Prime callers: 125 passed in 1.55s;
- `bun run gen:types:check`: all generated Pydantic/settings/TypeScript
  artifacts up to date;
- `bun run typecheck`: exit 0 with no diagnostics;
- LSP diagnostics: clean for `common/web_contracts/control.py`,
  `web-react/src/helpers/command.ts`, and
  `web-react/src/helpers/dashboard/buttonsForMode.ts`.

The repository-wide pytest run was explicitly cancelled at the integration
owner's direction; it is not part of this fix's validation evidence.
