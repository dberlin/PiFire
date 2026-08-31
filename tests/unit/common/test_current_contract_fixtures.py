from inspect import signature

from common.learning_trajectory import JsonValue
from common.settings_schema import SETTINGS_SCHEMA_VERSION
from controller.mpc_model import MODEL_SCHEMA
from tests.fakes.current_contracts import (
    current_model_snapshot,
    current_settings_payload,
    current_trajectory_frame,
)


def test_current_settings_payload_uses_the_current_schema_and_learning_default() -> None:
    payload = current_settings_payload()

    assert payload["schema_version"] == SETTINGS_SCHEMA_VERSION
    assert payload["controller"]["config"]["pid_sp"]["enable_identification"] is True


def test_current_trajectory_frame_keeps_the_callers_role_generation() -> None:
    frame = current_trajectory_frame(3, role_generation=19)

    assert frame.role_generation == 19


def test_current_model_snapshot_uses_the_current_model_schema() -> None:
    snapshot = current_model_snapshot(parameters={"gain": 1.5}, revision=7)

    assert snapshot == {
        "version": MODEL_SCHEMA,
        "revision": 7,
        "parameters": {"gain": 1.5},
    }


def test_current_model_snapshot_owns_nested_parameter_values() -> None:
    nested_object: dict[str, JsonValue] = {"enabled": True}
    nested_array: list[JsonValue] = [nested_object]
    parameters: dict[str, JsonValue] = {"nested": nested_array}

    snapshot = current_model_snapshot(parameters=parameters, revision=7)
    nested_object["enabled"] = False
    nested_array.append("caller mutation")

    assert snapshot["parameters"] == {"nested": [{"enabled": True}]}


def test_current_contract_builders_do_not_accept_version_overrides() -> None:
    parameter_names = {
        builder.__name__: tuple(signature(builder).parameters)
        for builder in (
            current_settings_payload,
            current_trajectory_frame,
            current_model_snapshot,
        )
    }

    assert parameter_names == {
        "current_settings_payload": (),
        "current_trajectory_frame": (
            "sequence",
            "role_generation",
            "start_ms",
            "end_ms",
            "partial",
        ),
        "current_model_snapshot": ("parameters", "revision"),
    }
    assert all(
        "schema" not in parameter and "version" not in parameter
        for parameters in parameter_names.values()
        for parameter in parameters
    )
