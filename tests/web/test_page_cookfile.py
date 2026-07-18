"""Playwright coverage for the cookfile page (blueprints/cookfile/routes.py's
`cookfile_page`/`cookfile_update`, plus `_get_cookfilelist`/
`_get_cookfilelist_details`) -- the cook-history detail viewer for a single
`.pifire` cook file (a zip archive of metadata/graph_data/raw_data/
graph_labels/events/comments/assets JSON, see file_mgmt/cookfile.py's
`create_cookfile`/`read_cookfile`/`_default_cookfilestruct`).

Follows the pattern established in test_page_settings.py/test_page_pellets.py;
see tests/web/conftest.py for the shared harness.

A real .pifire is required
---------------------------
Every action in this route reads an existing cookfile off disk -- there is
no in-memory fixture to substitute. Rather than driving a full cook cycle
(startup->hold->stop, which needs the real control-runtime loop this test
harness deliberately doesn't run -- see conftest.py's `drain_control_writes`
docstring) to get `create_cookfile()` to produce one, `_write_cookfile()`
below hand-builds a minimal-but-valid `.pifire` zip directly matching the
schema `_default_cookfilestruct()`/`create_cookfile()` produce, using the
live server's own `settings["versions"]["cookfile"]` value so
`read_cookfile()`'s version-gate (file version >= settings version) passes.
Two latent crashers were found while sizing the minimum viable content (see
task report): `prepare_event_totals()` (common/app.py) does `events[-1]`/
`events[0]`/`events[-2]` unconditionally -- an empty or single-entry
`events.json` throws IndexError -- and `prepare_csv()` does
`data[0].keys()` unconditionally -- an empty `raw_data.json` does too. Both
are worked around here by seeding >=2 events / >=1 raw_data row (matching
common.defaults.default_metrics()'s exact key set so nothing KeyErrors),
not by patching the app code.

Folder isolation
-----------------
`cookfile_page`/`cookfile_update` correctly read
`current_app.config["HISTORY_FOLDER"]`. `file_mgmt/cookfile.py` and
file_mgmt/common.py each separately define their OWN module-level
`HISTORY_FOLDER = "./history/"` constant (used by `create_cookfile()` and
`fixup_assets()` respectively) instead of app.config -- the same
config/module-constant split found on the recipes side.
`_isolated_history_folder` below patches all three (app.config +
both module constants) to a per-module temp dir, restoring on teardown.

Interaction style
------------------
`cookfile_update`'s branches (comments/metadata/graph_labels/media) are all
JSON POSTs -- covered via direct `page.request.post(..., data=...)`,
persistence asserted by reading the `.pifire` zip back directly (mirrors
`read_settings_from_server()` in conftest.py). `cookfile_page`'s own
`cookfilelist` branch is exercised through the REAL UI: `history/index.html`
auto-loads it via AJAX on page load (history.js's `gotoCFPage`), and
clicking a row's "Open" button submits a real form
(`managecookfile` -> POST `/history/cookfile`, in blueprints/history/routes.py
-- see below) that renders `cookfile/index.html`. The remaining
`cookfile_page` form/JSON actions (`thumbSelected`, `repairCF`, `upgradeCF`,
`delmedialist`, `full_graph`, `getcommentassets`, `managemediacomment`,
`getallmedia`, `navimage`) are covered via direct POST.

Finding: `cookfile_page` has no bare GET render
-------------------------------------------------
Every branch in `cookfile_page` is gated behind
`request.method == "POST"` (json- or form-content-type); a plain
`GET /cookfile/` falls through to the final
`jsonify({"result": "ERROR", ...})`. The actual "view one cookfile" page is
rendered by a DIFFERENT route -- `blueprints/history/routes.py`'s
`history_page(action="cookfile")`, action `opencookfile` -- which shares the
same `cookfile/index.html` template. `test_cookfile_page_bare_get_returns_error`
below characterizes this; `test_history_lists_and_opens_cookfile_via_real_ui`
drives the real click-path through `history_page` to get onto that template
for the first time in the browser.

NOT covered (see task report for details):
- `ulcookfilereq` (cook-file upload) and the `ulmediafn`/`ulthumbfn` (media
  upload) form actions in `cookfile_page`: `ulcookfilereq` has a latent bug
  (`remotefile.save(os.path.join("HISTORY_FOLDER", filename))` -- the
  literal string `"HISTORY_FOLDER"`, not the variable) that would create a
  real `./HISTORY_FOLDER/` directory under the process's cwd (the actual
  repo checkout, since live_server runs in-process) if exercised --
  deliberately not triggered to avoid polluting the repo tree; see report.
- `repairCF`/`upgradeCF` ARE covered (our fabricated file's version matches
  the live `settings["versions"]["cookfile"]`, so both are effectively
  no-op upgrades that still exercise the full read/rewrite path).
"""

