# Schema Versioning for Persisted Shapes — Design

**Date:** 2026-08-02
**Status:** Approved design, pending implementation plan
**Backlog:** `react-migration-backlog.md`, item 10, *Schema and toolchain
follow-ups* — "nothing PiFire persists records the schema it was written
against"

## Goal

Give every durable blob PiFire writes an explicit, enforced record of the SHAPE
it was written against, so that:

- a shape migration is gated on the shape's own version rather than on the
  release build number, and
- staleness is a comparison rather than a heuristic.

The intended end state is that no future shape change needs an ungated repair
pass or a synthesized fingerprint to be safe.

## Background

### What exists today

**`settings["versions"]` is the RELEASE version, not a shape version.** It is
copied wholesale from `updater/updater_manifest.json`'s `metadata.versions`
(`common/defaults.py:38`):

```json
{"server": "1.11.0", "cookfile": "1.5.0", "recipe": "1.0.0", "build": 74}
```

`upgrade_settings(prev_ver, ...)` gates on `prev_ver`, derived from
`versions.server` (`common/settings_migration.py:237`), and
`_upgrade_settings_in_store` decides whether to call it by comparing
`versions.server`/`versions.build` against the code's current values
(`common/datastore.py:423-430`).

**That comparison is demonstrably not a proxy for shape.** A real grill
database sat at `1.11.0 build 71` — the code's own current version, so the gate
was closed — while still holding the pre-71 `i2c_bus_kind`/`i2c_bus_num`
settings shape. The version had been bumped by a release that did not yet
contain the migration. Once the release number passes an install, the gate can
never reopen for it, and there is nothing else to ask.

**`wizard/wizard_manifest.json` declares no identity at all.** Its top-level
keys are exactly `modules`, `probe_config_options`, `boards`. Nothing says which
manifest a given file is.

**The `wizard:install` draft blob likewise carries no declared identity.** A
draft keys its answers by manifest dependency NAME, so one written against a
different manifest binds its answers to nothing.

### The two substitutes already built

Both of these work. Both exist because the thing this spec proposes did not.

1. **The i2c settings-shape repair runs UNGATED.**
   `_migrate_i2c_buses(settings)` is called unconditionally from
   `_upgrade_settings_in_store` on every connect, outside the version check,
   because a version-gated migration was skipped by an unchecked path four
   separate times on that branch. It is idempotent and returns `False` when
   there is nothing to do, so the cost today is a scan, not a write.

2. **The wizard draft carries a SHA-256 fingerprint of the manifest.**
   `_manifest_fingerprint()` (`blueprints/api_wizard/routes.py:53`) hashes the
   sorted `section/module/dependency` triples plus each probes module's
   `device_specific` config labels; `_draft_is_stale()` compares it, and treats
   an unstamped draft as stale without bothering to hash. It was synthesized
   precisely because the manifest declares no identity of its own — and it
   caught a real defect: a stale draft silently rendered "Basic" for a grill
   running on two USB-I2C bridges.

### The pattern already exists, one layer down

`common/datastore.py:215-250` versions the TABLE schema with SQLite's
`PRAGMA user_version`, currently `4`:

```python
version = conn.execute("PRAGMA user_version").fetchone()[0]
if 0 < version < 3:
    ...  # rebuild metrics
if 0 < version < 4:
    with transaction(conn):
        _migrate_history_to_numeric_psp(conn)
if version < 4:
    conn.execute("PRAGMA user_version=4")
```

Monotonic integer, ordered gated steps, `0 <` guards so a freshly-created
database skips migrations the DDL already satisfied, and the stamp written last
so a crash mid-migration retries from scratch rather than leaving a half-built
table.

**The tables have this. The blobs stored INSIDE those tables do not.** That
asymmetry is the entire gap. This spec proposes applying the mechanism the
repository already trusts, one layer up.

Worth noting alongside: `updater_manifest.json` already carries `cookfile:
1.5.0` and `recipe: 1.0.0`, which ARE genuine shape versions — `read_cookfile`
gates on the former. They are correct, and they are parked inside the RELEASE
manifest, which is very likely why nobody thought to add a settings one: the
file's name says "updater", so its contents read as release metadata.

## The central distinction

