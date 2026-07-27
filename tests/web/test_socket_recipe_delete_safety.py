"""blueprints/mobile/socket_io.py's recipe_delete ran os.system(f"rm {path}")
on an unsanitized client string. Its HTTP sibling was hardened and tested; this
copy was not. These tests are that fix's net."""

import json
import os

import flask
import pytest

from app import app as flask_app
from blueprints.mobile import socket_io
from tests.web.archive_builders import write_recipe


@pytest.fixture
def recipe_folder_at(ds, tmp_path):
    """Point socket_io's module-level `recipe_folder` at a fresh temp dir that
    is a subdirectory of `tmp_path`, so a `../` traversal resolves to a
    sentinel a test writes directly into `tmp_path`. `ds` gives `_post_app_data`
    an isolated datastore to read `read_settings_store()` against.
    """
    recipe_dir = os.path.join(str(tmp_path), "recipes") + "/"
    os.makedirs(recipe_dir)
    original = socket_io.recipe_folder
    socket_io.recipe_folder = recipe_dir
    yield recipe_dir
    socket_io.recipe_folder = original


def _delete(socket_io_mod, filename):
    """Drive recipe_delete the way a real Socket.IO client does: through the
    module-level `_post_app_data` dispatcher, inside a request context so the
    handler sees a `request.sid` the way `tests/web/test_socketio_app_data.py`
    already does for other socket handlers."""
    payload = json.dumps({"recipes_action": {"filename": filename}})
    with flask_app.test_request_context():
        flask.request.sid = "sid-recipe-delete-test"
        return socket_io_mod._post_app_data("recipes_action", "recipe_delete", payload)


def test_recipe_delete_refuses_a_traversal(monkeypatch, tmp_path, recipe_folder_at):
    outside = tmp_path / "sentinel.pfrecipe"
    outside.write_text("keep me")
    _delete(socket_io, f"../{outside.name}")
    assert outside.exists()


def test_recipe_delete_does_not_execute_a_shell_payload(monkeypatch, tmp_path, recipe_folder_at):
    marker = tmp_path / "pwned"
    _delete(socket_io, f"x.pfrecipe; touch {marker}")
    assert not marker.exists()


def test_recipe_delete_removes_a_real_recipe(recipe_folder_at):
    name = write_recipe(recipe_folder_at, "Deletable")
    _delete(socket_io, name)
    assert not os.path.isfile(os.path.join(recipe_folder_at, name))
