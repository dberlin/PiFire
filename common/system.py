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

from common.common import write_log
from common.control_delta import control_delta
from common.persistence.control import enqueue_control_delta
from common.persistence.install_state import load_os_info, store_os_info
from common.persistence.runtime import read_settings


def is_real_hardware(settings=None):
    """
    Check if running on real hardware as opposed to a prototype/test environment.

    :return: True if running on real hardware (i.e. Raspberry Pi), else False.
    """
    if settings == None:
        settings = read_settings()

    return bool(settings["platform"]["real_hw"])


def restart_control():
    """
    Restart the Control Script

    Gated on real hardware, like every other lifecycle call in this module:
    supervisor is how a real appliance runs these processes, and on a dev box
    the command either fails or restarts something the developer is using.
    """
    if is_real_hardware():
        os.system("sleep 3 && sudo supervisorctl restart control &")


def restart_webapp():
    """
    Restart the WebApp Script

    Gated on real hardware -- see restart_control.
    """
    if is_real_hardware():
        os.system("sleep 3 && sudo supervisorctl restart webapp &")


def restart_scripts():
    """Restart PiFire's supervisor programs: control, webapp and display.

    `supervisorctl restart all`, not a restart of the supervisor SERVICE. The
    unit is named `supervisor` on Debian / Raspberry Pi OS and `supervisord` on
    Fedora / RHEL, and this had no way to tell which, so it tried every name in
    turn. Each installer's sudoers grant names only its own unit, so the wrong
    guess was not merely a missing unit -- it was outside NOPASSWD, and a sudo
    that found a tty would sit at a password prompt until the timeout, which
    then abandoned the remaining names entirely. `supervisorctl` is one name on
    both platforms, and both installers already grant it.

    It also stops short of bouncing supervisord itself, which is all that was
    ever wanted: the three programs come back and the supervisor managing them
    stays up.

    No systemctl fallback. Reaching this means the webapp is answering requests,
    and the webapp is one of the programs supervisord manages -- so a supervisord
    that needs starting cannot be the one that just served this.
    """
    if not is_real_hardware():
        return

    def _restart():
        try:
            result = subprocess.run(
                ["sudo", "supervisorctl", "restart", "all"],
                capture_output=True,
                text=True,
                timeout=60,
                # sudo must never reach a password prompt: given a tty on stdin
                # it would block until the timeout and restart nothing.
                stdin=subprocess.DEVNULL,
                # Its own session, so that restarting `webapp` -- the program
                # this call is running inside -- cannot take the client with it
                # part-way through the sequence, leaving the rest stopped.
                start_new_session=True,
            )
            if result.returncode != 0:
                print(f"Failed to restart supervisor programs: {result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            print("supervisorctl restart timed out")
        except Exception as e:
            print(f"Error running supervisorctl: {e}")

    # Off the request thread: this kills the webapp that is answering, so the
    # response has to go out first.
    threading.Thread(target=_restart, daemon=True).start()


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


def probe_os_info(loggername="events"):
    """Probe operating-system information (/etc/os-release + `uname -m`).

    Pure: returns the values and touches nothing. Use this when you want to
    KNOW something about the OS -- board-config.py's rpi_config_write reads
    VERSION_ID to pick between /boot/config.txt and /boot/firmware/config.txt,
    and has no interest in the cache.

    Previously ``get_os_info(persist=False)``. That signature was the worst of
    the accessor-naming wave: a ``get_``-named function that WROTE, with the
    destructive flag defaulting to **True**, so every call site had to opt OUT
    of a side effect its name did not admit -- and rpi_config_write, the one
    caller that genuinely just wanted a value, took the default. See
    :func:`refresh_os_info` for the writing half.

    (Older still, it wrote an os_info.json resolved against the process CWD, so
    where the cache landed depended on who started PiFire and a plain read
    could create one in the wrong directory. The datastore is the single source
    of truth for live state; JSON files are exports, not the live copy.)
    """
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
        return os_info

    except Exception as e:
        event = f"Error getting OS info: {e!s}"
        write_log(event, loggername=loggername)
        return os_info


def refresh_os_info(loggername="events"):
    """Probe the OS info AND refresh the datastore's cached copy.

    :func:`probe_os_info` plus the write, named so the write is visible at the
    call site. Three callers, each of which wants the cache updated:
    board-config.py's ``--osversion`` provisioning flag, the ``os_info`` system
    command (grillplat/system_commands.py), and :func:`get_display_os_info`'s
    cache-miss backfill.

    A failed probe does NOT write, preserving ``get_os_info(persist=True)``'s
    behaviour (its ``store_os_info()`` sat inside the ``try``, after the last
    thing that could raise). probe_os_info() returns ``{}`` on failure and
    always sets ARCHITECTURE on success, so emptiness is the success signal;
    an unwritable cache is left as a miss rather than overwritten with nothing.

    :return: The probed OS info (also now cached, if the probe succeeded).
    """
    os_info = probe_os_info(loggername=loggername)
    if os_info:
        store_os_info(os_info)
    return os_info


def get_display_os_info():
    """Get OS info for display purposes (admin page / mobile app system-info panel).

    Reads the datastore's cached OS info, falling back to a live probe if the cache
    is missing/empty; backfills any missing fields with "Unknown"; computes BITS
    from ARCHITECTURE.

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
        os_info = load_os_info()
        if not os_info:
            # Cache miss: probe live and populate it. The probe is cheap and
            # local (/etc/os-release + uname), and the cache now lives in the
            # datastore, so this no longer depends on -- or writes to -- the
            # process CWD. refresh_, not probe_: backfilling the cache is the
            # point, so the next reader is a hit.
            os_info = refresh_os_info()
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
    :param origin: Forwarded to enqueue_control_delta() as the queued writer's
        source label. The default preserves the historical unlabeled admin
        writer; socket_io supplies ``"app-socketio"``.
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
    # The control["system"] members this call actually assigns, so the delta can
    # name exactly those. A member NOT probed on this platform stays absent from
    # the envelope, which is silence -- the old whole-dict write re-sent every
    # one of them from a stale read.
    assigned = {}
    supported_cmds = get_supported_cmds()

    if "check_wifi_quality" in supported_cmds:
        process_command(action="sys", arglist=["check_wifi_quality"], origin="admin")  # Request supported commands
        data = get_system_command_output(requested="check_wifi_quality")
        if data["result"] != "OK":
            failures.append(data["message"])
        assigned["wifi_quality_value"] = data["data"].get("wifi_quality_value", None)
        assigned["wifi_quality_max"] = data["data"].get("wifi_quality_max", None)
        assigned["wifi_quality_percentage"] = data["data"].get("wifi_quality_percentage", None)
        control["system"].update(assigned)

    if "check_throttled" in supported_cmds:
        process_command(action="sys", arglist=["check_throttled"], origin="admin")  # Request supported commands
        data = get_system_command_output(requested="check_throttled")
        if data["result"] != "OK":
            failures.append(data["message"])
        assigned["cpu_throttled"] = data["data"].get("cpu_throttled", None)
        assigned["cpu_under_voltage"] = data["data"].get("cpu_under_voltage", None)
        control["system"].update(assigned)

        if control["system"]["cpu_throttled"] or control["system"]["cpu_under_voltage"]:
            failures.append(
                "CPU Throttled / Undervoltage event has occurred.  Check your power supply for proper voltage."
            )

    if "check_cpu_temp" in supported_cmds:
        process_command(action="sys", arglist=["check_cpu_temp"], origin="admin")  # Request supported commands
        data = get_system_command_output(requested="check_cpu_temp")
        if data["result"] != "OK":
            failures.append(data["message"])
        assigned["cpu_temp"] = data["data"].get("cpu_temp", None)
        control["system"].update(assigned)

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

    # NOTE on nulls: a probe that answered with nothing assigns None, and under
    # a delta that is a VALUE -- it lands as null. The old path ran these through
    # strip_null_members (json_patch/RFC 7386 would have DELETED the key), so a
    # failed probe silently left the previous reading in place. Writing null is
    # the honest answer to "we asked and got nothing" and is what the admin page
    # renders as Unknown; a stale reading presented as current is worse.
    enqueue_control_delta(control_delta(set_values={"system": assigned}), origin=origin)

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
