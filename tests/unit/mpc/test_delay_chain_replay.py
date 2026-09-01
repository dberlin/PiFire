from __future__ import annotations

from math import inf, nan

import numpy as np
import pytest
from scipy.linalg import expm

from common.learning_trajectory import FrameDeliveryCertainty, LearningTrajectoryFrame
from controller.mpc_model import replay_delay_chain

_FRAME_MS = 20_000
_WALL_OFFSET_MS = 1_700_000_000_000


def _frame(
    sequence: int,
    load: float,
    *,
    start_ms: int | None = None,
    temperature_c: float = 110.0,
    certainty: FrameDeliveryCertainty = FrameDeliveryCertainty.EXACT,
) -> LearningTrajectoryFrame:
    monotonic_start_ms = sequence * _FRAME_MS if start_ms is None else start_ms
    monotonic_end_ms = monotonic_start_ms + _FRAME_MS
    wall_start_ms = _WALL_OFFSET_MS + monotonic_start_ms
    wall_end_ms = _WALL_OFFSET_MS + monotonic_end_ms
    duration_s = _FRAME_MS / 1_000
    delivered_auger_s = load * duration_s
    return LearningTrajectoryFrame(
        sequence=sequence,
        monotonic_start_ms=monotonic_start_ms,
        monotonic_end_ms=monotonic_end_ms,
        wall_start_ms=wall_start_ms,
        wall_end_ms=wall_end_ms,
        chamber_temperature_c=temperature_c,
        temperature_sample_monotonic_ms=monotonic_end_ms,
        temperature_sample_wall_ms=wall_end_ms,
        temperature_sample_age_ms=0,
        temperature_sample_wall_age_ms=0,
        temperature_sample_clock_skew_ms=0,
        source_temperature_units="C",
        settings_revision=1,
        probe_valid=True,
        probe_source="test-probe",
        ambient_temperature_c=20.0,
        ambient_source="configured",
        ambient_uncertainty_c=1.0,
        delivered_auger_on_seconds=delivered_auger_s,
        realized_auger_duty=load,
        normalized_combustion_load=load,
        delivered_fan_on_seconds=duration_s,
        fan_duty_integral_seconds=duration_s * 0.5,
        mean_actual_fan_duty=0.5,
        auger_delivery_certainty=certainty,
        fan_delivery_certainty=FrameDeliveryCertainty.EXACT,
        effective_mode="Smoke",
        recipe_step_id=None,
        complete=True,
        continuous=True,
        partial=False,
        boundary_reason=None,
    )


def _direct_state_space_replay(
    loads: tuple[float, ...],
    *,
    theta: float,
    n_delay: int,
    initial_load: float,
) -> tuple[float, ...]:
    """Independent affine state-space exponential, not Erlang coefficients."""
    if n_delay == 0:
        return ()
    rate = n_delay / theta
    states = np.full(n_delay, initial_load, dtype=float)
    for load in loads:
        generator = np.zeros((n_delay + 1, n_delay + 1), dtype=float)
        generator[:n_delay, :n_delay] -= np.eye(n_delay) * rate
        for index in range(1, n_delay):
            generator[index, index - 1] = rate
        generator[0, -1] = rate * load
        augmented = np.concatenate((states, np.ones(1)))
        states = (expm(generator * (_FRAME_MS / 1_000)) @ augmented)[:n_delay]
    return tuple(float(value) for value in states)


@pytest.mark.parametrize(
    ("loads", "theta", "n_delay", "initial_load"),
    (
        pytest.param((0.65, 0.65, 0.65), 60.0, 4, 0.1, id="constant"),
        pytest.param((0.1, 0.1, 0.8, 0.8), 85.0, 8, 0.2, id="step"),
        pytest.param((0.0, 0.9, 0.0, 0.4, 0.0), 47.5, 5, 0.35, id="pulse"),
    ),
)
def test_replay_matches_independent_affine_erlang_solution_for_constant_step_and_pulse(
    loads: tuple[float, ...],
    theta: float,
    n_delay: int,
    initial_load: float,
) -> None:
    intervals = tuple(_frame(index, load) for index, load in enumerate(loads))

    actual = replay_delay_chain(
        intervals,
        theta=theta,
        n_delay=n_delay,
        initial_load=initial_load,
    )
    expected = _direct_state_space_replay(
        loads,
        theta=theta,
        n_delay=n_delay,
        initial_load=initial_load,
    )

    np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-12)


