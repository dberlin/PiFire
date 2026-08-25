"""RED contracts for the isolated, single-request grey fitting process."""

from __future__ import annotations

import math
import os
from dataclasses import FrozenInstanceError

import pytest

from controller.acados.contracts import GreyBoxMPCConfig
from controller.model_learning.contracts import CandidateOrigin, FitRequest, FitWindowIdentity, FrameObservation
from controller.runtime.model_fitting import (
    FIT_LOG_BOUNDS,
    FITTED_PARAMETERS,
    MAX_FIT_OBSERVATIONS,
    FitErrorCode,
    FitSubmission,
    GreyFitError,
    GreyFitJob,
    GreyFitSuccess,
    GreyFitWorker,
    _fit_grey_job,
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


def _frame(sequence: int) -> FrameObservation:
    realized = (0.2, 0.8, 0.5)[sequence % 3]
    return FrameObservation(
        frame_start_s=sequence * 25.0,
        frame_end_s=(sequence + 1) * 25.0,
        temp_c=80.0 + 0.3 * sequence,
        setpoint_c=120.0,
        ambient_c=20.0,
        requested_q=realized,
        realized_q=realized,
        requested_auger_duty=realized,
        delivered_on_s=realized * 25.0,
        requested_fan_duty=0.5,
        actual_fan_duty=0.5,
        result_revision=sequence + 1,
        output_source="controller",
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=True,
        role_generation=4,
        observation_sequence=sequence,
    )


def _request(*, first: int = 0, last: int = 11) -> FitRequest:
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


def _job(*, count: int = 12) -> GreyFitJob:
    return GreyFitJob(
        request=_request(last=count - 1),
        observations=tuple(_frame(index) for index in range(count)),
        config=GreyBoxMPCConfig(horizon_steps=12, temperature_weight=7.0, terminal_weight=9.0, move_weight=0.03),
    )


def _successful_kernel(job: GreyFitJob) -> GreyFitSuccess:
    values = {name: getattr(job.config, name) for name in job.config.__dataclass_fields__}
    values.update(C_c=900.0, K_Q=700.0, theta=75.0)
    return GreyFitSuccess(
        request=job.request,
        config=GreyBoxMPCConfig(**values),
        rmse_c=1.25,
        max_error_c=2.5,
        identifiability=1.1,
        sample_count=len(job.observations),
        temperature_band_c=(job.observations[0].temp_c, job.observations[-1].temp_c),
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
        "theta": (math.log(1e-9), math.log(1200.0)),
    }


def test_job_owns_an_immutable_bounded_snapshot() -> None:
    source = [_frame(index) for index in range(12)]
    job = GreyFitJob(request=_request(), observations=source, config=GreyBoxMPCConfig(horizon_steps=12))
    source.clear()
    assert len(job.observations) == 12
    assert isinstance(job.observations, tuple)
    with pytest.raises(FrozenInstanceError):
        job.config = GreyBoxMPCConfig()  # type: ignore[misc]
    with pytest.raises(ValueError, match="bounded"):
        GreyFitJob(
            request=_request(last=MAX_FIT_OBSERVATIONS),
            observations=tuple(_frame(index) for index in range(MAX_FIT_OBSERVATIONS + 1)),
            config=GreyBoxMPCConfig(),
        )


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
    assert success.config.delay_states == 8
    assert success.config.timestep_s == 25.0


def test_worker_is_spawned_single_process_single_request_and_restores_parent_thread_environment(monkeypatch) -> None:
    for index, name in enumerate(_THREAD_VARIABLES, start=2):
        monkeypatch.setenv(name, str(index))
    with GreyFitWorker(kernel=_successful_kernel) as worker:
        assert worker.start_method == "spawn"
        assert worker.process_count == 1
        assert worker.submit(_job()) is FitSubmission.ACCEPTED
        assert worker.submit(_job()) is FitSubmission.BUSY
        message = worker.receive(timeout_s=10.0)
    assert isinstance(message.outcome, GreyFitSuccess)
    assert message.request == _request()
    assert message.worker_start_method == "spawn"
    assert dict(message.worker_thread_environment) == {name: "1" for name in _THREAD_VARIABLES}
    assert worker.alive is False
    assert {name: os.environ.get(name) for name in _THREAD_VARIABLES} == {
        name: str(index) for index, name in enumerate(_THREAD_VARIABLES, start=2)
    }


def test_worker_exception_is_a_typed_error_with_the_exact_request_and_never_escapes() -> None:
    with GreyFitWorker(kernel=_raising_kernel) as worker:
        assert worker.submit(_job()) is FitSubmission.ACCEPTED
        message = worker.receive(timeout_s=10.0)
    assert isinstance(message.outcome, GreyFitError)
    assert message.outcome.request == _request()
    assert message.outcome.code is FitErrorCode.FIT_EXCEPTION
    assert message.outcome.error_type == "FloatingPointError"
    assert message.outcome.detail == "non-finite residual"


def test_blocking_receive_returns_typed_process_exit_when_child_dies() -> None:
    with GreyFitWorker(kernel=_exiting_kernel) as worker:
        assert worker.submit(_job()) is FitSubmission.ACCEPTED
        message = worker.receive()
    assert isinstance(message.outcome, GreyFitError)
    assert message.outcome.code is FitErrorCode.PROCESS_EXIT
    assert message.outcome.request == _request()


def test_result_identity_is_lossless_and_next_request_waits_for_result_drain() -> None:
    first = _job()
    second = GreyFitJob(
        request=_request(first=12, last=23),
        observations=tuple(_frame(index) for index in range(12, 24)),
        config=first.config,
    )
    with GreyFitWorker(kernel=_successful_kernel) as worker:
        assert worker.submit(first) is FitSubmission.ACCEPTED
        first_message = worker.receive(timeout_s=10.0)
        assert first_message.outcome.request == first.request
        assert worker.submit(second) is FitSubmission.ACCEPTED
        second_message = worker.receive(timeout_s=10.0)
    assert second_message.outcome.request == second.request
    assert second_message.outcome.request.window == second.request.window


def test_fit_aligns_each_end_temperature_interval_with_the_following_frame_load(monkeypatch) -> None:
    captured = {}

    def fit_params(t, temp, q, **kwargs):
        captured.update(t=tuple(t), temp=tuple(temp), q=tuple(q), kwargs=kwargs)
        return {
            "C_c": 900.0,
            "h_amb": kwargs["init"]["h_amb"],
            "K_Q": 700.0,
            "theta": 75.0,
            "sigma": kwargs["sigma"],
            "n_delay": kwargs["n_delay"],
            "T_amb": kwargs["T_amb"],
            "converged": True,
            "nfev": 4,
        }

    monkeypatch.setattr("controller.update_mpc.fit_params", fit_params)
    monkeypatch.setattr("controller.update_mpc.fit_quality", lambda *_args, **_kwargs: (1.0, 2.0))
    monkeypatch.setattr("controller.update_mpc.identifiability", lambda *_args, **_kwargs: 1.0)
    job = _job()
    _fit_grey_job(job)
    expected = tuple(frame.realized_q for frame in job.observations[1:]) + (job.observations[-1].realized_q,)
    assert captured["q"] == expected
    assert captured["t"][0] == 0.0
