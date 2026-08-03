# Modelling `control:current` — Design

**Date:** 2026-08-03
**Backlog item:** `docs/superpowers/backlogs/backend-backlog.md` item 3.

## Problem

`control:current` is the highest-traffic durable blob PiFire has. The control
loop writes it once per pass; the web tier, the Qt Quick display, the
pygame/flex display and two public API routes read it. It is a bare dict with
single-letter keys (`P`, `F`, `AUX`, `PSP`, `NT`, `TS`, `LAST`), no model, no
validation and no declared shape, so every consumer carries its own idea of
what the keys mean and which may be absent.

Two concrete defects follow from that, both found while sizing this work:

1. **`InMemoryStore` and `SqliteStore` disagree on what `current` is.**
   `controller/runtime/modes/base.py:693` calls `ctx.store.write_current(in_data)`
   with the `probe_history` / `primary_setpoint` shape. `SqliteStore.write_current`
   delegates to `common.datastore_accessors.write_current`, which *transforms*
   that into the `P`/`F`/`AUX` blob. `InMemoryStore.write_current`
   (`controller/runtime/store.py:247`) does `self._current = copy.deepcopy(in_data)` —
   verbatim. A test that writes through the fake and then reads it back is
   asserting against a shape production never produces.

2. **`InMemoryStore.flush_current` omits `LAST`.** Production's
   `flush_current` builds `{"P", "F", "PSP", "NT", "AUX", "LAST"}`; the fake's
   (`store.py:234`) builds the same minus `LAST`.

Both are the same root cause: the structure is built by hand in four places
(`datastore_accessors.write_current`, `datastore_accessors.flush_current`,
`InMemoryStore.flush_current`, and — by omission — `InMemoryStore.write_current`)
and nothing holds them to one shape.

## Approach

Two layers, matching what the repo already does elsewhere: pydantic validates
and normalises at the storage boundary (`common/settings_schema.py`,
`common/pellets_schema.py`), and a plain frozen dataclass is what runtime code
holds and passes around (`controller/runtime/state.py`,
`controller/runtime/context.py`).

**The stored blob and the wire keep the single-letter spelling in this
change.** Canonical names exist in Python, where the code is written and read;
the letters stay on disk and in `/api/get/current` until no consumer subscripts
them. That is what makes the migration incremental rather than a flag day, and
it means `/api/get/current` stays byte-identical, so
`tests/characterization/test_process_command_golden.py` does **not**
re-baseline. Flipping storage and the wire to canonical names is a separate,
later change (filed in the backlog).

## `common/current_schema.py`

### `LastReading`

| field | type | notes |
|---|---|---|
| `temp` | `int \| float` | the last non-`None` reading |
| `ts` | `int` | epoch ms when it was taken |

`temp`/`ts` keep their names. They were introduced 2026-08-02, have three
consumers, and `ts` is unambiguous nested inside a reading; renaming them would
be churn, not cleanup.

### `CurrentSchema`

`model_config = ConfigDict(extra="forbid", populate_by_name=True)`.

| canonical field | type | default | validation alias | serialization alias |
|---|---|---|---|---|
| `primary` | `dict[str, int \| float \| None]` | `{}` | `AliasChoices("primary", "P")` | `P` |
| `food` | `dict[str, int \| float \| None]` | `{}` | `AliasChoices("food", "F")` | `F` |
| `aux` | `dict[str, int \| float \| None]` | `{}` | `AliasChoices("aux", "AUX")` | `AUX` |
| `primary_setpoint` | `int \| float` | `0` | `AliasChoices("primary_setpoint", "PSP")` | `PSP` |
| `notify_targets` | `dict[str, int \| float]` | `{}` | `AliasChoices("notify_targets", "NT")` | `NT` |
| `timestamp` | `int` | `0` | `AliasChoices("timestamp", "TS")` | `TS` |
| `last_readings` | `dict[str, LastReading]` | `{}` | `AliasChoices("last_readings", "LAST")` | `LAST` |

Two constraints that are not decoration:

- **Numeric unions are `int | float`, in that order.** Pydantic's smart union
  keeps `0` an `int`. Declaring `float` would round-trip `"PSP": 0` as
  `"PSP": 0.0`, which is a visible change to `/api/get/current` and would break
  the characterization golden.
