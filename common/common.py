"""
==============================================================================
 PiFire Common Module
==============================================================================

Description: This library provides functions that are common to
  both app.py and control.py

==============================================================================
"""

"""
==============================================================================
 Imported Modules
==============================================================================
"""
import time
import datetime
import os
import io
import json
import re
import math
import uuid
import random
import logging
import subprocess
import threading
from enum import Enum
from logging.handlers import RotatingFileHandler
from collections.abc import Mapping
from ratelimitingfilter import RateLimitingFilter
from common import datastore
from common.sqlite_queue import SqliteQueue, SqliteMembershipList
from common.sqlite_log_handler import SqliteLogHandler

# *****************************************
# Enums
# *****************************************


class WriteKind(Enum):
    OVERWRITE = "overwrite"  # replace control:general wholesale (legacy True)
    MERGE = "merge"  # queue a partial change, deep-merged on execute (legacy False)


# *****************************************
# Constants and Globals
# *****************************************
"""
==============================================================================
 Constants and Globals
==============================================================================
"""
BACKUP_PATH = "./backups/"  # Path to backups of settings.json, pelletdb.json


"""
==============================================================================
 Functions
==============================================================================
"""


def create_logger(
    name,
    filename="./logs/pifire.log",
    messageformat="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    maxBytes=1 * 1024 * 1024,  # 1 MB
    backupCount=3,
):
    """Create or Get Existing Logger"""
    logger = logging.getLogger(name)
    """ 
		If the logger does not exist, create one. Else return the logger. 
		Note: If the a log-level change is needed, the developer should directly set the log level on the logger, instead of using 
		this function.  
	"""
    if not logger.hasHandlers():
        logger.setLevel(level)
        formatter = logging.Formatter(fmt=messageformat, datefmt="%Y-%m-%d %H:%M:%S %z")
        # datefmt='%Y-%m-%d %H:%M:%S'
        # Add a rate limit filter for the voltage error logging
        config = {"match": ["An error occurred reading the voltage from one of the ports."]}
        ratelimit = RateLimitingFilter(rate=1, per=60, burst=5, **config)  # Allow 1 per 60s (with periodic burst of 5)

        # RotatingFileHandler
        rotating_handler = RotatingFileHandler(filename, maxBytes=maxBytes, backupCount=backupCount)
        rotating_handler.setFormatter(formatter)
        rotating_handler.addFilter(ratelimit)
        logger.addHandler(rotating_handler)

        # SqliteLogHandler
        sqlite_handler = SqliteLogHandler(name)
        sqlite_handler.setFormatter(formatter)
        sqlite_handler.addFilter(ratelimit)
        logger.addHandler(sqlite_handler)
    return logger


def display_sleep_timeout(settings):
    """Idle seconds before the display sleeps; 0 = never. Defaults to 300 on
    missing/invalid values. Negative values clamp to 0."""
    try:
        value = int(settings["display"]["sleep_timeout"])
    except KeyError, TypeError, ValueError:
        return 300
    return value if value > 0 else 0


def get_display_info(settings):
    """Return human-readable info about the currently selected display.

    Used by the admin GPIO info page, where a DSI/HDMI (or other non-SPI)
    display has no dc/led/rst GPIO pins worth showing -- its resolution and
    type are the meaningful facts instead.

    :param settings: The settings dictionary.
    :return: dict with 'module', 'type' (friendly name) and 'resolution'
             ('WxH' string, or None when unknown).
    """
    display_module = settings.get("modules", {}).get("display", "none")
    info = {"module": display_module, "type": display_module, "resolution": None}

    # Prefer the wizard manifest's friendly name for the display type.
    manifest = read_generic_json("./wizard/wizard_manifest.json")
    module_meta = manifest.get("modules", {}).get("display", {}).get(display_module, {})
    if module_meta.get("friendly_name"):
        info["type"] = module_meta["friendly_name"]

    # Resolution comes from the display's data JSON metadata when it has one
    # (DSI/HDMI and pygame-style displays), otherwise fall back to a WxH token
    # embedded in the module name (e.g. 'st7789_240x320' -> '240x320').
    display_config = settings.get("display", {}).get("config", {}).get(display_module, {})
    data_filename = display_config.get("display_data_filename")
    if data_filename:
        display_data = read_generic_json(data_filename)
        metadata = display_data.get("metadata", {}) if isinstance(display_data, dict) else {}
        width = metadata.get("screen_width")
        height = metadata.get("screen_height")
        if width and height:
            info["resolution"] = f"{width}x{height}"
    if info["resolution"] is None:
        match = re.search(r"(\d+x\d+)", display_module)
        if match:
            info["resolution"] = match.group(1)

    return info


def get_probe_list(settings):
    probe_list = []
    for probe in settings["probe_settings"]["probe_map"]["probe_info"]:
        if probe["type"] != "Aux":
            probe_list.append((probe["label"], probe["name"]))

    return probe_list


def get_notify_targets(notify_data):
    notify_targets = {}
    for item in notify_data:
        if item["type"] == "probe":
            notify_targets[item["label"]] = item["target"]
    return notify_targets


def generate_uuid():
    """
    Generate a uuid based on mac address and random int

    :return: A string uuid
    """
    node = uuid.getnode()
    rand_int = random.randint(100, 200)
    generated_uuid = uuid.uuid1(node + rand_int)

    return str(generated_uuid)


def strip_null_members(obj, _stripped=None, _prefix=""):
    """Recursively drop dict keys whose value is None so a json_patch() merge
    ignores them instead of deleting the target key.

    json_patch() implements RFC 7386 JSON Merge Patch, where a null MEMBER of the
    patch object deletes that key from the target. PiFire's merge contract (which
    historically used deep_update) only ever adds or overwrites keys -- it never
    deletes -- so nulls are stripped before patching.

    Lists are returned unchanged: json_patch replaces arrays atomically and never
    walks their elements, so nulls nested inside arrays (e.g. notify_data[*].eta)
    are preserved exactly, matching the old deep_update behavior of overwriting a
    list wholesale.

    If `_stripped` (a list) is passed in, the dotted path of every dropped key is
    appended to it, so callers can report which partials still carry nulls. After
    the base.py None->False cleanup no PiFire-internal path should trip this, so a
    non-empty result flags a source still to be fixed (see execute_control_writes).
    """
    if isinstance(obj, Mapping):
        result = {}
        for key, value in obj.items():
            if value is None:
                if _stripped is not None:
                    _stripped.append(f"{_prefix}{key}")
                continue
            result[key] = strip_null_members(value, _stripped, f"{_prefix}{key}.")
        return result
    return obj


