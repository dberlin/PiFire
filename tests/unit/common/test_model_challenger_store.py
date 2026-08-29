"""Behavioral contract for the singleton durable model challenger authority."""

from __future__ import annotations

import sqlite3
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from common import datastore
from common.learning_trajectory import (
    FitCorpusIdentity,
    FitCorpusSlice,
    ModelFitLineage,
    canonical_trajectory_digest,
    trajectory_json_value,
)
from common.model_evidence import (
    MODEL_EVIDENCE_SCHEMA_VERSION,
    ChallengerRoundEvidence,
    EvidenceKind,
    ModelEvidenceRecord,
)
from common.persistence.model_challenger import (
    ModelChallengerConflictError,
    ModelChallengerState,
    compare_and_swap_model_challenger,
    complete_model_challenger_round,
    create_model_challenger,
    prepare_model_challenger_activation,
    qualify_model_challenger,
    read_model_challenger,
    retire_model_challenger,
)
from common.persistence.model_evidence import read_model_activation, read_model_evidence
from controller.model_learning.activation import (
    ActivationPhase,
    GreyControlPairDescriptor,
    PreparedActivationRecord,
    canonical_snapshot_digest,
)
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin

_REQUIRED_HORIZONS = (3, 15, 45, 90, 180)


def _digest(label: str) -> str:
    return sha256(label.encode()).hexdigest()


def _descriptor(label: str, *, theta: float, candidate_generation: int) -> GreyControlPairDescriptor:
    configuration = {
        "schema": "pifire-grey-box-model/v4",
        "n_delay": 8,
        "parameters": {
            "C_c": 320.0,
            "K_Q": 350.0,
            "theta": theta,
            "h_amb": 0.5,
            "T_amb": 20.0,
            "sigma": 1.4e-9,
        },
        "label": label,
    }
    return GreyControlPairDescriptor(
        model_digest=canonical_snapshot_digest(configuration),
        configuration=configuration,
        estimator_kind="ekf",
        solver_kind="acados-grey",
        candidate_generation=candidate_generation,
        role_generation=4,
    )


def _corpus(label: str = "challenger") -> FitCorpusIdentity:
    corpus_slice = FitCorpusSlice(
        segment_id=f"segment-{label}",
        through_ordinal=2,
        prefix_digest=_digest(f"prefix-{label}"),
        pre_roll_count=1,
        scored_count=2,
    )
    payload = {
        "schema_version": 1,
        "corpus_revision": 7,
        "fit_partition_digest": _digest(f"partition-{label}"),
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
        schema_version=payload["schema_version"],
        corpus_revision=payload["corpus_revision"],
        fit_partition_digest=payload["fit_partition_digest"],
        slices=(corpus_slice,),
        corpus_digest=canonical_trajectory_digest(payload),
    )


def _manifest(label: str = "calibration") -> dict[str, object]:
    return {
        "command_revision": 11,
        "session_id": f"session-{label}",
        "completed_stages": ["low", "middle", "high", "coast"],
        "stage_evidence_ids": [
            f"{label}-low",
            f"{label}-middle",
            f"{label}-high",
            f"{label}-coast",
        ],
    }


