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
# S2 Task 5: two-layer rejection. Layer 1 (PartialSettingsSchema, on the raw
# delta) catches a structurally- or type-bad delta before anything is
# touched. Layer 2 (write_settings()'s now-strict gate, on the merged tree)
# catches anything the sparse delta alone couldn't evaluate. Both layers
# leave the store untouched and return the same {"result": "error", ...}
# envelope shape as every other failure path in this action -- no 500.
# ---------------------------------------------------------------------------


def test_settings_update_rejects_bad_field_type_layer2_full_tree(client):
    """Brief's canonical pin: a bad scalar nested two levels deep. Also
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
# Final-review fix: Layer 1's PartialSettingsSchema.model_validate(...,
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
    """Proven-reachable repro (final review): pwm.min_duty_cycle=10 in the
    store (legitimately settable via PwmTab; schema has no `ge` floor on it),
    then StartupTab's sparse delta {"startup": {"pwm_duty_cycle": 15}}. 15 is
    within the STORE's [10, 100] but below the schema DEFAULT's min of 20 --
    pre-fix, Layer 1 falsely rejected this against the default; Layer 2 (the
    merged, real tree) has always accepted it. Pre-S2 this save worked."""
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
