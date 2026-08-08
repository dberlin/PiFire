"""End-to-end acceptance for the real-grill MPC evidence rollout boundary."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest

from tests.unit.runtime.conftest import hold_cycle as _runtime_hold_cycle

from common.controller_model_state import CheckpointSaveOutcome
from common.control_trace import AmbientSource
from common.datastore_accessors import (
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
from controller.applied_output import AppliedOutput, FrameFeedbackDisposition, OutputSource
from controller.linear_mpc.adaptation import AdaptationPolicy, OnlineAdaptation
from controller.linear_mpc.contracts import FrameObservation
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
from controller.runtime.modes.hold import HoldMode
from controller.mpc import CalibrationCommand, Controller
from controller.runtime.model_persistence import ModelPersistenceWorker
from controller.runtime.runner import SyncControllerRunner
from tests.unit.mpc.test_innovation_state_space import _config as state_space_config
from tests.unit.mpc.test_innovation_state_space import _frames as state_space_frames


@pytest.fixture
def hold_cycle(monkeypatch: pytest.MonkeyPatch):
    return _runtime_hold_cycle.__wrapped__(monkeypatch)


_GENERATION = 4


class _Estimator:
    def __init__(self) -> None:
        self.x = np.array([20.0, 0.0])
        self.t_step = 20.0

    def update(self, _load: float, temperature: float) -> np.ndarray:
        self.x = np.array([temperature, 0.0])
        return self.x.copy()


class _Policy:
    def __init__(self) -> None:
        self.target_c = 110.0
        self.ambient_c = 17.0
        self.feedforward_per_c = 0.0022
        self.proportional_gain = 0.0008
        self.steps = 0
        self.equilibrium_load = lambda: 0.0

    def make_step(self, state: object) -> np.ndarray:
        temperature = float(np.asarray(state).reshape(-1)[0])
        duty = self.feedforward_per_c * (self.target_c - self.ambient_c)
        duty += self.proportional_gain * (self.target_c - temperature)
        self.steps += 1
        return np.array([[float(np.clip(duty, 0.04, 0.9) - self.equilibrium_load())]])


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


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _new_plant(plant_type: type[GrillSim], seed: int) -> GrillSim:
    return plant_type(seed=seed, T0=105.0) if plant_type is MAKGrillSim else plant_type(seed=seed)


def _step_plant_frame(
    plant: GrillSim,
    auger_duty: float,
    fan_duty: float | None,
) -> tuple[float, float, float]:
    on_seconds = round(20.0 * auger_duty)
    realized_auger_duty = on_seconds / 20.0
    fan_fraction = 0.5 if fan_duty is None else fan_duty
    if fan_fraction > 1.0:
        fan_fraction /= 100.0
    for second in range(20):
        plant.step(second < on_seconds, fan_fraction)
    return plant.measured(), realized_auger_duty, fan_fraction


def _plant_snapshot(plant_type: type[GrillSim], seed: int) -> dict[str, object]:
    plant = _new_plant(plant_type, seed)
    frames: list[FrameObservation] = []
    for sequence in range(420):
        requested = 0.15 + 0.7 * ((sequence * 17 % 23) / 22.0)
        observed, realized_auger_duty, realized_fan_duty = _step_plant_frame(
            plant,
            requested * 0.9,
            0.5,
        )
        frames.append(
            FrameObservation(
                frame_start_s=sequence * 20.0,
                frame_end_s=(sequence + 1) * 20.0,
                temp_c=observed,
                setpoint_c=180.0,
                ambient_c=plant.T_amb,
                requested_q=requested,
                realized_q=realized_auger_duty / 0.9,
                requested_auger_duty=requested * 0.9,
                delivered_on_s=realized_auger_duty * 20.0,
                requested_fan_duty=0.5,
                actual_fan_duty=realized_fan_duty,
                result_revision=sequence + 1,
                output_source="controller",
                lid_open=False,
                safety_inhibited=False,
                manual_override=False,
                stale=False,
                skipped=False,
                reset=False,
                continuous=True,
                role_generation=0,
                observation_sequence=sequence + 1,
            )
        )
    config = replace(
        state_space_config(orders=(1, 2), delays=(1, 2, 3)),
        max_buffer_samples=512,
        refresh_interval_s=100_000.0,
    )
    model = InnovationStateSpace(config)
    assert model.fit(frames).accepted
    return dict(model.snapshot())


def _temperature_band(temperature_c: float) -> str:
    if temperature_c < 135.0:
        return "low"
    if temperature_c < 190.0:
        return "middle"
    return "high"


def _run_evidence_cook(
    runner: SyncControllerRunner,
    hold: HoldMode,
    controller: Controller,
    plant: GrillSim,
    clock: _Clock,
    *,
    cook_id: str,
    frames: int,
    calibration: bool,
) -> None:
    generation = controller._online.role_generation
    runner.bind_evidence_context(runner.configuration_revision(), f"session-{cook_id}", cook_id)
    hold._trace_session_id = f"session-{cook_id}"
    hold._trace_cook_id = cook_id
    policy = controller._acceptance_policy
    if calibration:
        runner.request_calibration(
            CalibrationCommand(
                action="start",
                command_revision=1,
                ambient_c=plant.T_amb,
                ambient_source="configured",
                empty_grill_confirmed=True,
                pellets_confirmed=True,
            )
        )
    completed_stages: tuple[str, ...] = ()
    temperature = plant.measured()
    for sequence in range(frames):
        if calibration:
            last = controller._calibration.snapshot().get("last")
            stage = getattr(last, "stage", None)
            completed = getattr(last, "completed_stages", ())
            if stage == "coast" and len(completed) < 3:
                stage = ("low", "middle", "high")[len(completed)]
            target = {"low": 107.2, "middle": 162.8, "high": 218.3}.get(stage, 107.2)
        else:
            target = 180.0
        policy.target_c = target
        controller.set_target(target)
        result = runner.latest_from(temperature)
        assert result.allocation is not None and result.baseline_allocation is not None
        decision = result.calibration
        assert decision is not None
        temperature, realized_auger_duty, realized_fan_duty = _step_plant_frame(
            plant,
            result.allocation.auger_duty,
            result.allocation.fan_duty,
        )
        clock.now += 20.0
        runner.set_output(
            AppliedOutput(
                ratio=realized_auger_duty,
                source=OutputSource.CONTROLLER,
                timestamp=clock.now,
                requested=result.allocation.auger_duty,
                producing_result_revision=result.revision,
                producing_calibration_revision=decision.command_revision,
                producing_calibration_action=decision.command_action,
                producing_calibration_generation=decision.command_generation,
                feedback_disposition=FrameFeedbackDisposition.COMPLETE,
                sample_complete=True,
            )
        )
        completed_stages = decision.completed_stages or completed_stages
        observation = FrameObservation(
            frame_start_s=clock.now - 20.0,
            frame_end_s=clock.now,
            temp_c=temperature,
            setpoint_c=target,
            ambient_c=plant.T_amb,
            requested_q=result.allocation.normalized_combustion_load,
            realized_q=min(1.0, realized_auger_duty / result.allocation.u_max),
            requested_auger_duty=result.allocation.auger_duty,
            delivered_on_s=realized_auger_duty * 20.0,
            requested_fan_duty=result.allocation.fan_duty,
            actual_fan_duty=realized_fan_duty,
            result_revision=result.revision,
            output_source="controller",
            lid_open=False,
            safety_inhibited=False,
            manual_override=False,
            stale=False,
            skipped=False,
            reset=False,
            continuous=True,
            role_generation=generation,
            observation_sequence=sequence + 1,
            ambient_source=AmbientSource.CONFIGURED,
            baseline_q=result.baseline_allocation.normalized_combustion_load,
            probe_q=decision.probe_q,
            allocated_q=result.allocation.normalized_combustion_load,
            scheduled_on_s=realized_auger_duty * 20.0,
            realized_auger_duty=realized_auger_duty,
            allocator_revision=result.allocation.allocator_revision,
            allocation_clamp_reasons=(),
            calibration_stage=decision.stage,
            calibration_fit=calibration and decision.command_revision > 0,
            temperature_band=_temperature_band(temperature),
            calibration_command_revision=decision.command_revision,
            calibration_command_action=decision.command_action,
            baseline_allocation=result.baseline_allocation,
            combined_allocation=result.allocation,
            calibration_status="active" if decision.active else "inactive",
            completed_calibration_stages=decision.completed_stages,
        )
        hold._deliver_completed_pulse_observation((sequence, sequence + 1), observation)
        hold._reconcile_model_observation_outcomes(clock.now)
        if calibration and not decision.active and completed_stages == ("low", "middle", "high"):
            break
    if calibration:
        assert completed_stages == ("low", "middle", "high")
        assert decision.stage == "high"
        assert decision.events[-1].kind == "completed"
    return None


def _fitted_snapshot() -> dict[str, object]:
    model = InnovationStateSpace(state_space_config(orders=(1,), delays=(1,)))
    assert model.fit(state_space_frames(order=1)).accepted
    return model.snapshot()


def _snapshot_last_time(snapshot: dict[str, object]) -> float:
    record = snapshot.get("record")
    assert isinstance(record, dict)
    lag = record.get("lag")
    assert isinstance(lag, dict)
    times = lag.get("time_s")
    assert isinstance(times, list) and times
    value = times[-1]
    assert isinstance(value, int | float) and not isinstance(value, bool)
    return float(value)


def _configure_mpc(monkeypatch: pytest.MonkeyPatch) -> Controller:
    policy = _Policy()
    monkeypatch.setattr(Controller, "_build_for", lambda self, cfg: (_Estimator(), None, object(), policy))
    controller = Controller(
        {"n_delay": 0, "enable_fan_input": False, "enable_online_adaptation": True},
        "C",
        {"u_max": 0.9},
        _online_challenger_kind="state-space",
    )
    policy.equilibrium_load = lambda: controller._policy_equilibrium_load
    controller._acceptance_policy = policy
    controller.set_target(110.0)
    return controller


@pytest.mark.parametrize(
    ("plant_type", "seed"),
    ((GrillSim, 111), (MAKGrillSim, 222)),
    ids=("GrillSim", "MAKGrillSim"),
)
def test_complete_real_grill_evidence_lifecycle_remains_confidence_gated(
    ds,
    hold_cycle,
    monkeypatch: pytest.MonkeyPatch,
    plant_type: type[GrillSim],
    seed: int,
) -> None:
    plant = _new_plant(plant_type, seed)
    plant_name = plant_type.__name__
    clock = _Clock()
    monkeypatch.setattr("controller.runtime.runner.time.monotonic", clock)
    monkeypatch.setattr("controller.mpc.time.monotonic", clock)
    controller = _configure_mpc(monkeypatch)
    assert controller._online is not None
    candidate_snapshot = _plant_snapshot(plant_type, seed)
    clock.now = _snapshot_last_time(candidate_snapshot)
    initial_candidate_snapshot = deepcopy(candidate_snapshot)
    if plant_type is MAKGrillSim:
        controller._acceptance_policy.ambient_c = plant.T_amb
        controller._acceptance_policy.feedforward_per_c = 0.001
        controller._acceptance_policy.proportional_gain = 0.003
    candidate = InnovationStateSpace.from_snapshot(candidate_snapshot)
    controller._online = OnlineAdaptation(
        controller._new_grey_box_model(),
        candidate,
        replace(AdaptationPolicy(), required_consecutive_wins=99),
    )
    candidate_digest = controller._online.challenger_digest
    incumbent_digest = OnlineAdaptation.model_digest(controller._online.incumbent)
    runner = SyncControllerRunner(controller)
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    assert hold._persistence_worker is not None
    assert hold._persistence_worker.flush_and_stop(timeout=1.0)
    hold._persistence_worker = ModelPersistenceWorker(
        _PersistenceStore(),
        _Logger(),
        append_evidence=append_model_evidence,
        evidence_capacity=10_000,
    )
    hold.state.metrics = {"id": f"real-grill-{plant_name}"}
    hold._ensure_trace_session(0.0)

    # 1. The production Hold runner remains grey-box and probe-free before an explicit command.
    hold.on_tick(4.0, plant.measured(), hold.grill.get_output_status())
    ordinary = runner.latest_from(plant.measured())
    assert ordinary.calibration is not None
    assert ordinary.allocation is not None
    assert ordinary.baseline_allocation is not None
    assert ordinary.calibration.probe_q == 0.0
    assert ordinary.allocation == ordinary.baseline_allocation
    assert controller.get_status()["activation"]["active_kind"] == GREY_BOX_KIND

    # 2-4. Guarded calibration and later ordinary cooks traverse the production
    # Hold reconciliation, trace, serializer, and persistence path.
    _run_evidence_cook(
        runner,
        hold,
        controller,
        plant,
        clock,
        cook_id="calibration-cook",
        frames=1_000,
        calibration=True,
    )
    _run_evidence_cook(
        runner,
        hold,
        controller,
        plant,
        clock,
        cook_id="ordinary-a",
        frames=80,
        calibration=False,
    )
    _run_evidence_cook(
        runner,
        hold,
        controller,
        plant,
        clock,
        cook_id="ordinary-b",
        frames=80,
        calibration=False,
    )
    hold.ctx.clock.advance(max(0.0, clock.now + 1.0 - hold.ctx.clock.now()))
    hold.teardown(plant.measured())
    simulator_records = tuple(
        record for record in read_model_evidence() if record.cook_id in {"calibration-cook", "ordinary-a", "ordinary-b"}
    )
    calibration_records = tuple(record for record in simulator_records if record.cook_id == "calibration-cook")
    summaries = tuple(
        record.payload for record in calibration_records if isinstance(record.payload, CalibrationSummaryEvidence)
    )
    assert any(summary.probe_q != 0.0 and abs(summary.probe_q) <= 0.05 for summary in summaries)
    assert {summary.stage for summary in summaries} >= {"low", "middle", "high", "coast"}
    for summary in summaries:
        assert summary.combined_allocation is not None
        requested_on_seconds = 20.0 * summary.combined_allocation.auger_duty
        assert summary.delivered_on_seconds == round(requested_on_seconds)
        assert summary.scheduled_on_seconds == summary.delivered_on_seconds
        assert summary.requested_fan_duty is None
        assert summary.actual_fan_duty == pytest.approx(0.5)
    assert any(
        summary.delivered_on_seconds != pytest.approx(20.0 * summary.combined_allocation.auger_duty)
        for summary in summaries
        if summary.combined_allocation is not None
    )
    origins = tuple(record for record in simulator_records if isinstance(record.payload, ForecastOriginEvidence))
    assert {record.cook_id for record in origins if not record.payload.calibration_fit} == {
        "ordinary-a",
        "ordinary-b",
    }
    assert all(record.payload.calibration_fit for record in origins if record.cook_id == "calibration-cook")
    assert all(record.payload.origin_time_ms < record.payload.completion_time_ms for record in origins)

    candidate_snapshot = dict(controller._online.challenger.snapshot())
    assert canonical_snapshot_digest(candidate_snapshot) == candidate_digest
    assert candidate_snapshot["record"] != initial_candidate_snapshot["record"]
    append_model_evidence(
        [
            _record(
                f"{plant_name}:timing:simulator",
                TimingDistributionEvidence(
                    sample_count=50,
                    p50_ms=10.0,
                    p95_ms=20.0,
                    p99_ms=200.0,
                    hardware_provenance="workstation",
                ),
                candidate_digest=candidate_digest,
                incumbent_digest=incumbent_digest,
                timestamp_ms=int(clock.now * 1_000) + 1,
                generation=controller._online.role_generation,
            )
        ]
    )
    simulator_report = current_evidence_report(read_model_evidence())
    simulator_payload = simulator_report.to_dict()
    assert simulator_payload["active_model"]["kind"] == GREY_BOX_KIND
    assert simulator_payload["default_model"]["kind"] == GREY_BOX_KIND
    assert simulator_payload["target_timing"]["hardware_provenance"] == "workstation"
    assert simulator_payload["target_timing"]["gate_passed"] is False
    plant_specific_failures = (
        {
            "absolute-rmse:3/high/heating/configured/0",
            "absolute-rmse:3/middle/heating/configured/0",
            "absolute-rmse:15/middle/heating/configured/0",
            "signed-bias:3/high/coasting/configured/0",
            "band-error:3/high/coasting/configured/0",
        }
        if plant_type is MAKGrillSim
        else set()
    )
    expected_blockers = {
        "state-alignment",
        "production-prospective-construction",
        "braking-error",
        "target-timing",
        "provenance-integrity",
        "missing-horizon-90",
        "missing-horizon-180",
        "bootstrap-unavailable",
        "relative-bootstrap",
        "cook-effective-weight",
        "signed-bias:3/high/heating/configured/0",
        "band-error:3/high/heating/configured/0",
        "signed-bias:3/middle/heating/configured/0",
        "band-error:3/middle/heating/configured/0",
        "absolute-rmse:15/high/coasting/configured/0",
        "signed-bias:15/high/coasting/configured/0",
        "band-error:15/high/coasting/configured/0",
        "absolute-rmse:15/high/heating/configured/0",
        "signed-bias:15/high/heating/configured/0",
        "band-error:15/high/heating/configured/0",
        "signed-bias:15/middle/heating/configured/0",
        "band-error:15/middle/heating/configured/0",
        "absolute-rmse:45/high/coasting/configured/0",
        "signed-bias:45/high/coasting/configured/0",
        "band-error:45/high/coasting/configured/0",
        "absolute-rmse:45/high/heating/configured/0",
        "signed-bias:45/high/heating/configured/0",
        "band-error:45/high/heating/configured/0",
        "absolute-rmse:45/middle/heating/configured/0",
        "signed-bias:45/middle/heating/configured/0",
        "band-error:45/middle/heating/configured/0",
    }
    expected_blockers.update(plant_specific_failures)
    assert set(simulator_payload["blockers"]) == expected_blockers
    expected_missing_gates = {
        "state-alignment",
        "production-prospective-construction",
        "braking-error",
        "target-timing",
        "provenance-integrity",
        "missing-horizon-90",
        "missing-horizon-180",
        "bootstrap:3/high/coasting/configured/0",
        "relative-bootstrap:3/high/coasting/configured/0",
        "cook-weight:3/high/coasting/configured/0",
        "signed-bias:3/high/heating/configured/0",
        "band-error:3/high/heating/configured/0",
        "bootstrap:3/high/heating/configured/0",
        "relative-bootstrap:3/high/heating/configured/0",
        "cook-weight:3/high/heating/configured/0",
        "signed-bias:3/middle/heating/configured/0",
        "band-error:3/middle/heating/configured/0",
        "absolute-rmse:15/high/coasting/configured/0",
        "signed-bias:15/high/coasting/configured/0",
        "band-error:15/high/coasting/configured/0",
        "bootstrap:15/high/coasting/configured/0",
        "relative-bootstrap:15/high/coasting/configured/0",
        "cook-weight:15/high/coasting/configured/0",
        "absolute-rmse:15/high/heating/configured/0",
        "signed-bias:15/high/heating/configured/0",
        "band-error:15/high/heating/configured/0",
        "bootstrap:15/high/heating/configured/0",
        "relative-bootstrap:15/high/heating/configured/0",
        "cook-weight:15/high/heating/configured/0",
        "signed-bias:15/middle/heating/configured/0",
        "band-error:15/middle/heating/configured/0",
        "absolute-rmse:45/high/coasting/configured/0",
        "signed-bias:45/high/coasting/configured/0",
        "band-error:45/high/coasting/configured/0",
        "bootstrap:45/high/coasting/configured/0",
        "relative-bootstrap:45/high/coasting/configured/0",
        "cook-weight:45/high/coasting/configured/0",
        "absolute-rmse:45/high/heating/configured/0",
        "signed-bias:45/high/heating/configured/0",
        "band-error:45/high/heating/configured/0",
        "bootstrap:45/high/heating/configured/0",
        "relative-bootstrap:45/high/heating/configured/0",
        "absolute-rmse:45/middle/heating/configured/0",
        "signed-bias:45/middle/heating/configured/0",
        "band-error:45/middle/heating/configured/0",
        "bootstrap:45/middle/heating/configured/0",
        "relative-bootstrap:45/middle/heating/configured/0",
        "cook-weight:45/middle/heating/configured/0",
    }
    expected_missing_gates.update(plant_specific_failures)
    if plant_type is MAKGrillSim:
        expected_missing_gates.difference_update(
            {
                "bootstrap:3/high/heating/configured/0",
                "relative-bootstrap:3/high/heating/configured/0",
                "cook-weight:3/high/heating/configured/0",
                "cook-weight:15/high/heating/configured/0",
            }
        )
        expected_missing_gates.update(
            {
                "bootstrap:3/middle/heating/configured/0",
                "relative-bootstrap:3/middle/heating/configured/0",
                "cook-weight:3/middle/heating/configured/0",
                "bootstrap:15/middle/heating/configured/0",
                "relative-bootstrap:15/middle/heating/configured/0",
                "cook-weight:15/middle/heating/configured/0",
            }
        )
    assert set(simulator_payload["missing_gates"]) == expected_missing_gates
    assert simulator_payload["status"] != "ready-for-review"

    artifact = json.loads(build_evidence_artifact(simulator_report, read_model_evidence()))
    assert artifact["authority"] == "read-only-evidence"
    assert artifact["report"]["calibration"]["completed_stages"] == ["low", "middle", "high", "coast"]

    # Compact evidence survives the raw trace retention boundary without
    # manufacturing a qualifying decision for a simulator that did not pass.
    evidence_ids = tuple(record.evidence_id for record in read_model_evidence())
    assert prune_control_trace(int(clock.now * 1_000) + 1, limit=10_000) > 0
    assert tuple(record.evidence_id for record in read_model_evidence()) == evidence_ids
    assert read_model_activation() is None


def test_runner_terminal_drop_survives_raw_pruning_as_a_confidence_blocker(
    ds,
    hold_cycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _configure_mpc(monkeypatch)
    assert controller._online is not None
    candidate_snapshot = _fitted_snapshot()
    candidate = InnovationStateSpace.from_snapshot(candidate_snapshot)
    controller._online = OnlineAdaptation(controller._new_grey_box_model(), candidate, AdaptationPolicy())
    candidate_digest = controller._online.challenger_digest
    generation = controller._online.role_generation
    runner = SyncControllerRunner(controller)
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.state.metrics = {"id": "runner-terminal-drop"}
    hold._ensure_trace_session(0.0)
    assert hold._persistence_worker is not None
    assert hold._persistence_worker.flush_and_stop(timeout=1.0)
    worker = ModelPersistenceWorker(
        _PersistenceStore(),
        _Logger(),
        append_evidence=append_model_evidence,
    )
    hold._persistence_worker = worker
    frame_offset_s = _snapshot_last_time(candidate_snapshot)

    for index, frame in enumerate(state_space_frames(order=1, count=31), start=1):
        observation = replace(
            frame,
            frame_start_s=frame.frame_start_s + frame_offset_s,
            frame_end_s=frame.frame_end_s + frame_offset_s,
            observation_sequence=index,
            output_source=OutputSource.CONTROLLER.value,
            result_revision=index,
        )
        hold._deliver_completed_pulse_observation((index, index + 1), observation)
    hold._reconcile_model_observation_outcomes(now=3_000.0)

    assert worker.flush_and_stop(timeout=1.0)
    safe = runner.latest_from(100.0)
    assert safe.allocation == safe.baseline_allocation
    assert controller.get_status()["activation"]["active_kind"] == GREY_BOX_KIND
    hold._persistence_worker = None
    hold.teardown(100.0)

    gaps = tuple(
        record
        for record in read_model_evidence()
        if isinstance(record.payload, RecorderGapEvidence) and record.payload.reason == "runner-outcome-evicted"
    )
    assert len(gaps) == 1
    assert gaps[0].role_generation == generation
    assert gaps[0].model_digest is None
    assert gaps[0].provenance_digest is None
    gap_ids = tuple(record.evidence_id for record in gaps)
    assert prune_control_trace(3_000_000, limit=100) >= 1
    assert (
        tuple(record.evidence_id for record in read_model_evidence() if isinstance(record.payload, RecorderGapEvidence))
        == gap_ids
    )

    report = evaluate_confidence(
        read_model_evidence(),
        activation_state={
            "status": "collecting",
            "active_kind": GREY_BOX_KIND,
            "candidate_digest": candidate_digest,
            "candidate_generation": generation,
        },
        target_timing=None,
        config=ConfidenceConfig(bootstrap_seed=7),
    )
    assert next(gate for gate in report.gates if gate.name == "evidence-continuity") == GateResult(
        "evidence-continuity",
        False,
        "runner-outcome-evicted",
    )
    assert "runner-outcome-evicted" in report.blockers


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

    overflow_worker = ModelPersistenceWorker(
        _PersistenceStore(),
        _Logger(),
        evidence_capacity=1,
    )
    overflow = overflow_worker.submit_evidence_batch(
        (summary, summary.model_copy(update={"evidence_id": "failure:overflow"}))
    )
    assert not overflow.accepted
    assert isinstance(overflow.recorder_gap.payload, RecorderGapEvidence)
    assert overflow.recorder_gap.payload.reason == "evidence-queue-overflow"
    assert overflow_worker.evidence_blocked
    gap_report = evaluate_confidence(
        (overflow.recorder_gap,),
        activation_state={
            "status": "collecting",
            "active_kind": GREY_BOX_KIND,
            "candidate_digest": candidate_digest,
            "candidate_generation": _GENERATION,
        },
        target_timing=None,
        config=ConfidenceConfig(bootstrap_seed=7),
    )
    assert next(gate for gate in gap_report.gates if gate.name == "evidence-continuity") == GateResult(
        "evidence-continuity",
        False,
        "evidence-queue-overflow",
    )
    assert "evidence-queue-overflow" in gap_report.blockers
    assert overflow_worker.flush_and_stop(timeout=1.0)
    assert safe_runner.latest_from(100.5).allocation == before_failure.baseline_allocation

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
