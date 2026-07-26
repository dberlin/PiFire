"""REST coverage for /api/pellets -- the pellet inventory manager's HTTP seam.

The React pellets page reads the pellet database over the socket
(`socket_pellet_data`, blueprints/mobile/socket_io.py:174) and writes through
`POST /api/pellets`, which dispatches to the shared handlers in
common/pellets_actions.py -- the same eight actions the Socket.IO app-data
channel uses (their other pinning net is test_socketio_app_data.py).

`GET /api/pellets` exists so a test can assert store state without trusting
the UI it is testing, and so a client with no socket can cold-start. It is
also the SHAPE PIN for web-react/src/helpers/pellets/pelletTypes.ts, which is
hand-written (the pellet DB has no JSON schema, so `gen:types` cannot cover
it). If you add a field to that TS file, add it to
test_get_pellets_returns_full_database in the same commit.

See tests/web/conftest.py for the shared live_server harness.
"""

from tests.web.conftest import (
    apply_control,
    drain_control_writes,
    read_control_from_server,
    requires_chromium,
)

pytestmark = requires_chromium


def read_pellets_from_server():
    """Read the pellet DB via the datastore singleton live_server shares with
    this process. Same trick as conftest's read_settings_from_server(); see
    test_page_pellets.py for the identical helper on the Flask-page side."""
    from common.datastore_accessors import read_pellets_store

    return read_pellets_store()


def test_get_pellets_returns_full_database(live_server, page):
    resp = page.request.get(f"{live_server}/api/pellets")
    assert resp.status == 200
    body = resp.json()
    assert body["result"] == "OK"
    pellets = body["data"]["pellets"]
    # SHAPE PIN: helpers/pellets/pelletTypes.ts is hand-written against this.
    assert set(pellets) == {"current", "woods", "brands", "archive", "log", "lastupdated"}
    assert set(pellets["current"]) == {"pelletid", "hopper_level", "date_loaded", "est_usage"}
    any_profile = next(iter(pellets["archive"].values()))
    assert set(any_profile) == {"id", "brand", "wood", "rating", "comments"}
    assert isinstance(pellets["brands"], list)
    assert isinstance(pellets["woods"], list)
    assert isinstance(pellets["log"], dict)


def test_post_pellets_edit_brands_round_trip(live_server, page):
    add = page.request.post(
        f"{live_server}/api/pellets",
        data={"action": "edit_brands", "data": {"new_brand": "REST Brand"}},
    )
    assert add.status == 200
    assert add.json()["result"] == "OK"
    assert "REST Brand" in read_pellets_from_server()["brands"]

    rm = page.request.post(
        f"{live_server}/api/pellets",
        data={"action": "edit_brands", "data": {"delete_brand": "REST Brand"}},
    )
    assert rm.status == 200
    assert "REST Brand" not in read_pellets_from_server()["brands"]


def test_post_pellets_unknown_action(live_server, page):
    resp = page.request.post(f"{live_server}/api/pellets", data={"action": "nope", "data": {}})
    assert resp.status == 200
    body = resp.json()
    assert body["result"] == "Error"
    assert body["message"] == "Error: Received request without valid action"


def test_post_pellets_hopper_check_sets_control_flag(live_server, page):
    apply_control(lambda c: c.__setitem__("hopper_check", False))
    resp = page.request.post(f"{live_server}/api/pellets", data={"action": "hopper_check", "data": {}})
    assert resp.json()["result"] == "OK"
    drain_control_writes()
    assert read_control_from_server()["hopper_check"] is True
