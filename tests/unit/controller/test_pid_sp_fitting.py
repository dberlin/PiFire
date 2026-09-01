from __future__ import annotations

from dataclasses import replace

import pytest

from common.learning_trajectory import (
    FitCorpusIdentity,
    FrameDeliveryCertainty,
    LearningTrajectoryFrame,
    LearningTrajectorySegment,
    TrajectoryBreakReason,
    canonical_fit_corpus_digest,
    canonical_trajectory_digest,
)
from common.persistence.learning_trajectory import trajectory_frame_prefix_digest
from controller.model_learning.contracts import CandidateOrigin, FitRequest
from controller.model_learning.pid_sp_fitting import PidSpFitStatus, fit_pid_sp_corpus
from controller.pid_sp_delay_evidence import DelayBasin, DelayBlocker, DelayProfile
from controller.pid_sp_model_selection import ModelForm
from tests.unit.common._learning_trajectory_contract_fixtures import (
    _corpus_identity,
    _digest,
    _frame,
    _segment,
    _slice,
)
from tests.unit.controller._pid_sp_model_selection_helpers import _comparison, _fit


def _exact_frame(sequence: int, duty: float, *, role_generation: int = 4) -> LearningTrajectoryFrame:
    frame = _frame(
        sequence,
        delivered_auger_on_seconds=20.0 * duty,
        realized_auger_duty=duty,
        normalized_combustion_load=duty,
    )
    return replace(frame, role_generation=role_generation)


def _episode_segment(
    segment_id: str,
    *,
    start_sequence: int,
    cook_id: str = "cook-1",
    role_generation: int = 4,
) -> LearningTrajectorySegment:
    duties = (0.2, 0.2, 0.7, 0.7, 0.7)
    frames = tuple(
        _exact_frame(start_sequence + offset, duty, role_generation=role_generation)
        for offset, duty in enumerate(duties)
    )
    return _segment(
        segment_id=segment_id,
        cook_id=cook_id,
        trajectory_session_id=f"trajectory-{segment_id}",
        trace_session_ids=(f"trace-{segment_id}",),
        pre_roll_frames=(),
        scored_hold_frames=frames,
        generation_audit_ranges=(
            {
                "start_sequence": frames[0].sequence,
                "end_sequence": frames[-1].sequence,
                "role_generation": role_generation,
            },
        ),
    )


def _identity(*segments: LearningTrajectorySegment) -> FitCorpusIdentity:
    slices = tuple(
        _slice(
            segment.segment_id,
            through_ordinal=len(segment.pre_roll_frames) + len(segment.scored_hold_frames) - 1,
            pre_roll_count=len(segment.pre_roll_frames),
            scored_count=len(segment.scored_hold_frames),
            prefix_digest=trajectory_frame_prefix_digest((*segment.pre_roll_frames, *segment.scored_hold_frames)),
            segment_content_digest=segment.content_digest,
        )
        for segment in segments
    )
    return _corpus_identity(
        slices=slices,
        fit_partition_digest=segments[0].fit_partition_digest,
    )


def _request(identity: FitCorpusIdentity, configuration: object) -> FitRequest:
    return FitRequest(
        request_id="pid-sp-fit-1",
        origin=CandidateOrigin.PASSIVE_ONLINE,
        fit_corpus=identity,
        configuration_digest=canonical_trajectory_digest(configuration),
        parent_incumbent_digest=_digest("incumbent"),
        parent_incumbent_generation=3,
        candidate_generation=4,
    )


def test_materializes_exact_role_generation_and_realized_frame_duty_without_crossing_segment() -> None:
    segment = _episode_segment("episode-segment", start_sequence=10)
    configuration = {"pid_sp": {"enabled": True}}

    result = fit_pid_sp_corpus(
        _request(_identity(segment), configuration),
        (segment,),
        configuration,
    )

    assert result.status is PidSpFitStatus.INSUFFICIENT
    assert result.reason == "insufficient-excitation-episodes"
    assert result.request.fit_corpus == _identity(segment)
    assert len(result.episodes) == 1
    episode = result.episodes[0]
    assert episode.episode_id == "episode-segment:10-14"
    assert episode.transition_at_s == 40.0
    assert episode.duty_before == 0.2
    assert episode.duty_after == 0.7
    assert episode.terminal_reason == TrajectoryBreakReason.STOP.value
    assert [interval.role_generation for interval in episode.intervals] == [4] * 5
    assert [interval.temperature_f for interval in episode.intervals] == [
        frame.chamber_temperature_c * 9.0 / 5.0 + 32.0 for frame in segment.scored_hold_frames
    ]
    assert tuple(
        (part.start_s, part.end_s, part.realized_duty) for part in episode.intervals[0].duty_segments or ()
    ) == ((0.0, 20.0, 0.2),)
    assert result.comparison is None
    assert result.checkpoint_candidate is None


