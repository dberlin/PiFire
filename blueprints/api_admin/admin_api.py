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
import pathlib
import sqlite3
import tempfile
import zipfile

from common import datastore, log_actions
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

#: Owned by common/log_actions.py, which every transport shares. Re-bound here
#: rather than read through the module so this surface keeps its own name to
#: resolve: every helper below does `folder or LOG_FOLDER` and passes the result
#: down explicitly, which is what lets a test repoint just this surface.
LOG_FOLDER = log_actions.LOG_FOLDER


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


def list_log_families(folder=None):
    """{stem: [member filenames, OLDEST FIRST]}; see common.log_actions.

    list_logs() above deliberately keeps its flat `.log`-only contract: the
    admin page's LogsCard is built against it. This is the view that can see a
    rotated file.

    `folder` resolves at call time; see list_logs.
    """
    return log_actions.list_log_families(folder or LOG_FOLDER)


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


def log_family_listing(folder=None):
    """[{stem, members, bytes}] for every family, ordered by stem.

    `bytes` is the STITCHED total across the family, not the size of the newest
    member. The client seeds its tail cursor from it, and a cursor that started
    at the size of `events.log` alone would sit mid-stream -- the first poll
    would then hand back lines the viewer had already drawn.

    `folder` resolves at call time; see list_logs.
    """
    folder = folder or LOG_FOLDER
    listing = []
    for stem, members in list_log_families(folder).items():
        total = 0
        for name in members:
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                continue
        listing.append({"stem": stem, "members": members, "bytes": total})
    return listing


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
    """Empty the event log in BOTH stores; see common.log_actions.

    Flask runs `os.system("rm ./logs/events.log")` for this, and so did the
    mobile Socket.IO surface. Three things that single `rm` misses -- rotated
    members, the database rows, and the descriptor the running process still
    holds open -- are handled once, in the shared action.

    Returns True: _MAINTENANCE_ACTIONS dispatches this and the admin page's
    MaintenanceCard is built against the resulting response shape.

    `folder` resolves at call time; see list_logs.
    """
    return log_actions.clear_events_log(folder or LOG_FOLDER)


def delete_logs(folder=None):
    """Clear every member of every log family; see common.log_actions.

    Rotated members are included. Deleting only `*.log` left the backups on
    disk, so the log viewer still had content to show after a "Delete All" --
    the operation reported success while the user could see it had not worked.

    "Cleared", not "removed", for the live member of each family: it is
    truncated in place so that handlers already holding it open keep writing
    where the viewer looks.

    `folder` resolves at call time; see list_logs.
    """
    return log_actions.clear_all_logs(folder or LOG_FOLDER)


def _archive_log_families(archive, folder, prefix=""):
    """Write every log family member into an already-open ZipFile.

    Shared by the logs-only archive and the diagnostics bundle so the two cannot
    drift into disagreeing about which files count as logs -- rotated members
    included, which is the part a plain `.log` glob gets wrong.
    """
    for members in list_log_families(folder).values():
        for name in members:
            archive.write(os.path.join(folder, name), arcname=prefix + name)


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
        _archive_log_families(zf, folder)
    return archive


def _snapshot_database(destination):
    """Write a consistent, standalone copy of the live database to `destination`.

    Deliberately NOT shutil.copy. datastore opens pifire.db in WAL mode and
    control.py, app.py and display_process.py all write to it while this runs, so
    the bytes of the main database file on their own are a torn snapshot: every
    row still sitting in pifire.db-wal is missing from it, and the recipient reads
    a database that silently stops short of the moment they cared about. Shipping
    the -wal and -shm alongside would be the other way out, and is worse -- three
    files a recipient must keep together and a format that is only readable by a
    compatible SQLite.

    VACUUM INTO reads the source through one consistent transaction and emits a
    fully checkpointed database that stands alone. It is read-only with respect
    to the source, so a diagnostics download cannot disturb a running cook.
    """
    source = datastore.DB_PATH
    try:
        #  Read-only for the same reason: a plain connection that happens to be
        #  the last one open checkpoints and truncates the live WAL on close.
        connection = sqlite3.connect(f"{pathlib.Path(source).resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        #  Read-only access to a WAL database needs a -shm to share; if no writer
        #  has left one behind there is nothing live to protect either.
        connection = sqlite3.connect(source)
    try:
        try:
            connection.execute("VACUUM INTO ?", (destination,))
        except sqlite3.OperationalError:
            #  VACUUM INTO landed in SQLite 3.27 (Pi OS ships far newer). The
            #  backup API is older and equally consistent against live writers.
            target = sqlite3.connect(destination)
            try:
                connection.backup(target)
            finally:
                target.close()
    finally:
        connection.close()


def build_diagnostics_bundle(folder=None):
    """Zip a database snapshot plus every log into a temp file; return its path.

    One artifact to hand whoever is debugging a grill: the database carries
    settings, history, control traces and the database-backed logs, and the log
    folder carries what only ever exists as files. There is no settings.json to
    include -- nothing writes one; settings live in the database.

    Same private-mkdtemp staging as build_log_archive, for the same reason.
    """
    folder = folder or LOG_FOLDER
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    staging = tempfile.mkdtemp(prefix="pifire-diagnostics-")
    snapshot = os.path.join(staging, "pifire.db")
    _snapshot_database(snapshot)
    archive = os.path.join(staging, f"PiFire_Diagnostics_{stamp}.zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(snapshot, arcname="pifire.db")
        _archive_log_families(zf, folder, prefix="logs/")
    #  The snapshot is a whole second copy of the database; it has served its
    #  purpose once it is inside the zip.
    os.remove(snapshot)
    return archive


def pip_list():
    """The installed-package snapshot the admin page shows. Empty until
    `updater.py -p` has been run, which is not an error."""
    listing = read_generic_json("pip_list.json")
    return [] if listing == {} else listing
