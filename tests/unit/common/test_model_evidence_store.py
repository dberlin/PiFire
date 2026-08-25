"""Behavioral contracts for the durable compact model-evidence ledger."""

import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from common.control_trace import AllocationClampReason, AmbientSource
from common.model_evidence import (
    MODEL_EVIDENCE_SCHEMA_VERSION,
    ActivationEvidence,
    AllocationEvidence,
    CalibrationSummaryEvidence,
    CandidateAssessmentEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    FallbackEvidence,
    FitLifecycleEvidence,
    ForecastOriginEvidence,
    ModelEvidenceRecord,
    RollbackEvidence,
    TimingDistributionEvidence,
)
from common.persistence.model_evidence import (
    ModelActivationPair,
    append_model_evidence,
    commit_model_activation,
    commit_model_activation_phase,
    commit_model_rollback,
    invalidate_model_evidence_schema,
    read_model_activation,
    read_model_evidence,
    reset_model_evidence,
)

_DIGEST = "a" * 64
_OTHER_DIGEST = "b" * 64


def _forecast(evidence_id: str, timestamp_ms: int, *, sequence: int = 1) -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind.FORECAST_ORIGIN,
        session_id="session-a",
        cook_id="cook-a",
        timestamp_ms=timestamp_ms,
        role_generation=2,
        model_digest=_OTHER_DIGEST,
        provenance_digest=_DIGEST,
        payload=ForecastOriginEvidence(
            origin_sequence=sequence,
            origin_time_ms=timestamp_ms - 1,
            completion_time_ms=timestamp_ms,
            horizon_steps=3,
            incumbent_digest=_DIGEST,
            challenger_digest=_OTHER_DIGEST,
            incumbent_prediction_c=100.0,
            challenger_prediction_c=101.0,
            observed_temperature_c=102.0,
            incumbent_error_c=2.0,
            challenger_error_c=1.0,
            temperature_band="near-target",
            phase="coasting",
            ambient_source=AmbientSource.CONFIGURED,
            calibration_fit=False,
        ),
    )


def _confidence(
    evidence_id: str = "confidence-a",
    *,
    decision_id: str = "decision-a",
    timestamp_ms: int = 400,
) -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind.CONFIDENCE_DECISION,
        session_id="session-a",
        cook_id="cook-a",
        timestamp_ms=timestamp_ms,
        role_generation=2,
        model_digest=_DIGEST,
        provenance_digest=_OTHER_DIGEST,
        payload=ConfidenceDecisionEvidence(decision_id=decision_id, blocked=False),
    )


def _activation(evidence_id: str = "activation-a") -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind.ACTIVATION,
        session_id="session-a",
        cook_id="cook-a",
        timestamp_ms=500,
        role_generation=3,
        model_digest=_DIGEST,
        provenance_digest=_OTHER_DIGEST,
        payload=ActivationEvidence(
            decision_id="decision-a",
            active_snapshot_json='{"revision": 3, "model": "new"}',
            rollback_snapshot_json='{"revision": 2, "model": "old"}',
            controller_configuration_digest=_OTHER_DIGEST,
        ),
    )


def _pair(
    model_digest: str,
    *,
    candidate_generation: int,
    role_generation: int,
    configuration_revision: int,
) -> ModelActivationPair:
    return ModelActivationPair(
        model_digest=model_digest,
        configuration_json=f'{{"revision":{configuration_revision}}}',
        estimator_kind="grey-estimator",
        solver_kind="native-solver",
        candidate_generation=candidate_generation,
        role_generation=role_generation,
        ownership_digest=_OTHER_DIGEST,
    )


def _phase_record(phase: str = "prepared", *, transaction_id: str = "transaction-a"):
    incumbent = _pair(
        _OTHER_DIGEST,
        candidate_generation=2,
        role_generation=2,
        configuration_revision=2,
    )
    candidate = _pair(
        _DIGEST,
        candidate_generation=3,
        role_generation=3,
        configuration_revision=3,
    )
    return SimpleNamespace(
        phase=phase,
        transaction_id=transaction_id,
        decision_id="decision-a",
        incumbent=incumbent,
        candidate=candidate,
        rollback=incumbent,
        origin="passive-online",
        policy="passive-auto",
        reason=None,
    )


