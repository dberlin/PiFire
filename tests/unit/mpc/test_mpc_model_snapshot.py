"""Grey-only v4 controller checkpoint writer and strict runtime restore."""

from __future__ import annotations

import copy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from controller.mpc import GreySnapshotInvalid, _DEFAULTS, Controller, migrate_grey_learning_snapshot
from controller.model_learning.activation import ActivationPhase, GreyControlPairDescriptor

from controller.runtime.model_fitting import TeardownRefitOutcome


CURRENT_SCHEMA = 4
CYCLE = {"u_min": 0.1, "u_max": 0.9}
PARAMS = {
    "C_c": 2520.0,
    "h_amb": 18.5,
    "T_amb": 21.0,
    "theta": 47.0,
    "n_delay": 8,
    "K_Q": 910.0,
    "sigma": 0.0,
}


def _controller(**overrides):
    return Controller(dict(_DEFAULTS, **overrides), "C", dict(CYCLE))


def _identified():
    controller = _controller()
    controller._adopt_model(PARAMS, rmse=1.7, samples=420, band_c=(80.0, 220.0), nfev=11)
    return controller


def test_unidentified_controller_writes_complete_v4_defaults():
    snapshot = _controller().get_model_snapshot()

    assert snapshot["version"] == CURRENT_SCHEMA
    assert snapshot["structure"] == {"kind": "grey-box", "n_delay": 8, "state_count": 10}
    assert snapshot["identification"] == {"status": "unidentified"}
    assert snapshot["active"]["parameters"]["n_delay"] == 8
    assert snapshot["active"]["metadata"]["samples"] == 0


def test_current_writer_has_every_grey_learning_section_and_no_process_job_or_retired_kind():
    snapshot = _identified().get_model_snapshot()

    assert set(snapshot) == {
        "version",
        "revision",
        "schema",
        "structure",
        "active",
        "challenger",
        "active_pair",
        "window",
        "candidate_pair",
        "evidence",
        "origin",
        "policy",
        "identification",
        "cook_refit",
        "identities",
        "activation",
        "failure",
    }
    assert snapshot["active_pair"] == _identified().active_control_pair.descriptor.to_dict()
    assert snapshot["candidate_pair"] is None
    encoded = json.dumps(snapshot, allow_nan=False, sort_keys=True)
    assert "job" not in encoded
    assert "scheduled-arx" not in encoded
    assert "innovation-state-space" not in encoded
    assert "neural" not in encoded


def test_v4_writer_is_json_safe_and_preserves_active_fit_provenance():
    snapshot = _identified().get_model_snapshot()

    json.dumps(snapshot, allow_nan=False)
    assert snapshot["active"] == {
        "parameters": PARAMS,
        "metadata": {
            "rmse": 1.7,
            "samples": 420,
            "band_c": [80.0, 220.0],
            "nfev": 11,
        },
    }


def test_revision_advances_on_adoption_and_restored_revision_is_carried_forward():
    source = _identified()
    snapshot = source.get_model_snapshot()
    snapshot["revision"] = 42
    restored = _controller()

    assert restored.restore_model(snapshot) is True
    restored._adopt_model(PARAMS, rmse=1.2, samples=500, band_c=(90.0, 210.0))
    assert restored.finalize_cook_refit(TeardownRefitOutcome.ACCEPTED_NEXT_COOK) is True
    assert restored.get_model_snapshot()["revision"] == 43


def test_restore_applies_v4_parameters_to_running_estimator_and_policy():
    snapshot = _identified().get_model_snapshot()
    restored = _controller()

    assert restored.restore_model(snapshot) is True
    assert restored.cfg["C_c"] == pytest.approx(2520.0)
    assert restored.cfg["theta"] == pytest.approx(47.0)
    assert restored.mpc.config.C_c == pytest.approx(2520.0)
    assert restored.estimator.C_c == pytest.approx(2520.0)


def test_restore_rebinds_owned_active_pair_to_rebuilt_handles():
    source = _identified()
    snapshot = source.get_model_snapshot()
    restored = _controller()
    previous = restored.active_control_pair

    assert restored.restore_model(snapshot) is True

    active = restored.active_control_pair
    assert active is not previous
    assert active.estimator is restored.estimator
    assert active.solver is restored.mpc


def test_restore_rotates_learning_to_the_restored_pair_generation():
    source = _identified()
    snapshot = source.get_model_snapshot()
    restored_descriptor = replace(
        source.active_control_pair.descriptor,
        candidate_generation=7,
        role_generation=7,
        ownership_digest="",
    )
    assert isinstance(restored_descriptor, GreyControlPairDescriptor)
    snapshot["revision"] = 7
    snapshot["active_pair"] = restored_descriptor.to_dict()
    snapshot["identities"]["active_digest"] = restored_descriptor.model_digest
    snapshot["identities"]["active_generation"] = 7
    restored = _controller(enable_online_adaptation=True)

    try:
        assert restored.restore_model(snapshot) is True
        assert restored._learning_role_generation == 7
        assert restored._teardown_history.role_generation == 7
    finally:
        restored.close()
        source.close()