def _state(
    *,
    revision: int = 0,
    phase: str = "built",
    origin: CandidateOrigin = CandidateOrigin.PASSIVE_ONLINE,
    policy: ActivationPolicy = ActivationPolicy.CAUSAL_AUTO,
    corpus: FitCorpusIdentity | None = None,
    incumbent: GreyControlPairDescriptor | None = None,
    candidate: GreyControlPairDescriptor | None = None,
    controller_configuration_digest: str | None = None,
    calibration_manifest: dict[str, object] | None = None,
    evaluation_epoch: int = 0,
    evaluation_round: int = 0,
    consecutive_wins: int = 0,
    last_decision_id: str | None = None,
    last_evidence_id: str | None = None,
    activation_transaction_id: str | None = None,
    retirement_reason: str | None = None,
    retired_ms: int | None = None,
    fit_preparation: dict[str, object] | None = None,
) -> ModelChallengerState:
    fit_corpus = _corpus() if corpus is None else corpus
    active = _descriptor("incumbent", theta=50.0, candidate_generation=4) if incumbent is None else incumbent
    challenger = _descriptor("candidate", theta=65.0, candidate_generation=5) if candidate is None else candidate
    lineage = ModelFitLineage(
        request_id="fit-challenger-1",
        parent_incumbent_digest=active.model_digest,
        parent_incumbent_generation=active.candidate_generation,
        candidate_generation=challenger.candidate_generation,
        fit_corpus=fit_corpus,
        fit_corpus_digest=fit_corpus.corpus_digest,
        trigger_origin=getattr(origin, "value", origin),
        result_status="succeeded",
        candidate_digest=challenger.model_digest,
    )
    preparation = fit_preparation or {
        "request_id": lineage.request_id,
        "accepted": True,
        "candidate_digest": challenger.model_digest,
        "native_build": "passed",
        "dry_solve": "passed",
        "target_timing": {"target": "pi", "p99_ms": 4.0, "limit_ms": 5.0},
    }
    return ModelChallengerState(
        schema_version=1,
        challenger_id="challenger-1",
        revision=revision,
        phase=phase,
        origin=origin,
        policy=policy,
        fit_corpus=fit_corpus,
        fit_lineage=lineage,
        fit_preparation=preparation,
        controller_configuration_digest=(
            _digest("controller-configuration")
            if controller_configuration_digest is None
            else controller_configuration_digest
        ),
        incumbent=active,
        candidate=challenger,
        calibration_manifest=calibration_manifest,
        evaluation_epoch=evaluation_epoch,
        evaluation_round=evaluation_round,
        consecutive_wins=consecutive_wins,
        required_wins=2,
        last_decision_id=last_decision_id,
        last_evidence_id=last_evidence_id,
        activation_transaction_id=activation_transaction_id,
        retirement_reason=retirement_reason,
        created_ms=1_000,
        updated_ms=1_000 + revision,
        retired_ms=retired_ms,
    )


def _round_evidence(state: ModelChallengerState, *, round_number: int) -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=f"challenger-round-{state.evaluation_epoch}-{round_number}",
        kind=EvidenceKind.CHALLENGER_ROUND,
        session_id=f"evaluation-epoch-{state.evaluation_epoch}",
        cook_id="cook-challenger",
        timestamp_ms=2_000 + round_number,
        role_generation=state.incumbent.role_generation,
        model_digest=state.candidate.model_digest,
        provenance_digest=state.incumbent.model_digest,
        payload=ChallengerRoundEvidence(
            challenger_id=state.challenger_id,
            evaluation_epoch=state.evaluation_epoch,
            evaluation_round=round_number,
            decision_id=f"decision-{state.evaluation_epoch}-{round_number}",
            accepted=True,
            required_horizons=_REQUIRED_HORIZONS,
            completed_horizons=_REQUIRED_HORIZONS,
            incumbent_digest=state.incumbent.model_digest,
            candidate_digest=state.candidate.model_digest,
        ),
    )


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "model-challenger.sqlite"


def test_state_is_strict_frozen_and_owns_nested_json() -> None:
    preparation = {
        "request_id": "fit-challenger-1",
        "accepted": True,
        "candidate_digest": _descriptor("candidate", theta=65.0, candidate_generation=5).model_digest,
        "native_build": "passed",
        "dry_solve": "passed",
        "target_timing": {"target": "pi", "p99_ms": 4.0, "limit_ms": 5.0},
    }
    manifest = _manifest()
    state = _state(
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        policy=ActivationPolicy.CAUSAL_AUTO,
        fit_preparation=preparation,
        calibration_manifest=manifest,
    )

    preparation["target_timing"]["p99_ms"] = 999.0  # type: ignore[index]
    manifest["completed_stages"].clear()  # type: ignore[union-attr]

    assert trajectory_json_value(state.fit_preparation)["target_timing"] == {
        "target": "pi",
        "p99_ms": 4.0,
        "limit_ms": 5.0,
    }
    assert trajectory_json_value(state.calibration_manifest)["completed_stages"] == [
        "low",
        "middle",
        "high",
        "coast",
    ]
    with pytest.raises(FrozenInstanceError):
        state.revision = 99  # type: ignore[misc]


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"revision": True}, "revision"),
        ({"phase": "unknown"}, "phase"),
        ({"origin": "passive-online"}, "origin"),
        ({"policy": ActivationPolicy.PASSIVE_AUTO}, "origin-policy"),
        ({"controller_configuration_digest": "bad"}, "configuration"),
        ({"evaluation_round": 1}, "decision"),
        ({"consecutive_wins": 3}, "wins"),
        ({"last_decision_id": "decision-only"}, "evidence"),
        (
            {
                "phase": "activating",
                "evaluation_round": 2,
                "consecutive_wins": 2,
                "last_decision_id": "decision-0-2",
                "last_evidence_id": "challenger-round-0-2",
            },
            "activation",
        ),
        ({"phase": "retired"}, "retirement"),
        ({"retirement_reason": "not-retired"}, "retirement"),
    ],
)
def test_state_rejects_invalid_types_and_invariants(changes: dict[str, object], reason: str) -> None:
    with pytest.raises((TypeError, ValueError, ValidationError), match=reason):
        _state(**changes)  # type: ignore[arg-type]


