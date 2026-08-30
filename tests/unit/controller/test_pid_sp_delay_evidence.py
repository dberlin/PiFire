from __future__ import annotations

import dataclasses
import json
import math

import numpy as np
import pytest

import controller.pid_sp_delay_evidence as delay_evidence
from controller.pid_sp_delay_evidence import (
    DelayBasin,
    DelayBlocker,
    DelayProfile,
    EpisodeAccumulator,
    EpisodeInputHistory,
    ExcitationEpisode,
    profile_delays,
    select_delay_basin,
)
from controller.pid_sp_observation import PidSpDutySegment, PidSpInterval
from tests.e2e.real_cook_replay import (
    load_pid_sp_august_28_intervals as _load_successive_terminal_update_intervals,
)


def _interval(
    index: int,
    *,
    duty: float,
    temperature_f: float,
    continuous: bool = True,
) -> PidSpInterval:
    return PidSpInterval(
        start_s=index * 20.0,
        end_s=(index + 1) * 20.0,
        temperature_f=temperature_f,
        realized_duty=duty,
        continuous=continuous,
        observation_sequence=index + 1,
        role_generation=0,
    )


def _canonical_completed_evidence(accumulator: EpisodeAccumulator) -> bytes:
    episodes = accumulator.completed()
    profile = profile_delays(episodes, model_form="ipdt", max_delay_s=300)
    return json.dumps(
        {
            "episodes": [dataclasses.asdict(episode) for episode in episodes],
            "profile": dataclasses.asdict(profile),
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _losses_with_basin(lower_s: int, upper_s: int, representative_s: int, bound_s: int):
    return tuple(
        (
            delay_s,
            1.0
            if delay_s == representative_s
            else 1.04
            if lower_s <= delay_s <= upper_s
            else 1.051
            if delay_s in (lower_s - 5, upper_s + 5)
            else 1.25,
        )
        for delay_s in range(0, bound_s + 1, 5)
    )


def _august_episode(intervals: tuple[PidSpInterval, ...]) -> ExcitationEpisode:
    return ExcitationEpisode(
        episode_id="august-28-cook-episode",
        intervals=intervals,
        transition_at_s=intervals[1].start_s,
        duty_before=intervals[0].realized_duty,
        duty_after=intervals[1].realized_duty,
        terminal_reason="archive-end",
    )


def _alternating_ipdt_episode(episode_id: str, noise_phase: int) -> ExcitationEpisode:
    def duty_at(time_s: float) -> float:
        return float((math.floor(time_s / 100.0) % 2) == 0)

    intervals: list[PidSpInterval] = []
    temperature_f = 200.0
    for sequence in range(140):
        start_s = -300.0 + sequence * 20.0
        end_s = start_s + 20.0
        duty = duty_at(start_s)
        delayed_duty = duty_at(start_s - 100.0)
        deterministic_noise = 0.025 * (((sequence + noise_phase) % 7) - 3)
        temperature_f += 20.0 * (-0.10 + 0.40 * delayed_duty + deterministic_noise)
        intervals.append(
            PidSpInterval(
                start_s=start_s,
                end_s=end_s,
                temperature_f=temperature_f,
                realized_duty=duty,
                continuous=True,
                observation_sequence=sequence + 1,
                role_generation=0,
            )
        )
    return ExcitationEpisode(
        episode_id=episode_id,
        intervals=tuple(intervals),
        transition_at_s=0.0,
        duty_before=0.0,
        duty_after=1.0,
        terminal_reason="response-window-complete",
    )


def test_flat_rows_do_not_change_a_completed_episode_profile():
    accumulator = EpisodeAccumulator()

    for index in range(10):
        accumulator.observe(_interval(index, duty=0.20, temperature_f=100.0))
    for index in range(10, 120):
        elapsed_s = (index - 10) * 20.0
        response_s = max(0.0, elapsed_s - 200.0)
        temperature_f = 100.0 + 80.0 * (1.0 - math.exp(-response_s / 500.0))
        accumulator.observe(_interval(index, duty=0.65, temperature_f=temperature_f))
    accumulator.observe(_interval(120, duty=0.65, temperature_f=178.0, continuous=False))

    completed = accumulator.completed()
    assert isinstance(completed, tuple)
    assert len(completed) == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        completed[0].terminal_reason = "mutated"
    frozen = _canonical_completed_evidence(accumulator)

    for index in range(121, 221):
        accumulator.observe(_interval(index, duty=0.65, temperature_f=178.0))

    assert _canonical_completed_evidence(accumulator) == frozen


def test_raw_basin_selection_reports_shape_but_cannot_authorize():
    profile = select_delay_basin(
        _losses_with_basin(185, 225, 205, 300),
        episode_ids=("episode-a", "episode-b"),
        evaluated_bound_s=300,
    )

    basin = profile.basin
    assert (basin.lower_s, basin.upper_s) == (185, 225)
    assert basin.representative_s == 205
    assert basin.episode_count == 2
    assert basin.width_s == 40
    assert basin.interior is True
    assert basin.blockers == (DelayBlocker.INSUFFICIENT_CONFIDENCE_EVIDENCE,)
    assert profile.blockers == basin.blockers
    assert profile.authorized is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        basin.lower_s = 180
    with pytest.raises(dataclasses.FrozenInstanceError):
        profile.authorized = False


def test_upper_edge_expands_from_300_to_450_seconds():
    profile = select_delay_basin(
        _losses_with_basin(290, 300, 300, 300),
        episode_ids=("episode-a", "episode-b"),
        evaluated_bound_s=300,
        confidence_interval_s=(290, 300),
    )

    assert profile.basin is not None
    assert profile.basin.upper_s == 300
    assert profile.basin.interior is False
    assert profile.blockers == (DelayBlocker.DELAY_BASIN_EDGE,)
    assert profile.basin.blockers == profile.blockers
    assert tuple(blocker.value for blocker in profile.blockers) == ("delay-basin-edge",)
    assert profile.next_evaluated_bound_s == 450
    assert profile.authorized is False


def test_basin_touching_900_seconds_fails_closed():
    profile = select_delay_basin(
        _losses_with_basin(880, 900, 895, 900),
        episode_ids=("episode-a", "episode-b"),
        evaluated_bound_s=900,
        confidence_interval_s=(880, 900),
    )

    assert profile.basin is not None
    assert profile.basin.upper_s == 900
    assert profile.blockers == (DelayBlocker.DELAY_RANGE_EXHAUSTED,)
    assert profile.basin.blockers == profile.blockers
    assert tuple(blocker.value for blocker in profile.blockers) == ("delay-range-exhausted",)
    assert profile.next_evaluated_bound_s is None
    assert profile.authorized is False


def test_two_independent_episodes_are_required_for_authorization():
    losses = _losses_with_basin(185, 225, 205, 300)
    one_episode = select_delay_basin(
        losses,
        episode_ids=("episode-a",),
        evaluated_bound_s=300,
        confidence_interval_s=(185, 225),
    )
    repeated_rows_are_still_one_episode = select_delay_basin(
        losses,
        episode_ids=("episode-a", "episode-a"),
        evaluated_bound_s=300,
        confidence_interval_s=(185, 225),
    )
    two_episodes = select_delay_basin(
        losses,
        episode_ids=("episode-a", "episode-b"),
        evaluated_bound_s=300,
        confidence_interval_s=(185, 225),
    )

    assert one_episode.blockers == (DelayBlocker.INSUFFICIENT_EXCITATION_EPISODES,)
    assert one_episode.basin is not None
    assert one_episode.basin.blockers == one_episode.blockers
    assert tuple(blocker.value for blocker in one_episode.blockers) == ("insufficient-excitation-episodes",)
    assert one_episode.authorized is False
    assert repeated_rows_are_still_one_episode.blockers == (DelayBlocker.INSUFFICIENT_EXCITATION_EPISODES,)
    assert repeated_rows_are_still_one_episode.authorized is False
    assert DelayBlocker.INSUFFICIENT_EXCITATION_EPISODES not in two_episodes.blockers
    assert two_episodes.authorized is True


def test_august_28_fixture_reports_raw_190_225_basin_with_broad_confidence():
    intervals = _load_successive_terminal_update_intervals()
    episode = _august_episode(intervals)

    profile = profile_delays((episode,), model_form="ipdt", max_delay_s=300)

    assert profile.basin is not None
    best_index = min(
        range(len(profile.candidate_losses)),
        key=lambda index: profile.candidate_losses[index][1],
    )
    threshold = profile.candidate_losses[best_index][1] * 1.05
    raw_lower_index = best_index
    while raw_lower_index and profile.candidate_losses[raw_lower_index - 1][1] <= threshold:
        raw_lower_index -= 1
    raw_upper_index = best_index
    while (
        raw_upper_index + 1 < len(profile.candidate_losses)
        and profile.candidate_losses[raw_upper_index + 1][1] <= threshold
    ):
        raw_upper_index += 1
    assert (
        profile.candidate_losses[raw_lower_index][0],
        profile.candidate_losses[raw_upper_index][0],
    ) == (190, 225)
    assert (
        profile.basin.confidence_lower_s,
        profile.basin.confidence_upper_s,
    ) == (125, 225)
    assert profile.basin.confidence_method == "moving-block-refit"
    assert profile.basin.confidence_resamples == 500
    assert (profile.basin.lower_s, profile.basin.upper_s) == (190, 225)
    assert profile.basin.representative_s == 205
    assert profile.basin.width_s == 35
    assert profile.basin.interior is True
    assert profile.blockers == (
        DelayBlocker.DELAY_BASIN_TOO_WIDE,
        DelayBlocker.INSUFFICIENT_EXCITATION_EPISODES,
    )
    assert profile.basin.blockers == profile.blockers
    assert tuple(blocker.value for blocker in profile.blockers) == (
        "delay-basin-too-wide",
        "insufficient-excitation-episodes",
    )
    assert profile.authorized is False


def test_every_delay_candidate_uses_the_same_common_observation_support():
    intervals = _load_successive_terminal_update_intervals()
    original = _august_episode(intervals)
    altered_early_rows = tuple(
        dataclasses.replace(
            interval,
            temperature_f=interval.temperature_f + 75.0 * math.sin(interval.end_s),
        )
        if interval.end_s < 250.0
        else interval
        for interval in intervals
    )
    altered = dataclasses.replace(original, intervals=altered_early_rows)

    original_profile = profile_delays((original,), model_form="ipdt", max_delay_s=300)
    altered_profile = profile_delays((altered,), model_form="ipdt", max_delay_s=300)

    assert altered_profile.candidate_losses == original_profile.candidate_losses
    assert altered_profile == original_profile


def test_authorization_consumes_moving_block_confidence_bounds():
    wide_confidence = select_delay_basin(
        _losses_with_basin(185, 225, 205, 300),
        episode_ids=("episode-a", "episode-b"),
        evaluated_bound_s=300,
        confidence_interval_s=(170, 240),
    )
    edge_confidence = select_delay_basin(
        _losses_with_basin(20, 40, 30, 300),
        episode_ids=("episode-a", "episode-b"),
        evaluated_bound_s=300,
        confidence_interval_s=(0, 35),
    )

    assert wide_confidence.basin is not None
    assert (
        wide_confidence.basin.lower_s,
        wide_confidence.basin.upper_s,
    ) == (185, 225)
    assert (
        wide_confidence.basin.confidence_lower_s,
        wide_confidence.basin.confidence_upper_s,
    ) == (170, 240)
    assert wide_confidence.basin.confidence_method == "provided"
    assert wide_confidence.basin.confidence_resamples == 0
    assert wide_confidence.blockers == (DelayBlocker.DELAY_BASIN_TOO_WIDE,)
    assert wide_confidence.authorized is False
    assert edge_confidence.basin is not None
    assert (
        edge_confidence.basin.lower_s,
        edge_confidence.basin.upper_s,
    ) == (20, 40)
    assert (
        edge_confidence.basin.confidence_lower_s,
        edge_confidence.basin.confidence_upper_s,
    ) == (0, 35)
    assert edge_confidence.blockers == (DelayBlocker.DELAY_BASIN_EDGE,)
    assert edge_confidence.authorized is False


def test_invalid_off_basin_candidate_does_not_collapse_confidence_resampling():
    episodes = (
        _alternating_ipdt_episode("alternating-a", noise_phase=0),
        _alternating_ipdt_episode("alternating-b", noise_phase=3),
    )

    first = profile_delays(episodes, model_form="ipdt", max_delay_s=300)
    second = profile_delays(episodes, model_form="ipdt", max_delay_s=300)

    assert first == second
    assert first.basin is not None
    assert first.basin.episode_count == 2
    assert first.basin.confidence_method == "moving-block-refit"
    assert first.basin.confidence_resamples == 500
    best_loss = min(loss for _, loss in first.candidate_losses)
    assert first.candidate_losses[0][1] > best_loss * 100.0


def test_no_physically_valid_candidate_returns_typed_unavailable_profile(
    monkeypatch,
) -> None:
    episodes = (
        _alternating_ipdt_episode("episode-a", 0),
        _alternating_ipdt_episode("episode-b", 1),
    )
    monkeypatch.setattr(
        delay_evidence,
        "_candidate_validation_loss",
        lambda _designs, _model_form: math.inf,
    )

    profile = profile_delays(episodes, model_form="fopdt", max_delay_s=300)

    assert profile.model_form == "fopdt"
    assert profile.evaluated_bound_s == 300
    assert profile.candidate_losses == tuple((delay_s, 1e300) for delay_s in range(0, 301, 5))
    assert profile.episode_ids == ("episode-a", "episode-b")
    assert profile.basin is None
    assert profile.next_evaluated_bound_s is None
    assert profile.blockers == (DelayBlocker.NO_PHYSICALLY_VALID_CANDIDATE,)
    assert profile.authorized is False


def test_profile_delays_does_not_translate_candidate_kernel_contract_errors(
    monkeypatch,
) -> None:
    episodes = (
        _alternating_ipdt_episode("episode-a", 0),
        _alternating_ipdt_episode("episode-b", 1),
    )

    def fail_contract(_designs, _model_form):
        raise RuntimeError("candidate-kernel-contract")

    monkeypatch.setattr(
        delay_evidence,
        "_candidate_validation_loss",
        fail_contract,
    )

    with pytest.raises(RuntimeError, match="candidate-kernel-contract"):
        profile_delays(episodes, model_form="fopdt", max_delay_s=300)


def test_second_sustained_transition_preserves_two_uncontaminated_episodes():
    accumulator = EpisodeAccumulator()
    for index in range(4):
        accumulator.observe(_interval(index, duty=0.20, temperature_f=100.0))
    for index in range(4, 8):
        accumulator.observe(_interval(index, duty=0.60, temperature_f=100.0 + index))
    for index in range(8, 12):
        accumulator.observe(_interval(index, duty=0.30, temperature_f=108.0 + index))
    accumulator.observe(_interval(12, duty=0.30, temperature_f=120.0, continuous=False))

    first, second = accumulator.completed()
    assert first.episode_id != second.episode_id
    assert first.terminal_reason == "new-sustained-transition"
    assert first.duty_before == 0.20
    assert first.duty_after == 0.60
    assert second.duty_before == 0.60
    assert second.duty_after == 0.30
    assert all(interval.end_s <= second.transition_at_s for interval in first.intervals)
    assert second.terminal_reason == "discontinuity"


def test_completed_episode_retention_evicts_the_oldest_evidence():
    accumulator = EpisodeAccumulator(max_completed_episodes=2)
    index = 0
    for _ in range(4):
        accumulator.observe(_interval(index, duty=0.10, temperature_f=100.0))
        index += 1

    evicted_episode_id = None
    for target_duty in (0.60, 0.20, 0.70):
        for _ in range(4):
            accumulator.observe(_interval(index, duty=target_duty, temperature_f=100.0 + index))
            index += 1
        accumulator.observe(
            _interval(
                index,
                duty=target_duty,
                temperature_f=100.0 + index,
                continuous=False,
            )
        )
        index += 1
        if evicted_episode_id is None:
            evicted_episode_id = accumulator.completed()[0].episode_id

    retained = accumulator.completed()
    assert len(retained) == 2
    assert evicted_episode_id not in tuple(episode.episode_id for episode in retained)


def test_excitation_episode_rejects_a_subthreshold_transition():
    with pytest.raises(ValueError, match="transition"):
        ExcitationEpisode(
            episode_id="too-small",
            intervals=(_interval(0, duty=0.20, temperature_f=100.0),),
            transition_at_s=0.0,
            duty_before=0.20,
            duty_after=0.24,
            terminal_reason="discontinuity",
        )


def test_moving_block_refit_stays_within_episode_and_scores_held_out_rows(monkeypatch):
    sampled_rows = delay_evidence._sample_episode_rows(
        row_count=8,
        block_length=4,
        generator=np.random.default_rng(7),
    )
    assert sampled_rows.shape == (8,)
    for block in sampled_rows.reshape(2, 4):
        np.testing.assert_array_equal(np.diff(block), np.ones(3, dtype=int))
        assert int(block[0]) >= 0
        assert int(block[-1]) < 8

    monkeypatch.setattr(
        delay_evidence,
        "_fit_physical",
        lambda _x, _y, _form: np.array([0.0, 0.0]),
    )
    sampled_training_x = np.column_stack((np.ones(8), np.arange(8, dtype=float)))
    sampled_training_y = np.ones(8)
    held_out_x = np.column_stack((np.ones(3), np.arange(3, dtype=float)))
    held_out_y = np.full(3, 10.0)

    loss = delay_evidence._refit_score_held_out(
        sampled_training_x,
        sampled_training_y,
        held_out_x,
        held_out_y,
        sampled_rows,
        "ipdt",
    )

    assert loss == 100.0
    assert loss != 1.0


def test_nonbootstrap_confidence_fallback_cannot_authorize_a_single_candidate():
    losses = tuple((delay_s, 1.0 if delay_s == 150 else 1e300) for delay_s in range(0, 301, 5))

    profile = select_delay_basin(
        losses,
        episode_ids=("episode-a", "episode-b"),
        evaluated_bound_s=300,
    )

    assert profile.basin is not None
    assert (profile.basin.lower_s, profile.basin.upper_s) == (150, 150)
    assert profile.basin.confidence_method == "raw-basin"
    assert profile.basin.confidence_resamples == 0
    assert profile.blockers == (DelayBlocker.INSUFFICIENT_CONFIDENCE_EVIDENCE,)
    assert profile.authorized is False


def test_confidence_sufficiency_cannot_be_overridden_by_a_caller():
    with pytest.raises(TypeError, match="confidence_evidence_sufficient"):
        select_delay_basin(
            _losses_with_basin(185, 225, 205, 300),
            episode_ids=("episode-a", "episode-b"),
            evaluated_bound_s=300,
            confidence_evidence_sufficient=True,
        )


def test_partial_moving_block_success_is_insufficient_confidence(monkeypatch):
    row = delay_evidence._TemperatureRow(
        start_s=0.0,
        end_s=20.0,
        previous_temperature_f=100.0,
        terminal_temperature_f=101.0,
        ledger=object(),
    )
    prepared = ((row, row, row, row), (row, row, row, row))
    candidate_designs = tuple(
        (
            delay_s,
            (
                (np.full((4, 2), marker), np.ones(4)),
                (np.full((4, 2), marker), np.ones(4)),
            ),
        )
        for delay_s, marker in ((100, 1.0), (105, 2.0))
    )
    calls = 0

    def partially_finite_refit(*args):
        nonlocal calls
        attempt = calls // 2
        calls += 1
        if attempt == 0:
            return math.inf
        training_x = args[0]
        return float(training_x[0, 0])

    monkeypatch.setattr(
        delay_evidence,
        "_refit_score_held_out",
        partially_finite_refit,
    )
    basin = DelayBasin(
        lower_s=100,
        upper_s=105,
        representative_s=100,
        confidence_lower_s=100,
        confidence_upper_s=105,
        confidence_method="raw-basin",
        confidence_resamples=0,
        episode_count=2,
        interior=True,
        blockers=(DelayBlocker.INSUFFICIENT_CONFIDENCE_EVIDENCE,),
    )

    confidence = delay_evidence._confidence_bounds(
        basin,
        candidate_designs,
        prepared,
        "ipdt",
    )

    assert calls == 1_000
    assert confidence.sufficient is False
    assert confidence.method == "raw-basin"
    assert confidence.resamples == 499
    profile = select_delay_basin(
        tuple((delay_s, 1.0) for delay_s in range(0, 301, 5)),
        episode_ids=("episode-a", "episode-b"),
        evaluated_bound_s=300,
    )
    assert DelayBlocker.INSUFFICIENT_CONFIDENCE_EVIDENCE in profile.blockers
    assert profile.authorized is False


def test_delay_profile_rejects_authorization_with_an_exhausted_range():
    blockers = (DelayBlocker.DELAY_RANGE_EXHAUSTED,)
    basin = DelayBasin(
        lower_s=880,
        upper_s=900,
        representative_s=895,
        confidence_lower_s=880,
        confidence_upper_s=900,
        confidence_method="provided",
        confidence_resamples=0,
        episode_count=2,
        interior=False,
        blockers=blockers,
    )

    with pytest.raises(ValueError, match="authorized"):
        DelayProfile(
            model_form="ipdt",
            evaluated_bound_s=900,
            candidate_losses=((880, 1.04), (895, 1.0), (900, 1.02)),
            episode_ids=("episode-a", "episode-b"),
            basin=basin,
            next_evaluated_bound_s=None,
            blockers=blockers,
            authorized=True,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"lower_s": 210, "representative_s": 205},
        {"upper_s": 200, "representative_s": 205},
        {"confidence_lower_s": 230, "confidence_upper_s": 225},
    ],
)
def test_delay_basin_rejects_inconsistent_bounds(changes):
    values = {
        "lower_s": 190,
        "upper_s": 225,
        "representative_s": 205,
        "confidence_lower_s": 185,
        "confidence_upper_s": 230,
        "confidence_method": "provided",
        "confidence_resamples": 0,
        "episode_count": 2,
        "interior": True,
        "blockers": (),
    }
    values.update(changes)

    with pytest.raises(ValueError, match="ordered"):
        DelayBasin(**values)


