# Settings Toolchain Follow-ups — Design

**Status:** design, awaiting review.
**Scope:** the seven items left open under *Schema and toolchain follow-ups* in
`backlogs/react-migration-backlog.md` once persisted schema versioning shipped
(2026-08-02).

## Where these items came from

Each traces to a deferral recorded in an earlier spec and re-checked in
`audits/2026-07-26-deferred-inventory-specs-audits.md`. They are listed here
with their audit IDs so a reader can follow either thread back.

| # | Item | Audit ID | Source spec |
|---|---|---|---|
| 1a | Defaults consolidation | SS-1 | `2026-07-22-settings-schema-scoping.md` **S3** |
| 1b | Typed deep-path `setPath` | SS-1 | same |
| 2 | Per-controller schema generation | SS-2 | same, lines 162-164 |
| 3 | `additionalProperties` stripping | S2-1 | `2026-07-23-settings-schema-s2-design.md` |
| 4 | `<path>: <why>` save-error display | S2-2 | same |
| 5 | Dotted error path → offending widget | S2-2 | same |
| 6 | Read-path validation | S2-3 | same, `## Non-goals` |
| 7 | The four 2b-1 follow-ups | S2B2-1 | `2026-07-22-toolchain-rsbuild-ts7-biome-design.md` lines 170-175 |

## Two findings that reshape the work

**Item 3 is already done, and its audit entry is wrong about the cause.**
S2-1 reads: the generated types "contain 12 `[k: string]` index signatures, so
the generated `Settings` type still admits arbitrary keys and hides typos at
compile time." Checked against the committed schema and the generated file:

- Every modelled section emits `"additionalProperties": false` — `_Section` is
  `extra="forbid"`, which S2's strict-mode work established. `ControllerSettings`,
  `PwmSettings`, `CycleData` and `PushbulletService` all generate as **closed**
  interfaces. A typo'd field already fails `bun run typecheck`.
- Exactly six nodes carry `"additionalProperties": true`, and none of them has
  `properties`: `dashboard.dashboards`' inner dicts, `display.config`'s inner
  dict, `OneSignalService.devices`, the `probe_devices`/`probe_info` array
  items, and `probe_profiles`' inner dict.
- The rest are `dict[str, X]` value schemas, which *must* generate an index
  signature. `history_page.probe_config` becoming
  `{[k: string]: ProbeChartConfig}` is correct output, not leakage.

So there is nothing to strip, and stripping the `dict[str, X]` form would be
actively wrong. What is left of item 3 is *modelling* the zones that are loose
on purpose — which is item 2 generalised. **Item 3 is closed as
done-by-S2**; the backlog entry is corrected with this evidence, and no code
changes for it.

**Item 1's "defaults consolidation" is two items with different owners.** The
S3 text predates `test_defaults_instantiation_parity`
(`tests/unit/common/test_settings_schema.py:377`), which already fails the suite
when a `defaults.py` literal drifts from its pydantic field default.
Consolidation would upgrade that from *detected* to *structurally impossible* —
real, but smaller than the item implies, and it cannot go all the way: the nine
`MASKED_PATHS` entries have no static default to generate from. That half is
Python-side and is filed as **`backlogs/backend-backlog.md` item 1**, out of scope here.
The half that is genuinely unguarded is the frontend's, and it is in scope: see
Slice A4.

## Decisions taken

| Question | Decision |
|---|---|
| Packaging | One spec, three independently shippable slices |
| Read-path validation (6) | Observe-only: validate, log, return the tree unchanged |
| Error transport (4, 5) | Backend adds a structured `errors` array; `message` unchanged |
| Defaults (1a) | Generate a TypeScript defaults constant; leave `defaults.py` alone |

---

## Slice A — the generation chain

`web-react/scripts/gen-types.ts` is today a thin wrapper around
`compileFromFile`, emitting one artifact. It becomes the home for three, each
with a `--check` drift gate like the one it already has.

### A1. Close item 3

Backlog edit only. Record that `extra="forbid"` closed it, that the surviving
index signatures are the six deliberately-loose zones plus legitimate
`dict[str, X]` mappings, and that S2-1's stated cause was wrong.

### A2. Per-controller config types

`common/settings_schema.py:337` models the field as:

```python
config: dict[str, dict[str, float | int | bool | str]] = {}
```

which generates:

```ts
export interface Config {
  [k: string]: { [k: string]: number | boolean | string };
}
```

`controller/controllers.json` declares exactly what belongs there: nine
controllers, each with a `config` array of `{option_name, option_type, ...}`
(`pid` 4 options, `pid_parallel` 4, `mpc` 27, `fuzzy` and `ml` none). A new
emitter writes `controllerTypes.gen.ts`:

