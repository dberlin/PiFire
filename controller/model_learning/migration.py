"""Controller policy for selecting and migrating durable MPC learning authority."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass

from common import datastore
from common.model_evidence import (
    EvidenceKind,
    ModelEvidenceRecord,
    SchemaInvalidationEvidence,
)
from common.persistence.model_evidence import (
    ModelActivationState,
    _activation_state_row,
    _model_evidence_connection,
)


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
        not isinstance(snapshot, dict) or snapshot.get("version") != 4 or value.get("model_schema") != 4
    ):
        return None
    try:
        return migrate_grey_learning_snapshot(snapshot)
    except GreySnapshotInvalid:
        return None


def migrate_mpc_learning_authority(
    *,
    defaults,
    activation_input_key: str | None = None,
    database_path: str | os.PathLike[str] | None = None,
) -> GreyLearningMigrationResult:
    """Atomically converge the checkpoint and activation pointer on grey v4.

    ``activation_input_key`` names the legacy blob key to migrate from, for
    installations still on that layout. Normal startup omits it and migrates
    the singleton.
    Historical evidence rows are deliberately untouched.
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
                        "model_schema": 4,
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
                selected = migrate_grey_learning_snapshot(controller_snapshot)
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
                    "candidate_digest": None,
                    "candidate_generation": None,
                    "rollback_digest": None,
                    "rollback_generation": None,
                }
                if source == "active" and activation_input_key is None and state is not None:
                    selected["evidence"]["confidence_decision_id"] = state.evidence_decision_id
                    selected["origin"] = state.origin
                    selected["policy"] = state.policy
                    selected["activation"] = {
                        "phase": state.phase,
                        "pending_persistence": False,
                        "pending_swap": state.phase == "prepared",
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
            "model_schema": 4,
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
