"""Regression contracts for the production scheduled ARX learner."""

from __future__ import annotations

from collections.abc import Callable
import json
from dataclasses import FrozenInstanceError, replace

import numpy as np
import numpy.testing as npt
import pytest

from controller.linear_mpc.arx import ScheduledARX, ScheduledARXConfig
from controller.linear_mpc.contracts import AffinePrediction, FrameObservation, ModelUpdate


def frame(
    index: int,
    *,
    temp_c: float | None = None,
    requested_q: float | None = None,
    realized_q: float | None = None,
) -> FrameObservation:
    """Build one deterministic, eligible 20-second control frame."""
    q = 0.2 + 0.2 * ((index // 3) % 3) if requested_q is None else requested_q
    realized = q if realized_q is None else realized_q
    temperature = (
        100.0 + 0.13 * index + 1.8 * np.sin(index / 5.0)
        if temp_c is None
        else temp_c
    )
    return FrameObservation(
        frame_start_s=index * 20.0,
        frame_end_s=(index + 1) * 20.0,
        temp_c=float(temperature),
        setpoint_c=120.0,
        ambient_c=20.0,
        requested_q=float(q),
        realized_q=float(realized),
        requested_auger_duty=float(q),
        delivered_on_s=float(realized * 20.0),
        requested_fan_duty=None,
        actual_fan_duty=None,
        result_revision=3,
        output_source="controller",
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=True,
        role_generation=0,
    )


def test_contracts_are_immutable_and_defensively_own_arrays() -> None:
    observation = frame(0)
    with pytest.raises(FrozenInstanceError):
        observation.temp_c = 111.0  # type: ignore[misc]

    free = np.array([110.0, 111.0], dtype=np.float64)
    response = np.eye(2, dtype=np.float64)
    prediction = AffinePrediction(free, response)
    free[0] = 0.0
    response[0, 0] = 0.0
    assert prediction.free_output_c[0] == 110.0
    assert prediction.input_response_c[0, 0] == 1.0
    with pytest.raises(ValueError):
        prediction.free_output_c[0] = 0.0

    update = ModelUpdate(110.0, 111.0, 1.0, True)
    with pytest.raises(FrozenInstanceError):
        update.updated = False  # type: ignore[misc]


@pytest.mark.parametrize(
    "invalid",
    (
        lambda value: replace(value, frame_end_s=value.frame_start_s),
        lambda value: replace(value, temp_c=float("nan")),
        lambda value: replace(value, requested_q=-0.01),
        lambda value: replace(value, realized_q=1.01),
    ),
)
def test_frame_observation_rejects_invalid_time_numbers_and_duty(
    invalid: Callable[[FrameObservation], FrameObservation],
) -> None:
    with pytest.raises(ValueError):
        invalid(frame(0))


def test_scheduled_arx_snapshot_restores_exact_next_prediction_and_update() -> None:
    model = ScheduledARX(ScheduledARXConfig(na=2, nb=2, delays=(1, 2, 3)))
    model.fit(tuple(frame(index) for index in range(80)))
    for index in range(80, 92):
        model.observe(frame(index))

    snapshot = model.snapshot()
    restored = ScheduledARX.from_snapshot(snapshot)
    assert snapshot["schema"] == "scheduled-arx/v2"
    assert len(json.dumps(snapshot, separators=(",", ":"))) < 30_000

    next_frame = frame(92)
    expected_prediction = model.affine_prediction(
        4, next_frame.realized_q, np.full(4, next_frame.ambient_c)
    )
    expected_update = model.observe(next_frame)
    actual_prediction = restored.affine_prediction(
        4, next_frame.realized_q, np.full(4, next_frame.ambient_c)
    )
    actual_update = restored.observe(next_frame)

    npt.assert_allclose(
        actual_prediction.free_output_c, expected_prediction.free_output_c, atol=1e-12
    )
    npt.assert_allclose(
        actual_prediction.input_response_c,
        expected_prediction.input_response_c,
        atol=1e-12,
    )
    assert actual_update.predicted_temp_c == pytest.approx(
        expected_update.predicted_temp_c, abs=1e-12
    )
    assert actual_update.innovation_c == pytest.approx(
        expected_update.innovation_c, abs=1e-12
    )
    assert actual_update.updated is expected_update.updated

    following_frame = frame(93)
    expected_after_update = model.affine_prediction(
        4, following_frame.realized_q, np.full(4, following_frame.ambient_c)
    )
    actual_after_update = restored.affine_prediction(
        4, following_frame.realized_q, np.full(4, following_frame.ambient_c)
    )
    npt.assert_allclose(
        actual_after_update.free_output_c,
        expected_after_update.free_output_c,
        atol=1e-12,
    )
    npt.assert_allclose(
        actual_after_update.input_response_c,
        expected_after_update.input_response_c,
        atol=1e-12,
    )
    model.observe(following_frame)
    restored.observe(following_frame)
    assert restored.snapshot() == model.snapshot()


def test_two_populated_scheduled_arx_snapshots_fit_persisted_envelope() -> None:
    snapshots = []
    for offset in (0, 200):
        model = ScheduledARX(ScheduledARXConfig(na=2, nb=2, delays=(1, 2, 3)))
        model.fit(tuple(frame(index + offset) for index in range(80)))
        for index in range(80, 96):
            model.observe(frame(index + offset))
        snapshots.append(model.snapshot())

    assert len(json.dumps(snapshots, separators=(",", ":"))) < 60_000
