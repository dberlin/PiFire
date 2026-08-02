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
rate. Nothing here constrains a value beyond its type -- the writers in
common/pellets_actions.py do not, and a model stricter than the data in the
field turns a restore into a rejection.
"""

import copy

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator
from pydantic_core import ErrorDetails

from common.common import write_log


class _PelletSection(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PelletProfile(_PelletSection):
    # `id` repeats its own archive key. Nothing enforces the agreement.
    id: str
    brand: str
    wood: str
    rating: int
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
PELLETDB_SCHEMA_VERSION = 1


class PelletDbSchema(_PelletSection):
    schema_version: int = PELLETDB_SCHEMA_VERSION
    current: PelletCurrent
    archive: dict[str, PelletProfile]
    # Keyed by load time, valued by profile id or the literal "deleted" --
    # pellets_delete_profile rewrites rather than removes, so a log value is
    # not a key of `archive`.
    log: dict[str, str]
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


#: The shape migrations, in ascending order, as (target_version, migration).
#: A step's number is the version the database is AT once it has run; each
#: callable mutates in place and returns True if it changed anything. Empty at
#: version 1, which is today's shape modeled exactly.
_PELLET_MIGRATIONS: list = []


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
