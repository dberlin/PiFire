"""Live probe-map application logic, kept Flask-free.

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
