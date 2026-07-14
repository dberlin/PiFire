#!/usr/bin/env python3

"""
*****************************************
PiFire Shared I2C Bus Factory
*****************************************

Description:
  Single entry point for opening any I2C bus used by PiFire (probes, distance
  sensor, fan controller). Supports four bus kinds:

    basic      -- Blinka's board singleton: busio.I2C(board.SCL, board.SDA)
    extended   -- a kernel i2c-dev bus (/dev/i2c-N or an adapter-name match)
    ft232h     -- an FT232H USB adapter, via its Blinka MPSSE backend
    mcp2221    -- an MCP2221 USB adapter, via the EasyMCP2221 library

  ft232h/mcp2221 bypass the process-global `board` singleton so two USB
  adapters can run at once; they cannot be combined with `basic` (which owns
  `board`). See docs/superpowers/specs/2026-07-12-dual-usb-i2c-bus-design.md.

  mcp2221 uses EasyMCP2221 rather than Blinka's MCP2221 backend because
  Blinka's is a process-wide singleton (`mcp2221 = MCP2221()` opened once at
  import time): selecting a second serial re-points that *same* singleton's
  HID handle, silently stealing it out from under any bus already cached for
  the first serial. EasyMCP2221.Device() is a per-adapter object (deduped by
  USB path, not shared across different adapters), so multiple MCP2221s can
  be open and in use at once -- see
  docs/superpowers/specs/2026-07-14-mcp2221-easymcp2221-backend-design.md.
"""

import glob
import logging
import os
import threading

# Bus opens are logged here at DEBUG so it is obvious which physical bus/adapter
# is being resolved and opened when the control process runs in debug mode. The
# 'control' logger is the one control.py raises to DEBUG when debug_mode is set.
logger = logging.getLogger('control')

# USB-HID bus kinds that bypass Blinka's `board` singleton.
USB_HID_KINDS = frozenset({'ft232h', 'mcp2221'})

# Board/chip-forcing Blinka env vars. If any is set, `import board` is pinned to
# that backend process-wide, which silently breaks `basic` and any later
# `import board`. The MCP2221 entry is EXACT so the _HID_DELAY/_RESET_DELAY
# tuning vars stay allowed.
_FORBIDDEN_BLINKA_EXACT = frozenset(
	{
		'BLINKA_FT232H',
		'BLINKA_FT2232H',
		'BLINKA_FT4232H',
		'BLINKA_MCP2221',
		'BLINKA_U2IF',
		'BLINKA_GREATFET',
		'BLINKA_NOVA',
		'BLINKA_SPIDRIVER',
		'BLINKA_FORCECHIP',
		'BLINKA_FORCEBOARD',
	}
)
_FORBIDDEN_BLINKA_PREFIXES = ('BLINKA_FTX232H_',)

_UNSET = object()


class I2CBusConfigError(ValueError):
	"""Raised for an I2C bus configuration that cannot work on this host."""


def _read_usb_serial(bus_dir, max_hops=15):
	"""Return the USB iSerial of `bus_dir`'s (an i2c-N sysfs directory) USB
	ancestor, or None if it has none within `max_hops` parent directories (a
	non-USB adapter, e.g. a Pi's onboard I2C). Requires the ancestor to have
	both a 'serial' and an 'idVendor' file -- the USB *device* level in sysfs,
	as opposed to an interface level or an unrelated subsystem node that might
	also expose a 'serial' file (e.g. power_supply)."""
	current = os.path.realpath(bus_dir)
	for _ in range(max_hops):
		parent = os.path.dirname(current)
		if parent == current:
			return None
		current = parent
		serial_path = os.path.join(current, 'serial')
		vendor_path = os.path.join(current, 'idVendor')
		if os.path.isfile(serial_path) and os.path.isfile(vendor_path):
			try:
				with open(serial_path) as handle:
					return handle.read().strip()
			except OSError:
				return None
	return None


