"""
==============================================================================
 PiFire Default Structure Builders
==============================================================================

Description: Builders for PiFire's default configuration structures
  (settings, control, pellets, metrics, probe map, notification services).

  Extracted from common/common.py; common/common.py re-imports these names
  for now so that existing `common.common.X` call sites keep resolving.

==============================================================================
"""

import datetime
import math
import os
import time

from common.common import generate_uuid, get_probe_list, read_generic_json, read_updater_manifest
from common.modes import Mode, StatusState

# Set of default colors for charts.  Contains list of tuples (primary color, secondary color).
COLOR_LIST = [
    ("rgb(0, 64, 255, 1)", "rgb(0, 128, 255, 1)"),  # Blue
    ("rgb(0, 200, 64, 1)", "rgb(0, 232, 126, 1)"),  # Green
    ("rgb(132, 0, 0, 1)", "rgb(200, 0, 0, 1)"),  # Red
    ("rgb(126, 0, 126, 1)", "rgb(126, 64, 125, 1)"),  # Purple
    ("rgb(255, 210, 0, 1)", "rgb(255, 255, 0, 1)"),  # Yellow
    ("rgb(255, 126, 0, 1)", "rgb(255, 126, 64, 1)"),  # Orange
]


def default_settings():
    settings = {}

    updater_info = read_updater_manifest()
    settings["versions"] = updater_info["metadata"]["versions"]

    settings["server_info"] = {"uuid": generate_uuid()}

    settings["probe_settings"] = {}
    settings["probe_settings"]["probe_profiles"] = _default_probe_profiles()
    settings["probe_settings"]["probe_map"] = default_probe_map(settings["probe_settings"]["probe_profiles"])

    settings["globals"] = {
        "grill_name": "",
        "debug_mode": False,
        "page_theme": "light",
        "disp_rotation": 0,
        "units": "F",
        "augerrate": 0.3,  # (grams per second) default auger load rate is 10 grams / 30 seconds
        "first_time_setup": True,  # Set to True on first setup, to run wizard on load
        "ext_data": False,  # Set to True to allow tracking of extended data.  More data will be stored in the history database and can be reviewed in the CSV.
        "global_control_panel": False,  # Set to True to display control panel on most pages (except Updater, Wizard, Cookfile and some other pages)
        "boot_to_monitor": False,  # Set to True to boot directly into monitor mode
        "prime_ignition": False,  # Set to True to enable the igniter in prime & startup mode
        "updated_message": False,  # Set to True to display a pop-up message after the system has been updated
        "venv": True,  # Set to True if running in virtual environment (needed for Raspberry Pi OS Bookworm)
        "python_exec": ".venv/bin/python",  # Path to the python executable
        "uv": True,  # Set to True to enable UV for pip install
    }

    if os.path.exists("bin"):
        settings["globals"]["venv"] = True

    """ The following are platform related settings, such as pin assignments, etc. """
    settings["platform"] = {
        "devices": {
            "display": {
                "dc": 24,  # SPI Display (ex. ILI9341)
                "led": 5,  # SPI Display (ex. ILI9341)
                "rst": 25,  # SPI Display (ex. ILI9341)
            },
            "distance": {
                "echo": 27,  # HCSR04 Distance Sensor
                "trig": 23,  # HCSR04 Distance Sensor
                "i2c_bus_kind": "basic",  # VL53L0X/VL53L4CD/VL53L1X: "basic" | "extended"
                "i2c_bus_num": "CP2112",  # VL53L0X/VL53L4CD/VL53L1X: numbered bus or adapter-name match
                "address": None,  # VL53L0X/VL53L4CD/VL53L1X: optional I2C address override (hex string or int)
                "device": "/dev/ttyACM0",  # SEN0628: USB-serial device path
            },
            "input": {
                "down_dt": 20,  # Button (DOWN) or Encoder (DT)
                "enter_sw": 21,  # Button (ENTER) or Encoder (SW)
                "up_clk": 16,  # Button (UP) or Encoder (CLK)
            },
        },
        "inputs": {
            "selector": 17,  # Selector input to select between the OEM Controller or PiFire Controller
            "shutdown": 17,  # Shutdown GPIO Pin if implemented
        },
        "outputs": {"auger": 14, "dc_fan": 26, "fan": 15, "igniter": 18, "power": 4, "pwm": 13},
        "system": {
            "SPI0": {
                "CE0": 8,  # In case a non-standard CE/CS is utilized
                "CE1": 7,  # In case a non-standard CE/CS is utilized
            },
            "1WIRE": None,  # 1WIRE is used for probe devices specifically the DS18B20
        },
        "numato": {  # x86_numato platform: Numato USB relay board
            "device": "/dev/ttyACM0",  # serial (tty) device path
            "baudrate": 921600,
        },
        "fan_controller": {  # x86_numato platform: selectable EMC2101/EMC2301 fan PWM controller
            "chip": "emc2101",  # 'emc2101' or 'emc2301'
            "i2c_bus_kind": "basic",  # 'basic' = integrated I2C bus (board.SCL/SDA); 'extended' = numbered/bridge bus
            "i2c_bus_num": "1",  # extended only: /dev/i2c-N number or adapter-name match (e.g. 'CP2112')
            "address": "0x4c",  # fan controller I2C address (EMC2101 0x4C / EMC2301 0x2F)
        },
        "ft232h": {  # ft232h_relay platform: FT232H USB GPIO expander selection
            "url": "1"  # '1' = first FT232H; or a pyftdi URL to pick a specific device
        },
        "current": "custom",
        "dc_fan": False,  # True if system has a DC Fan (Does not indicate PWM)
        "triggerlevel": "HIGH",  # Active LOW / Active HIGH for the Relay Outputs
        "buttonslevel": "HIGH",  # Active LOW / Active HIGH for the button inputs
        "standalone": True,  # Standalone (without OEM controller present)
        "real_hw": True,  # Set to True if running on real hardware (i.e. Raspberry Pi), False if running in a test environment
        "system_type": "prototype",  # System type / core  (i.e. Raspberry Pi Zero W, Zero 2W, 3A, 3B, 3B+, 4, 5)
    }

    settings["cycle_data"] = {
        "HoldCycleTime": 25,
        "SmokeOnCycleTime": 15,  # Smoke/Startup Auger On Time.
        "SmokeOffCycleTime": 45,  # Smoke/Startup Auger Off Time.  Starting value for PMode (10s is added for each PMode setting)
        "PMode": 2,  # http://tipsforbbq.com/Definition/Traeger-P-Setting
        "u_min": 0.1,
        "u_max": 0.9,
        "LidOpenDetectEnabled": False,  #  Enable Lid Open Detection
        "LidOpenThreshold": 15,  #  Percentage drop in temperature from the hold temp, to trigger lid open event
        "LidOpenPauseTime": 60,  #  Number of seconds to pause when a lid open event is detected
        "FanPidEnabled": False,  #  Enable Fan PID Control (Experimental) - AC or DC fans without PWM control
    }

    settings["controller"] = {"selected": "pid"}

    settings["controller"]["config"] = _default_controller_config()

    settings["display"] = {"selected": "none", "sleep_timeout": 300}
    settings["display"]["config"] = _default_display_config()

    settings["keep_warm"] = {"temp": 165, "s_plus": False}

    settings["smoke_plus"] = {
        "enabled": False,  # Sets default Enable/Disable (True = Enabled, False = Disabled)
        "min_temp": 160,  # Minimum temperature to cycle fan on/off
        "max_temp": 220,  # Maximum temperature to cycle fan on/off
        "on_time": 5,  # Number of seconds the fan will remain ON
        "off_time": 5,  # Number of seconds the fan will remain OFF
        "duty_cycle": 75,  # Duty cycle that will be used during fan ramping. 20-100%
        "fan_ramp": False,  # If enabled fan will ramp up to speed instead of just turning on
    }

    settings["pwm"] = {
        "pwm_control": False,
        "update_time": 10,
        "frequency": 25000,  # PWM Fan Frequency. Intel 4-wire PWM spec specifies 25 kHz
        "min_duty_cycle": 20,  # This is the minimum duty cycle that can be set. Some fans stall below a certain speed
        "max_duty_cycle": 100,  # This is the maximum duty cycle that can be set. Can limit fans that are overpowered
        "temp_range_list": [3, 7, 10, 15],  # Temp Bands for Each Profile
        "profiles": [
            {
                "duty_cycle": 20  # Duty Cycle to set fan
            },
            {"duty_cycle": 35},
            {"duty_cycle": 50},
            {"duty_cycle": 75},
            {"duty_cycle": 100},
        ],
    }

    settings["safety"] = {
        "minstartuptemp": 75,  # User Defined. Minimum temperature allowed for startup.
        "maxstartuptemp": 100,  # User Defined. Take this value if the startup temp is higher than maxstartuptemp
        "maxtemp": 550,  # User Defined. If temp exceeds value in any mode, shut off. (including monitor mode)
        "reigniteretries": 1,  # Number of tries to reignite grill if it has gone below the safe temp (0 to disable)
        "startup_check": True,  # True = Enabled
        "allow_manual_changes": False,  # Allow the user to change outputs manually while grill is running
        "manual_override_time": 30,  # Number of seconds to override the controller with manual changes
    }

    settings["pelletlevel"] = {
        "warning_enabled": True,
        "warning_level": 25,  # Percent to begin low pellet warning notifications
        "warning_time": 20,  # Number of minutes to check for low pellets and send notification
        "empty": 22,  # Number of centimeters from the sensor that indicates empty
        "full": 4,  # Number of centimeters from the sensor that indicates full
    }

    settings["modules"] = {"grillplat": "prototype", "display": "none", "dist": "none"}

    settings["lastupdated"] = {"time": math.trunc(time.time())}

    settings["startup"] = {
        "duration": 240,  # Default startup time (seconds)
        "prime_on_startup": 0,  # Prime Amount (grams) [0 = disabled]
        "startup_exit_temp": 0,  # Exit startup at this temperature threshold. [0 = disabled]
        "start_to_mode": {
            "after_startup_mode": "Smoke",  # Transition to this mode after startup completes
            "primary_setpoint": 165,  # If Hold, set the setpoint
            "start_to_hold_prompt": False,  # If True, always prompt for hold temperature on startup
        },
        "smartstart": {
            "enabled": False,  # Disable Smart Start by default on new installations
            "exit_temp": 120,  # Exit temperature - exits smart start if this temperature is achieved
            "temp_range_list": [60, 80, 90],  # Min Temps for Each Profile
            "profiles": [
                {"startuptime": 360, "augerontime": 15, "p_mode": 0},
                {"startuptime": 360, "augerontime": 15, "p_mode": 1},
                {"startuptime": 240, "augerontime": 15, "p_mode": 3},
                {"startuptime": 240, "augerontime": 15, "p_mode": 5},
            ],
        },
        "pwm_duty_cycle": 100,  # Default PWM duty cycle during startup
    }

    settings["shutdown"] = {
        "shutdown_duration": 240,  # Default Shutdown time (seconds)
        "auto_power_off": False,  # Power off the system after shutdown (False = disabled)
    }

    settings["dashboard"] = _default_dashboard()

    settings["notify_services"] = default_notify_services()

    settings["history_page"] = {
        "minutes": 15,  # Sets default number of minutes to show in history
        "clearhistoryonstart": True,  # Clear history when StartUp Mode selected
        "autorefresh": "on",  # Sets history graph to auto refresh ('live' graph)
        "datapoints": 60,  # Number of data points to show on the history chart
        "probe_config": {},  # Empty probe config
    }
    settings["history_page"]["probe_config"] = default_probe_config(settings)

    settings["recipe"] = {}
    settings["recipe"]["probe_map"] = _default_recipe_probe_map(settings)

    return settings


