"""Deciding when the served React bundle is out of date, and rebuilding it.

web-react/dist is git-ignored -- a build artifact, not a checked-in one -- so
pulling new React sources leaves the previously built bundle in place and
blueprints/spa/routes.py goes on serving it. Every path that changes those
sources (update, branch change, manual rebuild) asks here whether a build is
owed.

Staleness is a MTIME comparison rather than a diff of what a merge brought in.
That credits whatever built the bundle last by whatever route, so an update
whose migration step already built it does not build again; and it covers
sources that changed with no merge at all -- a hand-edited file, an interrupted
build, a dist removed by `git clean`.
"""

import os
import subprocess

from common.common import log_path
from common.install_log import read_install_log

#: The updater's own log, which every rebuild line already reaches: the build
#: runs from updater.py and publishes through its logger.
BUILD_LOG_NAME = "update.log"

#: Boundaries updater.py writes around a rebuild. The log is shared with the
#: rest of the update -- git output, apt, pip -- so the transcript offered for a
#: failed build is scoped between these rather than being the whole file.
BUILD_RUN_MARKER = "=== web UI rebuild started ==="
BUILD_OK_MARKER = "=== web UI rebuild finished ==="
BUILD_FAIL_MARKER = "=== web UI rebuild failed ==="

#: Never walked when looking for the newest source. dist is the OUTPUT (walking
#: it would make the bundle newer than itself and nothing would ever rebuild),
#: node_modules is enormous and its mtimes track installs rather than edits,
#: .bun-toolchain is the downloaded build tool -- newer than the bundle the
#: moment it is fetched, which would leave the UI permanently "out of date" and
#: rebuilding forever -- and the rest are test and cache artifacts.
SKIP_DIRS = frozenset(
    {
        "dist",
        "node_modules",
        ".bun-toolchain",
        ".rsbuild",
        "coverage",
        "test-results",
        "playwright-report",
        "__pycache__",
    }
)


def web_dir(repo_root):
    return os.path.join(repo_root, "web-react")


def bundle_entry(repo_root):
    """The artifact the SPA blueprint serves, and the build's own timestamp."""
    return os.path.join(web_dir(repo_root), "dist", "index.html")


def newest_source_mtime(repo_root):
    """The most recent mtime anywhere in web-react, outside SKIP_DIRS."""
    newest = 0.0
    for root, dirs, files in os.walk(web_dir(repo_root)):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            try:
                mtime = os.path.getmtime(os.path.join(root, name))
            except OSError:
                # Raced with a checkout, or a broken symlink. One unreadable
                # file is not a reason to skip a rebuild or to force one.
                continue
            newest = max(newest, mtime)
    return newest


def web_ui_needs_rebuild(repo_root):
    """True when the bundle on disk is older than the sources it was built from.

    A missing bundle counts: a fresh clone has no web interface at all until
    something builds one.
    """
    bundle = bundle_entry(repo_root)
    if not os.path.isfile(bundle):
        return True
    return newest_source_mtime(repo_root) > os.path.getmtime(bundle)


def rebuild_web_ui(repo_root, on_line, runner=None):
    """Rebuild the bundle, feeding each output line to `on_line`. Returns the
    exit status (0 on success).

    `runner` exists so tests can drive this without ever launching a real
    build -- one takes minutes and needs the network.
    """
    command = ["bash", os.path.join(repo_root, "updater", "rebuild-web-ui.sh"), repo_root]
    if runner is not None:
        return runner(command, on_line)

    process = subprocess.Popen(
        command,
        cwd=repo_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        # bun writes its install and build progress to stderr, so a
        # stdout-only pipe would show an operator a silent several-minute gap
        # and no reason at all when it fails.
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
    )
    for line in process.stdout:
        stripped = line.strip()
        if stripped:
            on_line(stripped)
    return process.wait()


def build_log_path():
    return log_path(BUILD_LOG_NAME)


def read_build_log(offset=0, path=None):
    """The last rebuild's transcript from `offset` on, as (text, offset, reset).

    Incremental by byte offset for the same reason the wizard's is: a build
    reruns from the updater page, and re-sending the whole thing every second
    to a browser on a grill's wifi is not worth what it buys.
    """
    return read_install_log(offset, path or build_log_path(), marker=BUILD_RUN_MARKER)


def last_build_failed(path=None):
    """Whether the most recent rebuild ended in failure.

    Derived from the transcript rather than from the install-status blob: that
    blob holds one line, and an update whose rebuild failed goes on to overwrite
    it with "Finished!" seconds later. This still answers correctly after a
    restart, a page reload, or a poll that missed the moment.

    A build still running has an opening marker and no closing one, and is not
    a failure. Neither is a log written before any rebuild ran.
    """
    text, _, _ = read_build_log(path=path)
    if BUILD_RUN_MARKER not in text:
        return False
    return BUILD_FAIL_MARKER in text