def _enumerate_i2c_adapters(devices_path='/sys/bus/i2c/devices'):
	"""Return [{'bus_num': int, 'name': str, 'serial': str | None}, ...] for
	every i2c-dev adapter under devices_path. 'serial' is the USB iSerial of
	the adapter's USB ancestor (via _read_usb_serial), or None if it has none
	(e.g. an onboard/non-USB adapter)."""
	adapters = []
	for bus_dir in glob.glob(os.path.join(devices_path, 'i2c-*')):
		try:
			with open(os.path.join(bus_dir, 'name')) as handle:
				name = handle.read().strip()
		except OSError:
			continue
		try:
			bus_num = int(os.path.basename(bus_dir).split('-')[-1])
		except ValueError:
			continue
		adapters.append({'bus_num': bus_num, 'name': name, 'serial': _read_usb_serial(bus_dir)})
	return sorted(adapters, key=lambda a: a['bus_num'])


def find_i2c_bus(match, devices_path='/sys/bus/i2c/devices'):
	"""
	Return the integer i2c bus number whose adapter name contains `match`
	(case-insensitive), e.g. 'CP2112' for a USB-to-I2C bridge. Scans
	`<devices_path>/i2c-*/name`. Raises RuntimeError if zero or more than one
	adapter matches, so the caller fails clearly rather than guessing.
	"""
	match_lower = str(match).lower()
	adapters = _enumerate_i2c_adapters(devices_path)

	found = [a['bus_num'] for a in adapters if match_lower in a['name'].lower()]
	available = (
		', '.join(f'i2c-{a["bus_num"]} ({a["name"]!r})' for a in sorted(adapters, key=lambda a: a['bus_num']))
		or '(none)'
	)
	logger.debug('find_i2c_bus: matching %r among adapters: %s', match, available)
	if len(found) == 1:
		logger.debug('find_i2c_bus: %r matched i2c-%d', match, found[0])
		return found[0]
	if not found:
		raise RuntimeError(
			f'No i2c adapter found matching {match!r} under {devices_path}. Available adapters: {available}'
		)
	raise RuntimeError(f'Multiple i2c adapters match {match!r}: {sorted(found)}. Available adapters: {available}')


def find_i2c_bus_by_serial(serial, devices_path='/sys/bus/i2c/devices'):
	"""
	Return the integer i2c bus number whose adapter's USB iSerial exactly
	equals `serial` (case-sensitive, no substring matching -- a serial is
	meant to be unambiguous). Raises RuntimeError if zero or more than one
	adapter matches, listing every available adapter (with its serial, if
	any) so the error is actionable without a second lookup.
	"""
	target = str(serial)
	adapters = _enumerate_i2c_adapters(devices_path)

	found = [a['bus_num'] for a in adapters if a['serial'] == target]
	available = (
		', '.join(f'i2c-{a["bus_num"]} (serial={a["serial"]!r})' for a in sorted(adapters, key=lambda a: a['bus_num']))
		or '(none)'
	)
	logger.debug('find_i2c_bus_by_serial: matching %r among adapters: %s', serial, available)
	if len(found) == 1:
		logger.debug('find_i2c_bus_by_serial: %r matched i2c-%d', serial, found[0])
		return found[0]
	if not found:
		raise RuntimeError(
			f'No i2c adapter found with serial {serial!r} under {devices_path}. Available adapters: {available}'
		)
	raise RuntimeError(
		f'Multiple i2c adapters have serial {serial!r}: {sorted(found)}. Available adapters: {available}'
	)


def discover_extended_i2c_buses(devices_path='/sys/bus/i2c/devices'):
	"""Best-effort list of every extended-kind (kernel i2c-dev) adapter
	present, for the wizard's Discover button. Returns [] if devices_path
	doesn't exist or has no adapters; never raises."""
	return _enumerate_i2c_adapters(devices_path)


# MCP2221(A) chip's fixed USB VID/PID -- the same constants EasyMCP2221 and
# Blinka's MCP2221 backend both use internally.
_MCP2221_VID = 0x04D8
_MCP2221_PID = 0x00DD


