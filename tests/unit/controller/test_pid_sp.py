"""PID-SP composes the identifier and the predictor and models nothing itself."""

import copy
import importlib
import math
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from common.control_trace import AllocationClampReason
from common.learning_trajectory import (
    FitCorpusIdentity,
    FitCorpusSlice,
    canonical_fit_corpus_digest,
    canonical_trajectory_digest,
)
from common.model_evidence import PidSpFitDecisionEvidence
from common.persistence.learning_trajectory import FitCorpusEmptyError
from controller.applied_output import AppliedOutput, OutputSource
from controller.fopdt_identifier import CONFIRM_WINDOW
from controller.model_learning.contracts import CandidateOrigin, FrameObservation
from controller.model_learning.installation_identity import installation_identity_digest
from controller.model_learning.pid_sp_fitting import PidSpFitResult, PidSpFitStatus
from controller.mpc_allocator import AllocationResult
from controller.pid_sp import (
    STARTUP_REDUCTION,
    PidSpLearningOutcome,
)
from controller.pid_sp import (
    Controller as PidSpController,
)
from controller.pid_sp_delay_evidence import DelayBasin, DelayProfile
from controller.pid_sp_model_selection import (
    CONFIRMATION_WINDOW,
    FOPDT,
    HORIZONS_S,
    IPDT,
    SOPDT,
    ModelConfirmation,
    ModelFit,
    ModelForm,
    compare_model_fits,
    decode_pid_sp_checkpoint,
    encode_pid_sp_checkpoint,
)
from controller.pid_sp_observation import (
    PidSpObservationDecision,
    canonical_pid_sp_observation_model_digest,
)
from grillplat.actuator_capabilities import AUGER_TIMING

CONFIG = {"PB": 60.0, "Ti": 180.0, "Td": 45.0, "stable_window": 12, "center_factor": 0.0010}
CYCLE_DATA = {}
INSTALLATION_IDENTITY = b"pid-sp-test-installation"
INSTALLATION_IDENTITY_DIGEST = installation_identity_digest(lambda: INSTALLATION_IDENTITY)


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(time, "time", c)
    return c


def _controller(name, clock, units="F", *, installation_identity=INSTALLATION_IDENTITY):
    mod = importlib.import_module(f"controller.{name}")
    kwargs = {"installation_identity_provider": lambda: installation_identity} if name == "pid_sp" else {}
    return mod.Controller(dict(CONFIG), units, dict(CYCLE_DATA), **kwargs)


def _observe_completed_frame(
    controller,
    start_s,
    end_s,
    temperature_c,
    duty=0.9,
    **overrides,
):
    duration = end_s - start_s
    values = {
        "frame_start_s": start_s,
        "frame_end_s": end_s,
        "temp_c": temperature_c,
        "setpoint_c": 120.0,
        "ambient_c": 20.0,
        "requested_q": duty,
        "realized_q": duty,
        "requested_auger_duty": duty,
        "delivered_on_s": duration * duty,
        "requested_fan_duty": None,
        "actual_fan_duty": None,
        "result_revision": round(end_s * 1_000),
        "output_source": "controller",
        "lid_open": False,
        "safety_inhibited": False,
        "manual_override": False,
        "stale": False,
        "skipped": False,
        "reset": False,
        "continuous": True,
        "role_generation": 0,
        "observation_sequence": round(end_s * 1_000),
        "scheduled_on_s": duration * duty,
        "realized_auger_duty": duty,
    }
    values.update(overrides)
    return controller.observe_frame(FrameObservation(**values))


def _model_comparison(
    form=ModelForm.FOPDT,
    *,
    gain_offset=0.0,
    parameters=None,
    fit_corpus_digest="1" * 64,
    configuration_digest="2" * 64,
):
    parameters = (
        parameters
        or {
            ModelForm.IPDT: IPDT(K_i=0.4 + gain_offset, c0=-0.02, theta=5.0),
            ModelForm.FOPDT: FOPDT(K=500.0 + gain_offset, tau=800.0, theta=5.0),
            ModelForm.SOPDT: SOPDT(
                K=500.0 + gain_offset,
                tau_1=500.0,
                tau_2=900.0,
                theta=5.0,
            ),
        }[form]
    )
    representative_s = int(parameters.theta)
    episode_ids = ("episode-a", "episode-b", "episode-c")
    basin = DelayBasin(
        lower_s=representative_s,
        upper_s=representative_s,
        representative_s=representative_s,
        confidence_lower_s=representative_s,
        confidence_upper_s=representative_s,
        confidence_method="provided",
        confidence_resamples=0,
        episode_count=3,
        interior=True,
        blockers=(),
    )
    profile = DelayProfile(
        model_form=form.value,
        evaluated_bound_s=300,
        candidate_losses=((representative_s, 1.0),),
        episode_ids=episode_ids,
        basin=basin,
        next_evaluated_bound_s=None,
        blockers=(),
        authorized=True,
    )
    fit = ModelFit(
        form=form,
        parameters=parameters,
        delay_profile=profile,
        one_step_loss=1.0,
        horizon_losses=tuple((horizon, 1.0) for horizon in HORIZONS_S),
        fold_losses=(1.0, 1.0),
        episode_ids=episode_ids,
        common_row_ids=(
            ((6.0, 7.0),),
            ((6.0, 7.0),),
            ((6.0, 7.0),),
        ),
    )
    return compare_model_fits(
        (fit,),
        fit_corpus_digest=fit_corpus_digest,
        configuration_digest=configuration_digest,
    )


def _authorize_controller(controller, form=ModelForm.FOPDT):
    comparison = _model_comparison(
        form,
        configuration_digest=canonical_trajectory_digest(CONFIG),
    )
    for _ in range(CONFIRMATION_WINDOW):
        accepted = controller.accept_model_comparison(comparison)
    assert accepted.authorized
    return accepted


def _authorized_checkpoint(
    form,
    parameters,
    *,
    revision=3,
    provenance="common-validation",
    installation_digest=INSTALLATION_IDENTITY_DIGEST,
    configuration_digest=None,
):
    comparison = _model_comparison(
        form,
        parameters=parameters,
        configuration_digest=(
            canonical_trajectory_digest(CONFIG) if configuration_digest is None else configuration_digest
        ),
    )
    confirmation = ModelConfirmation()
    authorized = None
    for _ in range(CONFIRMATION_WINDOW):
        authorized = compare_model_fits(
            comparison.fits,
            fit_corpus_digest=comparison.fit_corpus_digest,
            configuration_digest=comparison.configuration_digest,
            confirmation=confirmation,
        )
    assert authorized is not None
    assert authorized.selected is not None
    return encode_pid_sp_checkpoint(
        authorized.selected,
        revision=revision,
        provenance=provenance,
        installation_identity_digest=installation_digest,
    )


def _fopdt_checkpoint(
    *,
    revision=3,
    theta=40.0,
    provenance="common-validation",
    installation_digest=INSTALLATION_IDENTITY_DIGEST,
    configuration_digest=None,
):
    return _authorized_checkpoint(
        ModelForm.FOPDT,
        FOPDT(K=800.0, tau=600.0, theta=theta),
        revision=revision,
        provenance=provenance,
        installation_digest=installation_digest,
        configuration_digest=configuration_digest,
    )


def _ipdt_checkpoint(*, revision=3):
    return _authorized_checkpoint(
        ModelForm.IPDT,
        IPDT(K_i=0.46, c0=-0.033, theta=90.0),
        revision=revision,
    )


def test_controller_owns_confirmation_and_activates_only_on_decision_twenty(clock):
    controller = _controller("pid_sp", clock)
    comparison = _model_comparison(ModelForm.SOPDT)

    for expected in range(1, CONFIRMATION_WINDOW):
        accepted = controller.accept_model_comparison(comparison)
        assert accepted.selected is not None
        assert accepted.selected.confirmation_observed == expected
        assert accepted.authorized is False
        assert controller.predictor.active is False
        assert controller.get_model_snapshot() is None
    pending_learning = controller.get_learning_diagnostics().as_json()
    assert pending_learning["comparison"]["primary_blocker"] == "confirmation-pending"
    assert pending_learning["comparison"]["confirmation"] == {
        "observed": CONFIRMATION_WINDOW - 1,
        "required": CONFIRMATION_WINDOW,
    }

    accepted = controller.accept_model_comparison(comparison)

    assert accepted.authorized is True
    assert controller.predictor.active is True
    assert controller.predictor.governing_model()["form"] == "sopdt"
    checkpoint = controller.get_model_snapshot()
    assert checkpoint is not None
    assert decode_pid_sp_checkpoint(checkpoint).selected == accepted.selected
    learning = controller.get_learning_diagnostics().as_json()
    assert learning["status"] == "active"
    assert learning["comparison"]["selected_form"] == "sopdt"
    assert learning["comparison"]["primary_blocker"] is None
    assert learning["comparison"]["comparison_threshold"] == 1.0
    assert learning["comparison"]["selection_margin"] == 0.0
    assert learning["comparison"]["forms"][0]["fold_losses"] == [1.0, 1.0]
    assert learning["comparison"]["forms"][0]["horizon_losses"] == [
        {"horizon_s": horizon, "loss": 1.0} for horizon in HORIZONS_S
    ]
    assert "best_residual" not in learning["identifier"]
    assert "runner_up_residual" not in learning["identifier"]
    assert "candidates_passing" not in learning["identifier"]


def test_controller_confirmation_resets_without_fitting_on_accept_path(clock):
    controller = _controller("pid_sp", clock)
    first = _model_comparison(ModelForm.IPDT)
    changed = _model_comparison(ModelForm.IPDT, gain_offset=0.01)
    for _ in range(7):
        controller.accept_model_comparison(first)

    accepted = controller.accept_model_comparison(changed)

    assert accepted.selected is not None
    assert accepted.selected.confirmation_observed == 1
    assert controller.predictor.active is False


