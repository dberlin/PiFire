from __future__ import annotations

import numpy as np

from dataclasses import replace

from common.model_evidence import EvidenceKind, ForecastOriginEvidence
from controller.linear_mpc.confidence import ConfidenceConfig, evaluate_confidence
from tests.unit.mpc.test_model_confidence import _qualifying, _state


def _interval(records):
    report = evaluate_confidence(records, activation_state=_state(), target_timing=None, config=ConfidenceConfig(bootstrap_seed=17))
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
        record.model_copy(
            update={
                "payload": replace(
                    record.payload,
                    challenger_error_c=0.1 if record.cook_id == "cook-a" else 0.9,
                    incumbent_error_c=1.0,
                )
            }
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
