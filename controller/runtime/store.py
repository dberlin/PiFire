"""Concrete SQLite and in-memory persistence adapters for the controller runtime."""

import copy
import logging
import threading
import time
from collections import deque
from collections.abc import Mapping

from common.common import ErrorKind, generate_uuid
from common.control_delta import (
    ControlDeltaError,
    is_control_delta,
    validate_control_delta,
)
from common.current_schema import dump_legacy, load_current, snapshot_from, zeroed_current
from common.defaults import METRIC_COLUMNS, default_control, default_metrics
from common.persistence import control as control_persistence
from common.persistence import history as history_persistence
from common.persistence import runtime as runtime_persistence
from common.persistence.protocols import PersistentQueue, QueueValue
from common.persistence.transforms import apply_control_delta, current_snapshot, initial_status
from common.sqlite_queue import SqliteQueue


def _drain(queue: PersistentQueue) -> list[QueueValue]:
    drained = []
    while queue.length() > 0:
        drained.append(queue.pop())
    return drained


class _DequeQueue:
    def __init__(self):
        self._d = deque()

    def push(self, item):
        self._d.append(item)

    def pop(self):
        return self._d.popleft() if self._d else None

    def length(self):
        return len(self._d)

    def list(self):
        return list(self._d)

    def flush(self):
        self._d.clear()

    def drain(self):
        return _drain(self)


