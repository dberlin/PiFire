from common.datastore_accessors import read_settings
from common.i2c_bus_config import parse_i2c_bus


def parse_bt_device_info(bt_devices):
    settings = read_settings()
    # Check if this hardware id is already in use
    for index, peripheral in enumerate(bt_devices):
        for device in settings["probe_settings"]["probe_map"]["probe_devices"]:
            # print(f'[DEBUG] Comparing {device["name"]} ({device["config"].get('hardware_id', None)}) to {name} ({hw_id})')
            if device["config"].get("hardware_id", None) == peripheral["hw_id"]:
                bt_devices[index]["info"] += f"This hardware ID is already in use by {device['device']}"
                return bt_devices
    return bt_devices


def get_settings_dependencies_values(settings, moduleData):
    moduleSettings = {}
    for setting, data in moduleData["settings_dependencies"].items():
        setting_location = data["settings"]
        setting_value = settings
        try:
            for setting_name in setting_location:
                setting_value = setting_value[setting_name]
        except KeyError, TypeError:
            # The setting isn't present yet -- normal during initial setup (e.g. a
            # platform whose settings haven't been written before this wizard run,
            # or a module that introduces new settings keys). Fall back to None so
            # the card still renders and the user can pick a value.
            setting_value = None
        moduleSettings[setting] = _constrain_to_options(setting_value, data.get("options"))
    return moduleSettings


def _constrain_to_options(value, options):
    """Return `value` as one of `options`' keys, or `value` untouched when the
    dependency has no option list.

    A module's option list is what that module ALLOWS, so a value outside it is
    not a preference to preserve -- it belongs to whatever was configured
    before. Most of a grillplatform entry is exactly this: its identity
    (platform.current / system_type) and its soldered-in pins each offer a
    single option, and selecting the module is what imposes them. Carrying the
    live value through instead left the installer writing the previous
    platform's values back, so an FT232H build came back as `custom` running the
    prototype platform, and switching the other way wrote FT232H pin names
    ("C2") onto a Raspberry Pi board.

    Options keys are the stringified form of what settings hold (pin 23 is the
    option "23", an unwired pin is "None", a flag is "True"), so the match is on
    str(value) and the KEY is returned -- the same normalization
    wizardInstallInfoExisting applies to every value, and what the installer's
    _convert_value reverses on the way back in.

    A composite dependency (i2c_bus) reads as an object rather than a scalar,
    and the manifest never gives one an option list -- so `not options` alone
    is what lets it pass through untouched.
    """
    if not options:
        return value
    text = str(value)
    return text if text in options else next(iter(options))


def wizardInstallInfoDefaults(wizardData, settings):

    wizardInstallInfo = {
        "modules": {
            "grillplatform": {
                "profile_selected": [],  # Reference the profile in wizardData > wizard_manifest.json
                "settings": {},
                "config": {},
            },
            "display": {"profile_selected": [], "settings": {}, "config": {}},
            "distance": {"profile_selected": [], "settings": {}, "config": {}},
            "probes": {"profile_selected": [], "settings": {"units": "F"}, "config": {}},
        },
        "probe_map": {},
    }
    """ Populate Modules Info with Defaults from Wizard Data including Settings """
    for component in ["grillplatform", "display", "distance"]:
        for module in wizardData["modules"][component]:
            if wizardData["modules"][component][module]["default"]:
                """ Populate Module Filename"""
                wizardInstallInfo["modules"][component]["profile_selected"].append(
                    module
                )  # TODO: Change wizard.py to reference the module filename instead, or in grill_platform use platform>system_type
                for setting in wizardData["modules"][component][module]["settings_dependencies"]:
                    """ Populate all settings with default value """
                    dep = wizardData["modules"][component][module]["settings_dependencies"][setting]
                    if "options" in dep:
                        default_value = list(dep["options"].keys())[0]
                    else:
                        default_value = dep.get("default", "")
                    wizardInstallInfo["modules"][component]["settings"][setting] = default_value
                if module == "display":
                    wizardInstallInfo["modules"][component]["config"] = settings["display"]["config"][module]

    """ Populate the default probe device / probe map from the default PCB Board """
    wizardInstallInfo["probe_map"] = wizardData["boards"][
        wizardInstallInfo["modules"]["grillplatform"]["profile_selected"][0]
    ]["probe_map"]

    """ Populate Probes Module List with all configured probe devices """
    for device in wizardInstallInfo["probe_map"]["probe_devices"]:
        wizardInstallInfo["modules"]["probes"]["profile_selected"].append(device["module"])

    return wizardInstallInfo


