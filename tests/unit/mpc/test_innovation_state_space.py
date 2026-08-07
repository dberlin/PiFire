"""Observable contracts for the bounded innovation state-space learner."""

from __future__ import annotations

from copy import deepcopy
import json
from dataclasses import FrozenInstanceError, replace

import numpy as np
import numpy.testing as npt
import pytest

from controller.linear_mpc.contracts import FrameObservation
from controller.linear_mpc.state_space import (
    AlignmentResult,
    InnovationStateSpace,
    RefreshDiagnostics,
    RefreshRejectionReason,
    StateSpaceConfig,
)


def _frames(*, order: int, count: int = 96) -> tuple[FrameObservation, ...]:
    """Build a deterministic stable thermal realization with persistently exciting input."""
    state = np.zeros(order, dtype=np.float64)
    if order == 1:
        transition = np.array([[0.82]])
        input_vector = np.array([0.28])
        output_vector = np.array([1.0])
    elif order == 2:
        transition = np.array([[0.73, 0.11], [-0.08, 0.57]])
        input_vector = np.array([0.23, 0.07])
        output_vector = np.array([1.0, 0.31])
    else:
        raise ValueError("fixture only defines first- and second-order systems")
    frames: list[FrameObservation] = []
    previous_q = 0.0
    for index in range(count):
        q = float(0.15 + 0.7 * ((index * 17 % 23) / 22.0))
        state = transition @ state + input_vector * previous_q
        frames.append(
            FrameObservation(
                frame_start_s=float(index * 20),
                frame_end_s=float((index + 1) * 20),
                temp_c=float(20.0 + output_vector @ state),
                setpoint_c=120.0,
                ambient_c=20.0,
                requested_q=q,
                realized_q=q,
                requested_auger_duty=q,
                delivered_on_s=q * 20.0,
                requested_fan_duty=None,
                actual_fan_duty=None,
                result_revision=0,
                output_source="test",
                lid_open=False,
                safety_inhibited=False,
                manual_override=False,
                stale=False,
                skipped=False,
                reset=False,
                continuous=True,
                role_generation=0,
            )
        )
        previous_q = q
    return tuple(frames)


def _config(*, orders: tuple[int, ...] = (1, 2), delays: tuple[int, ...] = (1, 2)) -> StateSpaceConfig:
    return StateSpaceConfig(
        orders=orders,
        delays=delays,
        block_rows=4,
        validation_fraction=0.25,
        max_buffer_samples=112,
        refresh_interval_s=60.0,
    )


@pytest.mark.parametrize("order", (1, 2))
def test_identifies_stable_system_deterministically(order: int) -> None:
    """Changing deterministic fitting to be history/order dependent must fail here."""
    frames = _frames(order=order)
    left = InnovationStateSpace(_config())
    right = InnovationStateSpace(_config())

    left_diagnostics = left.fit(frames)
    right_diagnostics = right.fit(frames)

    assert left_diagnostics.accepted and right_diagnostics.accepted
    assert left_diagnostics.selected_order == right_diagnostics.selected_order
    assert left_diagnostics.selected_delay == right_diagnostics.selected_delay
    left_snapshot = left.snapshot()
    right_snapshot = right.snapshot()
    assert left_snapshot["model"] == right_snapshot["model"]
    assert left_snapshot["state"] == right_snapshot["state"]
    assert [
        (attempt.order, attempt.delay, attempt.rejection_reasons, attempt.prediction_score, attempt.braking_score)
        for attempt in left_diagnostics.attempts
    ] == [
        (attempt.order, attempt.delay, attempt.rejection_reasons, attempt.prediction_score, attempt.braking_score)
        for attempt in right_diagnostics.attempts
    ]


def test_candidate_declaration_permutation_does_not_change_selection() -> None:
    """Selecting the first equivalent declaration rather than the tie-break must fail here."""
    frames = _frames(order=2)
    forward = InnovationStateSpace(_config(orders=(1, 2), delays=(1, 2)))
    reversed_ = InnovationStateSpace(_config(orders=(2, 1), delays=(2, 1)))

    forward_diagnostics = forward.fit(frames)
    reversed_diagnostics = reversed_.fit(frames)

    assert (forward_diagnostics.selected_order, forward_diagnostics.selected_delay) == (
        reversed_diagnostics.selected_order,
        reversed_diagnostics.selected_delay,
    )
    for name in ("A", "B", "C", "D", "E", "K"):
        npt.assert_allclose(forward.snapshot()["model"][name], reversed_.snapshot()["model"][name], atol=1e-12)


