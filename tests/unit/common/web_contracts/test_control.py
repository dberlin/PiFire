import math

import pytest
from pydantic import TypeAdapter, ValidationError

from common.web_contracts.control import (
    AddPelletProfileRequest,
    CommandRequest,
    ControlPatchRequest,
    EditPelletProfileRequest,
    NotifyEntry,
    NotifyUpdate,
    PelletActionRequest,
    PelletCurrent,
    PelletDbSchema,
    PelletProfileFields,
    TimerOptionsPayload,
    WledActionResponse,
    WledDiscoverResponse,
    WledPushProfilesRequest,
    WledTestProfileRequest,
)

from common.web_contracts.core import PelletSocketPayload

COMMAND_REQUESTS = (
    {"operation": "set_mode", "mode": "startup"},
    {"operation": "set_primary_setpoint", "temperature": 225},
    {"operation": "set_smoke_plus", "enabled": True},
    {"operation": "set_p_mode", "value": 4},
    {"operation": "prime", "grams": 125, "next_mode": "monitor"},
    {"operation": "timer_start", "seconds": 600},
    {
        "operation": "timer_start_with_options",
        "seconds": 600,
        "options": {"shutdown": True, "keepWarm": False},
    },
    {"operation": "timer_pause"},
    {"operation": "timer_stop"},
    {"operation": "timer_shutdown", "enabled": True},
    {"operation": "timer_keep_warm", "enabled": False},
    {"operation": "system", "command": "restart"},
    {"operation": "set_units", "units": "F"},
    {"operation": "manual_output", "output": "fan", "action": "toggle"},
    {"operation": "manual_pwm", "duty": 42},
)


@pytest.mark.parametrize("payload", COMMAND_REQUESTS)
def test_command_request_union_covers_command_client_path_grammar(payload):
    parsed = TypeAdapter(CommandRequest).validate_python(payload, strict=True)
    assert parsed.root.operation == payload["operation"]


def test_command_request_union_is_discriminated_by_operation():
    with pytest.raises(ValidationError):
        TypeAdapter(CommandRequest).validate_python({"operation": "not_a_command"}, strict=True)


@pytest.mark.parametrize(
    ("payload", "field"),
    (
        ({"operation": "set_primary_setpoint", "temperature": True}, "temperature"),
        ({"operation": "set_p_mode", "value": True}, "value"),
        ({"operation": "prime", "grams": True}, "grams"),
        ({"operation": "timer_start", "seconds": True}, "seconds"),
        ({"operation": "manual_pwm", "duty": True}, "duty"),
    ),
)
def test_command_numeric_fields_reject_booleans(payload, field):
    with pytest.raises(ValidationError) as exc:
        TypeAdapter(CommandRequest).validate_python(payload, strict=True)
    assert field in str(exc.value)


def test_timer_options_use_the_browser_facing_keep_warm_name():
    payload = TimerOptionsPayload.model_validate({"shutdown": True, "keepWarm": False}, strict=True)
    assert payload.model_dump(mode="json", by_alias=True) == {"shutdown": True, "keepWarm": False}


def test_notify_update_accepts_nested_json_values_without_any_escape_hatch():
    update = NotifyUpdate.model_validate(
        {
            "label": "Probe 1",
            "type": "probe",
            "fields": {"req": True, "target": 203, "eta": None, "metadata": {"tags": ["cook", 2]}},
        },
        strict=True,
    )
    assert update.fields["metadata"] == {"tags": ["cook", 2]}


@pytest.mark.parametrize("value", (math.inf, -math.inf, math.nan))
def test_notify_update_rejects_non_finite_json_numbers(value):
    with pytest.raises(ValidationError):
        NotifyUpdate.model_validate(
            {"label": "Probe 1", "type": "probe", "fields": {"target": value}},
            strict=True,
        )


def test_notify_entry_retains_device_specific_json_fields():
    entry = NotifyEntry.model_validate(
        {
            "label": "Hopper",
            "type": "hopper",
            "req": True,
            "shutdown": False,
            "keep_warm": False,
            "last_check": 123,
        },
        strict=True,
    )
    assert entry.model_dump(mode="json")["last_check"] == 123


def test_control_patch_preserves_sparse_rfc7396_members_and_notify_updates():
    patch = ControlPatchRequest.model_validate(
        {
            "recipe": {"step_data": {"pause": False}},
            "notify_updates": [{"label": "Probe 1", "type": "probe", "fields": {"req": True, "target": 203}}],
        },
        strict=True,
    )
    assert patch.model_dump(mode="json", exclude_unset=True) == {
        "recipe": {"step_data": {"pause": False}},
        "notify_updates": [{"label": "Probe 1", "type": "probe", "fields": {"req": True, "target": 203}}],
    }


