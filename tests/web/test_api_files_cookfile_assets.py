"""POST /api/files/cookfiles/assets/upload, .../assets/delete, .../thumbnail.

The Pillow pipeline is NOT mocked: add_asset rotates, thumbnails and resizes
for real (file_mgmt/media.py:26-61), and a mocked pipeline would not catch a
thumbnail that never lands in the archive.

SAFETY: uploads are staged in a private per-request tempfile.mkdtemp that is
rmtree'd in a finally, and the tests assert none survive. Reading a cook file's
assets symlinks ./static/img/tmp/{id} into the checkout, so
`static_img_tmp_cleanup` removes any symlink the test created.

Harness rationale: see tests/web/test_api_files_listing.py's docstring.
"""

import io
import os
import tempfile
import zipfile

import pytest
from PIL import Image
from common.web_contracts.content import CookFileAsset

from tests.web.archive_builders import write_cookfile

UPLOAD_URL = "/api/files/cookfiles/assets/upload"
DELETE_URL = "/api/files/cookfiles/assets/delete"
THUMB_URL = "/api/files/cookfiles/thumbnail"


@pytest.fixture
def client(api_files_client):
    return api_files_client


@pytest.fixture
def folders(api_files_folders):
    return api_files_folders


@pytest.fixture(autouse=True)
def static_img_tmp_cleanup():
    """Any read with unpackassets=True symlinks ./static/img/tmp/{id} into the
    repo tree (file_mgmt/common.py:85-88). Gitignored, but removed anyway so
    the working tree stays clean."""
    base = "./static/img/tmp"
    before = set(os.listdir(base)) if os.path.isdir(base) else set()
    yield
    if os.path.isdir(base):
        for leftover in set(os.listdir(base)) - before:
            target = os.path.join(base, leftover)
            if os.path.islink(target):
                os.unlink(target)


