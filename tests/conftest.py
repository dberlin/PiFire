import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from unittest import mock

import pytest

# Ensure the repository root is importable so `grillplat`, `common`, etc. resolve.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Must be set before any test module imports Qt/PySide6.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Must be set before numpy imports its BLAS backend, which reads these once.
#
# The controller suites solve thousands of small nonlinear programs -- a 24-step
# horizon over a handful of states -- and a matrix that size is far too small to
# repay starting a thread per operation. Left alone the run burns 711 s of CPU
# across 5.2 threads to do 126 s of work, and takes LONGER on the wall for it
# (137.7 s against 127.2 s measured over tests/unit/mpc + tests/unit/controller).
# So this is not a CPU-for-wall-clock trade: one thread is cheaper on both.
#
# setdefault, so a run that wants to measure the threaded behaviour can still
# ask for it from the environment.
for _blas_threads in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_blas_threads, "1")

# Must be set before `common.datastore` is imported: it resolves DB_PATH at
# IMPORT time (`os.environ.get("PIFIRE_DB_PATH", <repo>/pifire.db)`) and caches
# it as _ORIGINAL_DB_PATH, which `_reset_for_tests(None)` restores to.
#
# Without this, merely COLLECTING the suite creates a real pifire.db in the
# repo root: many test modules do `from app import app` at module scope, and
# app.py's boot wiring initialises the datastore on import -- before any
# fixture can redirect it. (Proven: `pytest --collect-only` alone created it.)
# The per-test `ds` / `live_server` fixtures still redirect as before; this
# only moves the *default* off the repo so an import-time touch is harmless.
_xdist_worker = os.environ.get("PYTEST_XDIST_WORKER")
if _xdist_worker:
    # The coordinator sets this before spawning workers, so setdefault() would
    # make every worker collect against one SQLite file and race datastore.init().
    os.environ["PIFIRE_DB_PATH"] = os.path.join(
        tempfile.mkdtemp(prefix=f"pifire-test-db-{_xdist_worker}-"),
        "pifire.db",
    )
else:
    os.environ.setdefault(
        "PIFIRE_DB_PATH",
        os.path.join(tempfile.mkdtemp(prefix="pifire-test-db-"), "pifire.db"),
    )

# Must be set before `common.common` is imported, for the same reason as
# PIFIRE_DB_PATH above: it resolves LOG_DIR at IMPORT time, and test modules
# that do `from app import app` at module scope build their loggers during
# COLLECTION -- before any fixture could redirect them.
#
# Without this the suite appended to the operator's real ./logs/events.log.
# That is why that file carried fixture strings ("Admin: Shutdown failed: boom",
# "[nonexistent_probe_module_xyz]", a WLED connection to 127.0.0.1:1) in the
# content the log viewer shows a user, and why the log files disagreed with the
# `logs` table -- tests already used a temporary database, but not temporary
# log files.
if _xdist_worker:
    os.environ["PIFIRE_LOG_DIR"] = tempfile.mkdtemp(prefix=f"pifire-test-logs-{_xdist_worker}-")
else:
    os.environ.setdefault("PIFIRE_LOG_DIR", tempfile.mkdtemp(prefix="pifire-test-logs-"))

