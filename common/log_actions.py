"""Clearing logs, for every transport that offers it.

Both transports used to run a shell here -- `os.system("rm logs/*.log")` on the
admin surface and `os.system("rm ./logs/events.log")` on the mobile Socket.IO
one. That is the same shape common/pellets_actions.py was extracted for, and for
the same reason: two copies of a destructive action drift, and only one of them
gets fixed.

The correctness that must not be duplicated is in clear_family below -- the live
member of a family is TRUNCATED, never unlinked.
"""

import os
import re

from common import datastore
from common.common import LOG_DIR

#: Derived from the one place logging resolves its directory, so no surface can
#: end up clearing a different folder than the one being written to.
LOG_FOLDER = os.path.join(LOG_DIR, "")

#: A log family member is `<stem>.log`, or `<stem>.log.<n>` once rotated.
#: Anything else in the folder -- logfiles.txt, stray notes -- is not a log.
LOG_MEMBER = re.compile(r"^(?P<stem>.+)\.log(?:\.(?P<index>\d+))?$")


def list_log_families(folder):
    """{stem: [member filenames, OLDEST FIRST]}.

    RotatingFileHandler shifts suffixes upward on rollover -- `x.log` becomes
    `x.log.1`, `x.log.1` becomes `x.log.2` -- so the highest-numbered member is
    the oldest and sorts first. `x.log` itself is index 0 and sorts last.

    `folder` is required rather than defaulted: callers own resolving it, so a
    surface that lets a test repoint its own LOG_FOLDER keeps working.
    """
    try:
        names = os.listdir(folder)
    except OSError:
        return {}
    families = {}
    for name in names:
        match = LOG_MEMBER.match(name)
        if match:
            index = int(match["index"] or 0)
            families.setdefault(match["stem"], []).append((index, name))
    return {
        stem: [name for _, name in sorted(members, key=lambda pair: -pair[0])]
        for stem, members in sorted(families.items())
    }


def clear_family(folder, members):
    """Empty one log family; return the member names that were handled.

    The live member is TRUNCATED, never unlinked. create_logger gives every
    logger a RotatingFileHandler, and on POSIX os.remove only drops the
    directory entry: the handler's descriptor stays valid and goes on appending
    to an orphaned inode. Unlinking therefore looks like it worked and is not --
    the file the viewer reads never fills again, and the bytes are not reclaimed
    until the process exits. control.py and display_process.py hold their own
    handlers on these same files and no web request can reopen them, so the only
    clear that reaches all three is one through the inode they share.

    Truncating is safe for those handlers because they append: once the file is
    empty the next write lands at offset 0 rather than leaving a sparse hole
    where the old bytes were.

    Rotated members are unlinked. Nothing holds them open, and truncating them
    would leave empty files sitting in the folder forever.
    """
    handled = []
    for name in members:
        path = os.path.join(folder, name)
        match = LOG_MEMBER.match(name)
        try:
            if match and match["index"]:
                os.remove(path)
            else:
                #  "r+b", not "w": "w" would CREATE the file when none exists,
                #  manufacturing an empty log for a process that never wrote one.
                with open(path, "r+b") as handle:
                    handle.truncate(0)
            handled.append(name)
        except OSError:
            continue
    return handled


def clear_events_log(folder=None):
    """Empty the event log in BOTH stores. Always True.

    Every logger create_logger builds writes to a RotatingFileHandler AND a
    SqliteLogHandler, so clearing one sink alone leaves the other holding what
    the user asked to be rid of.

    `folder` resolves at call time so a test can repoint LOG_FOLDER.
    """
    folder = folder or LOG_FOLDER
    clear_family(folder, list_log_families(folder).get("events", []))
    datastore.clear_log("events")
    return True


def clear_all_logs(folder=None):
    """Empty every family; return the sorted member names that were handled.

    The report exists because Flask's `rm` ran inside a bare `except:`, where a
    partial failure was indistinguishable from success. The live member of each
    family is emptied rather than removed, and is still named here: the user
    asked for that log to be empty, and it is.

    `folder` resolves at call time; see clear_events_log.
    """
    folder = folder or LOG_FOLDER
    handled = []
    for members in list_log_families(folder).values():
        handled.extend(clear_family(folder, members))
    return sorted(handled)
