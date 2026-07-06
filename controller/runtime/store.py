"""State-access seam. ValkeyStore is the ONLY production code touching common's
global Valkey funcs; InMemoryStore is the hermetic test double."""
import copy
from abc import ABC, abstractmethod
from collections import deque

from common.common import WriteKind, deep_update, default_control, default_metrics


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
	def read_control(self, flush=False): ...
	@abstractmethod
	def write_control(self, control, kind, origin='control'): ...
	@abstractmethod
	def execute_control_writes(self): ...
	# --- settings/status/current ---
	@abstractmethod
	def read_settings(self): ...
	@abstractmethod
	def read_status(self, init=False): ...
	@abstractmethod
	def write_status(self, status): ...
	@abstractmethod
	def read_current(self, zero_out=False): ...
	@abstractmethod
	def write_current(self, in_data): ...
	# --- history/metrics ---
	@abstractmethod
	def read_history(self, num_items=0, flushhistory=False): ...
	@abstractmethod
	def write_history(self, in_data, maxsizelines=28800, ext_data=False): ...
	@abstractmethod
	def read_metrics(self, all=False): ...
	@abstractmethod
	def write_metrics(self, metrics=None, flush=False, new_metric=False): ...
	@abstractmethod
	def write_tr(self, tr): ...
	# --- pellet/errors/misc ---
	@abstractmethod
	def read_pellet_db(self): ...
	@abstractmethod
	def write_pellet_db(self, db): ...
	@abstractmethod
	def read_errors(self, flush=False): ...
	@abstractmethod
	def write_errors(self, errors): ...
	@abstractmethod
	def write_generic_key(self, key, value): ...
	# --- queues ---
	@abstractmethod
	def system_commands(self): ...
	@abstractmethod
	def system_output(self): ...
	@abstractmethod
	def display_commands(self): ...


class InMemoryStore(Store):
	def __init__(self, control=None, settings=None, status=None, current=None,
				 pellet_db=None, metrics=None):
		self._control = copy.deepcopy(control) if control is not None else default_control()
		self._settings = copy.deepcopy(settings) if settings is not None else {}
		self._status = copy.deepcopy(status) if status is not None else {}
		self._current = copy.deepcopy(current) if current is not None else {}
		self._pellet = copy.deepcopy(pellet_db) if pellet_db is not None else {}
		self._metrics_list = [copy.deepcopy(metrics)] if metrics is not None else []
		self._history = []
		self._errors = []
		self._generic = {}
		self._tr = []
		self._write_queue = deque()   # pending MERGE partials
		self._systemq = _DequeQueue()
		self._systemo = _DequeQueue()
		self._displayq = _DequeQueue()

	def read_control(self, flush=False):
		if flush:
			# Mirror common.read_control(flush=True): reset control to defaults
			# and discard pending writes + system-command queues. (The Valkey
			# persistence-config toggle is a no-op for the in-memory fake.)
			self._control = default_control()
			self._write_queue.clear()
			self._systemq.flush()
			self._systemo.flush()
		return copy.deepcopy(self._control)

	def write_control(self, control, kind, origin='control'):
		if kind is WriteKind.OVERWRITE:
			self._control = copy.deepcopy(control)
		elif kind is WriteKind.MERGE:
			self._write_queue.append(copy.deepcopy(control))
		else:
			raise TypeError(f'write_control: kind must be WriteKind, got {kind!r}')

	def execute_control_writes(self):
		while self._write_queue:
			partial = self._write_queue.popleft()
			partial.pop('origin', None)
			self._control = deep_update(self._control, partial)

	def read_settings(self):
		return copy.deepcopy(self._settings)

	def read_status(self, init=False):
		return copy.deepcopy(self._status)

	def write_status(self, status):
		self._status = copy.deepcopy(status)

	def read_current(self, zero_out=False):
		return copy.deepcopy(self._current)

	def write_current(self, in_data):
		self._current = copy.deepcopy(in_data)

	def read_history(self, num_items=0, flushhistory=False):
		if flushhistory:
			self._history = []
		return list(self._history)

	def write_history(self, in_data, maxsizelines=28800, ext_data=False):
		self._history.append(copy.deepcopy(in_data))

	def read_metrics(self, all=False):
		if all:
			return list(self._metrics_list)
		return copy.deepcopy(self._metrics_list[-1]) if self._metrics_list else {}

	def write_metrics(self, metrics=None, flush=False, new_metric=False):
		if flush:
			self._metrics_list = []
		elif new_metric:
			# Mirrors common.write_metrics's default arg (metrics=default_metrics()):
			# a fresh metric record starts pre-populated with all known keys, not {}.
			self._metrics_list.append(metrics if metrics is not None else default_metrics())
		elif metrics is not None:
			if not self._metrics_list:
				self._metrics_list.append({})
			self._metrics_list[-1] = copy.deepcopy(metrics)

	def write_tr(self, tr):
		self._tr.append(copy.deepcopy(tr))

	def read_pellet_db(self):
		return copy.deepcopy(self._pellet)

	def write_pellet_db(self, db):
		self._pellet = copy.deepcopy(db)

	def read_errors(self, flush=False):
		if flush:
			self._errors = []
		return list(self._errors)

	def write_errors(self, errors):
		self._errors = list(errors)

	def write_generic_key(self, key, value):
		self._generic[key] = copy.deepcopy(value)

	def system_commands(self):
		return self._systemq

	def system_output(self):
		return self._systemo

	def display_commands(self):
		return self._displayq


