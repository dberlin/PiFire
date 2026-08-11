"""File-level recipe operations: create, download, upload, delete.

Mirrors tests/web/test_api_files_cookfile_write.py's E5/E7/E8 coverage for the
recipe surface. SAFETY: every test here writes only into the temp folder
`api_files_folders` creates.
"""

import io
import os
import zipfile
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


# --------------------------------------------------------------------------
# metadata
# --------------------------------------------------------------------------


def _step(food_probes=2, mode="Smoke"):
    return {
        "mode": mode,
        "trigger_temps": {"primary": 0, "food": [0] * food_probes},
        "hold_temp": 0,
        "timer": 0,
        "notify": False,
        "message": "",
        "pause": False,
    }


def _read_member_bytes(path, member):
    with zipfile.ZipFile(path) as archive:
        return archive.read(member)


def test_update_metadata_accepts_strict_integer_and_string_fields(client, folders):
    _history, recipe_dir = folders
    name = write_recipe(recipe_dir, "Brisket")
    resp = client.post(
        "/api/files/recipes/metadata",
        json={"file": name, "fields": {"title": "My Smoked Brisket", "prep_time": 45, "rating": 4}},
    )
    assert resp.status_code == 200
    assert resp.get_json()["result"] == "OK"

    detail = client.get(f"/api/files/recipes/detail?file={name}").get_json()
    assert detail["metadata"]["title"] == "My Smoked Brisket"
    assert detail["metadata"]["prep_time"] == 45
    assert detail["metadata"]["rating"] == 4


def test_update_metadata_rejects_fractional_integer_fields_without_writing(client, folders):
    _history, recipe_dir = folders
    name = write_recipe(recipe_dir, "Strict-Metadata")
    before = _read_member_bytes(recipe_dir + name, "metadata.json")

    resp = client.post(
        "/api/files/recipes/metadata",
        json={"file": name, "fields": {"rating": 1.5}},
    )

    assert resp.status_code == 400
    assert resp.get_json() == {
        "result": "Error",
        "message": "bad_request",
        "data": {"field": "rating"},
    }
    assert _read_member_bytes(recipe_dir + name, "metadata.json") == before


def test_raising_food_probes_pads_every_step(client, folders):
    """food_probes is structural: trigger_temps.food must carry exactly one
    entry per food probe on EVERY step, or the controller's probe_map remap
    (controller.py:156-163) indexes past the end."""
    name = write_recipe(folders[1], "Brisket", food_probes=2, steps=[_step(), _step()])
    resp = client.post("/api/files/recipes/metadata", json={"file": name, "fields": {"food_probes": 4}})
    assert resp.status_code == 200
    detail = client.get(f"/api/files/recipes/detail?file={name}").get_json()
    assert [len(s["trigger_temps"]["food"]) for s in detail["recipe"]["steps"]] == [4, 4]
    assert detail["recipe"]["steps"][0]["trigger_temps"]["food"][2] == 0
    assert detail["metadata"]["food_probes"] == 4


def test_lowering_food_probes_truncates_every_step(client, folders):
    name = write_recipe(folders[1], "Brisket", food_probes=2, steps=[_step(), _step()])
    resp = client.post("/api/files/recipes/metadata", json={"file": name, "fields": {"food_probes": 1}})
    assert resp.status_code == 200
    detail = client.get(f"/api/files/recipes/detail?file={name}").get_json()
    assert [len(s["trigger_temps"]["food"]) for s in detail["recipe"]["steps"]] == [1, 1]


def test_metadata_without_food_probes_never_opens_recipe_json(client, folders, monkeypatch):
    """The food_probes branch is the only one that touches recipe.json; if
    the field is absent from the patch, recipe.json must not even be read."""
    from blueprints.api_files import recipes_api

    name = write_recipe(folders[1], "Brisket")

    real_read = recipes_api.read_json_file_data

    def _guard(path, jsonfile, *a, **k):
        if jsonfile == "recipe":
            raise AssertionError("recipe.json was opened despite food_probes being absent")
        return real_read(path, jsonfile, *a, **k)

    monkeypatch.setattr(recipes_api, "read_json_file_data", _guard)
    resp = client.post("/api/files/recipes/metadata", json={"file": name, "fields": {"title": "New Title"}})
    assert resp.status_code == 200


