"""
*****************************************
PiFire Flexible Display Interface Library
*****************************************

 Description:
   This is a base class for displays, with
 a modular/flexible display size and layout.
 Other display libraries will inherit this
 base class and add device specific features.

*****************************************
"""

"""
 Imported Libraries
"""
import logging
import os
import socket
import time

import requests
from PIL import Image, ImageFilter

from common.common import display_sleep_timeout, read_generic_json
from common.control_delta import control_delta
from common.modes import Mode
from common.persistence.control import (
    enqueue_control_delta,
    read_control,
)
from common.persistence.runtime import (
    read_current,
    read_settings,
    read_status,
    write_settings,
)
from common.system import is_real_hardware
from display._loggers import resolve_loggers

# The widget classes below are never referenced by literal name in this file;
# _build_objects() resolves them dynamically via
# globals()[FlexObject_TypeMap[object_data["type"]]], so every class named as
# a FlexObject_TypeMap value must be present in this module's globals().
from display.flexobject import (
    AlertMessage,  # noqa: F401  # dynamic-dispatch
    ButtonRow,  # noqa: F401  # dynamic-dispatch
    ControlPanel,  # noqa: F401  # dynamic-dispatch
    CookTimeBar,  # noqa: F401  # dynamic-dispatch
    DutyPill,  # noqa: F401  # dynamic-dispatch
    FlexButton,  # noqa: F401  # dynamic-dispatch
    FlexObject_TypeMap,
    GaugeCircle,  # noqa: F401  # dynamic-dispatch
    GaugeCompact,  # noqa: F401  # dynamic-dispatch
    GaugeEmber,  # noqa: F401  # dynamic-dispatch
    HeaderBar,  # noqa: F401  # dynamic-dispatch
    HopperStatus,  # noqa: F401  # dynamic-dispatch
    HopperVertical,  # noqa: F401  # dynamic-dispatch
    InputNumber,  # noqa: F401  # dynamic-dispatch
    InputNumberSimple,  # noqa: F401  # dynamic-dispatch
    MenuGeneric,  # noqa: F401  # dynamic-dispatch
    MenuIcon,  # noqa: F401  # dynamic-dispatch
    MenuQRCode,  # noqa: F401  # dynamic-dispatch
    ModeBar,  # noqa: F401  # dynamic-dispatch
    PModeStatus,  # noqa: F401  # dynamic-dispatch
    ProbeCard,  # noqa: F401  # dynamic-dispatch
    SPlusStatus,  # noqa: F401  # dynamic-dispatch
    StatusIcon,  # noqa: F401  # dynamic-dispatch
    SystemCard,  # noqa: F401  # dynamic-dispatch
    TimerStatus,  # noqa: F401  # dynamic-dispatch
    resolve_accent,
)
from display.staleness import resolve_reading

"""
==================================================================================
Display base class definition
==================================================================================
"""


# FlexObject types added for the "ember" dashboard redesign.
# Objects of these types receive the resolved accent palette at build time.
NEW_EMBER_FLEX_TYPES = {
    "probe_card",
    "gauge_ember",
    "system_card",
    "duty_pill",
    "hopper_vertical",
    "header_bar",
    "button_row",
}


