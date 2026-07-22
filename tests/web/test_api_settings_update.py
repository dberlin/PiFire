"""Task 2 (phase2a settings foundation): `POST /api/settings_update`
(`blueprints/api/routes.py`'s `_api_post_settings_update` action).

Uses the same lightweight `app.test_client()` pattern as
tests/web/test_webapp_sqlite.py / tests/web/test_history_export_route.py (no
Playwright/live server needed for plain JSON POSTs), wrapped in a local
`client` fixture built on the shared `ds` fixture (tests/conftest.py) for an
isolated temp-SQLite datastore per test.

`write_control(..., WriteKind.MERGE, ...)` (used by `save_settings_and_flag_update`,
same as the existing `_api_post_control` action) only *queues* the partial --
see tests/web/conftest.py's `drain_control_writes()` docs and
test_page_api.py's `test_post_control_merges_via_write_control`, which drains
via `execute_control_writes()` before reading back. This module does the same
directly (no live_server here, so no `drain_control_writes()` helper import).
"""

import json

import pytest

from common.common import WriteKind
from common.datastore_accessors import execute_control_writes, read_control, read_settings, write_control


@pytest.fixture
def client(ds):
    from app import app as flask_app

    flask_app.config.update(TESTING=True)
    return flask_app.test_client()


def test_settings_update_persists_delta_and_sets_flag(client):
    body = {"settings": {"pwm": {"update_time": 7}}, "flags": ["settings_update"]}
    resp = client.post("/api/settings_update", data=json.dumps(body), content_type="application/json")
    assert resp.status_code == 200
    assert resp.get_json()["result"] == "success"
    assert read_settings()["pwm"]["update_time"] == 7

    execute_control_writes()
    assert read_control()["settings_update"] is True


def test_settings_update_empty_flags_sets_none(client):
    ctrl = read_control()
    ctrl["settings_update"] = False
    write_control(ctrl, WriteKind.OVERWRITE, origin="test")
    body = {"settings": {"globals": {"grill_name": "Smokey"}}, "flags": []}
    resp = client.post("/api/settings_update", data=json.dumps(body), content_type="application/json")
    assert resp.status_code == 200
    assert read_settings()["globals"]["grill_name"] == "Smokey"

    execute_control_writes()
    assert read_control()["settings_update"] is False