def _load_json_file(filename, default, retry_count=0, max_retries=None):
    """
    Load and parse a JSON file, encapsulating the open/read/parse-with-retry
    shape shared by several read_*_file functions in this module: open the
    file, parse it as JSON, return `default` if the file can't be
    opened/read, and retry (recursively) if the contents fail to parse as
    JSON -- which happens when a reader collides with a concurrent writer
    that hasn't finished yet.

    :param filename: path of the JSON file to read
    :param default: value returned if the file is missing/unreadable, or if
            JSON parsing still fails once the retry budget is exhausted
    :param retry_count: internal recursion counter; callers should leave this
            at its default of 0
    :param max_retries: maximum number of recursive retries to attempt when
            the file fails to parse as JSON. None (default) retries without
            bound, matching the historical read_wizard/read_updater_manifest
            behavior. Pass 0 to disable retries entirely, matching
            read_generic_json's historical behavior.
    :return: parsed JSON data, or `default`
    """
    try:
        json_data_file = os.fdopen(os.open(filename, os.O_RDONLY))
        json_data_string = json_data_file.read()
        data = json.loads(json_data_string)
        json_data_file.close()
        return data
    except IOError, OSError:
        write_log(f"ERROR: Could not read from {filename}.")
        return default
    except ValueError:
        # A ValueError Exception occurs when multiple accesses collide, this code attempts a retry.
        write_log(f"ERROR: Value Error Exception - JSONDecodeError reading {filename}")
        json_data_file.close()
        if max_retries is None or retry_count < max_retries:
            return _load_json_file(filename, default, retry_count=retry_count + 1, max_retries=max_retries)
        return default


def backup_settings():
    # Write the CURRENT settings (SQLite is the source of truth at runtime, the
    # settings.json file is not kept in sync) to a backup copy in
    # /[BACKUP_PATH]/PiFire_[DATE]_[TIME].json
    time_now = datetime.datetime.now()
    time_str = time_now.strftime("%m-%d-%y_%H%M%S")  # Truncate the microseconds
    backup_file = BACKUP_PATH + "PiFire_" + time_str + ".json"
    settings = read_settings()
    write_generic_json(settings, backup_file)
    # Save a path to the backup copy in the updater_manifest.json
    backup_manifest = read_generic_json("./backups/manifest.json")
    if backup_manifest == {}:
        backup_manifest = {"server_settings": {}}
        write_generic_json(backup_manifest, "./backups/manifest.json")

    server_version = settings["versions"]["server"]
    backup_manifest["server_settings"][server_version] = backup_file
    write_generic_json(backup_manifest, "backups/manifest.json")
    warning = f'Backed up your current settings to "{backup_file}" and setting these as the recovery settings for server version: {server_version}.'
    write_warning(warning)
    write_log(warning)
    return backup_file


def restore_settings(settings_default):
    """Look for backup file to restore from"""
    backup_manifest = read_generic_json("./backups/manifest.json")
    if backup_manifest == {}:
        backup_manifest = {"server_settings": {}, "pelletdb": {"current": ""}}
        write_generic_json(backup_manifest, "./backups/manifest.json")
    server_version = settings_default["versions"]["server"]
    backup_settings_file = backup_manifest["server_settings"].get(server_version, None)
    if backup_settings_file is not None:
        warning = f'Something failed when reading the "settings.json" file.  Restoring settings from the following backup settings file: {backup_settings_file}.'
        # Read the backup FILE (not SQLite -- that's the current, possibly
        # corrupt/absent, state we're recovering from).
        settings = read_settings_file(filename=backup_settings_file)
    else:
        warning = f'Something failed when reading the "settings.json" file.  Resetting settings to defaults, since no backup settings files were found.'
        settings = settings_default
    # Make the recovered settings the new current state in SQLite.
    write_settings_store(settings)
    write_warning(warning)
    write_log(warning)
    return settings


def read_pellet_db_file(filename="pelletdb.json", retry_count=0):
    """
    Read Pellet DataBase from file

    :param filename: Filename to use (default pelletdb.json)
    :param retry_count: Recursion guard for the corrupt-file self-repair path
            below (mirrors read_settings_file's retry_count<5 pattern). The
            self-repair calls backup_pellet_db(action='restore'), which calls
            back into this function against the backup file -- if that backup is
            ALSO corrupt, this bounds the resulting recursion instead of letting
            it run away (RecursionError) when every backup on record is corrupt.
    """

    pelletdb = default_pellets()

    # Read all lines of pelletdb.json into a list(array)
    try:
        json_data_file = os.fdopen(os.open(filename, os.O_RDONLY))
        json_data_string = json_data_file.read()
        pelletdb_struct = json.loads(json_data_string)
        json_data_file.close()
    except IOError, OSError:
        # File not found, return default pellet database
        return pelletdb
    except:
        """ Restore PelletDB from backup if available """
        if retry_count < 5:
            pelletdb_struct = backup_pellet_db(action="restore", retry_count=retry_count + 1)
        else:
            # Backup is also corrupt/unreadable after repeated attempts --
            # stop recursing and fall back to defaults.
            return default_pellets()

    # Overlay the read values over the top of the default values
    #  This ensures that any NEW fields are captured.
    update_db = False  # set flag in case an update needs to be written back

    for key in pelletdb.keys():
        if key in pelletdb_struct.keys():
            pelletdb[key] = pelletdb_struct[key].copy()
        else:
            update_db = True

    return pelletdb


def backup_pellet_db(action="backup", retry_count=0):
    """Backup & Restore Pellet Database

    :param retry_count: Forwarded to read_pellet_db_file() on the 'restore'
            path, so repeated corrupt-backup self-repair recursion is bounded
            (see read_pellet_db_file).
    """
    backup_manifest = read_generic_json("./backups/manifest.json")
    if backup_manifest == {}:
        backup_manifest = {"server_settings": {}, "pelletdb": {"current": ""}}
        write_generic_json(backup_manifest, "./backups/manifest.json")

    if backup_manifest.get("pelletdb", None) == None:
        """ If the structure doesn't exist, create it. """
        backup_manifest["pelletdb"] = {"current": None}

    if action == "backup":
        time_now = datetime.datetime.now()
        time_str = time_now.strftime("%m-%d-%y_%H%M%S")  # Truncate the microseconds
        backup_file = BACKUP_PATH + "PelletDB_" + time_str + ".json"
        # Write the CURRENT pellet DB (SQLite is the source of truth at
        # runtime, pelletdb.json is not kept in sync) directly to the backup file.
        pelletdb = read_pellet_db()
        write_generic_json(pelletdb, backup_file)
        backup_manifest["pelletdb"]["current"] = backup_file
        message = f"Pellet DB has been backed up to the following file: {backup_file}"
        write_generic_json(backup_manifest, "./backups/manifest.json")
        write_log(message)
        return backup_file
    elif action == "restore":
        backup_pelletdb = backup_manifest["pelletdb"].get("current", None)
        if backup_pelletdb is not None:
            pelletdb_backup_file = backup_pelletdb
            warning = f"There was an issue with loading the Pellet Database (possibly corruption).  Restoring from the following backup file: {backup_pelletdb}."
            # Read the backup FILE (not SQLite -- that's the current,
            # possibly corrupt, state we're recovering from).
            pelletdb = read_pellet_db_file(filename=pelletdb_backup_file, retry_count=retry_count)
            write_pellet_db(pelletdb)
        else:
            warning = f"There was an issue with loading the Pellet Database (possibly corruption).  No backups found, setting to defaults."
            pelletdb = default_pellets()
            write_pellet_db(pelletdb)
        write_warning(warning)
        write_log(warning)
        return pelletdb
    else:
        pass

    return


