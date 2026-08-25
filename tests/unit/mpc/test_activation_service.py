from dataclasses import FrozenInstanceError, replace
import json
from types import SimpleNamespace

import pytest

from common.model_evidence import EvidenceKind, ModelEvidenceRecord, RollbackEvidence
from common.persistence.model_evidence import (
    ModelActivationState,
    ModelRollbackCommitOutcome,
)
from controller.model_learning import activation_service as activation_service_module
from common.web_contracts.learning import ModelActivationRequest, ModelRollbackRequest
from controller.model_learning.activation import (
    GreyControlPairDescriptor,
    canonical_snapshot_digest,
)
from controller.model_learning.activation_service import (
    ActivationAccepted,
    ActivationRejected,
    ActivationRejectionCategory,
    ModelActivationService,
    RollbackAccepted,
    RollbackRejected,
    RollbackRejectionCategory,
)
from controller.model_learning.contracts import ActivationPolicy
from tests.unit.mpc._solver_fixtures import owned_pair


_NOW_MS = 1_725_000_123_456
_DECISION_ID = "decision-service-grey"


class _Handle:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.closed = 0
        self._failure = failure
        self.config = SimpleNamespace(T_amb=20.0)

    def close(self) -> None:
        self.closed += 1
        if self._failure is not None:
            raise self._failure


class _Report:
    def __init__(
        self,
        candidate_digest: str,
        *,
        status: str = "ready-for-review",
        decision_id: str = _DECISION_ID,
        policy: str = ActivationPolicy.OPERATOR_REVIEWED.value,
    ) -> None:
        self._payload = {
            "status": status,
            "candidate": {"digest": candidate_digest, "policy": policy},
            "decision_id": decision_id,
        }

    def to_dict(self) -> dict[str, object]:
        return self._payload


class _Receipt:
    def __init__(
        self,
        *,
        accepted: bool = True,
        completed: bool = True,
        durable: bool = True,
        waited: bool = True,
        error: str | None = None,
        wait_failure: Exception | None = None,
    ) -> None:
        self.accepted = accepted
        self.completed = completed
        self.durable = durable
        self.error = error
        self._waited = waited
        self._wait_failure = wait_failure
        self.wait_calls: list[float | None] = []

    def wait(self, timeout: float | None = None) -> bool:
        self.wait_calls.append(timeout)
        if self._wait_failure is not None:
            raise self._wait_failure
        return self._waited


class _Worker:
    def __init__(
        self,
        receipt: _Receipt | None = None,
        *,
        submit_failure: Exception | None = None,
        flush_result: bool = True,
        flush_failure: Exception | None = None,
    ) -> None:
        self.receipt = receipt or _Receipt()
        self.submit_failure = submit_failure
        self.flush_result = flush_result
        self.flush_failure = flush_failure
        self.records = []
        self.flush_calls: list[float] = []

    def submit_activation_phase(self, record, *, expected_phase):
        self.records.append((record, expected_phase))
        if self.submit_failure is not None:
            raise self.submit_failure
        return self.receipt

    def flush_and_stop(self, *, timeout: float) -> bool:
        self.flush_calls.append(timeout)
        if self.flush_failure is not None:
            raise self.flush_failure
        return self.flush_result


class _Factory:
    def __init__(
        self,
        incumbent: GreyControlPairDescriptor,
        candidate: GreyControlPairDescriptor,
        *,
        failure: str | None = None,
        candidate_close_failure: Exception | None = None,
        incumbent_close_failure: Exception | None = None,
    ) -> None:
        self.incumbent = incumbent
        self.candidate = candidate
        self.failure = failure
        self.calls: list[tuple[str, object]] = []
        self.incumbent_handles = (_Handle(), _Handle(failure=incumbent_close_failure))
        self.candidate_handles = (_Handle(), _Handle(failure=candidate_close_failure))
        self.incumbent_owner = owned_pair(incumbent, *self.incumbent_handles)
        self.candidate_owner = owned_pair(candidate, *self.candidate_handles)

    def migrate_legacy_descriptor(self, descriptor):
        self.calls.append(("migrate", descriptor))
        if self.failure == "migrate":
            raise ValueError("descriptor-migration-failed")
        return descriptor

    def restore(self, descriptor):
        self.calls.append(("restore", descriptor))
        if descriptor == self.incumbent:
            if self.failure == "incumbent-restore":
                raise RuntimeError("incumbent-restore-failed")
            return self.incumbent_owner
        if self.failure == "candidate-build":
            raise RuntimeError("candidate-build-failed-from-factory")
        if self.failure == "wrong-candidate":
            return self.incumbent_owner
        return self.candidate_owner

    def validate(self, pair) -> bool:
        self.calls.append(("validate", pair))
        if self.failure == "validate-raise":
            raise RuntimeError("candidate-validation-exploded")
        return self.failure != "validate-false"

    def dry_solve(self, pair, *, temperature_c: float):
        self.calls.append(("dry-solve", (pair, temperature_c)))
        if self.failure == "dry-raise":
            raise RuntimeError("native-dry-solve-exploded")
        return SimpleNamespace(accepted=self.failure != "dry-false")


