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

from common.i2c_bus import open_i2c_bus
from common.i2c_bus_config import parse_i2c_bus
from distance._sampled_base import SampledHopperLevel


class ToFHopperLevel(SampledHopperLevel):
    default_address = 0x29

    sensor_label = "TOF sensor"

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

        self._restart_sensor()
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

    def _read_distance_mm(self):
        """Return a single distance reading in millimeters. Subclasses must
        implement this."""
        raise NotImplementedError

    def _close_sensor(self):
        """Stop ranging / release the sensor. Optional; no-op by default."""
        pass