def test_fitted_model_preserves_the_learned_input_operating_point() -> None:
    """Centering errors must not turn a held command into a large forecast drift."""
    state = 0.0
    frames: list[FrameObservation] = []
    previous_q = 0.0
    for index, frame in enumerate(_frames(order=1, count=160)):
        q = 0.15 + 0.7 * ((index * 7) % 23) / 22 if index < 100 else 0.55
        state = 0.82 * state + 0.28 * previous_q
        frames.append(
            replace(
                frame,
                temp_c=20.0 + state + 0.02 * float(np.sin(index * 0.71)),
                requested_q=q,
                baseline_q=q,
                realized_q=q,
                requested_auger_duty=q,
                delivered_on_s=q * 20.0,
            )
        )
        previous_q = q
    model = InnovationStateSpace(_config(orders=(1,), delays=(1,)))
    assert model.fit(tuple(frames)).accepted

    prediction = model.forecast(
        tuple(frames),
        np.full(20, 0.55, dtype=np.float64),
        np.full(20, 20.0, dtype=np.float64),
    )

    assert max(abs(prediction - frames[-1].temp_c)) < 0.25


def _negative_gain_frames(*, count: int = 96) -> tuple[FrameObservation, ...]:
    """Build an otherwise-identifiable record whose command lowers temperature."""
    state = 0.0
    frames: list[FrameObservation] = []
    previous_q = 0.0
    for frame in _frames(order=1, count=count):
        state = 0.82 * state - 0.28 * previous_q
        frames.append(replace(frame, temp_c=20.0 + state))
        previous_q = frame.realized_q
    return tuple(frames)


def test_negative_identified_gain_is_rejected_without_reversing_an_incumbent() -> None:
    """Flipping a negative input vector into a positive candidate must fail here."""
    model = InnovationStateSpace(_config(orders=(1,), delays=(1,)))
    assert model.fit(_frames(order=1)).accepted
    before = model.snapshot()

    diagnostics = model.refresh(_negative_gain_frames())

    assert not diagnostics.accepted
    assert any(RefreshRejectionReason.IMPLAUSIBLE_GAIN in attempt.rejection_reasons for attempt in diagnostics.attempts)
    assert model.snapshot() == before


def test_constant_refresh_is_typed_and_transactional() -> None:
    """Replacing an incumbent when every candidate is rank deficient must fail here."""
    model = InnovationStateSpace(_config())
    model.fit(_frames(order=1))
    before = json.dumps(model.snapshot(), allow_nan=False, sort_keys=True)
    constant = tuple(
        replace(
            frame,
            temp_c=20.0,
            requested_q=0.4,
            baseline_q=0.4,
            realized_q=0.4,
            requested_auger_duty=0.4,
            delivered_on_s=8.0,
        )
        for frame in _frames(order=1, count=48)
    )

    diagnostics = model.refresh(constant)

    assert not diagnostics.accepted
    assert diagnostics.terminal_reason is RefreshRejectionReason.NO_VALID_CANDIDATE
    assert len(diagnostics.attempts) == 4
    assert all(attempt.rejection_reasons for attempt in diagnostics.attempts)
    assert json.dumps(model.snapshot(), allow_nan=False, sort_keys=True) == before


def test_accepted_model_is_physically_bounded_and_snapshot_is_finite() -> None:
    """Skipping projection or covariance checks must fail this accepted-model contract."""
    model = InnovationStateSpace(_config())
    diagnostics = model.fit(_frames(order=2))
    snapshot = model.snapshot()

    assert diagnostics.accepted
    assert all(attempt.elapsed_ms >= 0.0 for attempt in diagnostics.attempts)
    assert np.isfinite(np.asarray(snapshot["model"]["A"], dtype=np.float64)).all()
    assert np.isfinite(np.asarray(snapshot["model"]["B"], dtype=np.float64)).all()
    assert np.isfinite(np.asarray(snapshot["model"]["C"], dtype=np.float64)).all()
    assert np.isfinite(np.asarray(snapshot["model"]["D"], dtype=np.float64)).all()
    assert np.isfinite(np.asarray(snapshot["model"]["E"], dtype=np.float64)).all()
    assert np.isfinite(np.asarray(snapshot["model"]["K"], dtype=np.float64)).all()
    assert max(snapshot["model"]["poles"]) < model.config.max_pole_magnitude
    assert 0.0 < snapshot["model"]["steady_gain"] <= snapshot["bounds"]["max_steady_gain_c_per_q"]
    covariance = np.asarray(snapshot["state_covariance"], dtype=np.float64)
    assert np.linalg.eigvalsh(covariance).min() >= -1e-12
    prediction = model.affine_prediction(6, 0.4, np.full(6, 20.0))
    assert np.isfinite(prediction.free_output_c).all()
    assert np.isfinite(prediction.input_response_c).all()
    assert json.dumps(snapshot, allow_nan=False)


