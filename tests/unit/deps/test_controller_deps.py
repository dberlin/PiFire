"""Detection + detached-launch half of the on-demand extra install.

NOTHING here may run a real install: the `mpc` extra compiles CasADi from
source. Every test stubs at the subprocess boundary the code actually calls --
`install_extra_detached(popen=...)` -- and the one function in this module that
could ever reach `subprocess.Popen` is never called without that stub. There is
no `os.system`, no `sudo`, and no shell string anywhere in
common/controller_deps.py; the assertions below pin that.
"""

import subprocess

import pytest

from common import controller_deps as cd
from controller.mpc import _DEFAULTS


class _Popen:
    """Records the argv and kwargs it was handed; never spawns anything."""

    def __init__(self):
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        return object()


@pytest.fixture
def no_do_mpc(monkeypatch):
    """Make do_mpc look absent without uninstalling it.

    Patches the exact seam `missing_modules` uses, so the rest of the process
    (including pytest's own imports) is unaffected.
    """
    real_find_spec = cd.importlib.util.find_spec

    def fake(name, *args, **kwargs):
        if name == "do_mpc" or name.startswith("do_mpc."):
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(cd.importlib.util, "find_spec", fake)


# --- detection -------------------------------------------------------------


def test_controller_without_dependencies_block_needs_nothing():
    assert cd.controller_dependencies("pid") == {}
    assert cd.required_modules_for("pid", {}) == ()
    assert cd.check_controller_dependencies("pid", {}) is None


def test_mpc_declares_the_extra_in_the_manifest():
    assert cd.controller_dependencies("mpc") == {"extra": "mpc", "modules": ["do_mpc"]}


def test_mpc_is_fine_when_do_mpc_is_installed():
    # The dev venv has do-mpc (dev group), so nothing is missing here.
    assert cd.check_controller_dependencies("mpc", dict(_DEFAULTS)) is None


def test_mpc_reports_missing_do_mpc(no_do_mpc):
    missing = cd.check_controller_dependencies("mpc", dict(_DEFAULTS))
    assert missing is not None
    assert missing.controller == "mpc"
    assert missing.extra == "mpc"
    assert missing.modules == ("do_mpc",)


def test_net_policy_is_blocked_by_missing_do_mpc_like_every_mpc_config(no_do_mpc):
    # policy='net' + EKF is pure numpy/scipy, but that only describes the
    # calibration at settings-save time: a learned model or a hand-edited
    # thermal parameter can invalidate the artifact before the next cook,
    # dropping the controller onto the NLP on a machine this gate had already
    # cleared. requires_modules() no longer exempts this config, so the gate
    # must not either.
    cfg = dict(_DEFAULTS, policy="net")
    missing = cd.check_controller_dependencies("mpc", cfg)
    assert missing is not None
    assert missing.modules == ("do_mpc",)


def test_detection_falls_back_to_the_manifest_list_when_the_hook_is_unavailable(monkeypatch, no_do_mpc):
    # If controller.mpc cannot even be imported we must fail towards "needed"
    # rather than waving a broken selection through to the control loop.
    def boom(name):
        raise ImportError(name)

    monkeypatch.setattr(cd.importlib, "import_module", boom)
    assert cd.required_modules_for("mpc", dict(_DEFAULTS)) == ("do_mpc",)
    assert cd.check_controller_dependencies("mpc", dict(_DEFAULTS)).modules == ("do_mpc",)


# --- detached launch -------------------------------------------------------


def test_installer_command_is_an_argv_list_with_no_shell_metacharacters():
    command = cd.installer_command("mpc", python_exec="/opt/pifire/.venv/bin/python")
    assert command == ["/opt/pifire/.venv/bin/python", "-m", "common.extra_installer", "mpc"]
    assert not any(ch in part for part in command for ch in "&;|")


def test_install_extra_detached_spawns_the_installer_with_the_right_arguments(ds):
    popen = _Popen()
    started, message = cd.install_extra_detached("mpc", python_exec="/venv/bin/python", popen=popen)

    assert started is True and message == ""
    assert len(popen.calls) == 1
    command, kwargs = popen.calls[0]
    assert command == ["/venv/bin/python", "-m", "common.extra_installer", "mpc"]
    # Detached: it has to outlive the gunicorn worker that started it, and it
    # must not inherit pipes that would block on a full buffer during a build.
    assert kwargs["start_new_session"] is True
    assert kwargs["cwd"] == cd.PROJECT_ROOT
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert cd.install_state("mpc")["state"] == "running"


def test_install_extra_detached_does_not_start_a_second_build(ds):
    popen = _Popen()
    assert cd.install_extra_detached("mpc", popen=popen)[0] is True
    started, message = cd.install_extra_detached("mpc", popen=popen)
    assert started is False
    assert "already in progress" in message
    assert len(popen.calls) == 1  # not spawned twice


def test_a_stale_running_marker_does_not_block_forever(ds):
    cd.set_install_state("mpc", "running", "Starting...")
    state = cd.install_state("mpc")
    state["updated"] = state["updated"] - cd.STALE_INSTALL_SECONDS - 1
    cd.write_generic_key("extra_install:mpc", state)

    popen = _Popen()
    assert cd.install_extra_detached("mpc", popen=popen)[0] is True
    assert len(popen.calls) == 1


def test_finished_installs_can_be_retried(ds):
    cd.set_install_state("mpc", "failed", "exit code 1")
    popen = _Popen()
    assert cd.install_extra_detached("mpc", popen=popen)[0] is True


def test_a_spawn_failure_is_reported_not_raised(ds):
    def boom(*args, **kwargs):
        raise OSError("no such file")

    started, message = cd.install_extra_detached("mpc", popen=boom)
    assert started is False
    assert "no such file" in message
    assert cd.install_state("mpc")["state"] == "failed"


# --- the message the user reads -------------------------------------------


def test_message_says_what_is_wrong_what_is_happening_and_that_the_grill_is_safe():
    missing = cd.MissingDependency("mpc", "mpc", ("do_mpc",))
    text = cd.dependency_message(missing, started=True)
    assert "do_mpc" in text
    assert "Installing it in the background" in text
    assert "controller is unchanged" in text
    assert "MPC" in text


def test_message_when_no_install_could_be_started():
    missing = cd.MissingDependency("mpc", "mpc", ("do_mpc",))
    text = cd.dependency_message(missing, started=False, detail="An install is already in progress.")
    assert "already in progress" in text
    assert "controller is unchanged" in text


def test_message_when_there_is_no_extra_to_install():
    missing = cd.MissingDependency("mpc", None, ("do_mpc",))
    text = cd.dependency_message(missing, started=False)
    assert "no automatic install" in text
    assert "controller is unchanged" in text
