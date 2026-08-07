"""Conservative production-model evidence for the innovation state-space challenger.

This experiment deliberately does not select a controller model.  Both model arms
consume the same completed ``FrameObservation`` stream and command schedule;
therefore a successful artifact is evidence, not a runtime configuration change.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from math import isfinite, sqrt
from pathlib import Path
from time import perf_counter
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import numpy as np

from controller.applied_output import AppliedOutput, OutputSource
from controller.grill_sim import GrillSim, MAKGrillSim
from controller.linear_mpc.arx import ScheduledARX, ScheduledARXConfig
from controller.linear_mpc.contracts import FrameObservation
from controller.linear_mpc.state_space import (
    InnovationStateSpace,
    RefreshDiagnostics,
    RefreshRejectionReason,
    StateSpaceConfig,
)
from controller.linear_mpc.adaptation import OnlineAdaptation
from controller.mpc import Controller
from controller.mpc_allocator import normalized_load_from_auger_duty
from controller.runtime.logic.pulse import PulseScheduler
from docs.superpowers.experiments.linear_mpc_bakeoff.runner import frame_seconds, real_mak_record, record_frames

ARTIFACT_SCHEMA_VERSION = 3
ARMS = ("scheduled-arx", "innovation-state-space")
SIMULATOR_PLANTS = ("GrillSim", "MAKGrillSim")
MISMATCHES = ("wrong-delay", "wrong-pole", "wrong-gain")
FIXED_SEEDS = (0, 1, 2)
_REFRESH_BUDGET_MS = 250.0
_SOLVE_BUDGET_MS = 50.0
_DEFAULT_OUTPUT = Path("docs/superpowers/experiments/_state_space_online_compare.json")
_HEX = frozenset("0123456789abcdef")


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in _HEX for character in value)


def _valid_source_revision(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(character in _HEX for character in value)


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value))


def _model_digest(model: object) -> str:
    return OnlineAdaptation.model_digest(model)


def _instance_digest(model: object) -> str:
    return hashlib.sha256(f"{id(model)}".encode()).hexdigest()


def _command_owner_status(owner: Controller) -> dict[str, int | str]:
    adaptation = owner.get_status().get("adaptation")
    if not isinstance(adaptation, Mapping):
        raise RuntimeError("controller did not report adaptation status")
    active_model_kind = adaptation.get("active_model_kind")
    role_generation = adaptation.get("role_generation")
    if active_model_kind != "scheduled-arx" or not isinstance(role_generation, int) or role_generation < 0:
        raise RuntimeError("experiment command owner is not the scheduled-ARX incumbent")
    return {"active_model_kind": active_model_kind, "role_generation": role_generation}


def _snapshot_diagnostic(snapshot: Mapping[str, object], *, kind: str) -> dict[str, Any] | None:
    diagnostics = snapshot.get("diagnostics")
    if not isinstance(diagnostics, Mapping):
        return None
    attempts = diagnostics.get("attempts")
    if not isinstance(attempts, (list, tuple)):
        return None
    return {
        "kind": kind,
        "accepted": diagnostics.get("accepted"),
        "terminal_reason": diagnostics.get("terminal_reason"),
        "selected_order": diagnostics.get("selected_order"),
        "selected_delay": diagnostics.get("selected_delay"),
        "attempts": [dict(attempt) if isinstance(attempt, Mapping) else attempt for attempt in attempts],
    }


def _mismatch_config(mismatch: str) -> tuple[dict[str, float | int], dict[str, float | int]]:
    nominal = {"n_delay": 8, "theta": 50.0, "K_Q": 350.0}
    parameter, value = {
        "wrong-delay": ("n_delay", 2),
        "wrong-pole": ("theta", 180.0),
        "wrong-gain": ("K_Q", 90.0),
    }[mismatch]
    return {parameter: value}, {parameter: nominal[parameter]}


def _controller(*, mismatch: str, challenger: str | None) -> Controller:
    changed, _ = _mismatch_config(mismatch)
    repository = Path(__file__).resolve().parents[3]
    return Controller(
        {
            "control_period": float(frame_seconds()),
            "t_step": float(frame_seconds()),
            "policy": "net",
            "policy_net_path": str(repository / "controller" / "mpc_policy_net.npz"),
            "enable_online_adaptation": True,
            **changed,
        },
        "C",
        {"u_max": 0.9},
        _online_challenger_kind=challenger,
    )


def _control_metrics(temperatures: Sequence[float], targets: Sequence[float], duration_s: int) -> dict[str, float]:
    errors = [temperature - target for temperature, target in zip(temperatures, targets, strict=True)]
    rmse_f = sqrt(sum(error * error for error in errors) / len(errors)) * 9.0 / 5.0
    overshoot_f = max(0.0, max(errors)) * 9.0 / 5.0
    changed_at = next((index for index in range(1, len(targets)) if targets[index] != targets[index - 1]), 0)
    tolerance_c = 5.0 * 5.0 / 9.0
    settled = next(
        (
            index
            for index in range(changed_at, len(errors))
            if all(abs(value) <= tolerance_c for value in errors[index:])
        ),
        None,
    )
    settle_s = float(duration_s if settled is None else (settled - changed_at) * frame_seconds())
    return {"rmse_f": rmse_f, "overshoot_f": overshoot_f, "settle_s": settle_s}


def _frames_digest(frames: Sequence[FrameObservation]) -> str:
    """Hash exact emitted frame provenance without retaining duplicate payloads."""
    payload = json.dumps([asdict(frame) for frame in frames], sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _arx_calibration_prefix(
    *, simulator: GrillSim | MAKGrillSim, scheduler: PulseScheduler, u_max: float, frame_count: int = 48
) -> tuple[list[FrameObservation], float, bool]:
    """Emit a bounded calibration prefix through the selected simulator and pulse scheduler."""
    if frame_count <= 0:
        raise ValueError("frame_count must be positive")
    frames: list[FrameObservation] = []
    inputs = (0.05, 0.25, 0.7, 0.4, 0.85, 0.15, 0.55, 0.3)
    actual_on = False
    request = inputs[0]
    second = 0
    while len(frames) < frame_count:
        decision = scheduler.advance(request, float(second), actual_on)
        if decision.transition is not None:
            actual_on = decision.transition.command_on
        simulator.step(actual_on, 1.0, lid_open=False)
        for completed in decision.completed_frames:
            duration = completed.ended_at_s - completed.nominal_start_s
            applied_duty = float(completed.delivered_on_s) / duration if duration else 0.0
            frames.append(
                FrameObservation(
                    frame_start_s=float(completed.nominal_start_s),
                    frame_end_s=float(completed.ended_at_s),
                    temp_c=float(simulator.measured()),
                    setpoint_c=80.0,
                    ambient_c=float(simulator.T_amb),
                    requested_q=normalized_load_from_auger_duty(request, u_max=u_max),
                    realized_q=normalized_load_from_auger_duty(applied_duty, u_max=u_max),
                    requested_auger_duty=request,
                    delivered_on_s=float(completed.delivered_on_s),
                    requested_fan_duty=1.0,
                    actual_fan_duty=1.0,
                    result_revision=len(frames) + 1,
                    output_source="controller",
                    lid_open=False,
                    safety_inhibited=False,
                    manual_override=False,
                    stale=False,
                    skipped=False,
                    reset=False,
                    continuous=True,
                    role_generation=0,
                )
            )
            if len(frames) == frame_count:
                break
        if decision.completed_frames and len(frames) < frame_count:
            request = inputs[len(frames) % len(inputs)]
        second += 1
    return frames, float(second), actual_on


def _simulator_frames(
    *, plant: str, mismatch: str, seed: int, duration_s: int
) -> tuple[list[FrameObservation], str, dict[str, float], dict[str, dict[str, object]], dict[str, Any]]:
    """Exercise one scheduled-ARX owner and its attached state-space challenger."""
    simulator_type = {"GrillSim": GrillSim, "MAKGrillSim": MAKGrillSim}[plant]
    simulator = simulator_type(seed=seed, fixed_fan=1.0)
    controller = _controller(mismatch=mismatch, challenger="state-space")
    scheduler = PulseScheduler()
    online = controller._online
    if online is None or not isinstance(online.incumbent, ScheduledARX):
        raise RuntimeError("scheduled-ARX command owner did not initialize")
    calibration_frames, start_second, actual_on = _arx_calibration_prefix(
        simulator=simulator, scheduler=scheduler, u_max=controller.u_max
    )
    try:
        online.incumbent.fit(calibration_frames)
    except (RuntimeError, ValueError, np.linalg.LinAlgError) as error:
        raise RuntimeError("scheduled-ARX command owner is unidentifiable from emitted calibration prefix") from error
    controller.set_target(80.0)
    request = float(controller.update(float(simulator.measured()))["cycle_ratio"])
    command_status = _command_owner_status(controller)
    if command_status["active_model_kind"] != "scheduled-arx":
        raise RuntimeError("scheduled-ARX command owner is unidentifiable from emitted calibration prefix")
    models = {"scheduled-arx": online.incumbent, "innovation-state-space": online.challenger}
    state_model = models["innovation-state-space"]
    if getattr(state_model, "model_kind", None) != "innovation-state-space":
        raise RuntimeError("state-space challenger did not expose the runtime observer")
    evidence: dict[str, Any] = {
        "command_owner_status": command_status,
        "owner_initialization": {
            "source": "simulator-pulse-prefix",
            "frame_count": len(calibration_frames),
            "frames_digest": _frames_digest(calibration_frames),
        },
        "models": models,
        "scheduled-arx": {"snapshots": [], "timing": {"update": [], "refresh": [], "solve": []}},
        "innovation-state-space": {
            "instance_digest": _instance_digest(state_model),
            "role_generation": command_status["role_generation"],
            "snapshots": [],
            "timing": {"update": [], "refresh": [], "solve": []},
            "timing_digests": {"update": [], "refresh": [], "solve": []},
            "observed_digests": [],
            "refreshes": [],
            "refresh_digests": [],
            "alignment_digests": [],
            "adaptation_digests": [],
            "adaptation_generations": [],
        },
    }
    frames = list(calibration_frames)
    requests: list[float] = []
    temperatures: list[float] = []
    targets: list[float] = []
    evaluations: list[dict[str, object]] = []
    bootstrap_recorded = False
    for control_second in range(duration_s):
        second = int(start_second) + control_second
        target = 80.0 if control_second < duration_s // 2 else 125.0
        decision = scheduler.advance(request, float(second), actual_on)
        if decision.transition is not None:
            actual_on = decision.transition.command_on
        simulator.step(actual_on, 1.0, lid_open=False)
        for completed in decision.completed_frames:
            duration = completed.ended_at_s - completed.nominal_start_s
            applied_duty = float(completed.delivered_on_s) / duration if duration else 0.0
            temperature = float(simulator.measured())
            controller.set_output(
                AppliedOutput(
                    ratio=applied_duty,
                    source=OutputSource.CONTROLLER,
                    timestamp=float(completed.ended_at_s),
                    requested=request,
                )
            )
            controller.set_target(target)
            observation = FrameObservation(
                frame_start_s=float(completed.nominal_start_s),
                frame_end_s=float(completed.ended_at_s),
                temp_c=temperature,
                setpoint_c=target,
                ambient_c=float(simulator.T_amb),
                requested_q=normalized_load_from_auger_duty(request, u_max=controller.u_max),
                realized_q=normalized_load_from_auger_duty(applied_duty, u_max=controller.u_max),
                requested_auger_duty=request,
                delivered_on_s=float(completed.delivered_on_s),
                requested_fan_duty=1.0,
                actual_fan_duty=1.0,
                result_revision=len(frames) + 1,
                output_source="controller",
                lid_open=False,
                safety_inhibited=False,
                manual_override=False,
                stale=False,
                skipped=False,
                reset=False,
                continuous=True,
                role_generation=command_status["role_generation"],
            )
            before_state_snapshot = state_model.snapshot()
            before_state_fitted = (
                isinstance(before_state_snapshot, Mapping)
                and before_state_snapshot.get("schema") == "innovation-state-space/v2"
            )
            before_refresh_attempts = int(getattr(state_model, "refresh_attempts", 0))
            started = perf_counter()
            outcome = controller.observe_frame(observation)
            elapsed_ms = (perf_counter() - started) * 1_000.0
            for arm, model in models.items():
                snapshot = model.snapshot()
                digest = _model_digest(model)
                arm_evidence = evidence[arm]
                arm_evidence["timing"]["update"].append(elapsed_ms)
                if arm == "innovation-state-space":
                    arm_evidence["observed_digests"].append(digest)
                    arm_evidence["timing_digests"]["update"].append(digest)
                diagnostics = snapshot.get("diagnostics") if isinstance(snapshot, Mapping) else None
                accepted = isinstance(diagnostics, Mapping) and diagnostics.get("accepted") is True
                if arm == "scheduled-arx":
                    accepted = len(frames) >= 8
                if accepted:
                    arm_evidence["snapshots"].append((len(frames), snapshot, digest))
            state_snapshot = state_model.snapshot()
            state_digest = _model_digest(state_model)
            state_evidence = evidence["innovation-state-space"]
            current_refresh_attempts = int(getattr(state_model, "refresh_attempts", 0))
            bootstrap = not before_state_fitted and state_snapshot.get("schema") == "innovation-state-space/v2"
            if bootstrap or current_refresh_attempts > before_refresh_attempts:
                kind = "replacement" if bootstrap_recorded else "bootstrap"
                diagnostic = _snapshot_diagnostic(state_snapshot, kind=kind)
                if diagnostic is None:
                    raise RuntimeError("state-space challenger did not emit refresh diagnostics")
                diagnostic["model_digest"] = state_digest
                state_evidence["refreshes"].append(diagnostic)
                state_evidence["refresh_digests"].append(state_digest)
                state_evidence["timing"]["refresh"].append(elapsed_ms)
                state_evidence["timing_digests"]["refresh"].append(state_digest)
                if diagnostic.get("accepted") is True and not bootstrap_recorded:
                    bootstrap_recorded = True
                if _selected_replacement_alignment_errors([diagnostic]):
                    state_evidence["alignment_digests"].append(state_digest)
            evaluation = outcome.get("evaluation") if isinstance(outcome, Mapping) else None
            if evaluation is not None:
                runtime_evaluation = controller.get_status().get("adaptation", {}).get("last_evaluation_outcome")
                refresh = (
                    runtime_evaluation.get("state_space_refresh") if isinstance(runtime_evaluation, Mapping) else None
                )
                evaluation_digest = refresh.get("state_space_digest") if isinstance(refresh, Mapping) else None
                evaluation_generation = (
                    runtime_evaluation.get("role_generation") if isinstance(runtime_evaluation, Mapping) else None
                )
                if (
                    evaluation_digest != state_digest
                    or not isinstance(evaluation_generation, int)
                    or evaluation_generation != online.challenger_generation
                ):
                    raise RuntimeError("evaluation evidence is not bound to the current state-space challenger")
                promoted = bool(
                    evaluation.get("promoted", False)
                    if isinstance(evaluation, Mapping)
                    else getattr(evaluation, "promoted", False)
                )
                evaluations.append(
                    {
                        "promotion_eligible": promoted,
                        "prospective_digest": evaluation.get("prospective_digest")
                        if isinstance(evaluation, Mapping)
                        else getattr(evaluation, "prospective_digest", None),
                        "experiment_gate_blocked": promoted and not controller._online_experiment_active,
                        "state_space_digest": evaluation_digest,
                        "challenger_instance_digest": state_evidence["instance_digest"],
                        "role_generation": state_evidence["role_generation"],
                        "challenger_generation": evaluation_generation,
                    }
                )
                state_evidence["adaptation_digests"].append(evaluation_digest)
                state_evidence["adaptation_generations"].append(evaluation_generation)
            frames.append(observation)

            requests.append(request)
            temperatures.append(temperature)
            targets.append(target)
            request = float(controller.update(temperature)["cycle_ratio"])
            command_status = _command_owner_status(controller)

    digest = json.dumps(requests, separators=(",", ":"), allow_nan=False)
    adaptation = {
        "scheduled-arx": {
            "outcomes": len(frames),
            "evaluation_count": 0,
            "evaluations": [],
            "promotion_eligible": False,
            "experiment_gate_blocked": False,
            "safety_inhibits": 0,
        },
        "innovation-state-space": {
            "outcomes": len(frames),
            "evaluation_count": len(evaluations),
            "evaluations": evaluations,
            "promotion_eligible": any(item["promotion_eligible"] for item in evaluations),
            "experiment_gate_blocked": any(item["experiment_gate_blocked"] for item in evaluations),
            "safety_inhibits": 0,
        },
    }
    evidence["command_owner_status"] = command_status
    return frames, digest, _control_metrics(temperatures, targets, duration_s), adaptation, evidence


def _diagnostic(value: RefreshDiagnostics, *, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "accepted": value.accepted,
        "terminal_reason": None if value.terminal_reason is None else value.terminal_reason.value,
        "selected_order": value.selected_order,
        "selected_delay": value.selected_delay,
        "attempts": [
            {
                **asdict(attempt),
                "hankel_shape": list(attempt.hankel_shape),
                "singular_values": list(attempt.singular_values),
                "rejection_reasons": [reason.value for reason in attempt.rejection_reasons],
            }
            for attempt in value.attempts
        ],
    }


def _real_mak_unidentifiable_row(
    *, common: Mapping[str, Any], model: InnovationStateSpace, diagnostics: RefreshDiagnostics, elapsed_ms: float
) -> dict[str, Any]:
    """Record an honest prediction-only state-space identification failure."""
    diagnostic = _diagnostic(diagnostics, kind="bootstrap")
    return {
        **common,
        "arm": "innovation-state-space",
        "model_kind": "innovation-state-space",
        "status": "completed",
        "failure": {"reason": "unidentifiable-input"},
        "prediction_metrics": {
            "rmse_60_c": None,
            "rmse_300_c": None,
            "origin_count_60": 0,
            "origin_count_300": 0,
        },
        "control_metrics": None,
        "raw_timing_ms": {"update": [], "refresh": [elapsed_ms], "solve": []},
        "refreshes": [diagnostic],
        "alignment": {"attempted": True, "accepted": False, "max_error_c": None},
        "production_stack": {
            "observation": "FrameObservation",
            "pulse_scheduler": "PulseScheduler",
            "model": type(model).__name__,
            "input": "normalized_load_from_auger_duty",
        },
        "challenger_pairing": {
            "instance_digest": _instance_digest(model),
            "unidentifiable": True,
            "refresh_digests": [],
            "timing_digests": {"update": [], "refresh": [], "solve": []},
        },
    }


def _refresh_attempt_time(snapshot: Mapping[str, object]) -> float | None:
    """Return the last periodic refresh attempt time from a public snapshot."""
    status = snapshot.get("status")
    if not isinstance(status, Mapping):
        raise RuntimeError("state-space refresh status is invalid")
    attempt_time = status.get("last_refresh_time_s")
    if attempt_time is None:
        return None
    if not _finite(attempt_time):
        raise RuntimeError("state-space refresh attempt time is invalid")
    return float(attempt_time)


def _valid_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _refresh_diagnostic_errors(refresh: object) -> list[str]:
    """Validate serialized immutable refresh evidence without repairing it."""
    if not isinstance(refresh, Mapping):
        return ["refresh mapping"]
    if refresh.get("kind") not in {"bootstrap", "replacement"} or not _valid_digest(refresh.get("model_digest")):
        return ["refresh metadata"]
    accepted = refresh.get("accepted")
    attempts = refresh.get("attempts")
    if not isinstance(accepted, bool) or not isinstance(attempts, list):
        return ["refresh shape"]
    reasons = {reason.value for reason in RefreshRejectionReason}

    def attempt_errors(attempt: object) -> list[str]:
        if not isinstance(attempt, Mapping):
            return ["attempt mapping"]
        if (
            not isinstance(attempt.get("order"), int)
            or isinstance(attempt.get("order"), bool)
            or attempt["order"] <= 0
            or not isinstance(attempt.get("delay"), int)
            or isinstance(attempt.get("delay"), bool)
            or attempt["delay"] <= 0
            or not _valid_nonnegative_int(attempt.get("sample_count"))
            or not isinstance(attempt.get("hankel_shape"), list)
            or len(attempt["hankel_shape"]) != 2
            or any(not _valid_nonnegative_int(value) for value in attempt["hankel_shape"])
            or not isinstance(attempt.get("singular_values"), list)
            or any(not _finite(value) or float(value) < 0.0 for value in attempt["singular_values"])
            or not _valid_nonnegative_int(attempt.get("effective_rank"))
            or not isinstance(attempt.get("projection_applied"), bool)
            or not isinstance(attempt.get("rejection_reasons"), list)
            or any(reason not in reasons for reason in attempt["rejection_reasons"])
            or (
                attempt.get("alignment_error_c") is not None
                and (not _finite(attempt["alignment_error_c"]) or float(attempt["alignment_error_c"]) < 0.0)
            )
            or not _finite(attempt.get("elapsed_ms"))
            or float(attempt["elapsed_ms"]) < 0.0
        ):
            return ["attempt fields"]
        return []

    errors = [error for attempt in attempts for error in attempt_errors(attempt)]
    selected = (refresh.get("selected_order"), refresh.get("selected_delay"))
    if accepted:
        if refresh.get("terminal_reason") is not None:
            errors.append("accepted terminal")
        if (
            not isinstance(selected[0], int)
            or isinstance(selected[0], bool)
            or selected[0] <= 0
            or not isinstance(selected[1], int)
            or isinstance(selected[1], bool)
            or selected[1] <= 0
        ):
            errors.append("accepted selection")
            return errors
        matching = [
            attempt
            for attempt in attempts
            if isinstance(attempt, Mapping) and (attempt.get("order"), attempt.get("delay")) == selected
        ]
        if len(matching) != 1:
            errors.append("selected attempt")
            return errors
        attempt = matching[0]
        if (
            attempt.get("rejection_reasons") != []
            or not _valid_nonnegative_int(attempt.get("effective_rank"))
            or attempt["effective_rank"] < selected[0]
            or not _finite(attempt.get("condition_number"))
            or float(attempt["condition_number"]) < 1.0
            or not _finite(attempt.get("steady_gain"))
            or float(attempt["steady_gain"]) <= 0.0
            or not _finite(attempt.get("prediction_score"))
            or float(attempt["prediction_score"]) < 0.0
            or not _finite(attempt.get("braking_score"))
            or float(attempt["braking_score"]) < 0.0
        ):
            errors.append("selected gate")
        return errors
    if selected != (None, None) or refresh.get("terminal_reason") not in reasons:
        errors.append("rejected selection")
        return errors
    terminal = refresh["terminal_reason"]
    if not attempts:
        if terminal != "insufficient-samples":
            errors.append("empty rejected refresh")
        return errors
    attempt_reasons = {
        reason
        for attempt in attempts
        if isinstance(attempt, Mapping)
        for reason in attempt.get("rejection_reasons", [])
    }
    if (
        terminal == "no-valid-candidate"
        and any(not isinstance(attempt, Mapping) or not attempt.get("rejection_reasons") for attempt in attempts)
    ) or (terminal != "no-valid-candidate" and terminal not in attempt_reasons):
        errors.append("rejected reasons")
    return errors


def _is_real_mak_unidentifiable(row: Mapping[str, object]) -> bool:
    """Recognize the sole evidence exception where no state-space model exists."""
    refreshes = row.get("refreshes")
    timing = row.get("raw_timing_ms")
    pairing = row.get("challenger_pairing")
    return (
        row.get("plant") == "real-MAK"
        and row.get("arm") == "innovation-state-space"
        and row.get("status") == "completed"
        and row.get("failure") == {"reason": "unidentifiable-input"}
        and row.get("prediction_metrics")
        == {"rmse_60_c": None, "rmse_300_c": None, "origin_count_60": 0, "origin_count_300": 0}
        and row.get("alignment") == {"attempted": True, "accepted": False, "max_error_c": None}
        and isinstance(refreshes, list)
        and len(refreshes) == 1
        and isinstance(refreshes[0], Mapping)
        and refreshes[0].get("model_digest") is None
        and not _refresh_diagnostic_errors({**refreshes[0], "model_digest": "0" * 64})
        and isinstance(timing, Mapping)
        and set(timing) == {"update", "refresh", "solve"}
        and timing.get("update") == []
        and timing.get("solve") == []
        and isinstance(timing.get("refresh"), list)
        and len(timing["refresh"]) == 1
        and _finite(timing["refresh"][0])
        and float(timing["refresh"][0]) >= 0.0
        and isinstance(pairing, Mapping)
        and set(pairing) == {"instance_digest", "unidentifiable", "refresh_digests", "timing_digests"}
        and _valid_digest(pairing.get("instance_digest"))
        and pairing.get("unidentifiable") is True
        and pairing.get("refresh_digests") == []
        and pairing.get("timing_digests") == {"update": [], "refresh": [], "solve": []}
    )


def _selected_replacement_alignment_errors(refreshes: Sequence[object]) -> list[float]:
    """Return bounded alignment residuals from accepted replacement selections only."""
    errors: list[float] = []
    for refresh in refreshes:
        if _refresh_diagnostic_errors(refresh):
            continue
        if (
            not isinstance(refresh, Mapping)
            or refresh.get("kind") != "replacement"
            or refresh.get("accepted") is not True
        ):
            continue
        attempts = refresh.get("attempts")
        if not isinstance(attempts, list):
            continue
        selected = (refresh.get("selected_order"), refresh.get("selected_delay"))
        for attempt in attempts:
            if not isinstance(attempt, Mapping) or (attempt.get("order"), attempt.get("delay")) != selected:
                continue
            error = attempt.get("alignment_error_c")
            if _finite(error) and 0.0 <= float(error) <= 2.0:
                errors.append(float(error))
    return errors


def _runtime_model_row(
    *, arm: str, frames: Sequence[FrameObservation], common: Mapping[str, Any], runtime_evidence: Mapping[str, Any]
) -> dict[str, Any]:
    """Score snapshots captured from the command owner's actual online models."""
    model = runtime_evidence["models"][arm]
    arm_evidence = runtime_evidence[arm]
    timing = {kind: list(samples) for kind, samples in arm_evidence["timing"].items()}
    timing_digests = (
        {kind: list(samples) for kind, samples in arm_evidence["timing_digests"].items()}
        if arm == "innovation-state-space"
        else None
    )
    refreshes = list(arm_evidence.get("refreshes", []))
    horizon_errors = {3: [], 15: []}
    prediction_digests: list[str] = []
    prediction_origins: list[dict[str, int | str]] = []
    prediction_events: list[dict[str, int | str]] = []
    status = "completed"
    failure: dict[str, str] | None = None
    try:
        for index, snapshot, snapshot_digest in arm_evidence["snapshots"]:
            for horizon in horizon_errors:
                if index + horizon >= len(frames):
                    continue
                frozen = (
                    ScheduledARX.from_snapshot(snapshot)
                    if arm == "scheduled-arx"
                    else InnovationStateSpace.from_snapshot(snapshot)
                )
                future = frames[index + 1 : index + horizon + 1]
                started = perf_counter()
                prediction = frozen.affine_prediction(
                    horizon, frames[index].realized_q, [frame.ambient_c for frame in future]
                )
                timing["solve"].append((perf_counter() - started) * 1_000.0)
                if timing_digests is not None:
                    timing_digests["solve"].append(snapshot_digest)
                terminal = float(
                    prediction.free_output_c[-1]
                    + prediction.input_response_c[-1] @ np.asarray([frame.realized_q for frame in future])
                )
                horizon_errors[horizon].append(terminal - future[-1].temp_c)
                if timing_digests is not None:
                    prediction_digests.append(snapshot_digest)
                    event = {
                        "frame_index": index,
                        "horizon_steps": horizon,
                        "model_digest": snapshot_digest,
                        "event_source": "solve-timing",
                        "event_sequence": len(timing_digests["solve"]) - 1,
                    }
                    prediction_events.append(event)
                    prediction_origins.append(dict(event))
    except (RuntimeError, ValueError, np.linalg.LinAlgError) as error:
        status = "infrastructure-failed"
        failure = {"reason": type(error).__name__, "message": str(error)}
    prediction_metrics = {
        f"rmse_{horizon * frame_seconds()}_c": sqrt(sum(error * error for error in errors) / len(errors))
        if errors
        else None
        for horizon, errors in horizon_errors.items()
    }
    prediction_metrics.update({"origin_count_60": len(horizon_errors[3]), "origin_count_300": len(horizon_errors[15])})
    alignment_errors = _selected_replacement_alignment_errors(refreshes)
    row: dict[str, Any] = {
        **common,
        "arm": arm,
        "model_kind": arm,
        "status": status,
        "failure": failure,
        "prediction_metrics": prediction_metrics,
        "control_metrics": common.get("control_metrics"),
        "raw_timing_ms": timing,
        "refreshes": refreshes,
        "alignment": {
            "attempted": arm == "innovation-state-space",
            "accepted": bool(alignment_errors) if arm == "innovation-state-space" else False,
            "max_error_c": max(alignment_errors) if alignment_errors else None,
        },
        "production_stack": {
            "observation": "FrameObservation",
            "pulse_scheduler": "PulseScheduler",
            "model": type(model).__name__,
            "input": "normalized_load_from_auger_duty",
        },
    }
    if arm == "innovation-state-space":
        if timing_digests is None:
            raise RuntimeError("missing state-space timing digests")
        row["challenger_pairing"] = {
            "instance_digest": arm_evidence["instance_digest"],
            "role_generation": arm_evidence["role_generation"],
            "observed_digests": list(arm_evidence["observed_digests"]),
            "prediction_digests": prediction_digests,
            "prediction_origins": prediction_origins,
            "prediction_events": prediction_events,
            "refresh_digests": list(arm_evidence["refresh_digests"]),
            "alignment_digests": list(arm_evidence["alignment_digests"]),
            "timing_digests": timing_digests,
            "adaptation_digests": list(arm_evidence["adaptation_digests"]),
            "adaptation_generations": list(arm_evidence["adaptation_generations"]),
        }
    else:
        row["challenger_pairing"] = None
    return row


