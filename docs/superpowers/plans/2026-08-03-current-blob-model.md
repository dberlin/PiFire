# `control:current` Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the bare `control:current` dict with a pydantic model at the storage boundary and a frozen dataclass in memory, keeping the single-letter keys as serialization aliases so consumers migrate one at a time.

**Architecture:** `common/current_schema.py` holds `CurrentSchema` (pydantic, canonical field names, letter aliases), `CurrentSnapshot` (frozen dataclass), and the two builders — `build_current` and `zeroed_current` — that the accessors and both `Store` implementations share instead of each constructing the structure by hand. `read_current()` stays exactly as it is (raw dict, letters) as the legacy view; new code calls `read_current_snapshot()`.

**Tech Stack:** Python 3.14, pydantic 2.13+, SQLite via `common/datastore.py`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-03-current-blob-model-design.md`

## Global Constraints

- **Do not write to `/home/dannyb/sources/PiFire/pifire.db`.** A live `gunicorn` and a `control.py` are running against it. Tests use temp DBs via `datastore._reset_for_tests(str(tmp_path / "t.db"))`.
- Run tests as `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/...`. A bare `python`/`pytest` gives false failures.
- Format with `.venv/bin/ruff format <files>` before every commit. **Never `uvx ruff`** — the repo pins ruff <0.16 and a newer one reformats the tree.
- `except (A, B)` is written `except A, B` in this repo (Python 3.14+, ruff-canonical). Do not "fix" it back.
- Commit with **jj, not git**: `jj new` before the first edit of a task, `jj describe --stdin <<'EOF' … EOF` after. `git commit` silently works in this colocated repo and is wrong.
- `tests/characterization/test_process_command_golden.py` must stay green with **no edit** to `GOLDEN_SHA256` and no edit to `tests/characterization/fixtures/process_command_golden.json`. If it goes red, the change is wrong — the golden is not.
- Source comments state what the code achieves. Never narrate the change, the measurement, or the reasoning that led to it.
- New numeric fields are annotated `int | float`, in that order, never bare `float`. Pydantic's smart union keeps `0` an `int`; `float` would round-trip `"PSP": 0` as `0.0` and break the golden.

## Verified facts

Established by reading the tree on 2026-08-03. Do not re-derive; do not assume they still hold if a test disagrees — report the disagreement.

- The blob `pifire.db` holds right now, verbatim:
  `{"P": {"PitProbe": 0}, "F": {"PinkProbe": 0}, "PSP": 0, "NT": {"PinkProbe": 0, "PitProbe": 0}, "AUX": {}}`
  Five keys — no `TS`, no `LAST`. It was written by a build predating `LAST`, and it must still validate.
- `NT` values can be `None`: `display/_base_flex.py:834` reads `self.in_data["NT"][key] if self.in_data["NT"][key] is not None else 0`.
- `P`/`F`/`AUX` values can be `None`: a network-polled probe with a stale cache returns `None` rather than inventing a number.
- `tests/characterization/test_process_command_golden.py:801` seeds every case through `dsa.flush_current()`. Anything `flush_current` newly emits lands in the `get_current` golden entry.
- `controller/runtime/modes/base.py:693` calls `ctx.store.write_current(in_data)` with the `probe_history`-shaped dict — the only production caller.
- `_reference_temp` (`blueprints/api_tuner/routes.py:265`) has exactly one caller, line 304, and no direct test.

## File structure

| File | Responsibility |
|---|---|
| `common/current_schema.py` (new) | The model, the snapshot dataclass, the two builders, `load_current`, `dump_legacy`. Sole owner of the letter↔canonical mapping. |
| `common/datastore_accessors.py` (modify) | `write_current` / `flush_current` / `read_current_snapshot` delegate to the schema module. `read_current` untouched. `_carry_last_readings` deleted (moves into the schema module). |
| `controller/runtime/store.py` (modify) | `Store` ABC gains `read_current_snapshot`; `InMemoryStore` stops hand-building the structure; `SqliteStore` delegates. |
| `common/api_commands.py` (modify) | `_cmd_get_temp` reads a snapshot. |
| `blueprints/api_tuner/routes.py` (modify) | `_reference_temp` takes a snapshot. |
| `tests/unit/common/test_current_schema.py` (new) | Model behaviour: aliases, defaults, `extra="forbid"`, int-preservation, builders. |
| `tests/unit/datastore/test_current_accessors.py` (new) | Accessor behaviour against a temp DB: write/flush byte shape, snapshot fallback. |
| `tests/unit/datastore/test_sqlite_store_parity.py` (modify) | Fake↔real parity for `write_current` and `flush_current`. |

---

## Task 1: `common/current_schema.py`

**Files:**
- Create: `common/current_schema.py`
- Test: `tests/unit/common/test_current_schema.py`

**Interfaces:**
- Consumes: `write_log` from `common.common`.
- Produces, for Tasks 2–4:
  - `CurrentSchema` — pydantic model, fields `primary`, `food`, `aux`, `primary_setpoint`, `notify_targets`, `timestamp`, `last_readings`
  - `LastReading` — pydantic model, fields `temp: int | float`, `ts: int`
  - `CurrentSnapshot` — frozen dataclass, same seven field names
  - `build_current(in_data: dict, previous: CurrentSchema | None, now_ms: int) -> CurrentSchema`
  - `zeroed_current(probe_info: list[dict]) -> CurrentSchema`
  - `load_current(raw: dict) -> CurrentSchema | None`
  - `dump_legacy(schema: CurrentSchema, *, exclude_timestamp: bool = False) -> dict`
  - `to_snapshot(schema: CurrentSchema) -> CurrentSnapshot`

- [ ] **Step 1: Start a commit**

```bash
jj new
```

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/common/test_current_schema.py`:

