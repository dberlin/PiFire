#!/usr/bin/env python3

# *****************************************
# PiFire vl53l1x Interface Library
# *****************************************
#
# Description: This library supports getting the hopper level from the
#   VL53L1X distance sensor, via Adafruit's CircuitPython library.
#
# *****************************************

import time

from adafruit_vl53l1x import VL53L1X

from distance._tof_base import ToFHopperLevel


class HopperLevel(ToFHopperLevel):
    default_address = 0x29

    def _open_sensor(self, i2c, address):
        self.tof = VL53L1X(i2c, address=address)
        self.tof.start_ranging()

    def _read_distance_mm(self):
        while not self.tof.data_ready:
            time.sleep(0.001)
        distance_cm = self.tof.distance
        self.tof.clear_interrupt()
        # The VL53L1X returns None when the target is out of range / the
        # reading is invalid. Treat that as a zero reading so the base
        # sensing loop (which expects a number) skips it cleanly.
        if distance_cm is None:
            return 0
        return distance_cm * 10

    def _close_sensor(self):
        self.tof.stop_ranging()