def _rollback(evidence_id: str = "rollback-a") -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind.ROLLBACK,
        session_id="session-a",
        cook_id="cook-a",
        timestamp_ms=600,
        role_generation=4,
        model_digest=_DIGEST,
        provenance_digest=_OTHER_DIGEST,
        payload=RollbackEvidence(decision_id="decision-a", reason="runtime-failure"),
    )


def test_forecast_envelope_must_match_precommitted_payload_digests() -> None:
    record = _forecast("mismatched", 100)
    with pytest.raises(ValidationError, match="forecast envelope digests"):
        ModelEvidenceRecord(
            evidence_id=record.evidence_id,
            kind=record.kind,
            session_id=record.session_id,
            cook_id=record.cook_id,
            timestamp_ms=record.timestamp_ms,
            role_generation=record.role_generation,
            model_digest=_DIGEST,
            provenance_digest=record.provenance_digest,
            payload=record.payload,
        )


def test_calibration_summary_requires_matching_completed_frame_allocations() -> None:
    baseline = AllocationEvidence(
        0.3, 0.27, None, 0.9, 0.0, 100.0, False, AllocationClampReason.NONE, AllocationClampReason.NONE, 2
    )
    combined = AllocationEvidence(
        0.4, 0.36, None, 0.9, 0.0, 100.0, False, AllocationClampReason.NONE, AllocationClampReason.NONE, 2
    )

    payload = CalibrationSummaryEvidence(
        accepted=True,
        probe_count=1,
        result_revision=3,
        command_revision=2,
        command_action="start",
        baseline_q=0.3,
        probe_q=0.1,
        combined_q=0.4,
        baseline_allocation=baseline,
        combined_allocation=combined,
        scheduled_on_seconds=8.0,
        delivered_on_seconds=7.0,
    )

    assert payload.combined_allocation == combined
    with pytest.raises(ValidationError):
        replace(payload, result_revision=0)


def test_calibration_summary_accepts_delivery_that_overruns_the_schedule() -> None:
    """The auger relay releases on the control tick after the scheduled on-time elapses.

    These numbers come from a real framed pulse on the live grill: an 18.0 s schedule
    inside a 20.0 s frame delivered 18.0557 s. The physical bound is that delivery fits
    within the frame, and only FramedPulsePayload carries the frame duration needed to
    state it, so this payload accepts the delivered figure as measured.
    """
    baseline = AllocationEvidence(
        0.3, 0.27, None, 0.9, 0.0, 100.0, False, AllocationClampReason.NONE, AllocationClampReason.NONE, 2
    )
    combined = AllocationEvidence(
        0.4, 0.36, None, 0.9, 0.0, 100.0, False, AllocationClampReason.NONE, AllocationClampReason.NONE, 2
    )

    payload = CalibrationSummaryEvidence(
        accepted=True,
        probe_count=1,
        result_revision=3,
        command_revision=2,
        command_action="start",
        baseline_q=0.3,
        probe_q=0.1,
        combined_q=0.4,
        baseline_allocation=baseline,
        combined_allocation=combined,
        scheduled_on_seconds=18.0,
        delivered_on_seconds=18.0557,
    )

    assert payload.scheduled_on_seconds == 18.0
    assert payload.delivered_on_seconds == 18.0557