```python
import pytest
from pydantic import ValidationError

from common.current_schema import (
    CurrentSchema,
    LastReading,
    build_current,
    dump_legacy,
    load_current,
    to_snapshot,
    zeroed_current,
)

# The blob a real install held on 2026-08-03, read out of pifire.db. Written by
# a build that predates LAST, which is why timestamp and last_readings must
# default rather than be required.
LIVE_PRE_LAST_BLOB = {
    "P": {"PitProbe": 0},
    "F": {"PinkProbe": 0},
    "PSP": 0,
    "NT": {"PinkProbe": 0, "PitProbe": 0},
    "AUX": {},
}


def test_live_pre_last_blob_validates_with_defaults():
    schema = CurrentSchema.model_validate(LIVE_PRE_LAST_BLOB)
    assert schema.primary == {"PitProbe": 0}
    assert schema.food == {"PinkProbe": 0}
    assert schema.aux == {}
    assert schema.primary_setpoint == 0
    assert schema.notify_targets == {"PinkProbe": 0, "PitProbe": 0}
    assert schema.timestamp == 0
    assert schema.last_readings == {}


def test_canonical_names_are_accepted_too():
    schema = CurrentSchema.model_validate(
        {
            "primary": {"PitProbe": 210},
            "food": {},
            "aux": {},
            "primary_setpoint": 225,
            "notify_targets": {},
            "timestamp": 1707345482984,
            "last_readings": {},
        }
    )
    assert schema.primary == {"PitProbe": 210}
    assert schema.primary_setpoint == 225


def test_full_blob_round_trips_byte_identically():
    blob = {
        "P": {"PitProbe": 210},
        "F": {"PinkProbe": None},
        "AUX": {},
        "PSP": 0,
        "NT": {"PitProbe": 0, "PinkProbe": 165},
        "TS": 1707345482984,
        "LAST": {"PinkProbe": {"temp": 140, "ts": 1707345400000}},
    }
    assert dump_legacy(CurrentSchema.model_validate(blob)) == blob


def test_integer_zero_stays_an_integer():
    # float would round-trip PSP: 0 as 0.0, which is a visible change to
    # /api/get/current and breaks the characterization golden.
    dumped = dump_legacy(CurrentSchema.model_validate(LIVE_PRE_LAST_BLOB))
    assert isinstance(dumped["PSP"], int)
    assert isinstance(dumped["P"]["PitProbe"], int)


def test_none_readings_survive():
    schema = CurrentSchema.model_validate({"P": {"PitProbe": None}, "NT": {"PitProbe": None}})
    assert schema.primary["PitProbe"] is None
    assert schema.notify_targets["PitProbe"] is None


def test_unmodeled_key_is_rejected():
    with pytest.raises(ValidationError):
        CurrentSchema.model_validate(dict(LIVE_PRE_LAST_BLOB, SURPRISE=1))


def test_load_current_returns_none_on_a_bad_blob():
    assert load_current({"SURPRISE": 1}) is None


def test_load_current_parses_a_good_blob():
    assert load_current(LIVE_PRE_LAST_BLOB).primary == {"PitProbe": 0}


PROBE_INFO = [
    {"label": "PitProbe", "type": "Primary"},
    {"label": "PinkProbe", "type": "Food"},
    {"label": "Ambient", "type": "Aux"},
]


def test_zeroed_current_rebuilds_from_the_probe_map():
    schema = zeroed_current(PROBE_INFO)
    assert schema.primary == {"PitProbe": 0}
    assert schema.food == {"PinkProbe": 0}
    assert schema.aux == {"Ambient": 0}
    assert schema.notify_targets == {"PitProbe": 0, "PinkProbe": 0, "Ambient": 0}
    assert schema.last_readings == {}


def test_zeroed_current_omits_the_timestamp_when_asked():
    dumped = dump_legacy(zeroed_current(PROBE_INFO), exclude_timestamp=True)
    assert set(dumped) == {"P", "F", "AUX", "PSP", "NT", "LAST"}


IN_DATA = {
    "probe_history": {
        "primary": {"PitProbe": 210},
        "food": {"PinkProbe": 140},
        "aux": {},
    },
    "primary_setpoint": 225,
    "notify_targets": {"PitProbe": 0, "PinkProbe": 165},
}


def test_build_current_maps_probe_history_onto_the_sections():
    schema = build_current(IN_DATA, None, 1000)
    assert schema.primary == {"PitProbe": 210}
    assert schema.food == {"PinkProbe": 140}
    assert schema.aux == {}
    assert schema.primary_setpoint == 225
    assert schema.timestamp == 1000


def test_build_current_stamps_every_live_reading():
    schema = build_current(IN_DATA, None, 1000)
    assert schema.last_readings["PitProbe"] == LastReading(temp=210, ts=1000)
    assert schema.last_readings["PinkProbe"] == LastReading(temp=140, ts=1000)


def test_build_current_carries_a_stale_probe_forward():
    first = build_current(IN_DATA, None, 1000)
    gone = {
        "probe_history": {"primary": {"PitProbe": 212}, "food": {"PinkProbe": None}, "aux": {}},
        "primary_setpoint": 225,
        "notify_targets": {"PitProbe": 0, "PinkProbe": 165},
    }
    second = build_current(gone, first, 5000)
    assert second.food["PinkProbe"] is None
    assert second.last_readings["PinkProbe"] == LastReading(temp=140, ts=1000)
    assert second.last_readings["PitProbe"] == LastReading(temp=212, ts=5000)


def test_build_current_drops_a_probe_that_never_reported():
    never = {
        "probe_history": {"primary": {"PitProbe": None}, "food": {}, "aux": {}},
        "primary_setpoint": 0,
        "notify_targets": {"PitProbe": 0},
    }
    assert build_current(never, None, 1000).last_readings == {}


def test_snapshot_is_frozen_and_carries_canonical_names():
    import dataclasses

    snap = to_snapshot(build_current(IN_DATA, None, 1000))
    assert snap.primary == {"PitProbe": 210}
    assert snap.primary_setpoint == 225
    assert snap.timestamp == 1000
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.primary_setpoint = 300


def test_snapshot_does_not_alias_the_model():
    schema = build_current(IN_DATA, None, 1000)
    snap = to_snapshot(schema)
    schema.primary["PitProbe"] = 999
    assert snap.primary["PitProbe"] == 210
```

