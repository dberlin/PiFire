#!/usr/bin/env python3

# *****************************************
# PiFire Background-Sampled Hopper Level Base
# *****************************************
#
# Description: The sampling thread, hopper-percentage math and cached
#   `get_level()` contract shared by every distance sensor that must not be
#   read on the control loop's thread.
#
#   Transport-specific bases subclass this and supply the two hooks:
#   `_read_distance_mm` (one reading) and `_restart_sensor` (re-open the
#   underlying device when a sample cycle looks stuck). See
#   distance/_tof_base.py (I2C), distance/_serial_tof_base.py (USB serial)
#   and distance/hcsr04.py (GPIO ultrasonic).
#
#   This used to be duplicated per transport -- _serial_tof_base.py was
#   deliberately forked from _tof_base.py to avoid touching the shipped I2C
#   drivers for a single new consumer. Once the sampling interval became a
#   tuned value shared by all of them (distance/intervals.py) and the
#   ultrasonic sensor needed the same treatment, a third copy would have put
#   the same loop in three files. It is one loop now; the transports keep
#   their own bases and their own re-init behavior.
#
# *****************************************

import threading
import logging
import time

# The watchdog binds these directly instead of going through the `time` module
# attribute the sampling loop reads. It has to measure elapsed real time on a
# clock that cannot be stepped backwards by a clock adjustment, and it has to
# keep its own pacing whatever the sampling loop's clock is doing.
from time import monotonic as _monotonic, sleep as _sleep

from distance.intervals import SENSOR_OPEN_DEADLINE, SENSOR_SAMPLE_INTERVAL


class SensorOpenTimeout(RuntimeError):
    """A sensor did not finish opening inside its deadline.

    Raised on the constructing thread, so `build_devices()` substitutes
    distance.none and records an operator banner -- the same fallback it
    already takes for a sensor that fails to open outright.
    """


