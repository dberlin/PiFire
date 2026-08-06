"""MPC calibration samples come from typed SQLite control traces, never CSV logs."""

from dataclasses import replace

import numpy as np
import pytest

from common.control_trace import (
    ActuationMode,
    AllocationPayload,
    AppliedOutputPayload,
    ControlTraceRecord,
    ControllerType,
    FramedPulseFramePayload,
    InhibitReason,
    MpcFailureState,
    MpcUpdatePayload,
    ResultStaleState,
    RecorderGapPayload,
    SafetyEventPayload,
    SafetyEventType,
    SessionPayload,
    TraceEventKind,
    TraceSetting,
)
from common.datastore_accessors import append_control_trace
from controller.applied_output import OutputSource
from controller.update_mpc import TraceSelectionError, load_trace_samples
from controller.mpc_allocator import ALLOCATOR_REVISION, allocate


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
            pulse_slot_seconds=5.0,
            pulse_frame_seconds=5.0,
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
    revision: int,
    timestamp_ms: int,
    temperature: float,
    load: float,
    *,
    inhibit: InhibitReason = InhibitReason.NONE,
    stale: bool = False,
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
            actuation_mode=ActuationMode.FRAMED_PULSE,
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
            stale=stale,
            recovered=False,
            predicted_feasible=True,
            predicted_steady_load=load,
            solve_duration_ms=0,
            consecutive_deadline_miss_count=0,
            stale_state=ResultStaleState.STALE if stale else ResultStaleState.FRESH,
        ),
    )


def _allocation(revision: int, load: float) -> AllocationPayload:
    result = allocate(load, u_max=0.9, fan_min_pct=100.0, fan_max_pct=100.0, enable_fan=True)
    return AllocationPayload(
        result_revision=revision,
        normalized_combustion_load=result.normalized_combustion_load,
        requested_auger_duty=result.auger_duty,
        requested_fan_duty=result.fan_duty,
        u_max=result.u_max,
        fan_min_pct=result.fan_min_pct,
        fan_max_pct=result.fan_max_pct,
        fan_enabled=result.fan_enabled,
        mpc_has_fan_authority=True,
        auger_clamp_reason=result.auger_clamp_reason,
        fan_clamp_reason=result.fan_clamp_reason,
        allocator_revision=ALLOCATOR_REVISION,
    )


def _frame(revision: int, start_ms: int, end_ms: int, allocation: AllocationPayload) -> ControlTraceRecord:
    duration_seconds = (end_ms - start_ms) / 1000.0
    return ControlTraceRecord(
        ts_ms=end_ms,
        session_id=SESSION_ID,
        cook_id=COOK_ID,
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.ACTUATION_FRAME,
        payload=FramedPulseFramePayload(
            result_revision=revision,
            pulse_slot_seconds=5.0,
            frame_seconds=5.0,
            frame_start_ms=start_ms,
            frame_end_ms=end_ms,
            requested_combustion_load=allocation.normalized_combustion_load,
            requested_auger_duty=allocation.requested_auger_duty,
            credit_before_seconds=0.0,
            credit_after_seconds=0.0,
            scheduled_on_seconds=5.0,
            delivered_on_seconds=allocation.requested_auger_duty * duration_seconds,
            transition_count=2,
            actual_start_active=False,
            actual_end_active=False,
            requested_fan_duty=allocation.requested_fan_duty,
            applied_fan_duty=allocation.requested_fan_duty,
            skipped=False,
            stale_command=False,
            inhibit_reason=InhibitReason.NONE,
            reset_reason=None,
        ),
    )


def _applied(
    revision: int,
    recorded_ms: int,
    interval_start_ms: int,
    interval_end_ms: int,
    load: float | None,
    *,
    auger_duty: float = 0.2,
    complete: bool = True,
    source: OutputSource = OutputSource.CONTROLLER,
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
            realized_auger_duty=auger_duty,
            realized_combustion_load=load,
            actual_fan_duty=100.0,
            sample_complete=complete,
            output_source=source,
        ),
    )


def _revision_records(
    revision: int, start_ms: int, end_ms: int, temperature: float, load: float
) -> list[ControlTraceRecord]:
    allocation = _allocation(revision, load)
    return [
        _update(revision, start_ms, temperature, load),
        ControlTraceRecord(
            ts_ms=start_ms,
            session_id=SESSION_ID,
            cook_id=COOK_ID,
            controller=ControllerType.MPC,
            event_kind=TraceEventKind.ALLOCATION,
            payload=allocation,
        ),
        _frame(revision, start_ms, end_ms, allocation),
        _applied(
            revision,
            end_ms,
            start_ms,
            end_ms,
            load,
            auger_duty=allocation.requested_auger_duty,
        ),
    ]


