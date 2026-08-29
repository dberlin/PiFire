"""Off-path grey candidate preparation, evaluation, and handoff contracts."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from controller.acados.contracts import GreyBoxMPCConfig
from controller.model_learning.contracts import (
    ActivationPolicy,
    CandidateOrigin,
    FitResult,
    FitStatus,
    LearningStatus,
)
from controller.model_learning.evaluation import EvaluationConfig
from controller.runtime.model_fitting import (
    CandidatePreparation,
    FitSubmission,
    GreyLearningOrchestrator,
    LiveLearningIdentity,
    TriggerConfig,
    fit_trigger,
    handoff_candidate,
    paired_forecast_origin,
    prepare_candidate_off_path,
    stale_result_reasons,
)
from tests.unit.mpc._grey_online_helpers import (
    _CHALLENGER,
    _INCUMBENT,
    _corpus,
    _Estimator,
    _fit,
    _frame,
    _identity,
    _ImmediateFitWorker,
    _Native,
    _persistent_fit_job,
    _prepared_supersession_harness,
    _request,
    _timing,
)


def test_trigger_retains_minimum_sample_excitation_coverage_continuity_and_identifiability_gates() -> None:
    config = TriggerConfig(
        min_samples=9, min_input_variance=0.02, min_input_levels=3, min_temperature_span_c=8.0, min_identifiability=0.5
    )
    informative = tuple(_frame(index) for index in range(12))
    accepted = fit_trigger(informative, identifiability=0.8, config=config)
    assert accepted.ready is True
    assert accepted.input_variance == pytest.approx(0.08166666666666667)
    assert accepted.input_levels == 3
    below_minimum = fit_trigger(informative[:8], identifiability=0.8, config=config)
    assert below_minimum.blockers == ("minimum-samples",)
    assert below_minimum.input_variance == pytest.approx(0.0746484375)
    assert below_minimum.input_levels == 3
    empty = fit_trigger((), identifiability=0.8, config=config)
    assert empty.blockers == ("minimum-samples",)
    assert empty.input_variance == 0.0
    assert empty.input_levels == 0
    constant = tuple(
        _frame(index, requested_q=0.5, realized_q=0.5, requested_auger_duty=0.5, delivered_on_s=12.5)
        for index in range(12)
    )
    constant_decision = fit_trigger(constant, identifiability=0.8, config=config)
    assert constant_decision.blockers == ("insufficient-excitation",)
    assert constant_decision.input_variance == 0.0
    assert constant_decision.input_levels == 1
    narrow = tuple(_frame(index, temp_c=90.0 + index * 0.1) for index in range(12))
    assert fit_trigger(narrow, identifiability=0.8, config=config).blockers == ("insufficient-coverage",)
    broken = informative[:6] + (replace(informative[6], continuous=False),) + informative[7:]
    assert fit_trigger(broken, identifiability=0.8, config=config).blockers == ("discontinuity",)
    assert fit_trigger(informative, identifiability=0.49, config=config).blockers == ("identifiability",)


@pytest.mark.parametrize(
    "result_change, current_change, reason",
    [
        ({"origin": CandidateOrigin.OPERATOR_CALIBRATION}, {}, "origin-changed"),
        ({"request_id": "fit-b"}, {}, "request-changed"),
        ({"fit_corpus": _corpus(corpus_revision=2)}, {}, "corpus-changed"),
        ({"configuration_digest": "4" * 64}, {}, "configuration-changed"),
        ({}, {"configuration_digest": "4" * 64}, "configuration-changed"),
        ({"parent_incumbent_digest": "5" * 64}, {}, "incumbent-changed"),
        ({}, {"incumbent_digest": "5" * 64}, "incumbent-changed"),
        ({"parent_incumbent_generation": 5}, {}, "role-generation-changed"),
        ({}, {"role_generation": 5}, "role-generation-changed"),
        ({"candidate_generation": 8}, {}, "candidate-generation-changed"),
    ],
)
def test_delivery_rechecks_every_frozen_identity_and_discards_stale_results_visibly(
    result_change, current_change, reason
) -> None:
    request = _request()
    result = FitResult(
        request=replace(request, **result_change),
        status=FitStatus.SUCCEEDED,
        candidate_digest=_CHALLENGER,
    )
    reasons = stale_result_reasons(
        result,
        request=request,
        current_identity=_identity(**current_change),
        current_origin=request.origin,
    )
    assert reasons == (reason,)


@pytest.mark.parametrize(
    "live_generation, live_origin, reason",
    [
        (10, CandidateOrigin.PASSIVE_ONLINE, "candidate-generation-changed"),
        (9, CandidateOrigin.OPERATOR_CALIBRATION, "origin-changed"),
    ],
)
def test_delivery_rechecks_live_candidate_identity_even_when_result_echoes_request(
    live_generation, live_origin, reason
) -> None:
    request = _request()
    result = FitResult(
        request=request,
        status=FitStatus.SUCCEEDED,
        candidate_digest=_CHALLENGER,
    )
    assert stale_result_reasons(
        result,
        request=request,
        current_identity=_identity(candidate_generation=live_generation),
        current_origin=live_origin,
    ) == (reason,)


@pytest.mark.parametrize(
    "worker_disposition",
    (
        FitSubmission.BUSY,
        RuntimeError("worker submission failed"),
    ),
)
def test_superseding_submission_failure_preserves_prepared_candidate_and_evaluator(
    tmp_path,
    worker_disposition,
) -> None:
    orchestrator, worker, _, prepared, replacement_job = _prepared_supersession_harness(tmp_path)
    evaluator = orchestrator._evaluator
    persisted = []
    worker.next_submission = worker_disposition

    if isinstance(worker_disposition, BaseException):
        with pytest.raises(RuntimeError, match="worker submission failed"):
            orchestrator.submit_superseding_corpus_fit(
                replacement_job,
                prepared,
                persist=lambda: persisted.append(True),
            )
    else:
        assert orchestrator.submit_superseding_corpus_fit(
            replacement_job,
            prepared,
            persist=lambda: persisted.append(True),
        ) == (FitSubmission.BUSY, False)

    assert orchestrator.pending_request is None
    assert orchestrator.prepared is prepared
    assert orchestrator._evaluator is evaluator
    assert prepared.candidate_pair.controller.closed is False
    assert persisted == []
    orchestrator.close()


def test_superseding_persistence_failure_preserves_candidate_and_stales_accepted_fit(
    tmp_path,
) -> None:
    orchestrator, _, identity, prepared, replacement_job = _prepared_supersession_harness(tmp_path)
    evaluator = orchestrator._evaluator

    def reject_persistence() -> None:
        raise RuntimeError("candidate rejection is not durable")

    assert orchestrator.submit_superseding_corpus_fit(
        replacement_job,
        prepared,
        persist=reject_persistence,
    ) == (FitSubmission.ACCEPTED, False)
    assert orchestrator.pending_request is replacement_job.request
    assert orchestrator.prepared is prepared
    assert orchestrator._evaluator is evaluator
    assert prepared.candidate_pair.controller.closed is False

    delivery = orchestrator.poll_fit_off_path(
        live_identity=identity,
        live_origin=CandidateOrigin.OPERATOR_CALIBRATION,
    )
    assert delivery.stale_reasons == ("candidate-supersession-persistence-failed",)
    assert delivery.preparation is None
    assert orchestrator.pending_request is None
    assert orchestrator.prepared is prepared
    assert orchestrator._evaluator is evaluator
    assert prepared.candidate_pair.controller.closed is False
    orchestrator.close()


@pytest.mark.parametrize(
    "origin",
    [
        CandidateOrigin.PASSIVE_ONLINE,
        CandidateOrigin.OPERATOR_CALIBRATION,
    ],
)
def test_orchestrator_connects_persistent_job_to_off_path_preparation_without_swap(
    tmp_path,
    origin,
) -> None:
    worker = _ImmediateFitWorker()
    incumbent = object()
    config = GreyBoxMPCConfig(horizon_steps=12)
    identity, job = _persistent_fit_job(
        tmp_path,
        origin=origin,
        config=config,
    )
    orchestrator = GreyLearningOrchestrator(
        identity=identity,
        config=config,
        incumbent_pair=incumbent,
        estimator_factory=_Estimator,
        controller_factory=_Native,
        timing_probe=lambda _native: _timing(),
        trigger_config=TriggerConfig(
            min_samples=9,
            min_input_variance=0.02,
            min_input_levels=3,
            min_temperature_span_c=8.0,
            min_identifiability=0.5,
        ),
        worker=worker,
    )
    assert orchestrator.submit_corpus_fit(job) is FitSubmission.ACCEPTED
    assert orchestrator.submit_corpus_fit(job) is FitSubmission.BUSY
    assert worker.job.request.origin is origin
    delivery = orchestrator.poll_fit_off_path(live_identity=identity, live_origin=origin)
    assert delivery.stale_reasons == ()
    assert delivery.preparation.accepted is True
    assert delivery.preparation.incumbent_pair is incumbent
    assert orchestrator.submit_corpus_fit(job) is FitSubmission.BUSY
    assert orchestrator.incumbent_pair is incumbent
    orchestrator.close()
    assert worker.closed is True


def test_orchestrator_rechecks_actual_fit_identifiability_before_candidate_preparation(
    tmp_path,
) -> None:
    worker = _ImmediateFitWorker(identifiability=0.49)
    config = GreyBoxMPCConfig(horizon_steps=12)
    identity, job = _persistent_fit_job(
        tmp_path,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        config=config,
    )
    orchestrator = GreyLearningOrchestrator(
        identity=identity,
        config=config,
        incumbent_pair=object(),
        estimator_factory=_Estimator,
        controller_factory=_Native,
        timing_probe=lambda _native: _timing(),
        trigger_config=TriggerConfig(
            min_samples=9,
            min_input_variance=0.02,
            min_input_levels=3,
            min_temperature_span_c=8.0,
            min_identifiability=0.5,
        ),
        worker=worker,
    )
    assert orchestrator.submit_corpus_fit(job) is FitSubmission.ACCEPTED
    delivery = orchestrator.poll_fit_off_path(
        live_identity=identity,
        live_origin=CandidateOrigin.PASSIVE_ONLINE,
    )
    assert delivery.blockers == ("identifiability",)
    assert delivery.preparation is None
    assert orchestrator.prepared is None
    orchestrator.close()


def test_identity_digest_changes_require_atomic_config_and_incumbent_replacement_and_release_candidate(
    tmp_path,
) -> None:
    worker = _ImmediateFitWorker()
    incumbent = object()
    config = GreyBoxMPCConfig(horizon_steps=12)
    identity, job = _persistent_fit_job(
        tmp_path,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        config=config,
    )
    orchestrator = GreyLearningOrchestrator(
        identity=identity,
        config=config,
        incumbent_pair=incumbent,
        estimator_factory=_Estimator,
        controller_factory=_Native,
        timing_probe=lambda _native: _timing(),
        trigger_config=TriggerConfig(
            min_samples=9,
            min_input_variance=0.02,
            min_input_levels=3,
            min_temperature_span_c=8.0,
            min_identifiability=0.5,
        ),
        worker=worker,
    )
    assert orchestrator.submit_corpus_fit(job) is FitSubmission.ACCEPTED
    delivery = orchestrator.poll_fit_off_path(
        live_identity=identity,
        live_origin=CandidateOrigin.PASSIVE_ONLINE,
    )
    candidate_controller = delivery.preparation.candidate_pair.controller
    incumbent_only_identity = replace(identity, incumbent_digest="4" * 64)
    with pytest.raises(ValueError, match="corresponding incumbent pair"):
        orchestrator.update_identity(incumbent_only_identity)
    assert orchestrator.identity is identity
    assert candidate_controller.closed is False

    replacement_config = replace(config, C_c=400.0)
    replacement_identity = LiveLearningIdentity(
        session_id=identity.session_id,
        cook_id=identity.cook_id,
        # This is the complete-controller digest the scheduler supplies, not the
        # digest of GreyBoxMPCConfig alone.
        configuration_digest="5" * 64,
        incumbent_digest="4" * 64,
        role_generation=identity.role_generation + 1,
        candidate_generation=identity.candidate_generation + 1,
    )
    with pytest.raises(ValueError, match="corresponding config"):
        orchestrator.update_identity(replacement_identity)
    assert orchestrator.identity is identity
    assert candidate_controller.closed is False

    replacement_pair = object()
    orchestrator.update_identity(
        replacement_identity,
        config=replacement_config,
        incumbent_pair=replacement_pair,
    )
    assert candidate_controller.closed is True
    assert orchestrator.config is replacement_config
    assert orchestrator.incumbent_pair is replacement_pair
    assert orchestrator.prepared is None
    orchestrator.close()


def test_close_releases_an_untransferred_prepared_candidate_pair(tmp_path) -> None:
    worker = _ImmediateFitWorker()
    config = GreyBoxMPCConfig(horizon_steps=12)
    identity, job = _persistent_fit_job(
        tmp_path,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        config=config,
    )
    orchestrator = GreyLearningOrchestrator(
        identity=identity,
        config=config,
        incumbent_pair=object(),
        estimator_factory=_Estimator,
        controller_factory=_Native,
        timing_probe=lambda _native: _timing(),
        trigger_config=TriggerConfig(
            min_samples=9,
            min_input_variance=0.02,
            min_input_levels=3,
            min_temperature_span_c=8.0,
            min_identifiability=0.5,
        ),
        worker=worker,
    )
    assert orchestrator.submit_corpus_fit(job) is FitSubmission.ACCEPTED
    delivery = orchestrator.poll_fit_off_path(
        live_identity=identity,
        live_origin=CandidateOrigin.PASSIVE_ONLINE,
    )
    candidate_controller = delivery.preparation.candidate_pair.controller
    orchestrator.close()
    assert candidate_controller.closed is True


@pytest.mark.parametrize(
    "origin",
    (
        CandidateOrigin.PASSIVE_ONLINE,
        CandidateOrigin.OPERATOR_CALIBRATION,
    ),
)
def test_orchestrator_carries_both_origins_through_causal_evaluation_and_handoff_without_install(
    tmp_path,
    origin: CandidateOrigin,
) -> None:
    worker = _ImmediateFitWorker()
    incumbent = object()
    config = GreyBoxMPCConfig(horizon_steps=12)
    identity, job = _persistent_fit_job(
        tmp_path,
        origin=origin,
        config=config,
    )
    orchestrator = GreyLearningOrchestrator(
        identity=identity,
        config=config,
        incumbent_pair=incumbent,
        estimator_factory=_Estimator,
        controller_factory=_Native,
        timing_probe=lambda _native: _timing(),
        trigger_config=TriggerConfig(
            min_samples=9,
            min_input_variance=0.02,
            min_input_levels=3,
            min_temperature_span_c=8.0,
            min_identifiability=0.5,
        ),
        evaluation_config=EvaluationConfig(required_consecutive_wins=2),
        worker=worker,
    )
    assert orchestrator.submit_corpus_fit(job) is FitSubmission.ACCEPTED
    delivery = orchestrator.poll_fit_off_path(
        live_identity=identity,
        live_origin=origin,
    )
    assert delivery.preparation.accepted

    incumbent_predict = lambda _origin: -1000.0
    challenger_predict = lambda _origin: 0.0
    assert (
        len(
            orchestrator.register_causal_forecasts(
                _frame(9),
                incumbent_predict=incumbent_predict,
                challenger_predict=challenger_predict,
            )
        )
        == 5
    )
    for sequence in range(10, 190):
        orchestrator.observe_completed_frame(_frame(sequence), identifiability=0.8)
    first = orchestrator.evaluate_ready_off_path()
    assert first.consecutive_wins == 1
    assert first.accepted is False

    assert (
        len(
            orchestrator.register_causal_forecasts(
                _frame(190),
                incumbent_predict=incumbent_predict,
                challenger_predict=challenger_predict,
            )
        )
        == 5
    )
    for sequence in range(191, 371):
        orchestrator.observe_completed_frame(_frame(sequence), identifiability=0.8)
    second = orchestrator.evaluate_ready_off_path()
    assert second.accepted is True
    assert second.incumbent_digest == _INCUMBENT
    assert second.challenger_digest == delivery.preparation.candidate_digest

    handed = []
    outcome = orchestrator.handoff_if_ready(
        confidence_accepted=True,
        online_enabled=True,
        prepare=lambda exact, policy: handed.append((exact, policy)) or "prepared-id",
    )
    assert handed == [(delivery.preparation, ActivationPolicy.CAUSAL_AUTO)]
    assert outcome.active_pair is incumbent
    assert outcome.prepared_id == "prepared-id"
    repeated = orchestrator.handoff_if_ready(
        confidence_accepted=True,
        online_enabled=True,
        prepare=lambda *_args: pytest.fail("successful handoff must be idempotent"),
    )
    assert repeated is outcome
    candidate_controller = delivery.preparation.candidate_pair.controller
    orchestrator.close()
    assert candidate_controller.closed is False


def test_candidate_estimator_and_native_handle_are_built_and_dry_solved_off_path_as_one_pair() -> None:
    incumbent = object()
    prepared = prepare_candidate_off_path(
        _fit(),
        incumbent_pair=incumbent,
        estimator_factory=_Estimator,
        controller_factory=_Native,
        timing_probe=lambda _native: _timing(),
    )
    assert prepared.accepted is True
    assert prepared.blockers == ()
    assert prepared.incumbent_pair is incumbent
    assert prepared.candidate_pair.estimator.config is prepared.candidate.config
    assert prepared.candidate_pair.controller.config is prepared.candidate.config
    assert prepared.dry_solve_finite is True
    assert prepared.timing.p99_ms == 4.0


@pytest.mark.parametrize(
    "failure, reason",
    [
        ("estimator", "estimator-build"),
        ("native", "native-build"),
        ("solve", "native-dry-solve"),
        ("timing", "target-timing"),
    ],
)
def test_off_path_build_solve_and_timing_failures_reject_only_the_candidate(failure, reason) -> None:
    incumbent = object()

    def estimator_factory(config):
        if failure == "estimator":
            raise RuntimeError("estimator failed")
        return _Estimator(config)

    def native_factory(config):
        if failure == "native":
            raise RuntimeError("native failed")
        return _Native(config, fail=failure == "solve")

    prepared = prepare_candidate_off_path(
        _fit(),
        incumbent_pair=incumbent,
        estimator_factory=estimator_factory,
        controller_factory=native_factory,
        timing_probe=lambda _native: _timing(6.0 if failure == "timing" else 4.0),
    )
    assert prepared.accepted is False
    assert prepared.blockers == (reason,)
    assert prepared.incumbent_pair is incumbent
    assert prepared.candidate_pair is None


def test_incumbent_and_challenger_share_one_exact_causal_origin_and_probe_frames_are_excluded() -> None:
    seen = []

    def predict(label):
        def inner(origin):
            seen.append((label, origin))
            return 100.0 if label == "incumbent" else 101.0

        return inner

    normal = paired_forecast_origin(
        _frame(12),
        horizon_steps=15,
        candidate_generation=9,
        incumbent_digest=_INCUMBENT,
        challenger_digest=_CHALLENGER,
        incumbent_predict=predict("incumbent"),
        challenger_predict=predict("challenger"),
    )
    assert normal is not None
    assert seen[0][1] is seen[1][1]
    assert normal.incumbent_prediction_c == 100.0
    assert normal.challenger_prediction_c == 101.0
    probe = _frame(13, calibration_stage="low", calibration_fit=True, probe_q=0.05)
    assert (
        paired_forecast_origin(
            probe,
            horizon_steps=15,
            candidate_generation=9,
            incumbent_digest=_INCUMBENT,
            challenger_digest=_CHALLENGER,
            incumbent_predict=predict("incumbent"),
            challenger_predict=predict("challenger"),
        )
        is None
    )


def _accepted_evaluation(prepared):
    return SimpleNamespace(
        accepted=True,
        consecutive_wins=2,
        role_generation=4,
        candidate_generation=9,
        incumbent_digest=_INCUMBENT,
        challenger_digest=prepared.candidate_digest,
    )


@pytest.mark.parametrize(
    ("origin", "online_enabled", "expected_status", "expected_blockers"),
    (
        pytest.param(
            CandidateOrigin.PASSIVE_ONLINE,
            True,
            LearningStatus.ACTIVATING,
            (),
            id="passive-enabled",
        ),
        pytest.param(
            CandidateOrigin.OPERATOR_CALIBRATION,
            True,
            LearningStatus.ACTIVATING,
            (),
            id="operator-enabled",
        ),
        pytest.param(
            CandidateOrigin.OPERATOR_CALIBRATION,
            False,
            LearningStatus.ACTIVATING,
            (),
            id="operator-explicit-with-passive-disabled",
        ),
        pytest.param(
            CandidateOrigin.PASSIVE_ONLINE,
            False,
            LearningStatus.EVALUATING,
            ("online-disabled",),
            id="passive-disabled",
        ),
    ),
)
def test_causal_auto_handoff_is_shared_and_passive_disable_blocks_only_passive_admission(
    origin: CandidateOrigin,
    online_enabled: bool,
    expected_status: LearningStatus,
    expected_blockers: tuple[str, ...],
) -> None:
    prepared = CandidatePreparation.accepted_for_test(
        candidate=_fit(origin),
        candidate_pair=object(),
        incumbent_pair=object(),
        timing=_timing(),
    )
    handed = []
    outcome = handoff_candidate(
        prepared,
        evaluation=_accepted_evaluation(prepared),
        confidence_accepted=True,
        online_enabled=online_enabled,
        prepare=lambda exact, policy: handed.append((exact, policy)) or "prepared-id",
        install=lambda _pair: pytest.fail("the fit pipeline must never install a candidate"),
    )

    assert outcome.status is expected_status
    assert outcome.blockers == expected_blockers
    assert outcome.active_pair is prepared.incumbent_pair
    if expected_blockers:
        assert handed == []
        assert outcome.policy is None
        assert outcome.prepared_id is None
    else:
        assert handed == [(prepared, ActivationPolicy.CAUSAL_AUTO)]
        assert outcome.policy is ActivationPolicy.CAUSAL_AUTO
        assert outcome.prepared_id == "prepared-id"


@pytest.mark.parametrize(
    "digest_field, wrong_digest, blocker",
    [
        ("incumbent_digest", "4" * 64, "incumbent-changed"),
        ("challenger_digest", "5" * 64, "challenger-changed"),
    ],
)
def test_handoff_rechecks_exact_evaluation_model_digests(digest_field, wrong_digest, blocker) -> None:
    prepared = CandidatePreparation.accepted_for_test(
        candidate=_fit(),
        candidate_pair=object(),
        incumbent_pair=object(),
        timing=_timing(),
    )
    evaluation = _accepted_evaluation(prepared)
    setattr(evaluation, digest_field, wrong_digest)
    handed = []
    outcome = handoff_candidate(
        prepared,
        evaluation=evaluation,
        confidence_accepted=True,
        online_enabled=True,
        prepare=lambda *_args: handed.append(True),
        install=lambda _pair: pytest.fail("the fit pipeline must never install a candidate"),
    )
    assert handed == []
    assert outcome.blockers == (blocker,)
    assert outcome.active_pair is prepared.incumbent_pair
