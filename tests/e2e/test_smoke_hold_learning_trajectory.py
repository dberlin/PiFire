"""Default-running production proof for Smoke-to-Hold MPC trajectory seeding."""

from __future__ import annotations

import json
import logging
import threading
import time
import zipfile
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from itertools import count, pairwise
from math import ceil
from pathlib import Path
from typing import Any, cast

import pytest

import controller.runtime.model_fitting as model_fitting_module
import controller.runtime.modes.hold as hold_module
import controller.runtime.runner as runner_module
from common.common import ErrorKind
from common.control_delta import control_delta
from common.control_trace import (
    AllocationClampReason,
    AllocationPayload,
    AmbientSource,
    AmbientUncertainty,
    ControllerType,
    FramedPulseFramePayload,
    MpcFailureState,
    MpcUpdatePayload,
    PidSpUpdatePayload,
    TraceEventKind,
)
from common.controller_model_state import ControllerModelStore
from common.learning_trajectory import TrajectoryBreakReason
from common.model_evidence import (
    ConfidenceDecisionEvidence,
    EvidenceKind,
    ModelEvidenceRecord,
    PidSpFitDecisionEvidence,
)
from common.persistence import runtime as runtime_persistence
from common.persistence.control_trace import (
    read_control_trace_cook,
    read_control_trace_session,
)
from common.persistence.learning_trajectory import LearningTrajectoryRepository
from common.persistence.model_evidence import read_model_activation, read_model_evidence
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
    assert len(segment_records) == 2
    open_payload, finalized_payload = (record.payload for record in segment_records)
    assert open_payload.segment_id == finalized_payload.segment_id == "smoke-hold-production-segment"
    assert open_payload.trajectory_session_id == finalized_payload.trajectory_session_id
    assert finalized_payload.trajectory_session_id == trajectory.trajectory_session_id
    assert open_payload.pre_roll_frame_count >= seed.pre_roll_frame_count
    assert finalized_payload.pre_roll_frame_count == open_payload.pre_roll_frame_count
    assert open_payload.scored_hold_frame_count == finalized_payload.scored_hold_frame_count == 0
    assert open_payload.state == "open"
    assert open_payload.terminal_break_reason is None
    assert finalized_payload.state == "finalized"
    assert finalized_payload.terminal_break_reason == TrajectoryBreakReason.RECORDER_GAP.value


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


@pytest.mark.slow
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


_REAL_COOK_FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "real_cook_learning"
_REAL_COOK_MANIFEST_SHA256 = "93be80c440e31bb34dfbc9dd13f8f04842e339e2dff7d796c8f17b6dd1164ae6"


@dataclass(frozen=True, slots=True)
class _RealCookHoldStream:
    controller: str
    cook_id: str
    temperatures: tuple[tuple[int, float, float], ...]
    hold_start_index: int
    controller_config: dict[str, Any]
    control_period_seconds: float
    pulse_slot_seconds: float
    pulse_frame_seconds: float
    fan_pwm_capable: bool


