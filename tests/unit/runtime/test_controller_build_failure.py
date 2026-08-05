"""A controller that will not build must never take the control process down.

THE REGRESSION: do-mpc moved out of [project.dependencies] into the optional
`mpc` extra, so `uv sync --no-dev` -- what every installer runs -- no longer
installs it. controller/mpc.py imports do_mpc lazily inside __init__
(`_build_nlp`), so `controller.mpc` still IMPORTS fine and the failure lands in
the CONSTRUCTOR, which used to sit outside _build_core's try/except. The
ModuleNotFoundError propagated build_runner -> HoldMode.setup() ->
Controller.run() with nothing catching it: the control process died with the
auger already commanded on, supervisor restarted it, control was flushed to
Stop, and the cook ended with the fire out.

These tests simulate the missing package instead of uninstalling it -- a None
entry in sys.modules makes `import do_mpc` raise exactly as an absent package
does -- and assert the loop survives AND says something useful. They fail
against the pre-fix runner (ModuleNotFoundError escapes build_runner).

Nothing here shells out: no subprocess, no os.system, no install is triggered.
The control loop deliberately does NOT install anything -- see the module
docstring of common/controller_deps.py.
"""

import sys

import pytest

import tests.characterization.harness as harness  # noqa: F401  binds control.eventLogger
from common.common import ErrorKind
from common.datastore_accessors import read_errors
from common.control_trace import ControllerType
from controller.runtime.runner import (
    SyncControllerRunner,
    _build_core,
    build_runner,
)
from tests.characterization.fixtures import base_control, base_pellet_db, base_settings
from tests.characterization.harness import run_mode
from tests.fakes.grill import FakeGrillPlatform
from tests.fakes.probes import FakeProbes


@pytest.fixture
def no_do_mpc(monkeypatch):
    """Make `import do_mpc` fail without uninstalling anything.

    A None entry in sys.modules is CPython's documented "this import is
    blocked" marker, so every lazy `import do_mpc` in controller/mpc.py and
    controller/mpc_model.py raises, exactly as it would on a Pi that ran
    `uv sync --no-dev`. monkeypatch restores the real entry afterwards.
    """
    monkeypatch.setitem(sys.modules, "do_mpc", None)


def _mpc_settings():
    settings = base_settings()
    settings["controller"]["selected"] = "mpc"
    return settings


def _control():
    return {"primary_setpoint": 225}


class _Logger:
    def __init__(self):
        self.exceptions = []
        self.errors = []

    def exception(self, msg):
        self.exceptions.append(msg)

    def error(self, msg):
        self.errors.append(msg)


# --- the seam: _build_core must swallow a constructor failure ---------------


def test_build_core_returns_inactive_instead_of_raising(no_do_mpc, ds):
    logger = _Logger()

    core, status = _build_core(_mpc_settings(), _control(), logger=logger)

    assert core is None
    assert status == "Inactive"
    assert any("[mpc] controller" in msg for msg in logger.exceptions)


def test_build_core_does_not_need_a_logger_to_survive(no_do_mpc, ds):
    # The control loop calls this with logger=None in places; a missing logger
    # must not turn a handled failure back into a crash.
    assert _build_core(_mpc_settings(), _control()) == (None, "Inactive")


# --- build_runner substitutes the default controller -----------------------


def test_build_runner_falls_back_to_pid_and_keeps_controlling(no_do_mpc, ds):
    logger = _Logger()

    runner, status = build_runner(_mpc_settings(), _control(), logger=logger)

    # Not None: the grill is still regulated. This is the whole point -- a user
    # mid-cook must not lose control of their fire over a missing package.
    assert runner is not None
    assert status == "Active"
    assert isinstance(runner, SyncControllerRunner)  # pid is synchronous
    assert runner.controller_type() is ControllerType.PID
    # And it genuinely works: a temperature in gives a cycle ratio out. (The raw
    # value is unclamped -- HoldMode applies cycle_data's u_min/u_max -- so this
    # pins that a real number comes back, not the band.)
    runner.set_target(225)
    output = runner.latest_from(200.0)
    assert isinstance(output.cycle_ratio, float)
    assert runner.controller_state()["set_point"] == 225


