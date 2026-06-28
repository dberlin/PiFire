#!/usr/bin/env python3

'''
*****************************************
PiFire Probes MAX31865 Adafruit Module 
*****************************************

Description: 
  This module utilizes the MAX31865 hardware and returns temperature data.
	Depends on: pip3 install adafruit-circuitpython-max31865 

	Ex Device Definition: 
	
	device = {
			'device' : 'your_device_name',	# Unique name for the device
			'module' : 'max31865_adafruit',  		# Must be populated for this module to load properly
			'ports' : ['RTD0'],    			# This is defined in the module, so this does not need to be defined.
			'config' : {
				'cs' : 'D6', 			    # SPI Chip Select GPIO (defaults to D6)
				'rtd_nominal' : 1000, 		# RTD Nominal (Defaults to 1000)
				'ref_resistor' : 4300, 		# Reference Resistor (Defaults to 4300)
				'wires' : 2					# Number of RTD Probe Wires (Defaults to 2)
			} 
		}

'''

'''
*****************************************
 Imported Libraries
*****************************************
'''
import logging
import adafruit_max31865
from probes.base import ProbeInterface, resolve_spi_bus

'''
*****************************************
 Class Definitions
*****************************************
'''

class RTDDevice():
	''' MAX31865 Device Based on the Adafruit Module '''
	def __init__(self, spi, cs, rtd_nominal=1000, ref_resistor=4300, wires=2):
		self.wires = wires
		self.rtd_nominal = rtd_nominal
		self.ref_resistor = ref_resistor
		self.status = {}
		self.sensor = adafruit_max31865.MAX31865(
			spi, cs, rtd_nominal=self.rtd_nominal,
			ref_resistor=self.ref_resistor, wires=self.wires)

	@property
	def temperature(self):
		return self.sensor.temperature

	@property
	def resistance(self):
		return self.sensor.resistance

	def get_status(self):
		return self.status

class ReadProbes(ProbeInterface):

	def __init__(self, probe_info, device_info, units):
		super().__init__(probe_info, device_info, units)

	def _init_device(self):
		self.time_delay = 0
		self.device_info['ports'] = ['RTD0']
		config = self.device_info['config']
		spi, cs = resolve_spi_bus(config, default_cs='D6')
		rtd_nominal = int(config.get('rtd_nominal', 1000))
		ref_resistor = int(config.get('ref_resistor', 4300))
		wires = int(config.get('wires', 2))
		self.device = RTDDevice(spi, cs, rtd_nominal, ref_resistor, wires)

	def read_all_ports(self, output_data):
		''' Read temperature from device '''
		tempC = round(self.device.temperature, 1)
		tempF = int(tempC * (9/5) + 32) # Celsius to Fahrenheit
		port = self.device_info['ports'][0]

		''' Read resistance from device '''
		self.output_data['tr'][self.port_map[port]] = self.device.resistance

		''' Get average temperature from the queue and store it in the output data structure'''
		if port == self.primary_port:
			self.output_data['primary'][self.port_map[port]] = tempF if self.units == 'F' else tempC
		elif port in self.food_ports:
			self.output_data['food'][self.port_map[port]] = tempF if self.units == 'F' else tempC
		elif port in self.aux_ports:
			self.output_data['aux'][self.port_map[port]] = tempF if self.units == 'F' else tempC
		
		return self.output_data