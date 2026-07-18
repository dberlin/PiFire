"""Playwright coverage for the recipes page (blueprints/recipes/routes.py's
`recipes_page` GET render and `recipes_data`, a single ~350-line route that
creates/edits/saves/deletes on-disk `.pfrecipe` recipe files -- zip archives
of metadata.json/recipe.json/comments.json/assets.json, see
file_mgmt/recipes.py's `create_recipefile`/`read_recipefile`) plus several
read-only AJAX fragment/asset endpoints.

Follows the pattern established in test_page_settings.py/test_page_pellets.py;
see tests/web/conftest.py for the shared harness.

Folder isolation
-----------------
`recipes_data`/`recipes_page` read `current_app.config["RECIPE_FOLDER"]`
(default `"./recipes/"`, the *real* repo checkout's recipes dir). Separately,
`file_mgmt/recipes.py` has its OWN module-level `RECIPE_FOLDER = "./recipes/"`
constant that `create_recipefile()` and `get_recipefilelist_details()` read
directly instead of `current_app.config` -- a real inconsistency (see task
report). `_isolated_recipe_folder` below monkeypatches BOTH to a per-module
temp dir so these tests never touch the repo's actual `./recipes/` folder,
and restores both + removes the temp dir on teardown.

Interaction style
------------------
Every `recipes_data` action reachable from the real UI is itself a jQuery
`.load()`/AJAX call (form-encoded POST for HTML-fragment actions, JSON POST
for `deletefile`/`assetchange`) -- there is no plain HTML form whose own
submit button drives these, unlike settings/pellets. All actions are
therefore covered via direct POST (matching the `dashboard_config`/
`probe_select` exemplars in test_page_settings.py), with persistence
asserted by reading the `.pfrecipe` zip back via
`file_mgmt.recipes.read_recipefile()`.

NOT covered (see task report for details):
- `recipeassetmanager`'s `section == "comments"` branch: recipes have no
  "add comment" action anywhere in this route (only cookfiles do, via
  cookfile_update), so `recipe_data["comments"]` can never be non-empty
  through this blueprint -- exercising that branch would just prove an
  IndexError on the always-empty list, not real behavior.
- The upload-then-process round trip for `uploadassets` IS covered with a
  real in-memory PNG (Pillow), so the actual PIL rotate/thumbnail/resize
  pipeline in file_mgmt/media.py's `add_asset` runs for real, not mocked.
  It requires `/tmp/pifire` to already exist -- see the latent bug noted at
  `test_uploadassets_via_direct_post`.

Latent bug: `create_recipefile()` has no same-title dedup
-----------------------------------------------------------
Unlike its cookfile counterpart (`file_mgmt/cookfile.py`'s
`create_cookfile()`, which loops appending `-1`/`-2`/... on a title
collision), `file_mgmt/recipes.py`'s `create_recipefile()` derives its
title from `datetime.now().strftime("%Y-%m-%d--%H%M")` (minute resolution)
with NO collision check: `zipfile.ZipFile(filename, "w", ...)` silently
truncates and overwrites any `.pfrecipe` that already has that exact title,
losing its content, if two new recipes are created within the same
clock-minute. `_create_recipe()` below works around this in the tests
(picks the most-recently-modified `.pfrecipe`, not "the new one") rather
than assuming distinct filenames across calls.
"""

import io
import os
import shutil
import tempfile
import time
from urllib.parse import urlencode

import pytest
from PIL import Image

from tests.web.conftest import apply_control, requires_chromium

pytestmark = requires_chromium


@pytest.fixture(scope="module", autouse=True)
def _isolated_recipe_folder(live_server):
    from app import app as flask_app
    import file_mgmt.recipes as recipes_mod

    tmp_dir = tempfile.mkdtemp(prefix="pifire_test_recipes_")
    recipe_dir = os.path.join(tmp_dir, "recipes") + "/"
    os.makedirs(recipe_dir, exist_ok=True)

    orig_app_folder = flask_app.config["RECIPE_FOLDER"]
    orig_mod_folder = recipes_mod.RECIPE_FOLDER
    flask_app.config["RECIPE_FOLDER"] = recipe_dir
    recipes_mod.RECIPE_FOLDER = recipe_dir

    yield recipe_dir

    flask_app.config["RECIPE_FOLDER"] = orig_app_folder
    recipes_mod.RECIPE_FOLDER = orig_mod_folder
    shutil.rmtree(tmp_dir, ignore_errors=True)


