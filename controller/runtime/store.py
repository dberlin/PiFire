"""State-access seam. SqliteStore is the ONLY production code touching common's
global SQLite-backed funcs; InMemoryStore is the hermetic test double."""

import copy
import logging
import time
import threading
from collections.abc import Mapping
from abc import ABC, abstractmethod
from collections import deque

from common.common import ErrorKind, generate_uuid
from common.control_delta import (
    ControlDeltaError,
    is_control_delta,
    validate_control_delta,
)
from common.current_schema import dump_legacy, load_current, snapshot_from, zeroed_current
from common.defaults import METRIC_COLUMNS, default_control, default_metrics
from common.persistence.transforms import apply_control_delta, current_snapshot, initial_status


class Queue(ABC):
    @abstractmethod
    def push(self, item): ...
    @abstractmethod
    def pop(self): ...
    @abstractmethod
    def length(self): ...
    @abstractmethod
    def list(self): ...
    @abstractmethod
    def flush(self): ...

    def drain(self):
        out = []
        while self.length() > 0:
            out.append(self.pop())
        return out


class _DequeQueue(Queue):
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


class Store(ABC):
    # --- control ---
    @abstractmethod
    def read_control(self): ...
    @abstractmethod
    def flush_control(self): ...
    @abstractmethod
    def write_control_snapshot(self, control, *, origin="control"): ...
    @abstractmethod
    def enqueue_control_delta(self, delta, *, origin="control"): ...
    @abstractmethod
    def execute_control_writes(self): ...
    # --- settings/status/current ---
    @abstractmethod
    def read_settings(self): ...
    @abstractmethod
    def read_status(self): ...
    @abstractmethod
    def init_status(self): ...
    @abstractmethod
    def write_status(self, status): ...
    @abstractmethod
    def read_current(self): ...
    @abstractmethod
    def read_current_snapshot(self): ...
    @abstractmethod
    def flush_current(self): ...
    @abstractmethod
    def write_current(self, in_data): ...
    # --- history/metrics ---
    @abstractmethod
    def read_history(self, num_items=0): ...
    @abstractmethod
    def flush_history(self): ...
    @abstractmethod
    def write_history(self, in_data, maxsizelines=28800, ext_data=False): ...
    @abstractmethod
    def read_metrics(self): ...
    @abstractmethod
    def read_all_metrics(self): ...
    @abstractmethod
    def flush_metrics(self): ...
    @abstractmethod
    def append_metric(self, metrics=None): ...
    @abstractmethod
    def update_metrics(self, metrics): ...
    @abstractmethod
    def write_tr(self, tr): ...
    # --- pellet/errors/misc ---
    @abstractmethod
    def read_pellet_db(self): ...
    @abstractmethod
    def write_pellet_db(self, db): ...
    @abstractmethod
    def read_errors(self, kind): ...
    @abstractmethod
    def flush_errors(self, kind): ...
    @abstractmethod
    def write_errors(self, kind, errors): ...
    @abstractmethod
    def read_generic_key(self, key): ...
    @abstractmethod
    def write_generic_key(self, key, value): ...
    @abstractmethod
    def save_model_checkpoint(self, name, snapshot): ...
    # --- queues ---
    @abstractmethod
    def system_commands(self): ...
    @abstractmethod
    def system_output(self): ...
    @abstractmethod
    def display_commands(self): ...