The backlog entry asks for "an explicit schema version on the wizard manifest
and on every persisted blob". **Following that literally would be a mistake**,
because two different questions are being conflated, and only one of them wants
a declared version.

| | Question | Ordered? | Right answer |
|---|---|---|---|
| **A** | *Which migrations must run to bring this tree up to date?* | **Yes** | Monotonic integer |
| **B** | *Was this blob written against the artifact I am holding right now?* | **No** — boolean | Content hash |

A hash tells you **that** something differs, never **from what**, so it cannot
drive an ordered migration chain: question A needs the integer.

Question B needs no ordering, and there a content hash is **strictly better than
a declared version**, for one reason that this codebase has already paid for: a
declared version is a promise that a human remembers to bump it, and the record
here is that they do not. The i2c migration was skipped by an unchecked path
four separate times. A fingerprint derived from the content cannot be forgotten,
because nobody has to do anything.

**So: keep `_manifest_fingerprint`. Do not add a manifest version for the
draft's sake.** This is a deliberate divergence from the backlog entry's own
framing, and the reason is recorded here so it is not re-raised.

There IS a separate, real case for a declared `manifest_version` on
`wizard_manifest.json`: operator diagnostics and support ("which manifest is
this grill on?"), which a hash answers uselessly. That is a different
requirement with a different consumer, and it is **out of scope** below rather
than bundled in.

## Architecture

### 1. `SETTINGS_SCHEMA_VERSION`

A module-level integer in `common/settings_schema.py`, the single source of
truth for the settings tree's SHAPE. It has no relationship to
`versions.server`/`build` and must never be derived from them — that coupling is
the defect being fixed.

```python
#: The shape of the settings tree, independent of the release version.
#: Bumped by any change to the persisted shape that an existing install
#: cannot simply be validated into: a rename, a retype, a restructure.
#: NOT bumped for an added or deleted field -- the repair pass already
#: handles both losslessly (added takes its default, deleted is stripped).
SETTINGS_SCHEMA_VERSION = 1
```

Starting at 1, with `_migrate_i2c_buses` as step 1 -- see *Decisions taken*.

Stored as a top-level `settings["schema_version"]`, modeled on
`SettingsSchema` like any other field so the strict gate covers it.

**Top-level, not under `versions`.** Putting it beside `server`/`build` would
re-create the exact confusion this fixes — the next reader would assume the
sibling fields move together.

### 2. An ordered migration registry

Replacing the current ad-hoc chain of `if prev_ver[0] <= 1 and prev_ver[1] <= 4`
blocks for SHAPE concerns (the release-gated blocks stay where they are; see
*Backward compatibility*):

```python
#: (target_version, migration) in ascending order. Each callable takes the
#: tree, mutates in place, and returns True if it changed anything.
#: A step's number is the version the tree is AT once it has run.
_SHAPE_MIGRATIONS = [
    (1, _migrate_i2c_buses),
]
```

The runner, mirroring `_ensure_schema`'s structure deliberately:

```python
stored = settings.get("schema_version", 0)
for target, migrate in _SHAPE_MIGRATIONS:
    if stored < target and migrate(settings):
        changed = True
settings["schema_version"] = SETTINGS_SCHEMA_VERSION
```

The stamp is written **last and unconditionally**, inside the same
`BEGIN IMMEDIATE` that `_upgrade_settings_in_store` already holds, so a crash
mid-chain leaves the old stamp and the whole chain retries.

### 3. What each starting state does

| Stored `schema_version` | Meaning | Behaviour |
|---|---|---|
| absent | Every install that exists today | Treated as `0`; every step runs |
| `0 < n < CURRENT` | Migrated part-way | Steps above `n` run |
| `== CURRENT` | Current | Nothing runs; this is the steady state |
| `> CURRENT` | Downgraded PiFire | See below |

**A fresh install is stamped CURRENT by `default_settings()`**, so no migration
ever runs against a tree the defaults just built — the `0 <` guard in
`_ensure_schema` exists for exactly this reason and the same reasoning applies.

**A tree from the future** (an operator downgraded PiFire) must not be silently
migrated backwards or wiped. Log it, run nothing, and let the strict-schema
repair pass strip whatever the older code does not model. That is lossy for the
newer keys and it is the honest outcome: this code cannot know what they meant.
It is also not hypothetical — the updater offers branch switching.

### 4. The enforcement that makes the version trustworthy

**Without this section, the spec recreates the failure it is fixing.** A
declared version that a human must remember to bump is precisely what was
skipped four times.

A test hashes the shape that `SettingsSchema` models and compares it against a
committed constant:

```python
def test_shape_change_requires_a_schema_version_bump():
    """A shape change without a version bump is invisible until an operator's
    install fails to migrate. This is the gate that makes SETTINGS_SCHEMA_VERSION
    a fact rather than a promise."""
    assert _shape_digest(SettingsSchema) == COMMITTED_SHAPE_DIGEST
```

Changing the modeled shape fails the test with a message naming the two things
to do: bump `SETTINGS_SCHEMA_VERSION` (adding a migration if existing data needs
moving) and update the digest.

#### What the digest covers

**Paths, types AND constraints** (owner, 2026-08-02). Derived from pydantic's
`model_fields` by walking the model tree, one entry per leaf:

```
(dotted_path, annotation, sorted constraint metadata)
```

- **Path** uses the **alias** where one exists, because the alias is what is on
  disk — `platform.system.1WIRE`, not `platform.system.one_wire`. The digest
  describes the persisted shape, not the Python attribute names.
- **Type** is included: a retype is a migration-worthy change and the gate ought
  to catch it. `str` → `int` keeps the path and would otherwise pass silently.
- **Constraints** are included for the same reason, and it is not a corner
  case: tightening `rating` to `ge=1, le=5` is exactly the pellet DB's v2
  migration (§5.6). A tightened bound can reject data an install already holds,
  which is the definition of needing a migration.
- **Defaults and descriptions are EXCLUDED.** Changing a default cannot
  invalidate stored data — every existing tree already carries a value — so
  firing on one would train people to update the digest without reading it,
  which is how a gate stops working.

#### The consequence, accepted deliberately

Covering types means **widening an annotation fires the gate even though
widening alone needs no data rewrite** — as `2026-08-01-structured-i2c-bus-config`
did to the address field. An earlier draft treated that as a reason to cover
paths only. **Ruled otherwise (owner, 2026-08-02): supporting an old version and
rewriting it is fine.**

That ruling is what makes strictness affordable. The gate does not decide
whether a migration is needed; it decides that a human LOOKS. The two honest
responses to it are:

1. The change needs a rewrite → bump the version, add a migration step.
2. The change is a pure widening → bump the version, register a **no-op step**.

Option 2 is not a loophole, it is the record: the version increments, the tree
gets restamped, and the step list documents that this shape change deliberately
moved no data. Silently exempting the change would leave nothing saying that
question was ever asked.

#### Relationship to the manifest fingerprint

The two mechanisms are now deliberately NOT symmetrical, and the asymmetry is
the point. `_manifest_fingerprint` hashes **names only**, because a draft binds
to names and nothing else — a manifest that changes a module's description or
its option list still binds every drafted answer correctly. The settings digest
hashes **names, types and constraints**, because a stored tree binds to all
three: a retype or a tightened bound can make data that was valid yesterday
invalid today. Each hashes exactly what its consumer depends on.

Beyond that, the settings tree carries an integer as well, because only it has
to answer question A.

## Which blobs are in scope

The `kv` table holds ten keys. They do not all want the same treatment:

| Key | Durable? | Treatment |
|---|---|---|
| `settings:general` | Yes | **Versioned** — §1-4 above |
| `pellets:general` | Yes | **Modeled, then versioned** — §5 below |
| `wizard:install` | Yes | **Fingerprint** — already correct, unchanged |
| `system:os_info` | Cache | None — re-probed, no migration is meaningful |
| `control:*` (6 keys) | No | None — `flush_control()` at control-process boot |

## 5. The pellet database

`pellets:general` is durable and operator-owned — brands, woods, profiles and
the usage log — and it has **no schema model at all**, so unlike every other
durable blob it also has no repair pass. It is the one place where a shape
change has no safety net.

### 5.1 Why this is more urgent than "no migration net"

The same admin route restores both backups
(`blueprints/api_admin/routes.py:320-337`):

```python
if kind == "settings":
    try:
        write_settings(read_settings_file(filename=path, init=True))
    except SettingsValidationError as exc:
        return error("invalid_backup", 400, detail="; ".join(exc.errors))
    ...
else:
    write_pellet_db(read_pellet_db_file(filename=path))
```

The settings branch validates and refuses a bad backup with a 400 naming the
offending paths. **The pellet branch writes whatever JSON the file contained,
straight into the live store.** `write_pellet_db()` is a direct call to
`write_pellets_store()` with no gate of any kind. So this is not only a
migration gap: it is an unvalidated write path that an operator can reach from
the admin UI today, and the file it reads is one the same UI let them upload.

Modeling the blob closes both at once, because the model is what
`validate_settings_tree`'s equivalent would enforce.

### 5.2 The shape, taken from a live install

Read off the running grill rather than from `default_pellets()`, because the two
disagree and the defaults are the misleading one — see `est_usage` below. This
follows the rule the admin payload already taught the project the expensive way:
**type from a real response, not the producer's fallback literals.**

```
pelletdb
├── current      {pelletid, hopper_level, date_loaded, est_usage}
├── archive      {id: {id, brand, wood, rating, comments}}
├── log          {timestamp: pelletid | "deleted"}
├── brands       [str]        free-text vocabulary
├── woods        [str]        free-text vocabulary
└── lastupdated  {time: int}  same shape as settings
```

### 5.3 Five things a model written from `defaults.py` would get wrong

Each of these is live behaviour, verified in the writers, and each would make a
naively-authored model reject a legitimate database:

1. **`est_usage` is a FLOAT.** `default_pellets()` seeds the int `0`
   (`common/defaults.py:689`) and the live grill holds `171.19809679985045`,
   accumulated from the auger rate. Typing it `int` from the defaults would
   reject every grill that has ever cooked.

2. **`log` values are a pellet id OR the literal string `"deleted"`.**
   `pellets_delete_profile` does not remove the profile's log entries; it
   rewrites each one to `"deleted"`
   (`common/pellets_actions.py:158`). So "every log value is a key of
   `archive`" is FALSE and must not be modeled as a foreign key.

3. **`brand` and `wood` are NOT constrained to `brands` and `woods`.**
   `pellets_add_profile` copies `action_data["brand_name"]`/`["wood_type"]`
   verbatim (`:115-116`). The vocabulary lists are autocomplete suggestions,
   not an enumeration, and a profile may name a brand absent from `brands`.

4. **`rating` is unvalidated and untyped at the door.** It is
   `action_data["rating"]`, passed through with no bounds check and no
   coercion, by both add and edit (`:117`, `:141`). The live value is `int 4`;
   a form-encoded client could store `"4"`. The model has to decide whether to
   coerce or reject — see 5.5.

5. **`archive[k]["id"] == k` is redundantly stored** and nothing enforces it.
   It is true on the live install and is worth a model validator, but it is an
   invariant to START enforcing, not one to assume.

### 5.4 One invariant that IS enforced, and one bug found while checking

**Enforced:** `current.pelletid` is always a key of `archive`, because
`pellets_delete_profile` refuses to delete the loaded profile
(`:152-153`). That one is safe to model as a cross-field validator.

**Bug, found while establishing the above and NOT fixed by this spec:** the
`log` dict is keyed by `str(datetime.now())[0:19]` — local time, second
resolution, no timezone. Two profile loads within the same second collide on
the dict key and **one log entry is silently lost**. Both writers do it
(`pellets_load_profile:56`, `pellets_add_profile:128`). It is unlikely by hand
and entirely reachable by a script or a test. Recorded here because modeling the
log is what surfaced it; fixing it is a separate change, and it is a shape
change, so it would want a migration and hence a version — which is a small
argument that this spec is the right order to do things in.

### 5.5 Design

A `PelletDbSchema` in a new `common/pellets_schema.py`, mirroring
`settings_schema.py`'s structure so there is one pattern in the codebase rather
than two:

- `_Section`-equivalent base with `extra="forbid"`.
- `validate_pellet_db()` — strict validate, then the same self-healing repair
  wrapper on `extra_forbidden`, logging what it stripped.
- `write_pellet_db()` calls it, exactly as `write_settings()` calls
  `validate_settings_tree()`. **This is the change that closes 5.1.**
- `PELLETDB_SCHEMA_VERSION`, its own integer, stored as
  `pelletdb["schema_version"]`. Independent of the settings version: different
  shapes, different migration histories, and coupling them would mean bumping
  one to migrate the other.
- **Its own shape digest**, on the same terms as §4 — paths, types and
  constraints, excluding defaults. Two digests and two versions, one per blob,
  because the whole point is that each shape moves on its own schedule.

A real pydantic model throughout, not a hand-rolled shape check: the repair
wrapper, the alias handling and the `extra_forbidden` retry all exist already in
`settings_schema.py` and are worth having once rather than twice.

**The writers are in scope, so the shape is allowed to be sane.** An earlier
draft of this section argued for modeling loosely *because* the writers are
loose. That reasoning is inverted — it lets the sloppiest writer define the
schema forever, and three of the five items in 5.3 are defects rather than
requirements. The correct split is by TIME, not by strictness:

| | Policy |
|---|---|
| **New writes**, through the API | Validated strictly. The writer enforces the shape at the door. |
| **Existing data**, on read or restore | Never rejected for a legacy value. Migrated or repaired into the strict shape. |

That split is exactly what a schema version is for, and it is why the pellet DB
is a better first customer of this machinery than the settings tree: it needs
two versions on day one, so the mechanism is exercised rather than merely
installed.

### 5.6 Two versions

**Version 1 — today's shape, modeled exactly.** Every existing install
validates as-is, including all five traps in §5.3. This version adds **no
constraint**; its whole job is to make an unvalidated blob a validated one, so
§5.1's restore hole closes before anything is reshaped. `rating` is `int` with
coercion, `brand`/`wood` are `str`, log values are `str`.

Modeling today's shape faithfully is the load-bearing part, and it is why §5.3
took the shape off a live grill: a v1 that is stricter than the data in the
field turns a restore into a 400 and there is no migration to blame it on.

**Version 2 — the sane shape.** Each change is a migration, and each is only
possible because the writers can change with it:

| Change | Why | Migration |
|---|---|---|
| `rating` becomes bounded `1..5` | Unvalidated and untyped at the door today; a client can store `"4"` or `99` | Coerce to int, clamp into range, log each value changed |
| Drop `archive[k]["id"]` | Redundant with its own dict key, and nothing enforces agreement | Assert `id == k`, log any mismatch, delete the field |
| `log` keyed by epoch millis | Fixes the silent-drop bug in §5.4 — **decided by the owner**, as the one migration here that rewrites operator-visible data | Reparse each `"%Y-%m-%d %H:%M:%S"` key to epoch ms **in the local zone it was written in**; on a collision, keep both by advancing the later one 1 ms |
| `log` values become `{pelletid, deleted: bool}` | Replaces the `"deleted"` in-band sentinel with a field | `"deleted"` becomes `{pelletid: null, deleted: true}`; anything else `{pelletid: v, deleted: false}` |

**`brand`/`wood` stay free text, deliberately.** This is the one item in 5.3
that is a feature: "Custom" is a first-class brand, and an operator naming a
bag the vocabulary has not heard of is the normal case, not an error. The
writer change here is the opposite of a constraint — `pellets_add_profile`
should APPEND an unseen brand or wood to the vocabulary list, which is what the
lists are visibly for and what the UI already implies.

**`est_usage` stays float**, and `default_pellets()` is corrected to seed `0.0`.
That one was never a defect in the data, only in the defaults.

#### Why v2 is the worked example for §4's digest

Each row above is a change the digest CATCHES, and each catches it for a
different reason — which is the argument for covering types and constraints
rather than paths alone:

| Row | What the digest sees | Paths-only would have |
|---|---|---|
| `rating` bounded | constraint added | **missed it** |
| `id` dropped | path removed | caught it |
| `log` rekeyed | key type changed | **missed it** — the path is still `log` |
| `log` values become objects | leaf type changed | **missed it** |

Three of four are invisible to a paths-only digest, and all three rewrite data.
That is the case for §4's ruling, stated in the concrete rather than the
abstract.

### 5.7 Writer changes that go with version 2

`common/pellets_actions.py`, which both transports already share, so each fix
lands once:

- `pellets_add_profile` / `pellets_edit_profile`: coerce and bound `rating`;
  return a 400 naming the field rather than storing a bad value. Append unseen
  `brand`/`wood` to the vocabularies.
- `pellets_add_profile`: stop writing the redundant `id` field.
- `pellets_delete_profile`: write the tombstone object instead of the
  `"deleted"` string.
- Both log writers: key by epoch millis.

**`pellets_delete_profile`'s refusal to delete the loaded profile stays.** It is
the one invariant the code already enforces (5.4) and the model will now state
it, so the guard and the schema agree rather than the schema merely hoping.

### 5.8 Tests

**Version 1:**

- **The live shape validates.** Commit a fixture captured from a real grill,
  not one hand-written from the model — a fixture written from the model only
  proves the model agrees with itself.
- Each of the five traps in 5.3 is ACCEPTED at v1: float `est_usage`, a
  `"deleted"` log value, a brand outside `brands`, an int rating, an `id`/key
  pair.
- `current.pelletid` missing from `archive` is REJECTED at both versions.
- **A backup restore of a malformed pellet file is refused with a 400** and the
  live store is unchanged. This is the 5.1 fix; it needs a test at the ROUTE,
  not only at the model.
- The repair pass strips an unmodeled key, logs it, and the write proceeds.

**Version 2, one test per migration, each asserting the v1 input and the v2
output:**

- `rating` `"4"` → `4`; `99` → `5` with a log line; `0` → `1`.
- `id` dropped, and a mismatched `id`/key logged rather than silently resolved.
- Two log entries in the same second survive as two entries 1 ms apart —
  **this is the collision bug's regression test**, and it must fail against the
  unmigrated shape.
- `"deleted"` becomes `{pelletid: null, deleted: true}` and a normal entry
  becomes `{pelletid: <id>, deleted: false}`.
- A round trip through both versions is idempotent when run twice.

**The digest gate, per §4:**

- Both digests are committed and both tests fail on a shape change. Prove it by
  adding a constraint (not just a field) in the test, since a constraint is the
  case a paths-only digest would have missed and the one §5.6 relies on.

**Negative controls (required):**

- Delete the `validate_pellet_db()` call from `write_pellet_db()`; the restore
  test must fail. Without this the restore test passes on the model alone and
  proves nothing about the route.
- Revert the log-key migration; the same-second test must fail on the COUNT of
  entries, so order the assertions to trip on the count and not on a key
  format that would fail first for the wrong reason.

## Testing — the settings tree

The pellet DB's own tests are in §5.8.

- **A tree with no `schema_version` runs every step and ends stamped CURRENT.**
  This is the case every existing install hits exactly once.
- **A tree stamped CURRENT runs NO step.** Assert by spying on the migration
  callables, not by comparing the output tree — a no-op migration would make an
  output comparison pass while the step still ran.
- **A partially-migrated tree runs only the steps above its stamp.**
- **The stamp is not written when the transaction rolls back.** Raise from
  inside a step and assert the stored tree still carries the OLD stamp; a
  version stamped ahead of the data is the one failure mode that cannot
  self-heal on the next boot.
- **`default_settings()` is stamped CURRENT**, and a fresh install runs no
  migration.
- **A future stamp runs nothing and logs**, and does not crash the boot path.
- **Idempotence:** running the chain twice is identical to running it once.
- **The shape-digest gate fires.** Add a field to a model in the test and
  assert the digest test fails. This one needs a **negative control**: the gate
  is the load-bearing part, and a gate that cannot fail is the failure it is
  meant to prevent.
- **`_migrate_i2c_buses` keeps its existing tests unchanged.** It is being
  re-gated, not rewritten; if its own tests need editing, something is wrong.

## Backward compatibility

- **Existing installs need no operator action, for either blob.** An unstamped
  settings tree is version `0`, every step runs once, and the tree is stamped.
  The steps are the ones already running today, so the first boot after this
  change does what the current code already does — then stops doing it forever,
  which is the gain.
- **A pellet database validates at v1 exactly as it stands today** (§5.6), so
  the validation gate lands before anything reshapes the data. v2's migrations
  then run once, on a blob that is already known to be well-formed. Splitting it
  that way is deliberate: a first release that both introduced validation and
  rewrote the data would leave a rejected restore ambiguous between "your backup
  is malformed" and "the migration is wrong".
- **`versions.server`/`build` keep their current meaning and their current
  consumers** (the updater, the release banner, `upgrade_settings`'s existing
  v1.4/v1.6/v1.7 cascade). This spec adds a dimension; it removes nothing.
  Retro-fitting that cascade onto `schema_version` is possible and is
  deliberately not attempted here — those blocks are release-shaped by history,
  they are covered by their own tests, and rewriting them is a separate change
  with its own risk.
- **The release version is no longer load-bearing for the i2c shape**, which is
  the specific bug that motivated all of this.

## Out of scope

- **A declared `manifest_version` on `wizard_manifest.json`.** Argued above:
  the fingerprint is the better answer for staleness. A manifest version for
  operator diagnostics is a real but different requirement.
- **`cookfile`/`recipe` archive versions.** They already exist, already gate
  (`read_cookfile` via `semantic_ver_is_lower`), and travel inside the archive
  where they belong. Moving them out of `updater_manifest.json` would be
  tidier and is not worth a migration.
- **Versioning the `control:*` blobs.** Flushed at control-process boot; a
  migration would have nothing to migrate.
- **Fixing the pellet log's key-collision bug on its own.** It is real (§5.4)
  and it is fixed here only as part of the v2 migration, because changing the
  key format without a version is precisely the move this spec exists to stop.
- **A UI that constrains brand/wood to the vocabulary lists.** §5.6 keeps them
  free text on purpose; a picker is a product decision, not a schema one.

## Decisions taken

**Both schema versions start at 1** (owner, 2026-08-02).
`SETTINGS_SCHEMA_VERSION = 1` with `_migrate_i2c_buses` as step 1, and
`PELLETDB_SCHEMA_VERSION = 1` as the as-is shape.

For settings this means every existing install — including those already
carrying the new i2c shape — runs that step exactly once. It is idempotent and
returns `False` when there is nothing to do, so it costs a scan and no write.
The alternative, starting at 2 and declaring some installs "already at 1",
requires knowing which ones are, and not knowing that is the entire problem
this spec exists to solve. Starting at 1 assumes nothing.

**The writers may change to make the shape sane** (owner, 2026-08-02). This
reversed §5.5: the model is no longer bounded by what the loosest existing
writer happens to emit. See the time-based split there — strict for new writes,
never rejecting for existing data — and the two-version plan in §5.6 that it
makes possible.

**The shape digest covers types and constraints, not just paths** (owner,
2026-08-02), and **every shape is validated with pydantic** — the pellet DB gets
a real model in `common/pellets_schema.py`, not a hand-rolled check. See §4 for
what the digest includes and, more importantly, what it excludes: defaults and
descriptions, because neither can invalidate stored data.

The cost is that widening an annotation fires the gate for a change that needs
no data rewrite. **Accepted: "if we need to support an old version and rewrite
it, that's fine."** The gate's job is to make a human look, not to decide the
answer; a pure widening bumps the version and registers a no-op step, which is
the record that the question was asked.

**The pellet log is rewritten to epoch millis** (owner, 2026-08-02). This is the
only migration here that rewrites data an operator reads, which is why it was
raised separately. It fixes the silent-drop bug in §5.4.

## Open questions

None. The design is approved as written (owner, 2026-08-02).

Two judgement calls the spec made on its own rather than asking about were put
to the owner at approval and accepted. They are recorded because an implementer
who disagrees should raise it rather than quietly diverge:

1. **§5.6 keeps `brand`/`wood` free text**, and the writer APPENDS unseen values
   to the vocabulary lists. This is a behaviour change to the pellet UI's
   autocomplete, reasoned from the writers rather than from a product decision.
   If the vocabularies are ever meant to be curated, this is the line to revisit.
2. **§2 leaves `upgrade_settings`'s v1.4/v1.6/v1.7 cascade on the release
   version** rather than porting it to `schema_version`. Those blocks are
   release-shaped by history and covered by their own tests, so porting them is
   its own change with its own risk — but it does mean two migration mechanisms
   coexist indefinitely, and a future reader will find both. Whichever one a new
   migration belongs in, it is `schema_version`: the release-gated chain is
   closed to new entries.
