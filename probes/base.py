#!/usr/bin/env python3

'''
*****************************************
PiFire Probes Base Module 
*****************************************

Description: 
  This module serves as a base module for the probe devices.

'''

'''
*****************************************
 Imported Libraries
*****************************************
'''

import math
import time
import logging
import os
import glob
from probes.temp_queue import TempQueue

'''
*****************************************
 I2C Bus Helpers
*****************************************
'''

def find_i2c_bus(match, devices_path='/sys/bus/i2c/devices'):
	'''
	Return the integer i2c bus number whose adapter name contains `match`
	(case-insensitive), e.g. 'CP2112' for a USB-to-I2C bridge. Scans
	`<devices_path>/i2c-*/name`. Raises RuntimeError if zero or more than one
	adapter matches, so the caller fails clearly rather than guessing.
	'''
	match_lower = str(match).lower()
	found = []
	for bus_dir in glob.glob(os.path.join(devices_path, 'i2c-*')):
		try:
			with open(os.path.join(bus_dir, 'name')) as handle:
				name = handle.read().strip()
		except OSError:
			continue
		if match_lower in name.lower():
			try:
				found.append(int(os.path.basename(bus_dir).split('-')[-1]))
			except ValueError:
				continue
	if len(found) == 1:
		return found[0]
	if not found:
		raise RuntimeError(f'No i2c adapter found matching {match!r} under {devices_path}')
	raise RuntimeError(f'Multiple i2c adapters match {match!r}: {sorted(found)}')


def resolve_i2c_bus(bus):
	'''
	Resolve an extended-i2c-bus spec to a bus number. Accepts an int or numeric
	string (e.g. 3 / '3' -> /dev/i2c-3, used directly) or an adapter-name match
	string (e.g. 'CP2112' -> discovered via find_i2c_bus, robust against the
	dynamic bus numbers USB-to-I2C bridges get).
	'''
	try:
		return int(str(bus).strip())
	except (ValueError, TypeError):
		return find_i2c_bus(bus)


