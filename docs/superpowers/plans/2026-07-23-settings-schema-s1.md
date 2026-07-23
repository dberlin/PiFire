# Settings Schema S1 (Pydantic Shadow Models + Generated TS Types) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One source of truth for the Settings shape — pydantic models mirroring `common/defaults.py`, exported to committed JSON Schema, generating the TypeScript `Settings` interface that replaces `Record<string, any>` — with ZERO runtime behavior change.

**Architecture:** Shadow-first: `defaults.py` stays the defaults authority; a parity round-trip test pins the models to it. Schema drift is caught by a pytest test (Python side) and a `gen:types:check` script (TS side), so both generated artifacts fail the normal gates when stale.

**Tech Stack:** pydantic ≥2.13 (new dep), `json-schema-to-typescript` 15.x (web-react dev dep, bun), existing gates (uv/pytest, bun/rstest/biome/eslint/TS7).

## Global Constraints

- **ZERO runtime behavior change.** Python: no caller starts validating; the only new runtime surface is the export `__main__`. TS: type-level fixes only — no logic edits; the 227-test rstest suite and per-file 75% coverage thresholds must pass unchanged (a test may only change where its FIXTURE was shape-wrong and the new types exposed it — each such change justified in the task report).
- `defaults.py` values are the verbatim source for every model default — transcribe from `common/defaults.py` directly, never from memory or this plan. The parity test is the enforcement; a mismatch is a task failure, not a judgment call.
- Unknown keys allowed everywhere: every model carries `model_config = ConfigDict(extra="allow")`.
- Dynamic zones stay loose (spec): `history_page.probe_config: dict[str, ProbeChartConfig]`; `controller.config: dict[str, dict[str, float | int | bool | str]]`; `notify_services`: per-service models ONLY if `default_notify_services()` shapes are static — else `dict[str, dict[str, Any]]` (implementer decides from the code, states the verdict); `probe_settings`: stable outer shape, loose device blobs.
- `Literal` ONLY for: `globals.units: Literal["F", "C"]`, `history_page.autorefresh: Literal["on", "off"]`, `startup.start_to_mode.after_startup_mode: Literal["Smoke", "Hold"]`. No min/max/other constraints (S2).
- Python: `uv run pytest` (env `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy` if needed); `uvx ruff format` changed .py files before each commit; **after EVERY commit run `git show --stat HEAD` and verify it contains exactly the intended files** (the prek hook has contaminated scoped commits when the tree was dirty — keep the tree clean, stage explicit paths).
- web-react: **bun only**; gate `bun run typecheck && bun run lint && bun run test && bun run test:coverage && bun run build` (+ `bun run gen:types:check` once it exists); no new eslint-disable/biome-ignore; lint-staging rule (after any `biome check --write`, stage every fixed file, then `bun run lint` check-only).
- Do not touch the running control.py/gunicorn. E2e not run this phase.

---

### Task 1: Scaffold — dep, module skeleton, first 8 sections, parity harness

**Files:**
- Modify: `pyproject.toml` (add `"pydantic>=2.13",` to `dependencies`)
- Create: `common/settings_schema.py`
- Test: `tests/unit/common/test_settings_schema.py` (create; check `tests/unit/common/` exists — if the dir differs, follow where `tests/unit/` keeps common/ tests, e.g. alongside existing test_common*.py, and say so in the report)

**Interfaces:**
- Produces: `SettingsSchema` (root model), section models `SafetySettings, PelletLevel, KeepWarm, SmokePlus, CycleData, ShutdownSettings, Modules, Platform`; test helpers `assert_parity()`. Task 2 adds the remaining sections to the SAME module; Task 3 adds `__main__` export.

- [ ] **Step 1: Add the dependency** — `pyproject.toml` dependencies array: `"pydantic>=2.13",` (alphabetical position). Run `uv sync 2>&1 | tail -2` → resolves and installs (aarch64/x86 wheels exist; no build step expected).

- [ ] **Step 2: Write the failing parity test**

