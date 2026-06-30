#!/usr/bin/env python3

# *****************************************
# PiFire EMC2301 Fan Controller Driver
# *****************************************
#
# Description: Minimal driver for the Microchip EMC2301 SMBus PWM fan
#   controller. There is no Adafruit library for the EMC2301, so this class
#   mimics the slice of the Adafruit EMC2101 interface the x86 platform uses
#   (`manual_fan_speed`, `pwm_frequency`) over the same I2C bus objects.
#
# *****************************************

from adafruit_bus_device.i2c_device import I2CDevice

# Register addresses (Microchip EMC2301/2/3/5 DS20006532A).
_REG_CONFIG = 0x20  # Configuration
_REG_PWM_BASE_FREQ = 0x2D  # PWM base frequency select
_REG_FAN_SETTING = 0x30  # Direct PWM duty (0x00-0xFF)
_REG_PWM_DIVIDE = 0x31  # PWM divide ratio

# Configuration register bits.
_CONFIG_DIS_TO = 0x40  # bit 6: 1 = SMBus timeout disabled
_CONFIG_WD_EN = 0x20  # bit 5: 1 = watchdog runs continuously

# PWM base frequency: Hz -> 0x2D register value.
_BASE_FREQS = {26000: 0x00, 19531: 0x01, 4882: 0x02, 2441: 0x03}
_BASE_VALUE_TO_HZ = {value: hz for hz, value in _BASE_FREQS.items()}

_DEFAULT_ADDRESS = 0x2F
_MAX_DUTY = 0xFF


class EMC2301:
	def __init__(self, i2c_bus, address=_DEFAULT_ADDRESS):
		self.i2c_device = I2CDevice(i2c_bus, address)
		# Disable the SMBus timeout (DIS_TO=1) and keep the watchdog out of
		# continuous mode (WD_EN=0) so the fan is never force-ramped to full
		# speed during quiet periods; preserve the other config bits.
		config = self._read_register(_REG_CONFIG)
		config |= _CONFIG_DIS_TO
		config &= ~_CONFIG_WD_EN
		self._write_register(_REG_CONFIG, config)
		# Known 26 kHz output: 26 kHz base, divide by 1. Fan stopped.
		self._write_register(_REG_PWM_BASE_FREQ, _BASE_FREQS[26000])
		self._write_register(_REG_PWM_DIVIDE, 0x01)
		self._write_register(_REG_FAN_SETTING, 0x00)

	def _read_register(self, register):
		result = bytearray(1)
		with self.i2c_device as i2c:
			i2c.write_then_readinto(bytes([register]), result)
		return result[0]

	def _write_register(self, register, value):
		with self.i2c_device as i2c:
			i2c.write(bytes([register, value & 0xFF]))

	@property
	def manual_fan_speed(self):
		raw = self._read_register(_REG_FAN_SETTING)
		return (raw / _MAX_DUTY) * 100.0

	@manual_fan_speed.setter
	def manual_fan_speed(self, percent):
		if not 0 <= percent <= 100:
			raise ValueError('manual_fan_speed must be from 0-100')
		self._write_register(_REG_FAN_SETTING, round((percent / 100.0) * _MAX_DUTY))

	@property
	def pwm_frequency(self):
		base_value = self._read_register(_REG_PWM_BASE_FREQ) & 0x03
		divide = self._read_register(_REG_PWM_DIVIDE) or 1
		base_hz = _BASE_VALUE_TO_HZ.get(base_value, 26000)
		return base_hz / divide

	@pwm_frequency.setter
	def pwm_frequency(self, hz):
		nearest = min(_BASE_FREQS, key=lambda base: abs(base - hz))
		self._write_register(_REG_PWM_BASE_FREQ, _BASE_FREQS[nearest])
		self._write_register(_REG_PWM_DIVIDE, 0x01)