- [ ] **Step 3: Run them to verify they fail**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_current_schema.py -q
```

Expected: collection error, `ModuleNotFoundError: No module named 'common.current_schema'`.

- [ ] **Step 4: Write the module**

Create `common/current_schema.py`:

```python
"""Pydantic model and runtime snapshot for the `control:current` blob.

Mirrors common/settings_schema.py and common/pellets_schema.py in shape -- one
pattern in this codebase rather than three -- but without their versioning
machinery. `settings` and `pelletdb` hold data that cannot be regenerated, so a
shape change there is a migration. This blob is a cache of the last control
pass: a shape it cannot parse is discarded and refilled within the second, so
there is no `schema_version`, no migration registry and no committed digest.

The stored blob and the `/api/get/current` response spell their keys with the
single letters they always have. Canonical names live here, in Python, where
the code is read and written; the letters are serialization aliases, and
consumers move off them one at a time.
"""

from dataclasses import dataclass

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

from common.common import write_log


class _CurrentSection(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class LastReading(_CurrentSection):
    """A probe's last real reading, and when it was taken."""

    temp: int | float
    ts: int


#: A probe device may have no reading to give -- a network-polled one whose
#: cache has gone stale returns None rather than inventing a number -- and a
#: notify target is likewise absent until one is set.
_Readings = dict[str, int | float | None]


class CurrentSchema(_CurrentSection):
    primary: _Readings = Field(
        default_factory=dict,
        validation_alias=AliasChoices("primary", "P"),
        serialization_alias="P",
    )
    food: _Readings = Field(
        default_factory=dict,
        validation_alias=AliasChoices("food", "F"),
        serialization_alias="F",
    )
    aux: _Readings = Field(
        default_factory=dict,
        validation_alias=AliasChoices("aux", "AUX"),
        serialization_alias="AUX",
    )
    primary_setpoint: int | float = Field(
        default=0,
        validation_alias=AliasChoices("primary_setpoint", "PSP"),
        serialization_alias="PSP",
    )
    notify_targets: _Readings = Field(
        default_factory=dict,
        validation_alias=AliasChoices("notify_targets", "NT"),
        serialization_alias="NT",
    )
    # Absent from a flushed blob and from any blob written before per-probe
    # freshness existed, so it defaults rather than being required.
    timestamp: int = Field(
        default=0,
        validation_alias=AliasChoices("timestamp", "TS"),
        serialization_alias="TS",
    )
    last_readings: dict[str, LastReading] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("last_readings", "LAST"),
        serialization_alias="LAST",
    )


@dataclass(frozen=True, slots=True)
class CurrentSnapshot:
    """What runtime code holds: no validation cost per read, no dict indexing."""

    primary: _Readings
    food: _Readings
    aux: _Readings
    primary_setpoint: int | float
    notify_targets: _Readings
    timestamp: int
    last_readings: dict[str, LastReading]


def load_current(raw):
    """Validate a stored blob.

    :param raw: the dict as stored
    :return: the validated schema, or None if it will not parse
    """
    try:
        return CurrentSchema.model_validate(raw)
    except ValidationError as exc:
        write_log(f"control:current failed validation and was discarded: {exc}")
        return None


