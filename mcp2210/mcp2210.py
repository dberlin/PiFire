"""MCP2210 device class: USB-HID transport and command wrappers."""
import atexit
import time

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

    _SPI_RETRY_MAX = 200          # ~ retries before giving up on a busy engine
    _SPI_RETRY_SLEEP = 0.001      # seconds between busy retries

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

    @property
    def spi(self):
        if self._spi is None:
            from .spi import SPI
            self._spi = SPI(self)
        return self._spi

    def set_spi_settings(self, *, bitrate, mode, transfer_size,
                         idle_cs=0xFFFF, active_cs=0xFFFF):
        payload = p.pack_spi_settings(
            bitrate=int(bitrate), idle_cs=idle_cs, active_cs=active_cs,
            cs_to_data=0, data_to_cs=0, between_bytes=0,
            transfer_size=transfer_size, mode=mode,
        )
        self._xfer(bytes([p.CMD_SET_SPI_SETTINGS, 0, 0, 0]) + payload)

    def spi_exchange(self, data, *, bitrate, mode):
        data = bytes(data)
        total = len(data)
        self.set_spi_settings(bitrate=bitrate, mode=mode, transfer_size=total)
        rx = bytearray()
        idx = 0
        retries = 0
        while True:
            chunk = data[idx:idx + 60]
            req = bytes([p.CMD_SPI_TRANSFER, len(chunk), 0, 0]) + chunk
            resp = self._xfer(req, raise_on_status=False)
            status = resp[1]
            if status in (p.STATUS_IN_PROGRESS, p.STATUS_BUS_UNAVAILABLE):
                retries += 1
                if retries > self._SPI_RETRY_MAX:
                    raise MCP2210Error(
                        f"SPI transfer stalled (status 0x{status:02X})"
                    )
                time.sleep(self._SPI_RETRY_SLEEP)
                continue
            if status != p.STATUS_OK:
                raise MCP2210Error(f"SPI transfer failed (status 0x{status:02X})")
            retries = 0
            idx += len(chunk)
            rx_len = resp[2]
            if rx_len:
                rx += resp[4:4 + rx_len]
            if resp[3] == p.ENGINE_FINISHED:
                break
        return bytes(rx[:total])

    def close(self):
        if self._hid is not None:
            try:
                self._hid.close()
            finally:
                self._hid = None
