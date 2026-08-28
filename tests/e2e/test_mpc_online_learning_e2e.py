"""Production-boundary proof for durable passive MPC online learning."""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from threading import Condition
from typing import Any, cast

import pytest

from common import datastore
from common.control_trace import (
    TRACE_SCHEMA_VERSION,
    AllocationClampReason,
    AmbientSource,
    AmbientUncertainty,
    ControllerType,
    ModelEvaluationPayload,
    ModelObservationPayload,
    TraceEventKind,
)
from common.controller_model_state import ControllerModelStore
from common.model_evidence import (
    ActivationLifecycleEvidence,
    CandidateAssessmentEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    FitLifecycleEvidence,
    ForecastOriginEvidence,
    SessionSummaryEvidence,
)
from common.persistence.control_trace import read_control_trace_cook, read_control_trace_session
from common.persistence.model_evidence import read_model_activation, read_model_evidence
from controller.applied_output import AppliedOutput, FrameFeedbackDisposition, OutputSource
from controller.grill_sim import MAKGrillSim
from controller.model_learning.activation import ActivationPhase, PreparedActivationRecord
from controller.model_learning.contracts import (
    ActivationPolicy,
    CandidateOrigin,
    FrameObservation,
)
from controller.model_learning.report import current_learning_report
from controller.mpc import Controller
from controller.mpc_config import DEFAULT_MPC_CONFIG
from controller.mpc_snapshot import migrate_grey_learning_snapshot
from controller.runtime.control_trace_recorder import RETENTION_PERIOD_MS, ControlTraceRecorder
from controller.runtime.control_trace_session import (
    ControlTraceSession,
    TraceModelAuthority,
    TraceSessionContext,
)
from controller.runtime.model_fitting import FITTED_PARAMETERS
from controller.runtime.model_persistence import ModelPersistenceWorker
from controller.runtime.modes.hold_learning import HoldLearningRuntime
from controller.runtime.runner import SyncControllerRunner, ThreadedControllerRunner

_FRAME_SECONDS = 20
_FIT_SAMPLES = 120
_FIRST_EVALUATION_END = 300
_SECOND_EVALUATION_END = 301
_EVALUATION_PUBLICATION_SEQUENCE = 302
_REQUIRED_HORIZONS = {3, 15, 45, 90, 180}
_LOAD_LEVELS = (0.20, 0.55, 0.90)
_LEVEL_DWELL_FRAMES = 8
_U_MAX = 0.90
_SETPOINT_C = 200.0
_CYCLE = {"u_min": 0.1, "u_max": _U_MAX}
_OBSOLETE_V6_SCORES = {"incumbent_innovation_c", "challenger_innovation_c"}
_EXACT_V6_FIXTURE = Path(__file__).with_name("fixtures") / "restored_v6_passive_21_140.json"
_EXACT_V6_ROWS_SHA256 = "f9bf8eaa632a68e73586abfd93ef42c07d4bac91a824f4660fcc1af8cd59b0a6"
_EXACT_FOLLOWING_ROWS_SHA256 = "27bb8ed230195e426a88da623a46f05f406188e4dc5ad7fbf6a042c351eff60a"
_EXACT_V6_ACTIVE_DIGEST = "8749cde88b2906c4b8ea59a3ea6247aedef2cef67fcff1a3d4c91dc3e302d0ba"
_EXACT_V6_CANDIDATE_DIGEST = "597586b395cf0678201a19c9b8b10e4bf56b5c2985b5c8cdd4470cf7611cbb57"
_EXACT_PASSIVE_CONFIGURATION_DIGEST = "fd0ee82e1b9664ff9325f24664020a2570910096d055a1039aa1c9f8da0e09d1"
_PASSIVE_PARAMETERS_REFERENCE = {
    "C_c": 1767.5013593870272,
    "K_Q": 288.2098500448781,
    "T_amb": 20.0,
    "h_amb": 0.5,
    "n_delay": 8,
    "sigma": 1.4e-09,
    "theta": 52.241101540886156,
}
_EXACT_REPLAY_COOK_ID = "mpc-restored-v6-passive-21-140"


def _assert_passive_parameters(actual: Mapping[str, Any]) -> None:
    """Compare fitted thermal parameters within solver reproducibility tolerance.

    Only the three solver-fitted values (FITTED_PARAMETERS in
    controller.runtime.model_fitting: C_c, K_Q, theta) are a converged
    optimum rather than a fixed point: a different acados build reproduces
    them only to about six significant figures (worst observed relative
    deviation 1.2e-6, on theta). The remaining reference values are carried
    through from config unchanged and compare exactly. Exact equality on the
    fitted three would pin the suite to one machine's floating-point result.
    """

    assert actual.keys() == _PASSIVE_PARAMETERS_REFERENCE.keys()
    for key, expected in _PASSIVE_PARAMETERS_REFERENCE.items():
        if key in FITTED_PARAMETERS:
            assert actual[key] == pytest.approx(expected, rel=1e-5), key
        else:
            assert actual[key] == expected, key


_LOGGER = logging.getLogger(__name__)


class _TestLogger:
    def info(self, message: str) -> None:
        _LOGGER.info(message)

    def warning(self, message: str) -> None:
        _LOGGER.warning(message)

    def error(self, message: str) -> None:
        _LOGGER.error(message)


_TEST_LOGGER = _TestLogger()


class _FrameBoundaryGate:
    """Let the real threaded runner execute exactly one iteration per permit."""

    def __init__(self) -> None:
        self._condition = Condition()
        self._arrivals = 0
        self._permits = 0
        self._closed = False

    def __call__(self, _period_s: float) -> None:
        with self._condition:
            self._arrivals += 1
            self._condition.notify_all()
            released = self._condition.wait_for(
                lambda: self._permits > 0 or self._closed,
                timeout=30.0,
            )
            if not released:
                raise TimeoutError("threaded controller did not receive a frame-boundary permit")
            if self._permits:
                self._permits -= 1

    def wait_until_blocked(self) -> None:
        with self._condition:
            if not self._condition.wait_for(lambda: self._arrivals > 0, timeout=30.0):
                raise TimeoutError("threaded controller did not reach its initial boundary")

    def advance(self) -> None:
        with self._condition:
            target = self._arrivals + 1
            self._permits += 1
            self._condition.notify_all()
            if not self._condition.wait_for(
                lambda: self._arrivals >= target or self._closed,
                timeout=30.0,
            ):
                raise TimeoutError("threaded controller did not complete its frame boundary")

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


def _wait_until(
    predicate: Callable[[], Any],
    *,
    timeout_s: float,
    description: str,
) -> Any:
    """Bound asynchronous production workers without using timing sleeps."""

    deadline = time.monotonic() + timeout_s
    condition = Condition()
    with condition:
        while True:
            result = predicate()
            if result:
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                pytest.fail(f"timed out waiting for {description}")
            condition.wait(timeout=min(0.05, remaining))


