"""Helpers shared by the cookfile- and recipes-asset test modules.

Both suites upload the same one-pixel PNG through the same multipart shape
and read members back out of the same archive format; the two modules each
carried a private copy of all four helpers. Named without a `test_` prefix so
pytest does not collect this module.
"""

import contextlib
import fcntl
import io
import os
import tempfile

from PIL import Image


def png_bytes(color=(0, 200, 0), size=(16, 16)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def upload(client, url, name, asset_name="shot.png", payload=None, mimetype="image/png"):
    return client.post(
        url,
        data={"file": name, "assets": (io.BytesIO(payload if payload is not None else png_bytes()), asset_name)},
        content_type="multipart/form-data",
    )


def read_member(directory, name, member):
    from file_mgmt.common import read_json_file_data

    data, status = read_json_file_data(directory + name, member, unpackassets=False)
    assert status == "OK"
    return data


@contextlib.contextmanager
def cross_worker_lock(name):
    """Serialize a block of code across xdist WORKER PROCESSES, not just
    within one.

    `@pytest.mark.xdist_group` looks like the natural tool for this, but it
    is silently a no-op unless pytest is invoked with `--dist=loadgroup` --
    the suite's default addopts use plain `-n auto` (`--dist=load`), which
    ignores the marker with no warning. A POSIX advisory lock on a
    well-known /tmp path is dist-mode agnostic: it blocks at the OS level
    regardless of how xdist chose to schedule the two processes.
    """
    lock_path = os.path.join(tempfile.gettempdir(), f"pifire-test-lock-{name}")
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def make_static_img_tmp_cleanup():
    """Return a fixture function that cleans up ./static/img/tmp symlinks.

    Any read with unpackassets=True symlinks ./static/img/tmp/{id} into the
    repo tree (file_mgmt/common.py:85-88). Gitignored, but removed anyway so
    the working tree stays clean.

    That directory is one path shared by every xdist worker process (it's
    relative to the checkout, not per-test-isolated -- file_mgmt/common.py
    hardcodes it). Two tests that both unpack assets can run in different
    worker processes at the same time; the before/after os.listdir() diff
    below only knows about entries created within ITS OWN test, so if
    another worker's symlink appears in the window between this test's
    "before" snapshot and its cleanup, this cleanup deletes that OTHER
    test's still-in-use symlink out from under it (observed: a served asset
    404s or resolves to the wrong file, 1-in-4 to 2-in-3 runs). The
    cross-worker lock held for this whole fixture's lifetime -- setup
    through cleanup, i.e. the entire wrapped test -- makes that impossible:
    only one process can be inside this fixture, for any test using it, at
    a time.
    """

    def static_img_tmp_cleanup():
        base = "./static/img/tmp"
        with cross_worker_lock("static_img_tmp"):
            before = set(os.listdir(base)) if os.path.isdir(base) else set()
            yield
            if os.path.isdir(base):
                for leftover in set(os.listdir(base)) - before:
                    target = os.path.join(base, leftover)
                    if os.path.islink(target):
                        os.unlink(target)

    return static_img_tmp_cleanup
