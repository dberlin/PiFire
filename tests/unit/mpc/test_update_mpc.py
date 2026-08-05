"""MPC calibration samples come from typed SQLite control traces, never CSV logs."""

from dataclasses import replace

import numpy as np
import pytest

from common.control_trace import (
    ActuationMode,
    AppliedOutputPayload,
    ControlTraceRecord,
    ControllerType,
    FixedCycleFramePayload,
    InhibitReason,
    MpcFailureState,
    MpcUpdatePayload,
    RecorderGapPayload,
    SessionPayload,
    TraceEventKind,
    TraceSetting,
)
from common.datastore_accessors import append_control_trace
from controller.applied_output import OutputSource
from controller.update_mpc import TraceSelectionError, load_trace_samples


SESSION_ID = "mpc-session"
COOK_ID = "mak-cook"


def _session() -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=0,
        session_id=SESSION_ID,
        cook_id=COOK_ID,
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.SESSION,
        payload=SessionPayload(
            controller=ControllerType.MPC,
            controller_config=(TraceSetting(key="policy", value="nlp"),),
            temperature_unit="C",
            control_period_seconds=5.0,
            model_revision=1,
            model_provenance="configured",
            u_min=0.1,
            u_max=0.9,
            hold_cycle_seconds=None,
            pulse_slot_seconds=2.0,
            pulse_frame_seconds=20.0,
            fan_authority=True,
            fan_pwm_capable=True,
            fan_min_duty=40.0,
            fan_max_duty=100.0,
            setpoint=250.0,
            ambient_temperature=20.0,
            software_version="test",
            build_version="test",
        ),
    )


def _update(
    revision: int, timestamp_ms: int, temperature: float, load: float, *, inhibit: InhibitReason = InhibitReason.NONE
) -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=timestamp_ms,
        session_id=SESSION_ID,
        cook_id=COOK_ID,
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.CONTROL_UPDATE,
        payload=MpcUpdatePayload(
            monotonic_ms=timestamp_ms,
            wall_ms=timestamp_ms,
            result_revision=revision,
            result_age_ms=0,
            control_period_seconds=5.0,
            observed_dt_seconds=5.0,
            setpoint=250.0,
            measured_temperature=temperature,
            raw_output=load,
            requested_output=load,
            actuation_mode=ActuationMode.FIXED_CYCLE,
            prior_requested_auger_duty=0.2,
            prior_realized_auger_duty=0.2,
            requested_fan_duty=100.0,
            applied_fan_duty=100.0,
            output_source=OutputSource.CONTROLLER,
            inhibit_reason=inhibit,
            state_names=("temperature", "disturbance"),
            state_values=(temperature, 0.0),
            disturbance_estimate=0.0,
            model_revision=1,
            model_provenance="configured",
            raw_policy_firing_load=load,
            equilibrium_feed_forward=load,
            residual_move=0.0,
            bounded_firing_load=load,
            policy_kind="nlp",
            failure_state=MpcFailureState.SUCCESS,
            solve_start_ms=timestamp_ms,
            solve_end_ms=timestamp_ms,
            deadline_miss_count=0,
            stale=False,
            recovered=False,
            predicted_feasible=True,
            predicted_steady_load=load,
        ),
    )


def _applied(
    revision: int,
    recorded_ms: int,
    interval_start_ms: int,
    interval_end_ms: int,
    load: float | None,
    *,
    complete: bool = True,
) -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=recorded_ms,
        session_id=SESSION_ID,
        cook_id=COOK_ID,
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.APPLIED_OUTPUT,
        payload=AppliedOutputPayload(
            result_revision=revision,
            interval_start_ms=interval_start_ms,
            interval_end_ms=interval_end_ms,
            realized_auger_duty=0.2,
            realized_combustion_load=load,
            actual_fan_duty=100.0,
            sample_complete=complete,
            output_source=OutputSource.CONTROLLER,
        ),
    )


def _teardown_frame() -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=16_001,
        session_id=SESSION_ID,
        cook_id=COOK_ID,
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.ACTUATION_FRAME,
        payload=FixedCycleFramePayload(
            result_revision=12,
            raw_requested_duty=0.2,
            bounded_duty=0.2,
            u_min=0.1,
            u_max=0.9,
            cycle_start_ms=11_000,
            cycle_end_ms=16_000,
            scheduled_on_seconds=0.0,
            scheduled_off_seconds=5.0,
            actual_on_seconds=0.0,
            transition_count=0,
            fan_assist_active=False,
            inhibit_reason=InhibitReason.NONE,
            output_active=False,
        ),
    )


def _lifecycle_records(*, terminal_partial: bool = False) -> list[ControlTraceRecord]:
    records = [
        _session(),
        _update(2, 1_000, 100.0, 20.0),
        _applied(2, 1_001, 0, 1_000, 99.0),
        _update(7, 6_000, 110.0, 25.0),
        _applied(7, 6_001, 1_000, 6_000, 20.0),
        _update(12, 11_000, 120.0, 30.0),
        _applied(12, 11_001, 6_000, 11_000, 25.0),
    ]
    if terminal_partial:
        records.append(_applied(12, 11_002, 11_000, 16_000, None, complete=False))
        records.append(_teardown_frame())
    return records


