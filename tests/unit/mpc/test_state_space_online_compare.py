"""Strict evidence contracts for the state-space online challenger experiment."""

from copy import deepcopy
import json
from math import isfinite
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

import pytest

from docs.superpowers.experiments import state_space_online_compare as compare

_REVISION = "a" * 40


def _attempt(*, accepted: bool, measured_alignment: bool = False) -> dict[str, Any]:
    return {
        "order": 1,
        "delay": 1,
        "sample_count": 32,
        "hankel_shape": [8, 24],
        "singular_values": [4.0, 1.0],
        "effective_rank": 2,
        "condition_number": 4.0,
        "projection_applied": False,
        "steady_gain": 1.0,
        "alignment_error_c": 0.2 if measured_alignment else None,
        "prediction_score": 0.1,
        "braking_score": 0.1,
        "rejection_reasons": [] if accepted else ["rank-deficient"],
        "elapsed_ms": 1.0,
    }


def _refresh(*, accepted: bool, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "accepted": accepted,
        "terminal_reason": None if accepted else "rank-deficient",
        "selected_order": 1 if accepted else None,
        "selected_delay": 1 if accepted else None,
        "model_digest": "b" * 64,
        "attempts": [_attempt(accepted=accepted, measured_alignment=accepted and kind == "replacement")],
    }


def _challenger_pairing(*, state_space: bool, evaluations: int = 1) -> dict[str, Any] | None:
    if not state_space:
        return None
    digest = "b" * 64
    return {
        "observed_digests": [digest],
        "instance_digest": "c" * 64,
        "role_generation": 0,
        "prediction_digests": [digest],
        "prediction_origins": [
            {
                "frame_index": 0,
                "horizon_steps": 3,
                "model_digest": digest,
                "event_source": "solve-timing",
                "event_sequence": 0,
            }
        ],
        "prediction_events": [
            {
                "frame_index": 0,
                "horizon_steps": 3,
                "model_digest": digest,
                "event_source": "solve-timing",
                "event_sequence": 0,
            }
        ],
        "refresh_digests": [digest, digest],
        "alignment_digests": [digest],
        "timing_digests": {"update": [digest], "refresh": [digest], "solve": [digest]},
        "adaptation_digests": [digest] * evaluations,
    }


def _command_owner_status() -> dict[str, int | str]:
    return {"active_model_kind": "scheduled-arx", "role_generation": 0}


def _row(*, plant: str, mismatch: str, seed: int, arm: str) -> dict[str, Any]:
    state_space = arm == "innovation-state-space"
    return {
        "cell_key": f"{plant}:{mismatch}:{seed}:{arm}",
        "plant": plant,
        "mismatch": mismatch,
        "seed": seed,
        "arm": arm,
        "mode": "closed-loop",
        "status": "completed",
        "command_owner": "scheduled-arx",
        "command_owner_status": _command_owner_status(),
        "owner_initialization": {
            "source": "simulator-pulse-prefix",
            "frame_count": 48,
            "frames_digest": "d" * 64,
        },
        "shadow_only": state_space,
        "effective_duration_s": 800,
        "mismatch_evidence": {
            "parameter": {"wrong-delay": "n_delay", "wrong-pole": "theta", "wrong-gain": "K_Q"}[mismatch],
            "configured_value": 1.0,
            "nominal_value": 2.0,
        },
        "commands_digest": "same-command-stream",
        "adaptation": {
            "outcomes": 1,
            "evaluation_count": 1,
            "evaluations": [
                {
                    "promotion_eligible": state_space,
                    "experiment_gate_blocked": state_space,
                    "prospective_digest": "a" * 64 if state_space else None,
                    "state_space_digest": "b" * 64 if state_space else None,
                    "challenger_instance_digest": "c" * 64 if state_space else None,
                    "role_generation": 0 if state_space else None,
                }
            ],
            "safety_inhibits": 0,
            "promotion_eligible": state_space,
            "experiment_gate_blocked": state_space,
        },
        "prediction_metrics": {"rmse_60_c": 0.3, "rmse_300_c": 0.5, "origin_count_60": 1, "origin_count_300": 1},
        "control_metrics": {"rmse_f": 1.0, "overshoot_f": 2.0, "settle_s": 20.0},
        "raw_timing_ms": {"update": [0.1], "refresh": [1.0], "solve": [0.2]},
        "refreshes": (
            [_refresh(accepted=True, kind="bootstrap"), _refresh(accepted=True, kind="replacement")]
            if state_space
            else []
        ),
        "alignment": {"attempted": state_space, "accepted": state_space, "max_error_c": 0.2 if state_space else None},
        "challenger_pairing": _challenger_pairing(state_space=state_space),
        "model_kind": arm,
    }


