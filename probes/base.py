#!/usr/bin/env python3

"""
*****************************************
PiFire Probes Base Module
*****************************************

Description:
  This module serves as a base module for the probe devices.

"""

"""
*****************************************
 Imported Libraries
*****************************************
"""

import math
import time
import logging
import os
import glob
from probes.kalman import TempKalman

"""
*****************************************
 I2C Bus Helpers
*****************************************
"""


def find_i2c_bus(match, devices_path='/sys/bus/i2c/devices'):
	"""
	Return the integer i2c bus number whose adapter name contains `match`
	(case-insensitive), e.g. 'CP2112' for a USB-to-I2C bridge. Scans
	`<devices_path>/i2c-*/name`. Raises RuntimeError if zero or more than one
	adapter matches, so the caller fails clearly rather than guessing.
	"""
	match_lower = str(match).lower()
	adapters = []  # (bus_num, name) for every i2c adapter present
	for bus_dir in glob.glob(os.path.join(devices_path, 'i2c-*')):
		try:
			with open(os.path.join(bus_dir, 'name')) as handle:
				name = handle.read().strip()
		except OSError:
			continue
		try:
			bus_num = int(os.path.basename(bus_dir).split('-')[-1])
		except ValueError:
			continue
		adapters.append((bus_num, name))

	found = [num for num, name in adapters if match_lower in name.lower()]
	if len(found) == 1:
		return found[0]
	# Include what IS present so a misconfigured match string is easy to fix.
	available = ', '.join(f'i2c-{n} ({name!r})' for n, name in sorted(adapters)) or '(none)'
	if not found:
		raise RuntimeError(
			f'No i2c adapter found matching {match!r} under {devices_path}. Available adapters: {available}'
		)
	raise RuntimeError(f'Multiple i2c adapters match {match!r}: {sorted(found)}. Available adapters: {available}')


def resolve_i2c_bus(bus):
	"""
	Resolve an extended-i2c-bus spec to a bus number. Accepts an int or numeric
	string (e.g. 3 / '3' -> /dev/i2c-3, used directly) or an adapter-name match
	string (e.g. 'CP2112' -> discovered via find_i2c_bus, robust against the
	dynamic bus numbers USB-to-I2C bridges get).
	"""
	spec = str(bus).strip()
	# A plain number is a /dev/i2c-N bus index; anything else is an adapter-name
	# match. Check explicitly (rather than try/int/except) so a name like 'CP2112'
	# does not raise a ValueError -- only find_i2c_bus's clear "not found" error
	# can surface.
	if spec.isdigit():
		return int(spec)
	return find_i2c_bus(spec)


"""
*****************************************
 SPI Bus Helpers
*****************************************
"""

# Stored chip-select value -> board pin attribute name. The wizard stores the
# `list_values` entry, which for this field is the BCM name 'GPIOn'; the 'Dn'
# Adafruit name is accepted too so a legacy stored value or an in-code default
# still resolves. 'GPIO6' and 'D6' are the same physical pin (board.D6).
_SPI_CS_BOARD_PINS = {}
for _spi_cs_n in (2, 3, 4, 5, 6, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27):
	_SPI_CS_BOARD_PINS[f'GPIO{_spi_cs_n}'] = f'D{_spi_cs_n}'
	_SPI_CS_BOARD_PINS[f'D{_spi_cs_n}'] = f'D{_spi_cs_n}'
del _spi_cs_n

# Cache of opened MCP2210 bridges, keyed by serial. A USB-HID handle can be
# opened only once, so every probe on the same bridge must share one instance.
_MCP2210_CACHE = {}


def resolve_mcp2210(serial=None):
	"""
	Open (and cache) a single MCP2210 USB-to-SPI bridge per serial and return
	the shared instance. The MCP2210 HID handle can be opened only once, so
	probes sharing a bridge must share one instance; the cache guarantees that.
	serial=None or '' selects the first MCP2210 by VID/PID (0x04D8/0x00DE) and is
	cached under one canonical key.
	"""
	key = serial or ''  # None and '' both mean "the first/only bridge"
	if key not in _MCP2210_CACHE:
		from mcp2210 import MCP2210

		_MCP2210_CACHE[key] = MCP2210(serial=serial or None)
	return _MCP2210_CACHE[key]


def _gp_index(cs):
	"""
	Parse an MCP2210 GPIO chip-select spec to an int 0-8. Accepts 0-8, 'GP3', or
	'GPIO3'. Raises ValueError for anything else, so a misconfigured CS fails
	clearly rather than driving the wrong pin.
	"""
	text = str(cs).strip().upper()
	if text.startswith('GPIO'):
		text = text[4:]
	elif text.startswith('GP'):
		text = text[2:]
	if not text.isdigit():
		raise ValueError(f'Invalid MCP2210 chip-select {cs!r}; expected GP0-GP8')
	index = int(text)
	if not 0 <= index <= 8:
		raise ValueError(f'MCP2210 chip-select out of range: {cs!r} (GP0-GP8)')
	return index


