"""Empirical production-path comparison for opt-in online scheduled ARX.

The simulator matrix deliberately uses the same production controller, allocator,
and framed-pulse scheduler as Hold.  It is evidence generation, not a second
controller implementation.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from math import isfinite, sqrt
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import numpy as np

from controller.applied_output import AppliedOutput, OutputSource
from controller.grill_sim import GrillSim, MAKGrillSim
from controller.linear_mpc.arx import ScheduledARX, ScheduledARXConfig
from controller.linear_mpc.contracts import FrameObservation
from controller.linear_mpc.grey_box import GreyBoxPredictionAdapter
from controller.mpc import Controller
from controller.mpc_allocator import normalized_load_from_auger_duty
from controller.runtime.logic.pulse import PulseResetReason, PulseScheduler
from controller.runtime.runner import ThreadedControllerRunner
from docs.superpowers.experiments.linear_mpc_bakeoff.runner import (
    frame_seconds,
    real_mak_record,
    record_frames,
)
from docs.superpowers.experiments.linear_mpc_bakeoff.scenarios import ScenarioDefinition


ARTIFACT_SCHEMA_VERSION = 1
CONTROLLER_ARMS = ("baseline", "online")
PLANTS = ("GrillSim", "MAKGrillSim")
SCENARIOS = (
    "cold-start",
    "hold",
    "target-increase",
    "target-decrease-coast",
    "lid-interruption",
)
FIXED_SEEDS = (0, 1, 2)
REQUIRED_METRICS = frozenset(
    {
        "pct_within_5f",
        "overshoot_f",
        "settle_s",
        "rmse_f",
        "steady_peak_to_peak_f",
        "auger_on_s",
        "transitions_per_hour",
        "requested_realized_load_error",
        "deadline_misses",
        "stale_result_episodes",
        "prediction_rmse_60_c",
        "prediction_rmse_300_c",
        "braking_error_c",
        "promotions",
        "rollbacks",
    }
)

_DEFAULT_OUTPUT = Path("docs/superpowers/experiments/_online_arx_compare.json")
_TIMING_BUDGETS_MS = {"learner": 5.0, "evaluation": 250.0, "solve": 50.0}
_SIMULATOR_STACK = {
    "mpc": "Controller",
    "scheduled_arx": "ScheduledARX",
    "linear_policy": "LinearMPC",
    "allocator": "allocate",
    "pulse_scheduler": "PulseScheduler",
    "runner": "ThreadedControllerRunner",
}
_REAL_MAK_STACK = {
    "baseline": {"controller": "Controller", "prediction_model": "GreyBoxPredictionAdapter"},
    "online": {
        "controller": "Controller",
        "prediction_model": "ScheduledARX",
        "scheduled_arx_config": {"na": 2, "nb": 2, "delays": [1, 2, 3], "initial_covariance": 10.0},
    },
}
_PRECONDITION_MAX_S = 1_800
_PRECONDITION_HOLD_S = 60
_PREDICTION_METRICS = frozenset({"prediction_rmse_60_c", "prediction_rmse_300_c"})

_SOURCE_REVISION_HEX = frozenset("0123456789abcdef")


def _valid_source_revision(value: object) -> bool:
    """Require an immutable reviewed Git revision without normalizing it."""
    return isinstance(value, str) and len(value) == 40 and all(character in _SOURCE_REVISION_HEX for character in value)


def _valid_timing_environment(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == {"classification", "platform", "machine", "model", "source"}
        and value.get("classification") in {"target-device", "workstation"}
        and isinstance(value.get("platform"), str)
        and bool(value["platform"].strip())
        and isinstance(value.get("machine"), str)
        and bool(value["machine"].strip())
        and (
            (value.get("classification") == "workstation" and value.get("model") is None)
            or (
                value.get("classification") == "target-device"
                and isinstance(value.get("model"), str)
                and bool(value["model"].strip())
            )
        )
        and value.get("source") == "runtime-detected"
    )


_CONTROL_METRICS = REQUIRED_METRICS - _PREDICTION_METRICS


def _timing_environment() -> dict[str, str | None]:
    """Describe the runtime that produced timing evidence."""
    model = None
    for candidate in (
        Path("/sys/firmware/devicetree/base/model"),
        Path("/proc/device-tree/model"),
    ):
        try:
            detected = candidate.read_text(encoding="utf-8").replace("\x00", "").strip()
        except OSError:
            continue
        if detected.startswith("Raspberry Pi"):
            model = detected
            break
    machine = platform.machine().strip() or "unknown"
    return {
        "classification": "target-device" if model is not None else "workstation",
        "platform": platform.system().strip() or sys.platform,
        "machine": machine,
        "model": model,
        "source": "runtime-detected",
    }


def _scenario_definitions() -> dict[str, ScenarioDefinition]:
    """Return the fixed named schedules used by every simulator cell."""
    return {
        "cold-start": ScenarioDefinition("cold-start", 90.0, 90.0),
        "hold": ScenarioDefinition("hold", 110.0, 110.0),
        "target-increase": ScenarioDefinition("target-increase", 80.0, 135.0, step_at_s=240),
        "target-decrease-coast": ScenarioDefinition("target-decrease-coast", 135.0, 80.0, step_at_s=300),
        "lid-interruption": ScenarioDefinition("lid-interruption", 110.0, 110.0, lid_start_s=300, lid_duration_s=45),
    }


def _controller(*, online: bool) -> Controller:
    """Construct the shipping MPC with only its documented online switch changed."""
    repository = Path(__file__).parents[3]
    return Controller(
        {
            "control_period": float(frame_seconds()),
            "t_step": float(frame_seconds()),
            "policy": "net",
            "policy_net_path": str(repository / "controller" / "mpc_policy_net.npz"),
            "enable_online_adaptation": online,
        },
        "C",
        {"u_max": 0.9},
    )


def _frame_observation(
    *,
    frame: Any,
    temperature_c: float,
    target_c: float,
    ambient_c: float,
    requested_duty: float,
    source: OutputSource,
    lid_open: bool,
    maximum_duty: float,
    generation: int,
    result_revision: int,
    stale: bool,
) -> FrameObservation:
    duration = frame.ended_at_s - frame.nominal_start_s
    reset = frame.reset_reason is not None
    unsafe_source = source is not OutputSource.CONTROLLER
    return FrameObservation(
        frame_start_s=frame.nominal_start_s,
        frame_end_s=frame.ended_at_s,
        temp_c=float(temperature_c),
        setpoint_c=float(target_c),
        ambient_c=float(ambient_c),
        requested_q=normalized_load_from_auger_duty(requested_duty, u_max=maximum_duty),
        realized_q=normalized_load_from_auger_duty(
            frame.delivered_on_s / duration if duration else 0.0,
            u_max=maximum_duty,
        ),
        requested_auger_duty=float(requested_duty),
        delivered_on_s=float(frame.delivered_on_s),
        requested_fan_duty=1.0,
        actual_fan_duty=1.0,
        result_revision=int(result_revision),
        output_source=source.value,
        lid_open=lid_open,
        safety_inhibited=False,
        manual_override=source is OutputSource.MANUAL_OVERRIDE,
        stale=stale,
        skipped=frame.skipped,
        reset=reset,
        continuous=frame.complete
        and not frame.skipped
        and not lid_open
        and not reset
        and not stale
        and not unsafe_source,
        role_generation=generation,
    )


def _p99(values: Sequence[float]) -> float:
    return float(np.percentile(values, 99.0)) if values else 0.0


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value))


class _WorkerGate:
    """Deterministically release one worker loop and observe its next wait."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._arrivals = 0
        self._released = 0
        self._closed = False

    def __call__(self, _period_s: float) -> None:
        with self._condition:
            self._arrivals += 1
            self._condition.notify_all()
            self._condition.wait_for(lambda: self._closed or self._released >= self._arrivals)

    def advance(self) -> None:
        with self._condition:
            expected = self._released + 1
            if not self._condition.wait_for(lambda: self._arrivals >= expected, timeout=2.0):
                raise RuntimeError("threaded controller worker did not reach deterministic gate")
            self._released = expected
            self._condition.notify_all()
            if not self._condition.wait_for(lambda: self._arrivals >= expected + 1, timeout=2.0):
                raise RuntimeError("threaded controller worker did not complete deterministic loop")

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


