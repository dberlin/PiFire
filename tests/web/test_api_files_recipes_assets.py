"""API tests for /api/files/recipes/assets, /assets/upload and /assets/delete.

Direct templates: test_api_files_cookfile_assets.py (upload staging, delete,
serving contract) and test_api_files_cookfile_comments.py (whole-list write).
"""

import io
import os
import tempfile
import zipfile

import pytest
from PIL import Image
from common.web_contracts.content import RecipeAsset

from tests.web.archive_builders import write_recipe

UPLOAD_URL = "/api/files/recipes/assets/upload"
ASSETS_URL = "/api/files/recipes/assets"
DELETE_URL = "/api/files/recipes/assets/delete"


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


def _read_member(recipe_dir, name, member):
    from file_mgmt.common import read_json_file_data

    data, status = read_json_file_data(recipe_dir + name, member, unpackassets=False)
    assert status == "OK"
    return data


def _upload(client, name, asset_name="shot.png", payload=None, mimetype="image/png"):
    return client.post(
        UPLOAD_URL,
        data={"file": name, "assets": (io.BytesIO(payload if payload is not None else _png()), asset_name)},
        content_type="multipart/form-data",
    )


# --- upload ------------------------------------------------------------------


def test_asset_upload_runs_the_real_pillow_pipeline(client, folders, monkeypatch):
    """add_asset rotates, thumbnails and resizes with real Pillow
    (file_mgmt/media.py:26-61). Not mocked -- a mocked pipeline would not catch
    a thumbnail that never lands in the archive."""
    import blueprints.api_files.recipes_api as recipes_api

    _history_dir, recipe_dir = folders
    name = write_recipe(recipe_dir, "AssetUp-Recipe")
    real_mkdtemp = tempfile.mkdtemp
    staging_paths = []

    def tracked_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        if kwargs.get("prefix") == "pifire-upload-":
            staging_paths.append(path)
        return path

    monkeypatch.setattr(recipes_api.tempfile, "mkdtemp", tracked_mkdtemp)

    resp = _upload(client, name)
    assert resp.status_code == 200
    stored = resp.get_json()["data"]["assets"]
    assert [RecipeAsset.model_validate(asset, strict=True).model_dump(mode="json") for asset in stored] == stored
    assert len(stored) == 1 and stored[0]["type"] == "png"

    with zipfile.ZipFile(recipe_dir + name) as archive:
        members = set(archive.namelist())
    arc = f"{stored[0]['id']}.png"
    assert stored[0]["filename"] == arc
    assert f"assets/{arc}" in members
    assert f"assets/thumbs/{arc}" in members
    assert _read_member(recipe_dir, name, "assets")[0]["filename"] == arc

    # This request's private staging directory is removed even when other
    # xdist workers are creating their own upload directories concurrently.
    assert staging_paths
    assert all(not os.path.exists(path) for path in staging_paths)
    parent_id = _read_member(recipe_dir, name, "metadata")["id"]
    assert not os.path.exists(f"/tmp/pifire/{parent_id}")


def test_asset_upload_accepts_more_than_one_file(client, folders):
    _history_dir, recipe_dir = folders
    name = write_recipe(recipe_dir, "AssetMulti-Recipe")
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
    assert len(_read_member(recipe_dir, name, "assets")) == 2


def test_asset_upload_rejects_a_disallowed_extension(client, folders):
    _history_dir, recipe_dir = folders
    name = write_recipe(recipe_dir, "AssetBad-Recipe")
    resp = _upload(client, name, asset_name="evil.svg", payload=b"<svg onload=alert(1)>", mimetype="image/svg+xml")
    assert resp.status_code == 400
    assert resp.get_json()["message"] == "disallowed_file"
    assert _read_member(recipe_dir, name, "assets") == []