def resolve_spi_bus(config, default_cs):
	"""
	Build the (spi, chip_select) pair for an SPI probe from its config dict.
	  spi_bus_kind 'basic'   -> board.SPI() + digitalio.DigitalInOut(board pin)
	  spi_bus_kind 'mcp2210' -> shared MCP2210.spi + mcp.digital_inout(GP index)
	Reads standardized keys: spi_bus_kind (default 'basic'), cs (default
	`default_cs`), mcp2210_serial (default ''). Returns objects ready for an
	adafruit_bus_device / SPIDevice-based sensor constructor. Raises ValueError
	on an unknown spi_bus_kind or an unknown board chip-select. board/digitalio
	are imported lazily so this module imports without Blinka present.
	"""
	kind = config.get('spi_bus_kind', 'basic')
	cs = config.get('cs', default_cs)
	if kind == 'mcp2210':
		mcp = resolve_mcp2210(config.get('mcp2210_serial') or None)
		return mcp.spi, mcp.digital_inout(_gp_index(cs))
	if kind == 'basic':
		import board
		import digitalio

		try:
			pin_attr = _SPI_CS_BOARD_PINS[cs]
		except KeyError:
			raise ValueError(f'Unknown SPI chip-select {cs!r} for native board.SPI()')
		return board.SPI(), digitalio.DigitalInOut(getattr(board, pin_attr))
	raise ValueError(f'Unknown spi_bus_kind {kind!r}; expected "basic" or "mcp2210"')


"""
*****************************************
 Class Definitions
*****************************************
"""