"""
==============================================================================
 Power-action guard
==============================================================================

Installed at IMPORT time, like PIFIRE_DB_PATH and PIFIRE_LOG_DIR above and for
the same reason: it has to be in place before any test module imports anything,
and it has to stay in place for the whole session -- including for daemon
threads that outlive the test that started them.

A test run really did reboot this machine. `sudo supervisorctl restart all`,
`sudo systemctl reboot` and `sudo systemctl poweroff` all executed, five seconds
apart, and the suite reported nothing: the calls happen on daemon threads, so
the exceptions that would have surfaced them were swallowed with the thread.

Three things have to line up for that, and all three did:

  - `real_hw` was True in a fresh test datastore, so it claimed to be a real
    appliance and `is_real_hardware()` -- the gate in front of every lifecycle
    call in common/system.py -- passed. (Now seeded False; see the section
    below. That is the other layer, and the weaker one.)
  - The installers grant NOPASSWD sudo for exactly these commands, so on an
    installed Linux box they SUCCEED. On a developer machine without PiFire
    installed they fail harmlessly, which is why this stayed invisible.
  - The calls moved from `os.system("... sudo reboot &")` to `subprocess.run`,
    walking past harnesses that had patched os.system for years.

So the guard is on the PRIMITIVE, not on the named functions. Patching
`common.system.reboot_system` is defeated by any module that did
`from common.system import reboot_system` at import time, and patching
os.system was defeated by moving the call to subprocess -- that exact class of
escape is what has now bitten repeatedly. Every one of these paths ends at
`subprocess.Popen` or `os.system`, and code cannot be moved out from under
those without leaving Python entirely.

`subprocess.run`/`call`/`check_call`/`check_output` all construct a Popen, so
guarding Popen covers them too. os.spawn*/os.exec* are not used anywhere in the
tree; add them here if that changes.
"""

#: Programs that can power down the machine or bounce the services a developer
#: is working on. common/system.py is the only module in the tree that runs any
#: of them -- everything else naming them is a comment or a type literal -- so
#: blocking the PROGRAM rather than a program+subcommand pair costs nothing and
#: leaves no gap for `supervisorctl stop` to slip through.
_POWER_PROGRAMS = frozenset({"reboot", "poweroff", "halt", "shutdown", "systemctl", "supervisorctl"})

#: Attempts recorded even when the raise below is swallowed -- which is the
#: normal case here, since common/system.py runs these on daemon threads.
_power_attempts = []
_power_attempts_lock = threading.Lock()
_current_test = None

_real_popen = subprocess.Popen
_real_os_system = os.system
_real_thread_start = threading.Thread.start


def _stamped_thread_start(self):
    """Remember which test started each thread.

    Without this, a refusal is reported against whatever test happened to be
    running when the thread fired -- and common/system.py's threads sleep for
    seconds before acting, so that is reliably NOT the test that started them.
    Chasing one of these without the origin means bisecting the suite.
    """
    self._pf_origin_test = _current_test
    return _real_thread_start(self)


threading.Thread.start = _stamped_thread_start


def _command_tokens(command):
    """Program basenames in `command`, whether it is an argv list or a shell string."""
    if isinstance(command, bytes):
        command = command.decode("utf-8", "replace")
    if isinstance(command, str):
        try:
            parts = shlex.split(command)
        except ValueError:  # unbalanced quotes -- fall back to a crude split
            parts = command.split()
    elif isinstance(command, (list, tuple)):
        parts = [p.decode("utf-8", "replace") if isinstance(p, bytes) else str(p) for p in command]
    else:
        return []
    return [os.path.basename(p) for p in parts]


def _refuse_power_action(seam, command):
    """Record the attempt, then raise. Both, because either alone is not enough.

    The raise fails the test when the call is on the main thread. The record is
    what catches it when it is not: a daemon thread's exception goes to stderr
    and the test passes regardless, which is precisely how a reboot got through
    a green suite.
    """
    thread = threading.current_thread()
    # The test that started this THREAD, not the one running now -- those differ
    # whenever the call came off common/system.py's delayed daemon threads.
    origin = getattr(thread, "_pf_origin_test", None) or _current_test
    with _power_attempts_lock:
        _power_attempts.append((origin, thread.name, seam, command))
    raise AssertionError(
        f"BLOCKED: {seam} tried to run a power action: {command!r}\n"
        "Nothing in the test suite may restart or power down the machine. Patch the "
        "seam the code under test actually calls -- and patch where the name is BOUND, "
        "not on common.system, if the caller imported it at module level."
    )


def _guarded_popen(command, *args, **kwargs):
    if set(_command_tokens(command)) & _POWER_PROGRAMS:
        _refuse_power_action("subprocess.Popen", command)
    return _real_popen(command, *args, **kwargs)


def _guarded_os_system(command):
    if set(_command_tokens(command)) & _POWER_PROGRAMS:
        _refuse_power_action("os.system", command)
    return _real_os_system(command)


subprocess.Popen = _guarded_popen
os.system = _guarded_os_system


