"""Shared implementation for the stable Adafruit ADS1x15 probe plugins."""

import logging
import math
import sys
from collections.abc import Callable, Mapping
from typing import ClassVar

from common.i2c_bus_config import BasicBus, parse_i2c_bus


class AdafruitADSDevice:
    """Common device behavior selected by a public ADS chip adapter."""

    CHIP_FACTORY: ClassVar[Callable[..., object]]
    CHANNELS: ClassVar[Mapping[str, object]]

    def __init__(self, i2c_bus_addr=0x48, bus=None):
        self.logger = logging.getLogger("control")
        public_module = sys.modules[type(self).__module__]
        self.i2c = public_module.open_i2c_bus(bus or BasicBus())
        self.ads = self.CHIP_FACTORY(self.i2c, address=i2c_bus_addr)
        self.status = {}

    def read_voltage(self, port):
        try:
            public_module = sys.modules[type(self).__module__]
            read_data = public_module.AnalogIn(self.ads, self.CHANNELS[port])
            voltage = math.floor(read_data.voltage * 1000)
        except BaseException:
            self.logger.exception(f"Exception occurred while reading probe port {port}.  Trace dump: ")
            voltage = 0
        return voltage

    def get_status(self):
        return self.status


def initialize_ads_probe(owner, device_class, chip_name: str) -> None:
    """Initialize a probe owner while preserving each public module's device seam."""
    owner.time_delay = 0.008
    owner.device_info["ports"] = ["ADC0", "ADC1", "ADC2", "ADC3"]
    i2c_bus_addr = int(owner.device_info["config"].get("i2c_bus_addr", "0x48"), 16)
    bus = parse_i2c_bus(owner.device_info["config"].get("i2c_bus") or {"kind": "basic"})
    try:
        owner.device = device_class(i2c_bus_addr=i2c_bus_addr, bus=bus)
    except Exception:
        owner.logger.error(
            f"Something went wrong when trying to initialize the {chip_name} device "
            f"(i2c bus {bus.describe()}, address=0x{i2c_bus_addr:02X})."
        )
        raise