import io
import json
import os
import shutil
import tempfile
import time
import uuid
import zipfile

import pytest
from PIL import Image

from tests.web.conftest import read_settings_from_server, requires_chromium

pytestmark = requires_chromium


@pytest.fixture(scope="module", autouse=True)
def _isolated_history_folder(live_server):
    from app import app as flask_app
    import file_mgmt.cookfile as cookfile_mod
    import file_mgmt.common as common_mod

    tmp_dir = tempfile.mkdtemp(prefix="pifire_test_history_")
    history_dir = os.path.join(tmp_dir, "history") + "/"
    os.makedirs(history_dir, exist_ok=True)

    orig_app_folder = flask_app.config["HISTORY_FOLDER"]
    orig_cookfile_mod_folder = cookfile_mod.HISTORY_FOLDER
    orig_common_mod_folder = common_mod.HISTORY_FOLDER
    flask_app.config["HISTORY_FOLDER"] = history_dir
    cookfile_mod.HISTORY_FOLDER = history_dir
    common_mod.HISTORY_FOLDER = history_dir

    yield history_dir

    flask_app.config["HISTORY_FOLDER"] = orig_app_folder
    cookfile_mod.HISTORY_FOLDER = orig_cookfile_mod_folder
    common_mod.HISTORY_FOLDER = orig_common_mod_folder
    shutil.rmtree(tmp_dir, ignore_errors=True)


def _write_cookfile(history_dir, title, *, version=None, comments=None):
    """Hand-build a minimal-but-valid `.pifire` zip -- see module docstring
    for why this is necessary and which fields are load-bearing. Returns
    the bare filename (e.g. "E2E-CookFile.pifire")."""
    from common.defaults import default_metrics

    if version is None:
        version = read_settings_from_server()["versions"]["cookfile"]

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - 3600_000

    metadata = {
        "title": title,
        "starttime": start_ms,
        "endtime": now_ms,
        "units": "F",
        "thumbnail": "",
        "id": str(uuid.uuid4()),
        "version": version,
    }
    graph_data = {
        "time_labels": ["12:00:00", "12:05:00"],
        "chart_data": [{"label": "Grill", "data": [225, 230]}],
        "probe_mapper": {"probes": {"grill1": 0}, "targets": {}, "primarysp": {}},
    }
    graph_labels = {"probes": {"grill1": "Grill"}, "targets": {}, "primarysp": {}}
    raw_data = [
        {
            "T": start_ms,
            "P": {"grill1": 225},
            "PSP": 225,
            "F": {"probe1": 150},
            "NT": {"grill1": 225, "probe1": 165},
            "AUX": {},
        },
        {
            "T": now_ms,
            "P": {"grill1": 230},
            "PSP": 225,
            "F": {"probe1": 160},
            "NT": {"grill1": 225, "probe1": 165},
            "AUX": {},
        },
    ]
    event_start = default_metrics()
    event_start.update(
        {
            "id": 0,
            "starttime": start_ms,
            "endtime": now_ms,
            "mode": "Smoke",
            "augerontime": 120,
            "pellet_level_start": 100,
            "pellet_level_end": 95,
        }
    )
    event_end = default_metrics()
    event_end.update(
        {
            "id": 1,
            "starttime": now_ms,
            "endtime": now_ms,
            "mode": "Stop",
            "augerontime": 30,
            "pellet_level_start": 95,
            "pellet_level_end": 90,
        }
    )
    events = [event_start, event_end]

    files = {
        "metadata.json": metadata,
        "graph_data.json": graph_data,
        "raw_data.json": raw_data,
        "graph_labels.json": graph_labels,
        "events.json": events,
        "comments.json": comments if comments is not None else [],
        "assets.json": [],
    }

    filename = f"{title}.pifire"
    with zipfile.ZipFile(history_dir + filename, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, json.dumps(data))
    return filename


