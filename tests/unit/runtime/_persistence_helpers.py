"""Shared model-activation-state fixture for the hold persistence test suites.

Extracted from test_hold_model_persistence.py so other test modules (e.g.
test_hold_refit_trigger.py) can reuse this fixture-construction machinery
without importing a test module directly (importing a test module runs its
module-level code and collection side effects, and couples the two files).
"""

import json

from common.persistence.model_evidence import ModelActivationState
from controller.model_learning.activation import (
    ActivationPhase,
    GreyControlPairDescriptor,
    PreparedActivationRecord,
)
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin
from controller.model_learning.calibration import CalibrationDecision, CalibrationProgress
from controller.mpc_config import DEFAULT_MPC_CONFIG
from controller.mpc_factory import MpcPairFactory


def _inactive_calibration(_load, _temperature, _forecast) -> CalibrationDecision:
    return CalibrationDecision(False, 0.0, None, CalibrationProgress())


_PAIR_FACTORY = MpcPairFactory(
    DEFAULT_MPC_CONFIG,
    "C",
    {"u_min": 0.1, "u_max": 0.9},
    advance_calibration=_inactive_calibration,
    model_authority=lambda: (0, None),
    on_policy_failure=lambda _error: None,
)


def _current_pair_descriptor(
    theta: float,
    *,
    candidate_generation: int,
    role_generation: int,
) -> GreyControlPairDescriptor:
    settings = dict(DEFAULT_MPC_CONFIG)
    settings["theta"] = theta
    return _PAIR_FACTORY.descriptor(
        _PAIR_FACTORY.configured(
            settings,
            candidate_generation=candidate_generation,
            role_generation=role_generation,
        )
    )


def _pair_phase_state(phase: ActivationPhase = ActivationPhase.PREPARED):
    incumbent = _current_pair_descriptor(
        50.0,
        candidate_generation=3,
        role_generation=4,
    )
    candidate = _current_pair_descriptor(
        40.0,
        candidate_generation=4,
        role_generation=5,
    )
    prepared = PreparedActivationRecord.prepared(
        timestamp_ms=1_000,
        incumbent=incumbent,
        candidate=candidate,
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        policy=ActivationPolicy.OPERATOR_REVIEWED,
        decision_id="decision-grey-hold",
    )
    record = (
        prepared
        if phase is ActivationPhase.PREPARED
        else prepared.transition(
            phase,
            reason="interrupted-activation" if phase is ActivationPhase.ABORTED else None,
        )
    )
    active = candidate if phase is ActivationPhase.ACTIVE else incumbent
    state = ModelActivationState(
        active_snapshot_json=json.dumps(active.to_dict()["configuration"]),
        rollback_snapshot_json=json.dumps(incumbent.to_dict()["configuration"]),
        evidence_decision_id=record.decision_id,
        controller_configuration_digest=candidate.ownership_digest,
        role_generation=active.role_generation,
        phase=record.phase.value,
        transaction_id=record.transaction_id,
        incumbent_pair_json=json.dumps(incumbent.to_dict()),
        candidate_pair_json=json.dumps(candidate.to_dict()),
        rollback_pair_json=json.dumps(incumbent.to_dict()),
        origin=record.origin.value,
        policy=record.policy.value,
        candidate_generation=candidate.candidate_generation,
        candidate_digest=candidate.model_digest,
        reason=record.reason,
    )
    return state, record
