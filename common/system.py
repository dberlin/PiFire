"""
==============================================================================
 PiFire System Module
==============================================================================

Description: System-level operations -- detecting real hardware, restarting
  the control/webapp scripts, rebooting/shutting down the host, and probing
  OS / network (Wi-Fi link quality) information.

  Extracted from common/common.py; common/common.py re-imports these names
  for now so that existing `common.common.X` call sites keep resolving.

==============================================================================
"""

import os
import subprocess
import threading
import time

from common.common import write_generic_json, write_log
from common.datastore_accessors import read_settings


def is_real_hardware(settings=None):
    """
    Check if running on real hardware as opposed to a prototype/test environment.

    :return: True if running on real hardware (i.e. Raspberry Pi), else False.
    """
    if settings == None:
        settings = read_settings()

    return True if settings["platform"]["real_hw"] else False


def restart_control():
    """
    Restart the Control Script
    """
    os.system("sleep 3 && sudo supervisorctl restart control &")


def restart_webapp():
    """
    Restart the WebApp Script
    """
    os.system("sleep 3 && sudo supervisorctl restart webapp &")


def restart_scripts():
    """
    Restart the Control and WebApp Scripts by restarting the supervisor service.

    The supervisor systemd unit is named 'supervisor' on Debian / Raspberry Pi OS
    but 'supervisord' on Fedora / RHEL, so try each name in turn (systemctl first,
    then the legacy 'service' command) until one succeeds.
    """
    if is_real_hardware():

        def _restart_supervisor():
            service_names = ["supervisor", "supervisord"]
            # Prefer systemctl (modern systemd systems)
            for name in service_names:
                try:
                    result = subprocess.run(
                        ["sudo", "systemctl", "restart", name], capture_output=True, text=True, timeout=10
                    )
                    if result.returncode == 0:
                        return
                except subprocess.TimeoutExpired:
                    print("Supervisor restart command timed out")
                    return
                except Exception as e:
                    print(f"Error restarting {name} via systemctl: {e}")
            # Fall back to the legacy 'service' command for either name
            for name in service_names:
                try:
                    result = subprocess.run(
                        ["sudo", "service", name, "restart"], capture_output=True, text=True, timeout=10
                    )
                    if result.returncode == 0:
                        return
                except Exception as e:
                    print(f"Error restarting {name} via service: {e}")
            print("Failed to restart supervisor under any known service name")

        # Run in background thread to avoid blocking
        threading.Thread(target=_restart_supervisor, daemon=True).start()


def reboot_system():
    """
    Reboot the system
    """
    if is_real_hardware():

        def _reboot():
            try:
                time.sleep(3)  # Give time for response to be sent
                # Try systemctl first (preferred method for systemd)
                result = subprocess.run(["sudo", "systemctl", "reboot"], capture_output=True, text=True, timeout=10)
                if result.returncode != 0:
                    print(f"systemctl reboot failed: {result.stderr}")
                    # Fallback to traditional reboot command
                    subprocess.run(["sudo", "reboot"], timeout=10)
            except subprocess.TimeoutExpired:
                print("Reboot command timed out")
            except Exception as e:
                print(f"Error rebooting system: {e}")
                # Final fallback to original method
                os.system("sudo reboot")

        # Run in background thread
        threading.Thread(target=_reboot, daemon=True).start()


def shutdown_system():
    """
    Shutdown the system
    """
    if is_real_hardware():

        def _shutdown():
            try:
                time.sleep(3)  # Give time for response to be sent
                # Try systemctl first (preferred method for systemd)
                result = subprocess.run(["sudo", "systemctl", "poweroff"], capture_output=True, text=True, timeout=10)
                if result.returncode != 0:
                    print(f"systemctl poweroff failed: {result.stderr}")
                    # Fallback to traditional shutdown command
                    subprocess.run(["sudo", "shutdown", "-h", "now"], timeout=10)
            except subprocess.TimeoutExpired:
                print("Shutdown command timed out")
            except Exception as e:
                print(f"Error shutting down system: {e}")
                # Final fallback to original method
                os.system("sudo shutdown -h now")

        # Run in background thread
        threading.Thread(target=_shutdown, daemon=True).start()


