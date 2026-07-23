# Settings Schema S1 — Pydantic Shadow Models + Generated TS Types — Design Spec

**Date:** 2026-07-23
**Status:** Approved direction (user: pydantic, no spike, zod out); spec pending user review
**Parent:** `docs/superpowers/specs/2026-07-22-settings-schema-scoping.md` (all library
decisions recorded there)
**Scope:** S1 ONLY — shadow schema + generated TS type, ZERO behavior change.
S2 (server-side enforcement, pydantic-partial delta layer, constraint
migration) is the next phase and explicitly out of scope here.

## Goal

One source of truth for the Settings shape: pydantic v2 models mirroring
`common/defaults.py` → committed JSON Schema → generated TypeScript
`Settings` interface replacing `type Settings = Record<string, any>`
(`web-react/src/helpers/settings/settingsApi.ts:1`). Nothing validates at
runtime yet; `defaults.py` remains the defaults authority.

## Deliverables

1. **`common/settings_schema.py`** — pydantic 2.13.x models, one class per
   top-level section (21 sections, 504 nested keys measured):
   `Versions, ServerInfo, ProbeSettings, GlobalSettings, Platform, CycleData,
   ControllerConfig, DisplaySettings, KeepWarm, SmokePlus, PwmSettings,
   SafetySettings, PelletLevel, Modules, LastUpdated, StartupSettings
   (nested SmartStart, StartToMode), ShutdownSettings, Dashboard,
   NotifyServices, HistoryPage, Recipe` composed into a root
   `SettingsSchema` model. Naming: mirror the JSON keys via field names
   (snake_case keys match already); class names as above.
   - **Defaults inline**, copied from `defaults.py` values — the parity test
     makes divergence a failure.
   - **Dynamic zones stay deliberately loose:**
     `history_page.probe_config: dict[str, ProbeChartConfig]`;
     `controller.config: dict[str, dict[str, float | int | bool | str]]`;
     `notify_services: dict[str, dict]` (or per-service models ONLY if the
     shapes are stable — implementer checks `default_notify_services()`;
     if any service shape is user-mutable/dynamic, stay loose);
     `probe_settings.probe_profiles`/`probe_map`: model the stable outer
     shape, `dict`/`list[dict]` for device-specific inner blobs.
   - **Closed value sets as `Literal`** where stable: `globals.units:
     Literal["F", "C"]`, `history_page.autorefresh: Literal["on", "off"]`,
     `start_to_mode.after_startup_mode: Literal["Smoke", "Hold"]`. No other
     constraints in S1 (min/max clamps arrive in S2).
   - **Unknown keys ALLOWED everywhere** (`model_config = ConfigDict(extra="allow")`)
     — legacy stores and future upgrades must validate.
   - Types follow defaults.py exactly: float-valued defaults → `float`,
     int → `int`, bool → `bool` (S1 mirrors reality; no opinion changes).
2. **Parity characterization test** —
   `SettingsSchema.model_validate(default_settings()).model_dump(mode="json")`
   round-trips EQUAL to `default_settings()` (deep dict equality; `lastupdated.time`
   and `server_info.uuid` compared structurally since they're generated). A
   second case validates the output of the settings-migration path for the
   oldest supported version fixture if one exists in tests/ (implementer
   checks; skip with a note if no such fixture exists).
3. **Schema export** — `python -m common.settings_schema` prints
   `SettingsSchema.model_json_schema()` (2020-12) as stable-sorted JSON;
   committed at **`web-react/schema/settings.schema.json`**.
   **Drift check as a pytest test**: regenerate in-process and compare to the
   committed file's parsed content — schema drift fails the normal Python
   gate, no extra CI wiring.
4. **Generated TS types** — `bunx json-schema-to-typescript` (15.x, dev dep)
   from the committed schema → **`web-react/src/helpers/settings/settingsTypes.gen.ts`**
   (committed; generated-file header; excluded from biome formatting via
   biome.jsonc `files.includes` negation and from eslint via its ignores;
   INCLUDED in tsc). Scripts: `"gen:types"` (regenerate) and
   `"gen:types:check"` (regenerate to temp + diff — joins the web-react gate
   beside lint). Deterministic output required (json-schema-to-typescript is,
   with `sort-keys` stable input — the export's stable sort guarantees it).
5. **Type swap** — `settingsApi.ts`: `export type Settings = GeneratedSettings`
   (import from the gen file), delete the `Record<string, any>` and its
   biome `noExplicitAny` override for that file if it becomes unnecessary.
   **Fix every type error this surfaces** in tabs/helpers/tests — expected
   to be the phase's main labor; fixes must be type-level (annotations,
   narrowing, optional-chaining) with ZERO runtime behavior change (the 227
   rstest tests + suite coverage thresholds are the net; e2e untouched).
   Note: pydantic marks defaulted fields as not-required in the schema, so
   generated fields are mostly optional — matching the codebase's existing
   defensive access style.
6. **pyproject**: add `pydantic>=2.13` to dependencies. (pydantic-partial is
   S2 — NOT added now. YAGNI.)

## Non-goals (S2+, explicitly)

Runtime validation anywhere (read or write path); pydantic-partial;
min/max/enum constraint enforcement; replacing `defaults.py`; client-side
runtime validation; per-controller schema generation from controllers.json.

## Testing / verification

- Python: parity test + drift test + 2-3 spot tests (wrong-typed field →
  ValidationError when validated in strict experiments — note: with
  extra="allow" and lax mode, pydantic coerces "5"→5; S1 asserts the
  DOCUMENTED behavior, whatever the models do — pin it, don't fight it);
  full `uv run pytest tests/unit/ -q` green.
- web-react: full gate (`typecheck && lint && gen:types:check && test &&
  test:coverage && build`) green, 227+ tests, per-file coverage thresholds
  hold, zero runtime diffs (no component test assertions change EXCEPT
  where a test's fixture was shape-wrong and the new types exposed it —
  each such change justified in the task report).
- E2e: NOT rerun (no runtime change); final review may spot-check.

## Risks

- The 504-key transcription is the error surface — the parity test is the
  net (any wrong default/type/missing key fails it).
- Generated-type friction in strict TS (e.g. index-signature access on
  extra="allow" additionalProperties) — resolve per-site with narrowing;
  do NOT weaken tsconfig or add suppressions.
- Schema size: ~504 keys → a large .gen.ts; acceptable (types are free at
  runtime).

## Sequencing

After the in-flight metrics-safety fix wave lands (it touches common/,
avoid overlap). Estimated 6-8 SDD tasks: models (split across 2-3 tasks by
section groups) → parity+export+drift → TS gen wiring → type swap + error
fixes (1-2 tasks) → final review.