```python
"""Parity: the pydantic shadow models must round-trip default_settings() exactly.

extra="allow" means sections not yet modeled pass through untouched, so this
test is meaningful from the first section onward and total by Task 2.
"""

from common.defaults import default_settings
from common.settings_schema import SettingsSchema


def assert_parity(settings: dict) -> None:
    dumped = SettingsSchema.model_validate(settings).model_dump(mode="json")
    assert dumped == settings


def test_default_settings_round_trips():
    assert_parity(default_settings())
```

Run: `uv run pytest tests/unit/common/test_settings_schema.py -q` → FAIL (`ModuleNotFoundError: common.settings_schema`).

- [ ] **Step 3: Create `common/settings_schema.py`** — skeleton + the 8 simple scalar sections. Structure and conventions (SafetySettings shown COMPLETE as the worked example — every other section follows this pattern with values transcribed from `common/defaults.py`):

```python
"""Pydantic shadow models for the PiFire settings tree (S1: shape only).

common/defaults.py remains the defaults AUTHORITY — these models mirror it,
and tests/unit/common/test_settings_schema.py fails on any divergence.
Do not add validation constraints here in S1 (that is S2's job); do not
change a default here without changing defaults.py (parity will fail).
Unknown keys are allowed everywhere: legacy stores and future upgrades
must always validate.
"""

from pydantic import BaseModel, ConfigDict


class _Section(BaseModel):
    model_config = ConfigDict(extra="allow")


class SafetySettings(_Section):
    # Mirrors defaults.py settings["safety"] — transcribed 2026-07-23.
    minstartuptemp: int = 75
    maxstartuptemp: int = 100
    maxtemp: int = 550
    reigniteretries: int = 1
    startup_check: bool = True
    allow_manual_changes: bool = False
    manual_override_time: int = 30


# ... PelletLevel, KeepWarm, SmokePlus, CycleData, ShutdownSettings,
# Modules, Platform: same pattern, fields/defaults transcribed verbatim
# from defaults.py settings["pelletlevel"|"keep_warm"|"smoke_plus"|
# "cycle_data"|"shutdown"|"modules"|"platform"].


class SettingsSchema(_Section):
    safety: SafetySettings = SafetySettings()
    pelletlevel: PelletLevel = PelletLevel()
    keep_warm: KeepWarm = KeepWarm()
    smoke_plus: SmokePlus = SmokePlus()
    cycle_data: CycleData = CycleData()
    shutdown: ShutdownSettings = ShutdownSettings()
    modules: Modules = Modules()
    platform: Platform = Platform()
    # Remaining sections arrive in Task 2; extra="allow" passes them through.
```

Transcription rules: field name = JSON key verbatim; type = the Python type of the defaults.py value (`0.5` → `float`, `4` → `int`, `True` → `bool`, `"x"` → `str`, homogeneous list → `list[int]`/`list[float]`/etc., dict-of-scalars → a nested `_Section` subclass when keys are static). If a defaults.py value is float-typed but integral (e.g. `100.0`), the field is `float` — `model_dump(mode="json")` must reproduce the original exactly; if parity fails on int/float JSON representation for a specific field, match the field's type to what round-trips and note it in the report.

- [ ] **Step 4: Run parity — PASS.** `uv run pytest tests/unit/common/test_settings_schema.py -q`. If it fails, the FAILURE DIFF names the divergent key — fix the transcription (never "fix" defaults.py).

- [ ] **Step 5: Spot tests** (append to the test file):

```python
def test_extra_keys_survive():
    s = default_settings()
    s["safety"]["future_knob"] = 42
    s["totally_new_section"] = {"a": 1}
    assert_parity(s)


def test_lax_coercion_is_pinned():
    # S1 documents pydantic lax-mode behavior rather than fighting it:
    # numeric strings coerce. This pin makes S2's strictness decision explicit.
    s = default_settings()
    s["safety"]["maxtemp"] = "550"
    dumped = SettingsSchema.model_validate(s).model_dump(mode="json")
    assert dumped["safety"]["maxtemp"] == 550
```

Run → PASS.

- [ ] **Step 6: Format, gate, commit**

```bash
uvx ruff format common/settings_schema.py tests/unit/common/test_settings_schema.py
uv run pytest tests/unit/common/ -q   # green
git add pyproject.toml uv.lock common/settings_schema.py tests/unit/common/test_settings_schema.py
git commit -m "feat(schema): pydantic shadow models — scaffold + 8 scalar sections + parity test"
git show --stat HEAD   # verify EXACTLY these files
```

