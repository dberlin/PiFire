from __future__ import annotations

from dataclasses import FrozenInstanceError
from hashlib import sha256

import pytest

from controller.linear_mpc.confidence import (
    ConfidenceConfig,
    ConfidenceStatus,
    evaluate_confidence,
)


_DIGEST = sha256(b"challenger").hexdigest()
_INCUMBENT = sha256(b"incumbent").hexdigest()
_PROVENANCE = sha256(b"provenance").hexdigest()


def _record(kind: str, payload: dict[str, object], *, cook: str = "cook-a", generation: int = 1) -> dict[str, object]:
    return {
        "evidence_id": f"{kind}-{cook}-{generation}-{len(payload)}",
        "kind": kind,
        "session_id": f"session-{cook}",
        "cook_id": cook,
        "timestamp_ms": 1,
        "role_generation": generation,
        "model_digest": _DIGEST,
        "provenance_digest": _PROVENANCE,
        "schema_version": 1,
        "payload": payload,
    }


def qualifying_evidence() -> list[dict[str, object]]:
    records = [
        _record("calibration_summary", {"accepted": True, "stage": stage, "full_rank": True})
        for stage in ("low", "middle", "high", "coast")
    ]
    records.append(
        _record(
            "refresh_diagnostics",
            {
                "accepted": True,
                "full_rank": True,
                "finite_diagnostics": True,
                "pole_magnitude": 0.9,
                "gain": 1.0,
                "delay_steps": 3,
                "covariance_finite": True,
                "alignment_error_c": 1.0,
                "snapshot_round_trip": True,
                "sequential_wins": 2,
                "generation_continuity": True,
                "atomic_persistence": True,
                "model_integrity": True,
                "provenance_integrity": True,
                "schema_integrity": True,
                "untouched_future_rows": True,
                "production_prospective": True,
            },
        )
    )
    for cook in ("cook-a", "cook-b"):
        for horizon in (3, 15, 45, 90, 180):
            for phase in ("heating", "coasting"):
                for sequence in range(horizon):
                    sign = -1.0 if sequence % 2 else 1.0
                    records.append(
                        _record(
                            "forecast_origin",
                            {
                                "origin_sequence": sequence,
                                "horizon_steps": horizon,
                                "incumbent_error_c": 2.0 * sign,
                                "challenger_error_c": 1.0 * sign,
                                "temperature_band": "middle",
                                "phase": phase,
                                "ambient_source": "configured",
                                "calibration_fit": False,
                                "untouched_future": True,
                            },
                            cook=cook,
                        )
                    )
    return records
def _timing() -> dict[str, object]:
    return {"p99_ms": 249.0, "hardware_provenance": "target-hardware"}


def _config() -> ConfidenceConfig:
    return ConfidenceConfig(bootstrap_replicates=19, bootstrap_seed=7)