def _read_cookfile_json(history_dir, filename, jsonfile):
    from file_mgmt.common import read_json_file_data

    data, status = read_json_file_data(history_dir + filename, jsonfile, unpackassets=False)
    assert status == "OK"
    return data


def test_cookfile_page_bare_get_returns_error(live_server, page):
    """Characterizes the finding in the module docstring: there is no bare
    GET render at `/cookfile/` -- every branch requires a POST body."""
    resp = page.request.get(f"{live_server}/cookfile/")
    assert resp.status == 200
    assert resp.json()["result"] == "ERROR"


def test_history_lists_and_opens_cookfile_via_real_ui(live_server, page, _isolated_history_folder):
    """Real UI: history/index.html auto-loads the paginated cookfile list
    via AJAX (history.js's gotoCFPage -> POST /cookfile {cookfilelist:true}
    -- cookfile_page's own `cookfilelist` branch, exercising
    _get_cookfilelist/_get_cookfilelist_details), then the row's Open button
    submits a real form POST to /history/cookfile (opencookfile=filename),
    landing on cookfile/index.html -- the actual "view one cookfile" page."""
    history_dir = _isolated_history_folder
    filename = _write_cookfile(history_dir, "E2E-UI-CookFile")

    page.goto(f"{live_server}/history/")
    page.wait_for_selector(f"button#opencookfile[value='{filename}']")
    assert filename in page.content()

    with page.expect_navigation():
        page.locator(f"button#opencookfile[value='{filename}']").click()

    assert page.title().startswith("History")
    assert "Comments" in page.content()
    assert page.locator("#newcommenttext").count() == 1


def test_full_graph_via_direct_post(live_server, page, _isolated_history_folder):
    history_dir = _isolated_history_folder
    filename = _write_cookfile(history_dir, "E2E-FullGraph")

    resp = page.request.post(f"{live_server}/cookfile/", data={"full_graph": True, "filename": history_dir + filename})
    assert resp.status == 200
    body = resp.json()
    assert body["chart_data"] == [{"label": "Grill", "data": [225, 230]}]
    assert body["time_labels"] == ["12:00:00", "12:05:00"]
    assert body["probe_mapper"]["probes"] == {"grill1": 0}


def test_getcommentassets_and_getallmedia_and_managemediacomment_via_direct_post(
    live_server, page, _isolated_history_folder
):
    history_dir = _isolated_history_folder
    comments = [{"id": "c1", "text": "hi", "assets": ["a1.png"]}]
    filename = _write_cookfile(history_dir, "E2E-Media", comments=comments)
    cookfilepath = history_dir + filename

    resp = page.request.post(
        f"{live_server}/cookfile/", data={"getcommentassets": True, "cookfilename": cookfilepath, "commentid": "c1"}
    )
    assert resp.status == 200
    assert resp.json() == {"result": "OK", "assetlist": ["a1.png"]}

    resp2 = page.request.post(f"{live_server}/cookfile/", data={"getallmedia": True, "cookfilename": cookfilepath})
    assert resp2.status == 200
    assert resp2.json() == {"result": "OK", "assetlist": []}  # assets.json is empty in our fixture

    resp3 = page.request.post(
        f"{live_server}/cookfile/",
        data={"managemediacomment": True, "cookfilename": cookfilepath, "commentid": "c1"},
    )
    assert resp3.status == 200
    assert resp3.json() == {"result": "OK", "assetlist": []}  # assets.json empty -> no candidate assets to list


