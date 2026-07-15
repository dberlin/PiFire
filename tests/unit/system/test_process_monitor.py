import time

from common.process_mon import Process_Monitor


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