def discover_mcp2221_devices():
	"""Best-effort list of connected MCP2221 USB devices ({'serial', 'path'}),
	for the wizard's Discover button. Returns [] if the `hid` module isn't
	importable, or no devices are present -- never raises."""
	try:
		import hid
	except ImportError:
		return []
	try:
		return sorted(
			(
				{'serial': info.get('serial_number'), 'path': info.get('path')}
				for info in hid.enumerate(_MCP2221_VID, _MCP2221_PID)
				if info.get('serial_number')
			),
			key=lambda d: d['serial'].lower(),
		)
	except Exception:
		logger.debug('discover_mcp2221_devices: hid.enumerate failed', exc_info=True)
		return []


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


def resolve_i2c_bus(bus):
	"""
	Resolve an extended-i2c-bus spec to a bus number. Accepts an int or numeric
	string (e.g. 3 / '3' -> /dev/i2c-3, used directly), a 'serial:<ISERIAL>'
	USB-serial match (e.g. 'serial:0012AB34' -> discovered via
	find_i2c_bus_by_serial, the only way to distinguish two identical USB-to-I2C
	bridges), or an adapter-name match string (e.g. 'CP2112' -> discovered via
	find_i2c_bus, robust against the dynamic bus numbers USB-to-I2C bridges get).
	"""
	spec = str(bus).strip()
	if spec.lower().startswith('serial:'):
		serial = spec.split(':', 1)[1].strip()
		logger.debug('resolve_i2c_bus: %r is a USB-serial match, discovering the bus number', bus)
		return find_i2c_bus_by_serial(serial)
	if spec.isdigit():
		logger.debug('resolve_i2c_bus: %r is a numeric bus -> /dev/i2c-%s', bus, spec)
		return int(spec)
	logger.debug('resolve_i2c_bus: %r is an adapter-name match, discovering the bus number', bus)
	return find_i2c_bus(spec)


def validate_bus_kinds(kinds):
	"""Raise I2CBusConfigError if the set of bus kinds cannot coexist in one
	process. The only unworkable case is `basic` alongside a USB-HID kind:
	Blinka's board backend is process-global."""
	kinds = {str(k).lower() for k in kinds if k}
	if 'basic' in kinds and (kinds & USB_HID_KINDS):
		raise I2CBusConfigError(
			"'basic' I2C can't share a process with a USB-HID bus (ft232h/mcp2221): "
			"Blinka's board backend is process-global. Use 'extended' for the onboard "
			'bus (a Pi onboard I2C is reachable as extended bus 1).'
		)


def configured_bus_kinds(settings, probe_map):
	"""Collect every I2C bus kind across probe devices, the distance sensor, and
	the platform fan controller. Used to validate a whole wizard config."""
	kinds = set()
	for device in (probe_map or {}).get('probe_devices', []):
		kind = (device.get('config') or {}).get('i2c_bus_kind')
		if kind:
			kinds.add(kind)
	platform = (settings or {}).get('platform', {})
	distance = (platform.get('devices', {}) or {}).get('distance', {}) or {}
	if distance.get('i2c_bus_kind'):
		kinds.add(distance['i2c_bus_kind'])
	fan = platform.get('fan_controller', {}) or {}
	if fan.get('i2c_bus_kind'):
		kinds.add(fan['i2c_bus_kind'])
	return kinds


def assert_clean_blinka_env(environ=None):
	"""Raise I2CBusConfigError if any board/chip-forcing BLINKA_* var is set.
	Called once at control-process startup so nobody can force `basic`/`import
	board` onto a USB adapter via the environment."""
	environ = os.environ if environ is None else environ
	offenders = sorted(
		key
		for key in environ
		if key in _FORBIDDEN_BLINKA_EXACT or any(key.startswith(p) for p in _FORBIDDEN_BLINKA_PREFIXES)
	)
	if offenders:
		raise I2CBusConfigError(
			f'Board-forcing Blinka environment variable(s) set: {", ".join(offenders)}. '
			'Remove them and select the ft232h/mcp2221 bus kinds in the wizard instead; '
			'forcing the Blinka board via the environment breaks `basic` and any import board.'
		)