def test_navimage_via_direct_post(live_server, page, _isolated_history_folder):
    history_dir = _isolated_history_folder
    comments = [{"id": "c1", "text": "hi", "assets": ["a1.png", "a2.png", "a3.png"]}]
    filename = _write_cookfile(history_dir, "E2E-NavImage", comments=comments)
    cookfilepath = history_dir + filename

    resp_next = page.request.post(
        f"{live_server}/cookfile/",
        data={"navimage": "next", "mediafilename": "a1.png", "commentid": "c1", "cookfilename": cookfilepath},
    )
    assert resp_next.status == 200
    assert resp_next.json() == {"result": "OK", "mediafilename": "a2.png"}

    resp_wrap = page.request.post(
        f"{live_server}/cookfile/",
        data={"navimage": "next", "mediafilename": "a3.png", "commentid": "c1", "cookfilename": cookfilepath},
    )
    assert resp_wrap.status == 200
    assert resp_wrap.json() == {"result": "OK", "mediafilename": "a1.png"}

    resp_prev = page.request.post(
        f"{live_server}/cookfile/",
        data={"navimage": "prev", "mediafilename": "a1.png", "commentid": "c1", "cookfilename": cookfilepath},
    )
    assert resp_prev.status == 200
    assert resp_prev.json() == {"result": "OK", "mediafilename": "a3.png"}


def test_thumbselected_via_direct_post(live_server, page, _isolated_history_folder):
    history_dir = _isolated_history_folder
    filename = _write_cookfile(history_dir, "E2E-Thumb")

    resp = page.request.post(f"{live_server}/cookfile/", form={"thumbSelected": "asset123.png", "filename": filename})
    assert resp.status == 200
    assert "Comments" in resp.text()

    metadata = _read_cookfile_json(history_dir, filename, "metadata")
    assert metadata["thumbnail"] == "asset123.png"


def test_repaircf_and_upgradecf_via_direct_post(live_server, page, _isolated_history_folder):
    """Both actions run `upgrade_cookfile()` -- our fabricated file's
    version already matches the live settings version, so this exercises
    the full read-rewrite-rerender path as an effective no-op upgrade."""
    history_dir = _isolated_history_folder
    filename = _write_cookfile(history_dir, "E2E-Upgrade")
    cookfilepath = history_dir + filename

    resp_upgrade = page.request.post(f"{live_server}/cookfile/", form={"upgradeCF": cookfilepath})
    assert resp_upgrade.status == 200
    assert "Comments" in resp_upgrade.text()

    filename2 = _write_cookfile(history_dir, "E2E-Repair")
    cookfilepath2 = history_dir + filename2
    resp_repair = page.request.post(f"{live_server}/cookfile/", form={"repairCF": cookfilepath2})
    assert resp_repair.status == 200
    assert "Comments" in resp_repair.text()


def test_delmedialist_via_direct_post(live_server, page, _isolated_history_folder):
    history_dir = _isolated_history_folder
    comments = [{"id": "c1", "text": "hi", "assets": ["gone.png"]}]
    filename = _write_cookfile(history_dir, "E2E-DelMedia", comments=comments)

    resp = page.request.post(
        f"{live_server}/cookfile/",
        form={"delmedialist": filename, "delAssetlist": "gone.png"},
    )
    assert resp.status == 200
    assert "Comments" in resp.text()

    comments_after = _read_cookfile_json(history_dir, filename, "comments")
    assert comments_after[0]["assets"] == []