def test_load_trace_samples_pairs_temperature_with_the_next_update_s_complete_load(ds):
    append_control_trace(_lifecycle_records())

    time_s, temperature_c, combustion_load = load_trace_samples(cook_id=COOK_ID, database_path=ds.DB_PATH)

    np.testing.assert_allclose(time_s, (0.0, 5.0))
    np.testing.assert_allclose(temperature_c, (100.0, 110.0))
    np.testing.assert_allclose(combustion_load, (20.0, 25.0))


def test_load_trace_samples_accepts_an_active_session_without_a_terminal_partial(ds):
    append_control_trace(_lifecycle_records())

    _, temperature_c, combustion_load = load_trace_samples(session_id=SESSION_ID)

    np.testing.assert_allclose(temperature_c, (100.0, 110.0))
    np.testing.assert_allclose(combustion_load, (20.0, 25.0))


def test_load_trace_samples_ignores_one_terminal_partial_output(ds):
    append_control_trace(_lifecycle_records(terminal_partial=True))

    _, temperature_c, combustion_load = load_trace_samples(session_id=SESSION_ID)

    np.testing.assert_allclose(temperature_c, (100.0, 110.0))
    np.testing.assert_allclose(combustion_load, (20.0, 25.0))


def test_load_trace_samples_rejects_terminal_partial_with_a_nonlatest_revision(ds):
    records = _lifecycle_records(terminal_partial=True)
    partial = records[7].payload
    records[7] = records[7].model_copy(update={"payload": replace(partial, result_revision=7)})
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="latest update"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_complete_output_before_its_matching_update(ds):
    records = _lifecycle_records()
    records[3:5] = [
        _applied(7, 5_000, 1_000, 6_000, 20.0),
        _update(7, 6_000, 110.0, 25.0),
    ]
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="must follow its accepted update"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_complete_output_after_the_next_update(ds):
    records = _lifecycle_records()
    records[4:7] = [
        _update(12, 11_000, 120.0, 30.0),
        _applied(7, 11_001, 1_000, 6_000, 20.0),
        _applied(12, 11_002, 6_000, 11_000, 25.0),
    ]
    append_control_trace(records)
    with pytest.raises(TraceSelectionError, match="precede the next accepted update"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_accepts_skipped_numeric_revisions(ds):
    append_control_trace(_lifecycle_records())

    _, temperature_c, combustion_load = load_trace_samples(session_id=SESSION_ID)

    np.testing.assert_allclose(temperature_c, (100.0, 110.0))
    np.testing.assert_allclose(combustion_load, (20.0, 25.0))


def test_load_trace_samples_requires_exactly_one_selector():
    with pytest.raises(TraceSelectionError, match="exactly one of cook_id or session_id"):
        load_trace_samples()
    with pytest.raises(TraceSelectionError, match="exactly one of cook_id or session_id"):
        load_trace_samples(cook_id=COOK_ID, session_id=SESSION_ID)


def test_load_trace_samples_rejects_a_cook_with_multiple_sessions(ds):
    append_control_trace([_session(), _session().model_copy(update={"session_id": "second-session"})])

    with pytest.raises(TraceSelectionError, match="more than one control session"):
        load_trace_samples(cook_id=COOK_ID)


def test_load_trace_samples_rejects_recorder_gaps(ds):
    records = _lifecycle_records()
    records.append(
        ControlTraceRecord(
            ts_ms=16_000,
            session_id=SESSION_ID,
            cook_id=COOK_ID,
            controller=ControllerType.MPC,
            event_kind=TraceEventKind.RECORDER_GAP,
            payload=RecorderGapPayload(lost_record_count=1, gap_start_ms=15_000, gap_end_ms=16_000),
        )
    )
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="recorder gap"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_inhibited_intervals(ds):
    append_control_trace(
        [_session(), _update(2, 1_000, 100.0, 20.0, inhibit=InhibitReason.LID_OPEN), _update(7, 6_000, 110.0, 25.0)]
    )

    with pytest.raises(TraceSelectionError, match="inhibited"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_an_interior_partial_output(ds):
    records = _lifecycle_records()
    records[4] = _applied(7, 6_001, 1_000, 6_000, None, complete=False)
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="latest update"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_missing_complete_realized_load(ds):
    records = _lifecycle_records()
    records[4] = _applied(7, 6_001, 1_000, 6_000, None)
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="realized combustion load"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_duplicate_complete_output(ds):
    records = _lifecycle_records()
    records.append(_applied(7, 11_002, 1_000, 6_000, 20.0))
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="multiple complete"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_output_after_a_terminal_partial(ds):
    records = _lifecycle_records(terminal_partial=True)
    records.append(_applied(99, 16_002, 16_000, 21_000, 30.0))
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="later update or output"):
        load_trace_samples(session_id=SESSION_ID)