def dump_legacy(schema, *, exclude_timestamp=False):
    """The stored/wire form: single-letter keys, every field present.

    :param exclude_timestamp: drop ``TS``. A flushed structure holds no
        readings, so there is no time at which they were taken -- the same
        reason its ``LAST`` is empty rather than carried.
    """
    exclude = {"timestamp"} if exclude_timestamp else None
    return schema.model_dump(by_alias=True, exclude=exclude)


def to_snapshot(schema):
    """Copy a validated schema into the frozen snapshot runtime code holds."""
    return CurrentSnapshot(
        primary=dict(schema.primary),
        food=dict(schema.food),
        aux=dict(schema.aux),
        primary_setpoint=schema.primary_setpoint,
        notify_targets=dict(schema.notify_targets),
        timestamp=schema.timestamp,
        last_readings=dict(schema.last_readings),
    )


def build_current(in_data, previous, now_ms):
    """The structure one control pass produces.

    :param in_data: the control loop's probe_history-shaped dict
    :param previous: the preceding schema, or None on the first write
    :param now_ms: the timestamp to stamp, passed in so callers are not racing
        the wall clock
    """
    primary = in_data["probe_history"]["primary"]
    food = in_data["probe_history"]["food"]
    aux = in_data["probe_history"]["aux"]
    return CurrentSchema(
        primary=primary,
        food=food,
        aux=aux,
        primary_setpoint=in_data["primary_setpoint"],
        notify_targets=in_data["notify_targets"],
        timestamp=now_ms,
        last_readings=_carry_last_readings((primary, food, aux), previous, now_ms),
    )


def _carry_last_readings(sections, previous, now_ms):
    """Per-probe last real reading, keyed by probe label.

    ``timestamp`` stamps the whole structure and keeps advancing while one
    probe is stale, so a consumer cannot work out from it how old a particular
    probe's last real value is. Carrying it here rather than in a UI keeps
    every dashboard telling one story about the same probe, and survives a
    client reload.
    """
    carried = previous.last_readings if previous is not None else {}
    last = {}
    for section in sections:
        for label, value in section.items():
            if value is not None:
                last[label] = LastReading(temp=value, ts=now_ms)
            elif label in carried:
                last[label] = carried[label]
    return last


def zeroed_current(probe_info):
    """A zeroed structure rebuilt from the configured probe map.

    Rebuilt rather than blanked in place, so a probe added or removed since the
    last write is reflected. ``last_readings`` is left empty: carrying a
    last-good value across a flush would date it to a cook that is over.

    :param probe_info: settings["probe_settings"]["probe_map"]["probe_info"]
    """
    schema = CurrentSchema()
    for probe in probe_info:
        if probe["type"] == "Primary":
            schema.primary[probe["label"]] = 0
        if probe["type"] == "Food":
            schema.food[probe["label"]] = 0
        if probe["type"] == "Aux":
            schema.aux[probe["label"]] = 0
        schema.notify_targets[probe["label"]] = 0
    return schema
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_current_schema.py -q
```

Expected: PASS, 16 tests.

If `test_full_blob_round_trips_byte_identically` fails on key ORDER, ignore it — dict equality is order-insensitive. If it fails on `0` vs `0.0`, the union order is wrong: it must be `int | float`.

- [ ] **Step 6: Format and commit**

```bash
.venv/bin/ruff format common/current_schema.py tests/unit/common/test_current_schema.py
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_current_schema.py -q
jj describe --stdin <<'EOF'
feat(common): model the control:current blob

Canonical field names in Python; the single letters stay as
serialization aliases so the stored blob and /api/get/current do not
move. One builder for the structure a control pass produces and one for
the zeroed structure, so the four hand-written copies can go.
EOF
```

---

## Task 2: Accessors go through the model

**Files:**
- Modify: `common/datastore_accessors.py:652-738`
- Test: `tests/unit/datastore/test_current_accessors.py` (create)

**Interfaces:**
- Consumes: everything Task 1 produces.
- Produces: `read_current_snapshot()` in `common.datastore_accessors`, returning a `CurrentSnapshot`. `read_current()` keeps its existing signature and behaviour exactly.

- [ ] **Step 1: Start a commit**

```bash
jj new
```

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/datastore/test_current_accessors.py`:

