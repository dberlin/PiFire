"""Canonical conversion of typed MPC learning traces."""

from __future__ import annotations

from dataclasses import replace

import pytest

from common.control_trace import (
    ActuationMode,
    AppliedOutputPayload,
    ControlTraceRecord,
    ControllerType,
    FramedPulseFramePayload,
    InhibitReason,
    ModelObservationPayload,
    MpcFailureState,
    MpcUpdatePayload,
    ResultStaleState,
    SessionPayload,
    TraceEventKind,
    TraceSetting,
)
from controller.applied_output import OutputSource
from controller.linear_mpc.trace import TraceSelectionError, calibration_samples, learning_observations


_SESSION_ID = "learning-session"


def _session(unit: str = "C") -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=0,
        session_id=_SESSION_ID,
        cook_id="cook",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.SESSION,
        payload=SessionPayload(
            controller=ControllerType.MPC,
            controller_config=(TraceSetting(key="policy", value="linear-mpc"),),
            temperature_unit=unit,
            control_period_seconds=5.0,
            model_revision=1,
            model_provenance="configured",
            pulse_slot_seconds=2.0,
            pulse_frame_seconds=20.0,
            fan_authority=False,
            fan_pwm_capable=True,
            fan_min_duty=0.0,
            fan_max_duty=1.0,
            setpoint=248.0 if unit == "F" else 120.0,
            ambient_temperature=68.0 if unit == "F" else 20.0,
            software_version="test",
            build_version="test",
        ),
    )


def _update(revision: int = 1, *, temperature: float = 212.0, unit: str = "F") -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=20_000,
        session_id=_SESSION_ID,
        cook_id="cook",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.CONTROL_UPDATE,
        payload=MpcUpdatePayload(
            monotonic_ms=20_000,
            wall_ms=20_000,
            result_revision=revision,
            result_age_ms=0,
            control_period_seconds=5.0,
            observed_dt_seconds=5.0,
            setpoint=248.0 if unit == "F" else 120.0,
            measured_temperature=temperature,
            raw_output=0.4,
            requested_output=0.4,
            actuation_mode=ActuationMode.FRAMED_PULSE,
            prior_requested_auger_duty=0.2,
            prior_realized_auger_duty=0.2,
            requested_fan_duty=None,
            applied_fan_duty=None,
            output_source=OutputSource.CONTROLLER,
            inhibit_reason=InhibitReason.NONE,
            state_names=("temperature",),
            state_values=(temperature,),
            disturbance_estimate=0.0,
            model_revision=1,
            model_provenance="configured",
            raw_policy_firing_load=0.4,
            equilibrium_feed_forward=0.4,
            residual_move=0.0,
            bounded_firing_load=0.4,
            policy_kind="linear-mpc",
            failure_state=MpcFailureState.SUCCESS,
            solve_start_ms=20_000,
            solve_end_ms=20_000,
            deadline_miss_count=0,
            stale=False,
            recovered=False,
            predicted_feasible=True,
            predicted_steady_load=0.4,
            solve_duration_ms=0,
            consecutive_deadline_miss_count=0,
            stale_state=ResultStaleState.FRESH,
        ),
    )


def _frame(revision: int = 1) -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=20_000,
        session_id=_SESSION_ID,
        cook_id="cook",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.ACTUATION_FRAME,
        payload=FramedPulseFramePayload(
            result_revision=revision,
            pulse_slot_seconds=2.0,
            frame_seconds=20.0,
            frame_start_ms=0,
            frame_end_ms=20_000,
            requested_combustion_load=0.4,
            requested_auger_duty=0.4,
            credit_before_seconds=0.0,
            credit_after_seconds=0.0,
            scheduled_on_seconds=8.0,
            delivered_on_seconds=7.0,
            transition_count=2,
            actual_start_active=False,
            actual_end_active=False,
            requested_fan_duty=None,
            applied_fan_duty=None,
            skipped=False,
            stale_command=False,
            inhibit_reason=InhibitReason.NONE,
            reset_reason=None,
        ),
    )


