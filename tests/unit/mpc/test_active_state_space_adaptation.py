from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from common.model_evidence import (
    ConfidenceDecisionEvidence,
    EvidenceKind,
    ModelEvidenceRecord,
    RefreshDiagnosticsEvidence,
)
from controller.linear_mpc.activation import (
    ActivationManager,
    ActivationRequest,
    STATE_SPACE_KIND,
    canonical_snapshot_digest,
)
from controller.linear_mpc.adaptation import AdaptationPolicy, OnlineAdaptation
from controller.linear_mpc.confidence import (
    ConfidenceReport,
    ConfidenceStatus,
    GateResult,
    parameter_promotion_blockers,
)
from controller.linear_mpc.state_space import InnovationStateSpace
from tests.unit.mpc.test_innovation_state_space import _config, _frames
from tests.unit.mpc.test_online_adaptation import StateSpaceAffineModel, _populate_one_window, frame


class ParameterStateSpace(StateSpaceAffineModel):
    def __init__(self, *, gain: float, order: int = 1, bias: float = 0.0) -> None:
        super().__init__(bias=bias, cross_arm_offset_c=bias)
        self._gain = gain
        self.order = order

    def snapshot(self) -> dict[str, object]:
        snapshot = super().snapshot()
        snapshot["config"] = {"orders": [self.order], "delays": [self._delay]}
        model = snapshot["model"]
        assert isinstance(model, dict)
        model.update(
            A=[[0.8 if row == column else 0.0 for column in range(self.order)] for row in range(self.order)],
            B=[[self._gain] for _ in range(self.order)],
            C=[[1.0 for _ in range(self.order)]],
        )
        diagnostics = snapshot["diagnostics"]
        assert isinstance(diagnostics, dict)
        diagnostics.update(selected_order=self.order, selected_delay=self._delay)
        return snapshot


def _manager(*, required_wins: int = 1) -> OnlineAdaptation:
    incumbent = ParameterStateSpace(gain=1.0, bias=1.0)
    challenger = ParameterStateSpace(gain=1.1, bias=0.0)
    return OnlineAdaptation(
        incumbent,
        challenger,
        AdaptationPolicy(
            excitation_window=2,
            min_effective_updates=1,
            evaluation_interval_s=300.0,
            required_consecutive_wins=required_wins,
        ),
    )


def _complete_report(manager: OnlineAdaptation) -> ConfidenceReport:
    gates = tuple(
        GateResult(name, True)
        for name in (
            "candidate-lineage",
            "identifiability",
            "physical-validity",
            "absolute-confidence",
            "relative-confidence",
            "state-alignment",
            "target-timing",
            "atomic-persistence",
            "production-prospective-construction",
        )
    )
    return ConfidenceReport(
        status=ConfidenceStatus.ACTIVE,
        active_kind=STATE_SPACE_KIND,
        candidate_digest=manager.challenger_digest,
        generation=manager.challenger_generation,
        gates=gates,
        bootstrap_intervals=(),
        blockers=(),
        bootstrap_seed=0,
        bootstrap_replicates=10_000,
    )


def _winning_decision(manager: OnlineAdaptation):
    _populate_one_window(manager)
    decision = manager.evaluate_due(300.0)
    assert decision.promoted
    return decision


def _loader(payload):
    model = payload["model"]
    diagnostics = payload["diagnostics"]
    restored = ParameterStateSpace(
        gain=float(model["steady_gain"]),
        order=int(diagnostics["selected_order"]),
    )
    status = payload["status"]
    restored.refreshes = int(status["refreshes"])
    return restored


def test_refresh_starts_zero_win_generation_and_expires_pre_refresh_origins() -> None:
    manager = _manager()
    manager.observe(frame(0))
    assert manager.pending_origins
    manager._consecutive_wins = 1
    old_generation = manager.challenger_generation

    assert manager.refresh_challenger(ParameterStateSpace(gain=1.2))

    assert manager.challenger_generation == old_generation + 1
    assert manager.consecutive_wins == 0
    assert manager.pending_origins == ()
    manager.observe(frame(1))
    assert all(origin.generation == manager.challenger_generation for origin in manager.pending_origins)


def test_calibration_fit_frame_cannot_validate_current_parameter_generation() -> None:
    manager = _manager()
    manager.observe(replace(frame(0), calibration_fit=True, calibration_stage="low"))
    manager.observe(replace(frame(1), calibration_fit=True, calibration_stage="low"))

    assert manager.pending_origins == ()
    assert manager.completed_origins == ()


@pytest.mark.parametrize(
    "blocker",
    (
        "physical-validity",
        "absolute-confidence",
        "relative-confidence",
        "state-alignment",
        "target-timing",
        "atomic-persistence",
        "production-prospective-construction",
    ),
)
def test_every_incomplete_current_generation_gate_blocks_with_exact_reason(blocker: str) -> None:
    manager = _manager()
    report = _complete_report(manager)
    failed = replace(
        report,
        gates=report.gates + (GateResult(blocker, False, blocker),),
        blockers=(blocker,),
    )

    assert parameter_promotion_blockers(
        failed,
        candidate_digest=manager.challenger_digest,
        candidate_generation=manager.challenger_generation,
    ) == (blocker,)