@pytest.mark.parametrize(
    "defect",
    ("invalid-probe", "partial", "discontinuous", "uncertain", "missing-role"),
)
def test_excludes_frames_that_are_not_exact_final_hold_evidence(defect: str) -> None:
    segment = _episode_segment(f"bad-{defect}", start_sequence=20)
    frame = segment.scored_hold_frames[0]
    if defect == "invalid-probe":
        object.__setattr__(frame, "probe_valid", False)
        object.__setattr__(frame, "probe_source", None)
    elif defect == "partial":
        object.__setattr__(frame, "partial", True)
    elif defect == "discontinuous":
        object.__setattr__(frame, "continuous", False)
    elif defect == "uncertain":
        object.__setattr__(frame, "auger_delivery_certainty", FrameDeliveryCertainty.UNKNOWN)
    else:
        object.__setattr__(frame, "role_generation", None)
    configuration = {"pid_sp": {"enabled": True}}

    result = fit_pid_sp_corpus(
        _request(_identity(segment), configuration),
        (segment,),
        configuration,
    )

    assert result.status is PidSpFitStatus.INSUFFICIENT
    assert result.episodes == ()
    assert result.comparison is None


@pytest.mark.parametrize("state", ("open", "quarantined"))
def test_excludes_nonfinalized_segments(state: str) -> None:
    segment = _episode_segment(f"{state}-segment", start_sequence=30)
    object.__setattr__(segment, "state", state)
    if state == "open":
        object.__setattr__(segment, "terminal_break_reason", None)
    configuration = {"pid_sp": {"enabled": True}}

    result = fit_pid_sp_corpus(
        _request(_identity(segment), configuration),
        (segment,),
        configuration,
    )

    assert result.status is PidSpFitStatus.INSUFFICIENT
    assert result.episodes == ()


def test_request_configuration_segment_and_prefix_mismatches_fail_closed() -> None:
    segment = _episode_segment("bound-segment", start_sequence=40)
    configuration = {"pid_sp": {"enabled": True}}
    request = _request(_identity(segment), configuration)
    wrong_configuration = fit_pid_sp_corpus(
        request,
        (segment,),
        {"pid_sp": {"enabled": False}},
    )
    missing_segment = fit_pid_sp_corpus(request, (), configuration)
    incompatible = _episode_segment("bound-segment", start_sequence=40)
    object.__setattr__(incompatible, "fit_partition_digest", _digest("other-partition"))
    wrong_partition = fit_pid_sp_corpus(request, (incompatible,), configuration)
    wrong_slice = replace(
        request.fit_corpus.slices[0],
        prefix_digest=_digest("different-prefix-bytes"),
    )
    wrong_prefix_request = _request(
        _corpus_identity(
            slices=(wrong_slice,),
            fit_partition_digest=segment.fit_partition_digest,
        ),
        configuration,
    )
    wrong_prefix = fit_pid_sp_corpus(
        wrong_prefix_request,
        (segment,),
        configuration,
    )

    assert wrong_configuration.reason == "request-configuration-mismatch"
    assert missing_segment.reason == "request-segment-mismatch"
    assert wrong_partition.reason == "request-segment-mismatch"
    assert wrong_prefix.reason == "request-segment-mismatch"
    assert {
        wrong_configuration.status,
        missing_segment.status,
        wrong_partition.status,
        wrong_prefix.status,
    } == {PidSpFitStatus.FAILED}


def _profile(form: str, bound: int, *, edge: bool) -> DelayProfile:
    blockers = (DelayBlocker.DELAY_BASIN_EDGE,) if edge else ()
    lower = bound - 5 if edge else 40
    upper = bound if edge else 45
    basin = DelayBasin(
        lower_s=lower,
        upper_s=upper,
        representative_s=lower,
        confidence_lower_s=lower,
        confidence_upper_s=upper,
        confidence_method="provided",
        confidence_resamples=0,
        episode_count=2,
        interior=not edge,
        blockers=blockers,
    )
    return DelayProfile(
        model_form=form,
        evaluated_bound_s=bound,
        candidate_losses=((lower, 1.0), (upper, 1.01)),
        episode_ids=("episode-a", "episode-b"),
        basin=basin,
        next_evaluated_bound_s=bound + 150 if edge else None,
        blockers=blockers,
        authorized=not blockers,
    )


