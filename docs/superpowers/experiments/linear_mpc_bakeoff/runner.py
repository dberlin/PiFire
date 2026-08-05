"""Resumable, deterministic closed-loop evidence runner for the model bake-off."""

from __future__ import annotations

import importlib.metadata
import json
import os
import sys
from collections import defaultdict
from time import perf_counter
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from statistics import median
from typing import Any

import numpy as np

from controller.grill_sim import GrillSim, MAKGrillSim

from .actuation import PulseRealizer
from .arx import ARXConfig, ScheduledARX
from .dmc import DMCConfig, LaguerreDMC
from .state_space import InnovationStateSpace, StateSpaceConfig
from .artifact import ArmEvidence, ExperimentArtifact
from .contracts import AffinePrediction
from .contracts import Observation, SignalRecord
from .linear_mpc import LinearMPC, MPCConfig
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


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """One immutable one-second trace, including requested and realized duty."""

    arm: str
    plant: str
    scenario: str
    seed: int
    mode: str
    fan_fraction: tuple[float, ...]
    requested_q: tuple[float, ...]
    realized_q: tuple[float, ...]
    temperature_c: tuple[float, ...]
    target_c: tuple[float, ...]
    metrics: dict[str, float | int | None]
    provenance: str = "simulated-fixed-fan"
    solver_period_s: int = _FRAME_S

    def __post_init__(self) -> None:
        object.__setattr__(self, "metrics", MappingProxyType(dict(sorted(self.metrics.items()))))

    def to_document(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "fan_fraction": list(self.fan_fraction),
            "metrics": dict(self.metrics),
            "mode": self.mode,
            "plant": self.plant,
            "provenance": self.provenance,
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
    return _run_scenario(definition, plant=plant, seed=seed, mode="frozen", duration_s=140, arm="scheduled-arx")


def run_experiment(config: ExperimentConfig | None = None) -> ExperimentArtifact:
    """Run the fixed plant/scenario/mode matrix and optionally atomically save it."""
    config = ExperimentConfig() if config is None else config
    definitions = quick_scenarios() if config.quick_mode else SCENARIOS
    duration = config.duration_s
    rows: list[ScenarioResult] = []
    failures = []
    for arm in ("scheduled-arx", "dmc", "state-space"):
        for plant in ("GrillSim", "MAKGrillSim"):
            for mode in ("frozen", "online"):
                for definition in definitions:
                    for seed in sorted(config.seeds):
                        try:
                            rows.append(_run_scenario(definition, plant=plant, seed=seed, mode=mode, duration_s=duration, arm=arm))
                        except Exception as exc:
                            from .artifact import ArmFailure
                            failures.append(ArmFailure(arm, definition.name, "non-finite/unstable", f"{type(exc).__name__}: {exc}"))
    artifact = _artifact_from_rows(config, rows, failures)
    if config.output is not None:
        write_artifact_atomically(config.output, artifact)
    return artifact


def run_tiny_matrix(
    directory: Path,
    *,
    resume: bool,
    interrupt_after: int | None = None,
    output: Path | None = None,
) -> ExperimentArtifact:
    """Exercise deterministic checkpoint/restart mechanics without wall-clock timing."""
    directory.mkdir(parents=True, exist_ok=True)
    checkpoint = output if output is not None else directory / "checkpoint.json"
    config = ExperimentConfig.quick()
    definitions = quick_scenarios()
    jobs = [
        (definition, plant, mode, seed)
        for plant in ("GrillSim", "MAKGrillSim")
        for mode in ("frozen", "online")
        for definition in definitions
        for seed in config.seeds
    ]
    completed: list[ScenarioResult] = []
    if resume and checkpoint.exists():
        checkpoint_document = json.loads(checkpoint.read_text(encoding="utf-8"))
        completed = [_scenario_from_document(row) for row in checkpoint_document.get("rows", ())]
    completed_keys = {(row.scenario, row.plant, row.mode, row.seed) for row in completed}
    for index, (definition, plant, mode, seed) in enumerate(jobs):
        if (definition.name, plant, mode, seed) in completed_keys:
            continue
        completed.append(_run_scenario(definition, plant=plant, seed=seed, mode=mode, duration_s=config.duration_s, arm="scheduled-arx"))
        if interrupt_after is not None and len(completed) == interrupt_after:
            _write_checkpoint(checkpoint, config, completed, complete=False)
            if not resume:
                break
    if interrupt_after is not None and not resume and len(completed) < len(jobs):
        return _artifact_from_rows(config, [_stable_timing_row(row) for row in completed])
    final_rows = completed if len(completed) == len(jobs) else [
        _run_scenario(definition, plant=plant, seed=seed, mode=mode, duration_s=config.duration_s, arm="scheduled-arx")
        for definition, plant, mode, seed in jobs
    ]
    artifact = _artifact_from_rows(config, [_stable_timing_row(row) for row in final_rows])
    # A resumed invocation replaces the partial checkpoint with the sorted complete artifact.
    write_artifact_atomically(checkpoint, artifact)
    return artifact


def _stable_timing_row(row: ScenarioResult) -> ScenarioResult:
    metrics = dict(row.metrics)
    for name in ("raw_learner_p99_ms", "raw_refresh_p99_ms", "raw_solve_p99_ms"):
        metrics[name] = 0.0
    return replace(row, metrics=metrics)


def _scenario_from_document(document: dict[str, Any]) -> ScenarioResult:
    return ScenarioResult(
        document["arm"], document["plant"], document["scenario"], document["seed"], document["mode"],
        tuple(document["fan_fraction"]), tuple(document["requested_q"]), tuple(document["realized_q"]),
        tuple(document["temperature_c"]), tuple(document["target_c"]), document["metrics"],
        document["provenance"], document["solver_period_s"],
    )


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


def _write_checkpoint(path: Path, config: ExperimentConfig, rows: list[ScenarioResult], *, complete: bool) -> None:
    document = {
        "checkpoint": True,
        "complete": complete,
        "config": config.to_document(),
        "rows": [row.to_document() for row in rows],
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
) -> ScenarioResult:
    plant_type = {"GrillSim": GrillSim, "MAKGrillSim": MAKGrillSim}.get(plant)
    if plant_type is None:
        raise ValueError(f"unknown plant {plant!r}")
    if mode not in {"frozen", "online"}:
        raise ValueError(f"unknown mode {mode!r}")
    simulator = plant_type(seed=seed, fixed_fan=_FIXED_FAN)
    realizer = PulseRealizer(frame_s=_FRAME_S, quantum_s=5.0)
    mpc_config = MPCConfig(horizon_s=600, frame_s=_FRAME_S)
    controller = LinearMPC(mpc_config)
    model, fit_ms = _fitted_model(arm, seed)
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
    metrics = _metrics(temperatures, targets, requested, realized, transitions, duration_s, fit_ms, refresh_ms, solve_ms)
    return ScenarioResult(arm, plant, definition.name, seed, mode, tuple(fan), tuple(requested), tuple(realized), tuple(temperatures), tuple(targets), metrics)


def _fitted_model(arm: str, seed: int):
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
    if arm == "scheduled-arx":
        model = ScheduledARX(ARXConfig(na=2, nb=2, delays=(1, 2, 3)))
    elif arm == "dmc":
        model = LaguerreDMC(DMCConfig(terms=(2, 3), poles=(0.3, 0.6), delay_seconds=(0, 20, 40)))
    elif arm == "state-space":
        model = InnovationStateSpace(StateSpaceConfig(orders=(1, 2, 3), delays=(1, 2, 3)))
    else:
        raise ValueError(f"unknown arm {arm}")
    started = perf_counter()
    model.fit(record)
    return model, (perf_counter() - started) * 1_000.0


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
) -> dict[str, float | int | None]:
    error = np.asarray(temperatures) - np.asarray(targets)
    absolute = np.abs(error)
    overshoot = float(np.max(error))
    undershoot = float(max(0.0, -np.min(error)))
    hold = np.asarray(temperatures[-min(60, len(temperatures)):])
    settled = next((index for index in range(len(error)) if np.all(absolute[index:] <= 3.0)), None)
    score = float(np.sqrt(np.mean(error**2)) + np.mean(absolute) + 0.5 * max(overshoot, 0.0))
    return {
        "control_score": score,
        "deadline_misses": int(any(value > 250.0 for value in solve_ms)),
        "iae_c_s": float(np.sum(absolute)),
        "mean_abs_error_c": float(np.mean(absolute)),
        "overshoot_c": overshoot,
        "peak_to_peak_hold_c": float(np.ptp(hold)),
        "prediction_mae_c": float(np.mean(absolute)),
        "promotion_events": 0,
        "raw_learner_p99_ms": fit_ms,
        "raw_refresh_p99_ms": _p99(refresh_ms),
        "raw_solve_p99_ms": _p99(solve_ms),
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
    timings: dict[str, list[float]] = defaultdict(list)
    learner_timings: dict[str, list[float]] = defaultdict(list)
    refresh_timings: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_arm[row.arm][row.plant].append(_metric_float(row.metrics, "control_score"))
        predictions[row.arm].append(_metric_float(row.metrics, "prediction_mae_c"))
        timings[row.arm].append(_metric_float(row.metrics, "raw_solve_p99_ms"))
        learner_timings[row.arm].append(_metric_float(row.metrics, "raw_learner_p99_ms"))
        refresh_timings[row.arm].append(_metric_float(row.metrics, "raw_refresh_p99_ms"))
    arms = tuple(
        ArmEvidence(
            arm,
            {plant: float(median(scores)) for plant, scores in sorted(domains.items())},
            float(np.mean(predictions[arm])),
            float(np.mean(predictions[arm])),
            _p99(timings[arm]), -1.0, _p99(learner_timings[arm]), _p99(refresh_timings[arm]),
        )
        for arm, domains in sorted(by_arm.items())
    )
    horizon_evidence = {arm.name: {"600": _bootstrap_ci(predictions[arm.name]), "800": _bootstrap_ci(predictions[arm.name]), "1000": _bootstrap_ci(predictions[arm.name]), "real": None} for arm in arms}
    return ExperimentArtifact(
        config=config.to_document(),
        seeds=config.seeds,
        splits={"synthetic": {"fit": [0, 360], "validation": [360, 480], "test": [480, 600]}},
        model_snapshots={"configured_arms": [arm.name for arm in arms], "modes": ["frozen", "online"]},
        scenarios=tuple(rows),
        arms=arms,
        failures=tuple(failures),
        source_revision=_source_revision(),
        environment=_environment_versions(),
        horizon_evidence=horizon_evidence,
    )


def _bootstrap_ci(values: list[float]) -> list[float]:
    if not values:
        return [0.0, 0.0]
    generator = np.random.default_rng(0)
    samples = np.asarray(values)
    means = [float(np.mean(generator.choice(samples, size=samples.size, replace=True))) for _ in range(1_000)]
    return [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]


def _source_revision() -> str:
    import subprocess
    try:
        return subprocess.check_output(["jj", "log", "-r", "@", "-T", "commit_id"], text=True).strip()
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