def test_one_stage_replay_has_the_hand_calculated_unit_time_constant_response() -> None:
    actual = replay_delay_chain(
        (_frame(0, 1.0),),
        theta=20.0,
        n_delay=1,
        initial_load=0.0,
    )

    assert actual == pytest.approx((0.6321205588285577,), rel=1e-13, abs=1e-13)


@pytest.mark.parametrize(
    ("theta", "n_delay", "initial_load"),
    (
        pytest.param(0.0, 8, 0.5, id="zero-theta"),
        pytest.param(-1.0, 8, 0.5, id="negative-theta"),
        pytest.param(inf, 8, 0.5, id="infinite-theta"),
        pytest.param(nan, 8, 0.5, id="nan-theta"),
        pytest.param(60.0, -1, 0.5, id="negative-delay-count"),
        pytest.param(60.0, 8, -0.01, id="negative-initial-load"),
        pytest.param(60.0, 8, 1.01, id="oversized-initial-load"),
        pytest.param(60.0, 8, inf, id="infinite-initial-load"),
        pytest.param(60.0, 8, nan, id="nan-initial-load"),
    ),
)
def test_replay_rejects_nonphysical_model_and_initial_load_values(
    theta: float,
    n_delay: int,
    initial_load: float,
) -> None:
    with pytest.raises(ValueError):
        replay_delay_chain(
            (_frame(0, 0.5),),
            theta=theta,
            n_delay=n_delay,
            initial_load=initial_load,
        )


def test_zero_delay_chain_returns_empty_without_requiring_positive_theta() -> None:
    assert replay_delay_chain(
        (_frame(0, 0.5),),
        theta=0.0,
        n_delay=0,
        initial_load=0.5,
    ) == ()


@pytest.mark.parametrize(
    "bad_load",
    (-0.01, 1.01, inf, nan),
    ids=("negative", "oversized", "infinite", "nan"),
)
def test_replay_revalidates_normalized_load_at_its_boundary(bad_load: float) -> None:
    corrupted = _frame(0, 0.5)
    object.__setattr__(corrupted, "normalized_combustion_load", bad_load)

    with pytest.raises(ValueError):
        replay_delay_chain(
            (corrupted,),
            theta=60.0,
            n_delay=8,
            initial_load=0.5,
        )


def test_replay_rejects_nonpositive_overlapping_or_reverse_chronology() -> None:
    nonpositive = _frame(0, 0.5)
    object.__setattr__(
        nonpositive,
        "monotonic_end_ms",
        nonpositive.monotonic_start_ms,
    )
    with pytest.raises(ValueError):
        replay_delay_chain(
            (nonpositive,),
            theta=60.0,
            n_delay=8,
            initial_load=0.5,
        )

    first = _frame(0, 0.25, start_ms=0)
    overlapping = _frame(1, 0.75, start_ms=10_000)
    reverse = _frame(2, 0.5, start_ms=40_000)
    earlier = _frame(3, 0.5, start_ms=20_000)
    for intervals in ((first, overlapping), (reverse, earlier)):
        with pytest.raises(ValueError):
            replay_delay_chain(
                intervals,
                theta=60.0,
                n_delay=8,
                initial_load=0.5,
            )


def test_replay_rejects_delivery_that_is_not_exact() -> None:
    uncertain = _frame(
        0,
        0.5,
        certainty=FrameDeliveryCertainty.UNKNOWN,
    )

    with pytest.raises(ValueError):
        replay_delay_chain(
            (uncertain,),
            theta=60.0,
            n_delay=8,
            initial_load=0.5,
        )


def test_replay_does_not_read_frame_temperatures() -> None:
    loads = (0.1, 0.8, 0.3)
    cool = tuple(
        _frame(index, load, temperature_c=40.0 + index)
        for index, load in enumerate(loads)
    )
    hot = tuple(
        _frame(index, load, temperature_c=400.0 - index)
        for index, load in enumerate(loads)
    )

    assert replay_delay_chain(
        cool,
        theta=75.0,
        n_delay=8,
        initial_load=0.1,
    ) == replay_delay_chain(
        hot,
        theta=75.0,
        n_delay=8,
        initial_load=0.1,
    )