def test_metadata_rejects_unknown_field(client, folders):
    name = write_recipe(folders[1], "Brisket")
    resp = client.post("/api/files/recipes/metadata", json={"file": name, "fields": {"bogus": "x"}})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "bogus"


def test_metadata_refuses_traversal(client, folders):
    resp = client.post("/api/files/recipes/metadata", json={"file": "../../../etc/passwd", "fields": {"title": "x"}})
    assert resp.status_code == 404


def test_metadata_of_an_unknown_file_is_404(client, folders):
    resp = client.post("/api/files/recipes/metadata", json={"file": "Nope.pfrecipe", "fields": {"title": "x"}})
    assert resp.status_code == 404


def test_metadata_with_no_body_is_400(client, folders):
    assert client.post("/api/files/recipes/metadata").status_code == 400


def test_metadata_write_leaves_comments_json_byte_identical(client, folders):
    """A write endpoint reads and rewrites only the member it changes; it
    must never touch comments.json, which this feature does not model."""
    _history, recipe_dir = folders
    name = write_recipe(recipe_dir, "Brisket")
    path = recipe_dir + name
    before = _read_member_bytes(path, "comments.json")

    resp = client.post("/api/files/recipes/metadata", json={"file": name, "fields": {"title": "New Title"}})
    assert resp.status_code == 200

    after = _read_member_bytes(path, "comments.json")
    assert after == before


@pytest.mark.parametrize(
    ("endpoint", "mutation"),
    [
        ("ingredients", {"action": "add"}),
        (
            "ingredients",
            {"action": "update", "index": 0, "name": "Pepper", "quantity": "2 tsp"},
        ),
        ("ingredients", {"action": "delete", "index": 0}),
        ("instructions", {"action": "add"}),
        (
            "instructions",
            {"action": "update", "index": 0, "text": "Season", "ingredients": ["Salt"], "step": 0},
        ),
        ("instructions", {"action": "delete", "index": 0}),
    ],
)
def test_recipe_mutations_reject_unknown_members_without_writing(client, folders, endpoint, mutation):
    _history, recipe_dir = folders
    name = write_recipe(
        recipe_dir,
        f"Strict-{endpoint}-{mutation['action']}",
        ingredients=[{"name": "Salt", "quantity": "1 tsp", "assets": []}],
        instructions=[{"text": "Salt", "ingredients": ["Salt"], "assets": [], "step": 0}],
    )
    before = _read_member_bytes(recipe_dir + name, "recipe.json")

    resp = client.post(
        f"/api/files/recipes/{endpoint}",
        json={"file": name, **mutation, "future": True},
    )

    assert resp.status_code == 400
    assert resp.get_json() == {
        "result": "Error",
        "message": "bad_request",
        "data": {"field": "future"},
    }
    assert _read_member_bytes(recipe_dir + name, "recipe.json") == before


# --------------------------------------------------------------------------
# ingredients
# --------------------------------------------------------------------------


def test_add_ingredient_appends_a_blank_entry(client, folders):
    name = write_recipe(folders[1], "Brisket")
    resp = client.post("/api/files/recipes/ingredients", json={"file": name, "action": "add"})
    assert resp.status_code == 200
    detail = client.get(f"/api/files/recipes/detail?file={name}").get_json()
    assert detail["recipe"]["ingredients"] == [{"name": "", "quantity": "", "assets": []}]


def test_renaming_an_ingredient_rewrites_every_instruction_that_used_it(client, folders):
    name = write_recipe(
        folders[1],
        "Brisket",
        ingredients=[{"name": "Sugar", "quantity": "1c", "assets": []}],
        instructions=[
            {"text": "Rub", "ingredients": ["Sugar"], "assets": [], "step": 0},
            {"text": "Rest", "ingredients": [], "assets": [], "step": 1},
        ],
    )
    resp = client.post(
        "/api/files/recipes/ingredients",
        json={"file": name, "action": "update", "index": 0, "name": "Brown Sugar", "quantity": "1c"},
    )
    assert resp.status_code == 200
    detail = client.get(f"/api/files/recipes/detail?file={name}").get_json()
    assert detail["recipe"]["ingredients"][0]["name"] == "Brown Sugar"
    assert detail["recipe"]["instructions"][0]["ingredients"] == ["Brown Sugar"]
    assert detail["recipe"]["instructions"][1]["ingredients"] == []