def _default_dashboard():
    """
    Generate default dashboard settings by getting metadata from each json file in the /dashboard folder
    """
    dash_data = {"current": "Default", "dashboards": {}}
    # Define the folder path
    folder_path = "./dashboard"

    # Loop through files in the folder
    for filename in os.listdir(folder_path):
        # Check if the file is a JSON file
        if filename.endswith(".json"):
            dash_metadata = read_generic_json(os.path.join(folder_path, filename))
            dash_data["dashboards"][dash_metadata["name"]] = {
                "name": dash_metadata["name"],
                "friendly_name": dash_metadata["friendly_name"],
                "html_name": dash_metadata["html_name"],
                "metadata": filename,
                "custom": dash_metadata["custom"],
                "screenshot": dash_metadata.get(
                    "screenshot", ""
                ),  # Use get to avoid KeyError if screenshot is not present
                "config": {},
            }
            for item in dash_metadata["config"]:
                dash_data["dashboards"][dash_metadata["name"]]["config"][item["name"]] = item["default"]

    return dash_data


def _default_controller_config():
    controller_metadata = read_generic_json("./controller/controllers.json")
    config = {}
    for controller in controller_metadata["metadata"]:
        config[controller] = {}
        for option in controller_metadata["metadata"][controller]["config"]:
            config[controller][option["option_name"]] = option["option_default"]

    return config