@pytest.fixture
def power_guard():
    """For the guard's OWN tests, and nothing else.

    Two things a test of this guard needs that no other test may have:

    `drain()` takes the recorded attempts and clears them, so a test that
    deliberately triggers one is not then failed by the autouse fixture below.

    `executed` is filled INSTEAD of running anything: the real primitives are
    swapped for recorders for the duration. That is what makes it safe to point
    the real `reboot_system()` at a machine and check it is refused -- if the
    guard were broken, the call reaches a list rather than systemd.
    """

    class _Guard:
        def __init__(self):
            self.executed = []

        def drain(self):
            with _power_attempts_lock:
                attempts = list(_power_attempts)
                _power_attempts.clear()
            return attempts

        def executed_power_commands(self):
            """The entries in `executed` that are power actions.

            The recorders replace the process-wide primitives, so `executed`
            also collects whatever unrelated subprocesses other threads happen
            to run while the fixture is active -- under xdist a worker picks up
            `ip link` and `ifconfig` from the network probe. Asserting on the
            whole list fails a test of the guard on someone else's traffic.
            """
            return [command for command in self.executed if _POWER_PROGRAMS & set(_command_tokens(command))]

    guard = _Guard()
    globals_ = _guarded_popen.__globals__
    # Held in locals, NOT read back from the module on the way out: these names
    # are the very globals being replaced, so restoring from them would put the
    # recorders back permanently and every later subprocess in the session would
    # get a Mock instead of a process.
    saved_popen = globals_["_real_popen"]
    saved_os_system = globals_["_real_os_system"]

    def _record_popen(command, *args, **kwargs):
        guard.executed.append(command)
        return mock.Mock()

    def _record_os_system(command):
        guard.executed.append(command)
        return 0

    globals_["_real_popen"] = _record_popen
    globals_["_real_os_system"] = _record_os_system
    try:
        yield guard
    finally:
        globals_["_real_popen"] = saved_popen
        globals_["_real_os_system"] = saved_os_system
        guard.drain()


@pytest.fixture(autouse=True)
def _no_power_actions(request):
    """Fail any test that reached a power action, including from a thread.

    A refused call that happened on a daemon thread cannot fail the test by
    raising, so it is reported here instead. The grace period in
    `_restart_supervisor_programs` means such a thread can fire several seconds
    after the test that started it returned -- so the attempt is reported with
    the test that was running when it was MADE, which may not be this one.
    """
    global _current_test
    _current_test = request.node.nodeid
    with _power_attempts_lock:
        _power_attempts.clear()
    yield
    with _power_attempts_lock:
        attempts = list(_power_attempts)
        _power_attempts.clear()
    if attempts:
        detail = "\n".join(
            f"  {seam} {command!r} (thread {thread}, during {test})" for test, thread, seam, command in attempts
        )
        raise AssertionError(f"power action(s) attempted during this test:\n{detail}")


"""
==============================================================================
 A test datastore is not a real appliance
==============================================================================

`real_hw` answers one question -- "is there a grill on the other end of these
pins?" -- and for the test suite the answer is always no. It is True in
common/defaults.py because that is the right answer for the thing the wizard is
about to configure, and the wizard offers the value it finds in settings, so
flipping the shipped default would leave a first-time install on a Raspberry Pi
looking at "Real Hardware: No (Test Only)" pre-selected. The place the two
answers diverge is here.

This is the FIRST of the two layers in front of common/system.py, and the
weaker one: it short-circuits `is_real_hardware()` so the lifecycle calls are
never reached, whereas the power guard above catches them at the primitive no
matter how they got there. Neither replaces the other -- a test that wants the
real-hardware branch (tests/unit/system/test_system_lifecycle.py patches
`is_real_hardware` in ~20 places) walks straight past this one, and the guard is
what makes doing that safe.

The rebind is on `common.defaults.default_settings`, so every fresh datastore
built after this point gets it, including the ones that later tests mint with
`datastore._reset_for_tests()`. The sys.modules sweep covers what that misses:
`from common.defaults import default_settings` binds the function OBJECT, so a
module that imported it BEFORE this ran keeps the original -- the same
import-time escape that defeated the os.system patch for years. (The sweep
reaches `common.defaults` itself, since it is in sys.modules too; the explicit
rebind above is what the sweep is checking against, and says the intent
plainly.) Both are pinned by
tests/unit/system/test_tests_are_not_real_hardware.py, which fails on nine
tests when they are removed.
"""

