"""File-level recipe operations: create, download, upload, delete.

Mirrors tests/web/test_api_files_cookfile_write.py's E5/E7/E8 coverage for the
recipe surface. SAFETY: every test here writes only into the temp folder
`api_files_folders` creates.
"""

import io
import os
from unittest.mock import ANY

import pytest

from blueprints.api_files import routes
from tests.web.archive_builders import write_recipe

pytestmark = pytest.mark.usefixtures("api_files_folders")


@pytest.fixture
def client(api_files_client):
    return api_files_client


@pytest.fixture
def folders(api_files_folders):
    return api_files_folders


def _upload(client, filename, payload=b"fake recipe bytes"):
    return client.post(
        "/api/files/recipes/upload",
        data={"recipe": (io.BytesIO(payload), filename)},
        content_type="multipart/form-data",
    )


# --------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------


def test_create_returns_a_name_that_then_resolves_through_detail(client, folders):
    _history, recipe_dir = folders
    resp = client.post("/api/files/recipes/create")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["result"] == "OK"
    name = body["data"]["filename"]
    # A bare filename, not a path: create_recipefile() returns a full path,
    # and the client is never handed anything but its basename.
    assert "/" not in name
    assert os.path.isfile(recipe_dir + name)

    detail = client.get(f"/api/files/recipes/detail?file={name}")
    assert detail.status_code == 200


def test_two_creates_in_the_same_minute_produce_different_names(client, folders):
    first = client.post("/api/files/recipes/create").get_json()["data"]["filename"]
    second = client.post("/api/files/recipes/create").get_json()["data"]["filename"]
    assert first != second


# --------------------------------------------------------------------------
# download
# --------------------------------------------------------------------------


def test_download_streams_the_archive_bytes(client, folders):
    _history, recipe_dir = folders
    name = write_recipe(recipe_dir, "Download-Recipe")
    with open(recipe_dir + name, "rb") as handle:
        on_disk = handle.read()

    resp = client.get(f"/api/files/recipes/download?file={name}")
    assert resp.status_code == 200
    assert resp.data == on_disk
    assert "attachment" in resp.headers["Content-Disposition"]
    assert name in resp.headers["Content-Disposition"]


def test_download_refuses_traversal(client, folders):
    resp = client.get("/api/files/recipes/download?file=../../../etc/passwd")
    assert resp.status_code == 404
    assert b"root:" not in resp.data


def test_download_of_an_unknown_file_is_404(client, folders):
    assert client.get("/api/files/recipes/download?file=Nope.pfrecipe").status_code == 404


# --------------------------------------------------------------------------
# upload
# --------------------------------------------------------------------------


