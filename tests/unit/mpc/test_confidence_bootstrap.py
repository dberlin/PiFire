from __future__ import annotations

import numpy as np

from dataclasses import replace

from common.model_evidence import EvidenceKind, ForecastOriginEvidence
from controller.model_learning.confidence import ConfidenceConfig, evaluate_confidence
from tests.unit.mpc.test_model_confidence import _qualifying, _rebuild, _state


def _session_origin(
    record,
    *,
    session_id: str,
    origin_sequence: int,
    evidence_id: str,
):
    assert isinstance(record.payload, ForecastOriginEvidence)
    return _rebuild(
        record,
        evidence_id=evidence_id,
        session_id=session_id,
        payload=replace(
            record.payload,
            origin_sequence=origin_sequence,
            origin_time_ms=origin_sequence * 20,
            completion_time_ms=(origin_sequence + record.payload.horizon_steps) * 20,
        ),
    )


def _two_session_same_cook_horizon_three(*, first_session_has_block: bool):
    records = _qualifying()
    cook_a = tuple(
        record
        for record in records
        if isinstance(record.payload, ForecastOriginEvidence)
        and record.cook_id == "cook-a"
        and record.payload.horizon_steps == 3
    )
    cook_b = tuple(
        record
        for record in records
        if isinstance(record.payload, ForecastOriginEvidence)
        and record.cook_id == "cook-b"
        and record.payload.horizon_steps == 3
    )
    if first_session_has_block:
        sessions = (
            _session_origin(cook_a[0], session_id="session-a", origin_sequence=0, evidence_id="a-0"),
            _session_origin(cook_a[1], session_id="session-a", origin_sequence=1, evidence_id="a-1"),
            _session_origin(cook_a[2], session_id="session-a", origin_sequence=2, evidence_id="a-2"),
            _session_origin(cook_a[0], session_id="session-restarted", origin_sequence=3, evidence_id="b-3"),
            _session_origin(cook_a[1], session_id="session-restarted", origin_sequence=4, evidence_id="b-4"),
        )
    else:
        sessions = (
            _session_origin(cook_a[0], session_id="session-a", origin_sequence=0, evidence_id="a-0"),
            _session_origin(cook_a[1], session_id="session-a", origin_sequence=1, evidence_id="a-1"),
            _session_origin(cook_a[2], session_id="session-restarted", origin_sequence=2, evidence_id="b-2"),
            _session_origin(cook_a[0], session_id="session-restarted", origin_sequence=3, evidence_id="b-3"),
        )
    non_forecasts = tuple(record for record in records if not isinstance(record.payload, ForecastOriginEvidence))
    return non_forecasts + cook_b + sessions


def _interval(records):
    report = evaluate_confidence(
        records, activation_state=_state(), target_timing=None, config=ConfidenceConfig(bootstrap_seed=17)
    )
    return next(interval for interval in report.bootstrap_intervals if interval.horizon_steps == 3)


def test_ten_thousand_grouped_replicates_are_byte_identical() -> None:
    first = _interval(_qualifying())
    second = _interval(_qualifying())
    assert first == second
    assert repr(first).encode() == repr(second).encode()
    assert first.replicate_count == 10_000
    assert first.method == "hierarchical-cook-block"


def test_grouped_bootstrap_is_invariant_to_ledger_order() -> None:
    records = _qualifying()
    assert _interval(records) == _interval(tuple(reversed(records)))


def test_grouped_blocks_are_scientifically_distinct_from_independent_rows() -> None:
    records = tuple(
        _rebuild(
            record,
            payload=replace(
                record.payload,
                challenger_error_c=0.1 if record.cook_id == "cook-a" else 0.9,
                incumbent_error_c=1.0,
            ),
        )
        if record.kind is EvidenceKind.FORECAST_ORIGIN
        and isinstance(record.payload, ForecastOriginEvidence)
        and record.payload.horizon_steps == 3
        else record
        for record in _qualifying()
    )
    interval = _interval(records)
    ratios = np.array(
        [
            record.payload.challenger_error_c**2 / record.payload.incumbent_error_c**2
            for record in records
            if record.kind is EvidenceKind.FORECAST_ORIGIN
            and isinstance(record.payload, ForecastOriginEvidence)
            and record.payload.horizon_steps == 3
        ]
    )
    rng = np.random.default_rng(17)
    independent = np.quantile(
        np.sqrt(rng.choice(ratios, size=(10_000, len(ratios)), replace=True).mean(axis=1)),
        0.95,
        method="higher",
    )
    assert interval.upper_bound != independent


def test_one_cook_has_no_grouped_interval() -> None:
    records = tuple(record for record in _qualifying() if record.cook_id != "cook-b")
    interval = _interval(records)
    assert interval.available is False
    assert interval.replicate_count == 0


def test_same_cook_short_sessions_cannot_join_a_bootstrap_block() -> None:
    interval = _interval(_two_session_same_cook_horizon_three(first_session_has_block=False))

    assert interval.available is False
    assert interval.replicate_count == 0


def test_same_cook_session_with_a_full_block_remains_bootstrap_eligible() -> None:
    interval = _interval(_two_session_same_cook_horizon_three(first_session_has_block=True))

    assert interval.available is True
    assert interval.replicate_count == 10_000
