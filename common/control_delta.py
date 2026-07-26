"""
==============================================================================
 PiFire Control Deltas
==============================================================================

Description: The queued-control-write payload that states a writer's INTENT
  instead of the whole control snapshot it happened to read.

  This is a CROSS-PROCESS wire format. The web process (and the display
  process) build envelopes with control_delta(); the control process applies
  them in execute_control_writes(). Both ends are versioned, both ends are
  validated, and the wire shape is pinned by
  tests/unit/common/test_control_delta_envelope.py and
  tests/unit/datastore/test_sqlite_store_parity.py.
==============================================================================
"""

import copy
import logging
from collections.abc import Mapping

CONTROL_DELTA_KEY = "__control_delta__"
CONTROL_DELTA_VERSION = 1

#: Top-level members an envelope may carry. A strict whitelist, not a minimum:
#: a key we do not recognise means the writer and this reader disagree about
#: the format, and applying the half we understand is worse than dropping it.
_ALLOWED_MEMBERS = frozenset({CONTROL_DELTA_KEY, "origin", "set", "delete", "ops"})

#: Members that may never appear in `set`. `timer` is a coupled value object
#: (start/paused/end are one countdown and the code branches on their
#: COMBINATIONS) and `notify_data` is an array whose elements need addressing;
#: both are expressible only as ops, which is what lets the drain stop guessing.
_SET_FORBIDDEN = frozenset({"timer", "notify_data"})

_OP_FIELDS = {
    "timer.clear": (),
    "timer.pause": ("at",),
    "timer.start_or_resume": ("at", "seconds"),
    "timer.start_with_options": ("at", "seconds", "shutdown", "keep_warm"),
    "notify.set": ("label", "type", "fields"),
    "notify.delete": ("label", "type"),
    "notify.replace": ("entries",),
}
CONTROL_DELTA_OPS = frozenset(_OP_FIELDS)


class ControlDeltaError(ValueError):
    """A malformed envelope. Raised at PUSH time, in the writing process, so the
    traceback points at the writer rather than at a control-loop drain in
    another process minutes later."""


def control_delta(set_values=None, delete_paths=None, ops=None):
    """Build a validated delta envelope.

    :param set_values: members to assign (deep-merged). Presence is intent;
        absence is silence; None is a NULL VALUE, never a deletion.
    :param delete_paths: iterable of key paths to remove, e.g. [["recipe", "step_data"]].
        The only deletion channel there is.
    :param ops: ordered named operations, applied at drain time against live state.
    """
    envelope = {CONTROL_DELTA_KEY: CONTROL_DELTA_VERSION}
    if set_values:
        envelope["set"] = copy.deepcopy(dict(set_values))
    if delete_paths:
        envelope["delete"] = [list(path) for path in delete_paths]
    if ops:
        envelope["ops"] = [copy.deepcopy(dict(op)) for op in ops]
    validate_control_delta(envelope)
    return envelope


def is_control_delta(payload):
    """True when a queued payload is a delta envelope rather than a legacy partial."""
    return isinstance(payload, Mapping) and CONTROL_DELTA_KEY in payload


def validate_control_delta(envelope):
    """Raise ControlDeltaError unless `envelope` is a well-formed version-1 delta."""
    if not isinstance(envelope, Mapping):
        raise ControlDeltaError(f"delta must be a mapping, got {type(envelope).__name__}")
    unknown = sorted(set(envelope) - _ALLOWED_MEMBERS)
    if unknown:
        raise ControlDeltaError(f"unknown delta member(s): {', '.join(unknown)}")
    if envelope.get(CONTROL_DELTA_KEY) != CONTROL_DELTA_VERSION:
        raise ControlDeltaError(
            f"{CONTROL_DELTA_KEY} must be {CONTROL_DELTA_VERSION}, got {envelope.get(CONTROL_DELTA_KEY)!r}"
        )
    _validate_set(envelope.get("set"))
    _validate_delete(envelope.get("delete"))
    _validate_ops(envelope.get("ops"))


def _validate_set(set_values):
    if set_values is None:
        return
    if not isinstance(set_values, Mapping):
        raise ControlDeltaError(f"set must be a mapping, got {type(set_values).__name__}")
    forbidden = sorted(set(set_values) & _SET_FORBIDDEN)
    if forbidden:
        raise ControlDeltaError(
            f"set may not carry {', '.join(forbidden)}: use the matching timer.*/notify.* op, "
            f"which the drain evaluates against live state instead of against a stale read"
        )


