"""RED→GREEN contracts for bounded deterministic bake-off parallelism."""

from __future__ import annotations

import hashlib
from dataclasses import replace
import json
import os
import pickle
from pathlib import Path

import pytest
from docs.superpowers.experiments.linear_mpc_bakeoff import runner as runner_module

from docs.superpowers.experiments.linear_mpc_bakeoff.runner import (
    CellExecution,
    ExperimentConfig,
    MatrixJob,
    _checkpoint_path,
    _lpt_jobs,
    _execute_cells,
    _prepare_origin,
    _resolve_blas_threads,
    _resolve_workers,
    _run_matrix,
    _run_prepared_cell,
    _single_thread_worker_environment,
    run_experiment,
)


def _micro_config(*, workers: int | None) -> ExperimentConfig:
    return ExperimentConfig(
        quick_mode=True,
        seeds=(2,),
        duration_s=20,
        initializations=("correct",),
        workers=workers,
    )


def test_worker_resolution_precedence_and_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 12)
    monkeypatch.setenv("PIFIRE_LINEAR_MPC_WORKERS", "3")
    assert _resolve_workers(None, pending_bundles=99) == 3
    assert _resolve_workers(2, pending_bundles=99) == 2
    assert _resolve_workers(None, pending_bundles=1) == 1
    with pytest.raises(ValueError, match="positive integer"):
        _resolve_workers(0, pending_bundles=99)
    with pytest.raises(ValueError, match="safe cap"):
        _resolve_workers(9, pending_bundles=99)


def test_blas_resolution_precedence_and_core_product_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 12)
    monkeypatch.setenv("PIFIRE_LINEAR_MPC_BLAS_THREADS", "2")
    assert _resolve_blas_threads(None, workers=4) == 2
    assert _resolve_blas_threads(1, workers=8) == 1
    with pytest.raises(ValueError, match="CPU budget"):
        _resolve_blas_threads(3, workers=4)
    monkeypatch.setenv("PIFIRE_LINEAR_MPC_BLAS_THREADS", "0")
    with pytest.raises(ValueError, match="positive integer"):
        _resolve_blas_threads(None, workers=1)


def test_worker_environment_pins_every_native_pool() -> None:
    assert _single_thread_worker_environment() == {
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
    }


def test_lpt_scheduler_starts_high_cost_arms_first_in_stable_order() -> None:
    from docs.superpowers.experiments.linear_mpc_bakeoff.scenarios import quick_scenarios

    definition = quick_scenarios()[0]
    jobs = [
        MatrixJob(index, arm, "correct", definition, "GrillSim", "frozen", 2, 600, 20)
        for index, arm in enumerate(("scheduled-arx", "dmc", "state-space"))
    ]
    assert [job.arm for job in _lpt_jobs(jobs)] == [
        "state-space",
        "dmc",
        "scheduled-arx",
    ]


def test_lpt_refills_every_completed_slot_before_yielding_reversed_arrivals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from docs.superpowers.experiments.linear_mpc_bakeoff.scenarios import quick_scenarios

    definition = quick_scenarios()[0]
    jobs = [
        MatrixJob(index, arm, "correct", definition, "GrillSim", "frozen", 2, 600, 20)
        for index, arm in enumerate(("state-space", "dmc", "scheduled-arx", "dmc", "scheduled-arx"))
    ]
    submitted: list[MatrixJob] = []
    occupancy: list[tuple[int, int]] = []

    class Future:
        def __init__(self, job: MatrixJob) -> None:
            self.job = job

        def result(self) -> CellExecution:
            return CellExecution(self.job.ordinal, b'{"kind":"row","row":{}}\\n', 0.0)

    class Executor:
        def __init__(self, **_: object) -> None:
            pass

        def __enter__(self) -> "Executor":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def submit(self, _: object, job: MatrixJob, __: object) -> Future:
            submitted.append(job)
            return Future(job)

    def barrier_wait(inflight: dict[Future, MatrixJob], **_: object) -> tuple[set[Future], set[Future]]:
        occupancy.append((len(inflight), len(submitted)))
        completed = set(sorted(inflight, key=lambda future: future.job.ordinal, reverse=True)[:2])
        return completed, set(inflight).difference(completed)

    monkeypatch.setattr(runner_module, "ProcessPoolExecutor", Executor)
    monkeypatch.setattr(runner_module, "wait", barrier_wait)
    monkeypatch.setattr(runner_module, "_temporary_worker_environment", lambda _: ({}, {}))
    monkeypatch.setattr(runner_module, "_restore_worker_environment", lambda _: None)
    ordered = _lpt_jobs(jobs)
    completed = list(_execute_cells(ordered, {job.origin: object() for job in jobs}, workers=2, blas_threads=1))

    assert [job.arm for job in submitted[:2]] == ["state-space", "dmc"]
    assert all(active == 2 for active, submitted_count in occupancy if submitted_count < len(jobs))
    assert [result.ordinal for result in completed] == sorted(result.ordinal for result in completed)


