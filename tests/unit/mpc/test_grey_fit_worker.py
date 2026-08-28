"""Contracts for the isolated, single-request segmented grey fitting process."""

from __future__ import annotations

import hashlib
import math
import os
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import numpy as np
import pytest
from scipy import optimize

from common.learning_trajectory import FitCorpusIdentity, FitCorpusSlice, canonical_trajectory_digest
from controller.acados.contracts import GreyBoxMPCConfig
from controller.model_learning.contracts import CandidateOrigin, FitRequest, FitWindowIdentity
from controller.runtime.model_fitting import (
    FIT_LOG_BOUNDS,
    FITTED_PARAMETERS,
    MAX_FIT_OBSERVATIONS,
    FitErrorCode,
    FitSubmission,
    GreyFitError,
    GreyFitJob,
    GreyFitSegmentArrays,
    GreyFitSuccess,
    GreyFitWorker,
    fit_segmented_grey,
)

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_THREAD_VARIABLES = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


def _request(*, first: int, last: int) -> FitRequest:
    return FitRequest(
        request_id=f"fit-{first}-{last}",
        origin=CandidateOrigin.PASSIVE_ONLINE,
        window=FitWindowIdentity(
            session_id="session-a",
            cook_id="cook-a",
            first_observation_sequence=first,
            last_observation_sequence=last,
            configuration_digest=_DIGEST_A,
            incumbent_digest=_DIGEST_B,
            role_generation=4,
        ),
        candidate_generation=9,
    )


def _segment(*, first: int, count: int) -> GreyFitSegmentArrays:
    loads = tuple((0.2, 0.8, 0.5)[index % 3] for index in range(count))
    return GreyFitSegmentArrays(
        segment_id=f"segment-{first}",
        cook_id="cook-a",
        through_ordinal=count - 1,
        prefix_digest=hashlib.sha256(f"segment-{first}".encode()).hexdigest(),
        fit_partition_digest=_DIGEST_A,
        observation_sequences=tuple(range(first, first + count)),
        initial_load=loads[0],
        pre_roll_duration_s=(),
        pre_roll_load=(),
        pre_roll_temperature_c=(),
        hold_anchor_c=80.0,
        scored_duration_s=(20.0,) * count,
        scored_load=loads,
        scored_ambient_c=(20.0,) * count,
        scored_temperature_c=tuple(80.3 + 0.3 * index for index in range(count)),
        calibration_origin=(False,) * count,
    )


def _corpus(segment: GreyFitSegmentArrays) -> FitCorpusIdentity:
    corpus_slice = FitCorpusSlice(
        segment_id=segment.segment_id,
        through_ordinal=segment.through_ordinal,
        prefix_digest=segment.prefix_digest,
        pre_roll_count=0,
        scored_count=len(segment.scored_load),
    )
    payload = {
        "schema_version": 1,
        "corpus_revision": 1,
        "fit_partition_digest": _DIGEST_A,
        "slices": [
            {
                "segment_id": corpus_slice.segment_id,
                "through_ordinal": corpus_slice.through_ordinal,
                "prefix_digest": corpus_slice.prefix_digest,
                "pre_roll_count": corpus_slice.pre_roll_count,
                "scored_count": corpus_slice.scored_count,
            }
        ],
    }
    return FitCorpusIdentity(
        schema_version=1,
        corpus_revision=1,
        fit_partition_digest=_DIGEST_A,
        slices=(corpus_slice,),
        corpus_digest=canonical_trajectory_digest(payload),
    )


def _job(
    *,
    first: int = 0,
    count: int = 12,
    config: GreyBoxMPCConfig | None = None,
) -> GreyFitJob:
    segment = _segment(first=first, count=count)
    resolved = (
        GreyBoxMPCConfig(
            horizon_steps=12,
            temperature_weight=7.0,
            terminal_weight=9.0,
            move_weight=0.03,
        )
        if config is None
        else config
    )
    return GreyFitJob(
        request=_request(first=first, last=first + count - 1),
        corpus=_corpus(segment),
        segments=(segment,),
        config=resolved,
    )


def _successful_kernel(job: GreyFitJob) -> GreyFitSuccess:
    temperatures = np.concatenate([segment.scored_temperature_c for segment in job.segments])
    return GreyFitSuccess(
        request=job.request,
        config=replace(job.config, C_c=900.0, K_Q=700.0, theta=75.0),
        rmse_c=1.25,
        max_error_c=2.5,
        identifiability=1.1,
        sample_count=sum(len(segment.scored_load) for segment in job.segments),
        temperature_band_c=(float(np.min(temperatures)), float(np.max(temperatures))),
        nfev=17,
    )


def _exiting_kernel(_job: GreyFitJob) -> GreyFitSuccess:
    os._exit(7)


def _raising_kernel(_job: GreyFitJob) -> GreyFitSuccess:
    raise FloatingPointError("non-finite residual")