- **`timestamp` and `last_readings` need defaults.** `flush_current` writes a
  blob with no `TS` key at all, so a flushed blob must validate without one.

`extra="forbid"` is deliberate: an unmodeled key in the blob is a shape change
nobody declared, and the `LAST` episode is exactly that happening unnoticed.

### Module API

```python
def build_current(in_data: dict, previous: CurrentSchema | None, now_ms: int) -> CurrentSchema
```

Holds the `probe_history` → sections mapping and the per-probe carry-forward
that is `_carry_last_readings` today. `previous` is the preceding *validated
schema* — not the raw stored dict — so the carry-forward never has to know
which spelling is on disk and survives the eventual rename; `None` on the first
write. `now_ms` is passed in rather than read inside, so tests do not race the
wall clock.

```python
def load_current(raw: dict) -> CurrentSchema | None
```

Validates a stored blob, logging and returning `None` rather than raising when
it will not parse. Both stores and `read_current_snapshot()` go through it, so
the discard-and-refill policy is written once.

```python
def zeroed_current(probe_info: list[dict]) -> CurrentSchema
```

The zeroed structure both `flush_current` implementations build today: every
`Primary` label into `primary`, every `Food` into `food`, every `Aux` into
`aux`, all at `0`; every label into `notify_targets` at `0`; `last_readings`
empty, because carrying a last-good value across a flush would date it to a
cook that is over.

```python
def dump_legacy(schema: CurrentSchema) -> dict     # model_dump(by_alias=True)
def to_snapshot(schema: CurrentSchema) -> CurrentSnapshot
```

### `CurrentSnapshot`

`@dataclass(frozen=True, slots=True)`, canonical names only, same fields and
types as `CurrentSchema`, `last_readings` holding `LastReading` instances.

This is what new code holds. Frozen and slotted so it is cheap to pass across
seams on a path that runs once a second, with no validation cost per read. The
pydantic model exists only to parse and emit; the dataclass is what the program
actually carries.

## Accessors

`common/datastore_accessors.py`:

- `write_current(in_data)` → `build_current(in_data, _read_json_blob(...), now_ms)`
  then `_write_json_blob("control:current", dump_legacy(schema))`. Same bytes as
  today.
- `flush_current()` → `zeroed_current(probe_info)`, dumped with
  `exclude={"timestamp"}` and stored. Same key set as today.

  The exclusion is semantic, not a fudge: `TS` records when the readings were
  taken, and a flushed blob has no readings, so it has no time — the same
  reason `last_readings` is emptied rather than carried. It also matters
  mechanically: `tests/characterization/test_process_command_golden.py:801`
  seeds every case through `dsa.flush_current()`, so a `"TS": 0` appearing in
  the flushed blob would flow into the `get_current` golden entry and force a
  re-baseline.
- `read_current()` — **unchanged.** Raw dict, letters, no validation. It is the
  legacy view: deliberately dumb, and deleted when its last consumer leaves.
- `read_current_snapshot()` — new. Reads the blob, validates, returns a
  `CurrentSnapshot`.

`_carry_last_readings` moves into `current_schema.py` as an implementation
detail of `build_current`; nothing outside the accessor calls it today.

## Error handling

`read_current_snapshot()` never raises into a caller. On `ValidationError` it
logs at `error` with the pydantic message and returns a snapshot built from
`zeroed_current` against the configured probe map.

The blob is disposable — it is a cache of the last control pass, not a record —
and the next pass refills it within a second. A display or the control loop
taking an exception from a corrupt cache is strictly worse than showing zeroes
for one tick. For the same reason there is **no `schema_version` field, no
`_SHAPE_MIGRATIONS` entry, and no committed shape digest**: those exist for
`settings` and `pelletdb` because their contents cannot be regenerated, and
this blob's can.

Downgrading PiFire across this change is not a supported path and does not need
to be: the stored spelling does not change, so an older build reads the same
blob it always did.

## The stores

`controller/runtime/store.py`:

- `Store` ABC gains `read_current_snapshot(self)`.
- `SqliteStore.read_current_snapshot` delegates to the accessor, as its other
  methods do.