def _stub_comparison(monkeypatch: pytest.MonkeyPatch) -> object:
    comparison = _comparison(_fit(ModelForm.IPDT, (1.0, 1.0)))
    monkeypatch.setattr(
        "controller.model_learning.pid_sp_fitting.select_pid_sp_model",
        lambda episodes, profiles: comparison,
    )
    return comparison


def test_two_independent_segments_expand_delay_edges_and_reach_unconfirmed_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _episode_segment("segment-a", start_sequence=10, cook_id="cook-a")
    second = _episode_segment(
        "segment-b",
        start_sequence=20,
        cook_id="cook-b",
        role_generation=5,
    )
    configuration = {"pid_sp": {"enabled": True}}
    request = _request(_identity(first, second), configuration)
    calls: list[tuple[str, int]] = []

    def fake_profile(
        episodes: tuple[object, ...],
        model_form: str,
        max_delay_s: int,
    ) -> DelayProfile:
        assert len(episodes) == 2
        calls.append((model_form, max_delay_s))
        return _profile(model_form, max_delay_s, edge=max_delay_s == 300)

    expected_comparison = _stub_comparison(monkeypatch)
    monkeypatch.setattr(
        "controller.model_learning.pid_sp_fitting.profile_delays",
        fake_profile,
    )

    result = fit_pid_sp_corpus(request, (second, first), configuration)

    assert tuple(episode.episode_id for episode in result.episodes) == (
        "segment-a:10-14",
        "segment-b:20-24",
    )
    assert calls == [
        ("ipdt", 300),
        ("ipdt", 450),
        ("fopdt", 300),
        ("fopdt", 450),
        ("fopdt", 300),
        ("fopdt", 450),
    ]
    assert tuple(profile.model_form for profile in result.delay_profiles) == (
        "ipdt",
        "fopdt",
        "sopdt",
    )
    assert result.comparison is expected_comparison
    assert result.comparison.authorized is False
    assert result.checkpoint_candidate is None


def test_superseded_input_is_ignored_and_segment_input_order_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _episode_segment("segment-a", start_sequence=10)
    second = _episode_segment("segment-b", start_sequence=20, role_generation=5)
    superseded = _episode_segment("superseded", start_sequence=30)
    configuration = {"pid_sp": {"enabled": True}}
    request = _request(_identity(first, second), configuration)
    monkeypatch.setattr(
        "controller.model_learning.pid_sp_fitting._profile_form",
        lambda episodes, form: _profile(form.value, 300, edge=False),
    )
    _stub_comparison(monkeypatch)

    forward = fit_pid_sp_corpus(
        request,
        (first, superseded, second),
        configuration,
    )
    reversed_input = fit_pid_sp_corpus(
        request,
        (second, superseded, first),
        configuration,
    )

    assert forward == reversed_input
    assert all("superseded" not in episode.episode_id for episode in forward.episodes)
    assert forward.checkpoint_candidate is None


def test_one_segment_preserves_every_completed_sustained_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frames = tuple(
        _exact_frame(50 + offset, duty) for offset, duty in enumerate((0.2, 0.2, 0.7, 0.7, 0.7, 0.1, 0.1, 0.1))
    )
    segment = _segment(
        segment_id="multi-transition",
        pre_roll_frames=(),
        scored_hold_frames=frames,
        generation_audit_ranges=(
            {
                "start_sequence": frames[0].sequence,
                "end_sequence": frames[-1].sequence,
                "role_generation": 4,
            },
        ),
    )
    configuration = {"pid_sp": {"enabled": True}}
    request = _request(_identity(segment), configuration)
    monkeypatch.setattr(
        "controller.model_learning.pid_sp_fitting._profile_form",
        lambda episodes, form: _profile(form.value, 300, edge=False),
    )
    _stub_comparison(monkeypatch)

    result = fit_pid_sp_corpus(request, (segment,), configuration)

    assert tuple(episode.episode_id for episode in result.episodes) == (
        "multi-transition:50-54",
        "multi-transition:50-57",
    )
    assert tuple(episode.terminal_reason for episode in result.episodes) == (
        "new-sustained-transition",
        TrajectoryBreakReason.STOP.value,
    )
    assert result.comparison is not None
    assert result.checkpoint_candidate is None