class _Store:
    def __init__(self, checkpoint: dict[str, object] | None) -> None:
        self.checkpoint = checkpoint
        self.loads: list[str] = []

    def load(self, name: str) -> dict[str, object] | None:
        self.loads.append(name)
        return self.checkpoint


def _descriptor(theta: float, *, candidate_generation: int, role_generation: int):
    configuration = {"theta": theta}
    return GreyControlPairDescriptor(
        model_digest=canonical_snapshot_digest(configuration),
        configuration=configuration,
        estimator_kind="ekf",
        solver_kind="acados-grey",
        candidate_generation=candidate_generation,
        role_generation=role_generation,
    )


def _activation_fixture(
    *,
    report: _Report | None = None,
    checkpoint: dict[str, object] | None = None,
    factory_failure: str | None = None,
    candidate_close_failure: Exception | None = None,
    incumbent_close_failure: Exception | None = None,
    receipt: _Receipt | None = None,
    submit_failure: Exception | None = None,
    flush_result: bool = True,
    flush_failure: Exception | None = None,
):
    incumbent = _descriptor(50.0, candidate_generation=3, role_generation=4)
    candidate = _descriptor(40.0, candidate_generation=4, role_generation=5)
    factory = _Factory(
        incumbent,
        candidate,
        failure=factory_failure,
        candidate_close_failure=candidate_close_failure,
        incumbent_close_failure=incumbent_close_failure,
    )
    worker = _Worker(
        receipt,
        submit_failure=submit_failure,
        flush_result=flush_result,
        flush_failure=flush_failure,
    )
    store = _Store(
        checkpoint
        if checkpoint is not None
        else {
            "active_pair": incumbent.to_dict(),
            "candidate_pair": candidate.to_dict(),
        }
    )
    factory_provider_calls = []
    worker_factory_calls = []

    def factory_provider():
        factory_provider_calls.append(True)
        return factory

    def worker_factory():
        worker_factory_calls.append(True)
        return worker

    service = ModelActivationService(
        report_provider=lambda: report or _Report(candidate.model_digest),
        checkpoint_store=store,
        pair_factory_provider=factory_provider,
        persistence_worker_provider=worker_factory,
    )
    request = ModelActivationRequest(
        candidate_digest=candidate.model_digest,
        decision_id=_DECISION_ID,
    )
    return SimpleNamespace(
        service=service,
        request=request,
        incumbent=incumbent,
        candidate=candidate,
        factory=factory,
        worker=worker,
        store=store,
        factory_provider_calls=factory_provider_calls,
        worker_factory_calls=worker_factory_calls,
    )


def _active_state(
    incumbent: GreyControlPairDescriptor,
    candidate: GreyControlPairDescriptor,
    *,
    phase: str = "active",
    include_lineage: bool = True,
) -> ModelActivationState:
    return ModelActivationState(
        active_snapshot_json=json.dumps(dict(candidate.configuration)),
        rollback_snapshot_json=json.dumps(dict(incumbent.configuration)),
        evidence_decision_id=_DECISION_ID,
        controller_configuration_digest=candidate.ownership_digest,
        role_generation=candidate.role_generation,
        phase=phase,
        transaction_id="1" * 64,
        incumbent_pair_json=json.dumps(incumbent.to_dict()),
        candidate_pair_json=(json.dumps(candidate.to_dict()) if include_lineage else None),
        rollback_pair_json=(json.dumps(incumbent.to_dict()) if include_lineage else None),
        origin="operator-calibration",
        policy="operator-reviewed",
        candidate_generation=candidate.candidate_generation,
        candidate_digest=candidate.model_digest,
    )


