from dataclasses import dataclass
from math import ceil
from types import SimpleNamespace

import pytest

import controller.mpc as mpc_module
from controller.model_learning.activation import (
    ActivationPhase,
    PreparedActivationRecord,
)
from controller.model_learning.activation_runtime import ActivationRuntime
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin
from controller.model_learning.grey_runtime import GreyLearningRuntime
from controller.mpc_config import MpcConfig
from controller.mpc_factory import MpcPairFactory, OwnedMpcPair
from controller.mpc_model import EstimatorSeed
from controller.runtime.model_fitting import CandidatePair
from controller.runtime.model_persistence import (
    DurableActivationReceipt,
    EvidenceSubmission,
    ModelPersistenceWorker,
)
from tests.unit.mpc._solver_fixtures import (
    CYCLE,
    _config,
    _Estimator,
    _Solver,
    inactive_calibration,
)
from tests.unit.runtime._persistence_helpers import _pair_phase_state

_CONFIG = {
    "control_period": 2.0,
    "n_horizon": 5,
    "enable_online_adaptation": False,
}
_CYCLE = {"u_min": 0.1, "u_max": 0.8}


class _Calibration:
    def __init__(self, *, horizon_steps, u_max):
        del horizon_steps, u_max

    def advance(self, _load, _temperature, _forecast):
        raise AssertionError("calibration must not advance during construction")


class _Pair:
    def __init__(self, events):
        self._events = events
        self.core = SimpleNamespace(config=dict(_CONFIG), u_max=0.8)

    def close(self):
        self._events.append("pair-close")


class _Factory:
    def __init__(self, pair, events, *_args, **callbacks):
        self._pair = pair
        self._events = events
        self.callbacks = callbacks
        events.append("factory")

    def configured(self, *_args, **_kwargs):
        self._events.append("configured")
        return SimpleNamespace()

    def build(self, _configuration, *, authorized):
        assert authorized is True
        self._events.append("pair")
        return self._pair


class _Persistence:
    def __init__(self, events):
        self._events = events

    def close(self, timeout=2.0):
        del timeout
        self._events.append("persistence-close")


def _patch_construction(monkeypatch, events, pair):
    monkeypatch.setattr(mpc_module, "MpcCalibrationRuntime", _Calibration)
    monkeypatch.setattr(
        mpc_module,
        "MpcPairFactory",
        lambda *args, **callbacks: _Factory(
            pair,
            events,
            *args,
            **callbacks,
        ),
    )
    monkeypatch.setattr(mpc_module, "warn_about_model", lambda _config, *, logger=None: None)


class _ActivationPersistence(ModelPersistenceWorker):
    def __init__(self) -> None:
        self.reject_evidence = False
        self.close_count = 0
        self.phase_receipts: list[DurableActivationReceipt] = []

    def submit_activation_phase(self, _record, *, expected_phase):
        del expected_phase
        receipt = DurableActivationReceipt(accepted=True)
        self.phase_receipts.append(receipt)
        return receipt

    def submit_evidence(self, _record):
        return EvidenceSubmission(accepted=not self.reject_evidence)

    def close(self, timeout=2.0):
        del timeout
        self.close_count += 1
        return True


@dataclass(slots=True)
class _ActivationComposition:
    controller: mpc_module.Controller
    runtime: ActivationRuntime
    grey: GreyLearningRuntime
    incumbent: OwnedMpcPair
    candidate: OwnedMpcPair
    prepared: PreparedActivationRecord
    persistence: _ActivationPersistence

    def close(self) -> None:
        self.grey.close()
        self.runtime.close()
        self.incumbent.close()
        self.candidate.close()