def _png(color=(0, 200, 0), size=(16, 16)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _read_member(history_dir, name, member):
    from file_mgmt.common import read_json_file_data

    data, status = read_json_file_data(history_dir + name, member, unpackassets=False)
    assert status == "OK"
    return data


def _upload(client, name, asset_name="shot.png", payload=None, mimetype="image/png"):
    return client.post(
        UPLOAD_URL,
        data={"file": name, "assets": (io.BytesIO(payload if payload is not None else _png()), asset_name)},
        content_type="multipart/form-data",
    )


def test_asset_upload_runs_the_real_pillow_pipeline(client, folders, monkeypatch):
    """add_asset rotates, thumbnails and resizes with real Pillow
    (file_mgmt/media.py:26-61). Not mocked -- a mocked pipeline would not catch
    a thumbnail that never lands in the archive."""
    import blueprints.api_files.cookfile_api as cookfile_api

    history_dir, _ = folders
    name = write_cookfile(history_dir, "AssetUp-Cook")
    real_mkdtemp = tempfile.mkdtemp
    staging_paths = []

    def tracked_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        if kwargs.get("prefix") == "pifire-upload-":
            staging_paths.append(path)
        return path

    monkeypatch.setattr(cookfile_api.tempfile, "mkdtemp", tracked_mkdtemp)

    resp = _upload(client, name)
    assert resp.status_code == 200
    stored = resp.get_json()["data"]["assets"]
    assert [CookFileAsset.model_validate(asset, strict=True).model_dump(mode="json") for asset in stored] == stored
    assert len(stored) == 1 and stored[0]["type"] == "png"

    with zipfile.ZipFile(history_dir + name) as archive:
        members = set(archive.namelist())
    arc = f"{stored[0]['id']}.png"
    assert stored[0]["filename"] == arc
    assert f"assets/{arc}" in members
    assert f"assets/thumbs/{arc}" in members
    assert _read_member(history_dir, name, "assets")[0]["filename"] == arc

    # This request's private staging directory is removed even when other
    # xdist workers are creating their own upload directories concurrently.
    assert staging_paths
    assert all(not os.path.exists(path) for path in staging_paths)
    parent_id = _read_member(history_dir, name, "metadata")["id"]
    assert not os.path.exists(f"/tmp/pifire/{parent_id}")


def test_asset_upload_accepts_more_than_one_file(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "AssetMulti-Cook")
    resp = client.post(
        UPLOAD_URL,
        data={
            "file": name,
            "assets": [
                (io.BytesIO(_png((10, 10, 200))), "one.png"),
                (io.BytesIO(_png((200, 10, 10))), "two.png"),
            ],
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert len(resp.get_json()["data"]["assets"]) == 2
    assert len(_read_member(history_dir, name, "assets")) == 2


def test_asset_upload_rejects_a_disallowed_extension(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "AssetBad-Cook")
    resp = _upload(client, name, asset_name="evil.svg", payload=b"<svg onload=alert(1)>", mimetype="image/svg+xml")
    assert resp.status_code == 400
    assert resp.get_json()["message"] == "disallowed_file"
    assert _read_member(history_dir, name, "assets") == []


def test_asset_upload_with_no_asset_part_is_400(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "AssetNone-Cook")
    resp = client.post(UPLOAD_URL, data={"file": name}, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_asset_upload_refuses_traversal_on_the_archive_name(client, folders):
    resp = _upload(client, "../x.pifire")
    assert resp.status_code in (400, 404)


def test_asset_upload_filename_cannot_escape_the_staging_dir(client, folders):
    """Traversal on the asset's own filename, distinct from the archive name."""
    history_dir, _ = folders
    name = write_cookfile(history_dir, "AssetTrav-Cook")
    parent = os.path.dirname(history_dir.rstrip("/"))
    parent_before = set(os.listdir(parent))
    before = set(os.listdir(history_dir))

    resp = _upload(client, name, asset_name="../../escape.png")
    assert resp.status_code in (200, 400)
    assert set(os.listdir(parent)) == parent_before
    assert set(os.listdir(history_dir)) == before
    assert not os.path.exists("/tmp/escape.png")


def test_uploaded_asset_is_served_from_static_img_tmp(client, folders):
    """The browser-serving invariant: bytes at /static/img/tmp/{id}/{asset}
    equal the fullsize asset inside the zip."""
    history_dir, _ = folders
    name = write_cookfile(history_dir, "AssetServe-Cook")
    parent_id = _read_member(history_dir, name, "metadata")["id"]
    stored = _upload(client, name, asset_name="served.png", payload=_png((200, 20, 20), (24, 24)))
    arc = stored.get_json()["data"]["assets"][0]["filename"]

    #  The symlink is created by a read that unpacks assets; detail does one.
    assert client.get(f"/api/files/cookfiles/detail?file={name}").status_code == 200

    with zipfile.ZipFile(history_dir + name) as archive:
        archived = archive.read(f"assets/{arc}")

    served = client.get(f"/static/img/tmp/{parent_id}/{arc}")
    assert served.status_code == 200
    assert served.data == archived
    assert not os.path.exists(f"/tmp/pifire/{parent_id}")


def test_asset_delete_removes_it_from_the_archive_and_from_comments(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "AssetDel-Cook")
    arc = _upload(client, name, asset_name="gone.png").get_json()["data"]["assets"][0]["filename"]

    cid = client.post("/api/files/cookfiles/comments", json={"file": name, "action": "add", "text": "c"}).get_json()[
        "data"
    ]["id"]
    client.post("/api/files/cookfiles/comments/assets", json={"file": name, "id": cid, "assets": [arc]})

    resp = client.post(DELETE_URL, json={"file": name, "assets": [arc]})
    assert resp.status_code == 200
    assert _read_member(history_dir, name, "assets") == []
    assert _read_member(history_dir, name, "comments")[0]["assets"] == []
    with zipfile.ZipFile(history_dir + name) as archive:
        assert f"assets/{arc}" not in archive.namelist()
        assert f"assets/thumbs/{arc}" not in archive.namelist()


def test_asset_delete_clears_a_thumbnail_pointing_at_it(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "AssetThumbDel-Cook")
    arc = _upload(client, name, asset_name="t.png").get_json()["data"]["assets"][0]["filename"]
    assert client.post(THUMB_URL, json={"file": name, "asset": arc}).status_code == 200

    assert client.post(DELETE_URL, json={"file": name, "assets": [arc]}).status_code == 200
    assert _read_member(history_dir, name, "metadata")["thumbnail"] == ""


@pytest.mark.parametrize("bad", ["a.png", [1], {"a": 1}, None])
def test_asset_delete_requires_a_list_of_strings(client, folders, bad):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "AssetDelType-Cook")
    resp = client.post(DELETE_URL, json={"file": name, "assets": bad})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "assets"


def test_asset_delete_refuses_traversal(client, folders):
    """delmedialist reaches remove_assets, which rewrites the archive in place
    (file_mgmt/common.py:257-278) -- an uncontained path there is a write
    primitive, not just a read one."""
    resp = client.post(DELETE_URL, json={"file": "../x.pifire", "assets": ["a.png"]})
    assert resp.status_code in (400, 404)


def test_thumbnail_is_set_from_an_existing_asset(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Thumb-Cook")
    arc = _upload(client, name, asset_name="t.png").get_json()["data"]["assets"][0]["filename"]

    resp = client.post(THUMB_URL, json={"file": name, "asset": arc})
    assert resp.status_code == 200
    assert _read_member(history_dir, name, "metadata")["thumbnail"] == arc


def test_thumbnail_rejects_an_asset_the_file_does_not_have(client, folders):
    """Flask's thumbSelected writes whatever string it is handed
    (routes.py:202-210) -- test_page_cookfile.py stores the literal
    "asset123.png" for a file with no assets, producing a broken <img>
    forever."""
    history_dir, _ = folders
    name = write_cookfile(history_dir, "ThumbBad-Cook")
    resp = client.post(THUMB_URL, json={"file": name, "asset": "does-not-exist.png"})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "asset"
    assert _read_member(history_dir, name, "metadata")["thumbnail"] == ""


@pytest.mark.parametrize("bad", ["", 5, None])
def test_thumbnail_requires_a_non_empty_string(client, folders, bad):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "ThumbType-Cook")
    resp = client.post(THUMB_URL, json={"file": name, "asset": bad})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "asset"


def test_thumbnail_refuses_traversal(client, folders):
    resp = client.post(THUMB_URL, json={"file": "../x.pifire", "asset": "a.png"})
    assert resp.status_code in (400, 404)