def _validate_delete(delete_paths):
    if delete_paths is None:
        return
    if not isinstance(delete_paths, list):
        raise ControlDeltaError(f"delete must be a list, got {type(delete_paths).__name__}")
    for path in delete_paths:
        if not isinstance(path, list) or not path or not all(isinstance(k, str) for k in path):
            raise ControlDeltaError(f"delete path must be a non-empty list of strings, got {path!r}")


def _validate_ops(ops):
    if ops is None:
        return
    if not isinstance(ops, list):
        raise ControlDeltaError(f"ops must be a list, got {type(ops).__name__}")
    for op in ops:
        if not isinstance(op, Mapping) or "op" not in op:
            raise ControlDeltaError(f"each op must be a mapping with an 'op' key, got {op!r}")
        name = op["op"]
        if name not in _OP_FIELDS:
            raise ControlDeltaError(f"unknown op {name!r}; known: {', '.join(sorted(CONTROL_DELTA_OPS))}")
        missing = [f for f in _OP_FIELDS[name] if f not in op]
        if missing:
            raise ControlDeltaError(f"op {name!r} is missing field(s): {', '.join(missing)}")
        extra = sorted(set(op) - {"op"} - set(_OP_FIELDS[name]))
        if extra:
            raise ControlDeltaError(f"op {name!r} has unknown field(s): {', '.join(extra)}")
    _validate_op_types(ops)


def _validate_op_types(ops):
    for op in ops:
        name = op["op"]
        if name == "notify.set" and not isinstance(op["fields"], Mapping):
            raise ControlDeltaError("notify.set 'fields' must be a mapping")
        if name == "notify.replace" and not isinstance(op["entries"], list):
            raise ControlDeltaError("notify.replace 'entries' must be a list")
        if name == "timer.start_with_options" and not (
            isinstance(op["seconds"], int) and not isinstance(op["seconds"], bool) and op["seconds"] > 0
        ):
            raise ControlDeltaError("timer.start_with_options 'seconds' must be an int greater than zero")


def apply_control_delta(control, envelope, log=None):
    """Apply a delta envelope to `control` IN PLACE and return it.

    Order is `set` -> `ops` -> `delete`. `set` and `ops` have disjoint domains by
    construction (validation forbids `timer`/`notify_data` under `set`), so their
    relative order is not observable; `delete` runs last so a writer can assign
    and then remove within one envelope.

    An envelope whose version this build does not understand is DROPPED and
    logged at ERROR. It is never partially applied: the half we can parse is not
    evidence about the half we cannot. See the upgrade analysis in
    docs/superpowers/plans/2026-07-25-control-write-deltas.md.
    """
    log = log or logging.getLogger("control")
    version = envelope.get(CONTROL_DELTA_KEY)
    if version != CONTROL_DELTA_VERSION:
        log.error(
            "apply_control_delta: unsupported control delta version %r (this build understands %r); "
            "dropping the envelope from origin=%r. A newer PiFire queued this write.",
            version,
            CONTROL_DELTA_VERSION,
            envelope.get("origin"),
        )
        return control

    if "set" in envelope:
        _deep_assign(control, copy.deepcopy(envelope["set"]))
    for op in envelope.get("ops", ()):
        _apply_op(control, op, log)
    for path in envelope.get("delete", ()):
        _delete_path(control, path)
    return control


def _deep_assign(target, values):
    """deep_update without importing common.common (which imports this module's
    siblings). Mapping values recurse; everything else assigns."""
    for key, value in values.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), Mapping):
            _deep_assign(target[key], value)
        else:
            target[key] = value
    return target


def _delete_path(target, path):
    node = target
    for key in path[:-1]:
        node = node.get(key) if isinstance(node, Mapping) else None
        if not isinstance(node, Mapping):
            return
    if isinstance(node, Mapping):
        node.pop(path[-1], None)


def _apply_op(control, op, log):
    _OP_APPLIERS[op["op"]](control, op, log)


#: op name -> applier. Populated below as each op family is defined.
_OP_APPLIERS = {}