def test_asset_upload_with_no_asset_part_is_400(client, folders):
    _history_dir, recipe_dir = folders
    name = write_recipe(recipe_dir, "AssetNone-Recipe")
    resp = client.post(UPLOAD_URL, data={"file": name}, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_asset_upload_refuses_traversal_on_the_archive_name(client, folders):
    resp = _upload(client, "../x.pfrecipe")
    assert resp.status_code in (400, 404)


def test_asset_upload_filename_cannot_escape_the_staging_dir(client, folders):
    """Traversal on the asset's own filename, distinct from the archive name."""
    _history_dir, recipe_dir = folders
    name = write_recipe(recipe_dir, "AssetTrav-Recipe")
    parent = os.path.dirname(recipe_dir.rstrip("/"))
    parent_before = set(os.listdir(parent))
    before = set(os.listdir(recipe_dir))

    resp = _upload(client, name, asset_name="../../escape.png")
    assert resp.status_code in (200, 400)
    assert set(os.listdir(parent)) == parent_before
    assert set(os.listdir(recipe_dir)) == before
    assert not os.path.exists("/tmp/escape.png")


def test_uploaded_asset_is_served_from_static_img_tmp(client, folders):
    """The browser-serving invariant: bytes at /static/img/tmp/{id}/{asset}
    equal the fullsize asset inside the zip."""
    _history_dir, recipe_dir = folders
    name = write_recipe(recipe_dir, "AssetServe-Recipe")
    parent_id = _read_member(recipe_dir, name, "metadata")["id"]
    stored = _upload(client, name, asset_name="served.png", payload=_png((200, 20, 20), (24, 24)))
    arc = stored.get_json()["data"]["assets"][0]["filename"]

    #  The symlink is created by a read that unpacks assets; detail does one.
    assert client.get(f"/api/files/recipes/detail?file={name}").status_code == 200

    with zipfile.ZipFile(recipe_dir + name) as archive:
        archived = archive.read(f"assets/{arc}")

    served = client.get(f"/static/img/tmp/{parent_id}/{arc}")
    assert served.status_code == 200
    assert served.data == archived
    assert not os.path.exists(f"/tmp/pifire/{parent_id}")


# --- whole-list writes ("assets") --------------------------------------------


def test_selecting_a_splash_asset_sets_both_image_and_thumbnail(client, folders):
    """Flask writes both together (blueprints/recipes/routes.py:412-413). They
    are one user-facing choice; splitting them leaves a recipe whose card and
    header disagree."""
    _history_dir, recipe_dir = folders
    name = write_recipe(recipe_dir, "Splash-Recipe")
    arc = _upload(client, name, asset_name="splash.png").get_json()["data"]["assets"][0]["filename"]

    resp = client.post(ASSETS_URL, json={"file": name, "section": "splash", "assets": [arc]})
    assert resp.status_code == 200
    assert resp.get_json()["data"]["assets"] == [arc]
    metadata = _read_member(recipe_dir, name, "metadata")
    assert metadata["image"] == arc
    assert metadata["thumbnail"] == arc


def test_clearing_the_splash_clears_both(client, folders):
    """Flask clears both together (blueprints/recipes/routes.py:423-424)."""
    _history_dir, recipe_dir = folders
    name = write_recipe(recipe_dir, "SplashClear-Recipe")
    arc = _upload(client, name, asset_name="splash.png").get_json()["data"]["assets"][0]["filename"]
    assert client.post(ASSETS_URL, json={"file": name, "section": "splash", "assets": [arc]}).status_code == 200

    resp = client.post(ASSETS_URL, json={"file": name, "section": "splash", "assets": []})
    assert resp.status_code == 200
    assert resp.get_json()["data"]["assets"] == []
    metadata = _read_member(recipe_dir, name, "metadata")
    assert metadata["image"] == ""
    assert metadata["thumbnail"] == ""


def test_splash_rejects_more_than_one_asset(client, folders):
    _history_dir, recipe_dir = folders
    name = write_recipe(recipe_dir, "SplashMulti-Recipe")
    resp = client.post(ASSETS_URL, json={"file": name, "section": "splash", "assets": ["a.png", "b.png"]})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "assets"


def test_setting_an_ingredient_asset_list_replaces_it_wholesale(client, folders):
    """A stale client's own add/remove toggle could silently invert; a
    whole-list write states the intent and cannot invert (plan 1 Task 6)."""
    _history_dir, recipe_dir = folders
    name = write_recipe(
        recipe_dir, "IngredAssets-Recipe", ingredients=[{"name": "Salt", "quantity": "1 tsp", "assets": []}]
    )

    resp = client.post(
        ASSETS_URL, json={"file": name, "section": "ingredients", "index": 0, "assets": ["a.png", "b.png"]}
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["assets"] == ["a.png", "b.png"]
    recipe = _read_member(recipe_dir, name, "recipe")
    assert recipe["ingredients"][0]["assets"] == ["a.png", "b.png"]

    cleared = client.post(ASSETS_URL, json={"file": name, "section": "ingredients", "index": 0, "assets": []})
    assert cleared.status_code == 200
    recipe = _read_member(recipe_dir, name, "recipe")
    assert recipe["ingredients"][0]["assets"] == []


def test_setting_an_instruction_asset_list_replaces_it_wholesale(client, folders):
    _history_dir, recipe_dir = folders
    name = write_recipe(
        recipe_dir,
        "InstrAssets-Recipe",
        instructions=[{"text": "Season", "ingredients": [], "assets": [], "step": 0}],
    )

    resp = client.post(ASSETS_URL, json={"file": name, "section": "instructions", "index": 0, "assets": ["c.png"]})
    assert resp.status_code == 200
    assert resp.get_json()["data"]["assets"] == ["c.png"]
    recipe = _read_member(recipe_dir, name, "recipe")
    assert recipe["instructions"][0]["assets"] == ["c.png"]


def test_assets_out_of_range_index_is_400(client, folders):
    _history_dir, recipe_dir = folders
    name = write_recipe(recipe_dir, "AssetsIdx-Recipe")
    resp = client.post(ASSETS_URL, json={"file": name, "section": "ingredients", "index": 3, "assets": []})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "index"


def test_assets_requires_an_int_index_for_ingredients_and_instructions(client, folders):
    _history_dir, recipe_dir = folders
    name = write_recipe(
        recipe_dir, "AssetsIdxType-Recipe", ingredients=[{"name": "Salt", "quantity": "", "assets": []}]
    )
    resp = client.post(ASSETS_URL, json={"file": name, "section": "ingredients", "assets": []})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "index"


@pytest.mark.parametrize("bad", ["a.png", [1], {"a": 1}, None])
def test_assets_requires_a_list_of_strings(client, folders, bad):
    _history_dir, recipe_dir = folders
    name = write_recipe(recipe_dir, "AssetsType-Recipe")
    resp = client.post(ASSETS_URL, json={"file": name, "section": "splash", "assets": bad})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "assets"


def test_assets_rejects_comments_as_a_section(client, folders):
    """Recipe comments are deliberately unbuilt (human ruling): `comments` must
    not be a reachable section on this whole-list-write endpoint."""
    _history_dir, recipe_dir = folders
    name = write_recipe(recipe_dir, "AssetsComments-Recipe")
    resp = client.post(ASSETS_URL, json={"file": name, "section": "comments", "assets": []})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "section"


def test_assets_refuses_traversal(client, folders):
    resp = client.post(ASSETS_URL, json={"file": "../x.pfrecipe", "section": "splash", "assets": []})
    assert resp.status_code in (400, 404)


def test_assets_with_no_body_is_400(client, folders):
    _history_dir, recipe_dir = folders
    name = write_recipe(recipe_dir, "AssetsNoBody-Recipe")
    resp = client.post(ASSETS_URL, data={"file": name})
    assert resp.status_code == 400


# --- delete -------------------------------------------------------------


def test_asset_delete_removes_it_from_the_archive_and_assets_json(client, folders):
    _history_dir, recipe_dir = folders
    name = write_recipe(recipe_dir, "AssetDel-Recipe")
    arc = _upload(client, name, asset_name="gone.png").get_json()["data"]["assets"][0]["filename"]

    resp = client.post(DELETE_URL, json={"file": name, "assets": [arc]})
    assert resp.status_code == 200
    assert _read_member(recipe_dir, name, "assets") == []
    with zipfile.ZipFile(recipe_dir + name) as archive:
        assert f"assets/{arc}" not in archive.namelist()
        assert f"assets/thumbs/{arc}" not in archive.namelist()


def test_asset_delete_clears_the_splash_pointing_at_it(client, folders):
    _history_dir, recipe_dir = folders
    name = write_recipe(recipe_dir, "AssetSplashDel-Recipe")
    arc = _upload(client, name, asset_name="t.png").get_json()["data"]["assets"][0]["filename"]
    assert client.post(ASSETS_URL, json={"file": name, "section": "splash", "assets": [arc]}).status_code == 200

    assert client.post(DELETE_URL, json={"file": name, "assets": [arc]}).status_code == 200
    metadata = _read_member(recipe_dir, name, "metadata")
    assert metadata["image"] == ""
    assert metadata["thumbnail"] == ""


def test_asset_delete_removes_it_from_an_ingredient_and_an_instruction(client, folders):
    """Do not write a parallel scrubber (F2): remove_assets(...,
    filetype="recipefile") already scrubs both recipe.json members
    (file_mgmt/common.py:202-277)."""
    _history_dir, recipe_dir = folders
    name = write_recipe(
        recipe_dir,
        "AssetCascade-Recipe",
        ingredients=[{"name": "Salt", "quantity": "", "assets": []}],
        instructions=[{"text": "Season", "ingredients": [], "assets": [], "step": 0}],
    )
    arc = _upload(client, name, asset_name="shared.png").get_json()["data"]["assets"][0]["filename"]
    client.post(ASSETS_URL, json={"file": name, "section": "ingredients", "index": 0, "assets": [arc]})
    client.post(ASSETS_URL, json={"file": name, "section": "instructions", "index": 0, "assets": [arc]})

    assert client.post(DELETE_URL, json={"file": name, "assets": [arc]}).status_code == 200
    recipe = _read_member(recipe_dir, name, "recipe")
    assert recipe["ingredients"][0]["assets"] == []
    assert recipe["instructions"][0]["assets"] == []


@pytest.mark.parametrize("bad", ["a.png", [1], {"a": 1}, None])
def test_asset_delete_requires_a_list_of_strings(client, folders, bad):
    _history_dir, recipe_dir = folders
    name = write_recipe(recipe_dir, "AssetDelType-Recipe")
    resp = client.post(DELETE_URL, json={"file": name, "assets": bad})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "assets"


def test_asset_delete_refuses_traversal(client, folders):
    """remove_assets rewrites the archive in place (file_mgmt/common.py:257-278)
    -- an uncontained path there is a write primitive, not just a read one."""
    resp = client.post(DELETE_URL, json={"file": "../x.pfrecipe", "assets": ["a.png"]})
    assert resp.status_code in (400, 404)
