import math

import pytest
from pydantic import ValidationError

from common.web_contracts.content import (
    FileErrorDetail,
    HistoryAnnotation,
    HistoryDataset,
    HistoryPoint,
    MetricRecord,
    MetricsPayload,
    RecipeAssetAssignmentRequest,
    RecipeIngredientAddRequest,
    RecipeIngredientDeleteRequest,
    RecipeIngredientUpdateRequest,
    RecipeInstructionAddRequest,
    RecipeInstructionDeleteRequest,
    RecipeInstructionUpdateRequest,
    RecipeMetadataUpdateRequest,
    RecipeStep,
    RecipeStepDeleteRequest,
    RecipeStepInsertRequest,
    RecipeStepUpdateRequest,
)


def test_recipe_mutation_requests_preserve_concrete_action_shapes():
    requests = (
        RecipeMetadataUpdateRequest(file="dinner.pfrecipe", fields={"title": "Dinner", "food_probes": 2}),
        RecipeIngredientAddRequest(file="dinner.pfrecipe", action="add"),
        RecipeIngredientUpdateRequest(
            file="dinner.pfrecipe", action="update", index=0, name="Brisket", quantity="1 packer"
        ),
        RecipeIngredientDeleteRequest(file="dinner.pfrecipe", action="delete", index=0),
        RecipeInstructionAddRequest(file="dinner.pfrecipe", action="add"),
        RecipeInstructionUpdateRequest(
            file="dinner.pfrecipe",
            action="update",
            index=0,
            text="Trim",
            ingredients=["Brisket"],
            step=0,
        ),
        RecipeInstructionDeleteRequest(file="dinner.pfrecipe", action="delete", index=0),
        RecipeStepInsertRequest(file="dinner.pfrecipe", action="insert", index=1),
        RecipeStepUpdateRequest(
            file="dinner.pfrecipe",
            action="update",
            index=1,
            step=RecipeStep(
                mode="Hold",
                hold_temp=225,
                timer=30,
                notify=True,
                message="Ready",
                pause=False,
                trigger_temps={"primary": 225, "food": [165, 0]},
            ),
        ),
        RecipeStepDeleteRequest(file="dinner.pfrecipe", action="delete", index=1),
        RecipeAssetAssignmentRequest(
            file="dinner.pfrecipe", section="ingredients", index=0, assets=["photo.png"]
        ),
    )

    assert [request.model_dump(mode="json", exclude_unset=True) for request in requests] == [
        {"file": "dinner.pfrecipe", "fields": {"title": "Dinner", "food_probes": 2}},
        {"file": "dinner.pfrecipe", "action": "add"},
        {
            "file": "dinner.pfrecipe",
            "action": "update",
            "index": 0,
            "name": "Brisket",
            "quantity": "1 packer",
        },
        {"file": "dinner.pfrecipe", "action": "delete", "index": 0},
        {"file": "dinner.pfrecipe", "action": "add"},
        {
            "file": "dinner.pfrecipe",
            "action": "update",
            "index": 0,
            "text": "Trim",
            "ingredients": ["Brisket"],
            "step": 0,
        },
        {"file": "dinner.pfrecipe", "action": "delete", "index": 0},
        {"file": "dinner.pfrecipe", "action": "insert", "index": 1},
        {
            "file": "dinner.pfrecipe",
            "action": "update",
            "index": 1,
            "step": {
                "mode": "Hold",
                "hold_temp": 225,
                "timer": 30,
                "notify": True,
                "message": "Ready",
                "pause": False,
                "trigger_temps": {"primary": 225, "food": [165, 0]},
            },
        },
        {"file": "dinner.pfrecipe", "action": "delete", "index": 1},
        {"file": "dinner.pfrecipe", "section": "ingredients", "index": 0, "assets": ["photo.png"]},
    ]


def test_recipe_requests_reject_coercion_unknown_keys_and_non_finite_numbers():
    with pytest.raises(ValidationError):
        RecipeIngredientDeleteRequest.model_validate(
            {"file": "dinner.pfrecipe", "action": "delete", "index": True}, strict=True
        )
    with pytest.raises(ValidationError):
        RecipeStepUpdateRequest.model_validate(
            {
                "file": "dinner.pfrecipe",
                "action": "update",
                "index": 0,
                "step": {
                    "mode": "Hold",
                    "hold_temp": 225,
                    "timer": 30,
                    "notify": False,
                    "message": "",
                    "pause": False,
                    "trigger_temps": {"primary": 225, "food": [math.inf]},
                },
            },
            strict=True,
        )
    with pytest.raises(ValidationError):
        RecipeIngredientAddRequest.model_validate(
            {"file": "dinner.pfrecipe", "action": "add", "future": True}, strict=True
        )


def test_history_contract_preserves_empty_data_and_annotation_extras():
    dataset = HistoryDataset.model_validate(
        {"label": "Grill", "borderColor": "#f00", "hidden": False, "data": []}, strict=True
    )
    annotation = HistoryAnnotation.model_validate(
        {
            "type": "line",
            "xMin": 1_700_000_000_000,
            "xMax": 1_700_000_000_000,
            "borderColor": "blue",
            "borderWidth": 2,
            "display": True,
            "label": {"content": "Hold", "enabled": True, "position": "end"},
        },
        strict=True,
    )

    assert dataset.model_dump(mode="json", exclude_unset=True) == {
        "label": "Grill",
        "borderColor": "#f00",
        "hidden": False,
        "data": [],
    }
    assert annotation.model_dump(mode="json", exclude_unset=True)["label"]["content"] == "Hold"
    assert HistoryPoint.model_validate({"x": 1_700_000_000_000, "y": None}, strict=True).y is None


def test_metrics_contract_preserves_running_status_and_rejects_non_finite_scalars():
    record = MetricRecord.model_validate(
        {
            "id": "metric-id",
            "starttime": 1_700_000_000_000,
            "starttime_c": "12:00:00",
            "endtime": 0,
            "endtime_c": 0,
            "timeinmode": "Active",
            "mode": "Smoke",
            "augerontime": 100,
            "augerontime_c": "100 s",
            "estusage_m": "30 grams",
            "estusage_i": "0.07 pounds (1.06 ounces)",
            "fanontime": 0,
            "fanontime_c": "0",
            "smokeplus": True,
            "primary_setpoint": 225,
            "smart_start_profile": 0,
            "startup_temp": 0,
            "p_mode": 0,
            "auger_cycle_time": 0,
            "pellet_level_start": 100,
            "pellet_level_end": 95,
            "pellet_brand_type": "",
        },
        strict=True,
    )

    payload = MetricsPayload(metrics=[record], units="F", augerrate=0.3)
    assert payload.model_dump(mode="json")["metrics"][0]["endtime_c"] == 0
    with pytest.raises(ValidationError):
        MetricsPayload.model_validate({"metrics": [], "units": "F", "augerrate": math.nan}, strict=True)


def test_browser_normalized_file_error_detail_has_a_finite_status_vocabulary():
    detail = FileErrorDetail(status=422, message="unreadable", errortype="version")
    assert detail.model_dump(mode="json") == {
        "status": 422,
        "message": "unreadable",
        "errortype": "version",
    }
    with pytest.raises(ValidationError):
        FileErrorDetail.model_validate(
            {"status": "422", "message": "unreadable", "errortype": "legacy"}, strict=True
        )