def test_outcomes_are_exhaustive_immutable_typed_values() -> None:
    accepted = ActivationAccepted("a" * 64, _DECISION_ID, "b" * 64, 8)
    rejected = RollbackRejected(RollbackRejectionCategory.CONFLICT, "stale")

    with pytest.raises(FrozenInstanceError):
        accepted.role_generation = 9
    with pytest.raises(FrozenInstanceError):
        rejected.reason = "changed"

    assert accepted.accepted is True
    assert rejected.accepted is False


@pytest.mark.parametrize(
    ("report", "request_update", "reason"),
    (
        (_Report("c" * 64, status="collecting"), {}, "confidence decision is not ready-for-review"),
        (_Report("c" * 64), {"candidate_digest": "d" * 64}, "candidate-digest-changed"),
        (
            _Report("c" * 64, policy=ActivationPolicy.PASSIVE_AUTO.value),
            {},
            "manual activation requires operator-reviewed policy",
        ),
        (_Report("c" * 64, decision_id="newer"), {}, "stale-confidence-decision"),
    ),
)
def test_activation_rejects_stale_or_non_operator_report_before_native_work(
    report,
    request_update,
    reason,
) -> None:
    harness = _activation_fixture()
    report._payload["candidate"]["digest"] = harness.candidate.model_digest
    harness.service = ModelActivationService(
        report_provider=lambda: report,
        checkpoint_store=harness.store,
        pair_factory_provider=lambda: harness.factory,
        persistence_worker_provider=lambda: harness.worker,
    )
    request = harness.request.model_copy(update=request_update)

    outcome = harness.service.activate(request, now_ms=_NOW_MS)

    assert outcome == ActivationRejected(ActivationRejectionCategory.CONFLICT, reason)
    assert harness.factory.calls == []
    assert harness.worker.flush_calls == []


@pytest.mark.parametrize(
    ("checkpoint", "reason", "category"),
    (
        ({}, "candidate-pair-not-found", ActivationRejectionCategory.CONFLICT),
        (
            {"active_pair": {"configuration": {}}, "candidate_pair": {}},
            "model_digest must be a lowercase SHA-256 digest",
            ActivationRejectionCategory.CONFLICT,
        ),
    ),
)
def test_activation_rejects_missing_or_corrupt_checkpoint(
    checkpoint,
    reason,
    category,
) -> None:
    harness = _activation_fixture(checkpoint=checkpoint)

    outcome = harness.service.activate(harness.request, now_ms=_NOW_MS)

    assert outcome == ActivationRejected(category, reason)
    assert len(harness.factory_provider_calls) == (0 if checkpoint == {} else 1)
    assert harness.worker_factory_calls == []


def test_activation_rejects_absent_checkpoint() -> None:
    harness = _activation_fixture()
    harness.store.checkpoint = None

    outcome = harness.service.activate(harness.request, now_ms=_NOW_MS)

    assert outcome == ActivationRejected(
        ActivationRejectionCategory.CONFLICT,
        "candidate-snapshot-not-found",
    )
    assert harness.factory_provider_calls == []


@pytest.mark.parametrize(
    ("failure", "reason"),
    (
        ("migrate", "descriptor-migration-failed"),
        ("incumbent-restore", "incumbent-restore-failed"),
        ("candidate-build", "candidate-build-failed"),
        ("wrong-candidate", "candidate-build-failed"),
        ("validate-false", "candidate-validation-failed"),
        ("validate-raise", "candidate-validation-failed"),
        ("dry-false", "native-dry-solve-failed"),
        ("dry-raise", "native-dry-solve-failed"),
    ),
)
def test_activation_classifies_native_failures_and_closes_all_returned_owners_once(
    failure,
    reason,
) -> None:
    harness = _activation_fixture(factory_failure=failure)

    outcome = harness.service.activate(harness.request, now_ms=_NOW_MS)

    assert outcome == ActivationRejected(ActivationRejectionCategory.CONFLICT, reason)
    if failure == "migrate":
        assert harness.worker.flush_calls == []
    else:
        assert harness.worker.flush_calls == [2.0]
    assert all(handle.closed <= 1 for handle in harness.factory.incumbent_handles)
    assert all(handle.closed <= 1 for handle in harness.factory.candidate_handles)


