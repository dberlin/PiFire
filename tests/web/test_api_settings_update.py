"""`POST /api/settings_update`
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
from common.common import display_sleep_timeout
from common.datastore_accessors import (
    execute_control_writes,
    read_control,
    read_settings,
    write_control,
    write_settings,
)


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


def test_settings_update_rejects_unknown_flag(client):
    original_grill_name = read_settings()["globals"]["grill_name"]

    body = {"settings": {"globals": {"grill_name": "ShouldNotPersist"}}, "flags": ["mode"]}
    resp = client.post("/api/settings_update", data=json.dumps(body), content_type="application/json")
    assert resp.get_json()["result"] == "error"

    # Settings delta must NOT have been applied.
    assert read_settings()["globals"]["grill_name"] == original_grill_name

    # Control must NOT have gained a bogus "mode" flag.
    execute_control_writes()
    assert read_control().get("mode") is not True


# ---------------------------------------------------------------------------
# Two-layer rejection. Layer 1 (PartialSettingsSchema, on the raw
# delta) catches a structurally- or type-bad delta before anything is
# touched. Layer 2 (write_settings()'s strict gate, on the merged tree)
# catches anything the sparse delta alone couldn't evaluate. Both layers
# leave the store untouched and return the same {"result": "error", ...}
# envelope shape as every other failure path in this action -- no 500.
# ---------------------------------------------------------------------------


def test_settings_update_rejects_bad_field_type_layer2_full_tree(client):
    """Canonical pin: a bad scalar nested two levels deep. Also
    layer-1-catchable (maxtemp is typed on PartialSettingsSchema too), but
    pinned here as the full round-trip: envelope + untouched store."""
    before = read_settings()

    body = {"settings": {"safety": {"maxtemp": "nope"}}, "flags": []}
    resp = client.post("/api/settings_update", data=json.dumps(body), content_type="application/json")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["result"] == "error"
    assert "safety.maxtemp" in payload["message"]

    assert read_settings() == before


def test_settings_update_rejects_structurally_bad_delta_layer1(client):
    """A section replaced with a scalar (not even the right shape) is caught
    by the delta-layer (PartialSettingsSchema) before deep_update ever runs --
    distinct from the full-tree gate in the test above."""
    before = read_settings()

    body = {"settings": {"safety": 5}, "flags": []}
    resp = client.post("/api/settings_update", data=json.dumps(body), content_type="application/json")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["result"] == "error"
    assert "safety" in payload["message"]

    assert read_settings() == before


# ---------------------------------------------------------------------------
# Layer 1's PartialSettingsSchema.model_validate(...,
# strict=True) inherits SettingsSchema's cross-field model_validators
# (PwmSettings._check_profiles, SmartStart._check_profile_count,
# SettingsSchema._check_startup_pwm_duty_cycle). On a sparse delta those ran
# against each ABSENT section's STATIC DEFAULT, not the store's real values --
# so a legitimately-set store value (e.g. pwm.min_duty_cycle lowered via
# PwmTab) could make an unrelated, otherwise-valid sparse delta (e.g.
# StartupTab's bare pwm_duty_cycle) falsely rejected at Layer 1 even though
# it's fine against the real merged tree. Layer 1 now reports FIELD-level
# errors only; Layer 2 (write_settings() -> validate_settings_tree() on the
# merged tree) is the sole cross-field authority.
# ---------------------------------------------------------------------------


def test_settings_update_accepts_sparse_delta_valid_against_store_not_defaults(client):
    """Proven-reachable repro: pwm.min_duty_cycle=10 in the
    store (legitimately settable via PwmTab; schema has no `ge` floor on it),
    then StartupTab's sparse delta {"startup": {"pwm_duty_cycle": 15}}. 15 is
    within the STORE's [10, 100] but below the schema DEFAULT's min of 20 --
    pre-fix, Layer 1 falsely rejected this against the default; Layer 2 (the
    merged, real tree) has always accepted it. Before the strict schema
    existed, this save worked."""
    settings = read_settings()
    settings["pwm"]["min_duty_cycle"] = 10
    write_settings(settings)

    body = {"settings": {"startup": {"pwm_duty_cycle": 15}}, "flags": []}
    resp = client.post("/api/settings_update", data=json.dumps(body), content_type="application/json")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["result"] == "success", payload
    assert read_settings()["startup"]["pwm_duty_cycle"] == 15


def test_settings_update_layer2_still_rejects_delta_invalid_against_merged_tree(client):
    """Layer 2 remains authoritative: a valid-TYPED sparse delta that makes
    the MERGED tree invalid against the (unmodified, default) store must
    still be rejected -- pwm.min_duty_cycle defaults to 20, so
    startup.pwm_duty_cycle=5 violates SettingsSchema.
    _check_startup_pwm_duty_cycle on the merged tree even though Layer 1 no
    longer checks it directly."""
    before = read_settings()
    assert before["pwm"]["min_duty_cycle"] == 20  # precondition: default store

    body = {"settings": {"startup": {"pwm_duty_cycle": 5}}, "flags": []}
    resp = client.post("/api/settings_update", data=json.dumps(body), content_type="application/json")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["result"] == "error"
    assert "startup.pwm_duty_cycle" in payload["message"]

    assert read_settings() == before


# ---------------------------------------------------------------------------
# The React General tab's screen-sleep control (ruling 4, 2026-07-26,
# docs/superpowers/react-migration-backlog.md): it must actually drive the
# DPMS behaviour, so this pins the seam between the two processes. The web
# process writes settings["display"]["sleep_timeout"]; the display process
# re-reads it through common.common.display_sleep_timeout() once a second
# (display/qtapp.py's _timeout_fn -> PiFireBackend.TIMEOUT ->
# _update_idle -> asleep -> ScreenPowerController's `swaymsg output * dpms`).
# Every other hop of that path already has a test; this is the one that
# crosses the process boundary, and a control the display cannot see would be
# worse than no control.
# ---------------------------------------------------------------------------


def test_settings_update_sleep_timeout_is_what_the_display_reads(client):
    body = {"settings": {"display": {"sleep_timeout": 45}}, "flags": []}
    resp = client.post("/api/settings_update", data=json.dumps(body), content_type="application/json")

    assert resp.get_json()["result"] == "success"
    assert display_sleep_timeout(read_settings()) == 45


def test_settings_update_sleep_timeout_zero_reaches_the_display_as_never(client):
    """0 is not "unset": it is the value that disables sleeping entirely
    (qtbackend._update_idle wakes an already-asleep screen on it), so it has
    to survive the round trip as 0 rather than fall back to the 300 default."""
    body = {"settings": {"display": {"sleep_timeout": 0}}, "flags": []}
    resp = client.post("/api/settings_update", data=json.dumps(body), content_type="application/json")

    assert resp.get_json()["result"] == "success"
    assert display_sleep_timeout(read_settings()) == 0


def test_settings_update_rejects_a_negative_sleep_timeout(client):
    """The schema bound (settings_schema.py: ge=0) is what the React field's
    min=0 mirrors; a client that ignores it must not be able to store one."""
    before = read_settings()

    body = {"settings": {"display": {"sleep_timeout": -1}}, "flags": []}
    resp = client.post("/api/settings_update", data=json.dumps(body), content_type="application/json")

    assert resp.get_json()["result"] == "error"
    assert read_settings() == before


# ---------------------------------------------------------------------------
# The React SmartStart/PWM range-table saves ride the tab's single delta with
# the ["settings_update"] flag, where Flask's separate table forms send a BARE
# write. Judged a harmless superset -- the flag only makes the control loop
# re-read settings -- but the settings-state half of that claim was never
# pinned across the process boundary (deferred-inventory-plans finding #9). The
# seam pinned here: an array-subtree ("table") save WITH the flag stores the
# SAME settings tree as the identical delta WITHOUT it. The flag's only effect
# is the queued control re-read; it never changes what is written.
# ---------------------------------------------------------------------------


def _settings_update(client, body):
    return client.post("/api/settings_update", data=json.dumps(body), content_type="application/json")


def _reset_settings_update_flag():
    ctrl = read_control()
    ctrl["settings_update"] = False
    write_control(ctrl, WriteKind.OVERWRITE, origin="test")


def test_settings_update_table_save_flag_does_not_alter_stored_settings(client):
    # A real "table" payload: the whole pwm.profiles array with one duty_cycle
    # changed -- the shape PwmTab posts, a whole-array replace (not a per-element
    # merge). 25 is within the schema's [min_duty_cycle, max_duty_cycle] band.
    baseline = read_settings()
    new_profiles = [dict(p) for p in baseline["pwm"]["profiles"]]
    new_profiles[0]["duty_cycle"] = 25
    delta = {"pwm": {"profiles": new_profiles}}

    # 1) React's write: the delta plus the settings_update flag.
    _reset_settings_update_flag()
    assert _settings_update(client, {"settings": delta, "flags": ["settings_update"]}).get_json()["result"] == "success"
    execute_control_writes()
    with_flag = read_settings()
    assert read_control()["settings_update"] is True  # the flag's ONLY intended effect

    # 2) Flask's write: the identical delta as a bare write, from the same start.
    write_settings(baseline)
    _reset_settings_update_flag()
    assert _settings_update(client, {"settings": delta, "flags": []}).get_json()["result"] == "success"
    execute_control_writes()
    without_flag = read_settings()
    assert read_control()["settings_update"] is False

    # The array really landed (whole-array replace; sibling pwm fields intact)...
    assert without_flag["pwm"]["profiles"][0]["duty_cycle"] == 25
    assert without_flag["pwm"]["max_duty_cycle"] == baseline["pwm"]["max_duty_cycle"]
    # ...and the extra flag changed NOTHING about what was stored.
    assert with_flag == without_flag
