"""Behavioral contracts for off-path model persistence."""

import json
import threading

from common.controller_model_state import (
    MAX_SNAPSHOT_BYTES,
    CheckpointSaveOutcome,
    ControllerModelStore,
)
from common.control_trace import AmbientSource
from common.model_evidence import ActivationEvidence, EvidenceKind, ForecastOriginEvidence, ModelEvidenceRecord
from controller.runtime.model_persistence import EvidenceSubmission, ModelPersistenceWorker

_DIGEST = "a" * 64
_OTHER_DIGEST = "b" * 64


class _Store:
    def __init__(self):
        self.saved = []
        self.first_save_started = threading.Event()
        self.release_first_save = threading.Event()

    def save_outcome(self, name, snapshot):
        self.saved.append((name, snapshot))
        if len(self.saved) == 1:
            self.first_save_started.set()
            self.release_first_save.wait(timeout=1.0)
        return CheckpointSaveOutcome.SAVED


class _Logger:
    def __init__(self):
        self.errors = []

    def error(self, message):
        self.errors.append(message)


def _evidence(evidence_id: str) -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind.FORECAST_ORIGIN,
        session_id="session-a",
        cook_id="cook-a",
        timestamp_ms=200,
        role_generation=1,
        model_digest=_OTHER_DIGEST,
        provenance_digest=_DIGEST,
        payload=ForecastOriginEvidence(
            origin_sequence=1,
            origin_time_ms=100,
            completion_time_ms=200,
            horizon_steps=3,
            incumbent_digest=_DIGEST,
            challenger_digest=_OTHER_DIGEST,
            incumbent_prediction_c=100.0,
            challenger_prediction_c=101.0,
            observed_temperature_c=102.0,
            incumbent_error_c=2.0,
            challenger_error_c=1.0,
            temperature_band="near-target",
            phase="coasting",
            ambient_source=AmbientSource.CONFIGURED,
            calibration_fit=False,
        ),
    )


def _activation(evidence_id: str) -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind.ACTIVATION,
        session_id="session-a",
        cook_id="cook-a",
        timestamp_ms=201,
        role_generation=1,
        model_digest=_DIGEST,
        provenance_digest=_OTHER_DIGEST,
        payload=ActivationEvidence(
            decision_id="decision-a",
            active_snapshot_json='{"revision": 2}',
            rollback_snapshot_json='{"revision": 1}',
            controller_configuration_digest=_OTHER_DIGEST,
        ),
    )


def test_checkpoint_submission_coalesces_latest_owned_snapshot_without_blocking():
    store = _Store()
    worker = ModelPersistenceWorker(store, _Logger())
    try:
        assert worker.submit_checkpoint("mpc", {"revision": 1, "parameters": {"gain": 1}})
        assert store.first_save_started.wait(timeout=1.0)
        assert worker.submit_checkpoint("mpc", {"revision": 2, "parameters": {"gain": 2}})
        store.release_first_save.set()
        assert worker.flush_and_stop(timeout=1.0)
    finally:
        store.release_first_save.set()
        worker.flush_and_stop(timeout=1.0)

    assert [snapshot["revision"] for _, snapshot in store.saved] == [1, 2]


def test_bounded_evidence_overflow_returns_a_typed_gap_and_never_drops_activation():
    store = _Store()
    written = []
    activations = []
    worker = ModelPersistenceWorker(
        store,
        _Logger(),
        evidence_capacity=1,
        append_evidence=written.extend,
        commit_activation=activations.append,
    )
    try:
        assert worker.submit_checkpoint("mpc", {"revision": 1})
        assert store.first_save_started.wait(timeout=1.0)
        first = worker.submit_evidence(_evidence("first"))
        second = worker.submit_evidence(_evidence("second"))
        assert isinstance(first, EvidenceSubmission) and first.accepted
        assert isinstance(second, EvidenceSubmission) and not second.accepted
        assert second.recorder_gap is not None
        assert second.recorder_gap.kind is EvidenceKind.RECORDER_GAP
        assert worker.evidence_blocked
        assert worker.commit_activation(_activation("activation"))
        store.release_first_save.set()
        assert worker.flush_and_stop(timeout=1.0)
    finally:
        store.release_first_save.set()
        worker.flush_and_stop(timeout=1.0)

    assert written == [_evidence("first"), second.recorder_gap]
    assert activations == [_activation("activation")]


def test_evidence_submission_rejects_after_async_write_failure():
    failed = threading.Event()

    def fail_append(_records):
        failed.set()
        raise RuntimeError("disk unavailable")

    worker = ModelPersistenceWorker(_Store(), _Logger(), append_evidence=fail_append)
    try:
        assert worker.submit_evidence(_evidence("first")).accepted
        assert failed.wait(timeout=1.0)
        rejected = worker.submit_evidence(_evidence("second"))
        assert not rejected.accepted
        assert rejected.recorder_gap is not None
        assert rejected.recorder_gap.payload.reason == "persistence-failed"
    finally:
        worker.flush_and_stop(timeout=1.0)