class InMemoryStore(Store):
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

    def flush_control(self):
        # Mirror common.datastore_accessors.flush_control: reset control to
        # defaults and discard pending writes + system-command queues. (The
        # datastore persistence-config toggle is a no-op for the in-memory fake.)
        self._control = default_control()
        self._write_queue.clear()
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
        # Mirror common.datastore_accessors.init_status: build a fresh status
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
        schema = current_snapshot(previous, in_data, int(time.time() * 1000))
        self._current = dump_legacy(schema)

    def read_history(self, num_items=0):
        return list(self._history)

    def flush_history(self):
        # Mirror common.datastore_accessors.flush_history: history, current and
        # metrics all go. The old read_history(flushhistory=True) on this fake
        # dropped only history, so a test could see stale current/metrics that
        # production would have cleared.
        self._history = []
        self.flush_current()
        self.flush_metrics()

    def write_history(self, in_data, maxsizelines=28800, ext_data=False):
        self._history.append(copy.deepcopy(in_data))
        if len(self._history) > maxsizelines:
            self._history = self._history[-maxsizelines:]

    def read_metrics(self):
        # Mirror common.datastore_accessors.read_metrics: an empty store reads
        # back as default_metrics(), not {}. The fake used to return {}, so a
        # consumer that indexes a metrics column would KeyError against the fake
        # and quietly succeed in production (or vice versa).
        return copy.deepcopy(self._metrics_list[-1]) if self._metrics_list else default_metrics()

    def read_all_metrics(self):
        return copy.deepcopy(self._metrics_list)

    def flush_metrics(self):
        self._metrics_list = []

    def append_metric(self, metrics=None):
        # Mirror common.datastore_accessors.append_metric: a fresh record starts
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
        # Mirror common.datastore_accessors.update_metrics: presence, not
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
        # Mirror common.datastore_accessors.flush_errors: returns the NEW
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
        # Mirrors common.datastore_accessors.read_generic_key: an absent key
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


from common import datastore_accessors as _c
from common.sqlite_queue import SqliteQueue


class _SqliteQueueAdapter(Queue):
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


class SqliteStore(Store):
    """Thin pass-through to common.common — the only production code that touches
    the module-level SQLite connection."""

    def __init__(self):
        self._systemq = _SqliteQueueAdapter("queue_systemq")
        self._systemo = _SqliteQueueAdapter("queue_systemo")
        self._displayq = _SqliteQueueAdapter("queue_displayq")

    def read_control(self):
        return _c.read_control()

    def flush_control(self):
        return _c.flush_control()

    def write_control_snapshot(self, control, *, origin="control"):
        _c.write_control_snapshot(control, origin=origin)

    def enqueue_control_delta(self, delta, *, origin="control"):
        _c.enqueue_control_delta(delta, origin=origin)

    def execute_control_writes(self):
        _c.execute_control_writes()

    def read_settings(self):
        return _c.read_settings()

    def read_status(self):
        return _c.read_status()

    def init_status(self):
        return _c.init_status()

    def write_status(self, status):
        _c.write_status(status)

    def read_current(self):
        return _c.read_current()

    def read_current_snapshot(self):
        return _c.read_current_snapshot()

    def flush_current(self):
        return _c.flush_current()

    def write_current(self, in_data):
        _c.write_current(in_data)

    def read_history(self, num_items=0):
        return _c.read_history(num_items)

    def flush_history(self):
        _c.flush_history()

    def write_history(self, in_data, maxsizelines=28800, ext_data=False):
        _c.write_history(in_data, maxsizelines=maxsizelines, ext_data=ext_data)

    def read_metrics(self):
        return _c.read_metrics()

    def read_all_metrics(self):
        return _c.read_all_metrics()

    def flush_metrics(self):
        _c.flush_metrics()

    def append_metric(self, metrics=None):
        # metrics=None is passed straight through: append_metric() supplies its
        # own default_metrics() (handing it None would crash on metrics['starttime']).
        _c.append_metric(metrics)

    def update_metrics(self, metrics):
        _c.update_metrics(metrics)

    def write_tr(self, tr):
        _c.write_tr(tr)

    def read_pellet_db(self):
        return _c.read_pellet_db()

    def write_pellet_db(self, db):
        _c.write_pellet_db(db)

    def read_errors(self, kind):
        return _c.read_errors(kind)

    def flush_errors(self, kind):
        return _c.flush_errors(kind)

    def write_errors(self, kind, errors):
        _c.write_errors(kind, errors)

    def read_generic_key(self, key):
        return _c.read_generic_key(key)

    def write_generic_key(self, key, value):
        _c.write_generic_key(key, value)

    def system_commands(self):
        return self._systemq

    def save_model_checkpoint(self, name, snapshot):
        return _c.write_controller_model_checkpoint(name, snapshot)

    def system_output(self):
        return self._systemo

    def display_commands(self):
        return self._displayq
