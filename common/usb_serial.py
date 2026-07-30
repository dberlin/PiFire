#!/usr/bin/env python3

# *****************************************
# PiFire USB Serial Device Discovery
# *****************************************
#
# Description: Best-effort discovery of connected USB serial devices, for
#   the wizard's "Discover" button on serial-device-path settings fields
#   (e.g. distance/sen0628.py's device path). Optionally filtered by USB
#   vendor/product ID; when neither is given, every enumerable serial
#   device is returned, so a not-yet-configured vid/pid still yields a
#   usable (if unfiltered) device list rather than nothing.
#
# *****************************************

from serial.tools import list_ports


def _as_usb_id(value):
    """Coerce a USB vid/pid into the plain int pyserial reports on a port.

    The wizard manifest writes these the way USB IDs are always written --
    "0x2a19" -- while `port.vid` is an int, and `1 != "0x2a19"` silently
    matches nothing. So accept either: an int is used as-is, a string is read
    as hex with or without the 0x prefix (never as decimal; a bare "2a19" is
    still a hex USB ID), and None or "" means "do not filter on this".

    An unreadable value raises ValueError rather than degrading to "no
    filter". Falling back would list every serial device on the machine as
    though each one were the board being looked for, which is worse than an
    error the user can see and act on.
    """
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value), 16)
    except ValueError:
        raise ValueError(f"Not a USB vid/pid: {value!r} (expected an int or a hex string like '0x2a19')") from None


def discover_usb_serial_devices(vid=None, pid=None):
    """Best-effort list of connected USB serial devices, for the wizard's
    Discover button. Returns [] if pyserial can't enumerate ports. Each result
    is a dict with 'device', 'description', 'manufacturer', 'serial_number',
    'vid', 'pid'.

    `vid`/`pid` accept an int or a hex string (see _as_usb_id); a malformed
    one raises ValueError. Enumeration itself still never raises.
    """
    vid = _as_usb_id(vid)
    pid = _as_usb_id(pid)
    try:
        ports = list_ports.comports()
    except Exception:
        return []

    results = []
    for port in ports:
        if vid is not None and port.vid != vid:
            continue
        if pid is not None and port.pid != pid:
            continue
        results.append(
            {
                "device": port.device,
                "description": port.description or "",
                "manufacturer": getattr(port, "manufacturer", None) or "",
                "serial_number": port.serial_number or "",
                "vid": port.vid,
                "pid": port.pid,
            }
        )
    return results
