import pytest
from common.web_contracts.content import RecipeDetail

from tests.web.archive_builders import write_recipe

pytestmark = pytest.mark.usefixtures("api_files_folders")


@pytest.fixture
def client(api_files_client):
    return api_files_client


@pytest.fixture
def folders(api_files_folders):
    return api_files_folders


def test_detail_returns_the_four_editable_sections(client, folders):
    _history, recipe_dir = folders
    name = write_recipe(
        recipe_dir,
        "Brisket",
        ingredients=[{"name": "Brisket", "quantity": "1 packer", "assets": []}],
        instructions=[{"text": "Trim it", "ingredients": ["Brisket"], "assets": [], "step": 0}],
    )
    resp = client.get(f"/api/files/recipes/detail?file={name}")
    assert resp.status_code == 200
    body = resp.get_json()
    validated = RecipeDetail.model_validate(body, strict=True)
    assert validated.model_dump(mode="json", exclude_unset=True) == body
    assert body["metadata"]["title"] == "Brisket"
    assert body["recipe"]["ingredients"][0]["name"] == "Brisket"
    assert body["recipe"]["instructions"][0]["ingredients"] == ["Brisket"]
    assert len(body["recipe"]["steps"]) == 1
    assert body["assets"] == []
    # Ruling 1: comments are preserved in the archive but never published.
    assert "comments" not in body


@pytest.mark.parametrize(
    "hostile",
    ["../../../etc/passwd", "../secret.pfrecipe", "/etc/passwd", "..%2F..%2Fetc%2Fpasswd", ""],
)
def test_traversal_attempts_are_refused(client, folders, hostile):
    resp = client.get(f"/api/files/recipes/detail?file={hostile}")
    assert resp.status_code in (400, 404)
    assert b"passwd" not in resp.data
    assert b"root:" not in resp.data


def test_a_traversal_to_a_real_recipe_outside_the_folder_is_refused(client, folders, tmp_path):
    _history, recipe_dir = folders
    name = write_recipe(str(tmp_path) + "/", "Outside-Recipe")
    resp = client.get(f"/api/files/recipes/detail?file=../{name}")
    assert resp.status_code == 404
    assert "Outside-Recipe" not in resp.get_data(as_text=True)
