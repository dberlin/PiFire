import os
import subprocess
import sys

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


def test_kill_monitor_removed():
    assert not hasattr(Process_Monitor, "kill_monitor")


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
    )
    assert result.returncode == 0, result.stderr