class _LockedI2C:
	"""Wrap an I2C backend (Blinka's ft232h backend, or an EasyMCP2221
	backend for mcp2221) so Adafruit drivers can use it.

	The backend classes expose scan/writeto/readfrom_into/writeto_then_readfrom
	but not try_lock/unlock, which adafruit_bus_device.I2CDevice requires. Add a
	reentrant lock and delegate I/O to the backend."""

	def __init__(self, backend):
		self._backend = backend
		self._lock = threading.RLock()

	def try_lock(self):
		return self._lock.acquire(blocking=False)

	def unlock(self):
		try:
			self._lock.release()
		except RuntimeError:
			pass

	def scan(self):
		return self._backend.scan()

	def writeto(self, address, buffer, **kwargs):
		return self._backend.writeto(address, buffer, **kwargs)

	def readfrom_into(self, address, buffer, **kwargs):
		return self._backend.readfrom_into(address, buffer, **kwargs)

	def writeto_then_readfrom(self, address, out_buffer, in_buffer, **kwargs):
		return self._backend.writeto_then_readfrom(address, out_buffer, in_buffer, **kwargs)

	def deinit(self):
		deinit = getattr(self._backend, 'deinit', None)
		if deinit is not None:
			deinit()


class _EasyMCP2221Backend:
	"""Adapt an EasyMCP2221.Device to the scan/writeto/readfrom_into/
	writeto_then_readfrom surface _LockedI2C expects (the same surface the
	Blinka ft232h backend provides).

	Translates EasyMCP2221's NotAckError/TimeoutError/LowSCLError/LowSDAError
	into OSError, which is what adafruit_bus_device.I2CDevice's device-probe
	logic (and PiFire's own probe code) already knows how to treat as "no
	device at this address" / "bus fault"."""

	def __init__(self, device):
		from EasyMCP2221.exceptions import LowSCLError, LowSDAError, NotAckError, TimeoutError

		self._device = device
		self._errors = (NotAckError, TimeoutError, LowSCLError, LowSDAError)

	def scan(self):
		found = []
		for address in range(0x08, 0x78):
			try:
				self._device.I2C_read(address, 1)
			except self._errors:
				continue
			found.append(address)
		return found

	def writeto(self, address, buffer, *, start=0, end=None, **kwargs):
		end = len(buffer) if end is None else end
		data = bytes(buffer[start:end])
		try:
			if data:
				self._device.I2C_write(address, data)
			else:
				# EasyMCP2221.I2C_write rejects empty data; a zero-length
				# writeto is only ever used as a device-presence probe
				# (adafruit_bus_device.I2CDevice.__probe_for_device), so a
				# 1-byte read serves the same purpose.
				self._device.I2C_read(address, 1)
		except self._errors as exc:
			raise OSError(str(exc)) from exc

	def readfrom_into(self, address, buffer, *, start=0, end=None, **kwargs):
		end = len(buffer) if end is None else end
		try:
			data = self._device.I2C_read(address, end - start)
		except self._errors as exc:
			raise OSError(str(exc)) from exc
		buffer[start:end] = data

	def writeto_then_readfrom(
		self, address, out_buffer, in_buffer, *, out_start=0, out_end=None, in_start=0, in_end=None, **kwargs
	):
		out_end = len(out_buffer) if out_end is None else out_end
		in_end = len(in_buffer) if in_end is None else in_end
		try:
			self._device.I2C_write(address, bytes(out_buffer[out_start:out_end]), kind='nonstop')
			data = self._device.I2C_read(address, in_end - in_start, kind='restart')
		except self._errors as exc:
			raise OSError(str(exc)) from exc
		in_buffer[in_start:in_end] = data


_bus_cache = {}  # (kind, selector) -> bus object
_opened_kinds = set()  # kinds actually opened this process
_cache_lock = threading.RLock()


def reset_bus_state():
	"""Clear the bus cache and opened-kind registry. Tests only."""
	with _cache_lock:
		_bus_cache.clear()
		_opened_kinds.clear()