def _allocation_record(timestamp_ms: int, allocation: AllocationPayload) -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=timestamp_ms,
        session_id=SESSION_ID,
        cook_id=COOK_ID,
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.ALLOCATION,
        payload=allocation,
    )


def _lifecycle_records(*, terminal_partial: bool = False) -> list[ControlTraceRecord]:
    allocation = _allocation(12, 0.3)
    records = [
        _session(),
        _applied(0, 1_000, 0, 1_000, None, source=OutputSource.SEED),
        *_revision_records(2, 1_000, 6_000, 100.0, 0.2),
        *_revision_records(7, 6_000, 11_000, 110.0, 0.25),
        _update(12, 11_000, 120.0, 0.3),
        _allocation_record(11_000, allocation),
    ]
    if terminal_partial:
        records.extend(
            [
                _frame(12, 11_000, 16_000, allocation),
                _applied(
                    12,
                    16_000,
                    11_000,
                    16_000,
                    None,
                    auger_duty=allocation.requested_auger_duty,
                    complete=False,
                ),
            ]
        )
    return records


def _terminal_safety_reset_tail() -> list[ControlTraceRecord]:
    allocation = _allocation(12, 0.3)
    frame = _frame(12, 11_000, 12_000, allocation)
    reset_frame = frame.model_copy(
        update={"payload": replace(frame.payload, inhibit_reason=InhibitReason.SAFETY, reset_reason="mode_change")}
    )
    return [
        _applied(12, 12_000, 11_000, 12_000, 0.3, auger_duty=allocation.requested_auger_duty),
        ControlTraceRecord(
            ts_ms=12_000,
            session_id=SESSION_ID,
            cook_id=COOK_ID,
            controller=ControllerType.MPC,
            event_kind=TraceEventKind.SAFETY_EVENT,
            payload=SafetyEventPayload(
                event=SafetyEventType.SCHEDULER_RESET,
                inhibit_reason=InhibitReason.SAFETY,
                result_revision=12,
                detail="framed pulse scheduler reset: mode_change",
            ),
        ),
        reset_frame,
    ]


def _output_index(records: list[ControlTraceRecord], revision: int) -> int:
    return next(
        index
        for index, record in enumerate(records)
        if isinstance(record.payload, AppliedOutputPayload) and record.payload.result_revision == revision
    )


def test_load_trace_samples_pairs_temperature_with_its_own_complete_framed_load(ds):
    append_control_trace(_lifecycle_records())

    time_s, temperature_c, combustion_load = load_trace_samples(cook_id=COOK_ID, database_path=ds.DB_PATH)

    np.testing.assert_allclose(time_s, (0.0, 5.0))
    np.testing.assert_allclose(temperature_c, (100.0, 110.0))
    np.testing.assert_allclose(combustion_load, (0.2, 0.25))


def test_load_trace_samples_duration_weights_complete_intervals_for_one_framed_revision(ds):
    records = _lifecycle_records()
    index = _output_index(records, 2)
    allocation = _allocation(2, 0.2)
    records[index : index + 1] = [
        _applied(2, 6_000, 1_000, 3_000, 0.2, auger_duty=allocation.requested_auger_duty),
        _applied(2, 6_000, 3_000, 6_000, 0.2, auger_duty=allocation.requested_auger_duty),
    ]
    append_control_trace(records)

    _, _, combustion_load = load_trace_samples(session_id=SESSION_ID)

    np.testing.assert_allclose(combustion_load, (0.2, 0.25))


def test_load_trace_samples_skips_results_superseded_before_a_framed_interval(ds):
    records = _lifecycle_records()
    allocation = _allocation(4, 0.23)
    records.insert(4, _update(4, 4_000, 105.0, 0.23))
    records.insert(5, _allocation_record(4_000, allocation))
    append_control_trace(records)

    _, temperature_c, combustion_load = load_trace_samples(session_id=SESSION_ID)

    np.testing.assert_allclose(temperature_c, (100.0, 110.0))
    np.testing.assert_allclose(combustion_load, (0.2, 0.25))


