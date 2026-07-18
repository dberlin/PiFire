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

from common.common import WriteKind, read_generic_json, write_generic_json, write_log
from common.datastore_accessors import read_settings, write_control


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


def get_display_os_info():
    """Get OS info for display purposes (admin page / mobile app system-info panel).

    Reads the cached os_info.json, falling back to a live get_os_info() read if the
    cache is missing/empty; backfills any missing fields with "Unknown"; computes
    BITS from ARCHITECTURE.

    This collapses two independently-duplicated wrapper copies that used to live in
    blueprints/admin/routes.py and blueprints/mobile/socket_io.py. Those two copies
    disagreed on the missing-field default string ("Unknown." with a trailing period
    in admin/routes.py vs "Unknown" without one in socket_io.py) and on error handling
    (admin logged read errors; socket_io silently swallowed them via a bare `except`).
    This merged version unifies on "Unknown" (no trailing period) plus logging the
    error -- the ONE deliberate, user-approved behavior change in this refactor. No
    test asserts the trailing period, and it's invisible during normal (non-degraded)
    operation.
    """
    try:
        os_info = read_generic_json("os_info.json")
        if not os_info:
            os_info = get_os_info()
    except Exception as e:
        write_log(f"Error reading OS info: {e}", loggername="events")
        os_info = None

    if not os_info:
        os_info = {}

    defaults = {
        "PRETTY_NAME": "Unknown",
        "NAME": "Unknown",
        "VERSION_ID": "Unknown",
        "VERSION": "Unknown",
        "VERSION_CODENAME": "Unknown",
        "ARCHITECTURE": "Unknown",
    }
    for key, default in defaults.items():
        os_info.setdefault(key, default)

    arch = os_info["ARCHITECTURE"]
    if arch in {"armv7l", "armv6l", "armv5l", "arm", "i386", "i486", "i586", "i686"}:
        os_info["BITS"] = "32-Bit"
    elif arch in {"aarch64", "x86_64"}:
        os_info["BITS"] = "64-Bit"
    else:
        os_info["BITS"] = "Unknown"

    return os_info


def gather_system_info(control, origin="unknown"):
    """Gather live system info (uptime, OS info, wifi/throttle/cpu-temp/network/hardware)
    and write the results into control["system"][...].

    This collapses the shared process_command()/get_system_command_output() gather
    sequence that used to be independently reimplemented inline in
    blueprints/admin/routes.py's admin_page() and in
    blueprints/mobile/socket_io.py's _get_system_info(). Each caller keeps its own
    extra shape on top: admin_page() turns `failures` into its human-readable
    `errors[]` list; socket_io's _get_system_info() ignores `failures` and builds its
    own `info_details` return dict.

    :param control: Control dictionary; control["system"][...] keys are populated
        in place with the gathered wifi/throttle/cpu-temp readings, mirroring both
        callers' original behavior.
    :param origin: Forwarded to write_control()'s `origin` label. Defaults to
        write_control's own default ("unknown"), matching admin_page's original
        unlabeled write_control() call; socket_io passes origin="app-socketio" to
        preserve its original labeled call.
    :return: (system_info, failures) -- system_info is a dict with keys
        uptime/os_info/network_info/hardware_info; failures is a list of
        human-readable messages for any 'sys' subcommand that did not report
        result == "OK" (a caller may ignore this list, as socket_io does).
    """
    # Deferred imports to avoid a module-load-time circular import:
    # common.api_commands imports names from common.system at module level
    # (and common.app pulls it in transitively), so a module-top import here
    # would form a system -> app -> api_commands -> system cycle.
    from common.api_commands import process_command
    from common.app import get_supported_cmds, get_system_command_output

    system_info = {}

    system_info["uptime"] = os.popen("uptime").readline()

    system_info["os_info"] = get_display_os_info()

    system_info["network_info"] = {"Unknown": {"ip_address": "0.0.0.0", "mac_address": "00:00:00:00:00:00"}}

    system_info["hardware_info"] = {
        "total_ram": "Unknown",
        "available_ram": "Unknown",
        "cpu_info": {
            "hardware": "Unknown",
            "model": "Unknown",
            "model_name": "Unknown",
            "cores": "Unknown",
            "frequency": "Unknown",
        },
    }

    failures = []
    supported_cmds = get_supported_cmds()

    if "check_wifi_quality" in supported_cmds:
        process_command(action="sys", arglist=["check_wifi_quality"], origin="admin")  # Request supported commands
        data = get_system_command_output(requested="check_wifi_quality")
        if data["result"] != "OK":
            failures.append(data["message"])
        control["system"]["wifi_quality_value"] = data["data"].get("wifi_quality_value", None)
        control["system"]["wifi_quality_max"] = data["data"].get("wifi_quality_max", None)
        control["system"]["wifi_quality_percentage"] = data["data"].get("wifi_quality_percentage", None)

    if "check_throttled" in supported_cmds:
        process_command(action="sys", arglist=["check_throttled"], origin="admin")  # Request supported commands
        data = get_system_command_output(requested="check_throttled")
        if data["result"] != "OK":
            failures.append(data["message"])
        control["system"]["cpu_throttled"] = data["data"].get("cpu_throttled", None)
        control["system"]["cpu_under_voltage"] = data["data"].get("cpu_under_voltage", None)

        if control["system"]["cpu_throttled"] or control["system"]["cpu_under_voltage"]:
            failures.append(
                "CPU Throttled / Undervoltage event has occurred.  Check your power supply for proper voltage."
            )

    if "check_cpu_temp" in supported_cmds:
        process_command(action="sys", arglist=["check_cpu_temp"], origin="admin")  # Request supported commands
        data = get_system_command_output(requested="check_cpu_temp")
        if data["result"] != "OK":
            failures.append(data["message"])
        control["system"]["cpu_temp"] = data["data"].get("cpu_temp", None)

    if "network_info" in supported_cmds:
        process_command(action="sys", arglist=["network_info"], origin="admin")
        data = get_system_command_output(requested="network_info")
        if data["result"] != "OK":
            failures.append(data["message"])
        else:
            network_info = data.get("data", None)
            if network_info:
                system_info["network_info"] = network_info

    if "hardware_info" in supported_cmds:
        process_command(action="sys", arglist=["hardware_info"], origin="admin")
        data = get_system_command_output(requested="hardware_info")
        if data["result"] != "OK":
            failures.append(data["message"])
        else:
            system_info["hardware_info"] = data.get("data", {})

    write_control(control, WriteKind.MERGE, origin=origin)

    return system_info, failures


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
