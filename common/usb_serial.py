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


def discover_usb_serial_devices(vid=None, pid=None):
    """Best-effort list of connected USB serial devices, for the wizard's
    Discover button. Returns [] if pyserial can't enumerate ports; never
    raises. Each result is a dict with 'device', 'description',
    'manufacturer', 'serial_number', 'vid', 'pid'."""
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
