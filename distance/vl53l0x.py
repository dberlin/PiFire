#!/usr/bin/env python3

# *****************************************
# PiFire vl53l0x Interface Library
# *****************************************
#
# Description: This library supports getting the hopper level from the
#   VL53L0X distance sensor, via Adafruit's CircuitPython library.
#
# *****************************************

from adafruit_vl53l0x import VL53L0X

from distance._tof_base import ToFHopperLevel


class HopperLevel(ToFHopperLevel):
    default_address = 0x29

    def _open_sensor(self, i2c, address):
        self.tof = VL53L0X(i2c, address=address)

    def _read_distance_mm(self):
        return self.tof.range
