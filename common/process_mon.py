#!/usr/bin/env python3

"""
==============================================================================
 PiFire Process Monitor
==============================================================================

Description: This class object can be generated to both generate heartbeats
    and monitor heartbeats from a running process.  If the heartbeat fails to
    register before a set timeout, then the monitor will log the incident, fire off a command
    and stop.

    process = (str) Name of the process being monitored, used in logging
    command = (list) Command in subprocess format (i.e. ['echo', 'This is an example message.'])
    timeout = (int/float) Time in seconds to wait before logging an error and running the command

==============================================================================
"""

"""
==============================================================================
 Imported Modules
==============================================================================
"""
import logging
import subprocess
import threading
import time

from common.common import create_logger, log_path
from common.modes import Mode
from common.persistence.control import read_control, write_control_snapshot
from common.system import is_real_hardware
from notify.notifications import send_notifications

"""
==============================================================================
 Class Definition
==============================================================================
"""


class Process_Monitor:
    def __init__(self, process, command, timeout=5):
        self.process = process  # name of the process to monitor
        self.timeout = timeout  # time in seconds to wait before logging an error and running the specified command
        self.command = command  # subprocess formatted command to run when a timeout occurs

        self.last_heartbeat = time.time()
        self.active = False
        self.kill = False

        self.is_real_hw = is_real_hardware()

        # Setup logging
        log_level = logging.ERROR
        self.process_logger = create_logger(self.process, filename=log_path(f"{self.process}.log"), level=log_level)
        self.event_logger = create_logger(
            "events",
            filename=log_path("events.log"),
            messageformat="%(asctime)s [%(levelname)s] %(message)s",
            level=log_level,
        )

        # Setup process monitoring thread. Daemon, because a non-daemon one
        # keeps the interpreter alive at shutdown: this loop only ends when
        # stop_monitor() sets `kill`, and the one caller (ControlMode.run) sets
        # it on its LAST line, after all teardown. Any exception escaping the
        # work cycle therefore skipped it, and the control process could not
        # exit -- it hung with a live thread that 30 seconds later ran
        # `supervisorctl restart control` to recover itself. As a daemon it dies
        # with the process instead, so a crash exits, and the supervisor sees
        # the exit and restarts immediately. Monitoring is unaffected: what this
        # thread guards is a control loop that HANGS without raising, and a
        # hung process is not exiting for the daemon flag to matter to.
        self.process_thread = threading.Thread(target=self._heartbeat_check, daemon=True)
        self.process_thread.start()

    def heartbeat(self):
        self.last_heartbeat = time.time()

    def start_monitor(self):
        self.active = True

    def stop_monitor(self):
        # Terminate the heartbeat thread. base.run() builds a fresh
        # Process_Monitor per work cycle, so stopping always means "done with
        # this one" -- there is no restart-the-same-instance case to preserve.
        self.active = False
        self.kill = True

    def status(self):
        if self.kill:
            return "killed"
        if self.active:
            return "active"
        else:
            return "inactive"

    def _heartbeat_check(self):
        while True:
            while self.active:
                now = time.time()
                if now - self.last_heartbeat > self.timeout:
                    # Set control process critical error flag
                    control = read_control()
                    control["updated"] = True
                    control["mode"] = Mode.ERROR
                    control["critical_error"] = True
                    write_control_snapshot(control, origin="process_monitor")
                    # Send notification
                    send_notifications("Control_Process_Stopped")
                    # Log error
                    message = f"The {self.process} process experienced a timeout event (no heartbeat detected in {self.timeout} seconds) and is being reset."
                    self.event_logger.error(message)
                    self.process_logger.error(message)
                    # Execute command on real hardware only
                    if self.is_real_hw:
                        subprocess.run(self.command)
                    else:
                        print(message)
                    self.active = False  # Pause thread
                time.sleep(1)
            if self.kill:
                break
            time.sleep(0.25)
