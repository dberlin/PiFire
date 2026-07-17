"""
==============================================================================
 PiFire Settings Migration
==============================================================================

Description: Reading the settings.json FILE and migrating its contents across
  server versions -- the upgrade/downgrade paths and the version-overlay that
  runs on first import.

  Note: this is the FILE reader/migrator. At runtime SQLite is the source of
  truth for settings; see common/datastore_accessors.py.

  Extracted from common/common.py; common/common.py re-imports these names
  for now so that existing `common.common.X` call sites keep resolving.

==============================================================================
"""

import json
import os

from common.backups import backup_settings
from common.common import (
    deep_update,
    read_generic_json,
    semantic_ver_is_lower,
    semantic_ver_to_list,
    write_generic_json,
    write_log,
)
from common.datastore_accessors import write_settings_store, write_warning
from common.defaults import default_probe_config, default_settings


def read_settings_file(filename="settings.json", init=False, retry_count=0):
    """
    Read Settings from file

    :param filename: Filename to use (default settings.json)
    """

    try:
        json_data_file = os.fdopen(os.open(filename, os.O_RDONLY))
        json_data_string = json_data_file.read()
        settings = json.loads(json_data_string)
        json_data_file.close()

    except IOError, OSError:
        """ Settings file not found, return default settings """
        settings = default_settings()
        return settings
    except ValueError:
        # A ValueError Exception occurs when multiple accesses collide, this code attempts a retry.
        event = "ERROR: Value Error Exception - JSONDecodeError reading settings.json"
        write_log(event)
        json_data_file.close()
        # Retry Reading Settings
        if retry_count < 5:
            settings = read_settings_file(filename=filename, retry_count=retry_count + 1)
        else:
            """ Undefined settings file load error, indicates corruption """
            settings_default = default_settings()
            settings = restore_settings(settings_default)
            init = True

    if init:
        # Get latest settings format
        settings_default = default_settings()

        # Overlay the read values over the top of the default settings
        #  This ensures that any NEW fields are captured.
        update_settings = False  # set flag in case an update needs to be written back

        # Prevent the wizard from popping up on existing installations
        if "first_time_setup" not in settings["globals"].keys():
            settings["globals"]["first_time_setup"] = False
            update_settings = True

        # If default version is different from what is currently saved, update version in saved settings
        if "versions" not in settings.keys():
            """ Upgrading from extremely old version """
            settings["versions"] = settings_default["versions"]
            update_settings = True
        elif semantic_ver_is_lower(settings["versions"]["server"], settings_default["versions"]["server"]):
            """ Upgrade Path """
            backup_settings()  # Backup Old Settings Before Performing Upgrade
            warning = f"Upgrading your settings from {settings['versions']['server']} to {settings_default['versions']['server']}."
            write_warning(warning)
            write_log(warning)
            prev_ver = semantic_ver_to_list(settings["versions"]["server"])
            settings = upgrade_settings(prev_ver, settings, settings_default)
            settings["versions"] = settings_default["versions"]
            update_settings = True
        elif semantic_ver_is_lower(settings_default["versions"]["server"], settings["versions"]["server"]):
            """ Downgrade Path """
            backup_settings()  # Backup Old Settings Before Performing Downgrade
            settings = downgrade_settings(settings, settings_default)
            update_settings = True
        elif (settings_default["versions"]["server"] == settings["versions"]["server"]) and (
            settings["versions"]["build"] <= settings_default["versions"]["build"]
        ):
            """ Minor Upgrade Path """
            prev_ver = semantic_ver_to_list(settings["versions"]["server"])
            settings = upgrade_settings(prev_ver, settings, settings_default)
            settings["versions"] = settings_default["versions"]
            update_settings = True

        if settings["versions"].get("build", None) != settings_default["versions"]["build"]:
            settings["versions"]["build"] = settings_default["versions"]["build"]
            update_settings = True

        # Overlay the original settings on top of the default settings
        settings = deep_update(settings_default, settings)
        update_settings = True
        settings["history_page"]["probe_config"] = default_probe_config(
            settings
        )  # Fix issue with probe_configs resetting to defaults

    return settings


