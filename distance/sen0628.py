#!/usr/bin/env python3

# *****************************************
# PiFire SEN0628 Interface Library
# *****************************************
#
# Description: This library supports getting the hopper level from the
#   DFRobot SEN0628 (Gravity 8x8 Matrix ToF 3D Distance Sensor), connected
#   via its onboard USB-C port. Per DFRobot's wiki, the USB port is a live
#   data interface (not just firmware update) -- it enumerates as a fixed
#   115200-baud serial device speaking the same command protocol documented
#   for the sensor's UART pins.
#
#   Protocol reference: DFRobot's own Python driver
#   (github.com/DFRobot/DFRobot_MatrixLidar, python/raspberry/
#   DFRobot_matrixLidar.py). That reference script has two Python-2-only
#   bugs that would crash verbatim under Python 3 (ord() on an
#   already-int byte from iterating `bytes`, and writing a raw `list`
#   instead of `bytes`/`bytearray` to pyserial); this module reimplements
#   the protocol fixed for Python 3, and additionally corrects what looks
#   like a typo in the vendor's response-length parsing (`<< 2` where
#   every other 16-bit combine in the same file uses `<< 8`) -- this makes
#   no difference for the short response lengths this driver ever sees
#   (always < 64, so the high length byte is always 0 either way), but is
#   implemented correctly here regardless.
#
#   Only a single distance number is needed for hopper level, not the full
#   64-point depth matrix, so this driver reads the 2x2 block of points
#   nearest the matrix center -- (3,3), (3,4), (4,3), (4,4) in the 0-indexed
#   8x8 grid -- via four CMD_FIXED_POINT queries and averages them. The
#   single-point response format is unambiguous in the vendor protocol; the
#   full-matrix (CMD_ALLData) byte ordering is not documented anywhere
#   verifiable, so it is deliberately not used here.
#
#   NOTE: This library hasn't been tested against real hardware yet and is
#   provided for testing (see distance/hcsr04.py for the same disclaimer
#   style used elsewhere in this project).
#
# *****************************************

import time

from distance._serial_tof_base import SerialToFHopperLevel

CMD_SETMODE = 1
CMD_FIXED_POINT = 3
STATUS_SUCCESS = 0x53
STATUS_FAILED = 0x63

_SYNC_BYTE = b"\x55"
_CENTER_BLOCK = ((3, 3), (3, 4), (4, 3), (4, 4))
_RANGING_MATRIX_8X8 = 8


def _build_packet(cmd, args=()):
    """Build a `[len_hi, len_lo, cmd, *args]` command packet payload (the
    caller prefixes the 0x55 sync byte separately)."""
    length = len(args) + 1  # +1 for the command byte, per the vendor protocol's length field
    return bytes([(length >> 8) & 0xFF, length & 0xFF, cmd, *args])


def _recv_data(ser, length):
    if length <= 0:
        return []
    return list(ser.read(length))


def _recv_packet(ser, cmd, timeout=2.0):
    """Read and validate a response packet for `cmd`. Returns the response
    payload (list of ints), or None on timeout / status failure / a
    malformed or mismatched response."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = _recv_data(ser, 1)
        if not status:
            continue
        status = status[0]
        if status not in (STATUS_SUCCESS, STATUS_FAILED):
            continue
        command = _recv_data(ser, 1)
        if not command or command[0] != cmd:
            return None
        len_bytes = _recv_data(ser, 2)
        if len(len_bytes) < 2:
            return None
        length = (len_bytes[1] << 8) | len_bytes[0]
        if length > 128:
            return None
        data = _recv_data(ser, length) if length else []
        if status != STATUS_SUCCESS:
            return None
        return data
    return None


class HopperLevel(SerialToFHopperLevel):
    _setmode_retries = 3
    _setmode_recv_timeout = 2.0
    _read_recv_timeout = (
        0.5  # a live sensor answers in milliseconds; this timeout only matters when the sensor is silent
    )

    def _open_sensor(self, ser):
        self.ser = ser
        for _attempt in range(self._setmode_retries):
            ser.reset_input_buffer()
            ser.write(_SYNC_BYTE)
            ser.write(_build_packet(CMD_SETMODE, args=[0, 0, 0, _RANGING_MATRIX_8X8]))
            response = _recv_packet(ser, CMD_SETMODE, timeout=self._setmode_recv_timeout)
            if response is not None:
                time.sleep(5)  # matches the vendor driver's post-configure settle time
                return
        raise RuntimeError("SEN0628: sensor did not acknowledge ranging-mode configuration")

    def _get_fixed_point_mm(self, x, y):
        self.ser.reset_input_buffer()
        self.ser.write(_SYNC_BYTE)
        self.ser.write(_build_packet(CMD_FIXED_POINT, args=[x, y]))
        data = _recv_packet(self.ser, CMD_FIXED_POINT, timeout=self._read_recv_timeout)
        if not data or len(data) < 2:
            return 0
        return (data[1] << 8) | data[0]

    def _read_distance_mm(self):
        readings = [self._get_fixed_point_mm(x, y) for (x, y) in _CENTER_BLOCK]
        valid = [r for r in readings if r > 0]
        if not valid:
            return 0
        return sum(valid) / len(valid)
