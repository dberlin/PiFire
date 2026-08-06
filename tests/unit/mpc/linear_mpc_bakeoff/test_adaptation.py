"""Behavioral contracts for passive online model adaptation and promotion."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pytest

from docs.superpowers.experiments.linear_mpc_bakeoff.adaptation import (
    AlignmentEvidence,
    AdaptationManager,
    AdaptationPolicy,
    OperatingState,
    PromotionRejectionReason,
    ReplaySample,
    StratifiedReplay,
    UpdateRejectionReason,
    WindowScores,
)
from docs.superpowers.experiments.linear_mpc_bakeoff.contracts import (
    AffinePrediction,
    Observation,
    SignalRecord,
    UpdateOutcome,
)


class SpyModel:
    """Full adaptive-model spy that makes update ownership observable."""

    def __init__(
        self,
        snapshot: Mapping[str, object] | None = None,
        *,
        successful_updates: bool = True,
    ) -> None:
        self.observe_calls = 0
        self.track_calls = 0
        self._snapshot = dict(snapshot or viable_snapshot())
        self._successful_updates = successful_updates

    def fit(self, record: SignalRecord) -> None:
        del record

    def forecast(
        self,
        prefix: SignalRecord,
        q_future: np.ndarray,
        ambient_future: np.ndarray,
    ) -> np.ndarray:
        del prefix, ambient_future
        return np.zeros(q_future.size, dtype=np.float64)

    def observe(self, observation: Observation) -> UpdateOutcome:
        self.observe_calls += 1
        return UpdateOutcome(
            predicted_temp_c=observation.temp_c,
            observed_temp_c=observation.temp_c,
            innovation_c=0.0,
            updated=self._successful_updates,
        )

    def track(self, observation: Observation) -> UpdateOutcome:
        self.track_calls += 1
        return UpdateOutcome(
            predicted_temp_c=observation.temp_c,
            observed_temp_c=observation.temp_c,
            innovation_c=0.0,
            updated=False,
        )

    def affine_prediction(
        self,
        horizon_steps: int,
        q_previous: float,
        ambient_future: np.ndarray,
    ) -> AffinePrediction:
        del q_previous, ambient_future
        return AffinePrediction(
            np.zeros(horizon_steps, dtype=np.float64),
            np.zeros((horizon_steps, horizon_steps), dtype=np.float64),
        )

    def snapshot(self) -> Mapping[str, object]:
        return self._snapshot


def viable_snapshot() -> dict[str, object]:
    """Return diagnostics satisfying the promotion policy."""
    return {
        "poles": [0.8, 0.6],
        "steady_gain": 1.2,
        "delay_steps": 3,
        "alignment_error_c": 0.1,
    }


def score_bundle(
    *,
    candidate_prediction: float = 0.8,
    incumbent_prediction: float = 1.0,
    candidate_braking: float | None = 0.8,
    incumbent_braking: float | None = 1.0,
    window_id: str = "rolling-window-7",
) -> WindowScores:
    """Return all promotion evidence for one immutable untouched window."""
    return WindowScores(
        window_id=window_id,
        candidate_prediction_score=candidate_prediction,
        incumbent_prediction_score=incumbent_prediction,
        candidate_braking_score=candidate_braking,
        incumbent_braking_score=incumbent_braking,
    )


def manager_with_spy_model() -> tuple[AdaptationManager, SpyModel]:
    """Return a manager whose challenger spy exposes blocked updates."""
    challenger = SpyModel()
    return (
        AdaptationManager(
            incumbent=SpyModel(),
            challenger=challenger,
            policy=AdaptationPolicy(excitation_window=2, min_input_variance=0.01),
        ),
        challenger,
    )


def blocked_observation(reason: str) -> tuple[Observation, dict[str, object]]:
    """Encode each unsafe or untrustworthy passive-observation condition."""
    flags: dict[str, object] = {
        "lid_open": False,
        "safety_override": False,
        "manual_override": False,
        "probe_age_s": 0.0,
        "actuation_known": True,
    }
    match reason:
        case "lid-open":
            flags["lid_open"] = True
        case "safety":
            flags["safety_override"] = True
        case "manual":
            flags["manual_override"] = True
        case "stale-probe":
            flags["probe_age_s"] = 61.0
        case "unknown-actuation":
            flags["actuation_known"] = False
        case "unexcited":
            pass
        case _:
            raise ValueError(f"unexpected reason {reason}")
    return Observation(20.0, 110.0, 0.2, 20.0), flags


def hot_hold_samples(count: int) -> list[ReplaySample]:
    """Return common hot holding observations that must not evict rare strata."""
    return [ReplaySample(Observation(index * 20.0, 180.0, 0.85, 20.0), OperatingState.HOLD) for index in range(count)]


def low_coast_samples(count: int) -> list[ReplaySample]:
    """Return rare low-fire coast observations for replay-retention coverage."""
    return [
        ReplaySample(Observation((500 + index) * 20.0, 55.0, 0.1, 20.0), OperatingState.COAST) for index in range(count)
    ]


def promotion_fixture(
    *,
    challenger_snapshot: Mapping[str, object] | None = None,
    incumbent_snapshot: Mapping[str, object] | None = None,
    challenger_updates: bool = True,
    challenger_alignment: AlignmentEvidence = AlignmentEvidence.MEASURED,
    prime: bool = True,
) -> AdaptationManager:
    """Return a deterministic promotion fixture with explicit effective updates."""
    manager = AdaptationManager(
        incumbent=SpyModel(incumbent_snapshot),
        challenger=SpyModel(
            challenger_snapshot,
            successful_updates=challenger_updates,
        ),
        challenger_alignment=challenger_alignment,
        policy=AdaptationPolicy(
            excitation_window=2,
            min_input_variance=0.01,
            min_effective_samples=2,
        ),
    )
    if prime:
        manager.observe(Observation(0.0, 100.0, 0.0, 20.0))
        manager.observe(Observation(20.0, 101.0, 0.4, 20.0))
        manager.observe(Observation(40.0, 102.0, 0.0, 20.0))
    return manager


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("lid-open", UpdateRejectionReason.LID_OPEN),
        ("safety", UpdateRejectionReason.SAFETY_OVERRIDE),
        ("manual", UpdateRejectionReason.MANUAL_OVERRIDE),
        ("stale-probe", UpdateRejectionReason.STALE_PROBE),
        ("unknown-actuation", UpdateRejectionReason.UNKNOWN_ACTUATION),
        ("unexcited", UpdateRejectionReason.INSUFFICIENT_EXCITATION),
    ],
)
def test_blocked_samples_never_update(reason: str, expected: UpdateRejectionReason) -> None:
    manager, spy = manager_with_spy_model()
    observation, flags = blocked_observation(reason)

    outcome = manager.observe(observation, **flags)

    assert outcome.updated is False
    assert outcome.gate.reasons == (expected,)
    assert spy.observe_calls == 0


def test_untrusted_inputs_never_seed_excitation_history() -> None:
    manager, challenger = manager_with_spy_model()

    manager.observe(
        Observation(0.0, 100.0, 0.0, 20.0),
        manual_override=True,
    )
    unexcited = manager.observe(Observation(20.0, 101.0, 0.4, 20.0))
    excited = manager.observe(Observation(40.0, 102.0, 0.0, 20.0))

    assert unexcited.gate.reasons == (UpdateRejectionReason.INSUFFICIENT_EXCITATION,)
    assert excited.updated is True
    assert challenger.observe_calls == 1


def test_excited_samples_only_train_the_challenger_and_track_the_incumbent() -> None:
    manager, challenger = manager_with_spy_model()
    incumbent = manager.incumbent

    manager.observe(Observation(0.0, 100.0, 0.0, 20.0))
    outcome = manager.observe(Observation(20.0, 101.0, 0.4, 20.0))

    assert outcome.updated is True
    assert challenger.observe_calls == 1
    assert challenger.track_calls == 1
    assert isinstance(incumbent, SpyModel)
    assert incumbent.observe_calls == 0
    assert incumbent.track_calls == 2


def test_replay_retains_temperature_and_transient_strata() -> None:
    replay = StratifiedReplay(capacity=120, seed=1)
    replay.extend(hot_hold_samples(500))
    replay.extend(low_coast_samples(20))

    assert replay.count(stratum="low-coast") == 20
    assert len(replay) <= 120


def test_replay_replacement_is_deterministic() -> None:
    left = StratifiedReplay(capacity=12, seed=7)
    right = StratifiedReplay(capacity=12, seed=7)
    samples = hot_hold_samples(80) + low_coast_samples(20)

    left.extend(samples)
    right.extend(samples)

    assert left.samples == right.samples


def test_candidate_needs_two_validation_wins_from_one_window_bundle() -> None:
    manager = promotion_fixture()
    scores = score_bundle()

    assert not manager.evaluate(scores).promoted
    decision = manager.evaluate(scores)

    assert decision.promoted is True
    assert decision.window_id == "rolling-window-7"


def test_promotion_requires_two_distinct_windows_then_starts_a_new_generation() -> None:
    manager = promotion_fixture()

    first = manager.evaluate(score_bundle(window_id="interval-300"))
    second = manager.evaluate(score_bundle(window_id="interval-600"))

    assert not first.promoted
    assert second.promoted
    assert (first.window_id, second.window_id) == ("interval-300", "interval-600")
    assert manager.role_generation == 1

    manager.observe(Observation(60.0, 103.0, 0.4, 20.0))
    manager.observe(Observation(80.0, 104.0, 0.0, 20.0))
    later = manager.evaluate(score_bundle(window_id="interval-900"))

    assert not later.promoted
    assert later.consecutive_wins == 1
    assert later.window_id == "interval-900"


def test_promotion_api_rejects_independent_scalar_scores() -> None:
    manager = promotion_fixture()

    with pytest.raises(TypeError):
        manager.evaluate(candidate_score=0.8, incumbent_score=1.0)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("snapshot", "scores", "expected"),
    [
        ({**viable_snapshot(), "poles": [1.01]}, score_bundle(), PromotionRejectionReason.UNSTABLE_DYNAMICS),
        ({**viable_snapshot(), "steady_gain": 50.0}, score_bundle(), PromotionRejectionReason.IMPLAUSIBLE_GAIN),
        ({**viable_snapshot(), "delay_steps": 99}, score_bundle(), PromotionRejectionReason.IMPLAUSIBLE_DELAY),
        (
            {key: value for key, value in viable_snapshot().items() if key != "alignment_error_c"},
            score_bundle(),
            PromotionRejectionReason.STATE_ALIGNMENT,
        ),
        ({**viable_snapshot(), "alignment_error_c": 4.0}, score_bundle(), PromotionRejectionReason.STATE_ALIGNMENT),
        (viable_snapshot(), score_bundle(candidate_braking=1.2), PromotionRejectionReason.WORSE_BRAKING),
        (viable_snapshot(), score_bundle(candidate_braking=None), PromotionRejectionReason.WORSE_BRAKING),
    ],
)
def test_invalid_candidate_never_replaces_incumbent(
    snapshot: Mapping[str, object],
    scores: WindowScores,
    expected: PromotionRejectionReason,
) -> None:
    manager = promotion_fixture(challenger_snapshot=snapshot)
    incumbent = manager.incumbent

    decision = manager.evaluate(scores)

    assert decision.promoted is False
    assert expected in decision.reasons
    assert manager.incumbent is incumbent


def test_missing_incumbent_stability_evidence_blocks_promotion() -> None:
    manager = promotion_fixture(incumbent_snapshot={**viable_snapshot(), "poles": []})

    decision = manager.evaluate(score_bundle())

    assert PromotionRejectionReason.UNSTABLE_DYNAMICS in decision.reasons


def test_complex_poles_are_checked_by_magnitude() -> None:
    manager = promotion_fixture(challenger_snapshot={**viable_snapshot(), "poles": [0.6 + 0.4j]})

    manager.evaluate(score_bundle())
    decision = manager.evaluate(score_bundle())

    assert decision.promoted is True
    assert decision.stable_dynamics is True


def test_replay_does_not_substitute_for_successful_challenger_updates() -> None:
    manager = promotion_fixture(challenger_updates=False)

    decision = manager.evaluate(score_bundle())

    assert len(manager.replay) == 2
    assert decision.challenger_effective_updates == 0
    assert PromotionRejectionReason.INSUFFICIENT_SAMPLES in decision.reasons


def test_promotion_atomically_swaps_complete_challenger_snapshot() -> None:
    manager = promotion_fixture()
    incumbent = manager.incumbent
    challenger = manager.challenger

    manager.evaluate(score_bundle())
    decision = manager.evaluate(score_bundle())

    assert decision.promoted is True
    assert decision.consecutive_wins == 2
    assert manager.incumbent is challenger
    assert manager.challenger is incumbent
    assert dict(decision.candidate_snapshot) == {
        **viable_snapshot(),
        "poles": (0.8, 0.6),
    }


def test_dmc_singular_pole_is_stability_evidence() -> None:
    dmc_snapshot = {
        **{key: value for key, value in viable_snapshot().items() if key != "poles"},
        "schema": "laguerre-dmc/v1",
        "pole": 0.92,
    }
    manager = promotion_fixture(
        challenger_snapshot=dmc_snapshot,
        challenger_alignment=AlignmentEvidence.NOT_APPLICABLE,
    )

    manager.evaluate(score_bundle())
    decision = manager.evaluate(score_bundle())

    assert decision.promoted is True
    assert decision.stable_dynamics is True


@pytest.mark.parametrize(
    "schema",
    ["scheduled-arx/v1", "laguerre-dmc/v1"],
)
def test_non_state_space_models_may_declare_alignment_not_applicable(
    schema: str,
) -> None:
    manager = promotion_fixture(
        challenger_snapshot={**viable_snapshot(), "schema": schema},
        challenger_alignment=AlignmentEvidence.NOT_APPLICABLE,
    )

    manager.evaluate(score_bundle())
    decision = manager.evaluate(score_bundle())

    assert decision.promoted is True
    assert decision.state_aligned is True


def test_state_space_requires_measured_alignment_evidence() -> None:
    snapshot = {
        **{key: value for key, value in viable_snapshot().items() if key != "alignment_error_c"},
        "schema": "innovation-state-space/v1",
    }
    manager = promotion_fixture(challenger_snapshot=snapshot)

    decision = manager.evaluate(score_bundle())

    assert PromotionRejectionReason.STATE_ALIGNMENT in decision.reasons