```ts
export interface PidConfig { PB: number; Td: number; Ti: number; center: number }
export interface PidParallelConfig { Kp: number; Ki: number; Kd: number; Clamping: boolean }
export type FuzzyConfig = Record<string, never>;   // declares no options
export interface ControllerConfigs {
  pid: PidConfig;
  pid_parallel: PidParallelConfig;
  // ...one per controller
}
```

`option_type` maps `float`/`int` → `number`, `bool` → `boolean`,
`string` → `string`, and `list` → a union of `list_values` where they are
declared (`mpc.estimator`, `mpc.policy`) so a typo'd estimator name fails to
compile. `numlist` is not emitted: no controller declares it (recorded as
S2B2-2, still true).

`ControllerTab.tsx` stops indexing a `Record<string, unknown>`. The generated
map is additive — `SettingsSchema.controller.config` stays a loose dict on the
Python side, because a controller can be added by dropping a file in
`controller/` and the server must not reject a tree naming one it has never
heard of.

### A3. Typed `setPath`

`web-react/src/helpers/settings/delta.ts` is the whole helper:

```ts
export function setPath(obj: object, path: string, value: unknown): object
```

39 call sites pass a dotted string and an unchecked value.
`"startup.smartstart.exit_temp"` and `"startup.smartstart.exit_tmep"`
type-check identically today, and so do a `number` and a `boolean` for either.
It becomes:

```ts
export function setPath<P extends SettingsPath>(
  obj: object,
  path: P,
  value: ValueAt<Settings, P>,
): object
```

with `SettingsPath` and `ValueAt` built from the generated `Settings` type via
template-literal path types.

**Risk, and its fallback.** Recursive template-literal path types over a tree
this size can be slow or hit TypeScript's instantiation depth limit, and the
typechecker here is the `typescript7` alias rather than stock `tsc`. The gate
is `bun run typecheck` wall-clock: if it regresses materially or the types do
not resolve, fall back to a hand-written union of the ~39 paths actually in use
plus their value types. That fallback still catches both error classes; it just
has to be extended when a new path is written.

### A4. Generated defaults constant

The settings tabs carry **94** `??` fallbacks (`?? 60`, `?? 150`, `?? 50`),
hand-typed and checked by nothing. `StartupTab.tsx:56-63` alone has eight. The
committed `settings.schema.json` already carries a `default` for every field
pydantic gives one, so a third emitter writes `settingsDefaults.gen.ts` and
`st.duration ?? 60` becomes `st.duration ?? DEFAULTS.startup.duration`.

No Python changes. `defaults.py` stays authoritative, the parity test still
guards it, and the generator only reads what that chain already produces.

---

## Slice B — error plumbing

### B1. Structured errors on the wire

`common/settings_schema.py:691` builds the dotted paths and immediately
flattens them:

```python
def _format_errors(errs):
    return [f"{'.'.join(...)}: {err['msg']}" for err in errs]
```

`_api_post_settings_update` (`blueprints/api/routes.py:203`) then joins those
with `"; "` into one `message`, and `settingsApi.ts:82` reads `body.message`
and nothing else. The structure exists at the source and is destroyed twice
before it reaches a widget.

A sibling of `_format_errors` returns the pairs, so both forms come from one
place, and the rejection envelope gains a field:

```json
{
  "result": "error",
  "message": "Settings update failed: startup.duration: Input should be a valid integer",
  "errors": [{"path": "startup.duration", "message": "Input should be a valid integer"}],
  "data": {}
}
```

