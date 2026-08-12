"""File-level cook-file operations: download, CSV export, upload, delete, and
the mutating endpoints (title, probe-label rename, repair/upgrade, comments,
assets).

Three of these have never had a test before. `dl_cookfile`, `dl_graphfile` and
`dl_eventfile` (blueprints/cookfile/routes.py:159-182) appear nowhere in
tests/web/test_page_cookfile.py -- grep 'dl_' there and you get nothing -- and
the reason is documented in the export tests below: prepare_csv composes its
output path from the cook file's path, so the legacy branches only work when
HISTORY_FOLDER is literally "./history/", which is never true under an
isolated-folder fixture.

SAFETY: every test here writes only into the temp folders `api_files_folders`
creates, plus the /tmp CSV that prepare_csv insists on. The upload tests
explicitly assert that nothing appears outside the history folder.

Harness rationale: see tests/web/test_api_files_listing.py's docstring.
"""

import io
import os

import pytest

from tests.web._asset_helpers import read_member
from tests.web.archive_builders import write_cookfile, write_legacy_cookfile


@pytest.fixture
def client(api_files_client):
    return api_files_client


@pytest.fixture
def folders(api_files_folders):
    return api_files_folders


def _upload(client, filename, payload=b"fake cookfile bytes"):
    return client.post(
        "/api/files/cookfiles/upload",
        data={"file": (io.BytesIO(payload), filename)},
        content_type="multipart/form-data",
    )


# --------------------------------------------------------------------------
# E5 download
# --------------------------------------------------------------------------


def test_download_streams_the_archive_bytes(client, folders):
    """First test this behaviour has ever had: the legacy `dl_cookfile` branch
    (blueprints/cookfile/routes.py:159-162) has NO test."""
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Download-Cook")
    with open(history_dir + name, "rb") as handle:
        on_disk = handle.read()

    resp = client.get(f"/api/files/cookfiles/download?file={name}")
    assert resp.status_code == 200
    assert resp.data == on_disk
    assert "attachment" in resp.headers["Content-Disposition"]
    assert name in resp.headers["Content-Disposition"]


def test_download_refuses_to_read_outside_the_history_folder(client, folders):
    """The legacy branch does `send_file(request.form["dl_cookfile"])` with no
    check whatsoever -- an arbitrary file read. This one is contained."""
    resp = client.get("/api/files/cookfiles/download?file=../../../etc/passwd")
    assert resp.status_code == 404
    assert b"root:" not in resp.data


def test_download_of_an_unknown_file_is_404(client, folders):
    assert client.get("/api/files/cookfiles/download?file=Nope.pifire").status_code == 404


# --------------------------------------------------------------------------
# E6 CSV export
# --------------------------------------------------------------------------


