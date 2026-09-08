import os
import subprocess
import sys

import pytest

from common.modes import Mode
from controller.runtime.clock import ManualClock
from common.process_mon import Process_Monitor

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def test_stop_monitor_terminates_the_thread():
    mon = Process_Monitor("test", ["true"], timeout=30)
    thread = mon.process_thread
    assert thread.is_alive()
    mon.start_monitor()
    mon.stop_monitor()
    # The heartbeat loop sleeps up to 1s between checks; give it margin to exit.
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert mon.status() == "killed"


def test_an_unstopped_monitor_does_not_keep_the_process_alive(tmp_path):
    """A monitor nobody stopped must not block interpreter exit.

    stop_monitor() is the LAST line of ControlMode.run(), so an exception
    escaping the work cycle skips it. If the heartbeat thread were non-daemon
    the control process could not exit at all: it would hang with the thread
    still looping, and 30 seconds later that thread would run `supervisorctl
    restart control` to recover a process that had merely been waiting for it.

    Run in a subprocess because the assertion IS "the interpreter exits", which
    is unobservable from inside the interpreter making it. `start_monitor()` is
    deliberately never called, so `active` stays False and the timeout branch --
    the one that shells out -- is unreachable for the whole test.
    """
    program = (
        "from common.process_mon import Process_Monitor\nProcess_Monitor('daemon_exit_probe', ['true'], timeout=30)\n"
    )
    # Its own PIFIRE_DB_PATH, so the child seeds a fresh default datastore
    # rather than reading the repo's live one: __init__ needs
    # settings['platform']['real_hw'], and in the full suite earlier tests leave
    # that blob mid-edit -- which made this pass alone and fail in the suite.
    # cwd stays REPO_ROOT regardless: seeding defaults reads
    # updater/updater_manifest.json by relative path, and create_logger needs
    # ./logs/ to exist.
    env = {**os.environ, "PIFIRE_DB_PATH": str(tmp_path / "pifire.db")}
    # The timeout bounds the failure, it is not the assertion: against a
    # non-daemon thread this child never exits, so without it the suite would
    # hang here forever instead of reporting which test broke.
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("wall_jump", [-3600, 3600])
def test_watchdog_timeout_uses_thirty_elapsed_seconds(monkeypatch, wall_jump):
    import common.process_mon as module

    clock = ManualClock(wall_start=1_800_000_000, monotonic_start=100)
    snapshots = []
    recoveries = []
    notifications = []
    monkeypatch.setattr(module.threading.Thread, "start", lambda self: None)
    monkeypatch.setattr(module, "is_real_hardware", lambda: True)
    monkeypatch.setattr(module, "read_control", lambda: {})
    monkeypatch.setattr(module, "write_control_snapshot", lambda control, **kwargs: snapshots.append(control))
    monkeypatch.setattr(module, "send_notifications", notifications.append)
    monkeypatch.setattr(module.time, "time", clock.wall_time)
    monitor = Process_Monitor("test", lambda: recoveries.append("recovered"), timeout=30, monotonic=clock.monotonic)
    monitor.heartbeat()
    monitor.start_monitor()
    clock.jump_wall(wall_jump)
    clock.advance(29.9)
    advances = iter((0.1, 0.1))

    def next_check(seconds):
        if recoveries:
            monitor.stop_monitor()
            return
        assert snapshots == []
        assert notifications == []
        clock.advance(next(advances))

    monkeypatch.setattr(module.time, "sleep", next_check)
    try:
        monitor._heartbeat_check()
    finally:
        monitor.stop_monitor()
    assert snapshots == [{"updated": True, "mode": Mode.ERROR, "critical_error": True}]
    assert recoveries == ["recovered"]
    assert notifications == ["Control_Process_Stopped"]
