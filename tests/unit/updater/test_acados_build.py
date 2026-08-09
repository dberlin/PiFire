from __future__ import annotations

from pathlib import Path
import subprocess
import os
from typing import Callable

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

def test_public_script_resolves_python_module_outside_repository(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[3]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    python = fake_bin / "python3"
    python.write_text("#!/bin/sh\npwd\n")
    python.chmod(0o755)
    completed = subprocess.run(
        ["bash", str(repository / "rebuild-acados.sh"), "--if-needed"],
        cwd=tmp_path,
        env={"PATH": f"{fake_bin}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert Path(completed.stdout.strip()) == repository


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
    monkeypatch.setattr(updater, "set_updater_install_status", lambda *args: published.append(args))
    monkeypatch.setattr(updater, "time", type("_Time", (), {"sleep": staticmethod(lambda _: None)}))
    monkeypatch.setattr(
        updater,
        "install_update",
        lambda: (True, "Update Completed Successfully", " - native release published"),
    )
    monkeypatch.setattr(updater, "install_dependencies", lambda *args: (9, False))
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