def _load_real_cook_hold_stream(campaign_id: str, cook_name: str) -> _RealCookHoldStream:
    manifest_bytes = (_REAL_COOK_FIXTURE_ROOT / "manifest.json").read_bytes()
    assert sha256(manifest_bytes).hexdigest() == _REAL_COOK_MANIFEST_SHA256
    manifest = json.loads(manifest_bytes)
    campaign = next(item for item in manifest["campaigns"] if item["id"] == campaign_id)
    cook = next(item for item in campaign["cooks"] if Path(item["path"]).name == cook_name)
    archive_bytes = (_REAL_COOK_FIXTURE_ROOT / cook["path"]).read_bytes()
    assert sha256(archive_bytes).hexdigest() == cook["sanitized_sha256"]

    with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
        metadata = json.loads(archive.read("metadata.json"))
        sessions = json.loads(archive.read("sessions.json"))
        transitions = json.loads(archive.read("transitions.json"))
        chamber_samples = json.loads(archive.read("chamber_samples.json"))

    assert metadata["controller"] == campaign["controller"]
    assert metadata["cook_start_ms"] == cook["cook_start_ms"]
    assert metadata["cook_end_ms"] == cook["cook_end_ms"]
    assert metadata["chamber_sample_count"] == len(chamber_samples)
    assert transitions
    first_transition = min(transitions, key=lambda item: item["timestamp_ms"])
    session = next(item for item in sessions if item["session_id"] == first_transition["session_id"])
    payload = session["payload"]
    assert payload["controller"] == campaign["controller"]
    assert payload["temperature_unit"] == metadata["units"] == "F"
    assert first_transition["timestamp_ms"] >= metadata["cook_start_ms"]

    temperatures = tuple(
        (
            int(sample["timestamp_ms"]),
            float(sample["chamber_temperature_f"]),
            float(sample["setpoint_f"]),
        )
        for sample in chamber_samples
        if metadata["cook_start_ms"] <= int(sample["timestamp_ms"]) <= metadata["cook_end_ms"]
    )
    assert temperatures
    assert all(left[0] < right[0] for left, right in pairwise(temperatures))
    assert temperatures[0][0] == metadata["cook_start_ms"]
    assert temperatures[-1][0] == metadata["cook_end_ms"]
    hold_start_index = next(
        index for index, sample in enumerate(temperatures) if sample[0] >= first_transition["timestamp_ms"]
    )
    assert temperatures[hold_start_index][2] == pytest.approx(float(first_transition["setpoint_c"]) * 9.0 / 5.0 + 32.0)

    raw_config = payload["controller_config"]
    assert isinstance(raw_config, list)
    controller_config = {str(item["key"]): item["value"] for item in raw_config}
    return _RealCookHoldStream(
        controller=str(campaign["controller"]),
        cook_id=str(metadata["cook_id"]),
        temperatures=temperatures,
        hold_start_index=hold_start_index,
        controller_config=controller_config,
        control_period_seconds=float(payload["control_period_seconds"]),
        pulse_slot_seconds=float(payload["pulse_slot_seconds"]),
        pulse_frame_seconds=float(payload["pulse_frame_seconds"]),
        fan_pwm_capable=bool(payload["fan_pwm_capable"]),
    )


class _DeterministicRunnerPeriodGate:
    def __init__(self, *, start_s: float, period_s: float) -> None:
        self._condition = threading.Condition()
        self._period_s = period_s
        self._next_release_s = start_s + period_s
        self._wait_count = 0
        self._release_count = 0
        self._closed = False

    def __call__(self, _period_s: float) -> None:
        with self._condition:
            self._wait_count += 1
            wait_number = self._wait_count
            self._condition.notify_all()
            self._condition.wait_for(lambda: self._closed or self._release_count >= wait_number)

    def advance_to(self, now_s: float) -> None:
        if now_s < self._next_release_s:
            return
        while self._next_release_s <= now_s:
            self._next_release_s += self._period_s
        with self._condition:
            target = self._release_count + 1
            assert self._condition.wait_for(
                lambda: self._closed or self._wait_count >= target,
                timeout=5.0,
            )
            if self._closed:
                return
            self._release_count = target
            self._condition.notify_all()
            assert self._condition.wait_for(
                lambda: self._closed or self._wait_count >= target + 1,
                timeout=5.0,
            )

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


class _RealCookClock:
    def __init__(self, stream: _RealCookHoldStream, *, start_index: int) -> None:
        self._stream = stream
        start_ms = stream.temperatures[start_index][0]
        end_ms = stream.temperatures[-1][0]
        slot_ms = round(stream.pulse_slot_seconds * 1_000)
        source_timestamps = {timestamp_ms for timestamp_ms, _, _ in stream.temperatures[start_index:]}
        source_timestamps.update(range(start_ms, end_ms + 1, slot_ms))
        source_timestamps.add(end_ms)
        self._ticks = tuple(sorted(source_timestamps))
        self._tick_index = 0
        self._sample_index = start_index
        self._period_gate: _DeterministicRunnerPeriodGate | None = None
        self._pause_next_sleep = False

    @property
    def index(self) -> int:
        return self._sample_index

    @property
    def timestamp_ms(self) -> int:
        return self._ticks[self._tick_index]

    def now(self) -> float:
        return self.timestamp_ms / 1_000

    def bind_period_gate(self, gate: _DeterministicRunnerPeriodGate) -> None:
        self._period_gate = gate

    def pause_next_sleep(self) -> None:
        self._pause_next_sleep = True

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0.0
        if self._pause_next_sleep:
            self._pause_next_sleep = False
            return
        if self._tick_index + 1 < len(self._ticks):
            self._tick_index += 1
        while (
            self._sample_index + 1 < len(self._stream.temperatures)
            and self._stream.temperatures[self._sample_index + 1][0] <= self.timestamp_ms
        ):
            self._sample_index += 1
        if self._period_gate is not None:
            self._period_gate.advance_to(self.now())

    def pair_ms(self) -> tuple[int, int]:
        return self.timestamp_ms, self.timestamp_ms