from common import defaults as _defaults  # noqa: E402

shipped_default_settings = _defaults.default_settings


def _prototype_default_settings():
    settings = shipped_default_settings()
    settings["platform"]["real_hw"] = False
    return settings


_defaults.default_settings = _prototype_default_settings

for _module in list(sys.modules.values()):
    if getattr(_module, "default_settings", None) is shipped_default_settings:
        setattr(_module, "default_settings", _prototype_default_settings)  # noqa: B010

from common import datastore  # noqa: E402

# Repo root, used by tests that need to locate files (e.g. wizard/, display/)
# relative to the project rather than relative to the test file.
REPO_BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


# Removed: the session-scoped `_os_info_cache_off_repo` fixture. It rebound
# `get_os_info` on common.system and grillplat.system_commands to redirect a
# `filepath` default, so the suite would stop dropping an os_info.json in the
# repo root. That parameter is long gone -- the cache moved into the datastore
# -- so the shim was not redirecting anything; it was passing a tmp file path
# as `loggername` to every module-attribute call in the session. A harness that
# silently reshapes production behaviour is worse than the artifact it was
# guarding against, and there is no artifact any more.
# Pinned by tests/unit/common/test_os_info_read_path_is_pure.py.


def load_wizard_manifest():
    """Load and parse wizard/wizard_manifest.json from the repo root."""
    with open(os.path.join(REPO_BASE, "wizard", "wizard_manifest.json")) as f:
        return json.load(f)


def manifest_config_default(entry, option_name):
    """Return the default value of `option_name` within a manifest entry's config list."""
    for opt in entry["config"]:
        if opt["option_name"] == option_name:
            return opt["default"]
    raise AssertionError(f"{option_name} not in config")


# The resolution-agnostic source layout that every generated DSI display
# layout (display/dsi_<res>.json) is derived from.
DSI_LAYOUT_SRC = os.path.join(REPO_BASE, "display", "dsi_800x480t.json")


def load_json(path):
    """Load and parse a JSON file at `path`."""
    with open(path) as f:
        return json.load(f)


# Repo root (as a Path) and the QtQuick QML source tree, shared by the
# QtQuick component/screen tests below.
REPO = Path(__file__).resolve().parents[1]
QML_DIR = REPO / "display" / "qml"


@pytest.fixture
def qml_engine():
    """A QQmlApplicationEngine with the display/qml import path already added.

    Ensures a QGuiApplication exists first (QQmlApplicationEngine requires
    one), then returns a fresh engine ready for loading QML components.
    """
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine

    QGuiApplication.instance() or QGuiApplication([])
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR))
    return engine


@pytest.fixture
def ds(tmp_path):
    datastore._reset_for_tests(str(tmp_path / "t.db"))
    datastore.init()
    yield datastore
    datastore._reset_for_tests(None)


@pytest.fixture
def oracle():
    """Loader for tests/oracle/fixtures/<name>.json oracle files."""

    def _load(name):
        path = os.path.join(os.path.dirname(__file__), "oracle", "fixtures", f"{name}.json")
        return json.load(open(path))

    return _load


@pytest.fixture
def x86_platform():
    """A GrillPlatform (x86_numato) with all hardware mocked out.

    No `frequency` key is set in config, so GrillPlatform falls back to its
    class default -- tests relying on that default behavior depend on this.
    """
    import grillplat.x86_numato as mod

    with (
        mock.patch.object(mod, "NumatoUSBRelay"),
        mock.patch.object(mod, "EMC2101_LUT"),
        mock.patch.object(mod, "EMC2301"),
        mock.patch.object(mod, "open_i2c_bus"),
    ):
        config = {"outputs": {"power": 0, "igniter": 1, "auger": 2, "fan": 3}}
        yield mod.GrillPlatform(config)
