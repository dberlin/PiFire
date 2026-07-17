"""Hardware device construction, with prototype-fallback try/except logic:
each device module is imported dynamically from settings, and if it fails to
load or configure, a simulated "none" fallback is substituted and the failure
is recorded to the errors list/logs. build_devices() builds the grill
platform, probe complex, and distance sensor for the controller process;
build_display() builds only the display for the separate display process --
kept apart so the two processes never touch each other's hardware."""

import importlib

from common.common import get_probe_info, WriteKind
from common.datastore_accessors import (
    read_control,
    read_pellet_db,
    write_pellet_db,
    write_control,
    write_errors,
    write_generic_key,
)
from common.i2c_bus import assert_clean_blinka_env

from controller.runtime.context import Devices


def build_display(settings, *, errors, event_log, control_log):
    """
    Construct the display device with prototype-fallback logic: import the
    configured display module, falling back to the simulated "none" display
    (and recording the failure) if import or configuration fails. Used by the
    display process (display_process.py). NOT called by the controller
    (build_devices) -- the display process must never touch grill/probe/
    distance hardware, and the controller must never touch the display.

    :param settings: Settings dictionary
    :param errors: Errors list to append to (and persist via write_errors)
    :param event_log: Event logger
    :param control_log: Control logger
    :return: (display_or_None, errors)
    """
    units = settings["globals"]["units"]

    """
	Set up Display Module
	"""
    try:
        display_name = settings["modules"]["display"]
        DisplayModule = importlib.import_module(f"display.{display_name}")
        display_config = settings["display"]["config"][display_name]
        display_config["probe_info"] = get_probe_info(settings["probe_settings"]["probe_map"]["probe_info"])
        disp_rotation = display_config.get("rotation", 0)

    except:
        control_log.exception(f"Error occurred loading the display module ({display_name}). Trace dump: ")
        DisplayModule = importlib.import_module("display_none")
        error_event = (
            f"An error occurred loading the [{settings['modules']['display']}] display module.  The "
            f'"display_none" module has been loaded instead.  This sometimes means that the hardware is '
            f"not connected properly, or the module is not configured.  Please run the configuration wizard "
            f"again from the admin panel to fix this issue."
        )
        errors.append(error_event)
        write_errors(errors)
        event_log.error(error_event)
        control_log.error(error_event)
        if settings["globals"]["debug_mode"]:
            raise

    try:
        display_device = DisplayModule.Display(
            dev_pins=settings["platform"]["devices"],
            buttonslevel=settings["platform"]["buttonslevel"],
            rotation=disp_rotation,
            units=units,
            config=display_config,
        )
    except:
        control_log.exception(
            f"Error occurred configuring the display module ({settings['modules']['display']}). Trace dump: "
        )
        from display.none import Display  # Simulated Library for controlling the grill platform

        display_device = Display(
            dev_pins=settings["platform"]["devices"],
            buttonslevel=settings["platform"]["buttonslevel"],
            rotation=disp_rotation,
            units=units,
            config={},
        )
        error_event = (
            f"An error occurred configuring the [{settings['modules']['display']}] display object.  The "
            f'"display_none" module has been loaded instead.  This sometimes means that the hardware is '
            f"not connected properly, or the module is not configured.  Please run the configuration wizard "
            f"again from the admin panel to fix this issue."
        )
        errors.append(error_event)
        write_errors(errors)
        event_log.error(error_event)
        control_log.error(error_event)
        if settings["globals"]["debug_mode"]:
            raise

    return display_device, errors