def _activation_composition() -> _ActivationComposition:
    factory = MpcPairFactory(
        _config(control_period=2.0),
        "C",
        dict(CYCLE),
        advance_calibration=inactive_calibration,
        model_authority=lambda: (4, None),
        on_policy_failure=lambda _error: None,
        ekf_factory=_Estimator,
        kf_factory=_Estimator,
        solver_factory=_Solver,
    )
    incumbent = factory.build(
        factory.configured(
            _config(control_period=2.0, theta=50.0),
            candidate_generation=3,
            role_generation=4,
        ),
        authorized=True,
    )
    candidate = factory.build(
        factory.configured(
            _config(control_period=3.0, theta=40.0),
            candidate_generation=4,
            role_generation=5,
        ),
        authorized=False,
    )
    prepared = PreparedActivationRecord.prepared(
        timestamp_ms=1_000,
        incumbent=incumbent.descriptor,
        candidate=candidate.descriptor,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.PASSIVE_AUTO,
        decision_id="composition-transition",
    )
    persistence = _ActivationPersistence()
    runtime = ActivationRuntime(
        factory,
        incumbent,
        persistence,
        clock_ms=lambda: 2_000,
    )
    runtime.bind_estimator_seed_source(
        lambda theta, n_delay: EstimatorSeed(
            delay_states=(0.4,) * n_delay,
            chamber_temperature_c=110.0,
            disturbance=0.0,
            segment_id="composition-fixture",
            pre_roll_digest="c" * 64,
            pre_roll_frame_count=ceil(3 * theta / 20.0),
            required_frame_count=ceil(3 * theta / 20.0),
            status="exact",
        )
    )
    grey = GreyLearningRuntime(
        pair_factory=factory,
        activation_runtime=runtime,
        learning_enabled=False,
        units="C",
        cycle_data=dict(CYCLE),
        active_pair=lambda: runtime.active_pair,
        active_components=lambda: CandidatePair(
            runtime.active_pair.estimator,
            runtime.active_pair.solver,
        ),
        configuration=lambda: MpcConfig(runtime.active_pair.core.config),
        snapshot_parameters=lambda: runtime.active_pair.core.snapshot_parameters(),
        sync_configuration=lambda: None,
        append_trace=lambda _records: None,
    )
    grey.sync_activation_generation(exact=True)
    controller = object.__new__(mpc_module.Controller)
    controller.cfg = incumbent.core.config
    controller.set_point = 0.0
    controller._activation_runtime = runtime
    controller._grey_learning_runtime = grey
    return _ActivationComposition(
        controller,
        runtime,
        grey,
        incumbent,
        candidate,
        prepared,
        persistence,
    )


def _assert_activation_consistency(
    composition,
    expected_pair,
    *,
    control_period,
    role_generation,
):
    assert composition.controller.active_control_pair is expected_pair
    assert composition.runtime.active_pair is expected_pair
    assert expected_pair.core.config["control_period"] == pytest.approx(control_period)
    assert composition.controller.cfg == expected_pair.core.config
    assert composition.controller.get_control_period() == pytest.approx(control_period)
    assert composition.runtime.role_generation == role_generation
    assert composition.grey.learning_role_generation == role_generation


def test_controller_constructs_and_closes_focused_owners_in_dependency_order(
    monkeypatch,
):
    events = []
    pair = _Pair(events)

    def normalize(config):
        events.append("normalize")
        return dict(config)

    class Calibration(_Calibration):
        def __init__(self, *, horizon_steps, u_max):
            super().__init__(horizon_steps=horizon_steps, u_max=u_max)
            events.append("calibration")

    class Factory(_Factory):
        def __init__(self, *args, **callbacks):
            super().__init__(pair, events, *args, **callbacks)
            assert set(callbacks) == {
                "advance_calibration",
                "model_authority",
                "on_policy_failure",
            }

    class Persistence(_Persistence):
        def __init__(self, _store, _logger):
            super().__init__(events)
            events.append("persistence")

    class Activation:
        def __init__(
            self,
            _factory,
            active_pair,
            persistence,
            *,
            owns_persistence,
        ):
            self.active_pair = active_pair
            self._persistence = persistence
            self._owns_persistence = owns_persistence
            events.append("activation")

        def close(self):
            events.append("activation-close")
            if self._owns_persistence:
                self._persistence.close()
            self.active_pair.close()

    class Grey:
        def __init__(self, **_callbacks):
            events.append("grey")

        def close(self):
            events.append("grey-close")

    monkeypatch.setattr(mpc_module, "normalize_config", normalize)
    monkeypatch.setattr(mpc_module, "MpcCalibrationRuntime", Calibration)
    monkeypatch.setattr(mpc_module, "MpcPairFactory", Factory)
    monkeypatch.setattr(mpc_module, "ModelPersistenceWorker", Persistence)
    monkeypatch.setattr(mpc_module, "ActivationRuntime", Activation)
    monkeypatch.setattr(mpc_module, "GreyLearningRuntime", Grey)
    monkeypatch.setattr(mpc_module, "warn_about_model", lambda _config, *, logger=None: None)

    controller = mpc_module.Controller(dict(_CONFIG), "C", dict(_CYCLE))
    assert events == [
        "normalize",
        "calibration",
        "factory",
        "configured",
        "pair",
        "persistence",
        "activation",
        "grey",
    ]

    controller.close()
    controller.close()
    assert events[-4:] == [
        "grey-close",
        "activation-close",
        "persistence-close",
        "pair-close",
    ]