class ProbeInterface:
	def __init__(self, probe_info, device_info, units):
		self.units = units
		self.device_info = device_info
		if self.device_info['config'].get('transient', 'False') == 'True':
			self.transient = True
		else:
			self.transient = False
		self.set_profiles(probe_info)
		self._build_port_map(probe_info)
		self._build_output_data(probe_info)
		self._build_ports()
		self.primary_port = None
		self.food_ports = []
		self.aux_ports = []
		self._discover_port_types(probe_info)
		self._init_device()
		self.logger = logging.getLogger('control')

	def _init_device(self):
		self.time_delay = 0
		self.device = FakeDevice(self.port_map, self.primary_port, self.units)

	def _discover_port_types(self, probe_info):
		"""Find attached ports and identify their types"""
		for probe in probe_info:
			if probe['device'] == self.device_info['device']:
				if probe['type'] == 'Primary':
					self.primary_port = probe['port']
				if probe['type'] == 'Food':
					self.food_ports.append(probe['port'])
				if probe['type'] == 'Aux':
					self.aux_ports.append(probe['port'])

	def _build_port_map(self, probe_info):
		"""Build port mapping"""
		self.port_map = {}
		for port in self.device_info['ports']:
			for probe in probe_info:
				if (probe['device'] == self.device_info['device']) and (probe['port'] == port):
					self.port_map[port] = probe['label']

	def _build_output_data(self, probe_info):
		"""Build output data structure for probes"""
		self.output_data = {'primary': {}, 'food': {}, 'aux': {}, 'tr': {}}
		for probe in probe_info:
			if probe['device'] == self.device_info['device']:
				if probe['type'] == 'Primary':
					self.output_data['primary'][probe['label']] = 0
				elif probe['type'] == 'Food':
					self.output_data['food'][probe['label']] = 0
				elif probe['type'] == 'Aux':
					self.output_data['aux'][probe['label']] = 0
		""" Build output data structure for Tr tuning data """
		for port in self.port_map:
			self.output_data['tr'][self.port_map[port]] = 0

	def _build_ports(self):
		"""Build ports objects."""
		self.port_filters = {}
		for port in self.port_map:
			self.port_filters[port] = TempKalman(units=self.units)

	def _temp_to_resistance(self, temp, probe_profile):
		"""
		Determine the resistance value Tr for the port.
		Prototype uses the temperature and probe profile to determine the Tr value.
		"""
		A = probe_profile['A']
		B = probe_profile['B']
		C = probe_profile['C']

		try:
			if self.units == 'F':
				tempK = ((temp - 32) * (5 / 9)) + 273.15
			else:
				tempK = temp + 273.15

			"""
			 https://en.wikipedia.org/wiki/Steinhart%E2%80%93Hart_equation
			 Inverse of the equation, to determine Tr = Resistance Value of the thermistor
			"""

			x = (1 / (2 * C)) * (A - (1 / tempK))

			y = math.sqrt(math.pow((B / (3 * C)), 3) + math.pow(x, 2))

			Tr = math.exp(((y - x) ** (1 / 3)) - ((y + x) ** (1 / 3)))
		except:
			Tr = 0

		return Tr

	def _voltage_to_temp(self, voltage, probe_profile, port=None):
		if voltage == None:
			""" Transient probe detected. """
			return None, 0

		""" Check to make sure voltage is between 0V and Vs defined in profile, plus some guard band """
		if (voltage > 0) and (voltage <= ((probe_profile['Vs'] * 1000) * 1.01)):
			"""
				Voltage at the divider (i.e. input to the ADC)
			"""
			Vo = voltage / 1000  # mV to V of ADC (at the divider)

			"""
			Thermistor Resistor Value Ohms (R1)
			 R1 = ( (Vin * R2) - (Vout * R2) ) / Vout
			 Tr = ((probe_profile['Vs'] * probe_profile['Rd']) - (Vo * probe_profile['Rd'])) / Vo
			 R2 = ( Vout * R1 ) / ( Vin - Vout )
			"""

			if Vo < probe_profile['Vs']:
				Tr = (Vo * probe_profile['Rd']) / (probe_profile['Vs'] - Vo)
			else:
				Tr = (Vo * probe_profile['Rd']) / (0.001)

			""" Coefficient a, b, & c values """
			a = probe_profile['A']
			b = probe_profile['B']
			c = probe_profile['C']

			"""
		    Steinhart Hart Equation
			 1/T = A + B(ln(R)) + C(ln(R))^3
			 T = 1/(a + b[ln(ohm)] + c[ln(ohm)]^3)
			"""
			lnohm = math.log(Tr)  # ln(ohms)

			t1 = b * lnohm  # b[ln(ohm)]

			t2 = c * math.pow(lnohm, 3)  # c[ln(ohm)]^3

			tempK = 1 / (a + t1 + t2)  # calculate temperature in Kelvin

			tempC = tempK - 273.15  # Kelvin to Celsius

			tempF = tempC * (9 / 5) + 32  # Celsius to Farenheit

			""" Check bounds for realistic temperature values (ex. 0-600F, 0-314C), else report 0 """
			if (tempF < 0) or (tempF > 600):
				tempF = 0
			if (tempC < 0) or (tempC > 314):
				tempC = 0

		else:
			tempF = 0.0
			tempC = 0.0
			Tr = 0
			error_event = (
				f'An error occurred reading the voltage from device: {self.device_info["device"]}, '
				f'port: {port}. The voltage read {(voltage / 1000):,.2f}V ({voltage}mV) '
				f'was outside the expected range of 0mV to {probe_profile["Vs"]}V.  This usually means that '
				f'the voltage reference is set too low in the probe device configuration.  To fix this issue, '
				f'please set the voltage reference to a value greater than {(voltage / 1000):,.2f}V in the configuration wizard.'
			)
			self.logger.debug(error_event)

		if self.units == 'F':
			return tempF, round(Tr)  # Return Calculated Temperature and Thermistor Value in Ohms
		else:
			return tempC, round(Tr)  # Return Calculated Temperature and Thermistor Value in Ohms

	def read_all_ports(self, output_data):
		port_values = {}

		for port in self.port_map:
			""" Read Ports from Device """
			port_values[port] = self.device.read_voltage(port)

			""" Convert Voltage to Temperature and Tr """
			port_values[port], self.output_data['tr'][self.port_map[port]] = self._voltage_to_temp(
				port_values[port], self.probe_profiles[port], port=port
			)

			""" Filter the Temperature Reading (Kalman); None passes through """
			kalman = self.port_filters[port]
			output_value = kalman.update(port_values[port])

			""" Debug: raw probe reading vs. filtered output and Kalman state """
			if self.logger.isEnabledFor(logging.DEBUG):
				self.logger.debug(
					f'Kalman[{self.port_map[port]}] raw={port_values[port]} output={output_value} '
					f'est={round(kalman.x, 2) if kalman.x is not None else None} '
					f'rate={round(kalman.v, 3)}/s gated={kalman.gated} none_streak={kalman.none_streak}'
				)

			""" Get average temperature from the queue and store it in the output data structure"""
			if port == self.primary_port:
				self.output_data['primary'][self.port_map[port]] = output_value
			elif port in self.food_ports:
				self.output_data['food'][self.port_map[port]] = output_value
			elif port in self.aux_ports:
				self.output_data['aux'][self.port_map[port]] = output_value

			if self.time_delay:
				time.sleep(self.time_delay)  # Time delay, if needed for single-shot mode on some ADC's

		return self.output_data

	def update_units(self, units):
		self.units = 'C' if units == 'C' else 'F'
		self._init_device()

	def set_profiles(self, probe_info):
		"""Set the probe profile for each of the probes."""
		self.probe_profiles = {}
		for port in self.device_info['ports']:
			for probe in probe_info:
				if probe['device'] == self.device_info['device'] and probe['port'] == port:
					self.probe_profiles[port] = probe['profile']
					self.probe_profiles[port]['Rd'] = int(self.device_info['config'].get(port + '_rd', 10000))
					self.probe_profiles[port]['Vs'] = float(self.device_info['config'].get('voltage_ref', 3.28))

	def get_port_map(self):
		return self.port_map

	def get_device_info(self):
		self.device_info['status'] = self.device.get_status()
		return self.device_info

	def _to_celsius(self, fahrenheit):
		if fahrenheit is not None:
			return (fahrenheit - 32) * 5 / 9
		else:
			return None

	def _to_fahrenheit(self, celsius):
		if celsius is not None:
			return int(celsius * 9 / 5 + 32)
		else:
			return None


class FakeDevice:
	def __init__(self, port_map, primary_port, units):
		pass

	def read_voltage(self, port):
		pass
