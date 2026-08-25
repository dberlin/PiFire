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
    #  time.sleep goes with the thread: every non-waiting restart now opens with
    #  RESTART_GRACE_SECONDS, and running that for real would charge the suite
    #  three seconds per test to assert nothing.
    with (
        mock.patch.object(cc.threading, "Thread", _SyncThread),
        mock.patch.object(cc.time, "sleep") as slept,
    ):
        yield slept


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


def test_restart_control_asks_supervisorctl_for_control(sync_thread):
    #  is_real_hardware is pinned rather than left to the ambient datastore:
    #  it reads settings["platform"]["real_hw"], so without this the test's
    #  meaning depends on whichever settings blob happens to be loaded.
    ok = mock.Mock(returncode=0, stderr="")
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.subprocess, "run", return_value=ok) as run,
    ):
        cc.restart_control()

    assert run.call_args.args[0] == ["sudo", "supervisorctl", "restart", "control"]


def test_restart_webapp_asks_supervisorctl_for_webapp(sync_thread):
    ok = mock.Mock(returncode=0, stderr="")
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.subprocess, "run", return_value=ok) as run,
    ):
        cc.restart_webapp()

    assert run.call_args.args[0] == ["sudo", "supervisorctl", "restart", "webapp"]


def test_every_restart_runs_through_the_same_mechanism(sync_thread):
    """These three used to be two mechanisms. restart_control and
    restart_webapp shelled out with `os.system("sleep 3 && ... &")` -- no
    timeout, no DEVNULL on stdin, and a failure that went nowhere -- while
    restart_scripts used subprocess with all three. A caller had no way to know
    which behaviour it was getting from the name."""
    ok = mock.Mock(returncode=0, stderr="")
    seen = []
    for call in (cc.restart_control, cc.restart_webapp, cc.restart_scripts):
        with (
            mock.patch.object(cc, "is_real_hardware", return_value=True),
            mock.patch.object(cc.subprocess, "run", return_value=ok) as run,
            mock.patch.object(cc.os, "system") as shelled,
        ):
            call()
        seen.append(run.call_args)
        shelled.assert_not_called()

    for call_args in seen:
        assert call_args.kwargs["stdin"] is subprocess.DEVNULL
        assert call_args.kwargs["start_new_session"] is True
        assert call_args.kwargs["timeout"] == 60


def test_restart_control_noop_when_not_real_hardware(sync_thread):
    """These two were the only lifecycle calls in this module WITHOUT the
    is_real_hardware() gate that reboot_system, shutdown_system and
    restart_scripts all have -- so on a dev box they shelled out to sudo
    supervisorctl where every neighbouring function was a no-op."""
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=False),
        mock.patch.object(cc.subprocess, "run") as run,
    ):
        cc.restart_control()
    run.assert_not_called()


def test_restart_webapp_noop_when_not_real_hardware(sync_thread):
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=False),
        mock.patch.object(cc.subprocess, "run") as run,
    ):
        cc.restart_webapp()
    run.assert_not_called()


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


def test_restart_scripts_asks_supervisorctl_to_restart_the_programs(sync_thread):
    """One command, not a walk through service names.

    The supervisor UNIT is `supervisor` on Debian / Raspberry Pi OS and
    `supervisord` on Fedora / RHEL, and this had no way to tell which -- so it
    tried each in turn, and each installer's sudoers grant names only its own.
    `supervisorctl` is one name on both, and both installers grant it.
    """
    ok = mock.Mock(returncode=0, stderr="")
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.subprocess, "run", return_value=ok) as run,
    ):
        cc.restart_scripts()

    assert run.call_count == 1
    assert run.call_args.args[0] == ["sudo", "supervisorctl", "restart", "all"]


def test_restart_scripts_never_lets_sudo_reach_a_password_prompt(sync_thread):
    """With a tty on stdin, a sudo outside the NOPASSWD rule blocks at the
    prompt until the timeout and restarts nothing."""
    ok = mock.Mock(returncode=0, stderr="")
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.subprocess, "run", return_value=ok) as run,
    ):
        cc.restart_scripts()

    assert run.call_args.kwargs["stdin"] is subprocess.DEVNULL


