"""GET /api/files/cookfiles and /api/files/recipes -- the JSON listing
endpoints the React file browser needs and that did not exist before.

Both legacy listings return HTML fragments (blueprints/cookfile/routes.py:267
renders cookfile/_cookfile_list.html; blueprints/recipes/routes.py:123 renders
recipes/_recipefile_list.html), so there was nothing for a typed client to
consume. These are read-only and share one handler -- the single piece the
cookfile and recipe surfaces genuinely have in common.

Harness: flask test_client + the `ds` fixture, the model
tests/web/test_api_history.py set for JSON endpoints. Deliberately NOT the
playwright `live_server`: that would make every test here -- including the
containment tests in the sibling modules -- SKIP on a machine without chromium,
which is exactly where a security test must not be silent. There is no DOM
involved, so playwright buys nothing.
"""

import json

import pytest
from common.web_contracts.content import ContentErrorEnvelope, FileListing

from tests.web.archive_builders import write_cookfile, write_recipe


@pytest.fixture
def client(api_files_client):
    return api_files_client


@pytest.fixture
def folders(api_files_folders):
    return api_files_folders


def test_cookfiles_listing_returns_json_with_titles(client, folders):
    history_dir, _ = folders
    write_cookfile(history_dir, "AAA-Cook")
    write_cookfile(history_dir, "BBB-Cook")

    resp = client.get("/api/files/cookfiles?page=1&per_page=10&reverse=false")
    assert resp.status_code == 200
    body = resp.get_json()
    validated = FileListing.model_validate(body, strict=True)
    assert validated.model_dump(mode="json", exclude_unset=True) == body
    names = [i["filename"] for i in body["items"]]
    assert "AAA-Cook.pifire" in names and "BBB-Cook.pifire" in names
    titles = {i["filename"]: i["title"] for i in body["items"]}
    assert titles["AAA-Cook.pifire"] == "AAA-Cook"
    assert body["page"] == 1
    assert body["per_page"] == 10
    assert body["reverse"] is False
    assert body["total"] == 2


def test_cookfiles_listing_defaults_match_the_flask_page(client, folders):
    """history.js:337 calls gotoCFPage(1, true, 10) -- page 1, reverse, 10 per
    page. The endpoint's defaults must be the same so the React list opens on
    the same rows the Flask list did."""
    resp = client.get("/api/files/cookfiles")
    assert resp.status_code == 200
    body = resp.get_json()
    assert (body["page"], body["per_page"], body["reverse"]) == (1, 10, True)


def test_recipes_listing_uses_the_same_shape(client, folders):
    _, recipe_dir = folders
    write_recipe(recipe_dir, "Pulled-Pork")

    resp = client.get("/api/files/recipes")
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) == {"items", "page", "last_page", "per_page", "reverse", "total"}
    assert {"filename": "Pulled-Pork.pfrecipe", "title": "Pulled-Pork", "thumbnail": ""} in body["items"]


def test_the_two_kinds_do_not_see_each_others_files(client, folders):
    history_dir, recipe_dir = folders
    write_cookfile(history_dir, "Only-Cook")
    write_recipe(recipe_dir, "Only-Recipe")

    cooks = client.get("/api/files/cookfiles").get_json()
    recipes = client.get("/api/files/recipes").get_json()
    assert [i["filename"] for i in cooks["items"]] == ["Only-Cook.pifire"]
    assert [i["filename"] for i in recipes["items"]] == ["Only-Recipe.pfrecipe"]


def test_pagination_reports_last_page(client, folders):
    history_dir, _ = folders
    for i in range(12):
        write_cookfile(history_dir, f"Page-{i:02d}")
    body = client.get("/api/files/cookfiles?per_page=5").get_json()
    assert body["per_page"] == 5
    assert body["last_page"] == 3
    assert body["total"] == 12
    assert len(body["items"]) == 5


def test_reverse_ordering_is_honoured(client, folders):
    history_dir, _ = folders
    for i in range(3):
        write_cookfile(history_dir, f"Sort-{i}")
    desc = client.get("/api/files/cookfiles?reverse=true").get_json()
    asc = client.get("/api/files/cookfiles?reverse=false").get_json()
    assert [i["filename"] for i in desc["items"]] == ["Sort-2.pifire", "Sort-1.pifire", "Sort-0.pifire"]
    assert [i["filename"] for i in asc["items"]] == ["Sort-0.pifire", "Sort-1.pifire", "Sort-2.pifire"]


def test_unknown_kind_is_404(client, folders):
    resp = client.get("/api/files/pelletfiles")
    assert resp.status_code == 404
    assert resp.get_json()["result"] == "Error"


@pytest.mark.parametrize(
    "query,field",
    [("page=abc", "page"), ("page=0", "page"), ("per_page=7", "per_page"), ("per_page=xyz", "per_page")],
)
def test_bad_query_parameters_are_400_and_name_the_field(client, folders, query, field):
    resp = client.get(f"/api/files/cookfiles?{query}")
    assert resp.status_code == 400
    body = resp.get_json()
    validated = ContentErrorEnvelope.model_validate(body, strict=True)
    assert validated.model_dump(mode="json", exclude_unset=True) == body
    assert body["result"] == "Error"
    assert body["message"] == "bad_request"
    assert body["data"]["field"] == field


def test_listing_never_leaks_a_filesystem_path(client, folders):
    """Only bare filenames cross the wire. A client that is handed a path will
    send one back, and every legacy cookfile route that accepts one is an
    unvalidated open (blueprints/cookfile/routes.py:162)."""
    history_dir, _ = folders
    write_cookfile(history_dir, "NoPath-Cook")
    body = client.get("/api/files/cookfiles").get_json()
    assert body["items"]
    for item in body["items"]:
        assert "/" not in item["filename"]
        assert history_dir not in json.dumps(item)


def test_a_corrupt_archive_does_not_blank_the_listing(client, folders):
    history_dir, _ = folders
    write_cookfile(history_dir, "Good-Cook")
    with open(history_dir + "Broken.pifire", "w") as handle:
        handle.write("not a zip")
    body = client.get("/api/files/cookfiles").get_json()
    titles = {i["filename"]: i["title"] for i in body["items"]}
    assert titles == {"Good-Cook.pifire": "Good-Cook", "Broken.pifire": "ERROR"}
