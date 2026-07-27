"""What source revision this server process is actually running.

A gunicorn worker keeps whatever it imported when it forked, so a server started
before a commit goes on serving code that no longer exists on disk. New routes
answer 404 and old behaviour persists, which reads as a broken frontend rather
than a stale backend. That has now cost five separate tasks in this repo, most
recently a full e2e run whose failures were misdiagnosed as browser drift.

Two facts are published, because they answer different questions:

  revision  -- the git commit the working copy was on when this process
               imported. Identifies the build; also what a bug report should
               quote.
  stale     -- whether any Python source this process loaded has been modified
               since it started. This is the one a test suite should gate on:
               it catches an uncommitted edit too, which a revision comparison
               cannot, because in a jj-colocated checkout git HEAD tracks the
               working copy's PARENT and does not move when a file changes.

Both are computed without shelling out. `git rev-parse` would be a subprocess on
a code path that any client can reach, and this repo neutralises subprocess use
rather than adding to it.
"""

import os
import sys
import time

#: Repository root -- this file lives in common/, so one level up.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_revision(repo_root=_REPO_ROOT):
    """The current git commit id, by reading .git directly. None if unavailable.

    Returns the full 40-character sha. A detached HEAD holds the id inline; the
    usual case is a `ref:` line naming a file under .git, which may itself be
    absent when the ref has been packed, in which case packed-refs carries it.
    """
    head_path = os.path.join(repo_root, ".git", "HEAD")
    try:
        with open(head_path, encoding="utf-8") as handle:
            head = handle.read().strip()
    except OSError:
        return None

    if not head.startswith("ref:"):
        return head or None

    ref = head[4:].strip()
    try:
        with open(os.path.join(repo_root, ".git", ref), encoding="utf-8") as handle:
            return handle.read().strip() or None
    except OSError:
        pass

    try:
        with open(os.path.join(repo_root, ".git", "packed-refs"), encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
    except OSError:
        pass
    return None


def newest_source_mtime(repo_root=_REPO_ROOT):
    """The most recent mtime among the repo's Python modules this process loaded.

    Walks sys.modules rather than the filesystem so it measures what is actually
    imported -- a file this server never loads cannot make it stale, and the
    walk stays proportional to the app rather than to the checkout.
    """
    newest = 0.0
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if not path or not path.startswith(repo_root):
            continue
        try:
            newest = max(newest, os.path.getmtime(path))
        except OSError:
            continue
    return newest


#: Captured at import, which for a gunicorn worker is the moment it forked.
REVISION = read_revision()
STARTED_AT = time.time()


def status():
    """The payload /api/get/revision publishes.

    Walks sys.modules per call, which is why it is not folded into
    /api/get/versions: that one is polled by the mobile app, and two of these
    fields are clocks, which would make its golden fixture unpinnable.
    """
    newest = newest_source_mtime()
    return {
        "revision": REVISION,
        "started_at": STARTED_AT,
        "newest_source_mtime": newest,
        #  Compared against process start rather than against REVISION: an
        #  uncommitted edit moves the mtime and leaves the revision alone.
        "stale": newest > STARTED_AT,
    }
