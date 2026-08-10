"""POST /api/files/cookfiles/comments and .../comments/assets.

One endpoint with an `action` instead of the legacy blueprint's four separate
JSON keys (commentnew / editcomment / savecomment / delcomment), and one
whole-list asset write instead of its per-asset toggle. Both differences are
about failure modes, not tidiness -- see the tests.

Harness rationale: see tests/web/test_api_files_listing.py's docstring.
"""

import pytest
from common.web_contracts.content import CookFileComment

from tests.web.archive_builders import write_cookfile

URL = "/api/files/cookfiles/comments"
ASSETS_URL = "/api/files/cookfiles/comments/assets"


@pytest.fixture
def client(api_files_client):
    return api_files_client


@pytest.fixture
def folders(api_files_folders):
    return api_files_folders


def _read_comments(history_dir, name):
    from file_mgmt.common import read_json_file_data

    data, status = read_json_file_data(history_dir + name, "comments", unpackassets=False)
    assert status == "OK"
    return data


def _add(client, name, text="c"):
    resp = client.post(URL, json={"file": name, "action": "add", "text": text})
    assert resp.status_code == 200
    return resp.get_json()["data"]["id"]


def test_comment_lifecycle_add_update_delete(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Comment-Cook")

    added = client.post(URL, json={"file": name, "action": "add", "text": "First light"})
    assert added.status_code == 200
    data = added.get_json()["data"]
    validated = CookFileComment.model_validate(data, strict=True)
    assert validated.model_dump(mode="json", exclude_unset=True) == data
    cid = data["id"]
    assert data["text"] == "First light"
    assert data["edited"] == ""
    assert data["assets"] == []
    assert _read_comments(history_dir, name)[0]["text"] == "First light"

    updated = client.post(URL, json={"file": name, "action": "update", "id": cid, "text": "Second light"})
    assert updated.status_code == 200
    assert updated.get_json()["data"]["edited"] != ""
    assert _read_comments(history_dir, name)[0]["text"] == "Second light"

    deleted = client.post(URL, json={"file": name, "action": "delete", "id": cid})
    assert deleted.status_code == 200
    assert _read_comments(history_dir, name) == []


def test_adding_a_second_comment_keeps_the_first(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Two-Cook")
    _add(client, name, "one")
    _add(client, name, "two")
    assert [c["text"] for c in _read_comments(history_dir, name)] == ["one", "two"]


def test_comment_text_keeps_its_newlines_and_is_never_html(client, folders):
    """render_cookfile_page does `text.replace("\\n", "<br>")` (common/app.py:287)
    because Jinja emits it as HTML. React renders a text node, so the API must
    NOT inject markup -- doing so would print a literal <br> at best and be an
    XSS vector at worst."""
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Newline-Cook")
    text = "line one\nline two <script>x</script>"
    body = client.post(URL, json={"file": name, "action": "add", "text": text}).get_json()
    assert body["data"]["text"] == text
    assert "<br>" not in body["data"]["text"]
    assert _read_comments(history_dir, name)[0]["text"] == text


@pytest.mark.parametrize("action", ["update", "delete"])
def test_unknown_comment_id_is_404_not_a_false_success(client, folders, action):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "MissingC-Cook")
    resp = client.post(URL, json={"file": name, "action": action, "id": "nope", "text": "x"})
    assert resp.status_code == 404
    assert resp.get_json()["message"] == "comment_not_found"


def test_comment_on_an_unreadable_file_is_422_not_a_crash(client, folders):
    """The legacy `comments` branch reads without checking status and then does
    cookfiledata.append(...) on a dict -- AttributeError -> HTTP 500."""
    history_dir, _ = folders
    with open(history_dir + "Bad.pifire", "w") as handle:
        handle.write("nope")
    resp = client.post(URL, json={"file": "Bad.pifire", "action": "add", "text": "x"})
    assert resp.status_code == 422


def test_an_unknown_action_is_400(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "BadAct-Cook")
    resp = client.post(URL, json={"file": name, "action": "drop", "text": "x"})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "action"


@pytest.mark.parametrize(
    "body,field",
    [
        ({"action": "add"}, "text"),
        ({"action": "add", "text": 5}, "text"),
        ({"action": "update", "text": "x"}, "id"),
        ({"action": "update", "id": "a"}, "text"),
        ({"action": "delete"}, "id"),
    ],
)
def test_comment_requests_validate_their_fields(client, folders, body, field):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Valid-Cook")
    resp = client.post(URL, json={"file": name, **body})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == field


def test_comments_refuse_traversal(client, folders):
    resp = client.post(URL, json={"file": "../x.pifire", "action": "add", "text": "x"})
    assert resp.status_code in (400, 404)


def test_setting_a_comments_asset_list_replaces_it_wholesale(client, folders):
    """Flask toggles ONE asset per request and infers add-vs-remove from a
    client-sent `state` string (routes.py:566-586) -- so a stale client view
    silently inverts the operation. This endpoint takes the whole list the user
    ended up with, which cannot invert."""
    history_dir, _ = folders
    name = write_cookfile(history_dir, "Assets-Cook")
    cid = _add(client, name)

    resp = client.post(ASSETS_URL, json={"file": name, "id": cid, "assets": ["a.png", "b.png"]})
    assert resp.status_code == 200
    assert resp.get_json()["data"]["assets"] == ["a.png", "b.png"]
    assert _read_comments(history_dir, name)[0]["assets"] == ["a.png", "b.png"]

    cleared = client.post(ASSETS_URL, json={"file": name, "id": cid, "assets": []})
    assert cleared.status_code == 200
    assert _read_comments(history_dir, name)[0]["assets"] == []


@pytest.mark.parametrize("bad", ["a.png", [1, 2], {"a": 1}, None])
def test_comment_assets_must_be_a_list_of_strings(client, folders, bad):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "AssetType-Cook")
    cid = _add(client, name)
    resp = client.post(ASSETS_URL, json={"file": name, "id": cid, "assets": bad})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "assets"


def test_comment_assets_for_an_unknown_comment_is_404(client, folders):
    history_dir, _ = folders
    name = write_cookfile(history_dir, "NoC-Cook")
    resp = client.post(ASSETS_URL, json={"file": name, "id": "nope", "assets": []})
    assert resp.status_code == 404
    assert resp.get_json()["message"] == "comment_not_found"
