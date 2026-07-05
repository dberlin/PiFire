# Controller / Display Separation & Controller Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the PiFire control process and display into two independently-runnable processes communicating only through Valkey, and decompose the 870-line `_work_cycle` into a testable, behavior-preserving mode-handler state machine.

**Architecture:** A `Store` seam quarantines all Valkey access behind an interface (real `ValkeyStore` + `InMemoryStore` for tests). Hardware and state are passed to a `Controller` orchestrator and per-mode `ControlMode` handlers via an injected `ControllerContext`. Pure arithmetic/decision logic moves to side-effect-free modules. The display becomes a separate `display.py` process fed by a `control:displayq` Valkey queue plus the existing status/current keys.

**Tech Stack:** Python 3, pytest, Valkey (valkey-py), supervisord. No new runtime dependencies.

**Design spec:** `docs/superpowers/specs/2026-07-05-controller-display-separation-design.md`

## Global Constraints

- **Behavior-preserving is a hard requirement.** No mode's runtime behavior may change. Every decomposition task must keep the golden-master characterization tests (Phase 3) green with identical assertions.
- **Green suite at every commit.** `python -m pytest tests/ -q` (excluding pre-existing optional-dep collection errors: numpy/casadi/PyQt/hardware modules) must pass before each commit.
- **Self-contained commits.** Multiple concurrent Claude sessions commit clean commits to this branch. Keep each commit self-contained; scope any code review to the exact commit SHA(s) this plan produces, never the branch diff.
- **`WriteKind` argument is required** — no default. A missed call site must fail loudly (`TypeError`), never silently.
- **Persisted settings keys are immutable here** — do not rename `FanPidEnabled` or any `settings['controller']['config']` / on-disk key. Only rename in-memory local variables.
- **No new runtime dependencies.** Tests use only pytest + stdlib. Do not import numpy/casadi/PyQt in any new test.
- **Follow existing style** — tabs for indentation (matching `control.py`/`common.py`), `snake_case`, module docstring headers as in existing files.

---

## File Structure

**New files:**
- `controller/runtime/__init__.py` — package marker
- `controller/runtime/store.py` — `Store` ABC, `ValkeyStore`, `InMemoryStore`
- `controller/runtime/clock.py` — `Clock`, `RealClock`, `ManualClock`
- `controller/runtime/context.py` — `ControllerContext`, `Devices`
- `controller/runtime/notifier.py` — `Notifier`, `ValkeyNotifier`
- `controller/runtime/devices.py` — `build_devices()` factory
- `controller/runtime/runner.py` — `ControllerRunner`, `SyncControllerRunner`, `NormalizedOutput`
- `controller/runtime/state.py` — `WorkCycleState` dataclass
- `controller/runtime/logic/__init__.py`
- `controller/runtime/logic/cycle.py`, `smartstart.py`, `pwm.py`, `safety.py`, `fan.py`
- `controller/runtime/modes/__init__.py`
- `controller/runtime/modes/base.py` — `ControlMode` template
- `controller/runtime/modes/{startup,smoke,hold,shutdown,prime,monitor,manual,reignite}.py`
- `controller/runtime/controller.py` — `Controller` orchestrator + `RecipeMode`
- `display.py` — display process entry point + `DisplayFeeder`
- `auto-install/supervisor/display.conf`
- Test fakes: `tests/fakes/__init__.py`, `tests/fakes/grill.py`, `tests/fakes/probes.py`, `tests/fakes/distance.py`, `tests/fakes/notifier.py`, `tests/fakes/runner.py`
- Test files under `tests/` per task.

**Modified files:**
- `common/common.py` — add `WriteKind`, change `write_control` signature, update internal callers
- All ~156 `write_control` call sites (displays, blueprints, notify, common, control)
- `control.py` — reduced to a slim entry point by the end

---

## Phase 1 — `WriteKind` enum + global sweep

Design ref: spec §"The `WriteKind` enum". This phase is pure enabling; behavior identical.

### Task 1.1: Add `WriteKind` and change `write_control`

**Files:**
- Modify: `common/common.py` (`write_control` at 889-903, imports near top)
- Test: `tests/test_write_kind.py`

**Interfaces:**
- Produces: `common.WriteKind` (Enum with `OVERWRITE`, `MERGE`); `common.write_control(control: dict, kind: WriteKind, origin: str = 'unknown') -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_write_kind.py
import pytest
from enum import Enum


def test_write_kind_is_enum_with_two_members():
    from common.common import WriteKind
    assert issubclass(WriteKind, Enum)
    assert {m.name for m in WriteKind} == {'OVERWRITE', 'MERGE'}


def test_write_control_requires_kind():
    # kind is positional & required: calling without it raises TypeError
    from common.common import write_control
    with pytest.raises(TypeError):
        write_control({'mode': 'Stop'})


def test_write_control_rejects_non_writekind():
    from common.common import write_control
    with pytest.raises(TypeError):
        write_control({'mode': 'Stop'}, True)  # legacy boolean no longer accepted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_write_kind.py -v`
Expected: FAIL — `ImportError: cannot import name 'WriteKind'`

- [ ] **Step 3: Implement**

Add near the top of `common/common.py` (add `from enum import Enum` if absent):

```python
from enum import Enum


class WriteKind(Enum):
    OVERWRITE = 'overwrite'   # replace control:general wholesale (was direct_write=True)
    MERGE = 'merge'           # queue a partial change, deep-merged on execute (was direct_write=False)
```

Replace `write_control` (currently 889-903):

```python
def write_control(control, kind, origin='unknown'):
    """
    Write control to Valkey DB.

    :param control: Control Dictionary
    :param kind: WriteKind.OVERWRITE writes control:general directly.
                 WriteKind.MERGE queues a partial change for deep-merge on execute.
    :param origin: Source label recorded on merge writes.
    """
    global cmdsts

    if kind is WriteKind.OVERWRITE:
        cmdsts.set('control:general', json.dumps(control))
    elif kind is WriteKind.MERGE:
        control['origin'] = origin
        cmdsts.rpush('control:write', json.dumps(control))
    else:
        raise TypeError(f'write_control: kind must be WriteKind, got {kind!r}')
```

