"""Pydantic model for the pellet database blob (`pellets:general`).

Mirrors common/settings_schema.py deliberately -- one pattern in this codebase
rather than two. Unknown keys are REJECTED (`extra="forbid"` on
`_PelletSection`), and `validate_pellet_db()` wraps that strictness in the same
self-healing repair pass: when every failure is an unmodeled key, those keys
are stripped from a copy, each stripped path is logged, and validation is
retried once. Any other error still raises, so repair never masks a real bug.

The shape is taken from a LIVE install (tests/fixtures/pelletdb_live.json), not
from `default_pellets()`, because the two disagree: the defaults seed
`est_usage` as an int and the field holds a float accumulated from the auger
rate.

Strictness is split by TIME rather than by field. A new write is validated
against the model here, and data an install already holds is never rejected
for a legacy value -- `_PELLET_MIGRATIONS` carries it into the current shape
first. That split is what lets the model be stricter than the loosest writer
that ever ran.
"""

import copy
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator
from pydantic_core import ErrorDetails

from common.common import write_log


class _PelletSection(BaseModel):
    model_config = ConfigDict(extra="forbid")


#: A log key is the load time in epoch milliseconds, as a decimal string --
#: JSON object keys are strings, and the pattern is what keeps a
#: second-resolution timestamp from being stored under a key that says
#: milliseconds.
_EpochMsKey = Annotated[str, StringConstraints(pattern=r"^\d+$")]


class PelletLogEntry(_PelletSection):
    # A removed profile leaves a tombstone rather than an in-band "deleted"
    # string, so a log value has one type whatever happened to it.
    pelletid: str | None
    deleted: bool


class PelletProfile(_PelletSection):
    brand: str
    wood: str
    rating: int = Field(ge=1, le=5)
    comments: str


class PelletCurrent(_PelletSection):
    pelletid: str
    # An int percentage: every distance driver's get_level() returns one
    # (distance/_sampled_base.py takes int() of the computed level).
    hopper_level: int
    date_loaded: str
    # Grams since the last load, accumulated from the auger rate.
    est_usage: float


class PelletLastUpdated(_PelletSection):
    time: int


#: The shape of the pellet database, independent of both the release version
#: and the settings tree's shape version. Different shapes, different migration
#: histories: coupling them would mean bumping one to migrate the other.
PELLETDB_SCHEMA_VERSION = 2


class PelletDbSchema(_PelletSection):
    schema_version: int = PELLETDB_SCHEMA_VERSION
    current: PelletCurrent
    archive: dict[str, PelletProfile]
    # Keyed by load time in epoch milliseconds. A value names the profile
    # loaded, or is a tombstone -- pellets_delete_profile rewrites rather than
    # removes, so a log entry outlives the profile it points at.
    log: dict[_EpochMsKey, PelletLogEntry]
    # Autocomplete vocabularies, not enumerations: a profile may name a brand
    # or wood that is absent from these.
    brands: list[str]
    woods: list[str]
    lastupdated: PelletLastUpdated

    @model_validator(mode="after")
    def _loaded_profile_is_archived(self) -> "PelletDbSchema":
        # pellets_delete_profile refuses to delete the loaded profile, so this
        # holds in the store; stating it here makes the guard and the schema
        # agree rather than the schema merely hoping.
        if self.current.pelletid not in self.archive:
            raise ValueError("current.pelletid must be a key of archive")
        return self


def _migrate_pellets_to_v2(pelletdb: dict) -> bool:
    """Carry a version 1 database to version 2. Idempotent.

    Four changes: `rating` is coerced and clamped into 1..5, the redundant
    per-profile `id` is dropped, log keys become epoch milliseconds, and log
    values become objects rather than an id-or-"deleted" string.
    """
    changed = False

    for key, profile in (pelletdb.get("archive") or {}).items():
        if not isinstance(profile, dict):
            continue
        if "id" in profile:
            stored_id = profile.pop("id")
            if stored_id != key:
                write_log(f"pelletdb: archive['{key}'] carried id '{stored_id}'; the key wins")
            changed = True
        raw = profile.get("rating")
        try:
            rating = int(raw)
        except TypeError, ValueError:
            rating = 1
        rating = max(1, min(5, rating))
        if rating != raw:
            write_log(f"pelletdb: archive['{key}'].rating {raw!r} -> {rating}")
            profile["rating"] = rating
            changed = True

    log = pelletdb.get("log")
    if isinstance(log, dict) and any(not str(key).isdigit() for key in log):
        migrated: dict[str, object] = {}
        # Sorted, because "%Y-%m-%d %H:%M:%S" sorts chronologically as text and
        # the collision rule below has to advance the LATER of two entries.
        # strptime yields a naive datetime and .timestamp() reads it in the
        # system's local zone -- the zone str(datetime.now()) wrote it in.
        for key in sorted(log):
            try:
                stamp = int(datetime.strptime(key, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
            except TypeError, ValueError:
                write_log(f"pelletdb: dropped log entry with unreadable timestamp '{key}'")
                continue
            while str(stamp) in migrated:
                stamp += 1
            migrated[str(stamp)] = log[key]
        pelletdb["log"] = migrated
        changed = True

    for key, value in list((pelletdb.get("log") or {}).items()):
        if isinstance(value, str):
            pelletdb["log"][key] = (
                {"pelletid": None, "deleted": True} if value == "deleted" else {"pelletid": value, "deleted": False}
            )
            changed = True

    return changed


#: The shape migrations, in ascending order, as (target_version, migration).
#: A step's number is the version the database is AT once it has run; each
#: callable mutates in place and returns True if it changed anything.
_PELLET_MIGRATIONS = [
    (2, _migrate_pellets_to_v2),
]


class PelletDbValidationError(ValueError):
    """A pellet database failed strict schema validation."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _format_errors(errs: list[ErrorDetails]) -> list[str]:
    return [f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in errs]


def _strip_error_locs(db: dict, errors: list[ErrorDetails]) -> None:
    """Delete each error's `loc` path from `db` in place. Caller passes a COPY."""
    for err in errors:
        loc = err["loc"]
        cur = db
        for part in loc[:-1]:
            cur = cur.get(part) if isinstance(cur, dict) else None
            if not isinstance(cur, dict):
                break
        if isinstance(cur, dict):
            cur.pop(loc[-1], None)


def validate_pellet_db(pelletdb: dict) -> dict:
    """Strict-validate a pellet database; return the normalized dump.

    The single enforcement entry -- write_pellet_db() calls it before
    persisting. Raises PelletDbValidationError with dotted-path messages.
    """
    try:
        model = PelletDbSchema.model_validate(pelletdb, strict=True)
    except ValidationError as exc:
        errors = exc.errors()
        if not errors or any(err["type"] != "extra_forbidden" for err in errors):
            raise PelletDbValidationError(_format_errors(errors)) from exc

        repaired = copy.deepcopy(pelletdb)
        _strip_error_locs(repaired, errors)
        try:
            model = PelletDbSchema.model_validate(repaired, strict=True)
        except ValidationError as retry_exc:
            raise PelletDbValidationError(_format_errors(retry_exc.errors())) from retry_exc

        for err in errors:
            dotted = ".".join(str(part) for part in err["loc"])
            write_log(f"pelletdb: stripped unmodeled key '{dotted}' during write-time repair (was: {err['msg']})")
    return model.model_dump(mode="json")