def _default_display_config():
    display_metadata = read_generic_json("./wizard/wizard_manifest.json")
    display_metadata = display_metadata["modules"]["display"]

    config = {}
    for display in display_metadata:
        config[display] = {}
        for option in display_metadata[display]["config"]:
            config[display][option["option_name"]] = option["default"]

    return config


def _default_recipe_probe_map(settings):
    recipe_probe_map = {"primary": "", "food": []}
    for probe in settings["probe_settings"]["probe_map"]["probe_info"]:
        if probe["type"] == "Primary":
            recipe_probe_map["primary"] = probe["label"]
        elif probe["type"] == "Food":
            recipe_probe_map["food"].append(probe["label"])

    return recipe_probe_map


def default_probe_config(settings):
    """Builds an configuration information for all probes to be used by the history graph"""
    probe_config = {}
    color_index = 0
    for probe in settings["probe_settings"]["probe_map"]["probe_info"]:
        if probe["type"] in ["Primary", "Food"]:
            label = probe["label"]
            # Check if the label exists in settings already.
            if label in settings["history_page"]["probe_config"].keys():
                probe_config[label] = settings["history_page"]["probe_config"][label]
            else:
                probe_config[label] = {
                    "name": probe["name"],
                    "type": probe["type"],
                    "enabled": probe["enabled"],
                    "line_color": COLOR_LIST[color_index][0],
                    "line_color_target": COLOR_LIST[color_index][1],
                    "dash_setpoint": True,
                    "bg_color": COLOR_LIST[color_index][0],
                    "bg_color_target": COLOR_LIST[color_index][1],
                    "fill": False,
                }
                if probe["type"] == "Primary":
                    probe_config[label]["bg_color_setpoint"] = COLOR_LIST[color_index][0]
                    probe_config[label]["line_color_setpoint"] = COLOR_LIST[color_index][0]
            color_index += 1
            # If color index has gotten to the end of the COLOR_LIST, loop back to zero
            if color_index >= len(COLOR_LIST):
                color_index = 0
    return probe_config