def test_deleting_an_ingredient_removes_it_from_every_instruction(client, folders):
    name = write_recipe(
        folders[1],
        "Brisket",
        ingredients=[{"name": "Sugar", "quantity": "1c", "assets": []}],
        instructions=[
            {"text": "Rub", "ingredients": ["Sugar"], "assets": [], "step": 0},
        ],
    )
    resp = client.post("/api/files/recipes/ingredients", json={"file": name, "action": "delete", "index": 0})
    assert resp.status_code == 200
    detail = client.get(f"/api/files/recipes/detail?file={name}").get_json()
    assert detail["recipe"]["ingredients"] == []
    assert detail["recipe"]["instructions"][0]["ingredients"] == []


def test_ingredients_update_out_of_range_index_is_400(client, folders):
    name = write_recipe(folders[1], "Brisket", ingredients=[{"name": "Sugar", "quantity": "1c", "assets": []}])
    resp = client.post(
        "/api/files/recipes/ingredients",
        json={"file": name, "action": "update", "index": 5, "name": "X", "quantity": "1c"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "index"


def test_ingredients_delete_out_of_range_index_is_400(client, folders):
    name = write_recipe(folders[1], "Brisket", ingredients=[{"name": "Sugar", "quantity": "1c", "assets": []}])
    resp = client.post("/api/files/recipes/ingredients", json={"file": name, "action": "delete", "index": 5})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "index"


def test_ingredients_unknown_action_is_400(client, folders):
    name = write_recipe(folders[1], "Brisket")
    resp = client.post("/api/files/recipes/ingredients", json={"file": name, "action": "bogus"})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "action"


def test_ingredients_refuses_traversal(client, folders):
    resp = client.post("/api/files/recipes/ingredients", json={"file": "../../../etc/passwd", "action": "add"})
    assert resp.status_code == 404


def test_ingredients_with_no_body_is_400(client, folders):
    assert client.post("/api/files/recipes/ingredients").status_code == 400


# --------------------------------------------------------------------------
# instructions
# --------------------------------------------------------------------------


def test_add_instruction_appends_a_blank_entry(client, folders):
    name = write_recipe(folders[1], "Brisket")
    resp = client.post("/api/files/recipes/instructions", json={"file": name, "action": "add"})
    assert resp.status_code == 200
    detail = client.get(f"/api/files/recipes/detail?file={name}").get_json()
    assert detail["recipe"]["instructions"] == [{"text": "", "ingredients": [], "assets": [], "step": 0}]


def test_update_instruction_replaces_text_ingredients_and_step(client, folders):
    name = write_recipe(
        folders[1],
        "Brisket",
        ingredients=[{"name": "Sugar", "quantity": "1c", "assets": []}],
        instructions=[{"text": "Rub", "ingredients": [], "assets": [], "step": 0}],
    )
    resp = client.post(
        "/api/files/recipes/instructions",
        json={
            "file": name,
            "action": "update",
            "index": 0,
            "text": "Rub with sugar",
            "ingredients": ["Sugar"],
            "step": 1,
        },
    )
    assert resp.status_code == 200
    detail = client.get(f"/api/files/recipes/detail?file={name}").get_json()
    instruction = detail["recipe"]["instructions"][0]
    assert instruction["text"] == "Rub with sugar"
    assert instruction["ingredients"] == ["Sugar"]
    assert instruction["step"] == 1
    assert instruction["assets"] == []


def test_deleting_an_instruction_does_not_touch_ingredients(client, folders):
    """Nothing cascades from an instruction delete -- unlike ingredients,
    no other member references an instruction."""
    name = write_recipe(
        folders[1],
        "Brisket",
        ingredients=[{"name": "Sugar", "quantity": "1c", "assets": []}],
        instructions=[
            {"text": "Rub", "ingredients": ["Sugar"], "assets": [], "step": 0},
            {"text": "Rest", "ingredients": [], "assets": [], "step": 1},
        ],
    )
    resp = client.post("/api/files/recipes/instructions", json={"file": name, "action": "delete", "index": 0})
    assert resp.status_code == 200
    detail = client.get(f"/api/files/recipes/detail?file={name}").get_json()
    assert len(detail["recipe"]["instructions"]) == 1
    assert detail["recipe"]["instructions"][0]["text"] == "Rest"
    assert detail["recipe"]["ingredients"] == [{"name": "Sugar", "quantity": "1c", "assets": []}]


def test_update_instruction_rejects_an_ingredient_name_not_in_the_recipe(client, folders):
    """Flask does not check this; the React multi-select can only offer real
    names, so a request carrying an unknown one is a bug in something."""
    name = write_recipe(
        folders[1],
        "Brisket",
        ingredients=[{"name": "Sugar", "quantity": "1c", "assets": []}],
        instructions=[{"text": "Rub", "ingredients": [], "assets": [], "step": 0}],
    )
    resp = client.post(
        "/api/files/recipes/instructions",
        json={
            "file": name,
            "action": "update",
            "index": 0,
            "text": "Rub",
            "ingredients": ["Paprika"],
            "step": 0,
        },
    )
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "ingredients"
    detail = client.get(f"/api/files/recipes/detail?file={name}").get_json()
    assert detail["recipe"]["instructions"][0]["ingredients"] == []


def test_instructions_update_out_of_range_index_is_400(client, folders):
    name = write_recipe(
        folders[1], "Brisket", instructions=[{"text": "Rub", "ingredients": [], "assets": [], "step": 0}]
    )
    resp = client.post(
        "/api/files/recipes/instructions",
        json={"file": name, "action": "update", "index": 5, "text": "x", "ingredients": [], "step": 0},
    )
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "index"


def test_instructions_delete_out_of_range_index_is_400(client, folders):
    name = write_recipe(
        folders[1], "Brisket", instructions=[{"text": "Rub", "ingredients": [], "assets": [], "step": 0}]
    )
    resp = client.post("/api/files/recipes/instructions", json={"file": name, "action": "delete", "index": 5})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "index"


def test_instructions_unknown_action_is_400(client, folders):
    name = write_recipe(folders[1], "Brisket")
    resp = client.post("/api/files/recipes/instructions", json={"file": name, "action": "bogus"})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "action"


def test_instructions_refuses_traversal(client, folders):
    resp = client.post("/api/files/recipes/instructions", json={"file": "../../../etc/passwd", "action": "add"})
    assert resp.status_code == 404


def test_instructions_with_no_body_is_400(client, folders):
    assert client.post("/api/files/recipes/instructions").status_code == 400


def test_instructions_write_leaves_comments_json_byte_identical(client, folders):
    _history, recipe_dir = folders
    name = write_recipe(recipe_dir, "Brisket")
    path = recipe_dir + name
    before = _read_member_bytes(path, "comments.json")

    resp = client.post("/api/files/recipes/instructions", json={"file": name, "action": "add"})
    assert resp.status_code == 200

    after = _read_member_bytes(path, "comments.json")
    assert after == before


# --------------------------------------------------------------------------
# steps
# --------------------------------------------------------------------------


def _valid_step_payload(primary=0, food=None, **overrides):
    payload = {
        "mode": "Smoke",
        "message": "",
        "hold_temp": 0,
        "timer": 0,
        "notify": False,
        "pause": False,
        "trigger_temps": {"primary": primary, "food": [0, 0] if food is None else food},
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("action", ["insert", "update", "delete"])
def test_step_mutations_reject_outer_typo_members_without_writing(client, folders, action):
    _history, recipe_dir = folders
    name = write_recipe(recipe_dir, f"Strict-Step-{action}", steps=[_step()])
    before = _read_member_bytes(recipe_dir + name, "recipe.json")
    payload = {"file": name, "action": action, "index": 0, "future": True}
    if action == "update":
        payload["step"] = _valid_step_payload(mode="Hold", hold_temp=225)

    resp = client.post("/api/files/recipes/steps", json=payload)

    assert resp.status_code == 400
    assert resp.get_json() == {
        "result": "Error",
        "message": "bad_request",
        "data": {"field": "future"},
    }
    assert _read_member_bytes(recipe_dir + name, "recipe.json") == before


@pytest.mark.parametrize("location", ["step", "trigger_temps"])
def test_update_step_rejects_nested_typo_members_without_writing(client, folders, location):
    _history, recipe_dir = folders
    name = write_recipe(recipe_dir, f"Strict-Nested-Step-{location}", steps=[_step()])
    before = _read_member_bytes(recipe_dir + name, "recipe.json")
    step = _valid_step_payload(mode="Hold", hold_temp=225)
    target = step if location == "step" else step["trigger_temps"]
    target["future"] = True

    resp = client.post(
        "/api/files/recipes/steps",
        json={"file": name, "action": "update", "index": 0, "step": step},
    )

    assert resp.status_code == 400
    assert resp.get_json() == {
        "result": "Error",
        "message": "bad_request",
        "data": {"field": "future"},
    }
    assert _read_member_bytes(recipe_dir + name, "recipe.json") == before


def test_a_step_is_inserted_at_the_index_not_appended(client, folders):
    """Flask inserts (routes.py:281). A recipe is an ordered program;
    appending would put a new step after Shutdown."""
    name = write_recipe(folders[1], "Brisket", steps=[_step(mode="Smoke"), _step(mode="Shutdown")])
    resp = client.post("/api/files/recipes/steps", json={"file": name, "action": "insert", "index": 0})
    assert resp.status_code == 200
    detail = client.get(f"/api/files/recipes/detail?file={name}").get_json()
    assert [s["mode"] for s in detail["recipe"]["steps"]] == ["Smoke", "Smoke", "Shutdown"]
    assert len(detail["recipe"]["steps"]) == 3


def test_an_inserted_step_gets_one_trigger_temp_per_food_probe(client, folders):
    """Built from metadata.food_probes (routes.py:270-271), not from a
    neighbouring step -- a neighbour may itself be stale."""
    name = write_recipe(
        folders[1],
        "Brisket",
        food_probes=3,
        steps=[{**_step(food_probes=3), "trigger_temps": {"primary": 0, "food": [9, 9]}}],
    )
    resp = client.post("/api/files/recipes/steps", json={"file": name, "action": "insert", "index": 0})
    assert resp.status_code == 200
    detail = client.get(f"/api/files/recipes/detail?file={name}").get_json()
    assert detail["recipe"]["steps"][0]["trigger_temps"]["food"] == [0, 0, 0]


def test_insert_at_len_appends_at_the_end(client, folders):
    name = write_recipe(folders[1], "Brisket", steps=[_step(mode="Startup")])
    resp = client.post("/api/files/recipes/steps", json={"file": name, "action": "insert", "index": 1})
    assert resp.status_code == 200
    detail = client.get(f"/api/files/recipes/detail?file={name}").get_json()
    assert [s["mode"] for s in detail["recipe"]["steps"]] == ["Startup", "Smoke"]


def test_insert_out_of_range_index_is_400(client, folders):
    name = write_recipe(folders[1], "Brisket", steps=[_step()])
    resp = client.post("/api/files/recipes/steps", json={"file": name, "action": "insert", "index": 5})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "index"


def test_update_step_replaces_every_field(client, folders):
    name = write_recipe(folders[1], "Brisket", food_probes=2, steps=[_step()])
    new_step = _valid_step_payload(
        mode="Hold", primary=225, food=[160, 165], hold_temp=225, timer=30, notify=True, pause=True, message="Wrap it"
    )
    resp = client.post(
        "/api/files/recipes/steps",
        json={"file": name, "action": "update", "index": 0, "step": new_step},
    )
    assert resp.status_code == 200
    detail = client.get(f"/api/files/recipes/detail?file={name}").get_json()
    step = detail["recipe"]["steps"][0]
    assert step["mode"] == "Hold"
    assert step["hold_temp"] == 225
    assert step["timer"] == 30
    assert step["notify"] is True
    assert step["pause"] is True
    assert step["message"] == "Wrap it"
    assert step["trigger_temps"] == {"primary": 225, "food": [160, 165]}


@pytest.mark.parametrize("mode", ["Smoke", "Hold", "Startup", "Shutdown"])
def test_update_step_accepts_every_whitelisted_mode(client, folders, mode):
    """The editor only offers Smoke/Hold, but Startup and Shutdown are seeded
    by the recipe defaults and carried by every existing recipe, so a write
    must still accept all four."""
    name = write_recipe(folders[1], "Brisket", steps=[_step()])
    resp = client.post(
        "/api/files/recipes/steps",
        json={"file": name, "action": "update", "index": 0, "step": _valid_step_payload(mode=mode)},
    )
    assert resp.status_code == 200


def test_update_step_accepts_zero_as_a_legal_disabled_sentinel(client, folders):
    """0 is the disabled sentinel for hold_temp and both trigger_temps
    members -- legal, not "missing"."""
    name = write_recipe(folders[1], "Brisket", steps=[_step()])
    resp = client.post(
        "/api/files/recipes/steps",
        json={
            "file": name,
            "action": "update",
            "index": 0,
            "step": _valid_step_payload(hold_temp=0, primary=0, food=[0, 0]),
        },
    )
    assert resp.status_code == 200


def test_update_step_rejects_an_unknown_mode(client, folders):
    name = write_recipe(folders[1], "Brisket", steps=[_step()])
    resp = client.post(
        "/api/files/recipes/steps",
        json={"file": name, "action": "update", "index": 0, "step": _valid_step_payload(mode="Bogus")},
    )
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "mode"


@pytest.mark.parametrize("field,value", [("hold_temp", "225"), ("hold_temp", 1.5), ("timer", "30"), ("timer", 1.5)])
def test_update_step_rejects_a_non_int(client, folders, field, value):
    name = write_recipe(folders[1], "Brisket", steps=[_step()])
    resp = client.post(
        "/api/files/recipes/steps",
        json={"file": name, "action": "update", "index": 0, "step": _valid_step_payload(**{field: value})},
    )
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == field


@pytest.mark.parametrize("field", ["notify", "pause"])
def test_update_step_rejects_a_non_bool(client, folders, field):
    name = write_recipe(folders[1], "Brisket", steps=[_step()])
    resp = client.post(
        "/api/files/recipes/steps",
        json={"file": name, "action": "update", "index": 0, "step": _valid_step_payload(**{field: "true"})},
    )
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == field


def test_update_step_rejects_a_food_list_that_does_not_match_food_probes(client, folders):
    """A mismatch here is exactly the corruption Task 9's food_probes reshape
    exists to prevent."""
    name = write_recipe(folders[1], "Brisket", food_probes=2, steps=[_step(food_probes=2)])
    resp = client.post(
        "/api/files/recipes/steps",
        json={"file": name, "action": "update", "index": 0, "step": _valid_step_payload(food=[1, 2, 3])},
    )
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "trigger_temps"


def test_update_step_rejects_a_non_int_trigger_temp(client, folders):
    name = write_recipe(folders[1], "Brisket", steps=[_step()])
    resp = client.post(
        "/api/files/recipes/steps",
        json={"file": name, "action": "update", "index": 0, "step": _valid_step_payload(primary="225")},
    )
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "trigger_temps"


def test_update_step_out_of_range_index_is_400(client, folders):
    name = write_recipe(folders[1], "Brisket", steps=[_step()])
    resp = client.post(
        "/api/files/recipes/steps",
        json={"file": name, "action": "update", "index": 5, "step": _valid_step_payload()},
    )
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "index"


def test_delete_step(client, folders):
    name = write_recipe(folders[1], "Brisket", steps=[_step(mode="Startup"), _step(mode="Shutdown")])
    resp = client.post("/api/files/recipes/steps", json={"file": name, "action": "delete", "index": 0})
    assert resp.status_code == 200
    detail = client.get(f"/api/files/recipes/detail?file={name}").get_json()
    assert [s["mode"] for s in detail["recipe"]["steps"]] == ["Shutdown"]


def test_delete_step_out_of_range_index_is_400(client, folders):
    name = write_recipe(folders[1], "Brisket", steps=[_step()])
    resp = client.post("/api/files/recipes/steps", json={"file": name, "action": "delete", "index": 5})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "index"


def test_steps_unknown_action_is_400(client, folders):
    name = write_recipe(folders[1], "Brisket", steps=[_step()])
    resp = client.post("/api/files/recipes/steps", json={"file": name, "action": "bogus", "index": 0})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "action"


def test_steps_refuses_traversal(client, folders):
    resp = client.post("/api/files/recipes/steps", json={"file": "../../../etc/passwd", "action": "insert", "index": 0})
    assert resp.status_code == 404


def test_steps_with_no_body_is_400(client, folders):
    assert client.post("/api/files/recipes/steps").status_code == 400


def test_steps_write_leaves_comments_json_byte_identical(client, folders):
    _history, recipe_dir = folders
    name = write_recipe(recipe_dir, "Brisket", steps=[_step()])
    path = recipe_dir + name
    before = _read_member_bytes(path, "comments.json")

    resp = client.post("/api/files/recipes/steps", json={"file": name, "action": "insert", "index": 0})
    assert resp.status_code == 200

    after = _read_member_bytes(path, "comments.json")
    assert after == before
