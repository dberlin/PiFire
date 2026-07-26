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


#: The two keys a CLIENT-POSTED control patch may carry notify intent under.
#: They are not equivalent:
#:
#:   * `notify_data` is the WHOLE array. An entry it omits is a deletion rather
#:     than silence, so it can only be applied as a replace -- and a client that
#:     built it from a read the write queue is invisible to therefore reverts
#:     whatever another writer changed in the same control cycle. Kept for
#:     clients that already speak it; nothing in this repo posts it any more.
#:   * `notify_updates` states one patch per ADDRESSED entry, so two writers
#:     touching different entries (or different fields of one entry) both
#:     survive the drain. This is what in-repo clients post.
NOTIFY_POST_KEYS = ("notify_data", "notify_updates")


def notify_ops_from_post(payload):
    """Split a client-posted control patch into (plain_members, ops).

    `payload` is not mutated. Everything that is not a notify key comes back in
    `plain_members` for `control_delta(set_values=...)`; the notify keys become
    ops the drain evaluates against live state.

    A payload carrying both keys applies `notify_data` first, so the addressed
    `notify_updates` patches win. Raises ControlDeltaError on a malformed
    payload -- at request time, in the web process, rather than at a drain in
    the control process one cycle later.
    """
    members = dict(payload)
    entries = members.pop("notify_data", None)
    updates = members.pop("notify_updates", None)
    ops = []
    if entries is not None:
        ops.append({"op": "notify.replace", "entries": entries})
    if updates is not None:
        if not isinstance(updates, list):
            raise ControlDeltaError(f"notify_updates must be a list, got {type(updates).__name__}")
        for update in updates:
            if not isinstance(update, Mapping):
                raise ControlDeltaError(f"each notify_updates item must be a mapping, got {update!r}")
            # 1:1 with notify.set, so its validation -- required label/type/
            # fields, no unknown members, fields must be a mapping -- is the
            # only validation there is. No second copy to drift from it.
            ops.append({"op": "notify.set", **update})
    return members, (ops or None)


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
    log.debug("apply_control_delta: applying %s", op["op"])
    _OP_APPLIERS[op["op"]](control, op, log)


def _notify_index(control, label, type_):
    for index, entry in enumerate(control.get("notify_data", ())):
        if isinstance(entry, Mapping) and entry.get("label") == label and entry.get("type") == type_:
            return index
    return None


def _op_notify_set(control, op, log):
    index = _notify_index(control, op["label"], op["type"])
    if index is None:
        control.setdefault("notify_data", []).append(
            {"label": op["label"], "type": op["type"], **copy.deepcopy(dict(op["fields"]))}
        )
        return
    control["notify_data"][index].update(copy.deepcopy(dict(op["fields"])))


def _op_notify_delete(control, op, log):
    index = _notify_index(control, op["label"], op["type"])
    if index is not None:
        del control["notify_data"][index]


def _op_notify_replace(control, op, log):
    control["notify_data"] = copy.deepcopy(list(op["entries"]))


def _timer_notify_index(control):
    """_cmd_set_timer locates the timer entry by TYPE alone
    (common/api_commands.py:649-651), not by label. Match that."""
    for index, entry in enumerate(control.get("notify_data", ())):
        if isinstance(entry, Mapping) and entry.get("type") == "timer":
            return index
    return None


def _op_timer_clear(control, op, log):
    control["timer"]["start"] = 0
    control["timer"]["end"] = 0
    control["timer"]["paused"] = 0
    index = _timer_notify_index(control)
    if index is not None:
        entry = control["notify_data"][index]
        entry["req"] = False
        entry["shutdown"] = False
        entry["keep_warm"] = False


def _op_timer_pause(control, op, log):
    if control["timer"]["start"] == 0:
        # _cmd_set_timer's own start == 0 branch is a full clear, not a pause.
        _op_timer_clear(control, op, log)
        return
    index = _timer_notify_index(control)
    if index is not None:
        control["notify_data"][index]["req"] = False
    control["timer"]["paused"] = op["at"]


def _op_timer_start_or_resume(control, op, log):
    index = _timer_notify_index(control)
    if index is not None:
        # Set BEFORE the branch, matching common/api_commands.py:665.
        control["notify_data"][index]["req"] = True
    if control["timer"]["paused"] == 0:
        seconds = op["seconds"] if op["seconds"] is not None else 60
        control["timer"]["start"] = op["at"]
        control["timer"]["end"] = op["at"] + seconds
    else:
        control["timer"]["end"] = (control["timer"]["end"] - control["timer"]["paused"]) + op["at"]
        control["timer"]["paused"] = 0


def _op_timer_start_with_options(control, op, log):
    if control["timer"]["paused"] != 0:
        log.error(
            "apply_control_delta: dropping timer.start_with_options -- the timer is paused at drain time. "
            "The 4-argument REST form rejects a paused timer at request time, so another writer paused it "
            "inside this control cycle. Resume or stop it first."
        )
        return
    index = _timer_notify_index(control)
    if index is not None:
        entry = control["notify_data"][index]
        entry["req"] = True
        entry["shutdown"] = op["shutdown"]
        entry["keep_warm"] = op["keep_warm"]
    control["timer"]["start"] = op["at"]
    control["timer"]["end"] = op["at"] + op["seconds"]


#: op name -> applier. Every name here must also appear in _OP_FIELDS, which is
#: what the validator checks against, so an op can never be pushed that the
#: drain cannot apply.
_OP_APPLIERS = {
    "timer.clear": _op_timer_clear,
    "timer.pause": _op_timer_pause,
    "timer.start_or_resume": _op_timer_start_or_resume,
    "timer.start_with_options": _op_timer_start_with_options,
    "notify.set": _op_notify_set,
    "notify.delete": _op_notify_delete,
    "notify.replace": _op_notify_replace,
}