def test_state_space_rejects_oversized_affine_horizon_before_response_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unbounded horizon must not reach the quadratic response workspace."""
    model = InnovationStateSpace(_config(orders=(2,), delays=(1,)))
    assert model.fit(_frames(order=2)).accepted
    monkeypatch.setattr(
        "controller.linear_mpc.state_space.np.zeros",
        lambda *_args, **_kwargs: pytest.fail("oversized horizon allocated a response workspace"),
    )

    with pytest.raises(ValueError, match="horizon_steps must not exceed 180"):
        model.affine_prediction(181, 0.4, np.full(181, 20.0))


def test_state_space_rejects_oversized_buffer_in_construction_and_restore() -> None:
    """Configured history must remain bounded on both direct and persisted inputs."""
    with pytest.raises(ValueError, match="max_buffer_samples must not exceed 1800"):
        StateSpaceConfig(orders=(1,), delays=(1,), block_rows=2, max_buffer_samples=1_801)

    model = InnovationStateSpace(_config(orders=(2,), delays=(1,)))
    assert model.fit(_frames(order=2)).accepted
    snapshot = json.loads(json.dumps(model.snapshot(), allow_nan=False))
    snapshot["config"]["max_buffer_samples"] = 1_801

    with pytest.raises(ValueError, match="max_buffer_samples must not exceed 1800"):
        InnovationStateSpace.from_snapshot(snapshot)


def test_track_assimilates_measurement_without_claiming_parameter_learning() -> None:
    """Dropping track's Kalman correction or reporting it as learning must fail here."""
    frames = _frames(order=1, count=98)
    seed = InnovationStateSpace(_config(orders=(1,), delays=(1,)))
    assert seed.fit(frames[:96]).accepted
    observation = replace(frames[96], temp_c=frames[96].temp_c + 1.0)
    tracking = InnovationStateSpace.from_snapshot(seed.snapshot())
    observing = InnovationStateSpace.from_snapshot(seed.snapshot())

    tracked = tracking.track(observation)
    learned = observing.observe(observation)

    assert not tracked.updated
    assert learned.updated
    npt.assert_allclose(tracking._state, observing._state, atol=1e-12, rtol=1e-12)
    npt.assert_allclose(tracking._state_covariance, observing._state_covariance, atol=1e-12, rtol=1e-12)


def test_snapshot_current_output_uses_the_current_frame_for_direct_feedthrough() -> None:
    """Indexing D from the next slot rather than the current frame must fail here."""
    model = InnovationStateSpace(_config(orders=(1,), delays=(1,)))
    assert model.fit(_frames(order=1)).accepted
    realization = model._require_model()
    direct = replace(realization, D=np.array([0.37], dtype=np.float64))
    model._model = direct

    snapshot = model.snapshot()

    current_q = model._inputs[len(model._inputs) - 1 - direct.delay]
    next_q = model._inputs[len(model._inputs) - direct.delay]
    expected = float(model._ambients[-1] + direct.C @ model._state + direct.D[0] * (current_q - direct.input_mean))
    incorrect_next_index = float(
        model._ambients[-1] + direct.C @ model._state + direct.D[0] * (next_q - direct.input_mean)
    )

    assert snapshot["status"]["state_output_c"] == pytest.approx(expected)
    assert not np.isclose(expected, incorrect_next_index)


def test_accepted_refresh_records_state_alignment_evidence() -> None:
    """A valid refresh must install atomically and retain measured continuity evidence."""
    frames = _alignment_frames(count=120)
    model = InnovationStateSpace(_config(orders=(2,), delays=(1,)))
    assert model.fit(frames[:96]).accepted

    diagnostics = model.refresh(frames[:96])

    assert diagnostics.accepted
    selected = next(
        attempt
        for attempt in diagnostics.attempts
        if (attempt.order, attempt.delay) == (diagnostics.selected_order, diagnostics.selected_delay)
    )
    assert selected.alignment_error_c is not None
    assert selected.alignment_error_c <= 2.0
    assert model.snapshot()["diagnostics"]["accepted"] is True


