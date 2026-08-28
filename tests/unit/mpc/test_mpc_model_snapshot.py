"""Grey-only v4 controller checkpoint writer and strict runtime restore."""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import replace
from types import SimpleNamespace

import pytest

import controller.mpc as mpc_module
import controller.mpc_core as mpc_core_module
from common.controller_model_state import ControllerModelStore
from controller.applied_output import AppliedOutput, OutputSource
from controller.model_learning.activation import ActivationPhase, GreyControlPairDescriptor
from controller.mpc import Controller
from controller.mpc_config import DEFAULT_MPC_CONFIG
from controller.mpc_snapshot import GreySnapshotInvalid, migrate_grey_learning_snapshot
from controller.runtime.context import EVENT_LOG_NAME
from controller.runtime.model_fitting import TeardownRefitOutcome
from tests.unit.runtime._persistence_helpers import _pair_phase_state

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
    return Controller(dict(DEFAULT_MPC_CONFIG, **overrides), "C", dict(CYCLE))


def _adopt(
    controller: Controller,
    parameters=PARAMS,
    *,
    rmse=1.7,
    samples=420,
    band_c=(80.0, 220.0),
    nfev=11,
) -> None:
    active = controller.active_control_pair.descriptor
    settings = dict(controller.cfg)
    settings.update(parameters)
    pair = controller._pair_factory.build(
        controller._pair_factory.configured(
            settings,
            candidate_generation=active.candidate_generation + 1,
            role_generation=active.role_generation + 1,
            model_identified=True,
        ),
        authorized=False,
    )
    controller._grey_learning_runtime.adopt_model(
        pair,
        rmse=rmse,
        samples=samples,
        band_c=band_c,
        nfev=nfev,
    )


def _identified():
    controller = _controller()
    _adopt(controller)
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
    _adopt(restored, rmse=1.2, samples=500, band_c=(90.0, 210.0), nfev=None)
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


def test_restore_preserves_the_live_target_on_the_replacement_pair():
    snapshot = _identified().get_model_snapshot()
    restored = _controller()
    restored.set_target(107.2)
    restored.set_output(AppliedOutput(0.18, OutputSource.CONTROLLER, 1.0))

    assert restored.restore_model(snapshot) is True

    assert restored.active_control_pair.core.set_point_c == pytest.approx(107.2)
    assert restored.active_control_pair.core.applied_combustion_load == pytest.approx(0.2)
    result = restored.update((175.4 - 32.0) * 5.0 / 9.0)
    state = restored.active_control_pair.core.estimate
    allocation = restored.trace_allocation()

    assert state is not None
    assert all(0.0 <= value <= 1.0 for value in state[:8])
    assert state[:8] == pytest.approx([0.2] * 8)
    assert allocation is not None
    assert allocation.normalized_combustion_load == pytest.approx(1.0)
    assert result["cycle_ratio"] == pytest.approx(CYCLE["u_max"])


def test_restore_round_trips_complete_validated_v4_checkpoint_state() -> None:
    source = _identified()
    restored = _controller()
    snapshot = source.get_model_snapshot()
    assert snapshot is not None
    active = source.active_control_pair.descriptor
    candidate_settings = dict(source.cfg)
    candidate_settings["theta"] = float(candidate_settings["theta"]) + 1.0
    candidate_descriptor = source._pair_factory.descriptor(
        source._pair_factory.configured(
            candidate_settings,
            candidate_generation=active.candidate_generation + 1,
            role_generation=active.role_generation + 1,
            model_identified=True,
        )
    )
    challenger_parameters = dict(snapshot["active"]["parameters"])
    challenger_parameters["theta"] = candidate_settings["theta"]
    snapshot["challenger"] = {
        "parameters": challenger_parameters,
        "metadata": {
            "rmse": 1.1,
            "samples": 500,
            "band_c": [90.0, 230.0],
            "nfev": 7,
        },
    }
    snapshot["window"] = {
        "session_id": "restored-session",
        "cook_id": "restored-cook",
        "first_observation_sequence": 10,
        "last_observation_sequence": 510,
        "configuration_digest": candidate_descriptor.model_digest,
        "incumbent_digest": active.model_digest,
        "role_generation": candidate_descriptor.role_generation,
    }
    snapshot["candidate_pair"] = candidate_descriptor.to_dict()
    snapshot["evidence"]["confidence_decision_id"] = "restored-confidence"
    snapshot["origin"] = "operator-calibration"
    snapshot["policy"] = "operator-reviewed"
    snapshot["cook_refit"] = {
        "status": "succeeded",
        "latest": TeardownRefitOutcome.READY_FOR_REVIEW.value,
    }
    snapshot["identities"]["candidate_digest"] = candidate_descriptor.model_digest
    snapshot["identities"]["candidate_generation"] = candidate_descriptor.candidate_generation
    snapshot["activation"] = {
        "phase": "prepared",
        "pending_persistence": True,
        "pending_swap": True,
    }
    snapshot["failure"] = {
        "code": "restored-failure",
        "detail": "durable failure detail",
    }
    expected = migrate_grey_learning_snapshot(snapshot)

    try:
        assert restored.restore_model(snapshot) is True
        assert restored.get_model_snapshot() == expected
    finally:
        restored.close()
        source.close()


