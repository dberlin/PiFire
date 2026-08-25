"""
*****************************************
PiFire Shared I2C Bus Factory
*****************************************

Description:
  Single entry point for opening any I2C bus used by PiFire (probes, distance
  sensor, fan controller). Supports four bus kinds:

    basic      -- Blinka's board singleton: busio.I2C(board.SCL, board.SDA)
    kernel     -- a kernel i2c-dev bus (/dev/i2c-N, an adapter name, or a USB serial)
    ft232h     -- an FT232H USB adapter, via pyftdi (see grillplat/ft232h.py)
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

from __future__ import annotations

import glob
import logging
import os
import threading
from typing import TYPE_CHECKING, cast

from common.i2c_bus_config import (  # noqa: F401  # I2CBusConfigError is public here
    BasicBus,
    FT232HBus,
    I2CBus,
    I2CBusConfigError,
    KernelBus,
    MCP2221Bus,
    parse_i2c_bus,
)

if TYPE_CHECKING:
    from busio import I2C

# Bus opens are logged here at DEBUG so it is obvious which physical bus/adapter
# is being resolved and opened when the control process runs in debug mode. The
# 'control' logger is the one control.py raises to DEBUG when debug_mode is set.
logger = logging.getLogger("control")

# USB-HID bus kinds that bypass Blinka's `board` singleton.
USB_HID_KINDS = frozenset({"ft232h", "mcp2221"})

# Board/chip-forcing Blinka env vars. If any is set, `import board` is pinned to
# that backend process-wide, which silently breaks `basic` and any later
# `import board`. The MCP2221 entry is EXACT so the _HID_DELAY/_RESET_DELAY
# tuning vars stay allowed.
_FORBIDDEN_BLINKA_EXACT = frozenset(
    {
        "BLINKA_FT232H",
        "BLINKA_FT2232H",
        "BLINKA_FT4232H",
        "BLINKA_MCP2221",
        "BLINKA_U2IF",
        "BLINKA_GREATFET",
        "BLINKA_NOVA",
        "BLINKA_SPIDRIVER",
        "BLINKA_FORCECHIP",
        "BLINKA_FORCEBOARD",
    }
)
_FORBIDDEN_BLINKA_PREFIXES = ("BLINKA_FTX232H_",)


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
        serial_path = os.path.join(current, "serial")
        vendor_path = os.path.join(current, "idVendor")
        if os.path.isfile(serial_path) and os.path.isfile(vendor_path):
            try:
                with open(serial_path) as handle:
                    return handle.read().strip()
            except OSError:
                return None
    return None


def _enumerate_i2c_adapters(devices_path="/sys/bus/i2c/devices"):
    """Return [{'bus_num': int, 'name': str, 'serial': str | None}, ...] for
    every i2c-dev adapter under devices_path. 'serial' is the USB iSerial of
    the adapter's USB ancestor (via _read_usb_serial), or None if it has none
    (e.g. an onboard/non-USB adapter)."""
    adapters = []
    for bus_dir in glob.glob(os.path.join(devices_path, "i2c-*")):
        try:
            with open(os.path.join(bus_dir, "name")) as handle:
                name = handle.read().strip()
        except OSError:
            continue
        try:
            bus_num = int(os.path.basename(bus_dir).split("-")[-1])
        except ValueError:
            continue
        adapters.append({"bus_num": bus_num, "name": name, "serial": _read_usb_serial(bus_dir)})
    return sorted(adapters, key=lambda a: a["bus_num"])


def find_i2c_bus(match, devices_path="/sys/bus/i2c/devices"):
    """
    Return the integer i2c bus number whose adapter name contains `match`
    (case-insensitive), e.g. 'CP2112' for a USB-to-I2C bridge. Scans
    `<devices_path>/i2c-*/name`. Raises RuntimeError if zero or more than one
    adapter matches, so the caller fails clearly rather than guessing.
    """
    match_lower = str(match).lower()
    adapters = _enumerate_i2c_adapters(devices_path)

    found = [a["bus_num"] for a in adapters if match_lower in a["name"].lower()]
    available = (
        ", ".join(f"i2c-{a['bus_num']} ({a['name']!r})" for a in sorted(adapters, key=lambda a: a["bus_num"]))
        or "(none)"
    )
    logger.debug("find_i2c_bus: matching %r among adapters: %s", match, available)
    if len(found) == 1:
        logger.debug("find_i2c_bus: %r matched i2c-%d", match, found[0])
        return found[0]
    if not found:
        raise RuntimeError(
            f"No i2c adapter found matching {match!r} under {devices_path}. Available adapters: {available}"
        )
    raise RuntimeError(f"Multiple i2c adapters match {match!r}: {sorted(found)}. Available adapters: {available}")


def find_i2c_bus_by_serial(serial, devices_path="/sys/bus/i2c/devices"):
    """
    Return the integer i2c bus number whose adapter's USB iSerial exactly
    equals `serial` (case-sensitive, no substring matching -- a serial is
    meant to be unambiguous). Raises RuntimeError if zero or more than one
    adapter matches, listing every available adapter (with its serial, if
    any) so the error is actionable without a second lookup.
    """
    target = str(serial)
    adapters = _enumerate_i2c_adapters(devices_path)

    found = [a["bus_num"] for a in adapters if a["serial"] == target]
    available = (
        ", ".join(f"i2c-{a['bus_num']} (serial={a['serial']!r})" for a in sorted(adapters, key=lambda a: a["bus_num"]))
        or "(none)"
    )
    logger.debug("find_i2c_bus_by_serial: matching %r among adapters: %s", serial, available)
    if len(found) == 1:
        logger.debug("find_i2c_bus_by_serial: %r matched i2c-%d", serial, found[0])
        return found[0]
    if not found:
        raise RuntimeError(
            f"No i2c adapter found with serial {serial!r} under {devices_path}. Available adapters: {available}"
        )
    raise RuntimeError(
        f"Multiple i2c adapters have serial {serial!r}: {sorted(found)}. Available adapters: {available}"
    )


def discover_extended_i2c_buses(devices_path="/sys/bus/i2c/devices"):
    """Best-effort list of every extended-kind (kernel i2c-dev) adapter
    present, for the wizard's Discover button. Returns [] if devices_path
    doesn't exist or has no adapters; never raises."""
    return _enumerate_i2c_adapters(devices_path)