def _complete_artifact() -> dict[str, Any]:
    rows = [
        _row(plant=plant, mismatch=mismatch, seed=seed, arm=arm)
        for plant in compare.SIMULATOR_PLANTS
        for mismatch in compare.MISMATCHES
        for seed in compare.FIXED_SEEDS
        for arm in compare.ARMS
    ]
    real_rows = [
        {
            "cell_key": f"real-MAK:nominal:{arm}",
            "plant": "real-MAK",
            "mismatch": "nominal",
            "seed": None,
            "arm": arm,
            "mode": "prediction-only",
            "status": "completed",
            "chronological": True,
            "normalized_input": "normalized_load_from_auger_duty",
            "command_owner": "historical-requested-input",
            "command_owner_status": None,
            "shadow_only": arm == "innovation-state-space",
            "effective_duration_s": 0,
            "mismatch_evidence": {"parameter": "historical", "configured_value": 1.0, "nominal_value": 1.0},
            "adaptation": {
                "outcomes": 1,
                "evaluation_count": 0,
                "evaluations": [],
                "safety_inhibits": 0,
                "promotion_eligible": False,
                "experiment_gate_blocked": False,
            },
            "prediction_metrics": {"rmse_60_c": 0.3, "rmse_300_c": 0.5, "origin_count_60": 1, "origin_count_300": 1},
            "control_metrics": None,
            "raw_timing_ms": {"update": [0.1], "refresh": [1.0], "solve": [0.2]},
            "refreshes": (
                [_refresh(accepted=True, kind="bootstrap"), _refresh(accepted=True, kind="replacement")]
                if arm == "innovation-state-space"
                else []
            ),
            "alignment": {
                "attempted": arm == "innovation-state-space",
                "accepted": arm == "innovation-state-space",
                "max_error_c": 0.2 if arm == "innovation-state-space" else None,
            },
            "challenger_pairing": _challenger_pairing(state_space=arm == "innovation-state-space", evaluations=0),
            "model_kind": arm,
        }
        for arm in compare.ARMS
    ]
    artifact = {
        "schema_version": compare.ARTIFACT_SCHEMA_VERSION,
        "source_revision": _REVISION,
        "fixed_seeds": list(compare.FIXED_SEEDS),
        "rows": rows,
        "real_mak_rows": real_rows,
        "complete_cells": len(rows) + len(real_rows),
        "expected_cells": len(rows) + len(real_rows),
        "duplicates": [],
        "decision": {"ship": False, "reasons": ["conservative test refusal"]},
    }
    artifact["decision"] = compare.decide_ship(artifact)
    return artifact


def test_contract_accepts_complete_finite_provenanced_artifact() -> None:
    artifact = _complete_artifact()

    assert compare.artifact_contract_errors(artifact) == []
    assert artifact["decision"] == compare.decide_ship(artifact)


def test_contract_rejects_incomplete_duplicate_nonfinite_and_wrong_provenance() -> None:
    artifact = _complete_artifact()
    malformed = deepcopy(artifact)
    malformed["rows"].pop()
    malformed["duplicates"] = ["GrillSim:wrong-delay:0:scheduled-arx"]
    malformed["rows"][0]["raw_timing_ms"]["update"] = [float("nan")]
    malformed["real_mak_rows"][0]["normalized_input"] = "raw-duty"

    errors = compare.artifact_contract_errors(malformed)

    assert any("incomplete" in error for error in errors)
    assert any("duplicate" in error for error in errors)
    assert any("non-finite" in error for error in errors)
    assert any("normalized" in error for error in errors)


def test_contract_rejects_inconsistent_or_unproven_evaluation_evidence() -> None:
    artifact = _complete_artifact()
    state_row = next(row for row in artifact["rows"] if row["arm"] == "innovation-state-space")
    state_row["adaptation"]["evaluations"] = [
        {"promotion_eligible": True, "experiment_gate_blocked": True, "prospective_digest": "not-a-digest"}
    ]

    assert "evaluation evidence" in compare.artifact_contract_errors(artifact)