def test_restore_explicitly_migrates_legacy_active_descriptor_before_factory_restore() -> None:
    source = _identified()
    restored = _controller()
    snapshot = source.get_model_snapshot()
    assert snapshot is not None
    active = source.active_control_pair.descriptor
    legacy_configuration = {
        name: value
        for name, value in active.configuration.items()
        if name not in {"control_period", "est_q_temp", "est_q_dist", "est_r_meas"}
    }
    legacy = GreyControlPairDescriptor(
        model_digest=active.model_digest,
        configuration=legacy_configuration,
        estimator_kind=active.estimator_kind,
        solver_kind=active.solver_kind,
        candidate_generation=active.candidate_generation,
        role_generation=active.role_generation,
    )
    snapshot["active_pair"] = legacy.to_dict()
    expected = migrate_grey_learning_snapshot(snapshot)

    try:
        assert restored.restore_model(snapshot) is True
        assert restored.active_control_pair.descriptor.to_dict() == expected["active_pair"]
        assert restored.get_model_snapshot() == expected
    finally:
        restored.close()
        source.close()


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
        assert restored._grey_learning_runtime._learning_role_generation == 7
        assert restored._grey_learning_runtime._teardown_history.role_generation == 7
    finally:
        restored.close()
        source.close()


def test_runtime_restore_refuses_v3_even_though_one_shot_migration_accepts_it(caplog):
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
    with caplog.at_level(logging.WARNING, logger=EVENT_LOG_NAME):
        assert controller.restore_model(v3) is False
    assert "migration input only" in caplog.text


def _restarted_store(blobs):
    """A store over shared bytes with the empty caches a new process starts with."""

    def read(key):
        return json.loads(blobs[key]) if key in blobs else json.loads(None)

    def write(key, value):
        blobs[key] = json.dumps(value)

    return ControllerModelStore(reader=read, writer=write)


@pytest.mark.parametrize(
    "unrestorable",
    (
        migrate_grey_learning_snapshot(
            {
                "version": 3,
                "revision": 9,
                "params": PARAMS,
                "rmse": None,
                "samples": 0,
                "band_c": [0.0, 0.0],
                "nfev": None,
            }
        ),
        {
            "version": 3,
            "revision": 9,
            "params": PARAMS,
            "rmse": None,
            "samples": 0,
            "band_c": [0.0, 0.0],
            "nfev": None,
        },
    ),
    ids=("migrated-v4-carrying-no-restorable-pair", "superseded-v3-record"),
)
def test_a_refused_checkpoint_still_saves_the_refit_it_falls_back_to(unrestorable):
    blobs = {}
    assert _restarted_store(blobs).save("mpc", unrestorable) is True

    restarted = _restarted_store(blobs)
    controller = _controller()
    try:
        assert controller.restore_model(restarted.load("mpc")) is False

        _adopt(controller)
        assert controller.finalize_cook_refit(TeardownRefitOutcome.ACCEPTED_NEXT_COOK) is True
        checkpoint = controller.get_model_snapshot()

        assert checkpoint["revision"] == 10
        assert restarted.save("mpc", checkpoint) is True
        assert restarted.load("mpc")["revision"] == 10
    finally:
        controller.close()


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


def test_restore_build_failure_closes_partial_candidate_and_keeps_incumbent_usable(monkeypatch):
    source = _identified()
    snapshot = source.get_model_snapshot()
    target = _controller()
    incumbent = target.active_control_pair

    class CandidateEstimator:
        def __init__(self):
            self.closed = 0

        def close(self):
            self.closed += 1

    candidate_estimator = CandidateEstimator()
    monkeypatch.setattr(mpc_core_module, "GreyBoxEKF", lambda **_kwargs: candidate_estimator)
    monkeypatch.setattr(
        mpc_core_module,
        "AcadosGreyBoxMPC",
        lambda _config: (_ for _ in ()).throw(RuntimeError("restore solver unavailable")),
    )

    try:
        assert target.restore_model(snapshot) is False
        assert candidate_estimator.closed == 1
        assert target.active_control_pair is incumbent
        assert set(target.update(20.0)) == {"cycle_ratio", "fan"}
    finally:
        target.close()
        source.close()


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
    controller.terminate_mpc_activation("active-receipt-ambiguous")

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
        ("origin", "invalid-origin"),
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
    state, _record = _pair_phase_state(ActivationPhase.ACTIVE)
    assert controller.restore_activation(state, ())
    controller.terminate_mpc_activation("native solver crashed")

    live = controller._grey_learning_runtime.learning_status()

    assert live["status"] == "error"
    assert live["activation_phase"] == "active"
    assert live["failure"] == {
        "code": "activation-terminal",
        "detail": "native solver crashed",
        "terminal": True,
    }


def test_restore_refuses_a_pairless_placeholder_by_naming_its_missing_pair(caplog):
    """A record with no pair descriptor is declined for saying so, not for raising.

    controller/model_learning/migration.py writes exactly this record when no
    prior authority survives the grey-v4 cutover: shipped defaults, zero
    samples, no fit, and deliberately no pair to own. Refusing it is correct.
    Reaching that refusal through an AttributeError on the null field is not --
    the reason is what an operator needs and the only thing that leaves here.
    """
    placeholder = migrate_grey_learning_snapshot(
        {
            "version": 3,
            "revision": 1,
            "params": PARAMS,
            "rmse": None,
            "samples": 0,
            "band_c": [0.0, 0.0],
            "nfev": None,
        }
    )
    assert placeholder["active_pair"] is None
    controller = _controller()
    before = controller.get_model_snapshot()["active"]["parameters"]

    with caplog.at_level(logging.WARNING, logger=EVENT_LOG_NAME):
        assert controller.restore_model(placeholder) is False

    assert "active pair" in caplog.text
    assert "NoneType" not in caplog.text
    assert controller.get_model_snapshot()["active"]["parameters"] == before
