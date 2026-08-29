"""Shared qualified-challenger fixture for activation tests."""

from __future__ import annotations

from common.learning_trajectory import ModelFitLineage
from common.model_evidence import ChallengerRoundEvidence, EvidenceKind, ModelEvidenceRecord
from common.persistence.model_challenger import ModelChallengerState, create_model_challenger
from common.persistence.model_evidence import append_model_evidence
from controller.model_learning.activation import GreyControlPairDescriptor
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin
from tests.unit.common._model_challenger_helpers import _corpus


def _seed_qualified_challenger(
    incumbent: GreyControlPairDescriptor,
    candidate: GreyControlPairDescriptor,
    *,
    decision_id: str,
) -> ModelChallengerState:
    corpus = _corpus(decision_id)
    request_id = f"fit-{decision_id}"
    challenger_id = f"challenger-{decision_id}"
    evidence_id = f"challenger-round:{challenger_id}:0:2:{decision_id}"
    state = ModelChallengerState(
        schema_version=1,
        challenger_id=challenger_id,
        revision=0,
        phase="qualified",
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.CAUSAL_AUTO,
        fit_corpus=corpus,
        fit_lineage=ModelFitLineage(
            request_id=request_id,
            parent_incumbent_digest=incumbent.model_digest,
            parent_incumbent_generation=incumbent.role_generation,
            candidate_generation=candidate.candidate_generation,
            fit_corpus=corpus,
            fit_corpus_digest=corpus.corpus_digest,
            trigger_origin=CandidateOrigin.PASSIVE_ONLINE.value,
            result_status="succeeded",
            candidate_digest=candidate.model_digest,
        ),
        fit_preparation={
            "request_id": request_id,
            "accepted": True,
            "candidate_digest": candidate.model_digest,
            "required_horizons": [3, 15, 45, 90, 180],
            "native_build": "passed",
            "dry_solve": "passed",
            "target_timing": {
                "target": "test",
                "samples": 1,
                "p99_ms": 1.0,
                "limit_ms": 2.0,
            },
            "fit_corpus_digest": corpus.corpus_digest,
            "fit_result": {
                "rmse_c": 0.5,
                "max_error_c": 1.0,
                "identifiability": 0.9,
                "sample_count": 120,
                "temperature_band_c": [75.0, 160.0],
                "nfev": 4,
                "result_digest": "d" * 64,
            },
        },
        controller_configuration_digest="c" * 64,
        incumbent=incumbent,
        candidate=candidate,
        calibration_manifest=None,
        evaluation_epoch=0,
        evaluation_round=2,
        consecutive_wins=2,
        required_wins=2,
        last_decision_id=decision_id,
        last_evidence_id=evidence_id,
        activation_transaction_id=None,
        retirement_reason=None,
        created_ms=900,
        updated_ms=900,
        retired_ms=None,
    )
    append_model_evidence(
        (
            ModelEvidenceRecord(
                evidence_id=evidence_id,
                kind=EvidenceKind.CHALLENGER_ROUND,
                session_id="session-activation-fixture",
                cook_id=None,
                timestamp_ms=900,
                role_generation=incumbent.role_generation,
                model_digest=candidate.model_digest,
                provenance_digest=incumbent.model_digest,
                payload=ChallengerRoundEvidence(
                    challenger_id=challenger_id,
                    evaluation_epoch=0,
                    evaluation_round=2,
                    decision_id=decision_id,
                    accepted=True,
                    required_horizons=(3, 15, 45, 90, 180),
                    completed_horizons=(3, 15, 45, 90, 180),
                    incumbent_digest=incumbent.model_digest,
                    candidate_digest=candidate.model_digest,
                ),
            ),
        )
    )
    return create_model_challenger(state)
