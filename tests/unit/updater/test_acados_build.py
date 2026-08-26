from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from common.acados_build import (
    ACADOS_BUILD_FAIL_MARKER,
    ACADOS_BUILD_OK_MARKER,
    ACADOS_BUILD_RUN_MARKER,
    run_acados_build,
)


def test_conditional_helper_invokes_the_only_deployed_command_and_streams_every_line(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []
    seen: list[str] = []
    subprocess_lines = [
        "configure/fetch: started",
        "  compiler output retains indentation  ",
        "",
        "diagnostic: café / 温度",
        "diagnostic: café / 温度",
    ]

    def runner(command: tuple[str, ...], on_line: Callable[[str], None]) -> int:
        calls.append(command)
        for line in subprocess_lines:
            on_line(line)
        return 0

    code = run_acados_build(tmp_path, seen.append, if_needed=True, runner=runner)

    assert code == 0
    assert calls == [("bash", str(tmp_path / "rebuild-acados.sh"), "--if-needed")]
    assert seen == [ACADOS_BUILD_RUN_MARKER, *subprocess_lines, ACADOS_BUILD_OK_MARKER]


def test_full_helper_invokes_the_same_public_script_without_private_modes(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...], on_line: Callable[[str], None]) -> int:
        calls.append(command)
        on_line("full build output")
        return 0

    assert run_acados_build(tmp_path, lambda line: None, if_needed=False, runner=runner) == 0
    assert calls == [("bash", str(tmp_path / "rebuild-acados.sh"))]


def test_failure_marker_follows_all_retained_subprocess_lines(
    tmp_path: Path,
) -> None:
    seen: list[str] = []

    def runner(command: tuple[str, ...], on_line: Callable[[str], None]) -> int:
        del command
        on_line("compile line 1")
        on_line("compile line 2")
        return 17

    assert run_acados_build(tmp_path, seen.append, if_needed=True, runner=runner) == 17
    assert seen == [
        ACADOS_BUILD_RUN_MARKER,
        "compile line 1",
        "compile line 2",
        ACADOS_BUILD_FAIL_MARKER,
    ]
    assert ACADOS_BUILD_OK_MARKER not in seen


def test_runner_exception_still_closes_the_structured_stream_as_failed(
    tmp_path: Path,
) -> None:
    seen: list[str] = []

    def runner(command: tuple[str, ...], on_line: Callable[[str], None]) -> int:
        del command
        on_line("line before abrupt runner failure")
        raise OSError("runner failed")

    with pytest.raises(OSError, match="runner failed"):
        run_acados_build(tmp_path, seen.append, if_needed=True, runner=runner)

    assert seen == [
        ACADOS_BUILD_RUN_MARKER,
        "line before abrupt runner failure",
        ACADOS_BUILD_FAIL_MARKER,
    ]


def test_markers_are_unambiguous_phase_boundaries() -> None:
    assert ACADOS_BUILD_RUN_MARKER == "=== acados native rebuild started ==="
    assert ACADOS_BUILD_OK_MARKER == "=== acados native rebuild finished ==="
    assert ACADOS_BUILD_FAIL_MARKER == "=== acados native rebuild failed ==="
    assert len({ACADOS_BUILD_RUN_MARKER, ACADOS_BUILD_OK_MARKER, ACADOS_BUILD_FAIL_MARKER}) == 3


def test_standalone_rebuild_uses_the_conditional_shared_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import updater

    calls: list[tuple[Path, bool]] = []
    published: list[tuple[int, str, str]] = []
    monkeypatch.setattr(updater, "logger", logging.getLogger("acados-rebuild-test"), raising=False)
    monkeypatch.setattr(updater, "set_updater_install_status", lambda *args: published.append(args))

    def rebuild(repo_root: Path, on_line: Callable[[str], None], *, if_needed: bool) -> int:
        calls.append((repo_root, if_needed))
        on_line("conditional rebuild output")
        return 0

    monkeypatch.setattr(updater, "run_acados_build", rebuild)
    #  This run reaches FINISHED_PERCENT, and publish_finished() restarts
    #  PiFire's supervisor programs on the way out: a real
    #  `sudo supervisorctl restart all` against the developer's machine on any
    #  datastore that answers is_real_hardware() with True. The suite-wide
    #  `real_hw` of False (tests/conftest.py) closes that gate too; this is the
    #  layer that holds regardless of what the settings blob says.
    monkeypatch.setattr(updater, "restart_scripts", lambda wait=False: None)

    updater.run_acados_rebuild(tmp_path)

    assert calls == [(tmp_path, True)]
    assert (25, "Rebuilding acados native runtime...", "conditional rebuild output") in published
    assert published[-1][0] == updater.FINISHED_PERCENT


