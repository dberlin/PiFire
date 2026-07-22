# Shared Settings Schema (Python ⇄ React) — Scoping Document

**Date:** 2026-07-22
**Status:** SCOPING — comparison + phasing for discussion, not an approved design
**Prompted by:** Settings in React is `type Settings = Record<string, any>`
(`web-react/src/helpers/settings/settingsApi.ts:1` — the exact line carrying a
biome `noExplicitAny` override), and the backend has no validation layer at
all. One source of truth should type both sides.

## Current state (measured)

- The settings tree is defined imperatively in `common/defaults.py`
  `default_settings()`: **21 top-level sections, 504 nested keys**. Persisted
  in the SQLite datastore; read/written as a plain dict everywhere
  (`read_settings`/`write_settings`, `deep_update` merges).
- No schema, no validation: `POST /api/settings_update` deep-merges whatever
  arrives. Client-side coercions (2b-1/2b-2) exist precisely because the
  server can't enforce shape.
- Dynamic sub-shapes: `history_page.probe_config` keyed by probe label;
  `controller.config` keyed by controller name with per-controller fields
  (driven by `controller/controllers.json` metadata); `notify_services`;
  `probe_settings.probe_map/probe_profiles`.
- Existing invariants live only in prose/tests: `smartstart.profiles.length ==
  temp_range_list.length + 1`, colors as `rgb(r, g, b, 1)` strings,
  `autorefresh` as `"on"/"off"`, units `"F" | "C"`.

## Goal

One schema source of truth → (a) generated TypeScript `Settings` type
replacing `Record<string, any>`; (b) Python-side validation at the
settings-write boundary; (c) eventually, server-side enforcement that retires
the "React must replicate Flask's coercions" bug class.

## The pipeline shape (identical for every library option)

```
Python schema definition
   └─► JSON Schema file (committed: web-react/schema/settings.schema.json)
         ├─► CI drift check: regenerate + git diff --exit-code
         └─► bunx json-schema-to-typescript → src/helpers/settings/settingsTypes.gen.ts
               └─► export type Settings = GeneratedSettings (replaces Record<string, any>)
```

The library choice changes only the left box and what runtime validation
looks like.

## Library options (versions checked on PyPI 2026-07-22)

### A. pydantic v2 (2.13.4)

- **Schema export:** first-class, built-in (`model_json_schema()`, JSON Schema
  2020-12). Best-quality output for the TS generator; no plugin dependency.
- **Authoring:** typed class definitions with inline defaults — the most
  concise way to write 504 keys, and Python callers gain typed attribute
  access if we ever want it.
- **Runtime fit:** validation returns model *instances*; PiFire passes dicts
  everywhere, so boundary usage is `SettingsModel.model_validate(d).model_dump()`
  — workable, slightly clunky, keeps the dict world intact.
- **Deps:** `pydantic-core` is a compiled Rust wheel — available for the Pi
  (aarch64/armv7 wheels exist) but the heaviest of the three options.
- Not currently a dependency (checked `pyproject.toml`).

### B. marshmallow 4 (4.3.0) — user-requested evaluation

- **Runtime fit: the best match to this codebase.** `Schema().load(d)`
  validates AND returns a plain dict with defaults filled — dict-in/dict-out,
  zero model objects invading 500+ call sites. Pure Python, no compiled deps
  (safest install story on the Pi).
- **Schema export: the weak leg.** JSON Schema generation is NOT built in — it
  needs the third-party `marshmallow-jsonschema` (0.16.0). That project was
  dormant for years and saw a fresh release 2026-04; **its compatibility with
  marshmallow 4's breaking API changes and its target draft (historically
  draft-07, older) MUST be verified in a spike before committing** — this is
  the single gating risk for option B. (Fallback: `apispec` 6.10 emits
  OpenAPI-flavored schema from marshmallow, convertible but lossier.)
- **Authoring:** `fields.Integer(load_default=240)` style — more verbose than
  class-typed models, and no static-typing benefit on the Python side.

### C. msgspec (0.21.1) — lean dark horse

- **Schema export:** built-in (`msgspec.json.schema()`, 2020-12), like pydantic.
- **Authoring:** typed `Struct`s, as concise as pydantic; `convert()`/`to_builtins()`
  for dict boundaries. Tiny, very fast C extension (wheels available).
- **Trade-off:** smaller ecosystem/mindshare than the other two; fewer
  affordances (custom validators exist but are more manual); 0.x versioning.

### D. Handwritten JSON Schema (baseline)

Both sides consume a hand-authored `settings.schema.json` (Python validates
via `jsonschema`). No new modeling layer — but hand-writing and maintaining a
504-key schema with defaults is exactly the toil the libraries eliminate, and
defaults would live in TWO places (schema + defaults.py). Not recommended;
listed for completeness.