def test_prepared_origin_payload_is_picklable() -> None:
    payload = _prepare_origin(("scheduled-arx", "correct", "GrillSim", 2))
    assert pickle.loads(pickle.dumps(payload)).origin == payload.origin


def test_spawned_cell_result_is_picklable_document() -> None:
    from docs.superpowers.experiments.linear_mpc_bakeoff.scenarios import quick_scenarios

    payload = _prepare_origin(("scheduled-arx", "correct", "GrillSim", 2))
    job = MatrixJob(0, "scheduled-arx", "correct", quick_scenarios()[0], "GrillSim", "frozen", 2, 600, 20)
    result = _run_prepared_cell(job, payload)
    assert isinstance(pickle.loads(pickle.dumps(result)).row, bytes)
    document, failure = runner_module._execution_parts(result)
    assert failure is None
    assert document is not None
    assert document["arm"] == "scheduled-arx"


def test_serial_and_parallel_router_are_canonically_equivalent(tmp_path: Path) -> None:
    serial = run_experiment(_micro_config(workers=1))
    parallel = run_experiment(_micro_config(workers=2))

    assert len(parallel.scenarios) == 36
    assert serial.canonical_document() == parallel.canonical_document()
    state_space_rows = [row for row in parallel.scenarios if row.arm == "state-space"]
    assert state_space_rows
    assert all(
        isinstance(row.model_evidence["batch_fit_snapshot"]["steady_gain"], (int, float))
        and row.model_evidence["batch_fit_snapshot"]["steady_gain"] > 0.0
        for row in state_space_rows
    )


def test_incremental_checkpoint_head_uses_bounded_deltas(tmp_path: Path) -> None:
    output = tmp_path / "micro.manifest.json"
    partial = _run_matrix(
        _micro_config(workers=1),
        checkpoint=output,
        resume=False,
        interrupt_after=5,
    )

    checkpoint = _checkpoint_path(output)
    manifest = json.loads(checkpoint.read_text())
    entries, nodes = runner_module._checkpoint_delta_entries(
        checkpoint, manifest["head"], manifest["run_fingerprint"], manifest["accepted_count"]
    )

    assert len(partial.scenarios) == 5
    assert manifest["checkpoint_schema"] == "incremental-cas/v2"
    assert manifest["accepted_count"] == len(entries) == len(nodes) == 5
    assert checkpoint.stat().st_size < 4 * 1024
    assert all(node.stat().st_size < 1024 for node in nodes)
    assert all((checkpoint.parent / entry["name"]).is_file() for entry in entries)

    resumed = _run_matrix(_micro_config(workers=1), checkpoint=output, resume=True)
    assert resumed.scenarios


def test_relative_checkpoint_resumes_and_cleans_exact_verified_deltas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runner_module, "_source_revision", lambda: "a" * 40)
    output = Path("relative.manifest.json")
    partial = _run_matrix(_micro_config(workers=1), checkpoint=output, resume=False, interrupt_after=2)
    checkpoint = _checkpoint_path(output)

    assert len(partial.scenarios) == 2
    assert checkpoint.exists()
    assert list(tmp_path.glob("relative.checkpoint.manifest.delta.*.json"))

    resumed = _run_matrix(_micro_config(workers=1), checkpoint=output, resume=True)

    assert resumed.scenarios
    assert not checkpoint.exists()
    assert not list(tmp_path.glob("relative.checkpoint.manifest.delta.*.json"))


def test_resume_rejects_traversal_in_verified_delta_entry(tmp_path: Path) -> None:
    output = tmp_path / "micro.manifest.json"
    _run_matrix(_micro_config(workers=1), checkpoint=output, resume=False, interrupt_after=4)
    checkpoint = _checkpoint_path(output)
    manifest = json.loads(checkpoint.read_text())
    node_path = checkpoint.parent / manifest["head"]["name"]
    node = json.loads(node_path.read_text())
    node["entry"]["name"] = "../outside.gz"
    node_bytes = (json.dumps(node, sort_keys=True, separators=(",", ":")) + "\n").encode()
    digest = hashlib.sha256(node_bytes).hexdigest()
    replacement = checkpoint.with_name(f"{checkpoint.stem}.delta.{digest}.json")
    replacement.write_bytes(node_bytes)
    manifest["head"] = {"name": replacement.name, "sha256": digest}
    checkpoint.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="path"):
        _run_matrix(_micro_config(workers=1), checkpoint=output, resume=True)


def test_resume_config_mismatch_aborts_before_preparation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "micro.manifest.json"
    _run_matrix(_micro_config(workers=1), checkpoint=output, resume=False, interrupt_after=1)

    def no_prepare(*args: object, **kwargs: object) -> None:
        raise AssertionError("resume mismatch reached expensive preparation")

    monkeypatch.setattr(runner_module, "_prepare_origins", no_prepare)
    with pytest.raises(ValueError, match="config mismatch"):
        _run_matrix(
            replace(_micro_config(workers=1), duration_s=40),
            checkpoint=output,
            resume=True,
        )
