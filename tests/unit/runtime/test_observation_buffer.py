from common.model_evidence import EvidenceKind, SessionSummaryEvidence
from controller.model_learning.contracts import FrameObservation
from controller.runtime.observation_buffer import ObservationOutcomeBuffer
from controller.runtime.runner import ObservationOutcomeEnvelope, ObservationTerminalDrop


def _frame(index: int) -> FrameObservation:
    return FrameObservation(
        frame_start_s=index * 20.0,
        frame_end_s=(index + 1) * 20.0,
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
    )


def _evidence_outcome(*, eligible: bool = True, rejection_reasons: tuple[str, ...] = ()) -> dict[str, object]:
    return {
        "eligible": eligible,
        "rejection_reasons": rejection_reasons,
        "forecast_origin_evidence": (),
    }


def _envelope(
    sequence: int,
    generation: int,
    *,
    outcome: object | None = None,
) -> ObservationOutcomeEnvelope:
    return ObservationOutcomeEnvelope(
        submission_sequence=sequence,
        configuration_generation=generation,
        observation=_frame(sequence),
        outcome=_evidence_outcome() if outcome is None else outcome,
    )


def _drop(
    sequence: int,
    generation: int,
    reason: str = "runner-no-observation-outcome",
) -> ObservationTerminalDrop:
    return ObservationTerminalDrop(
        submission_sequence=sequence,
        configuration_generation=generation,
        observation=_frame(sequence),
        reason=reason,
    )


def test_buffer_preserves_runner_supplied_sequence_generation_and_order() -> None:
    buffer = ObservationOutcomeBuffer(capacity=4)
    buffer.append_outcome(_envelope(41, 7))
    buffer.append_outcome(_envelope(42, 8))
    buffer.bind_context(7, "session-seven", "cook-seven")
    buffer.bind_context(8, "session-eight", "cook-eight")

    drained = buffer.drain()

    assert [(envelope.submission_sequence, envelope.configuration_generation) for envelope in drained.envelopes] == [
        (41, 7),
        (42, 8),
    ]


def test_bounded_eviction_becomes_a_counted_terminal_drop() -> None:
    buffer = ObservationOutcomeBuffer(capacity=2)
    buffer.bind_context(3, "session", "cook")
    buffer.append_outcome(_envelope(10, 3))
    buffer.append_outcome(_envelope(11, 3))

    buffer.append_outcome(_envelope(12, 3))
    drained = buffer.drain()

    assert [envelope.submission_sequence for envelope in drained.envelopes] == [
        11,
        12,
    ]
    assert drained.terminal_drops == (_drop(10, 3, "runner-outcome-evicted"),)
    assert drained.dropped_count == 1
    assert drained.dropped_sequences == (10,)


def test_unbound_envelopes_and_drops_are_withheld_in_their_original_order() -> None:
    buffer = ObservationOutcomeBuffer(capacity=6)
    buffer.append_outcome(_envelope(1, 10))
    buffer.append_outcome(_envelope(2, 20))
    buffer.append_outcome(_envelope(3, 10))
    buffer.append_terminal_drop(_drop(4, 10))
    buffer.append_terminal_drop(_drop(5, 20))
    buffer.append_terminal_drop(_drop(6, 10))
    buffer.bind_context(10, "session-ten", "cook-ten")

    first = buffer.drain()

    assert [envelope.submission_sequence for envelope in first.envelopes] == [
        1,
        3,
    ]
    assert [drop.submission_sequence for drop in first.terminal_drops] == [
        4,
        6,
    ]
    assert first.dropped_count == 0
    assert first.dropped_sequences == ()

    buffer.bind_context(20, "session-twenty", "cook-twenty")
    second = buffer.drain()

    assert [envelope.submission_sequence for envelope in second.envelopes] == [2]
    assert [drop.submission_sequence for drop in second.terminal_drops] == [5]


def test_evidence_is_frozen_only_when_a_later_binding_releases_the_envelope() -> None:
    buffer = ObservationOutcomeBuffer(capacity=2)
    buffer.append_outcome(
        _envelope(
            8,
            4,
            outcome=_evidence_outcome(eligible=False, rejection_reasons=("lid-open",)),
        )
    )

    withheld = buffer.drain()

    assert withheld.envelopes == ()
    assert withheld.terminal_drops == ()

    buffer.bind_context(4, "session-four", "cook-four")
    released = buffer.drain()

    assert len(released.envelopes) == 1
    evidence = released.envelopes[0].evidence
    assert [record.kind for record in evidence] == [EvidenceKind.SESSION_SUMMARY]
    assert evidence[0].session_id == "session-four"
    assert evidence[0].cook_id == "cook-four"
    summary = evidence[0].payload
    assert isinstance(summary, SessionSummaryEvidence)
    assert summary.rejection_reasons == ("lid-open",)
    assert buffer.drain().envelopes == ()


def test_retired_context_fences_items_until_that_generation_is_rebound() -> None:
    buffer = ObservationOutcomeBuffer(capacity=2)
    buffer.bind_context(9, "retired-session", "retired-cook")
    buffer.append_outcome(_envelope(21, 9))
    buffer.append_terminal_drop(_drop(22, 9))

    buffer.retire_context(9)
    fenced = buffer.drain()

    assert fenced.envelopes == ()
    assert fenced.terminal_drops == ()

    buffer.bind_context(9, "replacement-session", "replacement-cook")
    released = buffer.drain()

    assert [envelope.submission_sequence for envelope in released.envelopes] == [21]
    assert [drop.submission_sequence for drop in released.terminal_drops] == [22]
    assert released.envelopes[0].evidence[0].session_id == "replacement-session"
    assert released.envelopes[0].evidence[0].cook_id == "replacement-cook"


def test_drain_resets_only_eviction_counters_for_delivered_drops() -> None:
    buffer = ObservationOutcomeBuffer(capacity=1)
    buffer.append_outcome(_envelope(30, 1))
    buffer.append_outcome(_envelope(31, 2))
    buffer.bind_context(2, "session-two", "cook-two")

    bound_only = buffer.drain()

    assert [envelope.submission_sequence for envelope in bound_only.envelopes] == [31]
    assert bound_only.terminal_drops == ()
    assert bound_only.dropped_count == 0
    assert bound_only.dropped_sequences == ()

    buffer.bind_context(1, "session-one", "cook-one")
    eviction = buffer.drain()

    assert eviction.envelopes == ()
    assert [drop.submission_sequence for drop in eviction.terminal_drops] == [30]
    assert eviction.dropped_count == 1
    assert eviction.dropped_sequences == (30,)

    empty = buffer.drain()
    assert empty.dropped_count == 0
    assert empty.dropped_sequences == ()


def test_append_copies_mutable_outcome_before_evidence_is_frozen() -> None:
    outcome = _evidence_outcome(
        eligible=False,
        rejection_reasons=("original-rejection",),
    )
    buffer = ObservationOutcomeBuffer(capacity=1)
    buffer.append_outcome(_envelope(50, 5, outcome=outcome))

    outcome["eligible"] = True
    outcome["rejection_reasons"] = ()
    buffer.bind_context(5, "session-five", "cook-five")
    evidence = buffer.drain().envelopes[0].evidence

    assert len(evidence) == 1
    summary = evidence[0].payload
    assert isinstance(summary, SessionSummaryEvidence)
    assert summary.accepted_observations == 0
    assert summary.rejected_observations == 1
    assert summary.rejection_reasons == ("original-rejection",)