@dataclass(frozen=True, slots=True)
class _PredictionOrigin:
    model: GreyBoxPredictionAdapter | ScheduledARX
    q_previous: float
    frame_index: int


def _active_prediction_origin(controller: Controller, *, q_previous: float, frame_index: int) -> _PredictionOrigin:
    """Freeze the active public production model after one completed frame."""
    active_kind = controller.get_status()["adaptation"]["active_model_kind"]
    if active_kind == "scheduled-arx":
        snapshot = controller.get_model_snapshot()
        if snapshot is None:
            raise RuntimeError("scheduled-ARX controller published no model snapshot")
        incumbent = snapshot["online_adaptation"]["incumbent"]
        model = ScheduledARX.from_snapshot(incumbent)
    elif active_kind == "grey-box":
        model = GreyBoxPredictionAdapter.from_controller(controller)
    else:
        raise RuntimeError(f"unknown active production model {active_kind!r}")
    return _PredictionOrigin(model=model, q_previous=float(q_previous), frame_index=frame_index)


def _eligible_continuity_frame(frame: FrameObservation) -> bool:
    """Reject every discontinuity even when a malformed row claims continuity."""
    return (
        frame.continuous
        and not frame.lid_open
        and not frame.safety_inhibited
        and not frame.manual_override
        and not frame.stale
        and not frame.skipped
        and not frame.reset
        and frame.output_source
        in {
            OutputSource.CONTROLLER.value,
            "requested-input-reconstruction",
            "bakeoff-record",
        }
    )


def _origin_prediction_scores(
    origins: Sequence[_PredictionOrigin], frames: Sequence[FrameObservation]
) -> dict[str, float]:
    """Score exact-horizon terminal residuals from immutable origins only."""
    scores: dict[str, float] = {}
    for horizon_s in (60, 300):
        horizon = horizon_s // frame_seconds()
        terminal_residuals: list[float] = []
        for origin in origins:
            if not 0 <= origin.frame_index < len(frames):
                continue
            origin_frame = frames[origin.frame_index]
            future = frames[origin.frame_index + 1 : origin.frame_index + 1 + horizon]
            window = [origin_frame, *future]
            if (
                len(future) != horizon
                or not all(_eligible_continuity_frame(frame) for frame in window)
                or not all(frame.frame_end_s - frame.frame_start_s == frame_seconds() for frame in window)
                or not all(left.frame_end_s == right.frame_start_s for left, right in zip(window, window[1:]))
            ):
                continue
            affine = origin.model.affine_prediction(
                horizon,
                origin.q_previous,
                [frame.ambient_c for frame in future],
            )
            prediction = affine.free_output_c + affine.input_response_c @ np.asarray(
                [frame.realized_q for frame in future],
                dtype=float,
            )
            terminal_residuals.append(float(prediction[-1] - future[-1].temp_c))
        if not terminal_residuals:
            raise RuntimeError(f"no supported {horizon_s}-second prediction origins")
        scores[f"prediction_rmse_{horizon_s}_c"] = float(sqrt(mean(value * value for value in terminal_residuals)))
    return scores


def _control_metrics(
    *,
    temperatures: Sequence[float],
    targets: Sequence[float],
    requested: Sequence[float],
    realized: Sequence[float],
    transitions: int,
    duration_s: int,
    auger_on_s: float,
    predictions: Mapping[str, float],
    promotions: int,
    rollbacks: int,
    braking_errors: Sequence[float],
    deadline_misses: int,
    stale_episodes: int,
) -> tuple[dict[str, float | None], dict[str, bool]]:
    error_c = np.asarray(temperatures, dtype=float) - np.asarray(targets, dtype=float)
    error_f = error_c * 9.0 / 5.0
    within = np.abs(error_f) <= 5.0
    settled = next((index for index in range(len(error_f)) if bool(np.all(within[index:]))), None)
    hold = np.asarray(temperatures[-min(60, len(temperatures)) :], dtype=float) * 9.0 / 5.0
    metrics: dict[str, float | None] = {
        "pct_within_5f": float(np.mean(within) * 100.0),
        "overshoot_f": float(max(0.0, np.max(error_f))),
        "settle_s": None if settled is None else float(settled),
        "rmse_f": float(sqrt(np.mean(error_f**2))),
        "steady_peak_to_peak_f": float(np.ptp(hold)),
        "auger_on_s": float(auger_on_s),
        "transitions_per_hour": float(transitions * 3600.0 / duration_s),
        "requested_realized_load_error": float(
            np.mean(np.abs(np.asarray(requested, dtype=float) - np.asarray(realized, dtype=float)))
        ),
        "deadline_misses": float(deadline_misses),
        "stale_result_episodes": float(stale_episodes),
        "prediction_rmse_60_c": float(predictions["prediction_rmse_60_c"]),
        "prediction_rmse_300_c": float(predictions["prediction_rmse_300_c"]),
        "braking_error_c": float(mean(braking_errors)) if braking_errors else None,
        "promotions": float(promotions),
        "rollbacks": float(rollbacks),
    }
    return metrics, {"settled": settled is not None, "braking_error_c": bool(braking_errors)}


def _timing_applicability(*, simulator: bool, online: bool) -> dict[str, bool]:
    return {
        "learner": online,
        "evaluation": simulator and online,
        "solve": simulator or not online,
    }


def _initialize_reheat_equilibrium(simulator: GrillSim, target_c: float) -> float:
    """Prepare an unscored fixed-fan equilibrium and return its steady auger duty."""
    target = float(target_c)
    fan_multiplier = 1.3  # fixed fan is 1.0: 0.6 + 0.7 * fan
    h_fc = simulator.h_fc0 * fan_multiplier
    h_amb = simulator.h_amb0 * fan_multiplier
    radiation_loss = simulator.sigma * ((target + 273.15) ** 4 - (simulator.T_amb + 273.15) ** 4)
    loss = h_amb * (target - simulator.T_amb) + radiation_loss
    steady_burn = loss / simulator.H
    simulator.T_c = simulator.T_meas = target
    simulator.T_f = target + loss / h_fc
    simulator.fuel = steady_burn / (simulator.k_burn * 1.1)
    transit_steps = len(simulator.transit)
    simulator.transit.clear()
    simulator.transit.extend([steady_burn] * transit_steps)
    return steady_burn / simulator.feed_rate


def _failed_row(*, arm: str, plant: str, scenario: str, seed: int, error: Exception) -> dict[str, Any]:
    return {
        "cell_key": f"{arm}:{plant}:{scenario}:{seed}",
        "arm": arm,
        "plant": plant,
        "scenario": scenario,
        "seed": seed,
        "status": "failed",
        "failure": {"reason": f"{type(error).__name__}: {error}"},
        "metrics": {name: None for name in sorted(REQUIRED_METRICS)},
        "metric_applicability": {"settled": False, "braking_error_c": False},
        "raw_timing_ms": {"learner": [], "evaluation": [], "solve": []},
        "timing_applicability": _timing_applicability(simulator=True, online=arm == "online"),
        "online_chronology": [],
        "runner_evidence": {
            "deadline_miss_count": None,
            "stale_state_transitions": [],
            "result_revisions": [],
            "statuses": [],
            "policy_failure_counts": [],
        },
        "preconditioning": {"applied": scenario != "cold-start", "duration_s": None, "hold_established": False},
        "outcomes": {"safety_inhibits": None, "unreachable_setpoints": None},
        "outcome_evidence": {"safety_inhibits": "unavailable", "unreachable_setpoints": "unavailable"},
        "production_stack": dict(_SIMULATOR_STACK),
        "actual_delivered_load_feedback": True,
    }