def test_qualifying_ledger_is_ready_for_review_without_ownership_change() -> None:
    report = evaluate_confidence(
        qualifying_evidence(),
        activation_state={"status": "collecting", "active_kind": "grey_box"},
        target_timing=_timing(),
        config=_config(),
    )

    assert report.status is ConfidenceStatus.READY_FOR_REVIEW
    assert report.active_kind == "grey_box"
    assert report.blockers == ()
    assert all(gate.passed for gate in report.gates)


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda rows: rows.__setitem__(0, _record("calibration_summary", {"accepted": True, "stage": "missing"})), "calibration-completeness"),
        (lambda rows: rows[4]["payload"].__setitem__("full_rank", False), "identifiability"),
        (lambda rows: rows[4]["payload"].__setitem__("pole_magnitude", 1.0), "pole-magnitude"),
        (lambda rows: rows[4]["payload"].__setitem__("gain", 0.0), "positive-gain"),
        (lambda rows: rows[4]["payload"].__setitem__("delay_steps", 16), "delay-limit"),
        (lambda rows: rows[4]["payload"].__setitem__("covariance_finite", False), "finite-covariance"),
        (lambda rows: rows[4]["payload"].__setitem__("alignment_error_c", 2.1), "state-alignment"),
        (lambda rows: rows[4]["payload"].__setitem__("snapshot_round_trip", False), "snapshot-round-trip"),
        (
            lambda rows: [
                row["payload"].__setitem__("challenger_error_c", 3.0)
                for row in rows
                if row["kind"] == "forecast_origin" and row["payload"]["horizon_steps"] == 3
            ],
            "absolute-rmse-3",
        ),
        (
            lambda rows: [
                row["payload"].__setitem__("challenger_error_c", 1.0)
                for row in rows
                if row["kind"] == "forecast_origin"
            ],
            "signed-bias",
        ),
        (
            lambda rows: [
                row["payload"].__setitem__("temperature_band", "")
                for row in rows
                if row["kind"] == "forecast_origin"
            ],
            "temperature-band-error",
        ),
        (
            lambda rows: [
                row["payload"].__setitem__("challenger_error_c", 3.0)
                for row in rows
                if row["kind"] == "forecast_origin" and row["payload"]["phase"] == "coasting"
            ],
            "braking-error",
        ),
        (
            lambda rows: [
                row["payload"].__setitem__("challenger_error_c", 2.0)
                for row in rows
                if row["kind"] == "forecast_origin"
            ],
            "relative-rmse",
        ),
        (lambda rows: rows.__setitem__(-1, rows[-1] | {"cook_id": "cook-a", "session_id": "session-cook-a"}), "bootstrap-unavailable"),
        (lambda rows: rows[4]["payload"].__setitem__("sequential_wins", 1), "sequential-wins"),
        (lambda rows: rows[4]["payload"].__setitem__("generation_continuity", False), "generation-continuity"),
        (lambda rows: rows[4]["payload"].__setitem__("atomic_persistence", False), "atomic-persistence"),
        (lambda rows: rows[4]["payload"].__setitem__("provenance_integrity", False), "provenance-integrity"),
        (lambda rows: rows[4]["payload"].__setitem__("model_integrity", False), "model-integrity"),
        (lambda rows: rows[4]["payload"].__setitem__("schema_integrity", False), "schema-integrity"),
        (
            lambda rows: rows[5]["payload"].__setitem__("untouched_future", False),
            "untouched-future-rows",
        ),
        (lambda rows: rows[4]["payload"].__setitem__("production_prospective", False), "production-prospective-construction"),
    ],
)
def test_each_independent_gate_fails_closed(mutate, reason: str) -> None:
    evidence = qualifying_evidence()
    mutate(evidence)
    report = evaluate_confidence(evidence, activation_state={"status": "collecting"}, target_timing=_timing(), config=_config())

    assert reason in report.blockers
    assert report.status is not ConfidenceStatus.READY_FOR_REVIEW


def test_target_hardware_p99_requires_target_provenance() -> None:
    report = evaluate_confidence(
        qualifying_evidence(), activation_state={"status": "collecting"}, target_timing={"p99_ms": 251.0, "hardware_provenance": "workstation"}, config=_config()
    )

    assert "target-timing" in report.blockers


def test_duplicate_rows_and_one_cook_cannot_manufacture_confidence() -> None:
    evidence = [row for row in qualifying_evidence() if row["cook_id"] == "cook-a"]
    evidence.extend(evidence)
    report = evaluate_confidence(evidence, activation_state={"status": "collecting"}, target_timing=_timing(), config=_config())

    assert "bootstrap-unavailable" in report.blockers
    assert "cook-effective-weight" in report.blockers


def test_authoritative_active_fallback_and_schema_states_are_preserved() -> None:
    evidence = qualifying_evidence()
    assert evaluate_confidence(evidence, activation_state={"status": "active"}, target_timing=_timing(), config=_config()).status is ConfidenceStatus.ACTIVE
    assert evaluate_confidence(evidence, activation_state={"status": "fallback"}, target_timing=_timing(), config=_config()).status is ConfidenceStatus.FALLBACK
    assert evaluate_confidence(evidence, activation_state={"status": "schema-invalidated"}, target_timing=_timing(), config=_config()).status is ConfidenceStatus.SCHEMA_INVALIDATED


def test_confidence_values_are_frozen_and_slotted() -> None:
    config = _config()
    with pytest.raises(FrozenInstanceError):
        config.bootstrap_seed = 4  # type: ignore[misc]
