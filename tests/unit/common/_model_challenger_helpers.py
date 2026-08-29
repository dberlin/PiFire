"""Shared model-challenger fixtures for unit tests."""

from __future__ import annotations

from hashlib import sha256

from common.learning_trajectory import (
    FitCorpusIdentity,
    FitCorpusSlice,
    ModelFitLineage,
    canonical_trajectory_digest,
)
from common.persistence.model_challenger import ModelChallengerState
from controller.model_learning.activation import (
    GreyControlPairDescriptor,
    canonical_snapshot_digest,
)
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin

_REQUIRED_HORIZONS = (3, 15, 45, 90, 180)


def _digest(label: str) -> str:
    return sha256(label.encode()).hexdigest()


def _descriptor(label: str, *, theta: float, candidate_generation: int) -> GreyControlPairDescriptor:
    configuration = {
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
        "label": label,
    }
    return GreyControlPairDescriptor(
        model_digest=canonical_snapshot_digest(configuration),
        configuration=configuration,
        estimator_kind="ekf",
        solver_kind="acados-grey",
        candidate_generation=candidate_generation,
        role_generation=4,
    )


def _corpus(label: str = "challenger") -> FitCorpusIdentity:
    corpus_slice = FitCorpusSlice(
        segment_id=f"segment-{label}",
        through_ordinal=2,
        prefix_digest=_digest(f"prefix-{label}"),
        pre_roll_count=1,
        scored_count=2,
    )
    payload = {
        "schema_version": 1,
        "corpus_revision": 7,
        "fit_partition_digest": _digest(f"partition-{label}"),
        "slices": [
            {
                "segment_id": corpus_slice.segment_id,
                "through_ordinal": corpus_slice.through_ordinal,
                "prefix_digest": corpus_slice.prefix_digest,
                "pre_roll_count": corpus_slice.pre_roll_count,
                "scored_count": corpus_slice.scored_count,
            }
        ],
    }
    return FitCorpusIdentity(
        schema_version=payload["schema_version"],
        corpus_revision=payload["corpus_revision"],
        fit_partition_digest=payload["fit_partition_digest"],
        slices=(corpus_slice,),
        corpus_digest=canonical_trajectory_digest(payload),
    )


def _manifest(label: str = "calibration") -> dict[str, object]:
    return {
        "command_revision": 11,
        "session_id": f"session-{label}",
        "completed_stages": ["low", "middle", "high", "coast"],
        "stage_evidence_ids": [
            f"{label}-low",
            f"{label}-middle",
            f"{label}-high",
            f"{label}-coast",
        ],
    }


def _state(
    *,
    revision: int = 0,
    phase: str = "built",
    origin: CandidateOrigin = CandidateOrigin.PASSIVE_ONLINE,
    policy: ActivationPolicy = ActivationPolicy.CAUSAL_AUTO,
    corpus: FitCorpusIdentity | None = None,
    incumbent: GreyControlPairDescriptor | None = None,
    candidate: GreyControlPairDescriptor | None = None,
    controller_configuration_digest: str | None = None,
    calibration_manifest: dict[str, object] | None = None,
    evaluation_epoch: int = 0,
    evaluation_round: int = 0,
    consecutive_wins: int = 0,
    last_decision_id: str | None = None,
    last_evidence_id: str | None = None,
    activation_transaction_id: str | None = None,
    retirement_reason: str | None = None,
    retired_ms: int | None = None,
    fit_preparation: dict[str, object] | None = None,
) -> ModelChallengerState:
    fit_corpus = _corpus() if corpus is None else corpus
    active = _descriptor("incumbent", theta=50.0, candidate_generation=4) if incumbent is None else incumbent
    challenger = _descriptor("candidate", theta=65.0, candidate_generation=5) if candidate is None else candidate
    lineage = ModelFitLineage(
        request_id="fit-challenger-1",
        parent_incumbent_digest=active.model_digest,
        parent_incumbent_generation=active.candidate_generation,
        candidate_generation=challenger.candidate_generation,
        fit_corpus=fit_corpus,
        fit_corpus_digest=fit_corpus.corpus_digest,
        trigger_origin=getattr(origin, "value", origin),
        result_status="succeeded",
        candidate_digest=challenger.model_digest,
    )
    preparation = fit_preparation or {
        "request_id": lineage.request_id,
        "accepted": True,
        "candidate_digest": challenger.model_digest,
        "native_build": "passed",
        "dry_solve": "passed",
        "target_timing": {"target": "pi", "p99_ms": 4.0, "limit_ms": 5.0},
    }
    return ModelChallengerState(
        schema_version=1,
        challenger_id="challenger-1",
        revision=revision,
        phase=phase,
        origin=origin,
        policy=policy,
        fit_corpus=fit_corpus,
        fit_lineage=lineage,
        fit_preparation=preparation,
        controller_configuration_digest=(
            _digest("controller-configuration")
            if controller_configuration_digest is None
            else controller_configuration_digest
        ),
        incumbent=active,
        candidate=challenger,
        calibration_manifest=calibration_manifest,
        evaluation_epoch=evaluation_epoch,
        evaluation_round=evaluation_round,
        consecutive_wins=consecutive_wins,
        required_wins=2,
        last_decision_id=last_decision_id,
        last_evidence_id=last_evidence_id,
        activation_transaction_id=activation_transaction_id,
        retirement_reason=retirement_reason,
        created_ms=1_000,
        updated_ms=1_000 + revision,
        retired_ms=retired_ms,
    )


def _qualified() -> ModelChallengerState:
    return _state(
        phase="qualified",
        evaluation_round=2,
        consecutive_wins=2,
        last_decision_id="decision-0-2",
        last_evidence_id="challenger-round-0-2",
    )
