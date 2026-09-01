import copy

from collections.abc import Mapping
from typing import Any

from common.defaults import default_settings
from common.learning_trajectory import (
    FrameDeliveryCertainty,
    JsonValue,
    LearningTrajectoryFrame,
    TrajectoryBreakReason,
)
from controller.mpc_model import MODEL_SCHEMA


def current_settings_payload() -> dict[str, Any]:
    return default_settings()


def current_trajectory_frame(
    sequence: int,
    *,
    role_generation: int = 4,
    start_ms: int | None = None,
    end_ms: int | None = None,
    partial: bool = False,
) -> LearningTrajectoryFrame:
    start_ms = sequence * 20_000 if start_ms is None else start_ms
    end_ms = start_ms + 20_000 if end_ms is None else end_ms
    duration_seconds = (end_ms - start_ms) / 1_000
    return LearningTrajectoryFrame(
        sequence=sequence,
        monotonic_start_ms=start_ms,
        monotonic_end_ms=end_ms,
        wall_start_ms=1_000_000 + start_ms,
        wall_end_ms=1_000_000 + end_ms,
        chamber_temperature_c=110.0 + sequence,
        temperature_sample_monotonic_ms=end_ms,
        temperature_sample_wall_ms=1_000_000 + end_ms,
        temperature_sample_age_ms=0,
        temperature_sample_wall_age_ms=0,
        temperature_sample_clock_skew_ms=0,
        source_temperature_units="C",
        settings_revision=7,
        probe_valid=True,
        probe_source="grill-probe-1",
        ambient_temperature_c=24.0,
        ambient_source="configured",
        ambient_uncertainty_c=1.5,
        delivered_auger_on_seconds=duration_seconds * 0.4,
        realized_auger_duty=0.4,
        normalized_combustion_load=0.4,
        delivered_fan_on_seconds=duration_seconds,
        fan_duty_integral_seconds=duration_seconds * 0.5,
        mean_actual_fan_duty=0.5,
        auger_delivery_certainty=FrameDeliveryCertainty.EXACT,
        fan_delivery_certainty=FrameDeliveryCertainty.EXACT,
        effective_mode="Hold",
        recipe_step_id=None,
        complete=not partial,
        continuous=True,
        partial=partial,
        boundary_reason=TrajectoryBreakReason.LEFT_HOLD if partial else None,
        role_generation=role_generation,
    )


def current_model_snapshot(
    *,
    parameters: Mapping[str, JsonValue],
    revision: int,
) -> dict[str, JsonValue]:
    return {
        "version": MODEL_SCHEMA,
        "revision": revision,
        "parameters": copy.deepcopy(dict(parameters)),
    }
