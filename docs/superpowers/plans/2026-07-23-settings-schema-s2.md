# Settings Schema S2 (Hard-Strict Validation at write_settings) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every settings write in the process validates strictly against the S1 schema and raises on failure — with every existing writer proven (and fixed where needed) BEFORE the gate flips.

**Architecture:** Matrix-before-enforcement: Tasks 1–2 build the strict validator + constraints; Tasks 3–4 characterize ALL 35 writer call sites against it pre-enforcement and fix what fails; Task 5 flips `write_settings` to hard-raise and wires boundary error handling; Task 6 proves e2e + sweeps. Ordering means the gate lands on a codebase already known-clean.

**Tech Stack:** pydantic 2.13 strict mode, `pydantic-partial>=0.11` (new dep), existing S1 pipeline (schema export / gen:types drift checks), uv/pytest, bun/rstest/Playwright.

## Global Constraints

- Enforcement point is `write_settings` in `common/datastore_accessors.py` — validate BEFORE persisting, raise `SettingsValidationError`, NO bypass parameter, atomic (failed write leaves store untouched). Applies to the `flush=True` path too.
- Strict semantics: pin what pydantic strict ACTUALLY does with tests (expected: `"550"`→int rejects; int→float widening accepted; bool-for-int rejects; unknown KEYS still allowed — `extra="allow"` unchanged). If observed behavior differs from expectation, PIN THE OBSERVED behavior with a comment and note it in the report — these are documentation pins, not fights.
- Constraints migrated ONLY where a clamp exists today in `blueprints/settings/routes.py` or the React tabs (enumerated in Task 2) — do not invent invariants.
- The persisted form becomes `validate_settings_tree()`'s normalized dump.
- defaults.py remains authority; S1's nets (parity, extras allowlist, defaults-instantiation, schema drift pytest, `gen:types:check`) must pass at every task's end — artifact regeneration (schema.json + settingsTypes.gen.ts) happens ONCE in Task 2 and must be committed there.
- Every handler bug the matrix finds = production fix + pin in the same commit (house pattern), listed per-bug in the task report.
- Python: `uv run pytest` (QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy env); `uvx ruff format` changed .py before commit; POST-COMMIT `git show --stat HEAD` verification (prek history); keep the tree clean; explicit-path staging.
- web-react (Task 6 only): **bun**; gate incl. `gen:types:check`; e2e needs gunicorn restart (no --reload) per the standing recipe; do NOT touch control.py.
- Do not restart/kill the running control.py or gunicorn except where Task 6's e2e recipe says so.

---

### Task 1: Strict validator + partial model + strict-semantics pins (no enforcement yet)

**Files:**
- Modify: `pyproject.toml` (add `"pydantic-partial>=0.11",`), `common/settings_schema.py`, `tests/unit/common/test_settings_schema.py`

**Interfaces:**
- Produces (later tasks rely on EXACT names): `class SettingsValidationError(ValueError)` with `.errors: list[str]` (each `"<dotted.path>: <why>"`) and a joined `str(e)`; `validate_settings_tree(settings: dict) -> dict` (strict-validate, return normalized `model_dump(mode="json")`, raise `SettingsValidationError`); `PartialSettingsSchema` (recursive all-optional twin).

- [ ] **Step 1: dep** — add `"pydantic-partial>=0.11",` to pyproject dependencies; `uv sync`.

- [ ] **Step 2: failing tests** (append to test file; DELETE `test_lax_coercion_is_pinned` in the same change — its replacement follows):

