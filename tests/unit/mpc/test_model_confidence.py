from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from common.model_evidence import (
    EvidenceKind,
    ModelEvidenceRecord,
    RecorderGapEvidence,
)
from controller.model_learning.confidence import (
    ConfidenceConfig,
    evaluate_confidence,
    qualification_gates,
)
from controller.model_learning.contracts import (
    ActivationPolicy,
    CandidateOrigin,
    LearningStatus,
)
from tests.unit.common.test_model_challenger_store import (
    _manifest,
)
from tests.unit.common.test_model_challenger_store import (
    _state as _challenger_state,
)
from tests.unit.mpc._confidence_helpers import _qualifying, _rebuild, _record, _state


def _report(
    records: tuple[ModelEvidenceRecord, ...],
    *,
    origin: CandidateOrigin = CandidateOrigin.PASSIVE_ONLINE,
    status: LearningStatus = LearningStatus.COLLECTING,
):
    return evaluate_confidence(
        records,
        activation_state=_state(origin=origin, status=status),
        target_timing=None,
        config=ConfidenceConfig(bootstrap_seed=7),
    )


def _durably_winning_challenger(
    origin: CandidateOrigin,
    *,
    calibration_manifest: dict[str, object] | None,
    wins: int = 2,
):
    return _challenger_state(
        phase="evaluating",
        origin=origin,
        policy=ActivationPolicy.CAUSAL_AUTO,
        calibration_manifest=calibration_manifest,
        evaluation_epoch=3,
        evaluation_round=wins,
        consecutive_wins=wins,
        last_decision_id=f"decision-3-{wins}" if wins else None,
        last_evidence_id=f"challenger-round-3-{wins}" if wins else None,
    )


@pytest.mark.parametrize(
    "origin",
    (
        CandidateOrigin.PASSIVE_ONLINE,
        CandidateOrigin.OPERATOR_CALIBRATION,
    ),
)
def test_causal_auto_origins_share_durable_qualification_gates(
    origin: CandidateOrigin,
) -> None:
    state = _durably_winning_challenger(
        origin,
        calibration_manifest=(_manifest() if origin is CandidateOrigin.OPERATOR_CALIBRATION else None),
    )

    decision = qualification_gates(state)

    assert state.policy is ActivationPolicy.CAUSAL_AUTO
    assert decision.accepted
    assert decision.blockers == ()


def test_operator_manifest_does_not_relax_the_shared_two_win_gate() -> None:
    passive = qualification_gates(
        _durably_winning_challenger(
            CandidateOrigin.PASSIVE_ONLINE,
            calibration_manifest=None,
            wins=1,
        )
    )
    operator = qualification_gates(
        _durably_winning_challenger(
            CandidateOrigin.OPERATOR_CALIBRATION,
            calibration_manifest=_manifest(),
            wins=1,
        )
    )

    assert not passive.accepted
    assert not operator.accepted
    assert operator.blockers == passive.blockers


@pytest.mark.parametrize(
    "manifest",
    (
        pytest.param(None, id="missing"),
        pytest.param(
            {
                "command_revision": 11,
                "session_id": "session-calibration",
                "completed_stages": ["low", "middle", "high"],
                "stage_evidence_ids": [
                    "calibration-low",
                    "calibration-middle",
                    "calibration-high",
                ],
            },
            id="partial",
        ),
        pytest.param(
            {
                "command_revision": 11,
                "session_id": "session-calibration",
                "completed_stages": ["low", "high", "middle", "coast"],
                "stage_evidence_ids": [
                    "calibration-low",
                    "calibration-high",
                    "calibration-middle",
                    "calibration-coast",
                ],
            },
            id="reordered",
        ),
        pytest.param(
            {
                "command_revision": 11,
                "session_id": "session-calibration",
                "completed_stages": ["low", "middle", "high", "coast"],
                "stage_evidence_ids": [
                    "calibration-low",
                    "calibration-middle",
                    "calibration-high",
                    "calibration-high",
                ],
            },
            id="duplicate-stage-evidence",
        ),
    ),
)
def test_operator_qualification_fails_closed_on_incomplete_manifest(
    manifest: dict[str, object] | None,
) -> None:
    decision = qualification_gates(
        _durably_winning_challenger(
            CandidateOrigin.OPERATOR_CALIBRATION,
            calibration_manifest=manifest,
        )
    )

    assert not decision.accepted
    assert decision.blockers == ("calibration-manifest",)


def test_distinct_role_and_candidate_generations_select_evidence_using_canonical_origin() -> None:
    report = _report(_qualifying())

    assert report.status is LearningStatus.QUALIFIED
    assert report.active_kind == "grey-box"
    assert report.generation == 9
    assert report.blockers == ()
    assert all(interval.replicate_count == 10_000 for interval in report.bootstrap_intervals)


def test_cook_refit_also_does_not_inherit_operator_calibration_completeness() -> None:
    report = _report(_qualifying(), origin=CandidateOrigin.COOK_REFIT)

    assert "calibration-completeness" not in report.blockers


def test_operator_calibration_requires_all_completed_probe_stages() -> None:
    incomplete = _report(_qualifying(), origin=CandidateOrigin.OPERATOR_CALIBRATION)
    complete = _report(
        _qualifying(include_calibration=True),
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
    )

    assert incomplete.blockers == ("calibration-completeness",)
    assert complete.blockers == ()
    assert complete.status is LearningStatus.QUALIFIED


@pytest.mark.parametrize("status", (LearningStatus.ACTIVATING, LearningStatus.ERROR))
def test_activating_and_error_are_authoritative_live_statuses(status: LearningStatus) -> None:
    report = _report(_qualifying(), status=status)

    assert report.status is status


def test_candidate_generation_and_digest_isolate_confidence_evidence() -> None:
    stale = tuple(_rebuild(record, role_generation=3) for record in _qualifying())
    report = _report(stale)

    assert report.status is LearningStatus.COLLECTING
    assert "candidate-lineage" in report.blockers


def test_destructive_evidence_gap_fails_closed_without_changing_the_grey_owner() -> None:
    gap = _record(
        EvidenceKind.RECORDER_GAP,
        RecorderGapEvidence(lost_record_count=1, reason="recorder-gap"),
        timestamp=99_999,
    )
    report = _report(_qualifying() + (gap,))

    assert report.active_kind == "grey-box"
    assert report.status is not LearningStatus.QUALIFIED
    assert report.blockers == ("recorder-gap",)


def test_config_is_frozen_and_bootstrap_replicates_are_fixed() -> None:
    config = ConfidenceConfig()
    with pytest.raises(FrozenInstanceError):
        config.bootstrap_seed = 8  # type: ignore[misc]
    with pytest.raises(ValueError, match="10,000"):
        ConfidenceConfig(bootstrap_replicates=9)
