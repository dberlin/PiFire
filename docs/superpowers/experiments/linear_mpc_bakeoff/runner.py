"""Resumable, deterministic closed-loop evidence runner for the model bake-off."""

from __future__ import annotations

from copy import deepcopy
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from functools import lru_cache
import gzip
import hashlib
import importlib.metadata
import json
import multiprocessing
import os
import sys
from collections import defaultdict, deque
from time import perf_counter
from pathlib import Path
from types import MappingProxyType
from statistics import median
from typing import Any, Callable, Mapping, Sequence
import numpy as np
from scipy.optimize import minimize


from controller.grill_sim import GrillSim, MAKGrillSim

from .actuation import PulseSimulationDriver
from .adaptation import (
    AdaptationManager,
    AdaptationPolicy,
    AlignmentEvidence,
    OperatingState,
    WindowScores,
)
from controller.linear_mpc.arx import ScheduledARX, ScheduledARXConfig
from .data import reconstruct_mak_fixture, resample_record
from .datasets import (
    DEFAULT_CALIBRATION_PROGRAM,
    MAK_CALIBRATION_PROGRAM,
    generate_calibration_record,
)
from .dmc import DMCConfig, LaguerreDMC
from .state_space import InnovationStateSpace, StateSpaceConfig
from .artifact import ArmEvidence, ExperimentArtifact, MatrixKey
from .contracts import SignalRecord
from controller.linear_mpc.contracts import AffinePrediction, FrameObservation
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
_DEFAULT_OUTPUT = Path("docs/superpowers/experiments/_linear_mpc_bakeoff.manifest.json")


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    """Frozen runner inputs; every value is serialized into its artifact."""

    quick_mode: bool = False
    seeds: tuple[int, ...] = (0, 1, 2)
    duration_s: int = 1_800
    control_budget_ms: float = 50.0
    initializations: tuple[str, ...] = ("correct", "wrong-gain", "wrong-pole", "wrong-delay")
    output: Path | None = None
    # Runtime execution policy never belongs to the scientific artifact.
    workers: int | None = None
    blas_threads: int | None = None

    def __post_init__(self) -> None:
        if not self.seeds or any(not isinstance(seed, int) for seed in self.seeds):
            raise ValueError("seeds must be non-empty integers")
        if self.duration_s < _FRAME_S:
            raise ValueError("duration_s must cover at least one controller frame")
        if self.control_budget_ms <= 0.0:
            raise ValueError("control_budget_ms must be positive")
        for name, value in (("workers", self.workers), ("blas_threads", self.blas_threads)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
                raise ValueError(f"{name} must be a positive integer when specified")

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


_NATIVE_THREAD_ENVIRONMENT = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


def _single_thread_worker_environment() -> dict[str, str]:
    """Return the complete native-thread policy inherited by spawned workers."""
    return {name: "1" for name in _NATIVE_THREAD_ENVIRONMENT}


def _safe_worker_cap() -> int:
    return min(8, max(1, (os.cpu_count() or 1) - 2))


def _parse_requested_workers(value: int | str | None, *, name: str = "workers") -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a positive integer") from error
    if str(value).strip() != str(parsed) or parsed < 1:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _resolve_workers(requested: int | None, *, pending_bundles: int) -> int:
    """Resolve CLI/config, environment, then auto policy without scientific state."""
    if pending_bundles < 1:
        return 0
    selected = _parse_requested_workers(
        requested if requested is not None else os.environ.get("PIFIRE_LINEAR_MPC_WORKERS")
    )
    cap = _safe_worker_cap()
    if selected is not None and selected > cap:
        raise ValueError(f"workers exceeds safe cap {cap}")
    return min(selected if selected is not None else cap, cap, pending_bundles)


def _resolve_blas_threads(requested: int | None, *, workers: int) -> int:
    selected = (
        _parse_requested_workers(
            requested if requested is not None else os.environ.get("PIFIRE_LINEAR_MPC_BLAS_THREADS"),
            name="blas_threads",
        )
        or 1
    )
    available = max(1, (os.cpu_count() or 1) - 2)
    if workers * selected > available:
        raise ValueError("workers * blas_threads exceeds available CPU budget")
    return selected


def _worker_environment(blas_threads: int) -> dict[str, str]:
    return {name: str(blas_threads) for name in _NATIVE_THREAD_ENVIRONMENT}


def _initialize_worker(blas_threads: int) -> None:
    """Defend the child environment even if an executor implementation delays import."""
    os.environ.update(_worker_environment(blas_threads))


def _select_validation_horizon(residuals_by_horizon: Mapping[int, tuple[float, ...]]) -> dict[str, Any]:
    """Summarize one already-isolated validation score set."""
    scores = {horizon_s: float(np.mean(np.abs(values))) for horizon_s, values in residuals_by_horizon.items()}
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
        "pooled_validation_scores": {str(horizon_s): scores[horizon_s] for horizon_s in sorted(scores)},
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
        checkpoint=output if output is not None else directory / "checkpoint.manifest.json",
        resume=resume,
        interrupt_after=interrupt_after,
    )


@dataclass(frozen=True, slots=True)
class PreparedOrigin:
    """Picklable immutable calibration/fitted-model payload shared by cell tasks."""

    arm: str
    initialization: str
    plant: str
    seed: int
    model: Any
    fit_ms: float
    before_residuals: Mapping[int, tuple[float, ...]]
    after_residuals: Mapping[int, tuple[float, ...]]
    calibration_time_s: np.ndarray
    calibration_temp_c: np.ndarray
    calibration_q: np.ndarray
    calibration_ambient_c: np.ndarray
    calibration_provenance: str
    calibration_metadata: dict[str, Any]
    diagnostics: dict[str, Any]

    @property
    def origin(self) -> tuple[str, str, str, int]:
        return (self.arm, self.initialization, self.plant, self.seed)


@dataclass(frozen=True, slots=True)
class MatrixJob:
    ordinal: int
    arm: str
    initialization: str
    definition: ScenarioDefinition
    plant: str
    mode: str
    seed: int
    horizon_s: int
    duration_s: int

    @property
    def key(self) -> MatrixKey:
        return MatrixKey(
            self.arm,
            self.initialization,
            self.plant,
            self.mode,
            self.definition.name,
            self.seed,
            self.horizon_s,
        )

    @property
    def origin(self) -> tuple[str, str, str, int]:
        return (self.arm, self.initialization, self.plant, self.seed)


@dataclass(frozen=True, slots=True)
class CellExecution:
    """Immutable canonical worker envelope; only the parent decodes it."""

    ordinal: int
    row: bytes
    elapsed_s: float


class ScientificRejection(FloatingPointError):
    """A finite/model rejection that is valid scientific cell evidence."""


def _prepare_origin(origin: tuple[str, str, str, int]) -> PreparedOrigin:
    arm, initialization, plant, seed = origin
    model, fit_ms, before, after, calibration = _prepared_model(arm, plant, seed, initialization)
    return PreparedOrigin(
        arm=arm,
        initialization=initialization,
        plant=plant,
        seed=seed,
        model=model,
        fit_ms=fit_ms,
        before_residuals={key: tuple(value) for key, value in before.items()},
        after_residuals={key: tuple(value) for key, value in after.items()},
        calibration_time_s=calibration.time_s,
        calibration_temp_c=calibration.temp_c,
        calibration_q=calibration.q,
        calibration_ambient_c=calibration.ambient_c,
        calibration_provenance=calibration.provenance,
        calibration_metadata=dict(calibration.metadata),
        diagnostics=_simulator_prediction_diagnostics_from_model(model, calibration, plant),
    )


def _run_prepared_cell(job: MatrixJob, prepared: PreparedOrigin) -> CellExecution:
    """Module-level spawned worker: no aggregation, checkpointing, or nested pools."""
    started = perf_counter()
    try:
        row = _run_scenario(
            job.definition,
            plant=job.plant,
            seed=job.seed,
            mode=job.mode,
            duration_s=job.duration_s,
            arm=job.arm,
            initialization=job.initialization,
            horizon_s=job.horizon_s,
            prepared=prepared,
        )
        return CellExecution(
            job.ordinal,
            _canonical_bytes({"kind": "row", "row": row.to_document()}),
            perf_counter() - started,
        )
    except ScientificRejection as error:
        from .artifact import ArmFailure

        failure = ArmFailure(
            job.arm,
            job.definition.name,
            "non-finite/unstable",
            f"{type(error).__name__}: {error}",
            job.key,
        )
        return CellExecution(
            job.ordinal,
            _canonical_bytes({"failure": failure.to_document(), "kind": "failure"}),
            perf_counter() - started,
        )


def _cell_weight(job: MatrixJob) -> tuple[int, int, int]:
    """Static deterministic LPT estimate; runtime observation never affects science."""
    arm_weight = {"state-space": 3, "dmc": 2, "scheduled-arx": 1}[job.arm]
    scenario_weight = 1 if "lid" in job.definition.name else 0
    return (arm_weight, job.horizon_s, scenario_weight)


