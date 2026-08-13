# Backend — Backlog

Actionable backlog for the Python server: Flask APIs, the control runtime,
persistence, schemas, updater, and server-side wizard code. Completed work,
rejected work, and obsolete findings are removed rather than retained here;
plans and repository history carry that record.

**Last reconciled against live code: 2026-08-13.**

## 1. Consolidate static settings defaults

`common/defaults.py::default_settings()` and the Pydantic tree in
`common/settings_schema.py` independently declare the same static defaults.
`tests/unit/common/test_settings_schema.py::test_defaults_instantiation_parity`
detects drift, but the duplication remains structurally possible.

The consolidation cannot replace all of `default_settings()`. Eleven dynamic
paths still need builders, including versions, install UUIDs, controller and
display module defaults, dashboard metadata, probe-derived data, and
`recipe.probe_map`. The decision to make before implementation is whether a
schema-generated static core plus hand-written dynamic overlay is clearer than
the current single hand-written builder with a parity contract.

## 2. Model durable history rows

`common/persistence/transforms.py::history_row_to_dict()` still returns an
unvalidated dictionary with the durable/wire keys `T`, `P`, `F`, `PSP`, `NT`,
`AUX`, and optional `EXD`. `common/persistence/history.py::read_history()`
returns those dictionaries directly to chart, cook-file, import/export, and
metrics consumers.

Give durable history rows the same model/runtime-view split used by
`common/current_schema.py`, reusing reading vocabulary where semantics match.
History and cook files survive restarts and upgrades, so this needs an explicit
compatibility and migration policy; the current blob's rebuild-on-mismatch rule
does not apply.

## 3. Retire legacy single-letter current keys

`common/current_schema.py` models `control:current`, but its
`serialization_alias` values still preserve `P`, `F`, `AUX`, `PSP`, `NT`, `TS`,
and `LAST`. `common/persistence/runtime.py::read_current()` still exposes the
legacy dictionary.

Production consumers remain in the display process, flex and Qt displays,
mobile Socket.IO, API command paths, and the controller store adapter. Migrate
each to `read_current_snapshot()`, then remove `read_current()` and the legacy
serialization aliases. Renaming `/api/get/current` and `/api/current` is a
client-visible wire break and must update their characterization and e2e
contracts in the same change. Keep validation aliases for one release only if
an existing stored blob must remain readable.
