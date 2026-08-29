"""Controller policy for selecting and migrating durable MPC learning authority."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, replace

from common import datastore
from common.learning_trajectory import (
    FitCorpusIdentity,
    FitCorpusSlice,
    ModelFitLineage,
    canonical_trajectory_digest,
)
from common.model_evidence import (
    EvidenceKind,
    ModelEvidenceRecord,
    SchemaInvalidationEvidence,
)
from common.persistence.model_challenger import (
    ModelChallengerState,
    _CorruptModelChallenger,
)
from common.persistence.model_challenger import (
    _read_in_transaction as _read_challenger_in_transaction,
)
from common.persistence.model_challenger import (
    _state_from_dict as _challenger_state_from_dict,
)
from common.persistence.model_challenger import (
    _write_state as _write_challenger_state,
)
from common.persistence.model_evidence import (
    ModelActivationState,
    _activation_state_row,
    _model_evidence_connection,
)
from controller.model_learning.activation import GreyControlPairDescriptor
from controller.model_learning.contracts import (
    ActivationPolicy,
    CandidateOrigin,
    activation_policy_for_origin,
)


def _exact_calibration_manifest(
    value: object,
    *,
    session_id: str | None = None,
) -> dict[str, object] | None:
    if not isinstance(value, dict) or set(value) != {
        "command_revision",
        "session_id",
        "completed_stages",
        "stage_evidence_ids",
    }:
        return None
    command_revision = value.get("command_revision")
    manifest_session = value.get("session_id")
    completed_stages = value.get("completed_stages")
    evidence_ids = value.get("stage_evidence_ids")
    if (
        not isinstance(command_revision, int)
        or isinstance(command_revision, bool)
        or command_revision <= 0
        or not isinstance(manifest_session, str)
        or not manifest_session.strip()
        or (session_id is not None and manifest_session != session_id)
        or not isinstance(completed_stages, (list, tuple))
        or tuple(completed_stages) != ("low", "middle", "high", "coast")
        or not isinstance(evidence_ids, (list, tuple))
        or len(evidence_ids) != 4
        or len(set(evidence_ids)) != 4
        or not all(isinstance(item, str) and item.strip() for item in evidence_ids)
    ):
        return None
    return {
        "command_revision": command_revision,
        "session_id": manifest_session,
        "completed_stages": list(completed_stages),
        "stage_evidence_ids": list(evidence_ids),
    }


def _normalize_durable_challenger_policy(
    connection: sqlite3.Connection,
) -> None:
    row = connection.execute("SELECT state_json FROM model_challenger_state WHERE singleton=1").fetchone()
    if row is None:
        return
    try:
        decoded = json.loads(row[0])
        if not isinstance(decoded, dict):
            return
        raw_origin = decoded.get("origin")
        stored_policy = decoded.get("policy")
        already_retired = decoded.get("phase") == "retired"
    except TypeError, ValueError, json.JSONDecodeError:
        return

    historical_policy = {
        "passive-online": "passive-auto",
        "operator-calibration": "operator-reviewed",
    }.get(raw_origin)
    retired_origin = raw_origin == "cook-refit" and stored_policy == "cook-refit"
    if retired_origin:
        origin = CandidateOrigin.PASSIVE_ONLINE
    else:
        if historical_policy is None:
            return
        try:
            origin = CandidateOrigin(raw_origin)
        except TypeError, ValueError:
            return
        current_policy = activation_policy_for_origin(origin)
        if stored_policy not in {current_policy.value, historical_policy}:
            return

    decoded["origin"] = origin.value
    decoded["policy"] = activation_policy_for_origin(origin).value
    lineage = decoded.get("fit_lineage")
    if retired_origin and isinstance(lineage, dict):
        lineage = dict(lineage)
        lineage["trigger_origin"] = origin.value
        decoded["fit_lineage"] = lineage
        decoded["phase"] = "retired"
        decoded["activation_transaction_id"] = None
        decoded["retirement_reason"] = "retired-origin:cook-refit"
        decoded["retired_ms"] = decoded.get("updated_ms")

    preparation_changed = False
    preparation = decoded.get("fit_preparation")
    corpus = decoded.get("fit_corpus")
    if isinstance(preparation, dict) and isinstance(corpus, dict) and "window" in preparation:
        preparation = dict(preparation)
        preparation.pop("window", None)
        preparation["fit_corpus_digest"] = corpus.get("corpus_digest")
        decoded["fit_preparation"] = preparation
        preparation_changed = True

    try:
        normalized = _challenger_state_from_dict(decoded)
    except TypeError, ValueError:
        return
    incomplete_operator = (
        not already_retired
        and origin is CandidateOrigin.OPERATOR_CALIBRATION
        and _exact_calibration_manifest(decoded.get("calibration_manifest")) is None
    )
    policy_changed = stored_policy != activation_policy_for_origin(origin).value
    if not retired_origin and not incomplete_operator and not policy_changed and not preparation_changed:
        return
    replacement = replace(
        normalized,
        revision=normalized.revision + 1,
        **(
            {
                "phase": "retired",
                "retirement_reason": "calibration-manifest",
                "retired_ms": normalized.updated_ms,
            }
            if incomplete_operator and not retired_origin
            else {}
        ),
    )
    _write_challenger_state(
        connection,
        replacement,
        insert=False,
        expected_revision=normalized.revision,
    )


def _normalize_activation_policy(
    connection: sqlite3.Connection,
    state: ModelActivationState,
) -> ModelActivationState:
    historical_policy = {
        "passive-online": "passive-auto",
        "operator-calibration": "operator-reviewed",
    }.get(state.origin)
    if state.origin == "cook-refit" and state.policy == "cook-refit":
        connection.execute("UPDATE model_activation_state SET origin=NULL, policy=NULL WHERE singleton=1")
        return replace(state, origin=None, policy=None)
    if historical_policy is None or state.policy != historical_policy:
        return state
    origin = CandidateOrigin(state.origin)
    current_policy = activation_policy_for_origin(origin)
    connection.execute(
        "UPDATE model_activation_state SET policy=? WHERE singleton=1",
        (current_policy.value,),
    )
    return replace(state, policy=current_policy.value)


def _controller_snapshot_for_migration(snapshot: object) -> object:
    if not isinstance(snapshot, dict) or snapshot.get("version") not in {4, 5}:
        return snapshot
    normalized = dict(snapshot)
    normalized.pop("calibration_manifest", None)
    for key in ("challenger", "evidence"):
        value = normalized.get(key)
        if isinstance(value, dict) and "calibration_manifest" in value:
            value = dict(value)
            value.pop("calibration_manifest", None)
            normalized[key] = value
    historical_policy = {
        "passive-online": "passive-auto",
        "operator-calibration": "operator-reviewed",
    }.get(normalized.get("origin"))
    if normalized.get("policy") == historical_policy and historical_policy is not None:
        normalized["policy"] = ActivationPolicy.CAUSAL_AUTO.value
    elif normalized.get("origin") == "cook-refit" and normalized.get("policy") == "cook-refit":
        normalized["origin"] = None
        normalized["policy"] = None
        normalized["activation"] = {
            "phase": "aborted",
            "pending_persistence": False,
            "pending_swap": False,
        }
        if snapshot.get("version") == 5:
            normalized["challenger_authority"] = None
    return normalized


@dataclass(frozen=True, slots=True)
class GreyLearningMigrationResult:
    """The authority selected by one atomic grey-only migration."""

    snapshot: dict[str, object]
    source: str
    reason: str | None


def _migration_json_blob(connection: sqlite3.Connection, key: str):
    row = connection.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except TypeError, ValueError, json.JSONDecodeError:
        return None


def _migration_authority_snapshot(value, *, current_only):
    from controller.mpc_snapshot import GreySnapshotInvalid, migrate_grey_learning_snapshot

    if not isinstance(value, dict) or value.get("model_kind") != "grey-box":
        return None
    snapshot = value.get("snapshot")
    if current_only and (
        not isinstance(snapshot, dict)
        or (snapshot.get("version"), value.get("model_schema")) not in {(4, 4), (5, 5), (6, 6)}
    ):
        return None
    try:
        return migrate_grey_learning_snapshot(snapshot)
    except GreySnapshotInvalid:
        return None


def _legacy_v4_challenger_state(
    snapshot: object,
) -> ModelChallengerState | None:
    """Import only one fully linked, review-ready legacy candidate."""

    if not isinstance(snapshot, dict) or snapshot.get("version") != 4:
        return None
    challenger = snapshot.get("challenger")
    window = snapshot.get("window")
    active_pair_value = snapshot.get("active_pair")
    candidate_pair_value = snapshot.get("candidate_pair")
    identities = snapshot.get("identities")
    evidence = snapshot.get("evidence")
    activation = snapshot.get("activation")
    cook_refit = snapshot.get("cook_refit")
    if not all(
        isinstance(value, dict)
        for value in (
            challenger,
            window,
            active_pair_value,
            candidate_pair_value,
            identities,
            evidence,
            activation,
            cook_refit,
        )
    ):
        return None
    assert isinstance(challenger, dict)
    assert isinstance(window, dict)
    assert isinstance(active_pair_value, dict)
    assert isinstance(candidate_pair_value, dict)
    assert isinstance(identities, dict)
    assert isinstance(evidence, dict)
    assert isinstance(activation, dict)
    assert isinstance(cook_refit, dict)
    if activation.get("phase") == "prepared" or cook_refit.get("latest") != "ready-for-review":
        return None
    raw_origin = snapshot.get("origin")
    expected_policy = {
        "passive-online": "passive-auto",
        "operator-calibration": "operator-reviewed",
    }.get(raw_origin)
    if expected_policy is None or snapshot.get("policy") != expected_policy:
        return None
    try:
        from controller.mpc_factory import MpcPairFactory

        incumbent = MpcPairFactory.migrate_legacy_descriptor(GreyControlPairDescriptor.from_dict(active_pair_value))
        candidate = MpcPairFactory.migrate_legacy_descriptor(GreyControlPairDescriptor.from_dict(candidate_pair_value))
        origin = CandidateOrigin(raw_origin)
    except KeyError, TypeError, ValueError:
        return None
    if (
        identities.get("active_digest") != incumbent.model_digest
        or identities.get("active_generation") != incumbent.role_generation
        or identities.get("candidate_digest") != candidate.model_digest
        or identities.get("candidate_generation") != candidate.candidate_generation
        or window.get("incumbent_digest") != incumbent.model_digest
        or window.get("role_generation") != incumbent.role_generation
    ):
        return None
    active_model = snapshot.get("active")
    candidate_parameters = challenger.get("parameters")
    active_parameters = active_model.get("parameters") if isinstance(active_model, dict) else None
    if not isinstance(active_parameters, dict) or not isinstance(candidate_parameters, dict):
        return None

    def descriptor_parameter(descriptor: GreyControlPairDescriptor, name: str) -> object:
        return descriptor.configuration.get("delay_states" if name == "n_delay" else name)

    if any(descriptor_parameter(incumbent, name) != value for name, value in active_parameters.items()) or any(
        descriptor_parameter(candidate, name) != value for name, value in candidate_parameters.items()
    ):
        return None
    decision_id = evidence.get("confidence_decision_id")
    configuration_digest = window.get("configuration_digest")
    session_id = window.get("session_id")
    cook_id = window.get("cook_id")
    first_sequence = window.get("first_observation_sequence")
    last_sequence = window.get("last_observation_sequence")
    if (
        not isinstance(decision_id, str)
        or not decision_id.strip()
        or not isinstance(configuration_digest, str)
        or len(configuration_digest) != 64
        or any(character not in "0123456789abcdef" for character in configuration_digest)
        or not isinstance(session_id, str)
        or not session_id.strip()
        or not isinstance(cook_id, str)
        or not cook_id.strip()
        or isinstance(first_sequence, bool)
        or not isinstance(first_sequence, int)
        or isinstance(last_sequence, bool)
        or not isinstance(last_sequence, int)
        or first_sequence < 0
        or last_sequence < first_sequence
    ):
        return None
    calibration_manifest = None
    if origin is CandidateOrigin.OPERATOR_CALIBRATION:
        raw_manifest = challenger.get(
            "calibration_manifest",
            evidence.get(
                "calibration_manifest",
                snapshot.get("calibration_manifest"),
            ),
        )
        calibration_manifest = _exact_calibration_manifest(
            raw_manifest,
            session_id=session_id,
        )
        if calibration_manifest is None:
            return None
    scored_count = last_sequence - first_sequence + 1
    prefix_digest = canonical_trajectory_digest(window)
    corpus_slice = FitCorpusSlice(
        segment_id=f"legacy-v4:{session_id}:{cook_id}",
        through_ordinal=scored_count - 1,
        prefix_digest=prefix_digest,
        pre_roll_count=0,
        scored_count=scored_count,
    )
    corpus_payload = {
        "schema_version": 1,
        "corpus_revision": 0,
        "fit_partition_digest": configuration_digest,
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
    corpus = FitCorpusIdentity(
        schema_version=1,
        corpus_revision=0,
        fit_partition_digest=configuration_digest,
        slices=(corpus_slice,),
        corpus_digest=canonical_trajectory_digest(corpus_payload),
    )
    identity_bytes = json.dumps(
        {
            "candidate": candidate.model_digest,
            "incumbent": incumbent.model_digest,
            "window": window,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    challenger_id = f"legacy-v4-{hashlib.sha256(identity_bytes).hexdigest()}"
    request_id = f"{challenger_id}-fit"
    lineage = ModelFitLineage(
        request_id=request_id,
        parent_incumbent_digest=incumbent.model_digest,
        parent_incumbent_generation=incumbent.role_generation,
        candidate_generation=candidate.candidate_generation,
        fit_corpus=corpus,
        fit_corpus_digest=corpus.corpus_digest,
        trigger_origin=origin.value,
        result_status="succeeded",
        candidate_digest=candidate.model_digest,
    )
    return ModelChallengerState(
        schema_version=1,
        challenger_id=challenger_id,
        revision=0,
        phase="evaluating",
        origin=origin,
        policy=activation_policy_for_origin(origin),
        fit_corpus=corpus,
        fit_lineage=lineage,
        fit_preparation={
            "request_id": request_id,
            "accepted": True,
            "candidate_digest": candidate.model_digest,
            "legacy_checkpoint_schema": 4,
            "fit_corpus_digest": corpus.corpus_digest,
            "fit_result": dict(challenger.get("metadata", {})),
            "target_timing": None,
        },
        controller_configuration_digest=configuration_digest,
        incumbent=incumbent,
        candidate=candidate,
        calibration_manifest=calibration_manifest,
        evaluation_epoch=0,
        evaluation_round=0,
        consecutive_wins=0,
        required_wins=2,
        last_decision_id=None,
        last_evidence_id=None,
        activation_transaction_id=None,
        retirement_reason=None,
        created_ms=0,
        updated_ms=0,
        retired_ms=None,
    )


def migrate_mpc_learning_authority(
    *,
    defaults,
    activation_input_key: str | None = None,
    database_path: str | os.PathLike[str] | None = None,
) -> GreyLearningMigrationResult:
    """Atomically converge checkpoint, activation, and challenger authority on grey v6.

    ``activation_input_key`` names the legacy blob key to migrate from, for
    installations still on that layout. Normal startup omits it and migrates
    the singletons. Historical evidence rows are deliberately untouched.
    """

    from common.controller_model_state import MODEL_STATE_KEY, SCHEMA_VERSION
    from controller.mpc_snapshot import GreySnapshotInvalid, migrate_grey_learning_snapshot

    if not isinstance(defaults, dict):
        raise TypeError("defaults must be an object")
    with _model_evidence_connection(database_path) as connection, datastore.transaction(connection) as conn:
        controller_envelope = _migration_json_blob(conn, MODEL_STATE_KEY)
        controller_snapshot = None
        if (
            isinstance(controller_envelope, dict)
            and controller_envelope.get("version") == SCHEMA_VERSION
            and isinstance(controller_envelope.get("models"), dict)
        ):
            controller_snapshot = controller_envelope["models"].get("mpc")
        revision = (
            controller_snapshot.get("revision", 0)
            if isinstance(controller_snapshot, dict)
            and isinstance(controller_snapshot.get("revision", 0), int)
            and not isinstance(controller_snapshot.get("revision", 0), bool)
            else 0
        )

        state = None
        activation_document = (
            _migration_json_blob(conn, activation_input_key) if activation_input_key is not None else None
        )
        if activation_input_key is None:
            row = _activation_state_row(conn)
            if row is not None:
                state = ModelActivationState(*row)
                state = _normalize_activation_policy(conn, state)

                def pair_authority(pair, fallback_json):
                    if pair is None:
                        try:
                            decoded = json.loads(fallback_json)
                        except TypeError, ValueError, json.JSONDecodeError:
                            return None
                        return {
                            "model_kind": (
                                decoded.get("model_kind", "grey-box") if isinstance(decoded, dict) else None
                            ),
                            "model_schema": decoded.get("version") if isinstance(decoded, dict) else None,
                            "snapshot": decoded,
                        }
                    configuration = dict(pair.configuration)
                    physical = {
                        "C_c": configuration.get("C_c"),
                        "h_amb": configuration.get("h_amb"),
                        "T_amb": configuration.get("T_amb"),
                        "theta": configuration.get("theta"),
                        "n_delay": configuration.get("n_delay", configuration.get("delay_states")),
                        "K_Q": configuration.get("K_Q"),
                        "sigma": configuration.get("sigma"),
                    }
                    try:
                        snapshot = migrate_grey_learning_snapshot(
                            {
                                "version": 3,
                                "revision": pair.role_generation,
                                "params": physical,
                                "rmse": None,
                                "samples": 0,
                                "band_c": [0.0, 0.0],
                                "nfev": None,
                            }
                        )
                    except GreySnapshotInvalid:
                        return None
                    return {
                        "model_kind": "grey-box",
                        "model_schema": 6,
                        "snapshot": snapshot,
                        "pair": pair.to_dict(),
                        "digest": pair.model_digest,
                        "generation": pair.role_generation,
                    }

                activation_document = {
                    "active": pair_authority(state.active_pair, state.active_snapshot_json),
                    "rollback": pair_authority(state.rollback_pair, state.rollback_snapshot_json),
                }

        invalidated = False
        selected = None
        source = "defaults"
        if isinstance(activation_document, dict):
            for name in ("active", "rollback"):
                authority = activation_document.get(name)
                candidate = _migration_authority_snapshot(authority, current_only=True)
                if candidate is not None:
                    selected = candidate
                    source = name
                    break
                if authority is not None:
                    invalidated = True
        if selected is None and controller_snapshot is not None:
            try:
                selected = migrate_grey_learning_snapshot(_controller_snapshot_for_migration(controller_snapshot))
                source = "controller"
            except GreySnapshotInvalid:
                invalidated = True
        if selected is None:
            selected = migrate_grey_learning_snapshot(
                {
                    "version": 3,
                    "revision": max(0, revision),
                    "params": defaults,
                    "rmse": None,
                    "samples": 0,
                    "band_c": [0.0, 0.0],
                    "nfev": None,
                }
            )
            source = "defaults"
            invalidated = True

        if source in ("active", "rollback") and isinstance(activation_document, dict):
            selected_authority = activation_document.get(source)
            selected_pair = selected_authority.get("pair") if isinstance(selected_authority, dict) else None
            if isinstance(selected_pair, dict):
                selected["revision"] = selected_pair["role_generation"]
                selected["identities"] = {
                    "active_digest": selected_pair["model_digest"],
                    "active_generation": selected_pair["role_generation"],
                    "rollback_digest": None,
                    "rollback_generation": None,
                }
            if source == "active" and activation_input_key is None and state is not None:
                selected["evidence"]["confidence_decision_id"] = state.evidence_decision_id
                selected["origin"] = state.origin
                selected["policy"] = state.policy
                selected["activation"] = {
                    "phase": ("aborted" if state.phase == "prepared" else state.phase),
                    "pending_persistence": False,
                    "pending_swap": False,
                }
            else:
                selected["evidence"]["confidence_decision_id"] = None
                selected["origin"] = None
                selected["policy"] = None
                selected["activation"] = {
                    "phase": "aborted",
                    "pending_persistence": False,
                    "pending_swap": False,
                }

        raw_activation = controller_snapshot.get("activation") if isinstance(controller_snapshot, dict) else None
        legacy_prepared = isinstance(raw_activation, dict) and raw_activation.get("phase") == "prepared"
        if legacy_prepared or (state is not None and state.phase == "prepared"):
            selected["activation"] = {
                "phase": "aborted",
                "pending_persistence": False,
                "pending_swap": False,
            }
            if state is not None and state.phase == "prepared":
                conn.execute(
                    """
                    UPDATE model_activation_state
                    SET phase='aborted',
                        reason='prepared activation interrupted during migration'
                    WHERE singleton=1 AND phase='prepared'
                    """
                )

        challenger_row_exists = (
            conn.execute("SELECT 1 FROM model_challenger_state WHERE singleton=1").fetchone() is not None
        )
        _normalize_durable_challenger_policy(conn)
        durable_challenger = None
        try:
            durable_challenger = _read_challenger_in_transaction(conn)
        except _CorruptModelChallenger as error:
            # Preserve recoverable lineage as a canonical retired record. A
            # fully unreadable singleton must be removed so it cannot poison
            # every future challenger write.
            conn.execute("DELETE FROM model_challenger_state WHERE singleton=1")
            salvaged = error.salvaged
            if salvaged is not None:
                durable_challenger = (
                    salvaged
                    if salvaged.phase == "retired"
                    else replace(
                        salvaged,
                        revision=salvaged.revision + 1,
                        phase="retired",
                        retirement_reason="corrupt-challenger",
                        updated_ms=salvaged.updated_ms,
                        retired_ms=salvaged.updated_ms,
                    )
                )
                _write_challenger_state(conn, durable_challenger, insert=True)
        imported = (
            _legacy_v4_challenger_state(controller_snapshot) if source == "controller" and not legacy_prepared else None
        )
        if imported is not None:
            if durable_challenger is None and not challenger_row_exists:
                _write_challenger_state(conn, imported, insert=True)
                durable_challenger = imported
            elif durable_challenger != imported:
                imported = None
            if imported is not None:
                selected["challenger_authority"] = {
                    "challenger_id": durable_challenger.challenger_id,
                    "revision": durable_challenger.revision,
                }

        authority_reference = selected.get("challenger_authority")
        reference_revision = authority_reference.get("revision") if isinstance(authority_reference, dict) else None
        compatible_reference = (
            isinstance(authority_reference, dict)
            and durable_challenger is not None
            and authority_reference.get("challenger_id") == durable_challenger.challenger_id
            and isinstance(reference_revision, int)
            and not isinstance(reference_revision, bool)
            and 0 <= reference_revision <= durable_challenger.revision
            and durable_challenger.phase != "retired"
        )
        if compatible_reference:
            selected["challenger_authority"] = {
                "challenger_id": durable_challenger.challenger_id,
                "revision": durable_challenger.revision,
            }
        else:
            if durable_challenger is not None and durable_challenger.phase != "retired":
                retired = replace(
                    durable_challenger,
                    revision=durable_challenger.revision + 1,
                    phase="retired",
                    retirement_reason="checkpoint-reference-mismatch",
                    updated_ms=max(durable_challenger.updated_ms, 0),
                    retired_ms=max(durable_challenger.updated_ms, 0),
                )
                _write_challenger_state(
                    conn,
                    retired,
                    insert=False,
                    expected_revision=durable_challenger.revision,
                )
                durable_challenger = retired
            selected["challenger_authority"] = None

        reason = "schema-invalidated" if invalidated else None
        models = (
            dict(controller_envelope["models"])
            if isinstance(controller_envelope, dict) and isinstance(controller_envelope.get("models"), dict)
            else {}
        )
        models["mpc"] = selected
        controller_json = json.dumps(
            {"version": SCHEMA_VERSION, "models": models},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        conn.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (MODEL_STATE_KEY, controller_json),
        )

        current = {
            "model_kind": "grey-box",
            "model_schema": 6,
            "snapshot": selected,
            "source": source,
        }
        if activation_input_key is not None:
            migrated_activation = {
                **(activation_document if isinstance(activation_document, dict) else {}),
                "current": current,
                "candidate": None,
                "evidence_decision_id": None,
                "migration_reason": reason,
            }
            conn.execute(
                "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (
                    activation_input_key,
                    json.dumps(
                        migrated_activation,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                ),
            )
        elif source not in ("active", "rollback"):
            conn.execute("DELETE FROM model_activation_state WHERE singleton=1")
        elif source == "active":
            selected_json = json.dumps(
                selected,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            conn.execute(
                """
                    UPDATE model_activation_state
                    SET active_snapshot_json=?
                    WHERE singleton=1
                    """,
                (selected_json,),
            )
        elif source == "rollback":
            authority = activation_document.get("rollback")
            pair_json = json.dumps(
                authority.get("pair"),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            selected_json = json.dumps(selected, sort_keys=True, separators=(",", ":"))
            conn.execute(
                """
                    UPDATE model_activation_state
                    SET active_snapshot_json=?, rollback_snapshot_json=?,
                        evidence_decision_id='', phase='aborted', transaction_id=NULL,
                        incumbent_pair_json=?, candidate_pair_json=NULL,
                        rollback_pair_json=?, origin=NULL, policy=NULL,
                        candidate_generation=NULL, candidate_digest=NULL,
                        reason='schema-invalidated'
                    WHERE singleton=1
                    """,
                (selected_json, selected_json, pair_json, pair_json),
            )
        if reason is not None:
            invalidation = ModelEvidenceRecord(
                evidence_id=f"mpc:schema-migration:{revision}:{source}",
                kind=EvidenceKind.SCHEMA_INVALIDATION,
                session_id="mpc-schema-migration",
                cook_id=None,
                timestamp_ms=0,
                role_generation=0,
                model_digest=current["snapshot"]["identities"]["active_digest"],
                provenance_digest=None,
                schema_version=3,
                payload=SchemaInvalidationEvidence(
                    previous_schema_version=3,
                    reason=reason,
                ),
            ).to_db_row()
            conn.execute(
                """
                    INSERT OR IGNORE INTO model_evidence(
                        evidence_id, session_id, cook_id, timestamp_ms, kind,
                        role_generation, model_digest, provenance_digest,
                        schema_version, payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                (
                    invalidation.evidence_id,
                    invalidation.session_id,
                    invalidation.cook_id,
                    invalidation.timestamp_ms,
                    invalidation.kind,
                    invalidation.role_generation,
                    invalidation.model_digest,
                    invalidation.provenance_digest,
                    invalidation.schema_version,
                    invalidation.payload,
                ),
            )

    return GreyLearningMigrationResult(selected, source, reason)
