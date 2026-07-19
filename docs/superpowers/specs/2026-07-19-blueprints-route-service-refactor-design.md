# Blueprints Route Service Refactor Design

## Goal

Refactor the remaining large Flask blueprint route handlers into thin route orchestration plus tested domain service/action handlers, while preserving current URLs, response shapes, templates, redirects, file behavior, and runtime side effects.

## Scope

Covered route domains:

1. `blueprints/api/routes.py`
2. `blueprints/pellets/routes.py`
3. `blueprints/history/routes.py`
4. `blueprints/cookfile/routes.py`
5. `blueprints/recipes/routes.py`
6. `blueprints/tuner/routes.py`
7. `blueprints/update/routes.py`
8. `blueprints/wizard/routes.py`

Out of scope:

- Changing route URLs or HTTP methods.
- Changing JSON response contracts.
- Replacing Flask blueprints or app registration.
- Large app-factory work; that remains a later roadmap item.
- Broad error/logging cleanup except where directly needed for extracted code.

## Architecture

Each domain keeps `blueprints/<domain>/routes.py` as the Flask boundary. Route functions should parse request inputs, call a service/action function, and return Flask responses. Business logic, file operations, data-shaping, validation, and action-specific behavior move into domain-local service modules such as `blueprints/<domain>/services.py`.

Long conditional chains become explicit dispatch maps. Use names that make the route boundary clear:

```python
JSON_ACTIONS = {
    "action_name": handle_action_name,
}

FORM_ACTIONS = {
    "submit_name": handle_submit_name,
}
```

For routes keyed by path action plus method, use tuple keys:

```python
ACTION_HANDLERS = {
    ("GET", "status"): get_status,
    ("POST", "save"): post_save,
}
```

Shared helpers are introduced only after duplication appears in at least two migrated domains. The likely first shared helper is cookfile/history event-summary preparation, because both domains currently prepare event totals/comments/chart labels from cookfile-like structures.

## Landing Strategy

This is one broad design, but implementation lands as one PR per route domain. Each PR must be independently reviewable and testable. Do not combine unrelated route domains in a single implementation branch.

Recommended landing order:

1. `api` — JSON response shape is easiest to characterize and lower UI/template risk.
2. `pellets` — medium-size, single domain.
3. `history` — enables shared event-summary helper before cookfile extraction.
4. `cookfile` — largest route; safer after shared event helpers exist.
5. `recipes` — large and file-upload heavy; apply patterns proven by earlier domains.
6. `tuner` — controller-adjacent; isolate after service pattern is stable.
7. `update` — operational behavior; keep late and heavily characterized.
8. `wizard` — highest setup/system risk; keep last.

## Domain Boundaries

### API

`blueprints/api/routes.py` should route actions to small handlers that return dicts/Flask responses matching current behavior. The service layer may call existing datastore/accessor helpers, but must not change response keys or HTTP status behavior.

### Pellets

`blueprints/pellets/routes.py` should separate pellet database reads/writes and action decisions from template rendering. The route remains responsible for choosing `render_template` or `jsonify`.

### History

`blueprints/history/routes.py` should extract history/cookfile data preparation into helper functions. If the same helper is needed by cookfile, place it in a shared module under `blueprints/cookfile/` or `file_mgmt/` only after tests pin both call sites.

### Cookfile

`blueprints/cookfile/routes.py` should split `cookfile_page` and `cookfile_update` into JSON action handlers, form action handlers, and render-data builders. Preserve download/upload response behavior, filenames, error lists, and template names.

### Recipes

`blueprints/recipes/routes.py` should split recipe list, upload, create/update, unit conversion, and asset behavior into service functions. Preserve upload rejection behavior and current recipe JSON shape.

### Tuner

`blueprints/tuner/routes.py` should isolate controller/tuning data access behind service functions. Because it is controller-adjacent, the PR should avoid simultaneous controller runtime changes.

### Update

`blueprints/update/routes.py` should isolate updater/install/status action handling. Avoid changing subprocess/system behavior unless pinned by characterization tests.

### Wizard

`blueprints/wizard/routes.py` should be migrated last. Extract wizard action handlers while preserving setup flow side effects, install status writes, redirects, and rendered templates.

## Testing Strategy

Every domain PR starts with characterization tests against current behavior before extraction. Route-level tests come first because the public contract is the Flask response, not the internal service function.

Minimum assertions per route domain:

- HTTP status code for representative GET and POST paths.
- JSON response keys and important values for JSON routes.
- Template/render behavior where existing tests can observe it.
- Existing error path behavior, especially upload rejection and missing-action handling.
- Side-effect writes mocked or asserted using existing test fixtures where practical.

After route behavior is pinned, add direct service tests only for non-trivial pure logic extracted from the route.

Per-domain verification:

```bash
uv run pytest tests/web -q
uv run pytest tests/unit tests/characterization -q
uv run ruff check .
```

Before merging the full P1 series:

```bash
uv run pytest -q
uv run ruff check .
```

## Error Handling

Route refactors must preserve current error responses. Do not broaden or narrow exception handling as part of the structural extraction unless a characterization test pins both the old and new behavior. If an extracted service needs to report an expected user error, return an explicit result object or tuple that the route converts to the existing Flask response.

## Review Checklist

For each domain PR:

- Route URLs and methods unchanged.
- Public response shapes unchanged.
- Template names unchanged.
- File upload/download behavior unchanged.
- Service module has domain-specific names, not a generic abstraction framework.
- Dispatch maps are explicit and easy to inspect.
- Focused tests fail before extraction if behavior is intentionally broken during local verification, then pass after extraction.
- `uv run ruff check .` passes with the current Ruff baseline.

## Self-Review

- Placeholder scan: no placeholders or deferred requirements remain.
- Scope check: broad design covers all remaining large route domains but requires one PR per route domain.
- Consistency check: architecture, landing order, and testing strategy all use the same domain-by-domain service extraction model.
- Ambiguity check: route contracts are explicitly behavior-preserving; shared helpers are allowed only after duplication is proven across migrated domains.
