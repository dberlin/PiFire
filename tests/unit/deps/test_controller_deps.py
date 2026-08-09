"""Controller availability and the generic detached-extra helper.

No test launches an installer. The acados MPC has no Python extra: its
availability boundary is ``controller.acados._library.load_native``.
"""

import subprocess

from common import controller_deps as cd


class _Popen:
    """Records the argv and kwargs it was handed; never spawns anything."""

    def __init__(self):
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        return object()



# --- detection -------------------------------------------------------------


def test_controller_without_dependencies_block_needs_nothing():
    assert cd.controller_dependencies("pid") == {}
    assert cd.required_modules_for("pid", {}) == ()
    assert cd.check_controller_dependencies("pid", {}) is None


def test_mpc_declares_no_python_extra_or_import_module():
    assert cd.controller_dependencies("mpc") == {}
    assert cd.required_modules_for("mpc", {}) == ()


def test_mpc_availability_calls_the_acados_native_loader(monkeypatch):
    calls = []
    monkeypatch.setattr(cd, "load_native", lambda: calls.append("load"), raising=False)

    assert cd.check_controller_dependencies("mpc", {}) is None
    assert calls == ["load"]

# --- detached launch -------------------------------------------------------


def test_installer_command_is_an_argv_list_with_no_shell_metacharacters():
    command = cd.installer_command("sample", python_exec="/opt/pifire/.venv/bin/python")
    assert command == ["/opt/pifire/.venv/bin/python", "-m", "common.extra_installer", "sample"]
    assert not any(ch in part for part in command for ch in "&;|")


def test_install_extra_detached_spawns_the_installer_with_the_right_arguments(ds):
    popen = _Popen()
    started, message = cd.install_extra_detached("sample", python_exec="/venv/bin/python", popen=popen)

    assert started is True and message == ""
    assert len(popen.calls) == 1
    command, kwargs = popen.calls[0]
    assert command == ["/venv/bin/python", "-m", "common.extra_installer", "sample"]
    # Detached: it has to outlive the gunicorn worker that started it, and it
    # must not inherit pipes that would block on a full buffer during a build.
    assert kwargs["start_new_session"] is True
    assert kwargs["cwd"] == cd.PROJECT_ROOT
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert cd.install_state("sample")["state"] == "running"


def test_install_extra_detached_does_not_start_a_second_build(ds):
    popen = _Popen()
    assert cd.install_extra_detached("sample", popen=popen)[0] is True
    started, message = cd.install_extra_detached("sample", popen=popen)
    assert started is False
    assert "already in progress" in message
    assert len(popen.calls) == 1  # not spawned twice


def test_a_stale_running_marker_does_not_block_forever(ds):
    cd.set_install_state("sample", "running", "Starting...")
    state = cd.install_state("sample")
    state["updated"] = state["updated"] - cd.STALE_INSTALL_SECONDS - 1
    cd.write_generic_key("extra_install:sample", state)

    popen = _Popen()
    assert cd.install_extra_detached("sample", popen=popen)[0] is True
    assert len(popen.calls) == 1


def test_finished_installs_can_be_retried(ds):
    cd.set_install_state("sample", "failed", "exit code 1")
    popen = _Popen()
    assert cd.install_extra_detached("sample", popen=popen)[0] is True


def test_a_spawn_failure_is_reported_not_raised(ds):
    def boom(*args, **kwargs):
        raise OSError("no such file")

    started, message = cd.install_extra_detached("sample", popen=boom)
    assert started is False
    assert "no such file" in message
    assert cd.install_state("sample")["state"] == "failed"


# --- the message the user reads -------------------------------------------


def test_message_says_what_is_wrong_what_is_happening_and_that_the_grill_is_safe():
    missing = cd.MissingDependency("sample", "sample", ("sample_module",))
    text = cd.dependency_message(missing, started=True)
    assert "sample_module" in text
    assert "Installing it in the background" in text
    assert "controller is unchanged" in text
    assert "SAMPLE" in text


def test_message_when_no_install_could_be_started():
    missing = cd.MissingDependency("sample", "sample", ("sample_module",))
    text = cd.dependency_message(missing, started=False, detail="An install is already in progress.")
    assert "already in progress" in text
    assert "controller is unchanged" in text


def test_message_when_there_is_no_extra_to_install():
    missing = cd.MissingDependency("sample", None, ("sample_module",))
    text = cd.dependency_message(missing, started=False)
    assert "no automatic install" in text
    assert "controller is unchanged" in text