class _RealCookProbes:
    def __init__(
        self,
        stream: _RealCookHoldStream,
        clock: _RealCookClock,
        store: SqliteStore,
        *,
        terminal_index: int,
        terminal_mode: str,
    ) -> None:
        self._stream = stream
        self._clock = clock
        self._store = store
        self._probes = FakeProbes()
        self._last_setpoint: float | None = None
        self._terminal_index = terminal_index
        self._terminal_mode = terminal_mode
        self._terminal_requested = False
        self.visited_timestamps: list[int] = []

    def read_probes(self, *, excitation=None, now=None):
        timestamp_ms, temperature_f, setpoint_f = self._stream.temperatures[self._clock.index]
        self.visited_timestamps.append(timestamp_ms)
        if setpoint_f > 0.0 and setpoint_f != self._last_setpoint:
            self._last_setpoint = setpoint_f
            self._store.enqueue_control_delta(
                control_delta(
                    set_values={
                        "primary_setpoint": setpoint_f,
                        "updated": False,
                    }
                ),
                origin="real-cook-hold-setpoint",
            )
        if self._clock.index == self._terminal_index and not self._terminal_requested:
            self._terminal_requested = True
            self._clock.pause_next_sleep()
            self._store.enqueue_control_delta(
                control_delta(
                    set_values={
                        "mode": self._terminal_mode,
                        "primary_setpoint": (setpoint_f if self._terminal_mode == "Hold" else 0.0),
                        "updated": True,
                    }
                ),
                origin="real-cook-hold-terminal",
            )
        return {
            "primary": {"Grill": temperature_f},
            "food": {},
            "aux": {},
            "tr": {},
        }

    def arm_stop(self) -> None:
        self._terminal_index = len(self._stream.temperatures) - 1
        self._terminal_mode = "Stop"
        self._terminal_requested = False

    def __getattr__(self, name: str):
        return getattr(self._probes, name)


def _wait_for_fit_worker(runner) -> None:
    deadline = time.monotonic() + 30.0
    while True:
        worker = getattr(runner, "_corpus_fit_thread", None)
        if worker is None:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            break
        worker.join(timeout=min(remaining, 0.1))
        if getattr(runner, "_corpus_fit_thread", None) is None:
            return
    assert getattr(runner, "_corpus_fit_thread", None) is None


