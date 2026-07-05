"""State-access seam. ValkeyStore is the ONLY production code touching common's
global Valkey funcs; InMemoryStore is the hermetic test double."""
import copy
from abc import ABC, abstractmethod
from collections import deque

from common.common import WriteKind, deep_update, default_control


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
	def write_history(self, in_data, ext_data=False): ...
	@abstractmethod
	def read_metrics(self, all=False): ...
	@abstractmethod
	def write_metrics(self, metrics=None, new_metric=False, flush=False): ...
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

	def read_control(self):
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

	def write_history(self, in_data, ext_data=False):
		self._history.append(copy.deepcopy(in_data))

	def read_metrics(self, all=False):
		if all:
			return list(self._metrics_list)
		return copy.deepcopy(self._metrics_list[-1]) if self._metrics_list else {}

	def write_metrics(self, metrics=None, new_metric=False, flush=False):
		if flush:
			self._metrics_list = []
		elif new_metric:
			self._metrics_list.append({})
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
