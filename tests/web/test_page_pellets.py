"""Playwright coverage for the pellet-management page
(blueprints/pellets/routes.py's `pellets_page`, a single route handling 7
POST/GET `action` branches plus the base render).

Follows the pattern established in test_page_settings.py; see
tests/web/conftest.py for the shared harness.

Actions covered here:
- (base GET, no action)   -- full-page render, key sections present.
- `addprofile`            -- real-UI style: fill the "Add Profile" form and
                              submit with the plain `add` button.
- `editprofile` (save)    -- direct-POST style: the edit form's id/name
                              attributes are keyed by the archive's
                              generated profile id (a timestamp-derived
                              alnum string), which isn't known until after
                              `addprofile` creates it -- read it back from
                              the pellets store, then POST directly.
- `editprofile` (delete)  -- direct-POST, same profile id.
- `editbrands` (add/del)  -- real-UI style: plain forms, no JS gating.
- `editwoods` (add/del)   -- direct-POST style (same shape as editbrands,
                              covering that interaction style once is enough).
- `loadprofile`           -- real-UI style: select a profile from the
                              dropdown and submit; asserts control's
                              `hopper_check` flag and the pellet log.
- `hopperlevel`            -- real-UI style: click the "Refresh Status" link
                              (plain GET), asserts `hopper_check` flips.
- `deletelog`             -- direct-POST style.

NOT covered: the `add_load` variant of `addprofile` (loads the just-added
profile immediately) -- functionally identical to `loadprofile` plus
`addprofile`, both of which are covered individually above.
"""

import pytest

from tests.web.conftest import apply_control, drain_control_writes, read_control_from_server, requires_chromium

pytestmark = requires_chromium


@pytest.fixture(autouse=True)
def seed_probe_device_info():
    """The base template's control panel polls /api/current client-side,
    which (like dash_page) calls read_probe_status() and 500s without this
    seeded. See test_page_dashboard.py's identical fixture for the full
    explanation. Harmless to the tests themselves (async background poll)
    but seeded anyway to keep server logs clean."""
    from common.datastore_accessors import write_generic_key

    write_generic_key("probe_device_info", [])


def read_pellets_from_server():
    """See read_settings_from_server()/read_control_from_server() in
    conftest.py for the thread-shared-datastore rationale -- same trick,
    applied to the pellet database."""
    from common.datastore_accessors import read_pellets_store

    return read_pellets_store()


def test_pellets_page_renders_key_sections(live_server, page):
    resp = page.goto(f"{live_server}/pellets/")

    assert resp.status == 200
    assert page.title().startswith("Pellet Management")
    assert "Current Load Out" in page.content()
    assert page.locator("#HopperStatus").count() == 1
    assert page.locator("form[name='editbrands']").count() == 2  # delete-rows form + add-new form
    assert page.locator("form[name='editwoods']").count() == 2
    assert page.locator("form[name='addprofile']").count() == 1
    assert page.locator("form[name='deletelog']").count() == 1


def test_addprofile_via_real_ui(live_server, page):
    page.goto(f"{live_server}/pellets/")
    page.click("a[href='#add_profile']")
    page.wait_for_selector("#add_profile.show, #add_profile.collapse.show", state="attached")

    page.select_option("#brand_name", "Generic")
    page.select_option("#wood_type", "Hickory")
    page.select_option("#rating", "4")
    page.fill("#comments", "E2E test profile")

    with page.expect_navigation():
        page.locator("form[name='addprofile'] button[name='addprofile'][value='add']").click()

    pelletdb = read_pellets_from_server()
    matches = [p for p in pelletdb["archive"].values() if p["comments"] == "E2E test profile"]
    assert len(matches) == 1
    assert matches[0]["brand"] == "Generic"
    assert matches[0]["wood"] == "Hickory"
    assert matches[0]["rating"] == 4


