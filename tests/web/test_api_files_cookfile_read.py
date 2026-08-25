"""GET /api/files/cookfiles/detail and .../chart -- the read half of the
cook-file API.

These replace two legacy actions that take a filesystem path from the client
and use it unvalidated: `full_graph` hands request.json["filename"] straight to
read_cookfile (blueprints/cookfile/routes.py:37) and the page's detail render
comes from history's `opencookfile`. Here the client sends a bare filename and
the server resolves it through common.file_browser.resolve_managed_file, so the
traversal tests below are the point of the module, not a formality.

Harness rationale: see tests/web/test_api_files_listing.py's docstring.
"""

import zipfile

import pytest

from common.web_contracts.content import CookFileChartData, CookFileDetail
from tests.web.archive_builders import write_cookfile

DIAGNOSTICS_BYTES = (
    b'{\n  "schema_version" : 1,\n  "cook_id": "task-6-api-shape",\n'
    b'  "captured_at_ms": 123456789,\n  "controllers": [],\n  "reports": [],\n'
    b'  "control_trace": {"records": [], "record_schema_versions": []},\n'
    b'  "model_evidence": {"records": [], "record_schema_versions": []},\n'
    b'  "capture_errors": []\n}\n'
)


def _write_diagnostics(history_dir, name):
    with zipfile.ZipFile(history_dir + name, "a", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("learning_diagnostics.json", DIAGNOSTICS_BYTES)


@pytest.fixture
def client(api_files_client):
    return api_files_client


@pytest.fixture
def folders(api_files_folders):
    return api_files_folders


def test_detail_returns_metadata_events_and_comments(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Detail-Cook")

    resp = client.get(f"/api/files/cookfiles/detail?file={name}")
    assert resp.status_code == 200
    body = resp.get_json()
    validated = CookFileDetail.model_validate(body, strict=True)
    assert validated.model_dump(mode="json", exclude_unset=True) == body
    assert body["filename"] == name
    assert body["metadata"]["title"] == "Detail-Cook"
    assert body["metadata"]["units"] == "F"
    assert len(body["events"]) == 2
    assert body["events"][0]["mode"] == "Smoke"
    assert body["graph_labels"]["probes"] == {"grill1": "Grill"}
    assert body["comments"] == []
    assert body["assets"] == []


def test_list_and_detail_do_not_expose_learning_diagnostics(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Diagnostics-Boundary-Cook")
    _write_diagnostics(history_dir, name)

    detail = client.get(f"/api/files/cookfiles/detail?file={name}")
    assert detail.status_code == 200
    assert set(detail.get_json()) == {
        "filename",
        "metadata",
        "graph_labels",
        "events",
        "event_totals",
        "comments",
        "assets",
    }

    listing = client.get("/api/files/cookfiles")
    assert listing.status_code == 200
    item = next(item for item in listing.get_json()["items"] if item["filename"] == name)
    assert set(item) == {"filename", "title", "thumbnail"}


def test_detail_formats_times_and_keeps_the_epochs(client, folders):
    """render_cookfile_page (common/app.py:289-290) MUTATES metadata,
    replacing the epochs with HH:MM:SS -- so the Flask template can never show
    a date. The endpoint sends both: the display string the table shows, and
    the raw epoch a client needs to render a locale-correct date."""
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Times-Cook")
    body = client.get(f"/api/files/cookfiles/detail?file={name}").get_json()

    assert isinstance(body["metadata"]["starttime_epoch"], int)
    assert isinstance(body["metadata"]["endtime_epoch"], int)
    assert body["metadata"]["starttime"].count(":") == 2  # HH:MM:SS
    assert body["metadata"]["endtime"].count(":") == 2


def test_detail_does_not_html_escape_comment_text(client, folders):
    """render_cookfile_page does comment["text"].replace("\\n", "<br>") because
    Jinja is about to emit it as HTML. React renders text nodes, so injecting
    markup here would be both wrong and an XSS vector."""
    history_dir, _ = folders
    comments = [{"id": "c1", "date": "d", "time": "t", "text": "line1\nline2", "edited": "", "assets": []}]
    name = write_cookfile(history_dir, "Comment-Cook", comments=comments)
    body = client.get(f"/api/files/cookfiles/detail?file={name}").get_json()
    assert body["comments"][0]["text"] == "line1\nline2"


def test_detail_includes_event_totals(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Totals-Cook")
    body = client.get(f"/api/files/cookfiles/detail?file={name}").get_json()
    totals = body["event_totals"]
    assert set(totals) >= {
        "augerontime",
        "estusage_m",
        "estusage_i",
        "cooktime",
        "pellet_level_start",
        "pellet_level_end",
    }


def test_detail_of_a_cook_with_one_event_reports_no_totals(client, folders):
    """prepare_event_totals indexes events[-2] unconditionally
    (common/app.py:168), so a one-event file raises IndexError and the Flask
    page 500s. An incomplete cook must still open here."""
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Short-Cook")
    from file_mgmt.common import read_json_file_data, update_json_file_data

    events, status = read_json_file_data(history_dir + name, "events", unpackassets=False)
    assert status == "OK"
    assert update_json_file_data(events[:1], history_dir + name, "events") == "OK"

    resp = client.get(f"/api/files/cookfiles/detail?file={name}")
    assert resp.status_code == 200
    assert resp.get_json()["event_totals"] == {}


def test_chart_returns_the_full_graph_payload(client, folders):
    """Same four keys the legacy `full_graph` action returns
    (blueprints/cookfile/routes.py:42-47), minus the arbitrary-path read."""
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Chart-Cook")
    resp = client.get(f"/api/files/cookfiles/chart?file={name}")
    assert resp.status_code == 200
    body = resp.get_json()
    validated = CookFileChartData.model_validate(body, strict=True)
    assert validated.model_dump(mode="json", exclude_unset=True) == body
    assert set(body) == {"time_labels", "chart_data", "probe_mapper", "annotations"}
    assert body["chart_data"][0]["label"] == "Grill"
    assert body["probe_mapper"]["probes"] == {"grill1": 0}
    assert all(isinstance(t, (int, float)) for t in body["time_labels"])


def test_chart_annotations_are_keyed_by_event(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Anno-Cook")
    body = client.get(f"/api/files/cookfiles/chart?file={name}").get_json()
    assert isinstance(body["annotations"], dict)
    assert all(key.startswith("event_") for key in body["annotations"])


@pytest.mark.parametrize("route", ["detail", "chart"])
def test_missing_file_parameter_is_400(client, folders, route):
    resp = client.get(f"/api/files/cookfiles/{route}")
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "file"


@pytest.mark.parametrize("route", ["detail", "chart"])
def test_unknown_file_is_404(client, folders, route):
    resp = client.get(f"/api/files/cookfiles/{route}?file=Nope.pifire")
    assert resp.status_code == 404
    assert resp.get_json()["message"] == "not_found"


@pytest.mark.parametrize(
    "hostile",
    ["../../../etc/passwd", "../secret.pifire", "/etc/passwd", "..%2F..%2Fetc%2Fpasswd", ""],
)
@pytest.mark.parametrize("route", ["detail", "chart"])
def test_traversal_attempts_are_refused(client, folders, route, hostile):
    """The legacy equivalents accept these: `full_graph` passes the filename
    straight to read_cookfile (routes.py:37) and `dl_cookfile` passes it
    straight to send_file (:162)."""
    resp = client.get(f"/api/files/cookfiles/{route}?file={hostile}")
    assert resp.status_code in (400, 404)
    assert b"passwd" not in resp.data
    assert b"root:" not in resp.data


def test_a_traversal_to_a_real_cookfile_outside_the_folder_is_refused(client, folders, tmp_path):
    """Not just non-existent paths: a VALID .pifire that simply lives somewhere
    else must be refused too, or containment is only hiding read errors."""
    history_dir, _ = folders
    outside = str(tmp_path) + "/"
    name = write_cookfile(outside, "Outside-Cook")
    resp = client.get(f"/api/files/cookfiles/detail?file=../{name}")
    assert resp.status_code == 404
    assert "Outside-Cook" not in resp.get_data(as_text=True)
    assert history_dir not in resp.get_data(as_text=True)


def test_a_corrupt_archive_is_422_with_an_errortype(client, folders):
    """Drives the React repair prompt -- the equivalent of cferror.html's
    'Attempt Repair' / 'Attempt Conversion' branch."""
    history_dir, _ = folders
    with open(history_dir + "Broken.pifire", "w") as handle:
        handle.write("not a zip at all")
    resp = client.get("/api/files/cookfiles/detail?file=Broken.pifire")
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["result"] == "Error"
    assert body["data"]["errortype"] in ("version", "asset", "other")


def test_an_old_version_archive_is_422_with_errortype_version(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Old-Cook", version="0.0.1")

    resp = client.get(f"/api/files/cookfiles/detail?file={name}")
    assert resp.status_code == 422
    assert resp.get_json()["data"]["errortype"] == "version"
