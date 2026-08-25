"""
==============================================================================
 PiFire Process Monitor
==============================================================================

Description: This class object can be generated to both generate heartbeats
    and monitor heartbeats from a running process.  If the heartbeat fails to
    register before a set timeout, then the monitor will log the incident, run a
    recovery callable and stop.

    process    = (str) Name of the process being monitored, used in logging
    on_timeout = (callable) Recovery to run when the heartbeat stops. Pass one of
                 common/system.py's lifecycle functions rather than a command:
                 that module owns how PiFire restarts things, including the
                 `sudo` the installers grant NOPASSWD for.
    timeout    = (int/float) Time in seconds to wait before logging an error and recovering

==============================================================================
"""

"""
==============================================================================
 Imported Modules
==============================================================================
"""
import logging
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
    def __init__(self, process, on_timeout, timeout=5):
        self.process = process  # name of the process to monitor
        self.timeout = timeout  # time in seconds to wait before logging an error and running the recovery
        #  A callable, not an argv list. This took `["supervisorctl", "restart",
        #  "control"]` and ran it verbatim -- with no `sudo`, unlike every other
        #  supervisorctl call in the tree, and the installers grant NOPASSWD for
        #  `sudo supervisorctl` specifically. Recovery from a hung control loop
        #  was one permission away from never working, and nothing pointed at
        #  it because the command lived here rather than in common/system.py.
        self.on_timeout = on_timeout

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
                    # Recover on real hardware only. The recovery itself gates
                    # on real hardware again -- it is common/system.py's rule,
                    # not this module's -- but the print is the dev-box
                    # behaviour worth keeping.
                    if self.is_real_hw:
                        self.on_timeout()
                    else:
                        print(message)
                    self.active = False  # Pause thread
                time.sleep(1)
            if self.kill:
                break
            time.sleep(0.25)