@pytest.mark.parametrize(
    ("receipt", "submit_failure", "reason", "category"),
    (
        (
            _Receipt(accepted=False, completed=True, durable=False, waited=False),
            None,
            "activation-persistence-unavailable",
            ActivationRejectionCategory.PERSISTENCE_UNAVAILABLE,
        ),
        (
            _Receipt(completed=False, durable=False, waited=False),
            None,
            "activation-persistence-not-durable",
            ActivationRejectionCategory.PERSISTENCE_UNAVAILABLE,
        ),
        (
            _Receipt(completed=False, durable=True, waited=True),
            None,
            "activation-persistence-not-durable",
            ActivationRejectionCategory.PERSISTENCE_UNAVAILABLE,
        ),
        (
            _Receipt(wait_failure=RuntimeError("wait failed")),
            None,
            "activation-persistence-not-durable",
            ActivationRejectionCategory.PERSISTENCE_UNAVAILABLE,
        ),
        (
            _Receipt(completed=True, durable=False, waited=False, error="ValueError: activation-authority-changed"),
            None,
            "activation-authority-changed",
            ActivationRejectionCategory.CONFLICT,
        ),
        (
            _Receipt(completed=True, durable=False, waited=False, error="ValueError: activation-state-changed"),
            None,
            "activation-state-changed",
            ActivationRejectionCategory.CONFLICT,
        ),
        (
            _Receipt(),
            RuntimeError("disk offline"),
            "activation-persistence-failed",
            ActivationRejectionCategory.PERSISTENCE_UNAVAILABLE,
        ),
    ),
)
def test_activation_requires_an_accepted_completed_durable_prepared_receipt(
    receipt,
    submit_failure,
    reason,
    category,
) -> None:
    harness = _activation_fixture(receipt=receipt, submit_failure=submit_failure)

    outcome = harness.service.activate(harness.request, now_ms=_NOW_MS)

    assert outcome == ActivationRejected(category, reason)
    assert harness.worker.flush_calls == [2.0]
    assert all(handle.closed == 1 for handle in harness.factory.incumbent_handles)
    assert all(handle.closed == 1 for handle in harness.factory.candidate_handles)


def test_activation_uses_one_factory_and_returns_only_after_prepared_owners_close() -> None:
    harness = _activation_fixture()

    outcome = harness.service.activate(harness.request, now_ms=_NOW_MS)

    assert outcome == ActivationAccepted(
        transaction_id=outcome.transaction_id,
        decision_id=_DECISION_ID,
        candidate_digest=harness.candidate.model_digest,
        role_generation=harness.candidate.role_generation,
    )
    assert len(harness.factory_provider_calls) == 1
    assert [name for name, _value in harness.factory.calls] == [
        "migrate",
        "migrate",
        "restore",
        "restore",
        "validate",
        "dry-solve",
    ]
    assert len(harness.worker.records) == 1
    record, expected_phase = harness.worker.records[0]
    assert expected_phase is None
    assert record.timestamp_ms == _NOW_MS
    assert record.incumbent == harness.incumbent
    assert record.candidate == harness.candidate
    assert record.policy is ActivationPolicy.OPERATOR_REVIEWED
    assert harness.worker.receipt.wait_calls == [2.0]
    assert harness.worker.flush_calls == [2.0]
    assert all(handle.closed == 1 for handle in harness.factory.incumbent_handles)
    assert all(handle.closed == 1 for handle in harness.factory.candidate_handles)


@pytest.mark.parametrize("cleanup", ("candidate", "worker-false", "worker-raise"))
def test_activation_classifies_cleanup_failure_after_success_and_attempts_all_cleanup(cleanup) -> None:
    harness = _activation_fixture(
        candidate_close_failure=(RuntimeError("candidate close failed") if cleanup == "candidate" else None),
        flush_result=cleanup != "worker-false",
        flush_failure=(RuntimeError("worker close failed") if cleanup == "worker-raise" else None),
    )
    outcome = harness.service.activate(harness.request, now_ms=_NOW_MS)

    assert outcome == ActivationRejected(
        ActivationRejectionCategory.CLEANUP_FAILED,
        "activation-cleanup-failed",
        cleanup_failed=True,
    )
    assert harness.worker.flush_calls == [2.0]
    assert all(handle.closed == 1 for handle in harness.factory.incumbent_handles)
    assert all(handle.closed == 1 for handle in harness.factory.candidate_handles)