@pytest.mark.parametrize(
    "replacement",
    (
        {"status": "rejected"},
        {"status": "rejected", "accepted": False, "probe_count": 1},
        {"status": "active", "probe_count": 0},
        {"status": "cancelled"},
    ),
)
def test_calibration_summary_rejects_status_that_contradicts_completed_frame(replacement) -> None:
    baseline = AllocationEvidence(
        0.3, 0.27, None, 0.9, 0.0, 100.0, False, AllocationClampReason.NONE, AllocationClampReason.NONE, 2
    )
    combined = AllocationEvidence(
        0.4, 0.36, None, 0.9, 0.0, 100.0, False, AllocationClampReason.NONE, AllocationClampReason.NONE, 2
    )
    payload = CalibrationSummaryEvidence(
        accepted=True,
        probe_count=1,
        result_revision=3,
        command_revision=2,
        command_action="start",
        baseline_q=0.3,
        probe_q=0.1,
        combined_q=0.4,
        baseline_allocation=baseline,
        combined_allocation=combined,
        scheduled_on_seconds=8.0,
        delivered_on_seconds=7.0,
        status="active",
    )

    with pytest.raises(ValidationError):
        replace(payload, **replacement)


def test_append_only_identity_insertion_order_and_batch_atomicity(ds):
    later = _forecast("forecast-later", 200, sequence=2)
    first = _forecast("forecast-first", 100)

    append_model_evidence([later, first])

    committed = read_model_evidence(session_id="session-a")
    assert committed == [later, first]
    would_be_partial = _forecast("forecast-before-duplicate", 300, sequence=3)
    with pytest.raises(sqlite3.IntegrityError):
        append_model_evidence([would_be_partial, later])
    assert read_model_evidence(session_id="session-a") == committed

    recovered = _forecast("forecast-after-duplicate", 400, sequence=4)
    append_model_evidence([recovered])
    assert read_model_evidence(session_id="session-a") == [*committed, recovered]


def test_raw_trace_pruning_cannot_delete_durable_evidence(ds):
    from common.control_trace import ControllerType, ControlTraceRecord, SessionPayload, TraceEventKind, TraceSetting
    from common.persistence.control_trace import append_control_trace, prune_control_trace

    evidence = _forecast("forecast-a", 100)
    trace = ControlTraceRecord(
        ts_ms=100,
        session_id="session-a",
        cook_id="cook-a",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.SESSION,
        payload=SessionPayload(
            controller=ControllerType.MPC,
            controller_config=(TraceSetting(key="horizon", value=3),),
            temperature_unit="F",
            control_period_seconds=1.0,
            model_revision=1,
            model_provenance="persisted",
            pulse_slot_seconds=1.0,
            pulse_frame_seconds=1.0,
            fan_authority=False,
            fan_pwm_capable=True,
            fan_min_duty=0.0,
            fan_max_duty=1.0,
            setpoint=225.0,
            ambient_temperature=70.0,
            software_version="test",
            build_version="test",
        ),
    )
    append_model_evidence([evidence])
    append_control_trace([trace])

    assert prune_control_trace(101, limit=1) == 1
    assert read_model_evidence(session_id="session-a") == [evidence]


def test_schema_invalidation_and_explicit_reset_are_the_only_ledger_deletions(ds):
    append_model_evidence([_forecast("first", 100)])
    invalidate_model_evidence_schema()
    assert read_model_evidence(session_id="session-a") == []

    append_model_evidence([_forecast("second", 101)])
    reset_model_evidence()
    assert read_model_evidence(session_id="session-a") == []