def test_contract_rejects_hard_coded_scheduled_arx_owner_when_runtime_reports_grey_box() -> None:
    artifact = _complete_artifact()
    artifact["rows"][0]["command_owner_status"]["active_model_kind"] = "grey-box"

    assert "command authority evidence" in compare.artifact_contract_errors(artifact)


def test_contract_rejects_state_space_evidence_paired_to_another_challenger_instance() -> None:
    artifact = _complete_artifact()
    state_row = next(row for row in artifact["rows"] if row["arm"] == "innovation-state-space")
    state_row["challenger_pairing"]["prediction_digests"] = ["d" * 64]

    assert "challenger evidence pairing" in compare.artifact_contract_errors(artifact)


def test_contract_rejects_final_only_prediction_digest_sequence() -> None:
    artifact = _complete_artifact()
    state_row = next(row for row in artifact["rows"] if row["arm"] == "innovation-state-space")
    pairing = state_row["challenger_pairing"]
    pairing["observed_digests"] = ["a" * 64, "b" * 64]
    pairing["prediction_origins"] = [
        {"frame_index": 0, "model_digest": "a" * 64},
        {"frame_index": 1, "model_digest": "b" * 64},
    ]
    pairing["prediction_digests"] = ["b" * 64, "b" * 64]
    state_row["raw_timing_ms"]["update"] = [0.1, 0.2]
    pairing["timing_digests"]["update"] = ["a" * 64, "b" * 64]

    assert "challenger evidence pairing" in compare.artifact_contract_errors(artifact, require_decision=False)
    assert compare.decide_ship(artifact)["ship"] is False


def test_contract_rejects_prediction_digest_or_origin_not_bound_to_its_solve_event() -> None:
    artifact = _complete_artifact()
    state_row = next(row for row in artifact["rows"] if row["arm"] == "innovation-state-space")
    pairing = state_row["challenger_pairing"]
    pairing["prediction_digests"][0] = "d" * 64
    pairing["prediction_origins"][0]["model_digest"] = "d" * 64

    assert "challenger evidence pairing" in compare.artifact_contract_errors(artifact, require_decision=False)
    assert compare.decide_ship(artifact)["ship"] is False


@pytest.mark.parametrize("event_sequences", [(0, 0), (1, 0)], ids=["reused", "reordered"])
def test_contract_rejects_non_bijective_prediction_solve_event_sequences(
    event_sequences: tuple[int, int],
) -> None:
    artifact = _complete_artifact()
    state_row = next(row for row in artifact["rows"] if row["arm"] == "innovation-state-space")
    pairing = state_row["challenger_pairing"]
    pairing["prediction_digests"].append("b" * 64)
    state_row["raw_timing_ms"]["solve"].append(0.2)
    pairing["timing_digests"]["solve"].append("b" * 64)
    for name in ("prediction_origins", "prediction_events"):
        records = pairing[name]
        records[0]["event_sequence"] = event_sequences[0]
        duplicate = deepcopy(records[0])
        duplicate["event_sequence"] = event_sequences[1]
        records.append(duplicate)

    assert "challenger evidence pairing" in compare.artifact_contract_errors(artifact, require_decision=False)
    assert compare.decide_ship(artifact)["ship"] is False


def test_contract_accepts_solve_event_digest_distinct_from_pre_update_digest() -> None:
    artifact = _complete_artifact()
    state_row = next(row for row in artifact["rows"] if row["arm"] == "innovation-state-space")
    pairing = state_row["challenger_pairing"]
    pairing["observed_digests"] = ["a" * 64]
    pairing["timing_digests"]["update"] = ["a" * 64]
    pairing["adaptation_digests"] = ["a" * 64]
    state_row["adaptation"]["evaluations"][0]["state_space_digest"] = "a" * 64

    assert compare.artifact_contract_errors(artifact, require_decision=False) == []
    assert compare.decide_ship(artifact)["ship"] is True


def test_contract_rejects_negative_selected_alignment_residual() -> None:
    artifact = _complete_artifact()
    state_row = next(row for row in artifact["rows"] if row["arm"] == "innovation-state-space")
    state_row["refreshes"][-1]["attempts"][0]["alignment_error_c"] = -0.01

    assert compare._refresh_diagnostic_errors(state_row["refreshes"][-1])
    assert compare._selected_replacement_alignment_errors(state_row["refreshes"]) == []
    assert "refresh diagnostics" in compare.artifact_contract_errors(artifact, require_decision=False)
    assert compare.decide_ship(artifact)["ship"] is False


