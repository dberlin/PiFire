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

import ADS1115

from common.i2c_bus_config import BasicBus, KernelBus, parse_i2c_bus
from probes.base import ProbeInterface

"""
*****************************************
 Class Definitions 
*****************************************
"""


class ADSDevice:
    """ADS1115 Device Based on the ADS1115 Python Module"""

    def __init__(self, i2c_bus_addr=0x48, bus=None):
        self.logger = logging.getLogger("control")
        self.ads = ADS1115.ADS1115(address=i2c_bus_addr)
        bus = bus or BasicBus()
        # Only set for a kernel bus, where WE opened the handle; on the basic
        # bus the ADS1115 library owns its own SMBus(1) and closing it is not
        # ours to do.
        self.smbus = None
        if isinstance(bus, KernelBus):
            # The ADS1115 library hardcodes smbus2.SMBus(1); repoint it at the
            # resolved /dev/i2c-N bus.
            import smbus2

            self.smbus = smbus2.SMBus(bus.resolve_bus_num())
            self.ads.i2c = self.smbus
        self.status = {}

    def close(self):
        """Close the extended-bus smbus2 handle this device opened, if any.
        Idempotent: the handle is dropped after closing."""
        if self.smbus is not None:
            self.smbus.close()
            self.smbus = None

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
        bus = parse_i2c_bus(self.device_info["config"].get("i2c_bus") or {"kind": "basic"})
        try:
            self.device = ADSDevice(i2c_bus_addr=i2c_bus_addr, bus=bus)
        except Exception:
            self.logger.error(
                "Something went wrong when trying to initialize the ADS1115 device "
                f"(i2c bus {bus.describe()}, address=0x{i2c_bus_addr:02X})."
            )
            raise

    def close(self):
        """Release the extended-bus i2c handle (see ADSDevice.close). The
        Adafruit ADS modules deliberately have no close(): their bus comes from
        the process-wide open_i2c_bus() cache and is shared with every other
        device on the same physical bus."""
        self.device.close()