'''
*****************************************
 Class Definitions
*****************************************
'''

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
		self.logger = logging.getLogger("control")

	def _init_device(self):
		self.time_delay = 0
		self.device = FakeDevice(self.port_map, self.primary_port, self.units)

	def _discover_port_types(self, probe_info):
		''' Find attached ports and identify their types '''
		for probe in probe_info:
			if probe['device'] == self.device_info['device']:
				if probe['type'] == 'Primary':
					self.primary_port = probe['port']
				if probe['type'] == 'Food':
					self.food_ports.append(probe['port'])
				if probe['type'] == 'Aux':
					self.aux_ports.append(probe['port'])

	def _build_port_map(self, probe_info):
		''' Build port mapping '''
		self.port_map = {}
		for port in self.device_info['ports']:
			for probe in probe_info:
				if (probe['device'] == self.device_info['device']) and (probe['port'] == port):
					self.port_map[port] = probe['label']

	def _build_output_data(self, probe_info):
		''' Build output data structure for probes '''
		self.output_data = {
			'primary' : {},
			'food' : {},
			'aux' : {}, 
			'tr' : {}
		}
		for probe in probe_info:
			if probe['device'] == self.device_info['device']:
				if probe['type'] == 'Primary':
					self.output_data['primary'][probe['label']] = 0
				elif probe['type'] == 'Food':
					self.output_data['food'][probe['label']] = 0
				elif probe['type'] == 'Aux':
					self.output_data['aux'][probe['label']] = 0
		''' Build output data structure for Tr tuning data '''
		for port in self.port_map:
			self.output_data['tr'][self.port_map[port]] = 0

	def _build_ports(self):
		''' Build ports objects. '''
		self.port_queues = {}
		for port in self.port_map:
			self.port_queues[port] = TempQueue(qlength=10, units=self.units)

	def _temp_to_resistance(self, temp, probe_profile):
		'''
		  Determine the resistance value Tr for the port.  
		  Prototype uses the temperature and probe profile to determine the Tr value. 
		'''
		A = probe_profile['A']
		B = probe_profile['B']
		C = probe_profile['C']

		try: 
			if self.units == 'F':
				tempK = ((temp - 32) * (5/9)) + 273.15
			else: 
				tempK = temp + 273.15

			'''
			 https://en.wikipedia.org/wiki/Steinhart%E2%80%93Hart_equation
			 Inverse of the equation, to determine Tr = Resistance Value of the thermistor
			'''

			x = (1/(2*C))*(A-(1/tempK))

			y = math.sqrt(math.pow((B/(3*C)),3)+math.pow(x,2))

			Tr = math.exp(((y-x)**(1/3)) - ((y+x)**(1/3)))
		except: 
			Tr = 0

		return Tr 

	def _voltage_to_temp(self, voltage, probe_profile, port=None):
		if voltage == None:
			''' Transient probe detected. '''
			return None, 0

		''' Check to make sure voltage is between 0V and Vs defined in profile, plus some guard band '''
		if(voltage > 0) and (voltage <= ((probe_profile['Vs'] * 1000) * 1.01)):
			'''
				Voltage at the divider (i.e. input to the ADC)
			'''
			Vo = (voltage / 1000) # mV to V of ADC (at the divider)
			
			'''
			Thermistor Resistor Value Ohms (R1)
			 R1 = ( (Vin * R2) - (Vout * R2) ) / Vout
			 Tr = ((probe_profile['Vs'] * probe_profile['Rd']) - (Vo * probe_profile['Rd'])) / Vo
			 R2 = ( Vout * R1 ) / ( Vin - Vout )
			'''
			
			if Vo < probe_profile['Vs']:
				Tr = ( Vo * probe_profile['Rd']) / ( probe_profile['Vs'] - Vo )
			else:
				Tr = ( Vo * probe_profile['Rd']) / ( 0.001 )

			''' Coefficient a, b, & c values '''
			a = probe_profile['A']
			b = probe_profile['B']
			c = probe_profile['C']

			'''
		    Steinhart Hart Equation
			 1/T = A + B(ln(R)) + C(ln(R))^3
			 T = 1/(a + b[ln(ohm)] + c[ln(ohm)]^3)
			'''
			lnohm = math.log(Tr) # ln(ohms)

			t1 = (b*lnohm) # b[ln(ohm)]

			t2 = c * math.pow(lnohm,3) # c[ln(ohm)]^3

			tempK = 1/(a + t1 + t2) # calculate temperature in Kelvin

			tempC = tempK - 273.15 # Kelvin to Celsius

			tempF = tempC * (9/5) + 32 # Celsius to Farenheit
			
			''' Check bounds for realistic temperature values (ex. 0-600F, 0-314C), else report 0 '''
			if (tempF < 0) or (tempF > 600):
				tempF = 0
			if (tempC < 0) or (tempC > 314):
				tempC = 0

		else:
			tempF = 0.0
			tempC = 0.0
			Tr = 0
			error_event = f'An error occurred reading the voltage from device: {self.device_info["device"]}, ' \
				f'port: {port}. The voltage read {(voltage / 1000):,.2f}V ({voltage}mV) ' \
				f'was outside the expected range of 0mV to {probe_profile["Vs"]}V.  This usually means that ' \
				f'the voltage reference is set too low in the probe device configuration.  To fix this issue, ' \
				f'please set the voltage reference to a value greater than {(voltage / 1000):,.2f}V in the configuration wizard.'	
			self.logger.debug(error_event)

		if self.units == 'F':
			return tempF, round(Tr)  # Return Calculated Temperature and Thermistor Value in Ohms
		else: 
			return tempC, round(Tr)  # Return Calculated Temperature and Thermistor Value in Ohms

	def read_all_ports(self, output_data):
		port_values = {}

		for port in self.port_map:
			''' Read Ports from Device '''
			port_values[port] = self.device.read_voltage(port)

			''' Convert Voltage to Temperature and Tr '''
			port_values[port], self.output_data['tr'][self.port_map[port]] = self._voltage_to_temp(port_values[port], self.probe_profiles[port], port=port)

			''' Enqueue the Temperature Readings to Port Queues '''
			if port_values[port] == None:
				''' If the read value is None, pass that to the output instead of adding to the queue '''
				output_value = None
			else:
				self.port_queues[port].enqueue(port_values[port])
				output_value = self.port_queues[port].average() 

			''' Get average temperature from the queue and store it in the output data structure'''
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
		''' Set the probe profile for each of the probes. '''
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