def test_pellet_database_and_socket_share_the_canonical_models():
    database = PelletDbSchema.model_validate(
        {
            "schema_version": 2,
            "current": {
                "pelletid": "p1",
                "hopper_level": 75,
                "date_loaded": "2026-08-10 12:00:00",
                "est_usage": 12.5,
            },
            "archive": {"p1": {"brand": "Generic", "wood": "Alder", "rating": 5, "comments": ""}},
            "log": {"1786363200000": {"pelletid": "p1", "deleted": False}},
            "brands": ["Generic"],
            "woods": ["Alder"],
            "lastupdated": {"time": 1786363200},
        },
        strict=True,
    )
    socket_payload = PelletSocketPayload.model_validate({"uuid": "abc", "pellets": database}, strict=True)
    assert socket_payload.pellets is database


@pytest.mark.parametrize("field", ("hopper_level", "est_usage"))
def test_pellet_current_numeric_fields_reject_booleans(field):
    payload = {
        "pelletid": "p1",
        "hopper_level": 75,
        "date_loaded": "2026-08-10 12:00:00",
        "est_usage": 1.5,
    }
    payload[field] = True
    with pytest.raises(ValidationError):
        PelletCurrent.model_validate(payload, strict=True)


@pytest.mark.parametrize("value", (math.inf, -math.inf, math.nan))
def test_pellet_usage_rejects_non_finite_values_before_serialization(value):
    with pytest.raises(ValidationError):
        PelletCurrent.model_validate(
            {
                "pelletid": "p1",
                "hopper_level": 75,
                "date_loaded": "2026-08-10 12:00:00",
                "est_usage": value,
            },
            strict=True,
        )


def test_pellet_action_union_covers_every_frontend_action():
    requests = (
        {"action": "load_profile", "data": {"profile": "p1"}},
        {"action": "hopper_check", "data": {}},
        {"action": "edit_brands", "data": {"new_brand": "Local"}},
        {"action": "edit_brands", "data": {"delete_brand": "Local"}},
        {"action": "edit_woods", "data": {"new_wood": "Oak"}},
        {"action": "edit_woods", "data": {"delete_wood": "Oak"}},
        {
            "action": "add_profile",
            "data": {
                "brand_name": "Local",
                "wood_type": "Oak",
                "rating": 4,
                "comments": "",
                "add_and_load": True,
            },
        },
        {
            "action": "edit_profile",
            "data": {
                "profile": "p1",
                "brand_name": "Local",
                "wood_type": "Oak",
                "rating": 4,
                "comments": "",
            },
        },
        {"action": "delete_profile", "data": {"profile": "p1"}},
        {"action": "delete_log", "data": {"log_item": "1786363200000"}},
    )
    adapter = TypeAdapter(PelletActionRequest)
    assert [adapter.validate_python(request, strict=True).root.action for request in requests] == [
        request["action"] for request in requests
    ]


def test_pellet_profile_fields_are_reused_by_add_and_edit_requests():
    fields = {"brand_name": "Local", "wood_type": "Oak", "rating": 4, "comments": ""}
    add = AddPelletProfileRequest.model_validate(
        {"action": "add_profile", "data": {**fields, "add_and_load": False}},
        strict=True,
    )
    edit = EditPelletProfileRequest.model_validate(
        {"action": "edit_profile", "data": {**fields, "profile": "p1"}},
        strict=True,
    )
    assert isinstance(add.data, PelletProfileFields)
    assert isinstance(edit.data, PelletProfileFields)


def test_pellet_rating_rejects_boolean():
    with pytest.raises(ValidationError):
        TypeAdapter(PelletActionRequest).validate_python(
            {
                "action": "add_profile",
                "data": {
                    "brand_name": "Local",
                    "wood_type": "Oak",
                    "rating": True,
                    "comments": "",
                    "add_and_load": False,
                },
            },
            strict=True,
        )


def test_wled_requests_reject_boolean_numeric_fields():
    with pytest.raises(ValidationError):
        WledPushProfilesRequest.model_validate(
            {"device_address": "192.168.1.50", "profile_numbers": {"idle": True}},
            strict=True,
        )
    with pytest.raises(ValidationError):
        WledTestProfileRequest.model_validate(
            {"device_address": "192.168.1.50", "profile_number": True},
            strict=True,
        )


def test_wled_discover_response_preserves_device_specific_fields():
    response = WledDiscoverResponse.model_validate(
        {
            "result": "success",
            "message": "Found 1 WLED devices",
            "devices": [
                {
                    "name": "Patio",
                    "ip": "192.168.1.50",
                    "port": 80,
                    "led_count": 144,
                    "version": "0.15.0",
                    "product": "WLED",
                    "mac": "001122334455",
                    "online": True,
                }
            ],
        },
        strict=True,
    )
    assert response.model_dump(mode="json")["devices"][0]["led_count"] == 144


def test_wled_action_response_keeps_push_specific_fields_optional():
    response = WledActionResponse.model_validate(
        {
            "result": "success",
            "message": "Successfully pushed 2 profiles",
            "profiles_pushed": 2,
            "profiles": ["idle", "cooking"],
        },
        strict=True,
    )
    assert response.model_dump(mode="json", exclude_unset=True)["profiles"] == ["idle", "cooking"]