def test_export_data_csv_has_a_header_and_one_row_per_sample(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Export-Cook")
    resp = client.get(f"/api/files/cookfiles/export?file={name}&kind=data")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    header = text.splitlines()[0]
    assert header.startswith("Time,")
    assert "grill1 Temp" in header
    assert len(text.splitlines()) == 3  # header + two raw_data samples
    assert "attachment" in resp.headers["Content-Disposition"]


def test_export_events_csv(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "ExportEv-Cook")
    resp = client.get(f"/api/files/cookfiles/export?file={name}&kind=events")
    assert resp.status_code == 200
    assert len(resp.get_data(as_text=True).splitlines()) >= 3  # header + two events


def test_export_works_under_a_non_default_history_folder(client, folders):
    """The bug this endpoint routes around: prepare_csv/prepare_metrics_csv do
    `filename.replace("./history/", "")` and then `"/tmp/" + filename + ".csv"`
    (common/app.py:173-176, :209-213). Given an absolute path under a temp dir
    that composes `/tmp//tmp/.../X.pifire-Pifire-Export.csv` and open() raises.
    This endpoint passes os.path.basename(name) in, so it is correct under any
    folder -- which is exactly why the legacy dl_graphfile/dl_eventfile
    branches could never be tested under the isolated-folder fixture."""
    history_dir, _ = folders
    assert not history_dir.startswith("./history/")
    name = write_cookfile(history_dir, "TempFolder-Cook")
    for kind in ("data", "events"):
        resp = client.get(f"/api/files/cookfiles/export?file={name}&kind={kind}")
        assert resp.status_code == 200, f"{kind} export failed under a temp history folder"


def test_export_rejects_an_unknown_kind(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "BadKind-Cook")
    resp = client.get(f"/api/files/cookfiles/export?file={name}&kind=sql")
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "kind"


def test_export_refuses_traversal(client, folders):
    resp = client.get("/api/files/cookfiles/export?file=../../../etc/passwd&kind=data")
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# E7 upload
# --------------------------------------------------------------------------


def test_upload_saves_into_the_configured_folder(client, folders):
    history_dir, _ = folders
    stray = os.path.join(os.getcwd(), "HISTORY_FOLDER")
    resp = _upload(client, "Uploaded.pifire")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["result"] == "OK"
    assert body["data"]["filename"] == "Uploaded.pifire"
    assert os.path.isfile(history_dir + "Uploaded.pifire")
    assert not os.path.exists(stray)


@pytest.mark.parametrize(
    "hostile_name",
    ["../../evil.pifire", "/tmp/evil.pifire", "..\\..\\evil.pifire", "sub/dir/evil.pifire"],
)
def test_upload_filename_cannot_escape_the_folder(client, folders, hostile_name):
    """Traversal on the UPLOADED filename, explicitly. secure_filename flattens
    the name and resolve_managed_file then contains the result; both run, and
    the assertion is that nothing lands outside history_dir."""
    history_dir, _ = folders
    before = set(os.listdir(history_dir))
    parent = os.path.dirname(history_dir.rstrip("/"))
    parent_before = set(os.listdir(parent))

    resp = _upload(client, hostile_name, payload=b"x")
    assert resp.status_code in (200, 400)
    assert set(os.listdir(parent)) == parent_before, "an upload escaped the history folder"
    for created in set(os.listdir(history_dir)) - before:
        assert "/" not in created and ".." not in created
    assert not os.path.exists("/tmp/evil.pifire")


def test_upload_rejects_a_disallowed_extension(client, folders):
    """config.py:10 ALLOWED_EXTENSIONS gates this, same as the legacy route."""
    history_dir, _ = folders
    resp = client.post(
        "/api/files/cookfiles/upload",
        data={"file": (io.BytesIO(b"rm -rf /"), "payload.sh")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert resp.get_json()["message"] == "disallowed_file"
    assert not os.path.exists(history_dir + "payload.sh")


def test_upload_with_no_file_part_is_400(client, folders):
    resp = client.post("/api/files/cookfiles/upload", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400


# Byte-identical to test_api_files_recipes_write.py's test of the same name
# (Task 15 audit). NOT redundant: both files' `client` fixture resolves to the
# same api_files_client, but each module's own `_upload()` posts to a
# different URL with a different form field (cookfiles/upload+"file" here vs.
# recipes/upload+"recipe" there) -- so this exercises the cookfile upload
# route's 400-on-empty-filename behavior specifically. Intentionally kept
# duplicated; allowlist both in Task 16's duplicate-test guard.
def test_upload_with_an_empty_filename_is_400(client, folders):
    resp = _upload(client, "")
    assert resp.status_code == 400


# --------------------------------------------------------------------------
# E8 delete
# --------------------------------------------------------------------------


def test_delete_removes_the_file(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Delete-Cook")
    resp = client.post("/api/files/cookfiles/delete", json={"file": name})
    assert resp.status_code == 200
    assert resp.get_json()["result"] == "OK"
    assert not os.path.exists(history_dir + name)


@pytest.mark.parametrize("hostile", ["../victim.pifire", "/etc/hosts", "", "Nope.pifire"])
def test_delete_refuses_traversal_and_unknown_names(client, folders, tmp_path, hostile):
    victim = tmp_path / "victim.pifire"
    victim.write_text("do not delete me")
    resp = client.post("/api/files/cookfiles/delete", json={"file": hostile})
    assert resp.status_code in (400, 404)
    assert victim.exists()


def test_delete_with_no_body_is_400(client, folders):
    assert client.post("/api/files/cookfiles/delete").status_code == 400


# --------------------------------------------------------------------------
# E9-E11 title, probe-label rename, repair/upgrade
# --------------------------------------------------------------------------


def test_title_rename_persists(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Title-Cook")
    resp = client.post("/api/files/cookfiles/title", json={"file": name, "title": "Sunday Brisket"})
    assert resp.status_code == 200 and resp.get_json()["result"] == "OK"
    assert read_member(history_dir, name, "metadata")["title"] == "Sunday Brisket"


def test_title_rename_does_not_rename_the_file(client, folders):
    """Flask's editTitle only touches metadata.json (routes.py:483-495). The
    filename is the identity the list and every URL use; renaming it here would
    break the open browser tab."""
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Stable-Cook")
    client.post("/api/files/cookfiles/title", json={"file": name, "title": "Something Else"})
    assert os.path.isfile(history_dir + name)


def test_title_requires_a_string(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "NoTitle-Cook")
    resp = client.post("/api/files/cookfiles/title", json={"file": name, "title": 7})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "title"


def test_label_rename_updates_labels_mapper_and_chart_label(client, folders):
    """_rename_graph_label (routes.py:499-554) is a five-step rewrite across
    TWO json members. All three effects are asserted."""
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Label-Cook")
    resp = client.post(
        "/api/files/cookfiles/label",
        json={"file": name, "old_label": "grill1", "new_label": "Main Grill"},
    )
    assert resp.status_code == 200
    safe = resp.get_json()["data"]["new_label_safe"]
    assert safe == "MainGrill"  # create_safe_name strips non-alnum (common/app.py:325)

    labels = read_member(history_dir, name, "graph_labels")
    assert labels["probes"][safe] == "Main Grill"
    assert "grill1" not in labels["probes"]

    graph = read_member(history_dir, name, "graph_data")
    assert safe in graph["probe_mapper"]["probes"]
    assert graph["chart_data"][graph["probe_mapper"]["probes"][safe]]["label"] == "Main Grill"


def test_label_rename_to_an_existing_label_is_refused(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Dup-Cook")
    client.post(
        "/api/files/cookfiles/label",
        json={"file": name, "old_label": "grill1", "new_label": "Main Grill"},
    )
    resp = client.post(
        "/api/files/cookfiles/label",
        json={"file": name, "old_label": "MainGrill", "new_label": "Main Grill"},
    )
    assert resp.status_code == 409
    assert resp.get_json()["message"] == "label_exists"


def test_label_rename_refusal_leaves_the_archive_untouched(client, folders):
    """A 409 must be a no-op, not a half-applied rename -- the legacy helper
    pops the old key before it discovers the collision."""
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Intact-Cook")
    client.post(
        "/api/files/cookfiles/label",
        json={"file": name, "old_label": "grill1", "new_label": "Main Grill"},
    )
    before = read_member(history_dir, name, "graph_labels")
    client.post(
        "/api/files/cookfiles/label",
        json={"file": name, "old_label": "MainGrill", "new_label": "Main Grill"},
    )
    assert read_member(history_dir, name, "graph_labels") == before


@pytest.mark.parametrize(
    "body,field",
    [
        ({"old_label": "grill1"}, "new_label"),
        ({"new_label": "X"}, "old_label"),
        ({"old_label": "", "new_label": "X"}, "old_label"),
        ({"old_label": "grill1", "new_label": "   "}, "new_label"),
    ],
)
def test_label_rename_requires_all_three_fields(client, folders, body, field):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Fields-Cook")
    resp = client.post("/api/files/cookfiles/label", json={"file": name, **body})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == field


def test_recover_upgrade_rewrites_a_current_version_file_as_a_no_op(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Upgrade-Cook")
    resp = client.post("/api/files/cookfiles/recover", json={"file": name, "action": "upgrade"})
    assert resp.status_code == 200 and resp.get_json()["result"] == "OK"
    assert read_member(history_dir, name, "metadata")["title"] == "Upgrade-Cook"


def test_recover_upgrade_makes_a_pre_1_5_file_readable(client, folders):
    """The point of the endpoint, over a genuine v1.0-shaped archive: detail
    422s with errortype "version", the client offers Attempt Conversion, and
    afterwards detail succeeds and the converted graph_labels are the modern
    nested shape."""
    history_dir, _ = folders
    name = write_legacy_cookfile(history_dir, "Convert-Cook")
    first = client.get(f"/api/files/cookfiles/detail?file={name}")
    assert first.status_code == 422
    assert first.get_json()["data"]["errortype"] == "version"

    assert client.post("/api/files/cookfiles/recover", json={"file": name, "action": "upgrade"}).status_code == 200
    after = client.get(f"/api/files/cookfiles/detail?file={name}")
    assert after.status_code == 200
    assert set(after.get_json()["graph_labels"]) == {"primarysp", "probes", "targets"}


def test_recover_repair_runs_upgrade_then_fixup_assets(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Repair-Cook")
    resp = client.post("/api/files/cookfiles/recover", json={"file": name, "action": "repair"})
    assert resp.status_code == 200 and resp.get_json()["result"] == "OK"


def test_recover_rejects_an_unknown_action(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Action-Cook")
    resp = client.post("/api/files/cookfiles/recover", json={"file": name, "action": "delete"})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "action"


@pytest.mark.parametrize(
    "route,body",
    [
        ("title", {"file": "../x.pifire", "title": "t"}),
        ("label", {"file": "../x.pifire", "old_label": "a", "new_label": "b"}),
        ("recover", {"file": "../x.pifire", "action": "upgrade"}),
    ],
)
def test_every_mutation_refuses_traversal(client, folders, route, body):
    """One sweep over all three, so a new mutation added without require_file
    fails here rather than shipping."""
    resp = client.post(f"/api/files/cookfiles/{route}", json=body)
    assert resp.status_code in (400, 404), route
