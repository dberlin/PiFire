"""Tests for wizard.py's dynamic reboot-required detection.

_run_install_commands() replaces the old static-manifest-flag lookup: it runs each
command in command_list and ORs together whichever ones print a REBOOT_REQUIRED=true
sentinel as (usually) their last line of stdout.

Includes a regression test for a readline/poll ordering bug: the original loop checked
`process.poll()` immediately after `readline()`, before processing the line just read.
Since our sentinel line is always the last thing a script prints right before exiting,
a process that has already exited by the time poll() is checked would have its final
line silently discarded -- exactly when the sentinel matters most.

subprocess.Popen is always mocked here; no real command is ever executed.
"""

import logging

import pytest

import wizard


class _FakeProcess:
    """Models `poll()` returning "still running" (None) as long as there are unread
    lines, and "exited" (0) once the last line has been read -- i.e. the process exits
    right as its final line becomes available, which is the real-world case that
    drops the last line under the old (buggy) loop ordering."""

    def __init__(self, lines):
        self._lines = list(lines)
        self._index = 0

    @property
    def stdout(self):
        return self

    def readline(self):
        if self._index < len(self._lines):
            line = self._lines[self._index]
            self._index += 1
            return line
        return ""

    def poll(self):
        return 0 if self._index >= len(self._lines) else None


@pytest.fixture(autouse=True)
def _quiet_status(monkeypatch):
    monkeypatch.setattr(wizard, "logger", logging.getLogger("wizard_reboot_test"), raising=False)
    monkeypatch.setattr(wizard, "set_wizard_install_status", lambda *a, **k: None)
    monkeypatch.setattr(wizard, "set_updater_install_status", lambda *a, **k: None)


def test_reboot_required_sentinel_as_last_line_is_not_dropped(monkeypatch):
    monkeypatch.setattr(wizard, "is_real_hardware", lambda *a, **k: True)
    fake = _FakeProcess(["doing setup things\n", "REBOOT_REQUIRED=true\n"])
    monkeypatch.setattr(wizard.subprocess, "Popen", lambda *a, **k: fake)

    percent, reboot_required = wizard._run_install_commands(
        command_list=[["sudo", "python", "board-config.py", "-s"]],
        percent=50,
        increment=10,
        status="Installing...",
        python_exec="python",
    )

    assert reboot_required is True
    assert percent == 60


def test_reboot_required_false_sentinel_as_last_line(monkeypatch):
    monkeypatch.setattr(wizard, "is_real_hardware", lambda *a, **k: True)
    fake = _FakeProcess(["doing setup things\n", "REBOOT_REQUIRED=false\n"])
    monkeypatch.setattr(wizard.subprocess, "Popen", lambda *a, **k: fake)

    _, reboot_required = wizard._run_install_commands(
        command_list=[["sudo", "python", "board-config.py", "-bl"]],
        percent=0,
        increment=10,
        status="Installing...",
        python_exec="python",
    )

    assert reboot_required is False


def test_no_sentinel_at_all_defaults_to_false(monkeypatch):
    """Matches raspi5.sh/bluepy.sh, which never print a sentinel."""
    monkeypatch.setattr(wizard, "is_real_hardware", lambda *a, **k: True)
    fake = _FakeProcess(["some output\n", "more output\n"])
    monkeypatch.setattr(wizard.subprocess, "Popen", lambda *a, **k: fake)

    _, reboot_required = wizard._run_install_commands(
        command_list=[["bash", "wizard/raspi5.sh"]],
        percent=0,
        increment=10,
        status="Installing...",
        python_exec="python",
    )

    assert reboot_required is False


def test_multiple_commands_are_ored_together(monkeypatch):
    fakes = [_FakeProcess(["ok\n", "REBOOT_REQUIRED=false\n"]), _FakeProcess(["ok\n", "REBOOT_REQUIRED=true\n"])]
    monkeypatch.setattr(wizard, "is_real_hardware", lambda *a, **k: True)
    monkeypatch.setattr(wizard.subprocess, "Popen", lambda *a, **k: fakes.pop(0))

    _, reboot_required = wizard._run_install_commands(
        command_list=[["cmd1"], ["cmd2"]], percent=0, increment=10, status="Installing...", python_exec="python"
    )

    assert reboot_required is True


def test_dev_mode_never_runs_a_subprocess_and_never_requires_reboot(monkeypatch):
    monkeypatch.setattr(wizard, "is_real_hardware", lambda *a, **k: False)
    monkeypatch.setattr(wizard.time, "sleep", lambda *a, **k: None)
    called = []
    monkeypatch.setattr(wizard.subprocess, "Popen", lambda *a, **k: called.append(1))

    _, reboot_required = wizard._run_install_commands(
        command_list=[["whatever"]], percent=0, increment=10, status="Installing...", python_exec="python"
    )

    assert reboot_required is False
    assert called == []