```python
import pytest

from common.settings_schema import (
    PartialSettingsSchema,
    SettingsValidationError,
    validate_settings_tree,
)


def test_strict_string_for_int_rejects():
    s = default_settings()
    s["safety"]["maxtemp"] = "550"
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("safety.maxtemp" in msg for msg in ei.value.errors)


def test_strict_int_widens_to_float():
    s = default_settings()
    s["cycle_data"]["u_min"] = 0  # int into a float field — pydantic strict allows widening
    validate_settings_tree(s)  # must not raise


def test_strict_bool_for_int_rejects():
    s = default_settings()
    s["safety"]["reigniteretries"] = True
    with pytest.raises(SettingsValidationError):
        validate_settings_tree(s)


def test_unknown_keys_still_allowed_under_strict():
    s = default_settings()
    s["safety"]["future_knob"] = 42
    validate_settings_tree(s)


def test_validate_returns_normalized_dump():
    s = default_settings()
    out = validate_settings_tree(s)
    assert out == s  # parity holds through the strict path on a clean tree


def test_partial_model_accepts_sparse_delta_and_rejects_bad_field():
    PartialSettingsSchema.model_validate({"safety": {"maxtemp": 500}}, strict=True)
    with pytest.raises(Exception):  # pydantic ValidationError from the partial
        PartialSettingsSchema.model_validate({"safety": {"maxtemp": "500"}}, strict=True)
```

Run → FAIL (imports missing).

- [ ] **Step 3: implement** in `common/settings_schema.py`:

```python
from pydantic import ValidationError
from pydantic_partial import create_partial_model


class SettingsValidationError(ValueError):
    """A settings tree (or delta) failed strict schema validation."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _format_errors(exc: ValidationError) -> list[str]:
    return [
        f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
        for err in exc.errors()
    ]


def validate_settings_tree(settings: dict) -> dict:
    """Strict-validate a full settings tree; return the normalized dump.

    This is S2's single enforcement entry — write_settings() calls it before
    persisting (Task 5). Raises SettingsValidationError with dotted-path
    messages on failure.
    """
    try:
        model = SettingsSchema.model_validate(settings, strict=True)
    except ValidationError as exc:
        raise SettingsValidationError(_format_errors(exc)) from exc
    return model.model_dump(mode="json")


PartialSettingsSchema = create_partial_model(SettingsSchema, recursive=True)
```

Note: if `create_partial_model(recursive=True)` misbehaves with `extra="allow"`/aliases (unproven combination), reduce Step 2's partial test to what works and report exactly what — the partial layer is an ERROR-QUALITY nicety for the API endpoint, not the enforcement mechanism; a shallow partial is an acceptable fallback, a broken build is not.

- [ ] **Step 4: run** — new tests PASS; S1 nets still green (`uv run pytest tests/unit/common/test_settings_schema.py -q`, expect 14-ish). If a strict pin FAILED because pydantic behaves differently than expected: pin observed behavior + comment + report (per Global Constraints).

- [ ] **Step 5: format, commit** — explicit paths (pyproject.toml uv.lock common/settings_schema.py tests/.../test_settings_schema.py), message `feat(schema): strict validator + partial model + strict-semantics pins (S2, unenforced)`, `git show --stat HEAD` check.

---

### Task 2: Constraint migration + 1WIRE promotion + Wled keys + artifact regen

**Files:**
- Modify: `common/settings_schema.py`, `tests/unit/common/test_settings_schema.py`, `web-react/schema/settings.schema.json` (regen), `web-react/src/helpers/settings/settingsTypes.gen.ts` (regen)

**Interfaces:**
- Consumes: Task 1's `validate_settings_tree`/`SettingsValidationError`.
- Produces: constrained schema; extras allowlist reduced to the 2 Primary-only setpoint colors.

- [ ] **Step 1: Enumerate today's clamps** (the ONLY constraints allowed; verify each source line before writing code): `blueprints/settings/routes.py` — prime_on_startup `<0 or >200 → 0` (~line 481), pwm_duty_cycle chained min/max against `pwm.min_duty_cycle`/`max_duty_cycle` (~line 494); React — StartupTab prime clamp + pwm clamp (mirrors), RangeProfileTable column min/max: smartstart startuptime 30–1200 / augerontime 1–60 / p_mode 0–9, pwm duty within min/max_duty_cycle; the `profiles == boundaries + 1` invariant (both smartstart and pwm — enforced by the React widget's construction). Grep the settings routes for any OTHER numeric clamp (`min(`, `max(`, `< 0`, `> ` patterns) and list findings in the report; add ONLY what's found.