@pytest.mark.parametrize(
    ("candidate_close_failure", "incumbent_close_failure", "flush_result", "flush_failure"),
    (
        (RuntimeError("candidate close failed"), None, True, None),
        (None, RuntimeError("incumbent close failed"), True, None),
        (None, None, False, None),
        (None, None, True, RuntimeError("worker close failed")),
    ),
)
def test_activation_preserves_domain_rejection_and_exposes_secondary_cleanup_failure(
    candidate_close_failure,
    incumbent_close_failure,
    flush_result,
    flush_failure,
) -> None:
    harness = _activation_fixture(
        factory_failure="validate-false",
        candidate_close_failure=candidate_close_failure,
        incumbent_close_failure=incumbent_close_failure,
        flush_result=flush_result,
        flush_failure=flush_failure,
    )

    outcome = harness.service.activate(harness.request, now_ms=_NOW_MS)

    assert outcome.category is ActivationRejectionCategory.CONFLICT
    assert outcome.reason == "candidate-validation-failed"
    assert outcome.cleanup_failed is True
    assert all(handle.closed >= 1 for handle in harness.factory.candidate_handles)
    assert all(handle.closed >= 1 for handle in harness.factory.incumbent_handles)
    assert harness.worker.flush_calls == [2.0]


def test_activation_factory_construction_failure_is_typed_without_starting_worker() -> None:
    harness = _activation_fixture()

    def fail_factory():
        raise RuntimeError("pair factory unavailable")

    service = ModelActivationService(
        report_provider=lambda: _Report(harness.candidate.model_digest),
        checkpoint_store=harness.store,
        pair_factory_provider=fail_factory,
        persistence_worker_provider=lambda: harness.worker,
    )

    outcome = service.activate(harness.request, now_ms=_NOW_MS)

    assert outcome == ActivationRejected(
        ActivationRejectionCategory.CONFLICT,
        "pair factory unavailable",
    )
    assert harness.worker.flush_calls == []


@pytest.mark.parametrize(
    ("state", "reason"),
    (
        (None, "there is no active grey generation"),
        ("prepared", "there is no active grey generation"),
        ("missing-lineage", "activation-lineage-missing"),
    ),
)
def test_rollback_rejects_missing_active_authority_or_lineage(state, reason) -> None:
    harness = _activation_fixture()
    activation = (
        None
        if state is None
        else _active_state(
            harness.incumbent,
            harness.candidate,
            phase=("active" if state == "missing-lineage" else state),
            include_lineage=state != "missing-lineage",
        )
    )
    service = ModelActivationService(
        activation_reader=lambda: activation,
        rollback_committer=lambda _record, *, expected_activation: pytest.fail("must not commit"),
    )

    outcome = service.rollback(ModelRollbackRequest(reason=" operator rollback "), now_ms=_NOW_MS)

    assert outcome == RollbackRejected(RollbackRejectionCategory.CONFLICT, reason)


def test_rollback_builds_exact_evidence_and_commits_against_identical_cas_authority() -> None:
    harness = _activation_fixture()
    activation = _active_state(harness.incumbent, harness.candidate)
    calls = []

    def commit(record, *, expected_activation):
        calls.append((record, expected_activation))
        return ModelRollbackCommitOutcome(record, True)

    service = ModelActivationService(
        activation_reader=lambda: activation,
        rollback_committer=commit,
    )

    outcome = service.rollback(ModelRollbackRequest(reason=" operator rollback "), now_ms=_NOW_MS)

    assert outcome == RollbackAccepted(
        decision_id=_DECISION_ID,
        reason="operator rollback",
        role_generation=harness.candidate.role_generation + 1,
        rollback_digest=harness.incumbent.model_digest,
    )
    assert len(calls) == 1
    decision, expected_activation = calls[0]
    assert expected_activation is activation
    assert decision == ModelEvidenceRecord(
        evidence_id=f"rollback:{_DECISION_ID}:{harness.candidate.role_generation + 1}:{_NOW_MS}",
        kind=EvidenceKind.ROLLBACK,
        session_id="api-manual-rollback",
        cook_id=None,
        timestamp_ms=_NOW_MS,
        role_generation=harness.candidate.role_generation + 1,
        model_digest=harness.candidate.model_digest,
        provenance_digest=harness.incumbent.model_digest,
        payload=RollbackEvidence(decision_id=_DECISION_ID, reason="operator rollback"),
    )