def test_upload_round_trips(client, folders):
    _history, recipe_dir = folders
    payload = b"fake recipe bytes"
    resp = _upload(client, "Uploaded.pfrecipe", payload=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["result"] == "OK"
    assert body["data"]["filename"] == "Uploaded.pfrecipe"
    with open(recipe_dir + "Uploaded.pfrecipe", "rb") as handle:
        assert handle.read() == payload


def test_upload_rejects_a_non_pfrecipe_extension(client, folders):
    """Reject any extension other than .pfrecipe, not the app-wide
    ALLOWED_EXTENSIONS (which also permits images and logs)."""
    _history, recipe_dir = folders
    resp = _upload(client, "notes.txt")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["data"]["field"] == "recipe"
    assert not os.path.exists(recipe_dir + "notes.txt")


@pytest.mark.parametrize(
    "hostile_name",
    ["../../evil.pfrecipe", "/tmp/evil.pfrecipe", "..\\..\\evil.pfrecipe", "sub/dir/evil.pfrecipe"],
)
def test_upload_filename_cannot_escape_the_folder(client, folders, hostile_name):
    _history, recipe_dir = folders
    before = set(os.listdir(recipe_dir))
    parent = os.path.dirname(recipe_dir.rstrip("/"))
    parent_before = set(os.listdir(parent))

    resp = _upload(client, hostile_name, payload=b"x")
    assert resp.status_code in (200, 400)
    assert set(os.listdir(parent)) == parent_before, "an upload escaped the recipe folder"
    for created in set(os.listdir(recipe_dir)) - before:
        assert "/" not in created and ".." not in created
    assert not os.path.exists("/tmp/evil.pfrecipe")


def test_upload_with_no_file_part_is_400(client, folders):
    resp = client.post("/api/files/recipes/upload", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_upload_with_an_empty_filename_is_400(client, folders):
    resp = _upload(client, "")
    assert resp.status_code == 400


# --------------------------------------------------------------------------
# delete
# --------------------------------------------------------------------------


def test_delete_removes_the_file(client, folders):
    _history, recipe_dir = folders
    name = write_recipe(recipe_dir, "Delete-Recipe")
    resp = client.post("/api/files/recipes/delete", json={"file": name})
    assert resp.status_code == 200
    assert resp.get_json()["result"] == "OK"
    assert not os.path.exists(recipe_dir + name)


@pytest.mark.parametrize("hostile", ["../victim.pfrecipe", "/etc/hosts", "", "Nope.pfrecipe"])
def test_delete_refuses_traversal_and_unknown_names(client, folders, tmp_path, hostile):
    victim = tmp_path / "victim.pfrecipe"
    victim.write_text("do not delete me")
    resp = client.post("/api/files/recipes/delete", json={"file": hostile})
    assert resp.status_code in (400, 404)
    assert victim.exists()


def test_delete_with_no_body_is_400(client, folders):
    assert client.post("/api/files/recipes/delete").status_code == 400


# --------------------------------------------------------------------------
# run
#
# G1/G6 SAFETY: every test below stubs both write_control and read_control on
# the routes module -- none of them may reach the real control store, and
# none of them may read live state.
# --------------------------------------------------------------------------


def test_run_refuses_unless_stopped(client, folders, monkeypatch):
    writes = []
    monkeypatch.setattr(routes, "write_control", lambda *a, **k: writes.append(a))
    monkeypatch.setattr(routes, "read_control", lambda: {"mode": "Hold"})
    name = write_recipe(folders[1], "Brisket")
    resp = client.post("/api/files/recipes/run", json={"file": name})
    assert resp.status_code == 409
    assert resp.get_json()["message"] == "not_stopped"
    assert writes == []


def test_run_sends_start_step_and_step_explicitly(client, folders, monkeypatch):
    """_api_post_control deep-merges, so a bare {filename} inherits the previous
    run's step and starts mid-recipe."""
    writes = []
    monkeypatch.setattr(routes, "write_control", lambda *a, **k: writes.append(a))
    monkeypatch.setattr(routes, "read_control", lambda: {"mode": "Stop"})
    _history, recipe_dir = folders
    name = write_recipe(recipe_dir, "Brisket")

    resp = client.post("/api/files/recipes/run", json={"file": name})

    assert resp.status_code == 200
    assert resp.get_json()["data"]["filename"] == name
    envelope, kind = writes[0][0], writes[0][1]
    assert kind is routes.WriteKind.DELTA
    delta = envelope["set"]
    assert delta["mode"] == routes.Mode.RECIPE
    assert delta["recipe"] == {"filename": ANY, "start_step": 0, "step": 0}
    # The path rule (bare filenames) governs what the client sends; the
    # server stores the resolved absolute path, since that is what
    # controller.py opens.
    filename = delta["recipe"]["filename"]
    assert os.path.isabs(filename)
    assert filename == os.path.join(recipe_dir, name)


def test_run_of_an_unknown_file_is_404(client, folders, monkeypatch):
    writes = []
    monkeypatch.setattr(routes, "write_control", lambda *a, **k: writes.append(a))
    monkeypatch.setattr(routes, "read_control", lambda: {"mode": "Stop"})
    resp = client.post("/api/files/recipes/run", json={"file": "Nope.pfrecipe"})
    assert resp.status_code == 404
    assert writes == []


def test_run_refuses_traversal_and_never_writes_control(client, folders, monkeypatch):
    writes = []
    monkeypatch.setattr(routes, "write_control", lambda *a, **k: writes.append(a))
    monkeypatch.setattr(routes, "read_control", lambda: {"mode": "Stop"})
    resp = client.post("/api/files/recipes/run", json={"file": "../../../etc/passwd"})
    assert resp.status_code == 404
    assert writes == []