def test_injected_controller_normal_close_never_closes_worker(monkeypatch) -> None:
    events = []
    pair = _Pair(events)
    persistence = _Persistence(events)
    _patch_construction(monkeypatch, events, pair)

    class Activation:
        def __init__(
            self,
            _factory,
            active_pair,
            persistence_worker,
            *,
            owns_persistence,
        ):
            assert owns_persistence is False
            self.active_pair = active_pair
            self._persistence = persistence_worker
            events.append("activation")

        def close(self):
            events.append("activation-close")
            self.active_pair.close()

    class Grey:
        def __init__(self, **_callbacks):
            events.append("grey")

        def close(self):
            events.append("grey-close")

    monkeypatch.setattr(mpc_module, "ActivationRuntime", Activation)
    monkeypatch.setattr(mpc_module, "GreyLearningRuntime", Grey)

    controller = mpc_module.Controller(
        dict(_CONFIG),
        "C",
        dict(_CYCLE),
        activation_persistence=persistence,
    )
    controller.close()
    controller.close()

    assert events[-3:] == ["grey-close", "activation-close", "pair-close"]
    assert "persistence-close" not in events


def test_injected_activation_construction_failure_does_not_close_worker(monkeypatch):
    events = []
    pair = _Pair(events)
    persistence = _Persistence(events)
    _patch_construction(monkeypatch, events, pair)

    def fail_activation(
        _factory,
        _pair,
        _persistence,
        *,
        owns_persistence,
    ):
        assert owns_persistence is False
        events.append("activation")
        raise LookupError("activation-construction")

    monkeypatch.setattr(mpc_module, "ActivationRuntime", fail_activation)

    with pytest.raises(LookupError, match="activation-construction"):
        mpc_module.Controller(
            dict(_CONFIG),
            "C",
            dict(_CYCLE),
            activation_persistence=persistence,
        )

    assert events == [
        "factory",
        "configured",
        "pair",
        "activation",
        "pair-close",
    ]


def test_internal_activation_construction_failure_closes_worker_once(monkeypatch):
    events = []
    pair = _Pair(events)
    persistence = _Persistence(events)
    _patch_construction(monkeypatch, events, pair)
    monkeypatch.setattr(
        mpc_module,
        "ModelPersistenceWorker",
        lambda _store, _logger: persistence,
    )

    def fail_activation(
        _factory,
        _pair,
        _persistence,
        *,
        owns_persistence,
    ):
        assert owns_persistence is True
        events.append("activation")
        raise LookupError("activation-construction")

    monkeypatch.setattr(mpc_module, "ActivationRuntime", fail_activation)

    with pytest.raises(LookupError, match="activation-construction"):
        mpc_module.Controller(dict(_CONFIG), "C", dict(_CYCLE))

    assert events == [
        "factory",
        "configured",
        "pair",
        "activation",
        "persistence-close",
        "pair-close",
    ]


def test_grey_construction_failure_preserves_original_after_activation_cleanup_failure(monkeypatch):
    events = []
    pair = _Pair(events)
    persistence = _Persistence(events)
    _patch_construction(monkeypatch, events, pair)

    class Activation:
        def __init__(
            self,
            _factory,
            active_pair,
            persistence_worker,
            *,
            owns_persistence,
        ):
            self.active_pair = active_pair
            self._persistence = persistence_worker
            self._owns_persistence = owns_persistence
            events.append("activation")

        def close(self):
            events.append("activation-close")
            if self._owns_persistence:
                self._persistence.close()
            self.active_pair.close()
            raise RuntimeError("activation-cleanup")

    def fail_grey(**_kwargs):
        events.append("grey")
        raise LookupError("grey-construction")

    monkeypatch.setattr(mpc_module, "ActivationRuntime", Activation)
    monkeypatch.setattr(mpc_module, "GreyLearningRuntime", fail_grey)

    with pytest.raises(LookupError, match="grey-construction"):
        mpc_module.Controller(
            dict(_CONFIG),
            "C",
            dict(_CYCLE),
            activation_persistence=persistence,
        )

    assert events[-3:] == [
        "grey",
        "activation-close",
        "pair-close",
    ]


