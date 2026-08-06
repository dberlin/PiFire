"""Behavioral contracts for the regularized adaptive Laguerre DMC arm."""

from __future__ import annotations

import numpy as np
import numpy.testing as npt

import pytest
from docs.superpowers.experiments.linear_mpc_bakeoff.contracts import (
    Observation,
    SignalRecord,
)
from docs.superpowers.experiments.linear_mpc_bakeoff.data import chronological_split
from docs.superpowers.experiments.linear_mpc_bakeoff.dmc import (
    DMCConfig,
    LaguerreDMC,
    laguerre_basis,
)


def delayed_first_order_record(*, delay_steps: int, pole: float, samples: int) -> SignalRecord:
    """Return a deterministic excitation of a delayed, positive first-order plant."""
    rng = np.random.default_rng(54)
    q = rng.uniform(0.0, 1.0, samples)
    temp_c = np.zeros(samples, dtype=np.float64)
    for index in range(delay_steps, samples):
        temp_c[index] = pole * temp_c[index - 1] + 0.08 * q[index - delay_steps]
    return SignalRecord(
        time_s=np.arange(samples, dtype=np.float64) * 20.0,
        temp_c=temp_c,
        q=q,
        ambient_c=np.zeros(samples, dtype=np.float64),
        provenance="deterministic delayed first-order plant",
    )


def free_run_rmse(model: LaguerreDMC, record: SignalRecord) -> float:
    """Score a model from the first tenth of a held-out deterministic record."""
    origin = record.time_s.size // 10
    prediction = model.forecast(
        SignalRecord(
            time_s=record.time_s[:origin],
            temp_c=record.temp_c[:origin],
            q=record.q[:origin],
            ambient_c=record.ambient_c[:origin],
            provenance=record.provenance,
        ),
        record.q[origin:],
        record.ambient_c[origin:],
    )
    return float(np.sqrt(np.mean((prediction - record.temp_c[origin:]) ** 2)))


def fitted_model_with_forced_negative_gain() -> LaguerreDMC:
    """Exercise the candidate projection path used after ordinary fitting."""
    model = LaguerreDMC(DMCConfig(terms=(8,), poles=(0.92,)))
    model.fit(delayed_first_order_record(delay_steps=3, pole=0.95, samples=300))
    model._active.coefficients.fill(-1.0)
    model._project_gain(model._active)
    return model


def test_track_updates_dmc_runtime_history_without_rewriting_parameters() -> None:
    """The frozen incumbent's state may advance, but its fitted response cannot."""
    record = delayed_first_order_record(delay_steps=3, pole=0.95, samples=300)
    model = LaguerreDMC(DMCConfig(terms=(8,), poles=(0.92,)))
    model.fit(record)
    before = model.snapshot()

    outcome = model.track(Observation(record.time_s[-1] + 20.0, 1.0, 0.3, 0.0))

    assert outcome.updated is False
    after = model.snapshot()
    assert after["terms"] == before["terms"]
    npt.assert_array_equal(after["step_response"], before["step_response"])


def test_laguerre_basis_is_deterministic_and_well_conditioned() -> None:
    basis = laguerre_basis(length=180, terms=12, pole=0.92)

    assert basis.shape == (180, 12)
    assert np.linalg.cond(basis.T @ basis) < 1e8
    npt.assert_allclose(basis, laguerre_basis(length=180, terms=12, pole=0.92))


def test_laguerre_basis_initializes_every_term_from_the_recurrence() -> None:
    pole = 0.92
    basis = laguerre_basis(length=180, terms=5, pole=pole)

    npt.assert_allclose(
        basis[0],
        np.sqrt(1.0 - pole * pole) * (-pole) ** np.arange(5),
    )


def test_dmc_recovers_delayed_step_response() -> None:
    split = chronological_split(delayed_first_order_record(delay_steps=5, pole=0.97, samples=1800), 0.75, 0.05)
    model = LaguerreDMC(DMCConfig(terms=(8, 12, 16), poles=(0.85, 0.92, 0.97)))

    model.fit(split.fit)

    response = model.snapshot()["step_response"]
    assert max(abs(response[:5])) < 0.02
    assert response[-1] > 0.0
    assert free_run_rmse(model, split.test) < 0.35


def test_dmc_rejects_negative_final_gain() -> None:
    candidate = fitted_model_with_forced_negative_gain()

    assert candidate.promotion_eligible is False


def test_dmc_projects_the_exposed_delayed_snapshot_endpoint() -> None:
    model = LaguerreDMC(
        DMCConfig(
            terms=(8,),
            poles=(0.92,),
            delay_seconds=(100,),
            final_gain_bounds=(0.5, 0.6),
        )
    )
    model.fit(delayed_first_order_record(delay_steps=5, pole=0.97, samples=600))

    snapshot = model.snapshot()

    assert snapshot["final_gain"] == pytest.approx(0.6)
    assert snapshot["step_response"][-1] == pytest.approx(0.6)


def test_snapshot_exposes_arm_neutral_gain_diagnostic() -> None:
    model = LaguerreDMC(DMCConfig(terms=(8,), poles=(0.92,)))
    model.fit(delayed_first_order_record(delay_steps=3, pole=0.95, samples=600))

    snapshot = model.snapshot()

    assert snapshot["steady_gain"] == snapshot["final_gain"]
    assert snapshot["delay_seconds"] == snapshot["delay_steps"] * 20.0


def test_dmc_affine_prediction_matches_shifted_step_response() -> None:
    record = delayed_first_order_record(delay_steps=4, pole=0.96, samples=600)
    model = LaguerreDMC(DMCConfig(terms=(8,), poles=(0.92,)))
    model.fit(record)
    horizon = 16
    ambient = np.zeros(horizon, dtype=np.float64)
    q = np.linspace(0.1, 0.8, horizon)

    affine = model.affine_prediction(horizon, q_previous=record.q[-1], ambient_future=ambient)
    direct = model.forecast(record, q, ambient)

    npt.assert_allclose(affine.free_output_c + affine.input_response_c @ q, direct)