def test_corpus_fit_confirms_once_offpath_and_never_trusts_in_ending_cook(
    monkeypatch,
) -> None:
    corpus_slice = FitCorpusSlice(
        segment_id="segment-a",
        through_ordinal=0,
        prefix_digest="a" * 64,
        segment_content_digest="b" * 64,
        pre_roll_count=0,
        scored_count=1,
    )
    corpus = FitCorpusIdentity(
        schema_version=2,
        corpus_revision=1,
        fit_partition_digest="c" * 64,
        slices=(corpus_slice,),
        corpus_digest=canonical_fit_corpus_digest(
            schema_version=2,
            corpus_revision=1,
            fit_partition_digest="c" * 64,
            slices=(corpus_slice,),
        ),
    )
    repository = SimpleNamespace(
        snapshot_threads=[],
        recorded=[],
        completed=[],
    )
    repository.snapshot_fit_corpus = lambda partition: (
        repository.snapshot_threads.append(threading.get_ident()) or SimpleNamespace(identity=corpus, segments=())
    )
    repository.record_fit_request = lambda snapshot, lineage: repository.recorded.append((snapshot, lineage))
    repository.complete_fit = lambda request_id, **result: repository.completed.append((request_id, result))
    persistence = SimpleNamespace(checkpoints=[], evidence=[], barrier_threads=[])
    persistence.submit_evidence = lambda record: persistence.evidence.append(record) or SimpleNamespace(accepted=True)
    persistence.submit_checkpoint = lambda name, snapshot: (
        persistence.checkpoints.append((name, snapshot, threading.get_ident())) or True
    )
    persistence.submit_durable_checkpoint = lambda name, snapshot: (
        persistence.checkpoints.append((name, snapshot, threading.get_ident()))
        or SimpleNamespace(
            accepted=True,
            completed=True,
            durable=True,
            wait=lambda timeout=None: True,
        )
    )
    persistence.submit_checkpoint_with_terminal_evidence = lambda name, prepared, committed, success, failure: (
        persistence.checkpoints.append((name, prepared, threading.get_ident()))
        or persistence.evidence.append(success)
        or persistence.checkpoints.append((name, committed, threading.get_ident()))
        or persistence.barrier_threads.append(threading.get_ident())
        or SimpleNamespace(
            accepted=True,
            completed=True,
            durable=True,
            wait=lambda timeout=None: True,
        )
    )
    persistence.barrier = lambda timeout=2.0: persistence.barrier_threads.append(threading.get_ident()) or True
    comparison = _model_comparison(ModelForm.FOPDT)

    def fit(request, segments, configuration):
        del segments, configuration
        return PidSpFitResult(
            request=request,
            status=PidSpFitStatus.EVALUATED,
            reason="model-comparison-evaluated",
            delay_profiles=(comparison.fits[0].delay_profile,),
            comparison=comparison,
        )

    monkeypatch.setattr("controller.pid_sp.fit_pid_sp_corpus", fit)
    caller_thread = threading.get_ident()
    controller = PidSpController(
        dict(CONFIG),
        "F",
        {},
        model_persistence=persistence,
        trajectory_repository=repository,
        fit_partition_digest=lambda: "c" * 64,
        installation_identity_provider=lambda: INSTALLATION_IDENTITY,
    )

    assert controller.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    controller.close()

    assert repository.snapshot_threads == [caller_thread]
    assert all(thread != caller_thread for thread in persistence.barrier_threads)
    assert controller.predictor.active is False
    assert controller._model_confirmation.observed == 1
    assert persistence.checkpoints[-1][0] == "pid_sp"
    assert persistence.checkpoints[0][1]["schema"] == "pid-sp-learning-prepare/v1"
    assert persistence.checkpoints[-1][1]["schema"] == "pid-sp-learning-checkpoint/v2"
    assert persistence.checkpoints[-1][1]["installation_identity_digest"] == INSTALLATION_IDENTITY_DIGEST
    payload = persistence.evidence[0].payload
    assert isinstance(payload, PidSpFitDecisionEvidence)
    assert payload.fit_corpus_digest == corpus.corpus_digest
    assert payload.configuration_digest == canonical_trajectory_digest(CONFIG)
    assert payload.parent_incumbent_digest == canonical_pid_sp_observation_model_digest(None)
    assert payload.confirmation_observed == 1
    assert payload.outcome == "rejected"


class _LifecycleRepository:
    def __init__(self, corpus, *, complete_error=None):
        self.snapshot = SimpleNamespace(identity=corpus, segments=())
        self.snapshot_calls = []
        self.recorded = []
        self.completed = []
        self.complete_error = complete_error

    def snapshot_fit_corpus(self, partition):
        self.snapshot_calls.append((partition, threading.get_ident()))
        return self.snapshot

    def record_fit_request(self, snapshot, lineage):
        self.recorded.append((snapshot, lineage))

    def complete_fit(self, request_id, **result):
        if self.complete_error is not None:
            raise self.complete_error
        self.completed.append((request_id, result))


class _LifecyclePersistence:
    def __init__(
        self,
        *,
        accept_evidence=True,
        accept_checkpoint=True,
        durable=True,
    ):
        self.accept_evidence = accept_evidence
        self.accept_checkpoint = accept_checkpoint
        self.durable = durable
        self.evidence = []
        self.checkpoints = []
        self.barriers = []

    @property
    def evidence_blocked(self):
        return not self.accept_evidence

    def contains_evidence(self, record):
        return any(candidate == record for candidate in self.evidence)

    def submit_evidence(self, record):
        if self.accept_evidence:
            self.evidence.append(record)
        return SimpleNamespace(accepted=self.accept_evidence)

    def submit_checkpoint(self, name, snapshot):
        if self.accept_checkpoint:
            self.checkpoints.append((name, copy.deepcopy(snapshot)))
        return self.accept_checkpoint

    def submit_durable_checkpoint(self, name, snapshot):
        accepted = self.accept_checkpoint
        durable = accepted and self.durable
        if durable:
            self.checkpoints.append((name, copy.deepcopy(snapshot)))
        return SimpleNamespace(
            accepted=accepted,
            completed=True,
            durable=durable,
            wait=lambda timeout=None: durable,
        )

    def submit_checkpoint_with_terminal_evidence(
        self,
        name,
        prepared,
        committed,
        success,
        failure,
    ):
        durable = self.accept_checkpoint and self.durable
        if durable:
            self.checkpoints.append((name, copy.deepcopy(prepared)))
            self.evidence.append(success)
            self.checkpoints.append((name, copy.deepcopy(committed)))
        elif self.accept_evidence:
            self.evidence.append(failure)
        return SimpleNamespace(
            accepted=True,
            completed=True,
            durable=durable,
            wait=lambda timeout=None: durable,
        )

    def barrier(self, timeout=2.0):
        self.barriers.append((timeout, threading.get_ident()))
        return self.durable


def _lifecycle_corpus(seed="a"):
    corpus_slice = FitCorpusSlice(
        segment_id=f"segment-{seed}",
        through_ordinal=0,
        prefix_digest=seed * 64,
        segment_content_digest="b" * 64,
        pre_roll_count=0,
        scored_count=1,
    )
    return FitCorpusIdentity(
        schema_version=2,
        corpus_revision=1,
        fit_partition_digest="c" * 64,
        slices=(corpus_slice,),
        corpus_digest=canonical_fit_corpus_digest(
            schema_version=2,
            corpus_revision=1,
            fit_partition_digest="c" * 64,
            slices=(corpus_slice,),
        ),
    )


def _lifecycle_controller(
    monkeypatch,
    result_factory,
    *,
    config=None,
    corpus=None,
    persistence=None,
    repository=None,
    session_id="session-a",
    cook_id="cook-a",
    role_generation=7,
    clock_ms=lambda: 123_456,
):
    corpus = _lifecycle_corpus() if corpus is None else corpus
    repository = _LifecycleRepository(corpus) if repository is None else repository
    persistence = _LifecyclePersistence() if persistence is None else persistence
    monkeypatch.setattr(
        "controller.pid_sp.fit_pid_sp_corpus",
        lambda request, segments, configuration: result_factory(request),
    )
    controller = PidSpController(
        dict(CONFIG if config is None else config),
        "F",
        {},
        model_persistence=persistence,
        trajectory_repository=repository,
        fit_partition_digest=lambda: "c" * 64,
        clock_ms=clock_ms,
        installation_identity_provider=lambda: INSTALLATION_IDENTITY,
    )
    controller.bind_learning_identity(
        session_id,
        cook_id,
        role_generation,
    )
    return controller, repository, persistence, corpus


def test_disabled_corpus_fit_emits_terminal_record_without_fit_or_checkpoint(
    monkeypatch,
) -> None:
    config = {**CONFIG, "enable_identification": False}
    controller, repository, persistence, _ = _lifecycle_controller(
        monkeypatch,
        lambda request: pytest.fail("disabled lifecycle must not fit"),
        config=config,
    )

    assert not controller.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    controller.close()

    assert repository.snapshot_calls == []
    assert persistence.checkpoints == []
    assert persistence.evidence[-1].payload.outcome == "disabled"
    record = persistence.evidence[-1]
    assert record.payload.parent_incumbent_digest == canonical_pid_sp_observation_model_digest(None)
    assert record.provenance_digest is None
    assert record.payload.request_bound is False
    assert record.payload.fit_corpus_digest is None


def test_empty_corpus_stop_is_an_explicit_pre_request_insufficient_terminal(
    monkeypatch,
) -> None:
    repository = _LifecycleRepository(_lifecycle_corpus())

    def empty(_partition):
        repository.snapshot_calls.append(("c" * 64, threading.get_ident()))
        raise FitCorpusEmptyError("fit corpus snapshot has no scored observations")

    repository.snapshot_fit_corpus = empty
    controller, _, persistence, _ = _lifecycle_controller(
        monkeypatch,
        lambda request: pytest.fail("empty corpus must not reach the fitter"),
        repository=repository,
    )

    assert controller.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    controller.close()

    assert repository.recorded == []
    assert persistence.checkpoints == []
    assert len(persistence.evidence) == 1
    record = persistence.evidence[0]
    assert record.payload.outcome == "insufficient"
    assert record.payload.reason == "fit-corpus-empty"
    assert record.payload.request_bound is False
    assert record.payload.fit_corpus_digest is None
    assert record.payload.parent_incumbent_digest == (canonical_pid_sp_observation_model_digest(None))


def test_untyped_snapshot_error_is_a_pre_request_failed_terminal(
    monkeypatch,
) -> None:
    repository = _LifecycleRepository(_lifecycle_corpus())

    def corrupt(_partition):
        raise ValueError("fit corpus snapshot has no scored observations")

    repository.snapshot_fit_corpus = corrupt
    controller, _, persistence, _ = _lifecycle_controller(
        monkeypatch,
        lambda request: pytest.fail("corrupt corpus must not reach the fitter"),
        repository=repository,
    )

    assert controller.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    controller.close()

    assert len(persistence.evidence) == 1
    record = persistence.evidence[0]
    assert record.payload.outcome == "failed"
    assert record.payload.request_bound is False
    assert record.payload.reason.startswith("fit-corpus-snapshot-failed:ValueError:")


