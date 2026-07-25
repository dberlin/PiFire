"""Optional per-controller Python dependencies: detection and on-demand install.

Most controllers need nothing beyond the base install. MPC needs do-mpc, which
drags in CasADi -- no Linux-ARM wheel, so every Pi compiles it from source
(minutes, plus torch/onnx via do-mpc's own [full] extra: gigabytes onto an SD
card). That is unacceptable to inflict on every PiFire install, so do-mpc lives
in the `mpc` optional-dependency extra in pyproject.toml and `uv sync --no-dev`
-- what the installers run -- deliberately leaves it out.

The cost of that is a hole this module fills: selecting a controller whose extra
is absent must not be allowed to reach the control loop. Two rules:

  1. NOTHING is hardcoded about *what* to install. controllers.json declares the
     name of PiFire's own pyproject extra ("mpc"), never a requirement string --
     the spec `do-mpc[full]>=5.1.1` exists in exactly one place, pyproject.toml,
     and the install command inherits it via `--extra`/the lockfile. A literal
     duplicated here is precisely the kind of thing that goes stale.
  2. Whether a *given config* actually needs the extra is the controller
     module's question, not ours: `controller.mpc.requires_modules(config)` knows
     that policy='net' + EKF is pure numpy/scipy and needs nothing. We ask it
     rather than re-deriving its constructor's branches.

The install itself is DETACHED (see `install_extra_detached`) because a CasADi
source build takes minutes: it must never block an HTTP request, and it must
never run inside the control process, which is holding a live fire.
"""

import importlib
import importlib.util
import os
import subprocess
import sys
import time

from common.common import read_generic_json
from common.datastore_accessors import read_generic_key, write_generic_key

# Directory containing pyproject.toml / controllers.json. Derived from this
# file's location, not the cwd: the detached installer and the Flask process do
# not necessarily share one.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTROLLERS_JSON = os.path.join(PROJECT_ROOT, "controller", "controllers.json")

_STATE_KEY_PREFIX = "extra_install:"

# An install still marked "running" after this long is presumed dead (process
# killed, box rebooted mid-build) and may be relaunched. A CasADi source build
# on a Pi is minutes, not tens of minutes, so this is generous on purpose.
STALE_INSTALL_SECONDS = 45 * 60


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

    Absent for every controller that needs nothing, which is all of them but MPC.
    """
    entry = _metadata(metadata).get(selected) or {}
    return entry.get("dependencies") or {}


def required_modules_for(selected, config, metadata=None):
    """Import names `selected` will need for THIS config.

    Prefers the controller module's own `requires_modules(config)` hook, which is
    config-aware (MPC's policy='net' path needs nothing). Falls back to the
    manifest's static list when there is no hook, or when importing the module to
    ask fails -- failing towards "it is needed" keeps the gate conservative.
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


def check_controller_dependencies(selected, config, metadata=None):
    """None if `selected` can be constructed here, else a MissingDependency."""
    needed = required_modules_for(selected, config, metadata)
    if not needed:
        return None
    missing = missing_modules(needed)
    if not missing:
        return None
    return MissingDependency(selected, controller_dependencies(selected, metadata).get("extra"), missing)


# --- install state, shared between the web process and the detached installer ---


def install_state(extra):
    """The last recorded state for `extra`, or None if it was never attempted."""
    try:
        return read_generic_key(_STATE_KEY_PREFIX + extra)
    except Exception:
        # read_generic_key json.loads()es a None blob when the key is absent.
        return None


def set_install_state(extra, state, message=""):
    """Record install progress. `state` is running | done | failed."""
    write_generic_key(_STATE_KEY_PREFIX + extra, {"state": state, "message": message, "updated": time.time()})


def install_running(extra, now=None):
    state = install_state(extra)
    if not state or state.get("state") != "running":
        return False
    try:
        updated = float(state.get("updated") or 0)
    except TypeError, ValueError:
        return False
    return (time.time() if now is None else now) - updated < STALE_INSTALL_SECONDS


def installer_command(extra, python_exec=None):
    """argv that launches the detached installer for `extra`.

    A module (`-m`) rather than a top-level script so it cannot be started with
    the wrong sys.path, and a plain argv list rather than a shell string so the
    extra name is never re-parsed by a shell.
    """
    return [python_exec or sys.executable or "python", "-m", "common.extra_installer", extra]


def install_extra_detached(extra, python_exec=None, popen=None):
    """Start `extra`'s install in a detached process; return (started, message).

    Detached (`start_new_session=True`, no pipes held open) so it outlives the
    gunicorn worker that started it -- a CasADi build easily outlasts a worker
    recycle, and a half-finished venv is worse than none.
    """
    if install_running(extra):
        return False, "An install of this dependency is already in progress."
    command = installer_command(extra, python_exec=python_exec)
    # Marked running BEFORE the spawn: two Save clicks in quick succession must
    # not start two builds into the same venv.
    set_install_state(extra, "running", "Starting installation...")
    try:
        (popen or subprocess.Popen)(
            command,
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except Exception as exc:
        set_install_state(extra, "failed", f"Could not start the installer: {exc}")
        return False, f"Could not start the installer: {exc}"
    return True, ""


def dependency_message(missing, started, detail=""):
    """The sentence the user sees when a controller selection is refused.

    Says what is wrong, what is being done about it, that their grill is
    untouched, and what to do next -- in that order.
    """
    names = ", ".join(missing.modules)
    head = f"The {missing.controller.upper()} controller needs the {names} package, which is not installed."
    if missing.extra is None:
        return f"{head} PiFire has no automatic install for it; install it manually and try again. The controller is unchanged."
    if started:
        return (
            f"{head} Installing it in the background now -- this can take several minutes on a Pi. "
            f"The controller is unchanged; select {missing.controller.upper()} again once the install finishes."
        )
    if detail:
        return f"{head} {detail} The controller is unchanged."
    return f"{head} The controller is unchanged."