def read_events(legacy=True):
    """
    Read event.log and populate an array of events.

    if legacy=true:
    :return: (event_list, num_events)

    if legacy=false:
    :return: (event_list, num_events)
    """
    # Read all lines of events.log into a list(array)
    try:
        with open("./logs/events.log") as event_file:
            event_lines = event_file.readlines()
            event_file.close()
    # If file not found error, then create events.log file
    except IOError, OSError:
        event_file = open("./logs/events.log", "w")
        event_file.close()
        event_lines = []

    # Initialize event_list list
    event_list = []

    # Get number of events
    num_events = len(event_lines)

    if legacy:
        for x in range(num_events):
            event_list.insert(0, event_lines[x].split(" ", 2))

        # Error handling if number of events is less than 10, fill array with empty
        if num_events < 10:
            for line in range((10 - num_events)):
                event_list.append(["--------", "--:--:--", "---"])
            num_events = 10
    else:
        for x in range(num_events):
            event_list.append(event_lines[x].split(" ", 2))
        return event_list

    return (event_list, num_events)


def read_log_file(filepath):
    # Read all lines of log file into a list(array)
    try:
        with open(filepath) as log_file:
            log_file_lines = log_file.readlines()
            log_file.close()
    # If file not found error, then log it
    except IOError, OSError:
        event = f"Unable to open log file: {filepath}"
        write_log(event)
        return []

    return log_file_lines


def add_line_numbers(event_list):
    event_lines = []
    for index, line in enumerate(event_list):
        event_lines.append([index, line])
    return event_lines


def write_log(event, loggername="events"):
    """
    Write event to event.log

    :param event: String event
    """
    log_level = logging.INFO
    eventLogger = create_logger(
        loggername,
        filename="./logs/events.log",
        messageformat="%(asctime)s [%(levelname)s] %(message)s",
        level=log_level,
    )
    eventLogger.info(event)


def write_event(settings, event):
    """
    Send event to log and console if debug mode enabled or only to log if
    string does not begin with *

    :param settings: Settings
    :param event: String event
    """
    if settings["globals"]["debug_mode"]:
        print(event)
        write_log(event)
    elif not event.startswith("*"):
        write_log(event)


def read_events_records(flush=False):
    """
    Read Events from events.log and return a list of event dictionaries.

    :param flush: True to clean events. False otherwise
    :return: events_list - list of {'date':, 'time':, 'message':} dicts
    """
    if flush:
        datastore.clear_log("events")
        return []

    events, num_events = read_events()
    events_list = []
    for item in range(min(num_events, 60)):
        events_list.append({"date": events[item][0], "time": events[item][1], "message": events[item][2].strip("\n")})
    return events_list


def unpack_history(datalist):
    temp_dict = {}  # Create temporary dictionary to store all of the history data lists
    temp_struct = datalist[0]  # Load the initial history data into a temporary dictionary
    for key in temp_struct.keys():  # Iterate each of the keys
        if key in ["P", "F", "NT", "EXD", "AUX"]:
            temp_dict[key] = {}
            for subkey in temp_struct[key]:
                temp_dict[key][subkey] = []
        else:
            temp_dict[key] = []  # Create an empty list for any other keys ('T', 'PSP')

    for index in range(len(datalist)):
        temp_struct = datalist[index]
        for key, value in temp_struct.items():
            if key in ["P", "F", "NT", "EXD", "AUX"]:
                for subkey, subvalue in temp_struct[key].items():
                    temp_dict[key][subkey].append(subvalue)
            else:
                temp_dict[key].append(value)  # Append list for any other keys ('T', 'PSP')
    return temp_dict


def convert_temp(units, temp):
    """
    Convert Temp Based on Units

    :param units: Units C or F
    :param temp: Temp to Convert
    :return: Converted Temp
    """
    if units == "F":
        temp_out = int(temp * (9 / 5) + 32)  # Celsius to Fahrenheit
    else:
        temp_out = int((temp - 32) * (5 / 9))  # Fahrenheit to Celsius
    return temp_out


def convert_settings_units(units, settings):
    """
    Convert Settings Units

    :param units: Units C or F
    :param settings: Settings
    :return: Updated Settings
    """
    if units in ["C", "F"] and units != settings["globals"]["units"]:
        settings["globals"]["units"] = units
        settings["startup"]["startup_exit_temp"] = convert_temp(units, settings["startup"]["startup_exit_temp"])
        settings["startup"]["start_to_mode"]["primary_setpoint"] = convert_temp(
            units, settings["startup"]["start_to_mode"]["primary_setpoint"]
        )
        settings["safety"]["maxstartuptemp"] = convert_temp(units, settings["safety"]["maxstartuptemp"])
        settings["safety"]["maxtemp"] = convert_temp(units, settings["safety"]["maxtemp"])
        settings["safety"]["minstartuptemp"] = convert_temp(units, settings["safety"]["minstartuptemp"])
        settings["smoke_plus"]["max_temp"] = convert_temp(units, settings["smoke_plus"]["max_temp"])
        settings["smoke_plus"]["min_temp"] = convert_temp(units, settings["smoke_plus"]["min_temp"])
        settings["keep_warm"]["temp"] = convert_temp(units, settings["keep_warm"]["temp"])
        for temp in range(0, len(settings["startup"]["smartstart"]["temp_range_list"])):
            settings["startup"]["smartstart"]["temp_range_list"][temp] = convert_temp(
                units, settings["startup"]["smartstart"]["temp_range_list"][temp]
            )
        settings["startup"]["smartstart"]["exit_temp"] = convert_temp(
            units, settings["startup"]["smartstart"]["exit_temp"]
        )
    return settings


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


def read_wizard(filename="wizard/wizard_manifest.json"):
    """
    Read Wizard Manifest Data from file

    :param filename: Filename to use (default wizard/wizard_manifest.json)
    :return: Wizard Data
    """
    return _load_json_file(filename, {"modules": {}})


def read_updater_manifest(filename="updater/updater_manifest.json"):
    """
    Read Updater Manifest Data from file

    :param filename: updater_manifest.json filename
    :return: Dependencies
    """
    return _load_json_file(filename, {"dependencies": {}})


def process_metrics(metrics_data, augerrate=0.3):
    # Process Additional Metrics Information for Display
    for index in range(0, len(metrics_data)):
        # Convert Start Time
        starttime = metrics_data[index]["starttime"]
        metrics_data[index]["starttime_c"] = epoch_to_time(starttime / 1000)
        # Convert End Time
        if metrics_data[index]["endtime"] == 0:
            endtime = 0
        else:
            endtime = epoch_to_time(metrics_data[index]["endtime"] / 1000)
        metrics_data[index]["endtime_c"] = endtime
        # Time in Mode
        if metrics_data[index]["mode"] == "Stop":
            timeinmode = "NA"
        elif metrics_data[index]["endtime"] == 0:
            timeinmode = "Active"
        else:
            seconds = int((metrics_data[index]["endtime"] / 1000) - (metrics_data[index]["starttime"] / 1000))
            if seconds > 60:
                timeinmode = f"{int(seconds / 60)} m {seconds % 60} s"
            else:
                timeinmode = f"{seconds} s"
        metrics_data[index]["timeinmode"] = timeinmode
        # Convert Auger On Time
        metrics_data[index]["augerontime_c"] = str(int(metrics_data[index]["augerontime"])) + " s"
        # Estimated Pellet Usage
        grams = int(metrics_data[index]["augerontime"] * augerrate)
        pounds = round(grams * 0.00220462, 2)
        ounces = round(grams * 0.03527392, 2)
        metrics_data[index]["estusage_m"] = f"{grams} grams"
        metrics_data[index]["estusage_i"] = f"{pounds} pounds ({ounces} ounces)"

    return metrics_data