def test_restart_scripts_survives_restarting_the_webapp_it_runs_inside(sync_thread):
    """`restart all` includes `webapp`, which is this very process. Without its
    own session the client can be taken down part-way through the sequence,
    leaving the remaining programs stopped."""
    ok = mock.Mock(returncode=0, stderr="")
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.subprocess, "run", return_value=ok) as run,
    ):
        cc.restart_scripts()

    assert run.call_args.kwargs["start_new_session"] is True


def test_restart_scripts_reports_a_refusal_without_raising(sync_thread, capsys):
    fail = mock.Mock(returncode=1, stderr="unix:///var/run/supervisor.sock refused connection\n")
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.subprocess, "run", return_value=fail),
    ):
        cc.restart_scripts()  # must not raise

    out = capsys.readouterr().out
    assert "Failed to restart supervisor program(s)" in out
    assert "refused connection" in out, "the reason is the only thing an operator can act on"


def test_restart_scripts_survives_a_timeout(sync_thread, capsys):
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="supervisorctl", timeout=60)),
    ):
        cc.restart_scripts()  # must not raise

    assert "timed out" in capsys.readouterr().out


def test_restart_scripts_survives_a_missing_supervisorctl(sync_thread, capsys):
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.subprocess, "run", side_effect=FileNotFoundError("no supervisorctl")),
    ):
        cc.restart_scripts()  # must not raise

    assert "Error running supervisorctl" in capsys.readouterr().out


# --------------------------------------------------------------------------
# wait=True -- for a caller that is about to exit
# --------------------------------------------------------------------------


def test_waiting_restart_does_not_use_a_thread_at_all():
    """updater.py exits the moment publish_finished returns. A daemon thread
    dies with its process, so the default path would never have run
    supervisorctl -- the update would announce a restart and perform none,
    which is exactly the bug wait=True exists for. No sync_thread fixture
    here: the point is that threading is not reached."""
    ok = mock.Mock(returncode=0, stderr="")
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.subprocess, "run", return_value=ok) as run,
        mock.patch.object(cc.threading, "Thread") as thread,
    ):
        cc.restart_scripts(wait=True)

    thread.assert_not_called()
    assert run.call_args.args[0] == ["sudo", "supervisorctl", "restart", "all"]


def test_waiting_restart_skips_the_grace_period():
    """The grace exists to let an in-flight response escape before the server
    answering it dies. A caller that waits has no response to protect, and
    sleeping would only delay a process whose whole job is now finished."""
    ok = mock.Mock(returncode=0, stderr="")
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.subprocess, "run", return_value=ok),
        mock.patch.object(cc.time, "sleep") as slept,
    ):
        cc.restart_scripts(wait=True)

    slept.assert_not_called()


def test_a_non_waiting_restart_lets_the_response_out_first(sync_thread):
    """Restarting `all` kills the webapp answering the request that asked for
    it. Without the pause the client can lose the connection before the reply
    lands, and a restart that worked looks like one that failed."""
    ok = mock.Mock(returncode=0, stderr="")
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.subprocess, "run", return_value=ok),
    ):
        cc.restart_scripts()

    sync_thread.assert_called_once_with(cc.RESTART_GRACE_SECONDS)


def test_a_waiting_restart_still_reports_a_refusal(capsys):
    fail = mock.Mock(returncode=1, stderr="refused connection\n")
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=True),
        mock.patch.object(cc.subprocess, "run", return_value=fail),
    ):
        cc.restart_scripts(wait=True)  # must not raise

    assert "refused connection" in capsys.readouterr().out


def test_a_waiting_restart_is_still_gated_on_real_hardware():
    with (
        mock.patch.object(cc, "is_real_hardware", return_value=False),
        mock.patch.object(cc.subprocess, "run") as run,
    ):
        cc.restart_scripts(wait=True)
    run.assert_not_called()


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
    run.assert_called_once_with(
        ["sudo", "systemctl", "reboot"], capture_output=True, text=True, timeout=10, check=False
    )


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
    run.assert_called_once_with(
        ["sudo", "systemctl", "poweroff"], capture_output=True, text=True, timeout=10, check=False
    )


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