def test_contract_rejects_accepted_rank_deficient_selected_attempt() -> None:
    artifact = _complete_artifact()
    state_row = next(row for row in artifact["rows"] if row["arm"] == "innovation-state-space")
    selected = state_row["refreshes"][-1]["attempts"][0]
    selected["effective_rank"] = 0

    assert "refresh diagnostics" in compare.artifact_contract_errors(artifact, require_decision=False)
    assert compare.decide_ship(artifact)["ship"] is False


def test_contract_rejects_incoherent_final_rejection_diagnostics() -> None:
    artifact = _complete_artifact()
    state_row = next(row for row in artifact["rows"] if row["arm"] == "innovation-state-space")
    final = state_row["refreshes"][-1]
    final.update(_refresh(accepted=False, kind="replacement"))
    final["terminal_reason"] = "implausible-gain"

    assert "refresh diagnostics" in compare.artifact_contract_errors(artifact, require_decision=False)
    assert compare.decide_ship(artifact)["ship"] is False


def test_refresh_contract_accepts_coherent_alignment_rejection_with_other_candidates() -> None:
    refresh = _refresh(accepted=False, kind="replacement")
    refresh["terminal_reason"] = "alignment-failed"
    refresh["attempts"][0]["rejection_reasons"] = ["alignment-failed"]
    refresh["attempts"].append(_attempt(accepted=True))
    refresh["attempts"][-1]["delay"] = 2

    assert compare._refresh_diagnostic_errors(refresh) == []


def test_refresh_contract_accepts_aggregate_no_valid_candidate_terminal() -> None:
    refresh = _refresh(accepted=False, kind="bootstrap")
    refresh["terminal_reason"] = "no-valid-candidate"

    assert compare._refresh_diagnostic_errors(refresh) == []


def test_contract_rejects_unproven_runtime_owner_initialization() -> None:
    artifact = _complete_artifact()
    artifact["rows"][0]["owner_initialization"]["source"] = "hand-generated-recurrence"

    assert "command owner initialization" in compare.artifact_contract_errors(artifact, require_decision=False)
    assert compare.decide_ship(artifact)["ship"] is False


def test_simulator_fails_explicitly_when_emitted_calibration_cannot_identify_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unidentifiable_fit(self: Any, frames: list[Any]) -> None:
        raise ValueError("no excitation")

    monkeypatch.setattr(compare.ScheduledARX, "fit", unidentifiable_fit)

    with pytest.raises(RuntimeError, match="unidentifiable from emitted calibration prefix"):
        compare._simulator_frames(plant="GrillSim", mismatch="wrong-delay", seed=0, duration_s=20)


def test_ship_requires_every_gate_and_an_accepted_wrong_model_recovery() -> None:
    artifact = _complete_artifact()
    next(row for row in artifact["rows"] if row["arm"] == "innovation-state-space")["raw_timing_ms"]["refresh"] = [
        251.0
    ]
    artifact["decision"] = {"ship": True, "reasons": []}

    errors = compare.artifact_contract_errors(artifact)

    assert any("ship" in error for error in errors)
    assert compare.decide_ship(_complete_artifact())["ship"] is True


def test_contract_rejects_non_null_failures_and_missing_forecast_origins() -> None:
    artifact = _complete_artifact()
    state_row = next(row for row in artifact["rows"] if row["arm"] == "innovation-state-space")
    state_row["failure"] = {"reason": "RuntimeError", "message": "model failed"}
    state_row["prediction_metrics"]["origin_count_60"] = 0
    state_row["prediction_metrics"]["origin_count_300"] = 0

    errors = compare.artifact_contract_errors(artifact, require_decision=False)

    assert "row failure" in errors
    assert "forecast origins" in errors
    assert compare.decide_ship(artifact)["ship"] is False


def test_contract_rejects_alignment_accepted_only_by_bootstrap_fit() -> None:
    artifact = _complete_artifact()
    state_row = next(row for row in artifact["rows"] if row["arm"] == "innovation-state-space")
    state_row["refreshes"] = [_refresh(accepted=True, kind="bootstrap")]

    assert "alignment evidence" in compare.artifact_contract_errors(artifact, require_decision=False)
    assert compare.decide_ship(artifact)["ship"] is False


