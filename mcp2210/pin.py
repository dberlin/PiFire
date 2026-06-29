"""digitalio-compatible GPIO for the MCP2210 (GP0-GP8)."""

from . import _protocol as p


class Pin:
	# Direction bit semantics match the chip: 1 = input, 0 = output.
	IN = 1
	OUT = 0
	PULL_UP = 'PULL_UP'
	PULL_DOWN = 'PULL_DOWN'

	def __init__(self, device, index):
		if not 0 <= index <= 8:
			raise ValueError('MCP2210 has GPIO GP0-GP8')
		self._device = device
		self.index = index
		self._mask = 1 << index

	def init(self, mode=IN, pull=None):
		if pull is not None:
			raise NotImplementedError('MCP2210 has no internal pull resistors')
		# 1. Ensure this pin is designated as GPIO in chip settings.
		cs = self._device.get_chip_settings()
		designations = cs['designations']
		if designations[self.index] != p.PIN_GPIO:
			designations[self.index] = p.PIN_GPIO
			self._device.set_chip_settings(designations, cs['gpio_output'], cs['gpio_direction'], cs['other'])
		# 2. Set this pin's direction bit.
		direction = self._device.get_gpio_direction()
		if mode == Pin.IN:
			direction |= self._mask
		else:
			direction &= ~self._mask
		self._device.set_gpio_direction(direction & 0x1FF)

	def value(self, val=None):
		if val is None:
			return 1 if (self._device.get_gpio_value() & self._mask) else 0
		current = self._device.get_gpio_value()
		if val:
			current |= self._mask
		else:
			current &= ~self._mask
		self._device.set_gpio_value(current & 0x1FF)
		return None


class DigitalInOut:
	"""Minimal CircuitPython digitalio.DigitalInOut surface over a Pin."""

	def __init__(self, pin):
		self._pin = pin
		self._direction = None

	def switch_to_output(self, value=False, drive_mode=None):
		self._pin.init(mode=Pin.OUT)
		self._direction = Pin.OUT
		self.value = value

	def switch_to_input(self, pull=None):
		self._pin.init(mode=Pin.IN, pull=pull)
		self._direction = Pin.IN

	@property
	def direction(self):
		return self._direction

	@direction.setter
	def direction(self, value):
		if value == Pin.OUT:
			self.switch_to_output()
		else:
			self.switch_to_input()

	@property
	def value(self):
		return bool(self._pin.value())

	@value.setter
	def value(self, val):
		self._pin.value(1 if val else 0)

	def deinit(self):
		self._pin = None