def _form_post(page, url, fields):
    """POST form-urlencoded data using `urlencode(..., doseq=True)` rather
    than Playwright's `form=` param -- Playwright's `form` serializes a
    list value with `str(the_list)` (a single "['a', 'b']"-shaped field),
    not repeated `key[]=a&key[]=b` entries, so it can't reproduce a real
    browser's multi-value form submit (used by ingredients[]/food[]
    fields). `urlencode(..., doseq=True)` does that correctly."""
    body = urlencode(fields, doseq=True)
    return page.request.post(url, data=body, headers={"content-type": "application/x-www-form-urlencoded"})


def _read_recipe(recipe_dir, filename):
    """Read a `.pfrecipe`'s full contents back off disk -- the direct,
    no-HTTP way to assert what an action just persisted (mirrors
    read_settings_from_server() in conftest.py, applied to the recipe file
    format instead of the settings datastore)."""
    from file_mgmt.recipes import read_recipefile

    data, status = read_recipefile(recipe_dir + filename)
    assert status == "OK"
    return data


def _create_recipe(page, live_server, recipe_dir):
    """POST recipeedit with filename="" -- the route's own "new recipe"
    action -- and return the bare filename of the just-created `.pfrecipe`.

    NOTE: can't diff directory listings before/after to find "the new
    file" -- create_recipefile()'s title has only minute resolution and,
    unlike create_cookfile(), has no same-title collision handling (see
    module docstring), so two calls within the same clock-minute silently
    overwrite the same file rather than producing two distinct ones. Pick
    the most-recently-modified `.pfrecipe` instead, which is correct either
    way (new file or in-place overwrite)."""
    before_call = time.time()
    resp = page.request.post(f"{live_server}/recipes/data", form={"recipeedit": "true", "filename": ""})
    assert resp.status == 200
    candidates = [f for f in os.listdir(recipe_dir) if f.endswith(".pfrecipe")]
    assert candidates
    candidates.sort(key=lambda f: os.stat(recipe_dir + f).st_mtime, reverse=True)
    newest = candidates[0]
    assert os.stat(recipe_dir + newest).st_mtime >= before_call - 1
    return newest


def test_recipes_page_renders_key_sections(live_server, page):
    resp = page.goto(f"{live_server}/recipes/")

    assert resp.status == 200
    assert page.title().startswith("Recipe Management")
    assert page.locator("#recipefilelist").count() == 1
    assert page.locator("#recipe_new_btn").count() == 1
    assert page.locator("#upload_recipe_file").count() == 1


def test_recipeedit_create_new_recipe_via_direct_post(live_server, page, _isolated_recipe_folder):
    recipe_dir = _isolated_recipe_folder
    filename = _create_recipe(page, live_server, recipe_dir)

    assert filename.endswith(".pfrecipe")
    recipe_data = _read_recipe(recipe_dir, filename)
    # create_recipefile()'s defaults: 3 steps (Startup/Hold/Shutdown), no
    # ingredients/instructions/comments/assets yet, food_probes == 2.
    assert len(recipe_data["recipe"]["steps"]) == 3
    assert recipe_data["recipe"]["ingredients"] == []
    assert recipe_data["recipe"]["instructions"] == []
    assert recipe_data["comments"] == []
    assert recipe_data["metadata"]["food_probes"] == 2


def test_recipeedit_existing_recipe_via_direct_post(live_server, page, _isolated_recipe_folder):
    recipe_dir = _isolated_recipe_folder
    filename = _create_recipe(page, live_server, recipe_dir)

    resp = page.request.post(f"{live_server}/recipes/data", form={"recipeedit": "true", "filename": filename})
    assert resp.status == 200
    assert filename in resp.text()


