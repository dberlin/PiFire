#!/usr/bin/env python3

"""FT232H USB adapter backend, via pyftdi directly (not Adafruit Blinka).

One pyftdi.i2c.I2cController per FT232H (cached by url) exposes the I2C bus
(get_port, for an EMC fan controller). This bypasses Blinka's process-global
`board` singleton, which resolves to the wrong board when `import board` runs
before BLINKA_FT232H is set (the ft232h_relay `board has no attribute 'C0'`
failure). See docs/superpowers/specs/2026-07-14-ft232h-pyftdi-backend-design.md.
"""

import logging
import threading

from common.i2c_bus import _LockedI2C

logger = logging.getLogger('control')

_I2C_FREQUENCY = 100_000  # Hz; matches Blinka's mpsse default.


def discover_ft232h_devices():
	"""Best-effort list of connected FT232H USB devices ({'url', 'serial',
	'description'}), for the wizard's Discover button. Returns [] if pyftdi
	isn't importable or no devices are present -- never raises."""
	try:
		from pyftdi.ftdi import Ftdi
	except ImportError:
		return []
	try:
		devices = []
		for descriptor, _interface_count in Ftdi.list_devices('ftdi://ftdi:232h/'):
			url = f'ftdi://ftdi:232h:{descriptor.sn}/1' if descriptor.sn else 'ftdi://ftdi:232h/1'
			devices.append({'url': url, 'serial': descriptor.sn, 'description': descriptor.description})
		return sorted(devices, key=lambda d: (d['serial'] or '').lower())
	except Exception:
		logger.debug('discover_ft232h_devices: Ftdi.list_devices failed', exc_info=True)
		return []


def canonical_url(selector):
	"""Canonical pyftdi url for an FT232H selector. Blank/'1'/None all mean
	'the first FT232H' -> one shared controller."""
	sel = '' if selector in (None, '') else str(selector)
	if sel in ('', '1'):
		return '1'
	return sel


_controllers = {}  # canonical_url -> I2cController
_gpios = {}  # canonical_url -> Ft232hGpio
_lock = threading.RLock()


def reset_state():
	"""Clear the controller and GPIO caches. Tests only."""
	with _lock:
		_controllers.clear()
		_gpios.clear()


def _new_controller(url, frequency):
	"""Open and configure a pyftdi I2cController. Isolated as a test seam."""
	from pyftdi.i2c import I2cController

	controller = I2cController()
	controller.configure(url, frequency=frequency)
	return controller


def _get_controller(selector):
	url = canonical_url(selector)
	with _lock:
		controller = _controllers.get(url)
		if controller is None:
			logger.debug('ft232h: opening pyftdi I2cController url=%r @ %d Hz', url, _I2C_FREQUENCY)
			controller = _new_controller(url, _I2C_FREQUENCY)
			_controllers[url] = controller
		return controller


class _PyFtdiI2CBackend:
	"""Adapt a pyftdi I2cController to the scan/writeto/readfrom_into/
	writeto_then_readfrom surface _LockedI2C expects. Translates pyftdi I2C
	errors into OSError (what adafruit_bus_device / probe code treat as
	'no device' / 'bus fault')."""

	def __init__(self, controller):
		from pyftdi.i2c import I2cIOError, I2cNackError, I2cTimeoutError

		self._controller = controller
		self._errors = (I2cNackError, I2cIOError, I2cTimeoutError)

	def scan(self):
		return [addr for addr in range(0x08, 0x78) if self._controller.poll(addr)]

	def writeto(self, address, buffer, *, start=0, end=None, **kwargs):
		end = len(buffer) if end is None else end
		data = bytes(buffer[start:end])
		try:
			self._controller.get_port(address).write(data)
		except self._errors as exc:
			raise OSError(str(exc)) from exc

	def readfrom_into(self, address, buffer, *, start=0, end=None, **kwargs):
		end = len(buffer) if end is None else end
		try:
			data = self._controller.get_port(address).read(end - start)
		except self._errors as exc:
			raise OSError(str(exc)) from exc
		buffer[start:end] = data

	def writeto_then_readfrom(
		self, address, out_buffer, in_buffer, *, out_start=0, out_end=None, in_start=0, in_end=None, **kwargs
	):
		out_end = len(out_buffer) if out_end is None else out_end
		in_end = len(in_buffer) if in_end is None else in_end
		try:
			data = self._controller.get_port(address).exchange(bytes(out_buffer[out_start:out_end]), in_end - in_start)
		except self._errors as exc:
			raise OSError(str(exc)) from exc
		in_buffer[in_start:in_end] = data


def construct_i2c_bus(selector):
	"""Open (or reuse) the FT232H for `selector` and return a _LockedI2C bus
	over its pyftdi I2C port."""
	controller = _get_controller(selector)
	return _LockedI2C(_PyFtdiI2CBackend(controller))