class DisplayBase:
    def __init__(
        self, dev_pins, buttonslevel="HIGH", rotation=0, units="F", config=None, *, event_log=None, control_log=None
    ):
        config = {} if config is None else config
        # Init Global Variables and Constants
        self.config = config

        # Ember dashboard accent palette, used by the new flex object types.
        self.accent = resolve_accent(self.config.get("accent_theme", "Ember"))

        self.dev_pins = dev_pins
        self.units = units

        self.in_data = None
        self.last_in_data = {}
        self.status_data = None
        self.last_status_data = {}

        self.input_enabled = False
        self.input_origin = None
        self.input_button = "button" in self.config.get("input_types_supported", [])
        self.input_encoder = "encoder" in self.config.get("input_types_supported", [])
        self.input_touch = "touch" in self.config.get("input_types_supported", [])

        self.display_active = None
        self.display_timeout = None
        self.TIMEOUT = display_sleep_timeout(read_settings())
        self.command = "splash"
        self.command_data = None

        self.real_hardware = bool(is_real_hardware())
        # Attempt to set the log level of PIL so that it does not pollute the logs
        logging.getLogger("PIL").setLevel(logging.CRITICAL + 1)

        # Setup loggers: both are handed in by build_display() (the display
        # process configures them), and each is named for the log it writes to
        # -- operator-facing messages to events.log, diagnostics to control.log.
        self.eventLogger, self.controlLogger = resolve_loggers(event_log, control_log)
        # Init Display Device, Input Device, Assets
        self._init_globals()
        self._init_framework()
        self._init_display_canvas()
        self._init_input()
        self._init_display_device()

    def _init_globals(self):
        # Init constants and variables
        self.buttonslevel = self.config.get("buttonslevel", "HIGH")
        if self.display_profile == None:
            self.display_profile = self.config.get("default_profile", "profile_1")

        """ Get Local IP Address """
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1)
            # doesn't even have to be reachable
            s.connect(("10.254.254.254", 1))
            self.ip_address = s.getsockname()[0]
        except Exception:
            self.ip_address = "127.0.0.1"
            self.eventLogger.error("Unable to get IP address of the system.")
        finally:
            s.close()

    def _init_framework(self):
        """
        Initialize the dash/home/menu framework
        """
        self.display_data = read_generic_json(self.config["display_data_filename"])
        self.ROTATION = self.config.get("rotation", 0)
        if self.ROTATION in [0, 180, 2]:
            self.WIDTH = self.display_data["metadata"].get("screen_width", 800)
            self.HEIGHT = self.display_data["metadata"].get("screen_height", 480)
        else:
            self.WIDTH = self.display_data["metadata"].get("screen_height", 480)
            self.HEIGHT = self.display_data["metadata"].get("screen_width", 800)
        self.SPLASH_DELAY = self.display_data["metadata"].get("splash_delay", 1000)
        self.FRAMERATE = self.display_data["metadata"].get("framerate", 30)
        if self.display_data[self.display_profile].get("home", []) == []:
            self.HOME_ENABLED = False
        else:
            self.HOME_ENABLED = True
        self.display_data[self.display_profile]["menus"]["qrcode"]["ip_address"] = self.ip_address

        self._fixup_display_data()

        self._init_assets()

    def _fixup_display_data(self):
        for index, object in enumerate(self.display_data[self.display_profile]["home"]):
            for key in list(object.keys()):
                if key in [
                    "position",
                    "size",
                    "fg_color",
                    "bg_color",
                    "color",
                    "active_color",
                    "inactive_color",
                    "sp_color",
                    "np_color",
                ]:
                    self.display_data[self.display_profile]["home"][index][key] = tuple(object[key])
        for index, object in enumerate(self.display_data[self.display_profile]["dash"]):
            # print(f'Object Name: {object["name"]}')
            for key in list(object.keys()):
                if key in [
                    "position",
                    "size",
                    "fg_color",
                    "bg_color",
                    "color",
                    "active_color",
                    "inactive_color",
                    "sp_color",
                    "np_color",
                ]:
                    # print(f'[{key}] = {object[key]}')
                    self.display_data[self.display_profile]["dash"][index][key] = tuple(object[key])
                    # print(f'converted = {tuple(object[key])}')
                if key in ["color_levels"]:
                    color_level_list = []
                    for item in self.display_data[self.display_profile]["dash"][index][key]:
                        color_level_list.append(tuple(item))
                    self.display_data[self.display_profile]["dash"][index][key] = color_level_list
                if key in ["units"]:
                    """ Ensure we start with the right units displayed """
                    self.display_data[self.display_profile]["dash"][index][key] = self.units
        for menu, object in self.display_data[self.display_profile]["menus"].items():
            for key in list(object.keys()):
                if key in [
                    "position",
                    "size",
                    "fg_color",
                    "bg_color",
                    "color",
                    "active_color",
                    "inactive_color",
                    "sp_color",
                    "np_color",
                ]:
                    self.display_data[self.display_profile]["menus"][menu][key] = tuple(object[key])
        for input, object in self.display_data[self.display_profile]["input"].items():
            for key in list(object.keys()):
                if key in [
                    "position",
                    "size",
                    "fg_color",
                    "bg_color",
                    "color",
                    "active_color",
                    "inactive_color",
                    "sp_color",
                    "np_color",
                ]:
                    self.display_data[self.display_profile]["input"][input][key] = tuple(object[key])
        # print(f'Fixed Up: \n{self.display_data[self.display_profile]["menus"]}')

    def _init_display_device(self):
        """
        Inheriting classes will override this function to init the display device and start the display thread.
        """

    def _init_display_canvas(self):
        """
        Setup display canvas to be used by PIL to display objects
        """
        self.display_canvas = Image.new("RGBA", (self.WIDTH, self.HEIGHT))

    def _init_input(self):
        """
        Inheriting classes will override this function to setup the inputs.
        """
        self.input_enabled = False  # If the inheriting class does not implement input, then clear this flag
        self.input_counter = 0

    def _display_loop(self):
        """
        Main display loop
        """
        while True:
            time.sleep(0.1)

    def _zero_dash_data(self):
        # self.last_in_data = {}
        # self.last_status_data = {}
        if self.status_data is not None or self.in_data is not None:
            self.status_data["mode"] = Mode.STOP
            for outpin in self.status_data["outpins"]:
                if outpin != "pwm":
                    self.status_data["outpins"][outpin] = False
            for probe in self.in_data["P"]:
                self.in_data["P"][probe] = 0
            for probe in self.in_data["F"]:
                self.in_data["F"][probe] = 0
            for probe in self.in_data["AUX"]:
                self.in_data["AUX"][probe] = 0

            self.in_data["PSP"] = 0

            for probe in self.in_data["NT"]:
                self.in_data["NT"][probe] = 0

    def _store_dash_objects(self):
        """Store the dash object list so that it does not need to be rebuilt"""
        self.dash_object_list = self.display_object_list.copy()

    def _restore_dash_objects(self):
        """Restore the dash object list to the main working display_object_list"""
        self.display_object_list = self.dash_object_list.copy()

    """
    ============== Input Callbacks ============= 
    
    Inheriting classes will override these functions for all inputs.
    """

    def _enter_callback(self):
        """
        Inheriting classes will override this function.
        """

    def _up_callback(self, held=False):
        """
        Inheriting classes will override this function to clear the display device.
        """

    def _down_callback(self, held=False):
        """
        Inheriting classes will override this function to clear the display device.
        """

    """
    ============== Graphics / Display / Draw Methods ============= 
    """

    def _init_assets(self):
        self._init_background()
        self._init_splash()

    def _init_background(self):
        background_image_path = self.display_data["metadata"]["dash_background"]
        self.background = Image.open(background_image_path)
        self.background = self.background.resize((self.WIDTH, self.HEIGHT))

    def _init_splash(self):
        splash_image_path = self.display_data["metadata"]["splash_image"]
        self.splash = Image.open(splash_image_path)
        width, height = self.splash.size
        if width > self.WIDTH or height > self.HEIGHT:
            # Scale the splash image to fit the display
            # Calculate the scaling factor based on aspect ratio
            ratio_w = self.WIDTH / float(width)
            ratio_h = self.HEIGHT / float(height)
            ratio = min(ratio_w, ratio_h)

            # Apply the scaling factor
            new_width = int(width * ratio)
            new_height = int(height * ratio)
            self.splash = self.splash.resize((new_width, new_height))

    def _wake_display(self):
        """
        Inheriting classes will override this function to wake the display device.
        """

    def _sleep_display(self):
        """
        Inheriting classes will override this function to sleep the display device.
        """

    def _display_clear(self):
        self.display_canvas.paste((0, 0, 0, 255), (0, 0, self.WIDTH, self.HEIGHT))

    def _display_canvas(self, canvas):
        """
        Inheriting classes will override this function to show the canvas on the display device.
        """

    def _display_splash(self):
        width, height = self.splash.size
        self.display_canvas.paste(self.splash, ((self.WIDTH // 2) - (width // 2), (self.HEIGHT // 2) - (height // 2)))
        self._display_canvas()

    def _display_background(self):
        """
        Inheriting classes will override this function to display the stored background image.
        """

    def _display_menu_background(self):
        """
        Inheriting classes will override this function to display menu background
        """

    def _build_objects(self, background=None):
        """
        Inheriting classes may override this function to ensure the right object type is loaded
        """
        self.display_object_list = []

        if self.display_active in ["home", "dash"]:
            section_data = self.display_data[self.display_profile][self.display_active]
        elif "menu_" in self.display_active:
            section_data = [self.display_data[self.display_profile]["menus"][self.display_active.replace("menu_", "")]]
        elif "input_" in self.display_active:
            section_data = [self.display_data[self.display_profile]["input"][self.display_active.replace("input_", "")]]
            section_data[0]["data"]["origin"] = self.input_origin
        else:
            return

        for object_data in section_data:
            """ Add the object to the display object list """

            if self.status_data.get("hopper_level_enabled", False) == False and object_data["type"] == "hopper_status":
                # Hopper Status is not enabled
                continue
            if self.status_data.get("hopper_level", None) != None and object_data["type"] == "hopper_status":
                object_data["data"]["level"] = self.status_data[
                    "hopper_level"
                ]  # Add the current hopper level to the object

            if (
                self.status_data.get("hopper_level_enabled", False) == False
                and object_data["type"] == "hopper_vertical"
            ):
                # Hopper level reporting is not enabled - skip the ember hopper card entirely
                continue
            if self.status_data.get("hopper_level", None) != None and object_data["type"] == "hopper_vertical":
                object_data.setdefault("data", {})
                object_data["data"]["level"] = self.status_data["hopper_level"]
                object_data["data"]["enabled"] = self.status_data.get("hopper_level_enabled", False)

            if object_data["type"] in NEW_EMBER_FLEX_TYPES:
                # Inject the resolved accent palette for the new ember dash objects
                object_data["accent"] = self.accent

            FlexObject_ClassName = FlexObject_TypeMap[object_data["type"]]
            FlexObject_Constructor = globals()[FlexObject_ClassName]
            self.display_object_list.append(FlexObject_Constructor(object_data["type"], object_data, background))

    def _configure_dash(self):
        """Build Food Probe Map"""
        num_food_probes = min(len(self.config["probe_info"]["food"]), self.display_data["metadata"]["max_food_probes"])
        self.food_probe_label_map = {}
        self.food_probe_name_map = {}
        # Ember dashboard probe_card_N slots mirror the food_probe_gauge_N mapping above.
        self.probe_card_label_map = {}
        self.probe_card_name_map = {}
        for index in range(num_food_probes):
            self.food_probe_label_map[f"food_probe_gauge_{index}"] = self.config["probe_info"]["food"][index]["label"]
            self.food_probe_name_map[f"food_probe_gauge_{index}"] = self.config["probe_info"]["food"][index]["name"]
            self.probe_card_label_map[f"probe_card_{index}"] = self.config["probe_info"]["food"][index]["label"]
            self.probe_card_name_map[f"probe_card_{index}"] = self.config["probe_info"]["food"][index]["name"]

        """ Remove Unused Food Probes & Rename Used Food Probes"""
        display_data_dash_list = []
        for object in self.display_data[self.display_profile]["dash"]:
            if "food_probe_gauge_" in object["name"] and object["name"] not in list(self.food_probe_label_map.keys()):
                pass
            elif "probe_card_" in object["name"] and object["name"] not in list(self.probe_card_label_map.keys()):
                # Hide any probe_card slot with no configured food probe
                pass
            else:
                if "food_probe_gauge_" in object["name"]:
                    """ Rename Displayed Food Probes """
                    object["label"] = self.food_probe_name_map[object["name"]]
                    object["units"] = self.units
                    object["button_value"] = [object["label"]]
                elif "probe_card_" in object["name"]:
                    """ Rename Displayed Probe Cards """
                    object.setdefault("data", {})
                    object["data"]["name"] = self.probe_card_name_map[object["name"]]
                    object["units"] = self.units
                elif object["name"] == "primary_gauge":
                    object["label"] = self.config["probe_info"]["primary"]["name"]
                    object["button_value"] = [object["label"]]

                display_data_dash_list.append(object)

        self.display_data[self.display_profile]["dash"] = display_data_dash_list

    """
    ============== Ember Dashboard Helpers =============

    Pure(ish) helpers that compute the live-data payload for the new ember
    flex objects. Kept as staticmethods so they can be unit-tested directly
    without constructing a full DisplayBase (which requires pygame/config).
    """

    @staticmethod
    def _button_row_for_mode(mode, recipe, recipe_paused):
        """Returns (button_type, button_list, button_active) for the button_row
        object, mirroring the existing control_panel branch and Qt's
        Menus.controlPanelForMode (display/qml/Menus.js)."""
        if recipe and mode != Mode.SHUTDOWN:
            type_item = "Error"
            if mode in (Mode.STARTUP, Mode.REIGNITE):
                type_item = "Startup"
            elif mode == Mode.SMOKE:
                type_item = "Smoke"
            elif mode == Mode.HOLD:
                type_item = "Hold"
            button_type = ["Next", type_item, "Stop", "Shutdown"]
            button_list = ["cmd_next_step", "cmd_none", "cmd_stop", "cmd_shutdown"]
            button_active = "Next" if recipe_paused else mode
            return button_type, button_list, button_active

        if mode in (Mode.STARTUP, Mode.REIGNITE):
            return ["Startup", "Smoke", "Hold", "Stop"], ["cmd_startup", "cmd_smoke", "input_hold", "cmd_stop"], mode
        if mode == Mode.SMOKE:
            return (
                ["Set Temp", "Hold", "Stop", "Shutdown"],
                ["input_hold", "input_hold", "cmd_stop", "cmd_shutdown"],
                mode,
            )
        if mode == Mode.HOLD:
            return (
                ["Set Temp", "Smoke", "Stop", "Shutdown"],
                ["input_hold", "cmd_smoke", "cmd_stop", "cmd_shutdown"],
                mode,
            )
        if mode == Mode.SHUTDOWN:
            return (
                ["Smoke", "Hold", "Stop", "Shutdown"],
                ["cmd_smoke", "input_hold", "cmd_stop", "cmd_shutdown"],
                mode,
            )
        # Stop / Prime / Monitor
        return ["Prime", "Startup", "Monitor", "Stop"], ["menu_prime", "menu_startup", "cmd_monitor", "cmd_stop"], mode

    @staticmethod
    def _duty_pills(status_data):
        """Returns (left_data, right_data) dicts for duty_pill_left/duty_pill_right:
        P-MODE/SMOKE+ while Smoke is active, otherwise AUGER/FAN DUTY.

        P-mode and Smoke+ describe the smoke cycle, so they are shown only
        where that cycle is what the grill is doing; everywhere else they
        reported settings governing nothing running. The duties are true in
        every mode. P-mode stays reachable from the PMode menu.
        """
        mode = status_data.get("mode", Mode.STOP)
        outpins = status_data.get("outpins", {})
        if mode == Mode.SMOKE:
            left = {"label": "P-MODE", "value": f"P-{status_data.get('p_mode', 0)}", "highlight": False}
            s_plus = bool(status_data.get("s_plus", False))
            right = {"label": "SMOKE+", "value": "ON" if s_plus else "OFF", "highlight": s_plus}
        else:
            left = {
                "label": "AUGER DUTY",
                "value": f"{round(status_data.get('cycle_ratio', 0) * 100)}%",
                "highlight": False,
            }
            right = {
                "label": "FAN DUTY",
                "value": f"{round(status_data.get('fan_duty', 0))}%",
                "highlight": bool(outpins.get("fan", False)),
            }
        return left, right

    @staticmethod
    def _timer_seconds_and_label(status_data, now):
        """Mirrors the countdown computation used by the legacy 'timer' dash
        branch (Prime/Startup/Reignite/Shutdown countdown, or Hold lid-open
        pause countdown). Returns (seconds, label); seconds is 0 and label is
        '' when no countdown is active."""
        mode = status_data.get("mode", Mode.STOP)
        if mode in (Mode.PRIME, Mode.STARTUP, Mode.REIGNITE, Mode.SHUTDOWN):
            if mode in (Mode.STARTUP, Mode.REIGNITE):
                duration = status_data.get("start_duration", 0)
            elif mode == Mode.PRIME:
                duration = status_data.get("prime_duration", 0)
            else:
                duration = status_data.get("shutdown_duration", 0)
            countdown = int(duration - (now - status_data.get("start_time", now)))
            return max(countdown, 0), "Timer"
        elif mode == Mode.HOLD and status_data.get("lid_open_detected"):
            countdown = int(status_data.get("lid_open_endtime", now) - now)
            return max(countdown, 0), "Lid Pause"
        return 0, ""

    @staticmethod
    def _cook_time_data(status_data, now):
        """Returns {'label':..., 'value':...} for the cook_time object:
        the active countdown (mm:ss) when a timer is running, else the
        elapsed cook time (H:MM:SS) computed from startup_timestamp, mirroring
        display/qtbackend.py's _update_timer_text/_update_cook_elapsed.

        NOTE: the not-yet-built 1280x720 bespoke layout has not yet defined
        the FlexObject type/contract for the 'cook_time' object name. This
        shape (data.label/data.value) matches the duty_pill contract as the
        closest existing analog; if that layout instead reuses the 'timer'
        type (TimerStatus), that widget reads top-level 'label' and
        data['seconds'] rather than data['value'], so _update_dash_objects
        also mirrors 'label' at the top level for compatibility. This should
        be revisited once that layout lands.
        """
        seconds, label = DisplayBase._timer_seconds_and_label(status_data, now)
        if seconds > 0:
            value = f"{seconds // 60:02d}:{seconds % 60:02d}"
            return {"label": label, "value": value}

        timestamp = status_data.get("startup_timestamp", 0) or 0
        mode = status_data.get("mode", Mode.STOP)
        if timestamp and mode not in (Mode.STOP, Mode.MONITOR):
            elapsed = max(int(now - timestamp), 0)
            hours, minutes, secs = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
            value = (f"{hours}:" if hours else "") + f"{minutes:02d}:{secs:02d}"
        else:
            value = "00:00"
        return {"label": "COOK TIME", "value": value}

    def _build_dash_map(self):
        """Setup dash object mapping"""
        self.dash_map = {}

        # print('Setting up Dash Map:')
        for index, object in enumerate(self.display_object_list):
            objectData = object.get_object_data()
            self.dash_map[objectData["name"]] = index
            # print(f' - Index: {index}, Maps to: {objectData["name"]}')

    def _update_dash_objects(self):
        if self.in_data is not None and self.status_data is not None:
            self._update_mode_change_group()
            self._update_header_bar()
            self._update_gauges_and_cards()
            self._update_output_icons()
            self._update_system_card()
            self._update_timer()
            self._update_cook_time()
            self._update_lid_indicator()
            self._update_lid_alert()
            self._update_lid_open_button()
            self._update_p_mode()
            self._update_smoke_plus()
            self._update_hopper()
            self._update_hopper_vertical()
            self._update_duty_pills()

            """ After all the updates, update the last states/data """
            self.last_in_data = self.in_data.copy()
            self.last_status_data = self.status_data.copy()

    def _update_mode_change_group(self):
        """Update Mode Bar and Control Panel (only on mode / recipe_paused transitions)"""
        if not (
            (self.status_data["mode"] != self.last_status_data.get("mode", "None"))
            or (self.status_data["recipe_paused"] != self.last_status_data.get("recipe_paused", "None"))
        ):
            return

        """ Disable Screen Timeout When not in Stop Mode """
        if self.status_data["mode"] not in [Mode.STOP]:
            self.display_timeout = None
        else:
            self.display_timeout = time.time() + self.TIMEOUT

        self._update_mode_bar()
        self._update_control_panel()
        self._update_button_row()
        self._update_primary_gauge_mode_label()
        self._update_lid_open_button_mode()

    def _update_mode_bar(self):
        """Mode Bar Update"""
        if "mode_bar" in self.dash_map:
            object_data = self.display_object_list[self.dash_map["mode_bar"]].get_object_data()

            if self.status_data["recipe"] and self.status_data["mode"] != Mode.SHUTDOWN:
                object_data["text"] = "Recipe: " + self.status_data["mode"]
            else:
                object_data["text"] = self.status_data["mode"]
            self.display_object_list[self.dash_map["mode_bar"]].update_object_data(object_data)

    def _update_control_panel(self):
        """Control Panel Update"""
        if "control_panel" in self.dash_map:
            object_data = self.display_object_list[self.dash_map["control_panel"]].get_object_data()
            object_data["button_active"] = self.status_data["mode"]
            if self.status_data["recipe"]:
                """ Recipe Mode """
                list_item = "cmd_none"
                type_item = "Error"
                if self.status_data["mode"] in [Mode.STARTUP, Mode.REIGNITE]:
                    type_item = "Startup"
                elif self.status_data["mode"] == Mode.SMOKE:
                    type_item = "Smoke"
                elif self.status_data["mode"] == Mode.HOLD:
                    type_item = "Hold"
                elif self.status_data["mode"] == Mode.SHUTDOWN:
                    type_item = "None"
                object_data["button_list"] = ["cmd_next_step", list_item, "cmd_stop", "cmd_shutdown"]
                object_data["button_type"] = ["Next", type_item, "Stop", "Shutdown"]
                if self.status_data["recipe_paused"]:
                    object_data["button_active"] = "Next"
            elif self.status_data["mode"] in [Mode.STARTUP, Mode.REIGNITE]:
                """ Startup Mode """
                object_data["button_list"] = ["cmd_startup", "cmd_smoke", "input_hold", "cmd_stop"]
                object_data["button_type"] = ["Startup", "Smoke", "Hold", "Stop"]
            elif self.status_data["mode"] in [Mode.SMOKE, Mode.HOLD, Mode.SHUTDOWN]:
                """ Smoke, Hold or Shutdown Modes """
                object_data["button_list"] = ["cmd_smoke", "input_hold", "cmd_stop", "cmd_shutdown"]
                object_data["button_type"] = ["Smoke", "Hold", "Stop", "Shutdown"]
            else:
                """ Stopped, Prime, Monitor Modes """
                object_data["button_list"] = ["menu_prime", "menu_startup", "cmd_monitor", "cmd_stop"]
                object_data["button_type"] = ["Prime", "Startup", "Monitor", "Stop"]

            self.display_object_list[self.dash_map["control_panel"]].update_object_data(object_data)

    def _update_button_row(self):
        """Button Row Update (ember dash)"""
        if "button_row" in self.dash_map:
            object_data = self.display_object_list[self.dash_map["button_row"]].get_object_data()
            button_type, button_list, button_active = self._button_row_for_mode(
                self.status_data["mode"], self.status_data["recipe"], self.status_data["recipe_paused"]
            )
            object_data["button_type"] = button_type
            object_data["button_list"] = button_list
            object_data["button_active"] = button_active
            self.display_object_list[self.dash_map["button_row"]].update_object_data(object_data)

    def _update_primary_gauge_mode_label(self):
        """Primary Gauge Mode Label Update (ember dash - gauge_ember)"""
        if "primary_gauge" in self.dash_map:
            object_data = self.display_object_list[self.dash_map["primary_gauge"]].get_object_data()
            object_data.setdefault("data", {})
            object_data["data"]["mode_label"] = self.status_data["mode"].upper()
            self.display_object_list[self.dash_map["primary_gauge"]].update_object_data(object_data)

    def _update_lid_open_button_mode(self):
        """Lid Open Button Update"""
        if "lid_open_button" in self.dash_map and self.status_data["mode"] == Mode.HOLD:
            object_data = self.display_object_list[self.dash_map["lid_open_button"]].get_object_data()
            if self.status_data.get("lid_open_detected", False):
                object_data["active"] = True
            else:
                object_data["active"] = False
            self.display_object_list[self.dash_map["lid_open_button"]].update_object_data(object_data)

    def _update_header_bar(self):
        """Header Bar Update (ember dash) - clock/IP/live-cooking-dot, independent of mode change"""
        if "header_bar" in self.dash_map:
            object_data = self.display_object_list[self.dash_map["header_bar"]].get_object_data()
            object_data.setdefault("data", {})
            new_clock = time.strftime("%H:%M")
            new_cooking = self.status_data["mode"] in (
                Mode.STARTUP,
                Mode.REIGNITE,
                Mode.SMOKE,
                Mode.HOLD,
                Mode.RECIPE,
            )
            if (
                object_data["data"].get("clock") != new_clock
                or object_data["data"].get("ip") != self.ip_address
                or object_data["data"].get("cooking") != new_cooking
            ):
                object_data["data"]["ip"] = self.ip_address
                object_data["data"]["clock"] = new_clock
                object_data["data"]["cooking"] = new_cooking
                self.display_object_list[self.dash_map["header_bar"]].update_object_data(object_data)

    def _update_gauges_and_cards(self):
        if self.last_in_data == {}:
            return
        self._update_primary_gauge_values()
        self._update_food_gauges()
        self._update_probe_cards()

    def _update_primary_gauge_values(self):
        """Update Primary Gauge Values

        Compared against what the gauge is showing rather than against the
        previous frame, for the same reason as the probe cards: a pit probe
        that stays unavailable reports the same thing every frame while its
        age keeps climbing.
        """
        primary_key = next(iter(self.in_data["P"].keys()))  # Get the key for the primary gauge
        now_ms = int(time.time() * 1000)
        temp, has_temp, stale = resolve_reading(
            self.in_data["P"][primary_key], self.in_data.get("LAST", {}).get(primary_key), now_ms
        )
        object_data = self.display_object_list[self.dash_map["primary_gauge"]].get_object_data()
        temps = [temp, self.in_data["NT"][primary_key], self.in_data["PSP"]]
        if (
            object_data["temps"] == temps
            and object_data.get("hasTemp") == has_temp
            and object_data.get("stale") == stale
        ):
            return
        """ Update the Primary Gauge """
        object_data["temps"][0] = temp
        object_data["temps"][1] = self.in_data["NT"][primary_key]
        object_data["temps"][2] = self.in_data["PSP"]
        object_data["hasTemp"] = has_temp
        object_data["stale"] = stale
        object_data["units"] = self.units
        # object_data['label'] = primary_key
        self.display_object_list[self.dash_map["primary_gauge"]].update_object_data(object_data)

    def _update_food_gauges(self):
        """Update Food Probe Gauges and Values"""
        food_gauge_keys = list(self.food_probe_label_map.keys())
        for gauge in food_gauge_keys:
            if gauge not in self.dash_map:
                # Layouts built entirely around probe_card_N (ember dash) have no
                # food_probe_gauge_N objects at all - nothing to update here.
                continue
            key = self.food_probe_label_map[gauge]
            if (
                self.last_in_data["F"][key] != self.in_data["F"][key]
                or self.last_in_data["NT"][key] != self.in_data["NT"][key]
            ):
                """ Update this food gauge """
                object_data = self.display_object_list[self.dash_map[gauge]].get_object_data()
                object_data["temps"][0] = self.in_data["F"][key] if self.in_data["F"][key] is not None else 0
                object_data["temps"][1] = self.in_data["NT"][key]
                object_data["temps"][2] = 0  # There is no set temp for food probes
                object_data["units"] = self.units
                self.display_object_list[self.dash_map[gauge]].update_object_data(object_data)

    def _update_probe_cards(self):
        """Update Probe Cards (ember dash)

        Compared against what the card is already showing rather than against
        the previous frame's readings: a probe that stays unavailable produces
        the same reading every frame while its age keeps climbing, so a gate on
        the readings alone would freeze the staleness line at the age it had
        when the probe went quiet.
        """
        now_ms = int(time.time() * 1000)
        last_readings = self.in_data.get("LAST", {})
        for card in list(self.probe_card_label_map.keys()):
            if card not in self.dash_map:
                continue
            key = self.probe_card_label_map[card]
            temp, has_temp, stale = resolve_reading(self.in_data["F"][key], last_readings.get(key), now_ms)
            target = self.in_data["NT"][key] if self.in_data["NT"][key] is not None else 0
            object_data = self.display_object_list[self.dash_map[card]].get_object_data()
            object_data.setdefault("data", {})
            shown = object_data["data"]
            if (shown.get("temp"), shown.get("hasTemp"), shown.get("stale"), shown.get("target")) == (
                temp,
                has_temp,
                stale,
                target,
            ):
                continue
            shown["name"] = self.probe_card_name_map[card]
            shown["temp"] = temp
            shown["hasTemp"] = has_temp
            shown["stale"] = stale
            shown["target"] = target
            object_data["units"] = self.units
            self.display_object_list[self.dash_map[card]].update_object_data(object_data)

    def _update_output_icons(self):
        """Update Output Status Icons"""
        if self.last_status_data.get("outpins") is None:
            self.last_status_data["outpins"] = self.status_data["outpins"].copy()
            for output in self.last_status_data["outpins"]:
                self.last_status_data["outpins"][output] = self.status_data["outpins"][output] == False
        for output in self.status_data["outpins"]:
            if self.status_data["outpins"][output] != self.last_status_data["outpins"].get(output):
                if output == "auger" and "auger_status" in self.dash_map:
                    self._update_output_icon("auger_status", output)
                if output == "fan" and "fan_status" in self.dash_map:
                    self._update_output_icon("fan_status", output)
                if output == "igniter" and "igniter_status" in self.dash_map:
                    self._update_output_icon("igniter_status", output)

    def _update_output_icon(self, dash_key, output):
        object_data = self.display_object_list[self.dash_map[dash_key]].get_object_data()
        object_data["animation_enabled"] = bool(self.status_data["outpins"][output])
        object_data["active"] = bool(self.status_data["outpins"][output])
        self.display_object_list[self.dash_map[dash_key]].update_object_data(object_data)

    def _update_system_card(self):
        """Update System Card (ember dash - fan/auger/igniter combined)"""
        if "system_card" in self.dash_map and self.status_data["outpins"] != self.last_status_data.get("outpins"):
            object_data = self.display_object_list[self.dash_map["system_card"]].get_object_data()
            object_data["data"] = {
                "fan": bool(self.status_data["outpins"].get("fan", False)),
                "auger": bool(self.status_data["outpins"].get("auger", False)),
                "igniter": bool(self.status_data["outpins"].get("igniter", False)),
            }
            self.display_object_list[self.dash_map["system_card"]].update_object_data(object_data)

    def _update_timer(self):
        """Update Timer Output"""
        if self.status_data["mode"] in [Mode.PRIME, Mode.STARTUP, Mode.REIGNITE, Mode.SHUTDOWN]:
            if self.status_data["mode"] in [Mode.STARTUP, Mode.REIGNITE]:
                duration = self.status_data["start_duration"]
            elif self.status_data["mode"] in [Mode.PRIME]:
                duration = self.status_data["prime_duration"]
            else:
                duration = self.status_data["shutdown_duration"]

            countdown = max(0, int(duration - (time.time() - self.status_data["start_time"])))
            self._set_timer_object(countdown, "Timer")

        elif self.status_data["mode"] in [Mode.HOLD] and self.status_data["lid_open_detected"]:
            """ In Hold Mode, use timer for lid open detection """
            countdown = max(0, int(self.status_data["lid_open_endtime"] - time.time()))
            self._set_timer_object(countdown, "Lid Pause")

        else:
            """ Clear the timer in other modes. """
            self._set_timer_object(0, None)

    def _set_timer_object(self, countdown, label):
        if "timer" in self.dash_map:
            object_data = self.display_object_list[self.dash_map["timer"]].get_object_data()
            if countdown != object_data["data"]["seconds"]:
                object_data["data"]["seconds"] = countdown
                if label is not None:
                    object_data["label"] = label
                self.display_object_list[self.dash_map["timer"]].update_object_data(object_data)

    def _update_cook_time(self):
        """Update Cook Time (ember dash) - active countdown, else elapsed cook time"""
        if "cook_time" in self.dash_map:
            object_data = self.display_object_list[self.dash_map["cook_time"]].get_object_data()
            new_data = self._cook_time_data(self.status_data, time.time())
            if object_data.get("data") != new_data:
                object_data.setdefault("data", {})
                object_data["data"]["label"] = new_data["label"]
                object_data["data"]["value"] = new_data["value"]
                object_data["label"] = new_data["label"]  # top-level fallback if type is 'timer'-like
                self.display_object_list[self.dash_map["cook_time"]].update_object_data(object_data)

    def _update_lid_indicator(self):
        """In Hold Mode, Check Lid Indicator"""
        if (
            self.status_data["mode"] in [Mode.HOLD]
            and self.last_status_data["lid_open_detected"] != self.status_data["lid_open_detected"]
            and "lid_indicator" in self.dash_map
        ):
            object_data = self.display_object_list[self.dash_map["lid_indicator"]].get_object_data()
            if self.status_data["lid_open_detected"]:
                object_data["active"] = True
            else:
                object_data["active"] = False
            self.display_object_list[self.dash_map["lid_indicator"]].update_object_data(object_data)

    def _update_lid_alert(self):
        """Lid Alert Update (ember dash) - active only while lid_open_detected, mirrors lid_indicator"""
        if "lid_alert" in self.dash_map:
            object_data = self.display_object_list[self.dash_map["lid_alert"]].get_object_data()
            new_active = bool(self.status_data.get("lid_open_detected", False))
            if object_data.get("active") != new_active:
                object_data["active"] = new_active
                self.display_object_list[self.dash_map["lid_alert"]].update_object_data(object_data)

    def _update_lid_open_button(self):
        """In Hold Mode, Show Lid Indicator Button (when lid_open_detected is False)"""
        if (
            self.status_data["mode"] in [Mode.HOLD]
            and self.last_status_data["lid_open_detected"] != self.status_data["lid_open_detected"]
            and "lid_open_button" in self.dash_map
        ):
            object_data = self.display_object_list[self.dash_map["lid_open_button"]].get_object_data()
            if self.status_data["lid_open_detected"]:
                object_data["active"] = True
            else:
                object_data["active"] = False
            self.display_object_list[self.dash_map["lid_open_button"]].update_object_data(object_data)

    def _update_p_mode(self):
        """Update PMode"""
        if (
            self.status_data["mode"] in [Mode.STARTUP, Mode.REIGNITE, Mode.SMOKE]
            and (
                (self.status_data["mode"] != self.last_status_data.get("mode", "None"))
                or (self.status_data["p_mode"] != self.last_status_data.get("p_mode", "None"))
            )
            and "p_mode" in self.dash_map
        ):
            object_data = self.display_object_list[self.dash_map["p_mode"]].get_object_data()
            object_data["active"] = True
            object_data["data"]["pmode"] = self.status_data["p_mode"]
            self.display_object_list[self.dash_map["p_mode"]].update_object_data(object_data)

        elif self.status_data["mode"] != self.last_status_data.get("mode", "None") and "p_mode" in self.dash_map:
            object_data = self.display_object_list[self.dash_map["p_mode"]].get_object_data()
            object_data["active"] = False
            self.display_object_list[self.dash_map["p_mode"]].update_object_data(object_data)

    def _update_smoke_plus(self):
        """Update Smoke Plus"""
        if self.status_data["s_plus"] != self.last_status_data.get("s_plus", None) and "smoke_plus" in self.dash_map:
            object_data = self.display_object_list[self.dash_map["smoke_plus"]].get_object_data()

            object_data["active"] = self.status_data["s_plus"]
            object_data["button_value"][0] = "off" if self.status_data["s_plus"] else "on"

            self.display_object_list[self.dash_map["smoke_plus"]].update_object_data(object_data)

    def _update_hopper(self):
        """Update Hopper Info"""
        if (
            self.status_data["hopper_level"] != self.last_status_data.get("hopper_level", None)
            and "hopper" in self.dash_map
        ):
            object_data = self.display_object_list[self.dash_map["hopper"]].get_object_data()
            object_data["data"]["level"] = max(self.status_data["hopper_level"], 0)
            object_data["data"]["level"] = min(object_data["data"]["level"], 100)

            self.display_object_list[self.dash_map["hopper"]].update_object_data(object_data)

    def _update_hopper_vertical(self):
        """Update Hopper Vertical (ember dash) - D1: object is absent from dash_map entirely
        when hopper_level_enabled is False (see _build_objects), so this naturally no-ops then."""
        if (
            self.status_data["hopper_level"] != self.last_status_data.get("hopper_level", None)
            and "hopper_vertical" in self.dash_map
        ):
            object_data = self.display_object_list[self.dash_map["hopper_vertical"]].get_object_data()
            object_data.setdefault("data", {})
            object_data["data"]["level"] = max(min(self.status_data["hopper_level"], 100), 0)
            object_data["data"]["enabled"] = self.status_data.get("hopper_level_enabled", False)
            self.display_object_list[self.dash_map["hopper_vertical"]].update_object_data(object_data)

    def _update_duty_pills(self):
        """Update Duty Pills (ember dash) - P-MODE/SMOKE+ in Smoke, else AUGER/FAN DUTY"""
        if "duty_pill_left" in self.dash_map or "duty_pill_right" in self.dash_map:
            left_data, right_data = self._duty_pills(self.status_data)
            if "duty_pill_left" in self.dash_map:
                object_data = self.display_object_list[self.dash_map["duty_pill_left"]].get_object_data()
                if object_data.get("data") != left_data:
                    object_data["data"] = left_data
                    self.display_object_list[self.dash_map["duty_pill_left"]].update_object_data(object_data)
            if "duty_pill_right" in self.dash_map:
                object_data = self.display_object_list[self.dash_map["duty_pill_right"]].get_object_data()
                if object_data.get("data") != right_data:
                    object_data["data"] = right_data
                    self.display_object_list[self.dash_map["duty_pill_right"]].update_object_data(object_data)

    def _draw_objects(self):
        for object in self.display_object_list:
            objectData = object.get_object_data()
            objectState = object.get_object_state()

            if objectData["animation_enabled"] and objectState["animation_active"]:
                object_image = object.update_object_data()
                self.display_updated = True
            else:
                object_image = object.get_object_canvas()

            if objectData["glow"]:
                object_image_glow = object_image.copy()
                object_image_glow = object_image_glow.filter(ImageFilter.GaussianBlur(radius=3))
                self.display_canvas.paste(object_image, objectData["position"], object_image_glow)
            self.display_canvas.paste(object_image, objectData["position"], object_image)

    def _display_loop_render_step(self):
        """
        Shared per-tick display state machine, hoisted from the (previously
        byte-identical) inner block of the pygame/DSI and ili9341f flex
        drivers' `_display_loop`. Advances display_timeout, initializes or
        updates the active screen (home/dash/menu/input), draws all objects,
        and pushes the canvas when updated; otherwise clears the display and
        falls through to the dash screen when idle.

        Each flex driver's `_display_loop` retains its own device-specific
        wrapper (event polling, frame pacing, thread/process setup) and
        simply calls this once per iteration.
        """
        if self.display_active != None:
            if self.display_timeout:
                if time.time() > self.display_timeout:
                    self.display_timeout = None
                    self.display_active = None
                    self.display_init = True

            if self.display_active == "home":
                if self.display_init:
                    """ Initialize Home Screen """
                    self._build_objects(self.background)
                    self.display_init = False
                    self.display_updated = True

            elif self.display_active == "dash":
                if self.display_init:
                    """ Initialize Dash Screen """
                    if self.dash_object_list == []:
                        self._init_dash()
                    self._restore_dash_objects()
                    self._update_dash_objects()
                    self.display_init = False
                    self.display_updated = True
                else:
                    self._update_dash_objects()
                self._display_background()

            elif self.display_active is not None:
                if (("menu_" in self.display_active) or ("input_" in self.display_active)) and self.display_init:
                    """ Initialize Menu / Input Dialog """
                    self._display_menu_background()
                    self._build_objects(self.menu_background)
                    self.display_init = False
                    self.display_updated = True

            """ Draw all objects. Perform any animations that need to be displayed. """
            self._draw_objects()

            if self.display_updated:
                self._display_canvas()
                self.display_update = False

        else:
            if self.display_init:
                self._display_clear()
                self.display_init = False
                if not self.HOME_ENABLED:
                    self.display_active = "dash"
                    self._init_dash()
                    self.display_active = None

    """
        ====================== Input/Event Handling ========================
    """

    def _fetch_data(self):
        """
        - Updates the current data for the display loop, if in a work mode
        """
        if self.in_data is None:
            self.last_in_data = {}
        self.in_data = read_current()

        if self.status_data is None:
            self.last_status_data = {}
        self.status_data = read_status()

        self.units = self.status_data["units"]

        """ Wake the display to the dash if it's currently off """
        if self.display_active == None and self.status_data["mode"] != Mode.STOP:
            self.display_active = "dash"
            self.display_init = True
            self._wake_display()
            self.display_timeout = None

    def _event_detect(self):
        """
        Called to detect input events from buttons, encoder, touch, etc.

        Hoisted from the flex drivers (_base_dsi.py, ili9341f.py), where this
        was byte-identical.
        """
        user_input = self.input_event  # Save to variable to prevent spurious changes
        self.command = None
        if user_input:
            if self.display_timeout is not None:
                self.display_timeout = time.time() + self.TIMEOUT
            if user_input not in ["UP", "DOWN", "ENTER", "TOUCH"]:
                self.input_event = None
                self.touch_pos = (0, 0)
                return
            elif user_input == "TOUCH" and self.input_touch:
                self._process_touch()
            elif user_input in ["UP", "DOWN", "ENTER"] and (self.input_button or self.input_encoder):
                self._process_button()

            # Clear the input event and touch_pos
            self.input_event = None
            self.touch_pos = (0, 0)

    def _wake_and_activate_display(self):
        """
        Wake the display & go to home/dash.

        Shared by `_process_button` (below) and by each flex driver's
        `_process_touch` override, for the case where the display was off.
        """
        self._wake_display()
        self.display_active = "home" if self.HOME_ENABLED else "dash"
        self.display_init = True
        self.display_timeout = time.time() + self.TIMEOUT

    def _process_button(self):
        """
        Hoisted from the flex drivers (_base_dsi.py, ili9341f.py), where this
        was byte-identical.
        """
        self._debounce()
        if self.display_active:
            if "dash" in self.display_active:
                """
				Process dash button events
				"""
                self._capture_background()
                self._store_dash_objects()
                if self.status_data["mode"] == Mode.STOP:
                    self.display_active = "menu_main"
                elif self.status_data["mode"] in [Mode.STARTUP, Mode.REIGNITE, Mode.SMOKE, Mode.HOLD, Mode.SHUTDOWN]:
                    self.display_active = "menu_main_active_normal"
                elif self.status_data["mode"] == Mode.MONITOR:
                    self.display_active = "menu_main_active_monitor"
                elif self.status_data["mode"] == Mode.RECIPE:
                    self.display_active = "menu_main_active_recipe"
                else:
                    self.display_active = "menu_main"
                self.display_init = True
            elif "menu_" in self.display_active:
                """
				Process menu button events
				"""
                objectData = self.display_object_list[0].get_object_data()
                button_selected = objectData["data"].get("button_selected", None)
                button_list = objectData.get("button_list", [])

                if button_selected is not None and button_list != []:
                    if self.input_event == "UP":
                        if button_selected <= 1:
                            objectData["data"]["button_selected"] = (
                                len(button_list) - 1
                            )  # Note: button_list has extra close_menu entry at index 0
                        else:
                            objectData["data"]["button_selected"] -= 1
                        self.display_object_list[0].update_object_data(updated_objectData=objectData)
                    elif self.input_event == "DOWN":
                        if len(button_list) - 1 > button_selected:
                            objectData["data"]["button_selected"] += 1
                        else:
                            objectData["data"]["button_selected"] = 1
                        self.display_object_list[0].update_object_data(updated_objectData=objectData)
                    elif self.input_event == "ENTER":
                        if "cmd_" in objectData["button_list"][button_selected]:
                            self.command = objectData["button_list"][button_selected]
                            if objectData.get("button_value", False):
                                self.command_data = objectData["button_value"][button_selected]
                            else:
                                self.command_data = None
                            self._command_handler()
                        elif objectData["button_list"][button_selected] == "menu_close":
                            self.display_active = "dash"
                            self.display_init = True
                        elif ("menu_" in objectData["button_list"][button_selected]) or (
                            "input_" in objectData["button_list"][button_selected]
                        ):
                            if self.display_active == "dash":
                                self._capture_background()
                                self._store_dash_objects()
                            if (
                                ("input_" in self.display_active)
                                and ("input_" in objectData["button_list"][button_selected])
                                and ("button_value" in list(objectData.keys()))
                            ):
                                self.input_origin = objectData["button_value"][button_selected]
                            self.display_active = objectData["button_list"][button_selected]
                            self.display_init = True
                        elif "button_" in button_selected:
                            objectData["data"]["input"] = objectData["button_list"][button_selected].replace(
                                "button_", ""
                            )
                            self.display_object_list[0].update_object_data(updated_objectData=objectData)
                elif self.input_event == "ENTER" and button_selected == None:
                    self.display_active = "dash"
                    self.display_init = True
            elif "input_" in self.display_active:
                """
				Process input button events
				"""
                objectData = self.display_object_list[0].get_object_data()
                if self.input_event == "UP":
                    objectData["data"]["input"] = "up"
                    self.display_object_list[0].update_object_data(updated_objectData=objectData)
                if self.input_event == "DOWN":
                    objectData["data"]["input"] = "down"
                    self.display_object_list[0].update_object_data(updated_objectData=objectData)
                if self.input_event == "ENTER":
                    self.command = objectData["command"]
                    self._command_handler()
                    self.display_active = "dash"
                    self.display_init = True
        else:
            """
			Wake the display & go to home/dash
			"""
            self._wake_and_activate_display()

    def _process_touch_areas(self):
        """
        Loop through current displayed objects and check for touch collisions.

        Hoisted from the flex drivers (_base_dsi.py, ili9341f.py), where this
        loop body was byte-identical; `_process_touch` itself stays per-driver
        since _base_dsi.py additionally rotates self.touch_pos beforehand
        (real touchscreen coordinates need it) while ili9341f.py does not.
        """
        for pointer, object in enumerate(self.display_object_list):
            objectData = object.get_object_data()
            for index, touch_area in enumerate(objectData["touch_areas"]):
                if touch_area.collidepoint(self.touch_pos):
                    # print(f'You touched {objectData["button_list"][index]}.')
                    if "cmd_" in objectData["button_list"][index]:
                        self.command = objectData["button_list"][index]
                        if objectData.get("button_value", False):
                            self.command_data = objectData["button_value"][index]
                        else:
                            self.command_data = None
                        self._command_handler()
                    elif objectData["button_list"][index] == "menu_close":
                        self.display_active = "dash"
                        self.display_init = True
                    elif ("menu_" in objectData["button_list"][index]) or (
                        "input_" in objectData["button_list"][index]
                    ):
                        if self.display_active == "dash":
                            self._capture_background()
                            self._store_dash_objects()
                        if ("input_" in objectData["button_list"][index]) and (
                            "button_value" in list(objectData.keys())
                        ):
                            self.input_origin = objectData["button_value"][index]
                        self.display_active = objectData["button_list"][index]
                        self.display_init = True
                    elif "button_" in objectData["button_list"][index]:
                        objectData["data"]["input"] = objectData["button_list"][index].replace("button_", "")
                        self.display_object_list[pointer].update_object_data(updated_objectData=objectData)

    def _command_handler(self):
        """
        Called to handle commands
        """
        # print(' > Command Handler Called < ')
        if "monitor" in self.command:
            data = {"updated": True, "mode": "Monitor"}
            enqueue_control_delta(control_delta(set_values=data), origin="display")
            # print('Sent Monitor Mode Command!')
            self.display_active = "dash"
            self.display_init = True

        if "startup" in self.command:
            data = {"updated": True, "mode": "Startup"}
            enqueue_control_delta(control_delta(set_values=data), origin="display")
            # print('Sent Startup Mode Command!')
            self.display_active = "dash"
            self.display_init = True

        if "smoke" in self.command:
            data = {"updated": True, "mode": "Smoke"}
            enqueue_control_delta(control_delta(set_values=data), origin="display")
            self.display_active = "dash"
            self.display_init = True

        if "hold" in self.command:
            """ Set hold target for primary probe """
            primary_setpoint = 0
            for pointer, object in enumerate(self.display_object_list):
                objectData = object.get_object_data()
                if objectData["data"].get("value", False):
                    primary_setpoint = objectData["data"].get("value", False)
                    break

            if primary_setpoint:
                data = {"updated": True, "mode": "Hold", "primary_setpoint": primary_setpoint}
                enqueue_control_delta(control_delta(set_values=data), origin="display")
            self.display_active = "dash"
            self.display_init = True

        if "notify" in self.command:
            """ Set notification targets for probes/grill """
            notify_target = 0
            for pointer, object in enumerate(self.display_object_list):
                objectData = object.get_object_data()
                if objectData["data"].get("value", False):
                    notify_target = objectData["data"].get("value", False)
                    break

            # The entry is found by NAME (not label), so label and type are read
            # off the matched entry. Only that entry's two fields travel: the
            # whole array used to go back, which meant a concurrent notify write
            # anywhere in it was reverted by this one.
            control = read_control()
            for notify_source in control["notify_data"]:
                if notify_source["name"] == self.input_origin:
                    enqueue_control_delta(
                        control_delta(
                            ops=[
                                {
                                    "op": "notify.set",
                                    "label": notify_source["label"],
                                    "type": notify_source["type"],
                                    "fields": {
                                        "target": notify_target,
                                        "req": bool(notify_target),
                                    },
                                }
                            ]
                        ),
                        origin="display",
                    )
                    break

            self.input_origin = None
            self.display_active = "dash"
            self.display_init = True

        if "shutdown" in self.command:
            data = {"updated": True, "mode": "Shutdown"}
            enqueue_control_delta(control_delta(set_values=data), origin="display")
            self.display_active = "dash"
            self.display_init = True

        if "stop" in self.command:
            data = {"updated": True, "mode": "Stop"}
            enqueue_control_delta(control_delta(set_values=data), origin="display")

            self._init_framework()
            self._zero_dash_data()
            self.display_active = "dash"
            self.display_init = True
            self.display_timeout = time.time() + self.TIMEOUT

        if "splus" in self.command:
            toggle = not self.last_status_data.get("s_plus", False)
            data = {"s_plus": toggle}
            enqueue_control_delta(control_delta(set_values=data), origin="display")
            self.display_active = "dash"
            self.display_init = True

        if "primestartup" in self.command:
            data = {"updated": True, "mode": "Prime", "prime_amount": self.command_data, "next_mode": "Startup"}
            enqueue_control_delta(control_delta(set_values=data), origin="display")
            self.display_active = "dash"
            self.display_init = True

        if "primeonly" in self.command:
            data = {"updated": True, "mode": "Prime", "prime_amount": self.command_data, "next_mode": "Stop"}
            enqueue_control_delta(control_delta(set_values=data), origin="display")
            self.display_active = "dash"
            self.display_init = True

        if "pmode" in self.command:
            # TODO : Change to API Call
            settings = read_settings()
            settings["cycle_data"]["PMode"] = self.command_data
            write_settings(settings)
            data = {"settings_update": True}
            enqueue_control_delta(control_delta(set_values=data), origin="display")

            self.display_active = "dash"
            self.display_init = True

        if "next_step" in self.command:
            # The read stays: which branch this is depends on the live recipe
            # step_data. Each branch then states only its own member.
            data = read_control()
            # Check if currently in 'Paused' Status
            if "triggered" in data["recipe"]["step_data"] and "pause" in data["recipe"]["step_data"]:
                if data["recipe"]["step_data"]["triggered"] and data["recipe"]["step_data"]["pause"]:
                    # 'Unpause' Recipe
                    enqueue_control_delta(
                        control_delta(set_values={"recipe": {"step_data": {"pause": False}}}), origin="display"
                    )
                else:
                    # User is forcing next step
                    enqueue_control_delta(control_delta(set_values={"updated": True}), origin="display")
            else:
                # User is forcing next step
                enqueue_control_delta(control_delta(set_values={"updated": True}), origin="display")
            self.display_active = "dash"
            self.display_init = True

        if "reboot" in self.command:
            data = {"updated": True, "mode": "Stop"}
            enqueue_control_delta(control_delta(set_values=data), origin="display")
            if self.real_hardware:
                os.system("sleep 3 && sudo reboot &")
            else:
                pass
            self.display_active = "dash"
            self.display_init = True
            self.display_loop_active = False

        if "poweroff" in self.command:
            data = {"updated": True, "mode": "Stop"}
            enqueue_control_delta(control_delta(set_values=data), origin="display")
            if self.real_hardware:
                os.system("sleep 3 && sudo shutdown -h now &")
            else:
                pass
            self.display_active = "dash"
            self.display_init = True
            self.display_loop_active = False

        if "restart" in self.command:
            data = {"updated": True, "mode": "Stop"}
            enqueue_control_delta(control_delta(set_values=data), origin="display")
            if self.real_hardware:
                # supervisorctl, like common/system.py's restart_scripts and
                # restart_webapp. `sudo service supervisor restart` named a unit
                # that only exists on Debian -- and no installer grants the
                # `service` command under NOPASSWD at all, so this asked for a
                # password no one was there to type.
                #
                # The sleep stays: `restart all` includes the display, and this
                # runs on the display's own thread.
                os.system("sleep 3 && sudo supervisorctl restart all &")
            else:
                pass
            self.display_active = "dash"
            self.display_init = True
            self.display_loop_active = False

        if "hopper" in self.command:
            data = {"hopper_check": True}
            enqueue_control_delta(control_delta(set_values=data), origin="display")
            self.display_active = "dash"
            self.display_init = True

        if "igniter_toggle" in self.command:
            try:
                requests.get("http://127.0.0.1:5000/api/set/manual/igniter/toggle")
            except:
                self.eventLogger.debug("Igniter Toggle Failed.")

        if "auger_toggle" in self.command:
            try:
                requests.get("http://127.0.0.1:5000/api/set/manual/auger/toggle")
            except:
                self.eventLogger.debug("Auger Toggle Failed.")

        if "fan_toggle" in self.command:
            try:
                requests.get("http://127.0.0.1:5000/api/set/manual/fan/toggle")
            except:
                self.eventLogger.debug("Fan Toggle Failed.")

        if "lid_open" in self.command:
            try:
                requests.get("http://127.0.0.1:5000/api/set/lid_open/toggle")
            except:
                self.eventLogger.debug("Lid Open Failed.")
            # Go back to Dash
            self.display_active = "dash"
            self.display_init = True

        if "none" in self.command:
            pass

        self.command = None

    """
    ================ Externally Available Methods ================
    """

    def display_status(self, in_data, status_data):
        """
        Stub from legacy implementation
        """

    def display_splash(self):
        """
        - Calls Splash Screen
        This function is currently unused and is only provided to maintain compatibility.
        """

    def clear_display(self):
        """
        - Clear display and turn off backlight
        This function is currently unused and is only provided to maintain compatibility.
        """
        # print('Clear Display Requested.')

    def display_text(self, text):
        """
        - Display some text
        This function is currently unused and is only provided to maintain compatibility.
        """
        # print(f'Display Text: {text}')

    def display_network(self):
        """
        - Display Network IP QR Code
        This function is currently unused and is only provided to maintain compatibility.
        """