def test_retained_smoke_pre_roll_supports_delay_without_becoming_fit_rows() -> None:
    pre_roll = (
        replace(_exact_frame(0, 0.1), effective_mode="Smoke"),
        replace(_exact_frame(1, 0.3), effective_mode="Smoke"),
    )
    scored = tuple(_exact_frame(2 + offset, duty) for offset, duty in enumerate((0.2, 0.2, 0.7, 0.7, 0.7)))
    segment = _segment(
        segment_id="pre-roll-supported",
        pre_roll_frames=pre_roll,
        scored_hold_frames=scored,
        generation_audit_ranges=(
            {
                "start_sequence": 0,
                "end_sequence": 6,
                "role_generation": 4,
            },
        ),
    )
    configuration = {"pid_sp": {"enabled": True}}

    result = fit_pid_sp_corpus(
        _request(_identity(segment), configuration),
        (segment,),
        configuration,
    )

    assert len(result.episodes) == 1
    episode = result.episodes[0]
    assert tuple(interval.observation_sequence for interval in episode.intervals) == (
        2,
        3,
        4,
        5,
        6,
    )
    assert episode.input_history is not None
    assert tuple((part.start_s, part.end_s, part.realized_duty) for part in episode.input_history.duty_segments) == (
        (-40.0, -20.0, 0.1),
        (-20.0, 0.0, 0.3),
    )


def test_late_transition_builds_duty_history_to_pruned_episode_start() -> None:
    scored = tuple(_exact_frame(100 + offset, duty) for offset, duty in enumerate((*((0.2,) * 55), 0.7, 0.7, 0.7)))
    segment = _segment(
        segment_id="late-transition",
        pre_roll_frames=(),
        scored_hold_frames=scored,
        generation_audit_ranges=(
            {
                "start_sequence": scored[0].sequence,
                "end_sequence": scored[-1].sequence,
                "role_generation": 4,
            },
        ),
    )
    configuration = {"pid_sp": {"enabled": True}}

    result = fit_pid_sp_corpus(
        _request(_identity(segment), configuration),
        (segment,),
        configuration,
    )

    assert len(result.episodes) == 1
    episode = result.episodes[0]
    assert episode.intervals[0].start_s > 0
    assert episode.input_history is not None
    assert episode.input_history.duty_segments[0].start_s == 0
    assert episode.input_history.duty_segments[-1].end_s == episode.intervals[0].start_s
    assert len(episode.input_history.duty_segments) == (episode.intervals[0].observation_sequence - scored[0].sequence)


def test_observation_schema_v2_is_readable_but_never_pid_sp_fit_evidence() -> None:
    segment = _episode_segment("legacy-v2", start_sequence=70)
    object.__setattr__(segment, "observation_schema_version", 2)
    configuration = {"pid_sp": {"enabled": True}}

    result = fit_pid_sp_corpus(
        _request(_identity(segment), configuration),
        (segment,),
        configuration,
    )

    assert result.status is PidSpFitStatus.INSUFFICIENT
    assert result.episodes == ()


def test_legacy_v1_corpus_is_readable_but_never_pid_sp_fit_evidence() -> None:
    segment = _episode_segment("legacy-corpus-v1", start_sequence=75)
    current = _identity(segment)
    legacy_slices = tuple(replace(item, segment_content_digest=None) for item in current.slices)
    legacy = FitCorpusIdentity(
        schema_version=1,
        corpus_revision=current.corpus_revision,
        fit_partition_digest=current.fit_partition_digest,
        slices=legacy_slices,
        corpus_digest=canonical_fit_corpus_digest(
            schema_version=1,
            corpus_revision=current.corpus_revision,
            fit_partition_digest=current.fit_partition_digest,
            slices=legacy_slices,
        ),
    )
    configuration = {"pid_sp": {"enabled": True}}

    result = fit_pid_sp_corpus(
        _request(legacy, configuration),
        (segment,),
        configuration,
    )

    assert result.status is PidSpFitStatus.FAILED
    assert result.reason == "request-corpus-schema-unsupported"


@pytest.mark.parametrize(
    "metadata_change",
    ("state", "terminal-reason", "generation-audit"),
)
def test_request_rejects_segment_content_changes(
    metadata_change: str,
) -> None:
    original = _episode_segment("metadata-bound", start_sequence=80)
    if metadata_change == "state":
        changed = replace(original, state="quarantined")
    elif metadata_change == "terminal-reason":
        changed = replace(
            original,
            terminal_break_reason=TrajectoryBreakReason.RETENTION_ROLLOVER,
        )
    else:
        changed = replace(
            original,
            scored_hold_frames=tuple(replace(frame, role_generation=5) for frame in original.scored_hold_frames),
            generation_audit_ranges=(
                {
                    "start_sequence": 80,
                    "end_sequence": 84,
                    "role_generation": 5,
                },
            ),
        )
    assert changed.content_digest != original.content_digest
    configuration = {"pid_sp": {"enabled": True}}

    result = fit_pid_sp_corpus(
        _request(_identity(original), configuration),
        (changed,),
        configuration,
    )

    assert result.status is PidSpFitStatus.FAILED
    assert result.reason == "request-segment-mismatch"