class InMemoryStore:
    def __init__(self, control=None, settings=None, status=None, current=None, pellet_db=None, metrics=None):
        self._control = copy.deepcopy(control) if control is not None else default_control()
        self._settings = copy.deepcopy(settings) if settings is not None else {}
        self._status = copy.deepcopy(status) if status is not None else {}
        self._current = copy.deepcopy(current) if current is not None else {}
        self._pellet = copy.deepcopy(pellet_db) if pellet_db is not None else {}
        self._metrics_list = [copy.deepcopy(metrics)] if metrics is not None else []
        self._history = []
        self._errors = {}
        self._generic = {}
        self._tr = []
        self._write_queue = deque()  # pending versioned control deltas
        self._systemq = _DequeQueue()
        self._systemo = _DequeQueue()
        self._displayq = _DequeQueue()
        self._generic_lock = threading.Lock()

    def read_control(self):
        return copy.deepcopy(self._control)

    def ensure_cook_id(self, *, preferred=None):
        cook_id = self._control.get("cook_id")
        if isinstance(cook_id, str) and bool(cook_id) and cook_id == cook_id.strip():
            return cook_id
        cook_id = (
            preferred
            if isinstance(preferred, str) and bool(preferred) and preferred == preferred.strip()
            else generate_uuid()
        )
        self._control["cook_id"] = cook_id
        return cook_id

    def flush_control(self, *, cook_id=None, preserve_system_commands=False):
        # Mirror control_persistence.flush_control: reset control to defaults,
        # optionally retaining the active cook identity, and discard pending
        # writes plus completed system-command output.
        self._control = default_control()
        self._control["cook_id"] = cook_id
        self._write_queue.clear()
        if not preserve_system_commands:
            self._systemq.flush()
        self._systemo.flush()
        return copy.deepcopy(self._control)

    def write_control_snapshot(self, control, *, origin="control"):
        del origin
        self._control = copy.deepcopy(control)

    def enqueue_control_delta(self, delta, *, origin="control"):
        validate_control_delta(delta)
        payload = copy.deepcopy(dict(delta))
        payload["origin"] = origin
        self._write_queue.append(payload)

    def execute_control_writes(self):
        log = logging.getLogger("control")
        while self._write_queue:
            command = self._write_queue.popleft()
            origin = command.get("origin") if isinstance(command, Mapping) else None
            try:
                if not is_control_delta(command):
                    raise ControlDeltaError("unversioned legacy control write")
                validate_control_delta(command)
                updated = copy.deepcopy(self._control)
                apply_control_delta(updated, command)
                self._control = updated
            except (ControlDeltaError, TypeError, ValueError, KeyError, IndexError, AttributeError) as error:
                log.error(
                    "execute_control_writes: rejected queued control write id=in-memory origin=%r: %s",
                    origin,
                    error,
                )

    def read_settings(self):
        return copy.deepcopy(self._settings)

    def read_status(self):
        return copy.deepcopy(self._status)

    def init_status(self):
        # Mirror runtime_persistence.init_status: build a fresh status
        # dict, PERSIST it, and return it. The old read_status(init=True) on
        # this fake ignored the flag and returned whatever was already there,
        # so a test never saw the seeded-from-settings shape production writes.
        status = initial_status(self._settings, self._pellet)
        self.write_status(status)
        return copy.deepcopy(status)

    def write_status(self, status):
        self._status = copy.deepcopy(status)

    def read_current(self):
        return copy.deepcopy(self._current)

    def read_current_snapshot(self):
        return snapshot_from(self._current, self._probe_info)

    def _probe_info(self):
        return self._settings.get("probe_settings", {}).get("probe_map", {}).get("probe_info", [])

    def flush_current(self):
        # Mirror runtime_persistence.flush_current: rebuild a zeroed
        # structure from the configured probe_map rather than blanking in
        # place, so a probe added or removed since the last write is reflected.
        self._current = dump_legacy(zeroed_current(self._probe_info()), exclude_timestamp=True)
        return copy.deepcopy(self._current)

    def write_current(self, in_data):
        # Mirror runtime_persistence.write_current: the caller hands in
        # probe_history-shaped data, and what is STORED is the transformed
        # blob.
        previous = load_current(self._current)
        schema = current_snapshot(previous, in_data, int(time.time() * 1000))
        self._current = dump_legacy(schema)

    def read_history(self, num_items=0):
        return list(self._history)

    def flush_history(self):
        # Mirror history_persistence.flush_history: history, current and
        # metrics all go. The old read_history(flushhistory=True) on this fake
        # dropped only history, so a test could see stale current/metrics that
        # production would have cleared.
        self._history = []
        self.flush_current()
        self._control["cook_id"] = None
        self.flush_metrics()

    def write_history(self, in_data, maxsizelines=28800, ext_data=False):
        self._history.append(copy.deepcopy(in_data))
        if len(self._history) > maxsizelines:
            self._history = self._history[-maxsizelines:]

    def read_metrics(self):
        # Mirror history_persistence.read_metrics: an empty store reads
        # back as default_metrics(), not {}. The fake used to return {}, so a
        # consumer that indexes a metrics column would KeyError against the fake
        # and quietly succeed in production (or vice versa).
        return copy.deepcopy(self._metrics_list[-1]) if self._metrics_list else default_metrics()

    def read_all_metrics(self):
        return copy.deepcopy(self._metrics_list)

    def flush_metrics(self):
        self._metrics_list = []

    def append_metric(self, metrics=None):
        # Mirror history_persistence.append_metric: a fresh record starts
        # from default_metrics() when none is given, is projected onto
        # METRIC_COLUMNS (unknown keys dropped, omitted columns None -- it is an
        # INSERT, there is no prior row to inherit from), and gets a stamped
        # starttime/id. The old fake stamped neither, so it could not reproduce
        # the None-starttime rows that crashed process_metrics in production.
        if metrics is None:
            metrics = default_metrics()
        row = {k: copy.deepcopy(metrics.get(k)) for k in METRIC_COLUMNS}
        row["starttime"] = time.time() * 1000
        row["id"] = generate_uuid()
        self._metrics_list.append(row)

    def update_metrics(self, metrics):
        # Mirror history_persistence.update_metrics: presence, not
        # truthiness, decides which columns move, and an explicit {"col": None}
        # still nulls it. The old fake REPLACED the last record wholesale, so a
        # partial dict left the fake holding a one-key row while production kept
        # every unmentioned column's prior value.
        if not self._metrics_list:
            self._metrics_list.append({k: copy.deepcopy(metrics.get(k)) for k in METRIC_COLUMNS})
            return
        last = self._metrics_list[-1]
        for k in METRIC_COLUMNS:
            if k in metrics:
                last[k] = copy.deepcopy(metrics[k])

    def write_tr(self, tr):
        self._tr.append(copy.deepcopy(tr))

    def read_pellet_db(self):
        return copy.deepcopy(self._pellet)

    def write_pellet_db(self, db):
        self._pellet = copy.deepcopy(db)

    def read_errors(self, kind):
        if not isinstance(kind, ErrorKind):
            raise ValueError(f"error kind must be an ErrorKind, got {kind!r}")
        if kind is ErrorKind.ALL:
            # Grouped by owner in ErrorKind declaration order, matching the
            # SQL accessor: a process rewriting its own list must not move its
            # banners relative to the other processes'.
            return [m for k in ErrorKind if k is not ErrorKind.ALL for m in self._errors.get(k, [])]
        return list(self._errors.get(kind, []))

    def flush_errors(self, kind):
        # Mirror runtime_persistence.flush_errors: returns the NEW
        # (empty) list, not the discarded contents -- callers use it as a fresh
        # accumulator.
        self.write_errors(kind, [])
        return []

    def write_errors(self, kind, errors):
        if not isinstance(kind, ErrorKind):
            raise ValueError(f"error kind must be an ErrorKind, got {kind!r}")
        if kind is ErrorKind.ALL:
            raise ValueError(f"{kind} is a read-only selector; write and flush need a single owning kind")
        self._errors[kind] = list(errors)

    def read_generic_key(self, key):
        # Mirror runtime_persistence.read_generic_key: an absent key
        # raises TypeError (that function calls json.loads(None)), not KeyError
        # -- callers like ControllerModelStore._read_state() catch precisely
        # that to distinguish "nothing written yet" from "read failed".
        if key not in self._generic:
            raise TypeError(f"read_generic_key: no value stored for key {key!r}")
        return copy.deepcopy(self._generic[key])

    def write_generic_key(self, key, value):
        self._generic[key] = copy.deepcopy(value)

    def system_commands(self):
        return self._systemq

    def save_model_checkpoint(self, name, snapshot):
        with self._generic_lock:
            state = self._generic.get("controller_model_state")
            if state is None:
                models = {}
            elif not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("models"), dict):
                return False
            else:
                models = copy.deepcopy(state["models"])
            existing = models.get(name)
            if isinstance(existing, dict) and existing.get("revision", -1) >= snapshot["revision"]:
                return False
            models[name] = copy.deepcopy(snapshot)
            self._generic["controller_model_state"] = {"version": 1, "models": models}
            return True

    def system_output(self):
        return self._systemo

    def display_commands(self):
        return self._displayq