from common import common as _c
from common.valkey_queue import ValkeyQueue


class _ValkeyQueueAdapter(Queue):
	def __init__(self, name):
		self._q = ValkeyQueue(name)

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


class ValkeyStore(Store):
	"""Thin pass-through to common.common — the only production code that touches
	the module-level Valkey connection."""

	def __init__(self):
		self._systemq = _ValkeyQueueAdapter('control:systemq')
		self._systemo = _ValkeyQueueAdapter('control:systemo')
		self._displayq = _ValkeyQueueAdapter('control:displayq')

	def read_control(self, flush=False):
		return _c.read_control(flush=flush)

	def write_control(self, control, kind, origin='control'):
		_c.write_control(control, kind, origin=origin)

	def execute_control_writes(self):
		_c.execute_control_writes()

	def read_settings(self):
		return _c.read_settings()

	def read_status(self, init=False):
		return _c.read_status(init=init)

	def write_status(self, status):
		_c.write_status(status)

	def read_current(self, zero_out=False):
		return _c.read_current(zero_out=zero_out)

	def write_current(self, in_data):
		_c.write_current(in_data)

	def read_history(self, num_items=0, flushhistory=False):
		return _c.read_history(num_items, flushhistory=flushhistory)

	def write_history(self, in_data, maxsizelines=28800, ext_data=False):
		_c.write_history(in_data, maxsizelines=maxsizelines, ext_data=ext_data)

	def read_metrics(self, all=False):
		return _c.read_metrics(all=all)

	def write_metrics(self, metrics=None, flush=False, new_metric=False):
		# When metrics is None, defer to common.write_metrics's own
		# default_metrics() default rather than overriding it with None
		# (passing None would crash the new_metric path on metrics['starttime']).
		if metrics is None:
			_c.write_metrics(flush=flush, new_metric=new_metric)
		else:
			_c.write_metrics(metrics=metrics, flush=flush, new_metric=new_metric)

	def write_tr(self, tr):
		_c.write_tr(tr)

	def read_pellet_db(self):
		return _c.read_pellet_db()

	def write_pellet_db(self, db):
		_c.write_pellet_db(db)

	def read_errors(self, flush=False):
		return _c.read_errors(flush=flush)

	def write_errors(self, errors):
		_c.write_errors(errors)

	def write_generic_key(self, key, value):
		_c.write_generic_key(key, value)

	def system_commands(self):
		return self._systemq

	def system_output(self):
		return self._systemo

	def display_commands(self):
		return self._displayq