def test_fit_contract_is_bounded_log_space_for_only_the_three_identifiable_parameters() -> None:
    assert FITTED_PARAMETERS == ("C_c", "K_Q", "theta")
    assert FIT_LOG_BOUNDS == {
        "C_c": (math.log(1.0), math.log(1e6)),
        "K_Q": (math.log(1e-3), math.log(1e4)),
        "theta": (math.log(25.0), math.log(1200.0)),
    }


def test_job_owns_read_only_compact_arrays_and_is_bounded() -> None:
    job = _job()
    segment = job.segments[0]

    assert isinstance(job.segments, tuple)
    assert segment.scored_load.flags.owndata is True
    assert segment.scored_load.flags.writeable is False
    assert not hasattr(job, "observations")
    with pytest.raises(ValueError):
        segment.scored_load[0] = 1.0
    with pytest.raises(FrozenInstanceError):
        job.config = GreyBoxMPCConfig()  # type: ignore[misc]
    with pytest.raises(ValueError, match="bounded"):
        _job(count=MAX_FIT_OBSERVATIONS + 1)


def test_success_changes_only_free_physics_and_preserves_fixed_structure_and_weights() -> None:
    job = _job()
    success = _successful_kernel(job)
    assert (success.config.C_c, success.config.K_Q, success.config.theta) != (
        job.config.C_c,
        job.config.K_Q,
        job.config.theta,
    )
    for fixed in (
        "h_amb",
        "T_amb",
        "sigma",
        "delay_states",
        "state_size",
        "timestep_s",
        "horizon_steps",
        "temperature_weight",
        "terminal_weight",
        "move_weight",
        "residual_weight",
        "max_iterations",
    ):
        assert getattr(success.config, fixed) == getattr(job.config, fixed)


def test_worker_is_spawned_single_process_single_request_and_restores_parent_thread_environment(monkeypatch) -> None:
    job = _job()
    for index, name in enumerate(_THREAD_VARIABLES, start=2):
        monkeypatch.setenv(name, str(index))
    with GreyFitWorker(kernel=_successful_kernel) as worker:
        assert worker.start_method == "spawn"
        assert worker.process_count == 1
        assert worker.submit(job) is FitSubmission.ACCEPTED
        assert worker.submit(_job()) is FitSubmission.BUSY
        message = worker.receive(timeout_s=10.0)
    assert isinstance(message.outcome, GreyFitSuccess)
    assert message.request == job.request
    assert message.worker_start_method == "spawn"
    assert dict(message.worker_thread_environment) == {name: "1" for name in _THREAD_VARIABLES}
    assert worker.alive is False
    assert {name: os.environ.get(name) for name in _THREAD_VARIABLES} == {
        name: str(index) for index, name in enumerate(_THREAD_VARIABLES, start=2)
    }


def test_worker_exception_is_a_typed_error_with_the_exact_request_and_never_escapes() -> None:
    job = _job()
    with GreyFitWorker(kernel=_raising_kernel) as worker:
        assert worker.submit(job) is FitSubmission.ACCEPTED
        message = worker.receive(timeout_s=10.0)
    assert isinstance(message.outcome, GreyFitError)
    assert message.outcome.request == job.request
    assert message.outcome.code is FitErrorCode.FIT_EXCEPTION
    assert message.outcome.error_type == "FloatingPointError"
    assert message.outcome.detail == "non-finite residual"


def test_blocking_receive_returns_typed_process_exit_when_child_dies() -> None:
    job = _job()
    with GreyFitWorker(kernel=_exiting_kernel) as worker:
        assert worker.submit(job) is FitSubmission.ACCEPTED
        message = worker.receive()
    assert isinstance(message.outcome, GreyFitError)
    assert message.outcome.code is FitErrorCode.PROCESS_EXIT
    assert message.outcome.request == job.request


def test_result_identity_is_lossless_and_next_request_waits_for_result_drain() -> None:
    first = _job()
    second = _job(first=12, config=first.config)
    with GreyFitWorker(kernel=_successful_kernel) as worker:
        assert worker.submit(first) is FitSubmission.ACCEPTED
        first_message = worker.receive(timeout_s=10.0)
        assert first_message.outcome.request == first.request
        assert worker.submit(second) is FitSubmission.ACCEPTED
        second_message = worker.receive(timeout_s=10.0)
    assert second_message.outcome.request == second.request
    assert second_message.outcome.request.window == second.request.window


def test_default_segmented_kernel_keeps_fixed_residual_shape_and_uses_no_continuous_job_path(
    monkeypatch,
) -> None:
    job = _job()
    point = np.log(np.asarray([getattr(job.config, key) for key in FITTED_PARAMETERS]))
    residual_lengths = []

    def fixed(residual, _x0, *args, **kwargs):
        residual_lengths.append(len(residual(point)))
        return SimpleNamespace(x=point, status=1, nfev=1, success=True)

    monkeypatch.setattr(optimize, "least_squares", fixed)
    outcome = fit_segmented_grey(job)

    assert isinstance(outcome, GreyFitSuccess)
    assert residual_lengths == [12, 12]
    assert outcome.optimizer_residual_count == 12
    assert not hasattr(job, "observations")