def test_state_rejects_fit_corpus_lineage_disagreement() -> None:
    with pytest.raises((ValueError, ValidationError), match="corpus"):
        replace(_state(), fit_corpus=_corpus("changed"))


def test_create_read_and_revision_cas_are_exact_idempotent_and_conflict_closed(
    database_path: Path,
) -> None:
    built = _state()
    assert create_model_challenger(built, database_path=database_path) == built
    assert create_model_challenger(built, database_path=database_path) == built
    assert read_model_challenger(database_path=database_path) == built

    with pytest.raises(ModelChallengerConflictError):
        create_model_challenger(replace(built, challenger_id="other"), database_path=database_path)

    evaluating = replace(built, revision=1, phase="evaluating", updated_ms=1_001)
    assert (
        compare_and_swap_model_challenger(
            expected_revision=0,
            replacement=evaluating,
            database_path=database_path,
        )
        == evaluating
    )
    assert (
        compare_and_swap_model_challenger(
            expected_revision=0,
            replacement=evaluating,
            database_path=database_path,
        )
        == evaluating
    )

    with pytest.raises(ModelChallengerConflictError):
        compare_and_swap_model_challenger(
            expected_revision=0,
            replacement=replace(evaluating, updated_ms=9_999),
            database_path=database_path,
        )
    assert read_model_challenger(database_path=database_path) == evaluating


def test_complete_round_appends_schema_v4_evidence_and_progress_atomically(
    database_path: Path,
) -> None:
    assert MODEL_EVIDENCE_SCHEMA_VERSION == 4
    evaluating = _state(phase="evaluating")
    create_model_challenger(evaluating, database_path=database_path)
    evidence = _round_evidence(evaluating, round_number=1)

    progressed = complete_model_challenger_round(
        expected_revision=0,
        evidence=evidence,
        database_path=database_path,
    )

    assert progressed.revision == 1
    assert progressed.evaluation_round == 1
    assert progressed.consecutive_wins == 1
    assert progressed.last_decision_id == evidence.payload.decision_id
    assert progressed.last_evidence_id == evidence.evidence_id
    assert read_model_evidence(database_path=database_path) == [evidence]
    assert (
        complete_model_challenger_round(
            expected_revision=0,
            evidence=evidence,
            database_path=database_path,
        )
        == progressed
    )


@pytest.mark.parametrize("failing_table", ["model_evidence", "model_challenger_state"])
def test_complete_round_rolls_back_both_writes_on_either_failure(database_path: Path, failing_table: str) -> None:
    evaluating = _state(phase="evaluating")
    create_model_challenger(evaluating, database_path=database_path)
    event = "INSERT" if failing_table == "model_evidence" else "UPDATE"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            f"""CREATE TRIGGER fail_round_{failing_table}
            BEFORE {event} ON {failing_table}
            BEGIN SELECT RAISE(ABORT, 'injected round failure'); END"""
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(sqlite3.IntegrityError, match="injected round failure"):
        complete_model_challenger_round(
            expected_revision=0,
            evidence=_round_evidence(evaluating, round_number=1),
            database_path=database_path,
        )
    assert read_model_challenger(database_path=database_path) == evaluating
    assert read_model_evidence(database_path=database_path) == []


def _qualified() -> ModelChallengerState:
    return _state(
        phase="qualified",
        evaluation_round=2,
        consecutive_wins=2,
        last_decision_id="decision-0-2",
        last_evidence_id="challenger-round-0-2",
    )