@pytest.mark.parametrize(
    ("failure", "category", "reason"),
    (
        (ValueError("activation-state-changed"), RollbackRejectionCategory.CONFLICT, "activation-state-changed"),
        (
            RuntimeError("disk offline"),
            RollbackRejectionCategory.PERSISTENCE_UNAVAILABLE,
            "rollback-persistence-failed: disk offline",
        ),
    ),
)
def test_rollback_classifies_cas_and_persistence_failures(failure, category, reason) -> None:
    harness = _activation_fixture()
    activation = _active_state(harness.incumbent, harness.candidate)

    def fail_commit(_record, *, expected_activation):
        assert expected_activation is activation
        raise failure

    service = ModelActivationService(
        activation_reader=lambda: activation,
        rollback_committer=fail_commit,
    )

    outcome = service.rollback(ModelRollbackRequest(reason="operator rollback"), now_ms=_NOW_MS)

    assert outcome == RollbackRejected(category, reason)


def test_rollback_idempotent_commit_returns_the_original_durable_lifecycle() -> None:
    harness = _activation_fixture()
    activation = _active_state(harness.incumbent, harness.candidate)
    original = ModelEvidenceRecord(
        evidence_id="rollback-original",
        kind=EvidenceKind.ROLLBACK,
        session_id="api-manual-rollback",
        cook_id=None,
        timestamp_ms=_NOW_MS - 10,
        role_generation=harness.candidate.role_generation + 1,
        model_digest=harness.candidate.model_digest,
        provenance_digest=harness.incumbent.model_digest,
        payload=RollbackEvidence(decision_id=_DECISION_ID, reason="first reason"),
    )
    service = ModelActivationService(
        activation_reader=lambda: activation,
        rollback_committer=lambda _record, *, expected_activation: ModelRollbackCommitOutcome(
            original,
            False,
        ),
    )

    outcome = service.rollback(ModelRollbackRequest(reason="retry reason"), now_ms=_NOW_MS)

    assert outcome == RollbackAccepted(
        decision_id=_DECISION_ID,
        reason="first reason",
        role_generation=harness.candidate.role_generation + 1,
        rollback_digest=harness.incumbent.model_digest,
    )


def test_activation_classifies_report_and_checkpoint_collaborator_exceptions() -> None:
    harness = _activation_fixture()

    def fail_report():
        raise RuntimeError("report offline")

    report_failure = ModelActivationService(
        report_provider=fail_report,
        checkpoint_store=harness.store,
        pair_factory_provider=lambda: harness.factory,
        persistence_worker_provider=lambda: harness.worker,
    ).activate(harness.request, now_ms=_NOW_MS)

    class _FailingStore:
        def load(self, _name):
            raise RuntimeError("checkpoint offline")

    checkpoint_failure = ModelActivationService(
        report_provider=lambda: _Report(harness.candidate.model_digest),
        checkpoint_store=_FailingStore(),
        pair_factory_provider=lambda: harness.factory,
        persistence_worker_provider=lambda: harness.worker,
    ).activate(harness.request, now_ms=_NOW_MS)

    assert report_failure == ActivationRejected(
        ActivationRejectionCategory.PERSISTENCE_UNAVAILABLE,
        "activation-report-failed: report offline",
    )
    assert checkpoint_failure == ActivationRejected(
        ActivationRejectionCategory.PERSISTENCE_UNAVAILABLE,
        "activation-checkpoint-failed: checkpoint offline",
    )


def test_activation_classifies_worker_construction_failure() -> None:
    harness = _activation_fixture()

    def fail_worker():
        raise RuntimeError("worker offline")

    service = ModelActivationService(
        report_provider=lambda: _Report(harness.candidate.model_digest),
        checkpoint_store=harness.store,
        pair_factory_provider=lambda: harness.factory,
        persistence_worker_provider=fail_worker,
    )

    outcome = service.activate(harness.request, now_ms=_NOW_MS)

    assert outcome == ActivationRejected(
        ActivationRejectionCategory.PERSISTENCE_UNAVAILABLE,
        "activation-persistence-failed",
    )