```python
import json

import pytest


@pytest.fixture
def db(tmp_path):
    from common import datastore

    datastore._reset_for_tests(str(tmp_path / "t.db"))
    datastore.init()
    yield datastore
    datastore._reset_for_tests(None)


PROBE_INFO = [
    {"label": "PitProbe", "name": "Pit", "type": "Primary", "enabled": True},
    {"label": "PinkProbe", "name": "Pink", "type": "Food", "enabled": True},
]

IN_DATA = {
    "probe_history": {
        "primary": {"PitProbe": 210},
        "food": {"PinkProbe": 140},
        "aux": {},
    },
    "primary_setpoint": 225,
    "notify_targets": {"PitProbe": 0, "PinkProbe": 165},
}


def _seed_probe_map(db):
    from common import datastore_accessors as dsa

    settings = dsa.read_settings()
    settings["probe_settings"]["probe_map"]["probe_info"] = PROBE_INFO
    dsa.write_settings(settings)


def test_flush_current_writes_the_same_key_set_as_before(db):
    # The characterization golden seeds every case through flush_current(), so
    # a key appearing here appears in the public get_current response.
    from common import datastore_accessors as dsa

    _seed_probe_map(db)
    dsa.flush_current()
    stored = json.loads(db.get_blob("control:current"))
    assert set(stored) == {"P", "F", "AUX", "PSP", "NT", "LAST"}
    assert stored["P"] == {"PitProbe": 0}
    assert stored["F"] == {"PinkProbe": 0}
    assert stored["NT"] == {"PitProbe": 0, "PinkProbe": 0}
    assert stored["LAST"] == {}


def test_write_current_stores_the_letter_spelled_blob(db):
    from common import datastore_accessors as dsa

    _seed_probe_map(db)
    dsa.write_current(IN_DATA)
    stored = json.loads(db.get_blob("control:current"))
    assert set(stored) == {"P", "F", "AUX", "PSP", "NT", "TS", "LAST"}
    assert stored["P"] == {"PitProbe": 210}
    assert stored["PSP"] == 225
    assert stored["TS"] > 0
    assert stored["LAST"]["PinkProbe"]["temp"] == 140


def test_read_current_still_returns_the_raw_letter_dict(db):
    from common import datastore_accessors as dsa

    _seed_probe_map(db)
    dsa.write_current(IN_DATA)
    current = dsa.read_current()
    assert isinstance(current, dict)
    assert current["P"] == {"PitProbe": 210}


def test_read_current_snapshot_returns_canonical_names(db):
    from common import datastore_accessors as dsa

    _seed_probe_map(db)
    dsa.write_current(IN_DATA)
    snap = dsa.read_current_snapshot()
    assert snap.primary == {"PitProbe": 210}
    assert snap.food == {"PinkProbe": 140}
    assert snap.primary_setpoint == 225
    assert snap.last_readings["PinkProbe"].temp == 140


def test_read_current_snapshot_survives_a_corrupt_blob(db):
    # The blob is a cache of the last control pass. A display or the control
    # loop taking an exception from it is strictly worse than one tick of
    # zeroes, and the next pass refills it.
    from common import datastore_accessors as dsa

    _seed_probe_map(db)
    db.set_blob("control:current", json.dumps({"SURPRISE": 1}))
    snap = dsa.read_current_snapshot()
    assert snap.primary == {"PitProbe": 0}
    assert snap.food == {"PinkProbe": 0}
    assert snap.last_readings == {}


def test_write_current_carries_a_stale_probe_across_passes(db):
    from common import datastore_accessors as dsa

    _seed_probe_map(db)
    dsa.write_current(IN_DATA)
    first = json.loads(db.get_blob("control:current"))["LAST"]["PinkProbe"]
    gone = {
        "probe_history": {"primary": {"PitProbe": 212}, "food": {"PinkProbe": None}, "aux": {}},
        "primary_setpoint": 225,
        "notify_targets": {"PitProbe": 0, "PinkProbe": 165},
    }
    dsa.write_current(gone)
    stored = json.loads(db.get_blob("control:current"))
    assert stored["F"]["PinkProbe"] is None
    assert stored["LAST"]["PinkProbe"] == first
```

- [ ] **Step 3: Run them to verify they fail**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/datastore/test_current_accessors.py -q
```

Expected: `test_read_current_snapshot_*` fail with `AttributeError: module 'common.datastore_accessors' has no attribute 'read_current_snapshot'`. The `flush_current` / `write_current` / `read_current` ones should already PASS — they pin today's behaviour, which the rewrite must not change.

- [ ] **Step 4: Add the import**

In `common/datastore_accessors.py`, after the `from common.control_delta import ...` line (line 30), add:

```python
from common.current_schema import build_current, dump_legacy, load_current, to_snapshot, zeroed_current
```

- [ ] **Step 5: Replace `write_current` and `_carry_last_readings`**

Replace `common/datastore_accessors.py:652-696` — the whole of `write_current` plus the whole of `_carry_last_readings` — with:

```python
def write_current(in_data):
    """
    Write current and populate a dictionary of data

    :param in_data: dictionary containing current temperatures
    """
    previous = load_current(_read_json_blob("control:current", dict))
    schema = build_current(in_data, previous, int(time.time() * 1000))
    _write_json_blob("control:current", dump_legacy(schema))
```

- [ ] **Step 6: Replace `flush_current`'s body**

In `common/datastore_accessors.py`, replace the body of `flush_current` below its docstring (everything from `settings = read_settings()` to the `return`) with:

```python
    settings = read_settings()
    schema = zeroed_current(settings["probe_settings"]["probe_map"]["probe_info"])
    # TS is dropped: a flushed structure holds no readings, so there is no time
    # at which they were taken.
    _write_json_blob("control:current", dump_legacy(schema, exclude_timestamp=True))

    return _read_json_blob("control:current", dict)
