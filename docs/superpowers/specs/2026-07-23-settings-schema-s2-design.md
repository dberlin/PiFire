# Settings Schema S2 — Hard-Strict Validation at write_settings — Design Spec

**Date:** 2026-07-23
**Status:** Approved direction (user: option C "hard-strict everywhere, just deal with it";
reject-atomically; strict types with the legacy writers tested and fixed); spec pending
user review
**Parents:** `2026-07-22-settings-schema-scoping.md`, `2026-07-23-settings-schema-s1-design.md` (S1 shipped: shadow models, parity+drift nets, generated TS types)

## Decisions (user-confirmed)

1. **Enforcement point: `write_settings` itself.** Every settings write in the
   process validates strictly BEFORE persisting and **raises** on failure —
   no warn-mode, no escape hatch. Callers that produce invalid trees are bugs
   and get fixed in this phase.
2. **Atomic rejection**: validation happens before any DB write; a failed
   write leaves the store untouched.
3. **Strict types**: `"550"` for an int field REJECTS (pydantic strict mode;
   int→float widening stays allowed per pydantic semantics — pinned by test).
   The S1 lax-coercion pin test is deleted and replaced by strict-rejection
   pins. Unknown KEYS remain allowed (`extra="allow"` is about forward
   compat, not type laxity — unchanged).
4. **Legacy writers get a characterization matrix + fixes**: every Flask
   handler that writes settings is driven with realistic form data and must
   produce a strictly-valid tree; failures are handler bugs (missing casts,
   checkbox string leakage) fixed TDD-style.

## Blast radius (measured 2026-07-23)

35 `write_settings(` call sites across 13 files (+ `save_settings_and_flag_update`
wrapper used by 4): `blueprints/settings/routes.py` (9), `blueprints/mobile/socket_io.py`
(6), `blueprints/admin/routes.py` (6), `wizard.py` (2), `updater.py` (2),
`common/api_commands.py` (2 — includes the F↔C units conversion, the biggest
tree-rewriter), `blueprints/history/routes.py` (2), `notify/notifications.py`,
`display/_base_flex.py` (the on-device UI writes settings), `common/app.py`,
`blueprints/wizard/routes.py`, `blueprints/dash/routes.py`,
`blueprints/api/routes.py`. Plus the settings-migration path
(`upgrade_settings` output is written on startup after upgrades) — every
supported migration input must emit a strictly-valid tree.

**Accepted risk (explicit)**: an unfixed writer discovered in production now
raises instead of silently persisting a malformed tree. In the control
process an uncaught raise can kill the loop — the mitigation is the test
matrix below, not a runtime escape hatch. ("Just deal with it.")

## Deliverables

### 1. Schema hardening (`common/settings_schema.py`)

- `validate_settings_tree(settings: dict) -> dict` — the ONE public entry:
  strict-mode validation (`SettingsSchema.model_validate(settings, strict=True)`),
  returns `model_dump(mode="json")` of the validated tree (so writes persist
  the normalized form), raises `SettingsValidationError` (new, carries a
  `path: why` message list) on failure.
- `PartialSettingsSchema = create_partial_model(SettingsSchema, recursive=True)`
  (new dep `pydantic-partial>=0.11`) — used by the API endpoint's delta layer
  for early, precise errors before merging.
- **Constraint migration** (the clamps React currently duplicates become
  schema truth): `startup.prime_on_startup: Field(ge=0, le=200)`;
  `pwm.min_duty_cycle/max_duty_cycle` ordering + `startup.pwm_duty_cycle`
  within them (cross-field → `model_validator`); smartstart
  `len(profiles) == len(temp_range_list) + 1` (`model_validator`); pwm
  `len(profiles) == len(temp_range_list) + 1`; `safety.maxtemp > maxstartuptemp`
  is NOT added (no legacy invariant existed — do not invent constraints;
  ONLY migrate clamps that exist in Flask handlers or React tabs today,
  enumerated in the plan from `blueprints/settings/routes.py` + the React
  tab coercions).
- `platform.system` `1WIRE` → real field via `Field(alias="1WIRE")` +
  `populate_by_name=True`; extras allowlist shrinks to the 2 Primary-only
  setpoint color fields.
- Wled preset dicts (`profile_numbers`/`mode_presets`/`event_presets`) get
  their stable key sets modeled (the S2 deferral lands here).
- Schema/TS artifacts regenerate (constraints appear as JSON Schema
  min/max etc. → generated TS types unchanged in shape; drift + gen checks
  keep passing).

### 2. Enforcement (`common/datastore_accessors.py`)

`write_settings()` calls `validate_settings_tree()` first; persists the
validated dump; raises `SettingsValidationError` on failure. `flush=True`
path included. No caller-selectable bypass.

### 3. Boundary error handling (reject ≠ crash at UI edges)

- `/api/settings_update`: delta validated via `PartialSettingsSchema`
  (early `<path>: <why>`), then deep_update + the strict write; both
  failure modes → `{result: "error", message: ...}`, HTTP status matching
  the endpoint's existing error convention, store untouched.
- Flask Jinja handlers + admin/wizard/history/dash routes: catch
  `SettingsValidationError` at the blueprint layer (per-blueprint
  errorhandler or the existing flash/error pattern — plan picks per
  blueprint's convention) → user-visible error, no 500.
- `blueprints/mobile/socket_io.py`: error emitted back on the socket per
  its existing response shape.
- Internal callers (control loop, updater, notifications, display) do NOT
  catch — a raise there is a bug the matrix must have prevented; the
  existing tick()-level protections (cookfile wrap) stay as-is, nothing new
  swallows the error.

### 4. The writer characterization matrix (the "deal with it" work)

Tests driving EVERY writer path with realistic inputs, asserting the write
succeeds under hard-strict (and asserting rejection for known-bad inputs at
the two UI boundaries):
- All `_settings_*` POST handlers (settings blueprint) with form payloads
  mirroring the Jinja templates' field names/values (checkbox "on",
  stringly numbers — whatever the templates actually submit).
- Admin, wizard (finish path mocked per its existing test harness),
  history, dash, socket_io writers.
- `common/api_commands.py` units conversion F↔C round-trip validates.
- Migration matrix: every supported `upgrade_settings` starting fixture →
  strictly valid output.
- Every handler bug found = production fix + pin in the same commit
  (house latent-bug pattern).

### 5. React side

- Client clamps STAY (UX). `useSaveSettings`'s error path already surfaces
  `{result:"error"}` messages — add the `<path>: <why>` display polish only
  if it's already plumbed; NO new UI work in S2 (that's cosmetics for later).
- One e2e addition: a deliberately-invalid API write (via request context)
  asserting the reject envelope + store-untouched read-back; existing 9
  e2e must stay green (proves the React app writes strictly-valid trees).

## Non-goals

Client-side runtime validation; deleting React clamps; per-controller
config schema generation; additionalProperties stripping in TS generation
(still backlog); replacing defaults.py; any read-path validation.

## Testing / verification

- Strict-semantics pins: string-for-int rejects, int-for-float accepted,
  bool-for-int rejected (pydantic strict), unknown keys still pass,
  constraint violations reject with path-bearing messages, atomicity
  (failed write → store byte-identical).
- The writer matrix (above) green; full `tests/unit/ tests/characterization/`
  green; web gate + gen/drift checks green (artifacts regenerated once,
  committed); e2e 9+1.
- S1's drift nets keep passing (extras allowlist updated for 1WIRE's
  promotion).

## Sequencing note

After S2 merges, the queued projects run in order: notifications page,
then probe-config page (their own brainstorm→spec→plan cycles).