def test_later_window_refresh_rejects_non_equivalent_dynamics_transactionally() -> None:
    """Continuity at one sample cannot install a dynamics-changing replacement."""
    frames = _later_window_frames()
    model = InnovationStateSpace(replace(_config(orders=(2,), delays=(1,)), refresh_interval_s=10_000.0))
    assert model.fit(frames[:96]).accepted
    for frame in frames[96:]:
        model.observe(frame)
    before = model.snapshot()

    diagnostics = model.refresh(model._history_frames())

    assert not diagnostics.accepted
    assert diagnostics.terminal_reason is RefreshRejectionReason.ALIGNMENT_FAILED
    assert model.snapshot() == before


def test_refresh_maps_equal_order_similarity_into_incumbent_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An equivalent replacement must retain the incumbent's exact coordinates."""
    frames = _alignment_frames()
    incumbent = InnovationStateSpace(_config(orders=(2,), delays=(1,)))
    assert incumbent.fit(frames).accepted
    before = incumbent.snapshot()
    transform = np.array([[1.0, 0.25], [-0.2, 1.0]], dtype=np.float64)
    candidate = InnovationStateSpace.from_snapshot(_similarity_transformed_snapshot(before, transform))
    _inject_refresh_candidate(monkeypatch, incumbent, candidate)

    diagnostics = incumbent.refresh(frames)

    assert diagnostics.accepted
    selected = next(
        attempt
        for attempt in diagnostics.attempts
        if (attempt.order, attempt.delay) == (diagnostics.selected_order, diagnostics.selected_delay)
    )
    assert selected.alignment_error_c is not None
    assert selected.alignment_error_c <= 2.0
    npt.assert_allclose(incumbent._state, np.asarray(before["state"], dtype=np.float64), atol=1e-10, rtol=1e-10)
    npt.assert_allclose(
        incumbent._state_covariance,
        np.asarray(before["state_covariance"], dtype=np.float64),
        atol=1e-10,
        rtol=1e-10,
    )
    for name in ("A", "B", "C", "D", "E", "K", "process_covariance"):
        npt.assert_allclose(
            incumbent.snapshot()["model"][name],
            before["model"][name],
            atol=1e-10,
            rtol=1e-10,
        )


def test_equivalent_refresh_carries_posterior_filter_state_into_next_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equivalent replacement must preserve the incumbent's posterior Kalman continuation."""
    frames = _alignment_frames(count=120)
    incumbent = InnovationStateSpace(replace(_config(orders=(2,), delays=(1,)), refresh_interval_s=10_000.0))
    assert incumbent.fit(frames[:96]).accepted
    for frame in frames[96:108]:
        _ = incumbent.observe(frame)
    before = incumbent.snapshot()
    control = InnovationStateSpace.from_snapshot(before)
    transform = np.array([[1.0, 0.25], [-0.2, 1.0]], dtype=np.float64)
    candidate = InnovationStateSpace.from_snapshot(_similarity_transformed_snapshot(before, transform))
    _inject_refresh_candidate(monkeypatch, incumbent, candidate)

    diagnostics = incumbent.refresh(incumbent._history_frames())

    assert diagnostics.accepted
    npt.assert_allclose(incumbent._state, control._state, atol=1e-12, rtol=1e-12)
    npt.assert_allclose(
        incumbent._state_covariance,
        control._state_covariance,
        atol=1e-12,
        rtol=1e-12,
    )
    expected_update = control.observe(frames[108])
    actual_update = incumbent.observe(frames[108])
    npt.assert_allclose(
        (actual_update.predicted_temp_c, actual_update.innovation_c),
        (expected_update.predicted_temp_c, expected_update.innovation_c),
        atol=1e-12,
        rtol=1e-12,
    )
    assert actual_update.observed_temp_c == expected_update.observed_temp_c
    assert actual_update.updated is expected_update.updated
    npt.assert_allclose(incumbent._state, control._state, atol=1e-12, rtol=1e-12)
    npt.assert_allclose(
        incumbent._state_covariance,
        control._state_covariance,
        atol=1e-12,
        rtol=1e-12,
    )


