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

import datetime
import os
import re
import tempfile
import zipfile

from common.common import read_generic_json
from common.system import gather_system_info

#: backup_settings() writes PiFire_<ts>.json and backup_pellet_db() writes
#: PelletDB_<ts>.json into one folder, so the prefix is the only thing that
#: distinguishes them. manifest.json shares the folder and is bookkeeping --
#: offering it as restorable would let a user overwrite live settings with a
#: manifest.
_BACKUP_PREFIXES = {"settings": "PiFire_", "pelletdb": "PelletDB_"}

#: The two kinds a client may name. Exposed so routes validate against one list.
BACKUP_KINDS = frozenset(_BACKUP_PREFIXES)

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


def list_logs(folder=None):
    """Bare .log filenames. Same missing-folder tolerance as list_backups.

    `folder` resolves at CALL time rather than defaulting to LOG_FOLDER in the
    signature: a default argument binds once at definition, so a test that
    monkeypatches the module constant would never be seen.
    """
    folder = folder or LOG_FOLDER
    try:
        return sorted(n for n in os.listdir(folder) if n.endswith(".log"))
    except OSError:
        return []


#: A log family member is `<stem>.log`, or `<stem>.log.<n>` once rotated.
#: Anything else in the folder -- logfiles.txt, stray notes -- is not a log and
#: is not offered.
_LOG_MEMBER = re.compile(r"^(?P<stem>.+)\.log(?:\.(?P<index>\d+))?$")


def list_log_families(folder=None):
    """{stem: [member filenames, OLDEST FIRST]}.

    RotatingFileHandler shifts suffixes upward on rollover -- `x.log` becomes
    `x.log.1`, `x.log.1` becomes `x.log.2` -- so the highest-numbered member is
    the oldest and sorts first. `x.log` itself is index 0 and sorts last.

    list_logs() above deliberately keeps its flat `.log`-only contract: the
    admin page's LogsCard is built against it. This is the view that can see a
    rotated file.

    `folder` resolves at call time; see list_logs.
    """
    folder = folder or LOG_FOLDER
    try:
        names = os.listdir(folder)
    except OSError:
        return {}
    families = {}
    for name in names:
        match = _LOG_MEMBER.match(name)
        if match:
            index = int(match["index"] or 0)
            families.setdefault(match["stem"], []).append((index, name))
    return {
        stem: [name for _, name in sorted(members, key=lambda pair: -pair[0])]
        for stem, members in sorted(families.items())
    }


def stitch_family(stem, folder=None):
    """One family as a single byte stream, oldest first, or None if unknown.

    The stem is looked up in list_log_families rather than joined onto a path,
    so no client-supplied path component reaches the filesystem anywhere in this
    function. Flask's logs page concatenates a request field onto the logs
    folder in two places (send_file and read_log_file); this shape is what makes
    that hole unreachable rather than merely unlikely.

    `folder` resolves at call time; see list_logs.
    """
    folder = folder or LOG_FOLDER
    members = list_log_families(folder).get(stem)
    if members is None:
        return None
    chunks = []
    for name in members:
        try:
            with open(os.path.join(folder, name), "rb") as handle:
                chunk = handle.read()
        except OSError:
            continue
        #  A member truncated without its final newline would otherwise weld its
        #  last line onto the next member's first.
        if chunk and not chunk.endswith(b"\n"):
            chunk += b"\n"
        chunks.append(chunk)
    return b"".join(chunks)


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


def clear_events_log(folder=None):
    """Delete the events log.

    Flask runs `os.system("rm ./logs/events.log")` for this. The path is built
    here rather than handed to a shell -- no interpolation, no shell, and a
    missing file is success rather than a silently swallowed `rm` error.

    `folder` resolves at call time; see list_logs.
    """
    folder = folder or LOG_FOLDER
    try:
        os.remove(os.path.join(folder, "events.log"))
    except FileNotFoundError:
        pass
    return True


def delete_logs(folder=None):
    """Delete every member of every log family, reporting what went.

    Flask runs `os.system("rm logs/*.log")` inside a bare `except:`, so a
    failure is indistinguishable from success. This enumerates server-side and
    names what it removed.

    Rotated members are included. Deleting only `*.log` left the backups on
    disk, so the log viewer still had content to show after a "Delete All" --
    the operation reported success while the user could see it had not worked.

    `folder` resolves at call time; see list_logs.
    """
    folder = folder or LOG_FOLDER
    removed = []
    for members in list_log_families(folder).values():
        for name in members:
            try:
                os.remove(os.path.join(folder, name))
                removed.append(name)
            except OSError:
                continue
    return sorted(removed)


def build_log_archive(folder=None):
    """Zip every log family member into a temp file and return its path.

    Staged in a private mkdtemp rather than a predictable /tmp name: a
    world-writable, guessable path is how an attacker plants or reads content
    a later step trusts. The caller send_file()s it; the OS reaps the temp dir.
    """
    folder = folder or LOG_FOLDER
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    staging = tempfile.mkdtemp(prefix="pifire-logs-")
    archive = os.path.join(staging, f"PiFire_Logs_{stamp}.zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for members in list_log_families(folder).values():
            for name in members:
                zf.write(os.path.join(folder, name), arcname=name)
    return archive


def pip_list():
    """The installed-package snapshot the admin page shows. Empty until
    `updater.py -p` has been run, which is not an error."""
    listing = read_generic_json("pip_list.json")
    return [] if listing == {} else listing