def epoch_to_time(epoch):
    end_time = datetime.datetime.fromtimestamp(epoch)
    return end_time.strftime("%H:%M:%S")


def semantic_ver_to_list(version_string):
    # Count number of '.' in string
    decimal_count = version_string.count(".")
    ver_list = version_string.split(".")

    if decimal_count == 0:
        ver_list = [0, 0, 0]
    elif decimal_count < 2:
        ver_list.append("0")

    ver_list = list(map(int, ver_list))

    return ver_list


def semantic_ver_is_lower(version_A, version_B):
    version_A = semantic_ver_to_list(version_A)
    version_B = semantic_ver_to_list(version_B)

    if version_A[0] < version_B[0]:
        return True
    elif version_A[0] > version_B[0]:
        return False
    else:
        if version_A[1] < version_B[1]:
            return True
        elif version_A[1] > version_B[1]:
            return False
        else:
            if version_A[2] < version_B[2]:
                return True
            elif version_A[2] > version_B[2]:
                return False
    return False


def seconds_to_string(seconds):
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)

    if h > 0:
        time_string = f"{h}h {m}m {s}s"
    elif m > 0:
        time_string = f"{m}m {s}s"
    else:
        time_string = f"{s}s"

    return time_string


def get_system_command_output(requested="supported_commands", timeout=1):
    system_output = SqliteQueue("queue_systemo")
    endtime = timeout + time.time()
    while time.time() < endtime:
        while system_output.length() > 0:
            data = system_output.pop()
            if data["command"][0] == requested:
                return data

    return {
        "command": [requested, None, None, None],
        "result": "ERROR",
        "message": "The requested command output could not be found.",
        "data": {"Response_Was": "To_Fast"},
    }


def read_generic_json(filename):
    # Historical behavior: no retry on a JSON parse error -- give up
    # immediately and return the empty-dict default.
    return _load_json_file(filename, {}, max_retries=0)


def write_generic_json(dictionary, filename):
    try:
        json_data_string = json.dumps(dictionary, indent=2, sort_keys=True)
        with open(filename, "w") as json_file:
            json_file.write(json_data_string)
    except:
        event = f"Error writing generic json file ({filename})"
        write_log(event)


def get_probe_info(probe_info):
    """Create a structure with probe information for the display to use."""
    probe_structure = {"primary": {}, "food": []}
    for probe in probe_info:
        if probe["type"] == "Primary":
            probe_structure["primary"]["name"] = probe["name"]
            probe_structure["primary"]["label"] = probe["label"]
        elif probe["type"] == "Food":
            food_probe = {"name": probe["name"], "label": probe["label"]}
            probe_structure["food"].append(food_probe)

    return probe_structure


def read_probe_status(probe_info):
    """
    Creates a structured status report for all probes in the system by combining probe configuration
    information with current device status information.

    Args:
            probe_info (list): List of probe configuration dictionaries containing information about each
                    probe such as type, label, device, etc.

    Returns:
            dict: A nested dictionary containing probe status information organized by probe type:
                    {
                            'P': {    # Primary probes
                                    '<probe_label>': {
                                            'status': {},
                                            'config': {},
                                            'enabled': bool,
                                            'profile': str or None,
                                            'port': str or None,
                                            'type': str or None,
                                            'device': str or None,
                                            'label': str or None,
                                            'name': str or None
                                    }
                            },
                            'F': {},  # Food probes (same structure as P)
                            'AUX': {} # Auxiliary probes (same structure as P)
                    }

    Example:
            probe_info = [
                    {
                            'type': 'Primary',
                            'label': 'Grill',
                            'device': 'device1',
                            ...
                    },
                    ...
            ]
            status = read_probe_status(probe_info)
            # Returns structured status information for all probes
    """
    # Get current device status information from the datastore
    probe_device_info = read_generic_key("probe_device_info")
    # print(f'Probe Device Info: {probe_device_info}')

    # Initialize the status structure
    probe_status = {
        "P": {},  # Primary probes
        "F": {},  # Food probes
        "AUX": {},  # Auxiliary probes
    }

    # Process each probe in the configuration
    for probe in probe_info:
        # Determine section based on probe type
        if probe["type"] == "Primary":
            section = "P"
        elif probe["type"] == "Food":
            section = "F"
        elif probe["type"] == "Aux":
            section = "AUX"
        probe_device = probe["device"]

        # Find matching device status and combine with probe configuration
        for device in probe_device_info:
            if device["device"] == probe_device:
                probe_status[section][probe["label"]] = {}  # Initialize dict for this probe
                probe_status[section][probe["label"]]["status"] = device.get("status", {})
                probe_status[section][probe["label"]]["config"] = device.get("config", {})
                probe_status[section][probe["label"]]["enabled"] = probe.get("enabled", True)
                probe_status[section][probe["label"]]["profile"] = probe.get("profile", None)
                probe_status[section][probe["label"]]["port"] = probe.get("port", None)
                probe_status[section][probe["label"]]["type"] = probe.get("type", None)
                probe_status[section][probe["label"]]["device"] = probe.get("device", None)
                probe_status[section][probe["label"]]["label"] = probe.get("label", None)
                probe_status[section][probe["label"]]["name"] = probe.get("name", None)

    return probe_status


# Borrowed from: https://stackoverflow.com/questions/3232943/update-value-of-a-nested-dictionary-of-varying-depth
# Attributed to Alex Martelli and Alex Telon
def deep_update(dictionary, updates):
    for key, value in updates.items():
        if isinstance(value, Mapping):
            dictionary[key] = deep_update(dictionary.get(key, {}), value)
        else:
            dictionary[key] = value
    return dictionary


MODE_MAP = {
    "startup": "Startup",
    "smoke": "Smoke",
    "shutdown": "Shutdown",
    "stop": "Stop",
    "reignite": "Reignite",
    "monitor": "Monitor",
    "error": "Error",
    "prime": "Prime",
    "hold": "Hold",
    "manual": "Manual",
}


# Borrowed from: https://pythonhow.com/how/check-if-a-string-is-a-float/
# Attributed to Python How
# Slightly modified to check if string is None
def is_float(string):
    if string is not None:
        if string.replace(".", "").isnumeric():
            return True
    return False


