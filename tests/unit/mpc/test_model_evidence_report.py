"""Unified model-learning report vocabulary and authority contracts."""

from __future__ import annotations


import pytest

from common.model_evidence import EvidenceKind, ModelEvidenceRecord, RecorderGapEvidence
from controller.model_learning import report as report_module
from controller.model_learning.contracts import CandidateOrigin, CheckStatus, FitStatus, LearningStatus
from controller.model_learning.report import build_learning_report, current_learning_report


_CANDIDATE = "b" * 64
_INCUMBENT = "a" * 64


def _evidence(evidence_id: str = "gap-1") -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind.RECORDER_GAP,
        session_id="session-a",
        cook_id="cook-a",
        timestamp_ms=1,
        role_generation=4,
        model_digest=_CANDIDATE,
        provenance_digest=_INCUMBENT,
        payload=RecorderGapEvidence(lost_record_count=1, reason="recorder-gap"),
    )


def _activation(*, phase: str = "prepared") -> dict[str, object]:
    return {
        "phase": phase,
        "incumbent_digest": _INCUMBENT,
        "candidate_digest": _CANDIDATE,
        "role_generation": 4,
        "candidate_generation": 9,
        "decision_id": "decision-9",
        "origin": CandidateOrigin.OPERATOR_CALIBRATION.value,
    }


def _live(
    *,
    status: LearningStatus = LearningStatus.COLLECTING,
    fit_status: FitStatus = FitStatus.IDLE,
    build_status: CheckStatus = CheckStatus.NOT_RUN,
) -> dict[str, object]:
    return {
        "status": status,
        "fit_status": fit_status,
        "origin": CandidateOrigin.OPERATOR_CALIBRATION,
        "role_generation": 4,
        "candidate_generation": 9,
        "candidate_digest": _CANDIDATE,
        "checkpoint_digest": _INCUMBENT,
        "checks": {"native-build": build_status},
    }


def _payload(*, status: LearningStatus = LearningStatus.COLLECTING) -> dict[str, object]:
    return build_learning_report(
        (),
        activation_state=_activation(),
        live_status=_live(status=status),
        calibration_command_high_water=7,
    ).as_dict()


def _section(payload: dict[str, object], name: str) -> dict[str, object]:
    value = payload[name]
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize("status", tuple(LearningStatus))
def test_report_emits_only_the_locked_status_vocabulary(status: LearningStatus) -> None:
    payload = _payload(status=status)

    assert payload["status"] == status.value
    assert payload["status"] in {
        "collecting",
        "insufficient-excitation",
        "fitting",
        "evaluating",
        "ready-for-review",
        "activating",
        "active",
        "fallback",
        "error",
        "schema-invalidated",
    }


def test_report_serializes_locked_fit_and_check_statuses_without_linear_model_fields() -> None:
    payload = build_learning_report(
        (),
        activation_state=_activation(),
        live_status=_live(fit_status=FitStatus.RUNNING, build_status=CheckStatus.PASSED),
        calibration_command_high_water=7,
    ).as_dict()

    fit = _section(payload, "fit")
    checks = _section(payload, "checks")
    candidate = _section(payload, "candidate")
    assert fit["status"] == "running"
    assert checks["native-build"] == "passed"
    assert candidate["origin"] == "operator-calibration"
    assert candidate["role_generation"] == 4
    assert candidate["candidate_generation"] == 9
    assert "pole_magnitude" not in candidate
    assert "covariance" not in candidate
    assert "state_alignment" not in candidate


def test_prepared_activation_is_reported_as_activating_not_active() -> None:
    payload = build_learning_report(
        (),
        activation_state=_activation(phase="prepared"),
        live_status=_live(status=LearningStatus.ACTIVATING),
        calibration_command_high_water=7,
    ).as_dict()

    assert payload["status"] == "activating"
    activation = _section(payload, "activation")
    active_model = _section(payload, "active_model")
    assert activation["phase"] == "prepared"
    assert activation["pending_frame_boundary_swap"] is True
    assert active_model["digest"] == _INCUMBENT


def test_report_projects_calibration_command_high_water_without_making_it_a_second_state_machine() -> None:
    payload = _payload()

    calibration = _section(payload, "calibration")
    assert calibration["command_high_water"] == 7
    assert "command_status" not in calibration


def test_current_report_cache_key_includes_evidence_activation_live_status_and_command_high_water(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    original = report_module.build_learning_report

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(report_module, "build_learning_report", counted)
    base_records = (_evidence(),)
    base_activation = _activation()
    base_live = _live()

    first = current_learning_report(
        base_records,
        activation_state=base_activation,
        live_status=base_live,
        calibration_command_high_water=7,
    )
    duplicate = current_learning_report(
        base_records,
        activation_state=dict(base_activation),
        live_status=dict(base_live),
        calibration_command_high_water=7,
    )
    assert first is duplicate
    assert calls == 1

    current_learning_report(
        (_evidence("gap-2"),),
        activation_state=base_activation,
        live_status=base_live,
        calibration_command_high_water=7,
    )
    current_learning_report(
        base_records,
        activation_state={**base_activation, "phase": "aborted"},
        live_status=base_live,
        calibration_command_high_water=7,
    )
    current_learning_report(
        base_records,
        activation_state=base_activation,
        live_status={**base_live, "status": LearningStatus.ERROR},
        calibration_command_high_water=7,
    )
    current_learning_report(
        base_records,
        activation_state=base_activation,
        live_status=base_live,
        calibration_command_high_water=8,
    )

    assert calls == 5


def test_invalid_live_checkpoint_generation_fails_closed_visibly() -> None:
    live = _live()
    live["role_generation"] = 5

    payload = build_learning_report(
        (),
        activation_state=_activation(),
        live_status=live,
        calibration_command_high_water=7,
    ).as_dict()

    assert payload["status"] == "error"
    assert payload["errors"] == ["live-role-generation-mismatch"]


@pytest.mark.parametrize(
    ("field", "value", "error"),
    (
        ("candidate_digest", "c" * 64, "live-candidate-digest-mismatch"),
        ("origin", CandidateOrigin.COOK_REFIT, "live-candidate-origin-mismatch"),
    ),
)
def test_inconsistent_live_candidate_authority_fails_closed_visibly(
    field: str,
    value: object,
    error: str,
) -> None:
    live = _live()
    live[field] = value

    payload = build_learning_report(
        (),
        activation_state=_activation(),
        live_status=live,
        calibration_command_high_water=7,
    ).as_dict()

    assert payload["status"] == "error"
    assert payload["errors"] == [error]