def _load_for(sequence: int) -> float:
    if sequence >= _FIT_SAMPLES:
        # Evaluation forecasts assume the origin load persists through the
        # horizon; hold it steady so each score measures the models, not a
        # deliberately false future-input assumption.
        return 0.55
    return _LOAD_LEVELS[(sequence // _LEVEL_DWELL_FRAMES) % len(_LOAD_LEVELS)]


def _simulated_frame(plant: MAKGrillSim, sequence: int) -> FrameObservation:
    normalized_load = _load_for(sequence)
    auger_duty = normalized_load * _U_MAX
    for _ in range(_FRAME_SECONDS):
        plant.step(auger_duty, 1.0)
    frame_start_s = sequence * _FRAME_SECONDS
    frame_end_s = frame_start_s + _FRAME_SECONDS
    return FrameObservation(
        frame_start_s=frame_start_s,
        frame_end_s=frame_end_s,
        temp_c=plant.measured(),
        setpoint_c=_SETPOINT_C,
        ambient_c=plant.T_amb,
        requested_q=normalized_load,
        realized_q=normalized_load,
        baseline_q=normalized_load,
        allocated_q=normalized_load,
        requested_auger_duty=auger_duty,
        scheduled_on_s=auger_duty * _FRAME_SECONDS,
        delivered_on_s=auger_duty * _FRAME_SECONDS,
        realized_auger_duty=auger_duty,
        requested_fan_duty=None,
        actual_fan_duty=1.0,
        allocator_revision=2,
        allocation_clamp_reasons=(AllocationClampReason.NONE,),
        result_revision=sequence + 1,
        output_source=OutputSource.CONTROLLER.value,
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=True,
        role_generation=0,
        observation_sequence=sequence,
        probe_source="mak-sim-probe",
        ambient_source=AmbientSource.CONFIGURED,
        ambient_uncertainty=AmbientUncertainty.UNMEASURED,
    )


def _drive_frame(
    *,
    plant: MAKGrillSim,
    sequence: int,
    runner: ThreadedControllerRunner,
    gate: _FrameBoundaryGate,
    learning: HoldLearningRuntime,
) -> FrameObservation:
    observation = _simulated_frame(plant, sequence)
    auger_duty = observation.realized_auger_duty
    assert auger_duty is not None
    runner.submit(observation.temp_c)
    learning.submit_completed_observation(
        (int(observation.frame_start_s), int(observation.frame_end_s)),
        observation,
        AppliedOutput(
            ratio=auger_duty,
            requested=observation.requested_auger_duty,
            source=OutputSource.CONTROLLER,
            timestamp=observation.frame_end_s,
            producing_result_revision=observation.result_revision,
            feedback_disposition=FrameFeedbackDisposition.COMPLETE,
            sample_complete=True,
        ),
    )
    gate.advance()
    learning.reconcile_outcomes(observation.frame_end_s + 0.001)
    learning.reconcile_activation()
    learning.drain_activation_events()
    _wait_until(
        lambda: next(
            (
                record
                for record in read_model_evidence(kind=EvidenceKind.SESSION_SUMMARY)
                if record.timestamp_ms == int(observation.frame_end_s * 1_000)
                and isinstance(record.payload, SessionSummaryEvidence)
                and record.payload.accepted_observations == 1
            ),
            None,
        ),
        timeout_s=5.0,
        description=f"durable typed evidence for observation {sequence}",
    )
    return observation


def _exact_passive_observation(row: dict[str, Any]) -> FrameObservation:
    """Rehydrate one exact v6 frame while removing only calibration authority."""

    return FrameObservation(
        frame_start_s=row["frame_start_ms"] / 1_000.0,
        frame_end_s=row["frame_end_ms"] / 1_000.0,
        temp_c=row["temp_c"],
        setpoint_c=row["setpoint_c"],
        ambient_c=row["ambient_c"],
        requested_q=row["requested_combustion_load"],
        realized_q=row["realized_combustion_load"],
        baseline_q=row["requested_combustion_load"],
        probe_q=0.0,
        allocated_q=row["allocated_combustion_load"],
        requested_auger_duty=row["requested_auger_duty"],
        scheduled_on_s=row["scheduled_on_seconds"],
        delivered_on_s=row["delivered_on_seconds"],
        realized_auger_duty=row["realized_auger_duty"],
        requested_fan_duty=row["requested_fan_duty"],
        actual_fan_duty=row["actual_fan_duty"],
        allocator_revision=row["allocator_revision"],
        allocation_clamp_reasons=(),
        result_revision=row["result_revision"],
        output_source=row["output_source"],
        lid_open=row["lid_open"],
        safety_inhibited=row["safety_inhibited"],
        manual_override=row["manual_override"],
        stale=row["stale"],
        skipped=row["skipped"],
        reset=row["reset"],
        continuous=row["continuous"],
        role_generation=0,
        observation_sequence=row["observation_sequence"],
        probe_valid=True,
        probe_source="chamber",
        ambient_source=AmbientSource.CONFIGURED,
        ambient_uncertainty=AmbientUncertainty.UNMEASURED,
        calibration_stage=None,
        calibration_fit=False,
        calibration_command_revision=0,
        calibration_command_action="none",
        calibration_cancellation_reason=None,
        calibration_status="inactive",
        cancellation_command_revision=0,
        cancellation_command_action="none",
        completed_calibration_stages=(),
    )


def _drive_exact_passive_frame(
    *,
    row: dict[str, Any],
    runner: ThreadedControllerRunner,
    gate: _FrameBoundaryGate,
    learning: HoldLearningRuntime,
) -> FrameObservation:
    observation = _exact_passive_observation(row)
    auger_duty = observation.realized_auger_duty
    assert auger_duty is not None
    runner.submit(observation.temp_c)
    learning.submit_completed_observation(
        (row["frame_start_ms"], row["frame_end_ms"]),
        observation,
        AppliedOutput(
            ratio=auger_duty,
            requested=observation.requested_auger_duty,
            source=OutputSource(observation.output_source),
            timestamp=observation.frame_end_s,
            producing_result_revision=observation.result_revision,
            feedback_disposition=FrameFeedbackDisposition.COMPLETE,
            sample_complete=True,
        ),
    )
    gate.advance()
    learning.reconcile_outcomes(observation.frame_end_s)
    learning.reconcile_activation()
    learning.drain_activation_events()
    return observation


def _seed_exact_v6_checkpoint(fixture: dict[str, Any]) -> dict[str, Any]:
    checkpoint = cast(dict[str, Any], fixture["checkpoint"])
    assert ControllerModelStore().save("mpc", checkpoint)

    provenance = cast(dict[str, Any], fixture["provenance"])
    legacy_payload = cast(dict[str, Any], fixture["legacy_v6_observation"])
    with datastore.transaction() as connection:
        connection.execute(
            """
            INSERT INTO control_trace(
                ts_ms, session_id, cook_id, controller, event_kind,
                schema_version, payload
            ) VALUES (?, ?, ?, ?, ?, 6, ?)
            """,
            (
                legacy_payload["frame_end_ms"],
                provenance["source_session_id"],
                provenance["source_cook_id"],
                ControllerType.MPC.value,
                TraceEventKind.MODEL_OBSERVATION.value,
                json.dumps(legacy_payload, sort_keys=True, separators=(",", ":")),
            ),
        )
    return checkpoint


def _snapshot_candidate_lineage(snapshot: dict[str, Any]) -> dict[str, Any]:
    identities = cast(dict[str, Any], snapshot["identities"])
    return {
        "origin": snapshot["origin"],
        "policy": snapshot["policy"],
        "candidate_digest": identities["candidate_digest"],
        "candidate_generation": identities["candidate_generation"],
        "window": snapshot["window"],
    }


def _report_candidate_lineage(report: dict[str, Any]) -> dict[str, Any]:
    candidate = cast(dict[str, Any], report["candidate"])
    return {
        "origin": candidate["origin"],
        "policy": candidate["policy"],
        "candidate_digest": candidate["digest"],
        "candidate_generation": candidate["candidate_generation"],
        "window": report["window"],
    }


def _legacy_observation(active_digest: str) -> None:
    legacy_payload = {
        "frame_start_ms": 0,
        "frame_end_ms": 20_000,
        "temp_c": 110.0,
        "setpoint_c": 120.0,
        "ambient_c": 20.0,
        "baseline_combustion_load": 0.40,
        "calibration_probe_load": 0.0,
        "requested_combustion_load": 0.40,
        "allocated_combustion_load": 0.40,
        "realized_combustion_load": 0.40,
        "requested_auger_duty": 0.20,
        "scheduled_on_seconds": 8.0,
        "delivered_on_seconds": 8.0,
        "realized_auger_duty": 0.20,
        "allocator_revision": 2,
        "allocation_clamp_reasons": [],
        "observation_sequence": 1,
        "probe_valid": True,
        "probe_source": "legacy-probe",
        "ambient_source": AmbientSource.CONFIGURED.value,
        "ambient_uncertainty": AmbientUncertainty.UNMEASURED.value,
        "calibration_stage": None,
        "calibration_fit": False,
        "eligible": True,
        "rejection_reasons": [],
        "input_variance": 0.03,
        "input_levels": 3,
        "effective_updates": 120,
        "role_generation": 0,
        "model_digest": active_digest,
        "result_revision": 7,
        "output_source": OutputSource.CONTROLLER.value,
        "lid_open": False,
        "safety_inhibited": False,
        "manual_override": False,
        "stale": False,
        "skipped": False,
        "reset": False,
        "continuous": True,
        "payload_type": "model_observation",
        "incumbent_innovation_c": 4.5,
        "challenger_innovation_c": 2.25,
    }
    with datastore.transaction() as connection:
        connection.execute(
            """
            INSERT INTO control_trace(
                ts_ms, session_id, cook_id, controller, event_kind,
                schema_version, payload
            ) VALUES (?, ?, ?, ?, ?, 6, ?)
            """,
            (
                20_000,
                "legacy-v6-session",
                "legacy-v6-cook",
                ControllerType.MPC.value,
                TraceEventKind.MODEL_OBSERVATION.value,
                json.dumps(legacy_payload, sort_keys=True, separators=(",", ":")),
            ),
        )


def _seed_v6_state(config: dict[str, Any]) -> str:
    """Seed the stale prior-cook authority represented by the field diagnostics."""

    source = Controller(config, "C", dict(_CYCLE))
    try:
        raw_snapshot = source.get_model_snapshot()
        assert isinstance(raw_snapshot, dict)
        snapshot: dict[str, Any] = raw_snapshot
        active = source.active_control_pair.descriptor
        active_configuration: dict[str, Any] = dict(active.configuration)
        candidate_settings: dict[str, Any] = dict(source.cfg)
        candidate_settings["theta"] = float(candidate_settings["theta"]) + 25.0
        candidate = source._pair_factory.descriptor(
            source._pair_factory.configured(
                candidate_settings,
                candidate_generation=1,
                role_generation=1,
                model_identified=True,
            )
        )
        candidate_configuration: dict[str, Any] = dict(candidate.configuration)
    finally:
        source.close()
    decision_id = "legacy-undurable-operator-decision"
    prepared = PreparedActivationRecord.prepared(
        timestamp_ms=1_000,
        incumbent=active,
        candidate=candidate,
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        policy=ActivationPolicy.OPERATOR_REVIEWED,
        decision_id=decision_id,
    )
    aborted = prepared.transition(ActivationPhase.ABORTED, reason="legacy-interrupted-activation")

    challenger_parameters = dict(snapshot["active"]["parameters"])
    challenger_parameters["theta"] = candidate_configuration["theta"]
    snapshot["challenger"] = {
        "parameters": challenger_parameters,
        "metadata": {
            "rmse": 1.5,
            "samples": _FIT_SAMPLES,
            "band_c": [40.0, 160.0],
            "nfev": 8,
        },
    }
    snapshot["window"] = {
        "session_id": "legacy-v6-session",
        "cook_id": "legacy-v6-cook",
        "first_observation_sequence": 10,
        "last_observation_sequence": 129,
        "configuration_digest": "d" * 64,
        "incumbent_digest": active.model_digest,
        "role_generation": 0,
    }
    snapshot["candidate_pair"] = candidate.to_dict()
    snapshot["evidence"]["confidence_decision_id"] = None
    snapshot["origin"] = CandidateOrigin.OPERATOR_CALIBRATION.value
    snapshot["policy"] = ActivationPolicy.OPERATOR_REVIEWED.value
    snapshot["identities"]["candidate_digest"] = candidate.model_digest
    snapshot["identities"]["candidate_generation"] = candidate.candidate_generation
    snapshot["activation"] = {
        "phase": ActivationPhase.ABORTED.value,
        "pending_persistence": False,
        "pending_swap": False,
    }
    snapshot["failure"] = None
    canonical = migrate_grey_learning_snapshot(snapshot)
    assert canonical is not None
    assert ControllerModelStore().save("mpc", canonical)

    with datastore.transaction() as connection:
        connection.execute(
            """
            INSERT INTO model_activation_state(
                singleton, active_snapshot_json, rollback_snapshot_json,
                evidence_decision_id, controller_configuration_digest,
                role_generation, phase, transaction_id, incumbent_pair_json,
                candidate_pair_json, rollback_pair_json, origin, policy,
                candidate_generation, candidate_digest, reason
            ) VALUES (1, ?, ?, ?, ?, 0, 'aborted', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                json.dumps(active_configuration, sort_keys=True, separators=(",", ":")),
                json.dumps(active_configuration, sort_keys=True, separators=(",", ":")),
                decision_id,
                candidate.ownership_digest,
                aborted.transaction_id,
                json.dumps(active.to_dict(), sort_keys=True, separators=(",", ":")),
                json.dumps(candidate.to_dict(), sort_keys=True, separators=(",", ":")),
                json.dumps(active.to_dict(), sort_keys=True, separators=(",", ":")),
                CandidateOrigin.OPERATOR_CALIBRATION.value,
                ActivationPolicy.OPERATOR_REVIEWED.value,
                candidate.candidate_generation,
                candidate.model_digest,
                aborted.reason,
            ),
        )
    _legacy_observation(active.model_digest)
    return aborted.transaction_id


def _trace_context(
    snapshot: dict[str, Any],
    config: dict[str, Any],
    cook_id: str,
    *,
    setpoint_c: float = _SETPOINT_C,
    ambient_c: float = MAKGrillSim.AMBIENT_C,
) -> TraceSessionContext:
    return TraceSessionContext(
        controller=ControllerType.MPC,
        controller_config=config,
        temperature_unit="C",
        control_period_seconds=float(config["control_period"]),
        fallback_model=TraceModelAuthority(snapshot, "runner"),
        runner_snapshot_fallback_safe=True,
        pulse_slot_seconds=1.0,
        pulse_frame_seconds=float(_FRAME_SECONDS),
        fan_authority=False,
        fan_pwm_capable=False,
        fan_min_duty=0.0,
        fan_max_duty=1.0,
        setpoint=setpoint_c,
        ambient_temperature=ambient_c,
        software_version="e2e",
        build_version="e2e",
        cook_id=cook_id,
        runner_generation=0,
    )


def _identity(snapshot: dict[str, Any]) -> tuple[str, int]:
    identities = snapshot["identities"]
    assert isinstance(identities, dict)
    digest = identities["active_digest"]
    generation = identities["active_generation"]
    assert isinstance(digest, str)
    assert isinstance(generation, int)
    return digest, generation


@pytest.mark.parametrize(
    "database_state",
    ("fresh-v7", "upgraded-v6"),
    ids=("fresh-v7", "upgraded-v6"),
)
def test_passive_online_learning_crosses_trace_persistence_activation_and_restart(
    ds,
    database_state: str,
) -> None:
    config: dict[str, Any] = dict(DEFAULT_MPC_CONFIG)
    config["enable_online_adaptation"] = True
    stale_transaction_id = _seed_v6_state(config) if database_state == "upgraded-v6" else None
    cook_id = f"mpc-online-learning-{database_state}"

    gate = _FrameBoundaryGate()
    core = Controller(config, "C", dict(_CYCLE))
    runner = ThreadedControllerRunner(
        core,
        controller_type=ControllerType.MPC,
        wait_for_period=gate,
    )
    gate.wait_until_blocked()
    model_store = ControllerModelStore()
    persistence = ModelPersistenceWorker(model_store, _TEST_LOGGER)
    recorder = ControlTraceRecorder(
        monotonic_clock=lambda: 0,
        wall_clock=lambda: RETENTION_PERIOD_MS,
    )
    trace = ControlTraceSession(recorder, warning=_TEST_LOGGER.warning)
    learning = HoldLearningRuntime(
        runner=runner,
        model_store=model_store,
        persistence=persistence,
        trace=trace,
        controller_name="mpc",
        logger=_TEST_LOGGER,
        initial_generation=0,
    )
    learned_snapshot: dict[str, Any] | None = None
    initial_snapshot: dict[str, Any] | None = None
    before_active_boundary: dict[str, Any] | None = None
    durable_active = None
    try:
        learning.restore_model(timestamp_ms=0)
        learning.reconcile_activation()
        gate.advance()
        learning.reconcile_outcomes(0.001)

        restored_or_configured = runner.get_model_snapshot()
        assert isinstance(restored_or_configured, dict)
        identity = trace.ensure_open(
            _trace_context(restored_or_configured, config, cook_id),
            timestamp_ms=0,
        )
        assert identity is not None
        learning.bind_generation(0)
        runner.set_target(_SETPOINT_C)
        runner.submit(MAKGrillSim.AMBIENT_C)
        gate.advance()
        initial_snapshot = runner.get_model_snapshot()
        assert isinstance(initial_snapshot, dict)
        initial_digest, initial_generation = _identity(initial_snapshot)
        assert initial_generation == 0

        plant = MAKGrillSim(seed=7)
        for sequence in range(_FIT_SAMPLES):
            _drive_frame(
                plant=plant,
                sequence=sequence,
                runner=runner,
                gate=gate,
                learning=learning,
            )

        _wait_until(
            lambda: (
                state
                if (state := core.get_learning_diagnostics().state).get("status") == "evaluating"
                and state.get("fit_status") == "succeeded"
                else None
            ),
            timeout_s=90.0,
            description="the real grey fit and candidate preparation",
        )

        for sequence in range(_FIT_SAMPLES, _FIRST_EVALUATION_END + 1):
            _drive_frame(
                plant=plant,
                sequence=sequence,
                runner=runner,
                gate=gate,
                learning=learning,
            )

        first_confidence = _wait_until(
            lambda: next(
                (
                    record
                    for record in read_model_evidence(kind=EvidenceKind.CONFIDENCE_DECISION)
                    if isinstance(record.payload, ConfidenceDecisionEvidence)
                    and record.payload.blocked
                    and record.payload.reason == "confidence-rejected"
                ),
                None,
            ),
            timeout_s=30.0,
            description="the first winning causal confidence window",
        )

        _drive_frame(
            plant=plant,
            sequence=_SECOND_EVALUATION_END,
            runner=runner,
            gate=gate,
            learning=learning,
        )
        second_confidence = _wait_until(
            lambda: next(
                (
                    record
                    for record in read_model_evidence(kind=EvidenceKind.CONFIDENCE_DECISION)
                    if isinstance(record.payload, ConfidenceDecisionEvidence)
                    and not record.payload.blocked
                    and record.payload.decision_id != first_confidence.payload.decision_id
                ),
                None,
            ),
            timeout_s=30.0,
            description="the second winning causal confidence window",
        )
        prepared_state = _wait_until(
            lambda: (
                state
                if (state := read_model_activation()) is not None
                and state.phase == ActivationPhase.PREPARED.value
                and state.transaction_id != stale_transaction_id
                else None
            ),
            timeout_s=30.0,
            description="durable passive activation preparation",
        )
        assert prepared_state.evidence_decision_id == second_confidence.payload.decision_id

        learning.reconcile_activation()
        _drive_frame(
            plant=plant,
            sequence=_EVALUATION_PUBLICATION_SEQUENCE,
            runner=runner,
            gate=gate,
            learning=learning,
        )
        durable_active = _wait_until(
            lambda: (
                state
                if (state := read_model_activation()) is not None
                and state.phase == ActivationPhase.ACTIVE.value
                and state.transaction_id == prepared_state.transaction_id
                else None
            ),
            timeout_s=30.0,
            description="durable active activation phase",
        )

        before_active_boundary = runner.get_model_snapshot()
        assert isinstance(before_active_boundary, dict)
        assert _identity(before_active_boundary) == (initial_digest, initial_generation)
        assert core.activation_output_authorized is False

        learning.reconcile_activation()
        gate.advance()
        learning.reconcile_activation()
        learning.drain_activation_events()
        learned_snapshot = runner.get_model_snapshot()
        assert isinstance(learned_snapshot, dict)
        learned_digest, learned_generation = _identity(learned_snapshot)
        assert learned_generation > initial_generation
        assert learned_digest != initial_digest
        assert core.activation_output_authorized is True
        assert durable_active.role_generation == learned_generation
        assert durable_active.active_pair is not None
        assert durable_active.active_pair.model_digest == learned_digest
        assert learning.submit_online_checkpoint(learned_snapshot)
    finally:
        learning.finish_teardown(generation=0)
        runner.stop()

    assert initial_snapshot is not None
    assert before_active_boundary is not None
    assert learned_snapshot is not None
    assert durable_active is not None
    initial_parameters = cast(
        dict[str, Any],
        cast(dict[str, Any], initial_snapshot["active"])["parameters"],
    )
    learned_active = cast(dict[str, Any], learned_snapshot["active"])
    learned_parameters = cast(dict[str, Any], learned_active["parameters"])
    assert any(learned_parameters[name] != initial_parameters[name] for name in ("C_c", "K_Q", "theta"))

    raw_persisted = ControllerModelStore().load("mpc")
    assert isinstance(raw_persisted, dict)
    persisted: dict[str, Any] = raw_persisted
    assert _identity(persisted) == _identity(learned_snapshot)
    assert cast(dict[str, Any], persisted["active"])["parameters"] == learned_parameters

    cook_trace = read_control_trace_cook(cook_id)
    observation_records = [
        record
        for record in cook_trace
        if record.event_kind is TraceEventKind.MODEL_OBSERVATION and isinstance(record.payload, ModelObservationPayload)
    ]
    assert len(observation_records) == _EVALUATION_PUBLICATION_SEQUENCE + 1
    assert all(record.schema_version == TRACE_SCHEMA_VERSION for record in observation_records)
    for record in observation_records:
        payload = cast(ModelObservationPayload, record.payload)
        assert payload.eligible
        normalized_load = _load_for(payload.observation_sequence)
        assert payload.requested_combustion_load == pytest.approx(normalized_load)
        assert payload.realized_combustion_load == pytest.approx(normalized_load)
        assert payload.requested_auger_duty == pytest.approx(normalized_load * _U_MAX)
        assert payload.delivered_on_seconds == pytest.approx(normalized_load * _U_MAX * _FRAME_SECONDS)
        assert _OBSOLETE_V6_SCORES.isdisjoint(json.loads(record.to_db_row().payload))
    fit_gate_observation = next(
        cast(ModelObservationPayload, record.payload)
        for record in observation_records
        if cast(ModelObservationPayload, record.payload).observation_sequence == 119
    )
    assert fit_gate_observation.effective_updates == _FIT_SAMPLES
    assert fit_gate_observation.input_levels == 3
    assert fit_gate_observation.input_variance == pytest.approx(0.08166666666666668)

    evaluations = [
        cast(ModelEvaluationPayload, record.payload)
        for record in cook_trace
        if record.event_kind is TraceEventKind.MODEL_EVALUATION and isinstance(record.payload, ModelEvaluationPayload)
    ]
    wins = [evaluation.consecutive_wins for evaluation in evaluations]
    assert wins[:2] == [1, 2]
    assert all(earlier < later for earlier, later in itertools.pairwise(wins))
    assert all(not evaluation.rejection_reasons for evaluation in evaluations)
    assert all(
        {score.horizon_steps for score in evaluation.horizon_scores} == _REQUIRED_HORIZONS
        and all(
            score.challenger_rmse_c is not None
            and score.incumbent_rmse_c is not None
            and score.challenger_rmse_c < score.incumbent_rmse_c
            for score in evaluation.horizon_scores
        )
        for evaluation in evaluations
    )

    cook_evidence = read_model_evidence(cook_id=cook_id)
    fit_records = [record.payload for record in cook_evidence if isinstance(record.payload, FitLifecycleEvidence)]
    assert [record.status for record in fit_records] == ["queued", "succeeded"]
    assert len({record.request_id for record in fit_records}) == 1
    assert all(record.origin == CandidateOrigin.PASSIVE_ONLINE.value for record in fit_records)
    assessments = [
        record.payload for record in cook_evidence if isinstance(record.payload, CandidateAssessmentEvidence)
    ]
    assert len(assessments) >= 2
    assessment_ids = [assessment.decision_id for assessment in assessments]
    evaluation_ids = [evaluation.decision_id for evaluation in evaluations]
    assert evaluation_ids == assessment_ids[: len(evaluation_ids)]
    assert assessments[0].confidence_accepted is False
    assert assessments[0].rejection_reasons == ("confidence-rejected",)
    assert all(assessment.confidence_accepted for assessment in assessments[1:])
    assert all(assessment.rejection_reasons == () for assessment in assessments[1:])
    assert assessments[1].decision_id == second_confidence.payload.decision_id
    assert assessments[1].decision_id == durable_active.evidence_decision_id
    assert all(
        assessment.fit_accepted
        and assessment.identifiability_accepted
        and assessment.native_build == "passed"
        and assessment.native_dry_solve == "passed"
        and assessment.target_timing == "passed"
        for assessment in assessments
    )
    forecast_records = [
        record.payload for record in cook_evidence if isinstance(record.payload, ForecastOriginEvidence)
    ]
    assert {forecast.horizon_steps for forecast in forecast_records} == _REQUIRED_HORIZONS
    assert all(not forecast.calibration_fit for forecast in forecast_records)

    lifecycle = [
        record.payload
        for record in read_model_evidence(kind=EvidenceKind.ACTIVATION_LIFECYCLE)
        if isinstance(record.payload, ActivationLifecycleEvidence)
        and record.payload.decision_id == durable_active.evidence_decision_id
    ]
    assert {record.phase for record in lifecycle} == {"prepared", "active"}
    assert all(record.origin == CandidateOrigin.PASSIVE_ONLINE.value for record in lifecycle)
    assert all(record.policy == ActivationPolicy.PASSIVE_AUTO.value for record in lifecycle)

    if database_state == "upgraded-v6":
        legacy = read_control_trace_session("legacy-v6-session")
        assert len(legacy) == 1
        assert legacy[0].schema_version == 6
        assert _OBSOLETE_V6_SCORES.isdisjoint(json.loads(legacy[0].to_db_row().payload))
        assert not any(
            isinstance(record.payload, ConfidenceDecisionEvidence)
            and record.payload.decision_id == "legacy-undurable-operator-decision"
            for record in read_model_evidence()
        )

    restart_core = Controller(config, "C", dict(_CYCLE))
    restart_runner = SyncControllerRunner(restart_core, controller_type=ControllerType.MPC)
    restart_learning = HoldLearningRuntime(
        runner=restart_runner,
        model_store=ControllerModelStore(),
        persistence=None,
        trace=None,
        controller_name="mpc",
        logger=_TEST_LOGGER,
        initial_generation=0,
    )
    try:
        restart_runner.set_target(_SETPOINT_C)
        restart_learning.restore_model(timestamp_ms=10_000_000)
        restart_learning.reconcile_activation()
        raw_restored = restart_runner.get_model_snapshot()
        assert isinstance(raw_restored, dict)
        restored: dict[str, Any] = raw_restored
        assert _identity(restored) == _identity(learned_snapshot)
        assert cast(dict[str, Any], restored["active"])["parameters"] == learned_parameters
        assert restart_core.activation_output_authorized is True
    finally:
        restart_learning.finish_teardown(generation=0)
        restart_runner.stop()


def test_restored_v6_checkpoint_rebinds_exact_passive_candidate_provenance(ds) -> None:
    fixture = cast(
        dict[str, Any],
        json.loads(_EXACT_V6_FIXTURE.read_text(encoding="utf-8")),
    )
    provenance = cast(dict[str, Any], fixture["provenance"])
    fields = cast(list[str], fixture["frame_fields"])
    encoded_rows = cast(list[list[Any]], fixture["frame_rows"])
    rows = [dict(zip(fields, values, strict=True)) for values in encoded_rows]
    encoded_following_rows = cast(list[list[Any]], fixture["following_frame_rows"])
    following_rows = [dict(zip(fields, values, strict=True)) for values in encoded_following_rows]
    canonical_rows = json.dumps(
        rows,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    canonical_following_rows = json.dumps(
        following_rows,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    assert fixture["schema"] == "pifire-e2e-restored-v6-passive-replay/v1"
    assert len(fields) == 25
    assert all(len(values) == len(fields) for values in encoded_rows)
    assert all(len(values) == len(fields) for values in encoded_following_rows)
    assert len(rows) == _FIT_SAMPLES
    assert [row["observation_sequence"] for row in rows] == list(range(21, 141))
    assert rows[0]["frame_start_ms"] == 1_787_871_688_267
    assert rows[-1]["frame_end_ms"] == 1_787_874_088_267
    assert len(following_rows) == 1
    assert following_rows[0]["observation_sequence"] == 141
    assert following_rows[0]["frame_start_ms"] == rows[-1]["frame_end_ms"]
    assert following_rows[0]["frame_end_ms"] == 1_787_874_108_267
    assert hashlib.sha256(canonical_rows).hexdigest() == fixture["frame_rows_sha256"]
    assert fixture["frame_rows_sha256"] == _EXACT_V6_ROWS_SHA256
    assert hashlib.sha256(canonical_following_rows).hexdigest() == fixture["following_frame_rows_sha256"]
    assert fixture["following_frame_rows_sha256"] == _EXACT_FOLLOWING_ROWS_SHA256
    assert provenance == {
        "cook_archive": {
            "member": "learning_diagnostics.json",
            "name": "2026-08-27--2015-CookFile.pifire",
            "sha256": "e60243b901d0244344954d1e93ed86e449cbee0b40320f7b37f146fbaab981a5",
        },
        "diagnostics_archive": {
            "member": "pifire.db",
            "name": "PiFire_Diagnostics_20260827-202055 (1).zip",
            "sha256": "9c296ca2d70121d9ed62d9950680b24e6dbfcb41ab83894662c41b2ea80fc60d",
        },
        "source_checkpoint_key": "controller_model_state",
        "source_checkpoint_revision": 2,
        "source_cook_id": "0ad52abe-a266-11f1-9074-88a29e57b0e5",
        "source_sequence_range": [21, 140],
        "source_session_id": "0236ccee-7931-4723-a5ba-776773109fa9",
        "source_trace_schema_version": 6,
    }

    source_checkpoint = _seed_exact_v6_checkpoint(fixture)
    source_identities = cast(dict[str, Any], source_checkpoint["identities"])
    source_active = cast(dict[str, Any], source_checkpoint["active"])
    source_challenger = cast(dict[str, Any], source_checkpoint["challenger"])
    source_active_pair = cast(dict[str, Any], source_checkpoint["active_pair"])
    source_candidate_pair = cast(dict[str, Any], source_checkpoint["candidate_pair"])
    assert source_checkpoint["schema"] == "pifire-grey-learning/v4"
    assert source_checkpoint["revision"] == 2
    assert source_checkpoint["origin"] == CandidateOrigin.OPERATOR_CALIBRATION.value
    assert source_checkpoint["policy"] is None
    assert source_checkpoint["activation"] == {
        "pending_persistence": False,
        "pending_swap": False,
        "phase": ActivationPhase.ABORTED.value,
    }
    assert source_identities == {
        "active_digest": _EXACT_V6_ACTIVE_DIGEST,
        "active_generation": 0,
        "candidate_digest": _EXACT_V6_CANDIDATE_DIGEST,
        "candidate_generation": 1,
        "rollback_digest": None,
        "rollback_generation": None,
    }
    assert source_active["parameters"] == {
        "C_c": 320.0,
        "K_Q": 350.0,
        "T_amb": 20.0,
        "h_amb": 0.5,
        "n_delay": 8,
        "sigma": 1.4e-09,
        "theta": 50.0,
    }
    assert source_challenger["parameters"] == {
        "C_c": 1767.4962464102664,
        "K_Q": 288.20982775716607,
        "T_amb": 20.0,
        "h_amb": 0.5,
        "n_delay": 8,
        "sigma": 1.4e-09,
        "theta": 52.24105678450995,
    }
    assert source_active_pair["model_digest"] == _EXACT_V6_ACTIVE_DIGEST
    assert source_active_pair["ownership_digest"] == (
        "f0eb4c8212c0419201e540bf40d2124ec48ad6d9bcd571ff3d8a7ee7cfd9a207"
    )
    assert source_candidate_pair["model_digest"] == _EXACT_V6_CANDIDATE_DIGEST
    assert source_candidate_pair["ownership_digest"] == (
        "7d0bed9087979a5b39de36329ba8e1934572ec3a121042feb6e7f60af83fc5c6"
    )
    assert source_checkpoint["window"] == {
        "configuration_digest": "2f49eeaca6aecc624162d29933246072c34a52ff583773a8449a394982d7ba43",
        "cook_id": provenance["source_cook_id"],
        "first_observation_sequence": 21,
        "incumbent_digest": _EXACT_V6_ACTIVE_DIGEST,
        "last_observation_sequence": 140,
        "role_generation": 0,
        "session_id": provenance["source_session_id"],
    }
    assert ControllerModelStore().load_strict("mpc") == source_checkpoint

    legacy_source = cast(dict[str, Any], fixture["legacy_v6_observation"])
    raw_legacy_payload = json.loads(
        datastore.connection()
        .execute(
            "SELECT payload FROM control_trace WHERE session_id=?",
            (provenance["source_session_id"],),
        )
        .fetchone()[0]
    )
    assert _OBSOLETE_V6_SCORES.issubset(raw_legacy_payload)
    legacy_records = read_control_trace_session(provenance["source_session_id"])
    assert len(legacy_records) == 1
    assert legacy_records[0].schema_version == 6
    assert isinstance(legacy_records[0].payload, ModelObservationPayload)
    expected_migrated_v6 = dict(legacy_source)
    for obsolete_key in _OBSOLETE_V6_SCORES:
        expected_migrated_v6.pop(obsolete_key)
    migrated_v6 = json.loads(legacy_records[0].to_db_row().payload)
    assert migrated_v6 == expected_migrated_v6
    assert _OBSOLETE_V6_SCORES.isdisjoint(migrated_v6)

    config: dict[str, Any] = dict(DEFAULT_MPC_CONFIG)
    config["enable_online_adaptation"] = True
    first_row = rows[0]
    gate = _FrameBoundaryGate()
    core = Controller(config, "C", dict(_CYCLE))
    runner = ThreadedControllerRunner(
        core,
        controller_type=ControllerType.MPC,
        wait_for_period=gate,
    )
    gate.wait_until_blocked()
    persistence = ModelPersistenceWorker(ControllerModelStore(), _TEST_LOGGER)
    trace = ControlTraceSession(
        ControlTraceRecorder(
            monotonic_clock=lambda: 0,
            wall_clock=lambda: RETENTION_PERIOD_MS,
        ),
        warning=_TEST_LOGGER.warning,
    )
    learning = HoldLearningRuntime(
        runner=runner,
        model_store=ControllerModelStore(),
        persistence=persistence,
        trace=trace,
        controller_name="mpc",
        logger=_TEST_LOGGER,
        initial_generation=0,
    )
    trace_identity = None
    fit_state: dict[str, Any] | None = None
    fit_evidence_records = []
    live_checkpoint: dict[str, Any] | None = None
    persisted_checkpoint: dict[str, Any] | None = None
    normalized_report: dict[str, Any] | None = None
    passive_candidate_digest: str | None = None
    try:
        learning.restore_model(timestamp_ms=first_row["frame_start_ms"] - 1)
        learning.reconcile_activation()
        gate.advance()
        learning.reconcile_outcomes(first_row["frame_start_ms"] / 1_000.0 - 0.001)
        runner.set_target(first_row["setpoint_c"])
        runner.submit(first_row["ambient_c"])
        gate.advance()

        def restored_v6_projection():
            snapshot = runner.get_model_snapshot()
            if not isinstance(snapshot, dict):
                return None
            identities = snapshot.get("identities")
            if not isinstance(identities, dict):
                return None
            return snapshot if identities.get("candidate_digest") == _EXACT_V6_CANDIDATE_DIGEST else None

        restored_v6 = cast(
            dict[str, Any],
            _wait_until(
                restored_v6_projection,
                timeout_s=5.0,
                description="the exact restored v6 candidate projection",
            ),
        )
        assert _identity(restored_v6) == (_EXACT_V6_ACTIVE_DIGEST, 0)
        assert _snapshot_candidate_lineage(restored_v6) == {
            "origin": CandidateOrigin.OPERATOR_CALIBRATION.value,
            "policy": None,
            "candidate_digest": _EXACT_V6_CANDIDATE_DIGEST,
            "candidate_generation": 1,
            "window": source_checkpoint["window"],
        }
        trace_identity = trace.ensure_open(
            _trace_context(
                restored_v6,
                config,
                _EXACT_REPLAY_COOK_ID,
                setpoint_c=first_row["setpoint_c"],
                ambient_c=first_row["ambient_c"],
            ),
            timestamp_ms=first_row["frame_start_ms"],
        )
        assert trace_identity is not None
        learning.bind_generation(0)

        for row in rows:
            _drive_exact_passive_frame(
                row=row,
                runner=runner,
                gate=gate,
                learning=learning,
            )

        fit_state = cast(
            dict[str, Any],
            _wait_until(
                lambda: (
                    state
                    if (state := dict(core.get_learning_diagnostics().state)).get("status") == "evaluating"
                    and state.get("fit_status") == "succeeded"
                    and isinstance(state.get("candidate_digest"), str)
                    else None
                ),
                timeout_s=90.0,
                description="the passive 21-140 grey fit",
            ),
        )
        passive_candidate_digest = cast(str, fit_state["candidate_digest"])
        # The point of this test: the passive fit must produce its own candidate,
        # not rebind either identity carried in by the restored v6 checkpoint.
        assert passive_candidate_digest != _EXACT_V6_CANDIDATE_DIGEST
        assert passive_candidate_digest != _EXACT_V6_ACTIVE_DIGEST
        for following_row in following_rows:
            _drive_exact_passive_frame(
                row=following_row,
                runner=runner,
                gate=gate,
                learning=learning,
            )
        fit_state = dict(core.get_learning_diagnostics().state)
        assert fit_state["status"] == "evaluating"
        assert fit_state["fit_status"] == "succeeded"

        def durable_fit_lifecycle():
            records = [
                record
                for record in read_model_evidence(cook_id=_EXACT_REPLAY_COOK_ID)
                if isinstance(record.payload, FitLifecycleEvidence)
            ]
            statuses = [cast(FitLifecycleEvidence, record.payload).status for record in records]
            return records if statuses == ["queued", "succeeded"] else None

        fit_evidence_records = _wait_until(
            durable_fit_lifecycle,
            timeout_s=30.0,
            description="durable exact passive fit lifecycle",
        )
        raw_live_checkpoint = runner.get_model_snapshot()
        assert isinstance(raw_live_checkpoint, dict)
        live_checkpoint = raw_live_checkpoint
        live_challenger = cast(dict[str, Any], live_checkpoint["challenger"])
        _assert_passive_parameters(cast(Mapping[str, Any], live_challenger["parameters"]))
        metadata = cast(dict[str, Any], live_challenger["metadata"])
        assert metadata.keys() == {"band_c", "nfev", "rmse", "samples"}
        assert metadata["band_c"] == [100.66666666666667, 109.11111111111111]
        # nfev is the solver's iteration count, observed identical across the
        # acados builds tested so far; a different linear solver or
        # termination tolerance is the first thing to suspect if this
        # assertion breaks on another build.
        assert metadata["nfev"] == 9
        assert metadata["samples"] == 120
        assert metadata["rmse"] == pytest.approx(1.6228871053911238, rel=1e-9)
        assert learning.submit_online_checkpoint(live_checkpoint)
        assert persistence.flush_and_stop(timeout=30.0)
        raw_persisted_checkpoint = ControllerModelStore().load_strict("mpc")
        assert isinstance(raw_persisted_checkpoint, dict)
        persisted_checkpoint = raw_persisted_checkpoint
        normalized_report = current_learning_report(
            tuple(read_model_evidence()),
            activation_state=read_model_activation(),
            live_status=fit_state,
            checkpoint_required=True,
            calibration_command_high_water=0,
            checkpoint=persisted_checkpoint,
        ).as_dict()
    finally:
        gate.close()
        learning.finish_teardown(generation=0)
        runner.stop()

    assert trace_identity is not None
    assert fit_state is not None
    assert live_checkpoint is not None
    assert persisted_checkpoint is not None
    assert normalized_report is not None
    assert passive_candidate_digest is not None
    observation_records = [
        record
        for record in read_control_trace_cook(_EXACT_REPLAY_COOK_ID)
        if record.event_kind is TraceEventKind.MODEL_OBSERVATION and isinstance(record.payload, ModelObservationPayload)
    ]
    assert len(observation_records) == _FIT_SAMPLES + 1
    assert all(record.schema_version == TRACE_SCHEMA_VERSION for record in observation_records)
    replay_rows = []
    for record in observation_records:
        payload = cast(ModelObservationPayload, record.payload)
        payload_dict = json.loads(record.to_db_row().payload)
        replay_rows.append({field: payload_dict.get(field) for field in fields})
        assert payload.eligible
        assert payload.rejection_reasons == ()
        assert payload.calibration_fit is False
        assert payload.calibration_stage is None
        assert payload.calibration_probe_load == 0.0
        assert payload.baseline_combustion_load == payload.requested_combustion_load
        assert payload.calibration_status == "inactive"
        assert payload.calibration_command_revision == 0
        assert payload.calibration_command_action == "none"
        assert payload.cancellation_command_revision == 0
        assert payload.cancellation_command_action == "none"
    replay_bytes = json.dumps(
        replay_rows[:_FIT_SAMPLES],
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    replay_following_bytes = json.dumps(
        replay_rows[_FIT_SAMPLES:],
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert hashlib.sha256(replay_bytes).hexdigest() == _EXACT_V6_ROWS_SHA256
    assert hashlib.sha256(replay_following_bytes).hexdigest() == _EXACT_FOLLOWING_ROWS_SHA256
    fit_gate_observation = cast(ModelObservationPayload, observation_records[-2].payload)
    assert fit_gate_observation.observation_sequence == 140
    assert fit_gate_observation.effective_updates == _FIT_SAMPLES
    assert fit_gate_observation.input_levels == 72
    assert fit_gate_observation.input_variance == 0.055502661581373174
    assert cast(ModelObservationPayload, observation_records[-1].payload).observation_sequence == 141

    fit_payloads = [cast(FitLifecycleEvidence, record.payload) for record in fit_evidence_records]
    assert [payload.status for payload in fit_payloads] == ["queued", "succeeded"]
    assert len({payload.request_id for payload in fit_payloads}) == 1
    assert all(
        payload.origin == CandidateOrigin.PASSIVE_ONLINE.value
        and payload.policy == ActivationPolicy.PASSIVE_AUTO.value
        and payload.window_id == f"{trace_identity.session_id}:21:140"
        for payload in fit_payloads
    )
    assert fit_evidence_records[0].model_digest == _EXACT_V6_ACTIVE_DIGEST
    assert fit_evidence_records[1].model_digest == passive_candidate_digest
    assert not any(
        isinstance(
            record.payload,
            (
                CandidateAssessmentEvidence,
                ConfidenceDecisionEvidence,
                ActivationLifecycleEvidence,
            ),
        )
        for record in read_model_evidence(cook_id=_EXACT_REPLAY_COOK_ID)
    )
    assert read_model_activation() is None

    restart_core = Controller(config, "C", dict(_CYCLE))
    restart_runner = SyncControllerRunner(restart_core, controller_type=ControllerType.MPC)
    restart_learning = HoldLearningRuntime(
        runner=restart_runner,
        model_store=ControllerModelStore(),
        persistence=None,
        trace=None,
        controller_name="mpc",
        logger=_TEST_LOGGER,
        initial_generation=0,
    )
    try:
        restart_runner.set_target(first_row["setpoint_c"])
        restart_learning.restore_model(timestamp_ms=following_rows[-1]["frame_end_ms"] + 1)
        restart_learning.reconcile_activation()
        raw_restart_checkpoint = restart_runner.get_model_snapshot()
        assert isinstance(raw_restart_checkpoint, dict)
        restart_checkpoint: dict[str, Any] = raw_restart_checkpoint
    finally:
        restart_learning.finish_teardown(generation=0)
        restart_runner.stop()

    expected_window = {
        "configuration_digest": _EXACT_PASSIVE_CONFIGURATION_DIGEST,
        "cook_id": _EXACT_REPLAY_COOK_ID,
        "first_observation_sequence": 21,
        "incumbent_digest": _EXACT_V6_ACTIVE_DIGEST,
        "last_observation_sequence": 140,
        "role_generation": 0,
        "session_id": trace_identity.session_id,
    }
    expected_lineage = {
        "origin": CandidateOrigin.PASSIVE_ONLINE.value,
        "policy": ActivationPolicy.PASSIVE_AUTO.value,
        "candidate_digest": passive_candidate_digest,
        "candidate_generation": 1,
        "window": expected_window,
    }
    lineage_by_surface = {
        "checkpoint": _snapshot_candidate_lineage(live_checkpoint),
        "normalized_report": _report_candidate_lineage(normalized_report),
        "persistence": _snapshot_candidate_lineage(persisted_checkpoint),
        "restart": _snapshot_candidate_lineage(restart_checkpoint),
    }
    assert lineage_by_surface == {surface: expected_lineage for surface in lineage_by_surface}

    assert normalized_report["status"] == "evaluating"
    assert normalized_report["mode"] == CandidateOrigin.PASSIVE_ONLINE.value
    assert normalized_report["blockers"] == []
    for checkpoint in (live_checkpoint, persisted_checkpoint, restart_checkpoint):
        assert _identity(checkpoint) == (_EXACT_V6_ACTIVE_DIGEST, 0)
        assert checkpoint["revision"] == source_checkpoint["revision"] + 1
        assert checkpoint["activation"] == {
            "pending_persistence": False,
            "pending_swap": False,
            "phase": ActivationPhase.ABORTED.value,
        }
        challenger = cast(dict[str, Any], checkpoint["challenger"])
        _assert_passive_parameters(cast(Mapping[str, Any], challenger["parameters"]))
