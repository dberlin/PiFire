"""Resumable, deterministic closed-loop evidence runner for the model bake-off."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import importlib.metadata
import json
import os
import sys
from collections import defaultdict
from time import perf_counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import MappingProxyType
from statistics import median
from typing import Any, Mapping
import numpy as np


from controller.grill_sim import GrillSim, MAKGrillSim

from .actuation import PulseRealizer
from .arx import ARXConfig, ScheduledARX
from .dmc import DMCConfig, LaguerreDMC
from .state_space import InnovationStateSpace, StateSpaceConfig
from .artifact import ArmEvidence, ExperimentArtifact, MatrixKey
from .contracts import AffinePrediction
from .contracts import Observation, SignalRecord
from .linear_mpc import LinearMPC, MPCConfig, select_validation_horizon
from .scenarios import SCENARIOS, ScenarioDefinition, quick_scenarios

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
    initializations: tuple[str, ...] = ("wrong-gain", "wrong-pole", "wrong-delay")
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
        return cls(quick_mode=True, seeds=(2,), duration_s=140)

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
    """Freeze a horizon from the pre-test validation-window residual scores only."""
    scores = {
        horizon_s: float(np.mean(np.abs(values)))
        for horizon_s, values in residuals_by_horizon.items()
    }
    selected = select_validation_horizon(scores)
    best = min(scores.values())
    within_one_percent = scores[selected] <= best * 1.01
    return {
        "selected_horizon_s": selected,
        "tie_rationale": (
            f"{selected} seconds is within 1% of the validation best"
            if within_one_percent and selected != min(scores, key=scores.get)
            else f"{selected} seconds is the validation best"
        ),
        "validation_scores": {str(horizon_s): scores[horizon_s] for horizon_s in sorted(scores)},
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
    evidence_id: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _evidence_id(self.arm, self.seed, self.initialization))
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
            "provenance": self.provenance,
            "raw_timing_ms": {
                "learner": list(self.raw_learner_ms),
                "refresh": list(self.raw_refresh_ms),
                "solve": list(self.raw_solve_ms),
            },
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
    selections = _horizon_selection_document(config)
    jobs = [
        (arm, initialization, definition, plant, mode, seed, selections[_evidence_id(arm, seed, initialization)]["selected_horizon_s"])
        for arm in ("scheduled-arx", "dmc", "state-space")
        for initialization in config.initializations
        for plant in ("GrillSim", "MAKGrillSim")
        for mode in ("frozen", "online")
        for definition in definitions
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
    mpc_config = MPCConfig(horizon_s=horizon_s, frame_s=_FRAME_S)
    controller = LinearMPC(mpc_config)
    model, fit_ms, before_residuals, after_residuals = _fitted_model(arm, seed, initialization)
    temperatures: list[float] = []
    requested: list[float] = []
    realized: list[float] = []
    targets: list[float] = []
    fan: list[float] = []
    transitions = 0
    integral_error = 0.0
    frame = realizer.frame(0.0)
    solve_ms: list[float] = []
    refresh_ms: list[float] = []
    for second in range(duration_s):
        target = definition.target_at(second)
        if second % _FRAME_S == 0:
            observed_c = simulator.measured()
            if mode == "online":
                integral_error = float(np.clip(integral_error + (target - observed_c) * _FRAME_S, -2_000.0, 2_000.0))
            solve_started = perf_counter()
            prediction = model.affine_prediction(
                mpc_config.horizon_steps,
                frame.requested_duty,
                np.full(mpc_config.horizon_steps, simulator.T_amb),
            )
            solve = controller.solve(prediction, setpoint_c=target, q_previous=frame.requested_duty)
            solve_ms.append((perf_counter() - solve_started) * 1_000.0)
            frame = realizer.frame(float(solve.sequence_q[0]))
            transitions += frame.transitions
        auger_on = second % _FRAME_S < frame.on_seconds
        simulator.step(auger_on, _FIXED_FAN, lid_open=definition.lid_open_at(second))
        temperatures.append(simulator.measured())
        requested.append(frame.requested_duty)
        realized.append(frame.realized_duty)
        targets.append(target)
        fan.append(_FIXED_FAN)
        if mode == "online" and second % _FRAME_S == 0:
            refresh_started = perf_counter()
            model.observe(Observation(12_000.0 + float(second), temperatures[-1], frame.realized_duty, simulator.T_amb))
            refresh_ms.append((perf_counter() - refresh_started) * 1_000.0)
    metrics = _metrics(
        temperatures,
        targets,
        requested,
        realized,
        transitions,
        duration_s,
        fit_ms,
        refresh_ms,
        solve_ms,
        after_residuals,
        before_residuals,
    )
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
        raw_learner_ms=(fit_ms,),
        raw_refresh_ms=tuple(refresh_ms),
        raw_solve_ms=tuple(solve_ms),
        horizon_residuals_c={str(horizon): tuple(values) for horizon, values in after_residuals.items()},
        pre_recovery_residuals_c={str(horizon): tuple(values) for horizon, values in before_residuals.items()},
    )
def _fitted_model(arm: str, seed: int, initialization: str):
    model, fit_ms, before, after = _prepared_model(arm, seed, initialization)
    return (
        deepcopy(model),
        fit_ms,
        {horizon: list(values) for horizon, values in before.items()},
        {horizon: list(values) for horizon, values in after.items()},
    )


@lru_cache(maxsize=None)
def _prepared_model(arm: str, seed: int, initialization: str):
    """Fit chronologically before all evaluation windows, then recover on later observations."""
    record, wrong_record = _identification_records(seed, initialization)
    fit_end = 360
    model = _model_for_initialization(arm, initialization)
    fit_record = _record_slice(
        record if arm == "state-space" and initialization == "wrong-delay" else wrong_record,
        0,
        fit_end,
    )
    started = perf_counter()
    model.fit(fit_record)
    fit_ms = (perf_counter() - started) * 1_000.0
    before = _horizon_residuals(model, record, starts=range(fit_end, 480, 20))
    for index in range(fit_end, 480):
        model.observe(
            Observation(
                float(record.time_s[index]),
                float(record.temp_c[index]),
                float(record.q[index]),
                float(record.ambient_c[index]),
            )
        )
    after = _horizon_residuals(model, record, starts=range(480, 600, 20))
    return model, fit_ms, before, after


def _identification_records(seed: int, initialization: str) -> tuple[SignalRecord, SignalRecord]:
    generator = np.random.default_rng(seed)
    samples = 600
    time_s = np.arange(samples, dtype=np.float64) * _FRAME_S
    q = generator.choice(np.array([0.05, 0.2, 0.45, 0.75]), samples)
    ambient = 20.0 + 1.5 * np.sin(time_s / 1_400.0)
    state = np.zeros(2)
    temperatures = np.empty(samples)
    delayed = np.pad(q, (2, 0))
    transition = np.array([[0.74, -0.18], [1.0, 0.0]])
    for index in range(samples):
        temperatures[index] = ambient[index] + state[0] + generator.normal(0.0, 0.015)
        state = transition @ state + np.array([0.9 * delayed[index], 0.0])
    record = SignalRecord(time_s, temperatures, q, ambient, "synthetic-identification")
    if initialization == "wrong-gain":
        wrong = SignalRecord(time_s, temperatures, q * 0.45, ambient, "wrong-gain-initialization")
    elif initialization == "wrong-pole":
        wrong = SignalRecord(time_s, ambient + (temperatures - ambient) * 1.6, q, ambient, "wrong-pole-initialization")
    elif initialization == "wrong-delay":
        wrong = SignalRecord(time_s, temperatures, np.roll(q, 3), ambient, "wrong-delay-initialization")
    else:
        raise ValueError(f"unknown initialization {initialization!r}")
    return record, wrong


def _model_for_initialization(arm: str, initialization: str):
    if arm == "scheduled-arx":
        configs = {
            "wrong-gain": ARXConfig(na=2, nb=2, delays=(1, 2, 3), initial_covariance=10.0),
            "wrong-pole": ARXConfig(na=2, nb=2, delays=(1, 2, 3), forgetting_factor=0.90),
            "wrong-delay": ARXConfig(na=2, nb=2, delays=(4, 5, 6)),
        }
        return ScheduledARX(configs[initialization])
    if arm == "dmc":
        configs = {
            "wrong-gain": DMCConfig(terms=(2, 3), poles=(0.3, 0.6), delay_seconds=(0, 20, 40), final_gain_bounds=(1e-6, 0.2)),
            "wrong-pole": DMCConfig(terms=(2, 3), poles=(0.05, 0.15), delay_seconds=(0, 20, 40)),
            "wrong-delay": DMCConfig(terms=(2, 3), poles=(0.3, 0.6), delay_seconds=(60, 80, 100)),
        }
        return LaguerreDMC(configs[initialization])
    if arm == "state-space":
        configs = {
            "wrong-gain": StateSpaceConfig(orders=(1,), delays=(2,), parameter_penalty=0.1),
            "wrong-pole": StateSpaceConfig(orders=(1,), delays=(2,), block_rows=12),
            "wrong-delay": StateSpaceConfig(orders=(1, 2, 3), delays=(1, 3)),
        }
        return InnovationStateSpace(configs[initialization])
    raise ValueError(f"unknown arm {arm}")


def _initialization_snapshot(arm: str, initialization: str) -> dict[str, Any]:
    model = _model_for_initialization(arm, initialization)
    transform = {
        "wrong-gain": "requested-duty × 0.45",
        "wrong-pole": "temperature-deviation × 1.6",
        "wrong-delay": "requested-duty shifted 3 frames",
    }[initialization]
    if arm == "state-space" and initialization == "wrong-delay":
        transform = "state-space candidates omit delay 2"
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
    )


def _horizon_residuals(model: Any, record: SignalRecord, *, starts: range) -> dict[int, list[float]]:
    residuals: dict[int, list[float]] = {}
    for horizon_s in (600, 800, 1_000):
        steps = horizon_s // _FRAME_S
        values = []
        for start in starts:
            if start + steps > record.temp_c.size:
                continue
            prefix = _record_slice(record, 0, start)
            predicted = model.forecast(prefix, record.q[start : start + steps], record.ambient_c[start : start + steps])
            values.append(float(np.mean(np.abs(predicted - record.temp_c[start : start + steps]))))
        residuals[horizon_s] = values
    return residuals


def _row_key(row: ScenarioResult) -> tuple[str, str, str, str, str, int, int]:
    return (row.arm, row.initialization, row.plant, row.scenario, row.mode, row.seed, row.mpc_horizon_s)


def _evidence_id(arm: str, seed: int, initialization: str) -> str:
    """Return the immutable identity of one prepared-model origin."""
    return f"{arm}:{seed}:{initialization}"


def _horizon_selection_document(config: ExperimentConfig) -> dict[str, dict[str, Any]]:
    """Record each arm/seed/wrong-model validation-only selection before testing."""
    selections = {}
    for arm in ("scheduled-arx", "dmc", "state-space"):
        for initialization in config.initializations:
            for seed in sorted(config.seeds):
                _, _, validation_residuals, _ = _prepared_model(arm, seed, initialization)
                selections[_evidence_id(arm, seed, initialization)] = _select_validation_horizon(validation_residuals)
    return dict(sorted(selections.items()))


def _p99(values: list[float]) -> float:
    return float(np.percentile(values, 99.0)) if values else 0.0


def _metrics(
    temperatures: list[float],
    targets: list[float],
    requested: list[float],
    realized: list[float],
    transitions: int,
    duration_s: int,
    fit_ms: float,
    refresh_ms: list[float],
    solve_ms: list[float],
    residuals: dict[int, list[float]],
    before_residuals: dict[int, list[float]],
) -> dict[str, float | int | None]:
    error = np.asarray(temperatures) - np.asarray(targets)
    absolute = np.abs(error)
    overshoot = float(np.max(error))
    undershoot = float(max(0.0, -np.min(error)))
    hold = np.asarray(temperatures[-min(60, len(temperatures)):])
    settled = next((index for index in range(len(error)) if np.all(absolute[index:] <= 3.0)), None)
    score = float(np.sqrt(np.mean(error**2)) + np.mean(absolute) + 0.5 * max(overshoot, 0.0))
    before_mae = float(np.mean(before_residuals[600]))
    after_mae = float(np.mean(residuals[600]))
    recovery_ratio = (
        after_mae / before_mae
        if before_mae > 0.0
        else (0.0 if after_mae == 0.0 else float(np.finfo(np.float64).max))
    )
    return {
        "control_score": score,
        "deadline_misses": int(any(value > 250.0 for value in solve_ms)),
        "iae_c_s": float(np.sum(absolute)),
        "mean_abs_error_c": float(np.mean(absolute)),
        "overshoot_c": overshoot,
        "peak_to_peak_hold_c": float(np.ptp(hold)),
        "prediction_mae_c": float(np.mean(absolute)),
        "prediction_residual_1000_c": float(np.mean(residuals[1_000])),
        "prediction_residual_600_c": float(np.mean(residuals[600])),
        "prediction_residual_800_c": float(np.mean(residuals[800])),
        "promotion_events": 0,
        "raw_learner_p99_ms": fit_ms,
        "raw_refresh_p99_ms": _p99(refresh_ms),
        "raw_solve_p99_ms": _p99(solve_ms),
        "recovery_after_mae_c": after_mae,
        "recovery_before_mae_c": before_mae,
        "recovery_improvement_delta_c": before_mae - after_mae,
        "recovery_improvement_ratio": recovery_ratio,
        "requested_realized_duty_mae": float(np.mean(np.abs(np.asarray(requested) - np.asarray(realized)))),
        "rmse_c": float(np.sqrt(np.mean(error**2))),
        "settling_s": settled,
        "transitions_per_hour": float(transitions * 3600.0 / duration_s),
        "undershoot_c": undershoot,
    }


def _metric_float(metrics: dict[str, float | int | None], name: str) -> float:
    value = metrics[name]
    if not isinstance(value, (float, int)):
        raise ValueError(f"{name} must be numeric")
    return float(value)


def _artifact_from_rows(config: ExperimentConfig, rows: list[ScenarioResult], failures=()) -> ExperimentArtifact:
    by_arm: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    predictions: dict[str, list[float]] = defaultdict(list)
    before_maes: dict[str, list[float]] = defaultdict(list)
    after_maes: dict[str, list[float]] = defaultdict(list)
    recovery_ratios: dict[str, list[float]] = defaultdict(list)
    recovery_deltas: dict[str, list[float]] = defaultdict(list)
    horizon_origins: dict[str, dict[str, dict[str, tuple[float, ...]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    seen_evidence: set[tuple[str, str]] = set()
    learner_samples: dict[str, list[float]] = defaultdict(list)
    refresh_samples: dict[str, list[float]] = defaultdict(list)
    solve_samples: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_arm[row.arm][row.plant].append(_metric_float(row.metrics, "control_score"))
        evidence_key = (row.arm, row.evidence_id)
        if evidence_key not in seen_evidence:
            seen_evidence.add(evidence_key)
            predictions[row.arm].extend((row.horizon_residuals_c or {})["600"])
            before_maes[row.arm].append(_metric_float(row.metrics, "recovery_before_mae_c"))
            after_maes[row.arm].append(_metric_float(row.metrics, "recovery_after_mae_c"))
            recovery_ratios[row.arm].append(_metric_float(row.metrics, "recovery_improvement_ratio"))
            recovery_deltas[row.arm].append(_metric_float(row.metrics, "recovery_improvement_delta_c"))
            for horizon in ("600", "800", "1000"):
                horizon_origins[row.arm][horizon][row.evidence_id] = (row.horizon_residuals_c or {})[horizon]
        learner_samples[row.arm].extend(row.raw_learner_ms)
        refresh_samples[row.arm].extend(row.raw_refresh_ms)
        solve_samples[row.arm].extend(row.raw_solve_ms)
    arms = tuple(
        ArmEvidence(
            name=arm,
            domain_median_scores={plant: float(median(scores)) for plant, scores in sorted(domains.items())},
            prediction_error=float(np.mean(predictions[arm])),
            before_mae=float(np.mean(before_maes[arm])),
            after_mae=float(np.mean(after_maes[arm])),
            recovery_improvement_ratio=float(np.mean(recovery_ratios[arm])),
            recovery_improvement_delta=float(np.mean(recovery_deltas[arm])),
            raw_solve_p99_ms=_p99(solve_samples[arm]),
            raw_learner_ms=tuple(learner_samples[arm]),
            raw_refresh_ms=tuple(refresh_samples[arm]),
            raw_solve_ms=tuple(solve_samples[arm]),
        )
        for arm, domains in sorted(by_arm.items())
    )
    horizon_evidence = {
        arm.name: {
            **{
                horizon: {
                    "bootstrap_ci": _bootstrap_ci(horizon_origins[arm.name][horizon]),
                    "residuals_c": _flatten_origins(horizon_origins[arm.name][horizon]),
                }
                for horizon in ("600", "800", "1000")
            },
            "real": None,
        }
        for arm in arms
    }
    artifact_config = {
        **config.to_document(),
        "horizon_selection": _horizon_selection_document(config),
        "horizon_selection_window": [360, 480],
        "horizon_tie_rule": "shortest horizon within 1% of validation best",
    }
    return ExperimentArtifact(
        config=artifact_config,
        seeds=config.seeds,
        splits={"synthetic": {"fit": [0, 360], "validation": [360, 480], "test": [480, 600]}},
        model_snapshots={
            "configured_arms": [arm.name for arm in arms],
            "modes": ["frozen", "online"],
            "wrong_model_initializations": {
                arm: {
                    initialization: _initialization_snapshot(arm, initialization)
                    for initialization in config.initializations
                }
                for arm in ("scheduled-arx", "dmc", "state-space")
            },
        },
        scenarios=tuple(rows),
        arms=arms,
        failures=tuple(failures),
        source_revision=_source_revision(),
        environment=_environment_versions(),
        horizon_evidence=horizon_evidence,
    )


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
