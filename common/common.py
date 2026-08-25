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
import copy
import datetime
import json
import logging
import os
import random
import time
import uuid
from collections.abc import Mapping
from enum import Enum
from logging.handlers import RotatingFileHandler

from ratelimitingfilter import RateLimitingFilter

from common import datastore
from common.modes import Mode
from common.sqlite_log_handler import SqliteLogHandler
from common.sqlite_queue import SqliteQueue

# *****************************************
# Enums
# *****************************************


class ErrorKind(Enum):
    """Which process owns a dashboard error banner.

    Each process is independently supervised and rewrites its own list
    wholesale on restart, so the owner is what keeps one process's restart
    from erasing another's banners.

    Declaration order is the dashboard's display order for
    :data:`ErrorKind.ALL`.
    """

    CONTROL = "control"
    DISPLAY = "display"
    WEB = "web"
    # A read-only selector, not an owner: nothing writes banners "as ALL".
    ALL = "all"


# *****************************************
# Constants and Globals
# *****************************************
"""
==============================================================================
 Constants and Globals
==============================================================================
"""
BACKUP_PATH = "./backups/"  # Settings/pellet-DB backups exported from SQLite, plus their manifest.json


"""
==============================================================================
 Functions
==============================================================================
"""


#: Every log file path in the tree derives from this, so logging can be
#: redirected wholesale. The test suite does exactly that: before it could,
#: tests appended to the operator's real ./logs/events.log -- which is why that
#: file carried fixture strings like "Admin: Shutdown failed: boom" in the
#: content the log viewer shows, and why the files and the `logs` table
#: diverged (tests use a temporary database).
#:
#: Read from the environment at IMPORT time, the same shape as datastore's
#: PIFIRE_DB_PATH and for the same reason: many test modules do
#: `from app import app` at module scope, so loggers are built during
#: collection, before any fixture can run.
LOG_DIR = os.environ.get("PIFIRE_LOG_DIR", "./logs")

#: Names create_logger has actually attached handlers for. logging.getLogger
#: caches by name, so a logger built before LOG_DIR moved keeps its old file
#: handler until something detaches it; reset_loggers needs to find those.
_CREATED_LOGGERS = set()


def log_path(name):
    """Path to a log file inside LOG_DIR."""
    return os.path.join(LOG_DIR, name)


def reset_loggers():
    """Detach and close every handler create_logger attached.

    The next create_logger call for the same name sees `not logger.handlers`
    and rebuilds against the current LOG_DIR. Redirecting LOG_DIR alone is not
    enough: app.py builds its loggers at import time, before any test fixture
    can run.
    """
    for name in sorted(_CREATED_LOGGERS):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
    _CREATED_LOGGERS.clear()


def create_logger(
    name,
    filename=None,
    messageformat="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    maxBytes=1 * 1024 * 1024,  # 1 MB
    backupCount=3,
):
    """Create or Get Existing Logger"""
    #  Resolved at call time, not bound as a default argument: a default binds
    #  once at import and would ignore any later LOG_DIR change.
    filename = filename or log_path("pifire.log")
    logger = logging.getLogger(name)
    """ 
		If the logger does not exist, create one. Else return the logger. 
		Note: If the a log-level change is needed, the developer should directly set the log level on the logger, instead of using 
		this function.  
	"""
    if not logger.handlers:
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

        _CREATED_LOGGERS.add(name)
    return logger


def display_sleep_timeout(settings):
    """Idle seconds before the display sleeps; 0 = never. Defaults to 300 on
    missing/invalid values. Negative values clamp to 0."""
    try:
        value = int(settings["display"]["sleep_timeout"])
    except KeyError, TypeError, ValueError:
        return 300
    return max(0, value)


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
    except OSError:
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
        with open(log_path("events.log")) as event_file:
            event_lines = event_file.readlines()
            event_file.close()
    # If file not found error, then create events.log file
    except OSError:
        with open(log_path("events.log"), "w"):
            pass
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
            for line in range(10 - num_events):
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
    except OSError:
        event = f"Unable to open log file: {filepath}"
        write_log(event)
        return []

    return log_file_lines


