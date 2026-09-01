"""Direct behavioral contracts for the grey learning runtime."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from math import ceil
from types import SimpleNamespace

import pytest

from common.control_trace import AllocationClampReason, TraceEventKind
from common.model_evidence import (
    AllocationEvidence,
    CalibrationSummaryEvidence,
    EvidenceKind,
    ModelEvidenceRecord,
)
from common.persistence.model_challenger import read_model_challenger
from common.persistence.model_evidence import append_model_evidence, read_model_activation
from controller.model_learning.contracts import (
    ActivationPolicy,
    CandidateOrigin,
    FitRequest,
    LearningStatus,
)
from controller.model_learning.evaluation import (
    EvaluationConfig,
    EvaluationDecision,
)
from controller.model_learning.grey_runtime import GreyLearningProcessOwner
from controller.mpc_model import EstimatorSeed
from controller.runtime.model_fitting import (
    CandidatePair,
    FitSubmission,
    GreyFitJob,
    GreyLearningOrchestrator,
    LiveLearningIdentity,
    TargetTimingEvidence,
    TriggerConfig,
    TriggerDecision,
    handoff_candidate,
    segmented_corpus_fit_job,
)
from tests.unit.common._learning_trajectory_helpers import _finalize_segment, _segment
from tests.unit.common._model_challenger_helpers import _corpus
from tests.unit.mpc._grey_learning_runtime_helpers import (
    _COMPLETE_SCORES,
    _automatic_candidate,
    _CheckpointStore,
    _close_prepared_candidate,
    _ControlledDeliveryCorpusWorker,
    _CorpusRepositoryProbe,
    _CorpusWorker,
    _DeliveringCorpusWorker,
    _descriptor,
    _fit_success,
    _frame,
    _harness,
    _operator_candidate,
    _ProbeSolver,
    _reopened_corpus,
    _reopened_ready_passive_corpus,
    _seed_durable_challenger,
    _SubmissionFailureWorker,
    _SuccessfulWorker,
)


def test_accepted_fit_lineage_advances_once_and_blocks_passive_fit_until_retired(
    ds,
    tmp_path,
) -> None:
    repository, partition = _reopened_ready_passive_corpus(tmp_path)
    harness = _harness()
    preparation_source, _evaluation, components = _operator_candidate(harness)
    request = replace(
        preparation_source.candidate.request,
        origin=CandidateOrigin.PASSIVE_ONLINE,
    )
    preparation = replace(
        preparation_source,
        candidate=replace(preparation_source.candidate, request=request),
    )

    def delivery_for(candidate_preparation):
        return SimpleNamespace(
            message=SimpleNamespace(
                request=candidate_preparation.candidate.request,
                outcome=SimpleNamespace(config=candidate_preparation.candidate.config),
            ),
            stale_reasons=(),
            blockers=(),
            preparation=candidate_preparation,
        )

    class _Learning:
        def __init__(self):
            self.prepared = preparation
            self.pending_request = request
            self.handoff = None
            self.worker = SimpleNamespace(busy=False)
            self.delivery = delivery_for(preparation)
            self.evaluation_config = EvaluationConfig(required_consecutive_wins=2)

        def poll_fit_off_path(self, **_kwargs):
            return self.delivery

        def evaluate_ready_off_path(self):
            return None

        def _release_prepared(self) -> None:
            prepared = self.prepared
            self.prepared = None
            if prepared is not None and prepared.accepted:
                _close_prepared_candidate(prepared)

        def _reset_prepared_evaluation(self) -> None:
            self.handoff = None

        def close(self):
            self._release_prepared()

    learning = _Learning()
    harness.runtime._learning = learning
    before = harness.runtime.get_model_snapshot()

    harness.runtime.poll_learning_off_path(
        live_origin=CandidateOrigin.PASSIVE_ONLINE,
    )
    first = harness.runtime.get_model_snapshot()
    first_challenger = read_model_challenger()
    assert first_challenger is not None
    harness.runtime._trajectory_repository = repository
    harness.runtime._fit_partition_digest = lambda: partition
    harness.runtime._learning_enabled = True
    assert not harness.runtime.request_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    harness.runtime._trajectory_repository = None
    harness.runtime._fit_partition_digest = None
    harness.runtime._learning_enabled = False
    harness.runtime.poll_learning_off_path(
        live_origin=CandidateOrigin.PASSIVE_ONLINE,
    )
    second = harness.runtime.get_model_snapshot()

    next_corpus = _corpus("next-fit")
    next_request = replace(
        request,
        request_id="d" * 64,
        fit_corpus=next_corpus,
    )
    next_preparation = replace(
        preparation,
        candidate=replace(
            preparation.candidate,
            request=next_request,
            result_digest=next_request.request_id,
        ),
    )
    learning.prepared = next_preparation
    learning.pending_request = next_request
    learning.delivery = delivery_for(next_preparation)
    harness.runtime.poll_learning_off_path(
        live_origin=CandidateOrigin.PASSIVE_ONLINE,
    )
    third = harness.runtime.get_model_snapshot()
    third_challenger = read_model_challenger()
    assert third_challenger is not None
    harness.runtime.poll_learning_off_path(
        live_origin=CandidateOrigin.PASSIVE_ONLINE,
    )
    fourth = harness.runtime.get_model_snapshot()

    assert first == second
    assert first["revision"] == before["revision"] + 1
    assert first["challenger_authority"] == {
        "challenger_id": first_challenger.challenger_id,
        "revision": first_challenger.revision,
    }
    assert first_challenger.origin is CandidateOrigin.PASSIVE_ONLINE
    assert first_challenger.policy is ActivationPolicy.CAUSAL_AUTO
    assert first_challenger.fit_preparation["fit_corpus_digest"] == request.fit_corpus.corpus_digest
    assert first_challenger.candidate.model_digest == preparation.candidate_digest
    assert first_challenger.candidate.candidate_generation == request.candidate_generation
    assert third == fourth
    assert third["revision"] == first["revision"] + 1
    assert third["challenger_authority"] == {
        "challenger_id": third_challenger.challenger_id,
        "revision": third_challenger.revision,
    }
    assert third_challenger.fit_preparation["fit_corpus_digest"] == next_request.fit_corpus.corpus_digest
    assert third_challenger.candidate.model_digest == preparation.candidate_digest
    assert third_challenger.candidate.candidate_generation == request.candidate_generation
    harness.runtime.close()
    assert components.estimator.closed
    assert components.controller.closed
    harness.activation.close()


def test_queued_fit_lifecycle_is_memory_only_until_off_path_poll(monkeypatch) -> None:
    instances = []

    class _Learning:
        evaluation_config = EvaluationConfig(required_consecutive_wins=2)

        def __init__(self, **_kwargs) -> None:
            self.request = None
            self.prepared = None
            instances.append(self)

        def start(self) -> None:
            return None

        def observe_completed_frame(self, _observation, *, identifiability):
            assert identifiability == 1.0
            return SimpleNamespace(
                request=self.request,
                completed_forecasts=(),
                history=SimpleNamespace(accepted=True, reasons=()),
                trigger=TriggerDecision(False, ("minimum-samples",), 0.0, 1),
            )

        def register_causal_forecasts(self, *_args, **_kwargs):
            return ()

        def poll_fit_off_path(self, **_kwargs):
            return None

        def evaluate_ready_off_path(self):
            return None

        def _release_prepared(self) -> None:
            self.prepared = None

        def _reset_prepared_evaluation(self) -> None:
            return None

        def close(self) -> None:
            self._release_prepared()

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _Learning,
    )
    control_thread = []
    trace_threads = []

    def append_trace(_records) -> None:
        trace_threads.append(threading.get_ident())
        if threading.get_ident() in control_thread:
            raise AssertionError("trace persistence ran on observe_frame worker")

    harness = _harness(learning_enabled=True, append_trace=append_trace)
    identity = harness.runtime.learning_identity()
    instances[0].request = FitRequest(
        request_id="d" * 64,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        fit_corpus=_corpus("queued-fit"),
        configuration_digest=identity.configuration_digest,
        parent_incumbent_digest=identity.incumbent_digest,
        parent_incumbent_generation=identity.role_generation,
        candidate_generation=identity.candidate_generation,
    )
    errors = []

    def observe() -> None:
        control_thread.append(threading.get_ident())
        try:
            harness.runtime.observe_frame(_frame())
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=observe)
    worker.start()
    worker.join()
    assert errors == []
    assert trace_threads == []

    harness.runtime.poll_learning_off_path()

    assert trace_threads == [threading.get_ident()]
    harness.runtime.close()
    harness.activation.close()


def test_two_reopened_cooks_submit_one_ordered_persistent_corpus_job_off_path(
    tmp_path,
) -> None:
    repository, partition = _reopened_corpus(tmp_path)
    events = []
    probe = _CorpusRepositoryProbe(repository, events=events)
    _CorpusWorker.instances.clear()
    harness = _harness(
        trajectory_repository=probe,
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_CorpusWorker,
    )
    incumbent = harness.activation.active_pair

    harness.runtime.request_corpus_fit(CandidateOrigin.OPERATOR_CALIBRATION)

    assert events == []
    assert not _CorpusWorker.instances or _CorpusWorker.instances[-1].job is None

    harness.runtime.poll_learning_off_path()

    job = _CorpusWorker.instances[-1].job
    assert isinstance(job, GreyFitJob)
    assert tuple(item.segment_id for item in job.corpus.slices) == (
        "segment-a",
        "segment-b",
    )
    assert tuple(segment.segment_id for segment in job.segments) == (
        "segment-a",
        "segment-b",
    )
    assert tuple(segment.cook_id for segment in job.segments) == (
        "cook-segment-a",
        "cook-segment-b",
    )
    assert tuple(job.segments[0].observation_sequences) == (1, 2)
    assert job.segments[0].initial_load == 0.4
    assert tuple(job.segments[0].pre_roll_duration_s) == (20.0,)
    assert tuple(job.segments[0].pre_roll_load) == (0.4,)
    assert tuple(job.segments[0].pre_roll_temperature_c) == (110.0,)
    assert job.segments[0].hold_anchor_c == 110.01
    assert tuple(job.segments[0].scored_duration_s) == (20.0, 20.0)
    assert tuple(job.segments[0].scored_ambient_c) == (24.0, 24.0)
    assert tuple(job.segments[0].scored_temperature_c) == (110.01, 110.02)
    assert tuple(job.segments[0].scored_load) == (0.4, 0.4)
    assert tuple(job.segments[0].calibration_origin) == (False, False)
    assert job.segments[0].prefix_digest == job.corpus.slices[0].prefix_digest
    assert tuple(job.segments[1].calibration_origin) == (False, True)
    assert job.request.configuration_digest == harness.runtime.learning_identity().configuration_digest
    assert job.request.configuration_digest != job.corpus.fit_partition_digest
    assert harness.activation.active_pair is incumbent
    assert harness.activation.rollback_pair is None
    harness.runtime.close()
    harness.activation.close()


def test_fit_lifecycle_evidence_ids_distinguish_same_millisecond_transitions(
    tmp_path,
) -> None:
    repository, partition = _reopened_corpus(tmp_path)
    trace = []
    _DeliveringCorpusWorker.instances.clear()
    harness = _harness(
        trajectory_repository=repository,
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_DeliveringCorpusWorker,
        append_trace=trace.extend,
    )
    harness.runtime._clock_ms = lambda: 1234

    assert harness.runtime.request_corpus_fit(CandidateOrigin.OPERATOR_CALIBRATION)
    harness.runtime.poll_learning_off_path()
    delivery, _evaluation = harness.runtime.poll_learning_off_path()

    assert delivery is not None
    lifecycle = [record for record in harness.persistence.evidence if record.kind is EvidenceKind.FIT_LIFECYCLE]
    assert [record.payload.status for record in lifecycle] == ["queued", "succeeded"]
    assert len({record.evidence_id for record in lifecycle}) == 2
    assert [record.payload.status for record in trace if record.event_kind is TraceEventKind.FIT_LIFECYCLE] == [
        "queued",
        "succeeded",
    ]
    harness.runtime.close()
    harness.activation.close()


def test_completed_fit_terminalizes_before_challenger_persistence_failure(
    monkeypatch,
    tmp_path,
) -> None:
    repository, partition = _reopened_corpus(tmp_path)
    trace = []
    _DeliveringCorpusWorker.instances.clear()
    harness = _harness(
        trajectory_repository=repository,
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_DeliveringCorpusWorker,
        append_trace=trace.extend,
    )

    def reject_challenger(*_args, **_kwargs):
        raise RuntimeError("durable challenger unavailable")

    monkeypatch.setattr(
        harness.runtime,
        "_persist_durable_challenger",
        reject_challenger,
    )

    assert harness.runtime.request_corpus_fit(CandidateOrigin.OPERATOR_CALIBRATION)
    harness.runtime.poll_learning_off_path()
    delivery, _evaluation = harness.runtime.poll_learning_off_path()

    assert delivery is not None
    request = delivery.message.request
    assert [record.payload.status for record in trace if record.event_kind is TraceEventKind.FIT_LIFECYCLE] == [
        "queued",
        "succeeded",
    ]
    assert harness.runtime._consume_terminal_fit_ticket(
        request.request_id,
        request.origin,
    )
    harness.runtime.close()
    harness.activation.close()


@pytest.mark.parametrize(("enabled", "scheduled"), ((False, False), (True, True)))
def test_passive_mid_cook_corpus_submission_follows_only_online_adaptation(
    tmp_path,
    enabled,
    scheduled,
) -> None:
    repository, partition = _reopened_corpus(tmp_path)
    corpus_before = repository.snapshot_fit_corpus(partition).identity
    probe = _CorpusRepositoryProbe(repository)
    _CorpusWorker.instances.clear()
    harness = _harness(
        trajectory_repository=probe,
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_CorpusWorker,
        learning_enabled=enabled,
    )
    if enabled:
        harness.runtime._learning.trigger_config = TriggerConfig(
            min_samples=1,
            min_input_variance=0.0,
            min_input_levels=1,
            min_temperature_span_c=0.0,
            min_identifiability=0.0,
        )

    harness.runtime.request_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    harness.runtime.poll_learning_off_path()

    submitted = bool(_CorpusWorker.instances and _CorpusWorker.instances[-1].job)
    assert submitted is scheduled
    if scheduled:
        assert not harness.runtime.request_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    assert repository.snapshot_fit_corpus(partition).identity == corpus_before
    harness.runtime.close()
    harness.activation.close()


def test_teardown_ticket_joins_an_already_submitted_passive_fit(
    tmp_path,
) -> None:
    repository, partition = _reopened_ready_passive_corpus(tmp_path)
    _ControlledDeliveryCorpusWorker.instances.clear()
    harness = _harness(
        trajectory_repository=repository,
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_ControlledDeliveryCorpusWorker,
        learning_enabled=True,
    )

    assert harness.runtime.request_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    harness.runtime.poll_learning_off_path()
    pending = harness.runtime._learning.pending_request
    assert pending is not None
    assert not harness.runtime.request_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)

    joined_ticket = harness.runtime._request_corpus_fit_ticket(
        CandidateOrigin.PASSIVE_ONLINE,
        join_pending=True,
    )

    assert joined_ticket == pending.request_id
    _ControlledDeliveryCorpusWorker.instances[-1].released = True
    delivery, _evaluation = harness.runtime.poll_learning_off_path()
    assert delivery is not None
    assert harness.runtime._consume_terminal_fit_ticket(
        joined_ticket,
        CandidateOrigin.PASSIVE_ONLINE,
    )
    harness.runtime.close()
    harness.activation.close()


def test_teardown_queues_followup_when_pending_fit_predates_final_corpus(
    tmp_path,
) -> None:
    repository, partition = _reopened_ready_passive_corpus(tmp_path)
    _ControlledDeliveryCorpusWorker.instances.clear()
    harness = _harness(
        trajectory_repository=repository,
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_ControlledDeliveryCorpusWorker,
        learning_enabled=True,
    )

    assert harness.runtime.request_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    harness.runtime.poll_learning_off_path()
    pending = harness.runtime._learning.pending_request
    assert pending is not None
    _finalize_segment(
        repository,
        _segment("stop-finalized", epoch_ms=5_000_000, scored_count=2),
    )

    followup_ticket = harness.runtime._request_corpus_fit_ticket(
        CandidateOrigin.PASSIVE_ONLINE,
        join_pending=True,
    )

    assert followup_ticket is not None
    assert followup_ticket != pending.request_id
    worker = _ControlledDeliveryCorpusWorker.instances[-1]
    worker.released = True
    first_delivery, _evaluation = harness.runtime.poll_learning_off_path()
    assert first_delivery is not None
    harness.runtime.poll_learning_off_path()
    followup = harness.runtime._learning.pending_request
    assert followup is not None
    assert followup.request_id == followup_ticket
    assert followup.fit_corpus != pending.fit_corpus
    second_delivery, _evaluation = harness.runtime.poll_learning_off_path()
    assert second_delivery is not None
    assert harness.runtime._consume_terminal_fit_ticket(
        followup_ticket,
        CandidateOrigin.PASSIVE_ONLINE,
    )
    harness.runtime.close()
    harness.activation.close()


def test_passive_corpus_fit_terminalizes_not_ready_ticket_without_recording_a_run(
    tmp_path,
) -> None:
    repository, partition = _reopened_corpus(tmp_path)
    probe = _CorpusRepositoryProbe(repository)
    _CorpusWorker.instances.clear()
    harness = _harness(
        trajectory_repository=probe,
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_CorpusWorker,
        learning_enabled=True,
    )

    ticket = harness.runtime._request_corpus_fit_ticket(
        CandidateOrigin.PASSIVE_ONLINE,
    )
    assert ticket is not None
    harness.runtime.poll_learning_off_path()

    assert not _CorpusWorker.instances or _CorpusWorker.instances[-1].job is None
    assert all(event[0] != "record" for event in probe.events)
    assert harness.runtime._consume_terminal_fit_ticket(
        ticket,
        CandidateOrigin.PASSIVE_ONLINE,
    )
    harness.runtime.close()
    harness.activation.close()


def test_unresolved_fit_partition_terminalizes_the_queued_request() -> None:
    repository = _CorpusRepositoryProbe(SimpleNamespace())
    harness = _harness(
        trajectory_repository=repository,
        fit_partition_digest=lambda: None,
        fit_worker_factory=_CorpusWorker,
    )

    assert harness.runtime.request_corpus_fit(CandidateOrigin.OPERATOR_CALIBRATION)
    harness.runtime.poll_learning_off_path()

    failure = harness.runtime.learning_status()["failure"]
    assert failure["code"] == "corpus-snapshot-failed"
    assert "compatible" in failure["detail"]
    assert not harness.runtime._corpus_fit_intents
    harness.runtime.close()
    harness.activation.close()


def test_stale_delivery_terminalizes_the_durable_fit_run_as_stale(tmp_path) -> None:
    repository, partition = _reopened_corpus(tmp_path)
    probe = _CorpusRepositoryProbe(repository)
    _CorpusWorker.instances.clear()
    harness = _harness(
        trajectory_repository=probe,
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_DeliveringCorpusWorker,
    )

    assert harness.runtime.request_corpus_fit(CandidateOrigin.OPERATOR_CALIBRATION)
    harness.runtime.poll_learning_off_path()
    delivery = None
    for _ in range(3):
        delivery, _ = harness.runtime.poll_learning_off_path(
            live_origin=CandidateOrigin.PASSIVE_ONLINE,
        )
        if delivery is not None:
            break

    assert delivery is not None

    assert [event[0] for event in probe.events].count("complete") == 0
    assert [event[0] for event in probe.events].count("stale") == 1
    harness.runtime.close()
    harness.activation.close()


def test_process_owner_candidate_lookup_does_not_deadlock_with_off_path_poll() -> None:
    owner = GreyLearningProcessOwner()
    harness = _harness(
        process_owner=owner,
        learning_enabled=True,
    )
    lease = harness.runtime._process_lease
    assert lease is not None
    operation_entered = threading.Event()
    release_operation = threading.Event()
    lookup_finished = threading.Event()

    def hold_off_path_operation(_learning, _identity):
        operation_entered.set()
        assert release_operation.wait(2.0)

    operation = threading.Thread(
        target=owner.run,
        args=(lease, hold_off_path_operation),
    )
    lookup = threading.Thread(
        target=lambda: (owner.prepared(lease), lookup_finished.set()),
    )
    operation.start()
    assert operation_entered.wait(1.0)
    lookup.start()
    try:
        assert lookup_finished.wait(1.0)
    finally:
        release_operation.set()
        operation.join(timeout=2.0)
        lookup.join(timeout=2.0)
        harness.runtime.close()
        harness.activation.close()
        owner.close()


def test_process_learning_owner_rebinds_cumulative_candidate_to_the_next_cook(
    ds,
    tmp_path,
) -> None:
    repository, partition = _reopened_ready_passive_corpus(tmp_path)
    owner = GreyLearningProcessOwner()
    _CorpusWorker.instances.clear()
    first = _harness(
        trajectory_repository=_CorpusRepositoryProbe(repository),
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_DeliveringCorpusWorker,
        process_owner=owner,
        learning_enabled=True,
    )
    first.runtime.bind_learning_identity("session-a", "cook-a", 0)
    assert first.runtime.request_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    first.runtime.poll_learning_off_path()
    delivery, _ = first.runtime.poll_learning_off_path()
    assert delivery is not None
    prepared = first.runtime._learning.prepared
    assert prepared is not None and prepared.accepted
    candidate_pair = prepared.candidate_pair
    worker = _CorpusWorker.instances[-1]

    first.runtime.close()
    first.activation.close()

    assert owner.learning is not None
    assert owner.learning.prepared is prepared
    assert not worker.closed
    assert candidate_pair is not None
    assert not candidate_pair.controller.closed

    second = _harness(
        trajectory_repository=_CorpusRepositoryProbe(repository),
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_DeliveringCorpusWorker,
        process_owner=owner,
        learning_enabled=True,
    )
    second.runtime.bind_learning_identity("session-b", "cook-b", 0)

    assert second.runtime._learning is owner.learning
    assert second.runtime._learning.prepared is prepared
    assert second.activation.active_pair is second.active
    assert second.activation.rollback_pair is None
    second.runtime.close()
    second.activation.close()
    owner.close()
    assert worker.closed
    assert candidate_pair.controller.closed


def test_compatible_rebind_before_fit_delivery_exposes_candidate_to_current_runtime(
    ds,
    tmp_path,
) -> None:
    repository, partition = _reopened_ready_passive_corpus(tmp_path)
    owner = GreyLearningProcessOwner()
    probe = _CorpusRepositoryProbe(repository)
    _ControlledDeliveryCorpusWorker.instances.clear()
    first = _harness(
        trajectory_repository=probe,
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_ControlledDeliveryCorpusWorker,
        process_owner=owner,
        learning_enabled=True,
    )
    first.runtime.bind_learning_identity("session-a", "cook-a", 0)
    assert first.runtime.request_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    first.runtime.poll_learning_off_path()
    worker = _ControlledDeliveryCorpusWorker.instances[-1]

    second = _harness(
        trajectory_repository=_CorpusRepositoryProbe(repository),
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_ControlledDeliveryCorpusWorker,
        process_owner=owner,
        learning_enabled=True,
    )
    second.runtime.bind_learning_identity("session-b", "cook-b", 0)
    worker.released = True

    delivery, _ = first.runtime.poll_learning_off_path()

    assert delivery is not None
    prepared = owner.learning.prepared
    assert prepared is not None and prepared.accepted
    assert second.runtime._current_learning_candidate_pair() is prepared.candidate_pair
    assert [event[0] for event in probe.events].count("complete") == 1
    assert [event[0] for event in probe.events].count("stale") == 0
    first.runtime.close()
    first.activation.close()
    second.runtime.close()
    second.activation.close()
    owner.close()


def test_incompatible_rebind_before_fit_delivery_terminalizes_stale_without_candidate(
    ds,
    tmp_path,
) -> None:
    repository, partition = _reopened_ready_passive_corpus(tmp_path)
    owner = GreyLearningProcessOwner()
    probe = _CorpusRepositoryProbe(repository)
    _ControlledDeliveryCorpusWorker.instances.clear()
    first = _harness(
        trajectory_repository=probe,
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_ControlledDeliveryCorpusWorker,
        process_owner=owner,
        learning_enabled=True,
    )
    first.runtime.bind_learning_identity("session-a", "cook-a", 0)
    assert first.runtime.request_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    first.runtime.poll_learning_off_path()
    worker = _ControlledDeliveryCorpusWorker.instances[-1]

    second = _harness(
        trajectory_repository=_CorpusRepositoryProbe(repository),
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_ControlledDeliveryCorpusWorker,
        process_owner=owner,
        learning_enabled=True,
    )
    second.runtime.bind_learning_identity("session-b", "cook-b", 1)
    worker.released = True

    delivery, _ = first.runtime.poll_learning_off_path()

    assert delivery is not None
    assert "role-generation-changed" in delivery.stale_reasons
    assert owner.learning.prepared is None
    assert second.runtime._current_learning_candidate_pair() is None
    assert [event[0] for event in probe.events].count("complete") == 0
    assert [event[0] for event in probe.events].count("stale") == 1
    first.runtime.close()
    first.activation.close()
    second.runtime.close()
    second.activation.close()
    owner.close()


def test_rebound_runtime_rejects_duplicate_passive_fit_while_process_fit_is_pending(
    ds,
    tmp_path,
) -> None:
    repository, partition = _reopened_ready_passive_corpus(tmp_path)
    owner = GreyLearningProcessOwner()
    _ControlledDeliveryCorpusWorker.instances.clear()
    first = _harness(
        trajectory_repository=_CorpusRepositoryProbe(repository),
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_ControlledDeliveryCorpusWorker,
        process_owner=owner,
        learning_enabled=True,
    )
    first.runtime.bind_learning_identity("session-a", "cook-a", 0)
    assert first.runtime.request_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    first.runtime.poll_learning_off_path()
    assert owner.learning is not None
    assert owner.learning.pending_request is not None

    second = _harness(
        trajectory_repository=_CorpusRepositoryProbe(repository),
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_ControlledDeliveryCorpusWorker,
        process_owner=owner,
        learning_enabled=True,
    )
    second.runtime.bind_learning_identity("session-b", "cook-b", 0)

    assert not second.runtime.request_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    first.runtime.close()
    first.activation.close()
    second.runtime.close()
    second.activation.close()
    owner.close()


def test_replace_owned_prepared_supersedes_passive_candidate_after_durable_rejection(
    monkeypatch,
    ds,
    tmp_path,
) -> None:
    repository, partition = _reopened_ready_passive_corpus(tmp_path)
    owner = GreyLearningProcessOwner()
    _ControlledDeliveryCorpusWorker.instances.clear()
    first = _harness(
        trajectory_repository=_CorpusRepositoryProbe(repository),
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_ControlledDeliveryCorpusWorker,
        process_owner=owner,
        learning_enabled=True,
    )
    first.runtime.bind_learning_identity("session-a", "cook-a", 0)
    assert first.runtime.request_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    first.runtime.poll_learning_off_path()
    worker = _ControlledDeliveryCorpusWorker.instances[-1]
    worker.released = True
    delivery, _ = first.runtime.poll_learning_off_path()
    assert delivery is not None
    prepared = owner.learning.prepared
    assert prepared is not None and prepared.accepted
    old_pair = prepared.candidate_pair
    assert old_pair is not None

    second = _harness(
        trajectory_repository=_CorpusRepositoryProbe(repository),
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_ControlledDeliveryCorpusWorker,
        process_owner=owner,
    )
    second.runtime.bind_learning_identity("session-b", "cook-b", 0)
    newer_ticket = second.runtime._request_corpus_fit_ticket(
        CandidateOrigin.OPERATOR_CALIBRATION,
        replace_owned_prepared=True,
    )
    assert newer_ticket is not None
    closed_when_persisted = []
    submit_evidence = second.persistence.submit_evidence

    def record_close_state(record):
        closed_when_persisted.append(old_pair.controller.closed)
        return submit_evidence(record)

    monkeypatch.setattr(second.persistence, "submit_evidence", record_close_state)

    assert second.runtime.poll_learning_off_path() == (None, None)

    assert closed_when_persisted == [False]
    assert owner.learning.prepared is None
    assert old_pair.controller.closed
    assert worker.job.request.request_id == newer_ticket
    rejection = second.persistence.evidence[-1].payload
    assert rejection.rejection_reasons == ("superseded-by-newer-cumulative-fit",)
    first.runtime.close()
    first.activation.close()
    second.runtime.close()
    second.activation.close()
    owner.close()
    assert old_pair.controller.closed


def test_replace_owned_prepared_persistence_failure_preserves_old_and_stales_new_fit(
    ds,
    tmp_path,
) -> None:
    repository, partition = _reopened_ready_passive_corpus(tmp_path)
    owner = GreyLearningProcessOwner()
    probe = _CorpusRepositoryProbe(repository)
    _ControlledDeliveryCorpusWorker.instances.clear()
    first = _harness(
        trajectory_repository=probe,
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_ControlledDeliveryCorpusWorker,
        process_owner=owner,
        learning_enabled=True,
    )
    first.runtime.bind_learning_identity("session-a", "cook-a", 0)
    assert first.runtime.request_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    first.runtime.poll_learning_off_path()
    worker = _ControlledDeliveryCorpusWorker.instances[-1]
    worker.released = True
    first.runtime.poll_learning_off_path()
    prepared = owner.learning.prepared
    assert prepared is not None and prepared.accepted
    old_pair = prepared.candidate_pair
    assert old_pair is not None

    second = _harness(
        trajectory_repository=probe,
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_ControlledDeliveryCorpusWorker,
        process_owner=owner,
    )
    second.runtime.bind_learning_identity("session-b", "cook-b", 0)
    newer_ticket = second.runtime._request_corpus_fit_ticket(
        CandidateOrigin.OPERATOR_CALIBRATION,
        replace_owned_prepared=True,
    )
    assert newer_ticket is not None
    second.persistence.accept_evidence = False

    assert second.runtime.poll_learning_off_path() == (None, None)
    assert owner.learning.prepared is prepared
    assert not old_pair.controller.closed
    assert owner.learning.pending_request.request_id == newer_ticket

    second.persistence.accept_evidence = True
    delivery, _ = second.runtime.poll_learning_off_path()
    assert delivery.message.request.request_id == newer_ticket
    assert delivery.stale_reasons == ("candidate-supersession-persistence-failed",)
    assert owner.learning.prepared is prepared
    assert not old_pair.controller.closed
    assert second.runtime._corpus_fit_failure[0] == "candidate-supersession-persistence-failed"
    assert [event[0] for event in probe.events].count("stale") == 1
    assert second.runtime._consume_terminal_fit_ticket(
        newer_ticket,
        CandidateOrigin.OPERATOR_CALIBRATION,
    )
    first.runtime.close()
    first.activation.close()
    second.runtime.close()
    second.activation.close()
    owner.close()
    assert old_pair.controller.closed


def test_replace_owned_prepared_protects_operator_calibration_candidate(
    ds,
    tmp_path,
) -> None:
    repository, partition = _reopened_ready_passive_corpus(tmp_path)
    owner = GreyLearningProcessOwner()
    _ControlledDeliveryCorpusWorker.instances.clear()
    first = _harness(
        trajectory_repository=_CorpusRepositoryProbe(repository),
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_ControlledDeliveryCorpusWorker,
        process_owner=owner,
        learning_enabled=True,
    )
    first.runtime.bind_learning_identity("session-a", "cook-a", 0)
    preparation, _evaluation, components = _operator_candidate(first)
    durable = _seed_durable_challenger(
        first,
        preparation,
        phase="evaluating",
    )
    assert owner.learning is not None
    owner.learning.restore_persisted_challenger(
        preparation,
        evaluation_epoch=0,
        consecutive_wins=0,
    )
    first.runtime.close()
    first.activation.close()

    second = _harness(
        trajectory_repository=_CorpusRepositoryProbe(repository),
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_ControlledDeliveryCorpusWorker,
        process_owner=owner,
        learning_enabled=True,
    )
    second.runtime.bind_learning_identity("session-b", "cook-b", 0)
    ticket = second.runtime._request_corpus_fit_ticket(
        CandidateOrigin.OPERATOR_CALIBRATION,
        replace_owned_prepared=True,
    )
    assert ticket is not None

    assert second.runtime.poll_learning_off_path() == (None, None)
    assert owner.learning.prepared is preparation
    assert not components.estimator.closed
    assert not components.controller.closed
    assert _ControlledDeliveryCorpusWorker.instances[-1].job is None
    rejection = second.persistence.evidence[-1].payload
    assert rejection.rejection_reasons == ("superseded-by-prepared-operator-calibration-candidate",)
    assert read_model_challenger() == durable
    assert second.runtime._consume_terminal_fit_ticket(
        ticket,
        CandidateOrigin.OPERATOR_CALIBRATION,
    )
    second.runtime.close()
    second.activation.close()
    owner.close()
    assert components.estimator.closed
    assert components.controller.closed


def test_explicit_calibration_schedules_from_the_corpus_when_passive_fits_are_disabled(
    tmp_path,
) -> None:
    repository, partition = _reopened_corpus(tmp_path)
    _CorpusWorker.instances.clear()
    harness = _harness(
        trajectory_repository=_CorpusRepositoryProbe(repository),
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_CorpusWorker,
        learning_enabled=False,
    )

    harness.runtime.request_corpus_fit(CandidateOrigin.OPERATOR_CALIBRATION)
    harness.runtime.poll_learning_off_path()

    job = _CorpusWorker.instances[-1].job
    assert isinstance(job, GreyFitJob)
    assert job.request.origin is CandidateOrigin.OPERATOR_CALIBRATION
    assert harness.activation.active_pair is harness.active
    assert harness.activation.rollback_pair is None
    harness.runtime.close()
    harness.activation.close()


def test_incomplete_calibration_manifest_is_a_qualification_blocker(
    ds,
    tmp_path,
) -> None:
    repository, partition = _reopened_corpus(tmp_path)
    _CorpusWorker.instances.clear()
    harness = _harness(
        trajectory_repository=_CorpusRepositoryProbe(repository),
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_DeliveringCorpusWorker,
        learning_enabled=False,
    )

    harness.runtime.request_corpus_fit(CandidateOrigin.OPERATOR_CALIBRATION)
    harness.runtime.poll_learning_off_path()

    job = _CorpusWorker.instances[-1].job
    assert isinstance(job, GreyFitJob)
    assert harness.runtime._calibration_manifest_for_corpus(job.corpus) is None
    delivery = None
    for _ in range(3):
        delivery, _ = harness.runtime.poll_learning_off_path(
            live_origin=CandidateOrigin.OPERATOR_CALIBRATION,
        )
        if delivery is not None:
            break
    assert delivery is not None
    assert delivery.preparation.candidate_pair.controller.closed
    durable = read_model_challenger()
    assert durable is not None
    assert durable.phase == "retired"
    assert durable.retirement_reason == "calibration-manifest"
    assert harness.runtime.learning_status()["failure"] is None
    assert harness.runtime._learning is not None
    assert harness.runtime._learning.prepared is None
    harness.runtime.close()
    harness.activation.close()


def test_calibration_manifest_binds_one_complete_run_from_the_fit_corpus(
    tmp_path,
) -> None:
    repository, partition = _reopened_corpus(tmp_path)
    _CorpusWorker.instances.clear()
    harness = _harness(
        trajectory_repository=_CorpusRepositoryProbe(repository),
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_CorpusWorker,
        learning_enabled=False,
    )
    harness.runtime.request_corpus_fit(CandidateOrigin.OPERATOR_CALIBRATION)
    harness.runtime.poll_learning_off_path()
    job = _CorpusWorker.instances[-1].job
    assert isinstance(job, GreyFitJob)

    segment = repository.read_segment("segment-b")
    assert segment is not None
    frame = next(item for item in segment.scored_hold_frames if item.calibration_origin)
    stage_order = ("low", "middle", "high", "coast")

    def allocation(load: float) -> AllocationEvidence:
        return AllocationEvidence(
            normalized_combustion_load=load,
            auger_duty=load,
            fan_duty=None,
            u_max=1.0,
            fan_min_pct=0.0,
            fan_max_pct=100.0,
            fan_enabled=False,
            auger_clamp_reason=AllocationClampReason.NONE,
            fan_clamp_reason=AllocationClampReason.NONE,
            allocator_revision=2,
        )

    records = []
    for index, stage in enumerate(stage_order):
        probe_q = 0.0 if stage == "coast" else 0.1
        combined_q = 0.3 + probe_q
        records.append(
            ModelEvidenceRecord(
                evidence_id=f"calibration-{stage}",
                kind=EvidenceKind.CALIBRATION_SUMMARY,
                session_id=segment.trajectory_session_id,
                cook_id=segment.cook_id,
                timestamp_ms=frame.monotonic_start_ms + index,
                role_generation=4,
                model_digest="a" * 64,
                provenance_digest="b" * 64,
                payload=CalibrationSummaryEvidence(
                    accepted=True,
                    probe_count=0 if stage == "coast" else 1,
                    result_revision=index + 1,
                    command_revision=17,
                    command_action="start",
                    baseline_q=0.3,
                    probe_q=probe_q,
                    combined_q=combined_q,
                    baseline_allocation=allocation(0.3),
                    combined_allocation=allocation(combined_q),
                    scheduled_on_seconds=6.0,
                    delivered_on_seconds=6.0,
                    status="active",
                    stage=stage,
                    completed_stages=stage_order[:index],
                    continuous=True,
                ),
            )
        )
    first = records[0]
    newer_incomplete_run = ModelEvidenceRecord(
        evidence_id="calibration-newer-low",
        kind=first.kind,
        session_id=first.session_id,
        cook_id=first.cook_id,
        timestamp_ms=frame.monotonic_start_ms + len(stage_order),
        role_generation=first.role_generation,
        model_digest=first.model_digest,
        provenance_digest=first.provenance_digest,
        payload=replace(
            first.payload,
            result_revision=len(stage_order) + 1,
            command_revision=18,
        ),
    )
    append_model_evidence(
        [*records, newer_incomplete_run],
        database_path=repository._database_path,
    )

    assert harness.runtime._calibration_manifest_for_corpus(job.corpus) == {
        "command_revision": 17,
        "session_id": segment.trajectory_session_id,
        "completed_stages": list(stage_order),
        "stage_evidence_ids": [record.evidence_id for record in records],
    }
    harness.runtime.close()
    harness.activation.close()


def test_incompatible_partition_is_excluded_without_touching_model_authority(
    tmp_path,
) -> None:
    repository, partition = _reopened_corpus(tmp_path, include_incompatible=True)
    _CorpusWorker.instances.clear()
    harness = _harness(
        trajectory_repository=_CorpusRepositoryProbe(repository),
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_CorpusWorker,
    )
    incumbent = harness.activation.active_pair

    harness.runtime.request_corpus_fit(CandidateOrigin.OPERATOR_CALIBRATION)
    harness.runtime.poll_learning_off_path()

    job = _CorpusWorker.instances[-1].job
    assert tuple(segment.segment_id for segment in job.segments) == (
        "segment-a",
        "segment-b",
    )
    assert harness.activation.active_pair is incumbent
    assert incumbent.authorized
    assert harness.activation.rollback_pair is None
    harness.runtime.close()
    harness.activation.close()


@pytest.mark.parametrize(
    ("failure_stage", "failure_code"),
    (
        ("snapshot", "corpus-snapshot-failed"),
        ("record", "fit-run-persistence-failed"),
        ("submission", "fit-submission-failed"),
    ),
)
def test_corpus_failures_disable_learning_without_changing_control_authority(
    tmp_path,
    failure_stage,
    failure_code,
) -> None:
    repository, partition = _reopened_corpus(tmp_path)
    probe = _CorpusRepositoryProbe(
        repository,
        snapshot_error=(RuntimeError("corpus corrupt") if failure_stage == "snapshot" else None),
        record_error=(RuntimeError("fit lineage unavailable") if failure_stage == "record" else None),
    )
    worker_factory = _SubmissionFailureWorker if failure_stage == "submission" else _CorpusWorker
    _CorpusWorker.instances.clear()
    harness = _harness(
        trajectory_repository=probe,
        fit_partition_digest=lambda: partition,
        fit_worker_factory=worker_factory,
    )
    incumbent = harness.activation.active_pair
    rollback = harness.activation.rollback_pair

    harness.runtime.request_corpus_fit(CandidateOrigin.OPERATOR_CALIBRATION)
    harness.runtime.poll_learning_off_path()

    failure = harness.runtime.learning_status()["failure"]
    assert failure["code"] == failure_code
    assert harness.activation.active_pair is incumbent
    assert harness.activation.rollback_pair is rollback
    assert incumbent.authorized
    assert not incumbent.closed
    harness.runtime.close()
    harness.activation.close()


def test_promotion_and_rollback_preserve_the_compatible_fit_corpus(ds, tmp_path) -> None:
    repository, partition = _reopened_corpus(tmp_path)
    harness = _harness(
        trajectory_repository=_CorpusRepositoryProbe(repository),
        fit_partition_digest=lambda: partition,
        fit_worker_factory=_CorpusWorker,
    )
    before = repository.snapshot_fit_corpus(partition).identity
    identity_before = harness.runtime.learning_identity()
    incumbent = harness.activation.active_pair
    harness.activation.bind_estimator_seed_source(
        lambda theta, n_delay: EstimatorSeed(
            delay_states=(0.4,) * n_delay,
            chamber_temperature_c=110.0,
            disturbance=0.0,
            segment_id="segment-rollback",
            pre_roll_digest="c" * 64,
            pre_roll_frame_count=ceil(3 * theta / 20.0),
            required_frame_count=ceil(3 * theta / 20.0),
            status="exact",
        )
    )
    preparation, evaluation, _components = _operator_candidate(
        harness,
        fit_corpus=before,
    )
    _seed_durable_challenger(
        harness,
        preparation,
        phase="qualified",
        decision_id=evaluation.decision_id,
    )
    transaction_id = harness.runtime.prepare_automatic_activation(
        preparation,
        ActivationPolicy.CAUSAL_AUTO,
        evaluation,
    )
    assert transaction_id
    assert harness.activation.advance_activation() is False
    assert harness.activation.advance_activation() is True

    harness.runtime.sync_activation_generation()
    candidate = harness.activation.active_pair
    promoted_identity = harness.runtime.learning_identity()
    assert candidate.descriptor.model_digest == preparation.candidate_digest
    assert harness.activation.rollback_pair is incumbent
    assert repository.snapshot_fit_corpus(partition).identity == before

    assert promoted_identity.incumbent_digest == candidate.descriptor.model_digest
    assert promoted_identity.role_generation != identity_before.role_generation
    assert harness.activation.rollback_activation("operator rollback")
    harness.runtime.sync_activation_generation()
    rolled_back_identity = harness.runtime.learning_identity()

    assert harness.activation.active_pair is incumbent
    assert repository.snapshot_fit_corpus(partition).identity == before
    assert rolled_back_identity.incumbent_digest == incumbent.descriptor.model_digest
    assert rolled_back_identity.role_generation != promoted_identity.role_generation
    harness.runtime.close()
    harness.activation.close()


@pytest.mark.parametrize(
    ("preparation", "policy", "message"),
    (
        (SimpleNamespace(), object(), "causal-auto"),
        (
            SimpleNamespace(),
            ActivationPolicy.CAUSAL_AUTO,
            "candidate preparation is incomplete",
        ),
    ),
)
def test_automatic_activation_rejects_wrong_policy_or_incomplete_preparation(
    preparation,
    policy,
    message,
) -> None:
    harness = _harness()

    with pytest.raises(ValueError, match=message):
        harness.runtime.prepare_automatic_activation(preparation, policy)

    assert harness.activation.active_pair is harness.active
    harness.runtime.close()
    harness.activation.close()


def test_orchestrator_start_failure_closes_partial_learning_owner(monkeypatch) -> None:
    instances = []

    class _FailingLearning:
        def __init__(self, **_kwargs) -> None:
            self.closed = False
            instances.append(self)

        def start(self) -> None:
            raise RuntimeError("start failed")

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _FailingLearning,
    )

    with pytest.raises(RuntimeError, match="start failed"):
        _harness(learning_enabled=True)

    assert instances[0].closed


def test_rejected_combined_confidence_submission_does_not_trace_assessment() -> None:
    traces = []
    harness = _harness(append_trace=lambda records: traces.extend(records))
    preparation, evaluation, components = _automatic_candidate(harness)
    harness.persistence.accept_confidence = False

    with pytest.raises(RuntimeError, match="activation-confidence-not-durable"):
        harness.runtime._persist_candidate_evaluation(evaluation, preparation)

    assert traces == []
    _close_prepared_candidate(preparation)
    assert components.estimator.closed
    assert components.controller.closed
    harness.runtime.close()
    harness.activation.close()


def test_rejected_evaluation_persists_causal_blocker_and_projects_once(
    monkeypatch,
    ds,
) -> None:
    evaluation = EvaluationDecision(
        decision_id="c" * 64,
        accepted=False,
        role_generation=0,
        candidate_generation=1,
        incumbent_digest=_descriptor().model_digest,
        challenger_digest="d" * 64,
        scores=_COMPLETE_SCORES,
        consecutive_wins=0,
        blockers=("candidate-confidence-low",),
    )
    instances = []

    class _Learning:
        prepared = None
        evaluation_config = EvaluationConfig(required_consecutive_wins=2)

        def __init__(self, **_kwargs) -> None:
            self.closed = False
            instances.append(self)

        def start(self) -> None:
            return None

        def poll_fit_off_path(self, **_kwargs):
            return None

        def evaluate_ready_off_path(self):
            return evaluation

        def observe_completed_frame(self, _frame, *, identifiability):
            assert identifiability == 1.0
            return SimpleNamespace(
                history=SimpleNamespace(accepted=True, reasons=()),
                completed_forecasts=(),
                request=None,
                trigger=TriggerDecision(False, ("minimum-samples",), 0.125, 3),
            )

        def register_causal_forecasts(self, *_args, **_kwargs):
            return ()

        def update_identity(self, *_args, **_kwargs) -> None:
            self._release_prepared()
            self._reset_prepared_evaluation()

        def _release_prepared(self) -> None:
            prepared = self.prepared
            self.prepared = None
            if prepared is not None and prepared.accepted:
                _close_prepared_candidate(prepared)

        def _reset_prepared_evaluation(self) -> None:
            return None

        def retire_evaluated_candidate(self, _evaluation) -> bool:
            self._release_prepared()
            self._reset_prepared_evaluation()
            return True

        def close(self) -> None:
            self._release_prepared()
            self.closed = True

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _Learning,
    )
    harness = _harness(learning_enabled=True)
    preparation, candidate_evaluation, components = _automatic_candidate(harness)
    instances[0].prepared = preparation
    evaluation = replace(
        evaluation,
        role_generation=candidate_evaluation.role_generation,
        candidate_generation=candidate_evaluation.candidate_generation,
        incumbent_digest=candidate_evaluation.incumbent_digest,
        challenger_digest=candidate_evaluation.challenger_digest,
        completed_origins=candidate_evaluation.completed_origins,
    )
    _seed_durable_challenger(
        harness,
        preparation,
        phase="evaluating",
    )

    delivery, projected = harness.runtime.poll_learning_off_path()
    outcome = harness.runtime.observe_frame(_frame())

    assert delivery is None
    assert projected is not None
    assert outcome["evaluation_payload"] == projected
    assert outcome["confidence_accepted"] is False
    assert outcome["input_variance"] == 0.125
    assert outcome["input_levels"] == 3
    assert harness.persistence.confidence_preceding[-1][0].schema_version == 4
    assessment = harness.persistence.confidence_preceding[-1][0].payload
    assert assessment.rejection_reasons == ("candidate-confidence-low",)
    assert components.estimator.closed
    assert components.controller.closed
    assert harness.persistence.confidence[-1].payload.reason == "candidate-confidence-low"
    assert harness.runtime.observation_failure(_frame(), RuntimeError("boom"))["rejection_reasons"] == (
        "learner-exception",
    )
    evaluation = replace(evaluation, decision_id="d" * 64)
    assert harness.runtime.poll_learning_off_path() == (None, None)
    assert harness.runtime._corpus_fit_failure is None
    harness.runtime.bind_learning_identity("next", "cook", 1)
    harness.runtime.close()
    harness.activation.close()


def test_candidate_assessment_uses_activation_fifo_when_unrelated_evidence_is_rejected(
    monkeypatch,
    ds,
) -> None:
    evaluation = EvaluationDecision(
        decision_id="e" * 64,
        accepted=False,
        role_generation=0,
        candidate_generation=1,
        incumbent_digest=_descriptor().model_digest,
        challenger_digest="f" * 64,
        scores=_COMPLETE_SCORES,
        consecutive_wins=0,
        blockers=("stale-session",),
    )
    instances = []

    class _Learning:
        prepared = None
        evaluation_config = EvaluationConfig(required_consecutive_wins=2)

        def __init__(self, **_kwargs) -> None:
            instances.append(self)

        def start(self) -> None:
            return None

        def poll_fit_off_path(self, **_kwargs):
            return None

        def evaluate_ready_off_path(self):
            return evaluation

        def _release_prepared(self) -> None:
            prepared = self.prepared
            self.prepared = None
            if prepared is not None and prepared.accepted:
                _close_prepared_candidate(prepared)

        def _reset_prepared_evaluation(self) -> None:
            return None

        def retire_evaluated_candidate(self, _evaluation) -> bool:
            self._release_prepared()
            self._reset_prepared_evaluation()
            return True

        def close(self) -> None:
            self._release_prepared()

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _Learning,
    )
    harness = _harness(learning_enabled=True)
    preparation, candidate_evaluation, components = _automatic_candidate(harness)
    instances[0].prepared = preparation
    evaluation = replace(
        evaluation,
        role_generation=candidate_evaluation.role_generation,
        candidate_generation=candidate_evaluation.candidate_generation,
        incumbent_digest=candidate_evaluation.incumbent_digest,
        challenger_digest=candidate_evaluation.challenger_digest,
        completed_origins=candidate_evaluation.completed_origins,
    )
    _seed_durable_challenger(
        harness,
        preparation,
        phase="evaluating",
    )
    harness.persistence.accept_evidence = False

    harness.runtime.poll_learning_off_path()

    assert harness.persistence.evidence == []
    assert len(harness.persistence.confidence) == 1
    assert len(harness.persistence.confidence_preceding) == 1
    assert harness.persistence.confidence_preceding[0][0].schema_version == 4
    assert harness.persistence.confidence_preceding[0][0].payload.decision_id == evaluation.decision_id
    assert components.estimator.closed
    assert components.controller.closed
    assert harness.activation.active_pair is harness.active
    harness.runtime.close()
    harness.activation.close()


def test_successful_poll_hands_off_once_and_deduplicates_confidence(
    monkeypatch,
    ds,
) -> None:
    instances = []

    class _Learning:
        prepared = None
        evaluation_config = EvaluationConfig(required_consecutive_wins=2)

        def __init__(self, **_kwargs) -> None:
            self.evaluation = None
            self.handoffs = []
            instances.append(self)

        def start(self) -> None:
            return None

        def poll_fit_off_path(self, **_kwargs):
            return None

        def evaluate_ready_off_path(self):
            return self.evaluation

        def handoff_if_ready(self, **kwargs) -> None:
            self.handoffs.append(kwargs)

        def _release_prepared(self) -> None:
            prepared = self.prepared
            self.prepared = None
            if prepared is not None and prepared.accepted:
                _close_prepared_candidate(prepared)

        def _reset_prepared_evaluation(self) -> None:
            self.evaluation = None

        def close(self) -> None:
            self._release_prepared()

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _Learning,
    )
    harness = _harness(learning_enabled=True)
    preparation, evaluation, components = _automatic_candidate(harness)
    instances[0].prepared = preparation
    instances[0].evaluation = evaluation
    _seed_durable_challenger(
        harness,
        preparation,
        phase="evaluating",
        wins=1,
    )

    first = harness.runtime.poll_learning_off_path()
    second = harness.runtime.poll_learning_off_path()

    assert first[1] is not None
    assert second[1].decision_id == first[1].decision_id
    assert len(instances[0].handoffs) == 2
    assert len(harness.persistence.confidence) == 1
    assert harness.activation.active_pair is harness.active
    harness.runtime.close()
    assert components.estimator.closed
    assert components.controller.closed
    harness.activation.close()


def test_trace_projection_failure_terminates_activation_without_losing_evidence(
    monkeypatch,
    ds,
) -> None:
    instances = []

    class _Learning:
        prepared = None
        evaluation_config = EvaluationConfig(required_consecutive_wins=2)

        def __init__(self, **_kwargs) -> None:
            self.evaluation = None
            instances.append(self)

        def start(self) -> None:
            return None

        def poll_fit_off_path(self, **_kwargs):
            return None

        def evaluate_ready_off_path(self):
            return self.evaluation

        def handoff_if_ready(self, **_kwargs) -> None:
            return None

        def _release_prepared(self) -> None:
            prepared = self.prepared
            self.prepared = None
            if prepared is not None and prepared.accepted:
                _close_prepared_candidate(prepared)

        def _reset_prepared_evaluation(self) -> None:
            self.evaluation = None

        def close(self) -> None:
            self._release_prepared()

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _Learning,
    )

    def fail_trace(records):
        assert {record.schema_version for record in records} == {8}
        raise RuntimeError("trace unavailable")

    harness = _harness(learning_enabled=True, append_trace=fail_trace)
    preparation, evaluation, components = _automatic_candidate(harness)
    instances[0].prepared = preparation
    instances[0].evaluation = evaluation
    _seed_durable_challenger(
        harness,
        preparation,
        phase="evaluating",
    )

    harness.runtime.poll_learning_off_path()

    assert harness.persistence.evidence == []
    assert len(harness.persistence.confidence_preceding) == 1
    assert len(harness.persistence.confidence_preceding[0]) == 1
    assert harness.persistence.confidence_preceding[0][0].payload.decision_id == evaluation.decision_id
    assert harness.activation.terminated_reason == ("learning lifecycle trace failed: trace unavailable")
    harness.runtime.close()
    assert components.estimator.closed
    assert components.controller.closed
    harness.activation.close()


def test_operator_two_complete_wins_prepare_durable_causal_auto_activation(
    monkeypatch,
    ds,
) -> None:

    instances = []

    class _Learning:
        evaluation_config = EvaluationConfig(required_consecutive_wins=2)

        def __init__(self, **_kwargs) -> None:
            self.prepared = None
            self.evaluations = []
            self.last_evaluation = None
            self.handoffs = []
            self._ownership_transferred = False
            instances.append(self)

        def start(self) -> None:
            return None

        def poll_fit_off_path(self, **_kwargs):
            return None

        def evaluate_ready_off_path(self):
            self.last_evaluation = self.evaluations.pop(0) if self.evaluations else None
            return self.last_evaluation

        def handoff_if_ready(
            self,
            *,
            confidence_accepted,
            online_enabled,
            prepare,
        ):
            outcome = handoff_candidate(
                self.prepared,
                evaluation=self.last_evaluation,
                confidence_accepted=confidence_accepted,
                online_enabled=online_enabled,
                prepare=prepare,
                install=lambda _pair: pytest.fail("the fit pipeline must never install a candidate"),
            )
            self.handoffs.append(outcome)
            self._ownership_transferred = not outcome.blockers
            return outcome

        def _release_prepared(self) -> None:
            prepared = self.prepared
            self.prepared = None
            if prepared is not None and prepared.accepted and not self._ownership_transferred:
                _close_prepared_candidate(prepared)

        def _reset_prepared_evaluation(self) -> None:
            self.last_evaluation = None

        def close(self) -> None:
            self._release_prepared()

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _Learning,
    )
    store = _CheckpointStore()
    harness = _harness(
        learning_enabled=False,
        checkpoint_store=store,
        trajectory_repository=SimpleNamespace(),
        fit_partition_digest=lambda: "f" * 64,
    )
    preparation, winning, components = _operator_candidate(harness)
    first = replace(
        winning,
        decision_id="1" * 64,
        accepted=False,
        consecutive_wins=1,
    )
    second = replace(
        winning,
        decision_id="2" * 64,
        accepted=True,
        consecutive_wins=2,
    )
    instances[0].prepared = preparation
    instances[0].evaluations = [first, second]
    _seed_durable_challenger(
        harness,
        preparation,
        phase="evaluating",
    )
    incumbent = harness.activation.active_pair

    harness.runtime.poll_learning_off_path()

    after_first = read_model_challenger()
    assert after_first is not None
    assert after_first.phase == "evaluating"
    assert after_first.evaluation_round == 1
    assert after_first.consecutive_wins == 1
    assert not harness.activation.activation_pending
    assert harness.activation.active_pair is incumbent
    assert incumbent.authorized

    harness.runtime.poll_learning_off_path()

    activating = read_model_challenger()
    assert activating is not None
    assert activating.phase == "activating"
    assert activating.evaluation_round == 2
    assert activating.consecutive_wins == 2
    assert activating.activation_transaction_id is not None
    assert activating.origin is CandidateOrigin.OPERATOR_CALIBRATION
    assert activating.policy is ActivationPolicy.CAUSAL_AUTO
    assert harness.activation.activation_pending
    prepared = read_model_activation()
    assert prepared is not None
    assert prepared.phase == "prepared"
    assert prepared.origin == CandidateOrigin.OPERATOR_CALIBRATION.value
    assert prepared.policy == ActivationPolicy.CAUSAL_AUTO.value
    assert harness.activation.active_pair is incumbent
    assert incumbent.authorized
    assert not incumbent.closed
    assert len(instances[0].handoffs) == 1
    assert store.snapshots == []
    assert harness.runtime.learning_status()["status"] == "activating"
    harness.runtime.close()
    harness.activation.close()
    assert components.estimator.closed
    assert components.controller.closed


def test_learning_status_projects_queued_running_preparing_and_handoff_states(
    monkeypatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    instances = []

    class _Learning:
        evaluation_config = EvaluationConfig(required_consecutive_wins=2)

        def __init__(self, **_kwargs) -> None:
            self.pending_request = None
            self.worker = SimpleNamespace(busy=False)
            self.prepared = None
            self.handoff = None
            instances.append(self)

        def start(self) -> None:
            return None

        def poll_fit_off_path(self, **_kwargs):
            entered.set()
            assert release.wait(2.0)

        def evaluate_ready_off_path(self):
            return None

        def _release_prepared(self) -> None:
            self.prepared = None

        def _reset_prepared_evaluation(self) -> None:
            self.handoff = None

        def close(self) -> None:
            self._release_prepared()

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _Learning,
    )
    harness = _harness(learning_enabled=True)
    learning = instances[0]
    identity = harness.runtime.learning_identity()
    learning.pending_request = FitRequest(
        request_id="q" * 64,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        fit_corpus=_corpus("queued-status"),
        configuration_digest=identity.configuration_digest,
        parent_incumbent_digest=identity.incumbent_digest,
        parent_incumbent_generation=identity.role_generation,
        candidate_generation=identity.candidate_generation,
    )

    assert harness.runtime.learning_status()["fit_status"] == "queued"
    learning.worker.busy = True
    assert harness.runtime.learning_status()["fit_status"] == "running"

    learning.pending_request = None
    polling = threading.Thread(
        target=harness.runtime.poll_learning_off_path,
        kwargs={"live_origin": CandidateOrigin.PASSIVE_ONLINE},
    )
    polling.start()
    assert entered.wait(2.0)
    assert harness.runtime.learning_status()["fit_status"] == "running"
    release.set()
    polling.join(2.0)
    assert not polling.is_alive()

    learning.handoff = SimpleNamespace(status=LearningStatus.ACTIVE)
    assert harness.runtime.learning_status()["status"] == "active"
    harness.runtime.close()
    harness.activation.close()


def test_observation_rejects_invalid_public_input_without_owner_change() -> None:
    harness = _harness()
    incumbent = harness.activation.active_pair

    with pytest.raises(TypeError, match="FrameObservation"):
        harness.runtime.observe_frame(SimpleNamespace())

    assert harness.activation.active_pair is incumbent
    assert incumbent.authorized
    harness.runtime.close()
    harness.activation.close()


@pytest.mark.parametrize("estimator_kind", ("ekf", "kf"))
def test_automatic_activation_prepares_one_inert_owner_without_installing_output(
    estimator_kind,
    ds,
) -> None:
    harness = _harness(estimator_kind=estimator_kind)
    incumbent = harness.activation.active_pair
    preparation, evaluation, components = _automatic_candidate(harness)
    _seed_durable_challenger(
        harness,
        preparation,
        phase="qualified",
        decision_id=evaluation.decision_id,
    )

    transaction_id = harness.runtime.prepare_automatic_activation(
        preparation,
        ActivationPolicy.CAUSAL_AUTO,
        evaluation,
    )

    assert transaction_id
    assert harness.activation.active_pair is incumbent
    assert incumbent.authorized
    assert harness.activation.activation_pending
    assert not components.estimator.closed
    assert not components.controller.closed
    assert harness.runtime.learning_status()["status"] == "activating"
    harness.runtime.close()
    harness.activation.close()
    assert components.controller.closed
    assert components.estimator.closed


def test_automatic_activation_reuses_already_durable_confidence_decision(ds) -> None:
    harness = _harness()
    preparation, evaluation, components = _automatic_candidate(harness)
    _seed_durable_challenger(
        harness,
        preparation,
        phase="qualified",
        decision_id=evaluation.decision_id,
    )
    harness.activation.mark_confidence_persisted(evaluation.decision_id)

    transaction_id = harness.runtime.prepare_automatic_activation(
        preparation,
        ActivationPolicy.CAUSAL_AUTO,
        evaluation,
    )

    assert transaction_id
    assert harness.persistence.confidence == []
    assert harness.activation.activation_pending
    harness.runtime.close()
    harness.activation.close()
    assert components.estimator.closed
    assert components.controller.closed


@pytest.mark.parametrize(
    ("failure", "message"),
    (
        ("digest", "candidate-digest-changed"),
        ("evaluation", "activation-confidence-changed"),
        ("missing-evaluation", "activation-confidence-changed"),
        ("confidence", "activation-confidence-not-durable"),
        ("phase", "activation-persistence-not-durable"),
    ),
)
def test_automatic_activation_failure_closes_transferred_candidate_components(
    monkeypatch,
    failure,
    message,
    ds,
) -> None:
    harness = _harness()
    preparation, evaluation, components = _automatic_candidate(harness)
    _seed_durable_challenger(
        harness,
        preparation,
        phase="qualified",
        decision_id=evaluation.decision_id,
    )
    if failure == "digest":
        preparation.candidate_digest = "f" * 64
    elif failure == "evaluation":
        evaluation.accepted = False
    elif failure == "missing-evaluation":
        evaluation = None
    elif failure == "confidence":
        harness.persistence.accept_confidence = False
    else:

        def fail_prepared_activation(**_kwargs):
            raise RuntimeError("activation-persistence-unavailable")

        monkeypatch.setattr(
            "controller.model_learning.grey_runtime.prepare_model_challenger_activation",
            fail_prepared_activation,
        )

    with pytest.raises((RuntimeError, ValueError), match=message):
        harness.runtime.prepare_automatic_activation(
            preparation,
            ActivationPolicy.CAUSAL_AUTO,
            evaluation,
        )

    assert components.estimator.closed
    assert components.controller.closed
    assert harness.activation.active_pair is harness.active
    assert harness.active.authorized
    harness.runtime.close()
    harness.activation.close()


def test_real_orchestrator_detaches_raw_owner_after_queued_lifecycle_abort(
    tmp_path,
    ds,
) -> None:
    harness = _harness()
    repository, partition = _reopened_corpus(tmp_path)
    snapshot = repository.snapshot_fit_corpus(partition)
    active_descriptor = harness.activation.active_pair.descriptor

    class _CountingEstimator:
        def __init__(self, _native_config) -> None:
            self.close_count = 0

        def update(self, _load, temperature):
            return [0.0] * 8 + [float(temperature), 0.0]

        def close(self) -> None:
            self.close_count += 1

    class _CountingSolver(_ProbeSolver):
        def __init__(self, config) -> None:
            super().__init__(config)
            self.close_count = 0

        def close(self) -> None:
            self.close_count += 1
            super().close()

    class _ImmediateWorker(_SuccessfulWorker):
        def receive(self, *, timeout_s: float):
            assert timeout_s == 0.0
            assert self.job is not None
            return SimpleNamespace(outcome=_fit_success(self.job))

    identity = LiveLearningIdentity(
        session_id="session-handoff",
        cook_id="cook-handoff",
        configuration_digest=partition,
        incumbent_digest=active_descriptor.model_digest,
        role_generation=active_descriptor.role_generation,
        candidate_generation=active_descriptor.candidate_generation + 1,
    )
    orchestrator = GreyLearningOrchestrator(
        identity=identity,
        config=harness.activation.active_pair.solver.config,
        incumbent_pair=CandidatePair(
            harness.activation.active_pair.estimator,
            harness.activation.active_pair.solver,
        ),
        estimator_factory=_CountingEstimator,
        controller_factory=_CountingSolver,
        timing_probe=lambda _solver: TargetTimingEvidence(
            "candidate-dry-solve",
            3,
            1.0,
            25.0,
        ),
        trigger_config=TriggerConfig(
            min_samples=9,
            min_input_variance=0.02,
            min_input_levels=3,
            min_temperature_span_c=8.0,
            min_identifiability=0.5,
        ),
        evaluation_config=EvaluationConfig(required_consecutive_wins=2),
        worker=_ImmediateWorker(),
    )
    request = FitRequest(
        request_id="e" * 64,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        fit_corpus=snapshot.identity,
        configuration_digest=identity.configuration_digest,
        parent_incumbent_digest=identity.incumbent_digest,
        parent_incumbent_generation=identity.role_generation,
        candidate_generation=identity.candidate_generation,
    )
    job = segmented_corpus_fit_job(
        snapshot,
        request,
        harness.activation.active_pair.solver.config,
    )
    assert orchestrator.submit_corpus_fit(job) is FitSubmission.ACCEPTED
    delivery = orchestrator.poll_fit_off_path(
        live_identity=identity,
        live_origin=CandidateOrigin.PASSIVE_ONLINE,
    )
    assert delivery.preparation is not None
    estimator = delivery.preparation.candidate_pair.estimator
    solver = delivery.preparation.candidate_pair.controller
    incumbent_predict = lambda _origin: -1_000.0
    challenger_predict = lambda _origin: 0.0
    orchestrator.register_causal_forecasts(
        _frame(9),
        incumbent_predict=incumbent_predict,
        challenger_predict=challenger_predict,
    )
    for sequence in range(10, 190):
        orchestrator.observe_completed_frame(_frame(sequence), identifiability=0.8)
    assert not orchestrator.evaluate_ready_off_path().accepted
    orchestrator.register_causal_forecasts(
        _frame(190),
        incumbent_predict=incumbent_predict,
        challenger_predict=challenger_predict,
    )
    for sequence in range(191, 371):
        orchestrator.observe_completed_frame(_frame(sequence), identifiability=0.8)
    evaluation = orchestrator.evaluate_ready_off_path()
    assert evaluation.accepted
    _seed_durable_challenger(
        harness,
        delivery.preparation,
        phase="qualified",
        decision_id=evaluation.decision_id,
        fit_corpus=snapshot.identity,
    )
    harness.persistence.accept_evidence = False

    with pytest.raises(
        RuntimeError,
        match="learning-lifecycle-evidence-not-accepted",
    ):
        orchestrator.handoff_if_ready(
            confidence_accepted=True,
            online_enabled=True,
            prepare=lambda preparation, policy: harness.runtime.prepare_automatic_activation(
                preparation,
                policy,
                evaluation,
            ),
        )

    orchestrator.close()
    assert estimator.close_count == 1
    assert solver.close_count == 1
    harness.runtime.close()
    harness.activation.close()


def test_lifecycle_rejection_aborts_durable_prepared_owner_transactionally(ds) -> None:
    harness = _harness()
    preparation, evaluation, components = _automatic_candidate(harness)
    _seed_durable_challenger(
        harness,
        preparation,
        phase="qualified",
        decision_id=evaluation.decision_id,
    )
    harness.persistence.accept_evidence = False

    with pytest.raises(
        RuntimeError,
        match="learning-lifecycle-evidence-not-accepted",
    ):
        harness.runtime.prepare_automatic_activation(
            preparation,
            ActivationPolicy.CAUSAL_AUTO,
            evaluation,
        )

    assert components.estimator.closed
    assert components.controller.closed
    assert harness.activation.active_pair is harness.active
    assert harness.active.authorized
    assert not harness.activation.activation_pending
    harness.runtime.close()
    harness.activation.close()


def test_automatic_activation_queue_rejection_closes_inert_candidate(monkeypatch, ds) -> None:
    harness = _harness()
    preparation, evaluation, components = _automatic_candidate(harness)
    _seed_durable_challenger(
        harness,
        preparation,
        phase="qualified",
        decision_id=evaluation.decision_id,
    )
    monkeypatch.setattr(
        harness.activation,
        "queue_prepared_activation",
        lambda *_args, **_kwargs: False,
    )

    with pytest.raises(RuntimeError, match="activation-transition-rejected"):
        harness.runtime.prepare_automatic_activation(
            preparation,
            ActivationPolicy.CAUSAL_AUTO,
            evaluation,
        )

    assert components.estimator.closed
    assert components.controller.closed
    assert harness.activation.active_pair is harness.active
    harness.runtime.close()
    harness.activation.close()


@pytest.mark.parametrize(
    ("section", "key", "value"),
    (
        ("activation", "phase", "prepared"),
        ("activation", "phase", "active"),
    ),
)
def test_restore_preserves_each_supported_checkpoint_state(
    ds,
    section,
    key,
    value,
) -> None:
    source = _harness()
    snapshot = source.runtime.get_model_snapshot()
    snapshot[section][key] = value
    target = _harness()

    assert target.runtime.restore_model(snapshot)
    restored = target.runtime.get_model_snapshot()
    assert restored[section][key] == value
    if value == "active" and snapshot["challenger_authority"] is not None:
        assert restored == {
            **snapshot,
            "revision": snapshot["revision"] + 1,
            "challenger_authority": None,
        }
    else:
        assert restored == snapshot

    target.runtime.close()
    target.activation.close()
    source.runtime.close()
    source.activation.close()


def test_restore_rejects_crossed_active_identity_without_replacing_owner() -> None:
    harness = _harness()
    snapshot = harness.runtime.get_model_snapshot()
    snapshot["identities"]["active_digest"] = "f" * 64
    incumbent = harness.activation.active_pair

    assert harness.runtime.restore_model(snapshot) is False
    assert harness.activation.active_pair is incumbent
    assert incumbent.authorized
    assert not incumbent.closed
    harness.runtime.close()
    harness.activation.close()


def test_restore_replace_failure_closes_new_pair_and_keeps_incumbent(monkeypatch) -> None:
    source = _harness()
    snapshot = source.runtime.get_model_snapshot()
    target = _harness()
    incumbent = target.activation.active_pair
    built = []
    real_restore = target.factory.restore

    def restore(descriptor):
        pair = real_restore(descriptor)
        built.append(pair)
        return pair

    monkeypatch.setattr(target.factory, "restore", restore)
    monkeypatch.setattr(
        target.activation,
        "replace_active_pair",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("swap failed")),
    )

    assert target.runtime.restore_model(snapshot) is False
    assert target.activation.active_pair is incumbent
    assert incumbent.authorized
    assert built[0].closed
    target.runtime.close()
    target.activation.close()
    source.runtime.close()
    source.activation.close()


def test_runtime_snapshot_is_exact_json_safe_v7_without_process_jobs() -> None:
    harness = _harness()
    snapshot = harness.runtime.get_model_snapshot()
    assert snapshot is not None
    assert snapshot["version"] == 7
    assert snapshot["schema"] == "pifire-grey-learning/v7"
    assert set(snapshot) == {
        "version",
        "schema",
        "revision",
        "structure",
        "active",
        "evidence",
        "origin",
        "policy",
        "identification",
        "identities",
        "activation",
        "failure",
        "active_pair",
        "challenger_authority",
        "installation_identity_digest",
    }
    encoded = json.dumps(snapshot, sort_keys=True, allow_nan=False)
    assert "process" not in encoded
    assert "job" not in encoded
    harness.runtime.close()
    harness.activation.close()


def test_restore_parameter_mismatch_closes_built_pair_and_keeps_incumbent(
    monkeypatch,
) -> None:
    source = _harness()
    snapshot = source.runtime.get_model_snapshot()
    target = _harness()
    incumbent = target.activation.active_pair
    built = []
    real_restore = target.factory.restore

    def restore(descriptor):
        pair = real_restore(descriptor)
        built.append(pair)
        pair.core.config["theta"] = float(pair.core.config["theta"]) + 1.0
        return pair

    monkeypatch.setattr(target.factory, "restore", restore)

    assert target.runtime.restore_model(snapshot) is False
    assert target.activation.active_pair is incumbent
    assert incumbent.authorized
    assert built[0].closed
    target.runtime.close()
    target.activation.close()
    source.runtime.close()
    source.activation.close()


@pytest.mark.parametrize("failure", ("start", "replace"))
def test_restore_stages_learning_before_atomic_active_replacement(
    monkeypatch,
    failure,
) -> None:
    source = _harness()
    snapshot = source.runtime.get_model_snapshot()
    target = _harness(learning_enabled=True)
    incumbent = target.activation.active_pair
    before = target.runtime.get_model_snapshot()
    staged = []
    restored = []

    class _StagedLearning:
        def __init__(self, **_kwargs) -> None:
            self.closed = False
            staged.append(self)

        def start(self) -> None:
            if failure == "start":
                raise RuntimeError("staged learning failed")

        def close(self) -> None:
            self.closed = True

    real_restore = target.factory.restore

    def restore(descriptor):
        pair = real_restore(descriptor)
        restored.append(pair)
        return pair

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _StagedLearning,
    )
    monkeypatch.setattr(target.factory, "restore", restore)
    if failure == "replace":
        monkeypatch.setattr(
            target.activation,
            "replace_active_pair",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("replacement failed")),
        )

    assert target.runtime.restore_model(snapshot) is False
    assert target.activation.active_pair is incumbent
    assert incumbent.authorized
    assert not incumbent.closed
    assert target.runtime.get_model_snapshot() == before
    assert staged and staged[0].closed
    assert restored and restored[0].closed
    target.runtime.close()
    target.activation.close()
    source.runtime.close()
    source.activation.close()


def test_restore_stages_identity_from_exact_restored_full_configuration(
    monkeypatch,
) -> None:
    source = _harness(
        estimator_kind="kf",
        base_configuration={
            "estimator": "kf",
            "control_period": 7.0,
        },
    )
    snapshot = source.runtime.get_model_snapshot()
    target = _harness(
        learning_enabled=True,
        estimator_kind="ekf",
        base_configuration={
            "estimator": "ekf",
            "control_period": 3.0,
        },
    )
    staged = []

    class _StagedLearning:
        def __init__(self, **kwargs) -> None:
            self.identity = kwargs["identity"]
            self.poll_identities = []
            self.closed = False
            staged.append(self)

        def start(self) -> None:
            return None

        def poll_fit_off_path(self, *, live_identity, **_kwargs):
            self.poll_identities.append(live_identity)

        def evaluate_ready_off_path(self):
            return None

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _StagedLearning,
    )

    assert target.runtime.restore_model(snapshot)
    restored_learning = staged[-1]
    live_identity = target.runtime.learning_identity()
    assert restored_learning.identity == live_identity
    assert target.activation.active_pair.core.config["estimator"] == "kf"
    assert target.activation.active_pair.core.config["control_period"] == 7.0
    target.runtime.poll_learning_off_path(
        live_origin=CandidateOrigin.PASSIVE_ONLINE,
    )
    assert restored_learning.poll_identities == [live_identity]
    target.runtime.close()
    target.activation.close()
    source.runtime.close()
    source.activation.close()


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        {},
        {"version": 5},
        {
            "version": 4,
            "active": {"parameters": {"C_c": float("nan")}},
        },
    ],
)
def test_invalid_restore_is_atomic_and_leaves_active_owner_authorized(invalid) -> None:
    harness = _harness()
    before = harness.runtime.get_model_snapshot()
    assert harness.runtime.restore_model(invalid) is False
    assert harness.activation.active_pair is harness.active
    assert harness.active.authorized
    assert harness.runtime.get_model_snapshot() == before
    harness.runtime.close()
    harness.activation.close()


def test_close_is_idempotent_and_leaves_injected_activation_persistence_open() -> None:
    harness = _harness()
    harness.runtime.close()
    harness.runtime.close()
    assert harness.active.closed is False
    assert harness.persistence.close_count == 0
    harness.activation.close()
    assert harness.persistence.close_count == 0
