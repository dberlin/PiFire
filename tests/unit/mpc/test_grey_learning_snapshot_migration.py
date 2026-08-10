"""Task 11 grey-only checkpoint and durable-authority migration contracts."""

from __future__ import annotations

import json
import sqlite3

import pytest

from common import datastore
from common.controller_model_state import MODEL_STATE_KEY, SCHEMA_VERSION
from common.datastore_accessors import (
    migrate_mpc_learning_authority,
    read_model_activation,
    read_model_evidence,
)
from controller.mpc_snapshot import GreySnapshotInvalid, migrate_grey_learning_snapshot
from controller.mpc_model import MODEL_SCHEMA
from controller.model_learning.activation import GreyControlPairDescriptor, canonical_snapshot_digest
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin
from controller.model_learning.report import backend_learning_report


PARAMS = {
    "C_c": 2520.0,
    "h_amb": 18.5,
    "T_amb": 21.0,
    "theta": 47.0,
    "n_delay": 8,
    "K_Q": 910.0,
    "sigma": 0.0,
}


def _v3(**overrides):
    snapshot = {
        "version": 3,
        "revision": 17,
        "params": dict(PARAMS),
        "rmse": 1.7,
        "samples": 420,
        "band_c": [80.0, 220.0],
        "nfev": 11,
        "online_adaptation": {
            "active_model_kind": "scheduled-arx",
            "eligible_updates": 999,
            "incumbent": {"schema": "scheduled-arx/v2"},
            "challenger": {"schema": "innovation-state-space/v2"},
        },
        "neural_policy": {"weights": [1, 2, 3]},
    }
    snapshot.update(overrides)
    return snapshot


def _v4(revision=17, *, theta=47.0):
    migrated = migrate_grey_learning_snapshot(_v3(revision=revision, params={**PARAMS, "theta": theta}))
    assert migrated is not None
    return migrated


def _authority(snapshot, *, kind="grey-box", generation=4):
    return {
        "model_kind": kind,
        "model_schema": snapshot.get("version"),
        "snapshot": snapshot,
        "digest": "a" * 64,
        "generation": generation,
    }


def _seed_controller(snapshot):
    datastore.set_blob(
        MODEL_STATE_KEY,
        json.dumps({"version": SCHEMA_VERSION, "models": {"mpc": snapshot}}),
    )


def _stored_controller():
    return json.loads(datastore.get_blob(MODEL_STATE_KEY))["models"]["mpc"]


def test_model_schema_is_grey_v4():
    assert MODEL_SCHEMA == 4


def test_v3_migration_preserves_only_bounded_top_level_grey_data():
    migrated = migrate_grey_learning_snapshot(_v3())

    assert migrated["version"] == 4
    assert migrated["revision"] == 17
    assert migrated["structure"] == {"kind": "grey-box", "n_delay": 8, "state_count": 10}
    assert migrated["active"]["parameters"] == PARAMS
    assert migrated["active"]["metadata"] == {
        "rmse": 1.7,
        "samples": 420,
        "band_c": [80.0, 220.0],
        "nfev": 11,
    }
    encoded = json.dumps(migrated, sort_keys=True)
    assert "scheduled-arx" not in encoded
    assert "innovation-state-space" not in encoded
    assert "neural" not in encoded
    assert migrated["evidence"] == {"eligible": 0, "rejected": 0, "confidence_decision_id": None}


def test_v4_round_trip_is_lossless_and_never_emits_v3():
    current = _v4()
    assert migrate_grey_learning_snapshot(current) == current
    assert migrate_grey_learning_snapshot(current)["version"] == 4


@pytest.mark.parametrize("delay", [0, 4, 7, 9, 8.5])
def test_incompatible_delay_is_refused_with_visible_reason(delay):
    snapshot = _v3(params={**PARAMS, "n_delay": delay})
    with pytest.raises(GreySnapshotInvalid, match="incompatible-delay"):
        migrate_grey_learning_snapshot(snapshot)