def _canonical_selector(kind, selector):
	sel = '' if selector in (None, '') else str(selector)
	# For ft232h, blank and '1' both mean "first FT232H" -> one cache entry.
	if kind == 'ft232h' and sel in ('', '1'):
		sel = ''
	return sel


def _construct_ft232h(selector):
	from adafruit_blinka.microcontroller.ftdi_mpsse.mpsse.i2c import I2C as _FT232H_I2C

	# The backend reads BLINKA_FT232H only during __init__ (get_ft232h_url()).
	# Set it transiently and restore the prior value so the factory never leaves
	# a board-forcing var in the environment (keeps assert_clean_blinka_env true
	# process-wide). If a caller pre-set it (ft232h_relay), restore keeps it set.
	url = str(selector) if selector else '1'
	logger.debug('open_i2c_bus[ft232h]: opening FT232H via BLINKA_FT232H=%r', url)
	prev = os.environ.get('BLINKA_FT232H', _UNSET)
	os.environ['BLINKA_FT232H'] = url
	try:
		backend = _FT232H_I2C()
	finally:
		if prev is _UNSET:
			os.environ.pop('BLINKA_FT232H', None)
		else:
			os.environ['BLINKA_FT232H'] = prev
	return _LockedI2C(backend)


def _construct_mcp2221(selector):
	from EasyMCP2221 import Device as _MCP2221Device

	try:
		if selector:
			logger.debug('open_i2c_bus[mcp2221]: opening MCP2221 with serial=%r', selector)
			device = _MCP2221Device(usbserial=str(selector), scan_serial=True)
		else:
			logger.debug(
				'open_i2c_bus[mcp2221]: opening first MCP2221 (VID 0x%04X / PID 0x%04X)', _MCP2221_VID, _MCP2221_PID
			)
			device = _MCP2221Device()
	except RuntimeError as exc:
		raise I2CBusConfigError(str(exc)) from exc
	return _LockedI2C(_EasyMCP2221Backend(device))


def _construct_bus(kind, selector):
	if kind == 'basic':
		import board
		import busio

		logger.debug('open_i2c_bus[basic]: opening Blinka board.SCL/SDA')
		return busio.I2C(board.SCL, board.SDA)
	if kind == 'extended':
		from adafruit_extended_bus import ExtendedI2C

		bus_num = resolve_i2c_bus(selector)
		logger.debug('open_i2c_bus[extended]: opening /dev/i2c-%s (from selector=%r)', bus_num, selector)
		return ExtendedI2C(bus_num)
	if kind == 'ft232h':
		return _construct_ft232h(selector)
	if kind == 'mcp2221':
		return _construct_mcp2221(selector)
	raise I2CBusConfigError(f'Unknown i2c bus kind {kind!r}.')


def open_i2c_bus(bus_kind='basic', bus_selector=None):
	"""Return a busio.I2C-compatible bus for `bus_kind`, opening it if needed.

	bus_selector is the stored i2c_bus_num value: a /dev/i2c-N number or adapter
	match for `extended`, a pyftdi URL for `ft232h`, an MCP2221 serial for
	`mcp2221`; ignored for `basic`. Buses are cached per (kind, selector) for
	the process lifetime so every device on one physical bus shares one handle
	and lock. Raises I2CBusConfigError for an unworkable combination."""
	kind = (bus_kind or 'basic').strip().lower()
	selector = _canonical_selector(kind, bus_selector)
	with _cache_lock:
		validate_bus_kinds(_opened_kinds | {kind})
		key = (kind, selector)
		bus = _bus_cache.get(key)
		if bus is None:
			logger.debug('open_i2c_bus: opening new bus kind=%r selector=%r', kind, selector)
			bus = _construct_bus(kind, selector)
			_bus_cache[key] = bus
		else:
			logger.debug('open_i2c_bus: reusing cached bus kind=%r selector=%r', kind, selector)
		_opened_kinds.add(kind)
		return bus
