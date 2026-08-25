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

from pydantic import ValidationError
from pydantic_core import ErrorDetails

from common.common import write_log
from common.web_contracts import control as control_contracts


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


def _migrate_pellets_to_v3(pelletdb: dict) -> bool:
    """Record finite usage enforcement; valid JSON cannot store non-finite numbers."""
    return False


#: The shape migrations, in ascending order, as (target_version, migration).
#: A step's number is the version the database is AT once it has run; each
#: callable mutates in place and returns True if it changed anything.
_PELLET_MIGRATIONS = [
    (2, _migrate_pellets_to_v2),
    (3, _migrate_pellets_to_v3),
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
        model = control_contracts.PelletDbSchema.model_validate(pelletdb, strict=True)
    except ValidationError as exc:
        errors = exc.errors()
        if not errors or any(err["type"] != "extra_forbidden" for err in errors):
            raise PelletDbValidationError(_format_errors(errors)) from exc

        repaired = copy.deepcopy(pelletdb)
        _strip_error_locs(repaired, errors)
        try:
            model = control_contracts.PelletDbSchema.model_validate(repaired, strict=True)
        except ValidationError as retry_exc:
            raise PelletDbValidationError(_format_errors(retry_exc.errors())) from retry_exc

        for err in errors:
            dotted = ".".join(str(part) for part in err["loc"])
            write_log(f"pelletdb: stripped unmodeled key '{dotted}' during write-time repair (was: {err['msg']})")
    return model.model_dump(mode="json")