def _run_simulator_cell(
    *, arm: str, plant: str, scenario: ScenarioDefinition, seed: int, duration_s: int
) -> dict[str, Any]:
    plant_type = {"GrillSim": GrillSim, "MAKGrillSim": MAKGrillSim}[plant]
    initial_target = scenario.target_at(0)
    simulator_kwargs: dict[str, Any] = {"seed": seed, "fixed_fan": 1.0}
    if scenario.name != "cold-start" and plant == "MAKGrillSim":
        simulator_kwargs["T0"] = initial_target
    simulator = plant_type(**simulator_kwargs)
    steady_reheat_duty = (
        _initialize_reheat_equilibrium(simulator, initial_target) if scenario.name != "cold-start" else 0.0
    )
    controller = _controller(online=arm == "online")
    runner_gate = _WorkerGate()
    runner = ThreadedControllerRunner(controller, wait_for_period=runner_gate)
    timing: dict[str, list[float]] = {"learner": [], "evaluation": [], "solve": []}
    chronology: list[dict[str, Any]] = []
    runner_evidence: dict[str, Any] = {
        "deadline_miss_count": 0,
        "stale_state_transitions": [],
        "result_revisions": [],
        "statuses": [],
        "policy_failure_counts": [],
    }
    pending_scored_observations: list[tuple[FrameObservation, int]] = []
    pending_submission_indices: dict[int, int] = {}
    interval_on_s = 0.0
    interval_lid_open = False
    temperatures: list[float] = []
    cumulative_delivered_on_s = 0.0
    feedback_start_s = 0
    feedback_delivered_on_s = 0.0
    targets: list[float] = []
    requested_loads: list[float] = []
    realized_loads: list[float] = []
    completed: list[FrameObservation] = []
    origins: list[_PredictionOrigin] = []
    prediction_frames: list[FrameObservation] = []
    transitions = 0
    unreachable_setpoints = 0
    prior_target = initial_target
    actual_on = False
    last_request = 0.0
    scored_auger_on_s = 0.0

    def record_result(result: Any, *, frame_index: int, scored: bool, chronology_event: str = "runner-result") -> None:
        nonlocal last_request
        if result.revision <= 0 or result.allocation is None:
            raise RuntimeError("threaded Controller runner did not publish a completed production allocation")
        last_request = float(result.allocation.auger_duty)
        if result.solve_duration_seconds is None:
            raise RuntimeError("threaded Controller runner omitted solve duration")
        timing["solve"].append(float(result.solve_duration_seconds) * 1_000.0)
        runner_evidence["result_revisions"].append(int(result.revision))
        active_model = (result.status or {}).get("adaptation", {}).get("active_model_kind")
        runner_evidence["statuses"].append(
            active_model if active_model in {"grey-box", "scheduled-arx"} else "model-unavailable"
        )
        runner_evidence["deadline_miss_count"] = int(result.deadline_miss_count)
        policy_failures = (result.status or {}).get("policy_failures")
        if not isinstance(policy_failures, int) or policy_failures < 0:
            raise RuntimeError("threaded Controller runner omitted policy failure evidence")
        runner_evidence["policy_failure_counts"].append(policy_failures)
        stale_state = result.stale_state.value
        transitions_seen = runner_evidence["stale_state_transitions"]
        if not transitions_seen or transitions_seen[-1] != stale_state:
            transitions_seen.append(stale_state)
        if scored:
            chronology.append(
                {
                    "frame_index": frame_index,
                    "event": chronology_event,
                    "revision": int(result.revision),
                    "stale_state": stale_state,
                }
            )

    def advance_worker(
        *,
        temperature_c: float,
        frame_index: int,
        scored: bool,
        chronology_event: str = "runner-result",
    ) -> Any:
        prior_revision = runner.latest().revision
        runner.submit(float(temperature_c))
        runner_gate.advance()
        result = runner.latest()
        if result.revision != prior_revision + 1:
            raise RuntimeError("threaded Controller runner failed deterministic revision handoff")
        chronology_frame_index = pending_scored_observations[0][1] if pending_scored_observations else frame_index
        record_result(
            result,
            frame_index=chronology_frame_index,
            scored=scored,
            chronology_event=chronology_event,
        )
        process_due_observations(result)
        return result

    def process_due_observations(result: Any) -> None:
        nonlocal unreachable_setpoints
        status = result.status or {}
        adaptation = status.get("adaptation", {})
        if arm == "online":
            drain = runner.drain_observation_outcomes()
            if drain.dropped_count:
                raise RuntimeError("threaded Controller runner dropped online observation outcome")
            for envelope in drain:
                frame_index = pending_submission_indices.get(envelope.submission_sequence)
                if frame_index is None:
                    continue
                learner_duration = adaptation.get("learner_duration_seconds")
                if learner_duration is not None:
                    timing["learner"].append(float(learner_duration) * 1_000.0)
                outcome = envelope.outcome
                if not isinstance(outcome, Mapping):
                    continue
                if outcome.get("evaluation") is not None:
                    evaluation_duration = adaptation.get("evaluation_duration_seconds")
                    if evaluation_duration is not None:
                        timing["evaluation"].append(float(evaluation_duration) * 1_000.0)
                    chronology.append(
                        {
                            "frame_index": frame_index,
                            "event": "evaluation",
                            "revision": int(result.revision),
                            "stale_state": result.stale_state.value,
                        }
                    )
                lifecycle = outcome.get("lifecycle")
                if isinstance(lifecycle, Mapping):
                    chronology.append(
                        {
                            "frame_index": frame_index,
                            "event": "promotion" if lifecycle.get("event") == "adopt" else "rollback",
                            "revision": int(result.revision),
                            "stale_state": result.stale_state.value,
                        }
                    )
        for _observation, _frame_index in pending_scored_observations:
            feasibility = status.get("feasibility")
            if isinstance(feasibility, Mapping) and str(feasibility.get("state", "")).startswith("unreachable"):
                unreachable_setpoints += 1
        pending_scored_observations.clear()
        pending_submission_indices.clear()

    def freeze_prediction_origin(result: Any, frame: FrameObservation) -> None:
        prediction_frames.append(frame)
        if _eligible_continuity_frame(frame):
            origins.append(
                _active_prediction_origin(
                    controller,
                    q_previous=frame.realized_q,
                    frame_index=len(prediction_frames) - 1,
                )
            )

    def cadence_frame(
        *,
        end_s: int,
        temperature_c: float,
        source: OutputSource,
        delivered_on_s: float,
        requested_duty: float,
        lid_open: bool,
        result: Any,
    ) -> FrameObservation:
        duration = float(frame_seconds())
        return FrameObservation(
            frame_start_s=float(end_s) - duration,
            frame_end_s=float(end_s),
            temp_c=temperature_c,
            setpoint_c=float(prior_target),
            ambient_c=float(simulator.T_amb),
            requested_q=normalized_load_from_auger_duty(requested_duty, u_max=controller.u_max),
            realized_q=normalized_load_from_auger_duty(delivered_on_s / duration, u_max=controller.u_max),
            requested_auger_duty=float(requested_duty),
            delivered_on_s=float(delivered_on_s),
            requested_fan_duty=1.0,
            actual_fan_duty=1.0,
            result_revision=int(result.revision),
            output_source=source.value,
            lid_open=lid_open,
            safety_inhibited=False,
            manual_override=False,
            stale=result.stale_state.value == "stale",
            skipped=False,
            reset=False,
            continuous=not lid_open and result.stale_state.value != "stale",
            role_generation=int((result.status or {}).get("adaptation", {}).get("role_generation", 0)),
        )

    def consume_frame(
        frame: Any,
        *,
        temperature_c: float,
        source: OutputSource,
        lid_open: bool,
        scored: bool,
        command_target: float,
    ) -> None:
        current = runner.latest()
        observation = _frame_observation(
            frame=frame,
            temperature_c=temperature_c,
            target_c=prior_target,
            ambient_c=simulator.T_amb,
            requested_duty=frame.latched_request,
            source=source,
            lid_open=lid_open,
            maximum_duty=controller.u_max,
            generation=int((current.status or {}).get("adaptation", {}).get("role_generation", 0)),
            result_revision=int(current.revision),
            stale=current.stale_state.value == "stale",
        )
        # Applied output belongs to the independent controller cadence, not to
        # a shifted pulse frame.  The due tick reports its accumulated interval.
        runner.set_target(command_target)
        submission = runner.observe_frame(observation)
        if submission is None:
            raise RuntimeError("threaded Controller runner refused completed frame")
        if scored:
            frame_index = len(completed)
            completed.append(observation)
            pending_scored_observations.append((observation, frame_index))
            pending_submission_indices[submission.submission_sequence] = frame_index
            if observation.continuous:
                requested_loads.append(observation.requested_q)
                realized_loads.append(observation.realized_q)

    def precondition() -> dict[str, Any]:
        if scenario.name == "cold-start":
            return {"applied": False, "duration_s": 0, "hold_established": False}
        scheduler = PulseScheduler()
        precondition_on = False
        for second in range(_PRECONDITION_HOLD_S):
            decision = scheduler.advance(steady_reheat_duty, float(second), precondition_on)
            if decision.transition is not None:
                precondition_on = decision.transition.command_on
            simulator.step(precondition_on, 1.0, lid_open=False)
            if abs(float(simulator.measured()) - initial_target) * 9.0 / 5.0 > 5.0:
                raise RuntimeError("bounded preconditioning drifted outside the initial-setpoint hold")
        return {
            "applied": True,
            "duration_s": _PRECONDITION_HOLD_S,
            "hold_established": True,
        }

    try:
        preconditioning = precondition()
        tick_temp = float(simulator.measured())
        runner.set_target(prior_target)
        advance_worker(temperature_c=tick_temp, frame_index=-1, scored=False)
        scheduler = PulseScheduler()
        actual_on = False
        last_lid = False
        negative_transition_index: int | None = None
        for second in range(duration_s):
            if second > 0:
                tick_temp = float(simulator.measured())
            target = scenario.target_at(second)
            lid_open = scenario.lid_open_at(second)
            if lid_open and not last_lid:
                was_on_before_lid = actual_on
                if second > feedback_start_s:
                    runner.set_output(
                        AppliedOutput(
                            ratio=(cumulative_delivered_on_s - feedback_delivered_on_s) / (second - feedback_start_s),
                            source=OutputSource.CONTROLLER,
                            timestamp=float(second),
                            requested=last_request,
                        )
                    )
                    feedback_start_s = second
                    feedback_delivered_on_s = cumulative_delivered_on_s
                runner.set_output(
                    AppliedOutput(
                        ratio=0.0,
                        source=OutputSource.LID_OPEN,
                        timestamp=float(second),
                        requested=last_request,
                    )
                )
                interruption = scheduler.advance(last_request, float(second), actual_on)
                for frame in interruption.completed_frames:
                    consume_frame(
                        frame,
                        temperature_c=tick_temp,
                        source=OutputSource.CONTROLLER,
                        lid_open=False,
                        scored=True,
                        command_target=target,
                    )
                if was_on_before_lid:
                    transitions += 1
                interrupted = scheduler.reset(PulseResetReason.LID)
                if interrupted is not None and interrupted.ended_at_s > interrupted.nominal_start_s:
                    consume_frame(
                        interrupted,
                        temperature_c=tick_temp,
                        source=OutputSource.LID_OPEN,
                        lid_open=True,
                        scored=True,
                        command_target=target,
                    )
                actual_on = False
            elif not lid_open and last_lid:
                scheduler = PulseScheduler()
                actual_on = False
            if not lid_open:
                decision = scheduler.advance(last_request, float(second), actual_on)
                if decision.transition is not None:
                    transitions += 1
                    actual_on = decision.transition.command_on
                for frame in decision.completed_frames:
                    consume_frame(
                        frame,
                        temperature_c=tick_temp,
                        source=OutputSource.CONTROLLER,
                        lid_open=False,
                        scored=True,
                        command_target=target,
                    )
            runner.set_target(target)
            if second > 0 and second % frame_seconds() == 0:
                interval_source = OutputSource.LID_OPEN if interval_lid_open else OutputSource.CONTROLLER
                interval_request = last_request
                if not lid_open:
                    runner.set_output(
                        AppliedOutput(
                            ratio=(cumulative_delivered_on_s - feedback_delivered_on_s) / (second - feedback_start_s),
                            source=OutputSource.CONTROLLER,
                            timestamp=float(second),
                            requested=last_request,
                        )
                    )
                    feedback_start_s = second
                    feedback_delivered_on_s = cumulative_delivered_on_s
                result = advance_worker(
                    temperature_c=tick_temp,
                    frame_index=len(completed),
                    scored=True,
                )
                freeze_prediction_origin(
                    result,
                    cadence_frame(
                        end_s=second,
                        temperature_c=tick_temp,
                        source=interval_source,
                        delivered_on_s=interval_on_s,
                        requested_duty=interval_request,
                        lid_open=interval_lid_open,
                        result=result,
                    ),
                )
                interval_on_s = 0.0
                interval_lid_open = False
            simulator.step(actual_on and not lid_open, 1.0, lid_open=lid_open)
            cumulative_delivered_on_s += float(actual_on and not lid_open)
            temperatures.append(tick_temp)
            interval_on_s += float(actual_on and not lid_open)
            scored_auger_on_s += float(actual_on and not lid_open)
            interval_lid_open = interval_lid_open or lid_open
            targets.append(float(target))
            if second and target < scenario.target_at(second - 1):
                negative_transition_index = second
            prior_target = target
            last_lid = lid_open
        tail_temp = float(simulator.measured())
        tail = scheduler.advance(last_request, float(duration_s), actual_on) if not last_lid else None
        if tail is not None:
            for frame in tail.completed_frames:
                consume_frame(
                    frame,
                    temperature_c=tail_temp,
                    source=OutputSource.CONTROLLER,
                    lid_open=False,
                    scored=True,
                    command_target=prior_target,
                )
        if duration_s % frame_seconds() == 0:
            runner.set_target(prior_target)
            if not last_lid:
                runner.set_output(
                    AppliedOutput(
                        ratio=(cumulative_delivered_on_s - feedback_delivered_on_s) / (duration_s - feedback_start_s),
                        source=OutputSource.CONTROLLER,
                        timestamp=float(duration_s),
                        requested=last_request,
                    )
                )
                feedback_start_s = duration_s
                feedback_delivered_on_s = cumulative_delivered_on_s
            interval_source = OutputSource.LID_OPEN if interval_lid_open else OutputSource.CONTROLLER
            interval_request = last_request
            result = advance_worker(
                temperature_c=tail_temp,
                frame_index=len(completed),
                scored=True,
            )
            freeze_prediction_origin(
                result,
                cadence_frame(
                    end_s=duration_s,
                    temperature_c=tail_temp,
                    source=interval_source,
                    delivered_on_s=interval_on_s,
                    lid_open=interval_lid_open,
                    requested_duty=interval_request,
                    result=result,
                ),
            )
            interval_on_s = 0.0
            interval_lid_open = False
        predictions = _origin_prediction_scores(origins, prediction_frames)
        adaptation = controller.get_status()["adaptation"]
        braking_errors = (
            [
                abs(temperature - target)
                for temperature, target in zip(
                    temperatures[negative_transition_index:],
                    targets[negative_transition_index:],
                )
            ]
            if negative_transition_index is not None
            else []
        )
        metrics, applicability = _control_metrics(
            temperatures=temperatures,
            targets=targets,
            requested=requested_loads,
            realized=realized_loads,
            transitions=transitions,
            duration_s=duration_s,
            auger_on_s=scored_auger_on_s,
            predictions=predictions,
            promotions=int(adaptation["promotion_count"]),
            rollbacks=int(adaptation["rollback_count"]),
            braking_errors=braking_errors,
            deadline_misses=int(runner_evidence["deadline_miss_count"]),
            stale_episodes=sum(state == "stale" for state in runner_evidence["stale_state_transitions"]),
        )
        if not requested_loads:
            raise RuntimeError("no continuous completed frames for requested-realized load evidence")
        return {
            "cell_key": f"{arm}:{plant}:{scenario.name}:{seed}",
            "arm": arm,
            "plant": plant,
            "scenario": scenario.name,
            "seed": seed,
            "status": "completed",
            "failure": None,
            "metrics": metrics,
            "metric_applicability": applicability,
            "raw_timing_ms": timing,
            "timing_applicability": _timing_applicability(simulator=True, online=arm == "online"),
            "online_chronology": chronology,
            "runner_evidence": runner_evidence,
            "preconditioning": preconditioning,
            "outcomes": {"safety_inhibits": None, "unreachable_setpoints": unreachable_setpoints},
            "outcome_evidence": {"safety_inhibits": "unavailable", "unreachable_setpoints": "measured"},
            "production_stack": dict(_SIMULATOR_STACK),
            "actual_delivered_load_feedback": True,
        }
    finally:
        runner.stop()
        runner_gate.close()