def test_corrupt_payload_and_calibration_fit_forecast_are_rejected(ds):
    with pytest.raises(ValidationError, match="calibration"):
        replace(_forecast("calibration", 100).payload, calibration_fit=True)

    conn = ds.connection()
    conn.execute(
        """
        INSERT INTO model_evidence(
            evidence_id, session_id, cook_id, timestamp_ms, kind, role_generation,
            model_digest, provenance_digest, schema_version, payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("corrupt", "session-a", "cook-a", 100, "forecast_origin", 2, _DIGEST, _OTHER_DIGEST, 1, "[]"),
    )
    with pytest.raises(ValueError, match="invalid payload"):
        read_model_evidence(session_id="session-a")


def test_v1_timing_row_reads_with_unavailable_new_measurements(ds) -> None:
    conn = ds.connection()
    conn.execute(
        """
        INSERT INTO model_evidence(
            evidence_id, session_id, cook_id, timestamp_ms, kind, role_generation,
            model_digest, provenance_digest, schema_version, payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "v1-timing",
            "session-a",
            "cook-a",
            100,
            "timing_distribution",
            2,
            _OTHER_DIGEST,
            _DIGEST,
            1,
            '{"sample_count": 50, "p50_ms": 10.0, "p95_ms": 20.0, "payload_type": "timing_distribution"}',
        ),
    )

    record = read_model_evidence(session_id="session-a")[0]

    assert MODEL_EVIDENCE_SCHEMA_VERSION == 3
    assert record.schema_version == 1
    assert isinstance(record.payload, TimingDistributionEvidence)
    assert record.payload.p99_ms is None
    assert record.payload.hardware_provenance is None


def test_current_grey_fit_and_candidate_evidence_round_trip_without_state_space_fields(ds):
    fit = ModelEvidenceRecord(
        evidence_id="fit-grey",
        kind=EvidenceKind.FIT_LIFECYCLE,
        session_id="session-a",
        cook_id="cook-a",
        timestamp_ms=301,
        role_generation=4,
        model_digest=_DIGEST,
        provenance_digest=_OTHER_DIGEST,
        payload=FitLifecycleEvidence(
            request_id="fit-request-a",
            status="succeeded",
            origin="passive-online",
            policy="passive-auto",
            window_id="window-a",
        ),
    )
    assessment = ModelEvidenceRecord(
        evidence_id="assessment-grey",
        kind=EvidenceKind.CANDIDATE_ASSESSMENT,
        session_id="session-a",
        cook_id="cook-a",
        timestamp_ms=302,
        role_generation=4,
        model_digest=_DIGEST,
        provenance_digest=_OTHER_DIGEST,
        payload=CandidateAssessmentEvidence(
            decision_id="decision-a",
            origin="passive-online",
            policy="passive-auto",
            fit_accepted=True,
            identifiability_accepted=True,
            native_build="passed",
            native_dry_solve="passed",
            target_timing="passed",
            confidence_accepted=True,
        ),
    )

    append_model_evidence((fit, assessment))
    assert read_model_evidence(session_id="session-a")[-2:] == [fit, assessment]
    assert "pole_magnitude" not in assessment.payload.__dataclass_fields__
    assert "state_space" not in assessment.model_dump_json()


def test_retired_schema_confidence_remains_audit_history_but_cannot_authorize_activation(ds):
    retired = _confidence("retired-confidence", timestamp_ms=500).model_copy(update={"schema_version": 2})
    append_model_evidence((retired,))

    with pytest.raises(ValueError, match="activation-authority-changed"):
        commit_model_activation(_activation())

    assert read_model_evidence(session_id="session-a")[-1] == retired
    assert read_model_activation() is None


def test_activation_commit_preserves_prior_singleton_and_evidence_when_replacement_fails(ds):
    decision = _activation()
    append_model_evidence((_confidence(),))
    commit_model_activation(decision)
    active = read_model_activation()
    assert active is not None
    assert active.evidence_decision_id == "decision-a"
    assert active.active_snapshot_json == '{"revision": 3, "model": "new"}'
    assert read_model_evidence(kind="activation") == [decision]

    ds.connection().execute(
        """
        CREATE TEMP TRIGGER fail_activation_state_write
        BEFORE INSERT ON model_activation_state
        BEGIN
            SELECT RAISE(ABORT, 'simulated activation state failure');
        END
        """
    )
    replacement = decision.model_copy(update={"evidence_id": "activation-replacement"})
    try:
        with pytest.raises(sqlite3.IntegrityError, match="simulated activation state failure"):
            commit_model_activation(replacement)

        assert read_model_activation() == active
        assert read_model_evidence(kind="activation") == [decision]
    finally:
        ds.connection().execute("DROP TRIGGER fail_activation_state_write")

    commit_model_activation(replacement)
    assert read_model_activation() == active
    assert read_model_evidence(kind="activation") == [decision, replacement]