def test_parameter_promotion_requires_complete_confidence_and_durable_persistence() -> None:
    manager = _manager()
    decision = _winning_decision(manager)
    solve = SimpleNamespace(objective=0.0, kkt_residual=0.0)

    assert not manager.commit_promotion(
        decision.decision_id,
        solve,
        confidence=_complete_report(manager),
        persistence_committed=False,
    )
    assert manager.promotion_rejections[-1].reason == "atomic-persistence"

    manager = _manager()
    decision = _winning_decision(manager)
    prior_digest = OnlineAdaptation.model_digest(manager.incumbent)
    prior_role = manager.role_generation
    assert manager.commit_promotion(
        decision.decision_id,
        solve,
        confidence=_complete_report(manager),
        persistence_committed=True,
    )
    assert manager.role_generation == prior_role + 1
    assert manager.previous_incumbent_digest == prior_digest
    assert manager.rollback_generation == 0
    assert manager.consecutive_wins == 0
    assert manager.completed_origins == ()
    assert manager.pending_origins == ()
    assert OnlineAdaptation.model_digest(manager.incumbent) != OnlineAdaptation.model_digest(manager.challenger)


def test_structure_change_remains_manual_even_when_parameter_scores_win() -> None:
    manager = _manager()
    before = manager.challenger_digest

    assert not manager.refresh_challenger(ParameterStateSpace(gain=1.2, order=2))
    assert manager.challenger_digest == before
    assert manager.promotion_rejections[-1].reason == "structure-change-requires-manual-activation"


def test_failed_generation_is_fenced_and_snapshot_round_trip_preserves_identities() -> None:
    manager = _manager()
    decision = _winning_decision(manager)
    assert manager.commit_promotion(
        decision.decision_id,
        SimpleNamespace(objective=0.0, kkt_residual=0.0),
        confidence=_complete_report(manager),
        persistence_committed=True,
    )
    manager.fence_active_generation("residual-degradation")
    snapshot = manager.snapshot()

    restored = OnlineAdaptation.from_snapshot(snapshot, model_loader=_loader)

    assert restored.active_generation == manager.active_generation
    assert restored.challenger_generation == manager.challenger_generation
    assert restored.rollback_generation == manager.rollback_generation
    assert restored.last_decision_id == manager.last_decision_id
    assert restored.failed_generations == manager.failed_generations
    assert restored.promotion_rejections == manager.promotion_rejections
    assert restored.snapshot()["incumbent"] == snapshot["incumbent"]
    assert restored.snapshot()["challenger"] == snapshot["challenger"]


@pytest.fixture(name="fitted_snapshot", scope="module")
def _fitted_snapshot() -> dict[str, object]:
    model = InnovationStateSpace(_config(orders=(1,), delays=(1,)))
    assert model.fit(_frames(order=1)).accepted
    return model.snapshot()


def _record(evidence_id, payload, *, digest, provenance, generation, timestamp):
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind(payload.payload_type),
        session_id="parameter-promotion",
        cook_id="cook-1",
        timestamp_ms=timestamp,
        role_generation=generation,
        model_digest=digest,
        provenance_digest=provenance,
        payload=payload,
    )


def test_activation_manager_reuses_exact_two_phase_transaction_for_parameters(fitted_snapshot) -> None:
    grey = {"schema": "grey-box-adapter/v1", "gain": 1.0}
    candidates = {"snapshot": deepcopy(fitted_snapshot)}
    records = []
    appended = []
    reports = {"current": None}

    def persist(record):
        records.append(record)
        return True

    def candidate(_digest, _generation):
        return candidates["snapshot"]

    manager = ActivationManager(
        lambda: tuple(records),
        candidate_snapshot=candidate,
        rollback_snapshot=grey,
        controller_configuration={"controller": "mpc"},
        prospective_solve=lambda _candidate, _configuration: 0.3,
        confidence_report=lambda: reports["current"],
        persist_activation=persist,
        append_evidence=lambda record: (records.append(record), appended.append(record)),
        clock_ms=lambda: 1_000,
    )

    first_digest = canonical_snapshot_digest(candidates["snapshot"])
    grey_digest = canonical_snapshot_digest(grey)
    records.extend(
        (
            _record(
                "refresh-1",
                RefreshDiagnosticsEvidence(accepted=True),
                digest=first_digest,
                provenance=grey_digest,
                generation=1,
                timestamp=1,
            ),
            _record(
                "decision-1-record",
                ConfidenceDecisionEvidence(decision_id="decision-1", blocked=False),
                digest=first_digest,
                provenance=grey_digest,
                generation=1,
                timestamp=2,
            ),
        )
    )
    assert manager.commit(manager.prepare(ActivationRequest(first_digest, "decision-1"))).accepted
    first_active = manager.active_snapshot

    second_model = InnovationStateSpace.from_snapshot(fitted_snapshot)
    second_model.track(_frames(order=1, count=97)[96])
    second = second_model.snapshot()
    candidates["snapshot"] = second
    second_digest = canonical_snapshot_digest(second)
    generation = manager.state.role_generation + 1
    records.extend(
        (
            _record(
                "refresh-2",
                RefreshDiagnosticsEvidence(accepted=True),
                digest=second_digest,
                provenance=first_digest,
                generation=generation,
                timestamp=3,
            ),
            _record(
                "decision-2-record",
                ConfidenceDecisionEvidence(decision_id="decision-2", blocked=False),
                digest=second_digest,
                provenance=first_digest,
                generation=generation,
                timestamp=4,
            ),
        )
    )
    reports["current"] = ConfidenceReport(
        ConfidenceStatus.ACTIVE,
        STATE_SPACE_KIND,
        second_digest,
        generation,
        (GateResult("complete", True),),
        (),
        (),
        0,
        10_000,
    )

    prepared = manager.prepare_parameter_promotion(ActivationRequest(second_digest, "decision-2"))
    committed = manager.commit(prepared)

    assert committed.accepted and committed.parameter_promotion
    assert manager.active_snapshot == second
    assert manager.rollback_snapshot == first_active
    assert manager.state.active_digest == second_digest
    assert manager.state.decision_id == "decision-2"
    assert manager.state.role_generation > generation
    assert appended == []