def test_recipefilelist_via_direct_post(live_server, page, _isolated_recipe_folder):
    recipe_dir = _isolated_recipe_folder
    filename = _create_recipe(page, live_server, recipe_dir)
    page.request.post(
        f"{live_server}/recipes/data",
        form={"update": "metadata", "field": "title", "filename": filename, "value": "E2E Listed Recipe"},
    )

    resp = page.request.post(
        f"{live_server}/recipes/data",
        form={"recipefilelist": "true", "page": "1", "reverse": "true", "itemsperpage": "10"},
    )
    assert resp.status == 200
    body = resp.text()
    assert filename in body
    assert "E2E Listed Recipe" in body


def test_recipeview_via_direct_post(live_server, page, _isolated_recipe_folder):
    recipe_dir = _isolated_recipe_folder
    filename = _create_recipe(page, live_server, recipe_dir)
    page.request.post(
        f"{live_server}/recipes/data",
        form={"update": "metadata", "field": "title", "filename": filename, "value": "Viewable Recipe"},
    )

    resp = page.request.post(f"{live_server}/recipes/data", form={"recipeview": "true", "filename": filename})
    assert resp.status == 200
    assert "Viewable Recipe" in resp.text()


def test_update_metadata_fields_via_direct_post(live_server, page, _isolated_recipe_folder):
    recipe_dir = _isolated_recipe_folder
    filename = _create_recipe(page, live_server, recipe_dir)

    resp_title = page.request.post(
        f"{live_server}/recipes/data",
        form={"update": "metadata", "field": "title", "filename": filename, "value": "My Smoked Brisket"},
    )
    assert resp_title.status == 200
    assert "My Smoked Brisket" in resp_title.text()

    resp_prep = page.request.post(
        f"{live_server}/recipes/data",
        form={"update": "metadata", "field": "prep_time", "filename": filename, "value": "45"},
    )
    assert resp_prep.status == 200

    recipe_data = _read_recipe(recipe_dir, filename)
    assert recipe_data["metadata"]["title"] == "My Smoked Brisket"
    assert recipe_data["metadata"]["prep_time"] == 45


def test_update_metadata_food_probes_resizes_step_food_arrays(live_server, page, _isolated_recipe_folder):
    """`update`'s `food_probes` field is special-cased: besides setting
    metadata.food_probes, it pads/truncates every step's
    `trigger_temps.food` list to match the new count."""
    recipe_dir = _isolated_recipe_folder
    filename = _create_recipe(page, live_server, recipe_dir)

    recipe_data = _read_recipe(recipe_dir, filename)
    assert len(recipe_data["recipe"]["steps"][0]["trigger_temps"]["food"]) == 2  # default food_probes == 2

    resp = page.request.post(
        f"{live_server}/recipes/data",
        form={"update": "metadata", "field": "food_probes", "filename": filename, "value": "4"},
    )
    assert resp.status == 200

    recipe_data = _read_recipe(recipe_dir, filename)
    assert recipe_data["metadata"]["food_probes"] == 4
    for step in recipe_data["recipe"]["steps"]:
        assert len(step["trigger_temps"]["food"]) == 4

    resp = page.request.post(
        f"{live_server}/recipes/data",
        form={"update": "metadata", "field": "food_probes", "filename": filename, "value": "1"},
    )
    assert resp.status == 200
    recipe_data = _read_recipe(recipe_dir, filename)
    for step in recipe_data["recipe"]["steps"]:
        assert len(step["trigger_temps"]["food"]) == 1


