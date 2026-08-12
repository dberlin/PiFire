"""Stable Adafruit ADS1115 probe plugin."""

import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

from common.i2c_bus import open_i2c_bus
from probes._ads1x15_adafruit import AdafruitADSDevice, initialize_ads_probe
from probes.base import ProbeInterface


class ADSDevice(AdafruitADSDevice):
    """ADS1115 selector for the shared Adafruit ADS1x15 implementation."""

    CHIP_FACTORY = ADS.ADS1115
    CHANNELS = {"ADC0": ADS.P0, "ADC1": ADS.P1, "ADC2": ADS.P2, "ADC3": ADS.P3}


class ReadProbes(ProbeInterface):
    def _init_device(self):
        initialize_ads_probe(self, ADSDevice, "ADS1115")