- [ ] **Step 2: failing constraint pins** (representative set — write one per constraint from Step 1's final list):

```python
def test_prime_on_startup_range_rejects():
    s = default_settings()
    s["startup"]["prime_on_startup"] = 999
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("prime_on_startup" in m for m in ei.value.errors)


def test_smartstart_profile_count_invariant():
    s = default_settings()
    s["startup"]["smartstart"]["profiles"] = s["startup"]["smartstart"]["profiles"][:-1]
    with pytest.raises(SettingsValidationError):
        validate_settings_tree(s)


def test_pwm_duty_cycle_must_be_within_min_max():
    s = default_settings()
    s["startup"]["pwm_duty_cycle"] = 10  # below min_duty_cycle=20
    with pytest.raises(SettingsValidationError):
        validate_settings_tree(s)


def test_one_wire_key_is_now_a_real_field():
    s = default_settings()
    validate_settings_tree(s)  # 1WIRE arrives via alias, no longer an extra
```

- [ ] **Step 3: implement** — `Field(ge=..., le=...)` for scalar clamps; `model_validator(mode="after")` on the owning section models for cross-field rules (pwm duty within min/max; profiles==boundaries+1 on SmartStart AND PwmSettings); `platform.system`'s `1WIRE` → `one_wire: <type> = Field(alias="1WIRE", ...)` with `populate_by_name=True` on that model (`model_dump(mode="json", by_alias=True)`?? — CAREFUL: the normalized dump must emit the key AS `1WIRE`; set `serialization_alias="1WIRE"` or use `by_alias=True` in `validate_settings_tree`'s dump IF AND ONLY IF parity still holds for all other fields — the parity test decides; report the mechanism chosen); Wled preset dicts → models with the stable key sets (read `default_notify_services()` for the exact keys). Update the extras-allowlist test (1WIRE leaves the list). Keep the defaults-instantiation masks in sync if any modeled shape changed.

- [ ] **Step 4: regenerate artifacts ONCE** — `uv run python -m common.settings_schema > web-react/schema/settings.schema.json && cd web-react && bun run gen:types`; verify `bun run typecheck` still clean (constraints appear as min/max annotations, TS shape mostly unchanged; the 1WIRE field appears — check nothing consumed `platform.system` extras in TS).

- [ ] **Step 5: run everything** — schema tests green (S1 nets + Task 1 pins + new constraint pins); `uv run pytest tests/unit/ -q`; `cd web-react && bun run typecheck && bun run gen:types:check && bun run test`. Commit (all four files + tests), message `feat(schema): migrate legacy clamps to schema constraints; promote 1WIRE; model Wled preset keys`, --stat check.

---

### Task 3: Writer matrix I — settings blueprint handlers (pre-enforcement)

**Files:**
- Create: `tests/characterization/test_settings_writers_strict.py`
- Modify: `blueprints/settings/routes.py` ONLY where a handler is proven to write a strictly-invalid tree (fix + pin same commit)

**Interfaces:**
- Consumes: `validate_settings_tree` (assertion oracle — enforcement NOT flipped yet).
- Produces: proof that every `_settings_*` POST handler writes strict-clean trees.

- [ ] **Step 1: harness** — follow the existing characterization/web test style (`tests/web/` live_server or `tests/characterization/` patterns — read 2 examples first; use whichever harness lets you POST form data to `/settings/<action>` routes and then read back the written settings). Worked example shape:

