"""Controller availability checks at the settings boundary.

Catalog metadata may declare importable modules for a controller. MPC instead
uses the repository-published acados native runtime and has no optional Python
dependency or on-demand installer.
"""

import importlib
import importlib.util
import os

from common.common import read_generic_json

# Directory containing pyproject.toml / controllers.json. Derived from this
# file's location, not the cwd: the detached installer and the Flask process do
# not necessarily share one.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTROLLERS_JSON = os.path.join(PROJECT_ROOT, "controller", "controllers.json")


class MissingDependency:
    """A selected controller needs `modules`, which are not importable.

    `extra` is the pyproject extra that provides them, or None when
    controllers.json declares modules but no extra -- i.e. we can detect the
    problem but have no supported way to fix it automatically.
    """

    def __init__(self, controller, extra, modules):
        self.controller = controller
        self.extra = extra
        self.modules = tuple(modules)

    def __repr__(self):
        return f"MissingDependency(controller={self.controller!r}, extra={self.extra!r}, modules={self.modules!r})"


def _metadata(metadata=None):
    if metadata is not None:
        return metadata
    return read_generic_json(CONTROLLERS_JSON).get("metadata", {})


def controller_dependencies(selected, metadata=None):
    """The `dependencies` block controllers.json declares for `selected`.

    Absent for controllers whose dependencies are already in the base install.
    """
    entry = _metadata(metadata).get(selected) or {}
    return entry.get("dependencies") or {}


def required_modules_for(selected, config, metadata=None):
    """Import names `selected` will need for THIS config.

    Prefers the controller module's own `requires_modules(config)` hook: it is
    the one place that can answer per-config rather than per-controller.
    Falls back to the manifest's static list when there is no hook, or when
    importing the module to ask fails -- failing towards "it is needed" keeps
    the gate conservative.
    """
    declared = tuple(controller_dependencies(selected, metadata).get("modules") or ())
    if not declared:
        return ()
    try:
        module = importlib.import_module(f"controller.{selected}")
        hook = getattr(module, "requires_modules", None)
        if hook is None:
            return declared
        return tuple(hook(config))
    except Exception:
        return declared


def missing_modules(modules):
    """The subset of `modules` that is not importable in this interpreter."""
    missing = []
    for name in modules:
        try:
            found = importlib.util.find_spec(name) is not None
        except Exception:
            # find_spec raises rather than returning None when a parent package
            # is missing, and can raise from a broken installation.
            found = False
        if not found:
            missing.append(name)
    return tuple(missing)


def load_native():
    """Load and validate the published acados runtime for the MPC gate."""
    from controller.acados._library import load_native as load_acados_native

    return load_acados_native()


def check_controller_dependencies(selected, config, metadata=None):
    """None if `selected` can be constructed here, else a MissingDependency.

    MPC has no optional Python dependency: availability means a complete,
    ABI-compatible native release can be loaded.
    """
    if selected == "mpc":
        load_native()
        return None
    needed = required_modules_for(selected, config, metadata)
    if not needed:
        return None
    missing = missing_modules(needed)
    if not missing:
        return None
    return MissingDependency(selected, controller_dependencies(selected, metadata).get("extra"), missing)


def dependency_message(missing):
    """Describe a missing base dependency without attempting installation."""
    names = ", ".join(missing.modules)
    return (
        f"The {missing.controller.upper()} controller needs the {names} package, "
        "which is not installed. PiFire has no automatic install for it; install "
        "it manually and try again. The controller is unchanged."
    )


def guard_controller_selection(settings):
    """Refuse a settings save when its selected controller cannot be built."""
    try:
        selected = settings["controller"]["selected"]
        config = settings["controller"]["config"].get(selected, {})
    except KeyError, TypeError, AttributeError:
        return None
    try:
        missing = check_controller_dependencies(selected, config)
    except Exception as exc:
        return f"The {selected.upper()} controller is unavailable: {exc} The controller is unchanged."
    if missing is None:
        return None
    return dependency_message(missing)