def test_all_phases_and_qualified_to_prepared_atomic_cas(database_path: Path) -> None:
    built = create_model_challenger(_state(), database_path=database_path)
    evaluating = compare_and_swap_model_challenger(
        expected_revision=0,
        replacement=replace(built, revision=1, phase="evaluating", updated_ms=1_001),
        database_path=database_path,
    )
    first = complete_model_challenger_round(
        expected_revision=1,
        evidence=_round_evidence(evaluating, round_number=1),
        database_path=database_path,
    )
    second = complete_model_challenger_round(
        expected_revision=first.revision,
        evidence=_round_evidence(first, round_number=2),
        database_path=database_path,
    )
    qualified = qualify_model_challenger(
        expected_revision=second.revision,
        qualified_ms=3_000,
        database_path=database_path,
    )
    activation = PreparedActivationRecord.prepared(
        timestamp_ms=3_001,
        incumbent=qualified.incumbent,
        candidate=qualified.candidate,
        origin=qualified.origin,
        policy=qualified.policy,
        decision_id=qualified.last_decision_id,
    )
    activating = prepare_model_challenger_activation(
        expected_revision=qualified.revision,
        activation=activation,
        database_path=database_path,
    )
    durable_activation = read_model_activation(database_path=database_path)
    retired = retire_model_challenger(
        expected_revision=activating.revision,
        reason="activation-aborted",
        retired_ms=3_002,
        database_path=database_path,
    )

    assert [built.phase, evaluating.phase, qualified.phase, activating.phase, retired.phase] == [
        "built",
        "evaluating",
        "qualified",
        "activating",
        "retired",
    ]
    assert durable_activation is not None
    assert durable_activation.phase == ActivationPhase.PREPARED.value
    assert durable_activation.transaction_id == activation.transaction_id
    assert durable_activation.incumbent_pair == qualified.incumbent
    assert durable_activation.candidate_pair == qualified.candidate
    assert activating.activation_transaction_id == activation.transaction_id
    assert retired.retirement_reason == "activation-aborted"


@pytest.mark.parametrize("failing_table", ["model_activation_state", "model_challenger_state"])
def test_qualified_to_prepared_rolls_back_both_authorities_on_either_failure(
    database_path: Path, failing_table: str
) -> None:
    qualified = _qualified()
    create_model_challenger(qualified, database_path=database_path)
    activation = PreparedActivationRecord.prepared(
        timestamp_ms=4_000,
        incumbent=qualified.incumbent,
        candidate=qualified.candidate,
        origin=qualified.origin,
        policy=qualified.policy,
        decision_id=qualified.last_decision_id,
    )
    event = "INSERT" if failing_table == "model_activation_state" else "UPDATE"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            f"""CREATE TRIGGER fail_prepare_{failing_table}
            BEFORE {event} ON {failing_table}
            BEGIN SELECT RAISE(ABORT, 'injected prepare failure'); END"""
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(sqlite3.IntegrityError, match="injected prepare failure"):
        prepare_model_challenger_activation(
            expected_revision=0,
            activation=activation,
            database_path=database_path,
        )
    assert read_model_challenger(database_path=database_path) == qualified
    assert read_model_activation(database_path=database_path) is None


def _seed_v9_challenger_migration_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(datastore.SCHEMA + datastore._queue_ddl())
        for statement in datastore._LEARNING_TRAJECTORY_V9_DDL:
            connection.execute(statement)
        connection.execute(
            """
            UPDATE learning_trajectory_corpus
            SET corpus_revision=9, segment_count=1, scored_count=1
            WHERE singleton=1
            """
        )
        connection.execute(
            """
            INSERT INTO learning_trajectory_segment(
                segment_id, state, fit_partition_digest, header_json,
                start_monotonic_ms, end_monotonic_ms, start_wall_ms, end_wall_ms,
                start_sequence, end_sequence, hold_entry_json, hold_entry_revision,
                pre_roll_count, scored_count, next_ordinal, rolling_digest,
                final_digest, content_digest, begin_content_digest,
                roll_successor_segment_id, created_corpus_revision,
                updated_corpus_revision, finalized_corpus_revision,
                pre_roll_end_reason, terminal_break_reason, source_trace_digest,
                source_schema_version, source_row_digest
            ) VALUES(
                'segment-v9', 'finalized', ?, '{"schema_version":9}',
                100, 120, 1000, 1020, 7, 7, NULL, NULL,
                0, 1, 1, ?, ?, ?, ?, NULL, 9, 9, 9,
                'first-scored-frame', 'stop', ?, 7, ?
            )
            """,
            (
                _digest("v9-partition"),
                _digest("v9-rolling"),
                _digest("v9-final"),
                _digest("v9-content"),
                _digest("v9-begin"),
                _digest("v9-trace"),
                _digest("v9-source-row"),
            ),
        )
        connection.execute(
            """
            INSERT INTO learning_trajectory_frame(
                segment_id, ordinal, kind, payload_schema_version,
                interval_identity, canonical_json, frame_digest,
                created_corpus_revision
            ) VALUES('segment-v9', 0, 'scored', 2, 'interval-v9',
                     '{"schema_version":2}', ?, 9)
            """,
            (_digest("v9-frame"),),
        )
        connection.execute(
            """
            INSERT INTO learning_fit_run(
                request_id, status, fit_partition_digest, corpus_revision,
                corpus_digest, manifest_json, parent_incumbent_digest,
                parent_incumbent_generation, candidate_generation,
                trigger_origin, candidate_digest, result_error, created_ms,
                started_ms, completed_ms
            ) VALUES(
                'fit-v9', 'succeeded', ?, 9, ?, '{"legacy":true}', ?,
                4, 5, 'passive-online', ?, NULL, 1000, 1001, 1002
            )
            """,
            (
                _digest("v9-partition"),
                _digest("v9-corpus"),
                _digest("v9-incumbent"),
                _digest("v9-candidate"),
            ),
        )
        connection.execute(
            """
            INSERT INTO model_evidence(
                evidence_id, session_id, cook_id, timestamp_ms, kind,
                role_generation, model_digest, provenance_digest,
                schema_version, payload
            ) VALUES(
                'evidence-v9', 'session-v9', 'cook-v9', 1003,
                'fit_lifecycle', 4, ?, ?, 3, '{"legacy":true}'
            )
            """,
            (_digest("v9-candidate"), _digest("v9-incumbent")),
        )
        connection.execute(
            """
            INSERT INTO model_activation_state(
                singleton, active_snapshot_json, rollback_snapshot_json,
                evidence_decision_id, controller_configuration_digest,
                role_generation, phase
            ) VALUES(
                1, '{"theta":50.0}', '{"theta":49.0}', 'decision-v9', ?, 4,
                'active'
            )
            """,
            (_digest("v9-controller"),),
        )
        connection.execute("INSERT INTO kv(key, value) VALUES('preserved-v9', '{\"value\":9}')")
        connection.execute("PRAGMA user_version=9")
        connection.commit()
    finally:
        connection.close()


