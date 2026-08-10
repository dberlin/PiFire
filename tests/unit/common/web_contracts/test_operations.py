import math

import pytest
from pydantic import ValidationError

from common.web_contracts.operations import (
    AdminSettingsUpdate,
    AutoStatus,
    CoefficientsRequest,
    LogFamily,
    OsInfo,
    TunerPoint,
    UpdateStatus,
)


def test_os_info_preserves_unlisted_os_release_members() -> None:
    info = OsInfo.model_validate(
        {
            "PRETTY_NAME": "PiFire OS",
            "NAME": "PiFire",
            "VERSION": "1",
            "VERSION_ID": "1",
            "VERSION_CODENAME": "ember",
            "ARCHITECTURE": "aarch64",
            "BITS": "64-Bit",
            "IMAGE_ID": "pifire",
        },
        strict=True,
    )

    assert info.model_dump(mode="json", by_alias=True)["IMAGE_ID"] == "pifire"


def test_os_info_rejects_non_string_os_release_extras() -> None:
    payload = {
        "PRETTY_NAME": "PiFire OS",
        "NAME": "PiFire",
        "VERSION": "1",
        "VERSION_ID": "1",
        "VERSION_CODENAME": "ember",
        "ARCHITECTURE": "aarch64",
        "BITS": "64-Bit",
        "IMAGE_ID": 1,
    }

    with pytest.raises(ValidationError):
        OsInfo.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (LogFamily, {"stem": "events", "members": ["events.log"], "bytes": 1, "extra": True}),
        (UpdateStatus, {"percent": 1, "status": "working", "output": "", "extra": True}),
        (
            AutoStatus,
            {
                "current_tr": None,
                "current_temp": None,
                "high_tr": 0,
                "high_temp": 0,
                "medium_tr": 0,
                "medium_temp": 0,
                "low_tr": 0,
                "low_temp": 0,
                "samples": 0,
                "ready": False,
                "extra": True,
            },
        ),
    ],
)
def test_operations_models_other_than_os_info_reject_extra_members(model, payload) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload, strict=True)


def test_admin_settings_update_preserves_omission_and_rejects_null() -> None:
    update = AdminSettingsUpdate.model_validate({"debug_mode": True}, strict=True)

    assert update.model_dump(mode="json", by_alias=True) == {"debug_mode": True}
    with pytest.raises(ValidationError):
        AdminSettingsUpdate.model_validate({"debug_mode": None}, strict=True)


def test_admin_settings_update_requires_at_least_one_toggle() -> None:
    with pytest.raises(ValidationError):
        AdminSettingsUpdate.model_validate({}, strict=True)


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
def test_tuner_points_reject_non_finite_numbers(value: float) -> None:
    with pytest.raises(ValidationError):
        TunerPoint.model_validate({"segment": "High", "temp": value, "trohms": 1000}, strict=True)


def test_coefficients_request_requires_each_segment_once() -> None:
    point = {"segment": "High", "temp": 400, "trohms": 1200}

    with pytest.raises(ValidationError):
        CoefficientsRequest.model_validate({"points": [point, point, point]}, strict=True)
