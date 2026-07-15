#!/usr/bin/env python3
"""PiFire display launcher -- run the display process inside a Wayland kiosk.

The QtQuick display backends (module names starting with ``qtquick_``) build a
QGuiApplication and need a real Wayland session to render onto. The SPI and
pygame displays draw straight to hardware / the framebuffer and need no
compositor. Supervisor calls this shim instead of display_process.py directly:
it decides which case applies and ``execvp``s into the right command, so
supervisor keeps supervising a single process and ``stopasgroup`` still tears
everything down.

This file lives at the repo root (not under display/, a package) and imports
nothing from display/, preserving the invariant that PySide6 is never loaded in
the display parent process.
"""

import logging
import os
import sys

from common import read_settings


def build_launch_argv(settings, env):
    """Return (argv, env_updates) for launching the display process.

    :param settings: settings dict (uses settings['modules']['display'])
    :param env: current environment mapping; read-only, never mutated
    :return: (argv, env_updates). argv is the exec argv list; env_updates is a
            dict of environment variables to set before exec (empty on the bare
            path).
    """
    child = [sys.executable, "display_process.py"]
    display_name = settings["modules"]["display"]
    if not display_name.startswith("qtquick_"):
        return child, {}

    runtime_dir = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    env_updates = {"QT_QPA_PLATFORM": "wayland", "XDG_RUNTIME_DIR": runtime_dir}
    return ["cage", "-d", "-s", "--", *child], env_updates


def _ensure_runtime_dir(path):
    """Create XDG_RUNTIME_DIR 0700 if it does not already exist."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)


def main():
    settings = read_settings()
    argv, env_updates = build_launch_argv(settings, os.environ)
    log = logging.getLogger("display_launch")
    logging.basicConfig()
    runtime_dir = env_updates.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        try:
            _ensure_runtime_dir(runtime_dir)
        except OSError:
            log.exception("Failed to prepare XDG_RUNTIME_DIR: %s", runtime_dir)
            sys.exit(1)
    os.environ.update(env_updates)
    try:
        os.execvp(argv[0], argv)
    except OSError:
        log.exception("Failed to exec: %s", " ".join(argv))
        sys.exit(1)


if __name__ == "__main__":
    main()
