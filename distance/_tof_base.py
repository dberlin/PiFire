#!/usr/bin/env python3

# *****************************************
# PiFire ToF (Time-of-Flight) Hopper Level Base
# *****************************************
#
# Description: I2C-bus resolution and sensor open/re-open logic for the
#   VL53L0X, VL53L4CD and VL53L1X time-of-flight distance sensors. The
#   sampling thread, percentage math and cached get_level() contract live in
#   distance/_sampled_base.py, shared with the other transports. Each sensor
#   module subclasses ToFHopperLevel and implements _open_sensor,
#   _read_distance_mm, and (optionally) _close_sensor.
#
# *****************************************

import time

from common.i2c_bus import open_i2c_bus
from common.i2c_bus_config import parse_i2c_bus
from distance._sampled_base import SampledHopperLevel
from distance.intervals import TOF_DATA_READY_POLL, TOF_READ_DEADLINE


class ToFReadTimeout(RuntimeError):
    """A ToF sensor did not report data ready inside its deadline.

    A RuntimeError, which is also what the Adafruit VL53L0X driver raises from
    its own bounded poll, so all three ToF sensors signal a read that gave up
    the same way and the sampling loop needs one rule for them.
    """


class ToFHopperLevel(SampledHopperLevel):
    default_address = 0x29

    sensor_label = "TOF sensor"

    # How long one reading may wait for the sensor, and how often that wait
    # asks. Both are transactions on the shared I2C bus; the values and the
    # reasoning behind them live in distance/intervals.py.
    read_deadline_seconds = TOF_READ_DEADLINE
    data_ready_poll_seconds = TOF_DATA_READY_POLL

    def __init__(self, dev_pins, empty=22, full=4, debug=False):
        super().__init__(empty=empty, full=full, debug=debug)

        distance_pins = (dev_pins or {}).get("distance", {}) or {}
        self.bus = parse_i2c_bus(distance_pins.get("i2c_bus") or {"kind": "basic"})
        address = distance_pins.get("address")
        if address is None:
            self.address = self.default_address
        elif isinstance(address, str):
            self.address = int(address, 16)
        else:
            self.address = address

        self._open_with_deadline(self._restart_sensor)
        # Setup & Start Sensor Loop Thread
        self._start_sampling()

    def _open_i2c_bus(self):
        return open_i2c_bus(self.bus)

    def _restart_sensor(self):
        i2c = self._open_i2c_bus()
        self._open_sensor(i2c, self.address)

    def _open_sensor(self, i2c, address):
        """Construct the Adafruit driver instance at `address` on `i2c`, start
        ranging if the chip requires it, and set self.tof. Subclasses must
        implement this."""
        raise NotImplementedError

    def _await_data_ready(self):
        """Wait for `self.tof.data_ready`, and give up on it rather than wait
        forever.

        `data_ready` is a register read, so each check takes and releases the
        lock on a bus the probes and the grill platform share. A sensor that
        stops asserting it therefore keeps that bus busy for as long as the
        controller runs, which starves every other device on it. Reaching the
        deadline ends the reading; the sampling loop counts the cycle as failed
        and holds the sensor off the bus for a while.
        """
        deadline = time.monotonic() + self.read_deadline_seconds
        while not self.tof.data_ready:
            if time.monotonic() >= deadline:
                raise ToFReadTimeout(
                    f"The {self.sensor_label} did not report a reading within "
                    f"{self.read_deadline_seconds}s of being asked."
                )
            time.sleep(self.data_ready_poll_seconds)

    def _read_distance_mm(self):
        """Return a single distance reading in millimeters. Subclasses must
        implement this."""
        raise NotImplementedError

    def _close_sensor(self):
        """Stop ranging / release the sensor. Optional; no-op by default."""
        pass