def _manual_toggle(control, pin_name, arglist, reset_pwm_when_off=False):
    """
    Apply a manual on/off/toggle action to a single manual-output pin
    (power, igniter, fan, or auger) within the 'set'/'manual' command.

    Mirrors the per-pin blocks that used to be inlined in process_command:
      - reads/writes control["manual"]["change"] and ["output"] for `pin_name`
      - a "toggle" request resolves against the live status pin state
      - `reset_pwm_when_off=True` additionally resets control["manual"]["pwm"]
        to 100 when the output is turned off (this only applied to the
        original "fan" branch; do not enable it for the others).
    """
    control["manual"]["change"] = pin_name
    if arglist[2] == "toggle":
        status = read_status()
        if status["outpins"][pin_name]:
            arglist[2] = "false"
        else:
            arglist[2] = "true"
    if arglist[2] == "true":
        control["manual"]["output"] = True
    else:
        control["manual"]["output"] = False
        if reset_pwm_when_off:
            control["manual"]["pwm"] = 100
    return control


def _cmd_get_uuid(data, control, settings, arglist, origin, kind):
    """
    Get Server Uuid
    /api/get/uuid

    Returns:
    {
        'uuid' : <Server Uuid>
    }
    """
    data["data"]["uuid"] = settings["server_info"]["uuid"]


def _cmd_get_versions(data, control, settings, arglist, origin, kind):
    """
    Get Server Versions
    /api/get/versions

    Returns:
    {
        'version' : <Server version>,
        'build' : <Server build>
    }
    """
    data["data"]["version"] = settings["versions"]["server"]
    data["data"]["build"] = settings["versions"]["build"]


def _cmd_get_hopper(data, control, settings, arglist, origin, kind):
    """
    Get Hopper Level
    /api/get/hopper

    Returns:
    {
        'hopper' : <level>
    }
    """
    control["hopper_check"] = True
    write_control(control, kind, origin=origin)
    time.sleep(3)
    pelletdb = read_pellet_db()
    data["data"]["hopper"] = pelletdb["current"]["hopper_level"]


def _cmd_get_timer(data, control, settings, arglist, origin, kind):
    """
    Get Timer Data
    /api/get/timer

    Returns:
    {
        'start' : control['timer']['start'],
        'paused' : control['timer']['paused'],
        'end' : control['timer']['end'],
        'shutdown' : control['notify_data'][]['shutdown'],
        'keep_warm' : control['notify_data'][]['keep_warm'],
    }
    """
    data["data"]["start"] = control["timer"]["start"]
    data["data"]["paused"] = control["timer"]["paused"]
    data["data"]["end"] = control["timer"]["end"]
    """ Get index of timer object """
    for index, notify_obj in enumerate(control["notify_data"]):
        if notify_obj["type"] == "timer":
            break
    data["data"]["shutdown"] = control["notify_data"][index]["shutdown"]
    data["data"]["keep_warm"] = control["notify_data"][index]["keep_warm"]


def _cmd_get_notify(data, control, settings, arglist, origin, kind):
    """
    Get Notify Data
    /api/get/notify

    Returns:
        [
            {
            "eta": null,
            "keep_warm": false,
            "label": "Grill",
            "name": "GrillMain",
            "req": false,
            "shutdown": false,
            "target": 0,
            "type": "probe"
            },
            ...
            {
            "keep_warm": false,
            "label": "Hopper",
            "last_check": 0,
            "req": true,
            "shutdown": false,
            "type": "hopper"
            }
        ]
    """
    data["data"] = control["notify_data"]


def _cmd_get_status(data, control, settings, arglist, origin, kind):
    """
    Get Status Information for Key Items
    /api/get/status

    Returns (Example):
    {
        "display_mode": "Stop",
        "lid_open_detected": false,
        "lid_open_endtime": 0,
        "mode": "Stop",
        "name": "Development",
        "outpins": {
            "auger": false,
            "fan": false,
            "igniter": false,
            "power": false
        },
        "p_mode": 0,
        "prime_amount": 0,
        "prime_duration": 0,
        "s_plus": false,
        "shutdown_duration": 10,
        "start_duration": 30,
        "start_time": 0,
        "startup_timestamp": 0,
        "status": "",
        "ui_hash": 5734093427135650890,
        "units": "F"
    }
    """
    status = read_status()

    data["data"]["mode"] = control["mode"]
    data["data"]["display_mode"] = status["mode"]
    data["data"]["status"] = control["status"]
    data["data"]["s_plus"] = control["s_plus"]
    data["data"]["units"] = settings["globals"]["units"]
    data["data"]["name"] = settings["globals"]["grill_name"]
    data["data"]["start_time"] = status["start_time"]
    data["data"]["start_duration"] = status["start_duration"]
    data["data"]["shutdown_duration"] = status["shutdown_duration"]
    data["data"]["prime_duration"] = status["prime_duration"]
    data["data"]["prime_amount"] = status["prime_amount"]
    data["data"]["lid_open_detected"] = status["lid_open_detected"]
    data["data"]["lid_open_endtime"] = status["lid_open_endtime"]
    data["data"]["p_mode"] = status["p_mode"]
    data["data"]["outpins"] = status["outpins"]
    data["data"]["startup_timestamp"] = status["startup_timestamp"]
    data["data"]["ui_hash"] = hash(json.dumps(settings["probe_settings"]["probe_map"]["probe_info"]))


def _cmd_get_temp(data, control, settings, arglist, origin, kind):
    """
    Get Temperature
    /api/get/temp/{probe label}

    Returns:
    {
        'temp' : <probe temperature>
        'result' : 'OK'
    }
    """
    current_temps = read_current()

    if arglist[1] in current_temps["P"].keys():
        data["data"]["temp"] = current_temps["P"][arglist[1]]
    elif arglist[1] in current_temps["F"].keys():
        data["data"]["temp"] = current_temps["F"][arglist[1]]
    elif arglist[1] in current_temps["AUX"].keys():
        data["data"]["temp"] = current_temps["AUX"][arglist[1]]
    else:
        data["result"] = "ERROR"
        data["message"] = f"Probe {arglist[1]} not found or not specified."


def _cmd_get_current(data, control, settings, arglist, origin, kind):
    """
    Get Current Temp Data Structure
    /api/get/current

    Returns (Example):
    {
        "AUX": {},
        "F": {
            "Probe1": 204,
            "Probe2": 206
        },
        "NT": {
            "Grill": 0,
            "Probe1": 0,
            "Probe2": 0
        },
        "P": {
            "Grill": 518
        },
        "PSP": 0,
        "TS": 1707345482984
    }
    """
    current_temps = read_current()

    data["data"] = current_temps


def _cmd_get_mode(data, control, settings, arglist, origin, kind):
    """
    Get Current Mode
    /api/get/mode

    Returns:
    {
        'mode' : <Current Mode>
    }
    """
    data["data"]["mode"] = control["mode"]


def _cmd_set_psp(data, control, settings, arglist, origin, kind):
    """
    Primary Setpoint
    /api/set/psp/{integer/float temperature}
    """
    if is_float(arglist[1]):
        control["mode"] = "Hold"
        if settings["globals"]["units"] == "F":
            control["primary_setpoint"] = int(float(arglist[1]))
        else:
            control["primary_setpoint"] = float(arglist[1])
        control["updated"] = True
        write_control(control, kind, origin=origin)
    else:
        data["result"] = "ERROR"
        data["message"] = f"Primary set point should be an integer or float in degrees {settings['globals']['units']}"