class SampledHopperLevel:
    """Samples a distance sensor on a background thread and serves the last
    result from cache.

    THERE IS NO WAY TO WAIT FOR A MEASUREMENT FROM HERE, BY DESIGN. `get_level()`
    returns the cached percentage and returns now; `request_sample()` asks the
    sampling thread for a fresh reading and returns now. A caller reads a value
    some other thread already produced, or it reads nothing -- it never blocks.

    This used to be one method, `get_level(override=True)`, which set the
    request flag and then waited on a `threading.Event` for up to 3 SECONDS. Its
    only reason to exist was that nothing refreshed the hopper level on its own,
    so an on-demand check had to both ask and wait. The control loop now
    refreshes on a timer (distance/intervals.py), so the wait has no reason left
    -- and a 3s wait inside a loop that is timing the auger and igniter was
    never something to keep as a convenience. Asking and observing are separate
    operations now, and only the asking half is callable.
    """

    # Number of `_read_distance_mm()` readings averaged into one sample.
    samples_per_cycle = 3

    # A sample cycle slower than this means the sensor is not answering
    # normally, so re-initialize it. Sized per transport: the ToF sensors
    # answer in ~0.1-0.2s, the ultrasonic HC-SR04 deliberately takes ~1.1s.
    slow_cycle_seconds = 0.5

    # A sample cycle still in flight after this long is not slow, it is stuck:
    # no supported transport answers this late and then answers correctly. Set
    # well clear of slow_cycle_seconds (0.5s here, 2.0s for the ultrasonic) so
    # that a merely slow sensor is left to the re-initialization path rather
    # than being written off.
    stuck_cycle_seconds = 30

    # How often the watchdog compares the in-flight cycle's age against
    # stuck_cycle_seconds. It also bounds how long the watchdog thread outlives
    # `sensor_thread_active` going false.
    watchdog_poll_seconds = 1.0

    # How long __init__ waits for the sensor to open before giving up on it.
    # Overridable per transport; the shared value and the reasoning behind it
    # live in distance/intervals.py.
    open_deadline_seconds = SENSOR_OPEN_DEADLINE

    # Names the sensor in the stuck-sensor warning.
    sensor_label = "sensor"

    def __init__(self, empty=22, full=4, debug=False):
        self.logger = logging.getLogger("events")
        self.empty = empty  # Empty is greater than distance measured for empty
        self.full = full  # Full is less than or equal to the minimum full distance.
        self.debug = debug
        # The value every read serves until the sampling thread has produced a
        # real one. A fresh process publishes this and moves on rather than
        # waiting for the first sample.
        self.distance_read = 100
        # Number of samples completed since construction. Nothing in the
        # control path reads it; it exists so a caller (or a test) can observe
        # sampling progress WITHOUT a blocking wait primitive being available.
        self.sample_count = 0
        # False once the watchdog has given up on a read that never came back.
        # The cached level keeps being served either way -- see get_level().
        self.sensor_healthy = True
        # Monotonic stamp of the cycle currently in flight, or None between
        # cycles. The watchdog reads it from its own thread, which is the only
        # place a check can run while the sampling thread is stuck.
        self._cycle_started_at = None
        # Keeps the operator banner to one per stuck episode rather than one
        # per watchdog check.
        self._stuck_reported = False

        if self.empty <= self.full:
            event = "ERROR: Invalid Hopper Level Configuration Empty Level <= Full Level (forcing defaults)"
            self.logger.error(event)
            # Set defaults that are valid
            self.empty = 22
            self.full = 4

    def _open_with_deadline(self, open_sensor):
        """Run `open_sensor` on a throwaway worker and give up on it after
        `open_deadline_seconds`.

        Constructors go through this instead of opening inline, because they
        run on the control process's boot path: a sensor that never answers its
        open would otherwise mean control.py never finishes starting -- no
        control loop, no API.

        Every outcome that arrives in time reaches the caller unchanged. A
        normal open returns, and a failing open re-raises its own exception on
        the caller's thread, so `build_devices()` falls back to distance.none
        exactly as it always has. An overrun raises SensorOpenTimeout, which
        takes that same fallback.

        A worker that overruns is abandoned, never joined: a thread parked
        inside a driver's I2C or serial read cannot be cancelled from outside,
        so it is a daemon the interpreter will not wait for, and anything it
        eventually writes lands on an object the caller has already discarded.
        """
        outcome = {}
        finished = threading.Event()

        def _open():
            try:
                open_sensor()
            except BaseException as error:
                outcome["error"] = error
            finally:
                finished.set()

        worker = threading.Thread(target=_open, name=f"open {self.sensor_label}", daemon=True)
        worker.start()

        if not finished.wait(self.open_deadline_seconds):
            raise SensorOpenTimeout(
                f"The {self.sensor_label} did not finish opening within {self.open_deadline_seconds}s "
                "and has been left behind. Check that it is wired and powered, and that nothing else "
                "is holding its bus."
            )

        error = outcome.get("error")
        if error is not None:
            raise error

    def _start_sampling(self):
        """Start the background sampling thread. Subclasses call this at the
        end of __init__, once the sensor is open and readable."""
        self.sensor_thread_active = True
        self.sensor_thread_read_interval = SENSOR_SAMPLE_INTERVAL
        self.sample_requested = True  # take one immediately on startup
        self.sensor_thread = threading.Thread(target=self._sensing_loop)
        self.sensor_thread.start()
        # Deliberately not the sampling thread: a check that runs on the thread
        # doing the reading cannot run while that read is stuck, which is the
        # only case worth checking for. A daemon, so an abandoned sensor never
        # holds the process open.
        self.watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self.watchdog_thread.start()

    # ---- stuck-sensor watchdog ----

    def _watchdog_loop(self):
        """Watch the in-flight sample cycle's age from a thread the sampling
        loop cannot block."""
        while self.sensor_thread_active:
            self._check_stuck_cycle()
            _sleep(self.watchdog_poll_seconds)

    def _check_stuck_cycle(self):
        """Record the sensor as unhealthy once the cycle in flight has outlived
        `stuck_cycle_seconds`.

        The read itself cannot be interrupted -- nothing in Python can
        interrupt a thread parked in a driver call -- so this reports the state
        and lets the sampling loop stop trusting it."""
        started = self._cycle_started_at
        if started is None or self._stuck_reported:
            return
        if _monotonic() - started <= self.stuck_cycle_seconds:
            return

        self.sensor_healthy = False
        self._stuck_reported = True
        self.logger.error(
            f"The {self.sensor_label} stopped answering part-way through a reading and has been "
            f"silent for over {self.stuck_cycle_seconds}s. The hopper level on the dashboard is the "
            "last one it reported. The reading cannot be cancelled; sampling resumes on its own if "
            "the sensor answers again."
        )

    def _recover_from_stuck_read(self):
        """Re-open a sensor the watchdog gave up on, and put the loop back to
        work once that succeeds.

        Until it does, the loop keeps cycling without publishing: a reading
        that arrives minutes late describes a moment nobody can place, so it
        has no claim on the cached level."""
        try:
            self._restart_sensor()
        except Exception:
            self.logger.exception(f"Re-initializing the {self.sensor_label} after a stuck reading failed.")
            return

        self.sensor_healthy = True
        self._stuck_reported = False
        self.logger.info(f"The {self.sensor_label} is answering again; hopper readings have resumed.")

    # ---- transport hooks ----

    def _read_distance_mm(self):
        """Return a single distance reading in millimeters. Subclasses must
        implement this."""
        raise NotImplementedError

    def _restart_sensor(self):
        """Re-open the underlying device after a sample cycle ran long.
        Subclasses must implement this."""
        raise NotImplementedError

    # ---- sampling ----

    def _level_from_distance_cm(self, avg_dist):
        # If Average Distance is less than the full distance, we are at 100%
        if avg_dist <= self.full:
            return 100
        # If Average Distance is less than the empty distance, calculate percentage
        if avg_dist <= self.empty:
            capacity = self.empty - self.full
            adjusted_ratio = (self.empty / capacity) * 100
            return adjusted_ratio * (1 - (avg_dist / self.empty))
        # If Average Distance is higher than empty distance, report 0 level
        return 0

    def _sensing_loop(self):
        """This loop should run in a thread so that it does not stall the main control process"""
        sample_time = time.time()
        while self.sensor_thread_active:
            now = time.time()
            if self.sample_requested or (now > sample_time + self.sensor_thread_read_interval):
                # Clear the request BEFORE reading, not after. A request that
                # arrives while this cycle is in flight then survives into the
                # next iteration and gets its own fresh reading, instead of
                # being silently swallowed by the cycle it just missed.
                self.sample_requested = False

                if self.sensor_healthy:
                    self._take_sample()
                else:
                    # A sensor the watchdog has given up on gets re-opened, not
                    # re-read: another read would only be aimed at the device
                    # that is already not answering.
                    self._recover_from_stuck_read()

                # Counted LAST, so an observer that waits for the count to rise
                # sees a fully finished cycle -- reading published and any
                # re-init already done -- rather than a half-completed one.
                self.sample_count += 1
                sample_time = time.time()
            time.sleep(1)

    def _take_sample(self):
        """Average `samples_per_cycle` readings, publish the level, and
        re-initialize a sensor that answered slowly."""
        # Read the sensor multiple times and average the result
        avg_dist = 0
        start_time = time.time()
        # Stamped for the whole cycle -- the reads AND the re-initialization a
        # slow cycle triggers, either of which can park on the device -- so the
        # watchdog, on its own thread, can see how long the cycle has been in
        # flight WHILE it is in flight.
        self._cycle_started_at = _monotonic()
        try:
            for reading in range(self.samples_per_cycle):
                distance = self._read_distance_mm()
                if not self.sensor_healthy:
                    # The watchdog gave up on this cycle while the read was
                    # outstanding. What came back describes an unplaceable
                    # moment, and the rest of the burst would be aimed at a
                    # sensor already known not to be answering.
                    #
                    # Cleared before recovering, so a re-open that succeeds
                    # cannot be read by the watchdog as a cycle already
                    # long overdue and set off a second banner.
                    self._cycle_started_at = None
                    self._recover_from_stuck_read()
                    return
                if distance > 0:
                    if avg_dist > 0:
                        avg_dist = (avg_dist + distance) / 2
                    else:
                        avg_dist = distance

            # Convert mm to cm
            avg_dist = avg_dist / 10

            if self.debug:
                event = "* Average Distance Measured: " + str(avg_dist) + "cm"
                self.logger.debug(event)

            self.distance_read = int(self._level_from_distance_cm(avg_dist))

            # If it took a long time to get sensor data, then the sensor might be having issues
            if (time.time() - start_time) > self.slow_cycle_seconds:
                event = (
                    f"Warning: The {self.sensor_label} took longer than normal to get a reading.  "
                    "Re-initializing the sensor."
                )
                self.logger.info(event)
                self._restart_sensor()  # Attempt re-init of sensor
        finally:
            self._cycle_started_at = None

    # ---- public API ----

    def set_level(self, level=100):
        # Do nothing
        return ()

    def update_distances(self, empty=22, full=4):
        self.empty = empty
        self.full = full

    def get_distances(self):
        levels = {}
        levels["empty"] = self.empty
        levels["full"] = self.full
        return levels

    def request_sample(self):
        """Ask the sampling thread to take a fresh reading, and return NOW.

        The reading lands in the cache within a second or so, and reaches the
        datastore on the control loop's next timed refresh. Nothing waits: this
        sets a flag the sampling thread is already watching."""
        self.sample_requested = True

    def get_level(self):
        """Return the cached hopper level. Costs nothing and never blocks.

        Serves the last reading the sensor produced even after the watchdog has
        declared it stuck. This is an int percentage the control loop copies
        straight into pelletdb and the dashboard renders, so there is no value
        in range that means "unknown": 0 would read as an empty hopper and 100
        as a full one, and both would be believed. Pellet level also moves
        slowly, which leaves the last known value the best available estimate
        of it. Staleness is reported out of band instead, by `sensor_healthy`
        and by the operator banner the watchdog logs.

        Takes no `override` argument on purpose -- see the class docstring."""
        return self.distance_read
