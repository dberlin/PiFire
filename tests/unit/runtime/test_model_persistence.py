"""Behavioral contracts for off-path model persistence."""

import threading

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

    def save(self, name, snapshot):
        self.saved.append((name, snapshot))
        if len(self.saved) == 1:
            self.first_save_started.set()
            self.release_first_save.wait(timeout=1.0)
        return True


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
        model_digest=_DIGEST,
        provenance_digest=_OTHER_DIGEST,
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

    assert written == [_evidence("first")]
    assert activations == [_activation("activation")]
