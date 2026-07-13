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
    mcp2221a   -- an MCP2221A USB adapter, via its Blinka backend

  ft232h/mcp2221a bypass the process-global `board` singleton so two USB
  adapters can run at once; they cannot be combined with `basic` (which owns
  `board`). See docs/superpowers/specs/2026-07-12-dual-usb-i2c-bus-design.md.
"""

import glob
import os
import threading

# USB-HID bus kinds that bypass Blinka's `board` singleton.
USB_HID_KINDS = frozenset({'ft232h', 'mcp2221a'})

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


def find_i2c_bus(match, devices_path='/sys/bus/i2c/devices'):
	"""
	Return the integer i2c bus number whose adapter name contains `match`
	(case-insensitive), e.g. 'CP2112' for a USB-to-I2C bridge. Scans
	`<devices_path>/i2c-*/name`. Raises RuntimeError if zero or more than one
	adapter matches, so the caller fails clearly rather than guessing.
	"""
	match_lower = str(match).lower()
	adapters = []  # (bus_num, name) for every i2c adapter present
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
		adapters.append((bus_num, name))

	found = [num for num, name in adapters if match_lower in name.lower()]
	if len(found) == 1:
		return found[0]
	# Include what IS present so a misconfigured match string is easy to fix.
	available = ', '.join(f'i2c-{n} ({name!r})' for n, name in sorted(adapters)) or '(none)'
	if not found:
		raise RuntimeError(
			f'No i2c adapter found matching {match!r} under {devices_path}. Available adapters: {available}'
		)
	raise RuntimeError(f'Multiple i2c adapters match {match!r}: {sorted(found)}. Available adapters: {available}')


def resolve_i2c_bus(bus):
	"""
	Resolve an extended-i2c-bus spec to a bus number. Accepts an int or numeric
	string (e.g. 3 / '3' -> /dev/i2c-3, used directly) or an adapter-name match
	string (e.g. 'CP2112' -> discovered via find_i2c_bus, robust against the
	dynamic bus numbers USB-to-I2C bridges get).
	"""
	spec = str(bus).strip()
	if spec.isdigit():
		return int(spec)
	return find_i2c_bus(spec)


def validate_bus_kinds(kinds):
	"""Raise I2CBusConfigError if the set of bus kinds cannot coexist in one
	process. The only unworkable case is `basic` alongside a USB-HID kind:
	Blinka's board backend is process-global."""
	kinds = {str(k).lower() for k in kinds if k}
	if 'basic' in kinds and (kinds & USB_HID_KINDS):
		raise I2CBusConfigError(
			"'basic' I2C can't share a process with a USB-HID bus (ft232h/mcp2221a): "
			"Blinka's board backend is process-global. Use 'extended' for the onboard "
			'bus (a Pi onboard I2C is reachable as extended bus 1).'
		)


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
			'Remove them and select the ft232h/mcp2221a bus kinds in the wizard instead; '
			'forcing the Blinka board via the environment breaks `basic` and any import board.'
		)