def run_tiny_grillsim() -> list[dict[str, Any]]:
    """Run both production controller arms through a short GrillSim cell."""
    return [
        _run_simulator_cell(
            arm=arm, plant="GrillSim", scenario=_scenario_definitions()["cold-start"], seed=0, duration_s=360
        )
        for arm in CONTROLLER_ARMS
    ]


def run_tiny_mak_grillsim() -> list[dict[str, Any]]:
    """Run both production controller arms through a short MAKGrillSim cell."""
    return [
        _run_simulator_cell(
            arm=arm, plant="MAKGrillSim", scenario=_scenario_definitions()["cold-start"], seed=0, duration_s=360
        )
        for arm in CONTROLLER_ARMS
    ]


def _chronological_real_mak_row(arm: str) -> dict[str, Any]:
    """Replay historical requested inputs without making control claims."""
    record = real_mak_record()
    frames = record_frames(record)
    controller = _controller(online=arm == "online")
    shadow = ScheduledARX(ScheduledARXConfig(na=2, nb=2, delays=(1, 2, 3), initial_covariance=10.0))
    history_limit = max(
        shadow.config.na + 1,
        max(shadow.config.delays) + shadow.config.nb + 1,
    )
    timing: dict[str, list[float]] = {"learner": [], "evaluation": [], "solve": []}
    replayed: list[FrameObservation] = []
    origins: list[_PredictionOrigin] = []
    origin_indices: list[int] = []
    input_transform = {
        "source": "reconstructed_auger_duty",
        "operation": "normalized_load_from_auger_duty",
        "u_max": float(controller.u_max),
        "applied_once": True,
    }
    if len(frames) != len(record.q):
        raise RuntimeError("real-MAK frame reconstruction changed the record length")
    for index, frame in enumerate(frames):
        reconstructed_duty = float(record.q[index])
        normalized_q = normalized_load_from_auger_duty(reconstructed_duty, u_max=controller.u_max)
        controller.set_output(
            AppliedOutput(
                ratio=reconstructed_duty,
                source=OutputSource.SEED,
                timestamp=frame.frame_end_s,
                requested=normalized_q,
            )
        )
        controller.set_target(frame.setpoint_c)
        controller.update(frame.temp_c)
        if arm == "baseline":
            diagnostics = controller.trace_diagnostics()
            if diagnostics is not None:
                timing["solve"].append(float(diagnostics.solve_duration_seconds) * 1_000.0)
        observation = replace(
            frame,
            requested_q=normalized_q,
            realized_q=normalized_q,
            output_source="requested-input-reconstruction",
            role_generation=0,
        )
        replayed.append(observation)
        if arm == "online":
            started = perf_counter()
            if index < history_limit:
                shadow.track(observation)
            else:
                shadow.observe(observation)
            timing["learner"].append((perf_counter() - started) * 1_000.0)
        horizon = 300 // frame_seconds()
        if (
            index < history_limit
            or index + horizon >= len(frames)
            or not all(_eligible_continuity_frame(frame) for frame in frames[index + 1 : index + 1 + horizon])
        ):
            continue
        origin_indices.append(index)
        if arm == "online":
            origin = _PredictionOrigin(
                model=ScheduledARX.from_snapshot(shadow.snapshot()),
                q_previous=observation.realized_q,
                frame_index=len(replayed) - 1,
            )
        else:
            origin = _active_prediction_origin(
                controller,
                q_previous=observation.realized_q,
                frame_index=len(replayed) - 1,
            )
        origins.append(origin)
    prediction_metrics = _origin_prediction_scores(origins, replayed)
    metrics = {name: None for name in sorted(_CONTROL_METRICS)}
    metrics.update(prediction_metrics)
    return {
        "cell_key": f"{arm}:real-MAK:chronological-replay",
        "arm": arm,
        "plant": "real-MAK",
        "scenario": "chronological-replay",
        "status": "completed",
        "failure": None,
        "metrics": metrics,
        "unavailable_metrics": sorted(_CONTROL_METRICS),
        "input_provenance": "requested-input-reconstruction",
        "input_transform": input_transform,
        "prediction_origins": {
            "warmup_frames": history_limit,
            "origin_frame_indices": origin_indices,
            "origin_count": len(origin_indices),
        },
        "actual_delivered_load_feedback": False,
        "raw_timing_ms": timing,
        "timing_applicability": _timing_applicability(simulator=False, online=arm == "online"),
        "production_stack": dict(_REAL_MAK_STACK[arm]),
    }


