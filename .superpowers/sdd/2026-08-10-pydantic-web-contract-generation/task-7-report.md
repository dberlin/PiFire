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
        RecipeStepUpdateRequest,
        RecipeTriggerTemperatures,
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
