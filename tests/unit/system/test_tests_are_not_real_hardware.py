"""The suite must never claim to be running on a grill.

`is_real_hardware()` is the gate in front of every lifecycle call in
common/system.py -- the supervisor restarts, the reboot, the poweroff. It reads
`settings["platform"]["real_hw"]`, which is True in the shipped defaults, and
that is what made the reboot incident reachable: a fresh test datastore
presented itself as a real appliance, so the gate opened.

tests/conftest.py rebinds `common.defaults.default_settings` to answer False,
which is the first of two layers. The second is the power guard, which catches
the calls at `subprocess.Popen`/`os.system` regardless. This file pins the
first one, and pins the two things that make it correct rather than merely
convenient:

  * the SHIPPED default is still True, so a real install is unaffected and the
    wizard still offers "Yes" pre-selected (it renders whatever settings hold);
  * every module-level `from common.defaults import default_settings` binding
    points at the test version -- binding the function OBJECT at import time is
    exactly how patching os.system was defeated for years.

Deliberately NOT asserted here: that the True branch is unreachable. It is
reachable on purpose -- test_system_lifecycle.py patches `is_real_hardware` to
True in about twenty places to exercise the real paths, and the power guard is
what makes that safe.
"""

import sys
import threading

import pytest

import common.system as cs
from common import datastore, defaults
from common.persistence import runtime as runtime_persistence
from tests import conftest


def test_the_shipped_default_is_still_true():
    """The production answer, read from the unpatched function conftest saved.

    Without this the rest of the file proves nothing interesting: a repo-wide
    flip of common/defaults.py would satisfy every other assertion here while
    silently changing what a first-boot appliance does.
    """
    shipped = conftest.shipped_default_settings()

    assert shipped["platform"]["real_hw"] is True, (
        "the shipped default changed; a fresh install would now configure itself as "
        "a test environment, and the wizard would pre-select 'No (Test Only)'"
    )


def test_the_test_default_is_false():
    assert defaults.default_settings()["platform"]["real_hw"] is False


def test_a_fresh_datastore_is_not_real_hardware(ds):
    """The end of the path that matters: what the gate actually reads."""
    assert runtime_persistence.read_settings()["platform"]["real_hw"] is False
    assert cs.is_real_hardware() is False


def test_a_datastore_reset_mid_session_stays_false(tmp_path):
    """`_reset_for_tests` + `init()` re-seeds settings from defaults.

    Test modules mint datastores throughout the session, so an override that
    only stamped the first one would leave every later test back on the
    dangerous answer.
    """
    datastore._reset_for_tests(str(tmp_path / "second.db"))
    try:
        datastore.init()
        assert cs.is_real_hardware() is False
    finally:
        datastore._reset_for_tests(None)


def test_no_module_still_holds_the_shipped_default_settings():
    """`from common.defaults import default_settings` binds the object, not the name.

    A module imported before conftest's rebind keeps the original function and
    goes on seeding real_hw=True from it. That is the same import-time escape
    that made patching `common.system.reboot_system` useless, so it is checked
    rather than assumed.
    """
    stale = [
        name
        for name, module in list(sys.modules.items())
        if getattr(module, "default_settings", None) is conftest.shipped_default_settings
    ]

    assert stale == [], f"these modules bound the shipped default_settings before conftest rebound it: {stale}"


@pytest.fixture
def started_threads(monkeypatch):
    """Every thread common/system.py starts, and a join for whatever it started.

    The gate is the FIRST thing each lifecycle call does, and handing the work
    to a daemon thread is what it does next -- so "no thread was created" is the
    exact statement that the gate stayed shut, and unlike the power guard's
    recording it is true the instant the call returns rather than three seconds
    later. (Asserting only on the recording made this test pass with the whole
    override removed: the reboot thread had not woken up yet.)

    Joining on the way out matters for the failing case. A leaked thread fires
    during whatever test is running by then and fails that one instead.
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
    monkeypatch.setattr(cs, "RESTART_GRACE_SECONDS", 0)
    monkeypatch.setattr(cs.time, "sleep", lambda _seconds: None)
    yield captured
    for thread in captured:
        thread.join(10.0)


@pytest.mark.parametrize(
    "call",
    ["restart_control", "restart_webapp", "restart_scripts", "reboot_system", "shutdown_system"],
)
def test_every_lifecycle_call_is_a_no_op_by_default(call, power_guard, started_threads, ds):
    """No patching of the gate: `ds` is a fresh datastore, nothing more.

    `power_guard` swaps the primitives for recorders, so if the gate DID open
    this asserts on the recording instead of restarting the machine.
    """
    getattr(cs, call)()

    assert started_threads == [], f"{call} started its worker, so the real_hw gate let it through"
    assert power_guard.executed_power_commands() == [], f"{call} ran a command with real_hw False"
    assert power_guard.drain() == [], f"{call} reached the power guard, so the real_hw gate let it through"


def test_the_wizard_still_asks_every_platform():
    """A completed wizard is what writes True on a real install.

    The code default is only reachable by an install that never finished the
    wizard, so the question has to exist on every platform -- three of them were
    missing it, and inherited whatever the default happened to be.
    """
    manifest = conftest.load_wizard_manifest()
    platforms = manifest["modules"]["grillplatform"]

    missing = sorted(name for name, entry in platforms.items() if "real_hw" not in entry["settings_dependencies"])

    assert missing == [], f"these platforms never ask about real hardware: {missing}"

    for name, entry in platforms.items():
        options = entry["settings_dependencies"]["real_hw"]["options"]
        assert set(options) == {"True", "False"}, f"{name} offers {sorted(options)}"
