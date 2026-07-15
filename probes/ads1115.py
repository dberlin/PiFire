#!/usr/bin/env python3

"""
*****************************************
PiFire Probes ADS1115 Module
*****************************************

Description:
  This module utilizes the ADS1115 hardware and returns temperature data.

        Ex Device Definition:

        device = {
                        'device' : 'your_device_name',	# Unique name for the device
                        'module' : 'ads1115',  			# Must be populated for this module to load properly
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
import ADS1115
from probes.base import ProbeInterface, resolve_i2c_bus

"""
*****************************************
 Class Definitions 
*****************************************
"""


class ADSDevice:
    """ADS1115 Device Based on the ADS1115 Python Module"""

    def __init__(self, i2c_bus_addr=0x48, i2c_bus_kind="basic", i2c_bus_num=0):
        self.logger = logging.getLogger("control")
        self.ads = ADS1115.ADS1115(address=i2c_bus_addr)
        if i2c_bus_kind == "extended":
            # The ADS1115 library hardcodes smbus2.SMBus(1); repoint it at the
            # extended bus -- a /dev/i2c-N number or an adapter-name match (e.g.
            # 'CP2112') resolved against the available i2c adapters.
            import smbus2

            self.ads.i2c = smbus2.SMBus(resolve_i2c_bus(i2c_bus_num))
        self.status = {}

    def read_voltage(self, port):
        adc_ports = {"ADC0": 0, "ADC1": 1, "ADC2": 2, "ADC3": 3}
        try:
            voltage = self.ads.readADCSingleEnded(adc_ports[port])
        except:
            self.logger.exception(f"Exception occurred while reading probe port {port}.  Trace dump: ")
            voltage = 0
        return voltage

    def get_status(self):
        return self.status


class ReadProbes(ProbeInterface):
    def __init__(self, probe_info, device_info, units):
        super().__init__(probe_info, device_info, units)

    def _init_device(self):
        self.time_delay = 0.008
        self.device_info["ports"] = ["ADC0", "ADC1", "ADC2", "ADC3"]
        i2c_bus_addr = int(self.device_info["config"].get("i2c_bus_addr", "0x48"), 16)
        i2c_bus_kind = self.device_info["config"].get("i2c_bus_kind", "basic")
        i2c_bus_num = self.device_info["config"].get("i2c_bus_num", 0)
        try:
            self.device = ADSDevice(i2c_bus_addr=i2c_bus_addr, i2c_bus_kind=i2c_bus_kind, i2c_bus_num=i2c_bus_num)
        except Exception:
            self.logger.error(
                "Something went wrong when trying to initialize the ADS1115 device "
                f"(i2c bus kind={i2c_bus_kind!r}, address=0x{i2c_bus_addr:02X}, bus={i2c_bus_num!r})."
            )
            raise