def test_upload_media_and_thumbnail_via_direct_post(live_server, page, _isolated_history_folder):
    """`ulmediafn`/`ulthumbfn` run the same real Pillow media pipeline as
    recipes' `uploadassets` (file_mgmt/media.py's `add_asset`, plus
    `set_thumbnail` for the thumbnail variant) -- exercised with real
    in-memory PNGs. Unlike `ulcookfilereq` (see module docstring), these two
    correctly use the local `HISTORY_FOLDER` variable (not a literal
    string), so they're safe to drive against the isolated temp dir.

    Latent bug worked around: same as recipes' `uploadassets` (see
    test_page_recipes.py) -- `os.mkdir(tmp_path)` for `/tmp/pifire/<id>`
    has no parent creation, and a freshly-written cookfile has zero
    existing assets so the lazy `/tmp/pifire` creation inside
    `read_json_file_data`'s asset-unpack loop never fires. Pre-create it."""
    os.makedirs("/tmp/pifire", exist_ok=True)
    history_dir = _isolated_history_folder
    filename = _write_cookfile(history_dir, "E2E-UploadMedia")

    png_buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (0, 255, 0)).save(png_buffer, format="PNG")
    resp = page.request.post(
        f"{live_server}/cookfile/",
        multipart={
            "ulmediafn": filename,
            "ulmedia": {"name": "media1.png", "mimeType": "image/png", "buffer": png_buffer.getvalue()},
        },
    )
    assert resp.status == 200
    assert "Comments" in resp.text()
    assets = _read_cookfile_json(history_dir, filename, "assets")
    assert len(assets) == 1
    assert assets[0]["type"] == "png"

    thumb_buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (0, 0, 255)).save(thumb_buffer, format="PNG")
    resp2 = page.request.post(
        f"{live_server}/cookfile/",
        multipart={
            "ulthumbfn": filename,
            "ulthumbnail": {"name": "thumb1.png", "mimeType": "image/png", "buffer": thumb_buffer.getvalue()},
        },
    )
    assert resp2.status == 200
    assert "Comments" in resp2.text()
    metadata = _read_cookfile_json(history_dir, filename, "metadata")
    assert metadata["thumbnail"] != ""
    assets_after = _read_cookfile_json(history_dir, filename, "assets")
    assert len(assets_after) == 2


# --- cookfile_update ------------------------------------------------------


def test_cookfile_update_comments_full_lifecycle_via_direct_post(live_server, page, _isolated_history_folder):
    """Covers all 4 `comments` sub-actions: commentnew/editcomment/
    savecomment/delcomment."""
    history_dir = _isolated_history_folder
    filename = _write_cookfile(history_dir, "E2E-Comments")
    # cookfile_update, unlike cookfile_page, does NOT prefix its
    # requestjson["filename"] with HISTORY_FOLDER -- it's passed straight
    # to read_json_file_data(), so the caller (real JS) always sends the
    # FULL path. A bare filename here silently no-ops (read_json_file_data
    # can't find the zip relative to cwd, returns status != "OK") rather
    # than 404ing -- discovered while writing this test; see task report.
    cookfilepath = history_dir + filename

    new_resp = page.request.post(
        f"{live_server}/cookfile/update",
        data={"comments": True, "filename": cookfilepath, "commentnew": "First comment"},
    )
    assert new_resp.status == 200
    new_body = new_resp.json()
    assert new_body["result"] == "OK"
    comment_id = new_body["newcommentid"]
    comments = _read_cookfile_json(history_dir, filename, "comments")
    assert comments[0]["text"] == "First comment"
    assert comments[0]["id"] == comment_id

    edit_resp = page.request.post(
        f"{live_server}/cookfile/update",
        data={"comments": True, "filename": cookfilepath, "editcomment": comment_id},
    )
    assert edit_resp.status == 200
    assert edit_resp.json() == {"result": "OK", "text": "First comment"}

    save_resp = page.request.post(
        f"{live_server}/cookfile/update",
        data={"comments": True, "filename": cookfilepath, "savecomment": comment_id, "text": "Edited comment"},
    )
    assert save_resp.status == 200
    save_body = save_resp.json()
    assert save_body["result"] == "OK"
    assert save_body["text"] == "Edited comment"
    comments = _read_cookfile_json(history_dir, filename, "comments")
    assert comments[0]["text"] == "Edited comment"
    assert comments[0]["edited"] != ""

    del_resp = page.request.post(
        f"{live_server}/cookfile/update",
        data={"comments": True, "filename": cookfilepath, "delcomment": comment_id},
    )
    assert del_resp.status == 200
    assert del_resp.json() == {"result": "OK"}
    comments = _read_cookfile_json(history_dir, filename, "comments")
    assert comments == []


