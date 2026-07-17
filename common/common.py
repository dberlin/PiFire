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


""" Maps (action, subcommand) -> handler. `set` routes three subcommands to the
    shared notify handler, exactly as the original `arglist[0] in [...]` test did. """
""" Maps action -> handler for actions that have no subcommand ladder and so
    cannot be keyed by (action, subcommand). `sys` accepts any arglist and
    pushes it to the system queue verbatim. """


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
from common.backups import (  # noqa: E402,F401
    backup_pellet_db,
    backup_settings,
    read_pellet_db_file,
)
from common.settings_migration import (  # noqa: E402,F401
    downgrade_settings,
    read_settings_file,
    restore_settings,
    upgrade_settings,
)
from common.system import (  # noqa: E402,F401
    _detect_wireless_interface,
    _wifi_quality_from_iw,
    _wifi_quality_from_iwconfig,
    get_os_info,
    get_wifi_quality,
    is_real_hardware,
    reboot_system,
    restart_control,
    restart_scripts,
    restart_webapp,
    shutdown_system,
)
from common.api_commands import (  # noqa: E402,F401
    _ACTION_DISPATCH,
    _COMMAND_DISPATCH,
    _cmd_cmd_reboot,
    _cmd_cmd_restart,
    _cmd_cmd_shutdown,
    _cmd_get_current,
    _cmd_get_hopper,
    _cmd_get_mode,
    _cmd_get_notify,
    _cmd_get_status,
    _cmd_get_temp,
    _cmd_get_timer,
    _cmd_get_uuid,
    _cmd_get_versions,
    _cmd_set_duty_cycle,
    _cmd_set_lid_open,
    _cmd_set_manual,
    _cmd_set_mode,
    _cmd_set_notify,
    _cmd_set_pmode,
    _cmd_set_psp,
    _cmd_set_pwm,
    _cmd_set_splus,
    _cmd_set_timer,
    _cmd_set_tuning_mode,
    _cmd_set_units,
    _cmd_sys,
    _manual_toggle,
    _process_command_unknown,
    process_command,
)
