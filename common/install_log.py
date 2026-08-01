"""Reader for the wizard installer's transcript.

wizard.py logs every line its subprocesses emit to logs/wizard.log. That file
is the only complete record of an install: the install-status blob holds a
single line at a time, overwritten as fast as apt and uv produce output, so
whatever a 250ms poll happens to catch is a sample rather than a transcript.
This serves the file instead, which is why the browser can open the output
panel late in an install and still see everything from the beginning.

Reads are incremental by byte offset. The client sends the offset it has
already consumed and gets back only what has been appended since, so polling a
running install costs a few hundred bytes a tick rather than re-sending a log
that grows into the hundreds of KB.
"""

from common.common import log_path

WIZARD_LOG_NAME = "wizard.log"

#: Written by run_wizard() at the top of every install. The log outlives a
#: single run (RotatingFileHandler, 1 MB x 3 backups), so without a boundary in
#: the file the panel would open on the tail of some previous install.
RUN_MARKER = "=== wizard install run started ==="

#: Cap on a from-scratch read. An install that pulls a large apt set can log a
#: few hundred KB, and the panel is a scrollback, not an archive -- the whole
#: file stays available under /api/admin/logs/view for anyone who wants it.
MAX_BYTES = 256 * 1024

#: Published as the install status percent when the installer raises. The
#: browser polls percent and reads anything above 100 as "finished", so a
#: failure had no way to say so: the detached process just stopped writing and
#: the bar sat wherever it had got to, forever. Negative, because every real
#: percent -- including the 101/142 finished sentinels -- is positive.
INSTALL_FAILED_PERCENT = -1


def _run_start(data):
    """Byte offset of the current run's first line within `data`."""
    marker = data.rfind(RUN_MARKER.encode())
    if marker < 0:
        # No marker: either a log written before this existed, or a run whose
        # marker has already rotated out. Showing the whole file beats showing
        # nothing -- MAX_BYTES still bounds it.
        return 0
    return data.rfind(b"\n", 0, marker) + 1


def read_install_log(offset=0, path=None):
    """The current run's log from `offset` on.

    Returns (text, next_offset, reset). `reset` tells the client to replace its
    buffer rather than append to it, which is what has to happen when the
    offset it is holding predates the current run (a second install in the same
    session) or points past the end of the file (rotation truncated it under
    the cursor). Both cases are otherwise silent: appending would splice the
    tail of one run onto the head of another.
    """
    try:
        with open(path or log_path(WIZARD_LOG_NAME), "rb") as handle:
            data = handle.read()
    except OSError:
        # No install has run yet, or the log is unreadable. An empty transcript
        # is the honest answer; the status line is already saying what's up.
        return "", 0, offset != 0

    start = _run_start(data)
    # `<=`, not `<`: a client whose offset lands exactly ON the run start has
    # consumed nothing of this run, so everything in its buffer belongs to the
    # previous one. That is the ordinary shape of a second install in one
    # session -- the old run ended, its last byte is the new run's first -- and
    # a `<` here would let the two transcripts append into one.
    if offset <= start or offset > len(data):
        begin, reset = start, True
    else:
        begin, reset = offset, False

    if len(data) - begin > MAX_BYTES:
        begin = len(data) - MAX_BYTES
        # Resume at a line boundary so the first line shown is not a fragment.
        newline = data.find(b"\n", begin)
        begin = len(data) if newline < 0 else newline + 1
        reset = True

    return data[begin:].decode("utf-8", errors="replace"), len(data), reset