def _cmd_set_units(data, control, settings, arglist, origin, kind):
    """
    Units
    /api/set/units/{C/F}
    """
    if arglist[1] in ["C", "F"]:
        settings = convert_settings_units(arglist[1], settings)
        write_settings(settings)
        control["settings_update"] = True
        write_control(control, kind, origin=origin)
        control["updated"] = True
        control["units_change"] = True
        write_control(control, kind, origin=origin)
        # print(f'Settings Units Changed to {arglist[1]}')
    else:
        data["result"] = "ERROR"
        data["message"] = f"Set Units {arglist[1]} not recognized."


def _cmd_set_mode(data, control, settings, arglist, origin, kind):
    """
    Mode
    /api/set/mode/{mode} where mode = 'startup', 'smoke', 'shutdown', 'stop', 'reignite', 'monitor', 'error'
    /api/set/mode/prime/{prime amount in grams}[/{next mode}]
    /api/set/mode/hold/{integer/float temperature}
    """
    if arglist[1] in ["startup", "smoke", "shutdown", "stop", "reignite", "monitor", "error", "manual"]:
        control["mode"] = MODE_MAP[arglist[1]]
        control["updated"] = True
        write_control(control, kind, origin=origin)
    elif arglist[1] == "prime":
        try:
            if arglist[2] is not None:
                if arglist[2].isdigit():
                    control["mode"] = MODE_MAP[arglist[1]]
                    control["prime_amount"] = int(arglist[2])
                    control["updated"] = True
                    if arglist[3] in ["startup", "monitor"]:
                        control["next_mode"] = MODE_MAP[arglist[3]]
                    else:
                        control["next_mode"] = "Stop"
                    write_control(control, kind, origin=origin)
                else:
                    data["result"] = "ERROR"
                    data["message"] = f"Prime amount should be an integer in grams."
            else:
                data["result"] = "ERROR"
                data["message"] = f"Prime amount not specified."
        except:
            data["result"] = "ERROR"
            data["message"] = f"Set Mode {arglist[1]} with {arglist[2]} caused an exception."
    elif arglist[1] == "hold":
        if arglist[2] is not None:
            if is_float(arglist[2]):
                control["mode"] = MODE_MAP[arglist[1]]
                if settings["globals"]["units"] == "F":
                    control["primary_setpoint"] = int(float(arglist[2]))
                else:
                    control["primary_setpoint"] = float(arglist[2])
                control["updated"] = True
                write_control(control, kind, origin=origin)
            else:
                data["result"] = "ERROR"
                data["message"] = f"Set Mode {arglist[1]} with {arglist[2]} failed [not a number]."
        else:
            data["result"] = "ERROR"
            data["message"] = f"Set Mode {arglist[1]} with {arglist[2]} failed [no hold temp specified]."
    else:
        data["result"] = "ERROR"
        data["message"] = f"Get API Argument: {arglist[2]} not recognized."


def _cmd_set_pmode(data, control, settings, arglist, origin, kind):
    """
    PMode
    /api/set/pmode/{pmode value} where pmode value is between 0-9

    NOTE: hard-codes WriteKind.MERGE, ignoring the caller's `kind`. Preserved.
    """
    if arglist[1] is not None:
        if arglist[1].isdigit():
            if int(arglist[1]) >= 0 and int(arglist[1]) < 10:
                settings["cycle_data"]["PMode"] = int(arglist[1])
                write_settings(settings)
                control["settings_update"] = True
                write_control(control, WriteKind.MERGE, origin=origin)
            else:
                data["result"] = "ERROR"
                data["message"] = f"Set PMode out of range(0-9): {arglist[1]}"
        else:
            data["result"] = "ERROR"
            data["message"] = f"Set PMode invalid value."
    else:
        data["result"] = "ERROR"
        data["message"] = f"Set PMode invalid arguments."


def _cmd_set_splus(data, control, settings, arglist, origin, kind):
    """
    Smoke Plus
    /api/set/splus/{true/false}
    """
    if arglist[1] == "true":
        control["s_plus"] = True
    else:
        control["s_plus"] = False
    write_control(control, kind, origin=origin)


def _cmd_set_lid_open(data, control, settings, arglist, origin, kind):
    """
    Lid Open Toggle
    /api/set/lid_open/toggle

    NOTE: both branches of the if/else set lid_open_toggle to True, so no value
    can clear it. Preserved as-is.
    """
    if arglist[1] == "toggle":
        control["lid_open_toggle"] = True
    else:
        control["lid_open_toggle"] = True

    write_control(control, kind, origin=origin)


def _cmd_set_notify(data, control, settings, arglist, origin, kind):
    """
    Notify Settings
    /api/set/[notify:limit_high:limit_low]/{object}/ where object = probe label, 'Timer', 'Hopper'

    /api/set/notify/{object}/req/{true/false}
    /api/set/notify/{object}/target/{value}  (not valid for Timer or Hopper)
    /api/set/notify/{object}/shutdown/{true/false}
    /api/set/notify/{object}/keep_warm/{true/false}

    NOTE: hard-codes WriteKind.MERGE, ignoring the caller's `kind`. Also, the
    'target' path under units == 'C' writes control['primary_setpoint'] rather
    than the notify object's target. Both preserved as-is.
    """
    if arglist[1] is not None:
        if arglist[0] == "limit_high":
            limit = "probe_limit_high"
        elif arglist[0] == "limit_low":
            limit = "probe_limit_low"
        else:
            limit = None
        found = False
        for index, object in enumerate(control["notify_data"]):
            if object["label"] == arglist[1]:
                if limit is not None:
                    if object["type"] == limit:
                        found = True
                        break
                else:
                    found = True
                    break

        if not found:
            data["result"] = "ERROR"
            data["message"] = f"Notify object label {arglist[1]} was not found."
        else:
            # print(f'{object["label"]} FOUND')
            if arglist[2] in ["req", "shutdown", "keep_warm", "reignite"]:
                if arglist[3] == "true":
                    control["notify_data"][index][arglist[2]] = True
                else:
                    control["notify_data"][index][arglist[2]] = False
            elif arglist[2] == "target" and arglist[1] not in ["Timer", "Hopper"]:
                if is_float(arglist[3]):
                    if settings["globals"]["units"] == "F":
                        control["notify_data"][index]["target"] = int(float(arglist[3]))
                    else:
                        control["primary_setpoint"] = float(arglist[3])
                else:
                    data["result"] = "ERROR"
                    data["message"] = f"Notify object target value invalid or missing."
            else:
                data["result"] = "ERROR"
                data["message"] = f"Notify object update failed."
            write_control(control, WriteKind.MERGE, origin=origin)
    else:
        data["result"] = "ERROR"
        data["message"] = f"Notify object label was not specified."