def test_ship_pairs_chronological_real_mak_prediction_quality_and_origins() -> None:
    artifact = _complete_artifact()
    state_row = next(row for row in artifact["real_mak_rows"] if row["arm"] == "innovation-state-space")
    state_row["prediction_metrics"]["rmse_300_c"] = 0.56

    decision = compare.decide_ship(artifact)

    assert decision["ship"] is False
    assert "state-space prediction threshold" in decision["reasons"]

    state_row["prediction_metrics"]["origin_count_60"] = 0
    assert "forecast origins" in compare.artifact_contract_errors(artifact, require_decision=False)
    assert compare.decide_ship(artifact)["ship"] is False


def test_model_exception_produces_infrastructure_failed_incomplete_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenARX:
        def __init__(self, *_: object) -> None:
            pass

        def fit(self, _: object) -> None:
            raise RuntimeError("broken fit")

    monkeypatch.setattr(compare, "ScheduledARX", BrokenARX)

    row = compare._model_row(arm="scheduled-arx", frames=[], common={})

    assert row["status"] == "infrastructure-failed"
    assert row["failure"] == {"reason": "RuntimeError", "message": "broken fit"}
    assert row["prediction_metrics"] == {
        "rmse_60_c": None,
        "rmse_300_c": None,
        "origin_count_60": 0,
        "origin_count_300": 0,
    }


def test_real_mak_unidentifiable_state_space_input_is_a_completed_nonshipping_outcome() -> None:
    rows = compare._real_mak_rows()
    state_row = next(row for row in rows if row["arm"] == "innovation-state-space")

    assert state_row["status"] == "completed"
    assert state_row["failure"] == {"reason": "unidentifiable-input"}
    assert state_row["refreshes"][0]["kind"] == "bootstrap"
    assert state_row["refreshes"][0]["accepted"] is False
    assert state_row["refreshes"][0].get("model_digest") is None
    assert compare._is_real_mak_unidentifiable(state_row)
    assert state_row["prediction_metrics"] == {
        "rmse_60_c": None,
        "rmse_300_c": None,
        "origin_count_60": 0,
        "origin_count_300": 0,
    }
    artifact = _complete_artifact()
    artifact["real_mak_rows"] = rows
    artifact["decision"] = compare.decide_ship(artifact)

    assert compare.artifact_contract_errors(artifact) == []
    assert artifact["decision"]["ship"] is False
    assert "real-MAK state-space input is unidentifiable" in artifact["decision"]["reasons"]


