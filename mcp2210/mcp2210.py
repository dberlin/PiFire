"""MCP2210 device class: USB-HID transport and command wrappers."""
import atexit

from . import _protocol as p


class MCP2210Error(RuntimeError):
    """Base error for MCP2210 command failures."""


class MCP2210BusUnavailableError(MCP2210Error):
    """SPI bus not available / data not accepted (status 0xF7)."""


class MCP2210InProgressError(MCP2210Error):
    """SPI transfer already in progress (status 0xF8)."""


_STATUS_EXC = {
    p.STATUS_BUS_UNAVAILABLE: MCP2210BusUnavailableError,
    p.STATUS_IN_PROGRESS: MCP2210InProgressError,
}


class MCP2210:
    VID = 0x04D8
    PID = 0x00DE

    def __init__(self, vid=VID, pid=PID, serial=None, hid_device=None):
        if hid_device is not None:
            self._hid = hid_device
        else:
            import hid
            self._hid = hid.device()
            if serial is not None:
                self._hid.open(vid, pid, serial)
            else:
                self._hid.open(vid, pid)
        self._spi = None
        self._pins = {}
        atexit.register(self.close)

    def _xfer(self, data, raise_on_status=True):
        report = bytes(data)
        report = report + b"\x00" * (64 - len(report))
        self._hid.write(b"\x00" + report)
        resp = bytes(self._hid.read(64))
        if resp[0] != report[0]:
            raise MCP2210Error(
                f"command echo mismatch: sent 0x{report[0]:02X}, got 0x{resp[0]:02X}"
            )
        if raise_on_status and resp[1] != p.STATUS_OK:
            exc = _STATUS_EXC.get(resp[1], MCP2210Error)
            raise exc(f"command 0x{report[0]:02X} failed (status 0x{resp[1]:02X})")
        return resp

    def close(self):
        if self._hid is not None:
            try:
                self._hid.close()
            finally:
                self._hid = None
