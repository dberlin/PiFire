"""Shared streaming boundary for the sole acados rebuild command."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import subprocess


ACADOS_BUILD_RUN_MARKER = "=== acados native rebuild started ==="
ACADOS_BUILD_OK_MARKER = "=== acados native rebuild finished ==="
ACADOS_BUILD_FAIL_MARKER = "=== acados native rebuild failed ==="

LineCallback = Callable[[str], None]
BuildRunner = Callable[[tuple[str, ...], LineCallback], int]


def _stream_process(command: tuple[str, ...], on_line: LineCallback) -> int:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    for raw_line in process.stdout:
        if raw_line.endswith("\n"):
            raw_line = raw_line[:-1]
            if raw_line.endswith("\r"):
                raw_line = raw_line[:-1]
        on_line(raw_line)
    return process.wait()


def run_acados_build(
    repo_root: str | Path,
    on_line: LineCallback,
    *,
    if_needed: bool,
    runner: BuildRunner = _stream_process,
) -> int:
    """Run the public rebuild script while retaining every emitted output line."""
    repository = Path(repo_root).resolve()
    command = ["bash", str(repository / "rebuild-acados.sh")]
    if if_needed:
        command.append("--if-needed")

    on_line(ACADOS_BUILD_RUN_MARKER)
    try:
        return_code = runner(tuple(command), on_line)
    except BaseException:
        on_line(ACADOS_BUILD_FAIL_MARKER)
        raise
    on_line(ACADOS_BUILD_OK_MARKER if return_code == 0 else ACADOS_BUILD_FAIL_MARKER)
    return return_code