```

Delete the now-stale docstring paragraph that begins "LAST is emptied with the readings" — that reasoning lives on `zeroed_current` now. Keep the rest of the docstring.

- [ ] **Step 7: Add `read_current_snapshot` after `read_current`**

```python
def read_current_snapshot():
    """
    Read the current probe temps as a validated snapshot.

    An unparseable blob is discarded rather than repaired: it is a cache of the
    last control pass, and the next pass refills it.

    :return: CurrentSnapshot
    """
    schema = load_current(_read_json_blob("control:current", dict))
    if schema is None:
        settings = read_settings()
        schema = zeroed_current(settings["probe_settings"]["probe_map"]["probe_info"])
    return to_snapshot(schema)
```

- [ ] **Step 8: Run the new tests**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/datastore/test_current_accessors.py -q
```

Expected: PASS, 6 tests.

- [ ] **Step 9: Run the suites that already depend on this blob**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/characterization/test_process_command_golden.py \
  tests/web/test_socket_probe_staleness.py \
  tests/web/test_webapp_sqlite.py \
  tests/web/test_api_tuner_auto.py -q
```

Expected: PASS, with **no edit** to `GOLDEN_SHA256` or to `tests/characterization/fixtures/process_command_golden.json`. If the golden goes red, `flush_current` is emitting a key it did not emit before — fix `flush_current`, not the golden.

- [ ] **Step 10: Format and commit**

```bash
.venv/bin/ruff format common/datastore_accessors.py tests/unit/datastore/test_current_accessors.py
jj describe --stdin <<'EOF'
refactor(common): build control:current through its model

write_current and flush_current stop constructing the structure by hand
and go through the two builders. read_current keeps returning the raw
letter-spelled dict; read_current_snapshot is the typed way in.
EOF
```

---

## Task 3: Store parity

Fixes two defects the design names. Both must be **seen to fail** before the fix lands — a parity test that has never failed proves nothing.

**Files:**
- Modify: `controller/runtime/store.py` (`Store` ABC ~line 79, `InMemoryStore` lines 224-248, `SqliteStore` lines 411-419)
- Test: `tests/unit/datastore/test_sqlite_store_parity.py` (extend)

**Interfaces:**
- Consumes: everything Task 1 produces; `read_current_snapshot` from Task 2.
- Produces: `Store.read_current_snapshot()` on all three classes.

- [ ] **Step 1: Start a commit**

```bash
jj new
```

- [ ] **Step 2: Write the failing parity tests**

Append to `tests/unit/datastore/test_sqlite_store_parity.py`:

```python
_PARITY_PROBE_INFO = [
    {"label": "PitProbe", "name": "Pit", "type": "Primary", "enabled": True},
    {"label": "PinkProbe", "name": "Pink", "type": "Food", "enabled": True},
]

_PARITY_IN_DATA = {
    "probe_history": {
        "primary": {"PitProbe": 210},
        "food": {"PinkProbe": 140},
        "aux": {},
    },
    "primary_setpoint": 225,
    "notify_targets": {"PitProbe": 0, "PinkProbe": 165},
}


def _settings_with_probe_map(store):
    settings = store.read_settings()
    settings["probe_settings"]["probe_map"]["probe_info"] = _PARITY_PROBE_INFO
    return settings


def test_write_current_shape_parity(store):
    # The control loop hands write_current() probe_history-shaped data and what
    # gets STORED is the transformed blob. The fake used to keep the input
    # verbatim, so a test that wrote and then read through it was asserting
    # against a shape production never produces.
    from common import datastore_accessors as dsa
    from controller.runtime.store import InMemoryStore

    settings = _settings_with_probe_map(store)
    dsa.write_settings(settings)
    fake = InMemoryStore(settings=settings)

    store.write_current(_PARITY_IN_DATA)
    fake.write_current(_PARITY_IN_DATA)

    real_current = store.read_current()
    fake_current = fake.read_current()
    assert set(real_current) == set(fake_current)
    for key in ("P", "F", "AUX", "PSP", "NT"):
        assert real_current[key] == fake_current[key], key
    assert real_current["LAST"] == fake_current["LAST"]


def test_flush_current_shape_parity(store):
    from common import datastore_accessors as dsa
    from controller.runtime.store import InMemoryStore

    settings = _settings_with_probe_map(store)
    dsa.write_settings(settings)
    fake = InMemoryStore(settings=settings)

    assert store.flush_current() == fake.flush_current()


def test_read_current_snapshot_parity(store):
    from common import datastore_accessors as dsa
    from controller.runtime.store import InMemoryStore

    settings = _settings_with_probe_map(store)
    dsa.write_settings(settings)
    fake = InMemoryStore(settings=settings)

    store.write_current(_PARITY_IN_DATA)
    fake.write_current(_PARITY_IN_DATA)

    real = store.read_current_snapshot()
    fake_snap = fake.read_current_snapshot()
    assert real.primary == fake_snap.primary == {"PitProbe": 210}
    assert real.food == fake_snap.food == {"PinkProbe": 140}
    assert real.primary_setpoint == fake_snap.primary_setpoint == 225
    assert real.last_readings.keys() == fake_snap.last_readings.keys()
```

- [ ] **Step 3: Run them to verify they fail, and record HOW**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/unit/datastore/test_sqlite_store_parity.py -q -k "current"
```

