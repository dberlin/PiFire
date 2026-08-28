"""Grey-only checkpoint and durable-authority migration contracts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace

import pytest

from common import datastore
from common.controller_model_state import MODEL_STATE_KEY, SCHEMA_VERSION
from common.model_evidence import MODEL_EVIDENCE_SCHEMA_VERSION
from common.persistence.model_challenger import (
    compare_and_swap_model_challenger,
    read_model_challenger,
)
from common.persistence.model_evidence import read_model_activation, read_model_evidence
from controller.model_learning.activation import (
    ActivationPhase,
    GreyControlPairDescriptor,
    canonical_snapshot_digest,
)
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin
from controller.model_learning.migration import migrate_mpc_learning_authority
from controller.model_learning.report import backend_learning_report
from controller.mpc_factory import MpcPairFactory
from controller.mpc_model import MODEL_SCHEMA
from controller.mpc_snapshot import GreySnapshotInvalid, migrate_grey_learning_snapshot

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
    parameters = {**PARAMS, "theta": theta}
    active_digest = hashlib.sha256(
        json.dumps(
            parameters,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "version": 4,
        "revision": revision,
        "schema": "pifire-grey-learning/v4",
        "structure": {"kind": "grey-box", "n_delay": 8, "state_count": 10},
        "active": {
            "parameters": parameters,
            "metadata": {
                "rmse": 1.7,
                "samples": 420,
                "band_c": [80.0, 220.0],
                "nfev": 11,
            },
        },
        "challenger": None,
        "active_pair": None,
        "window": None,
        "candidate_pair": None,
        "evidence": {
            "eligible": 0,
            "rejected": 0,
            "confidence_decision_id": None,
        },
        "origin": None,
        "policy": None,
        "identification": {"status": "identified"},
        "cook_refit": {"status": "idle", "latest": None},
        "identities": {
            "active_digest": active_digest,
            "active_generation": 0,
            "candidate_digest": None,
            "candidate_generation": None,
            "rollback_digest": None,
            "rollback_generation": None,
        },
        "activation": {
            "phase": "aborted",
            "pending_persistence": False,
            "pending_swap": False,
        },
        "failure": None,
    }


def _ready_review_v4():
    snapshot = _v4(revision=4)
    active_configuration = {
        "schema": "pifire-grey-box-model/v4",
        "n_delay": 8,
        "parameters": {name: value for name, value in PARAMS.items() if name != "n_delay"},
    }
    candidate_configuration = {
        **active_configuration,
        "parameters": {
            **active_configuration["parameters"],
            "theta": 65.0,
        },
    }
    legacy_active = GreyControlPairDescriptor(
        model_digest=canonical_snapshot_digest(active_configuration),
        configuration=active_configuration,
        estimator_kind="ekf",
        solver_kind="acados-grey",
        candidate_generation=4,
        role_generation=4,
    )
    legacy_candidate = GreyControlPairDescriptor(
        model_digest=canonical_snapshot_digest(candidate_configuration),
        configuration=candidate_configuration,
        estimator_kind="ekf",
        solver_kind="acados-grey",
        candidate_generation=5,
        role_generation=4,
    )
    active = MpcPairFactory.migrate_legacy_descriptor(legacy_active)
    candidate = MpcPairFactory.migrate_legacy_descriptor(legacy_candidate)
    snapshot["active"] = {
        "parameters": dict(PARAMS),
        "metadata": {
            "rmse": 1.7,
            "samples": 420,
            "band_c": [80.0, 220.0],
            "nfev": 11,
        },
    }
    snapshot["challenger"] = {
        "parameters": {
            **PARAMS,
            "theta": 65.0,
        },
        "metadata": {
            "rmse": 1.2,
            "samples": 420,
            "band_c": [80.0, 220.0],
            "nfev": 9,
        },
    }
    snapshot["active_pair"] = legacy_active.to_dict()
    snapshot["candidate_pair"] = legacy_candidate.to_dict()
    snapshot["window"] = {
        "session_id": "legacy-session",
        "cook_id": "legacy-cook",
        "first_observation_sequence": 21,
        "last_observation_sequence": 440,
        "configuration_digest": "c" * 64,
        "incumbent_digest": active.model_digest,
        "role_generation": active.role_generation,
    }
    snapshot["evidence"]["confidence_decision_id"] = "legacy-ready-decision"
    snapshot["origin"] = CandidateOrigin.OPERATOR_CALIBRATION.value
    snapshot["policy"] = ActivationPolicy.OPERATOR_REVIEWED.value
    snapshot["cook_refit"] = {
        "status": "succeeded",
        "latest": "ready-for-review",
    }
    snapshot["identities"] = {
        "active_digest": active.model_digest,
        "active_generation": active.role_generation,
        "candidate_digest": candidate.model_digest,
        "candidate_generation": candidate.candidate_generation,
        "rollback_digest": None,
        "rollback_generation": None,
    }
    snapshot["activation"] = {
        "phase": "aborted",
        "pending_persistence": False,
        "pending_swap": False,
    }
    return snapshot, active, candidate


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


def test_model_schema_is_grey_v5():
    assert MODEL_SCHEMA == 5


def test_v3_migration_preserves_only_bounded_top_level_grey_data():
    migrated = migrate_grey_learning_snapshot(_v3())

    assert migrated["version"] == 5
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
    assert migrated["challenger_authority"] is None
    assert {
        "challenger",
        "window",
        "candidate_pair",
    }.isdisjoint(migrated)
    assert set(migrated["identities"]) == {
        "active_digest",
        "active_generation",
        "rollback_digest",
        "rollback_generation",
    }


def test_v4_normalizes_to_v5_without_importing_an_inline_candidate():
    migrated = migrate_grey_learning_snapshot(_v4())

    assert migrated["version"] == 5
    assert migrated["schema"] == "pifire-grey-learning/v5"
    assert migrated["challenger_authority"] is None
    assert {"challenger", "window", "candidate_pair"}.isdisjoint(migrated)


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
    assert stored["version"] == 5
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
        **PARAMS,
        "theta": 36.0,
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
    active_config = {"schema": "pifire-grey-box-model/v4", **PARAMS, "theta": 31.0}
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
    assert state.phase == "aborted"
    assert state.transaction_id == "transaction-active"
    assert state.reason is not None and "prepared" in state.reason
    assert state.evidence_decision_id == "decision-active"
    assert state.incumbent_pair == active
    assert state.candidate_generation == 7
    assert state.candidate_digest == active.model_digest
    assert result.snapshot["revision"] == active.role_generation
    assert result.snapshot["identities"]["active_digest"] == active.model_digest
    assert result.snapshot["identities"]["active_generation"] == active.role_generation
    assert result.snapshot["evidence"]["confidence_decision_id"] == "decision-active"
    assert result.snapshot["challenger_authority"] is None
    assert result.snapshot["activation"] == {
        "phase": "aborted",
        "pending_persistence": False,
        "pending_swap": False,
    }
    assert canonical_snapshot_digest(state.active_pair.configuration) == result.snapshot["identities"]["active_digest"]


def test_migration_invalidation_is_durable_and_surfaces_from_real_backend(ds):
    _seed_controller({"version": 3, "broken": True})

    result = migrate_mpc_learning_authority(defaults=PARAMS)
    report, records = backend_learning_report()

    invalidations = [
        record
        for record in records
        if record.kind.value == "schema_invalidation" and record.schema_version == MODEL_EVIDENCE_SCHEMA_VERSION
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


def test_non_object_defaults_are_rejected_before_authority_is_rewritten(ds):
    original = _v3()
    _seed_controller(original)

    with pytest.raises(TypeError, match="defaults must be an object"):
        migrate_mpc_learning_authority(defaults=[PARAMS])

    assert _stored_controller() == original


def test_current_active_precedes_rollback_and_controller_authorities(ds):
    _seed_controller(_v3(params={**PARAMS, "theta": 47.0}))
    datastore.set_blob(
        "mpc:model_activation_migration_input",
        json.dumps(
            {
                "active": _authority(_v4(theta=31.0)),
                "rollback": _authority(_v4(theta=36.0)),
            }
        ),
    )

    result = migrate_mpc_learning_authority(
        defaults=PARAMS,
        activation_input_key="mpc:model_activation_migration_input",
    )

    assert result.source == "active"
    assert result.reason is None
    assert result.snapshot["active"]["parameters"]["theta"] == 31.0
    assert _stored_controller() == result.snapshot


@pytest.mark.parametrize(
    ("malformed_active", "reason"),
    [
        (None, None),
        ([], "schema-invalidated"),
        (
            {
                "model_kind": "grey-box",
                "model_schema": 5,
                "snapshot": _v3(params={**PARAMS, "theta": 31.0}),
            },
            "schema-invalidated",
        ),
        (
            {
                "model_kind": "grey-box",
                "model_schema": 3,
                "snapshot": _v4(theta=31.0),
            },
            "schema-invalidated",
        ),
        (
            {
                "model_kind": "grey-box",
                "model_schema": 5,
                "snapshot": {"version": 5},
            },
            "schema-invalidated",
        ),
    ],
    ids=["absent", "non-object", "legacy-snapshot", "legacy-envelope", "invalid-current"],
)
def test_only_valid_current_active_authority_can_outrank_rollback(ds, malformed_active, reason):
    _seed_controller(_v3(params={**PARAMS, "theta": 47.0}))
    datastore.set_blob(
        "mpc:model_activation_migration_input",
        json.dumps(
            {
                "active": malformed_active,
                "rollback": _authority(_v4(theta=36.0)),
            }
        ),
    )

    result = migrate_mpc_learning_authority(
        defaults=PARAMS,
        activation_input_key="mpc:model_activation_migration_input",
    )

    assert result.source == "rollback"
    assert result.reason == reason
    assert result.snapshot["active"]["parameters"]["theta"] == 36.0
    migrated = json.loads(datastore.get_blob("mpc:model_activation_migration_input"))
    assert migrated["current"]["source"] == "rollback"
    assert migrated["current"]["snapshot"] == result.snapshot


def test_malformed_serialized_documents_are_replaced_by_defaults_without_partial_state(ds):
    ds.connection().execute("PRAGMA ignore_check_constraints = ON")
    datastore.set_blob(MODEL_STATE_KEY, "{not-json")
    datastore.set_blob("mpc:model_activation_migration_input", "{also-not-json")
    ds.connection().execute("PRAGMA ignore_check_constraints = OFF")

    result = migrate_mpc_learning_authority(
        defaults={**PARAMS, "theta": 52.0},
        activation_input_key="mpc:model_activation_migration_input",
    )

    assert result.source == "defaults"
    assert result.reason == "schema-invalidated"
    assert result.snapshot["revision"] == 0
    assert result.snapshot["active"]["parameters"]["theta"] == 52.0
    assert _stored_controller() == result.snapshot
    migrated = json.loads(datastore.get_blob("mpc:model_activation_migration_input"))
    assert migrated == {
        "candidate": None,
        "current": {
            "model_kind": "grey-box",
            "model_schema": 5,
            "snapshot": result.snapshot,
            "source": "defaults",
        },
        "evidence_decision_id": None,
        "migration_reason": "schema-invalidated",
    }


def test_legacy_singleton_snapshot_is_selected_when_no_pair_identity_exists(ds):
    _seed_controller(_v3(params={**PARAMS, "theta": 47.0}))
    active = _v4(revision=9, theta=33.0)
    with datastore.connection() as connection:
        connection.execute(
            """
            INSERT INTO model_activation_state(
                singleton, active_snapshot_json, rollback_snapshot_json,
                evidence_decision_id, controller_configuration_digest,
                role_generation, phase
            ) VALUES(1, ?, ?, 'legacy-decision', ?, 9, 'active')
            """,
            (
                json.dumps(active, indent=2),
                json.dumps(_v4(revision=8, theta=36.0)),
                "d" * 64,
            ),
        )

    result = migrate_mpc_learning_authority(defaults=PARAMS)
    state = read_model_activation()
    normalized_active = migrate_grey_learning_snapshot(active)

    assert result.source == "active"
    assert result.reason is None
    assert result.snapshot == normalized_active
    assert state is not None
    assert state.active_pair is None
    assert state.evidence_decision_id == "legacy-decision"
    assert state.active_snapshot_json == json.dumps(
        normalized_active,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    assert _stored_controller() == normalized_active


def test_invalid_active_pair_configuration_yields_to_valid_rollback_pair(ds):
    _seed_controller(_v3(params={**PARAMS, "theta": 47.0}))
    invalid_config = {
        "schema": "pifire-grey-box-model/v4",
        **PARAMS,
        "theta": 31.0,
        "n_delay": 7,
    }
    invalid_active = GreyControlPairDescriptor(
        model_digest=canonical_snapshot_digest(invalid_config),
        configuration=invalid_config,
        estimator_kind="ekf",
        solver_kind="acados-grey",
        candidate_generation=7,
        role_generation=8,
    )
    rollback_config = {
        "schema": "pifire-grey-box-model/v4",
        **PARAMS,
        "theta": 36.0,
    }
    rollback = GreyControlPairDescriptor(
        model_digest=canonical_snapshot_digest(rollback_config),
        configuration=rollback_config,
        estimator_kind="ekf",
        solver_kind="acados-grey",
        candidate_generation=3,
        role_generation=4,
    )
    with datastore.connection() as connection:
        connection.execute(
            """
            INSERT INTO model_activation_state(
                singleton, active_snapshot_json, rollback_snapshot_json,
                evidence_decision_id, controller_configuration_digest,
                role_generation, phase, incumbent_pair_json, rollback_pair_json
            ) VALUES(1, ?, ?, 'prepared-decision', ?, 8, 'prepared', ?, ?)
            """,
            (
                json.dumps(_v4(theta=31.0)),
                json.dumps(_v4(theta=36.0)),
                "d" * 64,
                json.dumps(invalid_active.to_dict(), sort_keys=True, separators=(",", ":")),
                json.dumps(rollback.to_dict(), sort_keys=True, separators=(",", ":")),
            ),
        )

    result = migrate_mpc_learning_authority(defaults=PARAMS)
    state = read_model_activation()

    assert result.source == "rollback"
    assert result.reason is None
    assert result.snapshot["active"]["parameters"]["theta"] == 36.0
    assert state is not None
    assert state.phase == "aborted"
    assert state.active_pair == rollback
    assert state.rollback_pair == rollback


def test_scalar_singleton_authorities_fall_back_to_controller_and_are_removed(ds):
    controller = _v3(params={**PARAMS, "theta": 47.0})
    _seed_controller(controller)
    with datastore.connection() as connection:
        connection.execute(
            """
            INSERT INTO model_activation_state(
                singleton, active_snapshot_json, rollback_snapshot_json,
                evidence_decision_id, controller_configuration_digest,
                role_generation
            ) VALUES(1, ?, ?, 'malformed-decision', ?, 4)
            """,
            (json.dumps("not-an-authority"), json.dumps(17), "d" * 64),
        )

    result = migrate_mpc_learning_authority(defaults=PARAMS)

    assert result.source == "controller"
    assert result.reason == "schema-invalidated"
    assert result.snapshot == migrate_grey_learning_snapshot(controller)
    assert read_model_activation() is None
    assert _stored_controller() == result.snapshot


def test_rejected_rollback_pointer_update_rolls_back_controller_and_singleton(ds):
    original = _v3(params={**PARAMS, "theta": 47.0})
    _seed_controller(original)
    rollback_config = {
        "schema": "pifire-grey-box-model/v4",
        **PARAMS,
        "theta": 36.0,
    }
    rollback = GreyControlPairDescriptor(
        model_digest=canonical_snapshot_digest(rollback_config),
        configuration=rollback_config,
        estimator_kind="ekf",
        solver_kind="acados-grey",
        candidate_generation=3,
        role_generation=4,
    )
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
                json.dumps({"model_kind": "scheduled-arx", "version": 3}),
                json.dumps(_v4(theta=36.0)),
                "d" * 64,
                json.dumps(rollback.to_dict(), sort_keys=True, separators=(",", ":")),
            ),
        )
        connection.execute(
            """
            CREATE TRIGGER reject_rollback_pointer_migration
            BEFORE UPDATE ON model_activation_state
            BEGIN
                SELECT RAISE(ABORT, 'injected rollback pointer failure');
            END
            """
        )
    original_state = read_model_activation()
    original_evidence = read_model_evidence()

    with pytest.raises(sqlite3.IntegrityError, match="injected rollback pointer"):
        migrate_mpc_learning_authority(defaults=PARAMS)

    assert _stored_controller() == original
    assert read_model_activation() == original_state
    assert read_model_evidence() == original_evidence


def test_valid_legacy_v4_ready_review_imports_one_evaluating_challenger(ds):
    legacy, active, candidate = _ready_review_v4()
    _seed_controller(legacy)

    result = migrate_mpc_learning_authority(defaults=PARAMS)
    challenger = read_model_challenger()

    assert challenger is not None
    assert challenger.phase == "evaluating"
    assert challenger.origin is CandidateOrigin.OPERATOR_CALIBRATION
    assert challenger.policy is ActivationPolicy.OPERATOR_REVIEWED
    assert challenger.incumbent == active
    assert challenger.candidate == candidate
    assert challenger.controller_configuration_digest == legacy["window"]["configuration_digest"]
    assert challenger.fit_lineage.parent_incumbent_digest == active.model_digest
    assert challenger.fit_lineage.candidate_digest == candidate.model_digest
    assert challenger.evaluation_epoch == 0
    assert challenger.evaluation_round == 0
    assert challenger.consecutive_wins == 0
    assert challenger.last_decision_id is None
    assert challenger.last_evidence_id is None
    assert result.snapshot["challenger_authority"] == {
        "challenger_id": challenger.challenger_id,
        "revision": challenger.revision,
    }
    assert {"challenger", "window", "candidate_pair"}.isdisjoint(result.snapshot)
    assert set(result.snapshot["identities"]) == {
        "active_digest",
        "active_generation",
        "rollback_digest",
        "rollback_generation",
    }
    assert _stored_controller() == result.snapshot


@pytest.mark.parametrize(
    "invalid_lineage",
    [
        "missing-challenger",
        "missing-window",
        "missing-candidate-pair",
        "candidate-digest",
        "incumbent-digest",
        "origin-policy",
    ],
)
def test_invalid_legacy_v4_candidate_is_absent_or_retired(ds, invalid_lineage: str) -> None:
    legacy, _, _ = _ready_review_v4()
    if invalid_lineage == "missing-challenger":
        legacy["challenger"] = None
    elif invalid_lineage == "missing-window":
        legacy["window"] = None
    elif invalid_lineage == "missing-candidate-pair":
        legacy["candidate_pair"] = None
    elif invalid_lineage == "candidate-digest":
        legacy["identities"] = {
            **legacy["identities"],
            "candidate_digest": "e" * 64,
        }
    elif invalid_lineage == "incumbent-digest":
        legacy["window"] = {
            **legacy["window"],
            "incumbent_digest": "f" * 64,
        }
    else:
        legacy["policy"] = ActivationPolicy.PASSIVE_AUTO.value
    _seed_controller(legacy)

    result = migrate_mpc_learning_authority(defaults=PARAMS)
    challenger = read_model_challenger()

    assert result.snapshot["challenger_authority"] is None
    assert {"challenger", "window", "candidate_pair"}.isdisjoint(result.snapshot)
    assert challenger is None or (challenger.phase == "retired" and challenger.retirement_reason is not None)


def test_legacy_v4_prepared_activation_aborts_without_importing_candidate(ds):
    legacy, _, _ = _ready_review_v4()
    legacy["activation"] = {
        "phase": "prepared",
        "pending_persistence": False,
        "pending_swap": True,
    }
    _seed_controller(legacy)

    result = migrate_mpc_learning_authority(defaults=PARAMS)
    activation = read_model_activation()
    challenger = read_model_challenger()

    assert result.snapshot["activation"] == {
        "phase": "aborted",
        "pending_persistence": False,
        "pending_swap": False,
    }
    assert result.snapshot["challenger_authority"] is None
    assert activation is None or activation.phase == ActivationPhase.ABORTED.value
    assert challenger is None or challenger.phase == "retired"


@pytest.mark.parametrize("mismatch", ["challenger-id", "revision"])
def test_v5_challenger_reference_mismatch_retires_instead_of_projecting(ds, mismatch: str) -> None:
    legacy, _, _ = _ready_review_v4()
    _seed_controller(legacy)
    imported = migrate_mpc_learning_authority(defaults=PARAMS)
    challenger = read_model_challenger()
    assert challenger is not None
    tampered = {
        **imported.snapshot,
        "challenger_authority": {
            "challenger_id": ("different-challenger" if mismatch == "challenger-id" else challenger.challenger_id),
            "revision": (challenger.revision + 1 if mismatch == "revision" else challenger.revision),
        },
    }
    _seed_controller(tampered)

    remigrated = migrate_mpc_learning_authority(defaults=PARAMS)
    retired = read_model_challenger()

    assert remigrated.snapshot["challenger_authority"] is None
    assert retired is not None
    assert retired.phase == "retired"
    assert retired.retirement_reason is not None and "reference" in retired.retirement_reason


def test_v5_older_revision_reference_adopts_newer_durable_progress(ds) -> None:
    legacy, _, _ = _ready_review_v4()
    _seed_controller(legacy)
    imported = migrate_mpc_learning_authority(defaults=PARAMS)
    challenger = read_model_challenger()
    assert challenger is not None
    assert imported.snapshot["challenger_authority"] == {
        "challenger_id": challenger.challenger_id,
        "revision": challenger.revision,
    }
    advanced = compare_and_swap_model_challenger(
        expected_revision=challenger.revision,
        replacement=replace(
            challenger,
            revision=challenger.revision + 1,
            updated_ms=challenger.updated_ms + 1,
        ),
    )

    remigrated = migrate_mpc_learning_authority(defaults=PARAMS)

    assert remigrated.snapshot["challenger_authority"] == {
        "challenger_id": advanced.challenger_id,
        "revision": advanced.revision,
    }
    assert read_model_challenger() == advanced


def test_unreadable_challenger_row_is_removed_during_migration(ds) -> None:
    legacy, _, _ = _ready_review_v4()
    _seed_controller(legacy)
    imported = migrate_mpc_learning_authority(defaults=PARAMS)
    assert imported.snapshot["challenger_authority"] is not None
    with datastore.connection() as connection:
        connection.execute("UPDATE model_challenger_state SET state_json='{}' WHERE singleton=1")

    remigrated = migrate_mpc_learning_authority(defaults=PARAMS)

    assert remigrated.snapshot["challenger_authority"] is None
    assert read_model_challenger() is None