def test_refresh_rejects_order_change_transactionally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replacement with another state dimension has no exact fixed-horizon map."""
    frames = _alignment_frames()
    incumbent = InnovationStateSpace(_config(orders=(2,), delays=(1,)))
    candidate_source = InnovationStateSpace(_config(orders=(1,), delays=(1,)))
    assert incumbent.fit(frames).accepted
    assert candidate_source.fit(frames).accepted
    before = incumbent.snapshot()
    _inject_refresh_candidate(monkeypatch, incumbent, candidate_source)

    diagnostics = incumbent.refresh(frames)

    assert not diagnostics.accepted
    assert diagnostics.terminal_reason is RefreshRejectionReason.ALIGNMENT_FAILED
    assert incumbent.snapshot() == before


def test_refresh_rejects_same_order_non_equivalent_dynamics_transactionally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sub-2 C present residual cannot mask a divergent future realization."""
    frames = _alignment_frames()
    incumbent = InnovationStateSpace(_config(orders=(2,), delays=(1,)))
    assert incumbent.fit(frames).accepted
    candidate_source = InnovationStateSpace.from_snapshot(incumbent.snapshot())
    candidate_source._model = replace(candidate_source._require_model(), E=candidate_source._require_model().E + 1e-4)
    before = incumbent.snapshot()
    _inject_refresh_candidate(monkeypatch, incumbent, candidate_source)

    diagnostics = incumbent.refresh(frames)

    assert not diagnostics.accepted
    assert diagnostics.terminal_reason is RefreshRejectionReason.ALIGNMENT_FAILED
    assert incumbent.snapshot() == before


