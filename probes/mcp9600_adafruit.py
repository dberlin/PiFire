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
from probes._mcp960x_adafruit import MCP960xDevice, MCP960xProbe

"""
*****************************************
 Class Definitions 
*****************************************
"""


class KTTDevice(MCP960xDevice):
    """MCP9600 device based on the Adafruit module."""


class ReadProbes(MCP960xProbe):
    device_class = KTTDevice
    default_i2c_address = 0x67