def test_add_update_delete_ingredients_via_direct_post(live_server, page, _isolated_recipe_folder):
    recipe_dir = _isolated_recipe_folder
    filename = _create_recipe(page, live_server, recipe_dir)

    add_resp = page.request.post(f"{live_server}/recipes/data", form={"add": "ingredients", "filename": filename})
    assert add_resp.status == 200
    recipe_data = _read_recipe(recipe_dir, filename)
    assert len(recipe_data["recipe"]["ingredients"]) == 1

    # Reference the (still-blank-named) ingredient from an instruction first,
    # to prove the "fixup" rename-cascade in the `update`/`ingredients`
    # branch: renaming ingredient 0 to "Brown Sugar" also rewrites any
    # instruction's `ingredients` list that referenced its old name.
    page.request.post(f"{live_server}/recipes/data", form={"add": "instructions", "filename": filename})
    _form_post(
        page,
        f"{live_server}/recipes/data",
        {
            "update": "instructions",
            "index": "0",
            "filename": filename,
            "text": "Rub it on",
            "step": "0",
            "ingredients[]": [""],  # blank ingredient name (matches ingredient 0's initial name)
        },
    )

    update_resp = page.request.post(
        f"{live_server}/recipes/data",
        form={"update": "ingredients", "index": "0", "filename": filename, "name": "Brown Sugar", "quantity": "1 cup"},
    )
    assert update_resp.status == 200
    recipe_data = _read_recipe(recipe_dir, filename)
    assert recipe_data["recipe"]["ingredients"][0]["name"] == "Brown Sugar"
    assert recipe_data["recipe"]["ingredients"][0]["quantity"] == "1 cup"
    assert "Brown Sugar" in recipe_data["recipe"]["instructions"][0]["ingredients"]

    del_resp = page.request.post(
        f"{live_server}/recipes/data", form={"delete": "ingredients", "index": "0", "filename": filename}
    )
    assert del_resp.status == 200
    recipe_data = _read_recipe(recipe_dir, filename)
    assert recipe_data["recipe"]["ingredients"] == []


def test_add_update_delete_instructions_via_direct_post(live_server, page, _isolated_recipe_folder):
    recipe_dir = _isolated_recipe_folder
    filename = _create_recipe(page, live_server, recipe_dir)

    add_resp = page.request.post(f"{live_server}/recipes/data", form={"add": "instructions", "filename": filename})
    assert add_resp.status == 200
    recipe_data = _read_recipe(recipe_dir, filename)
    assert len(recipe_data["recipe"]["instructions"]) == 1

    update_resp = page.request.post(
        f"{live_server}/recipes/data",
        form={
            "update": "instructions",
            "index": "0",
            "filename": filename,
            "text": "Smoke at 225F for 6 hours",
            "step": "1",
            "ingredients[]": [],
        },
    )
    assert update_resp.status == 200
    recipe_data = _read_recipe(recipe_dir, filename)
    assert recipe_data["recipe"]["instructions"][0]["text"] == "Smoke at 225F for 6 hours"
    assert recipe_data["recipe"]["instructions"][0]["step"] == 1

    del_resp = page.request.post(
        f"{live_server}/recipes/data", form={"delete": "instructions", "index": "0", "filename": filename}
    )
    assert del_resp.status == 200
    recipe_data = _read_recipe(recipe_dir, filename)
    assert recipe_data["recipe"]["instructions"] == []


def test_add_update_delete_steps_via_direct_post(live_server, page, _isolated_recipe_folder):
    recipe_dir = _isolated_recipe_folder
    filename = _create_recipe(page, live_server, recipe_dir)

    add_resp = page.request.post(
        f"{live_server}/recipes/data", form={"add": "steps", "index": "0", "filename": filename}
    )
    assert add_resp.status == 200
    recipe_data = _read_recipe(recipe_dir, filename)
    assert len(recipe_data["recipe"]["steps"]) == 4  # 3 defaults + 1 new, inserted at index 0

    update_resp = _form_post(
        page,
        f"{live_server}/recipes/data",
        {
            "update": "steps",
            "index": "0",
            "filename": filename,
            "hold_temp": "225",
            "timer": "3600",
            "mode": "Hold",
            "primary": "225",
            "food[]": ["165", "0"],
            "pause": "true",
            "notify": "true",
            "message": "Wrap it now",
        },
    )
    assert update_resp.status == 200
    recipe_data = _read_recipe(recipe_dir, filename)
    step0 = recipe_data["recipe"]["steps"][0]
    assert step0["hold_temp"] == 225
    assert step0["timer"] == 3600
    assert step0["mode"] == "Hold"
    assert step0["trigger_temps"]["primary"] == 225
    assert step0["trigger_temps"]["food"] == [165, 0]
    assert step0["pause"] is True
    assert step0["notify"] is True
    assert step0["message"] == "Wrap it now"

    del_resp = page.request.post(
        f"{live_server}/recipes/data", form={"delete": "steps", "index": "0", "filename": filename}
    )
    assert del_resp.status == 200
    recipe_data = _read_recipe(recipe_dir, filename)
    assert len(recipe_data["recipe"]["steps"]) == 3