def _model_row(
    *,
    arm: str,
    frames: Sequence[FrameObservation],
    common: Mapping[str, Any],
    runtime_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fit a standalone model only when no command-owner evidence was captured."""
    if runtime_evidence is not None:
        return _runtime_model_row(arm=arm, frames=frames, common=common, runtime_evidence=runtime_evidence)
    timing = {"update": [], "refresh": [], "solve": []}
    timing_digests = {"update": [], "refresh": [], "solve": []}
    refreshes: list[dict[str, Any]] = []
    snapshots: list[tuple[int, dict[str, object], str]] = []
    observed_digests: list[str] = []
    prediction_digests: list[str] = []
    prediction_origins: list[dict[str, int | str]] = []
    prediction_events: list[dict[str, int | str]] = []
    horizon_errors = {3: [], 15: []}
    if arm == "scheduled-arx":
        model: ScheduledARX | InnovationStateSpace = ScheduledARX(
            ScheduledARXConfig(na=2, nb=2, delays=(1, 2, 3), initial_covariance=10.0)
        )
        warmup = 8
    else:
        model = InnovationStateSpace(StateSpaceConfig(orders=(1, 2), delays=(1, 2, 3), refresh_interval_s=300.0))
        warmup = min(32, max(8, len(frames) // 2))
    status = "completed"
    failure: dict[str, str] | None = None
    try:
        started = perf_counter()
        if arm == "innovation-state-space":
            diagnostics = model.fit(frames[:warmup])
            elapsed_ms = (perf_counter() - started) * 1_000.0
            if not diagnostics.accepted:
                if common.get("plant") == "real-MAK":
                    return _real_mak_unidentifiable_row(
                        common=common, model=model, diagnostics=diagnostics, elapsed_ms=elapsed_ms
                    )
                raise RuntimeError("state-space input is unidentifiable")
            digest = _model_digest(model)
            diagnostic = _diagnostic(diagnostics, kind="bootstrap")
            diagnostic["model_digest"] = digest
            refreshes.append(diagnostic)
            timing_digests["refresh"].append(digest)
        else:
            model.fit(frames[:warmup])
        timing["refresh"].append((perf_counter() - started) * 1_000.0)
        refresh_attempt_time = _refresh_attempt_time(model.snapshot()) if arm == "innovation-state-space" else None
        for index, frame in enumerate(frames[warmup:], start=warmup):
            started = perf_counter()
            model.observe(frame)
            elapsed_ms = (perf_counter() - started) * 1_000.0
            timing["update"].append(elapsed_ms)
            snapshot = model.snapshot()
            digest = _model_digest(model)
            if arm == "innovation-state-space":
                observed_digests.append(digest)
                timing_digests["update"].append(digest)
                next_refresh_attempt_time = _refresh_attempt_time(snapshot)
                if next_refresh_attempt_time != refresh_attempt_time:
                    timing["refresh"].append(elapsed_ms)
                    diagnostic = _diagnostic(model.diagnostics, kind="replacement")
                    diagnostic["model_digest"] = digest
                    refreshes.append(diagnostic)
                    timing_digests["refresh"].append(digest)
                refresh_attempt_time = next_refresh_attempt_time
            snapshots.append((index, snapshot, digest))
        if frames:
            started = perf_counter()
            model.affine_prediction(1, frames[-1].realized_q, [frames[-1].ambient_c])
            timing["solve"].append((perf_counter() - started) * 1_000.0)
            if arm == "innovation-state-space":
                timing_digests["solve"].append(_model_digest(model))
        for index, snapshot, snapshot_digest in snapshots:
            for horizon in horizon_errors:
                if index + horizon >= len(frames):
                    continue
                frozen = (
                    ScheduledARX.from_snapshot(snapshot)
                    if arm == "scheduled-arx"
                    else InnovationStateSpace.from_snapshot(snapshot)
                )
                future = frames[index + 1 : index + horizon + 1]
                started = perf_counter()
                prediction = frozen.affine_prediction(
                    horizon, frames[index].realized_q, [frame.ambient_c for frame in future]
                )
                timing["solve"].append((perf_counter() - started) * 1_000.0)
                if arm == "innovation-state-space":
                    timing_digests["solve"].append(snapshot_digest)
                    prediction_digests.append(snapshot_digest)
                    event = {
                        "frame_index": index,
                        "horizon_steps": horizon,
                        "model_digest": snapshot_digest,
                        "event_source": "solve-timing",
                        "event_sequence": len(timing_digests["solve"]) - 1,
                    }
                    prediction_events.append(event)
                    prediction_origins.append(dict(event))
                terminal = float(
                    prediction.free_output_c[-1]
                    + prediction.input_response_c[-1] @ np.asarray([frame.realized_q for frame in future])
                )
                horizon_errors[horizon].append(terminal - future[-1].temp_c)
    except (RuntimeError, ValueError, np.linalg.LinAlgError) as error:
        status = "infrastructure-failed"
        failure = {"reason": type(error).__name__, "message": str(error)}
        if arm == "innovation-state-space":
            diagnostic = _diagnostic(model.diagnostics, kind="replacement")
            diagnostic["model_digest"] = _model_digest(model)
            refreshes.append(diagnostic)
    prediction_metrics = {
        f"rmse_{horizon * frame_seconds()}_c": sqrt(sum(error * error for error in errors) / len(errors))
        if errors
        else None
        for horizon, errors in horizon_errors.items()
    }
    prediction_metrics.update({"origin_count_60": len(horizon_errors[3]), "origin_count_300": len(horizon_errors[15])})
    alignment_errors = _selected_replacement_alignment_errors(refreshes)
    row = {
        **common,
        "arm": arm,
        "model_kind": arm,
        "status": status,
        "failure": failure,
        "prediction_metrics": prediction_metrics,
        "control_metrics": common.get("control_metrics"),
        "raw_timing_ms": timing,
        "refreshes": refreshes,
        "alignment": {
            "attempted": arm == "innovation-state-space",
            "accepted": bool(alignment_errors) if arm == "innovation-state-space" else False,
            "max_error_c": max(alignment_errors) if alignment_errors else None,
        },
        "production_stack": {
            "observation": "FrameObservation",
            "pulse_scheduler": "PulseScheduler",
            "model": type(model).__name__,
            "input": "normalized_load_from_auger_duty",
        },
    }
    if arm == "innovation-state-space":
        row["challenger_pairing"] = {
            "instance_digest": _instance_digest(model),
            "role_generation": 0,
            "observed_digests": observed_digests,
            "prediction_digests": prediction_digests,
            "prediction_origins": prediction_origins,
            "prediction_events": prediction_events,
            "refresh_digests": [refresh["model_digest"] for refresh in refreshes],
            "alignment_digests": [
                refresh["model_digest"] for refresh in refreshes if _selected_replacement_alignment_errors([refresh])
            ],
            "timing_digests": timing_digests,
            "adaptation_digests": [],
            "adaptation_generations": [],
        }
    else:
        row["challenger_pairing"] = None
    return row


def _simulator_rows(*, duration_s: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for plant in SIMULATOR_PLANTS:
        for mismatch in MISMATCHES:
            for seed in FIXED_SEEDS:
                frames, commands_digest, controls, adaptation, runtime_evidence = _simulator_frames(
                    plant=plant, mismatch=mismatch, seed=seed, duration_s=duration_s
                )
                changed, nominal = _mismatch_config(mismatch)
                for arm in ARMS:
                    common = {
                        "cell_key": f"{plant}:{mismatch}:{seed}:{arm}",
                        "plant": plant,
                        "mismatch": mismatch,
                        "owner_initialization": runtime_evidence["owner_initialization"],
                        "seed": seed,
                        "mode": "closed-loop",
                        "command_owner": runtime_evidence["command_owner_status"]["active_model_kind"],
                        "command_owner_status": runtime_evidence["command_owner_status"],
                        "shadow_only": arm == "innovation-state-space",
                        "effective_duration_s": duration_s,
                        "mismatch_evidence": {
                            "parameter": next(iter(changed)),
                            "configured_value": next(iter(changed.values())),
                            "nominal_value": next(iter(nominal.values())),
                        },
                        "commands_digest": commands_digest,
                        "control_metrics": controls,
                        "adaptation": adaptation[arm],
                    }
                    rows.append(_model_row(arm=arm, frames=frames, common=common, runtime_evidence=runtime_evidence))
    return rows


def _real_mak_rows() -> list[dict[str, Any]]:
    record = real_mak_record()
    frames = record_frames(record)
    normalized = [
        FrameObservation(
            **{
                **asdict(frame),
                "requested_q": normalized_load_from_auger_duty(float(record.q[index]), u_max=0.9),
                "baseline_q": normalized_load_from_auger_duty(float(record.q[index]), u_max=0.9),
                "probe_q": 0.0,
                "realized_q": normalized_load_from_auger_duty(float(record.q[index]), u_max=0.9),
                "output_source": "requested-input-reconstruction",
            }
        )
        for index, frame in enumerate(frames)
    ]
    rows = []
    for arm in ARMS:
        common = {
            "cell_key": f"real-MAK:nominal:{arm}",
            "plant": "real-MAK",
            "mismatch": "nominal",
            "seed": None,
            "mode": "prediction-only",
            "chronological": True,
            "normalized_input": "normalized_load_from_auger_duty",
            "command_owner_status": None,
            "command_owner": "historical-requested-input",
            "shadow_only": arm == "innovation-state-space",
            "effective_duration_s": 0,
            "mismatch_evidence": {"parameter": "historical", "configured_value": 1.0, "nominal_value": 1.0},
            "commands_digest": "historical-requested-input",
            "adaptation": {
                "outcomes": len(normalized),
                "evaluation_count": 0,
                "evaluations": [],
                "promotion_eligible": False,
                "experiment_gate_blocked": False,
                "safety_inhibits": 0,
            },
            "control_metrics": None,
        }
        rows.append(_model_row(arm=arm, frames=normalized, common=common))
    return rows


def _expected_keys() -> set[str]:
    return {
        f"{plant}:{mismatch}:{seed}:{arm}"
        for plant in SIMULATOR_PLANTS
        for mismatch in MISMATCHES
        for seed in FIXED_SEEDS
        for arm in ARMS
    } | {f"real-MAK:nominal:{arm}" for arm in ARMS}


def artifact_contract_errors(artifact: Mapping[str, Any], *, require_decision: bool = True) -> list[str]:
    """Return strict non-repairing evidence errors for this artifact schema."""
    errors: list[str] = []
    if artifact.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        errors.append("schema version")
    if not _valid_source_revision(artifact.get("source_revision")):
        errors.append("source revision")
    if artifact.get("fixed_seeds") != list(FIXED_SEEDS):
        errors.append("fixed seeds")
    rows = artifact.get("rows")
    real_rows = artifact.get("real_mak_rows")
    if not isinstance(rows, list) or not isinstance(real_rows, list):
        return [*errors, "rows"]
    all_rows = [*rows, *real_rows]
    keys = [row.get("cell_key") for row in all_rows if isinstance(row, Mapping)]
    duplicates = [key for key in keys if keys.count(key) > 1]
    if artifact.get("duplicates") != [] or duplicates:
        errors.append("duplicate cells")
    if set(keys) != _expected_keys() or len(all_rows) != len(_expected_keys()):
        errors.append("incomplete cells")
    if artifact.get("complete_cells") != len(all_rows) or artifact.get("expected_cells") != len(_expected_keys()):
        errors.append("complete cell accounting")
    for row in all_rows:
        if not isinstance(row, Mapping):
            errors.append("row mapping")
            continue
        if row.get("status") != "completed" or row.get("arm") not in ARMS:
            errors.append("completed arm row")
        if row.get("mode") == "closed-loop":
            owner_status = row.get("command_owner_status")
            if (
                row.get("command_owner") != "scheduled-arx"
                or not isinstance(row.get("shadow_only"), bool)
                or not isinstance(owner_status, Mapping)
                or owner_status.get("active_model_kind") != row.get("command_owner")
                or not isinstance(owner_status.get("role_generation"), int)
                or owner_status["role_generation"] < 0
            ):
                errors.append("command authority evidence")
            initialization = row.get("owner_initialization")
            if (
                not isinstance(initialization, Mapping)
                or initialization.get("source") != "simulator-pulse-prefix"
                or not isinstance(initialization.get("frame_count"), int)
                or isinstance(initialization.get("frame_count"), bool)
                or not 0 < initialization["frame_count"] <= 48
                or not _valid_digest(initialization.get("frames_digest"))
            ):
                errors.append("command owner initialization")
        elif (
            row.get("command_owner") != "historical-requested-input"
            or row.get("command_owner_status") is not None
            or not isinstance(row.get("shadow_only"), bool)
        ):
            errors.append("command authority evidence")
        adaptation = row.get("adaptation")
        if (
            not isinstance(adaptation, Mapping)
            or not all(
                isinstance(adaptation.get(name), int) and adaptation[name] >= 0
                for name in ("outcomes", "evaluation_count", "safety_inhibits")
            )
            or not isinstance(adaptation.get("promotion_eligible"), bool)
            or not isinstance(adaptation.get("experiment_gate_blocked"), bool)
        ):
            errors.append("adaptation evidence")
        evaluations = adaptation.get("evaluations") if isinstance(adaptation, Mapping) else None
        if (
            not isinstance(evaluations, list)
            or not isinstance(adaptation, Mapping)
            or adaptation.get("evaluation_count") != len(evaluations)
            or any(
                not isinstance(item, Mapping)
                or not isinstance(item.get("promotion_eligible"), bool)
                or not isinstance(item.get("experiment_gate_blocked"), bool)
                or (item.get("prospective_digest") is not None and not _valid_digest(item.get("prospective_digest")))
                or (item["promotion_eligible"] != (item.get("prospective_digest") is not None))
                or (item["experiment_gate_blocked"] and not item["promotion_eligible"])
                for item in evaluations
            )
            or adaptation.get("promotion_eligible")
            != any(item.get("promotion_eligible") is True for item in evaluations if isinstance(item, Mapping))
            or adaptation.get("experiment_gate_blocked")
            != any(item.get("experiment_gate_blocked") is True for item in evaluations if isinstance(item, Mapping))
            or (
                row.get("arm") == "innovation-state-space"
                and any(item["promotion_eligible"] and not item["experiment_gate_blocked"] for item in evaluations)
            )
        ):
            errors.append("evaluation evidence")
        unidentifiable_real_mak = _is_real_mak_unidentifiable(row)
        if row.get("arm") == "innovation-state-space" and not unidentifiable_real_mak:
            pairing = row.get("challenger_pairing")
            evaluation_items = evaluations if isinstance(evaluations, list) else []
            refreshes = row.get("refreshes")
            timing = row.get("raw_timing_ms")
            digest_lists = (
                "observed_digests",
                "prediction_digests",
                "refresh_digests",
                "alignment_digests",
                "adaptation_digests",
            )
            invalid_pairing = (
                not isinstance(pairing, Mapping)
                or not _valid_digest(pairing.get("instance_digest"))
                or not _valid_nonnegative_int(pairing.get("role_generation"))
                or any(
                    not isinstance(pairing.get(name), list)
                    or any(not _valid_digest(digest) for digest in pairing[name])
                    for name in digest_lists
                )
                or not isinstance(pairing.get("adaptation_generations"), list)
                or any(not _valid_nonnegative_int(generation) for generation in pairing["adaptation_generations"])
                or not isinstance(pairing.get("prediction_origins"), list)
                or not isinstance(pairing.get("prediction_events"), list)
                or not isinstance(pairing.get("timing_digests"), Mapping)
                or not isinstance(refreshes, list)
                or not isinstance(timing, Mapping)
            )
            if not invalid_pairing:
                timing_digests = pairing["timing_digests"]
                origins = pairing["prediction_origins"]
                events = pairing["prediction_events"]
                invalid_pairing = (
                    not pairing["observed_digests"]
                    or set(timing_digests) != {"update", "refresh", "solve"}
                    or any(
                        not isinstance(timing_digests[name], list)
                        or any(not _valid_digest(digest) for digest in timing_digests[name])
                        or not isinstance(timing.get(name), list)
                        or len(timing_digests[name]) != len(timing[name])
                        for name in ("update", "refresh", "solve")
                    )
                    or timing_digests["update"] != pairing["observed_digests"]
                    or len(origins) != len(pairing["prediction_digests"])
                    or origins != events
                    or pairing["prediction_digests"] != [origin["model_digest"] for origin in origins]
                    or any(
                        not isinstance(origin, Mapping)
                        or not _valid_nonnegative_int(origin.get("frame_index"))
                        or not _valid_nonnegative_int(origin.get("horizon_steps"))
                        or not _valid_digest(origin.get("model_digest"))
                        or origin.get("event_source") != "solve-timing"
                        or not _valid_nonnegative_int(origin.get("event_sequence"))
                        or origin["event_sequence"] >= len(timing_digests["solve"])
                        or timing_digests["solve"][origin["event_sequence"]] != origin["model_digest"]
                        for origin in origins
                    )
                    or len({origin["event_sequence"] for origin in origins}) != len(origins)
                    or [origin["event_sequence"] for origin in origins]
                    != sorted(origin["event_sequence"] for origin in origins)
                    or pairing["refresh_digests"]
                    != [refresh.get("model_digest") for refresh in refreshes if isinstance(refresh, Mapping)]
                    or pairing["alignment_digests"]
                    != [
                        refresh.get("model_digest")
                        for refresh in refreshes
                        if _selected_replacement_alignment_errors([refresh])
                    ]
                    or any(digest not in pairing["observed_digests"] for digest in pairing["adaptation_digests"])
                    or pairing["adaptation_digests"]
                    != [item.get("state_space_digest") for item in evaluation_items if isinstance(item, Mapping)]
                    or len(pairing["adaptation_generations"]) != len(pairing["adaptation_digests"])
                    or pairing["adaptation_generations"]
                    != [item.get("challenger_generation") for item in evaluation_items if isinstance(item, Mapping)]
                    or any(
                        not isinstance(item, Mapping)
                        or not _valid_digest(item.get("state_space_digest"))
                        or item.get("challenger_instance_digest") != pairing["instance_digest"]
                        or item.get("role_generation") != pairing["role_generation"]
                        or not _valid_nonnegative_int(item.get("challenger_generation"))
                        for item in evaluation_items
                    )
                )
            if invalid_pairing:
                errors.append("challenger evidence pairing")
        elif row.get("arm") != "innovation-state-space" and row.get("challenger_pairing") is not None:
            errors.append("challenger evidence pairing")
        mismatch = row.get("mismatch_evidence")
        if not isinstance(mismatch, Mapping) or not {"parameter", "configured_value", "nominal_value"} <= set(mismatch):
            errors.append("mismatch evidence")
        elif row.get("plant") != "real-MAK" and mismatch["configured_value"] == mismatch["nominal_value"]:
            errors.append("mismatch evidence")
        timing = row.get("raw_timing_ms")
        if not isinstance(timing, Mapping) or set(timing) != {"update", "refresh", "solve"}:
            errors.append("raw timing")
        elif any(
            not isinstance(samples, list) or any(not _finite(sample) or float(sample) < 0.0 for sample in samples)
            for samples in timing.values()
        ):
            errors.append("non-finite timing")
        refreshes = row.get("refreshes")
        if row.get("arm") == "innovation-state-space" and not unidentifiable_real_mak:
            if not isinstance(refreshes, list) or not refreshes:
                errors.append("refresh attempts")
            elif any(_refresh_diagnostic_errors(refresh) for refresh in refreshes):
                errors.append("refresh diagnostics")
        alignment = row.get("alignment")
        if not isinstance(alignment, Mapping):
            errors.append("alignment evidence")
        else:
            measured_alignment_errors = _selected_replacement_alignment_errors(
                refreshes if isinstance(refreshes, list) else []
            )
            expected_alignment = (
                bool(measured_alignment_errors) if row.get("arm") == "innovation-state-space" else False
            )
            expected_max_error = max(measured_alignment_errors) if measured_alignment_errors else None
            selected_replacement_errors = [
                attempt.get("alignment_error_c")
                for refresh in (refreshes if isinstance(refreshes, list) else [])
                if isinstance(refresh, Mapping)
                and refresh.get("kind") == "replacement"
                and refresh.get("accepted") is True
                for attempt in (refresh.get("attempts") if isinstance(refresh.get("attempts"), list) else [])
                if isinstance(attempt, Mapping)
                and (attempt.get("order"), attempt.get("delay"))
                == (refresh.get("selected_order"), refresh.get("selected_delay"))
                and attempt.get("alignment_error_c") is not None
            ]
            if (
                alignment.get("attempted") is not (row.get("arm") == "innovation-state-space")
                or alignment.get("accepted") is not expected_alignment
                or alignment.get("max_error_c") != expected_max_error
                or any(
                    not _finite(error) or float(error) < 0.0 or float(error) > 2.0
                    for error in selected_replacement_errors
                )
            ):
                errors.append("alignment evidence")
        if row.get("failure") is not None and not unidentifiable_real_mak:
            errors.append("row failure")
        prediction = row.get("prediction_metrics")
        if not unidentifiable_real_mak and (
            not isinstance(prediction, Mapping)
            or set(prediction) != {"rmse_60_c", "rmse_300_c", "origin_count_60", "origin_count_300"}
            or not _finite(prediction.get("rmse_60_c"))
            or not _finite(prediction.get("rmse_300_c"))
            or not isinstance(prediction.get("origin_count_60"), int)
            or isinstance(prediction.get("origin_count_60"), bool)
            or not isinstance(prediction.get("origin_count_300"), int)
            or isinstance(prediction.get("origin_count_300"), bool)
        ):
            errors.append("prediction metrics")
        elif not unidentifiable_real_mak and (
            prediction["origin_count_60"] <= 0 or prediction["origin_count_300"] <= 0
        ):
            errors.append("forecast origins")
        if row.get("plant") == "real-MAK":
            if (
                row.get("mode") != "prediction-only"
                or row.get("chronological") is not True
                or row.get("normalized_input") != "normalized_load_from_auger_duty"
                or row.get("control_metrics") is not None
            ):
                errors.append("normalized real-MAK provenance")
        elif row.get("mode") != "closed-loop" or not isinstance(row.get("control_metrics"), Mapping):
            errors.append("control metrics")
    decision = artifact.get("decision")
    if require_decision:
        if (
            not isinstance(decision, Mapping)
            or not isinstance(decision.get("ship"), bool)
            or not isinstance(decision.get("reasons"), list)
            or not all(isinstance(reason, str) and reason for reason in decision.get("reasons", []))
        ):
            errors.append("decision")
        elif decision != decide_ship(artifact):
            errors.append("ship decision is not recomputed")
    return list(dict.fromkeys(errors))


def decide_ship(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Make the deliberately conservative activation decision from raw evidence."""
    reasons = artifact_contract_errors(artifact, require_decision=False)
    simulator_rows = artifact.get("rows", [])
    real_mak_rows = artifact.get("real_mak_rows", [])
    evidence_rows = [
        row for collection in (simulator_rows, real_mak_rows) if isinstance(collection, list) for row in collection
    ]
    state_rows = [
        row for row in evidence_rows if isinstance(row, Mapping) and row.get("arm") == "innovation-state-space"
    ]
    arx_by_key = {
        (row.get("plant"), row.get("mismatch"), row.get("seed")): row
        for row in evidence_rows
        if isinstance(row, Mapping) and row.get("arm") == "scheduled-arx"
    }
    recoveries = [
        row
        for row in state_rows
        if row.get("mismatch") in MISMATCHES
        and any(
            isinstance(item, Mapping)
            and item.get("promotion_eligible") is True
            and item.get("experiment_gate_blocked") is True
            and _valid_digest(item.get("prospective_digest"))
            for item in (row["adaptation"].get("evaluations", []) if isinstance(row.get("adaptation"), Mapping) else [])
        )
    ]
    if not recoveries:
        reasons.append("no promotion-eligible wrong-model recovery blocked by experiment gate")
    for row in state_rows:
        if _is_real_mak_unidentifiable(row):
            reasons.append("real-MAK state-space input is unidentifiable")
            continue
        timing = row.get("raw_timing_ms", {})
        refresh = timing.get("refresh", []) if isinstance(timing, Mapping) else []
        solve = timing.get("solve", []) if isinstance(timing, Mapping) else []
        if refresh and float(np.percentile(refresh, 99.0)) > _REFRESH_BUDGET_MS:
            reasons.append("refresh p99 exceeds 250 ms")
        if solve and float(np.percentile(solve, 99.0)) > _SOLVE_BUDGET_MS:
            reasons.append("solve p99 exceeds 50 ms")
        adaptation = row.get("adaptation", {})
        if not isinstance(adaptation, Mapping) or adaptation.get("safety_inhibits") != 0:
            reasons.append("state-space safety evidence")
        if row.get("mode") == "closed-loop" and (
            row.get("command_owner") != "scheduled-arx" or row.get("shadow_only") is not True
        ):
            reasons.append("state-space command-authority leak")
        alignment = row.get("alignment")
        if (
            not isinstance(alignment, Mapping)
            or alignment.get("accepted") is not True
            or not _finite(alignment.get("max_error_c"))
            or float(alignment["max_error_c"]) < 0.0
            or float(alignment["max_error_c"]) > 2.0
        ):
            reasons.append("missing measured refresh alignment")
        arx = arx_by_key.get((row.get("plant"), row.get("mismatch"), row.get("seed")))
        if not isinstance(arx, Mapping):
            reasons.append("missing scheduled-ARX comparator")
            continue
        state_metrics = row.get("prediction_metrics")
        arx_metrics = arx.get("prediction_metrics")
        if (
            not isinstance(state_metrics, Mapping)
            or not isinstance(arx_metrics, Mapping)
            or any(
                not _finite(state_metrics.get(name))
                or not _finite(arx_metrics.get(name))
                or float(state_metrics[name]) > float(arx_metrics[name]) * 1.10
                for name in ("rmse_60_c", "rmse_300_c")
            )
        ):
            reasons.append("state-space prediction threshold")
    reasons = list(dict.fromkeys(reasons))
    return {"ship": not reasons, "reasons": reasons}


def decision_code_errors(artifact: Mapping[str, Any], catalog: Mapping[str, object]) -> list[str]:
    """Validate the clean-cutover selector rule without reading repository state."""
    decision = artifact.get("decision")
    if not isinstance(decision, Mapping) or not isinstance(decision.get("ship"), bool):
        return ["decision"]
    exposed = "online_model" in catalog or "state-space" in json.dumps(catalog, sort_keys=True)
    if not decision["ship"] and exposed:
        return ["state-space catalog exposure contradicts ship=false"]
    if decision["ship"] and not exposed:
        return ["ship=true requires state-space catalog exposure"]
    return []


def run_comparison(*, source_revision: str, duration_s: int = 1_800) -> dict[str, Any]:
    if not _valid_source_revision(source_revision):
        raise ValueError("source revision must be a lowercase 40-hex commit")
    if not isinstance(duration_s, int) or duration_s < 1_800 or duration_s % frame_seconds():
        raise ValueError("duration_s must be a whole cadence duration of at least 1800 seconds")
    rows = _simulator_rows(duration_s=duration_s)
    artifact: dict[str, Any] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "source_revision": source_revision,
        "fixed_seeds": list(FIXED_SEEDS),
        "rows": rows,
        "real_mak_rows": _real_mak_rows(),
        "complete_cells": len(rows) + len(ARMS),
        "expected_cells": len(_expected_keys()),
        "duplicates": [],
    }
    artifact["decision"] = decide_ship(artifact)
    return artifact


def write_artifact_atomically(artifact: Mapping[str, Any], output: Path) -> None:
    errors = artifact_contract_errors(artifact)
    if errors:
        raise ValueError("invalid artifact: " + "; ".join(errors))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(artifact, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(output)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare scheduled ARX against the shadow state-space challenger.")
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration-s", type=int, default=1_800)
    args = parser.parse_args(argv)
    try:
        artifact = run_comparison(source_revision=args.source_revision, duration_s=args.duration_s)
        write_artifact_atomically(artifact, args.output)
    except (RuntimeError, ValueError, OSError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