def test_initial_pair_failure_does_not_construct_or_close_untransferred_persistence(monkeypatch):
    events = []
    persistence = _Persistence(events)
    monkeypatch.setattr(mpc_module, "MpcCalibrationRuntime", _Calibration)

    class Factory:
        def __init__(self, *_args, **_kwargs):
            events.append("factory")

        def configured(self, *_args, **_kwargs):
            events.append("configured")
            return SimpleNamespace()

        def build(self, _configuration, *, authorized):
            assert authorized is True
            events.append("pair-failure")
            raise LookupError("initial-pair")

    monkeypatch.setattr(mpc_module, "MpcPairFactory", Factory)

    with pytest.raises(LookupError, match="initial-pair"):
        mpc_module.Controller(
            dict(_CONFIG),
            "C",
            dict(_CYCLE),
            activation_persistence=persistence,
        )

    assert events == ["factory", "configured", "pair-failure"]


def test_same_generation_activation_noop_does_not_synchronize_configuration():
    composition = _activation_composition()
    original_config = dict(composition.controller.cfg)
    composition.controller.cfg = original_config

    try:
        assert composition.controller.advance_activation() is True
        assert composition.runtime.active_pair is composition.incumbent
        assert composition.runtime.role_generation == 4
        assert composition.controller.cfg is original_config
        assert composition.controller.get_control_period() == pytest.approx(2.0)
        assert composition.grey.learning_role_generation == 4
    finally:
        composition.close()


def test_precommit_authorization_rejection_does_not_synchronize_configuration():
    composition = _activation_composition()
    original_config = dict(composition.controller.cfg)
    composition.controller.cfg = original_config
    active = composition.prepared.transition(ActivationPhase.ACTIVE)

    try:
        assert composition.controller.authorize_candidate_pair(active) is False
        assert composition.runtime.active_pair is composition.incumbent
        assert composition.runtime.role_generation == 4
        assert composition.controller.cfg is original_config
        assert composition.controller.get_control_period() == pytest.approx(2.0)
        assert composition.grey.learning_role_generation == 4
    finally:
        composition.close()


def test_rejected_precommit_transitions_do_not_synchronize_activation_identity():
    composition = _activation_composition()
    original_config = dict(composition.controller.cfg)
    composition.controller.cfg = original_config

    try:
        assert (
            composition.controller.compensate_candidate_pair(
                composition.candidate,
                composition.prepared,
                "compensation rejected",
            )
            is False
        )
        assert composition.controller.cfg is original_config
        assert composition.grey.learning_role_generation == 4

        assert composition.controller.activation_runtime_failure("solve failed") is False
        assert composition.controller.cfg is original_config
        assert composition.grey.learning_role_generation == 4

        assert composition.controller.rollback_activation("operator rollback") is False
        assert composition.controller.cfg is original_config
        assert composition.grey.learning_role_generation == 4
    finally:
        composition.close()


def test_advance_synchronizes_only_after_generation_commits_on_terminal_false():
    composition = _activation_composition()
    prepared_receipt = DurableActivationReceipt(accepted=True)
    prepared_receipt._complete(durable=True)

    try:
        assert composition.controller.queue_prepared_activation(
            composition.prepared,
            composition.candidate,
            prepared_receipt,
        )

        assert composition.controller.advance_activation() is False
        assert composition.runtime.active_pair is composition.candidate
        assert composition.runtime.role_generation == 4
        assert composition.controller.cfg is composition.incumbent.core.config
        assert composition.controller.get_control_period() == pytest.approx(2.0)
        assert composition.grey.learning_role_generation == 4

        composition.persistence.phase_receipts[-1]._complete(durable=True)
        composition.persistence.reject_evidence = True
        assert composition.controller.advance_activation() is False
        _assert_activation_consistency(
            composition,
            composition.candidate,
            control_period=3.0,
            role_generation=5,
        )
    finally:
        composition.close()