def _lpt_jobs(jobs: Sequence[MatrixJob]) -> list[MatrixJob]:
    return sorted(jobs, key=lambda job: (*(-value for value in _cell_weight(job)), job.ordinal))


def _temporary_worker_environment(blas_threads: int) -> tuple[dict[str, str | None], dict[str, str]]:
    environment = _worker_environment(blas_threads)
    previous = {name: os.environ.get(name) for name in environment}
    os.environ.update(environment)
    return previous, environment


def _restore_worker_environment(previous: Mapping[str, str | None]) -> None:
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _prepare_origins(
    origins: Sequence[tuple[str, str, str, int]], *, workers: int, blas_threads: int
) -> dict[tuple[str, str, str, int], PreparedOrigin]:
    """Run the validation barrier in bounded spawned processes, then reduce by origin."""
    previous, _ = _temporary_worker_environment(blas_threads)
    prepared: dict[tuple[str, str, str, int], PreparedOrigin] = {}
    iterator = iter(origins)
    context = multiprocessing.get_context("spawn")
    try:
        with ProcessPoolExecutor(
            max_workers=workers,
            mp_context=context,
            initializer=_initialize_worker,
            initargs=(blas_threads,),
        ) as executor:
            inflight = {}
            for _ in range(workers):
                try:
                    origin = next(iterator)
                except StopIteration:
                    break
                inflight[executor.submit(_prepare_origin, origin)] = origin
            while inflight:
                done, _ = wait(inflight, return_when=FIRST_COMPLETED)
                for future in done:
                    origin = inflight.pop(future)
                    prepared[origin] = future.result()
                    try:
                        next_origin = next(iterator)
                    except StopIteration:
                        continue
                    inflight[executor.submit(_prepare_origin, next_origin)] = next_origin
    finally:
        _restore_worker_environment(previous)
    return {origin: prepared[origin] for origin in origins}


def _execute_cells(
    jobs: Sequence[MatrixJob],
    prepared: Mapping[tuple[str, str, str, int], PreparedOrigin],
    *,
    workers: int,
    blas_threads: int,
):
    """Bounded work-conserving LPT cell scheduling; parent owns every side effect."""
    previous, _ = _temporary_worker_environment(blas_threads)
    iterator = iter(jobs)
    context = multiprocessing.get_context("spawn")
    try:
        with ProcessPoolExecutor(
            max_workers=workers,
            mp_context=context,
            initializer=_initialize_worker,
            initargs=(blas_threads,),
        ) as executor:
            inflight = {}
            for _ in range(workers):
                try:
                    job = next(iterator)
                except StopIteration:
                    break
                inflight[executor.submit(_run_prepared_cell, job, prepared[job.origin])] = job
            while inflight:
                done, _ = wait(inflight, return_when=FIRST_COMPLETED)
                completed = [(inflight.pop(future), future.result()) for future in done]
                for _ in completed:
                    try:
                        job = next(iterator)
                    except StopIteration:
                        break
                    inflight[executor.submit(_run_prepared_cell, job, prepared[job.origin])] = job
                for _, execution in sorted(completed, key=lambda item: item[1].ordinal):
                    yield execution
    finally:
        _restore_worker_environment(previous)


def _run_matrix(
    config: ExperimentConfig,
    *,
    checkpoint: Path | None,
    resume: bool,
    interrupt_after: int | None = None,
) -> ExperimentArtifact:
    source_revision = _source_revision()
    checkpoint_path = _checkpoint_path(checkpoint) if checkpoint is not None else None
    if resume:
        _validate_resume_source_revision(checkpoint_path, config, source_revision)
    definitions = quick_scenarios() if config.quick_mode else SCENARIOS
    origin_specs = tuple(
        (arm, initialization, plant, seed)
        for arm in ("scheduled-arx", "dmc", "state-space")
        for initialization in config.initializations
        for plant in ("GrillSim", "MAKGrillSim")
        for seed in sorted(config.seeds)
    )
    workers = _resolve_workers(config.workers, pending_bundles=len(origin_specs))
    blas_threads = _resolve_blas_threads(config.blas_threads, workers=max(1, workers))
    prepared = _prepare_origins(origin_specs, workers=workers, blas_threads=blas_threads)
    selection = _horizon_selection_document(config, prepared_origins=prepared)
    horizon_s = int(selection["selected_horizon_s"])
    jobs = [
        MatrixJob(
            ordinal,
            arm,
            initialization,
            definition,
            plant,
            mode,
            seed,
            horizon_s,
            config.duration_s,
        )
        for ordinal, (arm, initialization, definition, plant, mode, seed) in enumerate(
            (
                (arm, initialization, definition, plant, mode, seed)
                for arm in ("scheduled-arx", "dmc", "state-space")
                for initialization in config.initializations
                for definition in definitions
                for plant in ("GrillSim", "MAKGrillSim")
                if plant in definition.applicable_plants
                for mode in ("frozen", "online")
                for seed in sorted(config.seeds)
            )
        )
    ]
    if not resume:
        _discard_checkpoint(checkpoint_path)
    fingerprint = _run_fingerprint(config, selection, jobs, source_revision)
    rows, failures, completed = (
        _load_checkpoint_v2(checkpoint_path, config, selection, fingerprint, jobs, source_revision)
        if resume
        else ([], [], set())
    )
    pending = [job for job in jobs if job.ordinal not in completed]
    execution_metadata: list[dict[str, Any]] = []
    for execution in _execute_cells(_lpt_jobs(pending), prepared, workers=workers, blas_threads=blas_threads):
        row, failure = _execution_parts(execution)
        if row is not None:
            rows.append(_scenario_from_document(row))
        elif failure is not None:
            failures.append(failure)
        else:
            raise RuntimeError("worker returned no terminal cell result")
        execution_metadata.append(
            {
                "arm": jobs[execution.ordinal].arm,
                "elapsed_s": execution.elapsed_s,
                "ordinal": execution.ordinal,
            }
        )
        if checkpoint_path is not None:
            _write_checkpoint_v2(checkpoint_path, config, selection, fingerprint, jobs, source_revision, [execution])
        if interrupt_after is not None and len(rows) + len(failures) >= interrupt_after and not resume:
            return _artifact_from_rows(
                config,
                rows,
                failures,
                selection=selection,
                execution_metadata=execution_metadata,
                source_revision=source_revision,
            )
    artifact = _artifact_from_rows(
        config,
        rows,
        failures,
        selection=selection,
        execution_metadata=execution_metadata,
        source_revision=source_revision,
    )
    if checkpoint is not None:
        write_artifact_atomically(checkpoint, artifact)
        _remove_checkpoint_v2(checkpoint_path)
    return artifact


def _scenario_from_document(document: dict[str, Any]) -> ScenarioResult:
    raw_timing = document.get("raw_timing_ms", {})
    model_evidence = dict(document.get("model_evidence", {}))
    diagnostics = model_evidence.get("simulator_prediction_diagnostics")
    if isinstance(diagnostics, Mapping) and isinstance(diagnostics.get("diagnostics_c"), Mapping):
        diagnostics = dict(diagnostics)
        diagnostics["diagnostics_c"] = {
            horizon: diagnostics["diagnostics_c"].get(horizon)
            for horizon in ("60", "300", "900", "1800", "3600")
            if horizon in diagnostics["diagnostics_c"]
        }
        model_evidence["simulator_prediction_diagnostics"] = diagnostics
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
            str(horizon): tuple(values) for horizon, values in document.get("prediction_residuals_c", {}).items()
        },
        pre_recovery_residuals_c={
            str(horizon): tuple(values) for horizon, values in document.get("pre_recovery_residuals_c", {}).items()
        },
        provenance=document["provenance"],
        solver_period_s=document["solver_period_s"],
        model_evidence=model_evidence,
        promotion_history=tuple(document.get("promotion_history", ())),
        solver_evidence=tuple(document.get("solver_evidence", ())),
    )
    if "evidence_id" in document and document["evidence_id"] != row.evidence_id:
        raise ValueError("scenario evidence_id does not match its prepared-model origin")
    return row


_MAX_ARTIFACT_PART_BYTES = 90 * 1024 * 1024


def _checkpoint_path(output: Path) -> Path:
    """Keep in-progress evidence separate from the canonical final manifest."""
    suffix = ".manifest.json"
    if not output.name.endswith(suffix):
        raise ValueError("checkpoint output requires a .manifest.json path")
    return output.with_name(f"{output.name.removesuffix(suffix)}.checkpoint{suffix}")


