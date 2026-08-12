"""Helpers shared by the cookfile- and recipes-asset test modules.

Both suites upload the same one-pixel PNG through the same multipart shape
and read members back out of the same archive format; the two modules each
carried a private copy of all four helpers. Named without a `test_` prefix so
pytest does not collect this module.
"""

import io
import os

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


def make_static_img_tmp_cleanup():
    """Return a fixture function that cleans up ./static/img/tmp symlinks.

    Any read with unpackassets=True symlinks ./static/img/tmp/{id} into the
    repo tree (file_mgmt/common.py:85-88). Gitignored, but removed anyway so
    the working tree stays clean.
    """

    def static_img_tmp_cleanup():
        base = "./static/img/tmp"
        before = set(os.listdir(base)) if os.path.isdir(base) else set()
        yield
        if os.path.isdir(base):
            for leftover in set(os.listdir(base)) - before:
                target = os.path.join(base, leftover)
                if os.path.islink(target):
                    os.unlink(target)

    return static_img_tmp_cleanup
