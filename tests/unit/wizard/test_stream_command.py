"""wizard._stream_command: what the installer manages to capture.

Both defects here were invisible from the old UI, which showed a bar and a
one-line status. They become the whole feature once that output is on screen.

The subprocesses below are `python -c` one-liners that print and exit -- no
installer, no package manager, nothing that touches the system.
"""

import logging
import subprocess
import sys

import pytest

import wizard


@pytest.fixture
def captured(monkeypatch):
    """Collect what _stream_command publishes, without a datastore or log file."""
    lines = []
    monkeypatch.setattr(wizard, "set_wizard_install_status", lambda p, s, output: lines.append(output))
    monkeypatch.setattr(wizard, "logger", logging.getLogger("wizard_test"))
    return lines


def run(script, captured):
    return wizard._stream_command([sys.executable, "-c", script], 50, "Installing...")


def test_captures_stderr(captured):
    """uv writes its entire progress report -- resolution, builds, errors -- to
    stderr. Piping only stdout left the panel empty for the Python dependency
    step, which is the slowest one and the one worth watching."""
    run("import sys; print('Resolved 12 packages', file=sys.stderr)", captured)

    assert captured == ["Resolved 12 packages"]


def test_keeps_the_last_line_when_the_command_exits(captured):
    """The old loop tested process.poll() before consuming the line it had just
    read. poll() goes non-None while the final lines are still buffered, so the
    output describing how a command ended was exactly what got dropped."""
    run("print('working'); print('ERROR: could not build wheel')", captured)

    assert captured == ["working", "ERROR: could not build wheel"]


def test_interleaves_both_streams_in_order(captured):
    run(
        "import sys; print('one'); sys.stdout.flush();"
        " print('two', file=sys.stderr); sys.stderr.flush(); print('three')",
        captured,
    )

    assert captured == ["one", "two", "three"]


def test_returns_the_lines_for_sentinel_scanning(captured):
    """_run_install_commands reads REBOOT_REQUIRED=true out of the return value,
    so the sentinel has to survive the move to a shared helper."""
    lines = run("print('REBOOT_REQUIRED=true')", captured)

    assert lines == ["REBOOT_REQUIRED=true"]


def test_a_failing_command_reports_its_exit_code(captured):
    """A failed dependency must stop the detached installer rather than letting
    it publish a false 100% / Finished state."""
    with pytest.raises(subprocess.CalledProcessError) as raised:
        run("import sys; print('E: Unable to locate package', file=sys.stderr); sys.exit(1)", captured)

    assert raised.value.returncode == 1
    assert captured[0] == "E: Unable to locate package"
    assert captured[-1].endswith("exited with code 1")


def test_a_clean_exit_adds_no_failure_line(captured):
    run("print('done')", captured)

    assert captured == ["done"]
