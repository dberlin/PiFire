"""Contracts for horizon-safe open-loop prediction scoring."""

from pathlib import Path

import numpy as np

from docs.superpowers.experiments.linear_mpc_bakeoff.contracts import SignalRecord
from docs.superpowers.experiments.linear_mpc_bakeoff.data import reconstruct_mak_fixture
from docs.superpowers.experiments.linear_mpc_bakeoff.prediction import (
    prediction_origins,
    score_free_run,
)


FIXTURE = Path("tests/unit/mpc/fixtures/mak_cook_2026-08-02.csv")


def test_short_real_tail_marks_long_horizons_unavailable() -> None:
    record = reconstruct_mak_fixture(FIXTURE)

    availability = prediction_origins(record, (60, 300, 900, 1800, 3600))

    assert availability[3600] == ()
    assert availability[1800] == ()
    assert availability[60]


class IncrementingForecaster:
    """Forecasts the known one-degree-per-frame record without target access."""

    def __init__(self) -> None:
        self.prefix_lengths: list[int] = []
        self.future_temperatures_seen: bool = False

    def forecast(
        self,
        record_prefix: SignalRecord,
        q_future: np.ndarray,
        ambient_future: np.ndarray,
    ) -> np.ndarray:
        self.prefix_lengths.append(record_prefix.time_s.size)
        self.future_temperatures_seen = self.future_temperatures_seen or bool(record_prefix.temp_c[-1] > 23.0)
        assert q_future.shape == ambient_future.shape
        return record_prefix.temp_c[-1] + np.arange(1, q_future.size + 1)


def test_free_run_scores_only_horizons_with_complete_future_windows() -> None:
    record = SignalRecord(
        time_s=np.arange(0.0, 120.0, 20.0),
        temp_c=np.arange(20.0, 26.0),
        q=np.linspace(0.1, 0.6, 6),
        ambient_c=np.full(6, 20.0),
        provenance="test-record",
    )
    model = IncrementingForecaster()

    scores = score_free_run(model, record, horizons_s=(40, 200))

    assert scores[40].available is True
    assert scores[40].origins == (0, 1, 2, 3)
    assert scores[40].rmse_c == 0.0
    assert scores[40].max_abs_c == 0.0
    assert scores[40].bias_c == 0.0
    assert scores[40].p90_abs_c == 0.0
    assert scores[200].available is False
    assert scores[200].origins == ()
    assert scores[200].rmse_c is None
    assert model.prefix_lengths == [1, 2, 3, 4]
    assert model.future_temperatures_seen is False


class FutureInputForecaster:
    """Uses each post-origin requested input as the next temperature."""

    def forecast(
        self,
        record_prefix: SignalRecord,
        q_future: np.ndarray,
        ambient_future: np.ndarray,
    ) -> np.ndarray:
        del record_prefix, ambient_future
        return q_future * 100.0


def test_free_run_passes_post_origin_inputs_to_q_sensitive_forecasters() -> None:
    q = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
    record = SignalRecord(
        time_s=np.arange(0.0, 100.0, 20.0),
        temp_c=q * 100.0,
        q=q,
        ambient_c=np.full(q.size, 20.0),
        provenance="q-alignment-test",
    )

    scores = score_free_run(FutureInputForecaster(), record, horizons_s=(40,))

    assert scores[40].rmse_c == 0.0