def default_notify_services():
    services = {}

    services["apprise"] = {
        "enabled": False,
        "locations": [],  # list of locations
    }

    services["ifttt"] = {
        "enabled": False,
        "APIKey": "",  # API Key for WebMaker IFTTT App notification
    }

    services["pushbullet"] = {
        "enabled": False,
        "APIKey": "",  # API Key for PushBullet notifications
        "PublicURL": "",  # Used in PushBullet notifications
    }

    services["pushover"] = {
        "enabled": False,
        "APIKey": "",  # API Key for Pushover notifications
        "UserKeys": "",  # Comma-separated list of user keys
        "PublicURL": "",  # Used in Pushover notifications
    }

    services["onesignal"] = {"enabled": False, "uuid": generate_uuid(), "app_id": "", "devices": {}}

    services["influxdb"] = {"enabled": False, "url": "", "token": "", "org": "", "bucket": ""}

    services["mqtt"] = {
        "broker": "homeassistant.local",
        "enabled": False,
        "homeassistant_autodiscovery_topic": "homeassistant",
        "id": "PiFire",
        "password": "",
        "port": "1883",
        "update_sec": "30",
        "username": "",
    }

    services["wled"] = {
        "enabled": False,
        "device_address": "wled.local",
        "use_profiles": True,  # Use profile-based control (recommended)
        "use_suggested_presets": False,  # Use PiFire suggested LED behaviors instead of user presets (legacy)
        "profile_numbers": {
            # Default profile numbers for each PiFire state (200+ range to avoid conflicts)
            "idle": 200,
            "booting": 201,
            "preheat": 202,
            "cooking": 203,
            "cooldown": 204,
            "target_reached": 205,
            "overshoot_alarm": 206,
            "probe_alarm": 207,
            "low_pellets": 208,
            "timer_done": 209,
            "error_fault": 210,
            "night_mode": 211,
        },
        "mode_presets": {
            # Legacy traditional presets (kept for backward compatibility)
            "Stop": 1,
            "Startup": 1,
            "Reignite": 1,
            "Smoke": 1,
            "Hold": 1,
            "Shutdown": 1,
            "Prime": 1,
        },
        "event_presets": {
            # Legacy event presets (kept for backward compatibility)
            "Temp_Achieved": 1,
            "Recipe_Next": 1,
            "Grill_Error": 1,
            "Pellet_Level_Low": 1,
            "Timer_Expired": 1,
        },
        "suggested_config": {
            "cooking_color": "blue",  # blue or green
            "idle_brightness": 20,  # percentage (1-100)
            "night_mode": False,  # use dim amber instead of normal colors
            "led_count": 6,  # number of LEDs on the strip
        },
        "notify_duration": 120,  # number of seconds to keep notifications active
    }

    return services


