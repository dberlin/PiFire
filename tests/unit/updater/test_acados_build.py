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
