"""Tests for common/system.py's hardware-detection and OS-command helpers:
is_real_hardware, restart_control/restart_webapp/restart_scripts, and
reboot_system/shutdown_system.

The restart_scripts/reboot_system/shutdown_system bodies do their real work
inside a background daemon thread (threading.Thread(target=..., daemon=True))
so the caller isn't blocked. To exercise that inner logic deterministically
(and assert on it) without an actual background thread, `sync_thread` patches
threading.Thread so start() calls the target function synchronously in the
calling thread instead of spawning one.

All subprocess/os.system/time.sleep boundaries are mocked -- no real
supervisorctl/systemctl/reboot/shutdown command is ever invoked.
"""

import subprocess
from unittest import mock

import pytest

import common.system as cc


class _SyncThread:
    """Stand-in for threading.Thread that runs target() synchronously on start()."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self.daemon = daemon

    def start(self):
        self._target(*self._args, **self._kwargs)


@pytest.fixture
def sync_thread():
    with mock.patch.object(cc.threading, "Thread", _SyncThread):
        yield


# --------------------------------------------------------------------------
# is_real_hardware
# --------------------------------------------------------------------------


def test_is_real_hardware_true_from_passed_settings():
    assert cc.is_real_hardware({"platform": {"real_hw": True}}) is True


def test_is_real_hardware_false_from_passed_settings():
    assert cc.is_real_hardware({"platform": {"real_hw": False}}) is False


def test_is_real_hardware_reads_settings_when_none_passed():
    """settings=None (the default) must fall through to read_settings()."""
    fake_settings = {"platform": {"real_hw": True}}
    with mock.patch.object(cc, "read_settings", return_value=fake_settings) as m:
        assert cc.is_real_hardware() is True
    m.assert_called_once()


# --------------------------------------------------------------------------
# restart_control / restart_webapp
# --------------------------------------------------------------------------


def test_restart_control_invokes_supervisorctl_via_os_system():
    with mock.patch.object(cc.os, "system") as m:
        cc.restart_control()
    m.assert_called_once_with("sleep 3 && sudo supervisorctl restart control &")


def test_restart_webapp_invokes_supervisorctl_via_os_system():
    with mock.patch.object(cc.os, "system") as m:
        cc.restart_webapp()
    m.assert_called_once_with("sleep 3 && sudo supervisorctl restart webapp &")


# --------------------------------------------------------------------------
# restart_scripts
# --------------------------------------------------------------------------


def test_restart_scripts_noop_when_not_real_hardware(sync_thread):
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=False),
        mock.patch.object(cc.subprocess, "run") as run,
    ):
        cc.restart_scripts()
    run.assert_not_called()


def test_restart_scripts_succeeds_via_systemctl_supervisor(sync_thread):
    ok = mock.Mock(returncode=0)
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.subprocess, "run", return_value=ok) as run,
    ):
        cc.restart_scripts()

    # First systemctl attempt ("supervisor") succeeds, so no further calls.
    run.assert_called_once_with(
        ["sudo", "systemctl", "restart", "supervisor"], capture_output=True, text=True, timeout=10
    )


def test_restart_scripts_falls_back_through_service_names_and_commands(sync_thread):
    """systemctl fails for both service names, then the legacy 'service' command
    is tried for both names, succeeding on the second ('supervisord')."""
    fail = mock.Mock(returncode=1, stderr="nope")
    ok = mock.Mock(returncode=0)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        # succeed only on the final ('service', ..., 'supervisord', 'restart') call
        if cmd == ["sudo", "service", "supervisord", "restart"]:
            return ok
        return fail

    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.subprocess, "run", side_effect=fake_run),
    ):
        cc.restart_scripts()

    assert calls == [
        ["sudo", "systemctl", "restart", "supervisor"],
        ["sudo", "systemctl", "restart", "supervisord"],
        ["sudo", "service", "supervisor", "restart"],
        ["sudo", "service", "supervisord", "restart"],
    ]


def test_restart_scripts_all_attempts_fail_does_not_raise(sync_thread, capsys):
    fail = mock.Mock(returncode=1, stderr="nope")
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.subprocess, "run", return_value=fail),
    ):
        cc.restart_scripts()  # must not raise

    assert "Failed to restart supervisor under any known service name" in capsys.readouterr().out


def test_restart_scripts_systemctl_timeout_stops_without_service_fallback(sync_thread):
    """A TimeoutExpired on the systemctl branch returns immediately -- the
    'service' legacy fallback loop is only reached from the systemctl loop
    completing (not timing out), per the explicit early `return` in the
    except-TimeoutExpired handler."""
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(
            cc.subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="systemctl", timeout=10)
        ) as run,
    ):
        cc.restart_scripts()

    # Only the first systemctl attempt is made before the timeout aborts everything.
    run.assert_called_once_with(
        ["sudo", "systemctl", "restart", "supervisor"], capture_output=True, text=True, timeout=10
    )


def test_restart_scripts_service_loop_generic_exception_is_swallowed(sync_thread):
    """A non-timeout exception raised from the legacy 'service' fallback loop
    (not just the systemctl loop) must also be caught per-attempt and the
    loop must continue to the next name rather than propagating."""
    fail = mock.Mock(returncode=1, stderr="nope")
    ok = mock.Mock(returncode=0)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1] == "systemctl":
            return fail
        if cmd == ["sudo", "service", "supervisor", "restart"]:
            raise FileNotFoundError("no service command")
        return ok

    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.subprocess, "run", side_effect=fake_run),
    ):
        cc.restart_scripts()  # must not raise

    assert calls == [
        ["sudo", "systemctl", "restart", "supervisor"],
        ["sudo", "systemctl", "restart", "supervisord"],
        ["sudo", "service", "supervisor", "restart"],
        ["sudo", "service", "supervisord", "restart"],
    ]


def test_restart_scripts_generic_exception_is_swallowed_and_continues(sync_thread):
    """A non-timeout exception (e.g. FileNotFoundError if sudo/systemctl is
    missing) is caught per-attempt and the loop continues to the next name/
    command rather than propagating."""
    ok = mock.Mock(returncode=0)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1] == "systemctl":
            raise FileNotFoundError("no systemctl")
        return ok

    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.subprocess, "run", side_effect=fake_run),
    ):
        cc.restart_scripts()  # must not raise

    assert calls == [
        ["sudo", "systemctl", "restart", "supervisor"],
        ["sudo", "systemctl", "restart", "supervisord"],
        ["sudo", "service", "supervisor", "restart"],
    ]


# --------------------------------------------------------------------------
# reboot_system
# --------------------------------------------------------------------------


def test_reboot_system_noop_when_not_real_hardware(sync_thread):
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=False),
        mock.patch.object(cc.subprocess, "run") as run,
    ):
        cc.reboot_system()
    run.assert_not_called()


def test_reboot_system_uses_systemctl_when_it_succeeds(sync_thread):
    ok = mock.Mock(returncode=0)
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.time, "sleep") as sleep,
        mock.patch.object(cc.subprocess, "run", return_value=ok) as run,
    ):
        cc.reboot_system()

    sleep.assert_called_once_with(3)
    run.assert_called_once_with(["sudo", "systemctl", "reboot"], capture_output=True, text=True, timeout=10)


def test_reboot_system_falls_back_to_plain_reboot_on_nonzero_returncode(sync_thread, capsys):
    fail = mock.Mock(returncode=1, stderr="denied")
    ok = mock.Mock(returncode=0)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return fail if cmd[1] == "systemctl" else ok

    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.time, "sleep"),
        mock.patch.object(cc.subprocess, "run", side_effect=fake_run),
    ):
        cc.reboot_system()

    assert calls == [["sudo", "systemctl", "reboot"], ["sudo", "reboot"]]
    assert "systemctl reboot failed" in capsys.readouterr().out


def test_reboot_system_timeout_is_caught(sync_thread, capsys):
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.time, "sleep"),
        mock.patch.object(cc.subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="systemctl", timeout=10)),
    ):
        cc.reboot_system()  # must not raise

    assert "Reboot command timed out" in capsys.readouterr().out


def test_reboot_system_generic_exception_falls_back_to_os_system(sync_thread, capsys):
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.time, "sleep"),
        mock.patch.object(cc.subprocess, "run", side_effect=RuntimeError("boom")),
        mock.patch.object(cc.os, "system") as os_system,
    ):
        cc.reboot_system()

    os_system.assert_called_once_with("sudo reboot")
    assert "Error rebooting system: boom" in capsys.readouterr().out


# --------------------------------------------------------------------------
# shutdown_system
# --------------------------------------------------------------------------


def test_shutdown_system_noop_when_not_real_hardware(sync_thread):
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=False),
        mock.patch.object(cc.subprocess, "run") as run,
    ):
        cc.shutdown_system()
    run.assert_not_called()


def test_shutdown_system_uses_systemctl_when_it_succeeds(sync_thread):
    ok = mock.Mock(returncode=0)
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.time, "sleep") as sleep,
        mock.patch.object(cc.subprocess, "run", return_value=ok) as run,
    ):
        cc.shutdown_system()

    sleep.assert_called_once_with(3)
    run.assert_called_once_with(["sudo", "systemctl", "poweroff"], capture_output=True, text=True, timeout=10)


def test_shutdown_system_falls_back_to_plain_shutdown_on_nonzero_returncode(sync_thread, capsys):
    fail = mock.Mock(returncode=1, stderr="denied")
    ok = mock.Mock(returncode=0)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return fail if cmd[1] == "systemctl" else ok

    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.time, "sleep"),
        mock.patch.object(cc.subprocess, "run", side_effect=fake_run),
    ):
        cc.shutdown_system()

    assert calls == [["sudo", "systemctl", "poweroff"], ["sudo", "shutdown", "-h", "now"]]
    assert "systemctl poweroff failed" in capsys.readouterr().out


def test_shutdown_system_timeout_is_caught(sync_thread, capsys):
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.time, "sleep"),
        mock.patch.object(cc.subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="systemctl", timeout=10)),
    ):
        cc.shutdown_system()  # must not raise

    assert "Shutdown command timed out" in capsys.readouterr().out


def test_shutdown_system_generic_exception_falls_back_to_os_system(sync_thread, capsys):
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.time, "sleep"),
        mock.patch.object(cc.subprocess, "run", side_effect=RuntimeError("boom")),
        mock.patch.object(cc.os, "system") as os_system,
    ):
        cc.shutdown_system()

    os_system.assert_called_once_with("sudo shutdown -h now")
    assert "Error shutting down system: boom" in capsys.readouterr().out