def test_checkpoint_uses_the_store_default_json_byte_boundary_before_enqueue():
    logger = _Logger()
    store = _Store()
    store.release_first_save.set()
    worker = ModelPersistenceWorker(store, logger)
    snapshot = {"revision": 1, "blob": ""}
    snapshot["blob"] = "x" * (MAX_SNAPSHOT_BYTES - len(json.dumps(snapshot, allow_nan=False).encode("utf-8")))
    oversized = dict(snapshot, revision=2, blob=snapshot["blob"] + "x")
    try:
        assert len(json.dumps(snapshot, allow_nan=False).encode("utf-8")) == MAX_SNAPSHOT_BYTES
        assert worker.submit_checkpoint("mpc", snapshot)
        assert not worker.submit_checkpoint("mpc", oversized)
        assert worker.flush_and_stop(timeout=1.0)
    finally:
        store.release_first_save.set()
        worker.flush_and_stop(timeout=1.0)


def test_checkpoint_rejects_malformed_nonfinite_and_oversized_snapshots_synchronously():
    logger = _Logger()
    worker = ModelPersistenceWorker(_Store(), logger)
    try:
        assert not worker.submit_checkpoint("mpc", {"revision": -1})
        assert not worker.submit_checkpoint("mpc", {"revision": 1, "gain": float("nan")})
        assert not worker.submit_checkpoint("mpc", {"revision": 1, "blob": "x" * 65_537})
        assert logger.errors
    finally:
        worker.flush_and_stop(timeout=1.0)


def test_equal_checkpoint_revision_is_an_explicit_healthy_nonadvancing_outcome():
    state = {"version": 1, "models": {"mpc": {"revision": 1}}}
    store = ControllerModelStore(
        reader=lambda _key: state,
        writer=lambda _key, _value: None,
        conditional_writer=lambda _name, _snapshot: False,
    )
    worker = ModelPersistenceWorker(store, _Logger())
    try:
        assert worker.submit_checkpoint("mpc", {"revision": 1})
        assert worker.flush_and_stop(timeout=1.0)
        assert store.save_outcome("mpc", {"revision": 1}) is CheckpointSaveOutcome.NONADVANCING
        assert not worker.evidence_blocked
    finally:
        worker.flush_and_stop(timeout=1.0)


def test_conditional_checkpoint_writer_failure_blocks_later_submissions():
    state = {"version": 1, "models": {}}
    store = ControllerModelStore(
        reader=lambda _key: state,
        writer=lambda _key, _value: None,
        conditional_writer=lambda _name, _snapshot: False,
    )
    worker = ModelPersistenceWorker(store, _Logger())
    try:
        assert worker.submit_checkpoint("mpc", {"revision": 1})
        assert worker.flush_and_stop(timeout=1.0)
        assert worker.evidence_blocked
        assert not worker.submit_checkpoint("mpc", {"revision": 2})
        assert not worker.submit_evidence(_evidence("later")).accepted
        assert not worker.commit_activation(_activation("later"))
    finally:
        worker.flush_and_stop(timeout=1.0)


def test_save_only_store_cannot_coalesce_a_failed_checkpoint_as_healthy():
    class SaveOnlyStore:
        def __init__(self):
            self.save_calls = 0

        def save(self, _name, _snapshot):
            self.save_calls += 1
            return False

    store = SaveOnlyStore()
    worker = ModelPersistenceWorker(store, _Logger())
    try:
        assert worker.submit_checkpoint("mpc", {"revision": 1})
        assert worker.flush_and_stop(timeout=1.0)
        assert store.save_calls == 0
        assert worker.evidence_blocked
    finally:
        worker.flush_and_stop(timeout=1.0)


def test_explicit_failed_checkpoint_outcome_blocks_later_submissions():
    class FailedStore:
        def save_outcome(self, _name, _snapshot):
            return CheckpointSaveOutcome.FAILED

    worker = ModelPersistenceWorker(FailedStore(), _Logger())
    try:
        assert worker.submit_checkpoint("mpc", {"revision": 1})
        assert worker.flush_and_stop(timeout=1.0)
        assert worker.evidence_blocked
        assert not worker.submit_checkpoint("mpc", {"revision": 2})
        assert not worker.submit_evidence(_evidence("later")).accepted
        assert not worker.commit_activation(_activation("later"))
    finally:
        worker.flush_and_stop(timeout=1.0)

def test_evidence_batch_overflow_preserves_queued_fifo_and_records_every_omitted_origin():
    store = _Store()
    written = []
    worker = ModelPersistenceWorker(
        store,
        _Logger(),
        evidence_capacity=1,
        append_evidence=written.extend,
    )
    try:
        assert worker.submit_checkpoint("mpc", {"revision": 1})
        assert store.first_save_started.wait(timeout=1.0)
        assert worker.submit_evidence(_evidence("first")).accepted

        rejected = worker.submit_evidence_batch((_evidence("second"), _evidence("third")))

        assert not rejected.accepted
        assert rejected.recorder_gap is not None
        assert rejected.recorder_gap.payload.lost_record_count == 2
        assert worker.evidence_blocked
        store.release_first_save.set()
        assert worker.flush_and_stop(timeout=1.0)
    finally:
        store.release_first_save.set()
        worker.flush_and_stop(timeout=1.0)

    assert written == [_evidence("first"), rejected.recorder_gap]
