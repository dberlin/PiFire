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

import glob
import os

from serial.tools import list_ports

#: Where to look for a stable alias of a /dev/ttyACM<N>, best first.
#:
#: pyserial reports the kernel's own name -- /dev/ttyACM0 -- which is assigned
#: in USB enumeration order and therefore moves when devices are replugged,
#: when another adapter is added, or across a reboot. Configuring PiFire
#: against that name is what makes it point at the wrong device later, and the
#: failure is silent: the port opens, writes succeed, reads time out.
#:
#: /dev/pifire-* comes first because auto-install/udev/99-pifire.rules creates
#: those and they say what the device IS ("pifire-numato"); /dev/serial/by-id
#: is the distro-provided equivalent and exists without our rules installed.
_STABLE_LINK_GLOBS = ("/dev/pifire-*", "/dev/serial/by-id/*")


def _stable_device_path(device):
    """A stable symlink pointing at `device`, or None if there is not one.

    Never raises: discovery runs behind a wizard button, and a /dev that cannot
    be listed should cost the caller the alias, not the scan.
    """
    try:
        target = os.path.realpath(device)
    except OSError:
        return None
    for pattern in _STABLE_LINK_GLOBS:
        try:
            candidates = sorted(glob.glob(pattern))
        except OSError:
            continue
        for link in candidates:
            try:
                if os.path.islink(link) and os.path.realpath(link) == target:
                    return link
            except OSError:
                continue
    return None


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
                # A stable alias for the same hardware, when one exists.
                # Callers offering a device to be SAVED should prefer this --
                # see _stable_device_path for why the kernel name is a trap.
                "stable_device": _stable_device_path(port.device),
                "description": port.description or "",
                "manufacturer": getattr(port, "manufacturer", None) or "",
                "serial_number": port.serial_number or "",
                "vid": port.vid,
                "pid": port.pid,
            }
        )
    return results