def test_runtime_restore_refuses_v3_even_though_one_shot_migration_accepts_it(capsys):
    v3 = {
        "version": 3,
        "revision": 7,
        "params": PARAMS,
        "rmse": 1.7,
        "samples": 420,
        "band_c": [80.0, 220.0],
        "nfev": 11,
    }
    controller = _controller()

    assert migrate_grey_learning_snapshot(v3)["version"] == 4
    assert controller.restore_model(v3) is False
    assert "migration input only" in capsys.readouterr().out


@pytest.mark.parametrize(
    "mutation",
    [
        lambda snapshot: snapshot.update(version=5),
        lambda snapshot: snapshot.update(structure={"kind": "grey-box", "n_delay": 7, "state_count": 9}),
        lambda snapshot: snapshot["active"]["parameters"].update(C_c=float("nan")),
        lambda snapshot: snapshot["active"]["parameters"].update(C_c=-1.0),
        lambda snapshot: snapshot.pop("active"),
    ],
)
def test_runtime_restore_refuses_future_corrupt_or_incompatible_v4_atomically(mutation):
    controller = _controller()
    before = controller.get_model_snapshot()
    candidate = _identified().get_model_snapshot()
    mutation(candidate)

    assert controller.restore_model(candidate) is False
    assert controller.get_model_snapshot() == before


def test_status_exposes_live_learning_state_separately_from_durable_activation_identity():
    status = _controller(enable_online_adaptation=False).get_status()

    assert status["learning"] == {
        "status": "collecting",
        "fit_status": "idle",
        "role_generation": 0,
        "candidate_generation": None,
        "checkpoint_digest": status["learning"]["checkpoint_digest"],
        "candidate_digest": None,
        "origin": None,
        "checks": {},
        "activation_phase": "aborted",
        "pending_persistence": False,
        "pending_swap": False,
        "failure": None,
    }
    assert len(status["learning"]["checkpoint_digest"]) == 64


def test_terminal_activation_failure_remains_visible_in_status_and_checkpoint():
    controller = _controller()
    controller._activation_terminated_reason = "active-receipt-ambiguous"

    live = controller.get_status()["learning"]
    snapshot = controller.get_model_snapshot()
    assert live["status"] == "error"
    assert live["failure"] == {
        "code": "activation-terminal",
        "detail": "active-receipt-ambiguous",
        "terminal": True,
    }
    assert snapshot["failure"] == {
        "code": "activation-terminal",
        "detail": "active-receipt-ambiguous",
    }


def test_v4_round_trip_is_exact_for_identified_and_default_snapshots():
    for source in (_controller(), _identified()):
        snapshot = source.get_model_snapshot()
        restored = _controller()
        assert restored.restore_model(snapshot) is True
        assert restored.get_model_snapshot() == snapshot


@pytest.mark.parametrize(
    ("section", "corrupt"),
    (
        ("active", {"parameters": PARAMS, "metadata": {"samples": -1}}),
        ("challenger", {"parameters": PARAMS, "metadata": {"samples": -1}}),
        ("window", {"session_id": "missing-the-rest"}),
        ("evidence", {"eligible": -1, "rejected": 0, "confidence_decision_id": None}),
        ("origin", "scheduled-arx"),
        ("policy", "unreviewed"),
        ("identification", {"status": "maybe"}),
        ("cook_refit", {"status": "idle", "latest": 7}),
        (
            "identities",
            {
                "active_digest": "bad",
                "active_generation": 0,
                "candidate_digest": None,
                "candidate_generation": None,
                "rollback_digest": None,
                "rollback_generation": None,
            },
        ),
        (
            "activation",
            {"phase": "active", "pending_persistence": False, "pending_swap": 1},
        ),
        ("failure", {"code": "", "detail": "failed"}),
    ),
)
def test_every_nested_v4_section_is_validated_before_atomic_restore(section, corrupt):
    source = _identified()
    snapshot = copy.deepcopy(source.get_model_snapshot())
    snapshot[section] = corrupt
    target = _controller()
    before = target.get_model_snapshot()

    with pytest.raises(GreySnapshotInvalid):
        migrate_grey_learning_snapshot(snapshot)
    assert target.restore_model(snapshot) is False
    assert target.get_model_snapshot() == before


def test_real_live_status_failure_overrides_prior_active_activation():
    controller = _controller()
    controller._active_activation_record = SimpleNamespace(phase=ActivationPhase.ACTIVE)
    controller._activation_terminated_reason = "native solver crashed"

    live = controller._learning_live_status()

    assert live["status"] == "error"
    assert live["activation_phase"] == "active"
    assert live["failure"] == {
        "code": "activation-terminal",
        "detail": "native solver crashed",
        "terminal": True,
    }
