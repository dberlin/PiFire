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

from distance.intervals import SENSOR_SAMPLE_INTERVAL


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

        if self.empty <= self.full:
            event = "ERROR: Invalid Hopper Level Configuration Empty Level <= Full Level (forcing defaults)"
            self.logger.error(event)
            # Set defaults that are valid
            self.empty = 22
            self.full = 4

    def _start_sampling(self):
        """Start the background sampling thread. Subclasses call this at the
        end of __init__, once the sensor is open and readable."""
        self.sensor_thread_active = True
        self.sensor_thread_read_interval = SENSOR_SAMPLE_INTERVAL
        self.sample_requested = True  # take one immediately on startup
        self.sensor_thread = threading.Thread(target=self._sensing_loop)
        self.sensor_thread.start()

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

                # Read the sensor multiple times and average the result
                avg_dist = 0
                start_time = time.time()

                for reading in range(self.samples_per_cycle):
                    distance = self._read_distance_mm()
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

                # Counted LAST, so an observer that waits for the count to rise
                # sees a fully finished cycle -- reading published and any
                # re-init already done -- rather than a half-completed one.
                self.sample_count += 1
                sample_time = time.time()
            time.sleep(1)

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

        Takes no `override` argument on purpose -- see the class docstring."""
        return self.distance_read