def upgrade_settings(prev_ver, settings, settings_default):
    """Check if upgrading from v1.4.x or earlier"""
    if prev_ver[0] <= 1 and prev_ver[1] <= 4:
        settings["versions"] = settings_default["versions"]
        settings["globals"]["first_time_setup"] = True  # Force configuration for probes
        settings["startup"]["start_to_mode"]["primary_setpoint"] = settings["start_to_mode"]["grill1_setpoint"]
        settings["start_to_mode"].pop("grill1_setpoint")
        settings["dashboard"] = settings_default["dashboard"]
        # Move Notification Settings
        settings["notify_services"] = {}
        for key in settings_default["notify_services"].keys():
            settings["notify_services"][key] = settings[key]
        settings["probe_settings"].pop("probe_options")
        settings["probe_settings"].pop("probe_sources")
        settings["probe_settings"].pop("probes_enabled")
        settings["modules"].pop("adc")
        # Add ID to probe_profiles
        for profile in settings["probe_settings"]["probe_profiles"]:
            if "id" not in settings["probe_settings"]["probe_profiles"][profile].keys():
                settings["probe_settings"]["probe_profiles"][profile]["id"] = profile
    if prev_ver[0] <= 1 and prev_ver[1] <= 5:
        # if moving from v1.5 to v1.6, force a first-time setup to drive changes to the probe device setup
        settings["globals"]["first_time_setup"] = True
        settings["cycle_data"].pop("SmokeCycleTime")  # Remove old SmokeCycleTime
        settings["cycle_data"]["SmokeOnCycleTime"] = 15  # Name change for SmokeCycleTime variable
        settings["cycle_data"]["SmokeOffCycleTime"] = 45  # Added SmokeOffCycleTime variable
    """ Check if upgrading from v1.6.x or v1.7.0 build 7 """
    if (prev_ver[0] <= 1 and prev_ver[1] <= 6) or (
        prev_ver[0] == 1 and prev_ver[1] == 7 and settings["versions"].get("build", 0) <= 7
    ):
        settings["dashboard"] = settings_default["dashboard"]
    """ Check if upgrading from v1.7.0 build 45 """
    if (prev_ver[0] <= 1 and prev_ver[1] <= 6) or (
        prev_ver[0] == 1 and prev_ver[1] == 7 and settings["versions"].get("build", 0) <= 45
    ):
        # Move startup defaults to new 'startup' section of settings
        settings["startup"] = settings_default["startup"]
        settings["startup"]["duration"] = settings["globals"].get(
            "startup_timer", settings_default["startup"]["duration"]
        )
        settings["globals"].pop("startup_timer", None)
        settings["startup"]["startup_exit_temp"] = settings["globals"].get(
            "startup_exit_temp", settings_default["startup"]["startup_exit_temp"]
        )
        settings["globals"].pop("startup_exit_temp", None)
        settings["startup"]["start_to_mode"] = settings.get(
            "start_to_mode", settings_default["startup"]["start_to_mode"]
        )
        settings.pop("start_to_mode", None)
        settings["startup"]["smartstart"] = settings.get("smartstart", settings_default["startup"]["smartstart"])
        settings.pop("smartstart", None)
        settings["shutdown"] = settings_default["shutdown"]
        settings["shutdown"]["shutdown_duration"] = settings["globals"].get(
            "shutdown_timer", settings_default["shutdown"]["shutdown_duration"]
        )
        settings["globals"].pop("shutdown_timer", None)
        settings["shutdown"]["auto_power_off"] = settings["globals"].get(
            "auto_power_off", settings_default["shutdown"]["auto_power_off"]
        )
        settings["globals"].pop("auto_power_off", None)
    """ Check if upgrading from v1.7.x """
    if prev_ver[0] <= 1 and prev_ver[1] <= 7:
        """ Force running the configuration wizard again """
        settings["globals"]["first_time_setup"] = True
        """ Create platform section in settings with defaults """
        settings["platform"] = settings_default["platform"]
        """ Move platform global variables to platform section """
        if settings["globals"].get("buttonslevel", None) is not None:
            settings["platform"]["buttonslevel"] = settings["globals"].get("buttonslevel", "HIGH")
            settings["globals"].pop("buttonslevel")
        if settings["globals"].get("dc_fan", None) is not None:
            settings["platform"]["dc_fan"] = settings["globals"].get("dc_fan", False)
            settings["globals"].pop("dc_fan")
        if settings["globals"].get("real_hw", None) is not None:
            settings["platform"]["real_hw"] = settings["globals"].get("real_hw", True)
            settings["globals"].pop("real_hw")
        if settings["globals"].get("standalone", None) is not None:
            settings["platform"]["standalone"] = settings["globals"].get("standalone", True)
            settings["globals"].pop("standalone")
        if settings["globals"].get("triggerlevel", None) is not None:
            settings["platform"]["triggerlevel"] = settings["globals"].get("triggerlevel", "LOW")
            settings["globals"].pop("triggerlevel")
        """ Move pin definitions to platform section"""
        if settings.get("dev_pins", None) is not None:
            updated_dict = deep_update(settings["platform"]["devices"], settings["dev_pins"])
            settings["platform"]["devices"] = updated_dict
            settings.pop("dev_pins")
        if settings.get("inpins", None) is not None:
            updated_dict = deep_update(settings["platform"]["inputs"], settings["inpins"])
            settings["platform"]["inputs"] = updated_dict
            settings.pop("inpins")
        if settings.get("outpins", None) is not None:
            updated_dict = deep_update(settings["platform"]["outputs"], settings["outpins"])
            settings["platform"]["outputs"] = updated_dict
            settings.pop("outpins")
        """ Migrate module settings for the appropriate module support """
        settings["platform"]["current"] = (
            "custom"  # Since we do not know what PCB / System is installed on upgrade, set to custom
        )
        if settings["modules"]["grillplat"] == "prototype":
            settings["platform"]["system_type"] = "prototype"
        else:
            settings["platform"]["system_type"] = "raspberry_pi_all"
            settings["modules"]["grillplat"] == "raspberry_pi_all"

    """ Check if upgrading from v1.9.0 build 32 """
    if prev_ver[0] == 1 and prev_ver[1] == 9 and settings["versions"].get("build", 0) <= 32:
        for index, device in enumerate(settings["probe_settings"]["probe_map"]["probe_devices"]):
            if device["module"] == "bt_meater_alt":
                settings["probe_settings"]["probe_map"]["probe_devices"][index]["module"] = "bt_meater"
            elif device["module"] == "bt_meater":
                settings["probe_settings"]["probe_map"]["probe_devices"][index]["module"] = "bt_meater_exp"

    """ Check if upgrading from previous to v1.10 or from v1.10.0 build 0 """
    if (prev_ver[0] == 1 and prev_ver[1] == 10 and settings["versions"].get("build", 0) == 0) or (
        prev_ver[0] == 1 and prev_ver[1] < 10
    ):
        """ Setup new Python Exec and UV settings """
        if settings["globals"].get("venv", False):
            """ If using VENV, set the python_exec to the bin/python """
            settings["globals"]["python_exec"] = "bin/python"
            settings["globals"]["uv"] = False
        else:
            settings["globals"]["python_exec"] = "python"
            settings["globals"]["uv"] = False
            # TODO: Upgrade to VENV for older configs?

    """ Check if upgrading from previous to v1.10 or from v1.10.0 build 51 """
    if (prev_ver[0] == 1 and prev_ver[1] == 10 and settings["versions"].get("build", 0) <= 51) or (
        prev_ver[0] == 1 and prev_ver[1] < 10
    ):
        """ Update probe map devices to include module_filename """
        print("Upgrading probe map devices to include module_filename")
        for index, device in enumerate(settings["probe_settings"]["probe_map"]["probe_devices"]):
            if "module_filename" not in list(device.keys()):
                print(f"   Updating device: {device['device']} - {device['module']}")
                device["module_filename"] = device["module"]
                settings["probe_settings"]["probe_map"]["probe_devices"][index] = device

    """ Import any new probe profiles """
    for profile in list(settings_default["probe_settings"]["probe_profiles"].keys()):
        if profile not in list(settings["probe_settings"]["probe_profiles"].keys()):
            settings["probe_settings"]["probe_profiles"][profile] = settings_default["probe_settings"][
                "probe_profiles"
            ][profile]

    settings["globals"]["updated_message"] = True  # Display updated message after reset/reboot
    return settings


def downgrade_settings(settings, settings_default):
    """Look for backup file for the downgrade"""
    backup_manifest = read_generic_json("./backups/manifest.json")
    if backup_manifest == {}:
        backup_manifest = {"server_settings": {}, "pelletdb": {"current": ""}}
        write_generic_json(backup_manifest, "./backups/manifest.json")
    server_version = settings_default["versions"]["server"]
    backup_settings_file = backup_manifest["server_settings"].get(server_version, None)
    if backup_settings_file is not None:
        warning = f"Downgrade server version detected. [{settings['versions']['server']} -> {settings_default['versions']['server']}] Restoring settings from the following backup settings file: {backup_settings_file}."
        # Read the backup FILE (not SQLite); same fix as restore_settings().
        settings = read_settings_file(filename=backup_settings_file)
    else:
        warning = f"Downgrade server version detected. [{settings['versions']['server']} -> {settings_default['versions']['server']}] Resetting settings to defaults, since no backup settings files were found."
        settings = settings_default
    write_warning(warning)
    write_log(warning)
    return settings


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