def test_delay_profile_rejects_basin_audit_disagreement():
    basin = DelayBasin(
        lower_s=190,
        upper_s=225,
        representative_s=205,
        confidence_lower_s=185,
        confidence_upper_s=230,
        confidence_method="provided",
        confidence_resamples=0,
        episode_count=2,
        interior=True,
        blockers=(),
    )

    with pytest.raises(ValueError, match="episode"):
        DelayProfile(
            model_form="ipdt",
            evaluated_bound_s=300,
            candidate_losses=((190, 1.0), (225, 1.04)),
            episode_ids=("episode-a",),
            basin=basin,
            next_evaluated_bound_s=None,
            blockers=(),
            authorized=True,
        )


def test_typed_input_history_supports_delayed_duty_without_temperature_rows() -> None:
    history = EpisodeInputHistory(
        duty_segments=(
            PidSpDutySegment(-40.0, -20.0, 0.2),
            PidSpDutySegment(-20.0, 0.0, 0.4),
        )
    )
    episode = ExcitationEpisode(
        episode_id="history-supported",
        intervals=(
            _interval(0, duty=0.7, temperature_f=200.0),
            _interval(1, duty=0.7, temperature_f=201.0),
        ),
        transition_at_s=0.0,
        duty_before=0.4,
        duty_after=0.7,
        terminal_reason="stop",
        input_history=history,
    )

    ledger = delay_evidence._DutyLedger(episode)

    assert ledger.average(-40.0, -20.0) == 0.2
    assert ledger.average(-20.0, 0.0) == 0.4
    assert tuple(interval.temperature_f for interval in episode.intervals) == (
        200.0,
        201.0,
    )