def test_refresh_fragments_via_direct_post(live_server, page, _isolated_recipe_folder):
    """`refresh` re-renders a single section's fragment (used after an
    asset-viewer modal closes, per recipes.js) -- one representative test
    covers all 5 sub-actions since they share the identical
    read-then-render_template_string shape."""
    recipe_dir = _isolated_recipe_folder
    filename = _create_recipe(page, live_server, recipe_dir)

    for section in ("metadata", "description", "ingredients", "instructions", "steps"):
        resp = page.request.post(f"{live_server}/recipes/data", form={"refresh": section, "filename": filename})
        assert resp.status == 200, f"refresh={section} failed"


def test_reciperunstatus_via_direct_post(live_server, page, _isolated_recipe_folder):
    """`_recipe_status.html` renders the recipe's title, not its filename
    -- give the fixture recipe a distinctive title first so both branches
    have something unambiguous to assert against."""
    recipe_dir = _isolated_recipe_folder
    filename = _create_recipe(page, live_server, recipe_dir)
    page.request.post(
        f"{live_server}/recipes/data",
        form={"update": "metadata", "field": "title", "filename": filename, "value": "Running Recipe Title"},
    )

    # Branch 1: control.mode != "Recipe" -- uses the filename from the request.
    apply_control(lambda c: c.__setitem__("mode", "Stop"))
    resp = page.request.post(f"{live_server}/recipes/data", form={"reciperunstatus": "true", "filename": filename})
    assert resp.status == 200
    assert "Running Recipe Title" in resp.text()

    # Branch 2: control.mode == "Recipe" -- ignores the request's filename,
    # uses control["recipe"]["filename"] instead (the recipe currently running).
    def _set_running(c):
        c["mode"] = "Recipe"
        c["recipe"]["filename"] = recipe_dir + filename

    apply_control(_set_running)
    resp2 = page.request.post(
        f"{live_server}/recipes/data", form={"reciperunstatus": "true", "filename": "ignored.pfrecipe"}
    )
    assert resp2.status == 200
    assert "Running Recipe Title" in resp2.text()
    apply_control(lambda c: c.__setitem__("mode", "Stop"))


def test_recipeassetmanager_via_direct_post(live_server, page, _isolated_recipe_folder):
    recipe_dir = _isolated_recipe_folder
    filename = _create_recipe(page, live_server, recipe_dir)
    page.request.post(f"{live_server}/recipes/data", form={"add": "ingredients", "filename": filename})

    resp_splash = page.request.post(
        f"{live_server}/recipes/data",
        form={"recipeassetmanager": "true", "section": "splash", "index": "0", "filename": filename},
    )
    assert resp_splash.status == 200

    resp_ingredients = page.request.post(
        f"{live_server}/recipes/data",
        form={"recipeassetmanager": "true", "section": "ingredients", "index": "0", "filename": filename},
    )
    assert resp_ingredients.status == 200


def test_recipeshowasset_via_direct_post(live_server, page, _isolated_recipe_folder):
    recipe_dir = _isolated_recipe_folder
    filename = _create_recipe(page, live_server, recipe_dir)
    page.request.post(
        f"{live_server}/recipes/data",
        form={"update": "metadata", "field": "title", "filename": filename, "value": "Asset Test Recipe"},
    )
    page.request.post(f"{live_server}/recipes/data", form={"add": "ingredients", "filename": filename})

    resp_meta = page.request.post(
        f"{live_server}/recipes/data",
        form={
            "recipeshowasset": "true",
            "section": "metadata",
            "section_index": "0",
            "asset": "Asset Test Recipe",
            "filename": filename,
        },
    )
    assert resp_meta.status == 200

    resp_ingredients = page.request.post(
        f"{live_server}/recipes/data",
        form={
            "recipeshowasset": "true",
            "section": "ingredients",
            "section_index": "0",
            "asset": "",
            "filename": filename,
        },
    )
    assert resp_ingredients.status == 200


def test_deletefile_via_direct_post(live_server, page, _isolated_recipe_folder):
    recipe_dir = _isolated_recipe_folder
    filename = _create_recipe(page, live_server, recipe_dir)
    assert os.path.exists(recipe_dir + filename)

    resp = page.request.post(f"{live_server}/recipes/data", data={"deletefile": "true", "filename": filename})
    assert resp.status == 200
    assert resp.json()["result"] == "success"
    assert not os.path.exists(recipe_dir + filename)