def test_build_runner_tells_the_user_what_happened(no_do_mpc, ds):
    build_runner(_mpc_settings(), _control(), logger=_Logger())

    banner = "\n".join(read_errors(ErrorKind.CONTROL))
    assert "[mpc] controller could not be started" in banner
    # Actionable: names the package, and says how to fix it.
    assert "do_mpc" in banner
    assert "Settings > Controller" in banner
    # Honest about what is running the fire right now.
    assert "[pid] controller instead" in banner
    assert "selection has not been changed" in banner


def test_build_runner_does_not_rewrite_the_users_controller_choice(no_do_mpc, ds):
    settings = _mpc_settings()
    build_runner(settings, _control(), logger=_Logger())
    # Preserved so that re-saving it once do-mpc is installed just works.
    assert settings["controller"]["selected"] == "mpc"


def test_a_healthy_mpc_selection_is_not_substituted(ds):
    # Guard against the fallback becoming a silent always-on downgrade: with
    # do_mpc present, MPC must actually be built (and it wants the threaded
    # runner, unlike pid).
    settings = _mpc_settings()
    runner, status = build_runner(settings, _control(), logger=_Logger())
    try:
        assert status == "Active"
        assert runner.wants_async() is True
        assert runner.controller_type() is ControllerType.MPC
    finally:
        if runner is not None:
            runner.stop()


# --- mid-cook switch: keep the controller that is already running ----------


def test_reconfigure_to_a_broken_controller_keeps_the_previous_core(no_do_mpc, ds):
    """Selecting MPC mid-cook must be a no-op, not a loss of control.

    HoldMode.on_tick calls reconfigure() when control['controller_update'] is
    set. Before the fix this raised out of on_tick and killed the loop while the
    grill was lit and holding.
    """

    class _Core:
        def __init__(self):
            self.targets = []

        def set_target(self, value):
            self.targets.append(value)

        def update(self, temp):
            return 0.42

        def get_control_period(self):
            return 20

        def commands_fan(self):
            return False

        def wants_async(self):
            return False

    previous = _Core()
    runner = SyncControllerRunner(previous, controller_type=ControllerType.PID)
    logger = _Logger()

    status = runner.reconfigure(_mpc_settings(), _control(), logger=logger)

    assert status == "Inactive"
    assert runner._core is previous  # still the controller that was running
    assert runner.controller_type() is ControllerType.PID
    assert runner.latest_from(200.0).cycle_ratio == 0.42  # and still controlling
    banner = "\n".join(read_errors(ErrorKind.CONTROL))
    assert "Could not switch to the [mpc] controller" in banner
    assert "previous controller is still running your cook" in banner


# --- end to end: a Hold cycle survives and controls the grill --------------


def test_hold_cycle_survives_mpc_selected_without_do_mpc(no_do_mpc, ds):
    """The real proof: run an actual Hold work cycle with MPC selected and
    do_mpc absent. Before the fix this raised out of run_work_cycle -- which on
    a real grill is the control process exiting."""
    settings = _mpc_settings()
    control_data = base_control(mode="Hold")
    control_data["primary_setpoint"] = 225
    grill = FakeGrillPlatform()

    result = run_mode(
        "Hold",
        settings=settings,
        control_data=control_data,
        pellet_db=base_pellet_db(),
        probes=FakeProbes().script([200] * 12),
        probe_cap=8,
        grill=grill,
    )

    # It got past setup and ran the work loop rather than aborting at
    # setup_safety: the loop's own auger cycling is visible.
    names = [call[0] for call in result.grill_calls]
    assert "auger_on" in names
    # Powered up and under control, not dumped into an error/stop state.
    assert "power_on" in names
    # And the user has an explanation waiting on the dashboard.
    assert any("[mpc] controller could not be started" in error for error in read_errors(ErrorKind.CONTROL))