- `InMemoryStore.write_current(in_data)` calls `build_current` and stores
  `dump_legacy(...)`, so the fake stores what production stores. This is the fix
  for defect 1.
- `InMemoryStore.flush_current()` calls `zeroed_current`, which fixes the
  missing `LAST` (defect 2) by construction rather than by adding a key to a
  second literal.
- `InMemoryStore.read_current_snapshot` validates its own stored dict, so the
  fake exercises the same validation path production does.

`InMemoryStore` needs a `previous` for `build_current`: it passes its own
`self._current`, which is the legacy-spelled dict it just stored.

## Consumers migrated in this change

Two, both small pure reads, enough to prove the seam without leaving a model
with no callers:

- `common/api_commands.py::_cmd_get_temp` (line 300) — three-branch label
  lookup across `P`/`F`/`AUX` becomes one across
  `primary`/`food`/`aux`.
- `blueprints/api_tuner/routes.py::_reference_temp` (called at line 304).

Everything else stays on `read_current()` and moves later, one consumer per
change: `blueprints/mobile/socket_io.py::_get_probe_data`,
`display/_base_flex.py`, `display/qtapp.py` → `qtbackend`, and last of all
`_cmd_get_current` / `_api_get_current`, which emit the blob to the wire and so
hold the letters until the wire itself is renamed. `_base_flex` additionally
*mutates* through the subscript — its zeroing block at `_base_flex.py:279-289`
assigns into `self.in_data["P"]`, `["F"]`, `["AUX"]`, `["NT"]` and rebinds
`self.in_data["PSP"] = 0` — which a frozen dataclass will not accept. That
consumer needs its own change, not a line edit.

## Testing

- **Load fidelity against the live blob.** The blob `pifire.db` actually holds
  today is

  ```json
  {"P": {"PitProbe": 0}, "F": {"PinkProbe": 0}, "PSP": 0,
   "NT": {"PinkProbe": 0, "PitProbe": 0}, "AUX": {}}
  ```

  — five keys, written by a build that predates `LAST`. It must validate, with
  `timestamp == 0` and `last_readings == {}` coming from defaults. This is not
  a hypothetical: it is why those two defaults exist, and it is a real
  install's state, not a fixture written from the producer's own literals.

- **Round-trip fidelity.** A blob produced by the *current* `write_current` →
  `CurrentSchema` → `dump_legacy()` equals the input, with `0` still an `int`
  and `None` readings still `None`. Generate it by running the real producer
  against a temp DB; the assertion is round-trip identity, which has teeth
  whatever the input was.

  Note the asymmetry with the bullet above: `dump_legacy` emits every field
  including defaults, so a five-key blob does **not** round-trip to five keys.
  Loading old blobs and byte-preserving new ones are two different properties
  and get two different tests.

- **Flush output unchanged.** `flush_current()` writes the same key set it
  writes today — no `TS`. Assert the key set explicitly; the golden depends on
  it.
- **Fake/real parity.** The same `in_data` through `InMemoryStore` and through
  `SqliteStore` yields equal `read_current()` results. **This test must be seen
  to fail against the current `store.py` before the fix lands** — it is pinning
  defect 1, and a parity test that has never failed proves nothing.
- **Flush parity.** `flush_current()` on both stores yields equal dicts,
  `LAST` included. Also must be seen to fail first (defect 2).
- **Defaults.** A blob with no `TS` and no `LAST` — what `flush_current` writes —
  validates, and yields `timestamp == 0` and `last_readings == {}`.
- **Unmodeled key.** A blob carrying an extra key fails validation, and
  `read_current_snapshot()` returns a zeroed snapshot and logs rather than
  raising.
- **Golden unchanged.** `tests/characterization/test_process_command_golden.py`
  passes with no edit to `GOLDEN_SHA256` and no edit to
  `fixtures/process_command_golden.json`. If it cannot, the numeric-union
  decision above was got wrong and that is the thing to fix — not the golden.

Run with `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/`.

## Out of scope

Filed in `docs/superpowers/backlogs/backend-backlog.md` (items 4 and 5): the
history rows' identical single-letter vocabulary, and flipping the stored blob
and `/api/get/current` to canonical names once no consumer needs the letters.