def default_control():
    # Deferred import: default_control() reads settings, and read_settings() ->
    # read_settings_store() in turn falls back to default_settings() from this
    # module -- a genuine mutual dependency in the existing code (a "defaults"
    # builder reaching into the datastore). Importing at module scope would be a
    # circular import; importing here keeps the cycle out of import time.
    # Behavior is unchanged. See Task 8 report.
    from common.datastore_accessors import read_settings

    settings = read_settings()

    control = {}

    control["updated"] = True

    control["mode"] = Mode.STOP

    control["next_mode"] = Mode.STOP

    control["s_plus"] = settings["smoke_plus"]["enabled"]  # Smoke-Plus Feature Enable/Disable

    control["pwm_control"] = settings["pwm"]["pwm_control"]  # Temp Fan Control Enable/Disable

    control["duty_cycle"] = settings["pwm"]["max_duty_cycle"]  # Set PWM Fan Duty Cycle

    control["hopper_check"] = False  # Trigger a synchronous hopper level check

    control["recipe"] = {"filename": "", "start_step": 0, "step": 0, "step_data": {}}

    control["lid_open_toggle"] = False  # Request to set lid_open so that the controller will pause

    control["status"] = StatusState.UNSET

    control["probe_profile_update"] = False

    control["settings_update"] = False

    control["distance_update"] = False

    control["controller_update"] = False  # Used to indicate that the controller config/cycle data has been updated

    control["units_change"] = False  # Used to indicate that a units change has been requested

    control["tuning_mode"] = False  # Used to set tuning mode enabled so Tr values will be recorded (False by default)

    control["safety"] = {
        "startuptemp": 0,  # Set by control function at startup
        "afterstarttemp": 0,  # Set by control function during startup
        "reigniteretries": settings["safety"][
            "reigniteretries"
        ],  # Set by user to attempt a re-ignite when the grill drops below a certain temp
        "reignitelaststate": "Smoke",  # Set by control function to remember the last state we were in when the temp dropped below safety levels
    }

    control["primary_setpoint"] = 0  # Setpoint Temperature for Primary Probe (i.e. Grill Probe)

    control["notify_data"] = default_notify(settings)

    control["timer"] = {"start": 0, "paused": 0, "end": 0, "shutdown": False}

    control["manual"] = {"change": False, "pwm": 100}

    control["smartstart"] = {"startuptemp": 0, "profile_selected": 0}

    control["prime_amount"] = 10  # Default Prime Amount in Grams

    control["startup_timestamp"] = 0  # Timestamp of startup, used for cook time

    control["system"] = {}

    control["critical_error"] = False

    return control