def test_due_refresh_observation_latency_is_recorded_in_refresh_p99(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Clock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

        def advance(self, milliseconds: float) -> None:
            self.value += milliseconds / 1_000.0

    clock = Clock()

    class RefreshingStateSpace:
        def __init__(self, *_: object) -> None:
            self._refreshes = 0
            self._last_refresh_attempt_s: float | None = None
            self._refresh_attempts = 0

        def fit(self, _: object) -> SimpleNamespace:
            return SimpleNamespace(accepted=True)

        def observe(self, _: object) -> None:
            if self._refresh_attempts < 2:
                self._refresh_attempts += 1
                self._last_refresh_attempt_s = 20.0 * self._refresh_attempts
                clock.advance(275.0)

        @property
        def diagnostics(self) -> object:
            return object()

        def snapshot(self) -> dict[str, object]:
            return {"status": {"last_refresh_time_s": self._last_refresh_attempt_s}}

        @classmethod
        def from_snapshot(cls, _: object) -> "RefreshingStateSpace":
            return cls()

        def refresh(self, _: object) -> object:
            raise AssertionError("proof refreshes must be triggered by observations")

        def affine_prediction(self, horizon: int, *_: object) -> SimpleNamespace:
            return SimpleNamespace(
                free_output_c=np.zeros(horizon),
                input_response_c=np.zeros((horizon, horizon)),
            )

    frames = [
        compare.FrameObservation(
            frame_start_s=float(index * 20),
            frame_end_s=float((index + 1) * 20),
            temp_c=100.0,
            setpoint_c=120.0,
            ambient_c=20.0,
            requested_q=0.5,
            realized_q=0.5,
            requested_auger_duty=0.5,
            delivered_on_s=10.0,
            requested_fan_duty=None,
            actual_fan_duty=None,
            result_revision=index,
            output_source="test",
            lid_open=False,
            safety_inhibited=False,
            manual_override=False,
            stale=False,
            reset=False,
            skipped=False,
            continuous=True,
            role_generation=0,
        )
        for index in range(32)
    ]
    monkeypatch.setattr(compare, "InnovationStateSpace", RefreshingStateSpace)
    monkeypatch.setattr(compare, "perf_counter", clock)
    monkeypatch.setattr(
        compare,
        "_diagnostic",
        lambda _, *, kind: _refresh(accepted=kind == "bootstrap", kind=kind),
    )

    row = compare._model_row(arm="innovation-state-space", frames=frames, common={})

    assert any(sample == pytest.approx(275.0) for sample in row["raw_timing_ms"]["refresh"])
    assert float(np.percentile(row["raw_timing_ms"]["refresh"], 99.0)) > compare._REFRESH_BUDGET_MS
    replacements = [refresh for refresh in row["refreshes"] if refresh["kind"] == "replacement"]
    assert len(replacements) == 2
    assert all(refresh["accepted"] is False for refresh in replacements)


def test_complete_evidence_decision_is_deterministic() -> None:
    artifact = _complete_artifact()

    assert compare.decide_ship(deepcopy(artifact)) == compare.decide_ship(deepcopy(artifact))


def test_negative_decision_refuses_catalog_exposure() -> None:
    artifact = _complete_artifact()
    for row in artifact["rows"]:
        if row["arm"] == "innovation-state-space":
            row["adaptation"]["promotion_eligible"] = False
            row["adaptation"]["evaluations"][0] = {
                "promotion_eligible": False,
                "experiment_gate_blocked": False,
                "prospective_digest": None,
            }
    artifact["decision"] = compare.decide_ship(artifact)
    catalog = json.loads((Path(__file__).resolve().parents[3] / "controller" / "controllers.json").read_text())
    assert compare.decision_code_errors(artifact, catalog) == []
    assert compare.decision_code_errors(artifact, {"online_model": "state-space"}) == [
        "state-space catalog exposure contradicts ship=false"
    ]


@pytest.mark.parametrize("revision", ["A" * 40, "a" * 39, "g" * 40])
def test_comparison_rejects_noncanonical_source_revision(revision: str) -> None:
    with pytest.raises(ValueError, match="source revision"):
        compare.run_comparison(source_revision=revision, duration_s=20)


def test_comparison_requires_a_complete_identification_duration() -> None:
    with pytest.raises(ValueError, match="duration_s"):
        compare.run_comparison(source_revision=_REVISION, duration_s=800)


def test_cli_requires_source_revision_and_output() -> None:
    with pytest.raises(SystemExit) as error:
        compare.main([])
    assert error.value.code == 2


def test_generated_rows_are_finite_and_same_commands_for_both_arms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compare, "SIMULATOR_PLANTS", ("GrillSim",))
    monkeypatch.setattr(compare, "MISMATCHES", ("wrong-delay",))
    monkeypatch.setattr(compare, "FIXED_SEEDS", (0,))
    monkeypatch.setattr(compare, "_real_mak_rows", lambda: _complete_artifact()["real_mak_rows"])
    artifact = compare.run_comparison(source_revision=_REVISION, duration_s=1_800)
    assert compare.artifact_contract_errors(artifact) == []
    assert all(
        isfinite(value)
        for row in [*artifact["rows"], *artifact["real_mak_rows"]]
        for samples in row["raw_timing_ms"].values()
        for value in samples
    )
    grouped: dict[tuple[str, str, int], set[str]] = {}
    for row in artifact["rows"]:
        grouped.setdefault((row["plant"], row["mismatch"], row["seed"]), set()).add(row["commands_digest"])
    assert all(len(digests) == 1 for digests in grouped.values())
    assert all(row["effective_duration_s"] == 1_800 for row in artifact["rows"])
    assert all(
        row["mismatch_evidence"]["configured_value"] != row["mismatch_evidence"]["nominal_value"]
        for row in artifact["rows"]
    )
    assert all(
        row["prediction_metrics"]["origin_count_60"] > 0
        and row["prediction_metrics"]["origin_count_300"] > 0
        and row["adaptation"]["outcomes"] > 0
        for row in artifact["rows"]
    )
