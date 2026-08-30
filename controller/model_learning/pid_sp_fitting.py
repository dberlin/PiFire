"""Pure materialization and fitting of finalized PID-SP trajectory evidence."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from common.learning_trajectory import (
    TRAJECTORY_OBSERVATION_SCHEMA_VERSION,
    LearningTrajectoryFrame,
    LearningTrajectorySegment,
    canonical_trajectory_digest,
)
from common.persistence.learning_trajectory import trajectory_frame_prefix_digest
from controller.model_learning.contracts import FitRequest
from controller.pid_sp_delay_evidence import (
    INITIAL_DELAY_BOUND_S,
    DelayProfile,
    EpisodeAccumulator,
    EpisodeInputHistory,
    ExcitationEpisode,
    profile_delays,
)
from controller.pid_sp_model_selection import (
    ModelComparison,
    ModelForm,
    select_pid_sp_model,
)
from controller.pid_sp_observation import PidSpDutySegment, PidSpInterval


class PidSpFitStatus(StrEnum):
    FAILED = "failed"
    INSUFFICIENT = "insufficient"
    BLOCKED = "blocked"
    EVALUATED = "evaluated"


@dataclass(frozen=True, slots=True)
class PidSpFitResult:
    request: FitRequest
    status: PidSpFitStatus
    reason: str
    episodes: tuple[ExcitationEpisode, ...] = ()
    delay_profiles: tuple[DelayProfile, ...] = ()
    comparison: ModelComparison | None = None
    checkpoint_candidate: None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request, FitRequest):
            raise TypeError("request must be a FitRequest")
        if not isinstance(self.status, PidSpFitStatus):
            raise TypeError("status must be a PidSpFitStatus")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("reason must be nonblank")


def _failed(request: FitRequest, reason: str) -> PidSpFitResult:
    return PidSpFitResult(request=request, status=PidSpFitStatus.FAILED, reason=reason)


def _eligible_frame(frame: LearningTrajectoryFrame) -> bool:
    return (
        frame.complete
        and frame.continuous
        and not frame.partial
        and frame.probe_valid
        and frame.probe_source is not None
        and frame.effective_mode == "Hold"
        and frame.auger_delivery_certainty.value == "exact"
        and frame.fan_delivery_certainty.value == "exact"
        and frame.role_generation is not None
    )


def _audit_generation(segment: LearningTrajectorySegment, frame: LearningTrajectoryFrame) -> bool:
    matching = tuple(
        audit
        for audit in segment.generation_audit_ranges
        if isinstance(audit.get("start_sequence"), int)
        and isinstance(audit.get("end_sequence"), int)
        and audit["start_sequence"] <= frame.sequence <= audit["end_sequence"]
    )
    return len(matching) == 1 and matching[0].get("role_generation") == frame.role_generation


def _input_history(
    segment: LearningTrajectorySegment,
    origin_ms: int,
    episode_start_s: float,
) -> EpisodeInputHistory | None:
    retained: list[LearningTrajectoryFrame] = []
    next_start_ms = origin_ms + round(episode_start_s * 1_000.0)
    available = (
        *segment.pre_roll_frames,
        *(frame for frame in segment.scored_hold_frames if frame.monotonic_end_ms <= next_start_ms),
    )
    for frame in reversed(available):
        if (
            frame.monotonic_end_ms != next_start_ms
            or not frame.continuous
            or frame.auger_delivery_certainty.value != "exact"
        ):
            break
        retained.append(frame)
        next_start_ms = frame.monotonic_start_ms
    if not retained:
        return None
    retained.reverse()
    return EpisodeInputHistory(
        duty_segments=tuple(
            PidSpDutySegment(
                start_s=(frame.monotonic_start_ms - origin_ms) / 1_000.0,
                end_s=(frame.monotonic_end_ms - origin_ms) / 1_000.0,
                realized_duty=frame.realized_auger_duty,
            )
            for frame in retained
        )
    )


def _materialize_episodes(
    segment: LearningTrajectorySegment,
) -> tuple[ExcitationEpisode, ...]:
    frames = segment.scored_hold_frames
    if (
        segment.observation_schema_version != TRAJECTORY_OBSERVATION_SCHEMA_VERSION
        or segment.state != "finalized"
        or segment.terminal_break_reason is None
        or not frames
        or not all(_eligible_frame(frame) and _audit_generation(segment, frame) for frame in frames)
    ):
        return ()
    origin_ms = frames[0].monotonic_start_ms
    accumulator = EpisodeAccumulator()
    for frame in frames:
        accumulator.observe(
            PidSpInterval(
                start_s=(frame.monotonic_start_ms - origin_ms) / 1_000.0,
                end_s=(frame.monotonic_end_ms - origin_ms) / 1_000.0,
                temperature_f=frame.chamber_temperature_c * 9.0 / 5.0 + 32.0,
                realized_duty=frame.realized_auger_duty,
                continuous=frame.continuous,
                observation_sequence=frame.sequence,
                role_generation=frame.role_generation,
            )
        )
    accumulator.interrupt(segment.terminal_break_reason.value)
    return tuple(
        replace(
            episode,
            episode_id=(
                f"{segment.segment_id}:"
                f"{episode.intervals[0].observation_sequence}-"
                f"{episode.intervals[-1].observation_sequence}"
            ),
            input_history=_input_history(
                segment,
                origin_ms,
                episode.intervals[0].start_s,
            ),
        )
        for episode in accumulator.completed()
    )


def _profile_form(episodes: tuple[ExcitationEpisode, ...], form: ModelForm) -> DelayProfile:
    evidence_form = ModelForm.FOPDT if form is ModelForm.SOPDT else form
    bound = INITIAL_DELAY_BOUND_S
    while True:
        profile = profile_delays(episodes, evidence_form.value, bound)
        if profile.next_evaluated_bound_s is None:
            return replace(profile, model_form=form.value)
        bound = profile.next_evaluated_bound_s


def fit_pid_sp_corpus(
    request: FitRequest,
    segments: tuple[LearningTrajectorySegment, ...],
    configuration: object,
) -> PidSpFitResult:
    """Fit only the exact compatible finalized corpus prefix named by ``request``."""
    if not isinstance(request, FitRequest):
        raise TypeError("request must be a FitRequest")
    if not isinstance(segments, tuple) or not all(isinstance(item, LearningTrajectorySegment) for item in segments):
        raise TypeError("segments must be a tuple of LearningTrajectorySegment values")
    if request.fit_corpus.schema_version != 2:
        return _failed(request, "request-corpus-schema-unsupported")
    try:
        if canonical_trajectory_digest(configuration) != request.configuration_digest:
            return _failed(request, "request-configuration-mismatch")
    except TypeError, ValueError:
        return _failed(request, "request-configuration-mismatch")

    by_id: dict[str, LearningTrajectorySegment] = {}
    for segment in segments:
        if segment.segment_id in by_id:
            return _failed(request, "duplicate-segment-id")
        by_id[segment.segment_id] = segment
    selected: list[LearningTrajectorySegment] = []
    for corpus_slice in request.fit_corpus.slices:
        segment = by_id.get(corpus_slice.segment_id)
        if segment is None:
            return _failed(request, "request-segment-mismatch")
        if (
            segment.content_digest != corpus_slice.segment_content_digest
            or segment.fit_partition_digest != request.fit_corpus.fit_partition_digest
            or len(segment.pre_roll_frames) + len(segment.scored_hold_frames) - 1 != corpus_slice.through_ordinal
            or len(segment.pre_roll_frames) != corpus_slice.pre_roll_count
            or len(segment.scored_hold_frames) != corpus_slice.scored_count
            or trajectory_frame_prefix_digest((*segment.pre_roll_frames, *segment.scored_hold_frames))
            != corpus_slice.prefix_digest
        ):
            return _failed(request, "request-segment-mismatch")
        selected.append(segment)

    episodes = tuple(episode for segment in selected for episode in _materialize_episodes(segment))
    if len({episode.episode_id for episode in episodes}) < 2:
        return PidSpFitResult(
            request=request,
            status=PidSpFitStatus.INSUFFICIENT,
            reason="insufficient-excitation-episodes",
            episodes=episodes,
        )
    profiles = tuple(_profile_form(episodes, form) for form in ModelForm)
    comparison = select_pid_sp_model(
        episodes,
        {form: profile for form, profile in zip(ModelForm, profiles, strict=True)},
    )
    selected = comparison.selected is not None
    return PidSpFitResult(
        request=request,
        status=PidSpFitStatus.EVALUATED if selected else PidSpFitStatus.BLOCKED,
        reason=(
            "model-comparison-evaluated"
            if selected
            else (
                "insufficient-delay-identifiability"
                if any(not profile.authorized for profile in profiles)
                else "model-comparison-rejected"
            )
        ),
        episodes=episodes,
        delay_profiles=profiles,
        comparison=comparison,
    )
