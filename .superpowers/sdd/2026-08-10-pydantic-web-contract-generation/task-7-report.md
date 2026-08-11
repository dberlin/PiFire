# Task 7 Report

## Result

Implemented the content-domain Pydantic contracts and JSON-boundary validation for managed file listings, cookfile detail/chart/write responses, recipe detail/write responses, history charts, and metrics. Migrated the frontend JSON wire declarations and their direct consumers to the future generated `content.gen.ts` module. Handwritten request/error normalization, FormData upload helpers, download URLs, text/range handling, and browser-normalized chart types remain handwritten.

Implementation commit: `1f8b4412` (`noyzrtmn`) — `refactor(content): generate file and history contracts`.

## Source and tests

- Added `common/web_contracts/content.py` with strict finite-number and strict integer constraints, exact literals, omission-aware serialization, and extensible archive/detail records.
- Added contract validation tests before production wiring in `tests/unit/common/web_contracts/test_content.py` and response-parity assertions in the focused Flask tests.
- Validated the relevant Flask JSON response boundaries and concrete recipe mutation request bodies without changing existing status codes, messages, ordering, archive semantics, metrics scaling, download behavior, or non-JSON routes.
- Removed handwritten JSON wire declarations from the content helpers and migrated helper/component/unit-test imports directly to `../../helpers/contracts/content.gen` as appropriate. Deleted the obsolete `recipeTypes.ts`; kept `FileKind`, pagination choices, normalized result wrappers, request-error classes, URLs, uploads, downloads, and chart-renderer types handwritten.
- Used LSP references before changing exported TypeScript declarations and migrated every discovered direct consumer.

## Shared integration

The implementation commit intentionally excluded shared generator state. The serialized integration wave subsequently registered the content bundle. Review fix round 1 replaced the overly broad `RecipeAssetAssignmentRequest` entry with the two section-specific models. The current exact bundle is:

```python
ContractBundle(
    name="content",
    models=(
        AssetNamesData,
        ContentErrorData,
        ContentErrorEnvelope,
        CookFileAsset,
        CookFileAssetsData,
        CookFileChartData,
        CookFileComment,
        CookFileDetail,
        CookFileEvent,
        CookFileLabelData,
        CookFileLabels,
        CookFileMetadata,
        CookFileTotals,
        EmptyCookFileTotals,
        FileErrorDetail,
        FileListItem,
        FileListing,
        FilenameData,
        HistoryAnnotation,
        HistoryAnnotationLabel,
        HistoryChartData,
        HistoryDataset,
        HistoryGraphLabels,
        HistoryPoint,
        HistoryProbeMapper,
        Ingredient,
        Instruction,
        MetricRecord,
        MetricsPayload,
        RecipeAsset,
        RecipeAssetsData,
        RecipeBody,
        RecipeDetail,
        RecipeIndexedAssetAssignmentRequest,
        RecipeIngredientAddRequest,
        RecipeIngredientDeleteRequest,
        RecipeIngredientUpdateRequest,
        RecipeInstructionAddRequest,
        RecipeInstructionDeleteRequest,
        RecipeInstructionUpdateRequest,
        RecipeMetadata,
        RecipeMetadataFields,
        RecipeMetadataUpdateRequest,
        RecipeSplashAssetAssignmentRequest,
        RecipeStep,
        RecipeStepDeleteRequest,
        RecipeStepInsertRequest,
        RecipeStepRequest,
        RecipeStepUpdateRequest,
        RecipeTriggerTemperatures,
        RecipeTriggerTemperaturesRequest,
    ),
    typescript_output="content.gen.ts",
),
```

Generation now maintains:

- `web-react/schema/contracts/content.schema.json`
- `web-react/src/helpers/contracts/content.gen.ts`
- the `content.schema.json` to `content.gen.ts` entry in `web-react/schema/contracts/manifest.json`

`common/web_contracts/export.py` required no Task 7-specific change.

## Original-wave validation deferral

The concurrent implementation wave intentionally did not execute generation or validation. Review fix round 1 completed the focused checks recorded below; Playwright remains outside this request-boundary review fix.

## Concerns

- The route boundary validators deliberately preserve legacy optional/extra archive fields and current omission/null behavior. Tightening those extensible records would reject valid older archives.
- The first full focused Python run exposed the existing xdist/shared-`static/img/tmp` cleanup race in `test_uploaded_asset_is_served_from_static_img_tmp` (one transient 404). The isolated test passed, and an immediate full-suite rerun passed all 261 tests.
- Regeneration also refreshed concurrent control artifacts because other tasks had changed the registered control models. Those unrelated control paths are intentionally excluded from the Task 7 path-limited fix commit.

## Review fix round 1

Fix commit: `9bf4a9e7` (`tvxtvktz`) — `fix(content): enforce recipe request shapes`.

The review found three request-boundary gaps and all were fixed at the source:

