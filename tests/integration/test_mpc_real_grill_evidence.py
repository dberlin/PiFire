"""End-to-end acceptance for the real-grill MPC evidence rollout boundary."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest
from pydantic import ValidationError

from common.control_trace import (
    AmbientSource,
    ControlTraceRecord,
    ControllerType,
    SessionPayload,
    TraceEventKind,
    TraceSetting,
)
from common.controller_model_state import CheckpointSaveOutcome
from common.datastore_accessors import (
    append_control_trace,
    append_model_evidence,
    commit_model_activation,
    prune_control_trace,
    read_model_activation,
    read_model_evidence,
)
from common.model_evidence import (
    CalibrationSummaryEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    FallbackEvidence,
    ForecastOriginEvidence,
    ModelEvidenceRecord,
    RecorderGapEvidence,
    RefreshDiagnosticsEvidence,
    SessionSummaryEvidence,
    TimingDistributionEvidence,
)
from controller.grill_sim import GrillSim, MAKGrillSim
from controller.linear_mpc.activation import (
    ActivationManager,
    ActivationRequest,
    GREY_BOX_KIND,
    STATE_SPACE_KIND,
    canonical_snapshot_digest,
)
from controller.linear_mpc.confidence import (
    ConfidenceConfig,
    ConfidenceReport,
    ConfidenceStatus,
    GateResult,
    evaluate_confidence,
)
from controller.linear_mpc.report import build_evidence_artifact, current_evidence_report
from controller.linear_mpc.state_space import InnovationStateSpace
from controller.mpc import CalibrationCommand, Controller
from controller.runtime.model_persistence import ModelPersistenceWorker
from controller.runtime.runner import SyncControllerRunner
from tests.unit.mpc.test_innovation_state_space import _config as state_space_config
from tests.unit.mpc.test_innovation_state_space import _frames as state_space_frames

_HORIZONS = (3, 15, 45, 90, 180)
_GENERATION = 4


class _Estimator:
    def update(self, _load: float, _temperature: float) -> np.ndarray:
        return np.array([20.0, 0.0])


class _Policy:
    def make_step(self, _state: object) -> np.ndarray:
        return np.array([[0.4]])


class _SafeForecast:
    def forecast(self, q_future: object, _ambient_future: object) -> np.ndarray:
        return np.full(len(q_future), 101.0)  # type: ignore[arg-type]


class _PersistenceStore:
    def save_outcome(self, _name: str, _snapshot: dict[str, object]) -> CheckpointSaveOutcome:
        return CheckpointSaveOutcome.SAVED


class _Logger:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)


def _record(
    evidence_id: str,
    payload: object,
    *,
    candidate_digest: str,
    incumbent_digest: str,
    timestamp_ms: int,
    generation: int = _GENERATION,
    cook_id: str | None = "cook-a",
    session_id: str | None = None,
) -> ModelEvidenceRecord:
    kind = EvidenceKind(getattr(payload, "payload_type"))
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=kind,
        session_id=session_id or f"session-{cook_id or 'global'}",
        cook_id=cook_id,
        timestamp_ms=timestamp_ms,
        role_generation=generation,
        model_digest=candidate_digest,
        provenance_digest=incumbent_digest,
        payload=payload,
    )


def _qualifying_records(
    plant_name: str,
    *,
    candidate_digest: str,
    incumbent_digest: str,
    timing_provenance: str,
) -> tuple[ModelEvidenceRecord, ...]:
    records: list[ModelEvidenceRecord] = []
    for timestamp, stage in enumerate(("low", "middle", "high", "coast"), start=1):
        records.append(
            _record(
                f"{plant_name}:calibration:{stage}",
                CalibrationSummaryEvidence(
                    accepted=True,
                    probe_count=0,
                    stage=stage,
                    completed_stages=("low", "middle", "high") if stage == "coast" else (),
                    continuous=True,
                ),
                candidate_digest=candidate_digest,
                incumbent_digest=incumbent_digest,
                timestamp_ms=timestamp,
                cook_id="calibration-cook",
                session_id=f"{plant_name}:calibration",
            )
        )
    records.extend(
        (
            _record(
                f"{plant_name}:refresh",
                RefreshDiagnosticsEvidence(
                    accepted=True,
                    full_rank=True,
                    finite_diagnostics=True,
                    pole_magnitude=0.9,
                    gain=1.0,
                    delay_steps=3,
                    covariance_finite=True,
                    alignment_error_c=1.0,
                    snapshot_round_trip=True,
                    sequential_wins=2,
                    generation_continuity=True,
                    atomic_persistence=True,
                    production_prospective=True,
                    braking_error_c=1.0,
                    incumbent_braking_error_c=2.0,
                ),
                candidate_digest=candidate_digest,
                incumbent_digest=incumbent_digest,
                timestamp_ms=5,
            ),
            _record(
                f"{plant_name}:timing:{timing_provenance}",
                TimingDistributionEvidence(
                    sample_count=50,
                    p50_ms=10.0,
                    p95_ms=20.0,
                    p99_ms=200.0,
                    hardware_provenance=timing_provenance,
                ),
                candidate_digest=candidate_digest,
                incumbent_digest=incumbent_digest,
                timestamp_ms=6,
            ),
        )
    )
    timestamp = 7
    for cook in ("ordinary-a", "ordinary-b"):
        for horizon in _HORIZONS:
            for sequence in range(horizon):
                error = (-0.5, 0.5, 0.0)[sequence % 3]
                records.append(
                    _record(
                        f"{plant_name}:{cook}:{horizon}:{sequence}",
                        ForecastOriginEvidence(
                            origin_sequence=sequence,
                            origin_time_ms=sequence * 20_000,
                            completion_time_ms=(sequence + horizon) * 20_000,
                            horizon_steps=horizon,
                            incumbent_digest=incumbent_digest,
                            challenger_digest=candidate_digest,
                            incumbent_prediction_c=100.0,
                            challenger_prediction_c=100.0,
                            observed_temperature_c=100.0 + error,
                            incumbent_error_c=2.0 * error,
                            challenger_error_c=error,
                            temperature_band="middle",
                            phase="heating",
                            ambient_source=AmbientSource.CONFIGURED,
                            calibration_fit=False,
                        ),
                        candidate_digest=candidate_digest,
                        incumbent_digest=incumbent_digest,
                        timestamp_ms=timestamp,
                        cook_id=cook,
                        session_id=f"{plant_name}:{cook}",
                    )
                )
                timestamp += 1
    return tuple(records)


def _raw_trace(plant_name: str) -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=1,
        session_id=f"{plant_name}:raw-session",
        cook_id=f"{plant_name}:ordinary-a",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.SESSION,
        payload=SessionPayload(
            controller=ControllerType.MPC,
            controller_config=(TraceSetting(key="horizon", value=24),),
            temperature_unit="C",
            control_period_seconds=20.0,
            model_revision=1,
            model_provenance="simulator-only",
            pulse_slot_seconds=2.0,
            pulse_frame_seconds=20.0,
            fan_authority=False,
            fan_pwm_capable=True,
            fan_min_duty=0.0,
            fan_max_duty=1.0,
            setpoint=110.0,
            ambient_temperature=20.0,
            software_version="acceptance",
            build_version="acceptance",
        ),
    )


def _fitted_snapshot() -> dict[str, object]:
    model = InnovationStateSpace(state_space_config(orders=(1,), delays=(1,)))
    assert model.fit(state_space_frames(order=1)).accepted
    return model.snapshot()


def _configure_mpc(monkeypatch: pytest.MonkeyPatch) -> Controller:
    monkeypatch.setattr(Controller, "_build_for", lambda self, cfg: (_Estimator(), None, object(), _Policy()))
    monkeypatch.setattr(
        "controller.mpc.GreyBoxPredictionAdapter.from_controller",
        lambda _controller: _SafeForecast(),
    )
    controller = Controller({"n_delay": 0, "enable_fan_input": False}, "C", {"u_max": 0.9})
    controller.set_target(110.0)
    return controller


@pytest.mark.parametrize(
    ("plant_type", "seed"),
    ((GrillSim, 111), (MAKGrillSim, 222)),
    ids=("GrillSim", "MAKGrillSim"),
)
def test_complete_real_grill_evidence_lifecycle_preserves_rollout_boundary(
    ds,
    monkeypatch: pytest.MonkeyPatch,
    plant_type: type[GrillSim],
    seed: int,
) -> None:
    plant = plant_type(seed=seed)
    plant_name = plant_type.__name__
    controller = _configure_mpc(monkeypatch)
    runner = SyncControllerRunner(controller)

    # 1. Ordinary Hold remains an unmodified grey-box request on both plants.
    ordinary = runner.latest_from(plant.measured())
    assert ordinary.calibration is not None
    assert ordinary.allocation is not None
    assert ordinary.baseline_allocation is not None
    assert ordinary.calibration.probe_q == 0.0
    assert ordinary.allocation == ordinary.baseline_allocation
    assert controller.get_status()["activation"]["active_kind"] == GREY_BOX_KIND
    on_seconds = round(ordinary.allocation.auger_duty * 20)
    for second in range(20):
        plant.step(second < on_seconds, 0.5)

    # 2. Explicit guarded calibration is the only path that adds a bounded probe.
    start = CalibrationCommand(
        action="start",
        command_revision=1,
        maximum_temperature_c=130.0,
        ambient_c=20.0,
        ambient_source="configured",
        empty_grill_confirmed=True,
        pellets_confirmed=True,
        safety_ceiling_c=260.0,
    )
    runner.request_calibration(start)
    calibration = runner.latest_from(plant.measured())
    assert calibration.calibration is not None
    assert calibration.allocation is not None
    assert calibration.baseline_allocation is not None
    assert calibration.calibration.active
    assert 0.0 < abs(calibration.calibration.probe_q) <= 0.05
    assert calibration.allocation.normalized_combustion_load == pytest.approx(
        calibration.baseline_allocation.normalized_combustion_load + calibration.calibration.probe_q
    )
    runner.request_calibration(replace(start, action="stop", command_revision=2))
    stopped = runner.latest_from(plant.measured())
    assert stopped.calibration is not None
    assert stopped.calibration.probe_q == 0.0
    assert stopped.allocation == stopped.baseline_allocation

    candidate_snapshot = _fitted_snapshot()
    candidate_digest = canonical_snapshot_digest(candidate_snapshot)
    grey_snapshot = {"schema": "grey-box-adapter/v1", "gain": 1.0, "plant": plant_name}
    incumbent_digest = canonical_snapshot_digest(grey_snapshot)
    simulator_records = _qualifying_records(
        plant_name,
        candidate_digest=candidate_digest,
        incumbent_digest=incumbent_digest,
        timing_provenance="workstation",
    )

    # 3-4. Calibration fit is not validation; later ordinary cooks own immutable futures.
    origin = next(record.payload for record in simulator_records if isinstance(record.payload, ForecastOriginEvidence))
    assert isinstance(origin, ForecastOriginEvidence)
    with pytest.raises(ValidationError, match="calibration-fit"):
        replace(origin, calibration_fit=True)
    assert {record.cook_id for record in simulator_records if isinstance(record.payload, ForecastOriginEvidence)} == {
        "ordinary-a",
        "ordinary-b",
    }
    assert all(
        record.payload.origin_time_ms < record.payload.completion_time_ms
        for record in simulator_records
        if isinstance(record.payload, ForecastOriginEvidence)
    )

    append_model_evidence(simulator_records)
    append_control_trace([_raw_trace(plant_name)])

    # Simulator evidence is diagnostically complete but cannot satisfy target timing.
    simulator_report = current_evidence_report(read_model_evidence())
    simulator_payload = simulator_report.to_dict()
    assert simulator_payload["active_model"]["kind"] == GREY_BOX_KIND
    assert simulator_payload["default_model"]["kind"] == GREY_BOX_KIND
    assert simulator_payload["target_timing"]["hardware_provenance"] == "workstation"
    assert simulator_payload["target_timing"]["gate_passed"] is False
    assert simulator_payload["blockers"] == ["target-timing"]
    assert "target-timing" in simulator_payload["missing_gates"]

    # 5. Compact evidence remains reviewable after the raw trace retention boundary.
    evidence_ids = tuple(record.evidence_id for record in read_model_evidence())
    assert prune_control_trace(2, limit=1) == 1
    assert tuple(record.evidence_id for record in read_model_evidence()) == evidence_ids

    # Target-provenance timing is an explicit independent seam, never inferred from the simulator.
    target_timing = _record(
        f"{plant_name}:timing:target",
        TimingDistributionEvidence(
            sample_count=50,
            p50_ms=10.0,
            p95_ms=20.0,
            p99_ms=200.0,
            hardware_provenance="target-hardware",
        ),
        candidate_digest=candidate_digest,
        incumbent_digest=incumbent_digest,
        timestamp_ms=100_000,
    )
    stale_decision = _record(
        f"{plant_name}:decision:stale",
        ConfidenceDecisionEvidence(decision_id=f"{plant_name}:decision-stale", blocked=False),
        candidate_digest=candidate_digest,
        incumbent_digest=incumbent_digest,
        timestamp_ms=100_001,
        cook_id=None,
        session_id=f"{plant_name}:review",
    )
    exact_decision = _record(
        f"{plant_name}:decision:exact",
        ConfidenceDecisionEvidence(decision_id=f"{plant_name}:decision-exact", blocked=False),
        candidate_digest=candidate_digest,
        incumbent_digest=incumbent_digest,
        timestamp_ms=100_002,
        cook_id=None,
        session_id=f"{plant_name}:review",
    )
    append_model_evidence([target_timing, stale_decision, exact_decision])

    # 6. Complete evidence is review-ready, but review alone has no command authority.
    ready = evaluate_confidence(
        read_model_evidence(),
        activation_state={
            "status": "collecting",
            "active_kind": "grey_box",
            "candidate_digest": candidate_digest,
            "candidate_generation": _GENERATION,
        },
        target_timing=None,
        config=ConfidenceConfig(bootstrap_seed=7),
    )
    assert ready.status is ConfidenceStatus.READY_FOR_REVIEW
    assert ready.active_kind == "grey_box"
    ready_payload = current_evidence_report(read_model_evidence()).to_dict()
    assert ready_payload["status"] == "ready-for-review"
    assert ready_payload["active_model"]["kind"] == GREY_BOX_KIND
    assert ready_payload["decision_id"] == f"{plant_name}:decision-exact"
    artifact = json.loads(
        build_evidence_artifact(current_evidence_report(read_model_evidence()), read_model_evidence())
    )
    assert artifact["authority"] == "read-only-evidence"
    assert artifact["report"]["calibration"]["completed_stages"] == ["low", "middle", "high", "coast"]

    candidates = {candidate_digest: candidate_snapshot}
    confidence_holder: dict[str, ConfidenceReport | None] = {"report": None}

    def persist_activation(record: ModelEvidenceRecord) -> bool:
        commit_model_activation(record)
        return True

    manager = ActivationManager(
        lambda: tuple(read_model_evidence()),
        candidate_snapshot=lambda digest, _generation: candidates.get(digest),
        rollback_snapshot=grey_snapshot,
        controller_configuration={"controller": "mpc", "plant": plant_name},
        prospective_solve=lambda _candidate, _configuration: 0.37,
        confidence_report=lambda: confidence_holder["report"],
        persist_activation=persist_activation,
        append_evidence=lambda record: append_model_evidence([record]),
        invalidate_pending_origins=lambda _generation, _digest: None,
        session_id=f"{plant_name}:activation",
        clock_ms=lambda: 200_000,
    )

    # 7. A stale review identity is rejected exactly and cannot transfer ownership.
    stale = manager.prepare(ActivationRequest(candidate_digest, f"{plant_name}:decision-stale"))
    assert not stale.accepted
    assert stale.reason == "stale-confidence-decision"
    assert manager.active_kind == GREY_BOX_KIND

    # 8. Only the exact reviewed digest and decision commit atomically.
    exact_request = ActivationRequest(candidate_digest, f"{plant_name}:decision-exact")
    activated = manager.commit(manager.prepare(exact_request))
    assert activated.accepted
    assert manager.active_kind == STATE_SPACE_KIND
    assert manager.state.active_digest == candidate_digest
    assert read_model_activation() is not None
    active_payload = current_evidence_report(read_model_evidence(), activation_state=read_model_activation()).to_dict()
    assert active_payload["status"] == "active"
    assert active_payload["active_model"]["kind"] == STATE_SPACE_KIND
    assert active_payload["default_model"]["kind"] == GREY_BOX_KIND

    # 9. A parameter-only generation uses the identical prepare/persist/commit lifecycle.
    successor_model = InnovationStateSpace.from_snapshot(deepcopy(candidate_snapshot))
    successor_model.track(state_space_frames(order=1, count=97)[96])
    successor_snapshot = successor_model.snapshot()
    successor_digest = canonical_snapshot_digest(successor_snapshot)
    assert successor_digest != candidate_digest
    candidates[successor_digest] = successor_snapshot
    successor_generation = manager.state.role_generation + 1
    append_model_evidence(
        [
            _record(
                f"{plant_name}:refresh:successor",
                RefreshDiagnosticsEvidence(accepted=True),
                candidate_digest=successor_digest,
                incumbent_digest=candidate_digest,
                timestamp_ms=200_001,
                generation=successor_generation,
            ),
            _record(
                f"{plant_name}:decision:successor",
                ConfidenceDecisionEvidence(
                    decision_id=f"{plant_name}:decision-successor",
                    blocked=False,
                ),
                candidate_digest=successor_digest,
                incumbent_digest=candidate_digest,
                timestamp_ms=200_002,
                generation=successor_generation,
                cook_id=None,
                session_id=f"{plant_name}:adaptation",
            ),
        ]
    )
    confidence_holder["report"] = ConfidenceReport(
        ConfidenceStatus.ACTIVE,
        STATE_SPACE_KIND,
        successor_digest,
        successor_generation,
        (GateResult("complete", True),),
        (),
        (),
        7,
        10_000,
    )
    promotion_request = ActivationRequest(successor_digest, f"{plant_name}:decision-successor")
    promoted = manager.commit(manager.prepare_parameter_promotion(promotion_request))
    assert promoted.accepted and promoted.parameter_promotion
    assert manager.state.active_digest == successor_digest
    assert manager.rollback_snapshot == candidate_snapshot
    persisted_promotion = read_model_activation()
    assert persisted_promotion is not None
    assert persisted_promotion.active_snapshot_json == promoted.active_snapshot_json

    # 10. The active-failure branch records the failure and returns directly to grey-box.
    failure_records = [
        record
        for record in read_model_evidence()
        if record.role_generation == _GENERATION
        and (record.model_digest == candidate_digest or isinstance(record.payload, CalibrationSummaryEvidence))
    ]
    failure_events: list[ModelEvidenceRecord] = []
    failure_manager = ActivationManager(
        lambda: tuple(failure_records),
        candidate_snapshot=candidate_snapshot,
        rollback_snapshot=grey_snapshot,
        controller_configuration={"controller": "mpc", "plant": plant_name},
        prospective_solve=lambda _candidate, _configuration: 0.37,
        persist_activation=lambda record: failure_records.append(record) or True,
        append_evidence=lambda record: (failure_records.append(record), failure_events.append(record)),
        session_id=f"{plant_name}:failure",
        clock_ms=lambda: 300_000,
    )
    failure_prepared = failure_manager.prepare(exact_request)
    assert failure_manager.commit(failure_prepared).accepted
    failure_manager.note_safe_command(0.37)
    origins_before = tuple(
        record.evidence_id for record in failure_records if isinstance(record.payload, ForecastOriginEvidence)
    )
    failed_generation = failure_manager.state.role_generation
    fallback = failure_manager.fallback(
        "active-solve-failed",
        generation=failed_generation,
        last_safe_command=0.37,
    )
    origins_after = tuple(
        record.evidence_id for record in failure_records if isinstance(record.payload, ForecastOriginEvidence)
    )
    assert fallback.active_kind == GREY_BOX_KIND
    assert fallback.fallback_kind == GREY_BOX_KIND
    assert fallback.failed_generation == failed_generation
    assert fallback.failed_digest == candidate_digest
    assert fallback.last_safe_command == pytest.approx(0.37)
    assert origins_after == origins_before
    assert len(failure_events) == 1
    assert isinstance(failure_events[0].payload, FallbackEvidence)
    assert failure_events[0].payload.reason == "active-solve-failed"
    assert failure_manager.commit(failure_prepared).reason == "failed-generation-cannot-be-reenabled"


def test_persistence_activation_and_restart_failures_remain_off_the_safe_control_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_snapshot = _fitted_snapshot()
    candidate_digest = canonical_snapshot_digest(candidate_snapshot)
    grey_snapshot = {"schema": "grey-box-adapter/v1", "gain": 1.0}
    incumbent_digest = canonical_snapshot_digest(grey_snapshot)
    summary = _record(
        "failure:summary",
        SessionSummaryEvidence(
            completed_origins=0,
            accepted_observations=1,
            rejected_observations=0,
        ),
        candidate_digest=candidate_digest,
        incumbent_digest=incumbent_digest,
        timestamp_ms=1,
    )
    safe_controller = _configure_mpc(monkeypatch)
    safe_runner = SyncControllerRunner(safe_controller)
    before_failure = safe_runner.latest_from(100.0)
    assert before_failure.allocation == before_failure.baseline_allocation
    assert safe_controller.get_status()["activation"]["active_kind"] == GREY_BOX_KIND

    # Disk failure is terminal for learning, but it yields an exact typed gap off-path.
    def fail_write(_records: object) -> None:
        raise OSError("disk-full")

    worker = ModelPersistenceWorker(
        _PersistenceStore(),
        _Logger(),
        append_evidence=fail_write,
    )
    assert worker.submit_evidence(summary).accepted
    assert worker.flush_and_stop(timeout=1.0)
    rejected = worker.submit_evidence(summary.model_copy(update={"evidence_id": "failure:later"}))
    assert not rejected.accepted
    assert isinstance(rejected.recorder_gap.payload, RecorderGapEvidence)
    assert rejected.recorder_gap.payload.reason == "persistence-failed"
    assert worker.evidence_blocked
    after_failure = safe_runner.latest_from(101.0)
    assert after_failure.calibration is not None
    assert after_failure.calibration.probe_q == 0.0
    assert after_failure.allocation == after_failure.baseline_allocation
    assert safe_controller.get_status()["activation"]["active_kind"] == GREY_BOX_KIND

    decision = _record(
        "failure:decision",
        ConfidenceDecisionEvidence(decision_id="failure-decision", blocked=False),
        candidate_digest=candidate_digest,
        incumbent_digest=incumbent_digest,
        timestamp_ms=3,
        cook_id=None,
    )
    refresh = _record(
        "failure:refresh",
        RefreshDiagnosticsEvidence(accepted=True),
        candidate_digest=candidate_digest,
        incumbent_digest=incumbent_digest,
        timestamp_ms=2,
    )
    records = [refresh, decision]

    # A partial/declined activation transaction never publishes the candidate.
    partial = ActivationManager(
        lambda: tuple(records),
        candidate_snapshot=candidate_snapshot,
        rollback_snapshot=grey_snapshot,
        controller_configuration={"controller": "mpc"},
        prospective_solve=lambda _candidate, _configuration: 0.3,
        persist_activation=lambda _record: False,
    )
    request = ActivationRequest(candidate_digest, "failure-decision")
    partial_result = partial.commit(partial.prepare(request))
    assert not partial_result.accepted
    assert partial_result.reason == "activation-persistence-failed"
    assert partial.active_kind == GREY_BOX_KIND

    # Restart without a durable activation reconstructs no authority and fails closed exactly.
    restarted = ActivationManager(
        lambda: tuple(records),
        candidate_snapshot=candidate_snapshot,
        rollback_snapshot=grey_snapshot,
        controller_configuration={"controller": "mpc"},
        prospective_solve=lambda _candidate, _configuration: 0.3,
    )
    restart_result = restarted.commit(restarted.prepare(request))
    assert not restart_result.accepted
    assert restart_result.reason == "activation-persistence-unavailable"
    assert restarted.active_kind == GREY_BOX_KIND
