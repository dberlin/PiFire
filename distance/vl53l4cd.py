#!/usr/bin/env python3

# *****************************************
# PiFire vl53l4cd Interface Library
# *****************************************
#
# Description: This library supports getting the hopper level from the
#   VL53L4CD distance sensor, via Adafruit's CircuitPython library.
#
# *****************************************

import time

from adafruit_vl53l4cd import VL53L4CD

from distance._tof_base import ToFHopperLevel


class HopperLevel(ToFHopperLevel):
	default_address = 0x29

	def _open_sensor(self, i2c, address):
		self.tof = VL53L4CD(i2c, address=address)
		self.tof.start_ranging()

	def _read_distance_mm(self):
		while not self.tof.data_ready:
			time.sleep(0.001)
		distance_cm = self.tof.distance
		self.tof.clear_interrupt()
		return distance_cm * 10

	def _close_sensor(self):
		self.tof.stop_ranging()