def test_barrier_failure_records_pre_request_failed_terminal_without_snapshot(
    monkeypatch,
) -> None:
    controller, repository, persistence, _ = _lifecycle_controller(
        monkeypatch,
        lambda request: pytest.fail("barrier failure must not reach fitter"),
    )

    assert controller.record_corpus_fit_failed(
        CandidateOrigin.PASSIVE_ONLINE,
        "corpus-barrier-failed: trajectory persistence barrier did not become durable",
    )
    controller.close()

    assert repository.snapshot_calls == []
    assert persistence.checkpoints == []
    assert len(persistence.evidence) == 1
    record = persistence.evidence[0]
    assert record.payload.outcome == "failed"
    assert record.payload.reason.startswith("corpus-barrier-failed:")
    assert record.payload.request_bound is False
    assert record.payload.parent_incumbent_digest == (canonical_pid_sp_observation_model_digest(None))


def test_delay_profile_publishes_only_after_offpath_fit_completes(
    monkeypatch,
) -> None:
    comparison = _model_comparison(ModelForm.FOPDT)
    entered = threading.Event()
    release = threading.Event()

    def evaluated(request):
        entered.set()
        assert release.wait(1.0)
        return PidSpFitResult(
            request=request,
            status=PidSpFitStatus.EVALUATED,
            reason="model-comparison-evaluated",
            delay_profiles=(comparison.fits[0].delay_profile,),
            comparison=comparison,
        )

    controller, _, _, _ = _lifecycle_controller(monkeypatch, evaluated)

    assert controller.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    assert entered.wait(1.0)
    assert controller._delay_profile is None
    release.set()
    controller.close()

    assert controller._delay_profile == comparison.fits[0].delay_profile


def test_insufficient_fit_retains_episode_attribution_without_checkpoint(
    monkeypatch,
) -> None:
    episodes = (SimpleNamespace(episode_id="episode-a"),)

    def insufficient(request):
        return PidSpFitResult(
            request=request,
            status=PidSpFitStatus.INSUFFICIENT,
            reason="insufficient-excitation-episodes",
            episodes=episodes,
        )

    controller, _, persistence, _ = _lifecycle_controller(
        monkeypatch,
        insufficient,
    )

    assert controller.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    controller.close()

    payload = persistence.evidence[-1].payload
    assert payload.outcome == "insufficient"
    assert payload.episode_ids == ("episode-a",)
    assert persistence.checkpoints == []
    assert controller.predictor.active is False


def test_fit_publishes_the_selected_forms_delay_profile(monkeypatch) -> None:
    ipdt = _model_comparison(ModelForm.IPDT).fits[0]
    fopdt = _model_comparison(ModelForm.FOPDT).fits[0]
    comparison = compare_model_fits(
        (replace(ipdt, physical_blockers=("candidate-rejected",)), fopdt),
        fit_corpus_digest="1" * 64,
        configuration_digest="2" * 64,
    )
    assert comparison.selected is not None
    assert comparison.selected.form is ModelForm.FOPDT

    def evaluated(request):
        return PidSpFitResult(
            request=request,
            status=PidSpFitStatus.EVALUATED,
            reason="model-comparison-evaluated",
            delay_profiles=(ipdt.delay_profile, fopdt.delay_profile),
            comparison=comparison,
        )

    controller, _, _, _ = _lifecycle_controller(monkeypatch, evaluated)
    assert controller.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    controller.close()

    assert controller._delay_profile is fopdt.delay_profile


def test_blocked_fit_publishes_lowest_loss_provisional_delay_profile(
    monkeypatch,
) -> None:
    ipdt = replace(
        _model_comparison(ModelForm.IPDT).fits[0],
        physical_blockers=("candidate-rejected",),
    )
    fopdt = replace(
        _model_comparison(ModelForm.FOPDT).fits[0],
        one_step_loss=0.5,
        horizon_losses=tuple((horizon, 0.5) for horizon in HORIZONS_S),
        fold_losses=(0.5, 0.5),
        physical_blockers=("candidate-rejected",),
    )
    comparison = compare_model_fits(
        (ipdt, fopdt),
        fit_corpus_digest="1" * 64,
        configuration_digest="2" * 64,
    )
    assert comparison.selected is None

    def blocked(request):
        return PidSpFitResult(
            request=request,
            status=PidSpFitStatus.BLOCKED,
            reason="model-comparison-rejected",
            delay_profiles=(ipdt.delay_profile, fopdt.delay_profile),
            comparison=comparison,
        )

    controller, _, _, _ = _lifecycle_controller(monkeypatch, blocked)
    assert controller.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    controller.close()

    assert controller._delay_profile is fopdt.delay_profile


def test_selector_rejection_preserves_incumbent_and_persists_terminal_decision(
    monkeypatch,
) -> None:
    comparison = _model_comparison(ModelForm.FOPDT)
    rejected_fit = replace(
        comparison.fits[0],
        physical_blockers=("candidate-rejected",),
    )
    rejected = compare_model_fits(
        (rejected_fit,),
        fit_corpus_digest=comparison.fit_corpus_digest,
        configuration_digest=comparison.configuration_digest,
    )

    def blocked(request):
        return PidSpFitResult(
            request=request,
            status=PidSpFitStatus.BLOCKED,
            reason="model-comparison-rejected",
            comparison=rejected,
        )

    controller, _, persistence, _ = _lifecycle_controller(monkeypatch, blocked)
    assert controller.restore_model(_fopdt_checkpoint(revision=3))
    incumbent = copy.deepcopy(controller.get_model_snapshot())

    assert controller.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    controller.close()

    assert controller.get_model_snapshot() == incumbent
    assert persistence.checkpoints == []
    assert persistence.evidence[-1].payload.outcome == "rejected"


def test_fitter_exception_is_terminal_failed_and_preserves_fallback(
    monkeypatch,
) -> None:
    def fail(_request):
        raise RuntimeError("fit worker exploded")

    controller, _, persistence, _ = _lifecycle_controller(monkeypatch, fail)

    assert controller.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    controller.close()

    assert controller.predictor.active is False
    assert persistence.checkpoints == []
    assert persistence.evidence[-1].payload.outcome == "failed"
    assert "fit worker exploded" in persistence.evidence[-1].payload.reason


@pytest.mark.parametrize(
    ("failure", "persistence"),
    (
        ("evidence", _LifecyclePersistence(accept_evidence=False)),
        ("checkpoint", _LifecyclePersistence(accept_checkpoint=False)),
        ("barrier", _LifecyclePersistence(durable=False)),
    ),
)
def test_persistence_failure_is_terminal_checkpoint_failure_and_preserves_incumbent(
    monkeypatch,
    failure,
    persistence,
) -> None:
    comparison = _model_comparison(ModelForm.FOPDT)

    def evaluated(request):
        return PidSpFitResult(
            request=request,
            status=PidSpFitStatus.EVALUATED,
            reason="model-comparison-evaluated",
            comparison=comparison,
        )

    controller, _, persistence, _ = _lifecycle_controller(
        monkeypatch,
        evaluated,
        persistence=persistence,
    )
    assert controller.restore_model(_fopdt_checkpoint(revision=3))
    incumbent = copy.deepcopy(controller.get_model_snapshot())

    assert controller.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    controller.close()

    assert controller.get_model_snapshot() == incumbent
    assert controller._last_fit_outcome.value == "checkpoint-failure"
    assert persistence.checkpoints == []
    terminal = [record for record in persistence.evidence if isinstance(record.payload, PidSpFitDecisionEvidence)]
    assert len(terminal) <= 1
    if terminal:
        assert terminal[0].payload.outcome == "checkpoint-failure", failure


@pytest.mark.parametrize("compound_mode", ("missing-api", "rejected-receipt"))
def test_compound_fallback_records_attempted_confirmation_identity(
    monkeypatch,
    compound_mode,
) -> None:
    comparison = _model_comparison(ModelForm.FOPDT)

    def evaluated(request):
        return PidSpFitResult(
            request=request,
            status=PidSpFitStatus.EVALUATED,
            reason="model-comparison-evaluated",
            comparison=comparison,
        )

    persistence = _LifecyclePersistence()
    if compound_mode == "missing-api":
        persistence.submit_checkpoint_with_terminal_evidence = None
    else:
        persistence.submit_checkpoint_with_terminal_evidence = lambda *_args: SimpleNamespace(
            accepted=False,
            completed=False,
            durable=False,
            wait=lambda timeout=None: False,
        )
    controller, _, persistence, _ = _lifecycle_controller(
        monkeypatch,
        evaluated,
        persistence=persistence,
    )

    assert controller.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    controller.close()

    assert controller._last_fit_outcome is PidSpLearningOutcome.CHECKPOINT_FAILURE
    assert len(persistence.evidence) == 1
    terminal = persistence.evidence[0].payload
    assert terminal.outcome == "checkpoint-failure"
    assert terminal.selected_form == "fopdt"
    assert terminal.confirmation_observed == 1
    assert terminal.confirmation_candidate_digest is not None


def test_fit_manifest_completion_failure_never_queues_activatable_checkpoint(
    monkeypatch,
) -> None:
    comparison = _model_comparison(ModelForm.FOPDT)

    def evaluated(request):
        return PidSpFitResult(
            request=request,
            status=PidSpFitStatus.EVALUATED,
            reason="model-comparison-evaluated",
            comparison=comparison,
        )

    corpus = _lifecycle_corpus()
    repository = _LifecycleRepository(
        corpus,
        complete_error=RuntimeError("manifest write failed"),
    )
    controller, _, persistence, _ = _lifecycle_controller(
        monkeypatch,
        evaluated,
        corpus=corpus,
        repository=repository,
    )

    assert controller.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    controller.close()

    assert persistence.checkpoints == []
    assert len(persistence.evidence) == 1
    assert persistence.evidence[0].payload.outcome == "checkpoint-failure"
    assert persistence.evidence[0].payload.reason == "fit-run-persistence-failed"
    assert persistence.evidence[0].payload.selected_form == "fopdt"
    assert persistence.evidence[0].payload.confirmation_candidate_digest is not None
    fresh = PidSpController(dict(CONFIG), "F", {})
    assert fresh.predictor.active is False