def build_devices(settings, *, errors, event_log, control_log):
    """
    Construct the grill platform, probe complex, and distance/hopper-level
    devices, with the same prototype-fallback logic as build_display(): import
    each configured device module, falling back to a simulated device (and
    recording the failure) if import or configuration fails.

    Does NOT construct the display -- that is the display process's job via
    build_display(). The controller process must stay headless with respect
    to the display so that the two processes never race on the same
    hardware.

    :param settings: Settings dictionary
    :param errors: Errors list to append to (and persist via write_errors)
    :param event_log: Event logger
    :param control_log: Control logger
    :return: (Devices, errors)
    """
    # Refuse to start if a board-forcing BLINKA_* env var is set: it would pin
    # Blinka's `board` backend process-wide and silently break `basic` and any
    # import board. Devices must select ft232h/mcp2221 bus kinds instead.
    assert_clean_blinka_env()

    platform_config = settings["platform"]
    platform_config["frequency"] = settings["pwm"]["frequency"]
    units = settings["globals"]["units"]

    """
	Set up GrillPlatform Module
	"""
    try:
        grill_platform = settings["modules"]["grillplat"]
        GrillPlatModule = importlib.import_module(f"grillplat.{grill_platform}")

    except:
        control = read_control()
        control["critical_error"] = True
        write_control(control, WriteKind.OVERWRITE, origin="control")
        control_log.exception(
            f"Error occurred importing grillplatform module ({settings['modules']['grillplat']}). Trace dump: "
        )
        GrillPlatModule = importlib.import_module("grillplat.prototype")
        error_event = (
            f"An error occurred importing the [{settings['modules']['grillplat']}] platform module.  The "
            f"prototype module has been imported instead.  This sometimes means that the module does not exist or is not "
            f"properly named.  Please run the configuration wizard again from the admin "
            f"panel to fix this issue."
        )
        errors.append(error_event)
        write_errors(errors)
        event_log.error(error_event)
        control_log.error(error_event)
        if settings["globals"]["debug_mode"]:
            raise

    try:
        grill_platform = GrillPlatModule.GrillPlatform(platform_config)
    except:
        control = read_control()
        control["critical_error"] = True
        write_control(control, WriteKind.OVERWRITE, origin="control")
        control_log.exception(
            f"Error occurred configuring grillplatform module ({settings['modules']['grillplat']}). Trace dump: "
        )
        from grillplat.prototype import GrillPlatform  # Simulated Library for controlling the grill platform

        grill_platform = GrillPlatform(platform_config)
        error_event = (
            f"An error occurred configuring the [{settings['modules']['grillplat']}] platform object.  The "
            f"prototype module has been loaded instead.  This sometimes means that the hardware is not "
            f"connected properly, or the module is not configured.  Please run the configuration wizard "
            f"again from the admin panel to fix this issue."
        )
        errors.append(error_event)
        write_errors(errors)
        event_log.error(error_event)
        control_log.error(error_event)
        if settings["globals"]["debug_mode"]:
            raise

    """
	Set up Probes Input Module
	"""
    try:
        from probes.main import ProbesMain  # Probe device library: loads probe devices and maps them to ports

        probe_complex = ProbesMain(settings["probe_settings"]["probe_map"], settings["globals"]["units"])

    except:
        control_log.exception(f"Error occurred loading probes modules. Trace dump: ")
        # settings['probe_settings']['probe_map'] = default_probe_map(settings["probe_settings"]['probe_profiles'])
        probe_complex = ProbesMain(settings["probe_settings"]["probe_map"], settings["globals"]["units"], disable=True)
        error_event = (
            f"An error occurred loading the probes module(s).  All probes & probe devices have been disabled. "
            f"This sometimes means that the hardware is not connected properly, or the module is not configured correctly. "
            f"Please run the configuration wizard again from the admin panel to fix this issue. "
        )
        errors.append(error_event)
        write_errors(errors)
        event_log.error(error_event)
        control_log.error(error_event)
        if settings["globals"]["debug_mode"]:
            raise

    # Get probe initialization errors and pass along to the frontend
    probe_errors = probe_complex.get_errors()
    if len(probe_errors) > 0:
        for error in probe_errors:
            event_log.error(error)
            errors.append(error)
            write_errors(errors)

    # Get probe device info for frontend
    write_generic_key("probe_device_info", probe_complex.get_device_info())

    """
	Set up Distance (Hopper Level) Module
	"""
    try:
        dist_name = settings["modules"]["dist"]
        DistanceModule = importlib.import_module(f"distance.{dist_name}")

    except:
        control_log.exception(f"Error occurred loading the distance module ({dist_name}). Trace dump: ")
        DistanceModule = importlib.import_module("distance.none")
        error_event = (
            f"An error occurred loading the [{settings['modules']['dist']}] distance module.  The none "
            f"module has been loaded instead.  This sometimes means that the hardware is not connected "
            f"properly, or the module is not configured.  Please run the configuration wizard again from the "
            f"admin panel to fix this issue."
        )
        errors.append(error_event)
        write_errors(errors)
        event_log.error(error_event)
        control_log.error(error_event)

    try:
        if settings["modules"]["grillplat"] == "prototype" and settings["modules"]["dist"] == "prototype":
            # If in prototype mode, enable test reading (i.e. random values from proto distance sensor)
            dist_device = DistanceModule.HopperLevel(
                dev_pins=settings["platform"]["devices"],
                empty=settings["pelletlevel"]["empty"],
                full=settings["pelletlevel"]["full"],
                debug=settings["globals"]["debug_mode"],
                random=True,
            )
        else:
            dist_device = DistanceModule.HopperLevel(
                dev_pins=settings["platform"]["devices"],
                empty=settings["pelletlevel"]["empty"],
                full=settings["pelletlevel"]["full"],
                debug=settings["globals"]["debug_mode"],
            )
    except:
        control_log.exception(f"Error occurred configuring the distance module ({dist_name}). Trace dump: ")
        from distance.none import HopperLevel  # Simulated Library for controlling the grill platform

        dist_device = HopperLevel(
            dev_pins=settings["platform"]["devices"],
            empty=settings["pelletlevel"]["empty"],
            full=settings["pelletlevel"]["full"],
            debug=settings["globals"]["debug_mode"],
        )
        error_event = (
            f"An error occurred configuring the [{settings['modules']['dist']}] distance object.  The "
            f"none module has been loaded instead.  This sometimes means that the hardware is not "
            f"connected properly, or the module is not configured.  Please run the configuration wizard again "
            f"from the admin panel to fix this issue."
        )
        errors.append(error_event)
        write_errors(errors)
        event_log.error(error_event)
        control_log.error(error_event)

    # Get current hopper level and save it to the current pellet information
    pelletdb = read_pellet_db()
    pelletdb["current"]["hopper_level"] = dist_device.get_level(override=True)
    write_pellet_db(pelletdb)
    event_log.info(f"Hopper Level Checked @ {pelletdb['current']['hopper_level']}%")

    devices = Devices(grill_platform=grill_platform, probe_complex=probe_complex, dist_device=dist_device)
    return devices, errors
