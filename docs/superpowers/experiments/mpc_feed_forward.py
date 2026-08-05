#!/usr/bin/env python3
"""Fixed-seed, three-arm MPC scheduler and equilibrium-feed-forward gate."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
from collections import Counter
from itertools import product
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from common.control_trace import (
    ActuationMode,
    AllocationPayload,
    AppliedOutputPayload,
    ControlTraceRecord,
    ControllerType,
    FramedPulseFramePayload,
    InhibitReason,
    MpcUpdatePayload,
    ResultStaleState,
    SafetyEventPayload,
    SafetyEventType,
    SessionPayload,
    TraceEventKind,
    TraceSetting,
)
from common.defaults import default_settings
from common.datastore import _CONTROL_TRACE_DDL
from common.datastore_accessors import read_control_trace_session
from controller.applied_output import OutputSource
from controller.grill_sim import GrillSim, MAKGrillSim
from controller.mpc_model import steady_combustion_load
from controller.mpc_allocator import normalized_load_from_auger_duty
from controller.runtime.control_trace_recorder import ControlTraceRecorder
from controller.runtime.logic.pulse import PulseResetReason, PulseScheduler
from controller.runtime.modes.hold import HoldMode
from controller.runtime.state import WorkCycleState
from tests.characterization.fixtures import base_control, base_pellet_db, base_settings
from tests.characterization.harness import make_ctx
from tests.fakes.probes import FakeProbes
from controller.runtime.runner import ControllerUpdateResult, ThreadedControllerRunner
from docs.superpowers.experiments import controller_matrix
from docs.superpowers.experiments.mpc_pulse_allocator import ARMS as PULSE_ARMS, LEVELS as PULSE_LEVELS, _old_affine

OUT = Path(__file__).with_name("_mpc_feed_forward.json")
PULSE_EVIDENCE = Path(__file__).with_name("_mpc_pulse_allocator.json")
PLANTS = controller_matrix.PLANTS
SCENARIOS = controller_matrix.SCENARIOS
CAPABILITY_SCENARIO = controller_matrix.CAPABILITY_UNREACHABLE_HIGH_SCENARIO
SCENARIO_NAMES = (
    "steady_225",
    "steady_325",
    "steady_350",
    "steady_450",
    "step_225_275",
    "lid_open_225",
    CAPABILITY_SCENARIO,
)
SEEDS = (0, 1, 2, 3, 4)
LEGACY_CONTROL_PERIOD_S = 25.0
PULSE_TIMING = (2.0, 20.0)
LEGACY_ARM = "legacy_affine_fixed_25s"
NO_FEED_FORWARD_ARM = "normalized_framed_no_feed_forward"
FEED_FORWARD_ARM = "normalized_framed_feed_forward"
ARM_IDS = (LEGACY_ARM, NO_FEED_FORWARD_ARM, FEED_FORWARD_ARM)
DELAY_SECONDS = (0, 1, 2)

# Measured pulse evidence establishes the selected physical scheduler contract.
SELECTED_PULSE_ARM = "linear_coupled_2s_frame_20s"
PULSE_LOW_Q = 0.01
PULSE_LOW_Q_MAX_MEAN_DUTY = 0.02
PULSE_LOW_Q_MAX_DUTY_ERROR = 0.002
PULSE_TRANSITION_MAX_PER_HOUR = 360.0

# Defined before measurements. The normalized path may tie quality only within
# this tolerance, then must improve realized-load fidelity. Feed-forward must
# improve paired reachable RMSE by this amount before it can ship.
QUALITY_TOLERANCE_F = 0.25
MIN_LOAD_ERROR_IMPROVEMENT = 0.005
MIN_FEED_FORWARD_RMSE_IMPROVEMENT_F = 0.25


def configure_arm(controller, arm):
    """Apply experiment-only private seams without changing controller config."""
    if arm not in ARM_IDS:
        raise ValueError(f"unknown arm: {arm}")
    if arm in (LEGACY_ARM, NO_FEED_FORWARD_ARM):
        controller._equilibrium_load = lambda target, disturbance: 0.0
    else:
        controller._equilibrium_load = lambda target, disturbance: steady_combustion_load(
            controller.cfg, target, disturbance
        )
    if arm == LEGACY_ARM:
        controller.actuation_mode = lambda: ActuationMode.FIXED_CYCLE


def _legacy_affine(normalized_load):
    return _old_affine(float(normalized_load)).auger_duty


def _trace_settings(value, prefix=""):
    if isinstance(value, dict):
        settings = []
        for key in sorted(value):
            name = f"{prefix}.{key}" if prefix else str(key)
            settings.extend(_trace_settings(value[key], name))
        return settings
    if isinstance(value, bool | int | float | str):
        return [TraceSetting(key=prefix, value=value)]
    return []


class _CapturedRuntimeRecorder:
    """The recorder interface used by the established Hold fake-runtime seam."""

    def __init__(self, *, warning):
        self.records = []

    def record(self, record):
        self.records.append(record)

    def flush_due(self, _now_ms):
        return None

    def close(self):
        return None


def _hold_runtime(core, runner, *, label):
    """Build actual Hold around the delayed threaded runner using its normal fake platform."""
    import controller.runtime.modes.hold as hold_module

    settings = base_settings()
    settings["controller"]["selected"] = "mpc"
    settings["controller"]["config"]["mpc"] = dict(core.cfg)
    settings["cycle_data"] = dict(core.cycle_data)
    control = base_control(mode="Hold")
    control["primary_setpoint"] = 225.0
    ctx, _grill, _notifier = make_ctx(
        settings,
        control,
        base_pellet_db(),
        FakeProbes().script([225.0] * 100),
    )
    mode = HoldMode(ctx, WorkCycleState())
    mode.settings = settings
    mode.control = control
    mode.state.manual_override = {"igniter": 0, "auger": 0, "fan": 0, "power": 0, "pwm": 0}
    recorders = []
    original_build_runner = hold_module._runner_mod.build_runner
    original_recorder = hold_module.ControlTraceRecorder

    def recorder_factory(*, warning):
        recorder = _CapturedRuntimeRecorder(warning=warning)
        recorders.append(recorder)
        return recorder

    hold_module._runner_mod.build_runner = lambda *_args, **_kwargs: (runner, "Active")
    hold_module.ControlTraceRecorder = recorder_factory
    try:
        mode.setup()
    finally:
        hold_module._runner_mod.build_runner = original_build_runner
        hold_module.ControlTraceRecorder = original_recorder
    mode.state.metrics = {"id": label}
    return mode, recorders[0]


class _RunTrace:
    """One recorder-backed SQLite session for one executed experiment run."""

    def __init__(self, *, label):
        self.session_id = str(uuid.uuid4())
        self._directory = tempfile.TemporaryDirectory(prefix="pifire-task7-trace-")
        self._database = Path(self._directory.name) / "control_trace.sqlite3"
        self._connection = sqlite3.connect(self._database)
        self._connection.executescript(_CONTROL_TRACE_DDL)
        self._connection.commit()
        self._recorder = ControlTraceRecorder(
            append=self._append,
            prune=lambda _before, *, limit: 0,
            warning=lambda message: (_ for _ in ()).throw(RuntimeError(message)),
        )
        self._label = label
        self._closed = False
        self._control_period_s = None
        self._last_solve_t = None

    def _append(self, records):
        values = [record.to_db_row() for record in records]
        self._connection.executemany(
            "INSERT INTO control_trace (ts_ms, session_id, cook_id, controller, event_kind, schema_version, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row.ts_ms,
                    row.session_id,
                    row.cook_id,
                    row.controller,
                    row.event_kind,
                    row.schema_version,
                    row.payload,
                )
                for row in values
            ],
        )
        self._connection.commit()

    def _record(self, kind, payload, ts_s):
        self._recorder.record(
            ControlTraceRecord(
                ts_ms=int(round(ts_s * 1_000)),
                session_id=self.session_id,
                cook_id=self._label,
                controller=ControllerType.MPC,
                event_kind=kind,
                payload=payload,
            )
        )

    def start(self, *, core, effective_run, control_period_s, setpoint):
        self._control_period_s = float(control_period_s)
        self._actuation_mode = ActuationMode(effective_run["actuation_mode"])
        configuration = dict(effective_run["controller_config"])
        configuration["task7.scheduler_kind"] = effective_run["actuation_mode"]
        revision = int(getattr(core, "_model_revision", 0))
        self._record(
            TraceEventKind.SESSION,
            SessionPayload(
                controller=ControllerType.MPC,
                controller_config=tuple(_trace_settings(configuration)),
                temperature_unit="F",
                control_period_seconds=float(control_period_s),
                model_revision=revision,
                model_provenance="configured",
                u_min=float(effective_run["cycle_config"]["u_min"]),
                u_max=float(effective_run["cycle_config"]["u_max"]),
                hold_cycle_seconds=float(effective_run["cycle_config"]["HoldCycleTime"]),
                # The typed MPC trace schema requires pulse authority even for
                # the legacy realization; the row's scheduler remains the
                # authoritative effective-actuation record.
                pulse_slot_seconds=PULSE_TIMING[0],
                pulse_frame_seconds=PULSE_TIMING[1],
                fan_authority=bool(configuration.get("enable_fan_input", False)),
                fan_pwm_capable=False,
                fan_min_duty=float(configuration.get("fan_min_pct", 0.0)),
                fan_max_duty=float(configuration.get("fan_max_pct", 100.0)),
                setpoint=float(setpoint),
                ambient_temperature=float(configuration.get("T_amb", 0.0)),
                software_version="task7-experiment",
                build_version="task7-experiment",
            ),
            0.0,
        )

    def solved(self, *, t, result, requested):
        diagnostics = result.diagnostics
        if diagnostics is None or result.revision < 1 or result.solve_duration_seconds is None:
            raise ValueError("executed MPC solve did not yield revisioned diagnostics")
        fan = result.fan or {}
        observed_dt = self._control_period_s if self._last_solve_t is None else t - self._last_solve_t
        self._last_solve_t = t
        requested_fan = fan.get("duty")
        self._record(
            TraceEventKind.CONTROL_UPDATE,
            MpcUpdatePayload(
                monotonic_ms=int(round(t * 1_000)),
                wall_ms=int(round(t * 1_000)),
                result_revision=result.revision,
                result_age_ms=int(round(result.result_age_seconds * 1_000)),
                control_period_seconds=float(self._control_period_s),
                observed_dt_seconds=max(0.0, float(observed_dt)),
                setpoint=float(result.status["set_point"]) if result.status and "set_point" in result.status else 0.0,
                measured_temperature=float(result.input_temperature),
                raw_output=float(result.cycle_ratio),
                requested_output=float(requested),
                actuation_mode=self._actuation_mode,
                prior_requested_auger_duty=float(diagnostics.applied_combustion_load),
                prior_realized_auger_duty=float(diagnostics.applied_combustion_load),
                requested_fan_duty=None if requested_fan is None else float(requested_fan),
                applied_fan_duty=None if requested_fan is None else float(requested_fan),
                output_source=OutputSource.CONTROLLER,
                inhibit_reason=InhibitReason.NONE,
                state_names=tuple(diagnostics.state_names),
                state_values=tuple(float(value) for value in diagnostics.state_values),
                disturbance_estimate=float(diagnostics.disturbance_estimate),
                model_revision=int(diagnostics.model_revision),
                model_provenance=str(diagnostics.model_provenance),
                raw_policy_firing_load=diagnostics.raw_policy_firing_load,
                equilibrium_feed_forward=diagnostics.equilibrium_feed_forward,
                residual_move=diagnostics.residual_move,
                bounded_firing_load=float(diagnostics.bounded_firing_load),
                policy_kind=str(diagnostics.policy_kind),
                failure_state=diagnostics.failure_state,
                solve_start_ms=int(round(t * 1_000)),
                solve_end_ms=int(round(t * 1_000)),
                deadline_miss_count=int(result.deadline_miss_count),
                stale=result.stale_state is ResultStaleState.STALE,
                recovered=bool(result.recovered),
                predicted_feasible=None
                if diagnostics.feasibility is None
                else diagnostics.feasibility.state.value == "reachable",
                predicted_steady_load=None
                if diagnostics.feasibility is None
                else diagnostics.feasibility.predicted_steady_load,
                solve_duration_ms=max(0, int(round(result.solve_duration_seconds * 1_000))),
                consecutive_deadline_miss_count=int(result.consecutive_deadline_miss_count),
                stale_state=result.stale_state,
            ),
            t,
        )
        allocation = result.allocation
        if allocation is None:
            raise ValueError("executed MPC solve did not yield allocation provenance")
        self._record(
            TraceEventKind.ALLOCATION,
            AllocationPayload(
                result_revision=result.revision,
                normalized_combustion_load=float(allocation.normalized_combustion_load),
                requested_auger_duty=float(allocation.auger_duty),
                requested_fan_duty=allocation.fan_duty,
                u_max=float(allocation.u_max),
                fan_min_pct=float(allocation.fan_min_pct),
                fan_max_pct=float(allocation.fan_max_pct),
                fan_enabled=bool(allocation.fan_enabled),
                mpc_has_fan_authority=bool(allocation.fan_enabled),
                auger_clamp_reason=allocation.auger_clamp_reason,
                fan_clamp_reason=allocation.fan_clamp_reason,
                allocator_revision=int(allocation.allocator_revision),
            ),
            t,
        )

    def frames(self, *, t, revision, frames):
        for frame in frames:
            self._record(
                TraceEventKind.ACTUATION_FRAME,
                FramedPulseFramePayload(
                    result_revision=revision,
                    pulse_slot_seconds=PULSE_TIMING[0],
                    frame_seconds=PULSE_TIMING[1],
                    frame_start_ms=int(round(frame.nominal_start_s * 1_000)),
                    frame_end_ms=int(round(frame.ended_at_s * 1_000)),
                    requested_combustion_load=float(frame.latched_request),
                    requested_auger_duty=float(frame.latched_request),
                    credit_before_seconds=float(frame.credit_before_s),
                    credit_after_seconds=float(frame.credit_after_s),
                    scheduled_on_seconds=float(frame.scheduled_on_s),
                    delivered_on_seconds=float(frame.delivered_on_s),
                    transition_count=int(frame.observed_transition_count),
                    actual_start_active=bool(frame.actual_start_on),
                    actual_end_active=bool(frame.actual_end_on),
                    requested_fan_duty=None,
                    applied_fan_duty=None,
                    skipped=bool(frame.skipped),
                    stale_command=False,
                    inhibit_reason=InhibitReason.NONE,
                    reset_reason=None if frame.reset_reason is None else frame.reset_reason.value,
                ),
                t,
            )

    def applied(
        self,
        *,
        interval_start_s,
        interval_end_s,
        result,
        requested,
        realized,
        source=OutputSource.CONTROLLER,
        sample_complete=True,
    ):
        """Persist a non-empty delivered interval, complete only when fed back to MPC."""
        start_ms = int(round(interval_start_s * 1_000))
        end_ms = int(round(interval_end_s * 1_000))
        if end_ms <= start_ms:
            raise ValueError("applied output requires a non-zero delivered interval")
        allocation = result.allocation
        if allocation is None:
            raise ValueError("applied output requires allocator provenance")
        realized_duty = float(realized)
        self._record(
            TraceEventKind.APPLIED_OUTPUT,
            AppliedOutputPayload(
                result_revision=result.revision,
                interval_start_ms=start_ms,
                interval_end_ms=end_ms,
                realized_auger_duty=realized_duty,
                realized_combustion_load=(
                    normalized_load_from_auger_duty(realized_duty, u_max=allocation.u_max)
                    if sample_complete
                    else None
                ),
                actual_fan_duty=None,
                sample_complete=sample_complete,
                output_source=source,
            ),
            interval_end_s,
        )

    def safety(self, *, t, event, reason, revision, detail):
        self._record(
            TraceEventKind.SAFETY_EVENT,
            SafetyEventPayload(event=event, inhibit_reason=reason, result_revision=revision, detail=detail),
            t,
        )

    def close(self):
        if self._closed:
            raise RuntimeError("experiment trace session closed twice")
        self._closed = True
        self._recorder.close()
        self._connection.close()
        records = read_control_trace_session(self.session_id, database_path=self._database)
        if not records or records[0].event_kind is not TraceEventKind.SESSION:
            raise ValueError("recorder session was not persisted and reread")
        if any(record.session_id != self.session_id for record in records):
            raise ValueError("reread session mixed provenance")
        requested = [
            record.payload.result_revision for record in records if record.event_kind is TraceEventKind.CONTROL_UPDATE
        ]
        allocated = [
            record.payload.result_revision for record in records if record.event_kind is TraceEventKind.ALLOCATION
        ]
        applied = [
            record.payload.result_revision for record in records if record.event_kind is TraceEventKind.APPLIED_OUTPUT
        ]
        if not requested or set(requested) != set(allocated) or not applied or not set(applied) <= set(requested):
            raise ValueError("reread session lacks requested/applied/allocation revision provenance")
        diagnostics = [
            record.payload.result_revision for record in records if record.event_kind is TraceEventKind.CONTROL_UPDATE
        ]
        model_revisions = [
            record.payload.model_revision for record in records if record.event_kind is TraceEventKind.CONTROL_UPDATE
        ]
        provenance = [
            record.payload.model_provenance for record in records if record.event_kind is TraceEventKind.CONTROL_UPDATE
        ]
        applied_payloads = [
            record.payload for record in records if record.event_kind is TraceEventKind.APPLIED_OUTPUT
        ]
        ordered_intervals = sorted(applied_payloads, key=lambda payload: payload.interval_start_ms)
        overlap_count = sum(
            current.interval_start_ms < previous.interval_end_ms
            for previous, current in zip(ordered_intervals, ordered_intervals[1:])
        )
        gap_count = sum(
            current.interval_start_ms > previous.interval_end_ms
            for previous, current in zip(ordered_intervals, ordered_intervals[1:])
        )
        positive_duration = all(
            payload.interval_end_ms > payload.interval_start_ms for payload in ordered_intervals
        )
        normalized_load_inverted = all(
            not payload.sample_complete
            or payload.realized_combustion_load
            == normalized_load_from_auger_duty(
                payload.realized_auger_duty,
                u_max=next(
                    record.payload.u_max
                    for record in records
                    if record.event_kind is TraceEventKind.ALLOCATION
                    and record.payload.result_revision == payload.result_revision
                ),
            )
            for payload in ordered_intervals
        )
        if not ordered_intervals or not positive_duration or overlap_count or gap_count or not normalized_load_inverted:
            raise ValueError("reread applied intervals are not continuous delivered MPC feedback")
        total_duration_s = sum(
            (payload.interval_end_ms - payload.interval_start_ms) / 1_000 for payload in ordered_intervals
        )
        total_delivered_s = sum(
            payload.realized_auger_duty * (payload.interval_end_ms - payload.interval_start_ms) / 1_000
            for payload in ordered_intervals
        )
        applied_interval_summary = {
            "record_count": len(ordered_intervals),
            "complete_record_count": sum(payload.sample_complete for payload in ordered_intervals),
            "positive_duration": positive_duration,
            "contiguous": overlap_count == gap_count == 0,
            "overlap_count": overlap_count,
            "gap_count": gap_count,
            "total_duration_s": total_duration_s,
            "total_delivered_on_s": total_delivered_s,
            "mean_auger_duty": total_delivered_s / total_duration_s,
            "mean_combustion_load": sum(
                (payload.realized_combustion_load or 0.0)
                * (payload.interval_end_ms - payload.interval_start_ms)
                / 1_000
                for payload in ordered_intervals
            )
            / total_duration_s,
            "normalized_load_inverted": normalized_load_inverted,
        }

        def revision_summary(values):
            unique = sorted(set(values))
            return {
                "record_count": len(values),
                "unique_count": len(unique),
                "first": unique[0],
                "last": unique[-1],
                "contiguous": unique == list(range(unique[0], unique[-1] + 1)),
            }

        summary = {
            "session_id": self.session_id,
            "record_count": len(records),
            "event_counts": dict(sorted(Counter(record.event_kind.value for record in records).items())),
            "requested_revisions": revision_summary(requested),
            "applied_revisions": revision_summary(applied),
            "diagnostic_revisions": revision_summary(diagnostics),
            "model_revisions": sorted(set(model_revisions)),
            "model_provenance": sorted(set(provenance)),
            "applied_interval_summary": applied_interval_summary,
        }
        self._directory.cleanup()
        return summary


def _effective_configuration(row, arm):
    config = row["effective_run"]
    config["scheduler"] = (
        {"kind": "fixed_cycle", "control_period_s": LEGACY_CONTROL_PERIOD_S}
        if arm == LEGACY_ARM
        else {"kind": "framed_pulse", "pulse_quantum_s": PULSE_TIMING[0], "frame_s": PULSE_TIMING[1]}
    )
    config["experiment_seams"] = {
        "Controller._equilibrium_load": "zero" if arm != FEED_FORWARD_ARM else "steady_combustion_load"
    }
    return config


def _row(arm, plant, scenario_name, seed):
    trace = _RunTrace(label=f"task7:{arm}:{plant}:{scenario_name}:{seed}")
    raw = controller_matrix.run_scenario(
        "mpc",
        SCENARIOS[scenario_name],
        seed,
        plant=plant,
        config={"control_period": LEGACY_CONTROL_PERIOD_S} if arm == LEGACY_ARM else None,
        core_setup=lambda core: configure_arm(core, arm),
        output_transform=_legacy_affine if arm == LEGACY_ARM else None,
        trace_sink=trace,
    )
    session = raw["trace_session"]
    settle = raw["settle_s"]
    durations = raw["solver_duration_seconds"]
    if not durations or not all(float(duration) >= 0 for duration in durations):
        raise ValueError("canonical run did not collect finite solver diagnostics")
    return {
        "arm": arm,
        "plant": plant,
        "scenario": scenario_name,
        "seed": seed,
        "reachability": raw["reachability"],
        "trace_session_ids": [session["session_id"]],
        "trace_session_summary": session,
        "metrics": {
            "rmse_f": raw["rmse_f"],
            "iae_f_seconds": raw["iae"],
            "overshoot_f": raw["overshoot_f"],
            "undershoot_f": raw["undershoot_f"],
            "settle_time_s": None if settle is None else float(settle),
            "pct_within_band": raw["pct_within_5f"],
            "steady_peak_to_peak_f": raw["steady_peak_to_peak_f"],
            "auger_on_time_s": raw["auger_on_time_s"],
            "pellet_proxy": raw["pellet_proxy"],
            "requested_realized_load_error": raw["requested_realized_load_error"],
            "transitions_per_hour": raw["transitions_per_hour"],
        },
        "solver": {
            "duration_s": {"min": min(durations), "mean": sum(durations) / len(durations), "max": max(durations)},
            "deadline_misses": raw["deadline_misses"],
            "stale_result_episodes": raw["stale_result_episodes"],
        },
        "safety": {"outcome": {"lid_recovery_s": raw["lid_recovery_s"]}},
        "effective_configuration": _effective_configuration(raw, arm),
    }


def _expected_keys():
    return set(product(ARM_IDS, PLANTS, SCENARIO_NAMES, SEEDS))


def _key(row):
    return row["arm"], row["plant"], row["scenario"], row["seed"]


def _require_complete_rows(rows):
    if not isinstance(rows, list):
        raise ValueError("incomplete matrix rows")
    keys = []
    for row in rows:
        arm, plant, scenario, seed, reachability, metrics, solver, safety = _required(
            row,
            ("arm", "plant", "scenario", "seed", "reachability", "metrics", "solver", "safety"),
            "matrix row",
        )
        del arm, plant, scenario, seed, reachability
        _required(metrics, ("rmse_f", "requested_realized_load_error"), "matrix metrics")
        _required(solver, ("deadline_misses", "stale_result_episodes"), "matrix solver evidence")
        (outcome,) = _required(safety, ("outcome",), "matrix safety evidence")
        _required(outcome, ("lid_recovery_s",), "matrix lid-recovery evidence")
        keys.append(_key(row))
    expected = _expected_keys()
    if len(keys) != len(expected) or set(keys) != expected or len(set(keys)) != len(keys):
        raise ValueError("incomplete matrix: every unique arm/plant/scenario/seed row is required")


def load_pulse_evidence(path=PULSE_EVIDENCE):
    """Load the committed open-loop pulse measurement used by the shipment gate."""
    try:
        evidence = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("committed pulse evidence is unreadable") from error
    if not isinstance(evidence, dict):
        raise ValueError("committed pulse evidence must be an object")
    return evidence


def _required(mapping, keys, context):
    if not isinstance(mapping, dict) or not set(keys) <= set(mapping):
        raise ValueError(f"incomplete {context}")
    return tuple(mapping[key] for key in keys)



def _require_complete_pulse_evidence(pulse_evidence):
    (open_loop,) = _required(pulse_evidence, ("open_loop",), "pulse evidence")
    if not isinstance(open_loop, list):
        raise ValueError("incomplete pulse evidence open-loop rows")
    keys = []
    for row in open_loop:
        arm, plant, q = _required(row, ("arm", "plant", "q"), "pulse measurement row")
        _required(row, ("mean_duty", "duty_error", "switches_per_hour"), "pulse measurement row")
        keys.append((arm, plant, q))
    expected = set(product(PULSE_ARMS, PLANTS, PULSE_LEVELS))
    if len(keys) != len(expected) or set(keys) != expected or len(set(keys)) != len(keys):
        raise ValueError("incomplete pulse evidence rows")
    return open_loop

def _pulse_rows(pulse_evidence, arm, q):
    open_loop = _require_complete_pulse_evidence(pulse_evidence)
    matches = [row for row in open_loop if isinstance(row, dict) and row.get("arm") == arm and row.get("q") == q]
    plants = [row.get("plant") for row in matches]
    if len(matches) != len(PLANTS) or set(plants) != set(PLANTS) or len(set(plants)) != len(plants):
        raise ValueError(f"incomplete pulse evidence for {arm} q={q}")
    for row in matches:
        _required(row, ("mean_duty", "duty_error", "switches_per_hour"), "pulse measurement row")
    return matches


def _pulse_components(pulse_evidence):
    (conditions,) = _required(pulse_evidence, ("conditions",), "pulse evidence")
    (selected_scheduler,) = _required(conditions, ("selected_scheduler",), "pulse evidence conditions")
    _required(selected_scheduler, ("pulse_quantum_s", "frame_s"), "selected pulse timing")
    selected_timing = selected_scheduler == {
        "pulse_quantum_s": PULSE_TIMING[0],
        "frame_s": PULSE_TIMING[1],
    }
    selected_low = _pulse_rows(pulse_evidence, SELECTED_PULSE_ARM, PULSE_LOW_Q)
    legacy_low = _pulse_rows(pulse_evidence, "old_affine_fixed_25s", PULSE_LOW_Q)
    selected_mid = _pulse_rows(pulse_evidence, SELECTED_PULSE_ARM, 0.5)
    legacy_by_plant = {row["plant"]: row for row in legacy_low}
    low_fire_floor_removed = all(
        0.0 <= float(row["mean_duty"]) <= PULSE_LOW_Q_MAX_MEAN_DUTY
        and abs(float(row["duty_error"])) <= PULSE_LOW_Q_MAX_DUTY_ERROR
        and float(row["mean_duty"]) < float(legacy_by_plant[row["plant"]]["mean_duty"])
        for row in selected_low
    )
    pulse_transition_envelope_respected = all(
        0.0 <= float(row["switches_per_hour"]) <= PULSE_TRANSITION_MAX_PER_HOUR for row in selected_mid
    )
    return {
        "selected_pulse_timing": selected_timing,
        "low_fire_floor_removed": low_fire_floor_removed,
        "pulse_transition_envelope_respected": pulse_transition_envelope_respected,
    }


def _require_complete_delayed_cases(cases):
    if not isinstance(cases, list):
        raise ValueError("incomplete delayed-solver evidence")
    expected = set(product(PLANTS, DELAY_SECONDS))
    keys = []
    for case in cases:
        plant, delay = _required(case, ("plant", "delay_seconds"), "delayed-solver case")
        keys.append((plant, delay))
    if len(keys) != len(expected) or set(keys) != expected or len(set(keys)) != len(keys):
        raise ValueError("incomplete delayed-solver evidence")
    required_case_fields = (
        "delay_periods",
        "hold_cadence_normal",
        "observed_control_period_s",
        "accepted_revisions",
        "single_revision_authority",
        "frame_actualizations",
        "max_stale_authority_periods",
        "stale_protection_observed",
        "preemptions",
        "warning_recovery",
        "deadline_misses",
        "trace_session_summary",
    )
    for case in cases:
        _required(case, required_case_fields, "delayed-solver case")
    return cases


def _delayed_components(cases):
    cases = _require_complete_delayed_cases(cases)
    cadence = []
    frames = []
    single_revision = []
    authority = []
    deadline_stale_sequence = []
    warning_recovery = []
    preemptions_ok = []
    for case in cases:
        delay = float(case["delay_seconds"])
        delay_periods = float(case["delay_periods"])
        cadence.append(case["hold_cadence_normal"] is True and float(case["observed_control_period_s"]) == 0.25)
        revisions = case["accepted_revisions"]
        single_revision.append(
            case["single_revision_authority"] is True
            and isinstance(revisions, list)
            and len(revisions) >= 2
            and revisions == sorted(set(revisions))
        )
        actualizations = case["frame_actualizations"]
        frames.append(
            isinstance(actualizations, list)
            and bool(actualizations)
            and all(
                isinstance(frame, dict)
                and _required(
                    frame,
                    ("at_s", "pulse_quantum_s", "revision", "requested_load", "actual_end_on"),
                    "delayed frame actualization",
                )
                and float(frame["pulse_quantum_s"]) == PULSE_TIMING[0]
                and float(frame["at_s"]) % PULSE_TIMING[1] == 0.0
                and frame["revision"] in revisions
                and 0.0 <= float(frame["requested_load"]) <= 1.0
                for frame in actualizations
            )
        )
        authority.append(
            0.0 <= float(case["max_stale_authority_periods"]) <= delay_periods
        )
        warning = case["warning_recovery"]
        _required(warning, ("stale_advisories", "recovered"), "delayed warning recovery")
        if delay == 0.0:
            deadline_stale_sequence.append(
                case["stale_protection_observed"] is False and int(case["deadline_misses"]) == 0
            )
            warning_recovery.append(int(warning["stale_advisories"]) == 0 and warning["recovered"] is False)
        else:
            deadline_stale_sequence.append(
                case["stale_protection_observed"] is True and int(case["deadline_misses"]) >= 1
            )
            warning_recovery.append(int(warning["stale_advisories"]) == 1 and warning["recovered"] is True)
        preemptions = case["preemptions"]
        if not isinstance(preemptions, dict) or set(preemptions) != {"stop", "lid", "manual"}:
            raise ValueError("incomplete delayed stop/lid/manual preemption evidence")
        for name in ("stop", "lid", "manual"):
            evidence = preemptions[name]
            _required(
                evidence,
                (
                    "command_on_after_preemption",
                    "runtime_event",
                    "recorder_safety_events",
                    "scheduler_reset_observed",
                    "interrupted_frame",
                ),
                f"delayed {name} preemption",
            )
            expected_runtime_event = {
                "stop": "stop",
                "lid": "lid_detected",
                "manual": "manual_takeover",
            }[name]
            preemptions_ok.append(
                evidence["command_on_after_preemption"] is False
                and evidence["runtime_event"] == expected_runtime_event
                and isinstance(evidence["recorder_safety_events"], int)
                and evidence["recorder_safety_events"] > 0
                and evidence["interrupted_frame"] is True
                and evidence["scheduler_reset_observed"] is True
            )
        trace = case["trace_session_summary"]
        (event_counts,) = _required(trace, ("event_counts",), "delayed trace summary")
        if not isinstance(event_counts, dict) or event_counts.get("safety_event", 0) < 3:
            raise ValueError("incomplete delayed trace safety evidence")
    return {
        "delayed_hold_cadence": all(cadence),
        "delayed_frame_actualization": all(frames),
        "delayed_single_revision_authority": all(single_revision),
        "delayed_stale_authority_bounded": all(authority),
        "delayed_deadline_stale_sequence": all(deadline_stale_sequence),
        "delayed_warning_recovery": all(warning_recovery),
        "delayed_stop_lid_manual_preemption": all(preemptions_ok),
    }


def decision_from_rows(rows, delayed_solver_cases, pulse_evidence):
    """Recompute both shipment decisions from complete committed evidence."""
    _require_complete_rows(rows)
    by_key = {(row["plant"], row["scenario"], row["seed"], row["arm"]): row for row in rows}
    capability_rows = [row for row in rows if row["scenario"] == CAPABILITY_SCENARIO]
    capability_rows_upper_unreachable = all(row["reachability"] == "unreachable_high" for row in capability_rows)
    pairs = []
    for plant, scenario, seed in product(PLANTS, SCENARIO_NAMES, SEEDS):
        legacy, normalized, feed_forward = (by_key[(plant, scenario, seed, arm)] for arm in ARM_IDS)
        if all(row["reachability"] == "reachable" for row in (legacy, normalized, feed_forward)):
            pairs.append((legacy, normalized, feed_forward))
    if not pairs:
        raise ValueError("matrix has no paired reachable rows")
    median = lambda values: sorted(values)[len(values) // 2]
    quality = median([normalized["metrics"]["rmse_f"] - legacy["metrics"]["rmse_f"] for legacy, normalized, _ in pairs])
    load = median(
        [
            legacy["metrics"]["requested_realized_load_error"] - normalized["metrics"]["requested_realized_load_error"]
            for legacy, normalized, _ in pairs
        ]
    )
    feed_forward = median([normalized["metrics"]["rmse_f"] - ff["metrics"]["rmse_f"] for _, normalized, ff in pairs])
    legacy_lid_rows = [
        row
        for row in rows
        if row["arm"] == LEGACY_ARM
        and row["scenario"] == "lid_open_225"
        and row["safety"]["outcome"]["lid_recovery_s"] is not None
    ]
    if not legacy_lid_rows:
        raise ValueError("matrix has no legacy lid-recovery evidence")
    lid_recovery_preserved = all(
        by_key[(row["plant"], row["scenario"], row["seed"], NO_FEED_FORWARD_ARM)]["safety"]["outcome"][
            "lid_recovery_s"
        ]
        is not None
        for row in legacy_lid_rows
    )
    normalized_rows = [row for row in rows if row["arm"] == NO_FEED_FORWARD_ARM]
    components = {
        **_pulse_components(pulse_evidence),
        "lid_recovery_preserved": lid_recovery_preserved,
        "normalized_deadlines_clear": all(row["solver"]["deadline_misses"] == 0 for row in normalized_rows),
        "normalized_staleness_clear": all(row["solver"]["stale_result_episodes"] == 0 for row in normalized_rows),
        "quality_comparable_or_improved": quality <= QUALITY_TOLERANCE_F,
        "applied_load_fidelity_improved": load >= MIN_LOAD_ERROR_IMPROVEMENT,
        "capability_rows_upper_unreachable": capability_rows_upper_unreachable,
        "ranked_reachable_pairs_complete": len(pairs)
        == len(PLANTS) * (len(SCENARIO_NAMES) - 1) * len(SEEDS),
        **_delayed_components(delayed_solver_cases),
    }
    scheduler_ok = all(components.values())
    feed_forward_paired_improvement = feed_forward >= MIN_FEED_FORWARD_RMSE_IMPROVEMENT_F
    components["feed_forward_paired_improvement"] = feed_forward_paired_improvement
    return {
        "ranked_reachable_pairs": len(pairs),
        "median_scheduler_rmse_delta_f": quality,
        "median_scheduler_load_error_improvement": load,
        "median_feed_forward_rmse_improvement_f": feed_forward,
        "components": components,
        "ship_normalized_scheduler": scheduler_ok,
        "ship_feed_forward": scheduler_ok and feed_forward_paired_improvement,
    }


def _wait_for(predicate, *, timeout_s=8.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.005)
    raise TimeoutError("timed out waiting for executed delayed-solver run")


def _delayed_solver_case(plant_name, delay_s):
    """Run an actual threaded solve delay while a framed last command remains live."""
    core_module = __import__("controller.mpc", fromlist=["Controller"])
    core = core_module.Controller(
        {"control_period": 0.25},
        "F",
        dict(default_settings()["cycle_data"]),
    )
    configure_arm(core, NO_FEED_FORWARD_ARM)
    plant = {"GrillSim": GrillSim, "MAKGrillSim": MAKGrillSim}[plant_name](seed=0)
    trace = _RunTrace(label=f"task7:delay:{plant_name}:{delay_s}")
    effective_run = {
        "controller_config": dict(core.cfg),
        "cycle_config": dict(core.cycle_data),
        "actuation_mode": ActuationMode.FRAMED_PULSE.value,
    }
    trace.start(core=core, effective_run=effective_run, control_period_s=0.25, setpoint=225.0)
    permits = threading.Semaphore(0)
    warnings = []
    original_policy = core._policy_residual
    delays = iter((0.0, float(delay_s)))

    def injected_policy(*args):
        pause = next(delays, 0.0)
        if pause:
            time.sleep(pause)
        return original_policy(*args)

    core._policy_residual = injected_policy
    runner = ThreadedControllerRunner(
        core,
        controller_type=ControllerType.MPC,
        warning_callback=lambda state: warnings.append(state),
        wait_for_period=lambda _period: permits.acquire(),
    )
    observed_control_period_s = float(runner.control_period())
    try:
        runner.set_target(225.0)
        runner.submit(controller_matrix._c_to_f(plant.measured()))
        permits.release()
        first = _wait_for(lambda: (result := runner.latest()).revision >= 1 and result)
        trace.solved(t=0.0, result=first, requested=first.cycle_ratio)

        mode, runtime_recorder = _hold_runtime(core, runner, label=f"task7:delay:{plant_name}:{delay_s}")
        def advance_runtime(at_s):
            delta_s = at_s - mode.ctx.clock.now()
            if delta_s < 0.0:
                raise ValueError("runtime clock must be monotone")
            if delta_s:
                mode.ctx.clock.advance(delta_s)
            mode._last_now = at_s

        advance_runtime(0.0)
        mode.state.controller.cycle_start = -0.5
        mode.on_tick(0.0, 225.0, mode.grill.get_output_status())
        advance_runtime(20.0)
        mode.on_tick(20.0, 225.0, mode.grill.get_output_status())
        advance_runtime(40.0)
        mode.on_tick(40.0, 225.0, mode.grill.get_output_status())
        runtime_frames = [
            record.payload
            for record in runtime_recorder.records
            if record.event_kind is TraceEventKind.ACTUATION_FRAME
        ]
        if not runtime_frames:
            raise AssertionError("Hold did not record a framed actualization")
        first_frame = runtime_frames[-1]
        frame_start_s = first_frame.frame_start_ms / 1_000
        frame_end_s = first_frame.frame_end_ms / 1_000
        trace.applied(
            interval_start_s=frame_start_s,
            interval_end_s=frame_end_s,
            result=first,
            requested=first.cycle_ratio,
            realized=first_frame.delivered_on_seconds / (frame_end_s - frame_start_s),
        )
        frame_actualizations = [
            {
                "at_s": payload.frame_end_ms / 1_000,
                "pulse_quantum_s": payload.pulse_slot_seconds,
                "revision": payload.result_revision,
                "requested_load": payload.requested_combustion_load,
                "actual_end_on": payload.actual_end_active,
            }
            for payload in runtime_frames
        ]

        permits.release()
        if delay_s:
            time.sleep(0.60)
        during_delay = runner.latest()
        stale_observed = during_delay.stale_state is ResultStaleState.STALE
        stale_authority_periods = (
            during_delay.result_age_seconds / 0.25
            if stale_observed and during_delay.revision == first.revision
            else 0.0
        )
        advance_runtime(40.25)
        mode.on_tick(40.25, 225.0, mode.grill.get_output_status())

        preemptions = {}

        def observe_preemption(name, runtime_event, at_s, invoke):
            before = len(runtime_recorder.records)
            invoke()
            observed = runtime_recorder.records[before:]
            safety = [
                record
                for record in observed
                if record.event_kind is TraceEventKind.SAFETY_EVENT
            ]
            reset = [
                record
                for record in safety
                if record.payload.event is SafetyEventType.SCHEDULER_RESET
            ]
            interrupted = [
                record
                for record in observed
                if record.event_kind is TraceEventKind.ACTUATION_FRAME
                and record.payload.reset_reason is not None
            ]
            command_on = mode.grill.get_output_status()["auger"]
            if command_on or not safety or not reset or not interrupted:
                raise AssertionError(
                    f"Hold {name} preemption incomplete: command_on={command_on}, "
                    f"safety={len(safety)}, reset={len(reset)}, interrupted={len(interrupted)}"
                )
            trace.safety(
                t=at_s,
                event=safety[0].payload.event,
                reason=safety[0].payload.inhibit_reason,
                revision=safety[0].payload.result_revision,
                detail=safety[0].payload.detail,
            )
            preemptions[name] = {
                "observed_at_s": at_s,
                "command_on_after_preemption": command_on,
                "interrupted_frame": bool(interrupted),
                "runtime_event": runtime_event,
                "recorder_safety_events": len(safety),
                "scheduler_reset_observed": bool(reset),
            }

        observe_preemption(
            "manual",
            "manual_takeover",
            40.25,
            lambda: mode._on_manual_output("auger", False),
        )
        advance_runtime(40.3)
        mode.on_tick(40.3, 225.0, mode.grill.get_output_status())
        mode.control["lid_open_toggle"] = True
        advance_runtime(40.5)
        observe_preemption(
            "lid",
            "lid_detected",
            40.5,
            lambda: mode.on_tick(40.5, 225.0, mode.grill.get_output_status()),
        )

        second = _wait_for(
            lambda: (result := runner.latest()).revision >= first.revision + 1 and result, timeout_s=delay_s + 5.0
        )
        trace.solved(t=40.0 + float(delay_s), result=second, requested=second.cycle_ratio)
        restart_at_s = 60.0
        advance_runtime(restart_at_s)
        mode.state.lid.open_detected = False
        mode.state.lid.expires = 0.0
        mode.on_tick(restart_at_s, 225.0, mode.grill.get_output_status())
        stop_at_s = 60.25
        advance_runtime(stop_at_s)
        observe_preemption(
            "stop",
            "stop",
            stop_at_s,
            lambda: mode.teardown(225.0),
        )

        accepted = [first.revision, second.revision]
        if accepted != sorted(set(accepted)):
            raise AssertionError("threaded runner applied a revision more than once")
        if delay_s and not stale_observed:
            raise AssertionError("injected delayed solve did not exercise stale-result protection")
        session = trace.close()
        return {
            "plant": plant_name,
            "delay_seconds": delay_s,
            "delay_periods": float(delay_s) / observed_control_period_s,
            "trace_session_ids": [session["session_id"]],
            "trace_session_summary": session,
            "hold_cadence_normal": observed_control_period_s == 0.25,
            "observed_control_period_s": observed_control_period_s,
            "accepted_revisions": accepted,
            "single_revision_authority": True,
            "frame_actualizations": frame_actualizations,
            "max_stale_authority_periods": stale_authority_periods,
            "stale_protection_observed": stale_observed,
            "preemptions": preemptions,
            "warning_recovery": {
                "stale_advisories": sum(state is ResultStaleState.STALE for state in warnings),
                "recovered": any(state is ResultStaleState.FRESH for state in warnings),
            },
            "deadline_misses": second.deadline_miss_count,
        }
    finally:
        runner.stop()


def run_delayed_solver_cases():
    cases = [_delayed_solver_case(plant, delay) for plant, delay in product(PLANTS, DELAY_SECONDS)]
    expected = set(product(PLANTS, DELAY_SECONDS))
    if {(case["plant"], case["delay_seconds"]) for case in cases} != expected:
        raise ValueError("incomplete delayed-solver evidence")
    return sorted(cases, key=lambda case: (case["plant"], case["delay_seconds"]))


def _job(job):
    return _row(*job)


def _write_checkpoint(path, rows, *, complete):
    payload = {
        "header": {
            "format_version": 3,
            "complete": complete,
            "arm_ids": ARM_IDS,
            "plants": PLANTS,
            "scenario_names": SCENARIO_NAMES,
            "seeds": SEEDS,
        },
        "rows": sorted(rows, key=_key),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")
    os.replace(temporary, path)


def _resume_rows(path):
    if not path.is_file():
        return []
    payload = json.loads(path.read_text())
    header = payload["header"]
    if header["format_version"] != 3 or header.get("complete") is True:
        return []
    if tuple(header["arm_ids"]) != ARM_IDS or tuple(header["plants"]) != PLANTS:
        raise ValueError("partial checkpoint does not describe this matrix")
    if tuple(header["scenario_names"]) != SCENARIO_NAMES or tuple(header["seeds"]) != SEEDS:
        raise ValueError("partial checkpoint has different scenarios or seeds")
    rows = payload["rows"]
    keys = [_key(row) for row in rows]
    if len(keys) != len(set(keys)) or not set(keys) <= _expected_keys():
        raise ValueError("partial checkpoint has duplicate or foreign rows")
    return rows


def run_matrix(*, checkpoint, workers, resume):
    rows = _resume_rows(checkpoint) if resume else []
    remaining = sorted(_expected_keys() - {_key(row) for row in rows})
    jobs = [(arm, plant, scenario, seed) for arm, plant, scenario, seed in remaining]
    if workers == 1:
        iterator = map(_job, jobs)
        for row in iterator:
            rows.append(row)
            _write_checkpoint(checkpoint, rows, complete=False)
    else:
        with Pool(workers) as pool:
            for row in pool.imap(_job, jobs):
                rows.append(row)
                _write_checkpoint(checkpoint, rows, complete=False)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 1))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    checkpoint = args.out.with_suffix(args.out.suffix + ".partial")
    rows = run_matrix(checkpoint=checkpoint, workers=args.workers, resume=args.resume)
    delayed_cases = run_delayed_solver_cases()
    pulse_evidence = load_pulse_evidence()
    decision = decision_from_rows(rows, delayed_cases, pulse_evidence)
    payload = {
        "header": {
            "format_version": 3,
            "complete": True,
            "regeneration_command": (
                "uv run --no-sync python docs/superpowers/experiments/mpc_feed_forward.py "
                "--workers 8 --out docs/superpowers/experiments/_mpc_feed_forward.json"
            ),
            "decision_rule": {
                "quality_tolerance_f": QUALITY_TOLERANCE_F,
                "minimum_load_error_improvement": MIN_LOAD_ERROR_IMPROVEMENT,
                "minimum_feed_forward_rmse_improvement_f": MIN_FEED_FORWARD_RMSE_IMPROVEMENT_F,
            },
            "matrix": {"arms": ARM_IDS, "plants": PLANTS, "scenarios": SCENARIO_NAMES, "seeds": SEEDS},
        },
        "rows": rows,
        "summary": {"run_count": len(rows), "decision": decision},
        "delayed_solver_cases": delayed_cases,
        "pulse_evidence": pulse_evidence,
    }
    _require_complete_rows(payload["rows"])
    _require_complete_delayed_cases(payload["delayed_solver_cases"])
    _pulse_components(payload["pulse_evidence"])
    temporary = args.out.with_suffix(args.out.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")
    os.replace(temporary, args.out)
    checkpoint.unlink(missing_ok=True)
    print(
        f"WROTE {args.out}: {len(rows)} rows; scheduler ship={decision['ship_normalized_scheduler']}; "
        f"feed-forward ship={decision['ship_feed_forward']}; delayed cases={len(delayed_cases)}"
    )


if __name__ == "__main__":
    main()