def get_os_info(filepath="os_info.json", loggername="events"):
    """Get operating system information"""
    os_info = {}

    try:
        # Get OS release info
        with open("/etc/os-release", "r") as f:
            for line in f:
                if "=" in line:
                    key, value = line.strip().split("=", 1)
                    # Remove quotes if present
                    value = value.strip('"')
                    os_info[key] = value

        # Get architecture using uname -m
        arch = subprocess.check_output(["/bin/uname", "-m"]).decode().strip()
        os_info["ARCHITECTURE"] = arch

        # Save to JSON file
        write_generic_json(os_info, filepath)
        return os_info

    except Exception as e:
        event = f"Error getting OS info: {str(e)}"
        write_log(event, level="error", loggername=loggername)
        return os_info


def _detect_wireless_interface():
    """Return the name of the first wireless network interface, or 'wlan0' as a fallback.

    Wireless interfaces expose a 'wireless' subdirectory under /sys/class/net/<iface>.
    """
    try:
        for iface in sorted(os.listdir("/sys/class/net")):
            if os.path.isdir(f"/sys/class/net/{iface}/wireless"):
                return iface
    except OSError:
        pass
    return "wlan0"


def _wifi_quality_from_iwconfig(interface):
    """Parse the 'Link Quality=x/y' field from iwconfig.

    Returns a (value, max) tuple, or None if the field is not present. Raises
    FileNotFoundError if iwconfig is not installed.
    """
    output = subprocess.check_output(["iwconfig", interface], stderr=subprocess.DEVNULL)
    for line in output.decode("utf-8", errors="replace").splitlines():
        if "Link Quality=" in line:
            quality = line.split("Link Quality=")[1].split(" ")[0]
            value, maximum = quality.split("/")
            return int(value), int(maximum)
    return None


def _wifi_quality_from_iw(interface):
    """Parse the 'signal: N dBm' field from 'iw dev <interface> link'.

    Converts the signal strength to a 0-100 quality using the NetworkManager
    formula (clamp(2 * (dBm + 100), 0, 100)) and returns a (percentage, 100)
    tuple, or None if no signal line is present. Raises FileNotFoundError if iw
    is not installed.
    """
    output = subprocess.check_output(["iw", "dev", interface, "link"], stderr=subprocess.DEVNULL)
    for line in output.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("signal:"):
            dbm = int(line.split("signal:")[1].strip().split(" ")[0])
            percentage = max(0, min(100, 2 * (dbm + 100)))
            return percentage, 100
    return None


def get_wifi_quality(interface=None, logger=None):
    """Return Wi-Fi link quality using iwconfig when available, falling back to iw.

    The interface is auto-detected when not supplied. iwconfig is tried first; if
    it is not installed (FileNotFoundError) or fails to yield a reading, the newer
    iw tool is tried. Returns the standard system-command dict with
    wifi_quality_value / wifi_quality_max / wifi_quality_percentage in 'data'.
    """
    data = {"result": "ERROR", "message": "Unable to obtain wifi quality data.", "data": {}}

    if interface is None:
        interface = _detect_wireless_interface()

    reading = None
    for name, parser in (("iwconfig", _wifi_quality_from_iwconfig), ("iw", _wifi_quality_from_iw)):
        try:
            reading = parser(interface)
        except FileNotFoundError:
            if logger:
                logger.debug(f"{name} not found; trying next method for wifi quality.")
            continue
        except (subprocess.CalledProcessError, ValueError, IndexError) as e:
            if logger:
                logger.debug(f"{name} failed to obtain wifi quality: {e}")
            continue
        if reading is not None:
            break

    if reading is not None:
        value, maximum = reading
        percentage = round((value / maximum) * 100, 2)
        data["result"] = "OK"
        data["message"] = "Successfully obtained wifi quality data."
        data["data"] = {"wifi_quality_value": value, "wifi_quality_max": maximum, "wifi_quality_percentage": percentage}

    if logger:
        logger.debug(f"get_wifi_quality called. [data = {data}]")
    return data