def default_notify(settings):
    notify_data = []
    """ Get list of Probes """

    probe_list = get_probe_list(settings)

    """ Build list of probe notification data """

    for probe in settings["probe_settings"]["probe_map"]["probe_info"]:
        if probe["type"] != "Aux":
            notify_info = {
                "label": probe["label"],
                "name": probe["name"],
                "type": "probe",
                "req": False,
                "target": 0,
                "eta": None,
                "shutdown": False,
                "keep_warm": False,
                "reignite": False,
                "condition": "equal_above",
            }
            notify_data.append(notify_info)

            limit_high = notify_info.copy()  # Copy notify_info object into a new object
            limit_high["type"] = "probe_limit_high"
            limit_high["condition"] = "equal_above"
            limit_high["triggered"] = False
            notify_data.append(limit_high)

            limit_low = notify_info.copy()
            limit_low["type"] = "probe_limit_low"
            limit_low["condition"] = "equal_below"
            limit_low["triggered"] = False
            notify_data.append(limit_low)

    """ Add Timer notification data to list """
    notify_info = {"label": "Timer", "type": "timer", "req": False, "shutdown": False, "keep_warm": False}
    notify_data.append(notify_info)

    """ Add Hopper notification data to list """
    notify_info = {
        "label": "Hopper",
        "type": "hopper",
        "req": settings["pelletlevel"]["warning_enabled"],
        "last_check": 0,
        "shutdown": False,
        "keep_warm": False,
    }
    notify_data.append(notify_info)

    """ Add TEST notification data to list """
    notify_info = {"label": "Test", "type": "test", "req": False, "shutdown": False, "keep_warm": False}
    notify_data.append(notify_info)

    return notify_data


"""
List of Tuples ('metric_key', default_value)
 - This structure will be used to build the default metrics structure, and to export the data easily
 - To add a metric, simply add a tuple to this list.  
"""
metrics_items = [
    ("id", 0),
    ("starttime", 0),
    ("starttime_c", 0),  # Converted Start Time
    ("endtime", 0),
    ("endtime_c", 0),  # Converted End Time
    ("timeinmode", 0),  # Calculated Time in Mode
    ("mode", ""),
    ("augerontime", 0),
    ("augerontime_c", 0),  # Converted Auger On Time
    ("estusage_m", ""),  # Estimated pellet usage in metric (grams)
    ("estusage_i", ""),  # Estimated pellet usage in pounds (and ounces)
    ("fanontime", 0),
    ("fanontime_c", 0),  # Converted Fan On Time
    ("smokeplus", True),
    ("primary_setpoint", 0),
    ("smart_start_profile", 0),  # Smart Start Profile Selected
    ("startup_temp", 0),  # Smart Start Start Up Temp
    ("p_mode", 0),  # P_mode selected
    ("auger_cycle_time", 0),  # Auger Cycle Time
    ("pellet_level_start", 0),  # Pellet Level at the begining of this mode
    ("pellet_level_end", 0),  # Pellet Level at the end of this mode
    ("pellet_brand_type", ""),  # Pellet Brand and Wood Type
]

