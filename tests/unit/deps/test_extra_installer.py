"""The detached installer's command construction and outcome reporting.

NO REAL INSTALL RUNS HERE. `install()` is always given a `runner` stub, so
`run_streaming` -- the only function in common/extra_installer.py that touches
subprocess -- is never reached. Building the `mpc` extra compiles CasADi from
source; a test that accidentally ran it would take minutes and mutate the venv.
"""

import tomllib

import pytest

from common import controller_deps as cd
from common import extra_installer as ei


@pytest.fixture(autouse=True)
def _log_off_repo(tmp_path, monkeypatch):
    """Keep the installer's log out of the checkout's logs/ directory."""
    monkeypatch.setattr(ei, "LOG_PATH", str(tmp_path / "dependency-install.log"))


def _pyproject_mpc_extra():
    with open(f"{cd.PROJECT_ROOT}/pyproject.toml", "rb") as handle:
        return tomllib.load(handle)["project"]["optional-dependencies"]["mpc"]


# --- command construction --------------------------------------------------


def test_uv_command_installs_pifires_own_extra_by_name():
    command = ei.build_install_command("mpc", use_uv=True)
    assert command == ["uv", "sync", "--frozen", "--inexact", "--no-dev", "--extra", "mpc"]


def test_uv_command_keeps_inexact_so_wizard_installed_drivers_survive():
    # A plain `uv sync` PRUNES everything not in uv.lock, and the wizard installs
    # per-module probe/display dependencies with `uv pip install`. Dropping
    # --inexact would uninstall the user's hardware drivers as a side effect of
    # selecting a controller.
    assert "--inexact" in ei.build_install_command("mpc", use_uv=True)


def test_uv_command_never_hardcodes_the_package_spec():
    # The whole point of --extra: the `do-mpc>=5.1.1` spec stays in
    # pyproject.toml/uv.lock and cannot drift from a copy kept here.
    command = ei.build_install_command("mpc", use_uv=True)
    assert not any("do-mpc" in part or "do_mpc" in part for part in command)


def test_pip_command_passes_the_pyproject_spec_through_verbatim():
    command = ei.build_install_command("mpc", use_uv=False, python_exec="/venv/bin/python")
    assert command[:4] == ["/venv/bin/python", "-m", "pip", "install"]
    assert command[4:] == _pyproject_mpc_extra()


def test_pip_command_keeps_an_extras_marker_whole_as_one_argv_entry(tmp_path):
    # An extras marker is the easy silent mistake: `pkg` resolves and installs
    # fine where `pkg[extra]` was meant, so nothing errors -- you just end up
    # missing the extras. PiFire's own `mpc` extra carries no marker today, so
    # pin the behaviour against a synthetic project rather than leaving it
    # untested until some future extra needs one.
    (tmp_path / "pyproject.toml").write_text('[project.optional-dependencies]\nvision = ["some-pkg[cuda,dev]>=2.0"]\n')
    command = ei.build_install_command(
        "vision", use_uv=False, python_exec="/venv/bin/python", project_root=str(tmp_path)
    )
    # One argv element, brackets intact. An argv list is never re-parsed by a
    # shell, so `[` and `]` survive -- but only if the spec is not split.
    assert command[4:] == ["some-pkg[cuda,dev]>=2.0"]


def test_pip_requirements_are_read_from_pyproject_not_a_literal():
    assert ei.extra_requirements("mpc") == _pyproject_mpc_extra()


def test_unknown_extra_has_no_pip_command():
    with pytest.raises(ValueError):
        ei.build_install_command("not-an-extra", use_uv=False)


# --- outcome reporting -----------------------------------------------------


def test_successful_install_records_done_and_tells_the_user(ds):
    seen = {}

    def runner(command, on_line):
        seen["command"] = command
        on_line("Installed 6 packages")
        return 0

    assert ei.install("mpc", use_uv=True, runner=runner) is True
    assert seen["command"] == ["uv", "sync", "--frozen", "--inexact", "--no-dev", "--extra", "mpc"]
    assert cd.install_state("mpc")["state"] == "done"

    from common.datastore_accessors import read_warnings_snapshot

    banners = read_warnings_snapshot()["warnings"]
    assert any("finished installing" in w for w in banners)
    assert any("your grill is unaffected" in w.lower() for w in banners)


def test_failed_install_records_failed_and_raises_a_dashboard_error(ds):
    from common.datastore_accessors import read_errors

    assert ei.install("mpc", use_uv=True, runner=lambda command, on_line: 1) is False
    state = cd.install_state("mpc")
    assert state["state"] == "failed"
    assert "exit code 1" in state["message"]
    errors = read_errors()
    assert any("dependency-install.log" in e for e in errors)
    assert any("failed" in e for e in errors)


def test_a_runner_that_explodes_is_reported_not_propagated(ds):
    from common.datastore_accessors import read_errors

    def boom(command, on_line):
        raise OSError("uv: command not found")

    assert ei.install("mpc", use_uv=True, runner=boom) is False
    assert cd.install_state("mpc")["state"] == "failed"
    assert any("uv: command not found" in e for e in read_errors())


def test_an_unknown_extra_fails_cleanly_without_running_anything(ds):
    calls = []

    def runner(command, on_line):
        calls.append(command)
        return 0

    assert ei.install("not-an-extra", use_uv=False, runner=runner) is False
    assert calls == []
    assert cd.install_state("not-an-extra")["state"] == "failed"


def test_progress_lines_land_in_the_install_state(ds):
    def runner(command, on_line):
        on_line("Building casadi==3.7.0")
        return 0

    ei.install("mpc", use_uv=True, runner=runner)
    # Final state is 'done'; the running-state updates happened en route.
    assert cd.install_state("mpc")["state"] == "done"


def test_module_never_shells_out_or_escalates_privileges():
    # This repo has a history of tests triggering real reboots. The installer
    # must reach subprocess through one auditable argv call and nothing else.
    with open(ei.__file__) as handle:
        source = handle.read()
    assert "os.system" not in source
    assert "sudo" not in source
    assert "shell=True" not in source
    assert source.count("subprocess.Popen") == 1
