from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from common.model_evidence import (
    EvidenceKind,
    ModelEvidenceRecord,
    RecorderGapEvidence,
)
from controller.model_learning.confidence import ConfidenceConfig, evaluate_confidence
from controller.model_learning.contracts import CandidateOrigin, LearningStatus
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


def test_distinct_role_and_candidate_generations_select_evidence_using_canonical_origin() -> None:
    report = _report(_qualifying())

    assert report.status is LearningStatus.READY_FOR_REVIEW
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
    assert complete.status is LearningStatus.READY_FOR_REVIEW


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
    assert report.status is not LearningStatus.READY_FOR_REVIEW
    assert report.blockers == ("recorder-gap",)


def test_config_is_frozen_and_bootstrap_replicates_are_fixed() -> None:
    config = ConfidenceConfig()
    with pytest.raises(FrozenInstanceError):
        config.bootstrap_seed = 8  # type: ignore[misc]
    with pytest.raises(ValueError, match="10,000"):
        ConfidenceConfig(bootstrap_replicates=9)
