"""
==============================================================================
 PiFire Settings Migration
==============================================================================

Description: Reading a settings JSON FILE and migrating its contents across
  server versions -- the upgrade/downgrade paths and the version-overlay that
  runs on first import.

  Note: this is the FILE reader/migrator, and it is IMPORT-ONLY. SQLite
  (pifire.db) is the sole settings store -- see
  common/datastore_accessors.py. The only files this ever reads are the
  one-time first-boot import source (a settings.json left behind by a
  pre-SQLite install; see common/datastore.py::_first_boot_import) and the
  backup files the admin page restores from. Nothing writes a settings.json
  at runtime; one exists only if a human runs
  scripts/export-settings-json.py.

  Extracted from common/common.py; common/common.py re-imports these names
  for now so that existing `common.common.X` call sites keep resolving.

==============================================================================
"""

from collections.abc import MutableMapping

import copy
import json
import os

from common.backups import backup_settings
from common.common import (
    BACKUP_PATH,
    deep_update,
    read_generic_json,
    semantic_ver_is_lower,
    semantic_ver_to_list,
    write_generic_json,
    write_log,
)
from common.persistence.runtime import write_settings_store, write_warning
from common.defaults import default_probe_config, default_settings


def read_settings_file(filename="settings.json", init=False, retry_count=0):
    """
    Read Settings from a JSON FILE (not SQLite).

    Despite the name this is not unconditionally pure -- it has two write
    paths, both deliberate. They are documented here because the name alone
    does not admit them:

    * ``init=True`` (opt-in, OFF by default) runs the version-overlay /
      migration pipeline, which on an upgrade or downgrade calls
      ``backup_settings()`` (writes a backup JSON + updates the backup
      manifest) and ``write_warning()`` to tell the user their settings were
      migrated. The three production callers all pass it deliberately:
      ``common/datastore.py::_first_boot_import`` and the two settings-restore
      handlers in ``blueprints/admin/routes.py``.
    * Corruption recovery runs REGARDLESS of ``init``: if the file still fails
      to parse after 5 retries, ``restore_settings()`` recovers from a backup
      and persists the result via ``write_settings_store()``, then forces
      ``init = True`` so the recovered tree is migrated forward. Pinned by
      ``test_read_settings_file_retries_on_corrupt_json_then_restores``.

    With ``init=False`` and a readable, well-formed file this is a pure read.

    :param filename: File to read. The default is only ever taken by the
            one-time first-boot import, which looks for a settings.json left
            behind by a pre-SQLite install; every other caller passes a
            backup file path.
    :param init: Run the migration pipeline over the result (see above)
    :param retry_count: Internal -- recursion guard for the ValueError retry
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
        event = f"ERROR: Value Error Exception - JSONDecodeError reading {filename}"
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

        # Prevent the wizard from popping up on existing installations
        if "first_time_setup" not in settings["globals"].keys():
            settings["globals"]["first_time_setup"] = False

        # If default version is different from what is currently saved, update version in saved settings
        if "versions" not in settings.keys():
            """ Upgrading from extremely old version """
            settings["versions"] = settings_default["versions"]
        elif semantic_ver_is_lower(settings["versions"]["server"], settings_default["versions"]["server"]):
            """ Upgrade Path """
            backup_settings()  # Backup Old Settings Before Performing Upgrade
            warning = f"Upgrading your settings from {settings['versions']['server']} to {settings_default['versions']['server']}."
            write_warning(warning)
            write_log(warning)
            prev_ver = semantic_ver_to_list(settings["versions"]["server"])
            settings = upgrade_settings(prev_ver, settings, settings_default)
            settings["versions"] = settings_default["versions"]
        elif semantic_ver_is_lower(settings_default["versions"]["server"], settings["versions"]["server"]):
            """ Downgrade Path """
            backup_settings()  # Backup Old Settings Before Performing Downgrade
            settings = downgrade_settings(settings, settings_default)
        elif (settings_default["versions"]["server"] == settings["versions"]["server"]) and (
            settings["versions"]["build"] <= settings_default["versions"]["build"]
        ):
            """ Minor Upgrade Path """
            prev_ver = semantic_ver_to_list(settings["versions"]["server"])
            settings = upgrade_settings(prev_ver, settings, settings_default)
            settings["versions"] = settings_default["versions"]

        if settings["versions"].get("build", None) != settings_default["versions"]["build"]:
            settings["versions"]["build"] = settings_default["versions"]["build"]

        _apply_shape_migrations(settings, settings_default["schema_version"])

        # Overlay the original settings on top of the default settings
        settings = deep_update(settings_default, settings)
        settings["history_page"]["probe_config"] = default_probe_config(
            settings
        )  # Fix issue with probe_configs resetting to defaults

    return settings


def _legacy_bus_to_config(section):
    """The tagged i2c_bus object a legacy (i2c_bus_kind, i2c_bus_num) pair meant.

    String parsing only -- this runs during settings load, long before any
    adapter can be probed, so an adapter name stays an adapter name rather than
    being resolved to the serial behind it.
    """
    kind = str(section.get("i2c_bus_kind", "")).strip().lower()
    # Pre basic/extended installs stored only the bridge name.
    selector = section.get("i2c_bus_num", section.get("i2c_bus_match", ""))
    selector = "" if selector is None else str(selector).strip()

    if not kind:
        kind = "extended" if selector else "basic"

    if kind == "basic":
        # basic addresses the board's own SCL/SDA pins and never had a
        # selector, so anything stored beside it is not a discarded value.
        return {"kind": "basic"}

    if kind == "extended":
        if not selector:
            # find_i2c_bus("") substring-matches every adapter and raises, so
            # this configuration could never open a bus. The board's own pins
            # are the honest repair.
            write_log("I2C bus: 'extended' with no bus selected; falling back to the integrated bus.")
            return {"kind": "basic"}
        if selector.lower().startswith("serial:"):
            return {"kind": "kernel", "serial": selector.split(":", 1)[1].strip()}
        if selector.isdigit():
            return {"kind": "kernel", "bus_num": int(selector)}
        return {"kind": "kernel", "adapter": selector}

    if kind in ("ft232h", "mcp2221"):
        field = "url" if kind == "ft232h" else "serial"
        # '1' is the historical ft232h default and means "the first one found",
        # the same as blank. Normalize before deciding anything, so a legitimate
        # default is never mistaken for a leftover.
        if kind == "ft232h" and selector == "1":
            selector = ""
        # A selector naming a kernel adapter cannot address a USB-HID device.
        # Dropping it leaves "the first one found", which is what a fresh
        # install of this kind means.
        if kind == "ft232h":
            stranded = bool(selector) and not selector.lower().startswith("ftdi://")
        else:
            stranded = selector.lower().startswith("serial:") or selector.lower() in ("cp2112", "mcp2221")
        if stranded:
            write_log(f"I2C bus: dropping {selector!r}, which does not name a {kind} device.")
            selector = ""
        return {"kind": kind, field: selector}

    # A kind we do not recognize (including an explicit None or a non-string)
    # cannot tell us what its selector meant, so the selector goes with it.
    if selector:
        write_log(f"I2C bus: kind {section.get('i2c_bus_kind')!r} is not a bus kind; dropping {selector!r}.")
    return {"kind": "basic"}


def _i2c_bus_sections(settings):
    """Every mapping in the tree that stores one I2C bus configuration."""
    platform = settings.get("platform", {}) or {}
    yield (platform.get("devices", {}) or {}).get("distance")
    yield platform.get("fan_controller")
    probe_map = (settings.get("probe_settings", {}) or {}).get("probe_map", {}) or {}
    for device in probe_map.get("probe_devices", []) or []:
        yield device.get("config")


def _migrate_i2c_buses(settings):
    """Rewrite every legacy (i2c_bus_kind, i2c_bus_num) pair as one i2c_bus
    object. Idempotent: a section that already has i2c_bus is left alone.

    Returns True if any section was converted, False if there was nothing to
    do, so a caller running this unconditionally can skip a pointless write.
    """
    changed = False
    for section in _i2c_bus_sections(settings):
        if not isinstance(section, dict):
            continue
        legacy = {"i2c_bus_kind", "i2c_bus_num", "i2c_bus_match"} & set(section)
        if not legacy:
            continue
        if "i2c_bus" not in section:
            section["i2c_bus"] = _legacy_bus_to_config(section)
        for key in legacy:
            section.pop(key, None)
        changed = True
    return changed


def _remove_retired_mpc_logging_settings(settings):
    """Remove retired MPC logging settings from an otherwise valid tree."""
    controller = settings.get("controller")
    if not isinstance(controller, MutableMapping):
        return False
    config = controller.get("config")
    if not isinstance(config, MutableMapping):
        return False
    mpc = config.get("mpc")
    if not isinstance(mpc, MutableMapping):
        return False

    changed = False
    for key in ("log_data", "log_path"):
        if key in mpc:
            mpc.pop(key)
            changed = True
    return changed


def _remove_mpc_affine_load_bounds(settings):
    """Remove retired affine MPC load bounds without touching cycle settings."""
    controller = settings.get("controller")
    if not isinstance(controller, MutableMapping):
        return False
    config = controller.get("config")
    if not isinstance(config, MutableMapping):
        return False
    mpc = config.get("mpc")
    if not isinstance(mpc, MutableMapping):
        return False
    changed = False
    for key in ("Q_min", "Q_max"):
        if key in mpc:
            mpc.pop(key)
            changed = True
    return changed


def _remove_retired_fan_pid(settings):
    """Remove the retired fan-assist setting."""
    cycle_data = settings.get("cycle_data")
    if not isinstance(cycle_data, MutableMapping):
        return False
    if "FanPidEnabled" not in cycle_data:
        return False
    cycle_data.pop("FanPidEnabled")
    return True


_RETIRED_CONTROLLER_IDS = (
    "pid_clamping",
    "pid_clamping_percent_pb",
    "pid_ac",
    "pid_parallel",
    "fuzzy",
    "ml",
)


def _migrate_retired_controllers(settings):
    """Retire old controller selections without disturbing the PID config."""
    controller = settings.get("controller")
    if not isinstance(controller, MutableMapping):
        return False
    config = controller.get("config")
    if not isinstance(config, MutableMapping):
        return False

    changed = False
    if controller.get("selected") in _RETIRED_CONTROLLER_IDS:
        controller["selected"] = "pid"
        changed = True

    sentinel = object()
    for name in _RETIRED_CONTROLLER_IDS:
        if config.pop(name, sentinel) is not sentinel:
            changed = True
    return changed


def _clear_mpc_identification_choice(settings):
    """Drop a stored MPC identification switch so the shipped default governs.

    The switch shipped off, so every install that predates this carries an
    explicit `false` that would outlive the default being flipped on -- and a
    grill that learns its own chamber is worth roughly ten times the overshoot
    on its second cook. The key is removed rather than rewritten so there is
    one place that decides, and it is the manifest.
    """
    controller = settings.get("controller")
    if not isinstance(controller, MutableMapping):
        return False
    config = controller.get("config")
    if not isinstance(config, MutableMapping):
        return False
    mpc = config.get("mpc")
    if not isinstance(mpc, MutableMapping):
        return False
    sentinel = object()
    return mpc.pop("enable_identification", sentinel) is not sentinel


_RETIRED_ACADOS_MPC_KEYS = (
    "policy",
    "policy_net_path",
    "t_step",
    "n_delay",
    "C_f",
    "h_fc",
    "feed_forward",
    "enable_grey_box",
    "mhe_horizon",
    "pw_state",
    "pw_dist",
    "px_state",
    "px_dist",
    "r_meas",
    "Q_min",
    "Q_max",
    "log_data",
    "log_path",
)


def _migrate_acados_mpc_settings(settings):
    """Normalize the persisted MPC shape for the fixed acados grey-box model."""
    controller = settings.get("controller")
    if not isinstance(controller, MutableMapping):
        return False
    config = controller.get("config")
    if not isinstance(config, MutableMapping):
        return False
    mpc = config.get("mpc")
    if not isinstance(mpc, MutableMapping):
        return False

    changed = False
    if mpc.get("estimator") == "mhe":
        mpc["estimator"] = "ekf"
        changed = True

    horizon = mpc.get("n_horizon")
    if isinstance(horizon, int) and not isinstance(horizon, bool):
        integral_horizon = horizon
    elif isinstance(horizon, float) and horizon.is_integer():
        integral_horizon = int(horizon)
    else:
        integral_horizon = None
    if integral_horizon is not None:
        clamped_horizon = min(24, max(5, integral_horizon))
        if type(horizon) is not int or clamped_horizon != horizon:
            mpc["n_horizon"] = clamped_horizon
            changed = True

    sentinel = object()
    for key in _RETIRED_ACADOS_MPC_KEYS:
        if mpc.pop(key, sentinel) is not sentinel:
            changed = True
    return changed


def _add_mcp2221_selector(settings):
    """Seed the USB selector introduced for the MCP2221 relay platform."""
    platform = settings.get("platform")
    if not isinstance(platform, MutableMapping) or "mcp2221" in platform:
        return False
    platform["mcp2221"] = {"serial": ""}
    return True


#: The shape migrations, in ascending order, as (target_version, migration).
#: A step's number is the version the tree is AT once that step has run, and
#: each callable mutates the tree in place and returns True if it changed
#: anything. Gated on settings["schema_version"] alone -- the release-gated
#: cascade in upgrade_settings() below is closed to new entries.
_SHAPE_MIGRATIONS = [
    (1, _migrate_i2c_buses),
    (3, _migrate_retired_controllers),
    (4, _remove_retired_mpc_logging_settings),
    (5, _remove_mpc_affine_load_bounds),
    (6, _clear_mpc_identification_choice),
    (7, _remove_retired_fan_pid),
    (8, _add_mcp2221_selector),
    (9, _migrate_acados_mpc_settings),
]


def _apply_shape_migrations(settings, target_version):
    """Apply every missing shape migration, then atomically advance its stamp."""
    stamp = settings.get("schema_version", 0)
    if stamp > target_version:
        return False

    changed = False
    for target, migrate in _SHAPE_MIGRATIONS:
        if stamp < target and migrate(settings):
            changed = True
    if stamp != target_version:
        # The stamp is written only after every applicable migration succeeds,
        # so an interrupted import retries the complete sequence.
        settings["schema_version"] = target_version
        changed = True
    return changed


def upgrade_settings(prev_ver, settings, settings_default):
    """Check if upgrading from v1.4.x or earlier"""
    if prev_ver[0] <= 1 and prev_ver[1] <= 4:
        settings["versions"] = settings_default["versions"]
        settings["globals"]["first_time_setup"] = True  # Force configuration for probes
        # v1.4's legacy top-level start_to_mode only carried grill1_setpoint.
        # Reshape it (in place, at the top level) into the modern start_to_mode
        # structure -- carrying the user's configured setpoint into
        # primary_setpoint -- so the later v1.6/1.7 startup-split block below
        # moves the real, populated value (not an empty dict) into
        # settings["startup"]["start_to_mode"]. Popping only the grill1_setpoint
        # sub-key here (leaving {}) let that later block clobber the migrated
        # value with an empty dict, crashing the controller on first startup.
        legacy_setpoint = settings["start_to_mode"].get(
            "grill1_setpoint", settings_default["startup"]["start_to_mode"]["primary_setpoint"]
        )
        settings["start_to_mode"] = copy.deepcopy(settings_default["startup"]["start_to_mode"])
        settings["start_to_mode"]["primary_setpoint"] = legacy_setpoint
        settings["dashboard"] = settings_default["dashboard"]
        # Move Notification Settings
        settings["notify_services"] = {}
        for key in settings_default["notify_services"].keys():
            settings["notify_services"][key] = settings[key]
            settings.pop(key, None)
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
        # Move the top-level start_to_mode into the new startup section. Treat a
        # present-but-EMPTY dict the same as missing (fall back to defaults) so a
        # partially-migrated legacy value can never leave startup.start_to_mode
        # empty (which would KeyError the controller on first startup).
        settings["startup"]["start_to_mode"] = settings.get("start_to_mode") or copy.deepcopy(
            settings_default["startup"]["start_to_mode"]
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

    """ Check if upgrading from previous to v1.11 or from v1.11.0 build 71 """
    if (prev_ver[0] == 1 and prev_ver[1] == 11 and settings["versions"].get("build", 0) <= 71) or (
        prev_ver[0] == 1 and prev_ver[1] < 11
    ):
        _migrate_i2c_buses(settings)

    settings["globals"]["updated_message"] = True  # Display updated message after reset/reboot
    return settings


def downgrade_settings(settings, settings_default):
    """Look for backup file for the downgrade"""
    backup_manifest = read_generic_json(BACKUP_PATH + "manifest.json")
    if backup_manifest == {}:
        backup_manifest = {"server_settings": {}, "pelletdb": {"current": ""}}
        write_generic_json(backup_manifest, BACKUP_PATH + "manifest.json")
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
    backup_manifest = read_generic_json(BACKUP_PATH + "manifest.json")
    if backup_manifest == {}:
        backup_manifest = {"server_settings": {}, "pelletdb": {"current": ""}}
        write_generic_json(backup_manifest, BACKUP_PATH + "manifest.json")
    server_version = settings_default["versions"]["server"]
    backup_settings_file = backup_manifest["server_settings"].get(server_version, None)
    if backup_settings_file is not None:
        warning = f"Something failed when reading the settings file.  Restoring settings from the following backup settings file: {backup_settings_file}."
        # Read the backup FILE (not SQLite -- that's the current, possibly
        # corrupt/absent, state we're recovering from).
        settings = read_settings_file(filename=backup_settings_file)
    else:
        warning = "Something failed when reading the settings file.  Resetting settings to defaults, since no backup settings files were found."
        settings = settings_default
    # Make the recovered settings the new current state in SQLite.
    write_settings_store(settings)
    write_warning(warning)
    write_log(warning)
    return settings
