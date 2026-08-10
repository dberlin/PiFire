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

## Deferred shared integration

Task 7 intentionally did not edit `common/web_contracts/registry.py`, `common/web_contracts/export.py`, `web-react/schema/contracts/manifest.json`, any generated schema, or any `*.gen.ts` file.

The serialized integration wave must add this import to `common/web_contracts/registry.py`:

```python
from .content import (
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
    RecipeAssetAssignmentRequest,
    RecipeAssetsData,
    RecipeBody,
    RecipeDetail,
    RecipeIngredientAddRequest,
    RecipeIngredientDeleteRequest,
    RecipeIngredientUpdateRequest,
    RecipeInstructionAddRequest,
    RecipeInstructionDeleteRequest,
    RecipeInstructionUpdateRequest,
    RecipeMetadata,
    RecipeMetadataFields,
    RecipeMetadataUpdateRequest,
    RecipeStep,
    RecipeStepDeleteRequest,
    RecipeStepInsertRequest,
    RecipeStepUpdateRequest,
    RecipeTriggerTemperatures,
)
```

It must add this bundle in bundle-name order:

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
        RecipeAssetAssignmentRequest,
        RecipeAssetsData,
        RecipeBody,
        RecipeDetail,
        RecipeIngredientAddRequest,
        RecipeIngredientDeleteRequest,
        RecipeIngredientUpdateRequest,
        RecipeInstructionAddRequest,
        RecipeInstructionDeleteRequest,
        RecipeInstructionUpdateRequest,
        RecipeMetadata,
        RecipeMetadataFields,
        RecipeMetadataUpdateRequest,
        RecipeStep,
        RecipeStepDeleteRequest,
        RecipeStepInsertRequest,
        RecipeStepUpdateRequest,
        RecipeTriggerTemperatures,
    ),
    typescript_output="content.gen.ts",
),
```

Then run:

```bash
cd web-react
bun run gen:types
```

This must produce/update exactly:

- `web-react/schema/contracts/content.schema.json`
- `web-react/src/helpers/contracts/content.gen.ts`
- `web-react/schema/contracts/manifest.json`

## Deferred validation

Per the concurrent-wave constraint, no tests, formatters, linters, builds, typecheck, Playwright, or generation were run. The integration wave must run these commands verbatim:

```bash
python -m pytest -q tests/web/test_api_files_listing.py tests/web/test_api_files_cookfile_read.py tests/web/test_api_files_cookfile_write.py tests/web/test_api_files_cookfile_comments.py tests/web/test_api_files_cookfile_assets.py tests/web/test_api_files_recipes_read.py tests/web/test_api_files_recipes_write.py tests/web/test_api_files_recipes_assets.py tests/web/test_api_history.py tests/web/test_api_metrics.py
cd web-react
bunx rstest run tests/unit/helpers/files tests/unit/helpers/history tests/unit/helpers/metrics
bunx playwright test tests/e2e/cookfiles.spec.ts tests/e2e/recipes.spec.ts tests/e2e/history.spec.ts tests/e2e/metrics.spec.ts
bun run gen:types:check
bun run typecheck
```

The focused Python contract test should also be run after shared registration/generation:

```bash
python -m pytest -q tests/unit/common/web_contracts/test_content.py
```

## Concerns

- Frontend imports intentionally target the not-yet-generated `content.gen.ts`; TypeScript compilation remains expected to fail until the serialized integration wave registers and generates the content bundle.
- The route boundary validators deliberately preserve legacy optional/extra archive fields and current omission/null behavior. Tightening those extensible records would reject valid older archives.
- No shared generator or registry state was captured in the Task 7 implementation commit.