1. Ingredient and instruction action handlers now strict-validate the original JSON body against the selected concrete action model before calling `recipes_api`, so forbidden extra members cannot bypass `extra="forbid"`.
2. `/recipes/metadata` now validates the complete `RecipeMetadataUpdateRequest` and passes only `mutation.fields.model_dump(mode="json", exclude_unset=True)` to `set_metadata`; fractional integer fields are rejected without modifying the archive.
3. Recipe asset writes now use `RecipeSplashAssetAssignmentRequest` (no `index` field) or `RecipeIndexedAssetAssignmentRequest` (required strict integer `index`). The frontend helper exposes matching overloads, and splash calls no longer pass an `undefined` placeholder.

### RED evidence

```bash
python -m pytest -q tests/unit/common/web_contracts/test_content.py::test_recipe_asset_assignment_requires_the_section_specific_index_shape tests/web/test_api_files_recipes_write.py::test_update_metadata_rejects_fractional_integer_fields_without_writing tests/web/test_api_files_recipes_write.py::test_recipe_mutations_reject_unknown_members_without_writing tests/web/test_api_files_recipes_assets.py::test_splash_rejects_any_index_member
```

Result: `10 failed`, covering both invalid asset shapes, the fractional metadata write, and all six ingredient/instruction action variants.

```bash
cd web-react
bunx rstest run tests/unit/helpers/files/recipeApi.test.ts
```

Result: `1 failed | 14 passed`; the splash call serialized the fourth argument as an `index`.

### Generation and GREEN evidence

```bash
cd web-react
bun run gen:types
```

Result: exit 0; regenerated `content.schema.json` and `content.gen.ts` with the two section-specific request types.

```bash
python -m pytest -q tests/unit/common/web_contracts/test_content.py::test_recipe_asset_assignment_requires_the_section_specific_index_shape tests/web/test_api_files_recipes_write.py::test_update_metadata_accepts_strict_integer_and_string_fields tests/web/test_api_files_recipes_write.py::test_update_metadata_rejects_fractional_integer_fields_without_writing tests/web/test_api_files_recipes_write.py::test_recipe_mutations_reject_unknown_members_without_writing tests/web/test_api_files_recipes_assets.py::test_splash_rejects_any_index_member tests/web/test_api_files_recipes_assets.py::test_assets_requires_an_int_index_for_ingredients_and_instructions
```

Result: `12 passed in 3.58s`.

```bash
python -m pytest -q tests/unit/common/web_contracts/test_content.py tests/web/test_api_files_listing.py tests/web/test_api_files_cookfile_read.py tests/web/test_api_files_cookfile_write.py tests/web/test_api_files_cookfile_comments.py tests/web/test_api_files_cookfile_assets.py tests/web/test_api_files_recipes_read.py tests/web/test_api_files_recipes_write.py tests/web/test_api_files_recipes_assets.py tests/web/test_api_history.py tests/web/test_api_metrics.py
```

Final result: `261 passed in 6.04s`. The immediately preceding run had `260 passed, 1 failed` from the shared static-image cleanup race noted above; the failing test passed alone (`1 passed in 7.50s`) before the full clean rerun.

```bash
cd web-react
bunx rstest run tests/unit/helpers/files tests/unit/helpers/history tests/unit/helpers/metrics
bun run gen:types:check
bun run typecheck
```

Results:

- Rstest: `5 passed` files, `46 passed` tests.
- Contract drift: `Pydantic web contract artifacts are up to date`, generated defaults and TypeScript up to date, exit 0.
- TypeScript: exit 0.

```bash
ruff check blueprints/api_files/routes.py common/web_contracts/content.py common/web_contracts/registry.py tests/unit/common/web_contracts/test_content.py tests/web/test_api_files_recipes_write.py tests/web/test_api_files_recipes_assets.py
cd web-react
bunx biome check src/helpers/files/recipeApi.ts src/components/recipes/RecipeAssetManager.tsx tests/unit/helpers/files/recipeApi.test.ts
```

Results: Ruff `All checks passed`; Biome checked 3 files with no fixes required.

## Review fix round 2

Fix commit: `4c00bf80` (`lkozkyos`) — `fix(content): preserve legacy comment delete shape`.

The legacy comment form sends its `text` input for both update and delete actions. `CookFileCommentDeleteRequest` initially omitted that known field, so strict validation rejected a delete for an unknown ID with 400 before the service could preserve the historical 404 `comment_not_found` result. The delete variant now accepts an optional strict string `text` while continuing to forbid every unrelated member.

### RED evidence

```bash
python -m pytest -q 'tests/web/test_api_files_cookfile_comments.py::test_unknown_comment_id_is_404_not_a_false_success[delete]' tests/web/test_api_files_cookfile_comments.py::test_delete_comment_accepts_legacy_text_but_rejects_unrelated_extras
```

Result: `1 failed, 1 passed`; the legacy delete-with-text case returned 400 instead of 404, while the unrelated-extra rejection remained strict.

### GREEN evidence

The same focused command passed `2 passed in 2.95s`.

```bash
python -m pytest -q tests/unit/common/web_contracts/test_content.py tests/web/test_api_files_cookfile_comments.py
cd web-react
bun run gen:types:check
bun run typecheck
```

