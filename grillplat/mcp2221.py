#!/usr/bin/env python3

"""FT232H's sibling: the MCP2221 USB-I2C adapter backend.

Uses EasyMCP2221.Device rather than Blinka's MCP2221 backend, which is a
process-wide singleton (selecting a second serial silently steals the first
bus's HID handle). EasyMCP2221.Device is per-adapter, so multiple MCP2221s can
be open at once. See docs/superpowers/specs/2026-07-14-mcp2221-easymcp2221-backend-design.md.
"""

import logging
import threading

from common.i2c_bus import _LockedI2C

logger = logging.getLogger("control")

# MCP2221(A) chip's fixed USB VID/PID.
MCP2221_VID = 0x04D8
MCP2221_PID = 0x00DD


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
                {"serial": info.get("serial_number"), "path": info.get("path")}
                for info in hid.enumerate(MCP2221_VID, MCP2221_PID)
                if info.get("serial_number")
            ),
            key=lambda d: d["serial"].lower(),
        )
    except Exception:
        logger.debug("discover_mcp2221_devices: hid.enumerate failed", exc_info=True)
        return []


class _EasyMCP2221Backend:
    """Adapt an EasyMCP2221.Device to the scan/writeto/readfrom_into/
    writeto_then_readfrom surface _LockedI2C expects. Translates EasyMCP2221's
    NotAckError/TimeoutError/LowSCLError/LowSDAError into OSError."""

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
            self._device.I2C_write(address, bytes(out_buffer[out_start:out_end]), kind="nonstop")
            data = self._device.I2C_read(address, in_end - in_start, kind="restart")
        except self._errors as exc:
            raise OSError(str(exc)) from exc
        in_buffer[in_start:in_end] = data


# EasyMCP2221.Device -> _LockedI2C. Keyed by the Device object itself (identity),
# since EasyMCP2221.Device.__new__ returns the SAME object for the SAME physical
# adapter regardless of selector spelling; this makes an aliasing selector reuse
# the existing bus/lock instead of double-wrapping under an independent lock.
_mcp2221_bus_by_device = {}
_lock = threading.RLock()


def reset_state():
    """Clear the per-Device dedup registry. Tests only."""
    with _lock:
        _mcp2221_bus_by_device.clear()


def _open_mcp2221_device(selector):
    from common.i2c_bus import I2CBusConfigError
    from EasyMCP2221 import Device as _MCP2221Device

    try:
        if selector:
            logger.debug("open_i2c_bus[mcp2221]: opening MCP2221 with serial=%r", selector)
            return _MCP2221Device(usbserial=str(selector), scan_serial=True)
        logger.debug("open_i2c_bus[mcp2221]: opening first MCP2221 (VID 0x%04X / PID 0x%04X)", MCP2221_VID, MCP2221_PID)
        return _MCP2221Device()
    except RuntimeError as exc:
        raise I2CBusConfigError(str(exc)) from exc


def construct_i2c_bus(selector):
    """Open (or reuse) the MCP2221 for `selector` and return a _LockedI2C bus.
    Called while common.i2c_bus holds its construction lock, so the dedup
    registry stays atomic with the open."""
    device = _open_mcp2221_device(selector)
    bus = _mcp2221_bus_by_device.get(device)
    if bus is None:
        bus = _LockedI2C(_EasyMCP2221Backend(device))
        _mcp2221_bus_by_device[device] = bus
    else:
        logger.debug(
            "open_i2c_bus[mcp2221]: selector=%r aliases an already-open MCP2221; reusing its shared bus/lock", selector
        )
    return bus
