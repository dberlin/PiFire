#!/usr/bin/env python3

"""
*****************************************
PiFire Probes MCP9600 Adafruit Module
*****************************************

Description:
  This module utilizes the MCP9600 hardware and returns temperature data.
	Depends on: pip3 install adafruit-circuitpython-mcp9600

	Note: Still experimental.  Requires a slower i2c clock speed.
	  This may cause issues with other i2c device performance.
	  Edit /boot/config.txt to add:
	  'dtparam=i2c_arm_baudrate=10000'

	Ex Device Definition:

	device = {
			'device' : 'your_device_name',	# Unique name for the device
			'module' : 'mcp9600_adafruit',  # Must be populated for this module to load properly
			'ports' : ['KTT0'],    			# This is defined in the module, so this does not need to be defined.
			'config' : {
				'i2c_bus_addr' : '0x67',	# I2C Bus Address
				'tc_type' : 'K'				# Thermocouple type K/J/T/N/S/E/B/R (default K)
			}
		}

"""

"""
*****************************************
 Imported Libraries
*****************************************
"""
import logging
from adafruit_mcp9600 import MCP9600
from probes.base import ProbeInterface
from common.i2c_bus import open_i2c_bus


"""
*****************************************
 Class Definitions 
*****************************************
"""


class KTTDevice:
	"""MCP9600 Device Based on the Adafruit Module"""

	def __init__(self, i2c_bus_addr=0x67, i2c_bus_kind='basic', i2c_bus_num=0, tc_type='K'):
		self.logger = logging.getLogger('control')
		self.status = {}

		self.i2c = open_i2c_bus(i2c_bus_kind, i2c_bus_num)

		self.sensor = MCP9600(self.i2c, address=i2c_bus_addr, tctype=tc_type)

	@property
	def temperature(self):
		return self.sensor.temperature

	def get_status(self):
		return self.status


class ReadProbes(ProbeInterface):
	def __init__(self, probe_info, device_info, units):
		super().__init__(probe_info, device_info, units)

	def _init_device(self):
		self.time_delay = 0
		self.device_info['ports'] = ['KTT0']
		i2c_bus_addr = int(self.device_info['config'].get('i2c_bus_addr', '0x67'), 16)
		i2c_bus_kind = self.device_info['config'].get('i2c_bus_kind', 'basic')
		i2c_bus_num = self.device_info['config'].get('i2c_bus_num', 0)
		tc_type = self.device_info['config'].get('tc_type', 'K')
		try:
			self.device = KTTDevice(
				i2c_bus_addr=i2c_bus_addr, i2c_bus_kind=i2c_bus_kind, i2c_bus_num=i2c_bus_num, tc_type=tc_type
			)
		except Exception:
			self.logger.error(
				'Something went wrong when trying to initialize the MCP9600 device '
				f'(i2c bus kind={i2c_bus_kind!r}, address=0x{i2c_bus_addr:02X}, bus={i2c_bus_num!r}).'
			)
			raise

	def read_all_ports(self, output_data):
		"""Read temperature from device"""
		tempC = round(self.device.temperature, 1)
		tempF = round(tempC * (9 / 5) + 32, 1)  # Celsius to Fahrenheit
		port = self.device_info['ports'][0]

		""" Read resistance from device """
		self.output_data['tr'][self.port_map[port]] = 0  # resistance NA

		""" Store the raw temperature; Kalman filtering is applied centrally in ProbesMain.read_probes() via apply_filters() """
		if port == self.primary_port:
			self.output_data['primary'][self.port_map[port]] = tempF if self.units == 'F' else tempC
		elif port in self.food_ports:
			self.output_data['food'][self.port_map[port]] = tempF if self.units == 'F' else tempC
		elif port in self.aux_ports:
			self.output_data['aux'][self.port_map[port]] = tempF if self.units == 'F' else tempC

		return self.output_data
