"""Fast deterministic seams for bake-off orchestration unit tests.

The numerical model and one-scenario contracts call their concrete functions
and remain unpatched. Matrix tests exercise orchestration, persistence, and
aggregation without turning each unit test into the full scientific bake-off.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
import json
from typing import Any, Protocol

import pytest
from docs.superpowers.experiments.linear_mpc_bakeoff import runner as runner_module
from docs.superpowers.experiments.linear_mpc_bakeoff.runner import ScenarioResult


_HORIZONS = ("60", "300", "900", "1800", "3600")
_BOUNDARIES = {"fit": [0, 1], "validation": [1, 2], "test": [2, 3]}


class _ScenarioDefinition(Protocol):
    name: str


class _MatrixJob(Protocol):
    ordinal: int
    arm: str
    plant: str
    mode: str
    seed: int
    initialization: str
    duration_s: int
    horizon_s: int
    definition: _ScenarioDefinition


@dataclass(frozen=True, slots=True)
class _CellExecution:
    ordinal: int
    row: bytes
    elapsed_s: float


@dataclass(frozen=True, slots=True)
class _PreparedOrigin:
    arm: str
    initialization: str
    plant: str
    seed: int

    @property
    def before_residuals(self) -> dict[int, tuple[float, ...]]:
        return {600: (1.0,), 800: (1.1,), 1000: (1.2,)}


def _unit_origins(
    origins: Iterable[tuple[str, str, str, int]],
    **_: object,
) -> dict[tuple[str, str, str, int], _PreparedOrigin]:
    return {origin: _PreparedOrigin(origin[0], origin[1], origin[2], origin[3]) for origin in origins}


def _canonical_bytes(document: dict[str, object]) -> bytes:
    return (json.dumps(document, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _diagnostic_document() -> dict[str, Any]:
    diagnostics = {}
    for horizon in _HORIZONS:
        origin = {
            "coast_or_braking_mask": [True],
            "coast_braking_residuals_c": [1.0],
            "residuals_c": [1.0],
        }
        diagnostics[horizon] = {
            "bias_c": 1.0,
            "coast_braking_sample_count": 1,
            "coast_braking_temperature_error_c": 1.0,
            "delay_error_s": 0.0,
            "max_abs_error_c": 1.0,
            "origin_count": 1,
            "origins": [origin],
            "p90_abs_error_c": 1.0,
            "rmse_c": 1.0,
            "steady_gain_error_c_per_q": 0.0,
        }
    return {"boundaries": dict(_BOUNDARIES), "diagnostics_c": diagnostics}


def _unit_row(job: _MatrixJob) -> ScenarioResult:
    model_evidence: dict[str, Any] = {
        "batch_fit_snapshot": {"steady_gain": 1.0},
    }
    if job.duration_s >= 140:
        model_evidence["simulator_prediction_diagnostics"] = _diagnostic_document()
    return ScenarioResult(
        arm=job.arm,
        plant=job.plant,
        mode=job.mode,
        scenario=job.definition.name,
        seed=job.seed,
        initialization=job.initialization,
        fan_fraction=(1.0,),
        requested_q=(0.5,),
        realized_q=(0.5,),
        temperature_c=(100.0,),
        target_c=(100.0,),
        metrics={
            "control_score": 1.0,
            "recovery_after_mae_c": 1.0,
            "recovery_before_mae_c": 2.0,
            "recovery_improvement_delta_c": 1.0,
            "recovery_improvement_ratio": 0.5,
            "requested_realized_duty_mae": 0.0,
        },
        mpc_horizon_s=job.horizon_s,
        raw_learner_ms=(0.1,),
        raw_refresh_ms=(0.1,),
        raw_solve_ms=(0.1,),
        horizon_residuals_c={"600": (1.0,), "800": (1.1,), "1000": (1.2,)},
        model_evidence=model_evidence,
    )


def _unit_cell(job: _MatrixJob, prepared: object) -> _CellExecution:
    del prepared
    row = _unit_row(job)
    envelope: dict[str, object] = {"kind": "row", "row": row.to_document()}
    return _CellExecution(job.ordinal, _canonical_bytes(envelope), 0.0)


class _Future:
    def __init__(self, execution: _CellExecution) -> None:
        self._execution = execution

    def result(self) -> _CellExecution:
        return self._execution


class _Executor:
    def __init__(self, **_: object) -> None:
        pass

    def __enter__(self) -> _Executor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def submit(self, function: Any, job: _MatrixJob, prepared: object) -> _Future:
        return _Future(function(job, prepared))


def _complete_futures(
    futures: Iterable[_Future],
    **_: object,
) -> tuple[set[_Future], set[_Future]]:
    return set(futures), set()


@pytest.fixture(autouse=True)
def _fast_matrix_orchestration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace only whole-matrix scientific work with deterministic evidence."""

    monkeypatch.setattr(runner_module, "_prepare_origins", _unit_origins)
    monkeypatch.setattr(runner_module, "_run_prepared_cell", _unit_cell)
    monkeypatch.setattr(runner_module, "ProcessPoolExecutor", _Executor)
    monkeypatch.setattr(runner_module, "wait", _complete_futures)
    aggregate_diagnostics = runner_module._aggregate_simulator_diagnostics
    monkeypatch.setattr(
        runner_module,
        "_aggregate_simulator_diagnostics",
        lambda documents: (
            aggregate_diagnostics(documents) if documents else ({"by_domain": {}, "aggregate": {}}, False)
        ),
    )
    monkeypatch.setattr(
        runner_module,
        "_split_evidence",
        lambda config: {
            **{f"{plant}:{seed}": dict(_BOUNDARIES) for plant in ("GrillSim", "MAKGrillSim") for seed in config.seeds},
            "real-MAK": dict(_BOUNDARIES),
        },
    )
    monkeypatch.setattr(
        runner_module,
        "_real_mak_evidence",
        lambda arm: {
            "provenance": "requested-input-reconstruction",
            "diagnostics_c": {
                horizon: (None if arm == "state-space" or horizon in {"900", "1800", "3600"} else 1.0)
                for horizon in _HORIZONS
            },
            **({"failure": "unit fixture: unavailable"} if arm == "state-space" else {}),
        },
    )
    monkeypatch.setattr(
        runner_module,
        "_initialization_snapshot",
        lambda arm, initialization: {"arm": arm, "initialization": initialization},
    )
    monkeypatch.setattr(runner_module, "_source_revision", lambda: "a" * 40)
    monkeypatch.setattr(runner_module, "_environment_versions", lambda: {"python": "unit"})