def test_load_trace_samples_rejects_a_complete_controller_interval_without_a_frame(ds):
    records = _lifecycle_records()
    records.insert(4, _update(4, 4_000, 105.0, 0.23))
    records.insert(5, _applied(4, 5_000, 4_000, 5_000, 0.23))
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="complete framed interval"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_an_overlapping_actuated_frame_before_its_missing_output(ds):
    records = _lifecycle_records()
    allocation = _allocation(4, 0.23)
    records[4:4] = [
        _update(4, 4_000, 105.0, 0.23),
        ControlTraceRecord(
            ts_ms=4_000,
            session_id=SESSION_ID,
            cook_id=COOK_ID,
            controller=ControllerType.MPC,
            event_kind=TraceEventKind.ALLOCATION,
            payload=allocation,
        ),
        _frame(4, 4_000, 5_000, allocation),
    ]
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="overlapping framed intervals"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_latest_actuated_revision_without_any_output(ds):
    records = _lifecycle_records()
    allocation = next(
        record.payload
        for record in records
        if isinstance(record.payload, AllocationPayload) and record.payload.result_revision == 12
    )
    records.append(_frame(12, 11_000, 16_000, allocation))
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="actuated.*complete framed interval"):
        load_trace_samples(session_id=SESSION_ID)


@pytest.mark.parametrize("with_initial_seed", (True, False))
def test_load_trace_samples_accepts_pristine_framed_traces_with_or_without_initial_seed(ds, with_initial_seed):
    records = _lifecycle_records()
    if not with_initial_seed:
        records.pop(1)
    append_control_trace(records)

    _, temperature_c, combustion_load = load_trace_samples(session_id=SESSION_ID)

    np.testing.assert_allclose(temperature_c, (100.0, 110.0))
    np.testing.assert_allclose(combustion_load, (0.2, 0.25))


def test_load_trace_samples_rejects_a_late_revision_zero_seed(ds):
    records = _lifecycle_records()
    records.append(records[1].model_copy(update={"ts_ms": 12_000}))
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="initial seed"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_seed_after_a_framed_update(ds):
    records = _lifecycle_records()
    seed = records.pop(1)
    records.insert(4, seed.model_copy(update={"ts_ms": 6_000}))
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="initial seed"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_accepts_an_active_framed_session_without_a_terminal_partial(ds):
    append_control_trace(_lifecycle_records())

    _, temperature_c, combustion_load = load_trace_samples(session_id=SESSION_ID)

    np.testing.assert_allclose(temperature_c, (100.0, 110.0))
    np.testing.assert_allclose(combustion_load, (0.2, 0.25))


def test_load_trace_samples_ignores_one_terminal_safety_reset_frame_and_its_output(ds):
    records = _lifecycle_records()
    records.extend(_terminal_safety_reset_tail())
    append_control_trace(records)

    _, temperature_c, combustion_load = load_trace_samples(session_id=SESSION_ID)

    np.testing.assert_allclose(temperature_c, (100.0, 110.0))
    np.testing.assert_allclose(combustion_load, (0.2, 0.25))


def test_load_trace_samples_does_not_exclude_a_skipped_terminal_safety_reset_frame(ds):
    records = _lifecycle_records()
    records.extend(_terminal_safety_reset_tail())
    reset_frame = records[-1]
    records[-1] = reset_frame.model_copy(update={"payload": replace(reset_frame.payload, skipped=True)})
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="complete framed interval"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_overlapping_frames_before_reusing_one_applied_interval(ds):
    records = _lifecycle_records()
    duplicate_frame = next(
        record
        for record in records
        if isinstance(record.payload, FramedPulseFramePayload) and record.payload.result_revision == 7
    )
    records.append(duplicate_frame)
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="overlapping framed intervals"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_accepts_adjacent_same_revision_framed_intervals(ds):
    records = _lifecycle_records()[:-2]
    allocation = _allocation(7, 0.25)
    records.extend(
        [
            _frame(7, 11_000, 16_000, allocation),
            _applied(7, 16_000, 11_000, 16_000, 0.25, auger_duty=allocation.requested_auger_duty),
        ]
    )
    append_control_trace(records)

    _, temperature_c, combustion_load = load_trace_samples(session_id=SESSION_ID)

    np.testing.assert_allclose(temperature_c, (100.0, 110.0))
    np.testing.assert_allclose(combustion_load, (0.2, 0.25))


def test_load_trace_samples_rejects_terminal_safety_tail_without_realized_load(ds):
    records = _lifecycle_records()
    records.extend(_terminal_safety_reset_tail())
    output = records[-3]
    records[-3] = output.model_copy(update={"payload": replace(output.payload, realized_combustion_load=None)})
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="no realized combustion load"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_terminal_safety_tail_with_mismatched_realized_load(ds):
    records = _lifecycle_records()
    records.extend(_terminal_safety_reset_tail())
    output = records[-3]
    records[-3] = output.model_copy(update={"payload": replace(output.payload, realized_combustion_load=0.2)})
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="does not match applied auger duty"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_terminal_safety_frame_without_reset_provenance(ds):
    records = _lifecycle_records()
    records.extend(_terminal_safety_reset_tail())
    del records[-2]
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="complete framed interval"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_mid_session_safety_reset_frame_and_its_output(ds):
    records = _lifecycle_records()
    records.extend(_terminal_safety_reset_tail())
    records.append(_update(13, 12_001, 125.0, 0.3))
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="complete framed interval"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_ignores_one_terminal_partial_output(ds):
    append_control_trace(_lifecycle_records(terminal_partial=True))

    _, temperature_c, combustion_load = load_trace_samples(session_id=SESSION_ID)

    np.testing.assert_allclose(temperature_c, (100.0, 110.0))
    np.testing.assert_allclose(combustion_load, (0.2, 0.25))