def wizardInstallInfoExisting(wizardData, settings):
    wizardInstallInfo = {
        "modules": {
            "grillplatform": {"profile_selected": [settings["platform"]["current"]], "settings": {}, "config": {}},
            "display": {"profile_selected": [settings["modules"]["display"]], "settings": {}, "config": {}},
            "distance": {"profile_selected": [settings["modules"]["dist"]], "settings": {}, "config": {}},
            "probes": {"profile_selected": [], "settings": {"units": settings["globals"]["units"]}, "config": {}},
        },
        "probe_map": settings["probe_settings"]["probe_map"],
    }
    """ Populate Probes Module List with all configured probe devices """
    for device in wizardInstallInfo["probe_map"]["probe_devices"]:
        wizardInstallInfo["modules"]["probes"]["profile_selected"].append(device["module"])

    """ Populate Modules Info with current Settings """
    for module in ["grillplatform", "display", "distance"]:
        selected = wizardInstallInfo["modules"][module]["profile_selected"][0]
        """ Error condition if the item in settings doesn't match the wizard manifest """
        if selected not in wizardData["modules"][module].keys():
            if module == "grillplatform":
                selected = "custom"
                settings["platform"]["current"] = selected
            else:
                selected = "none"
            # profile_selected is ALWAYS a list, never a bare string -- an
            # invalid/stale saved module means "no selection".
            wizardInstallInfo["modules"][module]["profile_selected"] = []

        for setting in wizardData["modules"][module][selected]["settings_dependencies"]:
            dependency = wizardData["modules"][module][selected]["settings_dependencies"][setting]
            settingsLocation = dependency["settings"]
            settingsValue = settings.copy()
            for index in range(0, len(settingsLocation)):
                settingsValue = settingsValue[settingsLocation[index]]
            # A composite dependency (i2c_bus) is an object, not a scalar --
            # str()ing it would produce a Python repr the installer can never
            # parse back, so the manifest's declared type decides, not the
            # value's runtime shape. Every other setting still normalizes to
            # its string form for the option-matching in _constrain_to_options.
            wizardInstallInfo["modules"][module]["settings"][setting] = (
                settingsValue if dependency.get("type") == "i2c_bus" else str(settingsValue)
            )
        if module == "display":
            wizardInstallInfo["modules"][module]["config"] = settings["display"]["config"][
                settings["modules"]["display"]
            ]
    return wizardInstallInfo


def wizard_bus_kinds(wizardInstallInfo, wizardData):
    """Collect every active I2C bus kind from an assembled wizardInstallInfo,
    using the user's in-progress probe, distance-sensor, and EMC fan-controller
    selections. Used to validate the whole config at wizard finish."""
    kinds = set()
    for device in wizardInstallInfo.get("probe_map", {}).get("probe_devices", []):
        bus = (device.get("config") or {}).get("i2c_bus")
        if bus:
            kinds.add(parse_i2c_bus(bus).kind)
    distance_selected = (
        (wizardInstallInfo.get("modules", {}).get("distance", {}).get("profile_selected") or [None])[0] or ""
    ).lower()
    for module in ("grillplatform", "distance"):
        module_info = wizardInstallInfo.get("modules", {}).get(module, {}) or {}
        selected = (module_info.get("profile_selected") or [None])[0]
        module_settings = module_info.get("settings", {}) or {}
        module_manifest = (wizardData.get("modules", {}).get(module, {}) or {}).get(selected, {}) or {}
        dependencies = module_manifest.get("settings_dependencies", {}) or {}
        fan_chip = next(
            (
                module_settings.get(dep_name)
                for dep_name, dep in dependencies.items()
                if dep.get("settings") == ["platform", "fan_controller", "chip"]
            ),
            None,
        )
        fan_chip = str(fan_chip or "").lower()
        for dep_name, dep in dependencies.items():
            if dep.get("type") != "i2c_bus":
                continue
            if dep.get("settings") == ["platform", "devices", "distance", "i2c_bus"] and distance_selected not in {
                "vl53l0x",
                "vl53l4cd",
                "vl53l1x",
            }:
                continue
            if dep.get("settings") == ["platform", "fan_controller", "i2c_bus"] and fan_chip not in {
                "emc2101",
                "emc2301",
            }:
                continue
            value = module_settings.get(dep_name)
            if value:
                kinds.add(parse_i2c_bus(value).kind)
    return kinds
