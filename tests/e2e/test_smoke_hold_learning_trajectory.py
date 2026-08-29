"""Default-running production proof for Smoke-to-Hold MPC trajectory seeding."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import ceil
from typing import Any

import pytest

import controller.runtime.modes.hold as hold_module
import controller.runtime.runner as runner_module
from common.control_delta import control_delta
from common.control_trace import (
    AllocationClampReason,
    AmbientSource,
    AmbientUncertainty,
    ControllerType,
    TraceEventKind,
)
from common.controller_model_state import ControllerModelStore
from common.learning_trajectory import TrajectoryBreakReason
from common.model_evidence import ConfidenceDecisionEvidence, EvidenceKind, ModelEvidenceRecord
from common.persistence import runtime as runtime_persistence
from common.persistence.control_trace import read_control_trace_cook, read_control_trace_session
from common.persistence.learning_trajectory import LearningTrajectoryRepository
from common.persistence.model_evidence import read_model_activation
from controller.acados import GreyBoxMPCConfig
from controller.applied_output import AppliedOutput, OutputSource
from controller.model_learning.activation import PreparedActivationRecord
from controller.model_learning.contracts import (
    CandidateOrigin,
    FitRequest,
    FrameObservation,
    activation_policy_for_origin,
)
from controller.mpc import Controller
from controller.mpc_config import DEFAULT_MPC_CONFIG
from controller.runtime.actuation_delivery import ActuationDeliveryJournal, DeliveredGrillPlatform
from controller.runtime.control_trace_recorder import ControlTraceRecorder
from controller.runtime.control_trace_session import ControlTraceSession
from controller.runtime.controller import run_work_cycle
from controller.runtime.learning_trajectory import (
    LearningTrajectoryRuntime,
    ModeEntered,
    ModeExited,
    ThermalSample,
)
from controller.runtime.model_fitting import GreyFitSuccess, fit_segmented_grey, segmented_corpus_fit_job
from controller.runtime.model_persistence import ModelPersistenceWorker
from controller.runtime.modes.base import ControlMode
from controller.runtime.runner import SyncControllerRunner
from controller.runtime.store import SqliteStore
from tests.characterization.fixtures import base_control, base_pellet_db, base_settings
from tests.characterization.harness import make_ctx
from tests.e2e._mpc_online_learning_helpers import (
    _CYCLE,
    _FRAME_SECONDS,
    _MAK_SUPPORT_PARAMETERS,
    _SETPOINT_C,
    _TEST_LOGGER,
    _U_MAX,
    _mak_grey_corpus_rows,
    _trace_context,
)
from tests.fakes.grill import FakeGrillPlatform
from tests.fakes.probes import FakeProbes

_FRAME_MS = _FRAME_SECONDS * 1_000
_WALL_OFFSET_MS = 1_700_000_000_000
_CORPUS_START_MS = 20_000_000


def _digest(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class _FixedTimeline:
    monotonic_ms: int = 0

    def advance_frame(self) -> None:
        self.monotonic_ms += _FRAME_MS

    def pair(self) -> tuple[int, int]:
        return self.monotonic_ms, _WALL_OFFSET_MS + self.monotonic_ms

    def wall_ms(self) -> int:
        return _WALL_OFFSET_MS + self.monotonic_ms


class _TransitioningProbes:
    """Advance the physical clock once per real read, then request a real handoff."""

    def __init__(
        self,
        probes: FakeProbes,
        store: SqliteStore,
        timeline: _FixedTimeline,
        *,
        transition_on_read: int,
        target_mode: str,
        target_setpoint: float,
    ) -> None:
        self._probes = probes
        self._store = store
        self._timeline = timeline
        self._transition_on_read = transition_on_read
        self._target_mode = target_mode
        self._target_setpoint = target_setpoint
        self._reads = 0
        self._requested = False

    def read_probes(self, *, excitation=None, now=None):
        self._reads += 1
        self._timeline.advance_frame()
        if self._reads >= self._transition_on_read and not self._requested:
            self._requested = True
            self._store.enqueue_control_delta(
                control_delta(
                    set_values={
                        "mode": self._target_mode,
                        "primary_setpoint": self._target_setpoint,
                        "updated": True,
                    }
                ),
                origin="e2e-mode-boundary",
            )
        return self._probes.read_probes(excitation=excitation, now=now)

    def __getattr__(self, name: str):
        return getattr(self._probes, name)


class _ObservedSyncRunner(SyncControllerRunner):
    """Observe real runner boundaries while delegating every operation."""

    def __init__(self, core: Controller, persistence: ModelPersistenceWorker) -> None:
        ticks = iter(index / 1_000 for index in range(100_000))
        super().__init__(
            core,
            controller_type=ControllerType.MPC,
            model_persistence=persistence,
            monotonic_clock=lambda: next(ticks),
            wall_clock=lambda: _WALL_OFFSET_MS / 1_000,
        )
        self.core = core
        self.persistence = persistence
        self.events: list[str] = []
        self.seeds: list[Any] = []
        self.pre_solve_states: list[Any] = []
        self.outputs: list[tuple[AppliedOutput, str, bool, str | None]] = []
        self.incumbent_digest = core.active_control_pair.descriptor.model_digest
        self.candidate_digest: str | None = None
        self.prepared_queued = False
        self.preparation_error: BaseException | None = None

    def seed_operating_state(self, seed) -> None:
        self.events.append("seed")
        self.seeds.append(seed)
        super().seed_operating_state(seed)
        try:
            candidate_settings = dict(DEFAULT_MPC_CONFIG)
            candidate_settings["theta"] = float(candidate_settings["theta"]) + 10.0
            active = self.core.active_control_pair.descriptor
            configured = self.core._pair_factory.configured(  # noqa: SLF001 - ownership is asserted here
                candidate_settings,
                candidate_generation=active.candidate_generation + 1,
                role_generation=active.role_generation + 1,
                model_identified=True,
            )
            candidate = self.core._pair_factory.build(configured, authorized=False)  # noqa: SLF001
            decision_id = "smoke-hold-pre-active-candidate"
            prepared = PreparedActivationRecord.prepared(
                timestamp_ms=_WALL_OFFSET_MS,
                incumbent=active,
                candidate=candidate.descriptor,
                origin=CandidateOrigin.PASSIVE_ONLINE,
                policy=activation_policy_for_origin(CandidateOrigin.PASSIVE_ONLINE),
                decision_id=decision_id,
            )
            confidence = ModelEvidenceRecord(
                evidence_id="smoke-hold-pre-active-confidence",
                kind=EvidenceKind.CONFIDENCE_DECISION,
                session_id="smoke-hold-production",
                cook_id=None,
                timestamp_ms=_WALL_OFFSET_MS,
                role_generation=active.role_generation,
                model_digest=candidate.descriptor.model_digest,
                provenance_digest=active.model_digest,
                payload=ConfidenceDecisionEvidence(
                    decision_id=decision_id,
                    blocked=False,
                    reason=None,
                ),
            )
            confidence_receipt = self.core.submit_activation_confidence(confidence)
            if not (confidence_receipt is not None and confidence_receipt.wait(5.0) and confidence_receipt.durable):
                raise RuntimeError("confidence decision did not become durable")
            receipt = self.persistence.submit_activation_phase(prepared, expected_phase=None)
            durable = receipt.wait(5.0)
            self.prepared_queued = bool(
                durable and receipt.durable and self.core.queue_prepared_activation(prepared, candidate, receipt)
            )
            self.candidate_digest = candidate.descriptor.model_digest
            self.events.append("candidate-prepared")
        except BaseException as error:  # captured so the real mode always tears down cleanly
            self.preparation_error = error

    def latest(self):
        self.events.append("solve")
        self.pre_solve_states.append(self.core.active_control_pair.core.capture_operating_state())
        return super().latest()

    def set_output(self, applied: AppliedOutput) -> None:
        activation = read_model_activation()
        self.events.append("output")
        self.outputs.append(
            (
                applied,
                self.core.active_control_pair.descriptor.model_digest,
                self.core.activation_output_authorized,
                None if activation is None else activation.phase,
            )
        )
        super().set_output(applied)


def _seed_sqlite_store(store: SqliteStore, settings: dict[str, Any], control: dict[str, Any]) -> None:
    runtime_persistence.write_settings_store(settings)
    store.system_commands().flush()
    store.system_output().flush()
    store.display_commands().flush()
    store.flush_metrics()
    store.write_control_snapshot(control, origin="smoke-hold-e2e")
    store.write_pellet_db(base_pellet_db())


def test_smoke_to_hold_warms_real_mpc_before_first_solve_and_fences_pre_active_candidate(
    ds,
    monkeypatch,
) -> None:
    settings = base_settings()
    settings["globals"]["units"] = "C"
    settings["controller"]["selected"] = "mpc"
    settings["controller"]["config"]["mpc"] = dict(DEFAULT_MPC_CONFIG)
    settings["safety"]["maxtemp"] = 400
    control = base_control("Smoke")
    control["cook_id"] = "smoke-hold-production"
    control["primary_setpoint"] = 150.0
    control["safety"]["startuptemp"] = 0
    control["safety"]["afterstarttemp"] = 110.0

    store = SqliteStore()
    _seed_sqlite_store(store, settings, control)
    timeline = _FixedTimeline()
    journal = ActuationDeliveryJournal(
        monotonic_clock=lambda: timeline.monotonic_ms,
        wall_clock=timeline.wall_ms,
    )
    physical_grill = FakeGrillPlatform(outputs=tuple(settings["platform"]["outputs"]))
    physical_grill.dc_fan = False
    grill = DeliveredGrillPlatform(physical_grill, journal=journal, readback_authoritative=True)
    grill.fan_off()
    grill.auger_off()
    repository = LearningTrajectoryRepository()
    persistence = ModelPersistenceWorker(
        ControllerModelStore(
            reader=store.read_generic_key,
            writer=store.write_generic_key,
            conditional_writer=store.save_model_checkpoint,
        ),
        _TEST_LOGGER,
        trajectory_repository=repository,
    )
    trajectory = LearningTrajectoryRuntime(
        journal=journal,
        persistence=persistence,
        segment_id_factory=lambda: "smoke-hold-production-segment",
        trajectory_session_id_factory=lambda: "smoke-hold-production-trajectory",
    )
    smoke_probes = _TransitioningProbes(
        FakeProbes().script([110.0]),
        store,
        timeline,
        transition_on_read=ceil(3.0 * float(DEFAULT_MPC_CONFIG["theta"]) / _FRAME_SECONDS) + 3,
        target_mode="Hold",
        target_setpoint=150.0,
    )
    ctx, _, _ = make_ctx(settings, control, base_pellet_db(), smoke_probes, grill=grill, store=store)
    ctx.trajectory_repository = repository
    ctx.model_persistence = persistence
    ctx.learning_trajectory = trajectory
    monkeypatch.setattr(ControlMode, "_trajectory_clock_pair", staticmethod(timeline.pair))

    build_requests: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def forbidden_smoke_build(*args, **kwargs):
        build_requests.append((args, kwargs))
        return None, "Inactive"

    monkeypatch.setattr(runner_module, "build_runner", forbidden_smoke_build)
    run_work_cycle("Smoke", ctx)

    assert build_requests == []
    assert trajectory.trace_session_id is None
    assert read_control_trace_cook(control["cook_id"]) == []

    hold_control = store.read_control()
    hold_control["mode"] = "Hold"
    hold_control["primary_setpoint"] = 150.0
    hold_control["updated"] = False
    store.write_control_snapshot(hold_control, origin="smoke-hold-e2e-handoff")
    hold_probes = _TransitioningProbes(
        FakeProbes().script([110.0]),
        store,
        timeline,
        transition_on_read=4,
        target_mode="Stop",
        target_setpoint=0.0,
    )
    ctx.devices.probe_complex = hold_probes

    config = dict(DEFAULT_MPC_CONFIG)
    core = Controller(
        config,
        "C",
        dict(_CYCLE),
        activation_persistence=persistence,
        trajectory_repository=repository,
    )
    observed = _ObservedSyncRunner(core, persistence)
    monkeypatch.setattr(runner_module, "build_runner", lambda *args, **kwargs: (observed, "Active"))
    real_recorder = ControlTraceRecorder
    monkeypatch.setattr(
        hold_module,
        "ControlTraceRecorder",
        lambda warning=None: real_recorder(
            monotonic_clock=lambda: timeline.monotonic_ms,
            wall_clock=timeline.wall_ms,
            warning=warning,
        ),
    )

    try:
        run_work_cycle("Hold", ctx)
    finally:
        observed.stop()
        assert trajectory.close(), trajectory.status()

    assert observed.preparation_error is None
    assert observed.prepared_queued
    assert observed.candidate_digest is not None
    assert observed.events.index("seed") < observed.events.index("candidate-prepared") < observed.events.index("solve")
    assert observed.events.index("solve") < observed.events.index("output")
    seed = observed.seeds[0]
    before_first_solve = observed.pre_solve_states[0]
    assert seed.status == "exact"
    assert seed.pre_roll_frame_count == seed.required_frame_count
    assert before_first_solve.delay_states == seed.delay_states
    assert before_first_solve.measured_temperature_c == pytest.approx(seed.chamber_temperature_c)
    assert before_first_solve.disturbance == pytest.approx(0.0)
    assert observed.outputs
    assert all(owner == observed.incumbent_digest for _, owner, _, _ in observed.outputs)
    assert all(owner != observed.candidate_digest for _, owner, _, _ in observed.outputs)
    assert all(authorized for _, _, authorized, _ in observed.outputs)
    assert all(phase == "prepared" for _, _, _, phase in observed.outputs)

    trace_session_id = trajectory.trace_session_id
    assert trace_session_id is not None
    records = read_control_trace_session(trace_session_id)
    kinds = [record.event_kind for record in records]
    assert kinds[0] is TraceEventKind.SESSION
    assert TraceEventKind.ESTIMATOR_SEED in kinds
    assert TraceEventKind.CONTROL_UPDATE in kinds
    assert TraceEventKind.APPLIED_OUTPUT in kinds
    assert TraceEventKind.TRAJECTORY_SEGMENT in kinds
    assert kinds.index(TraceEventKind.ESTIMATOR_SEED) < kinds.index(TraceEventKind.CONTROL_UPDATE)
    assert all(record.cook_id == control["cook_id"] for record in records)
    segment_records = [record for record in records if record.event_kind is TraceEventKind.TRAJECTORY_SEGMENT]
    assert len(segment_records) == 1
    segment_payload = segment_records[0].payload
    assert segment_payload.segment_id == "smoke-hold-production-segment"
    assert segment_payload.trajectory_session_id == trajectory.trajectory_session_id
    assert segment_payload.pre_roll_frame_count >= seed.pre_roll_frame_count
    assert segment_payload.scored_hold_frame_count == 0
    assert segment_payload.terminal_break_reason is None


def _mode_entered(kind: str, *, at_ms: int, cook_id: str, trajectory_id: str) -> ModeEntered:
    identity = _digest(f"{cook_id}:compatible-physics")
    return ModeEntered(
        effective_mode=kind,
        persisted_mode=kind,
        monotonic_ms=at_ms,
        wall_ms=_WALL_OFFSET_MS + at_ms,
        cook_id=cook_id,
        trajectory_session_id=trajectory_id,
        trace_session_id="",
        recipe_step_id=None,
        units="C",
        settings_revision=1,
        collection_provenance={"origin": "passive-online"},
        configuration_provenance={"scenario": cook_id},
        cadence_digest=_digest("twenty-second-cadence"),
        model_structure_digest=identity,
        held_physics_digest=identity,
        delay_input_mapping_digest=_digest("normalized-combustion-load"),
        actuation_mapping_digest=_digest("auger-u-max-0.9"),
        scored_fan_regime_digest=_digest("fan-fixed-one"),
        ambient_semantics_digest=_digest("configured-ambient"),
        source_trace_digest=_digest(f"{cook_id}:source"),
        source_schema_version=1,
        source_row_digest=_digest(f"{cook_id}:row"),
        build_provenance={"server": "e2e", "build": "e2e"},
        auger_duty_ceiling=_U_MAX,
    )


def _sample(at_ms: int, temperature_c: float) -> ThermalSample:
    return ThermalSample(
        monotonic_ms=at_ms,
        wall_ms=_WALL_OFFSET_MS + at_ms,
        chamber_temperature=temperature_c,
        units="C",
        probe_valid=True,
        probe_source="deterministic-grey-plant",
        ambient_temperature=float(_MAK_SUPPORT_PARAMETERS["T_amb"]),
        ambient_source="configured",
        ambient_uncertainty=0.0,
        settings_revision=1,
        recipe_step_id=None,
    )


def _observation(row: dict[str, Any], index: int) -> FrameObservation:
    start_s = int(row["frame_start_ms"]) / 1_000
    end_s = int(row["frame_end_ms"]) / 1_000
    load = float(row["realized_combustion_load"])
    duty = load * _U_MAX
    return FrameObservation(
        frame_start_s=start_s,
        frame_end_s=end_s,
        temp_c=float(row["temp_c"]),
        setpoint_c=_SETPOINT_C,
        ambient_c=float(row["ambient_c"]),
        requested_q=load,
        realized_q=load,
        baseline_q=load,
        allocated_q=load,
        requested_auger_duty=duty,
        scheduled_on_s=duty * _FRAME_SECONDS,
        delivered_on_s=float(row["delivered_on_seconds"]),
        realized_auger_duty=duty,
        requested_fan_duty=None,
        actual_fan_duty=1.0,
        allocator_revision=2,
        allocation_clamp_reasons=(AllocationClampReason.NONE,),
        result_revision=index + 1,
        output_source=OutputSource.CONTROLLER.value,
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=True,
        role_generation=0,
        observation_sequence=int(row["observation_sequence"]),
        probe_source="deterministic-grey-plant",
        ambient_source=AmbientSource.CONFIGURED,
        ambient_uncertainty=AmbientUncertainty.UNMEASURED,
    )


def _record_fit_segment(database_path, *, warm: bool):
    cook_id = "warm-hold-fit" if warm else "cold-hold-fit"
    segment_id = f"{cook_id}-segment"
    trajectory_id = f"{cook_id}-trajectory"
    repository = LearningTrajectoryRepository(database_path=str(database_path))
    persistence = ModelPersistenceWorker(
        ControllerModelStore(),
        _TEST_LOGGER,
        trajectory_repository=repository,
    )
    timeline = _FixedTimeline(_CORPUS_START_MS)
    journal = ActuationDeliveryJournal(
        monotonic_clock=lambda: timeline.monotonic_ms,
        wall_clock=timeline.wall_ms,
    )
    grill = DeliveredGrillPlatform(
        FakeGrillPlatform(dc_fan=True),
        journal=journal,
        readback_authoritative=True,
    )
    runtime = LearningTrajectoryRuntime(
        journal=journal,
        persistence=persistence,
        segment_id_factory=lambda: segment_id,
        trajectory_session_id_factory=lambda: trajectory_id,
    )
    pre_roll_count = 21
    hold_start_ms = _CORPUS_START_MS + pre_roll_count * _FRAME_MS
    if warm:
        runtime.mode_entered(
            _mode_entered("Smoke", at_ms=_CORPUS_START_MS, cook_id=cook_id, trajectory_id=trajectory_id)
        )
        grill.fan_on()
        grill.auger_off()
        for index in range(pre_roll_count):
            start_ms = _CORPUS_START_MS + index * _FRAME_MS
            timeline.monotonic_ms = start_ms
            grill.auger_on()
            timeline.monotonic_ms = start_ms + round(0.2 * _U_MAX * _FRAME_MS)
            grill.auger_off()
            timeline.monotonic_ms = start_ms + _FRAME_MS
            runtime.observe_temperature(_sample(timeline.monotonic_ms, 20.0))
        runtime.mode_exited(
            ModeExited(
                effective_mode="Smoke",
                next_effective_mode="Hold",
                monotonic_ms=hold_start_ms,
                wall_ms=_WALL_OFFSET_MS + hold_start_ms,
            )
        )
    timeline.monotonic_ms = hold_start_ms
    grill.fan_on()
    grill.auger_off()
    runtime.mode_entered(_mode_entered("Hold", at_ms=hold_start_ms, cook_id=cook_id, trajectory_id=trajectory_id))
    runtime.observe_temperature(_sample(hold_start_ms, 20.0))

    recorder = ControlTraceRecorder(
        monotonic_clock=lambda: timeline.monotonic_ms,
        wall_clock=timeline.wall_ms,
    )
    trace = ControlTraceSession(recorder, warning=_TEST_LOGGER.warning)
    identity = trace.ensure_open(
        _trace_context({"revision": 0}, dict(DEFAULT_MPC_CONFIG), cook_id),
        timestamp_ms=hold_start_ms,
    )
    assert identity is not None
    assert runtime.bind_trace_session(
        identity.session_id,
        cook_id,
        trace.trajectory_segment_publisher(identity),
    )
    assert runtime.barrier(timeout=5.0)
    assert runtime.status().pre_roll_count == (21 if warm else 0)

    rows = _mak_grey_corpus_rows()
    for index, row in enumerate(rows):
        timeline.monotonic_ms = int(row["frame_end_ms"])
        runtime.observe_temperature(_sample(timeline.monotonic_ms, float(row["temp_c"])))
        runtime.observe_hold_frame(_observation(row, index))
        assert runtime.barrier(timeout=5.0)
    end_ms = int(rows[-1]["frame_end_ms"])
    runtime.mode_exited(
        ModeExited(
            effective_mode="Hold",
            next_effective_mode="Stop",
            monotonic_ms=end_ms,
            wall_ms=_WALL_OFFSET_MS + end_ms,
            reason=TrajectoryBreakReason.STOP,
        )
    )
    assert runtime.close()
    trace.close()
    segment = repository.read_segment(segment_id)
    assert segment is not None
    return repository, segment


def _fit(repository: LearningTrajectoryRepository, segment) -> GreyFitSuccess:
    snapshot = repository.snapshot_fit_corpus(segment.fit_partition_digest)
    request = FitRequest(
        request_id=f"fit-{segment.segment_id}",
        origin=CandidateOrigin.PASSIVE_ONLINE,
        fit_corpus=snapshot.identity,
        configuration_digest=_digest("stable-fit-configuration"),
        parent_incumbent_digest=_digest("stable-fit-incumbent"),
        parent_incumbent_generation=0,
        candidate_generation=1,
    )
    truth = _MAK_SUPPORT_PARAMETERS
    initial = GreyBoxMPCConfig(
        C_c=float(truth["C_c"]) * 1.15,
        K_Q=float(truth["K_Q"]) * 0.85,
        T_amb=float(truth["T_amb"]),
        h_amb=float(truth["h_amb"]),
        theta=float(truth["theta"]) * 1.10,
        sigma=float(truth["sigma"]),
    )
    result = fit_segmented_grey(segmented_corpus_fit_job(snapshot, request, initial))
    assert isinstance(result, GreyFitSuccess)
    return result


def test_cold_and_smoke_started_hold_fit_stable_parameters_from_one_physical_trajectory(
    ds,
    tmp_path,
) -> None:
    warm_repository, warm_segment = _record_fit_segment(tmp_path / "warm.db", warm=True)
    cold_repository, cold_segment = _record_fit_segment(tmp_path / "cold.db", warm=False)

    assert [frame.chamber_temperature_c for frame in warm_segment.scored_hold_frames] == [
        frame.chamber_temperature_c for frame in cold_segment.scored_hold_frames
    ]
    assert [frame.normalized_combustion_load for frame in warm_segment.scored_hold_frames] == [
        frame.normalized_combustion_load for frame in cold_segment.scored_hold_frames
    ]
    assert warm_segment.pre_roll_frames
    assert cold_segment.pre_roll_frames == ()
    assert [frame.normalized_combustion_load for frame in warm_segment.pre_roll_frames] == pytest.approx(
        [0.2] * len(warm_segment.pre_roll_frames)
    )

    warm = _fit(warm_repository, warm_segment)
    cold = _fit(cold_repository, cold_segment)
    truth = _MAK_SUPPORT_PARAMETERS
    for name in ("C_c", "K_Q", "theta"):
        warm_value = float(getattr(warm.config, name))
        cold_value = float(getattr(cold.config, name))
        assert warm_value == pytest.approx(float(truth[name]), rel=0.05)
        assert cold_value == pytest.approx(float(truth[name]), rel=0.05)
        assert warm_value == pytest.approx(cold_value, rel=0.03)