def _wide_profile(form: ModelForm) -> DelayProfile:
    blocker = DelayBlocker.DELAY_BASIN_TOO_WIDE
    basin = DelayBasin(
        lower_s=5,
        upper_s=10,
        representative_s=5,
        confidence_lower_s=5,
        confidence_upper_s=100,
        confidence_method="provided",
        confidence_resamples=0,
        episode_count=2,
        interior=True,
        blockers=(blocker,),
    )
    return DelayProfile(
        model_form=form.value,
        evaluated_bound_s=300,
        candidate_losses=((5, 1.0), (10, 1.01)),
        episode_ids=("segment-a:10-14", "segment-b:20-24"),
        basin=basin,
        next_evaluated_bound_s=None,
        blockers=(blocker,),
        authorized=False,
    )


def test_delay_basin_no_selection_is_exact_typed_identifiability_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _episode_segment("segment-a", start_sequence=10)
    second = _episode_segment("segment-b", start_sequence=20, role_generation=5)
    configuration = {"pid_sp": {"enabled": True}}
    request = _request(_identity(first, second), configuration)
    monkeypatch.setattr(
        "controller.model_learning.pid_sp_fitting._profile_form",
        lambda episodes, form: _wide_profile(form),
    )
    comparison = _comparison(
        _fit(
            ModelForm.IPDT,
            (1.0, 1.0),
            basin=(DelayBlocker.DELAY_BASIN_TOO_WIDE,),
        )
    )
    monkeypatch.setattr(
        "controller.model_learning.pid_sp_fitting.select_pid_sp_model",
        lambda episodes, profiles: comparison,
    )

    result = fit_pid_sp_corpus(request, (first, second), configuration)

    assert result.status is PidSpFitStatus.BLOCKED
    assert result.reason == "insufficient-delay-identifiability"
    assert result.comparison is comparison
    assert all(profile.blockers == (DelayBlocker.DELAY_BASIN_TOO_WIDE,) for profile in result.delay_profiles)
    assert result.checkpoint_candidate is None


@pytest.mark.parametrize(
    "blocker_field",
    ("physical", "uncertainty", "stability", "validation"),
)
def test_authorized_delays_with_rejected_model_fit_have_distinct_reason(
    monkeypatch: pytest.MonkeyPatch,
    blocker_field: str,
) -> None:
    first = _episode_segment("segment-a", start_sequence=10)
    second = _episode_segment("segment-b", start_sequence=20, role_generation=5)
    configuration = {"pid_sp": {"enabled": True}}
    request = _request(_identity(first, second), configuration)
    monkeypatch.setattr(
        "controller.model_learning.pid_sp_fitting._profile_form",
        lambda episodes, form: _profile(form.value, 300, edge=False),
    )
    comparison = _comparison(
        _fit(
            ModelForm.IPDT,
            (1.0, 1.0),
            **{blocker_field: (f"{blocker_field}-rejection",)},
        )
    )
    assert comparison.selected is None
    assert all(not fit.basin_blockers for fit in comparison.fits)
    monkeypatch.setattr(
        "controller.model_learning.pid_sp_fitting.select_pid_sp_model",
        lambda episodes, profiles: comparison,
    )

    result = fit_pid_sp_corpus(request, (first, second), configuration)

    assert result.status is PidSpFitStatus.BLOCKED
    assert result.reason == "model-comparison-rejected"
    assert result.comparison is comparison


def test_unexpected_profile_contract_fault_is_not_scientific_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _episode_segment("segment-a", start_sequence=10)
    second = _episode_segment("segment-b", start_sequence=20, role_generation=5)
    configuration = {"pid_sp": {"enabled": True}}
    request = _request(_identity(first, second), configuration)

    def contract_fault(episodes, form):
        raise ValueError("corrupt scientific input contract")

    monkeypatch.setattr(
        "controller.model_learning.pid_sp_fitting._profile_form",
        contract_fault,
    )

    with pytest.raises(ValueError, match="corrupt scientific input contract"):
        fit_pid_sp_corpus(request, (first, second), configuration)