def write_log(event, loggername="events"):
    """
    Write event to event.log

    :param event: String event
    """
    log_level = logging.INFO
    eventLogger = create_logger(
        loggername,
        filename=log_path("events.log"),
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


def flush_events_records():
    """
    Erase the events log.

    Previously reachable only as ``read_events_records(flush=True)`` -- the same
    delete-behind-a-read_-name defect as the old ``read_history(flushhistory=True)``
    (see common.persistence.history.flush_history).

    :return: An empty events list (the post-flush state).
    """
    datastore.clear_log("events")
    return []


def read_events_records():
    """
    Read Events from events.log and return a list of event dictionaries.

    :return: events_list - list of {'date':, 'time':, 'message':} dicts
    """
    events, num_events = read_events()
    events_list = []
    for item in range(min(num_events, 60)):
        events_list.append({"date": events[item][0], "time": events[item][1], "message": events[item][2].strip("\n")})
    return events_list


def unpack_history(datalist):
    temp_dict = {}  # Create temporary dictionary to store all of the history data lists
    temp_struct = datalist[0]  # Load the initial history data into a temporary dictionary
    for key in temp_struct:  # Iterate each of the keys
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
                for subkey, subvalue in value.items():
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


def convert_temp_delta(units, delta):
    """
    Convert a temperature DELTA (a difference between two readings, e.g.
    "degrees below setpoint") between C and F -- scale only, no +32 offset.
    `convert_temp` is for absolute readings and would corrupt a delta (a
    3-degree-C band would become "38", not "5.4", if run through it).

    :param units: target units, C or F
    :param delta: delta to convert, in the OTHER unit
    :return: converted delta
    """
    if units == "F":
        return int(delta * (9 / 5))
    else:
        return int(delta * (5 / 9))


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
        for temp in range(len(settings["startup"]["smartstart"]["temp_range_list"])):
            settings["startup"]["smartstart"]["temp_range_list"][temp] = convert_temp(
                units, settings["startup"]["smartstart"]["temp_range_list"][temp]
            )
        settings["startup"]["smartstart"]["exit_temp"] = convert_temp(
            units, settings["startup"]["smartstart"]["exit_temp"]
        )
        # pwm.temp_range_list is a set of "degrees below setpoint" duty-cycle
        # band thresholds (controller/runtime/logic/pwm.py compares
        # (setpoint - ptemp) against these), NOT absolute readings -- was
        # never converted at all here (a longstanding omission; smartstart's
        # temp_range_list above, an absolute-reading list, was). A delta
        # needs scale-only conversion (convert_temp_delta), never convert_temp's
        # +32 offset.
        for temp in range(len(settings["pwm"]["temp_range_list"])):
            settings["pwm"]["temp_range_list"][temp] = convert_temp_delta(
                units, settings["pwm"]["temp_range_list"][temp]
            )
    return settings


def notify_target_conversion_ops(notify_data, units):
    """Build notify.set ops that convert every armed notify target to `units`.

    A notify target lives in `control["notify_data"]`, not in settings, so
    `convert_settings_units` never touches it. Each armed entry (probe,
    probe_limit_high, probe_limit_low) carries a `target` in the CURRENT units;
    on a units change it must be converted like every other temperature.

    Returned as addressed `notify.set` ops rather than a whole-array replace so
    a notify write landing in the same control cycle survives the drain.

    A `target` of 0 is the "off" sentinel, not a temperature -- converting it
    (convert_temp("C", 0) == -17) would fabricate a garbage below-freezing
    target -- so it is skipped, as are timer/hopper entries that carry no
    `target` key at all. `convert_temp` assumes its input is in the OTHER unit,
    which for a real change is exactly the old unit; the caller therefore gates
    this on an ACTUAL unit change so a redundant same-unit write cannot
    double-convert.

    :param notify_data: control["notify_data"], the notify entry list
    :param units: the NEW units, C or F
    :return: a list of notify.set op dicts (empty when nothing is armed)
    """
    ops = []
    for entry in notify_data:
        target = entry.get("target")
        if target:
            ops.append(
                {
                    "op": "notify.set",
                    "label": entry["label"],
                    "type": entry["type"],
                    "fields": {"target": convert_temp(units, target)},
                }
            )
    return ops


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


def guard_none_metric_field(metrics_data, index, field, caller, default=0):
    """Read metrics_data[index][field], substituting (and writing back) `default`
    if it's None, and warn-logging that substitution.

    Shared None-guard for metrics rows that can be poisoned by update_metrics'
    "amend last record" path when handed a partial dict missing `field` --
    the row keeps whatever value that column already held; if it was never
    populated (or was itself nulled), a consumer that does arithmetic/ordering
    on the field would otherwise crash. Reused by every metrics consumer that
    dereferences these fields (process_metrics, prepare_annotations).

    :param metrics_data: list of metrics row dicts (mutated in place)
    :param index: row index into metrics_data
    :param field: column name to guard
    :param caller: name of the calling function, for the log message
    :param default: safe substitute when the field is None
    :return: the field's value (or `default` if it was None)
    """
    value = metrics_data[index][field]
    if value is None:
        write_log(
            f"WARNING: {caller} found a metrics row (index {index}) with a None {field}; using {default!r} as a safe default."
        )
        value = default
        metrics_data[index][field] = value
    return value


def process_metrics(metrics_data, augerrate=0.3):
    # Process Additional Metrics Information for Display
    for index in range(len(metrics_data)):
        # Convert Start Time
        starttime = guard_none_metric_field(metrics_data, index, "starttime", "process_metrics")
        metrics_data[index]["starttime_c"] = epoch_to_time(starttime / 1000)
        # Convert End Time
        endtime = guard_none_metric_field(metrics_data, index, "endtime", "process_metrics")
        if endtime == 0:
            endtime_c = 0
        else:
            endtime_c = epoch_to_time(endtime / 1000)
        metrics_data[index]["endtime_c"] = endtime_c
        # Time in Mode
        if metrics_data[index]["mode"] == Mode.STOP:
            timeinmode = "NA"
        elif endtime == 0:
            timeinmode = "Active"
        else:
            seconds = int((endtime / 1000) - (starttime / 1000))
            if seconds > 60:
                timeinmode = f"{int(seconds / 60)} m {seconds % 60} s"
            else:
                timeinmode = f"{seconds} s"
        metrics_data[index]["timeinmode"] = timeinmode
        # Convert Auger On Time
        augerontime = guard_none_metric_field(metrics_data, index, "augerontime", "process_metrics")
        metrics_data[index]["augerontime_c"] = str(int(augerontime)) + " s"
        # Estimated Pellet Usage
        grams = int(augerontime * augerrate)
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


def _is_output_for(entry, requested):
    try:
        return entry["command"][0] == requested
    except TypeError, KeyError, IndexError:
        return False


#: How long to wait between peeks at queue_systemo. Small enough that a
#: prompt answer is still picked up promptly, large enough that a full
#: timeout costs ~40 SELECTs instead of tens of thousands.
_SYSTEM_OUTPUT_POLL_INTERVAL = 0.025


def get_system_command_output(requested="supported_commands", timeout=1):
    """Wait (up to `timeout` seconds) for the control process's answer to `requested`.

    `queue_systemo` is shared by every consumer: dash_page and
    socket_io._check_control_status poll for "check_alive", get_supported_cmds
    for "supported_commands", common/system.py's system-info gather for
    check_wifi_quality/check_throttled/check_cpu_temp/network_info/
    hardware_info, and the wizard for "scan_bluetooth".

    This used to pop entries one at a time and DISCARD every non-matching one,
    so a poll running while another consumer's answer sat in the queue
    destroyed it -- that consumer then busy-waited out its whole timeout and
    returned the "could not be found" envelope for a command the control
    process had actually answered. Entries that are not ours are now left
    where they are.

    The wait between peeks is a real sleep. It used to be a bare `continue`,
    which spun this loop as fast as the interpreter would go -- and since the
    peek is a SQLite SELECT, that meant hammering the datastore with queries
    for the whole timeout, on the same database the control loop is using to
    time the auger and the igniter. Whenever the control process was slow or
    down (exactly when this function runs out its full timeout) the web process
    answered by making it slower.
    """
    system_output = SqliteQueue("queue_systemo")
    endtime = timeout + time.time()
    while time.time() < endtime:
        # Peek before touching anything: when our answer has not arrived yet
        # (the common case while waiting) the queue is left completely
        # undisturbed, rather than being churned pop-by-pop every iteration.
        if not any(_is_output_for(entry, requested) for entry in system_output.list()):
            time.sleep(_SYSTEM_OUTPUT_POLL_INTERVAL)
            continue
        # SqliteQueue is pop-from-head only, so reach our entry by popping the
        # ones ahead of it and pushing them back, preserving their order.
        deferred = []
        data = None
        while system_output.length() > 0:
            entry = system_output.pop()
            if entry is None:  # raced with another consumer
                break
            if _is_output_for(entry, requested):
                data = entry
                break
            deferred.append(entry)
        for entry in deferred:
            system_output.push(entry)
        if data is not None:
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
    "startup": Mode.STARTUP,
    "smoke": Mode.SMOKE,
    "shutdown": Mode.SHUTDOWN,
    "stop": Mode.STOP,
    "reignite": Mode.REIGNITE,
    "monitor": Mode.MONITOR,
    "error": Mode.ERROR,
    "prime": Mode.PRIME,
    "hold": Mode.HOLD,
    "manual": Mode.MANUAL,
}


# Borrowed from: https://pythonhow.com/how/check-if-a-string-is-a-float/
# Attributed to Python How
# Slightly modified to check if string is None
def is_float(string):
    if string is not None and string.replace(".", "").isnumeric():
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