@pytest.mark.parametrize(
    ("activation", "controller", "expected_source", "expected_theta", "reason"),
    [
        ({"active": _authority(_v4(theta=31.0))}, _v3(), "active", 31.0, None),
        ({"active": _authority(_v4(), kind="scheduled-arx")}, _v3(), "controller", 47.0, "schema-invalidated"),
        ({"active": _authority(_v4(), kind="innovation-state-space")}, _v3(), "controller", 47.0, "schema-invalidated"),
        (
            {
                "active": _authority(_v4(), kind="scheduled-arx"),
                "rollback": _authority(_v4(theta=36.0)),
            },
            _v3(),
            "rollback",
            36.0,
            "schema-invalidated",
        ),
        ({"active": {"broken": True}}, _v3(), "controller", 47.0, "schema-invalidated"),
        ({"active": {"broken": True}}, {"broken": True}, "defaults", 50.0, "schema-invalidated"),
    ],
)
def test_atomic_authority_migration_matrix(ds, activation, controller, expected_source, expected_theta, reason):
    _seed_controller(controller)
    datastore.set_blob("mpc:model_activation_migration_input", json.dumps(activation))

    result = migrate_mpc_learning_authority(
        defaults={**PARAMS, "theta": 50.0},
        activation_input_key="mpc:model_activation_migration_input",
    )

    stored = _stored_controller()
    assert result.source == expected_source
    assert result.reason == reason
    assert stored == result.snapshot
    assert stored["version"] == 4
    assert stored["active"]["parameters"]["theta"] == expected_theta
    migration = json.loads(datastore.get_blob("mpc:model_activation_migration_input"))
    assert migration["current"]["snapshot"] == stored
    assert migration["current"]["model_kind"] == "grey-box"
    assert migration["candidate"] is None
    assert migration["evidence_decision_id"] is None


def test_real_activation_singleton_rolls_back_to_compatible_grey_atomically(ds):
    _seed_controller(_v3(theta=47.0))
    rollback_config = {
        "schema": "pifire-grey-box-model/v4",
        **{**PARAMS, "theta": 36.0},
    }
    rollback = GreyControlPairDescriptor(
        model_digest=canonical_snapshot_digest(rollback_config),
        configuration=rollback_config,
        estimator_kind="ekf",
        solver_kind="acados-grey",
        candidate_generation=3,
        role_generation=4,
    )
    incompatible_active = json.dumps(
        {"model_kind": "scheduled-arx", "version": 3},
        sort_keys=True,
        separators=(",", ":"),
    )
    rollback_snapshot = json.dumps(_v4(theta=36.0), sort_keys=True, separators=(",", ":"))
    with datastore.connection() as connection:
        connection.execute(
            """
            INSERT INTO model_activation_state(
                singleton, active_snapshot_json, rollback_snapshot_json,
                evidence_decision_id, controller_configuration_digest,
                role_generation, phase, rollback_pair_json
            ) VALUES(1, ?, ?, 'retired-decision', ?, 4, 'active', ?)
            """,
            (
                incompatible_active,
                rollback_snapshot,
                "d" * 64,
                json.dumps(rollback.to_dict(), sort_keys=True, separators=(",", ":")),
            ),
        )

    result = migrate_mpc_learning_authority(defaults=PARAMS)

    state = read_model_activation()
    assert result.source == "rollback"
    assert result.reason == "schema-invalidated"
    assert result.snapshot["active"]["parameters"]["theta"] == 36.0
    assert state is not None
    assert state.phase == "aborted"
    assert state.candidate_pair is None
    assert state.active_pair == rollback
    assert state.evidence_decision_id == ""
    assert json.loads(state.active_snapshot_json) == result.snapshot
    assert _stored_controller() == result.snapshot