def test_standalone_rebuild_failure_is_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import updater

    published: list[tuple[int, str, str]] = []
    monkeypatch.setattr(updater, "logger", logging.getLogger("acados-rebuild-test"), raising=False)
    monkeypatch.setattr(updater, "set_updater_install_status", lambda *args: published.append(args))
    monkeypatch.setattr(updater, "run_acados_build", lambda *args, **kwargs: 17)

    with pytest.raises(SystemExit):
        updater.run_acados_rebuild(tmp_path)

    assert published[-1][0] < 0
    assert published[-1][1] == "Acados rebuild failed"
    assert "17" in published[-1][2]


def _staged_script(tmp_path: Path, *, with_venv: bool) -> tuple[Path, Path]:
    """Copy the real script into a throwaway tree whose interpreters are stubs.

    The script's interpreter is now an absolute path inside the repository, so a
    probe cannot be injected through PATH alone. Staging a copy lets the stub
    stand in for the venv without writing into the real one.
    """

    repository = Path(__file__).resolve().parents[3]
    staged = tmp_path / "repo"
    staged.mkdir()
    (staged / "rebuild-acados.sh").write_text((repository / "rebuild-acados.sh").read_text())
    (staged / "rebuild-acados.sh").chmod(0o755)
    if with_venv:
        venv_bin = staged / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        interpreter = venv_bin / "python3"
        interpreter.write_text("#!/bin/sh\necho venv\npwd\n")
        interpreter.chmod(0o755)
    decoy_bin = tmp_path / "bin"
    decoy_bin.mkdir()
    decoy = decoy_bin / "python3"
    decoy.write_text("#!/bin/sh\necho path\npwd\n")
    decoy.chmod(0o755)
    return staged, decoy_bin


def _run_staged(staged: Path, decoy_bin: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(staged / "rebuild-acados.sh"), "--if-needed"],
        cwd=cwd,
        env={"PATH": f"{decoy_bin}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_public_script_resolves_python_module_outside_repository(
    tmp_path: Path,
) -> None:
    staged, decoy_bin = _staged_script(tmp_path, with_venv=True)
    completed = _run_staged(staged, decoy_bin, cwd=tmp_path)
    assert completed.returncode == 0
    assert Path(completed.stdout.split()[-1]) == staged


def test_public_script_prefers_the_repository_venv_over_any_path_interpreter(
    tmp_path: Path,
) -> None:
    # Supervisor starts the control process with a PATH that carries no venv, so
    # the interpreter has to be resolved by absolute path rather than by lookup.
    staged, decoy_bin = _staged_script(tmp_path, with_venv=True)
    completed = _run_staged(staged, decoy_bin, cwd=tmp_path)
    assert completed.returncode == 0
    assert completed.stdout.split()[0] == "venv"


def test_public_script_falls_back_to_path_python_without_a_venv(
    tmp_path: Path,
) -> None:
    staged, decoy_bin = _staged_script(tmp_path, with_venv=False)
    completed = _run_staged(staged, decoy_bin, cwd=tmp_path)
    assert completed.returncode == 0
    assert completed.stdout.split()[0] == "path"
    assert Path(completed.stdout.split()[-1]) == staged


class _NativeFailure(RuntimeError):
    pass