class _SqliteQueueAdapter:
    def __init__(self, table):
        self._q = SqliteQueue(table)

    def push(self, item):
        self._q.push(item)

    def pop(self):
        return self._q.pop()

    def length(self):
        return self._q.length()

    def list(self):
        return self._q.list()

    def flush(self):
        self._q.flush()

    def drain(self):
        return _drain(self)


class SqliteStore:
    """Queues plus direct delegation to the extracted SQLite domain modules."""

    def __init__(self):
        self._systemq = _SqliteQueueAdapter("queue_systemq")
        self._systemo = _SqliteQueueAdapter("queue_systemo")
        self._displayq = _SqliteQueueAdapter("queue_displayq")

    def read_control(self):
        return control_persistence.read_control()

    def ensure_cook_id(self, *, preferred=None):
        return control_persistence.ensure_cook_id(preferred=preferred)

    def flush_control(self, *, cook_id=None, preserve_system_commands=False):
        return control_persistence.flush_control(
            cook_id=cook_id,
            preserve_system_commands=preserve_system_commands,
        )

    def write_control_snapshot(self, control, *, origin="control"):
        control_persistence.write_control_snapshot(control, origin=origin)

    def enqueue_control_delta(self, delta, *, origin="control"):
        control_persistence.enqueue_control_delta(delta, origin=origin)

    def execute_control_writes(self):
        control_persistence.execute_control_writes()

    def read_settings(self):
        return runtime_persistence.read_settings()

    def read_status(self):
        return runtime_persistence.read_status()

    def init_status(self):
        return runtime_persistence.init_status()

    def write_status(self, status):
        runtime_persistence.write_status(status)

    def read_current(self):
        return runtime_persistence.read_current()

    def read_current_snapshot(self):
        return runtime_persistence.read_current_snapshot()

    def flush_current(self):
        return runtime_persistence.flush_current()

    def write_current(self, in_data):
        runtime_persistence.write_current(in_data)

    def read_history(self, num_items=0):
        return history_persistence.read_history(num_items)

    def flush_history(self):
        history_persistence.flush_history()

    def write_history(self, in_data, maxsizelines=28800, ext_data=False):
        history_persistence.write_history(in_data, maxsizelines=maxsizelines, ext_data=ext_data)

    def read_metrics(self):
        return history_persistence.read_metrics()

    def read_all_metrics(self):
        return history_persistence.read_all_metrics()

    def flush_metrics(self):
        history_persistence.flush_metrics()

    def append_metric(self, metrics=None):
        history_persistence.append_metric(metrics)

    def update_metrics(self, metrics):
        history_persistence.update_metrics(metrics)

    def write_tr(self, tr):
        history_persistence.write_tr(tr)

    def read_pellet_db(self):
        return runtime_persistence.read_pellet_db()

    def write_pellet_db(self, db):
        runtime_persistence.write_pellet_db(db)

    def read_errors(self, kind):
        return runtime_persistence.read_errors(kind)

    def flush_errors(self, kind):
        return runtime_persistence.flush_errors(kind)

    def write_errors(self, kind, errors):
        runtime_persistence.write_errors(kind, errors)

    def read_generic_key(self, key):
        return runtime_persistence.read_generic_key(key)

    def write_generic_key(self, key, value):
        runtime_persistence.write_generic_key(key, value)

    def system_commands(self):
        return self._systemq

    def save_model_checkpoint(self, name, snapshot):
        return runtime_persistence.write_controller_model_checkpoint(name, snapshot)

    def system_output(self):
        return self._systemo

    def display_commands(self):
        return self._displayq