def discover_mcp2221_devices(*args, **kwargs):
    """Re-export: delegates to grillplat.mcp2221.discover_mcp2221_devices()."""
    from grillplat import mcp2221

    return mcp2221.discover_mcp2221_devices(*args, **kwargs)


def discover_ft232h_devices(*args, **kwargs):
    """Re-export: delegates to grillplat.ft232h.discover_ft232h_devices()."""
    from grillplat import ft232h

    return ft232h.discover_ft232h_devices(*args, **kwargs)


def validate_bus_kinds(kinds):
    """Raise I2CBusConfigError if the set of bus kinds cannot coexist in one
    process. The only unworkable case is `basic` alongside a USB-HID kind:
    Blinka's board backend is process-global."""
    kinds = {str(k).lower() for k in kinds if k}
    if "basic" in kinds and (kinds & USB_HID_KINDS):
        raise I2CBusConfigError(
            "'basic' I2C can't share a process with a USB-HID bus (ft232h/mcp2221): "
            "Blinka's board backend is process-global. Use 'kernel' for the onboard "
            "bus (a Pi onboard I2C is reachable as kernel bus 1)."
        )


def configured_bus_kinds(settings, probe_map):
    """Every active I2C bus kind across probe devices, the distance sensor,
    and an enabled EMC fan controller. Used to validate a whole wizard config
    before it is installed."""
    kinds = set()
    for device in (probe_map or {}).get("probe_devices", []):
        bus = (device.get("config") or {}).get("i2c_bus")
        if bus:
            kinds.add(parse_i2c_bus(bus).kind)
    platform = (settings or {}).get("platform", {})
    distance = (platform.get("devices", {}) or {}).get("distance", {}) or {}
    distance_module = str(((settings or {}).get("modules", {}) or {}).get("dist", "")).lower()
    if distance_module in {"vl53l0x", "vl53l4cd", "vl53l1x"} and distance.get("i2c_bus"):
        kinds.add(parse_i2c_bus(distance["i2c_bus"]).kind)
    fan_controller = platform.get("fan_controller", {}) or {}
    fan_chip = str(fan_controller.get("chip", "")).lower()
    if fan_chip in {"emc2101", "emc2301"} and fan_controller.get("i2c_bus"):
        kinds.add(parse_i2c_bus(fan_controller["i2c_bus"]).kind)
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
            f"Board-forcing Blinka environment variable(s) set: {', '.join(offenders)}. "
            "Remove them and select the ft232h/mcp2221 bus kinds in the wizard instead; "
            "forcing the Blinka board via the environment breaks `basic` and any import board."
        )