def test_rollback_classifies_authority_read_and_corrupt_lineage_failures() -> None:
    harness = _activation_fixture()

    def fail_read():
        raise RuntimeError("activation store offline")

    read_failure = ModelActivationService(activation_reader=fail_read).rollback(
        ModelRollbackRequest(reason="operator rollback"),
        now_ms=_NOW_MS,
    )
    corrupt = replace(
        _active_state(harness.incumbent, harness.candidate),
        candidate_pair_json="{not-json",
    )
    lineage_failure = ModelActivationService(activation_reader=lambda: corrupt).rollback(
        ModelRollbackRequest(reason="operator rollback"),
        now_ms=_NOW_MS,
    )

    assert read_failure == RollbackRejected(
        RollbackRejectionCategory.PERSISTENCE_UNAVAILABLE,
        "rollback-persistence-failed: activation store offline",
    )
    assert lineage_failure == RollbackRejected(
        RollbackRejectionCategory.CONFLICT,
        "activation-lineage-missing",
    )


def test_rollback_rejects_a_malformed_persistence_lifecycle() -> None:
    harness = _activation_fixture()
    activation = _active_state(harness.incumbent, harness.candidate)
    service = ModelActivationService(
        activation_reader=lambda: activation,
        rollback_committer=lambda _record, *, expected_activation: SimpleNamespace(
            record=SimpleNamespace(payload=object())
        ),
    )

    outcome = service.rollback(
        ModelRollbackRequest(reason="operator rollback"),
        now_ms=_NOW_MS,
    )

    assert outcome == RollbackRejected(
        RollbackRejectionCategory.PERSISTENCE_UNAVAILABLE,
        "rollback-persistence-failed: invalid lifecycle",
    )


def test_activation_rejects_a_non_object_candidate_projection_as_invalid_data() -> None:
    harness = _activation_fixture()
    report = _Report(harness.candidate.model_digest)
    report._payload["candidate"] = "not-an-object"
    service = ModelActivationService(
        report_provider=lambda: report,
        checkpoint_store=harness.store,
        pair_factory_provider=lambda: harness.factory,
        persistence_worker_provider=lambda: harness.worker,
    )

    outcome = service.activate(harness.request, now_ms=_NOW_MS)

    assert outcome == ActivationRejected(
        ActivationRejectionCategory.INVALID_DATA,
        "candidate report must be an object",
    )


@pytest.mark.parametrize(
    ("settings", "reason"),
    (
        (
            {"controller": {"selected": "pid", "config": {"pid": {}}}},
            "MPC must be the selected controller",
        ),
        (
            {
                "controller": {"selected": "mpc", "config": {"mpc": {}}},
                "globals": {"units": "C"},
            },
            "controller configuration is incomplete",
        ),
    ),
)
def test_activation_default_factory_rejects_invalid_controller_configuration(
    monkeypatch,
    settings,
    reason,
) -> None:
    harness = _activation_fixture()
    monkeypatch.setattr(activation_service_module, "read_settings", lambda: settings)
    service = ModelActivationService(
        report_provider=lambda: _Report(harness.candidate.model_digest),
        checkpoint_store=harness.store,
        persistence_worker_provider=lambda: harness.worker,
    )

    outcome = service.activate(harness.request, now_ms=_NOW_MS)

    assert outcome == ActivationRejected(
        ActivationRejectionCategory.CONFLICT,
        reason,
    )
    assert harness.worker.flush_calls == []


def test_activation_rechecks_the_durable_checkpoint_candidate_digest() -> None:
    harness = _activation_fixture()
    changed = _descriptor(41.0, candidate_generation=4, role_generation=5)
    harness.store.checkpoint = {
        "active_pair": harness.incumbent.to_dict(),
        "candidate_pair": changed.to_dict(),
    }

    outcome = harness.service.activate(harness.request, now_ms=_NOW_MS)

    assert outcome == ActivationRejected(
        ActivationRejectionCategory.CONFLICT,
        "candidate-digest-changed",
    )
    assert harness.worker_factory_calls == []