def _validate_resume_source_revision(path: Path | None, config: ExperimentConfig, source_revision: str) -> None:
    """Reject incompatible or malformed resume heads before expensive preparation."""
    if path is None or not path.exists():
        return
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("checkpoint is unreadable") from error
    if not isinstance(document, Mapping) or document.get("checkpoint_schema") != _CHECKPOINT_SCHEMA:
        raise ValueError("checkpoint schema mismatch")
    if document.get("source_revision") != source_revision:
        raise ValueError("checkpoint source_revision mismatch")
    if document.get("config") != config.to_document():
        raise ValueError("checkpoint config mismatch")


def _discard_checkpoint(path: Path | None) -> None:
    """Discard a fresh-run checkpoint without trusting malformed part references."""
    if path is None or not path.exists():
        return
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        path.unlink(missing_ok=True)
        return
    if not isinstance(document, dict):
        path.unlink(missing_ok=True)
        return
    if document.get("checkpoint_schema") == _CHECKPOINT_SCHEMA:
        _remove_checkpoint_v2(path)
        path.unlink(missing_ok=True)
        return
    parts = document.get("parts") if document.get("transport") == "gzip-shards/v1" else None
    if not isinstance(parts, list):
        path.unlink()
        return
    referenced = []
    parent = path.parent.resolve()
    for item in parts:
        name = item.get("name") if isinstance(item, Mapping) else None
        if not isinstance(name, str):
            path.unlink()
            return
        candidate = Path(name)
        part = (path.parent / candidate).resolve()
        if (
            candidate.name != name
            or not name.startswith(f"{path.stem}.")
            or not name.endswith(".gz")
            or part.parent != parent
        ):
            path.unlink()
            return
        referenced.append(part)
    path.unlink()
    for part in referenced:
        part.unlink(missing_ok=True)


def _transport_part_paths(path: Path) -> tuple[Path, ...]:
    """Return the exactly verified part paths named by an existing transport."""
    if not path.exists():
        return ()
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("transport") != "gzip-shards/v1":
        return ()
    parts = document.get("parts")
    if not isinstance(parts, list):
        raise ValueError("transport manifest requires a parts list")
    referenced = []
    for index, item in enumerate(parts):
        if not isinstance(item, Mapping):
            raise ValueError("transport manifest contains an invalid part")
        _verified_part(path, item, index)
        referenced.append(path.parent / str(item["name"]))
    return tuple(referenced)


def _remove_transport(path: Path | None) -> None:
    """Delete exactly the verified shards named by one transport manifest."""
    if path is None or not path.exists():
        return
    referenced = _transport_part_paths(path)
    path.unlink()
    for part in referenced:
        part.unlink(missing_ok=True)