def test_load_trace_samples_rejects_terminal_partial_with_a_nonlatest_revision(ds):
    records = _lifecycle_records(terminal_partial=True)
    index = _output_index(records, 12)
    partial = records[index].payload
    records[index] = records[index].model_copy(update={"payload": replace(partial, result_revision=7)})
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="latest update"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_complete_output_before_its_matching_update(ds):
    records = _lifecycle_records()
    update_index = next(
        index
        for index, record in enumerate(records)
        if isinstance(record.payload, MpcUpdatePayload) and record.payload.result_revision == 7
    )
    records.insert(
        update_index, _applied(7, 6_000, 6_000, 11_000, 0.25, auger_duty=_allocation(7, 0.25).requested_auger_duty)
    )
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="must follow its accepted update"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_accepts_output_recorded_after_a_newer_result(ds):
    records = _lifecycle_records()
    index = _output_index(records, 7)
    output = records.pop(index)
    latest_update = next(
        index
        for index, record in enumerate(records)
        if isinstance(record.payload, MpcUpdatePayload) and record.payload.result_revision == 12
    )
    records.insert(latest_update + 1, output.model_copy(update={"ts_ms": 11_000}))
    append_control_trace(records)

    _, temperature_c, combustion_load = load_trace_samples(session_id=SESSION_ID)

    np.testing.assert_allclose(temperature_c, (100.0, 110.0))
    np.testing.assert_allclose(combustion_load, (0.2, 0.25))


def test_load_trace_samples_accepts_skipped_numeric_revisions(ds):
    append_control_trace(_lifecycle_records())

    _, temperature_c, combustion_load = load_trace_samples(session_id=SESSION_ID)

    np.testing.assert_allclose(temperature_c, (100.0, 110.0))
    np.testing.assert_allclose(combustion_load, (0.2, 0.25))


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


def test_load_trace_samples_rejects_inhibited_updates(ds):
    records = _lifecycle_records()
    records[2] = _update(2, 1_000, 100.0, 0.2, inhibit=InhibitReason.LID_OPEN)
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="inhibited"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_stale_updates(ds):
    records = _lifecycle_records()
    records[2] = _update(2, 1_000, 100.0, 0.2, stale=True)
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="stale"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_an_interior_partial_output(ds):
    records = _lifecycle_records()
    index = _output_index(records, 7)
    allocation = _allocation(7, 0.25)
    records[index] = _applied(
        7, 11_000, 6_000, 11_000, None, auger_duty=allocation.requested_auger_duty, complete=False
    )
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="latest update"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_missing_complete_realized_load(ds):
    records = _lifecycle_records()
    index = _output_index(records, 7)
    allocation = _allocation(7, 0.25)
    records[index] = _applied(7, 11_000, 6_000, 11_000, None, auger_duty=allocation.requested_auger_duty)
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="realized combustion load"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_overlapping_complete_output(ds):
    records = _lifecycle_records()
    records.append(_applied(7, 11_002, 6_000, 11_000, 0.25, auger_duty=_allocation(7, 0.25).requested_auger_duty))
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="contiguous intervals"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_non_controller_output_source(ds):
    records = _lifecycle_records()
    index = _output_index(records, 2)
    records[index] = records[index].model_copy(
        update={"payload": replace(records[index].payload, output_source=OutputSource.LID_OPEN)}
    )
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="inhibited by lid_open"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_complete_interval_with_a_mismatched_revision(ds):
    records = _lifecycle_records()
    index = _output_index(records, 2)
    records[index] = records[index].model_copy(update={"payload": replace(records[index].payload, result_revision=4)})
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="does not match an accepted MPC update"):
        load_trace_samples(session_id=SESSION_ID)


def test_load_trace_samples_rejects_output_after_a_terminal_partial(ds):
    records = _lifecycle_records(terminal_partial=True)
    records.append(_applied(99, 16_002, 16_000, 21_000, 0.3))
    append_control_trace(records)

    with pytest.raises(TraceSelectionError, match="later update or output"):
        load_trace_samples(session_id=SESSION_ID)
