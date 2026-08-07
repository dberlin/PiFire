"""Causal completed-origin contracts for online model evidence."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from common.control_trace import AmbientSource
from controller.linear_mpc.adaptation import OnlineAdaptation
from controller.linear_mpc.contracts import AffinePrediction, FrameObservation, ModelUpdate


class _AffineModel:
    def __init__(self, *, marker: str, schema: str = "causal-origin-test/v1") -> None:
        self.marker = marker
        self.schema = schema

    def observe(self, observation: FrameObservation) -> ModelUpdate:
        return ModelUpdate(observation.temp_c, observation.temp_c, 0.0, True)

    def track(self, observation: FrameObservation) -> ModelUpdate:
        return ModelUpdate(observation.temp_c, observation.temp_c, 0.0, False)

    def affine_prediction(self, horizon_steps: int, _q_previous: float, _ambient_future: np.ndarray) -> AffinePrediction:
        return AffinePrediction(
            np.full(horizon_steps, float(len(self.marker))),
            np.zeros((horizon_steps, horizon_steps)),
        )

    def snapshot(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "marker": self.marker,
            "status": {"regions": [{"effective_samples": 30}], "steady_gain": 1.0},
            "active_delay": 2,
        }


def _frame(index: int, *, calibration_fit: bool = False) -> FrameObservation:
    q = 0.25 if index % 2 else 0.75
    return FrameObservation(
        frame_start_s=index * 20.0,
        frame_end_s=(index + 1) * 20.0,
        temp_c=100.0 + index,
        setpoint_c=180.0,
        ambient_c=20.0,
        requested_q=q,
        realized_q=q,
        requested_auger_duty=q,
        delivered_on_s=5.0,
        requested_fan_duty=None,
        actual_fan_duty=None,
        result_revision=index,
        output_source="controller",
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=True,
        role_generation=0,
        observation_sequence=index,
        ambient_source=AmbientSource.CONFIGURED,
        calibration_stage="probe" if calibration_fit else None,
        calibration_fit=calibration_fit,
    )


def test_origins_precommit_all_five_horizons_before_targets_and_complete_once() -> None:
    manager = OnlineAdaptation(_AffineModel(marker="incumbent"), _AffineModel(marker="challenger"))

    manager.observe(_frame(0))

    assert {origin.horizon_steps for origin in manager.pending_origins} == {3, 15, 45, 90, 180}
    assert not manager.completed_origins

    for index in range(1, 181):
        manager.observe(_frame(index))

    completed = tuple(origin for origin in manager.completed_origins if origin.origin_time_s == 20.0)
    assert {origin.horizon_steps for origin in completed} == {3, 15, 45, 90, 180}
    assert {origin.completion_time_s for origin in completed} == {80.0, 320.0, 920.0, 1820.0, 3620.0}

    count = len(manager.completed_origins)
    manager.observe(_frame(180))
    assert len(manager.completed_origins) == count


def test_refresh_keeps_precommitted_forecast_and_digest_immutable() -> None:
    manager = OnlineAdaptation(_AffineModel(marker="incumbent"), _AffineModel(marker="old"))
    manager.observe(_frame(0))
    origin = manager.pending_origins[0]
    old_prediction = origin.challenger_prediction_c

    manager.refresh_challenger(_AffineModel(marker="new challenger"))

    assert origin.challenger_prediction_c == old_prediction
    assert origin.challenger_digest != manager.challenger_digest


def test_incompatible_challenger_refresh_expires_pending_origins() -> None:
    manager = OnlineAdaptation(_AffineModel(marker="incumbent"), _AffineModel(marker="old"))
    manager.observe(_frame(0))

    manager.refresh_challenger(_AffineModel(marker="new", schema="other-model/v1"))

    assert not manager.pending_origins


def test_destructive_or_calibration_frame_cannot_complete_prior_validation_origin() -> None:
    manager = OnlineAdaptation(_AffineModel(marker="incumbent"), _AffineModel(marker="challenger"))
    manager.observe(_frame(0))
    manager.observe(replace(_frame(1), skipped=True))
    for index in range(2, 185):
        manager.observe(_frame(index))
    assert not any(origin.origin_time_s == 20.0 for origin in manager.completed_origins)

    manager = OnlineAdaptation(_AffineModel(marker="incumbent"), _AffineModel(marker="challenger"))
    manager.observe(_frame(0))
    manager.observe(_frame(1, calibration_fit=True))
    for index in range(2, 185):
        manager.observe(_frame(index))
    assert not any(origin.origin_time_s == 20.0 for origin in manager.completed_origins)