@pytest.mark.parametrize("failure", ("timestamp", "output-jump"))
def test_refresh_rejects_mismatched_or_discontinuous_candidate_transactionally(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Refresh candidates must be evaluated at the incumbent's terminal frame."""
    frames = _alignment_frames()
    incumbent = InnovationStateSpace(_config(orders=(2,), delays=(1,)))
    candidate_source = InnovationStateSpace(_config(orders=(2,), delays=(1,)))
    assert incumbent.fit(frames).accepted
    assert candidate_source.fit(frames).accepted
    before = incumbent.snapshot()
    if failure == "output-jump":
        candidate_source._model = replace(
            candidate_source._require_model(), E=candidate_source._require_model().E + 10.0
        )
    _inject_refresh_candidate(monkeypatch, incumbent, candidate_source)

    diagnostics = incumbent.refresh(frames[:-1] if failure == "timestamp" else frames)

    assert not diagnostics.accepted
    assert diagnostics.terminal_reason is RefreshRejectionReason.ALIGNMENT_FAILED
    assert incumbent.snapshot() == before


def test_rejected_periodic_refresh_waits_for_the_next_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected fit must not make every subsequent frame pay the SVD cost."""
    frames = _frames(order=1)
    model = InnovationStateSpace(_config(orders=(1,), delays=(1,)))
    assert model.fit(frames).accepted
    attempts = 0

    def reject(_observations: tuple[FrameObservation, ...]) -> RefreshDiagnostics:
        nonlocal attempts
        attempts += 1
        return RefreshDiagnostics(
            False,
            RefreshRejectionReason.INSUFFICIENT_SAMPLES,
            (),
        )

    monkeypatch.setattr(model, "refresh", reject)
    for revision, end_s in enumerate((1940.0, 1960.0, 1980.0, 2000.0, 2020.0, 2040.0), start=1):
        model.observe(
            replace(
                frames[-1],
                frame_start_s=end_s - 20.0,
                frame_end_s=end_s,
                result_revision=revision,
            )
        )

    assert attempts == 2
    assert model.diagnostics.terminal_reason is RefreshRejectionReason.INSUFFICIENT_SAMPLES


def test_unstable_record_is_projected_or_typed_rejected() -> None:
    """Leaving a fitted unstable pole unchecked must fail this safety boundary."""
    frames = tuple(
        replace(frame, temp_c=float(20.0 + 0.1 * 1.08**index)) for index, frame in enumerate(_frames(order=1))
    )
    model = InnovationStateSpace(_config(orders=(1,), delays=(1,)))

    diagnostics = model.fit(frames)

    if diagnostics.accepted:
        assert max(model.snapshot()["model"]["poles"]) < model.config.max_pole_magnitude
    else:
        assert diagnostics.terminal_reason is RefreshRejectionReason.NO_VALID_CANDIDATE
        assert all(attempt.rejection_reasons for attempt in diagnostics.attempts)


def test_invalid_diagnostic_records_are_immutable() -> None:
    """Allowing post-refresh evidence mutation must fail this auditability contract."""
    model = InnovationStateSpace(_config())
    model.fit(_frames(order=1))
    diagnostics = model.refresh(tuple())

    assert diagnostics.terminal_reason is RefreshRejectionReason.INSUFFICIENT_SAMPLES
    with pytest.raises(FrozenInstanceError):
        diagnostics.accepted = True  # type: ignore[misc]


def _alignment_frames(*, count: int = 96, change_at: int | None = None) -> tuple[FrameObservation, ...]:
    """Build an identifiable positive-gain ARX record for alignment and restore tests."""
    rng = np.random.default_rng(44)
    temperatures = [10.0, 11.0]
    inputs: list[float] = []
    frames: list[FrameObservation] = []
    for index, frame in enumerate(_frames(order=2, count=count)):
        q = 0.1 + 0.8 * ((index * 7) % 23) / 22
        inputs.append(q)
        if index >= 2:
            changed = change_at is not None and index >= change_at
            a1, a2 = (0.55, 0.12) if changed else (1.15, -0.32)
            b1, b2 = (9.0, -1.0) if changed else (4.0, 1.5)
            temperatures.append(
                a1 * temperatures[-1]
                + a2 * temperatures[-2]
                + b1 * (inputs[index - 1] - 0.5)
                + b2 * (inputs[index - 2] - 0.5)
                + 2.0
                + float(rng.normal(0.0, 0.05))
            )
        frames.append(
            replace(
                frame,
                temp_c=20.0 + temperatures[index],
                requested_q=q,
                baseline_q=q,
                realized_q=q,
                requested_auger_duty=q,
                delivered_on_s=q * 20.0,
            )
        )
    return tuple(frames)


def _later_window_frames() -> tuple[FrameObservation, ...]:
    """Build a continuous trajectory whose dynamics change after the fitted window."""
    return _alignment_frames(count=120, change_at=96)


def _inject_refresh_candidate(
    monkeypatch: pytest.MonkeyPatch,
    incumbent: InnovationStateSpace,
    source: InnovationStateSpace,
) -> None:
    """Replace fitting only to isolate refresh-alignment acceptance boundaries."""
    candidate = source._require_model()
    assert source._bounds is not None
    diagnostics = RefreshDiagnostics(
        True,
        None,
        source.diagnostics.attempts,
        candidate.order,
        candidate.delay,
    )
    monkeypatch.setattr(
        incumbent,
        "_identify",
        lambda _frames: (diagnostics, candidate, source._bounds),
    )


def _similarity_transformed_snapshot(snapshot: dict[str, object], transform: np.ndarray) -> dict[str, object]:
    """Express a snapshot in coordinates ``x_source = transform @ x_target``."""
    result = json.loads(json.dumps(snapshot, allow_nan=False))
    model = result["model"]
    inverse = np.linalg.inv(transform)
    for name in ("A", "process_covariance"):
        matrix = np.asarray(model[name], dtype=np.float64)
        model[name] = (inverse @ matrix @ transform if name == "A" else inverse @ matrix @ inverse.T).tolist()
    model["poles"] = [float(abs(value)) for value in np.linalg.eigvals(np.asarray(model["A"], dtype=np.float64))]
    for name in ("B", "E", "K"):
        model[name] = (inverse @ np.asarray(model[name], dtype=np.float64)).tolist()
    model["C"] = (np.asarray(model["C"], dtype=np.float64) @ transform).tolist()
    result["state"] = (inverse @ np.asarray(result["state"], dtype=np.float64)).tolist()
    covariance = np.asarray(result["state_covariance"], dtype=np.float64)
    result["state_covariance"] = (inverse @ covariance @ inverse.T).tolist()
    return result


def test_similarity_alignment_returns_owned_equivalent_realization_without_mutation() -> None:
    """A coordinate change must preserve output while leaving both arms untouched."""
    incumbent = InnovationStateSpace(_config(orders=(2,), delays=(1,)))
    assert incumbent.fit(_alignment_frames()).accepted
    before = incumbent.snapshot()
    transform = np.array([[1.0, 0.25], [-0.2, 1.0]], dtype=np.float64)
    challenger = InnovationStateSpace.from_snapshot(_similarity_transformed_snapshot(before, transform))
    challenger_before = challenger.snapshot()

    result = challenger.align_to(incumbent)

    assert isinstance(result, AlignmentResult)
    assert not result.transform.flags.writeable
    assert not result.aligned_state.flags.writeable
    assert result.output_error_c <= 1e-10
    npt.assert_allclose(result.transform @ challenger._state, incumbent._state, atol=1e-10)
    assert incumbent.snapshot() == before
    assert challenger.snapshot() == challenger_before


@pytest.mark.parametrize("mutation", ("non-equivalent", "rank-deficient", "non-finite"))
def test_invalid_similarity_alignment_is_transactional(mutation: str) -> None:
    """A bad realization must reject alignment without modifying either arm."""
    incumbent = InnovationStateSpace(_config(orders=(2,), delays=(1,)))
    assert incumbent.fit(_alignment_frames()).accepted
    challenger = InnovationStateSpace.from_snapshot(incumbent.snapshot())
    incumbent_before = incumbent.snapshot()
    if mutation == "non-equivalent":
        challenger._model = replace(challenger._require_model(), E=challenger._require_model().E + 1.0)
    elif mutation == "rank-deficient":
        challenger._model = replace(challenger._require_model(), C=np.zeros(2, dtype=np.float64))
    else:
        challenger._model = replace(challenger._require_model(), A=np.full((2, 2), np.nan))
    challenger_before = None if mutation == "non-finite" else challenger.snapshot()

    assert challenger.align_to(incumbent) is None
    assert incumbent.snapshot() == incumbent_before
    if challenger_before is not None:
        assert challenger.snapshot() == challenger_before


def test_snapshot_round_trip_preserves_full_filter_state_and_next_update() -> None:
    """Restoration must retain the exact affine and Kalman continuation."""
    frames = _alignment_frames(count=98)
    source = InnovationStateSpace(_config(orders=(2,), delays=(1,)))
    assert source.fit(frames[:96]).accepted
    source.observe(frames[96])
    snapshot = source.snapshot()
    restored = InnovationStateSpace.from_snapshot(snapshot)

    assert restored.snapshot() == snapshot
    assert len(json.dumps(snapshot, allow_nan=False).encode()) < 65_536
    expected = source.affine_prediction(6, frames[96].realized_q, np.full(6, frames[96].ambient_c))
    actual = restored.affine_prediction(6, frames[96].realized_q, np.full(6, frames[96].ambient_c))
    npt.assert_allclose(actual.free_output_c, expected.free_output_c, atol=1e-12, rtol=1e-12)
    npt.assert_allclose(actual.input_response_c, expected.input_response_c, atol=1e-12, rtol=1e-12)
    source_update = source.observe(frames[97])
    restored_update = restored.observe(frames[97])
    npt.assert_allclose(
        (
            restored_update.predicted_temp_c,
            restored_update.observed_temp_c,
            restored_update.innovation_c,
        ),
        (
            source_update.predicted_temp_c,
            source_update.observed_temp_c,
            source_update.innovation_c,
        ),
        atol=1e-12,
        rtol=1e-12,
    )
    assert restored_update.updated is source_update.updated


def test_active_snapshot_and_refreshed_challenger_have_independent_parameter_ownership() -> None:
    """Post-activation tracking must not let challenger refresh mutate the incumbent."""
    frames = _alignment_frames(count=98)
    fitted = InnovationStateSpace(_config(orders=(2,), delays=(1,)))
    assert fitted.fit(frames[:96]).accepted
    snapshot = fitted.snapshot()
    incumbent = InnovationStateSpace.from_snapshot(snapshot)
    challenger = InnovationStateSpace.from_snapshot(snapshot)
    incumbent_parameters = deepcopy(incumbent.snapshot()["model"])

    incumbent.track(frames[96])
    challenger.observe(frames[96])
    challenger.observe(frames[97])

    assert incumbent is not challenger
    assert incumbent.snapshot()["model"] == incumbent_parameters


@pytest.mark.parametrize("corruption", ("dimension", "covariance", "pole", "gain", "state"))
def test_snapshot_rejects_corrupt_realization_members(corruption: str) -> None:
    """No malformed matrix, covariance, gain, pole, or state may restore."""
    model = InnovationStateSpace(_config(orders=(2,), delays=(1,)))
    assert model.fit(_alignment_frames()).accepted
    snapshot = json.loads(json.dumps(model.snapshot(), allow_nan=False))
    if corruption == "dimension":
        snapshot["model"]["A"] = [[0.0]]
    elif corruption == "covariance":
        snapshot["model"]["process_covariance"][0][0] = -1.0
    elif corruption == "pole":
        snapshot["model"]["A"][0][0] = 1.0
    elif corruption == "gain":
        snapshot["model"]["K"][0] = float("inf")
    else:
        snapshot["state"][0] += 1.0

    with pytest.raises(ValueError):
        InnovationStateSpace.from_snapshot(snapshot)


@pytest.mark.parametrize(
    "kwargs",
    (
        {"orders": (9,), "delays": (1,)},
        {"orders": (1,), "delays": (31,)},
        {"orders": (1,), "delays": (1,), "block_rows": 33},
        {"orders": tuple(range(1, 9)), "delays": tuple(range(1, 10)), "block_rows": 8},
    ),
)
def test_configuration_rejects_unbounded_identification_grids(kwargs: dict[str, object]) -> None:
    """Production identification cannot allocate beyond its fixed candidate and Hankel caps."""
    with pytest.raises(ValueError):
        StateSpaceConfig(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "corruption", ("zero-process", "zero-state", "oversized-state", "inconsistent-k", "truncated-lag")
)
def test_snapshot_rejects_strict_filter_and_lag_corruption(corruption: str) -> None:
    """Stored covariance, Kalman gain, and synchronized lag are reconstructible facts."""
    source = InnovationStateSpace(_config(orders=(2,), delays=(1,)))
    assert source.fit(_alignment_frames()).accepted
    snapshot = json.loads(json.dumps(source.snapshot(), allow_nan=False))
    if corruption == "zero-process":
        snapshot["model"]["process_covariance"][0][0] = 0.0
    elif corruption == "zero-state":
        snapshot["state_covariance"][0][0] = 0.0
    elif corruption == "oversized-state":
        snapshot["state_covariance"][0][0] = source.config.covariance_ceiling * 2.0
    elif corruption == "inconsistent-k":
        snapshot["model"]["K"][0] += 0.1
    else:
        for values in snapshot["record"]["lag"].values():
            values.pop(0)

    with pytest.raises(ValueError):
        InnovationStateSpace.from_snapshot(snapshot)


def test_identification_uses_only_the_newest_bounded_window() -> None:
    """Caller replay cannot influence a bounded fit through samples older than its history cap."""
    frames = _alignment_frames(count=160)
    replay = InnovationStateSpace(_config(orders=(2,), delays=(1,)))
    window = InnovationStateSpace(_config(orders=(2,), delays=(1,)))

    replay_diagnostics = replay.fit(frames)
    window_diagnostics = window.fit(frames[-window.config.max_buffer_samples :])

    assert replay_diagnostics.selected_order == window_diagnostics.selected_order
    assert replay_diagnostics.selected_delay == window_diagnostics.selected_delay
    assert replay.snapshot()["model"] == window.snapshot()["model"]
    assert replay.snapshot()["state"] == window.snapshot()["state"]


def test_irregular_frames_fail_before_mutating_an_installed_filter() -> None:
    """Fit, refresh, forecast, and observe consume only contiguous complete 20-second frames."""
    frames = _alignment_frames(count=98)
    malformed = list(frames)
    malformed[40] = replace(
        malformed[40],
        frame_start_s=malformed[39].frame_end_s + 1.0,
        frame_end_s=malformed[40].frame_end_s + 1.0,
    )
    malformed_frames = tuple(malformed)
    model = InnovationStateSpace(_config(orders=(2,), delays=(1,)))
    assert model.fit(frames[:96]).accepted
    before = json.dumps(model.snapshot(), allow_nan=False, sort_keys=True)

    with pytest.raises(ValueError):
        InnovationStateSpace(_config(orders=(2,), delays=(1,))).fit(malformed_frames)
    with pytest.raises(ValueError):
        model.refresh(malformed_frames)
    with pytest.raises(ValueError):
        model.forecast(malformed_frames, np.array([0.4]), np.array([20.0]))
    with pytest.raises(ValueError):
        model.observe(
            replace(frames[96], frame_start_s=frames[96].frame_start_s + 1.0, frame_end_s=frames[96].frame_end_s + 1.0)
        )

    assert json.dumps(model.snapshot(), allow_nan=False, sort_keys=True) == before