def _write_transport_atomically(
    path: Path,
    payload: bytes,
    *,
    max_part_bytes: int = _MAX_ARTIFACT_PART_BYTES,
    before_publish: Callable[[], None] | None = None,
) -> None:
    """Publish arbitrary canonical JSON through the bounded gzip shard transport."""
    if max_part_bytes < 1 or max_part_bytes > _MAX_ARTIFACT_PART_BYTES:
        raise ValueError(f"max_part_bytes must be within 1..{_MAX_ARTIFACT_PART_BYTES}")
    if not path.name.endswith(".manifest.json"):
        raise ValueError("artifact publication requires a .manifest.json path")
    try:
        previous = _transport_part_paths(path)
    except ValueError:
        # A replacement must not trust or delete shards from a corrupt predecessor.
        previous = ()
    compressed = gzip.compress(payload, mtime=0)
    stream_hash = hashlib.sha256(compressed).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = []
    for index, offset in enumerate(range(0, len(compressed), max_part_bytes)):
        data = compressed[offset : offset + max_part_bytes]
        part_hash = hashlib.sha256(data).hexdigest()
        name = f"{path.stem}.{stream_hash[:16]}.part{index:04d}.{part_hash[:16]}.gz"
        _write_bytes_atomically(path.parent / name, data)
        parts.append({"name": name, "bytes": len(data), "sha256": part_hash})
    manifest = {
        "transport": "gzip-shards/v1",
        "parts": parts,
        "compressed_sha256": stream_hash,
        "canonical_json_sha256": hashlib.sha256(payload).hexdigest(),
        "canonical_json_bytes": len(payload),
    }
    if before_publish is not None:
        before_publish()
    _write_text_atomically(path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    active = {item["name"] for item in parts}
    for part in previous:
        if part.name not in active:
            part.unlink(missing_ok=True)


def load_artifact(path: Path) -> ExperimentArtifact:
    """Load either canonical gzip transport or a legacy JSON manifest exactly."""
    return ExperimentArtifact.from_json(_read_artifact_text(path))


def _read_artifact_document(path: Path) -> dict[str, Any]:
    return json.loads(_read_artifact_text(path))


def _read_artifact_text(path: Path) -> str:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as source:
            return source.read()
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("artifact document must be a JSON object")
    if document.get("transport") != "gzip-shards/v1":
        return json.dumps(document, indent=2, sort_keys=True)
    parts = document["parts"]
    compressed = b"".join(_verified_part(path, item, index) for index, item in enumerate(parts))
    if hashlib.sha256(compressed).hexdigest() != document["compressed_sha256"]:
        raise ValueError("compressed artifact checksum mismatch")
    payload = gzip.decompress(compressed)
    if (
        len(payload) != document["canonical_json_bytes"]
        or hashlib.sha256(payload).hexdigest() != document["canonical_json_sha256"]
    ):
        raise ValueError("canonical artifact checksum mismatch")
    return payload.decode("utf-8")


def _verified_part(manifest: Path, item: Mapping[str, Any], index: int) -> bytes:
    name = item["name"]
    if Path(name).name != name or not name.startswith(f"{manifest.stem}.") or not name.endswith(".gz"):
        raise ValueError("unsafe or unordered artifact part")
    part = (manifest.parent / name).resolve()
    if part.parent != manifest.parent.resolve():
        raise ValueError("unsafe artifact part path")
    data = part.read_bytes()
    if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise ValueError("artifact part checksum mismatch")
    return data


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_text_atomically(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as sink:
            sink.write(payload)
            sink.flush()
            os.fsync(sink.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_artifact_atomically(
    path: Path,
    artifact: ExperimentArtifact,
    *,
    max_part_bytes: int = _MAX_ARTIFACT_PART_BYTES,
    before_publish: Callable[[], None] | None = None,
) -> None:
    """Publish a deterministic manifest after bounded gzip shards are durable."""
    _write_transport_atomically(
        path,
        (artifact.to_json() + "\n").encode("utf-8"),
        max_part_bytes=max_part_bytes,
        before_publish=before_publish,
    )


def _write_bytes_atomically(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("wb") as sink:
            sink.write(data)
            sink.flush()
            os.fsync(sink.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
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
    _write_transport_atomically(
        path,
        (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
    )


_CHECKPOINT_SCHEMA = "incremental-cas/v2"


def _canonical_bytes(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(document, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _job_fingerprint(jobs: Sequence[MatrixJob]) -> str:
    return hashlib.sha256(_canonical_bytes({"jobs": [job.key.to_document() for job in jobs]})).hexdigest()


def _run_fingerprint(
    config: ExperimentConfig, selection: Mapping[str, Any], jobs: Sequence[MatrixJob], source_revision: str
) -> str:
    return hashlib.sha256(
        _canonical_bytes(
            {
                "checkpoint_schema": _CHECKPOINT_SCHEMA,
                "config": config.to_document(),
                "horizon_selection": selection,
                "jobs": _job_fingerprint(jobs),
                "source_revision": source_revision,
            }
        )
    ).hexdigest()


def _checkpoint_object_path(path: Path, digest: str) -> Path:
    if not digest.isalnum() or len(digest) != 64:
        raise ValueError("invalid checkpoint object digest")
    return path.with_name(f"{path.stem}.bundle.{digest}.json.gz")


def _checkpoint_entry_path(path: Path, entry: Mapping[str, Any]) -> Path:
    name = entry.get("name")
    if not isinstance(name, str):
        raise ValueError("checkpoint object path is missing")
    candidate = Path(name)
    expected_prefix = f"{path.stem}.bundle."
    resolved = (path.parent / candidate).resolve()
    if (
        candidate.name != name
        or not name.startswith(expected_prefix)
        or not name.endswith(".json.gz")
        or resolved.parent != path.parent.resolve()
    ):
        raise ValueError("unsafe checkpoint object path")
    return resolved


def _selection_identity(selection: Mapping[str, Any]) -> dict[str, Any]:
    """Persist a compact exact identity, not all validation residual payloads."""
    return {
        "origins_sha256": hashlib.sha256(_canonical_bytes({"origins": selection.get("origins", {})})).hexdigest(),
        "pooled_validation_scores": selection.get("pooled_validation_scores", {}),
        "selected_horizon_s": selection.get("selected_horizon_s"),
        "tie_rationale": selection.get("tie_rationale"),
    }


def _checkpoint_manifest(
    path: Path,
    config: ExperimentConfig,
    selection: Mapping[str, Any],
    fingerprint: str,
    jobs: Sequence[MatrixJob],
    source_revision: str,
    *,
    head: Mapping[str, Any] | None = None,
    count: int = 0,
) -> dict[str, Any]:
    return {
        "accepted_count": count,
        "checkpoint_schema": _CHECKPOINT_SCHEMA,
        "complete": False,
        "config": config.to_document(),
        "head": dict(head) if head is not None else None,
        "horizon_selection": _selection_identity(selection),
        "job_fingerprint": _job_fingerprint(jobs),
        "run_fingerprint": fingerprint,
        "schema_version": 2,
        "source_revision": source_revision,
    }


def _checkpoint_delta_path(path: Path, digest: str) -> Path:
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("invalid checkpoint delta digest")
    return path.with_name(f"{path.stem}.delta.{digest}.json")


def _checkpoint_delta_entries(
    path: Path, head: Mapping[str, Any] | None, fingerprint: str, count: int
) -> tuple[list[Mapping[str, Any]], list[Path]]:
    entries: list[Mapping[str, Any]] = []
    nodes: list[Path] = []
    seen: set[str] = set()
    expected_count = count
    current = head
    while current is not None:
        name = current.get("name") if isinstance(current, Mapping) else None
        digest = current.get("sha256") if isinstance(current, Mapping) else None
        if not isinstance(name, str) or not isinstance(digest, str) or name in seen:
            raise ValueError("invalid or cyclic checkpoint delta chain")
        seen.add(name)
        node_path = _checkpoint_delta_path(path.resolve(), digest)
        if node_path.name != name or node_path.parent != path.parent.resolve():
            raise ValueError("unsafe checkpoint delta path")
        canonical = node_path.read_bytes()
        if hashlib.sha256(canonical).hexdigest() != digest:
            raise ValueError("checkpoint delta checksum mismatch")
        node = json.loads(canonical)
        if not isinstance(node, Mapping) or node.get("run_fingerprint") != fingerprint:
            raise ValueError("checkpoint delta fingerprint mismatch")
        if node.get("count") != expected_count or not isinstance(node.get("entry"), Mapping):
            raise ValueError("checkpoint delta count mismatch")
        entries.append(node["entry"])
        nodes.append(node_path)
        current = node.get("parent")
        expected_count -= 1
    if expected_count != 0:
        raise ValueError("checkpoint delta chain length mismatch")
    return list(reversed(entries)), nodes


def _execution_parts(execution: CellExecution) -> tuple[dict[str, Any] | None, Any | None]:
    """Decode and verify the canonical immutable worker envelope in the parent."""
    try:
        envelope = json.loads(execution.row)
    except (TypeError, ValueError) as error:
        raise ValueError("worker returned invalid canonical envelope") from error
    if not isinstance(envelope, Mapping) or _canonical_bytes(envelope) != execution.row:
        raise ValueError("worker returned noncanonical envelope")
    if envelope.get("kind") == "row" and isinstance(envelope.get("row"), Mapping):
        return dict(envelope["row"]), None
    if envelope.get("kind") == "failure" and isinstance(envelope.get("failure"), Mapping):
        from .artifact import ArmFailure

        document = envelope["failure"]
        return None, ArmFailure(
            document["arm"],
            document["scenario"],
            document["category"],
            document["detail"],
            MatrixKey(**document["matrix_key"]),
        )
    raise ValueError("worker returned no terminal cell result")


def _write_checkpoint_v2(
    path: Path,
    config: ExperimentConfig,
    selection: Mapping[str, Any],
    fingerprint: str,
    jobs: Sequence[MatrixJob],
    source_revision: str,
    executions: Sequence[CellExecution],
) -> None:
    """Publish one immutable cell object and one constant-size delta per acceptance."""
    head: Mapping[str, Any] | None = None
    count = 0
    if path.exists():
        document = json.loads(path.read_text(encoding="utf-8"))
        required = _checkpoint_manifest(path, config, selection, fingerprint, jobs, source_revision)
        if not isinstance(document, Mapping) or document.get("schema_version") != 2:
            raise ValueError("checkpoint schema mismatch")
        for manifest_field in (
            "checkpoint_schema",
            "config",
            "horizon_selection",
            "job_fingerprint",
            "run_fingerprint",
            "source_revision",
        ):
            if document.get(manifest_field) != required[manifest_field]:
                raise ValueError(f"checkpoint {manifest_field} mismatch")
        head = document.get("head")
        count = document.get("accepted_count")
        if not isinstance(count, int) or count < 0:
            raise ValueError("checkpoint accepted count mismatch")
    for execution in executions:
        row, failure = _execution_parts(execution)
        key = _document_matrix_key(row).to_document() if row is not None else failure.matrix_key.to_document()
        payload = {
            "bundle_ordinal": execution.ordinal,
            "cell_ordinals": [execution.ordinal],
            "failures": [] if failure is None else [failure.to_document()],
            "matrix_keys": [key],
            "rows": [] if row is None else [row],
            "run_fingerprint": fingerprint,
        }
        canonical = _canonical_bytes(payload)
        digest = hashlib.sha256(canonical).hexdigest()
        object_path = _checkpoint_object_path(path, digest)
        compressed = gzip.compress(canonical, mtime=0)
        if len(compressed) > _MAX_ARTIFACT_PART_BYTES:
            raise ValueError("checkpoint bundle exceeds 90 MiB")
        if object_path.exists():
            if object_path.read_bytes() != compressed:
                raise ValueError("content-addressed checkpoint object mismatch")
        else:
            _write_bytes_atomically(object_path, compressed)
        entry = {
            "cell_ordinals": [execution.ordinal],
            "compressed_bytes": len(compressed),
            "compressed_sha256": hashlib.sha256(compressed).hexdigest(),
            "name": object_path.name,
            "sha256": digest,
        }
        count += 1
        node = {"count": count, "entry": entry, "parent": head, "run_fingerprint": fingerprint}
        delta_bytes = _canonical_bytes(node)
        delta_digest = hashlib.sha256(delta_bytes).hexdigest()
        delta_path = _checkpoint_delta_path(path, delta_digest)
        if delta_path.exists():
            if delta_path.read_bytes() != delta_bytes:
                raise ValueError("content-addressed checkpoint delta mismatch")
        else:
            _write_bytes_atomically(delta_path, delta_bytes)
        head = {"name": delta_path.name, "sha256": delta_digest}
        manifest = _checkpoint_manifest(
            path, config, selection, fingerprint, jobs, source_revision, head=head, count=count
        )
        _write_text_atomically(path, json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")


def _row_matrix_key(row: ScenarioResult) -> MatrixKey:
    return MatrixKey(row.arm, row.initialization, row.plant, row.mode, row.scenario, row.seed, row.mpc_horizon_s)


def _document_matrix_key(document: Mapping[str, Any]) -> MatrixKey:
    return MatrixKey(
        str(document["arm"]),
        str(document["initialization"]),
        str(document["plant"]),
        str(document["mode"]),
        str(document["scenario"]),
        int(document["seed"]),
        int(document["mpc_horizon_s"]),
    )


def _load_checkpoint_v2(
    path: Path | None,
    config: ExperimentConfig,
    selection: Mapping[str, Any],
    fingerprint: str,
    jobs: Sequence[MatrixJob],
    source_revision: str,
) -> tuple[list[ScenarioResult], list[Any], set[int]]:
    if path is None or not path.exists():
        return [], [], set()
    document = json.loads(path.read_text(encoding="utf-8"))
    required = _checkpoint_manifest(path, config, selection, fingerprint, jobs, source_revision)
    if not isinstance(document, Mapping) or document.get("schema_version") != 2:
        raise ValueError("checkpoint schema mismatch")
    for manifest_field in (
        "checkpoint_schema",
        "config",
        "horizon_selection",
        "job_fingerprint",
        "run_fingerprint",
        "source_revision",
    ):
        if document.get(manifest_field) != required[manifest_field]:
            raise ValueError(f"checkpoint {manifest_field} mismatch")
    count = document.get("accepted_count")
    if not isinstance(count, int) or count < 0:
        raise ValueError("checkpoint accepted count mismatch")
    entries, _ = _checkpoint_delta_entries(path, document.get("head"), fingerprint, count)
    expected = {job.ordinal: job.key.to_document() for job in jobs}
    rows: list[ScenarioResult] = []
    failures: list[Any] = []
    completed: set[int] = set()
    referenced_names: set[str] = set()
    for entry in entries:
        object_path = _checkpoint_entry_path(path, entry)
        if object_path.name in referenced_names:
            raise ValueError("duplicate checkpoint object reference")
        referenced_names.add(object_path.name)
        compressed = object_path.read_bytes()
        if len(compressed) != entry.get("compressed_bytes") or hashlib.sha256(compressed).hexdigest() != entry.get(
            "compressed_sha256"
        ):
            raise ValueError("checkpoint object checksum mismatch")
        canonical = gzip.decompress(compressed)
        if hashlib.sha256(canonical).hexdigest() != entry.get("sha256"):
            raise ValueError("checkpoint object canonical checksum mismatch")
        payload = json.loads(canonical)
        if not isinstance(payload, Mapping) or payload.get("run_fingerprint") != fingerprint:
            raise ValueError("checkpoint object fingerprint mismatch")
        ordinals, keys = payload.get("cell_ordinals"), payload.get("matrix_keys")
        if (
            not isinstance(ordinals, list)
            or not isinstance(keys, list)
            or len(ordinals) != 1
            or len(keys) != 1
            or entry.get("cell_ordinals") != ordinals
        ):
            raise ValueError("invalid checkpoint bundle membership")
        ordinal = ordinals[0]
        if not isinstance(ordinal, int) or ordinal in completed or expected.get(ordinal) != keys[0]:
            raise ValueError("duplicate or out-of-matrix checkpoint cell")
        stored_rows, stored_failures = payload.get("rows"), payload.get("failures")
        if (
            not isinstance(stored_rows, list)
            or not isinstance(stored_failures, list)
            or len(stored_rows) + len(stored_failures) != 1
        ):
            raise ValueError("invalid checkpoint terminal result")
        if stored_rows:
            row = _scenario_from_document(dict(stored_rows[0]))
            if _row_matrix_key(row).to_document() != expected[ordinal]:
                raise ValueError("checkpoint row key mismatch")
            rows.append(row)
        else:
            from .artifact import ArmFailure

            failure_document = stored_failures[0]
            failure = ArmFailure(
                failure_document["arm"],
                failure_document["scenario"],
                failure_document["category"],
                failure_document["detail"],
                MatrixKey(**failure_document["matrix_key"]),
            )
            if failure.matrix_key is None or failure.matrix_key.to_document() != expected[ordinal]:
                raise ValueError("checkpoint failure key mismatch")
            failures.append(failure)
        completed.add(ordinal)
    return rows, failures, completed


def _remove_checkpoint_v2(path: Path | None) -> None:
    """Remove exactly the objects and delta nodes reachable from a verified HEAD."""
    if path is None or not path.exists():
        return
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(document, Mapping)
            or document.get("checkpoint_schema") != _CHECKPOINT_SCHEMA
            or document.get("schema_version") != 2
        ):
            return
        count = document.get("accepted_count")
        fingerprint = document.get("run_fingerprint")
        if not isinstance(count, int) or not isinstance(fingerprint, str):
            return
        entries, nodes = _checkpoint_delta_entries(path, document.get("head"), fingerprint, count)
        objects = [_checkpoint_entry_path(path, entry) for entry in entries]
    except OSError, ValueError, json.JSONDecodeError:
        return
    path.unlink()
    for object_path in objects:
        object_path.unlink(missing_ok=True)
    for node_path in nodes:
        node_path.unlink(missing_ok=True)


def _adaptation_settings(arm: str, snapshot: Mapping[str, Any]) -> tuple[AdaptationPolicy, AlignmentEvidence]:
    """Derive arm-local promotion limits from the retained training snapshot."""
    bounds = snapshot.get("plausibility_bounds")
    if not isinstance(bounds, Mapping):
        raise ValueError(f"{arm} snapshot omitted training plausibility bounds")
    maximum = bounds.get("max_steady_gain_c_per_q", bounds.get("max_dc_gain_c_per_q"))
    if not isinstance(maximum, (int, float)) or not np.isfinite(maximum) or maximum <= 0.0:
        raise ValueError(f"{arm} snapshot has no finite training gain bound")
    alignment = AlignmentEvidence.MEASURED if arm == "state-space" else AlignmentEvidence.NOT_APPLICABLE
    return AdaptationPolicy(max_gain=float(maximum)), alignment


def _window_free_run_scores(
    samples: list[Mapping[str, Any]],
) -> tuple[WindowScores, dict[str, Any]]:
    """Score role snapshots before their untouched 60/300-second targets arrived."""
    horizon_steps = (3, 15)
    per_horizon: dict[str, dict[str, Any]] = {}
    candidate_means: list[float] = []
    incumbent_means: list[float] = []
    coast_candidate: list[float] = []
    coast_incumbent: list[float] = []
    for steps in horizon_steps:
        candidate_errors: list[float] = []
        incumbent_errors: list[float] = []
        origins: list[int] = []
        for index in range(len(samples) - steps + 1):
            origin = samples[index]
            future = samples[index : index + steps]
            q = np.asarray([float(item["q"]) for item in future], dtype=np.float64)
            ambient = np.asarray([float(item["ambient_c"]) for item in future], dtype=np.float64)
            actual = np.asarray([float(item["temp_c"]) for item in future], dtype=np.float64)
            candidate = origin["challenger"].affine_prediction(steps, float(origin["q_previous"]), ambient)
            incumbent = origin["incumbent"].affine_prediction(steps, float(origin["q_previous"]), ambient)
            candidate_prediction = candidate.free_output_c + candidate.input_response_c @ q
            incumbent_prediction = incumbent.free_output_c + incumbent.input_response_c @ q
            candidate_error = float(np.sqrt(np.mean((candidate_prediction - actual) ** 2)))
            incumbent_error = float(np.sqrt(np.mean((incumbent_prediction - actual) ** 2)))
            candidate_errors.append(candidate_error)
            incumbent_errors.append(incumbent_error)
            origins.append(int(origin["frame_s"]))
            braking_mask = np.asarray([bool(item["braking"]) for item in future])
            if braking_mask.any():
                coast_candidate.extend(np.abs(candidate_prediction[braking_mask] - actual[braking_mask]))
                coast_incumbent.extend(np.abs(incumbent_prediction[braking_mask] - actual[braking_mask]))
        per_horizon[str(steps * _FRAME_S)] = {
            "candidate_rmse_c": float(np.mean(candidate_errors)),
            "incumbent_rmse_c": float(np.mean(incumbent_errors)),
            "origin_frame_ids": origins,
        }
        candidate_means.append(float(np.mean(candidate_errors)))
        incumbent_means.append(float(np.mean(incumbent_errors)))
    frame_ids = [int(sample["frame_s"]) for sample in samples]
    return (
        WindowScores(
            window_id=str(samples[-1]["window_id"]),
            candidate_prediction_score=float(np.mean(candidate_means)),
            incumbent_prediction_score=float(np.mean(incumbent_means)),
            candidate_braking_score=_mean_or_none(coast_candidate),
            incumbent_braking_score=_mean_or_none(coast_incumbent),
        ),
        {
            "horizon_metrics": per_horizon,
            "score_frame_ids": frame_ids,
            "braking_or_coast_sample_count": int(sum(bool(sample["braking"]) for sample in samples)),
        },
    )


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
    prepared: PreparedOrigin | None = None,
) -> ScenarioResult:
    plant_type = {"GrillSim": GrillSim, "MAKGrillSim": MAKGrillSim}.get(plant)
    if plant_type is None:
        raise ValueError(f"unknown plant {plant!r}")
    if mode not in {"frozen", "online"}:
        raise ValueError(f"unknown mode {mode!r}")
    simulator = plant_type(seed=seed, fixed_fan=_FIXED_FAN)
    realizer = PulseSimulationDriver()
    mpc_config = MPCConfig(horizon_s=horizon_s, frame_s=_FRAME_S, tolerance=1e-3)
    controller = LinearMPC(mpc_config)
    if prepared is None:
        model, fit_ms, before_residuals, after_residuals, calibration = _fitted_model(arm, plant, seed, initialization)
        diagnostics = _simulator_prediction_diagnostics(arm, plant, seed, initialization)
    else:
        if prepared.origin != (arm, initialization, plant, seed):
            raise RuntimeError("prepared origin does not match cell identity")
        model = deepcopy(prepared.model)
        fit_ms = prepared.fit_ms
        before_residuals = {key: list(value) for key, value in prepared.before_residuals.items()}
        after_residuals = {key: list(value) for key, value in prepared.after_residuals.items()}
        calibration = SignalRecord(
            prepared.calibration_time_s,
            prepared.calibration_temp_c,
            prepared.calibration_q,
            prepared.calibration_ambient_c,
            prepared.calibration_provenance,
            metadata=prepared.calibration_metadata,
        )
        diagnostics = prepared.diagnostics
    batch_fit_snapshot = _json_value(_bakeoff_snapshot(model))
    policy, alignment = _adaptation_settings(arm, batch_fit_snapshot)
    manager = AdaptationManager(
        incumbent=model,
        challenger=deepcopy(model),
        policy=policy,
        incumbent_alignment=alignment,
        challenger_alignment=alignment,
        parameter_learning=mode == "online",
        replay_seed=seed,
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
    free_run_window: list[dict[str, Any]] = []
    free_run_classifications: list[dict[str, Any]] = []
    previous_realized_duty = 0.0
    previous_observation_target = definition.target_at(0)
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
            if (
                not np.isfinite(solve.objective)
                or not np.isfinite(solve.kkt_residual)
                or solve.kkt_residual > mpc_config.tolerance
            ):
                raise ScientificRejection(
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
                hessian, linear = condense_cost(prediction, target, frame.requested_duty, mpc_config.weights)
                reference = _independent_box_qp_reference(hessian, linear, solve.sequence_q)
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
        if (second + 1) % _FRAME_S == 0:
            safety_override = definition.safety_override_at(second)
            manual_override = definition.manual_override_at(second)
            frame_end_s = float(calibration.time_s[-1] + second + 1)
            observation = FrameObservation(
                frame_start_s=frame_end_s - _FRAME_S,
                frame_end_s=frame_end_s,
                temp_c=temperatures[-1],
                setpoint_c=target,
                ambient_c=simulator.T_amb,
                requested_q=frame.requested_duty,
                realized_q=frame.realized_duty,
                requested_auger_duty=frame.requested_duty,
                delivered_on_s=frame.on_seconds,
                requested_fan_duty=_FIXED_FAN,
                actual_fan_duty=_FIXED_FAN,
                result_revision=0,
                output_source="bakeoff-simulator",
                lid_open=lid_open,
                safety_inhibited=safety_override,
                manual_override=manual_override,
                stale=False,
                skipped=False,
                reset=False,
                continuous=True,
                role_generation=manager.role_generation,
            )
            state = (
                OperatingState.COAST
                if frame.realized_duty <= 0.05
                else (OperatingState.HOLD if target == definition.target_low_c else OperatingState.TRANSIENT)
            )
            role_generation = manager.role_generation
            braking = state is OperatingState.COAST or target < previous_observation_target
            free_run_window.append(
                {
                    "window_id": f"{plant}:{arm}:{initialization}:{second + 1}",
                    "frame_s": second + 1,
                    "role_generation": role_generation,
                    "incumbent": deepcopy(manager.incumbent),
                    "challenger": deepcopy(manager.challenger),
                    "q_previous": previous_realized_duty,
                    "q": frame.realized_duty,
                    "ambient_c": simulator.T_amb,
                    "temp_c": temperatures[-1],
                    "braking": braking,
                }
            )
            free_run_classifications.append(
                {
                    "frame_s": second + 1,
                    "braking": bool(braking),
                    "realized_duty": frame.realized_duty,
                    "target_c": target,
                }
            )
            previous_observation_target = target
            challenger_refresh_before = _refresh_marker(
                _bakeoff_snapshot(manager.challenger)
            )
            learner_started = perf_counter()
            outcome = manager.observe(
                observation,
                state=state,
                provenance="ordinary-cook",
                lid_open=lid_open,
                safety_override=safety_override,
                manual_override=manual_override,
            )
            observe_ms = (perf_counter() - learner_started) * 1_000.0
            challenger_refresh_after = _refresh_marker(
                _bakeoff_snapshot(manager.challenger)
            )
            if mode == "online" and outcome.gate.permitted:
                if challenger_refresh_after != challenger_refresh_before:
                    refresh_ms.append(observe_ms)
                else:
                    learner_ms.append(observe_ms)
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
            if mode == "online" and (second + 1) % 300 == 0:
                score_window = [
                    sample for sample in free_run_window if sample["role_generation"] == manager.role_generation
                ]
                free_run_window.clear()
                if len(score_window) < 2:
                    continue
                scores, score_evidence = _window_free_run_scores(score_window)
                decision = manager.evaluate(scores)
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
                        "plausible_gain": decision.plausible_gain,
                        "state_aligned": decision.state_aligned,
                        "sample_count": len(score_window),
                        "score_frame_ids": score_evidence["score_frame_ids"],
                        "score_role_generation": role_generation,
                        "score_role_generations": [role_generation],
                        "horizon_metrics": score_evidence["horizon_metrics"],
                        "braking_or_coast_sample_count": score_evidence["braking_or_coast_sample_count"],
                        "candidate_snapshot": _json_value(decision.candidate_snapshot),
                        "incumbent_snapshot": _json_value(decision.incumbent_snapshot),
                    }
                )
            previous_realized_duty = frame.realized_duty
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
        managed_recovery=next(
            (
                (
                    float(item["incumbent_prediction_score"]),
                    float(item["candidate_prediction_score"]),
                )
                for item in reversed(promotion_history)
                if item.get("kind") == "five-minute-evaluation"
                and item.get("promoted") is True
                and initialization != "correct"
            ),
            None,
        ),
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
            "final_active_snapshot": _json_value(_bakeoff_snapshot(active_model)),
            "simulator_prediction_diagnostics": diagnostics,
            "initial_batch_fit_ms": fit_ms,
            "adaptation": {
                "alignment": alignment.value,
                "policy": {"max_gain": policy.max_gain},
            },
            "free_run_classifications": free_run_classifications,
            "runtime_tracking": _json_value(manager.tracking_evidence),
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
    fit_end, validation_end = _simulator_boundaries(plant, samples)
    if min(fit_end, validation_end - fit_end, samples - validation_end) < 2:
        raise ValueError(f"{plant} calibration record cannot support chronological splits")
    model = _model_for_initialization(arm, initialization)
    fit_record = _record_slice(initialized_record, 0, fit_end)
    started = perf_counter()
    _fit_model(model, fit_record)
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


def _simulator_boundaries(plant: str, samples: int) -> tuple[int, int]:
    """Return fixed chronological endpoints independent of scenario duration."""
    if plant == "MAKGrillSim":
        fit_end, validation_end = 147, 315
    else:
        fit_end, validation_end = int(samples * 0.35), int(samples * 0.75)
    if not 2 <= fit_end < validation_end < samples:
        raise ValueError(f"{plant} calibration record cannot support fixed splits")
    return fit_end, validation_end


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
        record.ambient_c + (record.temp_c - record.ambient_c) * 1.6 if initialization == "wrong-pole" else record.temp_c
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
            "correct": ScheduledARXConfig(na=2, nb=2, delays=(1, 2, 3), initial_covariance=10.0),
            "wrong-gain": ScheduledARXConfig(na=2, nb=2, delays=(1, 2, 3), initial_covariance=10.0),
            "wrong-pole": ScheduledARXConfig(na=2, nb=2, delays=(1, 2, 3), forgetting_factor=0.90),
            "wrong-delay": ScheduledARXConfig(na=2, nb=2, delays=(4, 5, 6)),
        }
        return ScheduledARX(configs[initialization])
    if arm == "dmc":
        configs = {
            "correct": DMCConfig(terms=(2, 3), poles=(0.3, 0.6), delay_seconds=(0, 20, 40)),
            "wrong-gain": DMCConfig(
                terms=(2, 3), poles=(0.3, 0.6), delay_seconds=(0, 20, 40), final_gain_bounds=(1e-6, 0.2)
            ),
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

def _record_frames(record: SignalRecord) -> tuple[FrameObservation, ...]:
    return tuple(
        FrameObservation(
            frame_start_s=float(time_s) - _FRAME_S,
            frame_end_s=float(time_s),
            temp_c=float(temp_c),
            setpoint_c=float(temp_c),
            ambient_c=float(ambient_c),
            requested_q=float(q),
            realized_q=float(q),
            requested_auger_duty=float(q),
            delivered_on_s=float(q) * _FRAME_S,
            requested_fan_duty=None,
            actual_fan_duty=None,
            result_revision=0,
            output_source="bakeoff-record",
            lid_open=False,
            safety_inhibited=False,
            manual_override=False,
            stale=False,
            skipped=False,
            reset=False,
            continuous=True,
            role_generation=0,
        )
        for time_s, temp_c, q, ambient_c in zip(
            record.time_s, record.temp_c, record.q, record.ambient_c, strict=True
        )
    )


def _fit_model(model: Any, record: SignalRecord) -> None:
    if isinstance(model, ScheduledARX):
        model.fit(_record_frames(record))
    else:
        model.fit(record)


def _forecast_model(
    model: Any,
    prefix: SignalRecord,
    q_future: np.ndarray,
    ambient_future: np.ndarray,
) -> np.ndarray:
    if isinstance(model, ScheduledARX):
        return model.forecast(_record_frames(prefix), q_future, ambient_future)
    return model.forecast(prefix, q_future, ambient_future)

def _bakeoff_snapshot(model: Any) -> Mapping[str, object]:
    """Adapt a production v2 model snapshot to legacy evidence diagnostics."""
    snapshot = model.snapshot()
    if snapshot.get("schema") != "scheduled-arx/v2":
        return snapshot
    status = snapshot.get("status")
    active_delay = snapshot.get("active_delay")
    if not isinstance(status, Mapping) or not isinstance(active_delay, int):
        raise ValueError("production scheduled-ARX snapshot lacks status diagnostics")
    return {
        **snapshot,
        "delay_steps": active_delay,
        "delay_seconds": float(active_delay * _FRAME_S),
        "steady_gain": status.get("steady_gain"),
        "knots_c": status.get("knots_c"),
        "regions": status.get("regions"),
        "plausibility_bounds": {
            "max_dc_gain_c_per_q": status.get("max_dc_gain_c_per_q"),
            "max_ar_pole": status.get("max_ar_pole"),
        },
        "update_timing": {
            "last_observation_time_s": status.get("last_observation_time_s"),
            "refreshes": status.get("refreshes"),
            "max_forecast_deviation_c": status.get("max_forecast_deviation_c"),
            "last_refresh_sample": status.get("last_refresh_sample"),
        },
    }


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
            predicted = _forecast_model(
                model,
                prefix,
                record.q[start : start + steps],
                record.ambient_c[start : start + steps],
            )
            values.append(float(np.mean(np.abs(predicted - record.temp_c[start : start + steps]))))
        residuals[horizon_s] = values
    return residuals


def _simulator_prediction_diagnostics(arm: str, plant: str, seed: int, initialization: str) -> dict[str, Any]:
    """Publish diagnostics for legacy callers from their cached prepared origin."""
    model, _, _, _, record = _prepared_model(arm, plant, seed, initialization)
    return _simulator_prediction_diagnostics_from_model(model, record, plant)


def _simulator_prediction_diagnostics_from_model(model: Any, record: SignalRecord, plant: str) -> dict[str, Any]:
    """Publish raw untouched suffix forecasts from an already fitted origin."""
    fit_end, validation_end = _simulator_boundaries(plant, record.temp_c.size)
    diagnostics: dict[str, Any] = {}
    for horizon_s in (60, 300, 900, 1800, 3600):
        steps = horizon_s // _FRAME_S
        origins: list[dict[str, Any]] = []
        residuals: list[float] = []
        coast_braking_residuals: list[float] = []
        for start in range(validation_end, record.temp_c.size - steps + 1, max(1, steps // 6)):
            predicted = _forecast_model(
                model,
                _record_slice(record, 0, start),
                record.q[start : start + steps],
                record.ambient_c[start : start + steps],
            )
            values = [float(value) for value in predicted - record.temp_c[start : start + steps]]
            coast_mask, braking_mask = _coast_braking_masks(record, start, steps)
            selected = [
                value
                for value, coast, braking in zip(values, coast_mask, braking_mask, strict=True)
                if coast or braking
            ]
            residuals.extend(values)
            coast_braking_residuals.extend(selected)
            origins.append(
                {
                    "origin_index": start,
                    "origin_time_s": float(record.time_s[start]),
                    "coast_mask": coast_mask,
                    "braking_mask": braking_mask,
                    "coast_or_braking_mask": [
                        coast or braking for coast, braking in zip(coast_mask, braking_mask, strict=True)
                    ],
                    "residuals_c": values,
                    "coast_braking_residuals_c": selected,
                }
            )
        if not residuals:
            diagnostics[str(horizon_s)] = None
            continue
        values = np.asarray(residuals, dtype=np.float64)
        masked = np.asarray(coast_braking_residuals, dtype=np.float64)
        diagnostics[str(horizon_s)] = {
            "origins": origins,
            "origin_count": len(origins),
            "coast_braking_sample_count": int(masked.size),
            "rmse_c": float(np.sqrt(np.mean(values * values))),
            "max_abs_error_c": float(np.max(np.abs(values))),
            "bias_c": float(np.mean(values)),
            "p90_abs_error_c": float(np.percentile(np.abs(values), 90.0)),
            "coast_braking_temperature_error_c": (float(np.mean(np.abs(masked))) if masked.size else None),
            "steady_gain_error_c_per_q": _steady_gain_error(
                _bakeoff_snapshot(model), record
            ),
            "delay_error_s": _delay_error_s(
                _bakeoff_snapshot(model), record, validation_end
            ),
        }
    return {
        "boundaries": {
            "fit": [0, fit_end],
            "validation": [fit_end, validation_end],
            "test": [validation_end, int(record.temp_c.size)],
        },
        "diagnostics_c": diagnostics,
    }


def _coast_braking_masks(record: SignalRecord, start: int, steps: int) -> tuple[list[bool], list[bool]]:
    """Classify each future point; never apply a horizon-wide surrogate label."""
    future_q = record.q[start : start + steps]
    preceding_q = np.concatenate(([record.q[start - 1]], future_q[:-1]))
    return (
        [bool(value <= 0.05) for value in future_q],
        [bool(value < previous - 1e-9) for value, previous in zip(future_q, preceding_q, strict=True)],
    )


def _steady_gain_error(snapshot: Mapping[str, object], record: SignalRecord) -> float:
    """Compare the arm-neutral fitted gain to a measured simulator gain."""
    half = record.temp_c.size // 2
    observed_delta_q = float(np.mean(record.q[half:]) - np.mean(record.q[:half]))
    if abs(observed_delta_q) <= 1e-9:
        raise ValueError("simulator record cannot measure steady gain")
    fitted = snapshot.get("steady_gain")
    if not isinstance(fitted, (int, float)) or not np.isfinite(fitted):
        raise ValueError("fitted model lacks a finite steady_gain diagnostic")
    observed = float((np.mean(record.temp_c[half:]) - np.mean(record.temp_c[:half])) / observed_delta_q)
    return float(abs(float(fitted) - observed))


def _delay_error_s(snapshot: Mapping[str, object], record: SignalRecord, test_start: int) -> float:
    """Estimate delay against the untouched simulator suffix."""
    delay_steps = snapshot.get("delay_steps")
    if not isinstance(delay_steps, (int, float)):
        raise ValueError("fitted model lacks a delay_steps diagnostic")
    fitted = int(delay_steps)
    inputs = np.diff(record.q[test_start:])
    outputs = np.diff(record.temp_c[test_start:])
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


def _failure_sort_key(failure: Any) -> tuple[Any, ...]:
    key = getattr(failure, "matrix_key", None)
    if key is not None:
        return (
            key.arm,
            key.initialization,
            key.plant,
            key.mode,
            key.scenario,
            key.seed,
            key.mpc_horizon_s,
            failure.category,
            failure.detail,
        )
    return (failure.arm, "", "", "", failure.scenario, -1, -1, failure.category, failure.detail)


def _evidence_id(arm: str, plant: str, seed: int, initialization: str) -> str:
    """Return the immutable identity of one plant-specific prepared-model origin."""
    return f"{arm}:{plant}:{seed}:{initialization}"


def _horizon_selection_document(
    config: ExperimentConfig,
    *,
    prepared_origins: Mapping[tuple[str, str, str, int], PreparedOrigin] | None = None,
) -> dict[str, Any]:
    """Freeze one horizon after all independent validation origins have completed."""
    if prepared_origins is None:
        origins = tuple(
            (arm, initialization, plant, seed)
            for arm in ("scheduled-arx", "dmc", "state-space")
            for initialization in config.initializations
            for plant in ("GrillSim", "MAKGrillSim")
            for seed in sorted(config.seeds)
        )
        prepared_origins = _prepare_origins(origins, workers=1, blas_threads=1)
    residuals: dict[str, Mapping[int, tuple[float, ...]]] = {
        _evidence_id(origin.arm, origin.plant, origin.seed, origin.initialization): {
            horizon: tuple(values) for horizon, values in origin.before_residuals.items()
        }
        for _, origin in sorted(prepared_origins.items())
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
    managed_recovery: tuple[float, float] | None = None,
) -> dict[str, float | int | None]:
    error = np.asarray(temperatures) - np.asarray(targets)
    absolute = np.abs(error)
    overshoot = float(np.max(error))
    undershoot = float(max(0.0, -np.min(error)))
    hold = np.asarray(temperatures[-min(60, len(temperatures)) :])
    settled = next((index for index in range(len(error)) if np.all(absolute[index:] <= 3.0)), None)
    score = float(np.sqrt(np.mean(error**2)) + np.mean(absolute) + 0.5 * max(overshoot, 0.0))
    before_mae, after_mae = managed_recovery if managed_recovery is not None else (None, None)
    recovery_ratio = (
        after_mae / before_mae if before_mae is not None and after_mae is not None and before_mae > 0.0 else None
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


def _independent_box_qp_reference(hessian: np.ndarray, linear: np.ndarray, start: np.ndarray) -> dict[str, Any]:
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


def _aggregate_simulator_diagnostics(by_domain: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, Any], bool]:
    """Aggregate every simulator arm/domain/mode/initialization diagnostic."""
    metric_names = (
        "rmse_c",
        "max_abs_error_c",
        "bias_c",
        "p90_abs_error_c",
        "steady_gain_error_c_per_q",
        "delay_error_s",
        "coast_braking_temperature_error_c",
    )
    domains: dict[str, Any] = {}
    valid = bool(by_domain)
    for domain, document in sorted(by_domain.items()):
        diagnostics = document.get("diagnostics_c")
        if not isinstance(diagnostics, Mapping):
            valid = False
            continue
        domain_values: dict[str, Any] = {}
        for horizon, evidence in sorted(diagnostics.items(), key=lambda item: int(item[0])):
            if not isinstance(evidence, Mapping):
                valid = False
                continue
            values = {metric: evidence.get(metric) for metric in metric_names}
            values["origin_count"] = evidence.get("origin_count")
            values["coast_braking_sample_count"] = evidence.get("coast_braking_sample_count")
            domain_values[str(horizon)] = values
            valid = (
                valid
                and all(isinstance(value, (int, float)) and np.isfinite(value) for value in values.values())
                and values["origin_count"] > 0
                and values["coast_braking_sample_count"] > 0
            )
        domains[domain] = domain_values
    aggregate: dict[str, Any] = {}
    for horizon in ("60", "300", "900", "1800", "3600"):
        values = [diagnostics[horizon] for diagnostics in domains.values() if horizon in diagnostics]
        if len(values) != len(domains):
            valid = False
            continue
        aggregate[horizon] = {
            metric: (
                float(max(value[metric] for value in values))
                if metric == "max_abs_error_c"
                else float(np.mean([value[metric] for value in values]))
            )
            for metric in metric_names
        }
        aggregate[horizon]["origin_count"] = int(sum(value["origin_count"] for value in values))
        aggregate[horizon]["coast_braking_sample_count"] = int(
            sum(value["coast_braking_sample_count"] for value in values)
        )
    return {"by_domain": domains, "aggregate": aggregate}, valid


def _artifact_from_rows(
    config: ExperimentConfig,
    rows: list[ScenarioResult],
    failures=(),
    *,
    selection: Mapping[str, Any] | None = None,
    execution_metadata: Sequence[Mapping[str, Any]] = (),
    source_revision: str | None = None,
) -> ExperimentArtifact:
    """Reduce rows strictly in full matrix identity, never executor arrival order."""
    ordered_rows = sorted(rows, key=_row_key)
    by_arm: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    prediction_origins: dict[str, dict[str, dict[str, tuple[float, ...]]]] = defaultdict(lambda: defaultdict(dict))
    recovery: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    timings: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    seen_evidence: set[tuple[str, str]] = set()
    correct_scores: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    simulator_documents: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in ordered_rows:
        domain = f"{row.mode}:{row.initialization}:{row.plant}"
        by_arm[row.arm][domain].append(_metric_float(row.metrics, "control_score"))
        diagnostic = (row.model_evidence or {}).get("simulator_prediction_diagnostics")
        if isinstance(diagnostic, Mapping) and domain not in simulator_documents[row.arm]:
            simulator_documents[row.arm][domain] = diagnostic
        if row.initialization == "correct":
            correct_scores[(row.arm, row.plant, row.mode)].append(_metric_float(row.metrics, "control_score"))
        evidence_key = (row.arm, row.evidence_id)
        if evidence_key not in seen_evidence:
            seen_evidence.add(evidence_key)
            for horizon, values in (row.horizon_residuals_c or {}).items():
                prediction_origins[row.arm][horizon][row.evidence_id] = values
        if row.mode == "online" and row.initialization != "correct":
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
    simulator_aggregates = {arm: _aggregate_simulator_diagnostics(simulator_documents[arm]) for arm in by_arm}
    arm_values: list[ArmEvidence] = []
    for arm, domains in sorted(by_arm.items()):
        diagnostics, diagnostics_valid = simulator_aggregates[arm]
        sixty_minute = diagnostics["aggregate"].get("3600")
        prediction_error = (
            float(sixty_minute["rmse_c"])
            if isinstance(sixty_minute, Mapping)
            else _mean_or_none(_flatten_origins(prediction_origins[arm].get("600", {}))) or 0.0
        )
        diagnostic_values = sixty_minute if isinstance(sixty_minute, Mapping) else {}
        arm_values.append(
            ArmEvidence(
                name=arm,
                domain_median_scores={domain: float(median(scores)) for domain, scores in sorted(domains.items())},
                ranking_domain_scores={
                    plant: float(median(scores))
                    for (candidate_arm, plant, mode), scores in sorted(correct_scores.items())
                    if candidate_arm == arm and mode == "online"
                },
                correct_baseline_no_degradation=all(
                    float(median(scores)) <= float(median(correct_scores[(arm, plant, "frozen")])) * 1.01
                    for (candidate_arm, plant, mode), scores in correct_scores.items()
                    if candidate_arm == arm and mode == "online" and (arm, plant, "frozen") in correct_scores
                ),
                prediction_error=prediction_error,
                before_mae=_mean_or_none(recovery[arm]["recovery_before_mae_c"]) or 0.0,
                after_mae=_mean_or_none(recovery[arm]["recovery_after_mae_c"]) or 0.0,
                recovery_improvement_ratio=(_mean_or_none(recovery[arm]["recovery_improvement_ratio"]) or 0.0),
                recovery_improvement_delta=(_mean_or_none(recovery[arm]["recovery_improvement_delta_c"]) or 0.0),
                recovery_available=bool(recovery[arm]["recovery_improvement_ratio"]),
                raw_solve_p99_ms=_p99(timings[arm]["solve"]),
                raw_learner_ms=tuple(timings[arm]["learner"]),
                raw_refresh_ms=tuple(timings[arm]["refresh"]),
                raw_solve_ms=tuple(timings[arm]["solve"]),
                runtime_validity="not_measured",
                simulator_diagnostics=diagnostics,
                simulator_diagnostics_available=bool(diagnostics["aggregate"]),
                simulator_diagnostics_valid=diagnostics_valid,
                simulator_gain_error_c_per_q=float(diagnostic_values.get("steady_gain_error_c_per_q", 0.0)),
                simulator_delay_error_s=float(diagnostic_values.get("delay_error_s", 0.0)),
                simulator_coast_braking_error_c=float(diagnostic_values.get("coast_braking_temperature_error_c", 0.0)),
                target_missed=prediction_error > 5.0,
                operational_consequence=(
                    "not deployment-ready for 60-minute prediction; retain experiment-only use"
                    if prediction_error > 5.0
                    else None
                ),
            )
        )
    arms = tuple(arm_values)
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
            "simulator": arm.simulator_diagnostics,
        }
        for arm in arms
    }
    selection = _horizon_selection_document(config) if selection is None else dict(selection)
    return ExperimentArtifact(
        config={
            **config.to_document(),
            "execution_metadata": {
                "blas_threads": config.blas_threads or 1,
                "cells": [dict(item) for item in sorted(execution_metadata, key=lambda item: int(item["ordinal"]))],
                "workers": config.workers,
            },
            "horizon_selection": selection,
            "horizon_selection_window": "per-domain chronological validation partitions",
            "horizon_tie_rule": "shortest horizon within 1% of pooled validation best",
            "runtime_measurement": {
                "status": "not_measured",
                "reason": "concurrent workstation workloads contaminated timing evidence",
                "required_follow_up": "repeat timing evidence in an isolated rerun",
            },
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
            "fitted_by_domain": {row.evidence_id: dict(row.model_evidence or {}) for row in ordered_rows},
        },
        scenarios=tuple(ordered_rows),
        arms=arms,
        failures=tuple(sorted(failures, key=_failure_sort_key)),
        source_revision=source_revision if source_revision is not None else _source_revision(),
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
            _fit_model(candidate, fit_record)
            validation = _horizon_residuals(candidate, record, starts=validation_starts, horizons_s=(60, 300))
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
            str(horizon): _mean_or_none(values) for horizon, values in sorted(diagnostics.items())
        }
        result["origins"] = {str(horizon): list(values) for horizon, values in sorted(diagnostics.items())}
        result["validation_candidate_scores"] = [float(score) for score, _ in scored_candidates]
        result["fitted"] = _json_value(_bakeoff_snapshot(model))
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
            fit_end, validation_end = _simulator_boundaries(plant, record.temp_c.size)
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
        float(
            np.mean(
                [
                    value
                    for index in generator.integers(len(populated), size=len(populated))
                    for value in populated[index]
                ]
            )
        )
        for _ in range(1_000)
    ]
    return [float(np.percentile(samples, 2.5)), float(np.percentile(samples, 97.5))]


def _source_revision() -> str:
    """Capture this workspace's immutable revision without reading mutable files."""
    import subprocess

    try:
        revision = subprocess.check_output(
            ["jj", "--ignore-working-copy", "--no-pager", "log", "-r", "@", "--no-graph", "-T", "commit_id"],
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError("unable to capture a current Jujutsu source revision") from error
    if len(revision) != 40 or any(character not in "0123456789abcdef" for character in revision):
        raise RuntimeError("Jujutsu returned a stale or invalid source revision")
    return revision


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
