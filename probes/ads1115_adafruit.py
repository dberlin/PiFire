#!/usr/bin/env python3

"""
*****************************************
PiFire Probes ADS1115 Adafruit Module
*****************************************

Description:
  This module utilizes the adafruit ADS1115 hardware and returns temperature data.

	Ex Device Definition:

	device = {
			'device' : 'your_device_name',	# Unique name for the device
			'module' : 'ads1115_adafruit',	# Must be populated for this module to load properly
			'ports' : ['ADC0', 'ADC1', 'ADC2', 'ADC3'], # This is defined in the module, so this does not need to be defined.
			'config' : {
				'ADC0_rd': '10000',
                'ADC1_rd': '10000',
                'ADC2_rd': '10000',
                'ADC3_rd': '10000',
                'i2c_bus_addr': '0x48',
                'voltage_ref': '3.28'
			}
		}

"""

"""
*****************************************
 Imported Libraries
*****************************************
"""
import logging
import math
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
from probes.base import ProbeInterface
from common.i2c_bus import open_i2c_bus

"""
*****************************************
 Class Definitions 
*****************************************
"""


class ADSDevice:
	"""ADS1115 Device Based on the Adafruit Module"""

	def __init__(self, i2c_bus_addr=0x48, i2c_bus_kind='basic', i2c_bus_num=0):
		self.logger = logging.getLogger('control')
		# Create the I2C bus
		self.i2c = open_i2c_bus(i2c_bus_kind, i2c_bus_num)
		# Create the ADC object using the I2C bus
		self.ads = ADS.ADS1115(self.i2c, address=i2c_bus_addr)
		self.status = {}

	def read_voltage(self, port):
		adc_ports = {'ADC0': ADS.P0, 'ADC1': ADS.P1, 'ADC2': ADS.P2, 'ADC3': ADS.P3}
		try:
			read_data = AnalogIn(self.ads, adc_ports[port])
			voltage = math.floor(read_data.voltage * 1000)
		except:
			self.logger.exception(f'Exception occurred while reading probe port {port}.  Trace dump: ')
			voltage = 0
		return voltage

	def get_status(self):
		return self.status


class ReadProbes(ProbeInterface):
	def __init__(self, probe_info, device_info, units):
		super().__init__(probe_info, device_info, units)

	def _init_device(self):
		self.time_delay = 0.008
		self.device_info['ports'] = ['ADC0', 'ADC1', 'ADC2', 'ADC3']
		i2c_bus_addr = int(self.device_info['config'].get('i2c_bus_addr', '0x48'), 16)
		i2c_bus_kind = self.device_info['config'].get('i2c_bus_kind', 'basic')
		i2c_bus_num = self.device_info['config'].get('i2c_bus_num', 0)
		try:
			self.device = ADSDevice(i2c_bus_addr=i2c_bus_addr, i2c_bus_kind=i2c_bus_kind, i2c_bus_num=i2c_bus_num)
		except Exception:
			self.logger.error(
				'Something went wrong when trying to initialize the ADS1115 device '
				f'(i2c bus kind={i2c_bus_kind!r}, address=0x{i2c_bus_addr:02X}, bus={i2c_bus_num!r}).'
			)
			raise
