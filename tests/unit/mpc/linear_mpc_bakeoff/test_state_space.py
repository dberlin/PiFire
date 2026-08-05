"""Contracts for the adaptive innovation state-space model."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest

from docs.superpowers.experiments.linear_mpc_bakeoff.contracts import SignalRecord
from docs.superpowers.experiments.linear_mpc_bakeoff.data import chronological_split
from docs.superpowers.experiments.linear_mpc_bakeoff.state_space import (
    InnovationStateSpace,
    StateSpaceConfig,
    subspace_fit,
)


def known_state_space(*, seed: int) -> Callable[[int], SignalRecord]:
    """Return deterministic SISO records from a stable delayed order-two plant."""

    def record(samples: int) -> SignalRecord:
        rng = np.random.default_rng(seed)
        time_s = np.arange(samples, dtype=np.float64) * 20.0
        q = rng.choice(np.array([0.05, 0.2, 0.45, 0.75]), size=samples)
        ambient = 20.0 + 1.5 * np.sin(time_s / 1_400.0)
        state = np.zeros(2, dtype=np.float64)
        output = np.empty(samples, dtype=np.float64)
        delayed_q = np.pad(q, (2, 0))
        a = np.array([[0.74, -0.18], [1.0, 0.0]])
        b = np.array([0.9, 0.0])
        for index in range(samples):
            output[index] = ambient[index] + state[0] + rng.normal(0.0, 0.015)
            state = a @ state + b * delayed_q[index]
        return SignalRecord(time_s, output, q, ambient, "known-state-space")

    return record


def free_run_rmse(model: InnovationStateSpace, record: SignalRecord) -> float:
    split_at = 40
    forecast = model.forecast(
        SignalRecord(
            record.time_s[:split_at],
            record.temp_c[:split_at],
            record.q[:split_at],
            record.ambient_c[:split_at],
            record.provenance,
        ),
        record.q[split_at:],
        record.ambient_c[split_at:],
    )
    return float(np.sqrt(np.mean((forecast - record.temp_c[split_at:]) ** 2)))


def fitted_then_extended_record() -> tuple[InnovationStateSpace, SignalRecord]:
    record = known_state_space(seed=5)(900)
    model = InnovationStateSpace(StateSpaceConfig(orders=(1, 2, 3), delays=(1, 2, 3)))
    model.fit(
        SignalRecord(
            record.time_s[:600],
            record.temp_c[:600],
            record.q[:600],
            record.ambient_c[:600],
            record.provenance,
        )
    )
    return (
        model,
        SignalRecord(
            record.time_s[600:],
            record.temp_c[600:],
            record.q[600:],
            record.ambient_c[600:],
            record.provenance,
        ),
    )


def test_subspace_fit_recovers_order_two_dynamics() -> None:
    truth = known_state_space(seed=9)
    split = chronological_split(truth(samples=2400), 0.75, 0.05)
    model = InnovationStateSpace(StateSpaceConfig(orders=(1, 2, 3, 4), delays=(1, 2, 3)))
    model.fit(split.fit)

    assert model.snapshot()["order"] == 2
    assert model.snapshot()["delay_steps"] == 2
    assert max(abs(p) for p in model.snapshot()["poles"]) < 1.0
    assert free_run_rmse(model, split.test) < 0.25


def test_refresh_aligns_state_without_output_jump() -> None:
    model, extension = fitted_then_extended_record()
    before = model.current_output_c

    result = model.refresh(extension)

    assert result.accepted
    assert model.current_output_c == pytest.approx(before, abs=0.05)


def test_rejected_refresh_keeps_incumbent_but_records_attempt_cadence() -> None:
    model, extension = fitted_then_extended_record()
    before = model.snapshot()
    shifted = SignalRecord(
        extension.time_s,
        extension.temp_c + 100.0,
        extension.q,
        extension.ambient_c,
        extension.provenance,
    )

    result = model.refresh(shifted)

    assert not result.accepted
    after = model.snapshot()
    assert after["matrices"] == before["matrices"]
    assert after["state_covariance"] == before["state_covariance"]
    assert after["update_timing"]["last_attempt_time_s"] == extension.time_s[-1]


def test_subspace_fit_is_defensive_and_block_rows_affect_identification() -> None:
    source = known_state_space(seed=4)(samples=800)
    record = SignalRecord(
        source.time_s,
        source.temp_c,
        np.concatenate((source.q[:2], source.q[:-2])),
        source.ambient_c,
        source.provenance,
    )

    short_blocks = subspace_fit(record, order=2, block_rows=2)
    tall_blocks = subspace_fit(record, order=2, block_rows=6)

    assert not short_blocks.A.flags.writeable
    with pytest.raises(ValueError):
        short_blocks.A[0, 0] = 0.0
    assert not np.allclose(short_blocks.A, tall_blocks.A)


def test_fit_retains_only_bounded_online_history() -> None:
    record = known_state_space(seed=8)(samples=200)
    model = InnovationStateSpace(
        StateSpaceConfig(orders=(1, 2), delays=(1, 2), max_buffer_samples=32)
    )

    model.fit(record)

    assert model.history_record.time_s.size == 32
    assert model.snapshot()["buffer_samples"] == 32

def test_subspace_fit_recovers_nonzero_direct_feedthrough() -> None:
    samples = 500
    q = np.sin(np.arange(samples, dtype=np.float64) / 3.0)
    z = np.zeros(samples, dtype=np.float64)
    for index in range(1, samples):
        z[index] = 0.55 * z[index - 1] + 0.7 * q[index - 1] + 0.3 * q[index]
    record = SignalRecord(
        np.arange(samples, dtype=np.float64) * 20.0,
        20.0 + z,
        q,
        np.full(samples, 20.0),
        "direct-feedthrough",
    )

    fit = subspace_fit(record, order=2, block_rows=5)

    assert abs(fit.D[0]) > 0.1


def test_affine_prediction_matches_direct_forecast() -> None:
    model, extension = fitted_then_extended_record()
    horizon = 12
    ambient = extension.ambient_c[:horizon]
    q = extension.q[:horizon]

    affine = model.affine_prediction(
        horizon, q_previous=model.input_history[-1], ambient_future=ambient
    )
    direct = model.forecast(model.history_record, q, ambient)

    np.testing.assert_allclose(affine.free_output_c + affine.input_response_c @ q, direct)


def test_snapshot_is_recursively_immutable() -> None:
    model, _ = fitted_then_extended_record()

    snapshot = model.snapshot()

    with pytest.raises(TypeError):
        snapshot["order"] = 1  # type: ignore[index]
    with pytest.raises(TypeError):
        snapshot["matrices"]["A"][0] = 0.0  # type: ignore[index]



def general_latent_state_space(*, seed: int) -> Callable[[int], SignalRecord]:
    """Return a delayed realization whose latent coordinates are not companion states."""

    def record(samples: int) -> SignalRecord:
        rng = np.random.default_rng(seed)
        time_s = np.arange(samples, dtype=np.float64) * 20.0
        q = rng.uniform(-0.65, 0.85, size=samples)
        ambient = 19.0 + 0.8 * np.sin(time_s / 900.0)
        a = np.array([[0.67, 0.16], [-0.22, 0.51]])
        b = np.array([0.45, 0.18])
        c = np.array([0.7, -0.4])
        d = 0.28
        state_offset = np.array([0.03, -0.015])
        state = np.zeros(2, dtype=np.float64)
        z = np.empty(samples, dtype=np.float64)
        delayed_q = np.pad(q, (2, 0))
        for index in range(samples):
            delayed = delayed_q[index]
            z[index] = c @ state + d * delayed + 0.18
            state = a @ state + b * delayed + state_offset
        return SignalRecord(time_s, ambient + z, q, ambient, "general-latent-state-space")

    return record


def test_projected_realization_initializes_forecast_and_affine_with_direct_feedthrough() -> None:
    record = general_latent_state_space(seed=17)(samples=1_600)
    prefix = SignalRecord(
        record.time_s[:1_200],
        record.temp_c[:1_200],
        record.q[:1_200],
        record.ambient_c[:1_200],
        record.provenance,
    )
    future_q = record.q[1_200:]
    future_ambient = record.ambient_c[1_200:]
    model = InnovationStateSpace(
        StateSpaceConfig(orders=(2,), delays=(2,), block_rows=6)
    )

    model.fit(prefix)
    forecast = model.forecast(prefix, future_q, future_ambient)
    affine = model.affine_prediction(
        future_q.size,
        q_previous=prefix.q[-1],
        ambient_future=future_ambient,
    )

    assert abs(model.snapshot()["matrices"]["D"][0]) > 0.1
    assert float(np.sqrt(np.mean((forecast - record.temp_c[1_200:]) ** 2))) < 0.08
    np.testing.assert_allclose(affine.free_output_c + affine.input_response_c @ future_q, forecast)