def test_assetchange_via_direct_post(live_server, page, _isolated_recipe_folder):
    recipe_dir = _isolated_recipe_folder
    filename = _create_recipe(page, live_server, recipe_dir)
    page.request.post(f"{live_server}/recipes/data", form={"add": "ingredients", "filename": filename})

    add_splash = page.request.post(
        f"{live_server}/recipes/data",
        data={
            "assetchange": "true",
            "filename": filename,
            "section": "splash",
            "index": 0,
            "asset_name": "abc123.jpg",
            "asset_id": "abc123",
            "action": "add",
        },
    )
    assert add_splash.status == 200
    assert add_splash.json()["result"] == "success"
    recipe_data = _read_recipe(recipe_dir, filename)
    assert recipe_data["metadata"]["image"] == "abc123.jpg"
    assert recipe_data["metadata"]["thumbnail"] == "abc123.jpg"

    remove_splash = page.request.post(
        f"{live_server}/recipes/data",
        data={
            "assetchange": "true",
            "filename": filename,
            "section": "splash",
            "index": 0,
            "asset_name": "abc123.jpg",
            "asset_id": "abc123",
            "action": "remove",
        },
    )
    assert remove_splash.status == 200
    recipe_data = _read_recipe(recipe_dir, filename)
    assert recipe_data["metadata"]["image"] == ""
    assert recipe_data["metadata"]["thumbnail"] == ""

    add_ingredient_asset = page.request.post(
        f"{live_server}/recipes/data",
        data={
            "assetchange": "true",
            "filename": filename,
            "section": "ingredients",
            "index": 0,
            "asset_name": "ingredient-pic.png",
            "asset_id": "ingredient-pic",
            "action": "add",
        },
    )
    assert add_ingredient_asset.status == 200
    recipe_data = _read_recipe(recipe_dir, filename)
    assert "ingredient-pic.png" in recipe_data["recipe"]["ingredients"][0]["assets"]

    # `section == "delete"`: calls remove_assets() but doesn't check its
    # return status, always reporting "success" regardless.
    delete_action = page.request.post(
        f"{live_server}/recipes/data",
        data={
            "assetchange": "true",
            "filename": filename,
            "section": "delete",
            "index": 0,
            "asset_name": "nonexistent.png",
            "asset_id": "nonexistent",
            "action": "add",
        },
    )
    assert delete_action.status == 200
    assert delete_action.json()["result"] == "success"


def test_upload_recipe_file_via_direct_post(live_server, page, _isolated_recipe_folder):
    recipe_dir = _isolated_recipe_folder

    resp = page.request.post(
        f"{live_server}/recipes/data",
        multipart={
            "upload": "true",
            "recipefile": {
                "name": "uploaded-test.pfrecipe",
                "mimeType": "application/octet-stream",
                "buffer": b"not a real zip, just bytes",
            },
        },
    )
    assert resp.status == 200
    assert resp.json()["result"] == "success"
    assert os.path.exists(recipe_dir + "uploaded-test.pfrecipe")


def test_download_recipe_file_via_get(live_server, page, _isolated_recipe_folder):
    recipe_dir = _isolated_recipe_folder
    filename = _create_recipe(page, live_server, recipe_dir)

    resp = page.request.get(f"{live_server}/recipes/data/download/{filename}")
    assert resp.status == 200
    assert filename in (resp.headers.get("content-disposition") or "")