def _cmd_set_pwm(data, control, settings, arglist, origin, kind):
    """
    PWM Control

    /api/set/pwm/{true/false}
    """
    if arglist[1] == "true":
        control["pwm_control"] = True
    else:
        control["pwm_control"] = False
    write_control(control, kind, origin=origin)


def _cmd_set_duty_cycle(data, control, settings, arglist, origin, kind):
    """
    Duty Cycle

    /api/set/duty_cycle/{0-100 percent}

    NOTE: hard-codes WriteKind.MERGE, ignoring the caller's `kind`. Preserved.
    """
    if is_float(arglist[1]):
        duty_cycle = int(arglist[1])
        if duty_cycle >= 0 and duty_cycle <= 100:
            control["duty_cycle"] = duty_cycle
            write_control(control, WriteKind.MERGE, origin=origin)
        else:
            data["result"] = "ERROR"
            data["message"] = f"Duty cycle must be an integer between 0-100."
    else:
        data["result"] = "ERROR"
        data["message"] = f"Duty cycle must be specified as an integer between 0-100 percent."


def _cmd_set_tuning_mode(data, control, settings, arglist, origin, kind):
    """
    Tuning Mode Enable

    /api/set/tuning_mode/{true/false}
    """
    if arglist[1] == "true":
        control["tuning_mode"] = True
    else:
        control["tuning_mode"] = False
    write_control(control, kind, origin=origin)


def _cmd_set_timer(data, control, settings, arglist, origin, kind):
    """
    Timer Control

    /api/set/timer/start/{seconds}
    /api/set/timer/pause
    /api/set/timer/stop
    /api/set/timer/shutdown/{true/false}
    /api/set/timer/keep_warm/{true/false}

    NOTE: the start/pause/stop paths hard-code origin='app', ignoring the
    caller's `origin`; shutdown/keep_warm honor it. Preserved as-is.
    """

    """ Get index of timer object """
    for index, notify_obj in enumerate(control["notify_data"]):
        if notify_obj["type"] == "timer":
            break
    """ Get timestamp """
    now = time.time()

    if arglist[1] == "start":
        control["notify_data"][index]["req"] = True
        # If starting new timer
        if control["timer"]["paused"] == 0:
            control["timer"]["start"] = now
            if is_float(arglist[2]):
                seconds = int(float(arglist[2]))
                control["timer"]["end"] = now + seconds
            else:
                control["timer"]["end"] = now + 60
            write_log("Timer started.  Ends at: " + epoch_to_time(control["timer"]["end"]))
            write_control(control, kind, origin="app")
        else:  # If Timer was paused, restart with new end time.
            control["timer"]["end"] = (control["timer"]["end"] - control["timer"]["paused"]) + now
            control["timer"]["paused"] = 0
            write_log("Timer unpaused.  Ends at: " + epoch_to_time(control["timer"]["end"]))
            write_control(control, kind, origin="app")
    elif arglist[1] == "pause":
        if control["timer"]["start"] != 0:
            control["notify_data"][index]["req"] = False
            control["timer"]["paused"] = now
            write_log("Timer paused.")
            write_control(control, kind, origin="app")
        else:
            control["notify_data"][index]["req"] = False
            control["timer"]["start"] = 0
            control["timer"]["end"] = 0
            control["timer"]["paused"] = 0
            control["notify_data"][index]["shutdown"] = False
            control["notify_data"][index]["keep_warm"] = False
            write_log("Timer cleared.")
            write_control(control, kind, origin="app")
    elif arglist[1] == "stop":
        control["notify_data"][index]["req"] = False
        control["timer"]["start"] = 0
        control["timer"]["end"] = 0
        control["timer"]["paused"] = 0
        control["notify_data"][index]["shutdown"] = False
        control["notify_data"][index]["keep_warm"] = False
        write_log("Timer stopped.")
        write_control(control, kind, origin="app")
    elif arglist[1] == "shutdown":
        if arglist[2] == "true":
            control["notify_data"][index]["shutdown"] = True
        else:
            control["notify_data"][index]["shutdown"] = False
        write_control(control, kind, origin=origin)
    elif arglist[1] == "keep_warm":
        if arglist[2] == "true":
            control["notify_data"][index]["keep_warm"] = True
        else:
            control["notify_data"][index]["keep_warm"] = False
        write_control(control, kind, origin=origin)
    else:
        data["result"] = "ERROR"
        data["message"] = f"Timer command not recognized."


def _cmd_set_manual(data, control, settings, arglist, origin, kind):
    """
    Manual Control
    Note: Must already be in Manual mode (see set/mode command)
    /api/set/manual/power/{true/false/toggle}
    /api/set/manual/igniter/{true/false/toggle}
    /api/set/manual/fan/{true/false/toggle}
    /api/set/manual/auger/{true/false/toggle}
    /api/set/manual/pwm/{speed}

    NOTE: the write_control below is outside the if/elif chain, so a rejected
    (ERROR) request still writes control when control['manual']['change'] holds
    a stale value from a previous command. Preserved as-is.
    """

    if control["mode"] == "Manual" or settings["safety"]["allow_manual_changes"]:
        if arglist[1] == "power":
            control = _manual_toggle(control, "power", arglist)
        elif arglist[1] == "igniter":
            control = _manual_toggle(control, "igniter", arglist)
        elif arglist[1] == "fan":
            control = _manual_toggle(control, "fan", arglist, reset_pwm_when_off=True)
        elif arglist[1] == "auger":
            control = _manual_toggle(control, "auger", arglist)
        elif arglist[1] == "pwm" and is_float(arglist[2]):
            control["manual"]["change"] = "pwm"
            control["manual"]["output"] = True
            control["manual"]["pwm"] = int(float(arglist[2]))
        else:
            data["result"] = "ERROR"
            data["message"] = f"Manual command not recognized or contained an error."
        if control["manual"]["change"] in ["power", "igniter", "fan", "auger", "pwm"]:
            write_control(control, kind, origin=origin)

    else:
        data["result"] = "ERROR"
        data["message"] = f"Before changing manual outputs, system must be put into Manual mode."


def _cmd_cmd_restart(data, control, settings, arglist, origin, kind):
    """
    Restart Scripts
    /api/cmd/restart
    """
    restart_scripts()


def _cmd_cmd_reboot(data, control, settings, arglist, origin, kind):
    """
    Reboot System
    /api/cmd/reboot
    """
    reboot_system()


def _cmd_cmd_shutdown(data, control, settings, arglist, origin, kind):
    """
    Shutdown System
    /api/cmd/shutdown
    """
    shutdown_system()


def _cmd_sys(data, control, settings, arglist, origin, kind):
    """
    System Control Commands

    Unlike get/set/cmd, this action has no subcommand ladder: any arglist is
    pushed to the system queue verbatim. Note that the arglist pushed here is
    the PADDED one, so trailing Nones leak into the queue payload -- e.g.
    ['restart'] is pushed as ['restart', None, None, None]. Preserved as-is.
    """
    system_command_queue = SqliteQueue("queue_systemq")
    system_command_queue.push(arglist)