def test_authorization_lifecycle_rejection_synchronizes_committed_candidate_identity():
    composition = _activation_composition()
    active = composition.prepared.transition(ActivationPhase.ACTIVE)

    try:
        assert composition.controller.install_candidate_pair_inert(
            composition.candidate,
            composition.prepared,
        )
        composition.persistence.reject_evidence = True

        assert composition.controller.authorize_candidate_pair(active) is False
        _assert_activation_consistency(
            composition,
            composition.candidate,
            control_period=3.0,
            role_generation=5,
        )
    finally:
        composition.close()


def test_fallback_lifecycle_rejection_synchronizes_restored_owner_identity():
    composition = _activation_composition()
    active = composition.prepared.transition(ActivationPhase.ACTIVE)

    try:
        assert composition.controller.install_candidate_pair_inert(
            composition.candidate,
            composition.prepared,
        )
        assert composition.controller.authorize_candidate_pair(active) is True
        composition.persistence.reject_evidence = True

        assert composition.controller.activation_runtime_failure("solve failed") is False
        _assert_activation_consistency(
            composition,
            composition.incumbent,
            control_period=2.0,
            role_generation=6,
        )
    finally:
        composition.close()


def test_restore_retirement_failure_synchronizes_installed_owner_identity(
    monkeypatch,
):
    composition = _activation_composition()
    persisted, _record = _pair_phase_state(ActivationPhase.ACTIVE)
    original_close = OwnedMpcPair.close
    failed_once = False

    def fail_retired_incumbent_once(pair):
        nonlocal failed_once
        if pair is composition.incumbent and not failed_once:
            failed_once = True
            raise RuntimeError("retired incumbent close failed")
        return original_close(pair)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(OwnedMpcPair, "close", fail_retired_incumbent_once)
            assert composition.controller.restore_activation(persisted, ()) is False

        restored = composition.runtime.active_pair
        assert restored is not composition.incumbent
        assert restored.descriptor.role_generation == 5
        _assert_activation_consistency(
            composition,
            restored,
            control_period=5.0,
            role_generation=5,
        )
    finally:
        composition.close()


def test_successful_authorization_synchronizes_generation_and_public_configuration():
    controller = object.__new__(mpc_module.Controller)
    active_config = {"control_period": 3.0}
    controller.cfg = {"control_period": 2.0}
    controller.set_point = 0.0
    controller._activation_runtime = SimpleNamespace(
        active_pair=SimpleNamespace(
            core=SimpleNamespace(
                config=active_config,
                set_target=lambda _target: None,
                estimator_seed_status=None,
            )
        ),
        role_generation=5,
        authorize_candidate_pair=lambda _record: True,
    )
    syncs = []
    controller._grey_learning_runtime = SimpleNamespace(
        sync_activation_generation=lambda **kwargs: syncs.append(kwargs)
    )

    assert controller.authorize_candidate_pair(SimpleNamespace()) is True
    assert controller.cfg is active_config
    assert syncs == [{}]


def test_activation_confidence_preserves_preceding_fifo_evidence():
    controller = object.__new__(mpc_module.Controller)
    calls = []
    receipt = SimpleNamespace(accepted=True)
    controller._activation_runtime = SimpleNamespace(
        submit_activation_confidence=lambda record, *, preceding_evidence=(): (
            calls.append((record, preceding_evidence)),
            receipt,
        )[1]
    )
    decision = SimpleNamespace(kind="confidence")
    preceding = (SimpleNamespace(kind="evaluation"),)

    assert (
        controller.submit_activation_confidence(
            decision,
            preceding_evidence=preceding,
        )
        is receipt
    )
    assert calls == [(decision, preceding)]


def test_close_attempts_grey_then_activation_and_aggregates_failures_once():
    events = []
    controller = object.__new__(mpc_module.Controller)
    controller._closed = False

    def fail(label):
        def close():
            events.append(label)
            raise RuntimeError(label)

        return SimpleNamespace(close=close)

    controller._grey_learning_runtime = fail("grey-close")
    controller._activation_runtime = fail("activation-close")

    with pytest.raises(BaseExceptionGroup) as raised:
        controller.close()

    assert events == ["grey-close", "activation-close"]
    assert [str(error) for error in raised.value.exceptions] == [
        "grey-close",
        "activation-close",
    ]

    controller.close()
    assert events == ["grey-close", "activation-close"]
