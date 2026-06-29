#!/usr/bin/env python3

"""
*****************************************
PiFire Probes MAX31856 Adafruit Module
*****************************************

Description:
  This module utilizes the MAX31856 thermocouple hardware and returns
  temperature data. Depends on: pip3 install adafruit-circuitpython-max31856

	Ex Device Definition:

	device = {
			'device' : 'your_device_name',	# Unique name for the device
			'module' : 'max31856_adafruit',	# Must be populated for this module to load properly
			'ports' : ['TC0'],				# Defined in the module
			'config' : {
				'cs' : 'D6',				# SPI Chip Select (board pin or MCP2210 GP index)
				'spi_bus_kind' : 'basic',	# 'basic' (native SPI) or 'mcp2210'
				'mcp2210_serial' : '',		# Optional MCP2210 USB serial
				'tc_type' : 'K',			# Thermocouple type B/E/J/K/N/R/S/T (default K)
				'averaging' : 1,			# Averaging samples 1/2/4/8/16 (default 1)
				'noise_rejection' : 60		# Mains noise rejection 50/60 Hz (default 60)
			}
		}

"""

"""
*****************************************
 Imported Libraries
*****************************************
"""
import logging
import adafruit_max31856
from probes.base import ProbeInterface, resolve_spi_bus

# Config string -> adafruit_max31856.ThermocoupleType.* enum value
_TC_TYPES = {
	'B': adafruit_max31856.ThermocoupleType.B,
	'E': adafruit_max31856.ThermocoupleType.E,
	'J': adafruit_max31856.ThermocoupleType.J,
	'K': adafruit_max31856.ThermocoupleType.K,
	'N': adafruit_max31856.ThermocoupleType.N,
	'R': adafruit_max31856.ThermocoupleType.R,
	'S': adafruit_max31856.ThermocoupleType.S,
	'T': adafruit_max31856.ThermocoupleType.T,
}

"""
*****************************************
 Class Definitions
*****************************************
"""


class TCDevice:
	"""MAX31856 Thermocouple Device Based on the Adafruit Module"""

	def __init__(self, spi, cs, tc_type='K', averaging=1, noise_rejection=60):
		self.status = {}
		self.sensor = adafruit_max31856.MAX31856(spi, cs, thermocouple_type=_TC_TYPES[tc_type])
		self.sensor.averaging = averaging
		self.sensor.noise_rejection = noise_rejection

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
		self.device_info['ports'] = ['TC0']
		config = self.device_info['config']
		spi, cs = resolve_spi_bus(config, default_cs='D6')
		tc_type = config.get('tc_type', 'K')
		averaging = int(config.get('averaging', 1))
		noise_rejection = int(config.get('noise_rejection', 60))
		self.device = TCDevice(spi, cs, tc_type, averaging, noise_rejection)

	def read_all_ports(self, output_data):
		"""Read temperature from device"""
		tempC = round(self.device.temperature, 1)
		tempF = int(tempC * (9 / 5) + 32)  # Celsius to Fahrenheit
		port = self.device_info['ports'][0]

		""" Thermocouples have no resistance reading """
		self.output_data['tr'][self.port_map[port]] = 0

		""" Store the temperature in the output data structure """
		if port == self.primary_port:
			self.output_data['primary'][self.port_map[port]] = tempF if self.units == 'F' else tempC
		elif port in self.food_ports:
			self.output_data['food'][self.port_map[port]] = tempF if self.units == 'F' else tempC
		elif port in self.aux_ports:
			self.output_data['aux'][self.port_map[port]] = tempF if self.units == 'F' else tempC

		return self.output_data