def test_candidate_change_checkpoint_failure_keeps_attempted_confirmation_identity(
    monkeypatch,
) -> None:
    corpus = _lifecycle_corpus()
    changed = _model_comparison(ModelForm.FOPDT, gain_offset=0.01)

    def evaluated(request):
        return PidSpFitResult(
            request=request,
            status=PidSpFitStatus.EVALUATED,
            reason="model-comparison-evaluated",
            comparison=changed,
        )

    repository = _LifecycleRepository(
        corpus,
        complete_error=RuntimeError("manifest write failed"),
    )
    controller, _, persistence, _ = _lifecycle_controller(
        monkeypatch,
        evaluated,
        corpus=corpus,
        repository=repository,
    )
    previous = _model_comparison(ModelForm.FOPDT)
    for _ in range(3):
        controller.accept_model_comparison(previous, activate=False)
    previous_key, _ = controller._model_confirmation.snapshot()
    configuration_digest = canonical_trajectory_digest(CONFIG)
    controller._durable_confirmation_identity = (
        corpus.corpus_digest,
        configuration_digest,
        canonical_pid_sp_observation_model_digest(None),
    )
    attempted_confirmation = ModelConfirmation()
    compare_model_fits(
        changed.fits,
        fit_corpus_digest=corpus.corpus_digest,
        configuration_digest=configuration_digest,
        confirmation=attempted_confirmation,
    )
    attempted_key, _ = attempted_confirmation.snapshot()
    assert attempted_key != previous_key

    assert controller.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    controller.close()

    assert len(persistence.evidence) == 1
    terminal = persistence.evidence[0].payload
    assert terminal.outcome == "checkpoint-failure"
    assert terminal.confirmation_observed == 1
    assert terminal.confirmation_candidate_digest == attempted_key
    assert controller._model_confirmation.snapshot() == (previous_key, 3)


def test_identical_causal_fit_replay_has_identical_request_and_evidence_bytes(
    monkeypatch,
) -> None:
    comparison = _model_comparison(ModelForm.FOPDT)

    def evaluated(request):
        return PidSpFitResult(
            request=request,
            status=PidSpFitStatus.EVALUATED,
            reason="model-comparison-evaluated",
            comparison=comparison,
        )

    replays = []
    for _ in range(2):
        controller, repository, persistence, _ = _lifecycle_controller(
            monkeypatch,
            evaluated,
        )
        assert controller.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
        controller.close()
        replays.append(
            (
                repository.recorded[0][1].request_id,
                persistence.evidence[0].model_dump_json(),
            )
        )

    assert replays[0] == replays[1]

    changed, repository, persistence, _ = _lifecycle_controller(
        monkeypatch,
        evaluated,
        cook_id="cook-b",
    )
    assert changed.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    changed.close()

    assert repository.recorded[0][1].request_id != replays[0][0]
    assert persistence.evidence[0].model_dump_json() != replays[0][1]


def test_scheduled_fit_owns_one_corpus_snapshot_and_ticket_identity(
    monkeypatch,
) -> None:
    def insufficient(request):
        return PidSpFitResult(
            request=request,
            status=PidSpFitStatus.INSUFFICIENT,
            reason="insufficient-excitation-episodes",
        )

    first_corpus = _lifecycle_corpus("a")
    second_corpus = _lifecycle_corpus("d")
    repository = _LifecycleRepository(first_corpus)
    controller, _, _, _ = _lifecycle_controller(
        monkeypatch,
        insufficient,
        corpus=first_corpus,
        repository=repository,
    )
    first_snapshot = repository.snapshot

    first_ticket = controller._schedule_corpus_fit_ticket(CandidateOrigin.PASSIVE_ONLINE)
    repository.snapshot = SimpleNamespace(
        identity=second_corpus,
        segments=(),
    )
    assert controller.poll_learning_off_path() is PidSpLearningOutcome.INSUFFICIENT

    second_ticket = controller._schedule_corpus_fit_ticket(CandidateOrigin.PASSIVE_ONLINE)
    assert controller.poll_learning_off_path() is PidSpLearningOutcome.INSUFFICIENT
    controller.close()

    assert repository.recorded[0][0] is first_snapshot
    assert repository.recorded[1][0] is repository.snapshot
    assert first_ticket != second_ticket
    assert repository.recorded[0][1].request_id != repository.recorded[1][1].request_id


def test_twentieth_offpath_decision_checkpoints_for_cold_next_cook_without_live_trust(
    monkeypatch,
) -> None:
    comparison = _model_comparison(ModelForm.FOPDT)

    def evaluated(request):
        return PidSpFitResult(
            request=request,
            status=PidSpFitStatus.EVALUATED,
            reason="model-comparison-evaluated",
            comparison=comparison,
        )

    controller, _, persistence, corpus = _lifecycle_controller(
        monkeypatch,
        evaluated,
    )

    configuration_digest = canonical_trajectory_digest(CONFIG)
    normalized = compare_model_fits(
        comparison.fits,
        fit_corpus_digest=corpus.corpus_digest,
        configuration_digest=configuration_digest,
    )
    for _ in range(CONFIRMATION_WINDOW - 1):
        controller.accept_model_comparison(normalized, activate=False)
    controller._durable_confirmation_identity = (
        corpus.corpus_digest,
        configuration_digest,
        canonical_pid_sp_observation_model_digest(None),
    )

    assert controller.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    controller.close()

    assert controller.predictor.active is False
    assert persistence.evidence[-1].payload.outcome == "accepted-next-cook"
    checkpoint = persistence.checkpoints[-1][1]
    assert checkpoint["schema_version"] == 3
    assert checkpoint["installation_identity_digest"] == INSTALLATION_IDENTITY_DIGEST
    fresh = PidSpController(
        dict(CONFIG),
        "F",
        {},
        installation_identity_provider=lambda: INSTALLATION_IDENTITY,
    )
    assert fresh.restore_model(checkpoint)
    assert fresh.predictor.active is True


