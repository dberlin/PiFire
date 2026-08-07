from __future__ import annotations

from hashlib import sha256

import numpy as np

from controller.linear_mpc.confidence import ConfidenceConfig, evaluate_confidence


_DIGEST = sha256(b"challenger").hexdigest()
_PROVENANCE = sha256(b"provenance").hexdigest()


def _origin(cook: str, sequence: int, challenger: float, incumbent: float) -> dict[str, object]:
    return {
        "kind": "forecast_origin",
        "session_id": f"session-{cook}",
        "cook_id": cook,
        "role_generation": 4,
        "model_digest": _DIGEST,
        "provenance_digest": _PROVENANCE,
        "schema_version": 1,
        "payload": {
            "origin_sequence": sequence,
            "horizon_steps": 3,
            "incumbent_error_c": incumbent,
            "challenger_error_c": challenger,
            "temperature_band": "middle",
            "phase": "heating",
            "ambient_source": "configured",
            "calibration_fit": False,
            "untouched_future": True,
        },
    }


def _correlated_cooks() -> list[dict[str, object]]:
    # Each cook is internally correlated: one easy block and one hard block.
    return [
        *[_origin("a", sequence, 0.2 if sequence < 3 else 0.8, 1.0) for sequence in range(6)],
        *[_origin("b", sequence, 0.3 if sequence < 3 else 0.9, 1.0) for sequence in range(6)],
    ]


def _interval(evidence: list[dict[str, object]]):
    report = evaluate_confidence(
        evidence,
        activation_state={"status": "collecting"},
        target_timing={"p99_ms": 10.0, "hardware_provenance": "target-hardware"},
        config=ConfidenceConfig(bootstrap_seed=17, bootstrap_replicates=1_000),
    )
    return report.bootstrap_intervals[0]


def test_grouped_bootstrap_is_byte_identical_for_same_seed_and_evidence() -> None:
    first = _interval(_correlated_cooks())
    second = _interval(_correlated_cooks())

    assert first == second
    assert repr(first).encode() == repr(second).encode()
    assert first.method == "hierarchical-cook-block"


def test_grouped_bootstrap_is_invariant_to_ledger_row_order() -> None:
    evidence = _correlated_cooks()
    assert _interval(evidence) == _interval(list(reversed(evidence)))


def test_grouped_bootstrap_is_not_an_independent_row_bootstrap() -> None:
    evidence = _correlated_cooks()
    interval = _interval(evidence)
    ratios = np.array(
        [
            row["payload"]["challenger_error_c"] ** 2 / row["payload"]["incumbent_error_c"] ** 2
            for row in evidence
        ],
        dtype=np.float64,
    )
    rng = np.random.default_rng(17)
    independent = np.quantile(
        np.sqrt(rng.choice(ratios, size=(1_000, len(ratios)), replace=True).mean(axis=1)),
        0.95,
        method="higher",
    )

    assert interval.upper_bound < 1.0
    assert interval.upper_bound != independent


def test_fewer_than_two_cooks_has_no_bootstrap_confidence() -> None:
    interval = _interval([origin for origin in _correlated_cooks() if origin["cook_id"] == "a"])

    assert interval.available is False
    assert interval.upper_bound is None
