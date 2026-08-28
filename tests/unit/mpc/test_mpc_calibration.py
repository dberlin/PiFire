"""Offline calibration uses the same segmented grey-fit contract as runtime learning."""

import json
import math
from dataclasses import replace

import numpy as np
import pytest

from controller import update_mpc
from controller.model_learning.contracts import CandidateOrigin
from controller.model_promotion import T_FLOOR_C, T_HAZARD_C, effective_tau
from controller.mpc_config import DEFAULT_MPC_CONFIG
from controller.mpc_model import simulate_grey_box
from controller.runtime.model_fitting import (
    GreyFitMetric,
    GreyFitMetrics,
    GreyFitSuccess,
)

TRUTH = {"C_c": 11000.0, "h_amb": 0.5, "K_Q": 3200.0, "theta": 110.0}
T_AMB = 20.0
N_DELAY = int(DEFAULT_MPC_CONFIG["n_delay"])
SIGMA = 1.4e-9


def _init() -> dict[str, float]:
    return {
        "C_c": float(DEFAULT_MPC_CONFIG["C_c"]),
        "h_amb": float(DEFAULT_MPC_CONFIG["h_amb"]),
        "K_Q": float(DEFAULT_MPC_CONFIG["K_Q"]),
        "theta": float(DEFAULT_MPC_CONFIG["theta"]),
    }


def _trace_arrays() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    time_s = np.arange(0.0, 205.0, 5.0)
    load = np.where(time_s < 100.0, 0.65, 0.25)
    temperature_c = 90.0 + 0.08 * time_s
    return time_s, temperature_c, load


def _success() -> GreyFitSuccess:
    time_s, temperature_c, load = _trace_arrays()
    job = update_mpc.trace_fit_job(
        time_s,
        temperature_c,
        load,
        T_amb=T_AMB,
        init=_init(),
        sigma=SIGMA,
        n_delay=N_DELAY,
    )
    pooled = GreyFitMetric(
        sample_count=10,
        rmse_c=1.25,
        bias_c=0.1,
        error_band_c=(-2.0, 2.5),
        max_error_c=2.5,
        input_excitation=0.4,
        input_levels=2,
        identifiability_row_count=10,
        temperature_span_c=16.0,
    )
    metrics = GreyFitMetrics(
        pooled=pooled,
        by_segment=(replace(pooled, segment_id="typed-trace-calibration"),),
        by_cook=(replace(pooled, cook_id="typed-trace-calibration"),),
    )
    return GreyFitSuccess(
        request=job.request,
        config=job.config,
        rmse_c=pooled.rmse_c,
        max_error_c=pooled.max_error_c,
        identifiability=0.5,
        sample_count=pooled.sample_count,
        temperature_band_c=(90.0, 106.0),
        nfev=12,
        metrics=metrics,
    )


def _patch_cli(monkeypatch: pytest.MonkeyPatch, outcome: object) -> None:
    time_s, temperature_c, load = _trace_arrays()
    monkeypatch.setattr(
        update_mpc,
        "_load_trace_calibration",
        lambda **_kwargs: (time_s, temperature_c, load, T_AMB),
    )
    monkeypatch.setattr(update_mpc, "_fit_trace_segmented", lambda *_args, **_kwargs: outcome)


def test_trace_calibration_materializes_one_explicit_segmented_job() -> None:
    time_s, temperature_c, load = _trace_arrays()

    job = update_mpc.trace_fit_job(
        time_s,
        temperature_c,
        load,
        T_amb=T_AMB,
        init=_init(),
        sigma=SIGMA,
        n_delay=N_DELAY,
    )

    assert job.request.origin is CandidateOrigin.OPERATOR_CALIBRATION
    assert len(job.segments) == 1
    segment = job.segments[0]
    assert segment.segment_id == "typed-trace-calibration"
    assert np.array_equal(
        segment.observation_sequences,
        np.arange(1, len(segment.scored_load) + 1),
    )
    assert segment.calibration_origin.tolist() == [True] * len(segment.scored_load)
    assert job.corpus.slices[0].prefix_digest == segment.prefix_digest


