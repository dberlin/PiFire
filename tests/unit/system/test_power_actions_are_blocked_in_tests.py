"""The suite may not restart or power down the machine it runs on.

This is a regression test for an incident, not a hypothetical. A full test run
executed all of these, five seconds apart, and reported nothing:

    sudo /usr/bin/supervisorctl restart all
    sudo /usr/sbin/systemctl reboot
    sudo /usr/sbin/systemctl poweroff

Everything needed for that lined up at once. `real_hw` defaults to True, so
`is_real_hardware()` -- the gate in front of every lifecycle call in
common/system.py -- passes in a fresh test datastore. The installers grant
NOPASSWD sudo for exactly these commands, so on an installed Linux box they
succeed rather than failing the way they do on an uninstalled developer
machine. And common/system.py runs them on daemon threads, so no exception
could reach the test even in principle.

The guard lives in tests/conftest.py and wraps `subprocess.Popen` and
`os.system`, which is where every one of these paths ends. It is deliberately
NOT a patch of `reboot_system` and friends: those get imported by name into
other modules, so patching them where they are DEFINED is looked straight past
by any caller that bound them at import time -- and patching `os.system` was
already defeated once, when the calls moved to `subprocess`.

Every test below runs with the real primitives swapped for recorders (the
`power_guard` fixture), so a regression in the guard fails these tests instead
of taking the machine down with it.
"""

import os
import subprocess
import threading

import pytest

import common.system as cs

#: Verbatim from the sudo log of the incident, plus the two shell forms the
#: module still falls back to.
INCIDENT_COMMANDS = [
    ["sudo", "/usr/bin/supervisorctl", "restart", "all"],
    ["sudo", "/usr/sbin/systemctl", "reboot"],
    ["sudo", "/usr/sbin/systemctl", "poweroff"],
    ["sudo", "supervisorctl", "restart", "webapp"],
    ["sudo", "systemctl", "restart", "supervisord"],
    ["sudo", "reboot"],
    ["sudo", "shutdown", "-h", "now"],
]


def test_the_guard_is_installed_on_both_primitives():
    """If either is the stock implementation, nothing below proves anything."""
    assert getattr(subprocess.Popen, "__name__", "") == "_guarded_popen", (
        "subprocess.Popen is not the guarded wrapper; tests/conftest.py did not install it"
    )
    assert getattr(os.system, "__name__", "") == "_guarded_os_system", (
        "os.system is not the guarded wrapper; tests/conftest.py did not install it"
    )


@pytest.mark.parametrize("command", INCIDENT_COMMANDS, ids=lambda c: " ".join(c[1:]))
def test_each_command_from_the_incident_is_refused(command, power_guard):
    with pytest.raises(AssertionError, match="power action"):
        subprocess.Popen(command)

    assert power_guard.executed == [], "the command reached the real primitive"
    assert len(power_guard.drain()) == 1


@pytest.mark.parametrize("command", INCIDENT_COMMANDS, ids=lambda c: " ".join(c[1:]))
def test_the_same_commands_are_refused_as_shell_strings(command, power_guard):
    """os.system takes a shell string, and the old code paths still build them."""
    with pytest.raises(AssertionError, match="power action"):
        os.system(" ".join(command))

    assert power_guard.executed == []
    assert len(power_guard.drain()) == 1


def test_a_backgrounded_shell_string_is_still_refused(power_guard):
    """The form the tree used for years: a sleep, an && and a trailing &."""
    with pytest.raises(AssertionError, match="power action"):
        os.system("sleep 3 && sudo supervisorctl restart webapp &")

    assert power_guard.executed == []
    power_guard.drain()


def test_harmless_subprocesses_still_run():
    """The guard blocks power actions, not subprocesses.

    Without this, a guard that refused everything would pass every test above
    while breaking the parts of the suite that legitimately shell out.
    """
    assert subprocess.run(["/bin/echo", "ok"], capture_output=True, text=True, check=False).stdout.strip() == "ok"