```python
"""Every settings-blueprint writer must produce a strictly-valid tree.

Pre-enforcement matrix (S2 Task 3): write_settings does not yet validate;
these tests call validate_settings_tree() on the store's tree after each
handler runs, so handler bugs surface BEFORE the Task-5 gate flips.
"""

from common.settings_schema import validate_settings_tree


def _assert_store_strict(read_settings):
    validate_settings_tree(read_settings())  # raises on any handler bug


def test_settings_safety_post_writes_strict(client_and_store):
    client, read_settings = client_and_store
    client.post("/settings/safety", data={
        "minstartuptemp": "75", "maxstartuptemp": "100", "maxtemp": "550",
        "reigniteretries": "1", "startup_check": "on",
        "manual_override_time": "30",
    })
    _assert_store_strict(read_settings)
```

- [ ] **Step 2: cover every handler** — enumerate the `_SETTINGS_DISPATCH` keys in `blueprints/settings/routes.py` (POST entries) and write one test per handler with form payloads mirroring what the Jinja templates ACTUALLY submit (read `blueprints/settings/templates/settings/index.html` field names; checkboxes submit `"on"`; numbers submit as strings). This is where strict bites: handlers that store un-cast strings will fail the oracle.

- [ ] **Step 3: fix what fails** — each failure is a handler bug: add the missing `int()`/`float()`/checkbox handling AT THE HANDLER (matching its siblings' style). Pin stays in the matrix (it now passes). List every bug fixed (handler, field, symptom) in the report. If a failure is a SCHEMA transcription error instead (model wrong, not handler) — fix the model + note; the parity net must still pass.

- [ ] **Step 4: run** — matrix green; full `uv run pytest tests/unit/ tests/characterization/ -q` green. Commit: `test(settings): strict-writer matrix for settings blueprint + handler cast fixes`, --stat check.

---

### Task 4: Writer matrix II — everything else (pre-enforcement)

**Files:**
- Create: `tests/characterization/test_all_writers_strict.py`
- Modify: any writer proven invalid (fix + pin, same commit)

**Interfaces:**
- Consumes: Task 3's harness patterns + `validate_settings_tree` oracle.
- Produces: proof for the remaining 12 files' writers + the migration matrix.

- [ ] **Step 1: enumerate + cover** — the measured writer inventory (verify with a fresh grep; cover each REACHABLE write path):
  - `blueprints/admin/routes.py` (6 sites) — admin POSTs per its dispatch.
  - `blueprints/mobile/socket_io.py` (6) — drive via its test-callable functions (existing socketio characterization tests show how).
  - `common/api_commands.py` (2) — **units F↔C conversion both directions**: convert, assert strict, convert back, assert strict + values round-trip sanely.
  - `blueprints/history/routes.py` (2), `blueprints/dash/routes.py` (1), `blueprints/wizard/routes.py` (1 — mock the installer per the existing wizard-test pattern; do NOT let os.system run), `blueprints/api/routes.py` (1 — the settings_update endpoint with a VALID delta), `common/app.py` (1 — via save_settings_and_flag_update callers), `notify/notifications.py` (1 — construct per its unit-test fixtures), `display/_base_flex.py` (1 — the display writes settings; drive per the existing display-test harness with hardware neutralized), `updater.py`/`wizard.py` (2+2 — module-level scripts; test the FUNCTIONS that write, not the scripts, per whatever tests exist; if a site is genuinely unreachable in tests, document why in the report — an explicit skip-with-reason beats a fake test).
  - **Migration matrix**: for every starting-version fixture the settings-migration tests already use, run `upgrade_settings` → assert `validate_settings_tree(result)` passes.
- [ ] **Step 2: fix failures** (same fix+pin rule; safety rails: grep os.system/subprocess in everything you execute — wizard/admin/updater are the risky ones, neutralize per existing test fixtures).
- [ ] **Step 3: run** — both matrix files + full `tests/unit/ tests/characterization/` green. Commit: `test(settings): strict-writer matrix for all remaining writers + fixes`, --stat check.

---

### Task 5: Flip the gate + boundary error handling

**Files:**
- Modify: `common/datastore_accessors.py` (write_settings), `blueprints/api/routes.py` (settings_update delta layer + error envelope), `blueprints/settings/routes.py` + `blueprints/admin/routes.py` + `blueprints/mobile/socket_io.py` (catch → their existing error patterns), `tests/unit/common/` + `tests/web/` (pins)

**Interfaces:**
- Consumes: everything (matrix green means this flip should break nothing).
- Produces: hard-strict `write_settings`; `/api/settings_update` two-layer rejection.

- [ ] **Step 1: failing pins first**:

```python
def test_write_settings_rejects_invalid_tree_atomically(store_fixture):
    before = read_settings()
    bad = copy.deepcopy(before)
    bad["safety"]["maxtemp"] = "nope"
    with pytest.raises(SettingsValidationError):
        write_settings(bad)
    assert read_settings() == before  # untouched
```

Plus an api-level test: POST /api/settings_update with `{"settings": {"safety": {"maxtemp": "nope"}}, "flags": []}` → `{result: "error"}` envelope whose message contains `safety.maxtemp`, and read-back unchanged; and a delta-layer test with structurally-bad input (e.g. `{"settings": {"safety": 5}}`).

- [ ] **Step 2: flip** — in `write_settings`: `settings = validate_settings_tree(settings)` as the first statement (both normal and flush paths persist the normalized dump). NO bypass parameter.

- [ ] **Step 3: endpoint two-layer** — in `_api_post_settings_update`: (1) `PartialSettingsSchema.model_validate(request_json["settings"], strict=True)` in a try → on ValidationError return the error envelope early (reuse `_format_errors` — import from settings_schema; if that helper is private, expose `format_validation_errors`); (2) deep_update + let `write_settings` raise → catch `SettingsValidationError` → envelope. Match the endpoint's existing error-status convention exactly (read the sibling error returns).

- [ ] **Step 4: boundary catches** — settings/admin blueprints: wrap their write/save calls (or add a blueprint errorhandler for SettingsValidationError — pick whichever matches each blueprint's existing error style; state the choice per blueprint in the report) → user-visible error, no 500. socket_io: return its error-shaped payload. Internal callers: NOTHING added (a raise is a bug; the matrix already proved none exists).

- [ ] **Step 5: run EVERYTHING** — full `uv run pytest tests/ -q` (unit + characterization + web; expect ~2470+; [chromium] skips OK) green. Commit: `feat(settings): hard-strict validation at write_settings + boundary error handling`, --stat check.

---

### Task 6: E2e + final sweep

**Files:**
- Modify: `web-react/tests/e2e/settings.spec.ts` (one new test)

- [ ] **Step 1: e2e reject test** — via Playwright `request`: POST /api/settings_update with the invalid `safety.maxtemp: "nope"` delta → assert error envelope + a follow-up GET /api/settings shows the value unchanged. (Restore nothing — nothing was written.)
- [ ] **Step 2: restart gunicorn** (standing recipe — MUST pick up the new validation code), `bun run test:e2e` → all 10 pass (9 existing + 1 new; the 9 passing proves the React app's real writes are strict-clean end-to-end).
- [ ] **Step 3: full gates from clean** — Python full suite; web-react `typecheck && lint && gen:types:check && test && test:coverage && build`; regen idempotence round-trip (`git status` clean after).
- [ ] **Step 4: commit** the e2e test; report the evidence.

---

## Verification summary (maps to spec)

Deliverable 1→Tasks 1-2; 2 (enforcement)→Task 5; 3 (boundaries)→Task 5; 4 (matrix)→Tasks 3-4 (pre-flip ordering is the plan's core safety property); 5 (React stays + e2e)→Task 6. Strict-semantics + atomicity pins→Tasks 1/5. Artifacts regenerated once→Task 2.