class _LockedI2C:
    """Wrap an I2C backend (a pyftdi-based backend for ft232h, or an
    EasyMCP2221 backend for mcp2221) so Adafruit drivers can use it.

    The backend classes expose scan/writeto/readfrom_into/writeto_then_readfrom
    but not try_lock/unlock, which adafruit_bus_device.I2CDevice requires. Add a
    reentrant lock and delegate I/O to the backend."""

    def __init__(self, backend, lock=None):
        self._backend = backend
        self._lock = lock if lock is not None else threading.RLock()

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
        deinit = getattr(self._backend, "deinit", None)
        if deinit is not None:
            deinit()


_bus_cache = {}  # I2CBus -> bus object
_opened_kinds = set()  # kinds actually opened this process
_cache_lock = threading.RLock()


def reset_bus_state():
    """Clear the bus cache and opened-kind registry. Tests only."""
    from grillplat import ft232h, mcp2221

    with _cache_lock:
        _bus_cache.clear()
        _opened_kinds.clear()
        ft232h.reset_state()
        mcp2221.reset_state()


def _construct_bus(bus):
    if isinstance(bus, BasicBus):
        import board
        import busio

        logger.debug("open_i2c_bus[basic]: opening Blinka board.SCL/SDA")
        return busio.I2C(board.SCL, board.SDA)
    if isinstance(bus, KernelBus):
        from adafruit_extended_bus import ExtendedI2C

        bus_num = bus.resolve_bus_num()
        logger.debug("open_i2c_bus[kernel]: opening /dev/i2c-%s (from %s)", bus_num, bus.describe())
        return ExtendedI2C(bus_num)
    if isinstance(bus, FT232HBus):
        from grillplat import ft232h

        return ft232h.construct_i2c_bus(bus.url)
    if isinstance(bus, MCP2221Bus):
        from grillplat import mcp2221

        return mcp2221.construct_i2c_bus(bus.serial)
    raise I2CBusConfigError(f"Unknown I2C bus {bus!r}.")


def open_i2c_bus(bus: I2CBus | dict[str, object]) -> I2C:
    """Return a busio.I2C-compatible bus for `bus`, opening it if needed.

    `bus` is an I2CBus, or the stored mapping parse_i2c_bus accepts. Open buses
    are cached process-wide keyed by the bus object itself, so two devices that
    name the same hardware share one bus -- a single USB adapter opened twice
    yields two controllers fighting over one MPSSE engine.
    """
    bus = parse_i2c_bus(bus)
    with _cache_lock:
        validate_bus_kinds(_opened_kinds | {bus.kind})
        opened = _bus_cache.get(bus)
        if opened is None:
            logger.debug("open_i2c_bus: opening %s", bus.describe())
            opened = _construct_bus(bus)
            _bus_cache[bus] = opened
            _opened_kinds.add(bus.kind)
        return cast("I2C", opened)