def _observation(*, temp_c: float = 100.0, ambient_c: float = 20.0) -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=20_000,
        session_id=_SESSION_ID,
        cook_id="cook",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.MODEL_OBSERVATION,
        payload=ModelObservationPayload(
            frame_start_ms=0,
            frame_end_ms=20_000,
            temp_c=temp_c,
            setpoint_c=120.0,
            ambient_c=ambient_c,
            requested_combustion_load=0.4,
            realized_combustion_load=0.35,
            delivered_on_seconds=7.0,
            eligible=True,
            rejection_reasons=(),
            input_variance=0.01,
            input_levels=3,
            incumbent_innovation_c=1.0,
            challenger_innovation_c=0.5,
            effective_updates=21,
            role_generation=0,
            model_digest="a" * 64,
            result_revision=1,
            requested_auger_duty=0.4,
            output_source=OutputSource.CONTROLLER,
            lid_open=False,
            safety_inhibited=False,
            manual_override=False,
            stale=False,
            skipped=False,
            reset=False,
            continuous=True,
        ),
    )


def test_exact_observation_is_canonical_over_fallback_records() -> None:
    frames = learning_observations((_session("F"), _frame(), _update(), _observation()))

    assert len(frames) == 1
    assert frames[0].temp_c == pytest.approx(100.0)
    assert frames[0].ambient_c == pytest.approx(20.0)
    assert frames[0].realized_q == pytest.approx(0.35)


def test_fahrenheit_and_celsius_fallback_sessions_are_equivalent() -> None:
    fahrenheit = learning_observations((_session("F"), _frame(), _update(temperature=212.0)))
    celsius = learning_observations((_session("C"), _frame(), _update(temperature=100.0, unit="C")))

    assert fahrenheit == celsius

def test_fallback_normalizes_legacy_framed_delivery_to_canonical_q() -> None:
    legacy_frame = _frame().model_copy(
        update={
            "payload": replace(
                _frame().payload,
                requested_combustion_load=0.4,
                requested_auger_duty=0.36,
                scheduled_on_seconds=7.2,
                delivered_on_seconds=3.6,
            )
        }
    )
    canonical_observation = _observation().model_copy(
        update={
            "payload": replace(
                _observation().payload,
                requested_combustion_load=0.4,
                realized_combustion_load=0.2,
                delivered_on_seconds=3.6,
                requested_auger_duty=0.36,
            )
        }
    )

    replayed = learning_observations((_session("F"), legacy_frame, _update()))
    canonical = learning_observations((_session("F"), canonical_observation))

    assert replayed == canonical
    assert replayed[0].realized_q == pytest.approx(0.2)


@pytest.mark.parametrize(
    ("requested_q", "requested_duty", "delivered_on_s"),
    [
        (0.0, 0.0, 3.6),
        (0.4, 0.5, 3.6),
    ],
    ids=("unidentifiable-zero-request-with-delivery", "inconsistent-u-max"),
)
def test_fallback_rejects_legacy_frames_without_a_valid_input_scale(
    requested_q: float, requested_duty: float, delivered_on_s: float
) -> None:
    frame = _frame().model_copy(
        update={
            "payload": replace(
                _frame().payload,
                requested_combustion_load=requested_q,
                requested_auger_duty=requested_duty,
                scheduled_on_seconds=delivered_on_s,
                delivered_on_seconds=delivered_on_s,
            )
        }
    )

    with pytest.raises(TraceSelectionError):
        learning_observations((_session(), frame, _update()))


def test_fallback_preserves_zero_requested_and_delivered_input() -> None:
    legacy_frame = _frame().model_copy(
        update={
            "payload": replace(
                _frame().payload,
                requested_combustion_load=0.0,
                requested_auger_duty=0.0,
                scheduled_on_seconds=0.0,
                delivered_on_seconds=0.0,
            )
        }
    )
    canonical_observation = _observation().model_copy(
        update={
            "payload": replace(
                _observation().payload,
                requested_combustion_load=0.0,
                realized_combustion_load=0.0,
                delivered_on_seconds=0.0,
                requested_auger_duty=0.0,
            )
        }
    )

    replayed = learning_observations((_session("F"), legacy_frame, _update()))
    canonical = learning_observations((_session("F"), canonical_observation))

    assert replayed == canonical


