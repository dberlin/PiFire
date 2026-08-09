"""Durable grey-candidate preparation without runtime ownership transfer."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from controller.model_learning.activation import (
    ActivationCandidate,
    ActivationManager,
    ActivationRequest,
    canonical_snapshot_digest,
)
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin


_INCUMBENT = "a" * 64


def _snapshot(*, theta: float = 40.0) -> dict[str, object]:
    return {
        "schema": "pifire-grey-box-model/v4",
        "n_delay": 8,
        "parameters": {
            "C_c": 320.0,
            "K_Q": 350.0,
            "theta": theta,
            "h_amb": 0.5,
            "T_amb": 20.0,
            "sigma": 1.4e-9,
        },
    }


def _candidate(
    *,
    origin: CandidateOrigin = CandidateOrigin.OPERATOR_CALIBRATION,
    snapshot: dict[str, object] | None = None,
) -> ActivationCandidate:
    value = _snapshot() if snapshot is None else snapshot
    return ActivationCandidate(
        incumbent_digest=_INCUMBENT,
        candidate_digest=canonical_snapshot_digest(value),
        candidate_generation=9,
        role_generation=4,
        origin=origin,
        decision_id="decision-9",
        snapshot=value,
    )


class _BuiltPair:
    def __init__(self) -> None:
        self.installed = False

    def install(self) -> None:
        self.installed = True


def _manager(
    *,
    validation: bool = True,
    build_failure: Exception | None = None,
    dry_solve: bool = True,
    persistence: bool = True,
):
    calls: list[str] = []
    records: list[object] = []
    pair = _BuiltPair()

    def validate(candidate: ActivationCandidate) -> bool:
        calls.append("validate")
        assert candidate.snapshot["schema"] == "pifire-grey-box-model/v4"
        return validation

    def build(candidate: ActivationCandidate) -> _BuiltPair:
        calls.append("build")
        if build_failure is not None:
            raise build_failure
        assert candidate.snapshot["n_delay"] == 8
        return pair

    def solve(value: _BuiltPair) -> bool:
        calls.append("dry-solve")
        assert value is pair
        return dry_solve

    def persist(record: object) -> bool:
        calls.append("persist-prepared")
        records.append(record)
        return persistence

    return (
        ActivationManager(
            validate_candidate=validate,
            build_candidate=build,
            native_dry_solve=solve,
            persist_prepared=persist,
            clock_ms=lambda: 1_000,
        ),
        calls,
        records,
        pair,
    )


def test_request_is_only_the_reviewed_digest_and_decision_identity() -> None:
    request = ActivationRequest("b" * 64, "decision-9")

    assert request.candidate_digest == "b" * 64
    assert request.decision_id == "decision-9"
    assert set(request.__dataclass_fields__) == {"candidate_digest", "decision_id"}
    with pytest.raises(FrozenInstanceError):
        request.decision_id = "changed"  # type: ignore[misc]


def test_prepare_validates_builds_dry_solves_and_persists_in_that_order() -> None:
    manager, calls, records, pair = _manager()
    candidate = _candidate()
    request = ActivationRequest(candidate.candidate_digest, candidate.decision_id)

    prepared = manager.prepare(request, candidate, policy=ActivationPolicy.OPERATOR_REVIEWED)

    assert prepared.accepted
    assert prepared.phase == "prepared"
    assert prepared.candidate_digest == candidate.candidate_digest
    assert prepared.role_generation == 4
    assert prepared.candidate_generation == 9
    assert calls == ["validate", "build", "dry-solve", "persist-prepared"]
    assert records == [prepared.record]
    assert not pair.installed
    assert manager.prepared == prepared


@pytest.mark.parametrize(
    ("origin", "policy"),
    [
        (CandidateOrigin.PASSIVE_ONLINE, ActivationPolicy.PASSIVE_AUTO),
        (CandidateOrigin.OPERATOR_CALIBRATION, ActivationPolicy.OPERATOR_REVIEWED),
        (CandidateOrigin.COOK_REFIT, ActivationPolicy.COOK_REFIT),
    ],
)
def test_each_origin_accepts_only_its_locked_activation_policy(origin, policy) -> None:
    manager, *_ = _manager()
    candidate = _candidate(origin=origin)

    decision = manager.prepare(
        ActivationRequest(candidate.candidate_digest, candidate.decision_id),
        candidate,
        policy=policy,
    )

    assert decision.accepted
    assert decision.origin is origin
    assert decision.policy is policy


def test_manual_activation_is_legal_only_for_operator_reviewed_calibration() -> None:
    for origin in (CandidateOrigin.PASSIVE_ONLINE, CandidateOrigin.COOK_REFIT):
        manager, *_ = _manager()
        candidate = _candidate(origin=origin)

        decision = manager.prepare(
            ActivationRequest(candidate.candidate_digest, candidate.decision_id),
            candidate,
            policy=ActivationPolicy.OPERATOR_REVIEWED,
        )

        assert not decision.accepted
        assert decision.reason == "origin-policy-mismatch"
        assert manager.prepared is None


@pytest.mark.parametrize(
    ("manager_changes", "reason", "expected_calls"),
    [
        ({"validation": False}, "candidate-validation-failed", ["validate"]),
        ({"build_failure": ValueError("bad candidate")}, "candidate-build-failed", ["validate", "build"]),
        ({"dry_solve": False}, "native-dry-solve-failed", ["validate", "build", "dry-solve"]),
        (
            {"persistence": False},
            "activation-persistence-failed",
            ["validate", "build", "dry-solve", "persist-prepared"],
        ),
    ],
)
def test_each_preparation_failure_is_fail_closed_and_never_installs(manager_changes, reason, expected_calls) -> None:
    manager, calls, _records, pair = _manager(**manager_changes)
    candidate = _candidate()

    decision = manager.prepare(
        ActivationRequest(candidate.candidate_digest, candidate.decision_id),
        candidate,
        policy=ActivationPolicy.OPERATOR_REVIEWED,
    )

    assert not decision.accepted
    assert decision.reason == reason
    assert calls == expected_calls
    assert manager.prepared is None
    assert not pair.installed


def test_changed_digest_or_decision_is_rejected_before_candidate_callbacks() -> None:
    candidate = _candidate()
    for request in (
        ActivationRequest("f" * 64, candidate.decision_id),
        ActivationRequest(candidate.candidate_digest, "stale-decision"),
    ):
        manager, calls, _, pair = _manager()

        decision = manager.prepare(request, candidate, policy=ActivationPolicy.OPERATOR_REVIEWED)

        assert not decision.accepted
        assert decision.reason in {"candidate-digest-changed", "stale-decision"}
        assert calls == []
        assert not pair.installed


def test_canonical_digest_is_mapping_order_independent_and_parameter_sensitive() -> None:
    first = _snapshot()
    parameters = first["parameters"]
    assert isinstance(parameters, dict)
    reordered = {"parameters": dict(reversed(tuple(parameters.items()))), "n_delay": 8, "schema": first["schema"]}

    assert canonical_snapshot_digest(first) == canonical_snapshot_digest(reordered)
    assert canonical_snapshot_digest(first) != canonical_snapshot_digest(_snapshot(theta=41.0))