def test_a_command_merely_mentioning_a_power_word_still_runs():
    """Matching is on the program, not on the text anywhere in the argv."""
    result = subprocess.run(["/bin/echo", "reboot the grill"], capture_output=True, text=True, check=False)

    assert result.stdout.strip() == "reboot the grill"


# ---------------------------------------------------------------------------
# The real lifecycle functions, driven exactly as production drives them.
# ---------------------------------------------------------------------------


def _real_hardware(monkeypatch):
    """Make the gate PASS, which is its state in a fresh test datastore.

    `real_hw` defaults to True, so this is not a contrived configuration -- it
    is what every test in the suite runs with unless it says otherwise, and it
    is why the incident was reachable at all.
    """
    monkeypatch.setattr(cs, "is_real_hardware", lambda settings=None: True)


@pytest.mark.parametrize("call", ["restart_control", "restart_webapp", "restart_scripts"])
def test_supervisor_restarts_are_refused_on_a_waiting_caller(call, power_guard, monkeypatch):
    _real_hardware(monkeypatch)

    # wait=True runs inline, so the refusal surfaces here rather than on a thread.
    getattr(cs, call)(wait=True)

    assert power_guard.executed == [], f"{call} reached the real primitive"
    assert power_guard.drain(), f"{call} was not refused"


@pytest.fixture
def joined_threads(monkeypatch):
    """Capture the daemon threads common/system.py starts, so they can be joined.

    These functions hand their work to a thread they never return, and the work
    begins with a sleep. A test that merely polled for the result would return
    as soon as the FIRST refusal landed and leave the rest of that thread
    running -- to fire seconds later, during whatever unrelated test happened to
    be running by then, and fail that one instead. (That is not hypothetical: it
    is what an earlier version of this file did.)

    `common/system.py` does `import threading` and calls `threading.Thread`, so
    rebinding the module reference ON common.system captures its threads without
    touching threading for anything else in the process.
    """
    captured = []
    real_thread = threading.Thread

    class _Shim:
        @staticmethod
        def Thread(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            captured.append(thread)
            return thread

    monkeypatch.setattr(cs, "threading", _Shim)

    def _join(timeout=10.0):
        for thread in captured:
            thread.join(timeout)
            assert not thread.is_alive(), "a power-action thread did not finish"
        return captured

    yield _join


@pytest.mark.parametrize("call", ["restart_control", "restart_webapp", "restart_scripts"])
def test_supervisor_restarts_are_refused_on_the_daemon_thread_path(call, power_guard, joined_threads, monkeypatch):
    """The default path, and the one the incident took.

    The refusal cannot reach the caller here -- it is raised on a daemon thread
    whose exception goes to stderr -- so what this asserts is that nothing
    EXECUTED, and that the attempt was recorded. The recording is the only
    reason a run like this can fail a test at all.
    """
    _real_hardware(monkeypatch)
    monkeypatch.setattr(cs, "RESTART_GRACE_SECONDS", 0)

    getattr(cs, call)()
    joined_threads()

    assert power_guard.executed == [], f"{call} reached the real primitive"
    assert power_guard.drain(), f"{call} was not refused"


@pytest.mark.parametrize("call", ["reboot_system", "shutdown_system"])
def test_power_off_paths_are_refused(call, power_guard, joined_threads, monkeypatch):
    """reboot_system and shutdown_system, including their os.system fallbacks.

    Each tries subprocess first and falls back to os.system when that raises, so
    BOTH seams are exercised by one call -- and both have to be refused.
    """
    _real_hardware(monkeypatch)
    monkeypatch.setattr(cs.time, "sleep", lambda _seconds: None)

    getattr(cs, call)()
    joined_threads()

    assert power_guard.executed == [], f"{call} reached the real primitive"
    seams = {seam for _test, _thread, seam, _command in power_guard.drain()}
    assert seams == {"subprocess.Popen", "os.system"}, (
        f"{call} did not reach both seams; the os.system fallback is the one that used to escape"
    )