def _preserved_v9_rows(path: Path) -> dict[str, list[tuple[object, ...]]]:
    connection = sqlite3.connect(path)
    try:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in (
                "kv",
                "learning_trajectory_corpus",
                "learning_trajectory_segment",
                "learning_trajectory_frame",
                "learning_fit_run",
                "model_evidence",
                "model_activation_state",
            )
        }
    finally:
        connection.close()


def test_schema_v10_migration_is_additive_and_preserves_every_v9_authority_row(
    database_path: Path,
) -> None:
    _seed_v9_challenger_migration_database(database_path)
    preserved = _preserved_v9_rows(database_path)
    datastore._reset_for_tests(str(database_path))
    try:
        connection = datastore.connection()

        assert datastore.DB_SCHEMA_VERSION == 10
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 10
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='model_challenger_state'"
        ).fetchone() == ("model_challenger_state",)
        assert connection.execute("SELECT COUNT(*) FROM model_challenger_state").fetchone() == (0,)
        assert _preserved_v9_rows(database_path) == preserved
    finally:
        datastore._reset_for_tests(None)


def test_schema_v10_migration_rolls_back_ddl_and_version_bump_together(
    database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_v9_challenger_migration_database(database_path)
    preserved = _preserved_v9_rows(database_path)
    datastore._reset_for_tests(str(database_path))
    original_connect = datastore.sqlite3.connect

    class _CrashingConnection(sqlite3.Connection):
        def execute(self, sql: str, *args: Any, **kwargs: Any):
            normalized = "".join(sql.lower().split())
            if normalized == "pragmauser_version=10":
                raise RuntimeError("injected v10 migration crash")
            return super().execute(sql, *args, **kwargs)

    def crashing_connect(*args: Any, **kwargs: Any):
        kwargs["factory"] = _CrashingConnection
        return original_connect(*args, **kwargs)

    try:
        with (
            monkeypatch.context() as patch,
            pytest.raises(RuntimeError, match="injected v10 migration crash"),
        ):
            patch.setattr(datastore.sqlite3, "connect", crashing_connect)
            datastore.connection()

        check = sqlite3.connect(database_path)
        try:
            assert check.execute("PRAGMA user_version").fetchone()[0] == 9
            assert (
                check.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='model_challenger_state'"
                ).fetchone()
                is None
            )
        finally:
            check.close()
        assert _preserved_v9_rows(database_path) == preserved

        datastore._reset_for_tests(str(database_path))
        assert datastore.connection().execute("PRAGMA user_version").fetchone()[0] == 10
    finally:
        datastore._reset_for_tests(None)
