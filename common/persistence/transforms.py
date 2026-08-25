"""Pure value transformations shared by persistence adapters."""

from __future__ import annotations

from collections.abc import Mapping

from common.control_delta import apply_control_delta as _apply_control_delta
from common.current_schema import CurrentSchema, build_current
from common.persistence.protocols import JsonValue

type HistorySqlRow = tuple[
    int, int | float, str, str, str, str, str | None, float | None, float | None, int | float | None
]


def _child(source: Mapping[str, JsonValue], key: str) -> Mapping[str, JsonValue]:
    value = source.get(key)
    return value if isinstance(value, Mapping) else {}


def initial_status(settings: Mapping[str, JsonValue], pellet_db: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Build the controller's persisted initial status without mutating inputs."""
    modules = _child(settings, "modules")
    globals_ = _child(settings, "globals")
    current_pellets = _child(pellet_db, "current")
    return {
        "s_plus": False,
        "hopper_level_enabled": modules.get("dist", "none") != "none",
        "hopper_level": current_pellets.get("hopper_level", 100),
        "units": globals_.get("units", "F"),
        "mode": "Stop",
        "recipe": False,
        "startup_timestamp": 0,
        "start_time": 0,
        "start_duration": 0,
        "shutdown_duration": 0,
        "prime_duration": 0,
        "prime_amount": 0,
        "lid_open_detected": False,
        "lid_open_endtime": 0,
        "p_mode": 0,
        "recipe_paused": False,
        "outpins": {"auger": False, "fan": False, "igniter": False, "power": False},
        "cycle_ratio": 0,
        "fan_duty": 0,
    }


def current_snapshot(
    previous: CurrentSchema | None,
    incoming: Mapping[str, JsonValue],
    now_ms: int,
) -> CurrentSchema:
    """Map one control-loop sample to the durable current schema."""
    return build_current(incoming, previous, now_ms)


def history_row_to_dict(row: HistorySqlRow) -> dict[str, JsonValue]:
    """Decode one SQLite history row into the established wire shape."""
    import json

    ts, psp, primary, food, aux, notify_targets, extended_data, cr, rcr, fan_duty = row
    result: dict[str, JsonValue] = {
        "T": ts,
        "P": json.loads(primary),
        "F": json.loads(food),
        "PSP": psp,
        "NT": json.loads(notify_targets),
        "AUX": json.loads(aux),
        # Duty is emitted UNCONDITIONALLY, None included -- unlike EXD below.
        # `unpack_history` builds its whole key set from row 0 of the window,
        # so a key that only appears on some rows is silently dropped for the
        # entire read whenever the first row happens to lack it. EXD lives with
        # that (it is gated on a setting that can be flipped mid-cook); duty
        # must not, because every window that begins before the v8 migration
        # would lose the series for its newer rows too.
        "CR": cr,
        "RCR": rcr,
        "FD": fan_duty,
    }
    if extended_data is not None:
        result["EXD"] = json.loads(extended_data)
    return result


def apply_control_delta(
    control: dict[str, JsonValue],
    delta: Mapping[str, JsonValue],
    log=None,
) -> dict[str, JsonValue]:
    """Apply the canonical control-delta transform through the shared entry point."""
    return _apply_control_delta(control, delta, log)