### Comparison at a glance

| | pydantic 2.13 | marshmallow 4.3 | msgspec 0.21 | handwritten |
|---|---|---|---|---|
| JSON Schema export | built-in, 2020-12 | 3rd-party plugin (verify!) | built-in, 2020-12 | is the source |
| Validated output | model (needs dump) | **plain dict** | struct (needs convert) | dict |
| Authoring 504 keys | typed classes, concise | verbose field decls | typed structs, concise | very painful |
| Pi install | Rust wheel (heaviest) | **pure Python** | small C wheel | pure Python |
| Python typing win | yes | no | yes | no |
| Maturity/longevity | highest | high (plugin: uncertain) | good, 0.x | n/a |

**Recommendation:** pydantic v2, primarily for the first-class schema export
(the whole point of the exercise) and longevity — accepting the dump-at-boundary
clunk. **marshmallow is the right choice instead IF the spike confirms
`marshmallow-jsonschema` 0.16 works cleanly with marshmallow 4 and emits
schema the TS generator handles well** — its dict-native model is genuinely
the better fit for how PiFire's code passes settings around. Decision gate:
run a half-day spike building ONE section (e.g. `safety` + `startup` incl.
smartstart) in both candidates, generate TS from each, compare. msgspec is
the fallback if both disappoint.

## Phasing (library-agnostic)

**S1 — Shadow schema + generated TS type (no behavior change)**
1. Model all 21 sections. Dynamic zones stay loose on purpose:
   `probe_config: dict[str, ProbeChartConfig]`, `controller.config:
   dict[str, dict[str, float | int | bool]]` (per-controller shapes are
   metadata-driven; a later refinement can GENERATE per-controller schema from
   `controllers.json`), `notify_services` similar. Unknown-key policy: ALLOW
   extra keys (legacy/forward compat — settings upgrades add keys).
2. Parity characterization test: `validate(default_settings())` round-trips
   byte-equal (defaults.py remains the defaults authority in S1 — the schema
   only mirrors it; a mismatch fails CI).
3. Export script (`python -m common.settings_schema`) → committed
   `web-react/schema/settings.schema.json` + CI drift check.
4. `bunx json-schema-to-typescript` → committed
   `settingsTypes.gen.ts` (biome/eslint-ignored as generated); replace
   `Record<string, any>`; fix the type errors that surface in tabs (expected
   to be the main labor: today's `any` hides real mismatches).
   Estimated: 6–8 SDD tasks.

**S2 — Server-side enforcement**
5. `POST /api/settings_update` validates the MERGED result before
   `write_settings`; invalid → `{result: "error", message: <path + why>}`,
   nothing written. This is the payoff: the generic endpoint stops trusting
   clients, and future Flask-parity coercions become schema constraints
   (min/max/enum) enforced in ONE place.
6. Move numeric clamps (prime_on_startup 0–200, pwm duty bounds…) into the
   schema as constraints; delete the duplicated React-side clamps once the
   server rejects/coerces authoritatively (UX still pre-validates for nice
   errors, from the SAME generated schema via ajv if wanted).
   Estimated: 3–4 tasks.

**S3 (optional, later)** — defaults consolidation (defaults.py generated FROM
the schema or vice versa, ending the dual authority) and typed deep-path
helpers in React (template-literal-typed `setPath`). Nice-to-haves; not
needed for the payoff.

## Risks / open questions

1. **Library pick** (above — spike decides pydantic vs marshmallow; user calls it).
2. `marshmallow-jsonschema`×marshmallow-4 compat (gating for option B only).
3. Schema drift discipline: the committed schema + drift check must run in
   whatever CI exists (currently: the phase gates; no GitHub Actions on this
   repo — the gate script carries it).
4. `json-schema-to-typescript` output quality over patternProperties /
   additionalProperties zones — TS `Record<string, X>` is expected and fine.
5. Settings versioning/migrations: validation must tolerate older stores
   (extra keys allowed; missing keys defaulted) without masking real
   corruption. The upgrade path keeps running BEFORE validation.
6. Performance on the Pi: validate on WRITE only (writes are rare);
   never per-read in the 1s socket loop.
7. Sequencing: land AFTER 2b-2 completes (its tabs churn settings shapes),
   BEFORE the notifications/probe-config phases (which touch the most dynamic
   sections and would benefit most from generated types).

## Suggested next step

Approve the spike (half-day, throwaway worktree): `safety` + `startup`
modeled in pydantic AND marshmallow, schema → TS for both, side-by-side
comparison in a short report. Then pick, then full spec + plan for S1.
