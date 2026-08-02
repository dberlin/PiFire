"""Detached installer for one PiFire optional-dependency extra.

Started by `common.controller_deps.install_extra_detached` as

    <venv python> -m common.extra_installer mpc

in its own session, so it survives the gunicorn worker that spawned it. It is
never run in-process: installing the `mpc` extra compiles CasADi from source,
which is minutes of CPU on a Pi.

The requirement spec is NEVER written here. Both install paths inherit it from
pyproject.toml/uv.lock, so `do-mpc>=5.1.1` lives in exactly one place:

  * uv (the default, settings['globals']['uv']):
        uv sync --frozen --inexact --no-dev --extra <extra>
    --frozen installs exactly what uv.lock pins -- the lock already carries
    `do-mpc` with extras ["full"] under `marker = "extra == 'mpc'"` -- so there
    is no resolution step and no way to drift from what was tested. --inexact is
    load-bearing: a plain `uv sync` PRUNES anything not in the lock, and the
    wizard installs per-module dependencies with `uv pip install`, which are not
    in the lock. Without it, installing the MPC extra would silently uninstall
    the user's probe/display drivers.
  * pip (legacy non-uv installs): the requirement strings are read out of
    pyproject.toml's [project.optional-dependencies] and passed as argv, so a
    hand-maintained copy cannot drift from the declared spec. Each string is one
    argv entry and never goes through a shell, so an extras marker on some
    future extra (`pkg[a,b]>=1`) survives intact rather than being word-split.

Outcome is reported three ways: the install-state blob (polled by the settings
gate, so a second Save says something useful), a dashboard warning/error banner
(what the user actually sees), and logs/dependency-install.log (the detail).
"""

import os
import subprocess
import sys
import tomllib

from common.controller_deps import PROJECT_ROOT, set_install_state
from common.common import ErrorKind
from common.datastore_accessors import read_errors, write_errors, write_warning

LOG_PATH = os.path.join(PROJECT_ROOT, "logs", "dependency-install.log")


def extra_requirements(extra, project_root=None):
    """The requirement strings pyproject.toml declares for `extra`."""
    path = os.path.join(project_root or PROJECT_ROOT, "pyproject.toml")
    with open(path, "rb") as handle:
        pyproject = tomllib.load(handle)
    return list(pyproject.get("project", {}).get("optional-dependencies", {}).get(extra, []))


def build_install_command(extra, use_uv=True, python_exec=None, project_root=None):
    """argv for installing `extra`. See the module docstring for the rationale."""
    if use_uv:
        return ["uv", "sync", "--frozen", "--inexact", "--no-dev", "--extra", extra]
    requirements = extra_requirements(extra, project_root=project_root)
    if not requirements:
        raise ValueError(f"pyproject.toml declares no optional-dependencies entry named {extra!r}")
    return [python_exec or sys.executable or "python", "-m", "pip", "install", *requirements]


def _log(line):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as handle:
            handle.write(line.rstrip() + "\n")
    except OSError:
        pass


def run_streaming(command, on_line, cwd=None):
    """Run `command`, feeding each output line to `on_line`; return its exit code."""
    process = subprocess.Popen(
        command,
        cwd=cwd or PROJECT_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
    )
    for line in process.stdout:
        stripped = line.strip()
        if stripped:
            on_line(stripped)
    return process.wait()


def _append_error(text):
    # ErrorKind.WEB: this runs as a child of the webapp's install request, so
    # its banners belong to the web tier and survive a control-process restart.
    errors = read_errors(ErrorKind.WEB)
    errors.append(text)
    write_errors(ErrorKind.WEB, errors)


def install(extra, use_uv=True, python_exec=None, runner=None, project_root=None):
    """Install `extra` and report the outcome. Returns True on success.

    `runner` exists so tests can drive every branch without ever launching a
    real install -- see tests/unit/deps/test_extra_installer.py.
    """
    try:
        command = build_install_command(extra, use_uv=use_uv, python_exec=python_exec, project_root=project_root)
    except Exception as exc:
        message = f"Could not work out how to install the '{extra}' dependency: {exc}"
        _log(message)
        set_install_state(extra, "failed", message)
        _append_error(message)
        return False

    _log(f"--- installing extra '{extra}': {' '.join(command)}")
    set_install_state(extra, "running", f"Installing '{extra}'...")
    write_warning(
        f"Installing the optional '{extra}' dependency in the background. "
        f"This can take several minutes; your grill is unaffected."
    )

    def on_line(line):
        _log(line)
        set_install_state(extra, "running", line[:200])

    try:
        return_code = run_streaming(command, on_line, cwd=project_root) if runner is None else runner(command, on_line)
    except Exception as exc:
        message = f"Installing the optional '{extra}' dependency failed to start: {exc}"
        _log(message)
        set_install_state(extra, "failed", message)
        _append_error(f"{message} See logs/dependency-install.log.")
        return False

    if return_code == 0:
        _log(f"--- installed extra '{extra}'")
        set_install_state(extra, "done", f"Installed '{extra}'.")
        write_warning(f"The optional '{extra}' dependency finished installing. You can now select that controller.")
        return True

    message = f"Installing the optional '{extra}' dependency failed (exit code {return_code})."
    _log(message)
    set_install_state(extra, "failed", message)
    _append_error(f"{message} See logs/dependency-install.log for details.")
    return False


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m common.extra_installer <extra>", file=sys.stderr)
        return 2
    extra = argv[0]
    # Read late and defensively: a broken settings blob must not stop an install
    # the user is waiting on. uv is the default for every current installer.
    use_uv = True
    python_exec = None
    try:
        from common.datastore_accessors import read_settings

        globals_section = read_settings().get("globals", {})
        use_uv = bool(globals_section.get("uv", True))
        python_exec = globals_section.get("python_exec")
    except Exception:
        pass
    return 0 if install(extra, use_uv=use_uv, python_exec=python_exec) else 1


if __name__ == "__main__":
    raise SystemExit(main())