# The columnar `metrics` table's columns, in order -- built generically from
# metrics_items so the SQL column list can't drift out of sync with it.
METRIC_COLUMNS = [k for k, _ in metrics_items]


def default_metrics():
    metrics = {}

    for index in range(0, len(metrics_items)):
        metrics[metrics_items[index][0]] = metrics_items[index][1]

    return metrics


def default_pellets():
    pelletdb = {}

    now = str(datetime.datetime.now())
    now = now[0:19]  # Truncate the microseconds

    ID = "".join(filter(str.isalnum, str(datetime.datetime.now())))

    pelletdb["current"] = {
        "pelletid": ID,  # Pellet ID for the profile currently loaded
        "hopper_level": 100,  # Percentage of pellets remaining
        "date_loaded": now,  # Date that current pellets loaded
        "est_usage": 0,  # Estimated usage since loading (use auger load rate, and auger on time)
    }

    pelletdb["woods"] = [
        "Alder",
        "Almond",
        "Apple",
        "Apricot",
        "Blend",
        "Competition",
        "Cherry",
        "Chestnut",
        "Hickory",
        "Lemon",
        "Maple",
        "Mesquite",
        "Mulberry",
        "Nectarine",
        "Oak",
        "Orange",
        "Peach",
        "Pear",
        "Plum",
        "Walnut",
    ]

    pelletdb["brands"] = ["Generic", "Custom"]

    pelletdb["archive"] = {
        ID: {
            "id": ID,
            "brand": "Generic",
            "wood": "Alder",
            "rating": 4,
            "comments": "This is a placeholder profile.  Alder is generic and used in almost all pellets, "
            "regardless of the wood type indicated on the packaging.  It tends to burn "
            "consistently and produces a mild smoke.",
        }
    }

    pelletdb["log"] = {now: ID}

    pelletdb["lastupdated"] = {"time": math.trunc(time.time())}

    return pelletdb


def _default_probe_profiles():

    probes_json = read_generic_json("./probes/probes.json")
    probe_profiles = probes_json["profiles"]

    return probe_profiles


def default_probe_map(probe_profiles):

    probe_devices = []

    device = {
        "device": "proto_adc",  # Unique name for the device
        "module": "prototype",  # Module to support the hardware device
        "module_filename": "prototype",  # Filename of the module to load
        "ports": [
            "ADC0",
            "ADC1",
            "ADC2",
            "ADC3",
        ],  # Optionally define ports, otherwise, leave this up to the module to define
        "config": {
            "ADC0_rd": "10000",
            "ADC1_rd": "10000",
            "ADC2_rd": "10000",
            "ADC3_rd": "10000",
            "i2c_bus_addr": "0x48",
            "voltage_ref": "3.28",
        },  # Configuration data to pass to the module
    }

    probe_devices.append(device)

    probe_info = []

    grill_probe = {
        "type": "Primary",
        "label": "Grill",
        "name": "Grill",
        "profile": probe_profiles["99b8f02d-233d-11ee-a7a2-e5396c02c5fd"],
        "device": "proto_adc",
        "port": "ADC0",
        "enabled": True,
    }

    probe_info.append(grill_probe)

    for index in range(1, 4):
        name = f"Probe-{index}"
        label = "".join([x for x in name if x.isalnum()])
        # safe_label = "".join([x for x in name if x.isalnum()])
        probe = {
            "type": "Food",
            "label": label,
            "name": name,
            "profile": probe_profiles["TWPS00"],
            "device": "proto_adc",
            "port": f"ADC{index}",
            "enabled": True,
        }
        probe_info.append(probe)

    probe_map = {"probe_devices": probe_devices, "probe_info": probe_info}

    return probe_map