Results:

- Python: `26 passed in 3.60s`.
- Contract drift: all Pydantic artifacts, defaults, and generated TypeScript up to date.
- TypeScript: exit 0.

```bash
ruff format --check common/web_contracts/content.py tests/web/test_api_files_cookfile_comments.py
ruff check common/web_contracts/content.py tests/web/test_api_files_cookfile_comments.py
```

Results: both files formatted; Ruff `All checks passed`.

## Final-review step validation fix

Fix commit: `d9d75b5f` (`rmnpzkvs`) — `fix(content): validate original recipe step payloads`.

The step route previously rebuilt reduced dictionaries for insert/update/delete and `_validated_step_fields` rebuilt both `step` and `trigger_temps`. Those projections silently erased forbidden typo members before Pydantic saw them. The route now selects the concrete request model from `action`, validates the original body, and passes the validated request step to `recipes_api.update_step`.

Because archive response steps are intentionally extensible and finite-valued while recipe mutation inputs historically require strict integers, `RecipeStepRequest` and `RecipeTriggerTemperaturesRequest` provide the strict nested write shape without tightening old archive reads. Existing error fields remain unchanged: scalar trigger failures still report `trigger_temps`, while an actual forbidden nested member reports its own name.

### RED evidence

```bash
python -m pytest -q tests/web/test_api_files_recipes_write.py::test_step_mutations_reject_outer_typo_members_without_writing tests/web/test_api_files_recipes_write.py::test_update_step_rejects_nested_typo_members_without_writing
```

Result: `5 failed`; insert, update, and delete outer typos plus step-level and trigger-level nested typos all returned 200 and reached archive mutation.

### Integrated GREEN evidence

The first post-fix Python attempt was temporarily blocked at app import while Task 6 renamed the wizard response models; no retired private name was restored or worked around. After canonical wizard commit `8abc7ae0` (`qovyuptp`) landed, generation and the full focused checks were rerun on integrated ancestry:

```bash
python -m pytest -q tests/unit/common/web_contracts/test_content.py tests/web/test_api_files_recipes_write.py
cd web-react
bunx rstest run tests/unit/helpers/files/recipeApi.test.ts
bun run gen:types:check
bun run typecheck
```

Results:

- Python: `94 passed in 3.66s`.
- Rstest: `1 passed` file, `15 passed` tests.
- Contract drift: all Pydantic artifacts, defaults, and generated TypeScript up to date.
- TypeScript: exit 0.

```bash
ruff format --check blueprints/api_files/routes.py common/web_contracts/content.py common/web_contracts/registry.py common/web_contracts/inventory.py tests/unit/common/web_contracts/test_content.py tests/web/test_api_files_recipes_write.py
ruff check blueprints/api_files/routes.py common/web_contracts/content.py common/web_contracts/registry.py common/web_contracts/inventory.py tests/unit/common/web_contracts/test_content.py tests/web/test_api_files_recipes_write.py
```

Results: all 6 files formatted; Ruff `All checks passed`.

The path-limited fix commit also captures the combined `registry.py` and `inventory.py` changes by explicit coordination with Task 6; its focused inventory test remains in the Task 6 commit.

These are the latest results from the additional current-ancestry rerun requested after shared wizard integration was confirmed; the current workspace resolves stable change `qovyuptp` to commit `8abc7ae0`.

## Final-review action dispatch fix

Fix commit: `531e637e` (`kynsxowo`) — `fix(content): reject non-string recipe step actions`.

The action-selected request-model dispatch initially used a dictionary lookup before checking the JSON action's scalar type. Array and object actions are unhashable in Python, so they raised `TypeError` instead of retaining the route's 400 `bad_request` action error. The route now rejects every non-string action before lookup; the existing unknown-string path is unchanged.

### RED evidence

```bash
python -m pytest -q tests/web/test_api_files_recipes_write.py::test_step_mutations_reject_non_string_actions_without_writing
```

Result: `2 failed`; both the array and object cases raised `TypeError` at the request-model lookup.

### GREEN evidence

The focused regression passed `2 passed in 2.93s` with the exact 400 envelope and unchanged archive bytes. The full current-ancestry gates were then rerun:

```bash
python -m pytest -q tests/unit/common/web_contracts/test_content.py tests/web/test_api_files_recipes_write.py
cd web-react
bunx rstest run tests/unit/helpers/files/recipeApi.test.ts
bun run gen:types:check
bun run typecheck
```

Results:

- Python: `96 passed in 3.70s`, including the pre-existing unknown-string action case.
- Rstest: `1 passed` file, `15 passed` tests.
- Contract drift: all Pydantic artifacts, defaults, and generated TypeScript up to date.
- TypeScript: exit 0.

```bash
ruff format --check blueprints/api_files/routes.py tests/web/test_api_files_recipes_write.py
ruff check blueprints/api_files/routes.py tests/web/test_api_files_recipes_write.py
```

Results: both files formatted; Ruff `All checks passed`.