@pytest.mark.parametrize("operation", ["update", "branch"])
def test_source_change_native_failure_restores_exact_source_and_runtime_before_return(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    import updater

    events: list[str] = []
    snapshot = object()
    monkeypatch.setattr(
        updater,
        "ensure_acados_prerequisites",
        lambda *args, **kwargs: events.append("prerequisites") or (True, "prerequisites ready"),
    )
    monkeypatch.setattr(
        updater, "capture_update_snapshot", lambda *args, **kwargs: events.append("snapshot") or snapshot
    )
    monkeypatch.setattr(
        updater,
        "_install_update_checkout",
        lambda: events.append("checkout") or (True, "Update Completed Successfully", " - updated"),
    )
    monkeypatch.setattr(
        updater,
        "_change_branch_checkout",
        lambda branch: events.append(f"checkout:{branch}") or (True, "Branch Changed Successfully", " - changed"),
    )
    monkeypatch.setattr(
        updater,
        "run_acados_build",
        lambda *args, **kwargs: events.append("native") or 17,
    )
    monkeypatch.setattr(
        updater, "restore_update_snapshot", lambda value, *args, **kwargs: events.append("rollback") or None
    )
    monkeypatch.setattr(updater, "_publish", lambda *args: None)

    if operation == "update":
        success, status, output = updater.install_update()
        expected_checkout = "checkout"
    else:
        success, status, output = updater.change_branch("development")
        expected_checkout = "checkout:development"

    assert success is False
    assert "native" in status.lower()
    assert "17" in output
    assert events == ["prerequisites", "snapshot", expected_checkout, "native", "rollback"]


def test_dependency_failure_is_terminal_and_never_finishes_or_restarts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import updater

    published: list[tuple[int, str, str]] = []
    monkeypatch.setattr(updater, "logger", __import__("logging").getLogger("acados-update-test"), raising=False)
    monkeypatch.setattr(updater, "read_settings", lambda: {"versions": {"server": "1.12.0", "build": 92}})
    monkeypatch.setattr(updater, "selected_wizard_dependencies", lambda settings: None)
    monkeypatch.setattr(updater, "set_updater_install_status", lambda *args: published.append(args))
    monkeypatch.setattr(updater, "time", type("_Time", (), {"sleep": staticmethod(lambda _: None)}))
    monkeypatch.setattr(
        updater,
        "install_update",
        lambda: (True, "Update Completed Successfully", " - native release published"),
    )
    monkeypatch.setattr(updater, "install_dependencies", lambda *args: (9, False, ()))
    monkeypatch.setattr(
        updater,
        "rebuild_web_ui_if_stale",
        lambda: pytest.fail("web build must not run after dependency/migration failure"),
    )
    monkeypatch.setattr(updater, "publish_finished", lambda reboot: pytest.fail("failure must not publish Finished"))

    with pytest.raises(SystemExit):
        updater.run_update("main")

    assert published[-1][0] < 0
    assert "dependencies" in published[-1][1].lower()


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _commit_file(repo: Path, text: str, message: str) -> str:
    (repo / "tracked.txt").write_text(text)
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("flow_name", ["update", "branch"])
def test_real_git_flow_restores_branch_revision_runtime_and_terminal_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flow_name: str
) -> None:
    import updater
    from common import datastore
    from common.persistence.install_state import get_updater_install_status

    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    checkout = tmp_path / "pifire"
    remote.mkdir()
    _git(remote, "init", "--bare", "--initial-branch=main")
    seed.mkdir()
    _git(seed, "init", "--initial-branch=main")
    _git(seed, "config", "user.email", "tests@example.invalid")
    _git(seed, "config", "user.name", "PiFire tests")
    old_revision = _commit_file(seed, "old\n", "old")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "-u", "origin", "main")
    _git(tmp_path, "clone", str(remote), str(checkout))
    _git(checkout, "config", "user.email", "tests@example.invalid")
    _git(checkout, "config", "user.name", "PiFire tests")

    if flow_name == "update":
        _commit_file(seed, "new\n", "new")
        _git(seed, "push", "origin", "main")
        target = "main"
    else:
        _git(checkout, "checkout", "-b", "development")
        _commit_file(checkout, "development\n", "development")
        _git(checkout, "checkout", "main")
        target = "development"

    native = checkout / "controller" / "_native"
    (native / "releases" / "old").mkdir(parents=True)
    (native / "current").symlink_to("releases/old")
    rebuild = checkout / "rebuild-acados.sh"
    rebuild.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'cd "$(dirname "$0")"\n'
        "rm -f controller/_native/current\n"
        "ln -s releases/broken controller/_native/current\n"
        "exit 31\n"
    )
    rebuild.chmod(0o755)

    db_path = tmp_path / f"{flow_name}.db"
    datastore._reset_for_tests(str(db_path))
    datastore.init()
    cursor = {"versions": {"server": "1.12.0", "build": 92}}
    restart_marker = tmp_path / f"{flow_name}-restart"
    monkeypatch.chdir(checkout)
    monkeypatch.setattr(updater, "REPO_ROOT", str(checkout))
    monkeypatch.setattr(updater, "logger", logging.getLogger(f"real-{flow_name}-rollback"), raising=False)
    monkeypatch.setattr(updater, "read_settings", lambda: cursor)
    monkeypatch.setattr(updater, "selected_wizard_dependencies", lambda settings: None)
    monkeypatch.setattr(updater, "time", type("_Time", (), {"sleep": staticmethod(lambda _: None)}))
    monkeypatch.setattr(
        updater,
        "ensure_acados_prerequisites",
        lambda *args, **kwargs: (True, "prerequisites ready"),
    )
    monkeypatch.setattr(
        updater,
        "install_dependencies",
        lambda *args: pytest.fail("dependency/settings/cursor mutation ran after native failure"),
    )
    monkeypatch.setattr(
        updater,
        "rebuild_web_ui_if_stale",
        lambda: pytest.fail("web build ran after native failure"),
    )
    monkeypatch.setattr(updater, "publish_finished", lambda reboot: restart_marker.write_text("Finished"))

    run = updater.run_update if flow_name == "update" else updater.run_branch_change
    with pytest.raises(SystemExit):
        run(target)

    assert _git(checkout, "branch", "--show-current") == "main"
    assert _git(checkout, "rev-parse", "HEAD") == old_revision
    assert os.readlink(native / "current") == "releases/old"
    percent, status, output = get_updater_install_status()
    assert percent < 0
    assert "native" in status.lower()
    assert "31" in output
    assert cursor == {"versions": {"server": "1.12.0", "build": 92}}
    assert not restart_marker.exists()
    datastore._reset_for_tests(None)