def test_core_worker_drains_fit_scheduled_while_another_fit_is_running(
    monkeypatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def insufficient(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(1.0)
        return PidSpFitResult(
            request=request,
            status=PidSpFitStatus.INSUFFICIENT,
            reason="insufficient-excitation-episodes",
        )

    first_corpus = _lifecycle_corpus("a")
    repository = _LifecycleRepository(first_corpus)
    controller, _, persistence, _ = _lifecycle_controller(
        monkeypatch,
        insufficient,
        corpus=first_corpus,
        repository=repository,
    )

    assert controller.schedule_corpus_fit(CandidateOrigin.OPERATOR_CALIBRATION)
    assert entered.wait(1.0)
    repository.snapshot = SimpleNamespace(
        identity=_lifecycle_corpus("d"),
        segments=(),
    )
    assert controller.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    release.set()
    controller.close()

    assert len(repository.recorded) == 2
    assert len(persistence.evidence) == 2
    assert {record.payload.origin for record in persistence.evidence} == {"operator-calibration", "passive-online"}


def test_core_worker_serializes_disabled_terminal_behind_running_fit(
    monkeypatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def insufficient(request):
        entered.set()
        assert release.wait(1.0)
        return PidSpFitResult(
            request=request,
            status=PidSpFitStatus.INSUFFICIENT,
            reason="insufficient-excitation-episodes",
        )

    controller, _, persistence, _ = _lifecycle_controller(
        monkeypatch,
        insufficient,
    )

    assert controller.schedule_corpus_fit(CandidateOrigin.OPERATOR_CALIBRATION)
    assert entered.wait(1.0)
    assert controller.record_corpus_fit_disabled(
        CandidateOrigin.PASSIVE_ONLINE,
        "identification-disabled",
    )
    release.set()
    controller.close()

    assert [record.payload.outcome for record in persistence.evidence] == ["insufficient", "disabled"]


@pytest.mark.parametrize(
    ("crash_boundary", "confirmation_observed"),
    (
        ("after-prepare", 0),
        ("after-terminal", 1),
        ("after-commit", 1),
    ),
)
def test_prepared_checkpoint_cold_recovery_at_every_durable_boundary(
    monkeypatch,
    crash_boundary,
    confirmation_observed,
) -> None:
    comparison = _model_comparison(ModelForm.FOPDT)

    def evaluated(request):
        return PidSpFitResult(
            request=request,
            status=PidSpFitStatus.EVALUATED,
            reason="model-comparison-evaluated",
            comparison=comparison,
        )

    source, _, persistence, _ = _lifecycle_controller(
        monkeypatch,
        evaluated,
    )
    assert source.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    source.close()
    prepared = persistence.checkpoints[0][1]
    committed = persistence.checkpoints[-1][1]
    terminal = persistence.evidence[0]

    recovery_persistence = _LifecyclePersistence()
    if crash_boundary == "after-terminal":
        recovery_persistence.evidence.append(terminal)
    recovered = PidSpController(
        dict(CONFIG),
        "F",
        {},
        model_persistence=recovery_persistence,
        installation_identity_provider=lambda: INSTALLATION_IDENTITY,
    )
    snapshot = committed if crash_boundary == "after-commit" else prepared

    assert recovered.restore_model(snapshot)
    assert recovered.predictor.active is False
    assert recovered._model_confirmation.observed == confirmation_observed


@pytest.mark.parametrize(
    "corruption",
    (
        "malformed-terminal",
        "mismatched-terminal",
        "mismatched-proposed-corpus",
        "mismatched-proposed-candidate",
        "mismatched-proposed-parent",
        "mismatched-proposed-generation",
    ),
)
def test_prepared_checkpoint_aborts_when_terminal_commitment_is_not_exact(
    monkeypatch,
    corruption,
) -> None:
    comparison = _model_comparison(ModelForm.FOPDT)

    def evaluated(request):
        return PidSpFitResult(
            request=request,
            status=PidSpFitStatus.EVALUATED,
            reason="model-comparison-evaluated",
            comparison=comparison,
        )

    source, _, persistence, _ = _lifecycle_controller(
        monkeypatch,
        evaluated,
    )
    assert source.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    source.close()
    prepared = copy.deepcopy(persistence.checkpoints[0][1])
    terminal = persistence.evidence[0]
    recovery_persistence = _LifecyclePersistence()
    if corruption == "malformed-terminal":
        prepared["terminal_evidence_json"] = "{}"
        recovery_persistence.evidence.append(terminal)
    elif corruption == "mismatched-terminal":
        recovery_persistence.evidence.append(
            terminal.model_copy(
                update={
                    "payload": replace(
                        terminal.payload,
                        reason="different-terminal-lineage",
                    )
                }
            )
        )
    else:
        recovery_persistence.evidence.append(terminal)
        proposed = prepared["proposed"]
        assert isinstance(proposed, dict)
        checkpoint = proposed["checkpoint"]
        lineage = proposed["lineage"]
        assert isinstance(checkpoint, dict)
        assert isinstance(lineage, dict)
        if corruption == "mismatched-proposed-corpus":
            identity = checkpoint["identity"]
            assert isinstance(identity, dict)
            identity["fit_corpus_digest"] = "d" * 64
        elif corruption == "mismatched-proposed-candidate":
            lineage["candidate_digest"] = "d" * 64
        elif corruption == "mismatched-proposed-parent":
            lineage["parent_incumbent_digest"] = "d" * 64
        else:
            lineage["parent_incumbent_generation"] += 1
            lineage["candidate_generation"] += 1
    recovered = PidSpController(
        dict(CONFIG),
        "F",
        {},
        model_persistence=recovery_persistence,
        installation_identity_provider=lambda: INSTALLATION_IDENTITY,
    )

    assert recovered.restore_model(prepared)
    assert recovered.predictor.active is False
    assert recovered._model_confirmation.observed == 0


@pytest.mark.parametrize("changed", ("corpus", "config", "candidate", "incumbent"))
def test_changed_lifecycle_identity_resets_durable_confirmation(
    monkeypatch,
    changed,
) -> None:
    comparison = _model_comparison(
        ModelForm.FOPDT,
        gain_offset=0.01 if changed == "candidate" else 0.0,
    )

    def evaluated(request):
        return PidSpFitResult(
            request=request,
            status=PidSpFitStatus.EVALUATED,
            reason="model-comparison-evaluated",
            comparison=comparison,
        )

    config = {**CONFIG, "center_factor": 0.002} if changed == "config" else CONFIG
    corpus = _lifecycle_corpus("d") if changed == "corpus" else _lifecycle_corpus()
    controller, _, persistence, corpus = _lifecycle_controller(
        monkeypatch,
        evaluated,
        config=config,
        corpus=corpus,
    )
    base = _model_comparison(ModelForm.FOPDT)
    for _ in range(7):
        controller.accept_model_comparison(base, activate=False)
    controller._durable_confirmation_identity = (
        _lifecycle_corpus().corpus_digest,
        canonical_trajectory_digest(CONFIG),
        ("f" * 64 if changed == "incumbent" else canonical_pid_sp_observation_model_digest(None)),
    )

    assert controller.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    controller.close()

    assert persistence.evidence[-1].payload.confirmation_observed == 1


def test_failed_activation_preflight_is_transactional(clock, monkeypatch):
    controller = _controller("pid_sp", clock)
    _authorize_controller(controller, ModelForm.FOPDT)
    candidate = _model_comparison(ModelForm.SOPDT)
    for _ in range(CONFIRMATION_WINDOW - 1):
        controller.accept_model_comparison(candidate)

    incumbent_snapshot = copy.deepcopy(controller.get_model_snapshot())
    incumbent_identifier = copy.deepcopy(controller.identifier.status())
    incumbent_predictor = copy.deepcopy(controller.predictor.status())
    incumbent_comparison = controller._model_comparison
    incumbent_revision = controller._model_revision
    incumbent_diagnostics = json.dumps(
        controller.get_learning_diagnostics().as_json(),
        sort_keys=True,
        separators=(",", ":"),
    )
    monkeypatch.setattr(
        controller.predictor,
        "preflight_trust",
        lambda _model, *, authority_digest: False,
        raising=False,
    )

    with pytest.raises(ValueError, match="predictor preflight"):
        controller.accept_model_comparison(candidate)

    assert controller.identifier.status() == incumbent_identifier
    assert controller.predictor.status() == incumbent_predictor
    assert controller.get_model_snapshot() == incumbent_snapshot
    assert controller._model_comparison is incumbent_comparison
    assert controller._model_revision == incumbent_revision
    assert (
        json.dumps(
            controller.get_learning_diagnostics().as_json(),
            sort_keys=True,
            separators=(",", ":"),
        )
        == incumbent_diagnostics
    )


def test_new_authority_digest_reenables_disabled_predictor_with_same_physics(clock):
    controller = _controller("pid_sp", clock)
    first = _authorize_controller(controller, ModelForm.FOPDT)
    assert first.selected is not None
    controller.predictor._disable()
    assert controller.predictor.active is False

    changed_corpus = _model_comparison(
        ModelForm.FOPDT,
        fit_corpus_digest="3" * 64,
    )
    for _ in range(CONFIRMATION_WINDOW):
        accepted = controller.accept_model_comparison(changed_corpus)

    assert accepted.selected is not None
    assert accepted.selected.model_digest != first.selected.model_digest
    assert controller.predictor.active is True
    assert (
        decode_pid_sp_checkpoint(controller.get_model_snapshot()).selected.model_digest
        == accepted.selected.model_digest
    )


@pytest.mark.parametrize(
    "invalid",
    [
        {"revision": 1, "K": 800.0, "tau": 600.0, "theta": 40.0},
        {"schema_version": 1, "revision": 1, "provenance": "legacy", "selected": {}},
        {
            "schema_version": 2,
            "revision": 1,
            "provenance": "formless",
            "selected": {},
        },
    ],
)
def test_restore_rejects_legacy_old_schema_and_formless_without_output(
    clock,
    invalid,
):
    controller = _controller("pid_sp", clock)

    assert controller.restore_model(invalid) is False
    assert controller.predictor.active is False
    assert controller.get_model_snapshot() is None


def test_restore_activates_only_same_installation_current_configuration(clock):
    checkpoint = _fopdt_checkpoint(revision=7)
    controller = _controller("pid_sp", clock)

    assert controller.restore_model(checkpoint) is True
    assert controller.predictor.active is True
    assert controller._restore_revalidation_candidate is None


@pytest.mark.parametrize(
    (
        "installation_identity",
        "rebind_pending_identity",
        "expected_observed",
        "expected_active",
    ),
    [
        (INSTALLATION_IDENTITY, False, 1, True),
        (b"other-installation", False, 0, False),
        (b"other-installation", True, 0, False),
    ],
    ids=[
        "same-installation",
        "transplanted-installation",
        "matching-outer-identity-mismatched-incumbent",
    ],
)
def test_pending_confirmation_restores_only_with_bound_installation_authority(
    clock,
    installation_identity,
    rebind_pending_identity,
    expected_observed,
    expected_active,
):
    source = _controller("pid_sp", clock)
    _authorize_controller(source)
    source._model_confirmation.observe("d" * 64)
    source._durable_confirmation_identity = (
        "1" * 64,
        canonical_trajectory_digest(CONFIG),
        source._current_incumbent_digest(),
    )
    checkpoint = source.get_model_snapshot()
    assert checkpoint is not None
    if rebind_pending_identity:
        checkpoint["installation_identity_digest"] = installation_identity_digest(lambda: installation_identity)
    restored = _controller(
        "pid_sp",
        clock,
        installation_identity=installation_identity,
    )

    assert restored.restore_model(checkpoint) is True
    assert restored._model_confirmation.observed == expected_observed
    assert restored.predictor.active is expected_active
    assert (restored._durable_confirmation_identity is not None) is expected_active


@pytest.mark.parametrize("authority", ["legacy", "missing", "mismatched", "stale-configuration"])
def test_restore_stages_untrusted_checkpoint_inert_for_passive_revalidation(
    clock,
    authority,
):
    if authority == "stale-configuration":
        checkpoint = _fopdt_checkpoint(configuration_digest="f" * 64)
    else:
        checkpoint = _fopdt_checkpoint(
            installation_digest=(
                None if authority == "missing" else installation_identity_digest(lambda: b"other-installation")
            )
        )
        if authority == "legacy":
            checkpoint["schema_version"] = 2
            checkpoint.pop("installation_identity_digest")
    controller = _controller("pid_sp", clock)
    selected = decode_pid_sp_checkpoint(checkpoint).selected

    assert controller.restore_model(checkpoint) is True
    assert controller.predictor.active is False
    assert controller.get_model_snapshot() is None
    assert controller._restore_revalidation_candidate is not None
    assert controller._restore_revalidation_candidate.selected == selected


def test_passive_current_plant_confirmation_promotes_staged_restore_candidate(clock):
    configuration_digest = canonical_trajectory_digest(CONFIG)
    parameters = FOPDT(K=500.0, tau=800.0, theta=5.0)
    checkpoint = _authorized_checkpoint(
        ModelForm.FOPDT,
        parameters,
        installation_digest=installation_identity_digest(lambda: b"other-installation"),
        configuration_digest=configuration_digest,
    )
    candidate_digest = decode_pid_sp_checkpoint(checkpoint).selected.model_digest
    comparison = _model_comparison(
        ModelForm.FOPDT,
        parameters=parameters,
        configuration_digest=configuration_digest,
    )
    controller = _controller("pid_sp", clock)
    assert controller.restore_model(checkpoint) is True

    for _ in range(CONFIRMATION_WINDOW):
        confirmed = controller.accept_model_comparison(comparison)

    assert confirmed.authorized is True
    assert controller.predictor.active is True
    assert controller._active_selected_model is not None
    assert controller._active_selected_model.model_digest == candidate_digest
    assert controller._restore_revalidation_candidate is None


def test_passive_current_plant_rejection_retires_staged_restore_candidate(clock):
    checkpoint = _fopdt_checkpoint(installation_digest=installation_identity_digest(lambda: b"other-installation"))
    rejected_fit = replace(
        _model_comparison(
            configuration_digest=canonical_trajectory_digest(CONFIG),
        ).fits[0],
        physical_blockers=("candidate-rejected",),
    )
    rejected = compare_model_fits(
        (rejected_fit,),
        fit_corpus_digest="1" * 64,
        configuration_digest=canonical_trajectory_digest(CONFIG),
    )
    controller = _controller("pid_sp", clock)
    assert controller.restore_model(checkpoint) is True

    confirmation = controller.accept_model_comparison(rejected)

    assert confirmation.selected is None
    assert controller.predictor.active is False
    assert controller._restore_revalidation_candidate is None


def test_authorized_restore_is_active_before_first_tick_and_rejection_keeps_incumbent(
    clock,
):
    source = _controller("pid_sp", clock)
    _authorize_controller(source, ModelForm.SOPDT)
    checkpoint = source.get_model_snapshot()
    assert checkpoint is not None

    restored = _controller("pid_sp", clock)
    assert restored.restore_model(checkpoint) is True
    assert restored.predictor.active is True
    incumbent = restored.predictor.governing_model()

    forged = copy.deepcopy(checkpoint)
    forged["selected"]["model_digest"] = "0" * 64
    assert restored.restore_model(forged) is False
    assert restored.predictor.governing_model() == incumbent


def test_restored_checkpoint_discloses_active_authority_without_live_comparison(clock):
    checkpoint = _fopdt_checkpoint(revision=7)
    selected = decode_pid_sp_checkpoint(checkpoint).selected
    controller = _controller("pid_sp", clock)

    assert controller.restore_model(checkpoint) is True
    learning = controller.get_learning_diagnostics().as_json()

    assert learning["status"] == "active"
    assert learning["comparison"] is None
    assert learning["confirmation"] == {
        "observed": CONFIRMATION_WINDOW,
        "required": CONFIRMATION_WINDOW,
    }
    assert learning["active_model"] == {
        "form": "fopdt",
        "model_digest": selected.model_digest,
    }


def test_pid_sp_paces_its_guards_off_the_real_auger_frame(clock):
    """The three-cycle windows below are three control cycles. The control
    cycle is the auger's pulse frame, which is what actually paces the auger,
    and it comes from the platform rather than from any setting -- so an empty
    cycle_data gives the guards the frame they mean."""
    sp = _controller("pid_sp", clock)
    assert sp.cycle_time == AUGER_TIMING.frame_s


@pytest.mark.parametrize(
    ("raw_output", "expected_output", "expected_reason"),
    [
        pytest.param(1.25, 1.0, AllocationClampReason.AUGER_MAX, id="upper-bound"),
        pytest.param(-0.25, 0.0, AllocationClampReason.AUGER_MIN, id="lower-bound"),
    ],
)
def test_pid_sp_owns_bounded_direct_auger_allocation_after_every_update(
    clock,
    monkeypatch,
    raw_output,
    expected_output,
    expected_reason,
):
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    sp.last = 225.0
    monkeypatch.setattr(sp, "_bias", lambda: raw_output)
    sp.kp = sp.ki = sp.kd = 0.0
    clock.t += sp.cycle_time * 3

    output = sp.update(225.0)

    assert output == raw_output
    assert sp.u == expected_output
    diagnostics = sp.trace_diagnostics()
    assert diagnostics.raw_output == raw_output
    assert diagnostics.final_output == expected_output
    allocation = sp.trace_allocation()
    assert isinstance(allocation, AllocationResult)
    assert (
        allocation.normalized_combustion_load,
        allocation.auger_duty,
        allocation.fan_duty,
        allocation.u_max,
        allocation.fan_min_pct,
        allocation.fan_max_pct,
        allocation.fan_enabled,
        allocation.auger_clamp_reason,
        allocation.fan_clamp_reason,
    ) == (
        expected_output,
        expected_output,
        None,
        1.0,
        0.0,
        0.0,
        False,
        expected_reason,
        AllocationClampReason.NONE,
    )


def test_pid_sp_nonfinite_output_fails_closed_before_allocation(clock, monkeypatch):
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    sp.new_target = False
    sp.last = 225.0
    clock.t += sp.cycle_time * 3
    monkeypatch.setattr(
        sp,
        "_seed_integral_from_identified_hold",
        lambda _error: setattr(sp, "u", math.nan),
    )

    assert sp.update(225.0) == 0.0
    diagnostics = sp.trace_diagnostics()
    assert diagnostics.raw_output == diagnostics.final_output == 0.0
    allocation = sp.trace_allocation()
    assert allocation.normalized_combustion_load == allocation.auger_duty == 0.0
    assert allocation.auger_clamp_reason is AllocationClampReason.AUGER_NONFINITE
    assert allocation.fan_clamp_reason is AllocationClampReason.NONE


def test_pid_sp_completed_frame_returns_observation_outcome(clock):
    controller = _controller("pid_sp", clock)
    observation = FrameObservation(
        frame_start_s=0.0,
        frame_end_s=20.0,
        temp_c=100.0,
        setpoint_c=120.0,
        ambient_c=20.0,
        requested_q=0.25,
        realized_q=0.25,
        requested_auger_duty=0.25,
        delivered_on_s=5.0,
        requested_fan_duty=None,
        actual_fan_duty=None,
        result_revision=1,
        output_source="controller",
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=True,
        role_generation=0,
        observation_sequence=1,
        scheduled_on_s=5.0,
        realized_auger_duty=0.25,
    )

    outcome = controller.observe_frame(observation)

    assert outcome["controller"] == "pid_sp"
    assert outcome["eligible"] is True
    assert outcome["rejection_reasons"] == ()
    assert outcome["effective_updates"] == 1


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        ({"probe_valid": False}, PidSpObservationDecision.INVALID_PROBE.value),
        (
            {"output_source": OutputSource.MANUAL_OVERRIDE.value},
            PidSpObservationDecision.NON_CONTROLLER_OUTPUT.value,
        ),
        ({"continuous": False}, PidSpObservationDecision.DISCONTINUOUS.value),
        ({"lid_open": True}, PidSpObservationDecision.INHIBITED.value),
    ],
)
def test_rejected_frame_interrupts_but_never_contaminates_delay_episode(
    clock,
    overrides,
    expected_reason,
):
    controller = _controller("pid_sp", clock)
    for index in range(4):
        _observe_completed_frame(
            controller,
            index * 20.0,
            (index + 1) * 20.0,
            40.0,
            duty=0.2,
        )
    for index in range(4, 8):
        _observe_completed_frame(
            controller,
            index * 20.0,
            (index + 1) * 20.0,
            40.0 + index,
            duty=0.6,
        )

    _observe_completed_frame(
        controller,
        160.0,
        180.0,
        50.0,
        duty=0.9,
        **overrides,
    )

    episodes = controller.completed_excitation_episodes()
    assert len(episodes) == 1
    assert type(episodes[0].terminal_reason) is str
    assert episodes[0].terminal_reason == expected_reason
    assert episodes[0].intervals[-1].end_s == 160.0
    diagnostics = controller.get_learning_diagnostics().state
    assert diagnostics["delay_evidence"]["completed_episode_count"] == 1


def test_interrupt_retains_accepted_pending_transition_intervals(clock):
    controller = _controller("pid_sp", clock)
    for index in range(4):
        _observe_completed_frame(
            controller,
            index * 20.0,
            (index + 1) * 20.0,
            40.0,
            duty=0.2,
        )
    for index in range(4, 8):
        _observe_completed_frame(
            controller,
            index * 20.0,
            (index + 1) * 20.0,
            40.0 + index,
            duty=0.6,
        )
    _observe_completed_frame(controller, 160.0, 180.0, 50.0, duty=0.9)

    _observe_completed_frame(
        controller,
        180.0,
        200.0,
        51.0,
        duty=0.9,
        lid_open=True,
    )

    (episode,) = controller.completed_excitation_episodes()
    assert type(episode.terminal_reason) is str
    assert episode.terminal_reason == PidSpObservationDecision.INHIBITED.value
    assert episode.intervals[-1].end_s == 180.0


def test_the_startup_reduction_is_applied_to_the_new_output(clock):
    """Within the first three cycles after a setpoint change, u is the newly
    computed p+i+d scaled by STARTUP_REDUCTION -- not a stale prior output.



    Pinned against PID-SP's own p+i+d rather than another controller's output,
    so the assertion isolates the reduction rather than any difference in how
    the two seed their first derivative."""
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    clock.t += 20.0  # inside cycle_time * 3 of the setpoint change
    out_sp = sp.update(200.0)
    status = sp.get_status()
    assert out_sp == pytest.approx((status["p"] + status["i"] + status["d"]) * STARTUP_REDUCTION)


def test_the_reduction_stops_after_three_cycles(clock):
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    clock.t += 20.0 * 3 + 1
    out_sp = sp.update(200.0)
    status = sp.get_status()
    assert out_sp == pytest.approx(status["p"] + status["i"] + status["d"])


def test_the_reduction_stops_exactly_at_the_three_cycle_boundary(clock):
    """60 == cycle_time * 3 exactly: the guard's strict `<` must already read
    False on the boundary itself, not one tick early or late."""
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    clock.t += 20.0 * 3  # current_time - last_set_time == 60, not < 60
    out_sp = sp.update(200.0)
    status = sp.get_status()
    assert out_sp == pytest.approx(status["p"] + status["i"] + status["d"])


def test_repeated_identical_clock_values_keep_dt_floored_at_pid_sp_level(clock):
    """pid_sp calls _elapsed_since_last_update() at its own call site rather
    than inheriting a tested one; a raw subtraction here divides by exactly
    0.0 on a repeated clock reading and raises, rather than returning
    something merely inaccurate."""
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    sp.update(220.0)  # error == -5, else branch
    result = sp.update(221.0)  # same clock value, still else branch
    assert math.isfinite(result)


def test_a_trusted_model_makes_the_selected_temperature_diverge_from_measured(clock):
    """The first tick only anchors the clock; the correction appears on the
    second, once duty has been integrated across a real interval."""
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    sp.restore_model(_fopdt_checkpoint(revision=1))
    frame_start = clock.t
    clock.t += 20.0
    _observe_completed_frame(sp, frame_start, clock.t, (200.0 - 32.0) * 5.0 / 9.0)
    assert sp.update(200.0) is not None
    assert sp.get_status()["predictor"]["active"] is True
    assert sp.get_status()["selected_temp"] == 200.0  # anchored, correction still zero
    frame_start = clock.t
    clock.t += 20.0
    _observe_completed_frame(sp, frame_start, clock.t, (200.0 - 32.0) * 5.0 / 9.0)
    sp.update(200.0)
    status = sp.get_status()
    selected = status["selected_temp"]
    assert selected > 200.0  # x0 has moved, xd has not
    # P and error are computed from the corrected temperature, not the raw
    # probe reading -- the substitution this whole task exists to make.
    assert status["error"] == pytest.approx(selected - 225.0)
    assert status["p"] == pytest.approx(sp.kp * status["error"] + sp.center)


def test_disabled_predictor_reports_fallback_model_digest_while_identifier_remains_trusted(clock):
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    assert sp.restore_model(_fopdt_checkpoint(revision=1, theta=5.0))

    for measured_f in (200.0, 400.0, 600.0, 800.0, 1000.0):
        frame_start = clock.t
        clock.t += 20.0
        _observe_completed_frame(
            sp,
            frame_start,
            clock.t,
            (measured_f - 32.0) * 5.0 / 9.0,
            duty=0.0,
        )
        sp.update(measured_f)

    trusted_model = sp.identifier.trusted_model()
    fallback_digest = canonical_pid_sp_observation_model_digest(None)
    assert trusted_model is not None
    assert sp.predictor.status()["disabled"] is True
    assert canonical_pid_sp_observation_model_digest(trusted_model) != fallback_digest

    frame_start = clock.t
    clock.t += 20.0
    outcome = _observe_completed_frame(
        sp,
        frame_start,
        clock.t,
        (1000.0 - 32.0) * 5.0 / 9.0,
        duty=0.0,
    )

    assert outcome["eligible"] is True
    assert outcome["model_digest"] == fallback_digest


def test_the_derivative_never_mixes_a_measured_and_a_predicted_sample(clock):
    """Both terms of the derivative come from the selected series -- including
    the PREVIOUS one held in self.last, not just the current tick. The first
    tick anchors (selected == measured by construction), so a test that stops
    at tick two can't see self.last holding a raw measured value; it has to
    reach a third tick where the previous sample was itself corrected."""
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    sp.restore_model(_fopdt_checkpoint(revision=1))
    frame_start = clock.t
    clock.t += 20.0
    _observe_completed_frame(sp, frame_start, clock.t, (200.0 - 32.0) * 5.0 / 9.0)
    sp.update(200.0)  # anchors: selected == measured
    frame_start = clock.t
    clock.t += 20.0
    _observe_completed_frame(sp, frame_start, clock.t, (205.0 - 32.0) * 5.0 / 9.0)
    sp.update(205.0)
    second = sp.get_status()["selected_temp"]
    assert second != 205.0, "the predictor is not correcting; the test proves nothing"
    frame_start = clock.t
    clock.t += 20.0
    _observe_completed_frame(sp, frame_start, clock.t, (210.0 - 32.0) * 5.0 / 9.0)
    sp.update(210.0)
    status = sp.get_status()
    third = status["selected_temp"]
    assert status["d"] == pytest.approx(sp.kd * (third - second) / 20.0)


def test_set_target_preserves_the_learned_model(clock):
    sp = _controller("pid_sp", clock)
    sp.restore_model(_fopdt_checkpoint(revision=1))
    sp.set_target(275.0)
    assert sp.get_model_snapshot()["selected"]["parameters"]["K"] == 800.0
    assert sp.inter == 0.0  # but the target-dependent PID terms do reset


def test_no_tau_or_theta_config_is_read(clock):
    """A user-supplied tau=115 is outside the design's own trusted band.
    controller/controllers.json still advertises the options; what this pins is
    that pid_sp does not read them."""
    import controller.pid_sp as mod

    with open(mod.__file__) as handle:
        source = handle.read()
    assert 'config.get("tau"' not in source
    assert 'config.get("theta"' not in source
    assert "math.exp" not in source
    assert "self.roc" not in source


def test_the_first_update_computes_a_zero_derivative_regardless_of_starting_temperature(clock):
    """The first update's derivative is exactly zero regardless of the
    starting temperature, because self.last seeds from that same first
    reading rather than a fixed value.

    Both readings land inside the else branch (not the overshoot/undershoot
    short-circuits) so the derivative is actually computed, not left at its
    unexercised 0.0 default."""
    cold = _controller("pid_sp", clock)
    hot = _controller("pid_sp", clock)
    cold.set_target(150.0)
    hot.set_target(150.0)
    clock.t += 20.0
    cold.update(140.0)  # error == -10, inside the else branch
    hot.update(155.0)  # error == +5, inside the else branch
    assert cold.get_status()["d"] == 0.0
    assert hot.get_status()["d"] == 0.0


def test_start_change_temp_is_seeded_so_the_integral_guard_never_sees_none(clock):
    """set_target() records self.last as start_change_temp before any update
    has run, so a fresh construction leaves start_change_temp at None (self.last
    is also None until the first update seeds it). The integral-reset guard
    reaches `abs(self.start_change_temp - self.set_point)` once new_target is
    True, the 3-cycle delay has elapsed, and the error is inside the stable
    window but outside the +/-3 dead zone -- exactly the branch below. Without
    seeding start_change_temp this raises TypeError in the live control loop."""
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    clock.t += 20.0 * 3 + 5.0  # >= cycle_time * 3
    result = sp.update(220.0)  # abs(error) == 5: inside (3, stable_window]
    assert math.isfinite(result)


def test_derivative_is_not_suppressed_on_a_downward_set_point_change(clock):
    """D still reflects the selected-temperature rate of change on a downward
    setpoint change with new_target True and set_point below the current
    reading -- no suppression fires in that case."""
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    clock.t += 20.0
    sp.update(230.0)
    sp.set_target(200.0)  # downward: new_target True, set_point < last selected
    clock.t += 20.0
    sp.update(205.0)
    status = sp.get_status()
    assert status["d"] == pytest.approx(sp.kd * (205.0 - 230.0) / 20.0)
    assert status["d"] != 0.0


def test_a_last_selected_temperature_of_exactly_zero_is_repaired_on_a_new_target(clock):
    """In the untrusted regime, self.last is None only at startup; a selected
    temperature of exactly 0.0 in native units is a distinct case the None-seed
    does not cover. Without the repair, a setpoint change following that reading
    computes the derivative against a temperature that was never real.

    Asserted on the derivative directly rather than against another controller:
    the repair re-seeds self.last from the current selected temperature, so the
    derivative on that tick is exactly zero. Un-repaired it would be
    kd * 220.0 / dt, which is nowhere near it -- the pinned value cannot be
    reached by both paths.
    """
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    clock.t += 20.0
    sp.update(200.0)
    clock.t += 20.0
    sp.update(0.0)  # a reading of exactly 0.0 in native units: self.last becomes exactly 0.0
    assert sp.last == 0.0
    sp.set_target(225.0)  # any setpoint change: new_target True
    # Past the startup-reduction window (>= cycle_time * 3), so the reduction
    # cannot be confused with this repair.
    dt = 20.0 * 3 + 1
    clock.t += dt
    sp.update(220.0)

    assert sp.get_status()["d"] == pytest.approx(0.0)
    # The value the same tick would have produced had the repair not fired.
    assert sp.kd * 220.0 / dt != pytest.approx(0.0)


def test_a_first_approach_that_misses_the_band_still_clears_new_target(clock):
    """Reaching the set point is a crossing, not a band.

    `new_target` gates the integral-reset rule below it. While it is set, that
    rule fires on every tick and the integral is wiped before it can accumulate,
    so the loop has no way to remove a standing offset. A chamber that steps
    over a narrow band on its way up -- 20 F per tick here, so the closest
    approach either side of the set point is 5 F -- would latch `new_target` for
    the rest of the cook and park at that offset permanently. Measured on the
    MAK plant before this was fixed: two cooks differing only in starting
    temperature settled 8 F apart, one of them never once inside the band.
    """
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    # Every sample sits at least 5 F from the set point, so `abs(error) <= 3`
    # is never satisfied at any point on the approach.
    for temp in (200.0, 220.0, 230.0):
        clock.t += 20.0
        sp.update(temp)
        assert abs(temp - 225.0) >= 5.0

    assert not sp.new_target


def test_the_integral_accumulates_once_the_set_point_has_been_crossed(clock):
    """The consequence of the latch, pinned on the accumulator itself.

    While `new_target` is set the reset fires every tick, so the accumulator
    only ever holds the single tick added after it -- it cannot grow, whatever
    the error is. Past the crossing it integrates, which is what lets the loop
    pull out a standing offset. Asserted as growth between ticks rather than an
    absolute value, because the approach leaves it wherever it leaves it.
    """
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    for temp in (200.0, 220.0, 230.0):
        clock.t += 20.0
        sp.update(temp)

    clock.t += 20.0
    sp.update(233.0)
    after_one = sp.inter
    for _ in range(2):
        clock.t += 20.0
        sp.update(233.0)

    # Two further ticks at +8 F over 20 s each.
    assert sp.inter - after_one == pytest.approx(2 * 8.0 * 20.0)


@pytest.mark.parametrize("bias_from_model", [True, False], ids=["bias", "integral_seed"])
def test_the_identified_hold_duty_becomes_the_loops_zero_error_output(clock, bias_from_model):
    """`center` is where the loop sits at zero error and is a heuristic -- 0.225
    at a 225 F set point, where the grill holds near 0.07. Either route puts the
    loop at the identified duty instead; what differs is when.

    The seed can only fire inside the stable window, because outside it the
    integral reset wipes it on the same update that places it -- so on the
    approach, where the overshoot is made, the loop is still running against
    0.225. Supplying it as the proportional bias covers the whole climb, which
    is worth 7.6 F of overshoot down to 2.6 F at 225 F on MAKGrillSim.
    """
    config = {**CONFIG, "bias_from_model": bias_from_model}
    import controller.pid_sp as mod

    sp = mod.Controller(config, "F", dict(CYCLE_DATA))
    sp.set_target(225.0)
    held = 0.07
    sp.identifier.hold_duty = lambda u_max=1.0, target_f=None: held

    # Inside the stable window, so the seeding route is reachable at all.
    clock.t += 20.0
    sp.update(220.0)

    # u = bias + ki*inter + kd*derv, so this is the output at zero error.
    assert sp._bias() + sp.ki * sp.inter == pytest.approx(held)
    assert sp._integral_seeded
    assert sp.feed_forward == pytest.approx(held)


def test_the_integral_seed_is_a_seed_and_not_a_control_law(clock):
    """Placed once; after that the accumulator is the loop's own correction and
    overwriting it would discard what it exists to make."""
    config = {**CONFIG, "bias_from_model": False}
    import controller.pid_sp as mod

    sp = mod.Controller(config, "F", dict(CYCLE_DATA))
    sp.set_target(225.0)
    held = 0.07
    sp.identifier.hold_duty = lambda u_max=1.0, target_f=None: held

    clock.t += 20.0
    sp.update(220.0)
    assert sp.inter == pytest.approx((held - sp.center) / sp.ki)

    sp.inter = 0.0
    clock.t += 20.0
    sp.update(222.0)
    assert sp.inter != pytest.approx((held - sp.center) / sp.ki)


def test_new_target_reseeds_the_identified_hold_duty(clock):
    import controller.pid_sp as mod

    sp = mod.Controller({**CONFIG, "bias_from_model": False}, "F", dict(CYCLE_DATA))
    sp.set_target(225.0)
    sp.identifier.hold_duty = lambda u_max=1.0, target_f=None: 0.07
    clock.t += 20.0
    sp.update(220.0)
    assert sp._integral_seeded

    sp.set_target(300.0)
    sp.identifier.hold_duty = lambda u_max=1.0, target_f=None: 0.1
    for _ in range(2):
        clock.t += 20.0
        sp.update(300.0)

    assert sp._bias() + sp.ki * sp.inter == pytest.approx(0.1)
    assert sp._integral_seeded


def test_no_identified_hold_duty_leaves_the_integral_alone(clock):
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    sp.identifier.hold_duty = lambda u_max=1.0, target_f=None: None

    clock.t += 20.0
    sp.update(220.0)

    assert not sp._integral_seeded
    assert sp.feed_forward == sp.center


def test_progress_output_and_control_update_do_not_create_identifier_input(clock):
    """Only exact completed frames own PID-SP identification input."""
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    sp.set_output(AppliedOutput(0.9, OutputSource.CONTROLLER, clock.t))
    clock.t += 20.0
    sp.update(200.0)

    assert sp.get_status()["identifier"]["duty_segments"] == 0


def test_a_celsius_install_scales_error_and_corrections_from_fahrenheit(clock):
    """Every other test constructs with units='F', so _to_f / _from_f could
    each be replaced by the identity and nothing would fail. Runs the identical
    schedule in C and in F and checks the C instance's deltas are the F
    instance's deltas scaled by 5/9 -- the round trip a Celsius install's
    correctness rests on."""
    sp_c = _controller("pid_sp", clock, units="C")
    sp_f = _controller("pid_sp", clock, units="F")
    sp_c.set_target(107.0)
    sp_f.set_target(107.0 * 9 / 5 + 32)
    clock.t += 20.0
    sp_c.update(100.0)
    sp_f.update(100.0 * 9 / 5 + 32)
    error_c = sp_c.get_status()["error"]
    error_f = sp_f.get_status()["error"]
    assert error_c == pytest.approx(error_f * 5 / 9)

    model = _fopdt_checkpoint(revision=1)
    sp_c.restore_model(model)
    sp_f.restore_model(model)
    frame_start = clock.t
    clock.t += 20.0
    _observe_completed_frame(sp_c, frame_start, clock.t, 100.0)
    _observe_completed_frame(sp_f, frame_start, clock.t, 100.0)
    sp_c.update(100.0)
    sp_f.update(100.0 * 9 / 5 + 32)
    before_c = sp_c.get_status()["selected_temp"]
    before_f = sp_f.get_status()["selected_temp"]
    frame_start = clock.t
    clock.t += 20.0
    _observe_completed_frame(sp_c, frame_start, clock.t, 100.0)
    _observe_completed_frame(sp_f, frame_start, clock.t, 100.0)
    sp_c.update(100.0)
    sp_f.update(100.0 * 9 / 5 + 32)
    after_c = sp_c.get_status()["selected_temp"]
    after_f = sp_f.get_status()["selected_temp"]
    correction_c = after_c - before_c
    correction_f = after_f - before_f
    assert correction_f != 0.0, "the predictor is not correcting; the test proves nothing"
    assert correction_c == pytest.approx(correction_f * 5 / 9)


import json

from common.controller_model_state import MAX_SNAPSHOT_BYTES, ControllerModelStore


class _FakeBlobs:
    def __init__(self):
        self.blobs = {}

    def read(self, key):
        # An absent key raises TypeError, matching the real reader's contract
        # (controller/runtime/store.py:read_generic_key): ControllerModelStore
        # catches precisely TypeError to mean "nothing stored yet".
        return json.loads(self.blobs[key]) if key in self.blobs else json.loads(None)

    def write(self, key, value):
        self.blobs[key] = json.dumps(value)


def test_a_snapshot_survives_the_store_round_trip(clock):
    sp = _controller("pid_sp", clock)
    sp.restore_model(_fopdt_checkpoint(revision=3))
    blobs = _FakeBlobs()
    store = ControllerModelStore(reader=blobs.read, writer=blobs.write)

    assert store.save("pid_sp", sp.get_model_snapshot()) is True

    fresh = _controller("pid_sp", clock)
    assert fresh.get_model_snapshot() is None
    assert fresh.restore_model(store.load("pid_sp")) is True
    assert fresh.get_model_snapshot() == sp.get_model_snapshot()


def test_checkpoint_provenance_round_trips_without_target_rewriting(clock):
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    checkpoint = _fopdt_checkpoint(
        revision=3,
        provenance="learned-near-450f",
    )

    assert sp.restore_model(checkpoint) is True
    restored = sp.get_model_snapshot()
    assert restored["provenance"] == "learned-near-450f"
    assert restored["selected"]["parameters"] == checkpoint["selected"]["parameters"]


def test_checkpoint_is_canonical_fahrenheit_regardless_of_install_units(clock):
    sp = _controller("pid_sp", clock, units="C")
    sp.set_target(100.0)

    assert sp.restore_model(_fopdt_checkpoint(revision=3)) is True
    assert sp.get_model_snapshot()["selected"]["parameters"]["K"] == 800.0


def test_restore_does_not_refuse_on_opaque_provenance(clock):
    sp = _controller("pid_sp", clock)
    sp.set_target(600.0)
    checkpoint = _fopdt_checkpoint(
        revision=3,
        provenance="learned-at-another-operating-point",
    )

    assert sp.restore_model(checkpoint) is True
    assert sp.get_model_snapshot()["provenance"] == checkpoint["provenance"]


def test_restore_keeps_immutable_ipdt_evidence_envelope(clock):
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    checkpoint = _ipdt_checkpoint(revision=7)

    assert sp.restore_model(checkpoint) is True

    restored = sp.get_model_snapshot()
    assert restored == checkpoint
    assert restored["selected"]["parameters"]["c0"] == -0.033
    assert restored["revision"] == 7


def test_a_restored_model_is_active_on_the_first_tick(clock):
    """From the second cook onward there is no hour of plain PID."""
    blobs = _FakeBlobs()
    store = ControllerModelStore(reader=blobs.read, writer=blobs.write)
    store.save("pid_sp", _fopdt_checkpoint(revision=3))

    sp = _controller("pid_sp", clock)
    sp.restore_model(store.load("pid_sp"))
    sp.set_target(225.0)
    clock.t += 20.0
    sp.update(200.0)
    assert sp.get_status()["predictor"]["active"] is True


def test_an_integrating_model_survives_the_store(clock):
    """An integrating chamber is the form a real grill actually identifies, so
    the cook boundary has to carry it. It reached the predictor within a cook
    long before it could be persisted, which is why the FOPDT-shaped tests above
    could all pass while a real grill relearned its chamber every cook."""
    blobs = _FakeBlobs()
    store = ControllerModelStore(reader=blobs.read, writer=blobs.write)
    identified = _ipdt_checkpoint(revision=3)
    store.save("pid_sp", identified)

    sp = _controller("pid_sp", clock)
    assert sp.restore_model(store.load("pid_sp")) is True
    sp.set_target(225.0)
    clock.t += 20.0
    sp.update(200.0)
    assert sp.get_status()["predictor"]["active"] is True
    assert sp.get_model_snapshot()["selected"]["parameters"]["K_i"] == 0.46


def test_the_snapshot_satisfies_the_store_s_envelope_rules(clock):
    sp = _controller("pid_sp", clock)
    sp.restore_model(_fopdt_checkpoint(revision=3))
    snapshot = sp.get_model_snapshot()
    encoded = json.dumps(snapshot, allow_nan=False)
    assert len(encoded.encode("utf-8")) <= MAX_SNAPSHOT_BYTES
    # bool is an int subclass, and the store rejects it separately, so an
    # isinstance check alone would accept a revision the store refuses.
    assert isinstance(snapshot["revision"], int) and not isinstance(snapshot["revision"], bool)


def test_an_untrusted_controller_offers_nothing_to_persist(clock):
    assert _controller("pid_sp", clock).get_model_snapshot() is None


def test_get_learning_diagnostics_returns_owned_pid_sp_state(clock):
    sp = _controller("pid_sp", clock)

    diagnostics = sp.get_learning_diagnostics()
    first = diagnostics.as_json()
    first["gates"][0]["passed"] = True

    assert diagnostics.schema_version == 1
    assert diagnostics.as_json()["controller"] == "pid_sp"
    assert diagnostics.as_json()["gates"][0]["passed"] is False


def test_get_status_projects_one_identifier_and_predictor_snapshot(clock, monkeypatch):
    sp = _controller("pid_sp", clock)
    identifier = {
        "accepted": 0,
        "accepted_seconds": 0.0,
        "duty_std": 0.0,
        "temp_span": 0.0,
        "transition_seen": False,
        "duty_segments": 0,
        "raw_best_residual": 0.0,
        "raw_runner_up_residual": 0.0,
        "raw_candidates_passing": 0,
        "trusted": None,
        "distrust_count": 0,
        "distrust_ratio": 0.0,
    }
    predictor = {
        "active": False,
        "disabled": False,
        "x0": 0.0,
        "xd": 0.0,
        "z0": 0.0,
        "zd": 0.0,
        "residual_streak": 0,
        "truncated": 0,
        "model": None,
    }
    calls = {"identifier": 0, "predictor": 0}

    def identifier_status():
        calls["identifier"] += 1
        return identifier

    def predictor_status():
        calls["predictor"] += 1
        return predictor

    monkeypatch.setattr(sp.identifier, "status", identifier_status)
    monkeypatch.setattr(sp.predictor, "status", predictor_status)

    status = sp.get_status()

    assert calls == {"identifier": 1, "predictor": 1}
    assert status["identifier"] == identifier
    assert status["predictor"] == predictor
    assert status["identifier"] is status["learning"]["identifier"]
    assert status["predictor"] is status["learning"]["predictor"]
    assert status["learning"] == {
        "schema_version": 1,
        "controller": "pid_sp",
        "status": "collecting",
        "identifier": identifier,
        "predictor": predictor,
        "confirmation": {"observed": None, "required": CONFIRM_WINDOW},
        "comparison": None,
        "active_model": None,
        "delay_evidence": {
            "status": "insufficient-excitation-episodes",
            "completed_episode_count": 0,
            "evaluated_bound_s": 300,
            "profile_form": None,
            "raw_basin_lower_s": None,
            "raw_basin_upper_s": None,
            "raw_basin_representative_s": None,
            "confidence_lower_s": None,
            "confidence_upper_s": None,
            "confidence_method": None,
            "confidence_resamples": None,
            "blockers": ["insufficient-excitation-episodes"],
            "authorized": False,
        },
        "gates": [
            {
                "name": "accepted_samples",
                "passed": False,
                "observed": 0,
                "required": 25,
                "unit": "samples",
            },
            {
                "name": "accepted_duration",
                "passed": False,
                "observed": 0.0,
                "required": 500.0,
                "unit": "seconds",
            },
            {
                "name": "duty_standard_deviation",
                "passed": False,
                "observed": 0.0,
                "required": 0.05,
                "unit": "ratio",
            },
            {
                "name": "duty_transition",
                "passed": False,
                "observed": False,
                "required": True,
                "unit": None,
            },
            {
                "name": "temperature_span",
                "passed": False,
                "observed": 0.0,
                "required": 15.0,
                "unit": "°F",
            },
        ],
    }


def test_get_status_survives_the_mqtt_encoder(clock):
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    clock.t += 20.0
    sp.update(200.0)
    json.dumps(sp.get_status(), allow_nan=False)
