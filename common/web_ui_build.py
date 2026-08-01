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

#: Never walked when looking for the newest source. dist is the OUTPUT (walking
#: it would make the bundle newer than itself and nothing would ever rebuild),
#: node_modules is enormous and its mtimes track installs rather than edits,
#: and the rest are test and cache artifacts.
SKIP_DIRS = frozenset(
    {
        "dist",
        "node_modules",
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