def test_calibration_cli_reports_segmented_fit_quality_and_radiation_aware_response(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outcome = _success()
    _patch_cli(monkeypatch, outcome)
    monkeypatch.setattr("sys.argv", ["update_mpc", "--cook", "calibration-cook"])

    update_mpc.main()

    output = capsys.readouterr().out
    payload = update_mpc._fit_mapping(
        outcome,
        T_amb=T_AMB,
        init=_init(),
        sigma=SIGMA,
        n_delay=N_DELAY,
    )
    assert "Fit quality: RMSE 1.25 C, max error 2.50 C" in output
    assert f"{effective_tau(payload, T_HAZARD_C):.0f} s at {T_HAZARD_C:.0f} C" in output
    assert f"{effective_tau(payload, T_FLOOR_C):.0f} s at {T_FLOOR_C:.0f} C" in output
    assert "braking distance" not in output.lower()
    assert "horizon" not in output.lower()


def test_calibration_json_carries_current_fit_verdict_and_config(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli(monkeypatch, _success())
    monkeypatch.setattr(
        "sys.argv",
        ["update_mpc", "--cook", "calibration-cook", "--json"],
    )

    update_mpc.main()

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["fit"] == {
        "converged": True,
        "nfev": 12,
        "rmse_c": 1.25,
        "max_error_c": 2.5,
    }
    assert set(payload["config"]) == set(update_mpc.CONFIG_KEYS)
    assert captured.err == ""


def test_calibration_json_reports_segmented_fit_failure_without_nonfinite_literals(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli(monkeypatch, object())
    monkeypatch.setattr(
        "sys.argv",
        ["update_mpc", "--cook", "calibration-cook", "--json"],
    )

    update_mpc.main()

    captured = capsys.readouterr()
    assert "Infinity" not in captured.out
    assert "NaN" not in captured.out
    payload = json.loads(captured.out, parse_constant=lambda value: pytest.fail(value))
    assert payload["fit"] == {
        "converged": False,
        "nfev": 0,
        "rmse_c": None,
        "max_error_c": None,
    }
    assert "ran out of evaluations" in captured.err


def test_calibration_cli_rejects_ambient_that_disagrees_with_recorded_trace(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli(monkeypatch, _success())
    monkeypatch.setattr(
        "sys.argv",
        ["update_mpc", "--cook", "calibration-cook", "--t-amb", "19.0"],
    )

    with pytest.raises(SystemExit):
        update_mpc.main()

    assert "must match the trace's recorded ambient temperature" in capsys.readouterr().err


def test_calibration_json_encoder_refuses_nonfinite_numbers() -> None:
    for value in (math.inf, -math.inf, math.nan):
        with pytest.raises(ValueError):
            update_mpc._dump_json({"fit": {"rmse_c": value}})

    assert '"rmse_c": 1.5' in update_mpc._dump_json({"fit": {"rmse_c": 1.5}})


def test_grey_model_is_invariant_under_common_thermal_scaling() -> None:
    time_s = np.arange(0.0, 1205.0, 5.0)
    load = np.where(time_s < 600.0, 0.7, 0.3)
    baseline = simulate_grey_box(
        time_s,
        load,
        T0=25.0,
        T_amb=T_AMB,
        sigma=SIGMA,
        n_delay=N_DELAY,
        **TRUTH,
    )

    for scale in (0.25, 0.5, 2.0, 4.0):
        scaled = {
            key: value if key == "theta" else value * scale
            for key, value in TRUTH.items()
        }
        other = simulate_grey_box(
            time_s,
            load,
            T0=25.0,
            T_amb=T_AMB,
            sigma=SIGMA * scale,
            n_delay=N_DELAY,
            **scaled,
        )
        assert np.array_equal(other, baseline)