Expected, and each failure mode must be observed — if any of these *passes* at this step, stop and report it, because the defect it pins is not the defect described:

- `test_write_current_shape_parity` — FAIL. The fake's keys are `probe_history`, `primary_setpoint`, `notify_targets`; the real store's are `P`, `F`, `AUX`, `PSP`, `NT`, `TS`, `LAST`.
- `test_flush_current_shape_parity` — FAIL. The fake's flushed dict has no `LAST` key.
- `test_read_current_snapshot_parity` — FAIL. `AttributeError`, the method does not exist yet.

- [ ] **Step 4: Add the import to `controller/runtime/store.py`**

After `from common.defaults import METRIC_COLUMNS, default_control, default_metrics` (line 17), add:

```python
from common.current_schema import build_current, dump_legacy, load_current, to_snapshot, zeroed_current
```

`time` is already imported (line 5); `copy` is already imported (line 4).

- [ ] **Step 5: Add the abstract method**

In the `Store` ABC, immediately after the `read_current` declaration:

```python
    @abstractmethod
    def read_current_snapshot(self): ...
```

- [ ] **Step 6: Rewrite the `InMemoryStore` current methods**

Replace `InMemoryStore.flush_current` and `InMemoryStore.write_current` (lines 227-248) with:

```python
def _probe_info(self):
    return self._settings.get("probe_settings", {}).get("probe_map", {}).get("probe_info", [])


def flush_current(self):
    # Mirror common.datastore_accessors.flush_current: rebuild a zeroed
    # structure from the configured probe_map rather than blanking in
    # place, so a probe added or removed since the last write is reflected.
    self._current = dump_legacy(zeroed_current(self._probe_info()), exclude_timestamp=True)
    return copy.deepcopy(self._current)


def write_current(self, in_data):
    # Mirror common.datastore_accessors.write_current: the caller hands in
    # probe_history-shaped data, and what is STORED is the transformed
    # blob.
    previous = load_current(self._current)
    schema = build_current(in_data, previous, int(time.time() * 1000))
    self._current = dump_legacy(schema)
```

Then add, immediately after `read_current`:

```python
    def read_current_snapshot(self):
        schema = load_current(self._current)
        if schema is None:
            schema = zeroed_current(self._probe_info())
        return to_snapshot(schema)
```

- [ ] **Step 7: Add the `SqliteStore` delegate**

After `SqliteStore.read_current` (line 411-412):

```python
    def read_current_snapshot(self):
        return _c.read_current_snapshot()
```

- [ ] **Step 8: Run the parity tests**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/datastore/test_sqlite_store_parity.py -q
```

Expected: PASS, all tests in the file.

- [ ] **Step 9: Run every suite that touches a Store**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/unit/runtime tests/unit/controller tests/unit/datastore \
  tests/characterization tests/e2e/test_work_cycle_e2e.py -q
```

Expected: PASS. `InMemoryStore.read_current()` now returns a different (correct) shape, so a test that was asserting the old wrong shape will fail here — that test was encoding the defect and should be updated to the production shape, not worked around. Report any such test in your report file.

- [ ] **Step 10: Format and commit**

```bash
.venv/bin/ruff format controller/runtime/store.py tests/unit/datastore/test_sqlite_store_parity.py
jj describe --stdin <<'EOF'
fix(runtime): make the in-memory store hold what production holds

write_current kept its probe_history-shaped input verbatim where the
SQLite store transforms it, so the fake's read_current returned a shape
production never produces; flush_current omitted LAST. Both now go
through the shared builders, and read_current_snapshot joins the Store
interface.
EOF
```

---

## Task 4: Migrate two consumers

**Files:**
- Modify: `common/api_commands.py:30-38` (import), `common/api_commands.py:289-310`
- Modify: `blueprints/api_tuner/routes.py:20-31` (import), `:265-277`, `:304`
- Test: `tests/unit/datastore/test_current_accessors.py` (extend)

**Interfaces:**
- Consumes: `read_current_snapshot` from Task 2.
- Produces: nothing new. This task proves the seam works.

- [ ] **Step 1: Start a commit**

```bash
jj new
```

- [ ] **Step 2: Write the failing test**

Append to `tests/unit/datastore/test_current_accessors.py`:

```python
def test_get_temp_reports_a_stale_probe_as_none(db):
    # A probe with no reading must reach the API as null, not as 0. The bare
    # dict let each consumer decide that for itself.
    from common import api_commands, datastore_accessors as dsa

    _seed_probe_map(db)
    dsa.write_current(
        {
            "probe_history": {"primary": {"PitProbe": 210}, "food": {"PinkProbe": None}, "aux": {}},
            "primary_setpoint": 225,
            "notify_targets": {"PitProbe": 0, "PinkProbe": 165},
        }
    )
    data = {"data": {}, "result": "OK"}
    api_commands._cmd_get_temp(data, None, None, ["temp", "PinkProbe"], None, None)
    assert data["result"] == "OK"
    assert data["data"]["temp"] is None

    data = {"data": {}, "result": "OK"}
    api_commands._cmd_get_temp(data, None, None, ["temp", "PitProbe"], None, None)
    assert data["data"]["temp"] == 210

    data = {"data": {}, "result": "OK"}
    api_commands._cmd_get_temp(data, None, None, ["temp", "NoSuchProbe"], None, None)
    assert data["result"] == "ERROR"
```