def run_real_mak_replay() -> list[dict[str, Any]]:
    """Replay both arms against chronological real-MAK prediction evidence."""
    return [_chronological_real_mak_row(arm) for arm in CONTROLLER_ARMS]


def _control_score(rows: Sequence[Mapping[str, Any]]) -> float | None:
    completed = [row for row in rows if row["status"] == "completed"]
    if not completed:
        return None
    return float(
        mean(
            float(row["metrics"]["rmse_f"])
            + float(row["metrics"]["overshoot_f"])
            + 0.1 * float(row["metrics"]["steady_peak_to_peak_f"])
            for row in completed
        )
    )


def _aggregate(rows: Sequence[Mapping[str, Any]], arm: str) -> dict[str, float | None]:
    arm_rows = [row for row in rows if row["arm"] == arm]
    return {"control_score": _control_score(arm_rows)}


def _expected_simulator_keys() -> set[tuple[str, str, str, int]]:
    return {
        (arm, plant, scenario, seed)
        for arm in CONTROLLER_ARMS
        for plant in PLANTS
        for scenario in SCENARIOS
        for seed in FIXED_SEEDS
    }


def artifact_contract_errors(artifact: Mapping[str, Any], *, require_ship_decision: bool = True) -> list[str]:
    """Return every strict schema/evidence violation without repairing evidence."""
    errors: list[str] = []
    allowed = {
        "schema_version",
        "source_revision",
        "requested",
        "rows",
        "real_mak_rows",
        "timing_budgets_ms",
        "timing_environment",
        "aggregates",
        "ship_decision",
    }
    fields = set(artifact)
    if (require_ship_decision and fields != allowed) or (
        not require_ship_decision and fields not in (allowed, allowed - {"ship_decision"})
    ):
        errors.append("top-level fields are incomplete")
    if artifact.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        errors.append("schema version")
    if not _valid_source_revision(artifact.get("source_revision")):
        errors.append("source revision")
    expected_requested = {
        "seeds": list(FIXED_SEEDS),
        "plants": list(PLANTS),
        "scenarios": list(SCENARIOS),
        "controller_arms": list(CONTROLLER_ARMS),
    }
    if artifact.get("requested") != expected_requested:
        errors.append("requested matrix")
    budgets = artifact.get("timing_budgets_ms")
    if (
        not isinstance(budgets, Mapping)
        or set(budgets) != {"learner", "evaluation", "solve"}
        or not all(_finite_number(value) and value > 0 for value in budgets.values())
    ):
        errors.append("timing budgets")
    if not _valid_timing_environment(artifact.get("timing_environment")):
        errors.append("timing environment")
    rows = artifact.get("rows")
    if not isinstance(rows, list):
        return [*errors, "rows"]
    keys: set[tuple[str, str, str, int]] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            errors.append("row is not an object")
            continue
        key = (row.get("arm"), row.get("plant"), row.get("scenario"), row.get("seed"))
        if key in keys:
            errors.append("duplicate simulator row")
        keys.add(key)
        if key not in _expected_simulator_keys() or row.get("cell_key") != ":".join(map(str, key)):
            errors.append("simulator row identity")
        _validate_simulator_row(row, errors)
    if keys != _expected_simulator_keys():
        errors.append("incomplete simulator matrix")
    real_rows = artifact.get("real_mak_rows")
    if (
        not isinstance(real_rows, list)
        or {row.get("arm") for row in real_rows if isinstance(row, Mapping)} != set(CONTROLLER_ARMS)
        or len(real_rows) != len(CONTROLLER_ARMS)
    ):
        errors.append("real-MAK matrix")
    else:
        for row in real_rows:
            _validate_real_mak_row(row, errors)
        by_arm = {
            row["arm"]: row for row in real_rows if isinstance(row, Mapping) and row.get("arm") in CONTROLLER_ARMS
        }
        if set(by_arm) == set(CONTROLLER_ARMS) and (
            by_arm["baseline"].get("prediction_origins") != by_arm["online"].get("prediction_origins")
        ):
            errors.append("real-MAK common prediction origins")
    aggregates = artifact.get("aggregates")
    if not isinstance(aggregates, Mapping) or set(aggregates) != set(CONTROLLER_ARMS):
        errors.append("aggregates")
    else:
        for arm in CONTROLLER_ARMS:
            score = aggregates[arm].get("control_score") if isinstance(aggregates[arm], Mapping) else None
            completed = any(
                isinstance(row, Mapping) and row.get("arm") == arm and row.get("status") == "completed" for row in rows
            )
            if (
                not isinstance(aggregates[arm], Mapping)
                or (completed and not _finite_number(score))
                or (not completed and score is not None)
            ):
                errors.append("aggregate control scores")
    decision = artifact.get("ship_decision")
    if require_ship_decision:
        if (
            not isinstance(decision, Mapping)
            or set(decision) != {"ship", "reasons"}
            or not isinstance(decision["ship"], bool)
            or not isinstance(decision["reasons"], list)
            or not all(isinstance(reason, str) for reason in decision["reasons"])
        ):
            errors.append("ship decision")
        elif decision != decide_ship(artifact):
            errors.append("ship decision is not recomputed")
    try:
        json.dumps(artifact, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except TypeError, ValueError:
        errors.append("non-finite strict JSON")
    return errors


def _validate_timing(
    value: object, applicability: object, expected: Mapping[str, bool], status: object, errors: list[str]
) -> None:
    if applicability != expected:
        errors.append("timing applicability")
    if not isinstance(value, Mapping) or set(value) != set(expected):
        errors.append("raw timings")
        return
    for name, samples in value.items():
        if not isinstance(samples, list) or not all(_finite_number(sample) and sample >= 0 for sample in samples):
            errors.append("raw timing samples")
        elif status == "completed" and expected[name] and not samples:
            errors.append(f"missing applicable {name} timing evidence")


def _validate_online_chronology(value: object, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append("online chronology")
        return
    prior = -1
    for event in value:
        if (
            not isinstance(event, Mapping)
            or set(event) != {"frame_index", "event", "revision", "stale_state"}
            or not isinstance(event["frame_index"], int)
            or event["frame_index"] < prior
            or not isinstance(event["event"], str)
            or not isinstance(event["revision"], int)
            or event["revision"] < 0
            or event["stale_state"] not in {"fresh", "stale"}
        ):
            errors.append("online chronology")
            return
        prior = event["frame_index"]


def _validate_simulator_row(row: Mapping[str, Any], errors: list[str]) -> None:
    allowed = {
        "cell_key",
        "arm",
        "plant",
        "scenario",
        "seed",
        "status",
        "failure",
        "metrics",
        "metric_applicability",
        "raw_timing_ms",
        "timing_applicability",
        "online_chronology",
        "runner_evidence",
        "preconditioning",
        "outcomes",
        "outcome_evidence",
        "production_stack",
        "actual_delivered_load_feedback",
    }
    if set(row) != allowed:
        errors.append("simulator row fields")
    status = row.get("status")
    if (
        status not in {"completed", "failed"}
        or (status == "completed") != (row.get("failure") is None)
        or (status == "failed" and not isinstance(row.get("failure"), Mapping))
    ):
        errors.append("simulator row failure")
    metric_applicability = row.get("metric_applicability")
    if (
        not isinstance(metric_applicability, Mapping)
        or set(metric_applicability) != {"settled", "braking_error_c"}
        or not all(isinstance(value, bool) for value in metric_applicability.values())
    ):
        errors.append("metric applicability")
        metric_applicability = {}
    metrics = row.get("metrics")
    if not isinstance(metrics, Mapping) or set(metrics) != REQUIRED_METRICS:
        errors.append("simulator metrics")
    elif status == "completed":
        for name, value in metrics.items():
            nullable = (name == "settle_s" and not metric_applicability.get("settled")) or (
                name == "braking_error_c" and not metric_applicability.get("braking_error_c")
            )
            if (nullable and value is not None) or (not nullable and not _finite_number(value)):
                errors.append("simulator metric applicability")
                break
    elif not all(value is None or _finite_number(value) for value in metrics.values()):
        errors.append("failed simulator metrics")
    _validate_timing(
        row.get("raw_timing_ms"),
        row.get("timing_applicability"),
        _timing_applicability(simulator=True, online=row.get("arm") == "online"),
        status,
        errors,
    )
    _validate_online_chronology(row.get("online_chronology"), errors)
    runner_evidence = row.get("runner_evidence")
    if (
        not isinstance(runner_evidence, Mapping)
        or set(runner_evidence)
        != {
            "deadline_miss_count",
            "stale_state_transitions",
            "result_revisions",
            "statuses",
            "policy_failure_counts",
        }
        or (
            status == "completed"
            and (
                not isinstance(runner_evidence["deadline_miss_count"], int)
                or runner_evidence["deadline_miss_count"] < 0
                or not all(
                    isinstance(value, str) and value in {"fresh", "stale"}
                    for value in runner_evidence["stale_state_transitions"]
                )
                or not runner_evidence["result_revisions"]
                or not all(isinstance(value, int) and value > 0 for value in runner_evidence["result_revisions"])
                or not runner_evidence["statuses"]
                or not all(
                    isinstance(value, str) and value in {"grey-box", "scheduled-arx", "model-unavailable"}
                    for value in runner_evidence["statuses"]
                )
                or not runner_evidence["policy_failure_counts"]
                or not all(isinstance(value, int) and value >= 0 for value in runner_evidence["policy_failure_counts"])
            )
        )
    ):
        errors.append("runner evidence")
    elif status == "completed" and (
        metrics["deadline_misses"] != float(runner_evidence["deadline_miss_count"])
        or metrics["stale_result_episodes"]
        != float(sum(value == "stale" for value in runner_evidence["stale_state_transitions"]))
    ):
        errors.append("runner quality evidence mismatch")
    preconditioning = row.get("preconditioning")
    expected_preconditioned = row.get("scenario") != "cold-start"
    if (
        not isinstance(preconditioning, Mapping)
        or set(preconditioning) != {"applied", "duration_s", "hold_established"}
        or preconditioning.get("applied") is not expected_preconditioned
        or not isinstance(preconditioning.get("hold_established"), bool)
        or (
            status == "completed"
            and (
                (
                    expected_preconditioned
                    and (
                        not preconditioning["hold_established"]
                        or not isinstance(preconditioning["duration_s"], int)
                        or preconditioning["duration_s"] <= 0
                    )
                )
                or (
                    not expected_preconditioned
                    and (preconditioning["hold_established"] or preconditioning["duration_s"] != 0)
                )
            )
        )
        or (status == "failed" and (preconditioning["hold_established"] or preconditioning["duration_s"] is not None))
    ):
        errors.append("preconditioning evidence")
    outcomes = row.get("outcomes")
    outcome_evidence = row.get("outcome_evidence")
    if (
        not isinstance(outcomes, Mapping)
        or set(outcomes) != {"safety_inhibits", "unreachable_setpoints"}
        or not isinstance(outcome_evidence, Mapping)
        or set(outcome_evidence) != {"safety_inhibits", "unreachable_setpoints"}
    ):
        errors.append("simulator outcomes")
    elif status == "completed":
        safety_evidence = outcome_evidence["safety_inhibits"]
        valid_safety = (safety_evidence == "unavailable" and outcomes["safety_inhibits"] is None) or (
            safety_evidence == "measured"
            and isinstance(outcomes["safety_inhibits"], int)
            and outcomes["safety_inhibits"] >= 0
        )
        if (
            outcome_evidence["unreachable_setpoints"] != "measured"
            or not valid_safety
            or not isinstance(outcomes["unreachable_setpoints"], int)
            or outcomes["unreachable_setpoints"] < 0
        ):
            errors.append("simulator outcomes")
    elif (
        outcome_evidence != {"safety_inhibits": "unavailable", "unreachable_setpoints": "unavailable"}
        or outcomes["safety_inhibits"] is not None
        or outcomes["unreachable_setpoints"] is not None
    ):
        errors.append("simulator outcomes")
    if row.get("production_stack") != _SIMULATOR_STACK or row.get("actual_delivered_load_feedback") is not True:
        errors.append("production path evidence")


def _validate_real_mak_row(row: Mapping[str, Any], errors: list[str]) -> None:
    allowed = {
        "cell_key",
        "arm",
        "plant",
        "scenario",
        "status",
        "failure",
        "metrics",
        "unavailable_metrics",
        "input_provenance",
        "input_transform",
        "prediction_origins",
        "actual_delivered_load_feedback",
        "raw_timing_ms",
        "timing_applicability",
        "production_stack",
    }
    if not isinstance(row, Mapping) or set(row) != allowed:
        errors.append("real-MAK row fields")
        return
    status = row.get("status")
    if (
        row.get("arm") not in CONTROLLER_ARMS
        or row.get("plant") != "real-MAK"
        or row.get("scenario") != "chronological-replay"
        or row.get("cell_key") != f"{row.get('arm')}:real-MAK:chronological-replay"
        or status not in {"completed", "failed"}
        or (status == "completed") != (row.get("failure") is None)
    ):
        errors.append("real-MAK identity or failure")
    metrics = row.get("metrics")
    if not isinstance(metrics, Mapping) or set(metrics) != REQUIRED_METRICS:
        errors.append("real-MAK metrics")
    elif any(metrics[name] is not None for name in _CONTROL_METRICS):
        errors.append("real-MAK unavailable metrics")
    elif status == "completed" and not all(_finite_number(metrics[name]) for name in _PREDICTION_METRICS):
        errors.append("real-MAK prediction metrics")
    elif status == "failed" and not all(
        metrics[name] is None or _finite_number(metrics[name]) for name in _PREDICTION_METRICS
    ):
        errors.append("failed real-MAK prediction metrics")
    if row.get("unavailable_metrics") != sorted(_CONTROL_METRICS):
        errors.append("real-MAK unavailable metric list")
    if (
        row.get("input_provenance") != "requested-input-reconstruction"
        or row.get("input_transform")
        != {
            "source": "reconstructed_auger_duty",
            "operation": "normalized_load_from_auger_duty",
            "u_max": 0.9,
            "applied_once": True,
        }
        or row.get("actual_delivered_load_feedback") is not False
    ):
        errors.append("real-MAK input provenance")
    origins = row.get("prediction_origins")
    if (
        not isinstance(origins, Mapping)
        or set(origins) != {"warmup_frames", "origin_frame_indices", "origin_count"}
        or not isinstance(origins["warmup_frames"], int)
        or origins["warmup_frames"] < 1
        or not isinstance(origins["origin_frame_indices"], list)
        or not all(
            isinstance(value, int) and value >= origins["warmup_frames"] for value in origins["origin_frame_indices"]
        )
        or origins["origin_count"] != len(origins["origin_frame_indices"])
        or (status == "completed" and not origins["origin_frame_indices"])
    ):
        errors.append("real-MAK prediction origins")
    _validate_timing(
        row.get("raw_timing_ms"),
        row.get("timing_applicability"),
        _timing_applicability(simulator=False, online=row.get("arm") == "online"),
        status,
        errors,
    )
    if row.get("production_stack") != _REAL_MAK_STACK.get(row.get("arm")):
        errors.append("real-MAK prediction stack")


def decide_ship(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Make the conservative decision from pre-decision evidence only."""
    reasons = artifact_contract_errors(artifact, require_ship_decision=False)
    if reasons:
        return {"ship": False, "reasons": sorted(set(reasons))}
    rows = artifact["rows"]
    real_rows = artifact["real_mak_rows"]
    timing_environment = artifact["timing_environment"]
    if (
        timing_environment["classification"] != "target-device"
        or not isinstance(timing_environment["model"], str)
        or not timing_environment["model"].strip()
    ):
        reasons.append("target timing unavailable")
    if any(row["status"] != "completed" for row in rows) or any(row["status"] != "completed" for row in real_rows):
        reasons.append("incomplete requested cell")
        return {"ship": False, "reasons": sorted(set(reasons))}
    if any(row["outcome_evidence"]["safety_inhibits"] != "measured" for row in rows):
        reasons.append("safety inhibition evidence unavailable")
    for row in rows:
        evidence = row["runner_evidence"]
        if "model-unavailable" in evidence["statuses"]:
            reasons.append("runner model status unavailable")
        if evidence["deadline_miss_count"] > 0:
            reasons.append("runner deadline miss evidence")
        if any(state == "stale" for state in evidence["stale_state_transitions"]):
            reasons.append("runner stale result evidence")
    baseline = {(row["plant"], row["scenario"], row["seed"]): row for row in rows if row["arm"] == "baseline"}
    online = {(row["plant"], row["scenario"], row["seed"]): row for row in rows if row["arm"] == "online"}
    if not float(artifact["aggregates"]["online"]["control_score"]) < float(
        artifact["aggregates"]["baseline"]["control_score"]
    ):
        reasons.append("online control score is not strictly better")
    for key in sorted(baseline):
        before, after = baseline[key], online[key]
        if (
            before["outcome_evidence"]["unreachable_setpoints"] == "measured"
            and after["outcome_evidence"]["unreachable_setpoints"] == "measured"
            and after["outcomes"]["unreachable_setpoints"] > before["outcomes"]["unreachable_setpoints"]
        ):
            reasons.append("online reachability regression")
        if any(count > 0 for count in after["runner_evidence"]["policy_failure_counts"]) or max(
            after["runner_evidence"]["policy_failure_counts"]
        ) > max(before["runner_evidence"]["policy_failure_counts"]):
            reasons.append("online policy failure regression")
        for metric, reason in (
            ("transitions_per_hour", "relay transition"),
            ("stale_result_episodes", "stale result"),
            ("deadline_misses", "deadline"),
            ("requested_realized_load_error", "requested-realized load"),
        ):
            if float(after["metrics"][metric]) > float(before["metrics"][metric]):
                reasons.append(f"online {reason} regression")
    real = {row["arm"]: row for row in real_rows}
    for metric in sorted(_PREDICTION_METRICS):
        if float(real["online"]["metrics"][metric]) > float(real["baseline"]["metrics"][metric]):
            reasons.append(f"real-MAK {metric} regression")
    for row in [*rows, *real_rows]:
        for name, limit in artifact["timing_budgets_ms"].items():
            if row["timing_applicability"][name] and _p99(row["raw_timing_ms"][name]) > float(limit):
                reasons.append(f"{name} p99 budget")
    return {"ship": not reasons, "reasons": sorted(set(reasons))}


def run_comparison(*, source_revision: str, duration_s: int = 1_800) -> dict[str, Any]:
    """Run the full fixed simulator matrix and chronological real-MAK replay."""
    if not _valid_source_revision(source_revision):
        raise ValueError("source revision must be a lowercase 40-hex commit")
    if duration_s < 2 * frame_seconds():
        raise ValueError("duration_s must cover at least two pulse frames")
    definitions = _scenario_definitions()
    rows: list[dict[str, Any]] = []
    for arm in CONTROLLER_ARMS:
        for plant in PLANTS:
            for scenario_name in SCENARIOS:
                for seed in FIXED_SEEDS:
                    try:
                        rows.append(
                            _run_simulator_cell(
                                arm=arm,
                                plant=plant,
                                scenario=definitions[scenario_name],
                                seed=seed,
                                duration_s=duration_s,
                            )
                        )
                    except Exception as error:
                        rows.append(_failed_row(arm=arm, plant=plant, scenario=scenario_name, seed=seed, error=error))
    real_rows: list[dict[str, Any]] = []
    for arm in CONTROLLER_ARMS:
        try:
            real_rows.append(_chronological_real_mak_row(arm))
        except Exception as error:
            metrics = {name: None for name in sorted(REQUIRED_METRICS)}
            real_rows.append(
                {
                    "cell_key": f"{arm}:real-MAK:chronological-replay",
                    "arm": arm,
                    "plant": "real-MAK",
                    "scenario": "chronological-replay",
                    "status": "failed",
                    "failure": {"reason": f"{type(error).__name__}: {error}"},
                    "metrics": metrics,
                    "unavailable_metrics": sorted(_CONTROL_METRICS),
                    "input_provenance": "requested-input-reconstruction",
                    "input_transform": {
                        "source": "reconstructed_auger_duty",
                        "operation": "normalized_load_from_auger_duty",
                        "u_max": 0.9,
                        "applied_once": True,
                    },
                    "prediction_origins": {
                        "warmup_frames": max(3, 3 + 2 + 1),
                        "origin_frame_indices": [],
                        "origin_count": 0,
                    },
                    "actual_delivered_load_feedback": False,
                    "raw_timing_ms": {"learner": [], "evaluation": [], "solve": []},
                    "timing_applicability": _timing_applicability(simulator=False, online=arm == "online"),
                    "production_stack": dict(_REAL_MAK_STACK[arm]),
                }
            )
    artifact = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "source_revision": source_revision,
        "requested": {
            "seeds": list(FIXED_SEEDS),
            "plants": list(PLANTS),
            "scenarios": list(SCENARIOS),
            "controller_arms": list(CONTROLLER_ARMS),
        },
        "rows": sorted(rows, key=lambda row: (row["arm"], row["plant"], row["scenario"], row["seed"])),
        "real_mak_rows": sorted(real_rows, key=lambda row: row["arm"]),
        "timing_budgets_ms": dict(_TIMING_BUDGETS_MS),
        "timing_environment": _timing_environment(),
        "aggregates": {arm: _aggregate(rows, arm) for arm in CONTROLLER_ARMS},
    }
    artifact["ship_decision"] = decide_ship(artifact)
    return artifact


def write_artifact_atomically(artifact: Mapping[str, Any], output: Path) -> None:
    """Durably publish canonical strict JSON only after validating the full artifact."""
    errors = artifact_contract_errors(artifact)
    if errors:
        raise ValueError("invalid artifact: " + "; ".join(errors))
    if "ship_decision" in artifact and artifact["ship_decision"] != decide_ship(artifact):
        raise ValueError("invalid artifact: stored ship decision is stale")
    canonical = json.dumps(artifact, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(canonical)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
        directory = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def load_artifact(path: Path | None = None, *, expected_source_revision: str | None = None) -> dict[str, Any]:
    """Load strict experiment evidence, optionally bound to a reviewed revision."""
    selected = _DEFAULT_OUTPUT if path is None else Path(path)
    artifact = json.loads(selected.read_text(encoding="utf-8"))
    errors = artifact_contract_errors(artifact)
    if errors:
        raise ValueError("invalid artifact: " + "; ".join(errors))
    if expected_source_revision is not None:
        if not _valid_source_revision(expected_source_revision):
            raise ValueError("expected source revision must be a lowercase 40-hex commit")
        if artifact["source_revision"] != expected_source_revision:
            raise ValueError("artifact source revision does not match expected source revision")
    if "ship_decision" in artifact and artifact["ship_decision"] != decide_ship(artifact):
        raise ValueError("invalid artifact: stored ship decision is stale")
    return artifact


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare production MPC with opt-in online scheduled ARX.")
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT, help="strict JSON artifact path")
    parser.add_argument("--tiny", action="store_true", help="run the full matrix at a six-minute scenario duration")
    parser.add_argument(
        "--source-revision",
        required=True,
        help="immutable reviewed lowercase 40-hex source revision",
    )
    args = parser.parse_args(argv)
    artifact = run_comparison(
        source_revision=args.source_revision,
        duration_s=360 if args.tiny else 1_800,
    )
    write_artifact_atomically(artifact, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