`message` stays byte-identical, so every other consumer of this endpoint is
unaffected. Both rejection layers emit it (the delta's `validate_partial_settings`
pass and the merged tree's `SettingsValidationError`). The three failures with
no path — unknown flag, `guard_controller_selection`, the bare `except` — send
`errors: []` rather than inventing one.

Client-side splitting of the joined string was rejected: pydantic messages
contain both `"; "` and `": "` (`"Value error, ..."`), so it would mis-split on
real errors.

### B2. Path → widget

`applySettings` carries `errors` through; `useSaveSettings` exposes them
alongside `status`. `normalizeSaveError` keeps its current job as the fallback
for path-less failures.

Each tab already names every path it writes, in its `setPath` calls — that list
becomes a declared `path → field id` map on the tab. On rejection the matching
field gets `aria-invalid`, its message renders beneath it, and the first
offender is focused. Errors whose path matches no widget on the current tab
still render in the existing summary line, so nothing is silently dropped.

This shares wiring with C3: the same `aria-describedby` that points a field at
its hint points it at its error when there is one.

---

## Slice C — validation and the sweep

### C1. Read-path validation (item 6)

`read_settings()` (`common/datastore_accessors.py:407`) returns
`read_settings_store()` untouched. Nothing on the read path has ever looked at
the tree.

Validation goes in `init()`, **not** in `read_settings()`. `init()` runs in all
three processes (`app.py:39`, `control.py:70`, `display_process.py:51`), so
every process start is checked, at zero steady-state cost — where validating
each read would tax the control loop's hot path.

It observes and does not enforce: strict-validate, and on failure write the
dotted-path errors to the log and return. It must not raise, strip, or
normalise. With write-gating, the migration registry and the shape digest all
in place, a read failure now means a hand-edited database, a downgrade, or a
migration bug — all things to report, none worth refusing to boot a grill over,
possibly mid-cook.

### C2. `setTimeout` → `waitFor` (item 7a)

All **10** test files under `web-react/tests/unit/components/settings/tabs/`
still sleep on a timer; only two files in the whole settings tree use `waitFor`.

### C3. `aria-describedby` (item 7c)

**Zero** occurrences across all eight components in
`web-react/src/components/settings/fields/`. Two cases:

- `NumberField.tsx:52` renders `{hint && <span className="pf-field-hint">}` as a
  sibling with no association.
- `HistoryTab.tsx:181-187` renders the gated-toggle explanation ("Stop the grill
  to change extended-data logging") as a sibling of a disabled `Toggle` —
  and `Toggle.tsx` has no hint prop at all, so it gains one.

### C4. Float-vs-int audit (item 7d)

`NumberField.tsx:36` is `onChange(Number(e.target.value))`. Typing `2.5` into a
field backed by an `int` produces a float that the strict backend rejects on
save, with an error the user cannot connect to what they typed. Every numeric
field is audited against the schema's `integer` vs `number`, and integer-backed
fields round on blur, where the existing clamp already runs.

---

## Non-goals

- **`display.config` stays loose.** `wizard_manifest.json` does declare option
  types for all 30 display modules, so it *could* be generated — but its only
  editor is the wizard's `DisplayStep.tsx`, which is driven at runtime by the
  wizard manifest API and never reads the generated settings type. The manifest
  also spells the key `default` where `controllers.json` spells it
  `option_default`, so the emitter would need a second dialect for no current
  consumer. Revisit when something reads `SettingsSchema.display.config`.
- **The other four loose zones stay loose.** `probe_devices`, `probe_info`,
  `dashboards` and `onesignal.devices` are hardware- or discovery-derived;
  nothing declares their shape.
- **Client-side runtime validation (ajv/zod) is still out** — SS-3's open
  question is untouched. B1 moves the server's existing verdict to the client;
  it does not add a second validator. The S1/S2 disagreement SS-3 records
  ("delete the duplicated React clamps" vs "client clamps STAY (UX)") is
  resolved in favour of S2: clamps stay, and C4 extends them.
- **`defaults.py` generation** is `backlogs/backend-backlog.md` item 1.
- **No new settings surfaces.** Nothing here adds a field, tab or endpoint.

## Risks

| Risk | Slice | Mitigation |
|---|---|---|
| Template-literal path types slow or exceed instantiation depth | A3 | Typecheck wall-clock is the gate; fall back to a hand-written path union |
| A generated defaults constant drifts from the schema | A4 | `--check` mode in CI, same shape as the existing `gen:types:check` |
| `errors` array read as authoritative when a layer sends `[]` | B1 | The summary line stays the fallback; tests cover a path-less rejection |
| Init-time validation logs noisily on a legitimately old tree | C1 | It runs after the migration registry, so a tree it complains about is one migrations could not fix |
| `waitFor` conversion masks a real race by widening the window | C2 | Convert one file at a time; a test that only passes with a longer timeout is a finding, not a conversion |

## Testing

- **A2/A4:** the emitters are pure functions from manifest/schema JSON to a
  string — tested directly, plus a `--check` drift gate per artifact.
- **A3:** type-level tests (`@ts-expect-error` on a typo'd path and on a
  wrong-typed value) rather than runtime assertions; `setPath`'s runtime
  behaviour is unchanged and already covered.
- **B1:** pinned at both ends — a backend test asserting the envelope for each
  rejection layer, and a client test that a rejection reaches the widget. Per
  the standing rule for cross-process seams, neither end may be the only pin.
- **C1:** a store deliberately made invalid, asserting `init()` logs and
  returns; and the negative control that it does **not** raise and does **not**
  alter the tree.
- **C3:** assert the `aria-describedby` target resolves to the hint's `id`, not
  merely that the attribute exists.

## Sequencing

A2 → A3 → A4 within Slice A: A3's `ValueAt` is more useful once
`controller.config` is typed, and A4's emitter reuses A2's schema-walking. A1
is independent and can land first.

Slices B and C are independent of A and of each other. Within C, C3 and B2
touch the same components and should not run concurrently; C1, C2 and C4 are
disjoint from everything else.