- [ ] **Step 3: Run it to verify it fails**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/unit/datastore/test_current_accessors.py::test_get_temp_reports_a_stale_probe_as_none -q
```

Expected: FAIL. `_cmd_get_temp` uses `in current_temps["F"].keys()`, so a `None`-valued `PinkProbe` IS found and `data["data"]["temp"]` is set to `None` — meaning the first two assertions pass and this is a **weak** negative control. Confirm the failure is the third block (`NoSuchProbe`) or nothing at all. If the whole test passes before the change, say so in your report and keep it: it is a characterization test pinning behaviour the migration must not alter.

- [ ] **Step 4: Migrate `_cmd_get_temp`**

In `common/api_commands.py`, change the import at line 32 from `read_current,` to `read_current_snapshot,` **only if** `read_current` has no other use in the file — check with `grep -n read_current common/api_commands.py`. Line 337 (`_cmd_get_current`) still uses it, so **keep both**: add `read_current_snapshot,` alongside `read_current,` in the import block, in alphabetical position.

Replace the body of `_cmd_get_temp` below its docstring (lines 300-310) with:

```python
    current = read_current_snapshot()

    for section in (current.primary, current.food, current.aux):
        if arglist[1] in section:
            data["data"]["temp"] = section[arglist[1]]
            return

    data["result"] = "ERROR"
    data["message"] = f"Probe {arglist[1]} not found or not specified."
```

- [ ] **Step 5: Migrate `_reference_temp`**

In `blueprints/api_tuner/routes.py`, add `read_current_snapshot,` to the `from common.datastore_accessors import (` block in alphabetical position. Remove `read_current,` from it **only if** `grep -n "read_current(" blueprints/api_tuner/routes.py` shows no remaining call.

Replace `_reference_temp` (lines 265-277) with:

```python
def _reference_temp(current, reference):
    """The reference probe's temperature, or None if it is not reporting.

    Probes are checked primary, then food, then aux, matching Flask. None (not
    -1): a probe absent from every group is not reporting, which the client
    renders as "waiting" -- distinct from a real reading that happens to be
    zero.
    """
    for values in (current.primary, current.food, current.aux):
        if reference in values:
            return values[reference]
    return None
```

Change line 304 to:

```python
    current_temp = _reference_temp(read_current_snapshot(), reference)
```

- [ ] **Step 6: Run the tests**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/unit/datastore/test_current_accessors.py \
  tests/web/test_api_tuner_auto.py \
  tests/characterization/test_process_command_golden.py -q
```

Expected: PASS, golden included and unedited.

- [ ] **Step 7: Run the whole suite**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
```

Expected: PASS. Chromium-marked web tests may SKIP in a worktree without Chromium; note any skips in your report rather than treating them as green.

- [ ] **Step 8: Format and commit**

```bash
.venv/bin/ruff format common/api_commands.py blueprints/api_tuner/routes.py tests/unit/datastore/test_current_accessors.py
jj describe --stdin <<'EOF'
refactor: read the current snapshot in get_temp and the tuner

Two consumers off the letter-spelled dict. The rest follow one at a
time; get_current and the socket payload go last, because they emit the
letters to clients.
EOF
```

---

## Parallelization

Tasks 1 → 2 → 3 are strictly sequential: Task 2 needs Task 1's builders, and Task 3 needs both the builders and `read_current_snapshot`.

**Task 4 can run in parallel with Task 3** once Task 2 has landed. They share no files:

| | Task 3 | Task 4 |
|---|---|---|
| source | `controller/runtime/store.py` | `common/api_commands.py`, `blueprints/api_tuner/routes.py` |
| tests | `tests/unit/datastore/test_sqlite_store_parity.py` | `tests/unit/datastore/test_current_accessors.py` |

Disjoint files are not sufficient on their own — concurrency needs **isolated jj workspaces**, one per task, or the two agents will fight over the working copy. Both tasks end by running the full suite, so whichever lands second rebases and re-runs it.

If running them sequentially, do Task 3 first: it is the one carrying the defect fixes, and Task 4's full-suite gate then covers both.

## Verification before completion

The change is done when all of the following hold, each observed rather than assumed:

- `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q` is green.
- `git diff` (or `jj diff`) shows **no** change to `tests/characterization/fixtures/process_command_golden.json` and none to `GOLDEN_SHA256`.
- `grep -rn '_carry_last_readings' --include='*.py' .` returns only `common/current_schema.py`.
- `grep -c 'current\["P"\]\|current\["F"\]\|current\["AUX"\]' common/api_commands.py blueprints/api_tuner/routes.py` returns 0 for the two migrated functions (`_cmd_get_current` at `api_commands.py:337` legitimately still uses `read_current()` — it emits the blob to the wire).
- `.venv/bin/ruff format --check` is clean on every touched file.
