"""Guards for the live probe-map path, kept Flask-free.

The Flask wizard's probeconfig fragment API (blueprints/probeconfig/routes.py)
edits the wizard STAGING blob (`wizard:install`), and its edits reach live
settings only when the detached installer runs (wizard.py:227). This module
is the other path: applying a probe map straight to
settings["probe_settings"]["probe_map"] on a running PiFire, with the
guards the installer would otherwise have provided.

The one guard the installer provides that this module CANNOT is dependency
INSTALLATION (wizard.py:319-430 runs apt/pip/command_list per selected
module). So this module refuses instead: a probe module may be added here
only when it is already present in the live map (hence already installed),
or when its manifest declares no dependencies at all.

The application itself is NOT here: both this path and the installer call
common.defaults.set_probe_map(), which is what keeps the two from drifting
apart on what a probe rename has to update. It cannot live in this package --
importing anything under blueprints.api pulls in Flask and every route, and
wizard.py is a detached process that must not.
"""


def module_requires_install(module_data):
    """True when adding this probe module would need the wizard's installer.

    Reads the same three manifest lists wizard.py:319-334 collects. Six of
    the 18 probe modules declare none of them (max31865, prototype and the
    four virtual_* reducers) and are therefore safe to add on a running
    system; the other twelve are not.
    """
    if not isinstance(module_data, dict):
        return True
    return bool(
        module_data.get("py_dependencies") or module_data.get("apt_dependencies") or module_data.get("command_list")
    )


def valid_probe_map(probe_map):
    """The outer shape only, matching common/settings_schema.py:229-234's
    ProbeMap (probe_devices/probe_info are list[dict]; their contents are
    driver-specific and stay loose)."""
    if not isinstance(probe_map, dict):
        return False
    for key in ("probe_devices", "probe_info"):
        value = probe_map.get(key)
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            return False
    return True


def unsupported_new_modules(new_map, live_map, manifest_modules):
    """Modules the caller is ADDING that this path cannot install.

    A module already present in `live_map` was installed by a previous wizard
    run, so it is always allowed. Anything else must declare no dependencies
    (module_requires_install() == False). An unknown module -- not in the
    manifest at all -- is refused too: module_requires_install() returns True
    for a non-dict, so a stale/renamed module cannot slip through.
    """
    installed = {d.get("module") for d in live_map.get("probe_devices", []) if d.get("module")}
    offenders = set()
    for device in new_map.get("probe_devices", []):
        module = device.get("module")
        if not module or module in installed:
            continue
        if module_requires_install(manifest_modules.get(module)):
            offenders.add(module)
    return sorted(offenders)
