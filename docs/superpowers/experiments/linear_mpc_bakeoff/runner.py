"""Resumable, deterministic closed-loop evidence runner for the model bake-off."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import importlib.metadata
import json
import os
import sys
from collections import defaultdict, deque
from time import perf_counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import MappingProxyType
from statistics import median
from typing import Any, Mapping
import numpy as np
from scipy.optimize import minimize


from controller.grill_sim import GrillSim, MAKGrillSim

from .actuation import PulseRealizer
from .adaptation import AdaptationManager, OperatingState, WindowScores
from .arx import ARXConfig, ScheduledARX
from .data import reconstruct_mak_fixture, resample_record
from .datasets import (
    DEFAULT_CALIBRATION_PROGRAM,
    MAK_CALIBRATION_PROGRAM,
    generate_calibration_record,
)
from .dmc import DMCConfig, LaguerreDMC
from .state_space import InnovationStateSpace, StateSpaceConfig
from .artifact import ArmEvidence, ExperimentArtifact, MatrixKey
from .contracts import Observation, SignalRecord
from .scenarios import SCENARIOS, ScenarioDefinition, quick_scenarios
from .linear_mpc import (
    LinearMPC,
    MPCConfig,
    condense_cost,
    projected_gradient_qp,
    select_validation_horizon,
)
_FRAME_S = 20
_FIXED_FAN = 1.0
_DEFAULT_OUTPUT = Path("docs/superpowers/experiments/_linear_mpc_bakeoff.json")


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    """Frozen runner inputs; every value is serialized into its artifact."""

    quick_mode: bool = False
    seeds: tuple[int, ...] = (0, 1, 2)
    duration_s: int = 1_800
    control_budget_ms: float = 50.0
    initializations: tuple[str, ...] = ("correct", "wrong-gain", "wrong-pole", "wrong-delay")
    output: Path | None = None

    def __post_init__(self) -> None:
        if not self.seeds or any(not isinstance(seed, int) for seed in self.seeds):
            raise ValueError("seeds must be non-empty integers")
        if self.duration_s < _FRAME_S:
            raise ValueError("duration_s must cover at least one controller frame")
        if self.control_budget_ms <= 0.0:
            raise ValueError("control_budget_ms must be positive")

    @classmethod
    def quick(cls) -> "ExperimentConfig":
        return cls(
            quick_mode=True,
            seeds=(2,),
            duration_s=140,
            initializations=("correct", "wrong-gain", "wrong-pole", "wrong-delay"),
        )

    def to_document(self) -> dict[str, Any]:
        return {
            "control_budget_ms": self.control_budget_ms,
            "duration_s": self.duration_s,
            "initializations": list(self.initializations),
            "quick": self.quick_mode,
            "seeds": list(self.seeds),
            "solver_period_s": _FRAME_S,
        }



def _select_validation_horizon(residuals_by_horizon: Mapping[int, tuple[float, ...]]) -> dict[str, Any]:
    """Summarize one already-isolated validation score set."""
    scores = {
        horizon_s: float(np.mean(np.abs(values)))
        for horizon_s, values in residuals_by_horizon.items()
    }
    selected = select_validation_horizon(scores)
    best = min(scores.values())
    return {
        "selected_horizon_s": selected,
        "tie_rationale": (
            f"{selected} seconds is within 1% of the validation best"
            if selected != min(scores, key=lambda horizon_s: scores[horizon_s])
            else f"{selected} seconds is the validation best"
        ),
        "validation_scores": {str(horizon_s): scores[horizon_s] for horizon_s in sorted(scores)},
    }


def _validation_origins(
    *,
    record_samples: int,
    validation_start: int,
    validation_end: int,
    horizon_steps: int,
    frame_steps: int,
) -> tuple[int, ...]:
    """Return only validation origins whose complete targets stay in validation."""
    if not 0 <= validation_start <= validation_end <= record_samples:
        raise ValueError("validation bounds must lie inside the record")
    if horizon_steps < 1 or frame_steps < 1:
        raise ValueError("horizon and frame steps must be positive")
    return tuple(range(validation_start, validation_end - horizon_steps + 1, frame_steps))


def _common_validation_horizon(
    residuals_by_origin: Mapping[str, Mapping[int, tuple[float, ...]]],
) -> dict[str, Any]:
    """Pool every domain/arm initialization validation residual before one freeze."""
    pooled: dict[int, list[float]] = {600: [], 800: [], 1_000: []}
    for evidence_id in sorted(residuals_by_origin):
        for horizon_s, values in residuals_by_origin[evidence_id].items():
            pooled[horizon_s].extend(float(value) for value in values)
    scores = {
        horizon_s: float(np.mean(values)) if values else float(np.finfo(np.float64).max)
        for horizon_s, values in pooled.items()
    }
    selected = select_validation_horizon(scores)
    return {
        "selected_horizon_s": selected,
        "pooled_validation_scores": {
            str(horizon_s): scores[horizon_s] for horizon_s in sorted(scores)
        },
        "tie_rationale": "shortest horizon within 1% of pooled validation best",
    }


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """One immutable one-second trace, including requested and realized duty."""

    arm: str
    plant: str
    mode: str
    scenario: str
    seed: int
    initialization: str
    fan_fraction: tuple[float, ...]
    requested_q: tuple[float, ...]
    realized_q: tuple[float, ...]
    temperature_c: tuple[float, ...]
    target_c: tuple[float, ...]
    metrics: dict[str, float | int | None]
    mpc_horizon_s: int = 600
    raw_learner_ms: tuple[float, ...] = ()
    raw_refresh_ms: tuple[float, ...] = ()
    raw_solve_ms: tuple[float, ...] = ()
    provenance: str = "simulated-fixed-fan"
    solver_period_s: int = _FRAME_S
    horizon_residuals_c: Mapping[str, tuple[float, ...]] | None = None
    pre_recovery_residuals_c: Mapping[str, tuple[float, ...]] | None = None
    model_evidence: Mapping[str, Any] | None = None
    promotion_history: tuple[Mapping[str, Any], ...] = ()
    solver_evidence: tuple[Mapping[str, Any], ...] = ()
    evidence_id: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evidence_id",
            _evidence_id(self.arm, self.plant, self.seed, self.initialization),
        )
        object.__setattr__(self, "metrics", MappingProxyType(dict(sorted(self.metrics.items()))))
        for name in ("horizon_residuals_c", "pre_recovery_residuals_c"):
            values_by_horizon = getattr(self, name) or {}
            object.__setattr__(
                self,
                name,
                MappingProxyType(
                    {
                        str(horizon): tuple(float(value) for value in values)
                        for horizon, values in sorted(values_by_horizon.items())
                    }
                ),
            )
        object.__setattr__(self, "model_evidence", MappingProxyType(dict(self.model_evidence or {})))
        object.__setattr__(
            self,
            "promotion_history",
            tuple(MappingProxyType(dict(item)) for item in self.promotion_history),
        )
        object.__setattr__(
            self,
            "solver_evidence",
            tuple(MappingProxyType(dict(item)) for item in self.solver_evidence),
        )
    def to_document(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "evidence_id": self.evidence_id,
            "fan_fraction": list(self.fan_fraction),
            "initialization": self.initialization,
            "mpc_horizon_s": self.mpc_horizon_s,
            "metrics": dict(self.metrics),
            "mode": self.mode,
            "plant": self.plant,
            "prediction_residuals_c": {
                horizon: list(values) for horizon, values in (self.horizon_residuals_c or {}).items()
            },
            "pre_recovery_residuals_c": {
                horizon: list(values) for horizon, values in (self.pre_recovery_residuals_c or {}).items()
            },
            "model_evidence": dict(self.model_evidence or {}),
            "promotion_history": [dict(item) for item in self.promotion_history],
            "provenance": self.provenance,
            "raw_timing_ms": {
                "learner": list(self.raw_learner_ms),
                "refresh": list(self.raw_refresh_ms),
                "solve": list(self.raw_solve_ms),
            },
            "solver_evidence": [dict(item) for item in self.solver_evidence],
            "realized_q": list(self.realized_q),
            "requested_q": list(self.requested_q),
            "scenario": self.scenario,
            "seed": self.seed,
            "solver_period_s": self.solver_period_s,
            "target_c": list(self.target_c),
            "temperature_c": list(self.temperature_c),
        }


def run_tiny_scenario(*, plant: str, seed: int) -> ScenarioResult:
    """Run one tiny fixed-fan cold-start control trace used by smoke tests."""
    definition = next(item for item in quick_scenarios() if item.name == "low-step")
    return _run_scenario(
        definition,
        plant=plant,
        seed=seed,
        mode="frozen",
        duration_s=140,
        arm="scheduled-arx",
        initialization="wrong-gain",
    )


def run_experiment(config: ExperimentConfig | None = None, *, resume: bool = False) -> ExperimentArtifact:
    """Run the fixed three-arm matrix, atomically checkpointing every completed cell."""
    config = ExperimentConfig() if config is None else config
    return _run_matrix(config, checkpoint=config.output, resume=resume)


def run_tiny_matrix(
    directory: Path,
    *,
    resume: bool,
    interrupt_after: int | None = None,
    output: Path | None = None,
) -> ExperimentArtifact:
    """Run the quick three-arm matrix through the same checkpoint executor as full mode."""
    directory.mkdir(parents=True, exist_ok=True)
    return _run_matrix(
        ExperimentConfig.quick(),
        checkpoint=output if output is not None else directory / "checkpoint.json",
        resume=resume,
        interrupt_after=interrupt_after,
    )


def _run_matrix(
    config: ExperimentConfig,
    *,
    checkpoint: Path | None,
    resume: bool,
    interrupt_after: int | None = None,
) -> ExperimentArtifact:
    definitions = quick_scenarios() if config.quick_mode else SCENARIOS
    selection = _horizon_selection_document(config)
    horizon_s = int(selection["selected_horizon_s"])
    jobs = [
        (arm, initialization, definition, plant, mode, seed, horizon_s)
        for arm in ("scheduled-arx", "dmc", "state-space")
        for initialization in config.initializations
        for definition in definitions
        for plant in ("GrillSim", "MAKGrillSim")
        if plant in definition.applicable_plants
        for mode in ("frozen", "online")
        for seed in sorted(config.seeds)
    ]
    rows: list[ScenarioResult] = []
    failures = []
    if resume and checkpoint is not None and checkpoint.exists():
        checkpoint_document = json.loads(checkpoint.read_text(encoding="utf-8"))
        rows = [_scenario_from_document(row) for row in checkpoint_document.get("rows", ())]
        from .artifact import ArmFailure

        failures = [
            ArmFailure(
                item["arm"],
                item["scenario"],
                item["category"],
                item["detail"],
                MatrixKey(**item["matrix_key"]) if "matrix_key" in item else None,
            )
            for item in checkpoint_document.get("failures", ())
        ]
    completed = {
        (row.arm, row.initialization, row.scenario, row.plant, row.mode, row.seed, row.mpc_horizon_s)
        for row in rows
    }
    completed.update(
        (
            failure.matrix_key.arm,
            failure.matrix_key.initialization,
            failure.matrix_key.scenario,
            failure.matrix_key.plant,
            failure.matrix_key.mode,
            failure.matrix_key.seed,
            failure.matrix_key.mpc_horizon_s,
        )
        for failure in failures if failure.matrix_key is not None
    )
    for arm, initialization, definition, plant, mode, seed, horizon_s in jobs:
        key = (arm, initialization, definition.name, plant, mode, seed, horizon_s)
        if key in completed:
            continue
        try:
            row = _run_scenario(
                definition,
                plant=plant,
                seed=seed,
                mode=mode,
                duration_s=config.duration_s,
                arm=arm,
                initialization=initialization,
                horizon_s=horizon_s,
            )
            rows.append(row)
        except Exception as exc:
            from .artifact import ArmFailure

            failures.append(
                ArmFailure(
                    arm,
                    definition.name,
                    "non-finite/unstable",
                    f"{type(exc).__name__}: {exc}",
                    MatrixKey(arm, initialization, plant, mode, definition.name, seed, horizon_s),
                )
            )
        if checkpoint is not None:
            _write_checkpoint(checkpoint, config, rows, failures, complete=False)
        if interrupt_after is not None and len(rows) + len(failures) >= interrupt_after and not resume:
            return _artifact_from_rows(config, rows, failures)
    artifact = _artifact_from_rows(config, rows, failures)
    if checkpoint is not None:
        write_artifact_atomically(checkpoint, artifact)
    return artifact




def _scenario_from_document(document: dict[str, Any]) -> ScenarioResult:
    raw_timing = document.get("raw_timing_ms", {})
    row = ScenarioResult(
        arm=document["arm"],
        plant=document["plant"],
        scenario=document["scenario"],
        seed=document["seed"],
        mode=document["mode"],
        mpc_horizon_s=int(document.get("mpc_horizon_s", 600)),
        initialization=document["initialization"],
        fan_fraction=tuple(document["fan_fraction"]),
        requested_q=tuple(document["requested_q"]),
        realized_q=tuple(document["realized_q"]),
        temperature_c=tuple(document["temperature_c"]),
        target_c=tuple(document["target_c"]),
        metrics=document["metrics"],
        raw_learner_ms=tuple(raw_timing.get("learner", ())),
        raw_refresh_ms=tuple(raw_timing.get("refresh", ())),
        raw_solve_ms=tuple(raw_timing.get("solve", ())),
        horizon_residuals_c={
            str(horizon): tuple(values)
            for horizon, values in document.get("prediction_residuals_c", {}).items()
        },
        pre_recovery_residuals_c={
            str(horizon): tuple(values)
            for horizon, values in document.get("pre_recovery_residuals_c", {}).items()
        },
        provenance=document["provenance"],
        solver_period_s=document["solver_period_s"],
        model_evidence=document.get("model_evidence", {}),
        promotion_history=tuple(document.get("promotion_history", ())),
        solver_evidence=tuple(document.get("solver_evidence", ())),
    )
    if "evidence_id" in document and document["evidence_id"] != row.evidence_id:
        raise ValueError("scenario evidence_id does not match its prepared-model origin")
    return row


def write_artifact_atomically(path: Path, artifact: ExperimentArtifact) -> None:
    """Write final JSON through a same-directory temporary followed by ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(artifact.to_json() + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_checkpoint(
    path: Path,
    config: ExperimentConfig,
    rows: list[ScenarioResult],
    failures: list[Any],
    *,
    complete: bool,
) -> None:
    document = {
        "checkpoint": True,
        "complete": complete,
        "config": config.to_document(),
        "failures": [failure.to_document() for failure in failures],
        "rows": [row.to_document() for row in sorted(rows, key=_row_key)],
        "schema_version": 1,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()




def _run_scenario(
    definition: ScenarioDefinition,
    *,
    plant: str,
    seed: int,
    mode: str,
    duration_s: int,
    arm: str,
    initialization: str,
    horizon_s: int = 600,
) -> ScenarioResult:
    plant_type = {"GrillSim": GrillSim, "MAKGrillSim": MAKGrillSim}.get(plant)
    if plant_type is None:
        raise ValueError(f"unknown plant {plant!r}")
    if mode not in {"frozen", "online"}:
        raise ValueError(f"unknown mode {mode!r}")
    simulator = plant_type(seed=seed, fixed_fan=_FIXED_FAN)
    realizer = PulseRealizer(frame_s=_FRAME_S, quantum_s=5.0)
    mpc_config = MPCConfig(horizon_s=horizon_s, frame_s=_FRAME_S, tolerance=1e-3)
    controller = LinearMPC(mpc_config)
    model, fit_ms, before_residuals, after_residuals, calibration = _fitted_model(
        arm, plant, seed, initialization
    )
    batch_fit_snapshot = _json_value(model.snapshot())
    manager = (
        AdaptationManager(
            incumbent=model,
            challenger=deepcopy(model),
            replay_seed=seed,
        )
        if mode == "online"
        else None
    )
    temperatures: list[float] = []
    requested: list[float] = []
    realized: list[float] = []
    targets: list[float] = []
    fan: list[float] = []
    transitions = 0
    frame = realizer.frame(0.0)
    learner_ms: list[float] = []
    refresh_ms: list[float] = []
    solve_ms: list[float] = []
    solver_evidence: list[Mapping[str, Any]] = []
    promotion_history: list[Mapping[str, Any]] = []
    pre_assimilation_scores: deque[dict[str, float | bool | int]] = deque(maxlen=15)
    for second in range(duration_s):
        target = definition.target_at(second)
        if second % _FRAME_S == 0:
            observed_c = simulator.measured()
            active_model = manager.incumbent if manager is not None else model
            prediction = active_model.affine_prediction(
                mpc_config.horizon_steps,
                frame.requested_duty,
                np.full(mpc_config.horizon_steps, simulator.T_amb),
            )
            solve_started = perf_counter()
            solve = controller.solve(prediction, setpoint_c=target, q_previous=frame.requested_duty)
            elapsed_solve_ms = (perf_counter() - solve_started) * 1_000.0
            solve_ms.append(elapsed_solve_ms)
            if not np.isfinite(solve.objective) or not np.isfinite(solve.kkt_residual) or solve.kkt_residual > mpc_config.tolerance:
                raise ValueError(
                    f"solver certificate failed: kkt={solve.kkt_residual!r}, iterations={solve.iterations}"
                )
            evidence: dict[str, Any] = {
                "frame_s": second,
                "iterations": solve.iterations,
                "kkt_residual": solve.kkt_residual,
                "objective": solve.objective,
                "hessian_condition": solve.hessian_condition,
                "converged": True,
            }
            if second % 100 == 0:
                hessian, linear = condense_cost(
                    prediction, target, frame.requested_duty, mpc_config.weights
                )
                reference = _independent_box_qp_reference(
                    hessian, linear, solve.sequence_q
                )
                evidence.update(reference)
                if reference["reference_converged"]:
                    evidence["objective_gap"] = solve.objective - float(reference["reference_objective"])
                    evidence["kkt_gap"] = solve.kkt_residual - float(reference["reference_kkt_residual"])
            solver_evidence.append(evidence)
            frame = realizer.frame(float(solve.sequence_q[0]))
            transitions += frame.transitions
        auger_on = second % _FRAME_S < frame.on_seconds
        lid_open = definition.lid_open_at(second)
        simulator.step(auger_on, _FIXED_FAN, lid_open=lid_open)
        temperatures.append(simulator.measured())
        requested.append(frame.requested_duty)
        realized.append(frame.realized_duty)
        targets.append(target)
        fan.append(_FIXED_FAN)
        if manager is not None and (second + 1) % _FRAME_S == 0:
            observation = Observation(
                float(calibration.time_s[-1] + second + 1),
                temperatures[-1],
                frame.realized_duty,
                simulator.T_amb,
            )
            safety_override = definition.safety_override_at(second)
            manual_override = definition.manual_override_at(second)
            challenger_refresh_before = _refresh_marker(manager.challenger.snapshot())
            learner_started = perf_counter()
            outcome = manager.observe(
                observation,
                state=OperatingState.HOLD if target == definition.target_low_c else OperatingState.TRANSIENT,
                provenance="ordinary-cook",
                lid_open=lid_open,
                safety_override=safety_override,
                manual_override=manual_override,
            )
            observe_ms = (perf_counter() - learner_started) * 1_000.0
            challenger_refresh_after = _refresh_marker(manager.challenger.snapshot())
            if outcome.gate.permitted:
                if challenger_refresh_after != challenger_refresh_before:
                    refresh_ms.append(observe_ms)
                else:
                    learner_ms.append(observe_ms)
                if outcome.incumbent is None or outcome.challenger is None:
                    raise RuntimeError("permitted update must expose both pre-assimilation predictions")
                pre_assimilation_scores.append(
                    {
                        "frame_s": second + 1,
                        "role_generation": manager.role_generation,
                        "candidate_abs_error_c": abs(outcome.challenger.innovation_c),
                        "incumbent_abs_error_c": abs(outcome.incumbent.innovation_c),
                        "braking_or_coast": (
                            frame.realized_duty <= 0.05
                            or (second > 0 and target < targets[-2])
                        ),
                    }
                )
            if not outcome.gate.permitted:
                promotion_history.append(
                    {
                        "kind": "update-rejection",
                        "frame_s": second + 1,
                        "reasons": [reason.value for reason in outcome.gate.reasons],
                        "incumbent_updated": False,
                        "challenger_updated": False,
                    }
                )
            if (second + 1) % 300 == 0:
                score_window = tuple(
                    sample
                    for sample in pre_assimilation_scores
                    if sample["role_generation"] == manager.role_generation
                )
                pre_assimilation_scores.clear()
                if len(score_window) < 2:
                    continue
                braking_window = tuple(
                    sample for sample in score_window if sample["braking_or_coast"]
                )
                candidate_score = float(
                    np.mean([float(sample["candidate_abs_error_c"]) for sample in score_window])
                )
                incumbent_score = float(
                    np.mean([float(sample["incumbent_abs_error_c"]) for sample in score_window])
                )
                candidate_braking_score = (
                    float(
                        np.mean(
                            [
                                float(sample["candidate_abs_error_c"])
                                for sample in braking_window
                            ]
                        )
                    )
                    if braking_window
                    else None
                )
                incumbent_braking_score = (
                    float(
                        np.mean(
                            [
                                float(sample["incumbent_abs_error_c"])
                                for sample in braking_window
                            ]
                        )
                    )
                    if braking_window
                    else None
                )
                decision = manager.evaluate(
                    WindowScores(
                        window_id=f"{plant}:{arm}:{initialization}:{second + 1}",
                        candidate_prediction_score=candidate_score,
                        incumbent_prediction_score=incumbent_score,
                        candidate_braking_score=candidate_braking_score,
                        incumbent_braking_score=incumbent_braking_score,
                    )
                )
                promotion_history.append(
                    {
                        "kind": "five-minute-evaluation",
                        "window_id": decision.window_id,
                        "promoted": decision.promoted,
                        "reasons": [reason.value for reason in decision.reasons],
                        "consecutive_wins": decision.consecutive_wins,
                        "candidate_prediction_score": decision.candidate_prediction_score,
                        "incumbent_prediction_score": decision.incumbent_prediction_score,
                        "candidate_braking_score": decision.candidate_braking_score,
                        "incumbent_braking_score": decision.incumbent_braking_score,
                        "sample_count": len(score_window),
                        "braking_or_coast_sample_count": sum(
                            bool(sample["braking_or_coast"]) for sample in score_window
                        ),
                        "candidate_snapshot": _json_value(decision.candidate_snapshot),
                        "incumbent_snapshot": _json_value(decision.incumbent_snapshot),
                    }
                )
    metrics = _metrics(
        temperatures,
        targets,
        requested,
        realized,
        transitions,
        duration_s,
        learner_ms,
        refresh_ms,
        solve_ms,
        after_residuals,
        before_residuals,
        fit_ms,
        sum(1 for item in promotion_history if item.get("promoted") is True),
    )
    active_model = manager.incumbent if manager is not None else model
    return ScenarioResult(
        arm=arm,
        plant=plant,
        scenario=definition.name,
        seed=seed,
        mpc_horizon_s=horizon_s,
        mode=mode,
        initialization=initialization,
        fan_fraction=tuple(fan),
        requested_q=tuple(requested),
        realized_q=tuple(realized),
        temperature_c=tuple(temperatures),
        target_c=tuple(targets),
        metrics=metrics,
        raw_learner_ms=tuple(learner_ms),
        raw_refresh_ms=tuple(refresh_ms),
        raw_solve_ms=tuple(solve_ms),
        horizon_residuals_c={str(horizon): tuple(values) for horizon, values in after_residuals.items()},
        pre_recovery_residuals_c={str(horizon): tuple(values) for horizon, values in before_residuals.items()},
        provenance="simulated-fixed-fan",
        model_evidence={
            "calibration_provenance": calibration.provenance,
            "calibration_metadata": dict(calibration.metadata),
            "batch_fit_snapshot": batch_fit_snapshot,
            "final_active_snapshot": _json_value(active_model.snapshot()),
            "simulator_prediction_diagnostics": _simulator_prediction_diagnostics(
                arm, plant, seed, initialization
            ),
            "initial_batch_fit_ms": fit_ms,
        },
        promotion_history=tuple(promotion_history),
        solver_evidence=tuple(solver_evidence),
    )
def _fitted_model(arm: str, plant: str, seed: int, initialization: str):
    model, fit_ms, before, after, record = _prepared_model(arm, plant, seed, initialization)
    return (
        deepcopy(model),
        fit_ms,
        {horizon: list(values) for horizon, values in before.items()},
        {horizon: list(values) for horizon, values in after.items()},
        record,
    )


@lru_cache(maxsize=None)
def _prepared_model(arm: str, plant: str, seed: int, initialization: str):
    """Fit a domain-specific model, validate without leakage, then evaluate test."""
    record, initialized_record = _identification_records(plant, seed, initialization)
    samples = record.temp_c.size
    fit_end = int(samples * 0.35)
    validation_end = int(samples * 0.75)
    if min(fit_end, validation_end - fit_end, samples - validation_end) < 2:
        raise ValueError(f"{plant} calibration record cannot support chronological splits")
    model = _model_for_initialization(arm, initialization)
    fit_record = _record_slice(initialized_record, 0, fit_end)
    started = perf_counter()
    model.fit(fit_record)
    fit_ms = (perf_counter() - started) * 1_000.0
    before = {
        horizon_s: _horizon_residuals(
            model,
            record,
            starts=_validation_origins(
                record_samples=samples,
                validation_start=fit_end,
                validation_end=validation_end,
                horizon_steps=horizon_s // _FRAME_S,
                frame_steps=1,
            ),
            horizons_s=(horizon_s,),
        )[horizon_s]
        for horizon_s in (600, 800, 1_000)
    }
    for index in range(fit_end, validation_end):
        model.observe(
            Observation(
                float(record.time_s[index]),
                float(record.temp_c[index]),
                float(record.q[index]),
                float(record.ambient_c[index]),
            )
        )
    after = _horizon_residuals(
        model,
        record,
        starts=tuple(range(validation_end, samples, 1)),
        horizons_s=(600, 800, 1_000),
    )
    return model, fit_ms, before, after, record


@lru_cache(maxsize=None)
def _calibration_record(plant: str, seed: int) -> SignalRecord:
    program = DEFAULT_CALIBRATION_PROGRAM if plant == "GrillSim" else MAK_CALIBRATION_PROGRAM
    return generate_calibration_record(plant, seed, program)


def _identification_records(plant: str, seed: int, initialization: str) -> tuple[SignalRecord, SignalRecord]:
    """Return deterministic per-domain calibration evidence and an initial mismatch."""
    record = _calibration_record(plant, seed)
    if initialization == "correct":
        return record, record
    if initialization == "wrong-gain":
        q = record.q * 0.45
    elif initialization == "wrong-pole":
        q = record.q
    elif initialization == "wrong-delay":
        q = np.roll(record.q, 3)
    else:
        raise ValueError(f"unknown initialization {initialization!r}")
    temperatures = (
        record.ambient_c + (record.temp_c - record.ambient_c) * 1.6
        if initialization == "wrong-pole"
        else record.temp_c
    )
    return record, SignalRecord(
        record.time_s,
        temperatures,
        q,
        record.ambient_c,
        f"{initialization}-initialization",
        metadata={**record.metadata, "initialization": initialization},
    )


def _model_for_initialization(arm: str, initialization: str):
    if arm == "scheduled-arx":
        configs = {
            "correct": ARXConfig(na=2, nb=2, delays=(1, 2, 3), initial_covariance=10.0),
            "wrong-gain": ARXConfig(na=2, nb=2, delays=(1, 2, 3), initial_covariance=10.0),
            "wrong-pole": ARXConfig(na=2, nb=2, delays=(1, 2, 3), forgetting_factor=0.90),
            "wrong-delay": ARXConfig(na=2, nb=2, delays=(4, 5, 6)),
        }
        return ScheduledARX(configs[initialization])
    if arm == "dmc":
        configs = {
            "correct": DMCConfig(terms=(2, 3), poles=(0.3, 0.6), delay_seconds=(0, 20, 40)),
            "wrong-gain": DMCConfig(terms=(2, 3), poles=(0.3, 0.6), delay_seconds=(0, 20, 40), final_gain_bounds=(1e-6, 0.2)),
            "wrong-pole": DMCConfig(terms=(2, 3), poles=(0.05, 0.15), delay_seconds=(0, 20, 40)),
            "wrong-delay": DMCConfig(terms=(2, 3), poles=(0.3, 0.6), delay_seconds=(60, 80, 100)),
        }
        return LaguerreDMC(configs[initialization])
    if arm == "state-space":
        configs = {
            "correct": StateSpaceConfig(orders=(1,), delays=(2,), refresh_interval_s=300.0),
            "wrong-gain": StateSpaceConfig(orders=(1,), delays=(2,), parameter_penalty=0.1, refresh_interval_s=300.0),
            "wrong-pole": StateSpaceConfig(orders=(1,), delays=(2,), block_rows=12, refresh_interval_s=300.0),
            "wrong-delay": StateSpaceConfig(orders=(1, 2, 3), delays=(1, 3), refresh_interval_s=300.0),
        }
        return InnovationStateSpace(configs[initialization])
    raise ValueError(f"unknown arm {arm}")


def _initialization_snapshot(arm: str, initialization: str) -> dict[str, Any]:
    model = _model_for_initialization(arm, initialization)
    transform = {
        "correct": "calibrated deterministic record",
        "wrong-gain": "realized-duty × 0.45",
        "wrong-pole": "temperature-deviation × 1.6",
        "wrong-delay": "realized-duty shifted 3 frames",
    }[initialization]
    return {
        "initialization": initialization,
        "model_config": asdict(model._config),
        "record_transform": transform,
    }


def _record_slice(record: SignalRecord, begin: int, end: int) -> SignalRecord:
    return SignalRecord(
        record.time_s[begin:end],
        record.temp_c[begin:end],
        record.q[begin:end],
        record.ambient_c[begin:end],
        record.provenance,
        metadata=dict(record.metadata),
    )
def _horizon_residuals(
    model: Any,
    record: SignalRecord,
    *,
    starts: tuple[int, ...],
    horizons_s: tuple[int, ...],
) -> dict[int, list[float]]:
    """Score forecasts only where the full requested target lies in the segment."""
    residuals: dict[int, list[float]] = {}
    for horizon_s in horizons_s:
        steps = horizon_s // _FRAME_S
        values = []
        for start in starts:
            if start + steps > record.temp_c.size:
                continue
            prefix = _record_slice(record, 0, start)
            predicted = model.forecast(
                prefix,
                record.q[start : start + steps],
                record.ambient_c[start : start + steps],
            )
            values.append(float(np.mean(np.abs(predicted - record.temp_c[start : start + steps]))))
        residuals[horizon_s] = values
    return residuals


@lru_cache(maxsize=None)
def _simulator_prediction_diagnostics(
    arm: str, plant: str, seed: int, initialization: str
) -> dict[str, Any]:
    """Publish raw untouched simulator residuals and horizon metrics per model fit."""
    model, _, _, _, record = _prepared_model(arm, plant, seed, initialization)
    test_start = int(record.temp_c.size * 0.75)
    diagnostics: dict[str, Any] = {}
    for horizon_s in (60, 300, 900, 1800, 3600):
        steps = horizon_s // _FRAME_S
        origins: list[dict[str, Any]] = []
        residuals: list[float] = []
        coast_residuals: list[float] = []
        for start in range(test_start, record.temp_c.size - steps + 1, max(1, steps // 6)):
            predicted = model.forecast(
                _record_slice(record, 0, start),
                record.q[start : start + steps],
                record.ambient_c[start : start + steps],
            )
            errors = predicted - record.temp_c[start : start + steps]
            values = [float(value) for value in errors]
            residuals.extend(values)
            coast = bool(np.any(record.q[start : start + steps] <= 0.05))
            if coast:
                coast_residuals.extend(values)
            origins.append(
                {
                    "origin_index": start,
                    "origin_time_s": float(record.time_s[start]),
                    "coast_or_braking": coast,
                    "residuals_c": values,
                }
            )
        if not residuals:
            diagnostics[str(horizon_s)] = None
            continue
        values = np.asarray(residuals, dtype=np.float64)
        coast_values = np.asarray(
            coast_residuals if coast_residuals else residuals, dtype=np.float64
        )
        diagnostics[str(horizon_s)] = {
            "origins": origins,
            "rmse_c": float(np.sqrt(np.mean(values * values))),
            "max_abs_error_c": float(np.max(np.abs(values))),
            "bias_c": float(np.mean(values)),
            "p90_abs_error_c": float(np.percentile(np.abs(values), 90.0)),
            "coast_braking_temperature_error_c": float(
                np.mean(np.abs(coast_values))
            ),
            "steady_gain_error_c_per_q": _steady_gain_error(model.snapshot(), record),
            "delay_error_s": _delay_error_s(model.snapshot(), record),
        }
    return diagnostics


def _steady_gain_error(snapshot: Mapping[str, object], record: SignalRecord) -> float:
    """Compare model gain with a tail finite-difference measured from this simulator record."""
    half = record.temp_c.size // 2
    observed_delta_q = float(np.mean(record.q[half:]) - np.mean(record.q[:half]))
    observed = (
        float(np.mean(record.temp_c[half:]) - np.mean(record.temp_c[:half]))
        / observed_delta_q
        if abs(observed_delta_q) > 1e-9
        else 0.0
    )
    fitted = snapshot.get("steady_gain")
    if not isinstance(fitted, (int, float)):
        fitted = snapshot.get("final_gain", 0.0)
    return float(abs(float(fitted) - observed))


def _delay_error_s(snapshot: Mapping[str, object], record: SignalRecord) -> float:
    """Estimate a deterministic input/output lag from the held-out simulator suffix."""
    delay_steps = snapshot.get("delay_steps", 0)
    fitted = int(delay_steps) if isinstance(delay_steps, (int, float)) else 0
    start = int(record.temp_c.size * 0.75)
    inputs = np.diff(record.q[start:])
    outputs = np.diff(record.temp_c[start:])
    if not np.any(np.abs(inputs) > 1e-9) or not np.any(np.abs(outputs) > 1e-9):
        return float(fitted * _FRAME_S)
    limit = min(15, inputs.size - 1)
    observed = max(
        range(limit + 1),
        key=lambda lag: abs(float(np.dot(inputs[: inputs.size - lag], outputs[lag:]))),
    )
    return float(abs(fitted - observed) * _FRAME_S)


def _refresh_marker(snapshot: Mapping[str, object]) -> tuple[int, float | None]:
    """Identify either an accepted refresh or a measured re-identification attempt."""
    direct = snapshot.get("refreshes")
    count = direct if isinstance(direct, int) else 0
    timing = snapshot.get("update_timing")
    if isinstance(timing, Mapping):
        nested = timing.get("refreshes")
        if isinstance(nested, int):
            count = nested
        attempt = timing.get("last_attempt_time_s")
        if isinstance(attempt, (int, float)):
            return count, float(attempt)
    return count, None


def _row_key(row: ScenarioResult) -> tuple[str, str, str, str, str, int, int]:
    return (row.arm, row.initialization, row.plant, row.scenario, row.mode, row.seed, row.mpc_horizon_s)


def _evidence_id(arm: str, plant: str, seed: int, initialization: str) -> str:
    """Return the immutable identity of one plant-specific prepared-model origin."""
    return f"{arm}:{plant}:{seed}:{initialization}"


def _horizon_selection_document(config: ExperimentConfig) -> dict[str, Any]:
    """Freeze one horizon from pooled, validation-only, per-domain scores."""
    residuals: dict[str, Mapping[int, tuple[float, ...]]] = {}
    for arm in ("scheduled-arx", "dmc", "state-space"):
        for plant in ("GrillSim", "MAKGrillSim"):
            for initialization in config.initializations:
                for seed in sorted(config.seeds):
                    try:
                        _, _, validation_residuals, _, _ = _prepared_model(
                            arm, plant, seed, initialization
                        )
                    except (ValueError, np.linalg.LinAlgError):
                        continue
                    residuals[_evidence_id(arm, plant, seed, initialization)] = {
                        horizon: tuple(values)
                        for horizon, values in validation_residuals.items()
                    }
    document = _common_validation_horizon(residuals)
    document["origins"] = {
        origin: {str(horizon): list(values) for horizon, values in scores.items()}
        for origin, scores in sorted(residuals.items())
    }
    return document

def _metrics(
    temperatures: list[float],
    targets: list[float],
    requested: list[float],
    realized: list[float],
    transitions: int,
    duration_s: int,
    learner_ms: list[float],
    refresh_ms: list[float],
    solve_ms: list[float],
    residuals: dict[int, list[float]],
    before_residuals: dict[int, list[float]],
    initial_fit_ms: float,
    promotion_events: int,
) -> dict[str, float | int | None]:
    error = np.asarray(temperatures) - np.asarray(targets)
    absolute = np.abs(error)
    overshoot = float(np.max(error))
    undershoot = float(max(0.0, -np.min(error)))
    hold = np.asarray(temperatures[-min(60, len(temperatures)):])
    settled = next((index for index in range(len(error)) if np.all(absolute[index:] <= 3.0)), None)
    score = float(np.sqrt(np.mean(error**2)) + np.mean(absolute) + 0.5 * max(overshoot, 0.0))
    before_mae = _mean_or_none(before_residuals.get(600, ()))
    after_mae = _mean_or_none(residuals.get(600, ()))
    recovery_ratio = (
        after_mae / before_mae
        if before_mae is not None and after_mae is not None and before_mae > 0.0
        else None
    )
    return {
        "control_score": score,
        "deadline_misses": int(any(value > 250.0 for value in solve_ms)),
        "iae_c_s": float(np.sum(absolute)),
        "mean_abs_error_c": float(np.mean(absolute)),
        "overshoot_c": overshoot,
        "peak_to_peak_hold_c": float(np.ptp(hold)),
        "prediction_mae_c": float(np.mean(absolute)),
        "prediction_residual_1000_c": _mean_or_none(residuals.get(1_000, ())),
        "prediction_residual_600_c": _mean_or_none(residuals.get(600, ())),
        "prediction_residual_800_c": _mean_or_none(residuals.get(800, ())),
        "promotion_events": promotion_events,
        "initial_batch_fit_ms": initial_fit_ms,
        "raw_learner_p99_ms": _p99(learner_ms),
        "raw_refresh_p99_ms": _p99(refresh_ms),
        "raw_solve_p99_ms": _p99(solve_ms),
        "recovery_after_mae_c": after_mae,
        "recovery_before_mae_c": before_mae,
        "recovery_improvement_delta_c": (
            before_mae - after_mae if before_mae is not None and after_mae is not None else None
        ),
        "recovery_improvement_ratio": recovery_ratio,
        "requested_realized_duty_mae": float(np.mean(np.abs(np.asarray(requested) - np.asarray(realized)))),
        "rmse_c": float(np.sqrt(np.mean(error**2))),
        "settling_s": settled,
        "transitions_per_hour": float(transitions * 3600.0 / duration_s),
        "undershoot_c": undershoot,
    }


def _mean_or_none(values: list[float] | tuple[float, ...]) -> float | None:
    return float(np.mean(values)) if values else None


def _metric_float(metrics: dict[str, float | int | None], name: str) -> float:
    value = metrics[name]
    if not isinstance(value, (float, int)):
        raise ValueError(f"{name} must be numeric")
    return float(value)




def _json_value(value: Any) -> Any:
    """Convert model snapshots to immutable-artifact JSON scalars and sequences."""
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, np.ndarray):
        return [_json_value(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    return value
def _p99(values: list[float] | tuple[float, ...]) -> float:
    return float(np.percentile(values, 99.0)) if values else 0.0


def _independent_box_qp_reference(
    hessian: np.ndarray, linear: np.ndarray, start: np.ndarray
) -> dict[str, Any]:
    """Use SciPy L-BFGS-B, independent of the controller's projected gradient."""
    def objective(x: np.ndarray) -> float:
        return float(0.5 * x @ hessian @ x + linear @ x)

    def jacobian(x: np.ndarray) -> np.ndarray:
        return hessian @ x + linear

    try:
        result = minimize(
            objective,
            np.asarray(start, dtype=np.float64),
            jac=jacobian,
            method="L-BFGS-B",
            bounds=[(0.0, 1.0)] * linear.size,
            options={"ftol": 1e-14, "gtol": 1e-10, "maxiter": 100_000},
        )
        x = np.asarray(result.x, dtype=np.float64)
        gradient = jacobian(x)
        projected = np.where(
            x <= 1e-9,
            np.minimum(gradient, 0.0),
            np.where(x >= 1.0 - 1e-9, np.maximum(gradient, 0.0), gradient),
        )
        kkt = float(np.max(np.abs(projected)))
        converged = bool(result.success and np.isfinite(result.fun) and kkt <= 1e-7)
        document: dict[str, Any] = {
            "reference_method": "scipy-l-bfgs-b",
            "reference_converged": converged,
            "reference_iterations": int(result.nit),
            "reference_kkt_residual": kkt,
        }
        if converged:
            document["reference_objective"] = float(result.fun)
            document["reference_move_gap"] = float(np.max(np.abs(x - start)))
        else:
            document["reference_failure"] = str(result.message)
        return document
    except Exception as error:
        return {
            "reference_method": "scipy-l-bfgs-b",
            "reference_converged": False,
            "reference_failure": f"{type(error).__name__}: {error}",
        }


def _artifact_from_rows(config: ExperimentConfig, rows: list[ScenarioResult], failures=()) -> ExperimentArtifact:
    by_arm: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    prediction_origins: dict[str, dict[str, dict[str, tuple[float, ...]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    recovery: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    timings: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    seen_evidence: set[tuple[str, str]] = set()
    correct_scores: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for row in rows:
        domain = f"{row.mode}:{row.initialization}:{row.plant}"
        by_arm[row.arm][domain].append(_metric_float(row.metrics, "control_score"))
        if row.initialization == "correct":
            correct_scores[(row.arm, row.plant, row.mode)].append(
                _metric_float(row.metrics, "control_score")
            )
        evidence_key = (row.arm, row.evidence_id)
        if evidence_key not in seen_evidence:
            seen_evidence.add(evidence_key)
            for horizon, values in (row.horizon_residuals_c or {}).items():
                prediction_origins[row.arm][horizon][row.evidence_id] = values
            for metric in (
                "recovery_before_mae_c",
                "recovery_after_mae_c",
                "recovery_improvement_ratio",
                "recovery_improvement_delta_c",
            ):
                value = row.metrics.get(metric)
                if isinstance(value, (float, int)):
                    recovery[row.arm][metric].append(float(value))
        timings[row.arm]["learner"].extend(row.raw_learner_ms)
        timings[row.arm]["refresh"].extend(row.raw_refresh_ms)
        timings[row.arm]["solve"].extend(row.raw_solve_ms)
    arms = tuple(
        ArmEvidence(
            name=arm,
            domain_median_scores={
                domain: float(median(scores)) for domain, scores in sorted(domains.items())
            },
            ranking_domain_scores={
                plant: float(median(scores))
                for (candidate_arm, plant, mode), scores in sorted(correct_scores.items())
                if candidate_arm == arm and mode == "online"
            },
            correct_baseline_no_degradation=all(
                float(median(scores))
                <= float(median(correct_scores[(arm, plant, "frozen")])) * 1.01
                for (candidate_arm, plant, mode), scores in correct_scores.items()
                if candidate_arm == arm
                and mode == "online"
                and (arm, plant, "frozen") in correct_scores
            ),
            prediction_error=_mean_or_none(
                _flatten_origins(prediction_origins[arm].get("600", {}))
            )
            or 0.0,
            before_mae=_mean_or_none(recovery[arm]["recovery_before_mae_c"]) or 0.0,
            after_mae=_mean_or_none(recovery[arm]["recovery_after_mae_c"]) or 0.0,
            recovery_improvement_ratio=_mean_or_none(recovery[arm]["recovery_improvement_ratio"])
            or 0.0,
            recovery_improvement_delta=_mean_or_none(recovery[arm]["recovery_improvement_delta_c"])
            or 0.0,
            raw_solve_p99_ms=_p99(timings[arm]["solve"]),
            raw_learner_ms=tuple(timings[arm]["learner"]),
            raw_refresh_ms=tuple(timings[arm]["refresh"]),
            raw_solve_ms=tuple(timings[arm]["solve"]),
        )
        for arm, domains in sorted(by_arm.items())
    )
    horizon_evidence = {
        arm.name: {
            "mpc_validation_candidates": {
                horizon: {
                    "bootstrap_ci": _bootstrap_ci(prediction_origins[arm.name].get(horizon, {})),
                    "residuals_c": _flatten_origins(prediction_origins[arm.name].get(horizon, {})),
                }
                for horizon in ("600", "800", "1000")
            },
            "real": _real_mak_evidence(arm.name),
        }
        for arm in arms
    }
    selection = _horizon_selection_document(config)
    return ExperimentArtifact(
        config={
            **config.to_document(),
            "horizon_selection": selection,
            "horizon_selection_window": "per-domain chronological validation partitions",
            "horizon_tie_rule": "shortest horizon within 1% of pooled validation best",
        },
        seeds=config.seeds,
        splits=_split_evidence(config),
        model_snapshots={
            "configured_arms": [arm.name for arm in arms],
            "modes": ["frozen", "online"],
            "initializations": {
                arm: {
                    initialization: _initialization_snapshot(arm, initialization)
                    for initialization in config.initializations
                }
                for arm in ("scheduled-arx", "dmc", "state-space")
            },
            "fitted_by_domain": {
                row.evidence_id: dict(row.model_evidence or {})
                for row in rows
            },
        },
        scenarios=tuple(rows),
        arms=arms,
        failures=tuple(failures),
        source_revision=_source_revision(),
        environment=_environment_versions(),
        horizon_evidence=horizon_evidence,
    )


@lru_cache(maxsize=None)
def _real_mak_record() -> SignalRecord:
    fixture = Path(__file__).parents[4] / "tests/unit/mpc/fixtures/mak_cook_2026-08-02.csv"
    return resample_record(reconstruct_mak_fixture(fixture), _FRAME_S)


@lru_cache(maxsize=None)
def _real_mak_evidence(arm: str) -> dict[str, Any]:
    """Keep compact-model selection, validation, and test chronology disjoint."""
    record = _real_mak_record()
    fit_end, validation_end = _real_mak_boundaries(record.temp_c.size)
    result: dict[str, Any] = {
        "provenance": record.provenance,
        "metadata": dict(record.metadata),
        "boundaries": {
            "fit": [0, fit_end],
            "validation": [fit_end, validation_end],
            "test": [validation_end, int(record.temp_c.size)],
        },
        "diagnostics_c": {"60": None, "300": None, "900": None, "1800": None, "3600": None},
    }
    try:
        fit_record = _record_slice(record, 0, fit_end)
        validation_starts = tuple(range(fit_end, validation_end))
        candidates: tuple[Any, ...]
        if arm == "state-space":
            candidates = (
                InnovationStateSpace(
                    StateSpaceConfig(
                        orders=(1,),
                        delays=(1,),
                        block_rows=2,
                        refresh_interval_s=1e12,
                    )
                ),
                InnovationStateSpace(
                    StateSpaceConfig(
                        orders=(1,),
                        delays=(2,),
                        block_rows=2,
                        refresh_interval_s=1e12,
                    )
                ),
            )
        else:
            candidates = (_model_for_initialization(arm, "correct"),)
        scored_candidates: list[tuple[float, Any]] = []
        for candidate in candidates:
            candidate.fit(fit_record)
            validation = _horizon_residuals(
                candidate, record, starts=validation_starts, horizons_s=(60, 300)
            )
            score = _mean_or_none(validation[300]) or _mean_or_none(validation[60])
            if score is not None:
                scored_candidates.append((score, candidate))
        if not scored_candidates:
            raise ValueError("fixture validation segment cannot score compact candidates")
        _, model = min(scored_candidates, key=lambda item: item[0])
        diagnostics = _horizon_residuals(
            model,
            record,
            starts=tuple(range(validation_end, record.temp_c.size)),
            horizons_s=(60, 300, 900, 1800, 3600),
        )
        result["diagnostics_c"] = {
            str(horizon): _mean_or_none(values)
            for horizon, values in sorted(diagnostics.items())
        }
        result["origins"] = {
            str(horizon): list(values) for horizon, values in sorted(diagnostics.items())
        }
        result["validation_candidate_scores"] = [
            float(score) for score, _ in scored_candidates
        ]
        result["fitted"] = _json_value(model.snapshot())
    except Exception as error:
        result["failure"] = f"{type(error).__name__}: {error}"
    return result


def _real_mak_boundaries(samples: int) -> tuple[int, int]:
    """Reserve a contiguous test tail; never borrow it for compact-model choice."""
    if samples < 6:
        raise ValueError("fixture cannot support fit, validation, and test segments")
    fit_end = min(16, samples - 4)
    validation_end = min(fit_end + 19, samples - 2)
    if not 2 <= fit_end < validation_end < samples:
        raise ValueError("fixture cannot support chronological fit, validation, and test")
    return fit_end, validation_end

def _split_evidence(config: ExperimentConfig) -> dict[str, Any]:
    """Persist actual index/time boundaries for every immutable domain record."""
    domains: dict[str, Any] = {}
    for plant in ("GrillSim", "MAKGrillSim"):
        for seed in sorted(config.seeds):
            record = _calibration_record(plant, seed)
            fit_end = int(record.temp_c.size * 0.35)
            validation_end = int(record.temp_c.size * 0.75)
            domains[f"{plant}:{seed}"] = {
                "fit": [0, fit_end],
                "validation": [fit_end, validation_end],
                "test": [validation_end, int(record.temp_c.size)],
                "provenance": record.provenance,
            }
    real = _real_mak_record()
    fit_end, validation_end = _real_mak_boundaries(real.temp_c.size)
    domains["real-MAK"] = {
        "fit": [0, fit_end],
        "validation": [fit_end, validation_end],
        "test": [validation_end, int(real.temp_c.size)],
        "provenance": real.provenance,
        "diagnostic_horizons_s": [60, 300, 900, 1800, 3600],
    }
    return domains


def _flatten_origins(origins: Mapping[str, tuple[float, ...]]) -> list[float]:
    return [value for evidence_id in sorted(origins) for value in origins[evidence_id]]


def _bootstrap_ci(origins: Mapping[str, tuple[float, ...]]) -> list[float]:
    populated = tuple(values for _, values in sorted(origins.items()) if values)
    if not populated:
        return [0.0, 0.0]
    generator = np.random.default_rng(0)
    samples = [
        float(np.mean([value for index in generator.integers(len(populated), size=len(populated)) for value in populated[index]]))
        for _ in range(1_000)
    ]
    return [float(np.percentile(samples, 2.5)), float(np.percentile(samples, 97.5))]


def _source_revision() -> str:
    import subprocess
    try:
        return subprocess.check_output(["jj", "--no-pager", "log", "-r", "@", "--no-graph", "-T", "commit_id"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return os.environ.get("PIFIRE_REVISION", "unavailable")


def _environment_versions() -> dict[str, str]:
    versions = {"python": sys.version.split()[0]}
    for package in ("numpy", "scipy", "pydantic"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "unavailable"
    return versions


def default_output_path() -> Path:
    return _DEFAULT_OUTPUT
