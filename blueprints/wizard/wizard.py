from common.datastore_accessors import read_settings


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
            settingsLocation = wizardData["modules"][module][selected]["settings_dependencies"][setting]["settings"]
            settingsValue = settings.copy()
            for index in range(0, len(settingsLocation)):
                settingsValue = settingsValue[settingsLocation[index]]
            wizardInstallInfo["modules"][module]["settings"][setting] = str(settingsValue)
        if module == "display":
            wizardInstallInfo["modules"][module]["config"] = settings["display"]["config"][
                settings["modules"]["display"]
            ]
    return wizardInstallInfo


def wizard_bus_kinds(wizardInstallInfo, wizardData):
    """Collect every configured I2C bus kind from an assembled wizardInstallInfo,
    using the user's in-progress selections: each probe device's config
    i2c_bus_kind, plus any grillplatform/distance settings-dependency whose
    manifest `settings` path ends in 'i2c_bus_kind' (the fan controller and the
    distance sensor). Used to validate the whole config at wizard finish."""
    kinds = set()
    for device in wizardInstallInfo.get("probe_map", {}).get("probe_devices", []):
        kind = (device.get("config") or {}).get("i2c_bus_kind")
        if kind:
            kinds.add(kind)
    for module in ("grillplatform", "distance"):
        module_info = wizardInstallInfo.get("modules", {}).get(module, {}) or {}
        selected = (module_info.get("profile_selected") or [None])[0]
        module_settings = module_info.get("settings", {}) or {}
        module_manifest = (wizardData.get("modules", {}).get(module, {}) or {}).get(selected, {}) or {}
        for dep_name, dep in (module_manifest.get("settings_dependencies", {}) or {}).items():
            path = dep.get("settings") or []
            if path and path[-1] == "i2c_bus_kind":
                value = module_settings.get(dep_name)
                if value:
                    kinds.add(value)
    return kinds


def wizard_bus_selectors(wizardInstallInfo, wizardData):
    """The same sweep as wizard_bus_kinds, carrying each bus's selector with it:
    [(where, kind, selector), ...] for common.i2c_bus.validate_bus_selectors.

    A kind is paired with the i2c_bus_num that sits BESIDE it -- the dependency
    whose manifest `settings` path is the same one with 'i2c_bus_kind' swapped
    for 'i2c_bus_num'. Pairing on the settings path rather than on the
    dependency's own name is what lets a module name its two fields whatever it
    likes (the grillplatform entries prefix theirs with device_distance_, the
    distance modules do not).
    """
    selectors = []
    for device in wizardInstallInfo.get("probe_map", {}).get("probe_devices", []):
        config = device.get("config") or {}
        if config.get("i2c_bus_kind"):
            selectors.append(
                (device.get("device") or "probe device", config["i2c_bus_kind"], config.get("i2c_bus_num"))
            )
    for module in ("grillplatform", "distance"):
        module_info = wizardInstallInfo.get("modules", {}).get(module, {}) or {}
        selected = (module_info.get("profile_selected") or [None])[0]
        module_settings = module_info.get("settings", {}) or {}
        deps = ((wizardData.get("modules", {}).get(module, {}) or {}).get(selected, {}) or {}).get(
            "settings_dependencies", {}
        ) or {}
        # path -> dep name, so a kind can find the num sharing its parent.
        by_path = {tuple(dep.get("settings") or []): name for name, dep in deps.items()}
        for path, dep_name in by_path.items():
            if not path or path[-1] != "i2c_bus_kind":
                continue
            kind = module_settings.get(dep_name)
            num_dep = by_path.get(path[:-1] + ("i2c_bus_num",))
            if kind and num_dep:
                selectors.append((f"{module}/{dep_name}", kind, module_settings.get(num_dep)))
    return selectors