def test_active_source_rewrites_snapshot_pointer_without_losing_singleton_identity(ds):
    _seed_controller(_v3(theta=47.0))
    active_config = {"schema": "pifire-grey-box-model/v4", **{**PARAMS, "theta": 31.0}}
    active = GreyControlPairDescriptor(
        model_digest=canonical_snapshot_digest(active_config),
        configuration=active_config,
        estimator_kind="ekf",
        solver_kind="acados-grey",
        candidate_generation=7,
        role_generation=8,
    )
    noncanonical_snapshot_json = json.dumps(_v4(theta=31.0), indent=2)
    pair_json = json.dumps(active.to_dict(), sort_keys=True, separators=(",", ":"))
    with datastore.connection() as connection:
        connection.execute(
            """
            INSERT INTO model_activation_state(
                singleton, active_snapshot_json, rollback_snapshot_json,
                evidence_decision_id, controller_configuration_digest,
                role_generation, phase, transaction_id, incumbent_pair_json,
                origin, policy, candidate_generation, candidate_digest
            ) VALUES(1, ?, ?, 'decision-active', ?, 8, 'prepared',
                     'transaction-active', ?, 'operator-calibration',
                     'operator-reviewed', 7, ?)
            """,
            (
                noncanonical_snapshot_json,
                noncanonical_snapshot_json,
                "d" * 64,
                pair_json,
                active.model_digest,
            ),
        )

    result = migrate_mpc_learning_authority(defaults=PARAMS)
    state = read_model_activation()

    assert result.source == "active"
    assert state is not None
    assert state.active_snapshot_json == json.dumps(
        result.snapshot,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    assert state.phase == "prepared"
    assert state.transaction_id == "transaction-active"
    assert state.evidence_decision_id == "decision-active"
    assert state.incumbent_pair == active
    assert state.candidate_generation == 7
    assert state.candidate_digest == active.model_digest
    assert result.snapshot["revision"] == active.role_generation
    assert result.snapshot["identities"]["active_digest"] == active.model_digest
    assert result.snapshot["identities"]["active_generation"] == active.role_generation
    assert result.snapshot["evidence"]["confidence_decision_id"] == "decision-active"
    assert result.snapshot["origin"] == CandidateOrigin.OPERATOR_CALIBRATION.value
    assert result.snapshot["policy"] == ActivationPolicy.OPERATOR_REVIEWED.value
    assert result.snapshot["activation"] == {
        "phase": "prepared",
        "pending_persistence": False,
        "pending_swap": True,
    }
    assert canonical_snapshot_digest(state.active_pair.configuration) == result.snapshot["identities"]["active_digest"]


def test_migration_invalidation_is_durable_and_surfaces_from_real_backend(ds):
    _seed_controller({"version": 3, "broken": True})

    result = migrate_mpc_learning_authority(defaults=PARAMS)
    report, records = backend_learning_report()

    invalidations = [
        record for record in records if record.kind.value == "schema_invalidation" and record.schema_version == 3
    ]
    assert result.reason == "schema-invalidated"
    assert len(invalidations) == 1
    assert report.as_dict()["status"] == "schema-invalidated"
    assert _stored_controller() == result.snapshot


def test_atomic_migration_rolls_back_checkpoint_and_activation_pointer_together(ds):
    original = _v3()
    activation = {"active": _authority(_v4(theta=31.0))}
    _seed_controller(original)
    datastore.set_blob("mpc:model_activation_migration_input", json.dumps(activation))
    datastore.connection().execute(
        """
        CREATE TRIGGER reject_activation_migration
        BEFORE UPDATE OF value ON kv
        WHEN NEW.key = 'mpc:model_activation_migration_input'
        BEGIN
            SELECT RAISE(ABORT, 'injected activation write failure');
        END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected activation"):
        migrate_mpc_learning_authority(
            defaults=PARAMS,
            activation_input_key="mpc:model_activation_migration_input",
        )

    assert _stored_controller() == original
    assert json.loads(datastore.get_blob("mpc:model_activation_migration_input")) == activation