def _process_command_unknown(data, action, arglist):
    """
    Fallback for a command with no registered handler.

    Reproduces the four distinct error paths of the original if/elif ladder
    exactly. Note the inconsistent formatting, preserved as-is: the `get` path
    brackets the offending argument, while `set` and `cmd` do not.

    `arglist` has already been padded to `max_args`, so `arglist[0]` is always
    subscriptable here and is None when the caller passed no arguments -- the
    same value the original ladder's `else` branches interpolated.
    """
    data["result"] = "ERROR"
    if action == "get":
        data["message"] = f"Get API Argument: [{arglist[0]}] not recognized."
    elif action == "set":
        data["message"] = f"Set API Argument: {arglist[0]} not recognized."
    elif action == "cmd":
        data["message"] = f"CMD API Argument: {arglist[0]} not recognized."
    else:
        data["message"] = f"Action [{action}] not valid/recognized."


""" Maps (action, subcommand) -> handler. `set` routes three subcommands to the
    shared notify handler, exactly as the original `arglist[0] in [...]` test did. """
_COMMAND_DISPATCH = {
    ("get", "temp"): _cmd_get_temp,
    ("get", "current"): _cmd_get_current,
    ("get", "mode"): _cmd_get_mode,
    ("get", "uuid"): _cmd_get_uuid,
    ("get", "versions"): _cmd_get_versions,
    ("get", "hopper"): _cmd_get_hopper,
    ("get", "timer"): _cmd_get_timer,
    ("get", "notify"): _cmd_get_notify,
    ("get", "status"): _cmd_get_status,
    ("set", "psp"): _cmd_set_psp,
    ("set", "units"): _cmd_set_units,
    ("set", "mode"): _cmd_set_mode,
    ("set", "pmode"): _cmd_set_pmode,
    ("set", "splus"): _cmd_set_splus,
    ("set", "lid_open"): _cmd_set_lid_open,
    ("set", "notify"): _cmd_set_notify,
    ("set", "limit_high"): _cmd_set_notify,
    ("set", "limit_low"): _cmd_set_notify,
    ("set", "pwm"): _cmd_set_pwm,
    ("set", "duty_cycle"): _cmd_set_duty_cycle,
    ("set", "tuning_mode"): _cmd_set_tuning_mode,
    ("set", "timer"): _cmd_set_timer,
    ("set", "manual"): _cmd_set_manual,
    ("cmd", "restart"): _cmd_cmd_restart,
    ("cmd", "reboot"): _cmd_cmd_reboot,
    ("cmd", "shutdown"): _cmd_cmd_shutdown,
}

""" Maps action -> handler for actions that have no subcommand ladder and so
    cannot be keyed by (action, subcommand). `sys` accepts any arglist and
    pushes it to the system queue verbatim. """
_ACTION_DISPATCH = {
    "sys": _cmd_sys,
}


def process_command(action=None, arglist=[], origin="unknown", kind=WriteKind.MERGE):
    """
    Process incoming command from API or elsewhere
    """
    data = {}
    data["result"] = "OK"
    data["message"] = "Command was accepted successfully."
    data["data"] = {}

    control = read_control()
    settings = read_settings()

    """ Populate any empty args with None just in case """
    num_args = len(arglist)
    max_args = 4  # Needs updating if API adds deeper number of arguments

    for _ in range(max_args - num_args):
        arglist.append(None)

    """ Subcommand lookup first, then the action-only table for actions (sys)
        that dispatch on the action alone. """
    handler = _COMMAND_DISPATCH.get((action, arglist[0]))
    if handler is None:
        handler = _ACTION_DISPATCH.get(action)

    if handler is None:
        _process_command_unknown(data, action, arglist)
    else:
        handler(data, control, settings, arglist, origin, kind)

    return data


def set_nested_key_value(data, key_list, value):
    """
    Sets the value of a key in a nested dictionary and returns the modified dictionary.

    Args:
            data: The dictionary to modify.
            key_list: A list of keys representing the path to the nested key.
            value: The value to assign to the nested key.

    Returns:
            The modified dictionary.

    Raises:
            KeyError: If any key in the path is not found in the dictionary.
    """
    if not key_list:
        return data  # Reached the end of the key list, return the data

    current_key = key_list[0]
    # Check if the key exists and is a dictionary (except for the last key)
    if current_key not in data or (len(key_list) > 1 and not isinstance(data[current_key], dict)):
        raise KeyError(f"Key '{current_key}' not found or not a dictionary")

    # Check if we reached the bottom level (last key in the list)
    if len(key_list) == 1:
        data[current_key] = value
    else:
        # Recursive call for nested dictionaries
        data[current_key] = set_nested_key_value(data[current_key], key_list[1:], value)

    return data


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


# =============================================================================
#  TEMPORARY COMPATIBILITY RE-IMPORTS (Task 8 scaffolding -- removed in Task 9)
# =============================================================================
#  common/common.py has been split into common/defaults.py, common/system.py,
#  common/datastore_accessors.py, common/settings_migration.py and
#  common/api_commands.py. Task 9 rewrites the ~55 external import sites to
#  import from those modules directly and then deletes this block; until then
#  every moved name must keep resolving as `common.common.<name>`.
#
#  THIS BLOCK MUST STAY AT THE BOTTOM OF THIS FILE. common/common.py is
#  temporarily BOTH the bottom utility layer (write_log, read_generic_json,
#  generate_uuid, deep_update, ...) that the new modules import, AND the facade
#  that re-imports them -- an import cycle by construction. It resolves only
#  because every utility the new modules import is defined above this line by
#  the time the new modules execute. Do not move these imports to the top.
# =============================================================================
from common.defaults import (  # noqa: E402,F401
    COLOR_LIST,
    METRIC_COLUMNS,
    _default_controller_config,
    _default_dashboard,
    _default_display_config,
    _default_probe_profiles,
    _default_recipe_probe_map,
    default_control,
    default_metrics,
    default_notify,
    default_notify_services,
    default_pellets,
    default_probe_config,
    default_probe_map,
    default_settings,
    metrics_items,
)
from common.datastore_accessors import (  # noqa: E402,F401
    _flush_control,
    _get_install_status,
    _history_row_to_dict,
    _metrics_row_to_dict,
    _read_json_blob,
    _read_json_key_or_none,
    _set_install_status,
    _write_json_blob,
    execute_control_writes,
    get_updater_install_status,
    get_wizard_install_status,
    load_wizard_install_info,
    read_autotune,
    read_connected_users,
    read_control,
    read_current,
    read_errors,
    read_generic_key,
    read_history,
    read_metrics,
    read_pellet_db,
    read_pellets_store,
    read_settings,
    read_settings_store,
    read_status,
    read_tr,
    read_warnings,
    remove_connected_user,
    set_updater_install_status,
    set_wizard_install_status,
    store_wizard_install_info,
    write_autotune,
    write_connected_user,
    write_control,
    write_current,
    write_errors,
    write_generic_key,
    write_history,
    write_metrics,
    write_pellet_db,
    write_pellets_store,
    write_settings,
    write_settings_store,
    write_status,
    write_tr,
    write_warning,
)
from common.settings_migration import (  # noqa: E402,F401
    downgrade_settings,
    read_settings_file,
    upgrade_settings,
)