@pytest.mark.parametrize(
    "records",
    [
        lambda: (_session(), _frame(), _update(), _frame()),
        lambda: (
            _session(),
            _frame(),
            _update().model_copy(
                update={"payload": replace(_update().payload, output_source=OutputSource.MANUAL_OVERRIDE)}
            ),
        ),
        lambda: (
            _session(),
            _frame().model_copy(update={"payload": replace(_frame().payload, frame_end_ms=10_000)}),
            _update(),
        ),
        lambda: (
            _session(),
            _frame(),
            _update(),
            ControlTraceRecord(
                ts_ms=20_001,
                session_id=_SESSION_ID,
                cook_id="cook",
                controller=ControllerType.MPC,
                event_kind=TraceEventKind.APPLIED_OUTPUT,
                payload=AppliedOutputPayload(
                    result_revision=1,
                    interval_start_ms=0,
                    interval_end_ms=20_000,
                    realized_auger_duty=0.4,
                    realized_combustion_load=None,
                    actual_fan_duty=None,
                    sample_complete=False,
                    output_source=OutputSource.CONTROLLER,
                ),
            ),
        ),
        lambda: (_session(), _frame(), _update(), _update()),
        lambda: (
            _session(),
            _frame(),
            _update(),
            _frame().model_copy(
                update={
                    "ts_ms": 60_000,
                    "payload": replace(_frame().payload, result_revision=2, frame_start_ms=40_000, frame_end_ms=60_000),
                }
            ),
            _update(2).model_copy(
                update={"ts_ms": 60_000, "payload": replace(_update(2).payload, wall_ms=60_000, monotonic_ms=60_000)}
            ),
        ),
    ],
)
def test_fallback_rejects_ambiguous_or_incomplete_evidence(records) -> None:
    values = records()
    normalized = tuple(
        value
        if isinstance(value, ControlTraceRecord)
        else ControlTraceRecord(
            ts_ms=20_000,
            session_id=_SESSION_ID,
            cook_id="cook",
            controller=ControllerType.MPC,
            event_kind=TraceEventKind.ACTUATION_FRAME,
            payload=value,
        )
        for value in values
    )
    with pytest.raises(TraceSelectionError):
        learning_observations(normalized)


def test_exact_observation_rejects_omitted_gate_or_actuation_evidence() -> None:
    record = _observation().model_copy(update={"payload": replace(_observation().payload, output_source=None)})
    with pytest.raises(TraceSelectionError, match="omits required"):
        learning_observations((_session(), record))


@pytest.mark.parametrize("start_ms", (10_000, 40_000))
def test_exact_observation_sequences_reject_overlap_and_gaps(start_ms: int) -> None:
    first = _observation()
    second = _observation().model_copy(
        update={
            "ts_ms": start_ms + 20_000,
            "payload": replace(
                _observation().payload,
                frame_start_ms=start_ms,
                frame_end_ms=start_ms + 20_000,
                result_revision=2,
            ),
        }
    )
    with pytest.raises(TraceSelectionError, match="not contiguous"):
        learning_observations((_session(), first, second))


@pytest.mark.parametrize(
    "replacement",
    [
        {"eligible": False, "rejection_reasons": ("stale",)},
        {"output_source": OutputSource.MANUAL_OVERRIDE},
        {"lid_open": True},
        {"safety_inhibited": True},
        {"manual_override": True},
        {"stale": True},
        {"skipped": True},
        {"reset": True},
        {"continuous": False},
    ],
)
def test_calibration_rejects_exact_observation_gate_evidence(replacement) -> None:
    record = _observation().model_copy(update={"payload": replace(_observation().payload, **replacement)})
    with pytest.raises(TraceSelectionError, match="not eligible"):
        calibration_samples((_session(), record))


def test_learning_replay_preserves_rejected_exact_observations() -> None:
    record = _observation().model_copy(
        update={"payload": replace(_observation().payload, eligible=False, rejection_reasons=("stale",), stale=True)}
    )
    frames = learning_observations((_session(), record))
    assert len(frames) == 1
    assert frames[0].stale is True