def test_activation_commit_rejects_a_newer_confidence_authority(ds):
    append_model_evidence(
        (
            _confidence(),
            _confidence("confidence-b", decision_id="decision-b", timestamp_ms=600),
        )
    )

    with pytest.raises(ValueError, match="activation-authority-changed"):
        commit_model_activation(_activation())

    assert read_model_activation() is None
    assert read_model_evidence(kind="activation") == []


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_activation_snapshot_rejects_nonstandard_json_constants(constant):
    with pytest.raises(ValidationError, match="valid JSON"):
        ActivationEvidence(
            decision_id="decision-a",
            active_snapshot_json=f'{{"gain": {constant}}}',
            rollback_snapshot_json='{"revision": 1}',
            controller_configuration_digest=_OTHER_DIGEST,
        )


@pytest.mark.parametrize(
    ("descriptor", "message"),
    [
        ("[]", "descriptor must be an object"),
        ('{"configuration":[]}', "configuration must be an object"),
        (
            '{"configuration":{},"candidate_generation":true,"role_generation":0}',
            "generations must be non-negative integers",
        ),
        (
            (
                '{"configuration":{},"candidate_generation":0,"role_generation":0,'
                '"model_digest":"","estimator_kind":"grey","solver_kind":"native",'
                '"ownership_digest":"owner"}'
            ),
            "identity fields must be non-blank strings",
        ),
    ],
)
def test_activation_pair_rejects_malformed_stored_descriptors(descriptor, message) -> None:
    with pytest.raises(ValueError, match=message):
        ModelActivationPair.from_json(descriptor)


def test_activation_pair_equality_compares_stored_record_values() -> None:
    pair = _pair(
        _DIGEST,
        candidate_generation=3,
        role_generation=3,
        configuration_revision=3,
    )

    assert pair == ModelActivationPair.from_json(
        '{"candidate_generation":3,"configuration":{"revision":3},'
        '"estimator_kind":"grey-estimator","model_digest":"' + _DIGEST + '",'
        '"ownership_digest":"' + _OTHER_DIGEST + '","role_generation":3,'
        '"solver_kind":"native-solver"}'
    )


def test_append_rejects_invalid_containers_and_members_but_accepts_empty_batch(ds) -> None:
    with pytest.raises(TypeError, match="sequence"):
        append_model_evidence("not-a-record-sequence")
    with pytest.raises(TypeError, match="only ModelEvidenceRecord"):
        append_model_evidence([object()])

    append_model_evidence(())
    assert read_model_evidence() == []


@pytest.mark.parametrize(
    "filters",
    [
        {"session_id": " "},
        {"cook_id": ""},
        {"kind": "not-an-evidence-kind"},
    ],
)
def test_read_filters_reject_blank_identifiers_and_unknown_record_kind(ds, filters) -> None:
    with pytest.raises(ValueError):
        read_model_evidence(**filters)


def test_activation_commit_rejects_wrong_record_type_and_mixed_provenance(ds) -> None:
    with pytest.raises(ValueError, match="requires activation evidence"):
        commit_model_activation(_forecast("not-an-activation", 100))

    poison = _confidence("older-poison", timestamp_ms=399).model_copy(update={"provenance_digest": _DIGEST})
    append_model_evidence((poison, _confidence()))
    with pytest.raises(ValueError, match="activation-authority-changed"):
        commit_model_activation(_activation())

    assert read_model_activation() is None
    assert read_model_evidence(kind=EvidenceKind.ACTIVATION) == []


