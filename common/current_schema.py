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