def test_editprofile_save_and_delete_via_direct_post(live_server, page):
    # Precondition: add a profile via direct POST (mirrors the addprofile
    # route's own form field names) so this test owns its profile id
    # independent of test_addprofile_via_real_ui's.
    add_resp = page.request.post(
        f"{live_server}/pellets/addprofile",
        form={
            "addprofile": "add",
            "brand_name": "Custom",
            "wood_type": "Oak",
            "rating": "3",
            "comments": "to-be-edited",
        },
    )
    assert add_resp.status == 200
    pelletdb = read_pellets_from_server()
    profile_id = next(pid for pid, p in pelletdb["archive"].items() if p["comments"] == "to-be-edited")

    edit_resp = page.request.post(
        f"{live_server}/pellets/editprofile",
        form={
            "editprofile": profile_id,
            "brand_name": "Generic",
            "wood_type": "Maple",
            "rating": "5",
            "comments": "edited comments",
        },
    )
    assert edit_resp.status == 200
    pelletdb = read_pellets_from_server()
    assert pelletdb["archive"][profile_id]["brand"] == "Generic"
    assert pelletdb["archive"][profile_id]["wood"] == "Maple"
    assert pelletdb["archive"][profile_id]["rating"] == 5
    assert pelletdb["archive"][profile_id]["comments"] == "edited comments"

    # This profile isn't the currently-loaded one, so delete should succeed.
    assert pelletdb["current"]["pelletid"] != profile_id
    del_resp = page.request.post(
        f"{live_server}/pellets/editprofile",
        form={"delete": profile_id, "brand_name": "Generic", "wood_type": "Maple"},
    )
    assert del_resp.status == 200
    pelletdb = read_pellets_from_server()
    assert profile_id not in pelletdb["archive"]


def test_editbrands_add_and_delete_via_real_ui(live_server, page):
    page.goto(f"{live_server}/pellets/")

    page.fill("#newBrand", "E2E Brand")
    with page.expect_navigation():
        page.locator(".brandSaveButton").click()

    pelletdb = read_pellets_from_server()
    assert "E2E Brand" in pelletdb["brands"]

    # Re-render shows the new brand's own delete button; click it.
    page.wait_for_selector("button[name='delBrand'][value='E2E Brand']")
    with page.expect_navigation():
        page.locator("button[name='delBrand'][value='E2E Brand']").click()

    pelletdb = read_pellets_from_server()
    assert "E2E Brand" not in pelletdb["brands"]


def test_editwoods_add_and_delete_via_direct_post(live_server, page):
    add_resp = page.request.post(f"{live_server}/pellets/editwoods", form={"newWood": "E2E Wood"})
    assert add_resp.status == 200
    assert "E2E Wood" in read_pellets_from_server()["woods"]

    del_resp = page.request.post(f"{live_server}/pellets/editwoods", form={"delWood": "E2E Wood"})
    assert del_resp.status == 200
    assert "E2E Wood" not in read_pellets_from_server()["woods"]


def test_loadprofile_via_real_ui(live_server, page):
    # Seed a known profile to load via direct POST (its own action, already
    # covered above by the edit/delete test -- reusing it here as setup).
    page.request.post(
        f"{live_server}/pellets/addprofile",
        form={
            "addprofile": "add",
            "brand_name": "Generic",
            "wood_type": "Cherry",
            "rating": "5",
            "comments": "load-me",
        },
    )
    pelletdb = read_pellets_from_server()
    profile_id = next(pid for pid, p in pelletdb["archive"].items() if p["comments"] == "load-me")

    apply_control(lambda c: c.__setitem__("hopper_check", False))

    page.goto(f"{live_server}/pellets/")
    page.locator("button[data-target='#LoadNewModal']").click()
    page.wait_for_selector("#load_id")
    page.select_option("#load_id", profile_id)

    with page.expect_navigation():
        page.locator("form[name='load_profile'] button[name='load_profile']").click()

    drain_control_writes()  # loadprofile's hopper_check write is a MERGE; see conftest.
    pelletdb = read_pellets_from_server()
    assert pelletdb["current"]["pelletid"] == profile_id
    assert pelletdb["current"]["est_usage"] == 0
    assert profile_id in pelletdb["log"].values()
    assert read_control_from_server()["hopper_check"] is True


def test_hopperlevel_via_real_ui(live_server, page):
    apply_control(lambda c: c.__setitem__("hopper_check", False))
    assert read_control_from_server()["hopper_check"] is False

    page.goto(f"{live_server}/pellets/")
    with page.expect_navigation():
        page.locator("a[href='/pellets/hopperlevel']").click()

    drain_control_writes()  # hopperlevel's hopper_check write is a MERGE; see conftest.
    assert read_control_from_server()["hopper_check"] is True


def test_deletelog_via_direct_post(live_server, page):
    # Seed a log entry via loadprofile's own action so we have a known key
    # to delete (log keys are "str(datetime.now())[0:19]" timestamps).
    page.request.post(
        f"{live_server}/pellets/addprofile",
        form={
            "addprofile": "add_load",
            "brand_name": "Generic",
            "wood_type": "Pear",
            "rating": "2",
            "comments": "log-target",
        },
    )
    pelletdb = read_pellets_from_server()
    log_key = next(k for k, v in pelletdb["log"].items() if v == pelletdb["current"]["pelletid"])

    resp = page.request.post(f"{live_server}/pellets/deletelog", form={"delLog": log_key})
    assert resp.status == 200
    assert log_key not in read_pellets_from_server()["log"]
