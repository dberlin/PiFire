"""Shared grey online-learning fixtures for unit tests."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import numpy as np

from common.control_trace import AmbientSource
from common.learning_trajectory import (
    FitCorpusIdentity,
    FitCorpusSlice,
    canonical_fit_corpus_digest,
)
from common.persistence.learning_trajectory import LearningTrajectoryRepository
from controller.acados.contracts import GreyBoxMPCConfig
from controller.model_learning.contracts import (
    CandidateOrigin,
    FitRequest,
    FrameObservation,
)
from controller.runtime.model_fitting import (
    FitSubmission,
    GreyFitSuccess,
    GreyLearningOrchestrator,
    LiveLearningIdentity,
    TargetTimingEvidence,
    TriggerConfig,
    segmented_corpus_fit_job,
)
from tests.unit.common._learning_trajectory_helpers import (
    _finalize_segment,
    _segment,
)

_INCUMBENT = "1" * 64
_CHALLENGER = "2" * 64
_CONFIG = "3" * 64


def _frame(sequence: int, **changes) -> FrameObservation:
    q = (0.15, 0.50, 0.85)[sequence % 3]
    values: Any = {
        "frame_start_s": sequence * 20.0,
        "frame_end_s": (sequence + 1) * 20.0,
        "temp_c": 75.0 + sequence,
        "setpoint_c": 120.0,
        "ambient_c": 20.0,
        "requested_q": q,
        "realized_q": q,
        "requested_auger_duty": q,
        "delivered_on_s": q * 20.0,
        "requested_fan_duty": 0.5,
        "actual_fan_duty": 0.5,
        "result_revision": sequence + 1,
        "output_source": "controller",
        "lid_open": False,
        "safety_inhibited": False,
        "manual_override": False,
        "stale": False,
        "skipped": False,
        "reset": False,
        "continuous": True,
        "role_generation": 4,
        "observation_sequence": sequence,
        "ambient_source": AmbientSource.CONFIGURED,
    }
    values.update(changes)
    if "probe_q" in changes and "baseline_q" not in changes:
        values["baseline_q"] = values["requested_q"] - values["probe_q"]
    return FrameObservation(**values)


def _corpus(*, corpus_revision: int = 1) -> FitCorpusIdentity:
    marker = f"{corpus_revision % 16:x}" * 64
    corpus_slice = FitCorpusSlice(
        segment_id=f"segment-{corpus_revision}",
        through_ordinal=11,
        prefix_digest=marker,
        segment_content_digest=marker,
        pre_roll_count=0,
        scored_count=12,
    )
    return FitCorpusIdentity(
        schema_version=2,
        corpus_revision=corpus_revision,
        fit_partition_digest=_CONFIG,
        slices=(corpus_slice,),
        corpus_digest=canonical_fit_corpus_digest(
            schema_version=2,
            corpus_revision=corpus_revision,
            fit_partition_digest=_CONFIG,
            slices=(corpus_slice,),
        ),
    )


def _request(
    *,
    origin=CandidateOrigin.PASSIVE_ONLINE,
    fit_corpus=None,
    configuration_digest=_CONFIG,
    parent_incumbent_digest=_INCUMBENT,
    parent_incumbent_generation=4,
    candidate_generation=9,
    request_id="fit-a",
) -> FitRequest:
    return FitRequest(
        request_id=request_id,
        origin=origin,
        fit_corpus=_corpus() if fit_corpus is None else fit_corpus,
        configuration_digest=configuration_digest,
        parent_incumbent_digest=parent_incumbent_digest,
        parent_incumbent_generation=parent_incumbent_generation,
        candidate_generation=candidate_generation,
    )


def _identity(**changes) -> LiveLearningIdentity:
    values = {
        "session_id": "session-a",
        "cook_id": "cook-a",
        "configuration_digest": _CONFIG,
        "incumbent_digest": _INCUMBENT,
        "role_generation": 4,
        "candidate_generation": 9,
    }
    values.update(changes)
    return LiveLearningIdentity(**values)


def _fit(origin=CandidateOrigin.PASSIVE_ONLINE) -> GreyFitSuccess:
    return GreyFitSuccess(
        request=_request(origin=origin),
        config=GreyBoxMPCConfig(C_c=900.0, K_Q=700.0, theta=75.0, horizon_steps=12),
        rmse_c=1.0,
        max_error_c=2.0,
        identifiability=1.2,
        sample_count=12,
        temperature_band_c=(75.0, 110.0),
        nfev=11,
    )


def _persistent_fit_job(tmp_path, *, origin, config):
    repository = LearningTrajectoryRepository(str(tmp_path / "corpus.sqlite"))
    segment = replace(
        _segment("online-fit", scored_count=9),
        collection_provenance={
            "origin": origin.value,
            "role_generation": 4,
        },
    )
    _finalize_segment(repository, segment)
    snapshot = repository.snapshot_fit_corpus(segment.fit_partition_digest)
    identity = LiveLearningIdentity(
        session_id="session-a",
        cook_id="cook-a",
        configuration_digest=segment.fit_partition_digest,
        incumbent_digest=_INCUMBENT,
        role_generation=4,
        candidate_generation=9,
    )
    request = _request(
        origin=origin,
        fit_corpus=snapshot.identity,
        configuration_digest=identity.configuration_digest,
        parent_incumbent_digest=identity.incumbent_digest,
        parent_incumbent_generation=identity.role_generation,
        candidate_generation=identity.candidate_generation,
    )
    return identity, segmented_corpus_fit_job(snapshot, request, config)


class _Estimator:
    def __init__(self, config):
        self.config = config
        self.state = np.zeros(10)


class _Native:
    def __init__(self, config, *, fail=False):
        self.config = config
        self.fail = fail
        self.closed = False

    def solve(self, state, **_kwargs):
        if self.fail:
            raise RuntimeError("native rejected dry solve")
        assert np.asarray(state).shape == (10,)
        return SimpleNamespace(sequence_q=np.full(self.config.horizon_steps, 0.4), objective=1.0)

    def close(self):
        self.closed = True


def _timing(p99_ms=4.0):
    return TargetTimingEvidence(target="pi", samples=1000, p99_ms=p99_ms, limit_ms=5.0)


class _ImmediateFitWorker:
    def __init__(self, *, identifiability=1.2):
        self.job = None
        self.closed = False
        self.identifiability = identifiability

    def start(self):
        return self

    def submit(self, job):
        self.job = job
        return FitSubmission.ACCEPTED

    def receive(self, *, timeout_s):
        assert timeout_s == 0.0
        temperatures = tuple(float(value) for segment in self.job.segments for value in segment.scored_temperature_c)
        return SimpleNamespace(
            outcome=GreyFitSuccess(
                request=self.job.request,
                config=replace(self.job.config, C_c=900.0, K_Q=700.0, theta=75.0),
                rmse_c=1.0,
                max_error_c=2.0,
                identifiability=self.identifiability,
                sample_count=len(temperatures),
                temperature_band_c=(min(temperatures), max(temperatures)),
                nfev=4,
            )
        )

    def close(self):
        self.closed = True


class _ControlledSupersedingWorker(_ImmediateFitWorker):
    def __init__(self):
        super().__init__()
        self.next_submission: FitSubmission | BaseException = FitSubmission.ACCEPTED

    def submit(self, job):
        disposition = self.next_submission
        if isinstance(disposition, BaseException):
            raise disposition
        if disposition is FitSubmission.BUSY:
            return disposition
        return super().submit(job)


def _prepared_supersession_harness(tmp_path):
    worker = _ControlledSupersedingWorker()
    config = GreyBoxMPCConfig(horizon_steps=12)
    identity, job = _persistent_fit_job(
        tmp_path,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        config=config,
    )
    orchestrator = GreyLearningOrchestrator(
        identity=identity,
        config=config,
        incumbent_pair=object(),
        estimator_factory=_Estimator,
        controller_factory=_Native,
        timing_probe=lambda _native: _timing(),
        trigger_config=TriggerConfig(
            min_samples=9,
            min_input_variance=0.02,
            min_input_levels=3,
            min_temperature_span_c=8.0,
            min_identifiability=0.5,
        ),
        worker=worker,
    )
    assert orchestrator.submit_corpus_fit(job) is FitSubmission.ACCEPTED
    delivery = orchestrator.poll_fit_off_path(
        live_identity=identity,
        live_origin=CandidateOrigin.PASSIVE_ONLINE,
    )
    prepared = delivery.preparation
    assert prepared is not None and prepared.accepted
    replacement_request = replace(
        job.request,
        request_id="fit-b",
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
    )
    return (
        orchestrator,
        worker,
        identity,
        prepared,
        replace(job, request=replacement_request),
    )