---

### Task 2: Remaining 13 sections — full-tree parity

**Files:**
- Modify: `common/settings_schema.py`, `tests/unit/common/test_settings_schema.py`

**Interfaces:**
- Consumes: Task 1's module/conventions.
- Produces: sections `Versions, ServerInfo, ProbeSettings, GlobalSettings, ControllerSettings, DisplaySettings, PwmSettings, StartupSettings (nested SmartStart, StartToMode), Dashboard, NotifyServices (or loose dict — see constraint), HistoryPage (+ ProbeChartConfig), LastUpdated, Recipe` all wired into `SettingsSchema` with defaults; full-tree parity.

- [ ] **Step 1: Transcribe the remaining sections** per Task 1's conventions, honoring the Global Constraints' dynamic-zone and Literal rules. Specific notes:
  - `GlobalSettings.units: Literal["F", "C"] = "F"`; transcribe the REST of globals verbatim.
  - `StartupSettings` nests `SmartStart` (`enabled/exit_temp/temp_range_list: list[int]/profiles: list[SmartStartProfile]` where `SmartStartProfile` has `startuptime/augerontime/p_mode: int`) and `StartToMode` (`after_startup_mode: Literal["Smoke", "Hold"]`, `primary_setpoint: int`, `start_to_hold_prompt: bool`).
  - `PwmSettings.profiles: list[PwmProfile]` with `PwmProfile.duty_cycle: int`.
  - `HistoryPage.autorefresh: Literal["on", "off"]`; `probe_config: dict[str, ProbeChartConfig] = {}` where `ProbeChartConfig` (extra="allow") has `name: str`, `type: str`, `enabled: bool`, `line_color/bg_color/line_color_target/bg_color_target: str`, `dash_setpoint: bool`, `fill: bool` and NO required setpoint colors (Primary-only fields ride on extra="allow" — model only the always-present keys; check `default_probe_config` in defaults.py:307-336 for the authoritative always-present set; defaults for this model: none required beyond what round-trips — since probe_config defaults to `{}` in a fresh tree BEFORE `default_probe_config` fills it, give ProbeChartConfig fields no defaults and mark them required EXCEPT the Primary-only ones... simpler and correct: all fields required except setpoint pair; parity passes because default_settings() output rows always carry the always-present set).
  - `ControllerSettings`: `selected: str`, `config: dict[str, dict[str, float | int | bool | str]]`, plus any other keys defaults.py has (`cycle` etc. — transcribe what's there).
  - `Versions/ServerInfo/LastUpdated`: transcribe; `server_info.uuid: str` and `lastupdated.time: int` get NO default (values are generated per-install) — declare them required fields; parity still passes (validate-then-dump preserves the input values).
  - `NotifyServices`: inspect `default_notify_services()`; static per-service shapes → per-service `_Section` models; anything dynamic → `dict[str, Any]` for that service. State the verdict per service in the report.
  - `Dashboard`, `DisplaySettings`, `ProbeSettings`, `Recipe`: transcribe outer shape; device/dashboard blobs loose (`dict`/`list[dict]`) where content is driver-specific.

- [ ] **Step 2: Tighten the parity test's meaning** — add:

```python
def test_all_sections_are_modeled():
    # No top-level section may be passing through extra="allow" anymore.
    modeled = set(SettingsSchema.model_fields.keys())
    assert modeled == set(default_settings().keys())
```

Run full file → PASS (parity + extras + coercion pin + completeness).

- [ ] **Step 3: Migration-fixture parity (spec deliverable 2)** — `grep -rln "upgrade_settings\|settings_migration" tests/` for an existing oldest-version fixture; if one exists, add a test validating the MIGRATED output round-trips; if none exists, add a comment noting the check was consciously skipped and say so in the report.

- [ ] **Step 4: Format, full unit gate, commit**

```bash
uvx ruff format common/settings_schema.py tests/unit/common/test_settings_schema.py
uv run pytest tests/unit/ -q   # green, report count
git add common/settings_schema.py tests/unit/common/test_settings_schema.py
git commit -m "feat(schema): complete shadow models — all 21 sections, full-tree parity"
git show --stat HEAD
```

---

### Task 3: Schema export + committed schema + drift pytest

**Files:**
- Modify: `common/settings_schema.py` (add `__main__` block + `export_schema()`)
- Create: `web-react/schema/settings.schema.json` (generated, committed)
- Test: `tests/unit/common/test_settings_schema.py` (drift test)

**Interfaces:**
- Produces: `export_schema() -> dict` (stable-sorted-key schema dict); `python -m common.settings_schema` prints it as JSON; the committed schema file Task 4 consumes.

- [ ] **Step 1: Export function + main**

```python
import json


def export_schema() -> dict:
    return SettingsSchema.model_json_schema()


if __name__ == "__main__":
    print(json.dumps(export_schema(), indent=2, sort_keys=True))
```

- [ ] **Step 2: Generate the committed file**

```bash
mkdir -p web-react/schema
uv run python -m common.settings_schema > web-react/schema/settings.schema.json
head -5 web-react/schema/settings.schema.json   # sanity: JSON, $defs present
```

- [ ] **Step 3: Drift test** (append):

```python
import json
from pathlib import Path

from common.settings_schema import export_schema

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "web-react" / "schema" / "settings.schema.json"


def test_committed_schema_is_current():
    """Fails when models changed but web-react/schema/settings.schema.json
    wasn't regenerated (uv run python -m common.settings_schema > ...)."""
    committed = json.loads(SCHEMA_PATH.read_text())
    assert committed == export_schema()
```

(Verify the `parents[3]` depth against the test file's real location; adjust and note.) Run → PASS. Then prove it FAILS on drift: temporarily edit one default in a model, rerun → FAIL, revert → PASS. Paste both outputs.

- [ ] **Step 4: Format, gate, commit**

```bash
uvx ruff format common/settings_schema.py tests/unit/common/test_settings_schema.py
uv run pytest tests/unit/common/ -q
git add common/settings_schema.py tests/unit/common/test_settings_schema.py web-react/schema/settings.schema.json
git commit -m "feat(schema): JSON Schema export + committed schema + drift-check test"
git show --stat HEAD
```

---

### Task 4: TS type generation wiring

**Files:**
- Modify: `web-react/package.json` (dev dep + scripts), `web-react/biome.jsonc` (exclude the gen file), `web-react/eslint.config.js` (ignore the gen file)
- Create: `web-react/src/helpers/settings/settingsTypes.gen.ts` (generated, committed), `web-react/scripts/gen-types.ts` ONLY if a wrapper is needed — prefer plain package.json script lines

**Interfaces:**
- Consumes: `web-react/schema/settings.schema.json` (Task 3).
- Produces: exported interface (root name `SettingsSchema` — json-schema-to-typescript uses the schema title) in `settingsTypes.gen.ts`; scripts `gen:types` and `gen:types:check`. Task 5 imports the root type.

- [ ] **Step 1: Install + scripts**

```bash
cd web-react && bun add -d json-schema-to-typescript
```

package.json scripts:

```json
"gen:types": "json2ts -i schema/settings.schema.json -o src/helpers/settings/settingsTypes.gen.ts --bannerComment '/* eslint-disable */\n// GENERATED from schema/settings.schema.json — do not edit. Regenerate: bun run gen:types */'",
"gen:types:check": "json2ts -i schema/settings.schema.json -o /tmp/settingsTypes.gen.check.ts --bannerComment '/* eslint-disable */\n// GENERATED from schema/settings.schema.json — do not edit. Regenerate: bun run gen:types */' && diff -q /tmp/settingsTypes.gen.check.ts src/helpers/settings/settingsTypes.gen.ts",
```

(If the banner's quoting fights bun/package.json, move generation into `web-react/scripts/gen-types.ts` run via `bun scripts/gen-types.ts [--check]` calling the library API `compileFromFile` — same banner, same check semantics. Note: the `/* eslint-disable */` banner is the generated-file convention and is EXEMPT from the no-suppressions rule — it applies to a machine-written file excluded from lint anyway; keep eslint/biome excludes as the primary mechanism.)

- [ ] **Step 2: Generate + exclude from format/lint** — `bun run gen:types`; add `"!src/helpers/settings/settingsTypes.gen.ts"` to biome.jsonc `files.includes`; add the path to eslint.config.js `ignores`. Verify: `bun run lint` clean; `bunx tsc -b` clean (the file IS typechecked).

- [ ] **Step 3: Prove the check** — `bun run gen:types:check` → exit 0. Hand-edit one character in the gen file → check exits nonzero → `bun run gen:types` restores → exit 0. Paste outputs.

- [ ] **Step 4: Gate + commit**

```bash
bun run typecheck && bun run lint && bun run gen:types:check && bun run test && bun run test:coverage && bun run build
git add package.json bun.lock schema/settings.schema.json src/helpers/settings/settingsTypes.gen.ts biome.jsonc eslint.config.js
git commit -m "feat(web-react): generated Settings types from committed schema (gen:types + drift check)"
git show --stat HEAD
```

(schema/ was committed in Task 3 from the repo root — include here only if changed.)

---

### Task 5: The type swap — Settings = generated type, fix all fallout

**Files:**
- Modify: `web-react/src/helpers/settings/settingsApi.ts` (+ everywhere tsc complains: tabs, helpers, tests)

**Interfaces:**
- Consumes: Task 4's gen file (root interface name = whatever json2ts emitted from the schema title — check the file; expected `SettingsSchema`).
- Produces: `export type Settings = SettingsSchema` (aliased from the gen import); the codebase compiles against real settings types.

- [ ] **Step 1: Swap** in `settingsApi.ts`:

```ts
import type { SettingsSchema } from "./settingsTypes.gen";

export type Settings = SettingsSchema;
```

Remove the old `Record<string, any>`. If the biome.jsonc per-file `noExplicitAny` override for settingsApi.ts is now obsolete, remove it (check whether other `any`s remain in the file first).

- [ ] **Step 2: `bunx tsc -b` and fix EVERY error, type-level only.** Expected classes of fallout and the sanctioned fixes: optional-field access (`settings.safety.maxtemp` → the field is optional in generated types → optional-chain or local narrow with `??` mirroring the component's existing fallback value — NEVER invent a new fallback value, reuse the one already in the component); test fixtures missing fields (extend the fixture with real-shaped values from defaults.py); `setPath` string-path writes (untyped by design — if tsc doesn't complain, leave them; do NOT build typed paths in S1). Forbidden: `as any`, `@ts-ignore`/`@ts-expect-error`, weakening tsconfig, editing the gen file.

- [ ] **Step 3: Full gate** — `bun run typecheck && bun run lint && bun run gen:types:check && bun run test && bun run test:coverage && bun run build` all green; 227+ tests; coverage thresholds hold. Any test-file change beyond type annotations/fixture-shape completion must be listed + justified in the report (zero-runtime-change constraint).

- [ ] **Step 4: Commit**

```bash
git add -A src   # from web-react/ — then IMMEDIATELY: git status + git show --stat after commit to verify scope
git commit -m "feat(web-react): Settings is the schema-generated type (goodbye Record<string, any>)"
git show --stat HEAD
```

---

### Task 6: Final verification sweep

**Files:** none expected (verification; fixes only on failure)

- [ ] **Step 1: Python from clean** — `uv run pytest tests/unit/ tests/characterization/ -q` → green; paste counts.
- [ ] **Step 2: web-react from clean** — `cd web-react && rm -rf node_modules && bun install --frozen-lockfile && bun run typecheck && bun run lint && bun run gen:types:check && bun run test && bun run test:coverage && bun run build` → all green.
- [ ] **Step 3: Round-trip the whole pipeline once** — edit NOTHING; run `uv run python -m common.settings_schema > web-react/schema/settings.schema.json && cd web-react && bun run gen:types` and verify `git status` is CLEAN afterward (regeneration is idempotent — if a diff appears, determinism is broken: STOP and report).
- [ ] **Step 4: Report** the evidence; commit only if fixes were needed.

---

## Verification summary (maps to spec deliverables)

1→Tasks 1-2 (models, parity, spot pins, completeness). 2→Tasks 1-3 (parity + migration fixture + drift). 3→Task 3 (export + committed schema + drift pytest). 4→Task 4 (gen wiring + check). 5→Task 5 (swap + type-level fixes under the full gate). 6→Task 1 (pyproject). Idempotence→Task 6.