def _assert_real_cook_hold_smoke(
    ds,
    monkeypatch,
    caplog,
    *,
    campaign_id: str,
    cook_name: str,
    expected_controller: ControllerType,
) -> None:
    stream = _load_real_cook_hold_stream(campaign_id, cook_name)
    assert stream.controller == expected_controller.value

    settings = base_settings()
    settings["globals"]["units"] = "F"
    settings["controller"]["selected"] = stream.controller
    configured = dict(settings["controller"]["config"][stream.controller])
    configured.update(stream.controller_config)
    configured["enable_identification"] = True
    settings["controller"]["config"][stream.controller] = configured
    settings["platform"]["dc_fan"] = stream.fan_pwm_capable
    settings["safety"]["maxtemp"] = 600.0

    warm_with_smoke = expected_controller is ControllerType.MPC
    if expected_controller is ControllerType.MPC:
        required_pre_roll_frames = ceil(3.0 * float(configured["theta"]) / stream.pulse_frame_seconds) + 2
        smoke_start_ms = stream.temperatures[stream.hold_start_index][0] - round(
            required_pre_roll_frames * stream.pulse_frame_seconds * 1_000
        )
        stream_start_index = next(
            index for index, sample in enumerate(stream.temperatures) if sample[0] >= smoke_start_ms
        )
    else:
        stream_start_index = stream.hold_start_index
    first_setpoint = next(
        setpoint for _, _, setpoint in stream.temperatures[stream.hold_start_index :] if setpoint > 0.0
    )
    control = base_control("Smoke" if warm_with_smoke else "Hold")
    control["cook_id"] = stream.cook_id
    control["primary_setpoint"] = 0.0 if warm_with_smoke else first_setpoint
    control["safety"]["startuptemp"] = 0
    control["safety"]["afterstarttemp"] = 0

    store = SqliteStore()
    real_grey_fit_worker_start = model_fitting_module.GreyFitWorker.start
    captured_grey_fit_workers: list[Any] = []

    def capture_grey_fit_worker_start(worker):
        if all(captured is not worker for captured in captured_grey_fit_workers):
            captured_grey_fit_workers.append(worker)
        return real_grey_fit_worker_start(worker)

    monkeypatch.setattr(
        model_fitting_module.GreyFitWorker,
        "start",
        capture_grey_fit_worker_start,
    )
    _seed_sqlite_store(store, settings, control)
    clock = _RealCookClock(stream, start_index=stream_start_index)
    journal = ActuationDeliveryJournal(
        monotonic_clock=lambda: clock.timestamp_ms,
        wall_clock=lambda: clock.timestamp_ms,
    )
    physical_grill = FakeGrillPlatform(
        dc_fan=stream.fan_pwm_capable,
        outputs=tuple(settings["platform"]["outputs"]),
    )
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
    segment_ids = count(1)
    trajectory = LearningTrajectoryRuntime(
        journal=journal,
        persistence=persistence,
        segment_id_factory=lambda: f"{stream.cook_id}-segment-{next(segment_ids)}",
        trajectory_session_id_factory=lambda: f"{stream.cook_id}-trajectory",
    )
    observed_trajectory_frames: list[FrameObservation] = []
    real_observe_hold_frame = LearningTrajectoryRuntime.observe_hold_frame

    def capture_observe_hold_frame(runtime, observation, *, replay_only=False):
        observed_trajectory_frames.append(observation)
        return real_observe_hold_frame(runtime, observation, replay_only=replay_only)

    monkeypatch.setattr(
        LearningTrajectoryRuntime,
        "observe_hold_frame",
        capture_observe_hold_frame,
    )
    probes = _RealCookProbes(
        stream,
        clock,
        store,
        terminal_index=(stream.hold_start_index if warm_with_smoke else len(stream.temperatures) - 1),
        terminal_mode=("Hold" if warm_with_smoke else "Stop"),
    )
    ctx, _, _ = make_ctx(settings, control, base_pellet_db(), probes, grill=grill, store=store)
    ctx.clock = clock
    ctx.trajectory_repository = repository
    ctx.model_persistence = persistence
    ctx.learning_trajectory = trajectory

    original_build_runner = runner_module.build_runner
    captured_runners: list[tuple[Any, str]] = []

    def capture_production_runner(*args, **kwargs):
        built = original_build_runner(*args, **kwargs)
        captured_runners.append(built)
        runner, _status = built
        if runner is not None and runner.runs_async():
            period_gate = _DeterministicRunnerPeriodGate(
                start_s=clock.now(),
                period_s=float(runner.control_period()),
            )
            runner._wait_for_period = period_gate  # noqa: SLF001 - deterministic executor barrier
            clock.bind_period_gate(period_gate)
        return built

    monkeypatch.setattr(runner_module, "build_runner", capture_production_runner)
    monkeypatch.setattr(ControlMode, "_trajectory_clock_pair", staticmethod(clock.pair_ms))
    real_recorder = hold_module.ControlTraceRecorder
    monkeypatch.setattr(
        hold_module,
        "ControlTraceRecorder",
        lambda warning=None: real_recorder(
            monotonic_clock=lambda: clock.timestamp_ms,
            wall_clock=lambda: clock.timestamp_ms,
            warning=warning,
        ),
    )

    errors_before = tuple(runtime_persistence.read_errors(ErrorKind.CONTROL))
    trace_record_count_before = len(read_control_trace_cook(stream.cook_id))
    evidence_count_before = len(read_model_evidence())
    runner = None
    trajectory_closed = False
    report_before_restart = None
    grey_owner = getattr(ctx, "grey_learning_process", None)
    persistence_drained = False
    grey_owner_drained = False
    grey_fit_worker = None
    grey_fit_worker_drained = False
    try:
        with caplog.at_level(logging.WARNING):
            if warm_with_smoke:
                run_work_cycle("Smoke", ctx)
                hold_control = store.read_control()
                assert hold_control["mode"] == "Hold"
                hold_control["primary_setpoint"] = first_setpoint
                hold_control["updated"] = False
                store.write_control_snapshot(hold_control, origin="real-cook-hold-handoff")
                probes.arm_stop()
            run_work_cycle("Hold", ctx)

        assert len(captured_runners) == 1
        runner, runner_status = captured_runners[0]
        assert runner_status == "Active"
        assert runner.controller_type() is expected_controller
        assert runner.actuation_mode().value == "framed_pulse"
        if runner.runs_async():
            assert runner.control_period() == pytest.approx(stream.control_period_seconds)
        else:
            assert runner.control_period() is None
        assert stream.pulse_slot_seconds == pytest.approx(float(grill.auger_timing().pulse_s))
        assert stream.pulse_frame_seconds == pytest.approx(float(grill.auger_timing().frame_s))

        visited_start = stream_start_index
        assert tuple(dict.fromkeys(probes.visited_timestamps)) == tuple(
            timestamp_ms for timestamp_ms, _, _ in stream.temperatures[visited_start:]
        )
        assert store.read_control()["mode"] == "Stop"
        assert store.read_control()["updated"] is True
        assert runtime_persistence.read_errors(ErrorKind.CONTROL) == list(errors_before)

        drained = runner.drain_observation_outcomes()
        assert drained.envelopes == ()
        assert drained.terminal_drops == ()
        assert drained.dropped_count == 0
        assert drained.dropped_sequences == ()
        _wait_for_fit_worker(runner)
        if expected_controller is ControllerType.MPC:
            durable_after_stop = read_control_trace_cook(stream.cook_id)[trace_record_count_before:]
            fit_lifecycle_after_stop = [
                record.payload for record in durable_after_stop if record.event_kind is TraceEventKind.FIT_LIFECYCLE
            ]
            queued_stop_fit = next(
                (payload for payload in reversed(fit_lifecycle_after_stop) if payload.status == "queued"),
                None,
            )
            stop_fit_request_id = None if queued_stop_fit is None else queued_stop_fit.request_id
            stop_fit_statuses = [
                payload.status for payload in fit_lifecycle_after_stop if payload.request_id == stop_fit_request_id
            ]
            learning_core = getattr(runner, "_learning_core", None)
            grey_runtime = getattr(learning_core, "_grey_learning_runtime", None)
            if stop_fit_request_id is None:
                failure = getattr(grey_runtime, "_corpus_fit_failure", None)
                assert failure is not None and failure[0] == "corpus-snapshot-failed"
            else:
                assert stop_fit_statuses[-1] in {"succeeded", "failed", "stale"}, (
                    stop_fit_request_id,
                    stop_fit_statuses,
                )
                assert sum(status in {"succeeded", "failed", "stale"} for status in stop_fit_statuses) == 1
        worker = getattr(runner, "_thread", None)
        assert worker is None or not worker.is_alive()
        for attribute in (
            "_pending_observations",
            "_accepted_observations",
            "_inflight_observations",
            "_pending_dispatches",
            "_corpus_fit_plans",
        ):
            pending = getattr(runner, attribute, ())
            assert not pending, (attribute, pending)
        outcome_buffer = getattr(runner, "_observation_buffer", None)
        if outcome_buffer is not None:
            assert not outcome_buffer._outcomes  # noqa: SLF001 - terminal ownership proof
            assert not outcome_buffer._terminal_drops  # noqa: SLF001 - terminal ownership proof
    finally:
        grey_owner = getattr(ctx, "grey_learning_process", grey_owner)
        if grey_owner is not None and grey_owner.learning is not None:
            grey_fit_worker = grey_owner.learning.worker
        if grey_owner is not None:
            grey_owner.close()
        runners_to_stop = [built_runner for built_runner, _status in captured_runners if built_runner is not None]
        if runner is not None and all(owned is not runner for owned in runners_to_stop):
            runners_to_stop.append(runner)
        for owned_runner in runners_to_stop:
            owned_runner.stop()
        grey_owner_drained = grey_owner is None or not grey_owner.has_pending_fit()
        grey_fit_worker_drained = all(
            not worker.alive and not worker.busy and worker.process_count == 0 for worker in captured_grey_fit_workers
        )
        persistence_drained = persistence.barrier(timeout=5.0)
        trajectory_closed = trajectory.close()

    assert trajectory_closed
    assert grey_owner_drained
    expected_grey_fit_workers = 1 if expected_controller is ControllerType.MPC else 0
    assert len(captured_grey_fit_workers) == expected_grey_fit_workers
    if grey_fit_worker is None:
        assert not captured_grey_fit_workers
    else:
        assert captured_grey_fit_workers == [grey_fit_worker]
    assert grey_fit_worker_drained
    assert persistence_drained
    assert not persistence.failed

    report_before_restart = repository.corpus_report()
    assert report_before_restart.open_segment_count == 0
    records = read_control_trace_cook(stream.cook_id)[trace_record_count_before:]
    assert records
    trace_session_ids = {record.session_id for record in records}
    assert len(trace_session_ids) == 1
    if trajectory.trace_session_id is not None:
        assert trace_session_ids == {trajectory.trace_session_id}
    observation_payloads = [
        record.payload for record in records if record.event_kind is TraceEventKind.MODEL_OBSERVATION
    ]
    assert observation_payloads
    assert all(payload.eligible or payload.rejection_reasons for payload in observation_payloads)
    assert report_before_restart.quarantined_segment_count == 0
    assert report_before_restart.last_recovery_error is None

    control_updates = [record.payload for record in records if record.event_kind is TraceEventKind.CONTROL_UPDATE]
    assert control_updates
    assert all(
        payload.control_period_seconds == pytest.approx(stream.control_period_seconds) for payload in control_updates
    )
    if expected_controller is ControllerType.MPC:
        mpc_updates = [cast(MpcUpdatePayload, payload) for payload in control_updates]
        successful_solve_indexes = [
            index for index, payload in enumerate(mpc_updates) if payload.failure_state is MpcFailureState.SUCCESS
        ]
        assert successful_solve_indexes
        failed_solve_indexes = [
            index
            for index, payload in enumerate(mpc_updates)
            if payload.failure_state is MpcFailureState.POLICY_EXCEPTION
        ]
        for failed_index in failed_solve_indexes:
            recovery = next(
                (
                    payload
                    for payload in mpc_updates[failed_index + 1 :]
                    if payload.failure_state is MpcFailureState.SUCCESS
                ),
                None,
            )
            assert recovery is not None
        fit_lifecycle = [record.payload for record in records if record.event_kind is TraceEventKind.FIT_LIFECYCLE]
        assert fit_lifecycle
        assert all(payload.origin == "passive-online" for payload in fit_lifecycle)
        fit_statuses_by_request: dict[str, list[str]] = {}
        for payload in fit_lifecycle:
            fit_statuses_by_request.setdefault(payload.request_id, []).append(payload.status)
        stop_fit_request_id = next(
            payload.request_id for payload in reversed(fit_lifecycle) if payload.status == "queued"
        )
        stop_fit_statuses = fit_statuses_by_request[stop_fit_request_id]
        assert stop_fit_statuses[0] == "queued"
        assert sum(status in {"succeeded", "failed", "stale"} for status in stop_fit_statuses) == 1
        assert stop_fit_statuses[-1] in {"succeeded", "failed", "stale"}
    else:
        pid_updates = [cast(PidSpUpdatePayload, payload) for payload in control_updates]
        allocations = [
            cast(AllocationPayload, record.payload)
            for record in records
            if record.event_kind is TraceEventKind.ALLOCATION
        ]
        assert len(allocations) == len(pid_updates)
        updates_by_revision = {payload.result_revision: payload for payload in pid_updates}
        assert {payload.result_revision for payload in allocations} == set(updates_by_revision)
        for allocation in allocations:
            update = updates_by_revision[allocation.result_revision]
            expected_output = min(1.0, max(0.0, update.raw_output))
            expected_clamp = (
                AllocationClampReason.AUGER_MIN
                if update.raw_output < 0.0
                else (AllocationClampReason.AUGER_MAX if update.raw_output > 1.0 else AllocationClampReason.NONE)
            )
            assert update.requested_output == pytest.approx(expected_output)
            assert (
                allocation.normalized_combustion_load,
                allocation.requested_auger_duty,
            ) == pytest.approx((expected_output, expected_output))
            assert allocation.requested_fan_duty is None
            assert allocation.u_max == 1.0
            assert allocation.fan_min_pct == allocation.fan_max_pct == 0.0
            assert allocation.fan_enabled is False
            assert allocation.mpc_has_fan_authority is False
            assert allocation.auger_clamp_reason is expected_clamp
            assert allocation.fan_clamp_reason is AllocationClampReason.NONE
            assert allocation.allocator_revision == 2
        assert all(
            "missing-allocation" not in payload.rejection_reasons
            and "allocation-revision-mismatch" not in payload.rejection_reasons
            and payload.combined_allocation is not None
            and payload.allocator_revision == 2
            for payload in observation_payloads
        )
        assert any(payload.eligible for payload in observation_payloads), {
            reason for payload in observation_payloads for reason in payload.rejection_reasons
        }
        assert trajectory.trace_session_id is not None
        assert any(
            observation.continuous
            and observation.probe_source == "chamber"
            and observation.actual_fan_duty == 1.0
            and observation.combined_allocation is not None
            and observation.frame_end_s - observation.frame_start_s == pytest.approx(stream.pulse_frame_seconds)
            for observation in observed_trajectory_frames
        )
        assert report_before_restart.finalized_segment_count > 0
        assert [record for record in records if record.event_kind is TraceEventKind.TRAJECTORY_SEGMENT]
        pid_fit_evidence = [
            record.payload
            for record in read_model_evidence()[evidence_count_before:]
            if record.kind is EvidenceKind.PID_SP_FIT_DECISION
        ]
        assert len(pid_fit_evidence) == 1
        pid_stop_fit = pid_fit_evidence[0]
        assert isinstance(pid_stop_fit, PidSpFitDecisionEvidence)
        assert pid_stop_fit.origin == "passive-online"
        assert pid_stop_fit.request_bound, (
            pid_stop_fit.outcome,
            pid_stop_fit.reason,
            {kind.value: sum(record.event_kind is kind for record in records) for kind in TraceEventKind},
            {reason for payload in observation_payloads for reason in payload.rejection_reasons},
            trajectory.status(),
        )
        assert pid_stop_fit.outcome in {
            "insufficient",
            "rejected",
            "failed",
            "accepted-next-cook",
            "checkpoint-failure",
        }, (pid_stop_fit.outcome, pid_stop_fit.reason, pid_stop_fit.request_bound)
    assert not [record for record in records if record.event_kind is TraceEventKind.RECORDER_GAP]
    assert any(record.event_kind is TraceEventKind.MODEL_OBSERVATION for record in records)
    terminal_frames = [
        (index, cast(FramedPulseFramePayload, record.payload))
        for index, record in enumerate(records)
        if record.event_kind is TraceEventKind.ACTUATION_FRAME
        and cast(FramedPulseFramePayload, record.payload).frame_end_ms == stream.temperatures[-1][0]
    ]
    assert len(terminal_frames) == 1
    assert terminal_frames[0][1].reset_reason in {"mode_change", "safety"}
    terminal_index, terminal_frame = terminal_frames[0]
    matching_terminal_observations = [
        (index, record.payload)
        for index, record in enumerate(records)
        if record.event_kind is TraceEventKind.MODEL_OBSERVATION
        and record.payload.frame_start_ms == terminal_frame.frame_start_ms
        and record.payload.frame_end_ms == terminal_frame.frame_end_ms
    ]
    assert len(matching_terminal_observations) == 1
    terminal_observation_index, terminal_observation = matching_terminal_observations[0]
    assert terminal_index < terminal_observation_index
    assert terminal_observation.probe_valid
    assert terminal_observation.eligible or terminal_observation.rejection_reasons

    final_outputs = physical_grill.get_output_status()
    assert all(not final_outputs[name] for name in ("auger", "fan", "igniter", "power"))
    assert report_before_restart is not None
    restarted_report = LearningTrajectoryRepository().corpus_report()
    assert restarted_report.corpus_revision == report_before_restart.corpus_revision
    assert restarted_report.open_segment_count == 0
    assert restarted_report.finalized_segment_count == report_before_restart.finalized_segment_count
    assert restarted_report.scored_count == report_before_restart.scored_count


@pytest.mark.slow
def test_real_cook_mpc_chamber_stream_completes_full_hold_and_drains_learning(
    ds,
    monkeypatch,
    caplog,
) -> None:
    _assert_real_cook_hold_smoke(
        ds,
        monkeypatch,
        caplog,
        campaign_id="mpc-aug29",
        cook_name="2026-08-29--1219.pifire",
        expected_controller=ControllerType.MPC,
    )


@pytest.mark.slow
def test_real_cook_pid_sp_august_28_chamber_stream_completes_full_hold_and_drains_learning(
    ds,
    monkeypatch,
    caplog,
) -> None:
    _assert_real_cook_hold_smoke(
        ds,
        monkeypatch,
        caplog,
        campaign_id="pid-sp-aug28",
        cook_name="2026-08-28--1931.pifire",
        expected_controller=ControllerType.PID_SP,
    )