Update the two internal callers in `common/common.py`:
- `read_control(flush=True)` (~882): `write_control(control, direct_write=True, origin='common')` → `write_control(control, WriteKind.OVERWRITE, origin='common')`
- `execute_control_writes()` (~922): `write_control(control, direct_write=True, origin='writer')` → `write_control(control, WriteKind.OVERWRITE, origin='writer')`

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_write_kind.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add common/common.py tests/test_write_kind.py
git commit -m "feat(control): add required WriteKind enum to write_control"
```

### Task 1.2: Convert all remaining `write_control` call sites

**Files:**
- Modify (all `write_control(` callers except `common/common.py`, already done): `control.py`, `display/base_flex.py`, `display/base_240x320.py`, `display/base_240x240.py`, `display/base_320x480.py`, `display/ssd1306b.py`, `display/qtquick_flex.py`, `blueprints/settings/routes.py`, `blueprints/api/routes.py`, `blueprints/tuner/routes.py`, `blueprints/admin/routes.py`, `blueprints/pellets/routes.py`, `blueprints/mobile/socket_io.py`, `notify/mqtt_handler.py`, `notify/notifications.py`, `common/process_mon.py`

**Interfaces:**
- Consumes: `common.WriteKind`

- [ ] **Step 1: Enumerate every call site**

Run:
```bash
grep -rn "write_control(" --include='*.py' . | grep -v __pycache__ | grep -v '/tests/' | grep -v 'def write_control'
```
Expected: ~155 lines across the files above.

- [ ] **Step 2: Convert mechanically, per rule**

For each call site:
- `write_control(X, direct_write=True, origin=Y)` → `write_control(X, WriteKind.OVERWRITE, origin=Y)`
- `write_control(X, direct_write=True)` → `write_control(X, WriteKind.OVERWRITE)`
- `write_control(X, origin=Y)` (no `direct_write`) → `write_control(X, WriteKind.MERGE, origin=Y)`
- `write_control(X)` (no args) → `write_control(X, WriteKind.MERGE)`

Ensure each modified file imports `WriteKind`. Files doing `from common import *` (e.g. `control.py`) get it transitively — verify `WriteKind` is exported: if `common/__init__.py` re-exports names or uses `__all__`, add `'WriteKind'`. For files importing specific names (e.g. `from common.common import write_control`), add `WriteKind` to that import.

Verify none missed:
```bash
grep -rn "direct_write" --include='*.py' . | grep -v __pycache__
```
Expected: no matches.

- [ ] **Step 3: Verify the app imports and the suite still collects**

Run: `python -c "import control"` (must not error on `write_control`), then
`python -m pytest tests/ -q -p no:cacheprovider 2>&1 | tail -5`
Expected: same pass/error counts as before this plan (the 13 optional-dep collection errors are pre-existing and unrelated).

- [ ] **Step 4: Grep for accidental boolean leftovers**

Run: `grep -rn "WriteKind" --include='*.py' . | grep -v __pycache__ | wc -l`
Expected: ~156 (all sites converted + definition + imports).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: convert all write_control call sites to WriteKind"
```

---

## Phase 2 — `Store` seam

Design ref: spec §"The context object & store". Repoint state access behind an interface with no logic change yet.

### Task 2.1: `Clock` abstraction

**Files:**
- Create: `controller/runtime/__init__.py` (empty), `controller/runtime/clock.py`
- Test: `tests/test_clock.py`

**Interfaces:**
- Produces: `Clock` (ABC: `now() -> float`, `sleep(seconds: float) -> None`); `RealClock`; `ManualClock(start: float = 0.0)` with `now()`, `sleep(s)` (advances virtual time), and `advance(s)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_clock.py
from controller.runtime.clock import ManualClock, RealClock, Clock


def test_manual_clock_starts_at_zero_and_sleep_advances():
    c = ManualClock()
    assert c.now() == 0.0
    c.sleep(0.5)
    assert c.now() == 0.5


def test_manual_clock_advance():
    c = ManualClock(start=100.0)
    c.advance(3.0)
    assert c.now() == 103.0


def test_real_clock_is_a_clock():
    assert isinstance(RealClock(), Clock)
```

- [ ] **Step 2: Run — Expected: FAIL (module missing)**

Run: `python -m pytest tests/test_clock.py -v`

- [ ] **Step 3: Implement**

```python
# controller/runtime/clock.py
"""Injectable time source so the control loop is deterministically testable."""
import time
from abc import ABC, abstractmethod


class Clock(ABC):
    @abstractmethod
    def now(self) -> float: ...

    @abstractmethod
    def sleep(self, seconds: float) -> None: ...


class RealClock(Clock):
    def now(self):
        return time.time()

    def sleep(self, seconds):
        time.sleep(seconds)


class ManualClock(Clock):
    def __init__(self, start: float = 0.0):
        self._t = float(start)

    def now(self):
        return self._t

    def sleep(self, seconds):
        self._t += seconds

    def advance(self, seconds):
        self._t += seconds
```

- [ ] **Step 4: Run — Expected: PASS**

- [ ] **Step 5: Commit**

```bash
git add controller/runtime/__init__.py controller/runtime/clock.py tests/test_clock.py
git commit -m "feat(control): add injectable Clock (Real + Manual)"
```

### Task 2.2: `Store` interface + `InMemoryStore`

**Files:**
- Create: `controller/runtime/store.py`
- Test: `tests/test_in_memory_store.py`

**Interfaces:**
- Produces: `Store` (ABC) with methods: `read_control()`, `write_control(control, kind, origin='control')`, `execute_control_writes()`, `read_settings()`, `read_status(init=False)`, `write_status(status)`, `read_current(zero_out=False)`, `write_current(in_data)`, `read_history(num_items=0, flushhistory=False)`, `write_history(in_data, ext_data=False)`, `read_metrics(all=False)`, `write_metrics(metrics=None, new_metric=False, flush=False)`, `write_tr(tr)`, `read_pellet_db()`, `write_pellet_db(db)`, `read_errors(flush=False)`, `write_errors(errors)`, `write_generic_key(key, value)`, `system_commands() -> Queue`, `system_output() -> Queue`, `display_commands() -> Queue`.
- Produces: `Queue` protocol: `push(item)`, `pop()`, `length()`, `list()`, `flush()`, `drain() -> list` (drain = pop-all in order).
- Produces: `InMemoryStore` implementing all of the above with dicts + `collections.deque`, replicating OVERWRITE/MERGE + `deep_update`-on-execute.

- [ ] **Step 1: Write the failing test** (focus on the tricky merge semantics)

```python
# tests/test_in_memory_store.py
from common.common import WriteKind
from controller.runtime.store import InMemoryStore


def test_overwrite_replaces_whole_control():
    s = InMemoryStore(control={'mode': 'Stop', 'a': 1})
    s.write_control({'mode': 'Hold'}, WriteKind.OVERWRITE)
    assert s.read_control() == {'mode': 'Hold'}


def test_merge_is_deferred_until_execute():
    s = InMemoryStore(control={'mode': 'Stop', 'nested': {'x': 1, 'y': 2}})
    s.write_control({'nested': {'x': 9}}, WriteKind.MERGE, origin='display')
    # nothing changes until execute
    assert s.read_control()['nested'] == {'x': 1, 'y': 2}
    s.execute_control_writes()
    # deep_update: x replaced, y preserved
    assert s.read_control()['nested'] == {'x': 9, 'y': 2}
    assert s.read_control()['mode'] == 'Stop'


def test_merges_apply_in_fifo_order():
    s = InMemoryStore(control={'v': 0})
    s.write_control({'v': 1}, WriteKind.MERGE)
    s.write_control({'v': 2}, WriteKind.MERGE)
    s.execute_control_writes()
    assert s.read_control()['v'] == 2


def test_display_queue_drain_is_fifo_and_empties():
    s = InMemoryStore()
    s.display_commands().push(('text', 'ERROR'))
    s.display_commands().push(('clear', None))
    assert s.display_commands().drain() == [('text', 'ERROR'), ('clear', None)]
    assert s.display_commands().drain() == []


def test_read_control_returns_a_copy():
    s = InMemoryStore(control={'mode': 'Stop'})
    c = s.read_control()
    c['mode'] = 'Hold'
    assert s.read_control()['mode'] == 'Stop'
```

- [ ] **Step 2: Run — Expected: FAIL (module missing)**

- [ ] **Step 3: Implement**

```python
# controller/runtime/store.py
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
```

Before finalizing, verify each `read_*`/`write_*` signature against `common/common.py` (grep the `def`s) so `InMemoryStore` mirrors the real defaults (`read_history`, `write_history`, `read_metrics`, `write_metrics` especially). Adjust if any differ.

- [ ] **Step 4: Run — Expected: PASS**

- [ ] **Step 5: Commit**

```bash
git add controller/runtime/store.py tests/test_in_memory_store.py
git commit -m "feat(control): add Store interface and InMemoryStore"
```

### Task 2.3: `ValkeyStore` + parity test (real Valkey, skippable)

**Files:**
- Modify: `controller/runtime/store.py` (add `ValkeyStore`)
- Create: `tests/test_valkey_store_parity.py`

**Interfaces:**
- Consumes: `Store`, `common.common` module functions, `common.valkey_queue.ValkeyQueue`.
- Produces: `ValkeyStore()` — thin pass-through to `common.common` functions; queues wrap `ValkeyQueue('control:systemq'|'control:systemo'|'control:displayq')` adapted to the `Queue` interface.

- [ ] **Step 1: Write the failing parity test**

```python
# tests/test_valkey_store_parity.py
import pytest

valkey = pytest.importorskip("valkey")


def _valkey_available():
    try:
        valkey.StrictValkey('localhost', 6379, socket_connect_timeout=0.2).ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _valkey_available(), reason="no local valkey-server")


def test_valkey_store_smoke():
    from controller.runtime.store import ValkeyStore
    s = ValkeyStore()
    s.read_control()  # smoke: must not raise
    s.write_generic_key('parity_probe', {'ok': True})


def test_valkey_display_queue_roundtrip():
    from controller.runtime.store import ValkeyStore
    s = ValkeyStore()
    s.display_commands().flush()
    s.display_commands().push(['text', 'ERROR'])
    assert s.display_commands().drain() == [['text', 'ERROR']]
```

- [ ] **Step 2: Run — Expected: PASS (skipped) if no valkey, else FAIL (ValkeyStore missing)**

Run: `python -m pytest tests/test_valkey_store_parity.py -v`

- [ ] **Step 3: Implement `ValkeyStore`**

Append to `controller/runtime/store.py`:

```python
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

    def read_control(self):
        return _c.read_control()

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

    def write_history(self, in_data, ext_data=False):
        _c.write_history(in_data, ext_data=ext_data)

    def read_metrics(self, all=False):
        return _c.read_metrics(all=all)

    def write_metrics(self, metrics=None, new_metric=False, flush=False):
        _c.write_metrics(metrics=metrics, new_metric=new_metric, flush=flush)

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
```

Verify each wrapped signature against `common/common.py` (grep the `def`); adjust keyword names if any differ (e.g. `write_metrics`, `read_metrics`, `read_history`) so the pass-through is exact.

- [ ] **Step 4: Run — Expected: PASS (or skip without valkey)**

Run: `python -m pytest tests/test_valkey_store_parity.py tests/test_in_memory_store.py -v`

- [ ] **Step 5: Commit**

```bash
git add controller/runtime/store.py tests/test_valkey_store_parity.py
git commit -m "feat(control): add ValkeyStore pass-through + parity test"
```

---

## Phase 3 — Fakes + characterization harness (the equivalence oracle)

Design ref: spec §"Testing strategy". **This phase must be complete and green before ANY decomposition (Phases 5-7).** The characterization tests run against the *current* `_work_cycle` (after minimal seam-insertion) and later against the new handlers with identical assertions.

### Task 3.1: Hardware fakes + notifier fake + runner fake

**Files:**
- Create: `tests/fakes/__init__.py`, `tests/fakes/grill.py`, `tests/fakes/probes.py`, `tests/fakes/distance.py`, `tests/fakes/notifier.py`, `tests/fakes/runner.py`
- Test: `tests/test_fakes.py`

**Interfaces:**
- Produces: `FakeGrillPlatform` — grill-platform surface used by `control.py`: `get_input_status()`, `get_output_status()`, `set_pwm_frequency(f)`, `set_duty_cycle(pct)`, `igniter_on/off()`, `auger_on/off()`, `fan_on(dc=None)/fan_off()`, `power_on/off()`, `pwm_fan_ramp(...)`, `supported_commands(x)`, `cleanup()`. Records ordered `.calls` of `(method, args)`; output status toggles reflect on/off calls. Constructor: `FakeGrillPlatform(dc_fan=False, standalone=True, input_on=True, outputs=('power','auger','fan','igniter'))`.
- Produces: `FakeProbes` — `read_probes()` returns the next scripted `sensor_data`; `get_device_info()`, `get_errors()`, `update_probe_profiles(x)`, `update_units(x)`; `.script([...])`.
- Produces: `FakeDistance` — `get_level(override=False) -> int`, `update_distances(e, f)`.
- Produces: `FakeNotifier` — `send(name)` → `.sent`; `check(settings, control, **kw) -> control`; `get_targets(notify_data) -> {}`.
- Produces: `FakeControllerRunner` — `set_target`, `submit`, `reconfigure`, `latest() -> output`, `control_period()`, `.script([...])`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fakes.py
from tests.fakes.grill import FakeGrillPlatform
from tests.fakes.probes import FakeProbes
from tests.fakes.notifier import FakeNotifier


def test_grill_records_calls_and_toggles_output():
    g = FakeGrillPlatform(outputs=('power', 'auger', 'fan', 'igniter'))
    g.auger_on()
    assert g.get_output_status()['auger'] is True
    g.auger_off()
    assert g.get_output_status()['auger'] is False
    assert ('auger_on', ()) in g.calls
    assert g.calls[-1][0] == 'auger_off'


def test_probes_yield_scripted_sequence():
    p = FakeProbes()
    p.script([{'primary': {'Grill': 100}, 'food': {}, 'aux': {}, 'tr': {}},
              {'primary': {'Grill': 110}, 'food': {}, 'aux': {}, 'tr': {}}])
    assert list(p.read_probes()['primary'].values())[0] == 100
    assert list(p.read_probes()['primary'].values())[0] == 110


def test_notifier_records_sent():
    n = FakeNotifier()
    n.send('Grill_Error_01')
    assert n.sent == ['Grill_Error_01']
```

- [ ] **Step 2: Run — Expected: FAIL (modules missing)**

- [ ] **Step 3: Implement the fakes**

```python
# tests/fakes/grill.py
class FakeGrillPlatform:
    def __init__(self, dc_fan=False, standalone=True, input_on=True,
                 outputs=('power', 'auger', 'fan', 'igniter')):
        self.calls = []
        self._input_on = input_on
        self._status = {k: False for k in outputs}
        self._status['pwm'] = 100
        self._status['frequency'] = 100

    def _rec(self, name, *args):
        self.calls.append((name, args))

    def get_input_status(self):
        return self._input_on

    def set_input(self, on):          # test helper
        self._input_on = on

    def get_output_status(self):
        return dict(self._status)

    def set_pwm_frequency(self, f):
        self._rec('set_pwm_frequency', f); self._status['frequency'] = f

    def set_duty_cycle(self, pct):
        self._rec('set_duty_cycle', pct); self._status['pwm'] = pct

    def igniter_on(self):
        self._rec('igniter_on'); self._status['igniter'] = True

    def igniter_off(self):
        self._rec('igniter_off'); self._status['igniter'] = False

    def auger_on(self):
        self._rec('auger_on'); self._status['auger'] = True

    def auger_off(self):
        self._rec('auger_off'); self._status['auger'] = False

    def fan_on(self, dc=None):
        self._rec('fan_on', dc); self._status['fan'] = True

    def fan_off(self):
        self._rec('fan_off'); self._status['fan'] = False

    def power_on(self):
        self._rec('power_on'); self._status['power'] = True

    def power_off(self):
        self._rec('power_off'); self._status['power'] = False

    def pwm_fan_ramp(self, *a):
        self._rec('pwm_fan_ramp', *a)

    def supported_commands(self, x):
        return {'data': {'supported_cmds': []}}

    def cleanup(self):
        self._rec('cleanup')
```

```python
# tests/fakes/probes.py
class FakeProbes:
    def __init__(self):
        self._script = []
        self._i = 0
        self._info = {}
        self._errors = []

    def script(self, items):
        norm = []
        for it in items:
            if isinstance(it, dict):
                norm.append(it)
            else:
                norm.append({'primary': {'Grill': it}, 'food': {}, 'aux': {}, 'tr': {}})
        self._script = norm
        self._i = 0
        return self

    def read_probes(self):
        if not self._script:
            return {'primary': {'Grill': 0}, 'food': {}, 'aux': {}, 'tr': {}}
        item = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return item

    def get_device_info(self):
        return self._info

    def get_errors(self):
        return self._errors

    def update_probe_profiles(self, x):
        pass

    def update_units(self, x):
        pass
```

```python
# tests/fakes/distance.py
class FakeDistance:
    def __init__(self, level=100):
        self._level = level

    def get_level(self, override=False):
        return self._level

    def update_distances(self, empty, full):
        pass
```

```python
# tests/fakes/notifier.py
class FakeNotifier:
    def __init__(self):
        self.sent = []
        self.checks = []

    def send(self, name):
        self.sent.append(name)

    def check(self, settings, control, **kwargs):
        self.checks.append(kwargs)
        return control

    def get_targets(self, notify_data):
        return {}
```

```python
# tests/fakes/runner.py
class FakeControllerRunner:
    def __init__(self, period=None):
        self._script = []
        self._i = 0
        self.target = None
        self._period = period

    def script(self, outputs):
        self._script = list(outputs)
        self._i = 0
        return self

    def set_target(self, setpoint):
        self.target = setpoint

    def submit(self, temp):
        pass

    def reconfigure(self, settings, control):
        return 'Active'

    def control_period(self):
        return self._period

    def latest(self):
        if not self._script:
            return None
        out = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return out
```

Add empty `tests/fakes/__init__.py`.

- [ ] **Step 4: Run — Expected: PASS** (`python -m pytest tests/test_fakes.py -v`)

- [ ] **Step 5: Commit**

```bash
git add tests/fakes tests/test_fakes.py
git commit -m "test(control): add hardware/notifier/runner fakes"
```

### Task 3.2: Insert the `Store`/`Clock`/`Notifier` seam into current `control.py` (no behavior change)

**Files:**
- Modify: `control.py` — add an injectable `ctx` so `_work_cycle`/`_recipe_mode`/`_next_mode`/`_process_system_commands` and the main loop use it instead of bare globals, WITHOUT changing behavior. `__main__` constructs a real context (`ValkeyStore`, `RealClock`, `ValkeyNotifier`, real devices) so production is identical.
- Create: `controller/runtime/context.py`

**Interfaces:**
- Consumes: `Store`, `Clock`, `Notifier`, `Devices`.
- Produces: `_work_cycle(mode, ctx)`, `_recipe_mode(ctx, start_step=0)`, `_next_mode(ctx, next_mode, setpoint=0)`, `_process_system_commands(ctx)`. Display calls become `ctx.store.display_commands().push((...))`.

- [ ] **Step 1: Add `ControllerContext` shim**

```python
# controller/runtime/context.py
"""Bundle of everything a control cycle needs. Passed instead of globals."""
from dataclasses import dataclass


@dataclass
class Devices:
    grill_platform: object
    probe_complex: object
    dist_device: object


@dataclass
class ControllerContext:
    devices: object            # Devices
    store: object              # Store
    notifications: object      # Notifier
    clock: object              # Clock
    event_log: object = None
    control_log: object = None
```

- [ ] **Step 2: Route state/notify/time/devices through `ctx`** (mechanical, no logic change). Rules — apply verbatim:
- `read_control()` → `ctx.store.read_control()`; `write_control(c, WriteKind.X, origin='control')` → `ctx.store.write_control(c, WriteKind.X, origin='control')`; same for `read_settings`, `read_status`, `write_status`, `read_current`, `write_current`, `read_history`, `write_history`, `read_metrics`, `write_metrics`, `write_tr`, `read_pellet_db`, `write_pellet_db`, `read_errors`, `write_errors`, `write_generic_key`, `execute_control_writes`.
- `send_notifications(x)` → `ctx.notifications.send(x)`; `check_notify(s, c, **kw)` → `ctx.notifications.check(s, c, **kw)`; `get_notify_targets(nd)` → `ctx.notifications.get_targets(nd)`.
- `time.time()` → `ctx.clock.now()`; `time.sleep(s)` → `ctx.clock.sleep(s)`.
- `grill_platform`/`probe_complex`/`dist_device` → `ctx.devices.<name>`.
- `_process_system_commands(grill_platform)` → `_process_system_commands(ctx)` using `ctx.store.system_commands()` / `ctx.store.system_output()` instead of `ValkeyQueue(...)`.
- Display calls → queue pushes:
  - `display_device.display_text('ERROR')` → `ctx.store.display_commands().push(('text', 'ERROR'))`
  - `display_device.display_text('Re-Ignite')` → `ctx.store.display_commands().push(('text', 'Re-Ignite'))`
  - `display_device.clear_display()` → `ctx.store.display_commands().push(('clear', None))`
  - `display_device.display_status(in_data, status_data)` → **delete** (data flows via `write_status`/`write_current`).
- Update signatures and all call sites: `_work_cycle(mode, ctx)`, `_recipe_mode(ctx, start_step=0)`, `_next_mode(ctx, next_mode, setpoint=0)`.

- [ ] **Step 3: Build the real context in `__main__`**

```python
from controller.runtime.context import ControllerContext, Devices
from controller.runtime.store import ValkeyStore
from controller.runtime.clock import RealClock
from controller.runtime.notifier import ValkeyNotifier   # Task 4.1

ctx = ControllerContext(
    devices=Devices(grill_platform=grill_platform,
                    probe_complex=probe_complex,
                    dist_device=dist_device),
    store=ValkeyStore(),
    notifications=ValkeyNotifier(),
    clock=RealClock(),
    event_log=eventLogger,
    control_log=controlLogger,
)
```

Replace mode-dispatch calls: `_work_cycle('Startup', grill_platform, probe_complex, display_device, dist_device)` → `_work_cycle('Startup', ctx)`, etc. Leave display construction in place but unused for now (Phase 8 removes it). **Note:** Task 4.1 (`ValkeyNotifier`) is a dependency of this step's `__main__` import — implement 4.1 first, or temporarily inline a `ValkeyNotifier` shim and replace it in Phase 4.

- [ ] **Step 4: Verify import + no regression**

Run: `python -c "import control"` (succeeds); `python -m pytest tests/ -q 2>&1 | tail -3` (no new failures).

- [ ] **Step 5: Commit**

```bash
git add control.py controller/runtime/context.py
git commit -m "refactor(control): route work cycle through injected context (no behavior change)"
```

### Task 3.3: Characterization tests against the current loop

**Files:**
- Create: `tests/characterization/__init__.py`, `tests/characterization/fixtures.py`, `tests/characterization/harness.py`, `tests/characterization/test_modes_golden.py`

**Interfaces:**
- Consumes: `control._work_cycle`, `InMemoryStore`, fakes, `ManualClock`.
- Produces: `run_mode(mode, *, settings, control_data, pellet_db, probes, grill=None) -> CaptureResult` with `.grill_calls`, `.display_commands`, `.notifications`, `.final_control`, `.final_status`.

- [ ] **Step 1: Write fixtures + harness**

```python
# tests/characterization/harness.py
from dataclasses import dataclass, field

from controller.runtime.context import ControllerContext, Devices
from controller.runtime.store import InMemoryStore
from controller.runtime.clock import ManualClock
from tests.fakes.grill import FakeGrillPlatform
from tests.fakes.distance import FakeDistance
from tests.fakes.notifier import FakeNotifier
import control


@dataclass
class CaptureResult:
    grill_calls: list = field(default_factory=list)
    display_commands: list = field(default_factory=list)
    notifications: list = field(default_factory=list)
    final_control: dict = field(default_factory=dict)
    final_status: dict = field(default_factory=dict)


def make_ctx(settings, control_data, pellet_db, probes, grill=None):
    store = InMemoryStore(control=control_data, settings=settings, pellet_db=pellet_db)
    grill = grill or FakeGrillPlatform(
        dc_fan=settings['platform'].get('dc_fan', False),
        standalone=settings['platform'].get('standalone', True),
        outputs=tuple(settings['platform']['outputs']),
    )
    notifier = FakeNotifier()
    ctx = ControllerContext(
        devices=Devices(grill_platform=grill, probe_complex=probes,
                        dist_device=FakeDistance()),
        store=store, notifications=notifier, clock=ManualClock(),
    )
    return ctx, grill, notifier


def run_mode(mode, *, settings, control_data, pellet_db, probes, grill=None):
    ctx, grill, notifier = make_ctx(settings, control_data, pellet_db, probes, grill)
    control._work_cycle(mode, ctx)
    return CaptureResult(
        grill_calls=grill.calls,
        display_commands=ctx.store.display_commands().list(),
        notifications=notifier.sent,
        final_control=ctx.store.read_control(),
        final_status=ctx.store.read_status(),
    )
```

`tests/characterization/fixtures.py` provides `base_settings()`, `base_control(mode=...)`, `base_pellet_db()` returning realistic dicts. Derive from `common/common.py` `default_control()` plus the settings defaults (grep `default_settings` / `settings.json`); copy the exact nested keys the loop reads: `platform`, `safety`, `cycle_data`, `startup`, `pwm`, `smoke_plus`, `shutdown`, `globals`, `pelletlevel`, `modules`, `notify_services`.

- [ ] **Step 2: First golden scenario — max-temp safety**

```python
# tests/characterization/test_modes_golden.py
from tests.characterization.harness import run_mode
from tests.characterization.fixtures import base_settings, base_control, base_pellet_db
from tests.fakes.probes import FakeProbes


def test_smoke_over_maxtemp_triggers_error_and_notifies():
    settings = base_settings()
    settings['safety']['maxtemp'] = 500
    probes = FakeProbes().script([550, 550, 550])
    control_data = base_control(mode='Smoke')
    result = run_mode('Smoke', settings=settings, control_data=control_data,
                      pellet_db=base_pellet_db(), probes=probes)
    assert result.final_control['mode'] == 'Error'
    assert 'Grill_Error_01' in result.notifications
    assert ('text', 'ERROR') in result.display_commands
```

- [ ] **Step 3: Run — Expected: PASS** (`python -m pytest tests/characterization -v`)

- [ ] **Step 4: Add the remaining golden scenarios** — one test each; run once against the current loop, inspect the captured effects, then freeze the assertion:
Startup exit-on-exit-temp; Startup exit-on-timer; smart-start profile selection (assert metrics `augerontime`/`p_mode`); Smoke auger on/off sequence; Hold w/ `FakeControllerRunner` scripted output → auger cycle ratio + PWM duty; Hold lid-open → auger_off+fan_off; flameout `reigniteretries>0` → `Reignite` + `Grill_Error_03` + `('text','Re-Ignite')`; flameout `reigniteretries==0` → `Error` + `Grill_Error_02`; Prime elapse after `prime_duration`; Shutdown elapse; Manual override fan/auger/igniter/power; Monitor idle power-off. These frozen captures are the equivalence oracle for Phases 5-7.

- [ ] **Step 5: Commit**

```bash
git add tests/characterization
git commit -m "test(control): golden-master characterization of current work cycle"
```

---

## Phase 4 — Context completion: `Notifier` + `build_devices()`

### Task 4.1: `ValkeyNotifier`

**Files:**
- Create: `controller/runtime/notifier.py`
- Test: `tests/test_notifier_iface.py`

**Interfaces:**
- Produces: `Notifier` (ABC: `send(name)`, `check(settings, control, **kw) -> control`, `get_targets(notify_data) -> dict`); `ValkeyNotifier` delegating to `notify.notifications.send_notifications` / `check_notify` and `common.get_notify_targets`.

- [ ] **Step 1: Failing test**

```python
# tests/test_notifier_iface.py
from controller.runtime.notifier import Notifier


def test_valkey_notifier_is_a_notifier():
    from controller.runtime.notifier import ValkeyNotifier
    assert isinstance(ValkeyNotifier(), Notifier)
```

- [ ] **Step 2: Run — Expected: FAIL**

- [ ] **Step 3: Implement**

```python
# controller/runtime/notifier.py
"""Notification seam so the control loop can be tested without a real backend."""
from abc import ABC, abstractmethod


class Notifier(ABC):
    @abstractmethod
    def send(self, name): ...
    @abstractmethod
    def check(self, settings, control, **kwargs): ...
    @abstractmethod
    def get_targets(self, notify_data): ...


class ValkeyNotifier(Notifier):
    def send(self, name):
        from notify.notifications import send_notifications
        send_notifications(name)

    def check(self, settings, control, **kwargs):
        from notify.notifications import check_notify
        return check_notify(settings, control, **kwargs)

    def get_targets(self, notify_data):
        from common import get_notify_targets
        return get_notify_targets(notify_data)
```

Verify the import paths against the codebase (`grep -n "def send_notifications\|def check_notify" notify/notifications.py`, `grep -n "def get_notify_targets" common/common.py`); adjust if the names/locations differ.

- [ ] **Step 4: Run — Expected: PASS**

- [ ] **Step 5: Commit**

```bash
git add controller/runtime/notifier.py tests/test_notifier_iface.py
git commit -m "feat(control): add Notifier interface + ValkeyNotifier"
```

### Task 4.2: `build_devices()` factory

**Files:**
- Create: `controller/runtime/devices.py`
- Modify: `control.py` (`__main__` — replace the four try/except device-init blocks with `build_devices(...)`)
- Test: `tests/test_build_devices.py`

**Interfaces:**
- Produces: `build_devices(settings, *, include_display: bool, errors: list, event_log, control_log) -> tuple[Devices, display_or_None, errors]`. Moves the grill/probe/distance (and optional display) import+construct+fallback logic verbatim from `control.py:1200-1406`. `include_display=False` for the controller; `display.py` calls it with `include_display=True`.

- [ ] **Step 1: Failing test** (prototype modules construct without hardware; hand-build a minimal settings dict to keep hermetic)

```python
# tests/test_build_devices.py
def _proto_settings():
    return {
        'modules': {'grillplat': 'prototype', 'dist': 'prototype',
                    'display': 'none', 'probes': 'prototype'},
        'platform': {'devices': {}, 'buttonslevel': 'HIGH', 'outputs': ['power', 'auger', 'fan', 'igniter'],
                     'dc_fan': False, 'standalone': True},
        'pelletlevel': {'empty': 22, 'full': 4},
        'globals': {'units': 'F', 'debug_mode': False},
        'pwm': {'frequency': 100},
        'probe_settings': {'probe_map': {'probe_info': []}},
    }


def test_build_devices_prototype_platform_headless():
    from controller.runtime.devices import build_devices
    devices, display, errors = build_devices(
        _proto_settings(), include_display=False, errors=[], event_log=None, control_log=None)
    assert devices.grill_platform is not None
    assert devices.probe_complex is not None
    assert devices.dist_device is not None
    assert display is None
```

If the prototype modules read settings keys not in `_proto_settings()`, extend the dict with those exact keys (inspect the import errors).

- [ ] **Step 2: Run — Expected: FAIL**

- [ ] **Step 3: Implement** — move `control.py:1200-1406` device/display init into `build_devices`, returning `Devices(...)` and the display (or `None`). Preserve the exact prototype-fallback and `errors.append` behavior. In `control.py __main__`, replace those blocks with `build_devices(settings, include_display=False, ...)`.

- [ ] **Step 4: Run — Expected: PASS** (`python -m pytest tests/test_build_devices.py -v`), then `python -c "import control"`.

- [ ] **Step 5: Commit**

```bash
git add controller/runtime/devices.py control.py tests/test_build_devices.py
git commit -m "refactor(control): extract build_devices() factory"
```

---

## Phase 5 — `ControllerRunner` seam

Design ref: spec §"`ControllerRunner` seam". Characterization tests stay green throughout.

### Task 5.1: `SyncControllerRunner`

**Files:**
- Create: `controller/runtime/runner.py`
- Test: `tests/test_sync_runner.py`

**Interfaces:**
- Consumes: `controller.base.normalize_controller_output`, a controller core (`.update`, `.set_target`, `.get_control_period`), `importlib`.
- Produces: `NormalizedOutput = namedtuple('NormalizedOutput', ['cycle_ratio', 'fan'])`; `ControllerRunner` (ABC: `set_target`, `submit`, `latest`, `reconfigure`, `control_period`); `SyncControllerRunner(core)` with `latest_from(temp)` convenience + `controller_state()`; `build_runner(settings, control) -> (SyncControllerRunner|None, status)` replacing `_init_controller`.

- [ ] **Step 1: Failing test**

```python
# tests/test_sync_runner.py
from controller.runtime.runner import SyncControllerRunner, NormalizedOutput


class _Core:
    def __init__(self): self.target = None; self.period = 5.0
    def set_target(self, sp): self.target = sp
    def update(self, temp): return {'cycle_ratio': 0.4, 'fan': {'duty': 60}}
    def get_control_period(self): return self.period


def test_sync_runner_normalizes_dict_output():
    r = SyncControllerRunner(_Core())
    r.set_target(225)
    out = r.latest_from(200.0)
    assert isinstance(out, NormalizedOutput)
    assert out.cycle_ratio == 0.4
    assert out.fan == {'duty': 60}


def test_sync_runner_float_output_has_no_fan():
    class FloatCore(_Core):
        def update(self, temp): return 0.25
    out = SyncControllerRunner(FloatCore()).latest_from(190.0)
    assert out.cycle_ratio == 0.25 and out.fan is None
```

- [ ] **Step 2: Run — Expected: FAIL**

- [ ] **Step 3: Implement**

```python
# controller/runtime/runner.py
"""Temperature-controller execution seam (PID/MPC/etc). Sync impl == today's
inline behavior; a ThreadedControllerRunner may be added later for MPC."""
import importlib
from abc import ABC, abstractmethod
from collections import namedtuple

from controller.base import normalize_controller_output

NormalizedOutput = namedtuple('NormalizedOutput', ['cycle_ratio', 'fan'])


class ControllerRunner(ABC):
    @abstractmethod
    def set_target(self, setpoint): ...
    @abstractmethod
    def submit(self, temp): ...
    @abstractmethod
    def latest(self): ...
    @abstractmethod
    def reconfigure(self, settings, control): ...
    @abstractmethod
    def control_period(self): ...


class SyncControllerRunner(ControllerRunner):
    def __init__(self, core):
        self._core = core
        self._temp = None

    def set_target(self, setpoint):
        self._core.set_target(setpoint)

    def submit(self, temp):
        self._temp = temp

    def latest(self):
        raw = self._core.update(self._temp)
        ratio, fan = normalize_controller_output(raw)
        return NormalizedOutput(cycle_ratio=ratio, fan=fan)

    def latest_from(self, temp):
        self.submit(temp)
        return self.latest()

    def reconfigure(self, settings, control):
        core, status = _build_core(settings, control)
        if status == 'Active':
            self._core = core
        return status

    def control_period(self):
        return self._core.get_control_period()

    def controller_state(self):
        return dict(self._core.__dict__)


def _build_core(settings, control):
    try:
        controller_type = settings['controller']['selected']
        module = importlib.import_module(f'controller.{controller_type}')
    except Exception:
        return None, 'Inactive'
    core = module.Controller(
        settings['controller']['config'][controller_type],
        settings['globals']['units'], settings['cycle_data'])
    core.set_target(control['primary_setpoint'])
    return core, 'Active'


def build_runner(settings, control):
    core, status = _build_core(settings, control)
    if core is None:
        return None, status
    return SyncControllerRunner(core), status
```

- [ ] **Step 4: Run — Expected: PASS**

- [ ] **Step 5: Commit**

```bash
git add controller/runtime/runner.py tests/test_sync_runner.py
git commit -m "feat(control): add ControllerRunner seam (Sync impl)"
```

### Task 5.2: Use the runner in `_work_cycle` + neutral naming

**Files:**
- Modify: `control.py` (Hold path: `control.py:257, 439-446, 548-561, 599-606, 662-665`)
- Test: existing Hold characterization tests (must stay green).

**Interfaces:**
- Consumes: `build_runner`, `NormalizedOutput`.

- [ ] **Step 1: Replace `_init_controller` usage** — `controllerCore, controller_status = _init_controller(settings, control)` → `runner, controller_status = build_runner(settings, control)`. On `controller_update`, call `runner.reconfigure(settings, control)`.

- [ ] **Step 2: Replace inline update** (`control.py:548-551`):

```python
controller_interval = runner.control_period() or CycleTime
if (now - controllerCycleStart) > controller_interval:
    runner.submit(ptemp)
    out = runner.latest()
    controller_output, fan_cmd = out.cycle_ratio, out.fan
```

Rename Hold-path locals: `pid_output` → `controller_output`, `mpc_fan_duty` → `controller_fan_duty`, `ControlFanPid` → `fan_assist`, MQTT `pid_data` → `controller_data`. For `control.py:604` (`pid_data = controllerCore.__dict__`) → `controller_data = runner.controller_state()`.

- [ ] **Step 3: Run characterization — Expected: PASS** (`python -m pytest tests/characterization -v`). If a Hold golden changed, STOP and reconcile — the rename/seam altered behavior.

- [ ] **Step 4: Grep leftovers** — `grep -n "pid_output\|mpc_fan_duty\|ControlFanPid\|_init_controller\|controllerCore" control.py` → no matches (delete the now-unused `_init_controller` def).

- [ ] **Step 5: Commit**

```bash
git add control.py
git commit -m "refactor(control): drive Hold via ControllerRunner + neutral naming"
```

---

## Phase 6 — Pure-logic modules

Design ref: spec §"Pure-logic modules". Each module is its own task (same 5-step shape). Write an exhaustive unit test per function (boundaries/clamps) before wiring into `control.py`. Complete bodies must mirror the cited `control.py` lines exactly.

### Task 6.1: `logic/safety.py`

**Files:** Create `controller/runtime/logic/__init__.py`, `controller/runtime/logic/safety.py`; Test `tests/test_logic_safety.py`.

**Interfaces:** `startup_temp_bounds(ptemp, safety_settings) -> int`, `SafetyVerdict` (Enum OK/REIGNITE/ERROR), `evaluate_flameout(ptemp, startup_temp, reignite_retries) -> SafetyVerdict`, `over_max_temp(ptemp, safety_settings) -> bool`.

- [ ] **Step 1: Failing test**

```python
# tests/test_logic_safety.py
from controller.runtime.logic.safety import (
    startup_temp_bounds, evaluate_flameout, over_max_temp, SafetyVerdict)


def test_startup_temp_bounds_clamps_to_min_and_max():
    s = {'minstartuptemp': 100, 'maxstartuptemp': 200}
    assert startup_temp_bounds(50, s) == 100     # 0.9*50=45 -> min 100
    assert startup_temp_bounds(1000, s) == 200   # 0.9*1000=900 -> max 200
    assert startup_temp_bounds(150, s) == 135    # 0.9*150=135 within range


def test_evaluate_flameout():
    assert evaluate_flameout(210, 200, 0) is SafetyVerdict.OK
    assert evaluate_flameout(180, 200, 0) is SafetyVerdict.ERROR
    assert evaluate_flameout(180, 200, 2) is SafetyVerdict.REIGNITE


def test_over_max_temp():
    assert over_max_temp(501, {'maxtemp': 500}) is True
    assert over_max_temp(500, {'maxtemp': 500}) is False
```

- [ ] **Step 2: Run — FAIL.**  **Step 3: Implement:**

```python
# controller/runtime/logic/safety.py
"""Pure safety decisions extracted from _work_cycle. No I/O."""
from enum import Enum


class SafetyVerdict(Enum):
    OK = 'ok'
    REIGNITE = 'reignite'
    ERROR = 'error'


def startup_temp_bounds(ptemp, safety_settings):
    bound = int(max(ptemp * 0.9, safety_settings['minstartuptemp']))
    return int(min(bound, safety_settings['maxstartuptemp']))


def evaluate_flameout(ptemp, startup_temp, reignite_retries):
    if ptemp >= startup_temp:
        return SafetyVerdict.OK
    return SafetyVerdict.ERROR if reignite_retries == 0 else SafetyVerdict.REIGNITE


def over_max_temp(ptemp, safety_settings):
    return ptemp > safety_settings['maxtemp']
```

- [ ] **Step 4: Run — PASS.**  **Step 5: Commit** `feat(control): add pure safety logic`.

### Task 6.2: `logic/cycle.py`

**Interfaces:** `CycleTimes` dataclass (`on_time, off_time, cycle_time, cycle_ratio`); `smoke_cycle_times(cycle_data) -> CycleTimes` (`control.py:234-238/427-433`); `hold_initial_cycle(cycle_data) -> CycleTimes` (`:246-251`); `hold_update_cycle(controller_output, cycle_data, *, lid_open) -> CycleTimes` (`:553-575`, clamps `[u_min, u_max]`); `prime_cycle_times(prime_amount, auger_rate) -> CycleTimes` (`:272-278`).

Unit tests: smoke ratio = `OnTime/(OnTime+OffTime)` with `PMode*10` offset; hold clamps below `u_min` up to `u_min`, above `u_max` down to `u_max`; `lid_open=True` forces ratio to `u_min`. Bodies mirror the cited lines. Same 5-step shape. Commit `feat(control): add pure cycle-time logic`.

### Task 6.3: `logic/smartstart.py`

**Interfaces:** `select_profile(startup_temp, temp_range_list) -> int` (`:326-336` — first range exceeding temp, else `len(list)`); `profile_cycle(profile, cycle_data) -> tuple[CycleTimes, float, dict]` returning cycle times, `startup_timer`, metrics bits (`p_mode`, `auger_cycle_time`) from `:338-353`. Unit-test boundary temps (below first, between, above last). Commit `feat(control): add pure smart-start logic`.

### Task 6.4: `logic/pwm.py`

**Interfaces:** `hold_duty_cycle(setpoint, ptemp, pwm_settings) -> int` (`:776-791` — over-setpoint → `min_duty_cycle`; else first profile where `temp_range_list[i] >= (setpoint-ptemp)`, clamped `[min,max]`; fallthrough → `max_duty_cycle`); `ramp_params(smoke_plus, pwm_settings) -> tuple` (`:863-867`). Unit-test profile boundaries + clamps. Commit `feat(control): add pure PWM duty logic`.

### Task 6.5: `logic/fan.py`

**Interfaces:** `clamp_duty(duty, pwm_settings) -> int` (`_start_fan` clamp `:57-61`); `FanTimes` dataclass; `fan_assist_times(controller_output, total_fan_cycle, max_fan_ratio, u_min) -> FanTimes` (`:815-818` — `adjusted = max(0, output/u_min)`, `ratio = adjusted*max_fan_ratio`, on/off times); `smoke_plus_max_ratio(smoke_plus_settings, s_plus) -> float` (`:804-809`). Unit-test negative output → 0 fan, clamps. Commit `feat(control): add pure fan logic`.

### Task 6.6: Wire pure logic into `_work_cycle`

**Files:** Modify `control.py`; Test: characterization suite.

- [ ] **Step 1-2:** Replace each inline computation with the matching pure call (`:293-296` → `startup_temp_bounds`; `:553-575` → `hold_update_cycle`; `:776-791` → `hold_duty_cycle`; `:326-353` → `select_profile`/`profile_cycle`; `:815-818` → `fan_assist_times`; `_start_fan` clamp → `clamp_duty`). Assign results into the existing locals so downstream code is untouched.
- [ ] **Step 3:** Run characterization — **Expected: PASS, identical assertions.** Any diff = transcription error in a pure function; fix the function, not the golden value.
- [ ] **Step 4:** Grep to confirm no duplicated inline math remains where a pure fn now exists.
- [ ] **Step 5:** Commit `refactor(control): call pure-logic modules from work cycle`.

---

## Phase 7 — Mode-handler decomposition

Design ref: spec §"Mode handlers". Highest-risk phase. The characterization suite is the gate: identical assertions must pass against the new handlers.

### Task 7.1: `WorkCycleState`

**Files:** Create `controller/runtime/state.py`; Test `tests/test_work_cycle_state.py`.

**Interfaces:** `WorkCycleState` dataclass — every conditionally-defined loop-local, defaulted so no `locals()` check is needed: `cycle_ratio=0.0`, `raw_cycle_ratio=0.0`, `on_time=0.0`, `off_time=0.0`, `cycle_time=0.0`, `controller_output=0.0`, `controller_fan_duty=None`, `fan_assist=False`, `lid_open_detect=False`, `lid_open_expires=0.0`, `target_temp_achieved=False`, `prime_duration=0.0`, `prime_amount=0.0`, `startup_timer=0.0`, `raw_startup_temp=0.0`, `pwm_fan_ramping=False`, plus toggle timestamps (`start_time`, `auger_toggle_time`, `display_toggle_time`, `fan_cycle_toggle_time`, `hopper_toggle_time`, `fan_update_time`, `eta_toggle_time`, `temp_toggle_time`, `controller_cycle_start`), and `manual_override: dict`, `metrics: dict` (use `field(default_factory=dict)`).

- [ ] Standard 5 steps; test constructs it and checks defaults. Commit `feat(control): add WorkCycleState`.

### Task 7.2: `ControlMode` base skeleton

**Files:** Create `controller/runtime/modes/__init__.py`, `controller/runtime/modes/base.py`; Test `tests/test_mode_base.py`.

**Interfaces:** `ControlMode(ctx, state)` with shared `run()` (spec pseudocode) and hooks `setup`, `setup_safety`, `on_tick`, `check_safety`, `should_exit`, `status_fragment`, `teardown` (defaults: no-op / `{}` / `False`). Shared helpers `_drain_control_and_system_commands`, `_mode_change_requested`, `_apply_settings_updates`, `_handle_manual_overrides`, `_read_probes_and_write_current`, `_universal_safety` (max-temp only), `_publish_status_and_history`, `_final_cleanup` moved verbatim from the corresponding `control.py` regions (cited in 7.3). `name` is a class attribute.

- [ ] **Step 1:** Test a trivial subclass exiting after 1 tick, asserting hook call order via a recording list. **Steps 2-4** implement + green. **Step 5** commit `feat(control): add ControlMode template base`.

### Task 7.3: Per-mode handlers (one sub-task each)

For **each** mode: create `controller/runtime/modes/<mode>.py` subclassing `ControlMode`, move the mode-specific regions into the hooks, then run that mode's characterization tests. Source regions to relocate (verbatim, adapted to `self.state`/`self.ctx`):

| Mode | setup / setup_safety | on_tick | check_safety | should_exit | status_fragment | teardown |
|---|---|---|---|---|---|---|
| Startup / Reignite | `:227-232, 234-244, 289-298, 319-353` | `:544-616` auger cycle | `:708-709` record afterstarttemp | `:926-949` | p_mode | `:1003-1005` |
| Smoke | `:234-244` | `:544-616` + `:832-917` smoke-plus fan | (none) | (mode change) | — | — |
| Hold | `:246-269` (runner init) | `:546-616, 729-917` | `:710-727` flameout | (mode change) | primary_setpoint, lid fields | — |
| Shutdown | `:206-213` power/fan | — | (none) | `:952-953` | — | `:998-1001` |
| Prime | `:271-282` | `:544-616` auger cycle | (none) | `:956-957` | prime_duration/amount | `:998-1001` |
| Monitor / Manual | `:210-213` | `:483-542` manual only | (none) | (mode change) | — | `:998-1001` |

Shared pre-loop (`:181-232` base output state, metrics init `:215-225`) stays in `ControlMode.run()` before the mode `setup()` hook. Each mode sub-task:

- [ ] **Step 1:** Move the cited regions into the class hooks; route that mode through the new class (`MODES[mode](ctx, state).run()` from `_work_cycle`).
- [ ] **Step 2:** Run that mode's characterization tests — **Expected: PASS, identical assertions.**
- [ ] **Step 3:** If any assertion differs, STOP and reconcile (transcription error). Do not edit golden values.
- [ ] **Step 4:** `python -m pytest tests/characterization -q` (all modes green).
- [ ] **Step 5:** Commit `refactor(control): extract <Mode>Mode handler`.

Order: Monitor/Manual first (simplest), then Prime, Shutdown, Smoke, Startup/Reignite, Hold (most complex) last.

### Task 7.4: `Controller` orchestrator + `RecipeMode`

**Files:** Create `controller/runtime/controller.py`; Modify `control.py` (main loop → `Controller.run()`); Test `tests/test_controller_dispatch.py`, characterization recipe test.

**Interfaces:** `Controller(ctx)` with `run()` = today's `__main__` while-loop (`control.py:1427-1691`) dispatching to `MODES[mode]`; `RecipeMode(ctx)` = today's `_recipe_mode` (`:1040-1134`) invoking modes step by step. `MODES` = mode-name → handler-class mapping.

- [ ] **Step 1:** Move the dispatch loop into `Controller.run()`; move `_recipe_mode`/`_next_mode` into the orchestrator. Each `_work_cycle('X', ctx)` becomes `MODES['X'](ctx, WorkCycleState()).run()`.
- [ ] **Step 2:** Dispatch unit test: seed `InMemoryStore` with `control['mode']='Monitor', control['updated']=True`; run one orchestrator iteration; assert Monitor handler ran (fake grill `power_off` recorded) and the loop is stoppable.
- [ ] **Step 3:** Run full characterization including the Recipe step-progression + reignite-retry scenario — **Expected: PASS.**
- [ ] **Step 4:** `python -c "import control"`; ensure the `_work_cycle` shim is removed and unreferenced.
- [ ] **Step 5:** Commit `refactor(control): Controller orchestrator replaces main dispatch loop`.

---

## Phase 8 — Display separation

### Task 8.1: `display.py` + `DisplayFeeder`

**Files:** Create `display.py`; Test `tests/test_display_feeder.py`.

**Interfaces:** `DisplayFeeder(display, store, clock)` with `run()` (spec pseudocode) and a single-iteration `tick()` for testing.

- [ ] **Step 1: Failing test**

```python
# tests/test_display_feeder.py
from controller.runtime.store import InMemoryStore
from controller.runtime.clock import ManualClock
from display import DisplayFeeder


class _FakeDisplay:
    def __init__(self): self.calls = []
    def display_status(self, i, s): self.calls.append(('status', i, s))
    def display_text(self, t): self.calls.append(('text', t))
    def clear_display(self): self.calls.append(('clear',))
    def display_splash(self): self.calls.append(('splash',))


def test_feeder_pushes_status_and_drains_display_queue():
    store = InMemoryStore(current={'P': {}}, status={'mode': 'Hold', 'units': 'F'})
    store.display_commands().push(('text', 'ERROR'))
    store.display_commands().push(('clear', None))
    disp = _FakeDisplay()
    DisplayFeeder(disp, store, ManualClock()).tick()
    assert ('status', {'P': {}}, {'mode': 'Hold', 'units': 'F'}) in disp.calls
    assert ('text', 'ERROR') in disp.calls
    assert ('clear',) in disp.calls
    assert disp.calls.index(('text', 'ERROR')) < disp.calls.index(('clear',))
```

- [ ] **Step 2: Run — FAIL.**  **Step 3: Implement** `display.py`:

```python
#!/usr/bin/env python3
"""PiFire Display Process — renders from Valkey, independent of the controller."""
from common import read_settings
from controller.runtime.devices import build_devices
from controller.runtime.store import ValkeyStore
from controller.runtime.clock import RealClock


class DisplayFeeder:
    def __init__(self, display, store, clock):
        self.display, self.store, self.clock = display, store, clock

    def tick(self):
        in_data = self.store.read_current()
        status = self.store.read_status()
        if in_data and status:
            self.display.display_status(in_data, status)
        for cmd, arg in self.store.display_commands().drain():
            if cmd == 'text':
                self.display.display_text(arg)
            elif cmd == 'clear':
                self.display.clear_display()
            elif cmd == 'splash':
                self.display.display_splash()

    def run(self):
        while True:
            self.tick()
            self.clock.sleep(0.1)


if __name__ == '__main__':
    settings = read_settings()
    _devices, display_device, _errors = build_devices(
        settings, include_display=True, errors=[], event_log=None, control_log=None)
    DisplayFeeder(display_device, ValkeyStore(), RealClock()).run()
```

- [ ] **Step 4: Run — PASS.**  **Step 5: Commit** `feat(display): add display.py process + DisplayFeeder`.

### Task 8.2: Remove display construction from the controller + supervisor config

**Files:** Modify `control.py` (`__main__`: stop constructing `display_device`); Create `auto-install/supervisor/display.conf`.

- [ ] **Step 1:** Delete dead display references in `control.py __main__` (`build_devices(..., include_display=False)` returns `None`). Confirm: `grep -n "display_device" control.py` → empty.
- [ ] **Step 2:** Create `auto-install/supervisor/display.conf`:

```ini
[program:display]
command=/usr/local/bin/pifire/.venv/bin/python /usr/local/bin/pifire/display.py
directory=/usr/local/bin/pifire
autostart=true
autorestart=true
startretries=3
stderr_logfile=/usr/local/bin/pifire/logs/display.err.log
stdout_logfile=/usr/local/bin/pifire/logs/display.out.log
```

- [ ] **Step 3:** `python -c "import control"` and `python -c "import display"` succeed; full suite green.
- [ ] **Step 4:** Manual verification note (not automatable here): on-device, run `control` and `display` as separate supervisor programs; confirm the display shows status, ERROR on a forced error, clears on Stop; confirm killing the display process leaves the controller running unaffected.
- [ ] **Step 5:** Commit `feat(display): run display as separate process; controller headless`.

---

## Phase 9 — End-to-end tier (real Valkey)

### Task 9.1: E2E scenarios via `ValkeyStore`

**Files:** Create `tests/e2e/__init__.py`, `tests/e2e/test_work_cycle_e2e.py`.

**Interfaces:** Reuses the characterization scenarios but builds the context with `ValkeyStore` + real `valkey-server` (gated by the availability check from Task 2.3) + fake devices + `ManualClock`.

- [ ] **Step 1:** Parametrize a subset (max-temp Error, flameout Reignite, flameout Error, Hold cycle, Prime elapse) against `ValkeyStore`. Before each: flush control/status/current + queues; seed settings/control/pellet via the store; run the mode handler; assert the same outcomes (final control mode, notifications via injected `FakeNotifier` — only the store is real).
- [ ] **Step 2:** Run — PASS with valkey, SKIP without (`python -m pytest tests/e2e -v`).
- [ ] **Step 3:** Confirm parity: same scenario asserts identically under `InMemoryStore` and `ValkeyStore`. Any divergence exposes an `InMemoryStore` bug — fix the fake, not the test.
- [ ] **Step 4:** Document run instructions in the module docstring (`valkey-server` on localhost:6379; `pytest tests/e2e`).
- [ ] **Step 5:** Commit `test(control): end-to-end work-cycle tests against real Valkey`.

---

## Phase 10 — Slim entry point + docs

### Task 10.1: Reduce `control.py` to a thin entry point

**Files:** Modify `control.py`.

**Interfaces:** `control.py __main__` becomes: read settings, set up loggers, flush datastore, `build_devices(include_display=False)`, assemble `ControllerContext(ValkeyStore, ValkeyNotifier, RealClock, ...)`, register `atexit` handler, `Controller(ctx).run()`. Target ~80 lines.

- [ ] **Step 1:** Move remaining helpers (`exit_handler`, boot-to-monitor init, datastore flush) into `Controller` or keep minimal in `control.py`. Delete dead code (`_start_fan`, `_process_system_commands`, `_init_controller` already relocated/removed).
- [ ] **Step 2:** `python -c "import control"`; full suite + characterization green.
- [ ] **Step 3:** `wc -l control.py` (dramatically reduced); `grep -n "def _work_cycle\|def _recipe_mode\|def _next_mode" control.py` → empty.
- [ ] **Step 4:** Add `controller/runtime/README.md`: two-process model, `control:displayq`, and `ThreadedControllerRunner` as the documented follow-up.
- [ ] **Step 5:** Commit `refactor(control): slim control.py to a thin entry point`.

---

## Self-Review (completed during authoring)

- **Spec coverage:** Two processes (Ph 8), display optional/headless (Ph 8.2), `control:displayq` (Ph 3.2 producer, Ph 8.1 consumer), `WriteKind` required + global sweep (Ph 1), Store seam + in-memory + real-Valkey E2E (Ph 2, 9), context injection (Ph 3.2, 4), mode-handler decomposition + per-mode safety + status fragments + `WorkCycleState` (Ph 7), pure-logic modules (Ph 6), `ControllerRunner` sync + threaded-as-followup + neutral naming (Ph 5), golden-master-before-decompose (Ph 3 gates Ph 5-7), build-sequence order (Phases mirror spec steps 1-10). No gaps.
- **Placeholder scan:** No TBD/TODO; every code step shows full code. Mode-body relocations in Task 7.3 cite exact source line ranges rather than re-transcribing 870 lines — deliberate: the source is authoritative and the characterization suite guarantees equivalence; transcription would risk silent drift.
- **Type consistency:** `WriteKind`, `Store`, `Queue.drain()`, `NormalizedOutput(cycle_ratio, fan)`, `CycleTimes`, `SafetyVerdict`, `ControllerContext`/`Devices`, `WorkCycleState`, `ControllerRunner` (`control_period`, `reconfigure`, `controller_state`) used consistently across tasks.
- **Known ordering dependency:** Task 3.2's `__main__` imports `ValkeyNotifier` (Task 4.1) — noted inline; implement 4.1 first or inline a temporary shim.