def test_uploadassets_via_direct_post(live_server, page, _isolated_recipe_folder):
    """`uploadassets` runs the real media pipeline (file_mgmt/media.py's
    `add_asset`: rotate/thumbnail/resize via Pillow, then appends both the
    fullsize and thumbnail into the recipe's zip) -- exercised here with a
    real in-memory PNG rather than mocked, to prove the pipeline itself
    works end-to-end, not just that the route wires it up.

    Latent bug worked around: `uploadassets`' handler does
    `tmp_path = f"/tmp/pifire/{parent_id}"; if not os.path.exists(tmp_path):
    os.mkdir(tmp_path)` with NO `os.makedirs(..., exist_ok=True)`/parent
    creation -- if the shared `/tmp/pifire` parent doesn't exist yet (only
    ever created, lazily, inside `read_json_file_data`'s asset-unpacking
    loop when a file already HAS >=1 asset), this raises a bare
    `FileNotFoundError` -> 500. A freshly created recipe has zero assets,
    so that lazy creation never fires -- this is a real gap for a device's
    very first asset upload ever. Pre-creating `/tmp/pifire` here mirrors
    what a long-running real device would already have from any earlier
    asset view/upload, so the test can still exercise the real pipeline."""
    os.makedirs("/tmp/pifire", exist_ok=True)
    recipe_dir = _isolated_recipe_folder
    filename = _create_recipe(page, live_server, recipe_dir)

    png_buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (255, 0, 0)).save(png_buffer, format="PNG")

    resp = page.request.post(
        f"{live_server}/recipes/data",
        multipart={
            "uploadassets": "true",
            "filename": filename,
            "assetfiles": {"name": "test-asset.png", "mimeType": "image/png", "buffer": png_buffer.getvalue()},
        },
    )
    assert resp.status == 200
    body = resp.json()
    assert body["result"] == "success"
    assert body["errors"] == []

    recipe_data = _read_recipe(recipe_dir, filename)
    assert len(recipe_data["assets"]) == 1
    assert recipe_data["assets"][0]["type"] == "png"


@pytest.fixture
def _tmp_pifire_absent(tmp_path):
    """Ensure the real, shared `/tmp/pifire` asset-scratch dir is ABSENT for
    the duration of the test, so `test_uploadassets_creates_missing_tmp_pifire_parent_dir`
    below can exercise a genuinely fresh environment. `/tmp/pifire` is a real
    absolute path that may already exist on this machine and be in use by
    other processes/tests (see `test_uploadassets_via_direct_post` above,
    which pre-creates it) -- so if it exists, rename it out of the way to a
    unique backup path (derived from this test's own `tmp_path`, never
    time/random, to keep the id deterministic-per-test) and restore it in
    `finally`; whatever the test itself creates at `/tmp/pifire` is removed
    afterward either way, leaving the directory in its original state."""
    real_path = "/tmp/pifire"
    backup_path = f"/tmp/pifire.bak.{tmp_path.name}"
    backed_up = os.path.exists(real_path)
    if backed_up:
        os.rename(real_path, backup_path)
    try:
        yield
    finally:
        if os.path.exists(real_path):
            shutil.rmtree(real_path, ignore_errors=True)
        if backed_up:
            os.rename(backup_path, real_path)


def test_uploadassets_creates_missing_tmp_pifire_parent_dir(
    live_server, page, _isolated_recipe_folder, _tmp_pifire_absent
):
    """Regression test for the latent bug documented in
    `test_uploadassets_via_direct_post` above: with the `/tmp/pifire`
    PARENT dir genuinely absent (a fresh environment's very first asset
    upload ever), the handler used to do
    `tmp_path = f"/tmp/pifire/{parent_id}"; if not os.path.exists(tmp_path):
    os.mkdir(tmp_path)` -- `os.mkdir` only creates the leaf dir, so it raised
    a bare `FileNotFoundError` (-> 500) since `/tmp/pifire` itself didn't
    exist. Fixed to `os.makedirs(tmp_path, exist_ok=True)`, which creates
    both levels and doesn't error if they already exist."""
    assert not os.path.exists("/tmp/pifire")
    recipe_dir = _isolated_recipe_folder
    filename = _create_recipe(page, live_server, recipe_dir)

    png_buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (255, 0, 0)).save(png_buffer, format="PNG")

    resp = page.request.post(
        f"{live_server}/recipes/data",
        multipart={
            "uploadassets": "true",
            "filename": filename,
            "assetfiles": {"name": "test-asset.png", "mimeType": "image/png", "buffer": png_buffer.getvalue()},
        },
    )
    assert resp.status == 200
    body = resp.json()
    assert body["result"] == "success"
    assert body["errors"] == []

    recipe_data = _read_recipe(recipe_dir, filename)
    assert len(recipe_data["assets"]) == 1
