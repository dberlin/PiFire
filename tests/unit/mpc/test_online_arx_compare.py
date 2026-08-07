"""Behavioral contracts for production-path online scheduled-ARX evidence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from math import isfinite
from pathlib import Path
import subprocess
from types import SimpleNamespace
import sys
from typing import Any, Callable

import numpy as np
import pytest

import docs.superpowers.experiments.online_arx_compare as online_arx_compare

from docs.superpowers.experiments.online_arx_compare import (
    ARTIFACT_SCHEMA_VERSION,
    CONTROLLER_ARMS,
    FIXED_SEEDS,
    PLANTS,
    REQUIRED_METRICS,
    SCENARIOS,
    _PredictionOrigin,
    _control_metrics,
    _origin_prediction_scores,
    _run_simulator_cell,
    _scenario_definitions,
    artifact_contract_errors,
    decide_ship,
    load_artifact,
    run_real_mak_replay,
    run_tiny_grillsim,
    run_tiny_mak_grillsim,
)
from controller.linear_mpc.contracts import AffinePrediction, FrameObservation
from docs.superpowers.experiments.linear_mpc_bakeoff.contracts import SignalRecord


from controller.runtime.logic.pulse import PulseTransition


_PREDICTION_METRICS = frozenset({"prediction_rmse_60_c", "prediction_rmse_300_c"})
_REVIEWED_SOURCE_REVISION = "a" * 40

_CONTROL_METRICS = frozenset(REQUIRED_METRICS) - _PREDICTION_METRICS
_SIMULATOR_STACK = {
    "mpc": "Controller",
    "scheduled_arx": "ScheduledARX",
    "linear_policy": "LinearMPC",
    "allocator": "allocate",
    "pulse_scheduler": "PulseScheduler",
    "runner": "ThreadedControllerRunner",
}


def _metrics(*, arm: str, scenario: str) -> dict[str, float | None]:
    online = arm == "online"
    braking_applicable = scenario == "target-decrease-coast"
    return {
        "pct_within_5f": 95.0 if online else 90.0,
        "overshoot_f": 4.0 if online else 5.0,
        "settle_s": 50.0 if scenario != "cold-start" else None,
        "rmse_f": 2.0 if online else 3.0,
        "steady_peak_to_peak_f": 1.0,
        "auger_on_s": 100.0,
        "transitions_per_hour": 8.0 if online else 10.0,
        "requested_realized_load_error": 0.01 if online else 0.02,
        "deadline_misses": 0.0,
        "stale_result_episodes": 0.0,
        "prediction_rmse_60_c": 0.8 if online else 1.0,
        "prediction_rmse_300_c": 1.6 if online else 2.0,
        "braking_error_c": 0.5 if braking_applicable else None,
        "promotions": 1.0 if online else 0.0,
        "rollbacks": 0.0,
    }


def _row(*, arm: str, plant: str, scenario: str, seed: int) -> dict[str, Any]:
    online = arm == "online"
    return {
        "cell_key": f"{arm}:{plant}:{scenario}:{seed}",
        "arm": arm,
        "plant": plant,
        "scenario": scenario,
        "seed": seed,
        "status": "completed",
        "failure": None,
        "metrics": _metrics(arm=arm, scenario=scenario),
        "metric_applicability": {
            "settled": scenario != "cold-start",
            "braking_error_c": scenario == "target-decrease-coast",
        },
        "raw_timing_ms": {
            "learner": [1.0] if online else [],
            "evaluation": [2.0] if online else [],
            "solve": [3.0],
        },
        "timing_applicability": {"learner": online, "evaluation": online, "solve": True},
        "online_chronology": [
            {
                "frame_index": 10,
                "event": "evaluation",
                "revision": 1,
                "stale_state": "fresh",
            },
            {
                "frame_index": 20,
                "event": "promotion",
                "revision": 1,
                "stale_state": "fresh",
            },
        ]
        if online
        else [],
        "outcomes": {"safety_inhibits": None, "unreachable_setpoints": 0},
        "outcome_evidence": {
            "safety_inhibits": "unavailable",
            "unreachable_setpoints": "measured",
        },
        "runner_evidence": {
            "deadline_miss_count": 0,
            "stale_state_transitions": [],
            "result_revisions": [1],
            "statuses": ["scheduled-arx" if online else "grey-box"],
            "policy_failure_counts": [0],
        },
        "preconditioning": {
            "applied": scenario != "cold-start",
            "duration_s": 120 if scenario != "cold-start" else 0,
            "hold_established": scenario != "cold-start",
        },
        "production_stack": dict(_SIMULATOR_STACK),
        "actual_delivered_load_feedback": True,
    }


def _real_mak_row(*, arm: str) -> dict[str, Any]:
    online = arm == "online"
    return {
        "cell_key": f"{arm}:real-MAK:chronological-replay",
        "arm": arm,
        "plant": "real-MAK",
        "scenario": "chronological-replay",
        "status": "completed",
        "failure": None,
        "metrics": {
            **{metric: None for metric in _CONTROL_METRICS},
            "prediction_rmse_60_c": 0.8 if online else 1.0,
            "prediction_rmse_300_c": 1.6 if online else 2.0,
        },
        "unavailable_metrics": sorted(_CONTROL_METRICS),
        "input_provenance": "requested-input-reconstruction",
        "input_transform": {
            "source": "reconstructed_auger_duty",
            "operation": "normalized_load_from_auger_duty",
            "u_max": 0.9,
            "applied_once": True,
        },
        "actual_delivered_load_feedback": False,
        "raw_timing_ms": {
            "learner": [1.0] if online else [],
            "evaluation": [],
            "solve": [] if online else [3.0],
        },
        "timing_applicability": {"learner": online, "evaluation": False, "solve": not online},
        "production_stack": (
            {
                "controller": "Controller",
                "prediction_model": "ScheduledARX",
                "scheduled_arx_config": {
                    "na": 2,
                    "nb": 2,
                    "delays": [1, 2, 3],
                    "initial_covariance": 10.0,
                },
            }
            if online
            else {"controller": "Controller", "prediction_model": "GreyBoxPredictionAdapter"}
        ),
        "prediction_origins": {
            "warmup_frames": 6,
            "origin_frame_indices": [6, 7, 8],
            "origin_count": 3,
        },
    }


def _complete_artifact() -> dict[str, Any]:
    rows = [
        _row(arm=arm, plant=plant, scenario=scenario, seed=seed)
        for arm in CONTROLLER_ARMS
        for plant in PLANTS
        for scenario in SCENARIOS
        for seed in FIXED_SEEDS
    ]
    artifact = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "source_revision": _REVIEWED_SOURCE_REVISION,
        "requested": {
            "seeds": list(FIXED_SEEDS),
            "plants": list(PLANTS),
            "scenarios": list(SCENARIOS),
            "controller_arms": list(CONTROLLER_ARMS),
        },
        "rows": rows,
        "real_mak_rows": [_real_mak_row(arm=arm) for arm in CONTROLLER_ARMS],
        "timing_budgets_ms": {"learner": 5.0, "evaluation": 5.0, "solve": 5.0},
        "timing_environment": {
            "classification": "target-device",
            "platform": "linux",
            "machine": "aarch64",
            "model": "Raspberry Pi 5 Model B",
            "source": "runtime-detected",
        },
        "aggregates": {
            "baseline": {"control_score": 10.0},
            "online": {"control_score": 9.0},
        },
    }
    artifact["ship_decision"] = decide_ship(artifact)
    return artifact


def test_contract_requires_fixed_matrix_complete_unique_rows_and_explicit_failures() -> None:
    artifact = _complete_artifact()

    assert FIXED_SEEDS
    assert set(PLANTS) == {"GrillSim", "MAKGrillSim"}
    assert set(SCENARIOS) == {
        "cold-start",
        "hold",
        "target-increase",
        "target-decrease-coast",
        "lid-interruption",
    }
    assert set(CONTROLLER_ARMS) == {"baseline", "online"}
    assert set(REQUIRED_METRICS) == {
        "pct_within_5f",
        "overshoot_f",
        "settle_s",
        "rmse_f",
        "steady_peak_to_peak_f",
        "auger_on_s",
        "transitions_per_hour",
        "requested_realized_load_error",
        "deadline_misses",
        "stale_result_episodes",
        "prediction_rmse_60_c",
        "prediction_rmse_300_c",
        "braking_error_c",
        "promotions",
        "rollbacks",
    }
    assert artifact_contract_errors(artifact) == []
    decision = decide_ship(artifact)
    assert not decision["ship"]
    assert decision["reasons"]
    duplicate = deepcopy(artifact)
    duplicate["rows"].append(deepcopy(duplicate["rows"][0]))
    assert artifact_contract_errors(duplicate)

    missing = deepcopy(artifact)
    missing["rows"].pop()
    assert artifact_contract_errors(missing)

    failed = deepcopy(artifact)
    failed_row = failed["rows"][0]
    failed_row.update(status="failed", failure={"reason": "simulator unavailable"})
    failed_row["preconditioning"] = {
        "applied": False,
        "duration_s": None,
        "hold_established": False,
    }
    failed_row["outcomes"] = {"safety_inhibits": None, "unreachable_setpoints": None}
    failed_row["outcome_evidence"] = {
        "safety_inhibits": "unavailable",
        "unreachable_setpoints": "unavailable",
    }
    failed_row["runner_evidence"]["policy_failure_counts"] = []
    failed_row["runner_evidence"]["statuses"] = []
    failed["ship_decision"] = decide_ship(failed)
    assert artifact_contract_errors(failed) == []

    unpublished_failure = deepcopy(failed)
    unpublished_failure["rows"][0]["failure"] = None
    assert artifact_contract_errors(unpublished_failure)

    decision = decide_ship(failed)
    assert not decision["ship"]
    assert decision["reasons"]


def test_contract_is_strict_and_preserves_the_full_required_metric_set() -> None:
    artifact = _complete_artifact()

    malformed = deepcopy(artifact)
    malformed["rows"][0]["metrics"].pop("braking_error_c")
    assert artifact_contract_errors(malformed)

    malformed = deepcopy(artifact)
    malformed["rows"][0]["metrics"]["invented_metric"] = 1.0
    assert artifact_contract_errors(malformed)

    malformed = deepcopy(artifact)
    next(row for row in malformed["rows"] if row["arm"] == "online")["online_chronology"].reverse()
    assert artifact_contract_errors(malformed)

    malformed = deepcopy(artifact)
    malformed.pop("source_revision")
    assert "source revision" in artifact_contract_errors(malformed)

    malformed = deepcopy(artifact)
    malformed["schema_version"] = ARTIFACT_SCHEMA_VERSION + 1
    assert artifact_contract_errors(malformed)
    for field, values in {
        "seeds": [999],
        "plants": ["GrillSim"],
        "scenarios": ["hold"],
        "controller_arms": ["online"],
    }.items():
        malformed = deepcopy(artifact)
        malformed["requested"][field] = values
        assert artifact_contract_errors(malformed)

    malformed = deepcopy(artifact)
    malformed["unrecognized_top_level"] = True
    assert artifact_contract_errors(malformed)


@pytest.mark.parametrize(
    "revision",
    ("", "not-a-commit", "A" * 40, "a" * 39, "a" * 41),
)
def test_artifact_contract_rejects_malformed_reviewed_source_revisions(revision: str) -> None:
    artifact = _complete_artifact()
    artifact["source_revision"] = revision

    assert "source revision" in artifact_contract_errors(artifact)


def test_completed_50_percent_pulse_has_zero_requested_realized_frame_error() -> None:
    metrics, _ = _control_metrics(
        temperatures=[100.0],
        targets=[100.0],
        requested=[5.0 / 9.0],
        realized=[5.0 / 9.0],
        transitions=0,
        duration_s=1,
        auger_on_s=10.0,
        predictions={"prediction_rmse_60_c": 1.0, "prediction_rmse_300_c": 2.0},
        promotions=0,
        rollbacks=0,
        braking_errors=[],
        deadline_misses=0,
        stale_episodes=0,
    )

    assert metrics["requested_realized_load_error"] == pytest.approx(0.0)


def test_nonsettling_is_censored_and_braking_is_not_applicable_without_a_decrease() -> None:
    metrics, applicability = _control_metrics(
        temperatures=[0.0, 0.0],
        targets=[100.0, 100.0],
        requested=[5.0 / 9.0, 5.0 / 9.0],
        realized=[5.0 / 9.0, 5.0 / 9.0],
        transitions=0,
        duration_s=2,
        auger_on_s=0.0,
        predictions={"prediction_rmse_60_c": 1.0, "prediction_rmse_300_c": 2.0},
        promotions=0,
        rollbacks=0,
        braking_errors=[],
        deadline_misses=0,
        stale_episodes=0,
    )

    assert metrics["settle_s"] is None
    assert applicability == {"settled": False, "braking_error_c": False}
    assert metrics["braking_error_c"] is None


class _ZeroPredictionModel:
    def affine_prediction(self, horizon: int, q_previous: float, ambient_c: list[float]) -> AffinePrediction:
        del q_previous, ambient_c
        return AffinePrediction(
            free_output_c=np.zeros(horizon),
            input_response_c=np.zeros((horizon, horizon)),
        )


def _prediction_frame(
    index: int,
    *,
    temp_c: float,
    lid_open: bool = False,
    reset: bool = False,
    start_s: float | None = None,
) -> FrameObservation:
    start = float(index * 20 if start_s is None else start_s)
    return FrameObservation(
        frame_start_s=start,
        frame_end_s=start + 20.0,
        temp_c=temp_c,
        setpoint_c=100.0,
        ambient_c=20.0,
        requested_q=0.0,
        realized_q=0.0,
        requested_auger_duty=0.0,
        delivered_on_s=0.0,
        requested_fan_duty=1.0,
        actual_fan_duty=1.0,
        result_revision=0,
        output_source="controller",
        lid_open=lid_open,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=reset,
        continuous=True,
        role_generation=0,
    )


def test_prediction_scores_use_exact_terminal_residuals_and_exclude_lid_windows() -> None:
    frames = [_prediction_frame(index, temp_c=0.0) for index in range(32)]
    frames[3] = _prediction_frame(3, temp_c=10.0)
    frames[15] = _prediction_frame(15, temp_c=20.0)
    frames[17] = _prediction_frame(17, temp_c=0.0, lid_open=True)
    frames[19] = _prediction_frame(19, temp_c=100.0)
    frames[31] = _prediction_frame(31, temp_c=200.0)
    origins = [
        _PredictionOrigin(model=_ZeroPredictionModel(), q_previous=0.0, frame_index=0),
        _PredictionOrigin(model=_ZeroPredictionModel(), q_previous=0.0, frame_index=16),
    ]

    assert _origin_prediction_scores(origins, frames) == {
        "prediction_rmse_60_c": pytest.approx(10.0),
        "prediction_rmse_300_c": pytest.approx(20.0),
    }


def test_prediction_scoring_rejects_a_reset_origin() -> None:
    frames = [_prediction_frame(index, temp_c=0.0) for index in range(16)]
    frames[0] = _prediction_frame(0, temp_c=0.0, reset=True)

    with pytest.raises(RuntimeError, match="no supported"):
        _origin_prediction_scores(
            [_PredictionOrigin(model=_ZeroPredictionModel(), q_previous=0.0, frame_index=0)],
            frames,
        )


def test_prediction_scoring_rejects_a_continuous_window_with_a_45_second_gap() -> None:
    frames = [_prediction_frame(index, temp_c=0.0) for index in range(16)]
    frames[1] = _prediction_frame(1, temp_c=0.0, start_s=65.0)

    with pytest.raises(RuntimeError, match="no supported"):
        _origin_prediction_scores(
            [_PredictionOrigin(model=_ZeroPredictionModel(), q_previous=0.0, frame_index=0)],
            frames,
        )


def test_contract_requires_consistent_metric_and_timing_applicability() -> None:
    artifact = _complete_artifact()

    censored = deepcopy(artifact)
    censored_row = _online_row(censored)
    censored_row["metrics"]["settle_s"] = None
    censored_row["metric_applicability"]["settled"] = False
    censored["ship_decision"] = decide_ship(censored)
    assert artifact_contract_errors(censored) == []

    assert all(
        row["metric_applicability"]["braking_error_c"] is (row["scenario"] == "target-decrease-coast")
        and (row["metrics"]["braking_error_c"] is not None) is (row["scenario"] == "target-decrease-coast")
        for row in artifact["rows"]
    )

    malformed = deepcopy(censored)
    malformed_row = _online_row(malformed)
    malformed_row["metric_applicability"]["settled"] = True
    assert artifact_contract_errors(malformed)

    malformed = deepcopy(artifact)
    malformed_row = next(row for row in malformed["rows"] if row["scenario"] == "hold" and row["arm"] == "online")
    malformed_row["metrics"]["braking_error_c"] = 0.0
    assert artifact_contract_errors(malformed)

    malformed = deepcopy(artifact)
    malformed_row = _online_row(malformed)
    malformed_row["raw_timing_ms"]["learner"] = []
    assert artifact_contract_errors(malformed)

    malformed = deepcopy(artifact)
    malformed_row = _online_row(malformed)
    malformed_row["raw_timing_ms"]["evaluation"] = []
    assert artifact_contract_errors(malformed)

    malformed = deepcopy(artifact)
    malformed_row = _online_row(malformed)
    malformed_row["raw_timing_ms"]["solve"] = []
    assert artifact_contract_errors(malformed)

    malformed = deepcopy(artifact)
    malformed_row = _online_row(malformed)
    malformed_row["runner_evidence"]["deadline_miss_count"] = 1
    assert artifact_contract_errors(malformed)

    malformed = deepcopy(artifact)
    malformed_row = _online_row(malformed)
    malformed_row["runner_evidence"]["stale_state_transitions"] = ["stale"]
    assert artifact_contract_errors(malformed)

    malformed = deepcopy(artifact)
    malformed_row = _online_row(malformed)
    malformed_row["runner_evidence"]["policy_failure_counts"] = []
    assert artifact_contract_errors(malformed)

    malformed = deepcopy(artifact)
    malformed_row = _online_row(malformed)
    malformed_row["runner_evidence"]["policy_failure_counts"] = [0, -1]
    assert artifact_contract_errors(malformed)

    malformed = deepcopy(artifact)
    malformed_row = _online_row(malformed)
    malformed_row["runner_evidence"]["statuses"] = []
    assert artifact_contract_errors(malformed)

    malformed = deepcopy(artifact)
    malformed_row = _online_row(malformed)
    malformed_row["runner_evidence"]["statuses"] = ["invented"]
    assert artifact_contract_errors(malformed)


def test_named_non_cold_rows_publish_successful_preconditioning() -> None:
    artifact = load_artifact()
    non_cold_rows = [row for row in artifact["rows"] if row["scenario"] != "cold-start"]

    assert non_cold_rows
    assert all(
        row["preconditioning"]["applied"] is True
        and row["preconditioning"]["hold_established"] is True
        and 60 <= row["preconditioning"]["duration_s"] <= 1_800
        for row in non_cold_rows
    )

    malformed = deepcopy(artifact)
    next(row for row in malformed["rows"] if row["scenario"] == "hold")["preconditioning"]["hold_established"] = False
    assert artifact_contract_errors(malformed)


def test_unavailable_safety_evidence_honestly_blocks_shipping() -> None:
    artifact = _complete_artifact()

    assert all(
        row["outcomes"]["safety_inhibits"] is None and row["outcome_evidence"]["safety_inhibits"] == "unavailable"
        for row in artifact["rows"]
    )
    decision = decide_ship(artifact)
    assert not decision["ship"]
    assert any("safety" in reason for reason in decision["reasons"])


def test_contract_requires_raw_finite_timing_samples_and_finite_strict_json() -> None:
    artifact = _complete_artifact()

    for row in [*artifact["rows"], *artifact["real_mak_rows"]]:
        assert set(row["raw_timing_ms"]) == {"learner", "evaluation", "solve"}
        assert all(
            isinstance(sample, (int, float)) and isfinite(sample)
            for samples in row["raw_timing_ms"].values()
            for sample in samples
        )
    assert json.loads(json.dumps(artifact, allow_nan=False)) == artifact
    assert artifact_contract_errors(artifact) == []

    malformed = deepcopy(artifact)
    malformed["rows"][0]["raw_timing_ms"]["solve"] = [float("inf")]
    assert artifact_contract_errors(malformed)
    with pytest.raises(ValueError):
        json.dumps(malformed, allow_nan=False)


def test_shipping_requires_runtime_detected_target_timing_provenance() -> None:
    artifact = _complete_artifact()
    for row in artifact["rows"]:
        row["outcomes"]["safety_inhibits"] = 0
        row["outcome_evidence"]["safety_inhibits"] = "measured"
    artifact["ship_decision"] = decide_ship(artifact)

    assert artifact["timing_environment"] == {
        "classification": "target-device",
        "platform": "linux",
        "machine": "aarch64",
        "model": "Raspberry Pi 5 Model B",
        "source": "runtime-detected",
    }
    assert artifact_contract_errors(artifact) == []
    assert artifact["ship_decision"]["ship"] is True

    workstation = deepcopy(artifact)
    workstation["timing_environment"].update(
        classification="workstation",
        platform="darwin",
        machine="unknown",
        model=None,
    )
    workstation["ship_decision"] = decide_ship(workstation)

    assert artifact_contract_errors(workstation) == []
    assert workstation["ship_decision"] == {
        "ship": False,
        "reasons": ["target timing unavailable"],
    }

    target_device = deepcopy(workstation)
    target_device["timing_environment"].update(
        classification="target-device",
        platform="linux",
        machine="aarch64",
        model="Raspberry Pi 5 Model B",
    )
    target_device["ship_decision"] = decide_ship(target_device)

    assert artifact_contract_errors(target_device) == []
    assert target_device["ship_decision"]["ship"] is True

    for field, value in (
        ("platform", ""),
        ("machine", ""),
        ("source", "operator-claimed"),
    ):
        forged = deepcopy(target_device)
        forged["timing_environment"][field] = value
        forged["ship_decision"] = decide_ship(forged)
        assert artifact_contract_errors(forged)

    forged = deepcopy(target_device)
    forged["timing_environment"]["extra"] = "untracked"
    forged["ship_decision"] = decide_ship(forged)
    assert artifact_contract_errors(forged)

    stale = deepcopy(workstation)
    stale["timing_environment"].update(
        classification="target-device",
        platform="linux",
        machine="aarch64",
        model="Raspberry Pi 5 Model B",
    )
    assert artifact_contract_errors(stale) == ["ship decision is not recomputed"]


def test_timing_environment_requires_a_recognized_runtime_target_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(online_arx_compare.platform, "system", lambda: "Linux")
    monkeypatch.setattr(online_arx_compare.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(
        online_arx_compare.Path,
        "read_text",
        lambda _path, *, encoding: "\x00Raspberry Pi 5 Model B\x00",
    )

    assert online_arx_compare._timing_environment() == {
        "classification": "target-device",
        "platform": "Linux",
        "machine": "aarch64",
        "model": "Raspberry Pi 5 Model B",
        "source": "runtime-detected",
    }


def test_timing_environment_marks_an_unrecognized_runtime_as_workstation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(online_arx_compare.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(online_arx_compare.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(
        online_arx_compare.Path,
        "read_text",
        lambda _path, *, encoding: "Apple Mac",
    )

    assert online_arx_compare._timing_environment() == {
        "classification": "workstation",
        "platform": "Darwin",
        "machine": "arm64",
        "model": None,
        "source": "runtime-detected",
    }


@pytest.mark.parametrize(
    ("classification", "model"),
    (("target-device", None), ("workstation", "Raspberry Pi 5 Model B")),
)
def test_contract_rejects_inconsistent_runtime_timing_classification(
    classification: str,
    model: str | None,
) -> None:
    artifact = _complete_artifact()
    artifact["timing_environment"].update(classification=classification, model=model)
    artifact["ship_decision"] = decide_ship(artifact)

    assert "timing environment" in artifact_contract_errors(artifact)


def test_worker_gate_constructs_its_condition() -> None:
    assert online_arx_compare._WorkerGate()._condition is not None


def test_real_mak_control_metrics_are_explicitly_unavailable_not_fabricated() -> None:
    artifact = _complete_artifact()

    for row in artifact["real_mak_rows"]:
        assert set(row["metrics"]) == REQUIRED_METRICS
        assert set(row["unavailable_metrics"]) == _CONTROL_METRICS
        assert all(row["metrics"][metric] is None for metric in _CONTROL_METRICS)
        assert row["input_transform"] == {
            "source": "reconstructed_auger_duty",
            "operation": "normalized_load_from_auger_duty",
            "u_max": 0.9,
            "applied_once": True,
        }
        assert row["actual_delivered_load_feedback"] is False
        assert set(row["production_stack"]).isdisjoint({"allocator", "pulse_scheduler", "runner"})
        assert "output_source" not in row
        assert "online_chronology" not in row
    assert artifact_contract_errors(artifact) == []

    fabricated = deepcopy(artifact)
    fabricated["real_mak_rows"][0]["metrics"]["rmse_f"] = 0.0
    fabricated["real_mak_rows"][0]["unavailable_metrics"].remove("rmse_f")
    assert artifact_contract_errors(fabricated)


def test_real_mak_replay_publishes_prediction_only_chronological_evidence() -> None:
    rows = run_real_mak_replay()

    assert len(rows) == 2
    assert {row["arm"] for row in rows} == set(CONTROLLER_ARMS)
    by_arm = {row["arm"]: row for row in rows}
    origins = [row["prediction_origins"] for row in rows]
    assert {origin["origin_count"] for origin in origins} == {len(origins[0]["origin_frame_indices"])}
    assert {origin["origin_frame_indices"][0] for origin in origins} == {origins[0]["origin_frame_indices"][0]}
    assert {origin["warmup_frames"] for origin in origins} == {origins[0]["warmup_frames"]}

    for row in rows:
        assert row["status"] == "completed"
        assert set(row["metrics"]) == REQUIRED_METRICS
        assert set(row["unavailable_metrics"]) == _CONTROL_METRICS
        assert all(row["metrics"][metric] is None for metric in _CONTROL_METRICS)
        assert all(isfinite(row["metrics"][metric]) for metric in _PREDICTION_METRICS)
        assert row["input_provenance"] == "requested-input-reconstruction"
        assert row["input_transform"] == {
            "source": "reconstructed_auger_duty",
            "operation": "normalized_load_from_auger_duty",
            "u_max": 0.9,
            "applied_once": True,
        }
        assert row["actual_delivered_load_feedback"] is False
        assert set(row["production_stack"]).isdisjoint({"allocator", "pulse_scheduler", "runner"})
        assert "output_source" not in row
        assert "online_chronology" not in row

    assert by_arm["baseline"]["production_stack"] == {
        "controller": "Controller",
        "prediction_model": "GreyBoxPredictionAdapter",
    }
    assert by_arm["online"]["production_stack"] == {
        "controller": "Controller",
        "prediction_model": "ScheduledARX",
        "scheduled_arx_config": {
            "na": 2,
            "nb": 2,
            "delays": [1, 2, 3],
            "initial_covariance": 10.0,
        },
    }
    assert by_arm["baseline"]["timing_applicability"] == {
        "learner": False,
        "evaluation": False,
        "solve": True,
    }
    assert by_arm["online"]["timing_applicability"] == {
        "learner": True,
        "evaluation": False,
        "solve": False,
    }
    assert by_arm["online"]["raw_timing_ms"]["learner"]
    assert by_arm["online"]["raw_timing_ms"]["solve"] == []
    assert all(isfinite(sample) for row in rows for samples in row["raw_timing_ms"].values() for sample in samples)


def test_real_mak_replay_normalizes_reconstructed_duty_once_for_both_arms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    q = np.asarray([0.11, 0.24, 0.37, 0.52, 0.68] * 8, dtype=np.float64)
    record = SignalRecord(
        time_s=np.arange(1, q.size + 1, dtype=np.float64) * 20.0,
        temp_c=np.linspace(90.0, 130.0, q.size, dtype=np.float64),
        q=q,
        ambient_c=np.full(q.size, 22.0, dtype=np.float64),
        provenance="requested-input-reconstruction",
    )
    physical_ratio: dict[bool, list[float]] = {False: [], True: []}
    applied_q: dict[bool, list[float]] = {False: [], True: []}
    requested_duty: dict[bool, list[float]] = {False: [], True: []}
    controller_u_max: dict[bool, float] = {}
    controller_arm: dict[int, bool] = {}
    original_controller = online_arx_compare._controller
    original_set_output = online_arx_compare.Controller.set_output
    original_scheduled_arx = online_arx_compare.ScheduledARX

    class RecordingScheduledARX(original_scheduled_arx):
        observed_q: list[float] = []

        def track(self, observation: FrameObservation) -> Any:
            type(self).observed_q.append(observation.requested_q)
            return super().track(observation)

        def observe(self, observation: FrameObservation) -> Any:
            type(self).observed_q.append(observation.requested_q)
            return super().observe(observation)

    def controller(*, online: bool) -> Any:
        instance = original_controller(online=online)
        controller_arm[id(instance)] = online
        controller_u_max[online] = instance.u_max
        return instance

    def record_output(instance: Any, output: Any) -> Any:
        result = original_set_output(instance, output)
        arm = controller_arm[id(instance)]
        physical_ratio[arm].append(output.ratio)
        requested_duty[arm].append(output.requested)
        applied_q[arm].append(instance._applied_combustion_load)
        return result

    monkeypatch.setattr(online_arx_compare, "real_mak_record", lambda: record)
    monkeypatch.setattr(online_arx_compare, "_controller", controller)
    monkeypatch.setattr(online_arx_compare.Controller, "set_output", record_output)
    monkeypatch.setattr(online_arx_compare, "ScheduledARX", RecordingScheduledARX)

    run_real_mak_replay()

    for arm in (False, True):
        expected_normalized_q = q / controller_u_max[arm]
        np.testing.assert_allclose(requested_duty[arm], q, rtol=0.0, atol=1e-12)
        np.testing.assert_allclose(applied_q[arm], expected_normalized_q, rtol=0.0, atol=1e-12)
        np.testing.assert_array_equal(physical_ratio[arm], q)
    np.testing.assert_allclose(RecordingScheduledARX.observed_q, q / 0.9, rtol=0.0, atol=1e-12)


def _break_control_score(artifact: dict[str, Any]) -> None:
    artifact["aggregates"]["online"]["control_score"] = 10.0


def _online_rows(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in artifact["rows"] if row["arm"] == "online"]


def _online_row(artifact: dict[str, Any]) -> dict[str, Any]:
    return _online_rows(artifact)[0]


def _online_real_mak_row(artifact: dict[str, Any]) -> dict[str, Any]:
    return next(row for row in artifact["real_mak_rows"] if row["arm"] == "online")


def _break_safety_inhibit(artifact: dict[str, Any]) -> None:
    for row in _online_rows(artifact):
        row["outcomes"]["safety_inhibits"] = 1
        row["outcome_evidence"]["safety_inhibits"] = "measured"


def _break_reachability(artifact: dict[str, Any]) -> None:
    for row in _online_rows(artifact):
        row["outcomes"]["unreachable_setpoints"] = 1


def _break_transitions(artifact: dict[str, Any]) -> None:
    for row in _online_rows(artifact):
        row["metrics"]["transitions_per_hour"] = 11.0


def _break_stale_episodes(artifact: dict[str, Any]) -> None:
    for row in _online_rows(artifact):
        row["metrics"]["stale_result_episodes"] = 1.0
        row["runner_evidence"]["stale_state_transitions"] = ["stale"]


def _break_requested_realized_error(artifact: dict[str, Any]) -> None:
    for row in _online_rows(artifact):
        row["metrics"]["requested_realized_load_error"] = 0.03


def _break_model_unavailable(artifact: dict[str, Any]) -> None:
    for row in _online_rows(artifact):
        row["runner_evidence"]["statuses"] = ["model-unavailable"]


def _break_deadline_miss(artifact: dict[str, Any]) -> None:
    for row in _online_rows(artifact):
        row["metrics"]["deadline_misses"] = 1.0
        row["runner_evidence"]["deadline_miss_count"] = 1


def _break_policy_failure(artifact: dict[str, Any]) -> None:
    for row in _online_rows(artifact):
        row["runner_evidence"]["policy_failure_counts"] = [1]


def _break_real_mak_prediction(artifact: dict[str, Any]) -> None:
    _online_real_mak_row(artifact)["metrics"]["prediction_rmse_60_c"] = 1.1


def _break_learner_budget(artifact: dict[str, Any]) -> None:
    for row in _online_rows(artifact):
        row["raw_timing_ms"]["learner"] = [6.0]


def _break_evaluation_budget(artifact: dict[str, Any]) -> None:
    for row in _online_rows(artifact):
        row["raw_timing_ms"]["evaluation"] = [6.0]


def _break_solve_budget(artifact: dict[str, Any]) -> None:
    for row in _online_rows(artifact):
        row["raw_timing_ms"]["solve"] = [6.0]


def _break_completed_cell(artifact: dict[str, Any]) -> None:
    _online_row(artifact).update(status="failed", failure={"reason": "interrupted"})


@pytest.mark.parametrize(
    "regression",
    (
        _break_control_score,
        _break_safety_inhibit,
        _break_reachability,
        _break_transitions,
        _break_stale_episodes,
        _break_model_unavailable,
        _break_deadline_miss,
        _break_policy_failure,
        _break_requested_realized_error,
        _break_real_mak_prediction,
        _break_learner_budget,
        _break_evaluation_budget,
        _break_solve_budget,
        _break_completed_cell,
    ),
    ids=(
        "aggregate-control-score",
        "safety-inhibit",
        "reachability",
        "relay-transitions",
        "stale-result-episodes",
        "model-unavailable",
        "deadline-miss",
        "policy-failure",
        "requested-realized-load-error",
        "real-mak-supported-prediction",
        "learner-p99-budget",
        "evaluation-p99-budget",
        "solve-p99-budget",
        "incomplete-cell",
    ),
)
def test_ship_decision_rejects_each_regression_or_incomplete_cell(
    regression: Callable[[dict[str, Any]], None],
) -> None:
    artifact = _complete_artifact()
    regression(artifact)

    decision = decide_ship(artifact)

    assert not decision["ship"]
    assert decision["reasons"]


@pytest.mark.parametrize(
    ("regression", "reason"),
    (
        (_break_model_unavailable, "runner model status unavailable"),
        (_break_deadline_miss, "runner deadline miss evidence"),
        (_break_stale_episodes, "runner stale result evidence"),
        (_break_policy_failure, "online policy failure regression"),
    ),
)
def test_ship_decision_reports_runner_evidence_gates(regression: Callable[[dict[str, Any]], None], reason: str) -> None:
    artifact = _complete_artifact()
    regression(artifact)

    assert reason in decide_ship(artifact)["reasons"]


def test_published_artifact_requires_and_recomputes_ship_decision() -> None:
    artifact = load_artifact()

    assert artifact_contract_errors(artifact) == []
    assert artifact["ship_decision"] == decide_ship(artifact)
    assert isinstance(artifact["ship_decision"]["ship"], bool)
    assert isinstance(artifact["ship_decision"]["reasons"], list)

    unpublished = deepcopy(_complete_artifact())
    unpublished.pop("ship_decision")
    assert artifact_contract_errors(unpublished)

    stale = deepcopy(_complete_artifact())
    stale["ship_decision"] = {"ship": True, "reasons": []}
    assert artifact_contract_errors(stale)


def test_comparison_requires_an_explicit_reviewed_source_revision() -> None:
    with pytest.raises(TypeError):
        online_arx_compare.run_comparison(duration_s=2 * online_arx_compare.frame_seconds())


@pytest.mark.parametrize(
    "revision",
    ("", "not-a-commit", "A" * 40, "a" * 39, "a" * 41),
)
def test_comparison_rejects_malformed_reviewed_source_revisions(revision: str) -> None:
    with pytest.raises(ValueError, match="source revision"):
        online_arx_compare.run_comparison(
            duration_s=2 * online_arx_compare.frame_seconds(),
            source_revision=revision,
        )


def test_comparison_preserves_the_explicit_reviewed_source_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        online_arx_compare,
        "_run_simulator_cell",
        lambda *, arm, plant, scenario, seed, duration_s: _row(
            arm=arm,
            plant=plant,
            scenario=scenario.name,
            seed=seed,
        ),
    )
    monkeypatch.setattr(
        online_arx_compare,
        "_chronological_real_mak_row",
        lambda arm: _real_mak_row(arm=arm),
    )
    monkeypatch.setattr(
        online_arx_compare,
        "source_revision",
        lambda: pytest.fail("comparison must not derive its provenance from the mutable workspace"),
        raising=False,
    )

    artifact = online_arx_compare.run_comparison(
        duration_s=2 * online_arx_compare.frame_seconds(),
        source_revision=_REVIEWED_SOURCE_REVISION,
    )

    assert artifact["source_revision"] == _REVIEWED_SOURCE_REVISION


def test_published_artifact_validates_against_its_reviewed_source_revision(tmp_path: Path) -> None:
    artifact = _complete_artifact()
    output = tmp_path / "online-arx-evidence.json"

    online_arx_compare.write_artifact_atomically(artifact, output)

    assert (
        online_arx_compare.load_artifact(
            output,
            expected_source_revision=_REVIEWED_SOURCE_REVISION,
        )
        == artifact
    )
    with pytest.raises(ValueError, match="source revision"):
        online_arx_compare.load_artifact(
            output,
            expected_source_revision="b" * 40,
        )


def test_experiment_file_is_directly_invokable_from_repo_root() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            sys.executable,
            "docs/superpowers/experiments/online_arx_compare.py",
            "--help",
        ],
        cwd=repo_root,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()


@pytest.mark.parametrize(
    ("entry_point", "plant"),
    ((run_tiny_grillsim, "GrillSim"), (run_tiny_mak_grillsim, "MAKGrillSim")),
    ids=("tiny-grillsim", "tiny-mak-grillsim"),
)
def test_tiny_simulators_publish_production_stack_evidence(
    entry_point: Callable[[], list[dict[str, Any]]], plant: str
) -> None:
    rows = entry_point()

    assert {row["arm"] for row in rows} == set(CONTROLLER_ARMS)
    assert {row["plant"] for row in rows} == {plant}
    for row in rows:
        assert row["production_stack"] == _SIMULATOR_STACK
        assert row["actual_delivered_load_feedback"] is True
        assert row["production_stack"]["pulse_scheduler"] == "PulseScheduler"
        assert row["production_stack"]["runner"] == "ThreadedControllerRunner"
        assert row["timing_applicability"]["solve"] is True
        assert row["raw_timing_ms"]["solve"]


def test_lid_interruption_preserves_controller_cadence_without_duplicate_entry_result() -> None:
    scenarios = _scenario_definitions()
    hold = _run_simulator_cell(
        arm="online",
        plant="GrillSim",
        scenario=scenarios["hold"],
        seed=0,
        duration_s=720,
    )
    lid = _run_simulator_cell(
        arm="online",
        plant="GrillSim",
        scenario=scenarios["lid-interruption"],
        seed=0,
        duration_s=720,
    )

    assert hold["status"] == lid["status"] == "completed"
    assert len(hold["raw_timing_ms"]["solve"]) == 37
    assert len(lid["raw_timing_ms"]["solve"]) == 37
    runner_results = [event for event in lid["online_chronology"] if event["event"] == "runner-result"]
    assert len(runner_results) == 36
    assert [event["revision"] for event in runner_results] == list(
        range(runner_results[0]["revision"], runner_results[0]["revision"] + 36)
    )
    assert len(lid["raw_timing_ms"]["learner"]) < len(lid["raw_timing_ms"]["solve"])


def test_lid_prediction_frames_use_absolute_grid_and_resume_at_380_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    original = online_arx_compare._origin_prediction_scores

    def capture_scores(origins: list[Any], frames: list[FrameObservation]) -> dict[str, float]:
        captured["origins"] = origins
        captured["frames"] = frames
        return original(origins, frames)

    monkeypatch.setattr(online_arx_compare, "_origin_prediction_scores", capture_scores)
    row = _run_simulator_cell(
        arm="baseline",
        plant="GrillSim",
        scenario=_scenario_definitions()["lid-interruption"],
        seed=0,
        duration_s=720,
    )

    assert row["status"] == "completed"
    frames = captured["frames"]
    assert all(
        frame.frame_start_s % 20.0 == 0.0
        and frame.frame_end_s % 20.0 == 0.0
        and frame.frame_end_s - frame.frame_start_s == 20.0
        for frame in frames
    )
    origin_end_times = [frames[origin.frame_index].frame_end_s for origin in captured["origins"]]
    assert 365.0 not in origin_end_times

    assert min(end for end in origin_end_times if end > 345.0) == 380.0


def test_partial_reset_observation_ends_at_actual_reset_time() -> None:
    observation = online_arx_compare._frame_observation(
        frame=SimpleNamespace(
            nominal_start_s=280.0,
            ended_at_s=300.0,
            delivered_on_s=10.0,
            reset_reason="lid",
            skipped=False,
            complete=False,
            latched_request=0.5,
        ),
        temperature_c=100.0,
        target_c=100.0,
        ambient_c=20.0,
        requested_duty=0.5,
        source=online_arx_compare.OutputSource.LID_OPEN,
        lid_open=True,
        maximum_duty=0.9,
        generation=0,
        result_revision=0,
        stale=False,
    )
    assert observation.frame_start_s == 280.0
    assert observation.frame_end_s == 300.0
    assert observation.delivered_on_s == 10.0


def test_lid_entry_keeps_the_pre_lid_completed_frame_and_skips_zero_duration_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[dict[str, Any]] = []
    original = online_arx_compare._frame_observation

    def capture_observation(**kwargs: Any) -> FrameObservation:
        observed.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(online_arx_compare, "_frame_observation", capture_observation)
    row = _run_simulator_cell(
        arm="baseline",
        plant="GrillSim",
        scenario=_scenario_definitions()["lid-interruption"],
        seed=0,
        duration_s=720,
    )

    assert row["status"] == "completed"
    pre_lid = [
        value for value in observed if value["frame"].nominal_end_s == 300.0 and value["frame"].nominal_start_s == 280.0
    ]
    assert len(pre_lid) == 1
    assert pre_lid[0]["source"].value == "controller"
    assert pre_lid[0]["lid_open"] is False
    assert not any(
        value["source"].value == "lid_open" and value["frame"].ended_at_s <= value["frame"].nominal_start_s
        for value in observed
    )


def test_lid_forced_off_transition_and_post_close_request_use_runner_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    production_scheduler = online_arx_compare.PulseScheduler
    production_runner = online_arx_compare.ThreadedControllerRunner

    class RecordingScheduler:
        instances: list["RecordingScheduler"] = []

        def __init__(self) -> None:
            self.delegate = production_scheduler()
            self.calls: list[tuple[float, float, bool, Any]] = []
            self.instances.append(self)

        def advance(self, request: float, at_s: float, actual_auger_on: bool) -> Any:
            decision = self.delegate.advance(request, at_s, actual_auger_on)
            if at_s == 299.0:
                decision = replace(decision, transition=PulseTransition(at_s=299.0, command_on=True))
            elif at_s == 300.0:
                decision = replace(decision, transition=PulseTransition(at_s=300.0, command_on=False))
            self.calls.append((request, at_s, actual_auger_on, decision))
            return decision

        def reset(self, reason: Any) -> Any:
            return self.delegate.reset(reason)

    class RecordingRunner:
        instances: list["RecordingRunner"] = []

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.delegate = production_runner(*args, **kwargs)
            self.revisions: list[Any] = []
            self.outputs: list[Any] = []
            self.seen_revision = -1
            self.instances.append(self)

        def latest(self) -> Any:
            result = self.delegate.latest()
            if result.revision > self.seen_revision:
                self.revisions.append(result)
                self.seen_revision = result.revision
            return result

        def __getattr__(self, name: str) -> Any:
            return getattr(self.delegate, name)

        def set_output(self, output: Any) -> Any:
            self.outputs.append(output)
            return self.delegate.set_output(output)

    monkeypatch.setattr(online_arx_compare, "PulseScheduler", RecordingScheduler)
    monkeypatch.setattr(online_arx_compare, "ThreadedControllerRunner", RecordingRunner)
    row = _run_simulator_cell(
        arm="baseline",
        plant="GrillSim",
        scenario=_scenario_definitions()["lid-interruption"],
        seed=0,
        duration_s=720,
    )

    main_scheduler = next(
        instance for instance in RecordingScheduler.instances if any(at_s == 300.0 for _, at_s, _, _ in instance.calls)
    )
    (_, _, on_at_lid, lid_interruption) = next(call for call in main_scheduler.calls if call[1] == 300.0)
    assert on_at_lid is True
    assert lid_interruption.transition is not None
    post_close = next(
        instance for instance in RecordingScheduler.instances if instance.calls and instance.calls[0][1] == 345.0
    )
    runner = RecordingRunner.instances[0]
    refreshed_lid_request = next(result.allocation.auger_duty for result in runner.revisions if result.revision == 18)
    assert post_close.calls[0][0] == pytest.approx(refreshed_lid_request)
    assert not [output for output in runner.outputs if output.timestamp in {320.0, 340.0}]
    at_lid_entry = [output for output in runner.outputs if output.timestamp == 300.0]
    assert at_lid_entry[-1].ratio == 0.0
    assert at_lid_entry[-1].source.value == "lid_open"
    delivered_after_close = sum(
        int(decision.transition.command_on if decision.transition is not None else actual_on)
        for _, at_s, actual_on, decision in post_close.calls
        if at_s < 360.0
    )
    (at_360,) = [output for output in runner.outputs if output.timestamp == 360.0]
    assert at_360.source.value == "controller"
    assert at_360.ratio == pytest.approx(delivered_after_close / 60.0)

    expected_transitions = sum(
        decision.transition is not None for _, at_s, _, decision in main_scheduler.calls if at_s < 300.0
    )
    expected_transitions += 1
    expected_transitions += sum(
        decision.transition is not None for _, at_s, _, decision in post_close.calls if at_s < 720.0
    )
    assert row["metrics"]["transitions_per_hour"] == pytest.approx(expected_transitions * 3600.0 / 720.0)

    def actual_on_seconds(calls: list[tuple[float, float, bool, Any]], *, before: float) -> int:
        return sum(
            int(decision.transition.command_on if decision.transition is not None else actual_on)
            for _, at_s, actual_on, decision in calls
            if at_s < before
        )

    expected_auger_on_s = actual_on_seconds(main_scheduler.calls, before=300.0)
    expected_auger_on_s += actual_on_seconds(post_close.calls, before=720.0)
    assert row["metrics"]["auger_on_s"] == pytest.approx(expected_auger_on_s)


def test_braking_errors_begin_strictly_after_the_decrease_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []
    original = online_arx_compare._control_metrics

    def capture_metrics(**kwargs: Any) -> tuple[dict[str, float | None], dict[str, bool]]:
        captured.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(online_arx_compare, "_control_metrics", capture_metrics)
    row = _run_simulator_cell(
        arm="baseline",
        plant="GrillSim",
        scenario=_scenario_definitions()["target-decrease-coast"],
        seed=0,
        duration_s=360,
    )

    assert row["metric_applicability"]["braking_error_c"] is True
    assert len(captured) == 1
    arguments = captured[0]
    assert arguments["braking_errors"] == [
        abs(temperature - target)
        for temperature, target in zip(
            arguments["temperatures"][300:],
            arguments["targets"][300:],
        )
    ]
