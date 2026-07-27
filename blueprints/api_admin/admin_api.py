"""Admin handlers for /api/admin/*.

None of these shells out. The Flask admin blueprint reaches a shell in two
places -- `os.system("rm ./logs/events.log")` and `os.system("rm logs/*.log")` --
and this surface deliberately does not inherit either; the equivalents here use
os.remove against a server-built path.

The genuinely dangerous operations (reboot, shutdown, restart, factory reset)
keep their existing common/system.py implementations. This module is a door, not
a reimplementation -- there is exactly one place in the tree that knows how to
power the machine off, and it stays that way.
"""

import os

from common.common import read_generic_json
from common.system import gather_system_info

#: backup_settings() writes PiFire_<ts>.json and backup_pellet_db() writes
#: PelletDB_<ts>.json into one folder, so the prefix is the only thing that
#: distinguishes them. manifest.json shares the folder and is bookkeeping --
#: offering it as restorable would let a user overwrite live settings with a
#: manifest.
_BACKUP_PREFIXES = {"settings": "PiFire_", "pelletdb": "PelletDB_"}

LOG_FOLDER = "./logs/"


def list_backups(folder):
    """{kind: [bare filenames]}. A missing folder is empty, not an error -- a
    fresh install has no ./backups until something writes one."""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        names = []
    return {
        kind: [n for n in names if n.startswith(prefix) and n.endswith(".json")]
        for kind, prefix in _BACKUP_PREFIXES.items()
    }


def list_logs(folder=LOG_FOLDER):
    """Bare .log filenames. Same missing-folder tolerance as list_backups."""
    try:
        return sorted(n for n in os.listdir(folder) if n.endswith(".log"))
    except OSError:
        return []


def state_payload(settings, control, backup_folder):
    """Everything the admin page renders, in one read.

    `mode` rides along because every destructive control on the page is disabled
    unless the grill is stopped, and a second round trip to learn that would let
    the two disagree.

    gather_system_info populates control["system"] in place and writes control,
    so this read has a write behind it. That is inherited from admin_page(),
    which does the same -- the readings (cpu temp, wifi quality, throttling) are
    gathered on demand rather than published by the control loop.
    """
    system_info, _failures = gather_system_info(control, origin="api-admin")
    return {
        "system": system_info,
        "settings": {
            "debug_mode": settings["globals"].get("debug_mode", False),
            "boot_to_monitor": settings["globals"].get("boot_to_monitor", False),
        },
        "backups": list_backups(backup_folder),
        "logs": list_logs(),
        "mode": control.get("mode", ""),
    }


def pip_list():
    """The installed-package snapshot the admin page shows. Empty until
    `updater.py -p` has been run, which is not an error."""
    listing = read_generic_json("pip_list.json")
    return [] if listing == {} else listing