@pytest.mark.parametrize(
    ("phase", "expected_phase", "message"),
    [
        ("unknown", None, "unknown activation phase"),
        ("prepared", "prepared", "cannot have an expected phase"),
        ("active", None, "requires expected prepared phase"),
    ],
)
def test_activation_phase_validates_cas_transition_shape(phase, expected_phase, message, ds) -> None:
    with pytest.raises(ValueError, match=message):
        commit_model_activation_phase(
            SimpleNamespace(phase=phase),
            expected_phase=expected_phase,
        )


def test_prepared_activation_requires_current_authority_and_consistent_provenance(ds) -> None:
    prepared = _phase_record()
    with pytest.raises(ValueError, match="activation-authority-changed"):
        commit_model_activation_phase(prepared)

    poison = _confidence("older-poison", timestamp_ms=399).model_copy(update={"provenance_digest": _DIGEST})
    append_model_evidence((poison, _confidence()))
    with pytest.raises(ValueError, match="activation-authority-changed"):
        commit_model_activation_phase(prepared)

    assert read_model_activation() is None


def test_replaying_identical_prepared_activation_is_idempotent(ds) -> None:
    prepared = _phase_record()
    append_model_evidence((_confidence(),))
    commit_model_activation_phase(prepared)
    first = read_model_activation()

    commit_model_activation_phase(prepared)

    assert read_model_activation() == first


def test_rollback_commit_rejects_wrong_record_type_and_stale_cas_state(ds) -> None:
    with pytest.raises(ValueError, match="requires rollback evidence"):
        commit_model_rollback(_activation(), expected_activation=None)

    append_model_evidence((_confidence(),))
    commit_model_activation(_activation())
    active = read_model_activation()
    assert active is not None

    with pytest.raises(ValueError, match="activation-state-changed"):
        commit_model_rollback(
            _rollback(),
            expected_activation=replace(active, role_generation=active.role_generation + 1),
        )
    assert read_model_evidence(kind=EvidenceKind.ROLLBACK) == []


def test_legacy_rollback_requires_activation_lineage(ds) -> None:
    append_model_evidence((_confidence(),))
    commit_model_activation(_activation())
    active = read_model_activation()
    assert active is not None
    ds.connection().execute(
        "DELETE FROM model_evidence WHERE kind=?",
        (EvidenceKind.ACTIVATION.value,),
    )

    with pytest.raises(ValueError, match="activation-lineage-missing"):
        commit_model_rollback(_rollback(), expected_activation=active)

    assert read_model_evidence(kind=EvidenceKind.ROLLBACK) == []


def test_legacy_rollback_insert_is_idempotent(ds) -> None:
    append_model_evidence((_confidence(),))
    commit_model_activation(_activation())
    active = read_model_activation()
    assert active is not None
    rollback = _rollback()

    inserted = commit_model_rollback(rollback, expected_activation=active)
    replayed = commit_model_rollback(
        rollback.model_copy(update={"evidence_id": "rollback-retry"}),
        expected_activation=active,
    )

    assert inserted.inserted is True
    assert inserted.record == rollback
    assert replayed.inserted is False
    assert replayed.record == rollback
    assert read_model_evidence(kind=EvidenceKind.ROLLBACK) == [rollback]


def test_prior_fallback_satisfies_rollback_lifecycle(ds) -> None:
    append_model_evidence((_confidence(),))
    commit_model_activation(_activation())
    active = read_model_activation()
    assert active is not None
    fallback = ModelEvidenceRecord(
        evidence_id="fallback-a",
        kind=EvidenceKind.FALLBACK,
        session_id="session-a",
        cook_id="cook-a",
        timestamp_ms=550,
        role_generation=4,
        model_digest=_DIGEST,
        provenance_digest=_OTHER_DIGEST,
        payload=FallbackEvidence(
            decision_id="decision-a",
            reason="runtime-failure",
            failed_digest=_DIGEST,
            failed_generation=3,
        ),
    )
    append_model_evidence((fallback,))

    outcome = commit_model_rollback(_rollback(), expected_activation=active)

    assert outcome.inserted is False
    assert outcome.record == fallback
    assert read_model_evidence(kind=EvidenceKind.ROLLBACK) == []