def test_cookfile_update_metadata_edittitle_via_direct_post(live_server, page, _isolated_history_folder):
    history_dir = _isolated_history_folder
    filename = _write_cookfile(history_dir, "E2E-MetaUpdate")

    resp = page.request.post(
        f"{live_server}/cookfile/update",
        data={"metadata": True, "filename": history_dir + filename, "editTitle": "Renamed Cook"},
    )
    assert resp.status == 200
    assert resp.json() == {"result": "OK"}

    metadata = _read_cookfile_json(history_dir, filename, "metadata")
    assert metadata["title"] == "Renamed Cook"


def test_cookfile_update_graph_labels_via_direct_post(live_server, page, _isolated_history_folder):
    history_dir = _isolated_history_folder
    filename = _write_cookfile(history_dir, "E2E-GraphLabels")

    resp = page.request.post(
        f"{live_server}/cookfile/update",
        data={
            "graph_labels": True,
            "filename": history_dir + filename,
            "old_label": "grill1",
            "new_label": "Main Grill",
        },
    )
    assert resp.status == 200
    body = resp.json()
    assert body["result"] == "OK"
    assert body["new_label_safe"] == "main_grill" or body["new_label_safe"] != ""

    new_label_safe = body["new_label_safe"]
    graph_labels = _read_cookfile_json(history_dir, filename, "graph_labels")
    assert graph_labels["probes"][new_label_safe] == "Main Grill"
    assert "grill1" not in graph_labels["probes"]

    graph_data = _read_cookfile_json(history_dir, filename, "graph_data")
    assert new_label_safe in graph_data["probe_mapper"]["probes"]
    chart_index = graph_data["probe_mapper"]["probes"][new_label_safe]
    assert graph_data["chart_data"][chart_index]["label"] == "Main Grill"


def test_cookfile_update_media_toggle_via_direct_post(live_server, page, _isolated_history_folder):
    history_dir = _isolated_history_folder
    comments = [{"id": "c1", "text": "hi", "assets": []}]
    filename = _write_cookfile(history_dir, "E2E-MediaToggle", comments=comments)

    select_resp = page.request.post(
        f"{live_server}/cookfile/update",
        data={
            "media": True,
            "filename": history_dir + filename,
            "assetfilename": "photo.jpg",
            "commentid": "c1",
            "state": "unselected",
        },
    )
    assert select_resp.status == 200
    assert select_resp.json() == {"result": "OK"}
    comments_after = _read_cookfile_json(history_dir, filename, "comments")
    assert "photo.jpg" in comments_after[0]["assets"]

    unselect_resp = page.request.post(
        f"{live_server}/cookfile/update",
        data={
            "media": True,
            "filename": history_dir + filename,
            "assetfilename": "photo.jpg",
            "commentid": "c1",
            "state": "selected",
        },
    )
    assert unselect_resp.status == 200
    assert unselect_resp.json() == {"result": "OK"}
    comments_after = _read_cookfile_json(history_dir, filename, "comments")
    assert "photo.jpg" not in comments_after[0]["assets"]


def test_cookfile_update_bare_get_returns_error(live_server, page):
    """Same bare-GET characterization as cookfile_page: cookfile_update's
    entire body is gated behind `request.method == "POST"`."""
    resp = page.request.get(f"{live_server}/cookfile/update")
    assert resp.status == 200
    assert resp.json() == {"result": "ERROR"}
