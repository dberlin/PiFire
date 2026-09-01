"""Restart and invalidation contracts for the durable MPC challenger authority."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from common.persistence.model_challenger import (
    ModelChallengerState,
    create_model_challenger,
    prepare_model_challenger_activation,
    read_model_challenger,
    recover_model_challenger,
    retire_model_challenger,
)
from common.persistence.model_evidence import read_model_activation
from controller.model_learning.activation import ActivationPhase, PreparedActivationRecord
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin
from tests.unit.common._model_challenger_helpers import (
    _corpus,
    _descriptor,
    _manifest,
    _qualified,
    _state,
)
from tests.unit.mpc._grey_online_helpers import (
    _frame,
    _prepared_supersession_harness,
)


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "challenger-recovery.sqlite"


def _recover_exact(
    state: ModelChallengerState,
    database_path: Path,
    *,
    recovered_ms: int = 5_000,
    **changes: object,
) -> ModelChallengerState | None:
    arguments: dict[str, object] = {
        "incumbent": state.incumbent,
        "candidate": state.candidate,
        "controller_configuration_digest": state.controller_configuration_digest,
        "fit_corpus": state.fit_corpus,
        "calibration_manifest": state.calibration_manifest,
        "recovered_ms": recovered_ms,
        "database_path": database_path,
    }
    arguments.update(changes)
    return recover_model_challenger(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "origin",
    (
        CandidateOrigin.PASSIVE_ONLINE,
        CandidateOrigin.OPERATOR_CALIBRATION,
    ),
)
@pytest.mark.parametrize("wins", [0, 1])
def test_exact_restart_retains_only_completed_wins_and_starts_a_new_epoch(
    database_path: Path,
    wins: int,
    origin: CandidateOrigin,
) -> None:
    durable = _state(
        phase="evaluating",
        origin=origin,
        policy=ActivationPolicy.CAUSAL_AUTO,
        calibration_manifest=(_manifest() if origin is CandidateOrigin.OPERATOR_CALIBRATION else None),
        evaluation_epoch=3,
        evaluation_round=wins,
        consecutive_wins=wins,
        last_decision_id="decision-3-1" if wins else None,
        last_evidence_id="challenger-round-3-1" if wins else None,
    )
    create_model_challenger(durable, database_path=database_path)

    recovered = _recover_exact(durable, database_path, recovered_ms=5_100 + wins)

    assert recovered is not None
    assert recovered.phase == "evaluating"
    assert recovered.revision == durable.revision + 1
    assert recovered.evaluation_epoch == durable.evaluation_epoch + 1
    assert recovered.evaluation_round == 0
    assert recovered.consecutive_wins == wins
    assert recovered.last_decision_id == durable.last_decision_id
    assert recovered.last_evidence_id == durable.last_evidence_id
    assert recovered.updated_ms == 5_100 + wins
    assert recovered.origin == durable.origin
    assert recovered.policy == durable.policy
    assert recovered.controller_configuration_digest == durable.controller_configuration_digest
    assert recovered.required_wins == durable.required_wins
    assert recovered.fit_corpus == durable.fit_corpus
    assert recovered.fit_lineage == durable.fit_lineage
    assert recovered.fit_preparation == durable.fit_preparation
    assert recovered.incumbent == durable.incumbent
    assert recovered.candidate == durable.candidate
    assert recovered.calibration_manifest == durable.calibration_manifest
    assert read_model_challenger(database_path=database_path) == recovered


@pytest.mark.parametrize("wins", [0, 1])
def test_runtime_restore_uses_a_fresh_evaluator_with_no_pending_origins(tmp_path: Path, wins: int) -> None:
    orchestrator, _, _, preparation, _ = _prepared_supersession_harness(tmp_path)
    registered = orchestrator.register_causal_forecasts(
        _frame(9),
        incumbent_predict=lambda _origin: -1_000.0,
        challenger_predict=lambda _origin: 0.0,
    )
    original_evaluator = orchestrator._evaluator
    assert registered
    assert original_evaluator.pending_origins

    orchestrator.restore_persisted_challenger(
        preparation,
        evaluation_epoch=8,
        consecutive_wins=wins,
    )

    assert orchestrator.prepared is preparation
    assert orchestrator.evaluation_epoch == 8
    assert orchestrator._consecutive_wins == wins
    assert orchestrator._evaluator is not original_evaluator
    assert orchestrator._evaluator.pending_origins == ()
    assert orchestrator._evaluator.completed_origins == ()
    orchestrator.close()


@pytest.mark.parametrize(
    ("changed_field", "expected_reason"),
    [
        ("incumbent", "incumbent-changed"),
        ("candidate", "candidate-changed"),
        ("configuration", "configuration-changed"),
        ("corpus", "corpus-changed"),
        ("manifest", "calibration-manifest-changed"),
    ],
)
def test_restart_retires_every_exact_lineage_mismatch(
    database_path: Path, changed_field: str, expected_reason: str
) -> None:
    durable = _state(
        phase="evaluating",
        calibration_manifest=_manifest(),
        evaluation_epoch=2,
        evaluation_round=1,
        consecutive_wins=1,
        last_decision_id="decision-2-1",
        last_evidence_id="challenger-round-2-1",
    )
    create_model_challenger(durable, database_path=database_path)
    changes: dict[str, object] = {}
    if changed_field == "incumbent":
        changes["incumbent"] = _descriptor("replacement-incumbent", theta=55.0, candidate_generation=4)
    elif changed_field == "candidate":
        changes["candidate"] = _descriptor("replacement-candidate", theta=70.0, candidate_generation=5)
    elif changed_field == "configuration":
        changes["controller_configuration_digest"] = "a" * 64
    elif changed_field == "corpus":
        changes["fit_corpus"] = _corpus("replacement")
    else:
        changes["calibration_manifest"] = _manifest("replacement")

    assert (
        _recover_exact(
            durable,
            database_path,
            recovered_ms=6_000,
            **changes,
        )
        is None
    )

    retired = read_model_challenger(database_path=database_path)
    assert retired is not None
    assert retired.phase == "retired"
    assert retired.revision == durable.revision + 1
    assert retired.retirement_reason == expected_reason
    assert retired.retired_ms == 6_000
    assert retired.origin == durable.origin
    assert retired.policy == durable.policy
    assert retired.controller_configuration_digest == durable.controller_configuration_digest
    assert retired.fit_preparation == durable.fit_preparation
    assert retired.incumbent == durable.incumbent
    assert retired.candidate == durable.candidate
    assert retired.fit_corpus == durable.fit_corpus
    assert retired.fit_lineage == durable.fit_lineage
    assert retired.calibration_manifest == durable.calibration_manifest


@pytest.mark.parametrize("reason", ["activation-rollback", "safe-fallback"])
def test_rollback_and_fallback_retirement_cannot_resume(database_path: Path, reason: str) -> None:
    durable = _state(
        phase="evaluating",
        evaluation_epoch=1,
        evaluation_round=1,
        consecutive_wins=1,
        last_decision_id="decision-1-1",
        last_evidence_id="challenger-round-1-1",
    )
    create_model_challenger(durable, database_path=database_path)
    retired = retire_model_challenger(
        expected_revision=durable.revision,
        reason=reason,
        retired_ms=6_100,
        database_path=database_path,
    )

    assert _recover_exact(retired, database_path, recovered_ms=6_200) is None
    assert read_model_challenger(database_path=database_path) == retired
    assert retired.retirement_reason == reason


def test_corrupt_durable_state_retires_instead_of_resuming(
    database_path: Path,
) -> None:
    durable = _state(
        phase="evaluating",
        evaluation_round=1,
        consecutive_wins=1,
        last_decision_id="decision-0-1",
        last_evidence_id="challenger-round-0-1",
    )
    create_model_challenger(durable, database_path=database_path)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("UPDATE model_challenger_state SET phase='qualified' WHERE singleton=1")
        connection.commit()
    finally:
        connection.close()

    assert _recover_exact(durable, database_path, recovered_ms=6_300) is None
    retired = read_model_challenger(database_path=database_path)
    assert retired is not None
    assert retired.phase == "retired"
    assert retired.retirement_reason == "corrupt-challenger"
    assert retired.retired_ms == 6_300


def test_unreadable_durable_state_is_removed_during_recovery(
    database_path: Path,
) -> None:
    durable = _state()
    create_model_challenger(durable, database_path=database_path)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("UPDATE model_challenger_state SET state_json='{}' WHERE singleton=1")
        connection.commit()
    finally:
        connection.close()

    assert _recover_exact(durable, database_path, recovered_ms=6_400) is None
    assert read_model_challenger(database_path=database_path) is None


def test_prepared_crash_is_aborted_before_linked_challenger_recovery(
    database_path: Path,
) -> None:
    qualified = _qualified()
    create_model_challenger(qualified, database_path=database_path)
    prepared = PreparedActivationRecord.prepared(
        timestamp_ms=7_000,
        incumbent=qualified.incumbent,
        candidate=qualified.candidate,
        origin=qualified.origin,
        policy=qualified.policy,
        decision_id=qualified.last_decision_id,
    )
    activating = prepare_model_challenger_activation(
        expected_revision=qualified.revision,
        activation=prepared,
        database_path=database_path,
    )

    assert _recover_exact(activating, database_path, recovered_ms=7_100) is None

    activation = read_model_activation(database_path=database_path)
    challenger = read_model_challenger(database_path=database_path)
    assert activation is not None
    assert activation.phase == ActivationPhase.ABORTED.value
    assert activation.transaction_id == prepared.transaction_id
    assert activation.reason is not None and "prepared" in activation.reason
    assert challenger is not None
    assert challenger.phase == "retired"
    assert challenger.activation_transaction_id == prepared.transaction_id
    assert challenger.retirement_reason is not None and "prepared" in challenger.retirement_reason
    assert challenger.retired_ms == 7_100
