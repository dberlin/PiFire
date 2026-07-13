#!/usr/bin/env python3

# *****************************************
# PiFire ToF (Time-of-Flight) Hopper Level Base
# *****************************************
#
# Description: Shared threading / hopper-percentage-calculation / I2C-bus
#   resolution logic for the VL53L0X, VL53L4CD and VL53L1X time-of-flight
#   distance sensors. Each sensor module subclasses ToFHopperLevel and implements
#   _open_sensor, _read_distance_mm, and (optionally) _close_sensor.
#
# *****************************************

import threading
import logging
import time

from common.i2c_bus import open_i2c_bus


class ToFHopperLevel:
	default_address = 0x29

	def __init__(self, dev_pins, empty=22, full=4, debug=False):
		self.logger = logging.getLogger('events')
		self.empty = empty  # Empty is greater than distance measured for empty
		self.full = full  # Full is less than or equal to the minimum full distance.
		self.debug = debug
		self.distance_read = 100

		self.event = threading.Event()

		if self.empty <= self.full:
			event = 'ERROR: Invalid Hopper Level Configuration Empty Level <= Full Level (forcing defaults)'
			self.logger.error(event)
			# Set defaults that are valid
			self.empty = 22
			self.full = 4

		distance_pins = (dev_pins or {}).get('distance', {}) or {}
		self.i2c_bus_kind = distance_pins.get('i2c_bus_kind', 'basic')
		self.i2c_bus_num = distance_pins.get('i2c_bus_num', 'CP2112')
		address = distance_pins.get('address')
		if address is None:
			self.address = self.default_address
		elif isinstance(address, str):
			self.address = int(address, 16)
		else:
			self.address = address

		self.__start_sensor()
		# Setup & Start Sensor Loop Thread
		self.sensor_thread_active = True
		self.sensor_thread_read_interval = 60  # Read sensor every 60 seconds
		self.sensor_thread_override = True  # Allow override to do direct reads
		self.sensor_thread = threading.Thread(target=self._sensing_loop)
		self.sensor_thread.start()

	def _open_i2c_bus(self):
		return open_i2c_bus(self.i2c_bus_kind, self.i2c_bus_num)

	def __start_sensor(self):
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

	def _sensing_loop(self):
		"""This loop should run in a thread so that it does not stall the main control process"""
		sample_time = time.time()
		while self.sensor_thread_active:
			now = time.time()
			if self.sensor_thread_override or (now > sample_time + self.sensor_thread_read_interval):
				# Read the sensor multiple times and average the result
				avg_dist = 0
				start_time = time.time()

				for reading in range(3):
					distance = self._read_distance_mm()
					if distance > 0:
						if avg_dist > 0:
							avg_dist = (avg_dist + distance) / 2
						else:
							avg_dist = distance

				# Convert mm to cm
				avg_dist = avg_dist / 10

				if self.debug:
					event = '* Average Distance Measured: ' + str(avg_dist) + 'cm'
					self.logger.debug(event)

				# If Average Distance is less than the full distance, we are at 100%
				if avg_dist <= self.full:
					level = 100
				# If Average Distance is less than the empty distance, calculate percentage
				elif avg_dist <= self.empty:
					capacity = self.empty - self.full
					adjusted_ratio = (self.empty / capacity) * 100
					level = adjusted_ratio * (1 - (avg_dist / self.empty))
				# If Average Distance is higher than empty distance, report 0 level
				else:
					level = 0

				self.distance_read = int(level)

				# If it took a long time to get sensor data, then the sensor might be having issues
				if (time.time() - start_time) > 0.5:
					self.__start_sensor()  # Attempt re-init of sensor
					event = (
						'Warning: The TOF sensor took longer than normal to get a reading.  Re-initializing the sensor.'
					)
					self.logger.info(event)
				if self.sensor_thread_override:
					self.event.set()
					self.sensor_thread_override = False
				sample_time = time.time()
			time.sleep(1)

	def set_level(self, level=100):
		# Do nothing
		return ()

	def update_distances(self, empty=22, full=4):
		self.empty = empty
		self.full = full

	def get_distances(self):
		levels = {}
		levels['empty'] = self.empty
		levels['full'] = self.full
		return levels

	def get_level(self, override=False):
		"""If override selected, force the sensor thread to update"""
		if override:
			self.sensor_thread_override = True
			self.event.wait(3)  # Wait 3 seconds for sensor to update
			self.event.clear()  # Clear event flag
		return self.distance_read
