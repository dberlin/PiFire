"""Behavior contracts for fitting one grey model over independent segments."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from scipy import optimize
from scipy.linalg import expm

from common.learning_trajectory import (
    FitCorpusIdentity,
    FitCorpusSlice,
    canonical_fit_corpus_digest,
)
from controller.acados.contracts import GreyBoxMPCConfig
from controller.model_learning.contracts import CandidateOrigin, FitRequest
from controller.runtime import model_fitting as fitting
from controller.runtime.model_fitting import (
    FIT_VALUE_BOUNDS,
    FITTED_PARAMETERS,
    GreyFitError,
    GreyFitJob,
    GreyFitSegmentArrays,
    GreyFitSuccess,
    fit_segmented_grey,
)

_PARTITION = "7" * 64
_INCUMBENT = "8" * 64
_IDENT_STEP = 1e-3


def _config(**overrides: Any) -> GreyBoxMPCConfig:
    values: dict[str, Any] = {
        "C_c": 900.0,
        "h_amb": 0.45,
        "T_amb": 20.0,
        "theta": 50.0,
        "K_Q": 420.0,
        "sigma": 0.0,
        "horizon_steps": 12,
    }
    values.update(overrides)
    return GreyBoxMPCConfig(**values)


def _request(
    first: int,
    last: int,
    fit_corpus: FitCorpusIdentity,
    *,
    origin: CandidateOrigin = CandidateOrigin.PASSIVE_ONLINE,
) -> FitRequest:
    return FitRequest(
        request_id=f"segmented-{first}-{last}-{origin.value}",
        origin=origin,
        fit_corpus=fit_corpus,
        configuration_digest=_PARTITION,
        parent_incumbent_digest=_INCUMBENT,
        parent_incumbent_generation=4,
        candidate_generation=9,
    )


def _advance_linear_state(
    state: np.ndarray,
    duration_s: float,
    load: float,
    ambient_c: float,
    config: GreyBoxMPCConfig,
) -> np.ndarray:
    """Independent exact affine oracle for sigma=0 grey dynamics."""
    assert config.sigma == 0.0
    n_delay = config.delay_states
    size = n_delay + 1
    matrix = np.zeros((size + 1, size + 1), dtype=float)
    rate = n_delay / config.theta
    for index in range(n_delay):
        matrix[index, index] = -rate
        if index:
            matrix[index, index - 1] = rate
    matrix[0, -1] = rate * load
    chamber = n_delay
    matrix[chamber, chamber] = -config.h_amb / config.C_c
    matrix[chamber, n_delay - 1] = config.K_Q / config.C_c
    matrix[chamber, -1] = config.h_amb * ambient_c / config.C_c
    augmented = np.concatenate((state, np.asarray([1.0])))
    return (expm(matrix * duration_s) @ augmented)[:-1]


def _oracle_prediction(segment: GreyFitSegmentArrays, config: GreyBoxMPCConfig) -> np.ndarray:
    state = np.full(config.delay_states + 1, segment.initial_load, dtype=float)
    state[-1] = segment.hold_anchor_c
    for duration_s, load in zip(segment.pre_roll_duration_s, segment.pre_roll_load, strict=True):
        state = _advance_linear_state(state, float(duration_s), float(load), config.T_amb, config)
    state[-1] = segment.hold_anchor_c
    predicted = []
    for duration_s, load, ambient_c in zip(
        segment.scored_duration_s,
        segment.scored_load,
        segment.scored_ambient_c,
        strict=True,
    ):
        state = _advance_linear_state(state, float(duration_s), float(load), float(ambient_c), config)
        predicted.append(float(state[-1]))
    return np.asarray(predicted)


def _oracle_effective_mask(segment: GreyFitSegmentArrays, theta: float) -> np.ndarray:
    before = np.concatenate(
        (
            np.asarray([float(np.sum(segment.pre_roll_duration_s))]),
            float(np.sum(segment.pre_roll_duration_s)) + np.cumsum(segment.scored_duration_s[:-1]),
        )
    )
    return before >= 3.0 * theta


def _segment(
    segment_id: str,
    cook_id: str,
    *,
    config: GreyBoxMPCConfig,
    sequence_start: int,
    scored_load: tuple[float, ...],
    pre_roll_load: tuple[float, ...] = (0.2, 0.4, 0.6, 0.5, 0.3, 0.2),
    duration_s: float = 20.0,
    anchor_c: float = 75.0,
    ambient_c: float | tuple[float, ...] = 20.0,
    initial_load: float | None = None,
    errors_c: tuple[float, ...] | None = None,
    calibration_origin: bool = False,
) -> GreyFitSegmentArrays:
    count = len(scored_load)
    ambient = (ambient_c,) * count if isinstance(ambient_c, (int, float)) else ambient_c
    if len(ambient) != count:
        raise ValueError("test fixture ambient length must match scored rows")
    values = GreyFitSegmentArrays(
        segment_id=segment_id,
        cook_id=cook_id,
        through_ordinal=len(pre_roll_load) + count - 1,
        prefix_digest=hashlib.sha256(segment_id.encode()).hexdigest(),
        segment_content_digest=hashlib.sha256(f"segment-content:{segment_id}".encode()).hexdigest(),
        fit_partition_digest=_PARTITION,
        observation_sequences=tuple(range(sequence_start, sequence_start + count)),
        initial_load=pre_roll_load[0] if initial_load is None else initial_load,
        pre_roll_duration_s=(duration_s,) * len(pre_roll_load),
        pre_roll_load=pre_roll_load,
        pre_roll_temperature_c=tuple(500.0 + index for index in range(len(pre_roll_load))),
        hold_anchor_c=anchor_c,
        scored_duration_s=(duration_s,) * count,
        scored_load=scored_load,
        scored_ambient_c=ambient,
        scored_temperature_c=(anchor_c,) * count,
        calibration_origin=(calibration_origin,) * count,
    )
    predicted = _oracle_prediction(values, config)
    errors = np.zeros(count) if errors_c is None else np.asarray(errors_c, dtype=float)
    if len(errors) != count:
        raise ValueError("test fixture errors length must match scored rows")
    return replace(values, scored_temperature_c=tuple(predicted - errors))


def _corpus(segments: tuple[GreyFitSegmentArrays, ...], *, partition: str = _PARTITION) -> FitCorpusIdentity:
    slices = tuple(
        FitCorpusSlice(
            segment_id=segment.segment_id,
            through_ordinal=segment.through_ordinal,
            prefix_digest=segment.prefix_digest,
            segment_content_digest=segment.segment_content_digest,
            pre_roll_count=len(segment.pre_roll_load),
            scored_count=len(segment.scored_load),
        )
        for segment in segments
    )
    return FitCorpusIdentity(
        schema_version=2,
        corpus_revision=17,
        fit_partition_digest=partition,
        slices=slices,
        corpus_digest=canonical_fit_corpus_digest(
            schema_version=2,
            corpus_revision=17,
            fit_partition_digest=partition,
            slices=slices,
        ),
    )


def _job(segments: tuple[GreyFitSegmentArrays, ...], config: GreyBoxMPCConfig) -> GreyFitJob:
    first = min(int(segment.observation_sequences[0]) for segment in segments)
    last = max(int(segment.observation_sequences[-1]) for segment in segments)
    corpus = _corpus(segments)
    return GreyFitJob(
        request=_request(first, last, corpus),
        corpus=corpus,
        segments=segments,
        config=config,
    )


def _optimizer_point(config: GreyBoxMPCConfig) -> np.ndarray:
    return np.log(np.asarray([getattr(config, key) for key in FITTED_PARAMETERS], dtype=float))


def _pin_optimizer(monkeypatch: pytest.MonkeyPatch, *points: GreyBoxMPCConfig) -> list[np.ndarray]:
    calls: list[np.ndarray] = []

    def fixed(residual: Any, _x0: Any, *args: Any, **kwargs: Any) -> SimpleNamespace:
        point = points[min(len(calls), len(points) - 1)]
        vector = _optimizer_point(point)
        calls.append(np.asarray(residual(vector), dtype=float))
        return SimpleNamespace(x=vector, status=1, nfev=1, success=True)

    monkeypatch.setattr(optimize, "least_squares", fixed)
    return calls


def _metric(entries: Any, field: str, value: str) -> Any:
    return next(entry for entry in entries if getattr(entry, field) == value)


def _manual_metric(errors: np.ndarray, temperatures: np.ndarray, loads: np.ndarray) -> dict[str, Any]:
    return {
        "sample_count": len(errors),
        "rmse_c": float(np.sqrt(np.mean(errors**2))),
        "bias_c": float(np.mean(errors)),
        "error_band_c": (float(np.min(errors)), float(np.max(errors))),
        "max_error_c": float(np.max(np.abs(errors))),
        "input_excitation": float(np.var(loads)),
        "input_levels": len({float(value) for value in loads}),
        "identifiability_row_count": len(errors),
        "temperature_span_c": float(np.max(temperatures) - np.min(temperatures)),
    }


def _assert_metric(actual: Any, expected: dict[str, Any]) -> None:
    assert actual.sample_count == expected["sample_count"]
    assert actual.rmse_c == pytest.approx(expected["rmse_c"], abs=0.03)
    assert actual.bias_c == pytest.approx(expected["bias_c"], abs=0.03)
    assert actual.error_band_c == pytest.approx(expected["error_band_c"], abs=0.03)
    assert actual.max_error_c == pytest.approx(expected["max_error_c"], abs=0.03)
    assert actual.input_excitation == pytest.approx(expected["input_excitation"], abs=1e-12)
    assert actual.input_levels == expected["input_levels"]
    assert actual.identifiability_row_count == expected["identifiability_row_count"]
    assert actual.temperature_span_c == pytest.approx(expected["temperature_span_c"], abs=1e-9)


def test_independently_initialized_segmented_simulation_matches_manual_pooled_residuals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(theta=50.0)
    first = _segment(
        "segment-a",
        "cook-a",
        config=config,
        sequence_start=0,
        scored_load=(0.1, 0.8, 0.8, 0.2, 0.2),
        anchor_c=70.0,
        errors_c=(0.5, -1.0, 1.5, -2.0, 0.25),
    )
    second = _segment(
        "segment-b",
        "cook-b",
        config=config,
        sequence_start=20,
        scored_load=(0.7, 0.7, 0.1, 0.5),
        anchor_c=130.0,
        errors_c=(-0.75, 0.5, 1.25, -1.5),
    )
    _pin_optimizer(monkeypatch, config, config)

    result = fit_segmented_grey(_job((first, second), config))

    assert isinstance(result, GreyFitSuccess)
    manual_errors = np.concatenate(
        (
            (_oracle_prediction(first, config) - first.scored_temperature_c)[
                _oracle_effective_mask(first, config.theta)
            ],
            (_oracle_prediction(second, config) - second.scored_temperature_c)[
                _oracle_effective_mask(second, config.theta)
            ],
        )
    )
    assert result.metrics.pooled.rmse_c == pytest.approx(float(np.sqrt(np.mean(manual_errors**2))), abs=0.03)
    assert result.metrics.pooled.max_error_c == pytest.approx(float(np.max(np.abs(manual_errors))), abs=0.03)


def test_segmented_result_differs_from_illegal_concatenation_across_a_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(theta=25.0)
    first = _segment(
        "before-stop",
        "cook-a",
        config=config,
        sequence_start=0,
        scored_load=(0.9, 0.9, 0.9, 0.9),
        anchor_c=80.0,
    )
    second = _segment(
        "after-restart",
        "cook-b",
        config=config,
        sequence_start=20,
        scored_load=(0.1, 0.1, 0.1, 0.1),
        pre_roll_load=(0.1, 0.1, 0.1),
        anchor_c=145.0,
    )
    _pin_optimizer(monkeypatch, config, config)

    result = fit_segmented_grey(_job((first, second), config))

    assert isinstance(result, GreyFitSuccess)
    assert result.metrics.pooled.rmse_c == pytest.approx(0.0, abs=0.03)
    state = np.full(config.delay_states + 1, first.initial_load, dtype=float)
    state[-1] = first.hold_anchor_c
    for duration, load in zip(first.pre_roll_duration_s, first.pre_roll_load, strict=True):
        state = _advance_linear_state(state, float(duration), float(load), config.T_amb, config)
    state[-1] = first.hold_anchor_c
    for duration, load, ambient in zip(first.scored_duration_s, first.scored_load, first.scored_ambient_c, strict=True):
        state = _advance_linear_state(state, float(duration), float(load), float(ambient), config)
    illegal_second = []
    for duration, load, ambient in zip(
        second.scored_duration_s, second.scored_load, second.scored_ambient_c, strict=True
    ):
        state = _advance_linear_state(state, float(duration), float(load), float(ambient), config)
        illegal_second.append(float(state[-1]))
    illegal_error = np.asarray(illegal_second) - second.scored_temperature_c
    illegal_mask = _oracle_effective_mask(second, config.theta)
    assert float(np.sqrt(np.mean(illegal_error[illegal_mask] ** 2))) > 5.0


def test_one_shared_parameter_vector_is_optimized_across_every_segment() -> None:
    truth = _config(C_c=1250.0, K_Q=510.0, theta=75.0)
    incumbent = _config(C_c=700.0, K_Q=300.0, theta=35.0)
    first = _segment(
        "shared-a",
        "cook-a",
        config=truth,
        sequence_start=0,
        scored_load=(0.1, 0.9, 0.9, 0.2, 0.2, 0.8, 0.1, 0.7, 0.3, 0.9, 0.2, 0.6),
        pre_roll_load=(0.2,) * 10,
        anchor_c=65.0,
    )
    second = _segment(
        "shared-b",
        "cook-b",
        config=truth,
        sequence_start=30,
        scored_load=(0.8, 0.1, 0.6, 0.2, 0.9, 0.3, 0.7, 0.1, 0.8, 0.2, 0.5, 0.9),
        pre_roll_load=(0.8,) * 10,
        anchor_c=155.0,
        ambient_c=(12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0, 21.0, 22.0, 23.0),
    )

    result = fit_segmented_grey(_job((first, second), incumbent))

    assert isinstance(result, GreyFitSuccess)
    assert result.config.C_c == pytest.approx(truth.C_c, rel=0.03)
    assert result.config.K_Q == pytest.approx(truth.K_Q, rel=0.03)
    assert result.config.theta == pytest.approx(truth.theta, rel=0.03)
    assert tuple(metric.segment_id for metric in result.metrics.by_segment) == ("shared-a", "shared-b")
    assert all(not hasattr(metric, "config") for metric in result.metrics.by_segment)


def test_smoke_temperature_is_invariant_but_smoke_load_changes_warm_state_and_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(theta=50.0)
    base = _segment(
        "smoke",
        "cook-smoke",
        config=config,
        sequence_start=10,
        scored_load=(0.4, 0.7, 0.2, 0.8),
        pre_roll_load=(0.1, 0.2, 0.7, 0.9, 0.4, 0.3),
    )
    changed_temperature = replace(
        base,
        pre_roll_temperature_c=tuple(value + 900.0 for value in base.pre_roll_temperature_c),
    )
    changed_load = replace(base, pre_roll_load=(0.9,) * len(base.pre_roll_load))
    _pin_optimizer(monkeypatch, config, config)
    baseline = fit_segmented_grey(_job((base,), config))
    _pin_optimizer(monkeypatch, config, config)
    temperature_only = fit_segmented_grey(_job((changed_temperature,), config))
    _pin_optimizer(monkeypatch, config, config)
    load_changed = fit_segmented_grey(_job((changed_load,), config))

    assert isinstance(baseline, GreyFitSuccess)
    assert isinstance(temperature_only, GreyFitSuccess)
    assert isinstance(load_changed, GreyFitSuccess)
    assert temperature_only.config == baseline.config
    assert temperature_only.metrics == baseline.metrics
    assert load_changed.metrics.pooled.rmse_c > baseline.metrics.pooled.rmse_c + 0.1


def test_anchor_preroll_and_leading_hold_warmup_are_not_residuals_or_effective_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(theta=50.0)
    segment = _segment(
        "warmup",
        "cook-warmup",
        config=config,
        sequence_start=0,
        scored_load=(0.1, 0.8, 0.2, 0.7, 0.3, 0.6),
        pre_roll_load=(0.4, 0.4, 0.4, 0.4),
        anchor_c=333.0,
        errors_c=(900.0, -800.0, 3.0, -4.0, 5.0, -6.0),
    )
    _pin_optimizer(monkeypatch, config, config)

    result = fit_segmented_grey(_job((segment,), config))

    assert isinstance(result, GreyFitSuccess)
    assert tuple(result.effective_masks[0]) == (False, False, False, False, True, True)
    assert result.optimizer_residual_count == len(segment.scored_load)
    assert result.metrics.pooled.sample_count == 2
    assert result.metrics.pooled.rmse_c == pytest.approx(math.sqrt((5.0**2 + 6.0**2) / 2.0), abs=0.03)
    assert result.metrics.pooled.error_band_c == pytest.approx((-6.0, 5.0), abs=0.03)


def test_warming_segment_is_explicitly_excluded_without_poisoning_older_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(theta=25.0)
    usable = _segment(
        "usable",
        "cook-usable",
        config=config,
        sequence_start=0,
        scored_load=tuple((0.1, 0.9, 0.3, 0.7)[index % 4] for index in range(30)),
        pre_roll_load=(0.4,) * 4,
        anchor_c=70.0,
    )
    warming = _segment(
        "warming",
        "cook-warming",
        config=config,
        sequence_start=40,
        scored_load=(0.2, 0.8, 0.4),
        pre_roll_load=(),
        initial_load=0.2,
        anchor_c=130.0,
    )
    monkeypatch.setattr(fitting, "_score_identifiability", lambda _columns, _masks: 0.8)
    _pin_optimizer(monkeypatch, config, config)

    result = fit_segmented_grey(_job((usable, warming), config))

    assert isinstance(result, GreyFitSuccess)
    assert result.warmup_excluded_segment_ids == ("warming",)
    assert tuple(metric.segment_id for metric in result.metrics.by_segment) == ("usable",)
    assert tuple(metric.cook_id for metric in result.metrics.by_cook) == ("cook-usable",)
    assert tuple(result.effective_masks[1]) == (False,) * len(warming.scored_load)
    assert result.metrics.pooled.sample_count == 30
    assert result.rejection_reasons == ()


def test_all_warming_segments_fail_with_typed_insufficient_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(theta=25.0)
    first = _segment(
        "warming-first",
        "cook-first",
        config=config,
        sequence_start=0,
        scored_load=(0.2, 0.8, 0.4),
        pre_roll_load=(),
        initial_load=0.2,
    )
    second = _segment(
        "warming-second",
        "cook-second",
        config=config,
        sequence_start=10,
        scored_load=(0.8, 0.2, 0.6),
        pre_roll_load=(),
        initial_load=0.8,
    )
    _pin_optimizer(monkeypatch, config, config)

    result = fit_segmented_grey(_job((first, second), config))

    assert isinstance(result, GreyFitError)
    assert result.error_type == "InsufficientWarmup"
    assert result.detail == "segment-warmup-incomplete:warming-first"


def test_an_all_masked_delay_trial_cannot_beat_a_supported_imperfect_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = _config(theta=25.0)
    segment = _segment(
        "fixed-support",
        "cook-mask",
        config=initial,
        sequence_start=0,
        scored_load=tuple((0.1, 0.8, 0.2, 0.7)[index % 4] for index in range(50)),
        pre_roll_load=(),
        initial_load=0.5,
        errors_c=(1.0,) * 50,
    )
    objectives: list[tuple[float, float]] = []

    def probing(residual: Any, x0: Any, *args: Any, **kwargs: Any) -> SimpleNamespace:
        supported = np.asarray(x0, dtype=float).copy()
        unsupported = supported.copy()
        unsupported[FITTED_PARAMETERS.index("theta")] = math.log(1200.0)
        objectives.append((float(np.sum(residual(supported) ** 2)), float(np.sum(residual(unsupported) ** 2))))
        return SimpleNamespace(x=supported, status=1, nfev=2, success=True)

    monkeypatch.setattr(optimize, "least_squares", probing)
    result = fit_segmented_grey(_job((segment,), initial))

    assert isinstance(result, GreyFitSuccess)
    assert all(unsupported > supported > 0.0 for supported, unsupported in objectives)
    assert result.sample_count >= 30


@pytest.mark.parametrize("initial_theta", [35.0, 150.0])
def test_noisy_mixed_duration_segments_fit_without_mask_fixed_point_perfection(initial_theta: float) -> None:
    truth = _config(C_c=1250.0, K_Q=510.0, theta=75.0)
    incumbent = _config(C_c=1000.0, K_Q=450.0, theta=initial_theta)
    rng = np.random.default_rng(712)
    segments = tuple(
        _segment(
            f"noisy-{index}",
            f"cook-{index}",
            config=truth,
            sequence_start=index * 200,
            scored_load=tuple(float(value) for value in rng.choice((0.1, 0.3, 0.7, 0.9), size=count)),
            pre_roll_load=(0.2, 0.8, 0.4, 0.6, 0.3, 0.7),
            anchor_c=65.0 + index * 45.0,
            errors_c=tuple(float(value) for value in rng.normal(0.0, 0.15, size=count)),
        )
        for index, count in enumerate((110, 105, 8))
    )

    result = fit_segmented_grey(_job(segments, incumbent))

    assert isinstance(result, GreyFitSuccess), result
    assert result.config.C_c == pytest.approx(truth.C_c, rel=0.1)
    assert result.config.K_Q == pytest.approx(truth.K_Q, rel=0.1)
    assert result.config.theta == pytest.approx(truth.theta, rel=0.15)
    assert result.sample_count >= 30
    assert result.rmse_c < 0.3
    assert result.rejection_reasons == ()
    for segment, mask in zip(segments, result.effective_masks, strict=True):
        expected = _oracle_effective_mask(segment, result.config.theta) & _oracle_effective_mask(
            segment, incumbent.theta
        )
        assert np.array_equal(mask, expected)


def test_partial_preroll_duration_limits_theta_without_inventing_effective_time() -> None:
    truth = _config(theta=26.0)
    incumbent = _config(theta=25.0)
    segment = _segment(
        "partial-preroll",
        "cook-partial",
        config=truth,
        sequence_start=0,
        scored_load=tuple((0.1, 0.9, 0.3, 0.7)[index % 4] for index in range(30)),
        pre_roll_load=(0.4, 0.2, 0.8, 0.3),
    )
    segment = replace(segment, pre_roll_duration_s=(15.0, 20.0, 20.0, 20.0))
    segment = replace(segment, scored_temperature_c=_oracle_prediction(segment, truth))

    result = fit_segmented_grey(_job((segment,), incumbent))

    assert isinstance(result, GreyFitSuccess), result
    assert result.config.theta == 25.0
    assert result.sample_count == 30
    assert result.metrics.pooled.sample_count * fitting.FIT_CADENCE_S == 600.0


def test_fractional_millisecond_preroll_preserves_the_full_supported_duration() -> None:
    config = _config(theta=25.0)
    segment = _segment(
        "fractional-preroll",
        "cook-fractional",
        config=config,
        sequence_start=0,
        scored_load=tuple((0.1, 0.9, 0.3, 0.7)[index % 4] for index in range(30)),
        pre_roll_load=(0.4,) * 5,
    )
    segment = replace(segment, pre_roll_duration_s=(16.002, 20.0, 20.0, 20.0, 20.0))

    masks, ceiling = fitting._fit_support(_job((segment,), config))

    assert float(np.sum(segment.scored_duration_s[masks[0]])) == 600.0
    assert 3.0 * ceiling <= float(np.sum(segment.pre_roll_duration_s))


def test_candidate_and_incumbent_metrics_use_one_common_conservative_mask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incumbent = _config(theta=40.0)
    candidate = _config(theta=25.0)
    segment = _segment(
        "common-mask",
        "cook-common-mask",
        config=candidate,
        sequence_start=0,
        scored_load=(0.1, 0.8, 0.2, 0.7, 0.3, 0.6, 0.4, 0.9),
        pre_roll_load=(),
        initial_load=0.5,
        errors_c=(100.0, -100.0, 80.0, -80.0, 60.0, -60.0, 2.0, -3.0),
    )
    _pin_optimizer(monkeypatch, candidate, candidate)

    result = fit_segmented_grey(_job((segment,), incumbent))

    assert isinstance(result, GreyFitSuccess)
    conservative = _oracle_effective_mask(segment, incumbent.theta) & _oracle_effective_mask(segment, candidate.theta)
    assert tuple(result.effective_masks[0]) == tuple(conservative)
    candidate_error = (_oracle_prediction(segment, candidate) - segment.scored_temperature_c)[conservative]
    incumbent_error = (_oracle_prediction(segment, incumbent) - segment.scored_temperature_c)[conservative]
    assert result.metrics.pooled.sample_count == result.incumbent_metrics.pooled.sample_count == 2
    assert result.metrics.pooled.rmse_c == pytest.approx(float(np.sqrt(np.mean(candidate_error**2))), abs=0.03)
    assert result.incumbent_metrics.pooled.rmse_c == pytest.approx(
        float(np.sqrt(np.mean(incumbent_error**2))), abs=0.03
    )


def test_candidate_theta_controls_the_exact_pooled_duration_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incumbent = _config(theta=50.0)
    candidate = _config(theta=55.0)
    loads = tuple((0.1, 0.9, 0.3, 0.7)[index % 4] for index in range(38))
    segment = _segment(
        "candidate-theta",
        "cook-candidate-theta",
        config=candidate,
        sequence_start=0,
        scored_load=loads,
        pre_roll_load=(),
        initial_load=0.4,
    )
    monkeypatch.setattr(fitting, "_score_identifiability", lambda _columns, _masks: 0.8)

    longer_warmup = fitting.compare_segmented_grey(
        _job((segment,), incumbent), candidate=candidate, incumbent=incumbent
    )

    assert longer_warmup.metrics.pooled.sample_count == 29
    assert longer_warmup.rejection_reasons == ("minimum-effective-duration",)

    exact_segment = _segment(
        "exact-theta",
        "cook-exact-theta",
        config=incumbent,
        sequence_start=0,
        scored_load=loads,
        pre_roll_load=(),
        initial_load=0.4,
    )

    exact_boundary = fitting.compare_segmented_grey(
        _job((exact_segment,), incumbent), candidate=incumbent, incumbent=incumbent
    )

    assert exact_boundary.metrics.pooled.sample_count == 30
    assert exact_boundary.rejection_reasons == ()


def test_pooled_segment_and_cook_metrics_and_excitation_are_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(theta=25.0)
    first = _segment(
        "metric-a1",
        "cook-a",
        config=config,
        sequence_start=0,
        scored_load=(0.1, 0.2, 0.1, 0.4),
        pre_roll_load=(0.2, 0.2, 0.2),
        errors_c=(1.0, -2.0, 3.0, -4.0),
    )
    second = _segment(
        "metric-a2",
        "cook-a",
        config=config,
        sequence_start=10,
        scored_load=(0.5, 0.5, 0.8),
        pre_roll_load=(0.5, 0.5, 0.5),
        errors_c=(-1.5, 2.5, -3.5),
    )
    third = _segment(
        "metric-b1",
        "cook-b",
        config=config,
        sequence_start=20,
        scored_load=(0.9, 0.3, 0.6),
        pre_roll_load=(0.3, 0.3, 0.3),
        errors_c=(0.25, -0.75, 1.25),
        calibration_origin=True,
    )
    segments = (first, second, third)
    _pin_optimizer(monkeypatch, config, config)

    result = fit_segmented_grey(_job(segments, config))

    assert isinstance(result, GreyFitSuccess)
    all_errors = []
    all_temperatures = []
    all_loads = []
    for segment in segments:
        mask = _oracle_effective_mask(segment, config.theta)
        errors = (_oracle_prediction(segment, config) - segment.scored_temperature_c)[mask]
        temperatures = np.asarray(segment.scored_temperature_c)[mask]
        loads = np.asarray(segment.scored_load)[mask]
        _assert_metric(
            _metric(result.metrics.by_segment, "segment_id", segment.segment_id),
            _manual_metric(errors, temperatures, loads),
        )
        all_errors.append(errors)
        all_temperatures.append(temperatures)
        all_loads.append(loads)
    _assert_metric(
        result.metrics.pooled,
        _manual_metric(np.concatenate(all_errors), np.concatenate(all_temperatures), np.concatenate(all_loads)),
    )
    cook_a_errors = np.concatenate(all_errors[:2])
    cook_a_temperatures = np.concatenate(all_temperatures[:2])
    cook_a_loads = np.concatenate(all_loads[:2])
    _assert_metric(
        _metric(result.metrics.by_cook, "cook_id", "cook-a"),
        _manual_metric(cook_a_errors, cook_a_temperatures, cook_a_loads),
    )
    _assert_metric(
        _metric(result.metrics.by_cook, "cook_id", "cook-b"),
        _manual_metric(all_errors[2], all_temperatures[2], all_loads[2]),
    )


def test_aggregate_600_seconds_can_build_without_one_supported_cook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(theta=25.0)
    first = _segment(
        "aggregate-first",
        "cook-short-first",
        config=config,
        sequence_start=0,
        scored_load=tuple((0.1, 0.9, 0.3, 0.7)[index % 4] for index in range(15)),
        pre_roll_load=(0.4,) * 4,
        anchor_c=70.0,
    )
    second = _segment(
        "aggregate-second",
        "cook-short-second",
        config=config,
        sequence_start=30,
        scored_load=tuple((0.9, 0.2, 0.8, 0.1)[index % 4] for index in range(15)),
        pre_roll_load=(0.4,) * 4,
        anchor_c=130.0,
    )
    monkeypatch.setattr(fitting, "_score_identifiability", lambda _columns, _masks: 0.8)
    _pin_optimizer(monkeypatch, config, config)

    result = fit_segmented_grey(_job((first, second), config))

    assert isinstance(result, GreyFitSuccess)
    assert result.metrics.pooled.sample_count == 30
    assert not any(metric.supports_regression_gate for metric in result.metrics.by_cook)
    assert result.rejection_reasons == ()


@pytest.mark.parametrize(
    ("duration_s", "expected_reasons"),
    (
        (599.999, ("minimum-effective-duration",)),
        (600.0, ()),
    ),
)
def test_pooled_duration_gate_has_exact_600_second_boundary(
    monkeypatch: pytest.MonkeyPatch,
    duration_s: float,
    expected_reasons: tuple[str, ...],
) -> None:
    config = _config(theta=25.0)
    first = _segment(
        "boundary-first",
        "cook-boundary-first",
        config=config,
        sequence_start=0,
        scored_load=tuple((0.1, 0.9, 0.3, 0.7)[index % 4] for index in range(15)),
        pre_roll_load=(0.4,) * 4,
        anchor_c=70.0,
    )
    second = _segment(
        "boundary-second",
        "cook-boundary-second",
        config=config,
        sequence_start=30,
        scored_load=tuple((0.9, 0.2, 0.8, 0.1)[index % 4] for index in range(15)),
        pre_roll_load=(0.4,) * 4,
        anchor_c=130.0,
    )
    measured_duration = fitting._metric_duration_s

    def exact_duration(metric: Any) -> float:
        if metric.segment_id is None and metric.cook_id is None:
            return duration_s
        return measured_duration(metric)

    monkeypatch.setattr(fitting, "_metric_duration_s", exact_duration)
    monkeypatch.setattr(fitting, "_score_identifiability", lambda _columns, _masks: 0.8)
    _pin_optimizer(monkeypatch, config, config)

    result = fit_segmented_grey(_job((first, second), config))

    assert isinstance(result, GreyFitSuccess)
    assert result.rejection_reasons == expected_reasons


@pytest.mark.parametrize(
    ("case", "loads", "temperatures", "identifiability", "expected_reason"),
    (
        (
            "excitation",
            (0.4,) * 30,
            tuple(75.0 + index * 0.5 for index in range(30)),
            0.8,
            "insufficient-excitation",
        ),
        (
            "coverage",
            tuple((0.1, 0.9, 0.3, 0.7)[index % 4] for index in range(30)),
            tuple(75.0 + index * 0.1 for index in range(30)),
            0.8,
            "insufficient-coverage",
        ),
        (
            "identifiability",
            tuple((0.1, 0.9, 0.3, 0.7)[index % 4] for index in range(30)),
            tuple(75.0 + index * 0.5 for index in range(30)),
            0.49,
            "identifiability",
        ),
    ),
)
def test_pooled_evidence_gates_remain_strict(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    loads: tuple[float, ...],
    temperatures: tuple[float, ...],
    identifiability: float,
    expected_reason: str,
) -> None:
    config = _config(theta=25.0)
    generated = _segment(
        f"pooled-{case}",
        f"cook-pooled-{case}",
        config=config,
        sequence_start=0,
        scored_load=loads,
        pre_roll_load=(0.4,) * 4,
    )
    segment = replace(generated, scored_temperature_c=temperatures)
    monkeypatch.setattr(fitting, "_score_identifiability", lambda _columns, _masks: identifiability)
    _pin_optimizer(monkeypatch, config, config)

    result = fit_segmented_grey(_job((segment,), config))

    assert isinstance(result, GreyFitSuccess)
    assert result.rejection_reasons == (expected_reason,)


def test_pooled_regression_blocks_when_no_cook_is_individually_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incumbent = _config(C_c=900.0, K_Q=420.0, theta=25.0)
    candidate = _config(C_c=1500.0, K_Q=260.0, theta=25.0)
    first = _segment(
        "pooled-regression-first",
        "cook-short-first",
        config=incumbent,
        sequence_start=0,
        scored_load=tuple((0.1, 0.9, 0.3, 0.7)[index % 4] for index in range(15)),
        pre_roll_load=(0.4,) * 4,
        anchor_c=70.0,
    )
    second = _segment(
        "pooled-regression-second",
        "cook-short-second",
        config=incumbent,
        sequence_start=30,
        scored_load=tuple((0.9, 0.2, 0.8, 0.1)[index % 4] for index in range(15)),
        pre_roll_load=(0.4,) * 4,
        anchor_c=130.0,
    )
    monkeypatch.setattr(fitting, "_score_identifiability", lambda _columns, _masks: 0.8)
    _pin_optimizer(monkeypatch, candidate, candidate)

    result = fit_segmented_grey(_job((first, second), incumbent))

    assert isinstance(result, GreyFitSuccess)
    assert not any(metric.supports_regression_gate for metric in result.metrics.by_cook)
    assert result.metrics.pooled.rmse_c > result.incumbent_metrics.pooled.rmse_c
    assert result.rejection_reasons == ("pooled-regression",)


def test_supported_600_second_cook_still_vetoes_pooled_improvement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incumbent = _config(C_c=900.0, K_Q=420.0, theta=25.0)
    candidate = _config(C_c=1200.0, K_Q=420.0, theta=25.0)
    supported_source = _segment(
        "supported",
        "cook-supported",
        config=incumbent,
        sequence_start=0,
        scored_load=tuple((0.0, 0.5, 1.0, 0.5)[index % 4] for index in range(30)),
        pre_roll_load=(0.4,) * 4,
        anchor_c=20.0,
    )
    incumbent_prediction = _oracle_prediction(supported_source, incumbent)
    candidate_prediction = _oracle_prediction(supported_source, candidate)
    supported = replace(
        supported_source,
        scored_temperature_c=tuple(incumbent_prediction + 0.49 * (candidate_prediction - incumbent_prediction)),
    )
    short = _segment(
        "short",
        "cook-short",
        config=candidate,
        sequence_start=40,
        scored_load=tuple((1.0, 0.2, 0.8, 0.1)[index % 4] for index in range(29)),
        pre_roll_load=(0.4,) * 4,
        anchor_c=200.0,
    )
    monkeypatch.setattr(fitting, "_score_identifiability", lambda _columns, _masks: 0.8)
    _pin_optimizer(monkeypatch, candidate, candidate)

    result = fit_segmented_grey(_job((supported, short), incumbent))

    assert isinstance(result, GreyFitSuccess)
    supported_metric = _metric(result.metrics.by_cook, "cook_id", "cook-supported")
    short_metric = _metric(result.metrics.by_cook, "cook_id", "cook-short")
    assert supported_metric.supports_regression_gate is True
    assert short_metric.supports_regression_gate is False
    assert (
        supported_metric.rmse_c
        > _metric(
            result.incumbent_metrics.by_cook,
            "cook_id",
            "cook-supported",
        ).rmse_c
    )
    assert result.metrics.pooled.rmse_c <= result.incumbent_metrics.pooled.rmse_c
    assert result.rejection_reasons == ("per-cook-regression:cook-supported",)


def test_each_structurally_supported_cook_must_be_individually_identifiable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(theta=25.0)
    loads = tuple((0.1, 0.9, 0.3, 0.7)[index % 4] for index in range(120))
    good = _segment(
        "identifiable",
        "cook-identifiable",
        config=config,
        sequence_start=0,
        scored_load=loads,
        pre_roll_load=(0.4,) * 4,
    )
    rank_deficient = _segment(
        "rank-deficient",
        "cook-rank-deficient",
        config=config,
        sequence_start=200,
        scored_load=loads,
        pre_roll_load=(0.4,) * 4,
    )

    def scoped_identifiability(
        columns: tuple[tuple[np.ndarray, ...], ...],
        masks: tuple[np.ndarray, ...],
    ) -> float:
        del columns
        active_cooks = {
            segment.cook_id for segment, mask in zip((good, rank_deficient), masks, strict=True) if np.any(mask)
        }
        return 0.0 if active_cooks == {"cook-rank-deficient"} else 0.8

    monkeypatch.setattr(fitting, "_score_identifiability", scoped_identifiability)
    _pin_optimizer(monkeypatch, config, config)
    result = fit_segmented_grey(_job((good, rank_deficient), config))

    assert isinstance(result, GreyFitSuccess)
    assert result.identifiability == pytest.approx(0.8)
    identified = _metric(result.metrics.by_cook, "cook_id", "cook-identifiable")
    deficient = _metric(result.metrics.by_cook, "cook_id", "cook-rank-deficient")
    assert identified.identifiability == pytest.approx(0.8)
    assert identified.supports_regression_gate is True
    assert deficient.identifiability == pytest.approx(0.0)
    assert deficient.supports_regression_gate is False
    assert result.rejection_reasons == ()


def test_stacked_independent_jacobians_drive_identifiability(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(theta=30.0)
    first = _segment(
        "jacobian-a",
        "cook-jacobian",
        config=config,
        sequence_start=0,
        scored_load=(0.1, 0.1, 0.9, 0.9, 0.2, 0.2),
        pre_roll_load=(0.1, 0.1, 0.1),
        anchor_c=60.0,
    )
    second = _segment(
        "jacobian-b",
        "cook-jacobian",
        config=config,
        sequence_start=20,
        scored_load=(0.8, 0.3, 0.8, 0.3, 0.8, 0.3),
        pre_roll_load=(0.8, 0.8, 0.8),
        anchor_c=150.0,
        ambient_c=(10.0, 12.0, 14.0, 16.0, 18.0, 20.0),
    )
    _pin_optimizer(monkeypatch, config, config)

    result = fit_segmented_grey(_job((first, second), config))

    assert isinstance(result, GreyFitSuccess)
    rows = []
    for segment in (first, second):
        mask = _oracle_effective_mask(segment, config.theta)
        columns = []
        for key in FITTED_PARAMETERS:
            base = getattr(config, key)
            upper = replace(config, **{key: base * math.exp(_IDENT_STEP)})
            lower = replace(config, **{key: base * math.exp(-_IDENT_STEP)})
            columns.append(
                (_oracle_prediction(segment, upper) - _oracle_prediction(segment, lower)) / (2 * _IDENT_STEP)
            )
        rows.append(np.column_stack(columns)[mask])
    stacked = np.vstack(rows) / math.sqrt(sum(len(row) for row in rows))
    expected = float(np.linalg.svd(stacked, compute_uv=False)[-1])
    assert result.identifiability == pytest.approx(expected, rel=2e-3, abs=2e-4)


def test_near_bound_identifiability_probes_remain_inside_fit_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[tuple[float, float, float]] = []
    real_simulate = fitting._simulate_segments

    def recording_simulate(
        job: GreyFitJob,
        parameters: tuple[float, float, float],
    ) -> tuple[np.ndarray, ...] | None:
        recorded.append(parameters)
        return real_simulate(job, parameters)

    monkeypatch.setattr(fitting, "_simulate_segments", recording_simulate)
    for suffix, theta in (
        ("lower", FIT_VALUE_BOUNDS["theta"][0] * math.exp(_IDENT_STEP / 2.0)),
        ("upper", FIT_VALUE_BOUNDS["theta"][1] * math.exp(-_IDENT_STEP / 2.0)),
    ):
        config = _config(theta=theta)
        segment = _segment(
            f"near-{suffix}",
            f"cook-{suffix}",
            config=config,
            sequence_start=0,
            scored_load=(0.1, 0.8, 0.2, 0.7, 0.3, 0.6),
            pre_roll_load=(0.4,) * (180 if suffix == "upper" else 6),
        )
        _pin_optimizer(monkeypatch, config, config)
        result = fit_segmented_grey(_job((segment,), config))
        assert isinstance(result, GreyFitSuccess)

    for parameters in recorded:
        for key, value in zip(FITTED_PARAMETERS, parameters, strict=True):
            lower, upper = FIT_VALUE_BOUNDS[key]
            assert lower <= value <= upper


def test_incompatible_or_malformed_segment_fails_before_compatible_rows_are_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(theta=25.0)
    valid = _segment(
        "valid",
        "cook-valid",
        config=config,
        sequence_start=0,
        scored_load=(0.1, 0.8, 0.2),
    )
    with pytest.raises(ValueError, match="same length"):
        replace(valid, scored_ambient_c=(20.0, 20.0))
    incompatible = replace(valid, segment_id="incompatible", fit_partition_digest="9" * 64)
    calls = _pin_optimizer(monkeypatch, config, config)
    corpus = _corpus((valid, incompatible))

    with pytest.raises(ValueError, match="fit partition"):
        GreyFitJob(
            request=_request(0, 2, corpus),
            corpus=corpus,
            segments=(valid, incompatible),
            config=config,
        )
    assert calls == []
    assert np.array_equal(valid.scored_temperature_c, _oracle_prediction(valid, config))


def test_scored_rows_require_nominal_cadence_but_partial_preroll_is_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(theta=25.0)
    valid = _segment(
        "cadence",
        "cook-cadence",
        config=config,
        sequence_start=0,
        scored_load=(0.1, 0.8, 0.2, 0.7, 0.3, 0.6),
        pre_roll_load=(0.4,) * 4,
    )
    with pytest.raises(ValueError, match="nominal 20-second cadence"):
        replace(valid, scored_duration_s=(5.0,) * len(valid.scored_load))

    partial_preroll = replace(
        valid,
        pre_roll_duration_s=(5.0, 15.0, 20.0, 20.0),
    )
    _pin_optimizer(monkeypatch, config, config)
    result = fit_segmented_grey(_job((partial_preroll,), config))

    assert isinstance(result, GreyFitSuccess)
    assert partial_preroll.pre_roll_duration_s.tolist() == [5.0, 15.0, 20.0, 20.0]


def _metric_payload(metric: Any) -> dict[str, Any]:
    return {
        "sample_count": metric.sample_count,
        "rmse_c": metric.rmse_c,
        "bias_c": metric.bias_c,
        "error_band_c": list(metric.error_band_c),
        "max_error_c": metric.max_error_c,
        "input_excitation": metric.input_excitation,
        "input_levels": metric.input_levels,
        "identifiability_row_count": metric.identifiability_row_count,
        "temperature_span_c": metric.temperature_span_c,
        "identifiability": metric.identifiability,
    }


def _independent_result_digest(result: GreyFitSuccess, corpus: FitCorpusIdentity) -> str:
    payload = {
        "request_id": result.request.request_id,
        "corpus_digest": corpus.corpus_digest,
        "candidate": {key: getattr(result.config, key) for key in FITTED_PARAMETERS},
        "effective_masks": [[bool(value) for value in mask] for mask in result.effective_masks],
        "pooled": _metric_payload(result.metrics.pooled),
        "segments": [
            {"segment_id": metric.segment_id, **_metric_payload(metric)} for metric in result.metrics.by_segment
        ],
        "cooks": [{"cook_id": metric.cook_id, **_metric_payload(metric)} for metric in result.metrics.by_cook],
        "identifiability": result.identifiability,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _warming_replay_job(config: GreyBoxMPCConfig) -> GreyFitJob:
    usable = _segment(
        "replay-usable",
        "cook-replay-usable",
        config=config,
        sequence_start=0,
        scored_load=tuple((0.1, 0.9, 0.3, 0.7)[index % 4] for index in range(30)),
        pre_roll_load=(0.4,) * 4,
        anchor_c=70.0,
    )
    warming = _segment(
        "replay-warming",
        "cook-replay-warming",
        config=config,
        sequence_start=40,
        scored_load=(0.2, 0.8, 0.4),
        pre_roll_load=(),
        initial_load=0.2,
        anchor_c=130.0,
    )
    return _job((usable, warming), config)


def test_success_constructor_rejects_mismatched_warmup_exclusions_and_mask_cardinality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(theta=25.0)
    job = _warming_replay_job(config)
    monkeypatch.setattr(fitting, "_score_identifiability", lambda _columns, _masks: 0.8)
    _pin_optimizer(monkeypatch, config, config)
    result = fit_segmented_grey(job)
    assert isinstance(result, GreyFitSuccess)

    with pytest.raises(ValueError, match="warmup exclusions must exactly match all-false effective masks"):
        replace(result, warmup_excluded_segment_ids=())
    with pytest.raises(ValueError, match="effective mask count must equal corpus slice count"):
        replace(
            result,
            effective_masks=result.effective_masks[:-1],
            warmup_excluded_segment_ids=(),
        )


@pytest.mark.parametrize("length_delta", (-1, 1), ids=("truncated", "extended"))
def test_success_constructor_rejects_inner_mask_cardinality_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    length_delta: int,
) -> None:
    config = _config(theta=25.0)
    job = _warming_replay_job(config)
    monkeypatch.setattr(fitting, "_score_identifiability", lambda _columns, _masks: 0.8)
    _pin_optimizer(monkeypatch, config, config)
    result = fit_segmented_grey(job)
    assert isinstance(result, GreyFitSuccess)
    usable_mask = tuple(bool(value) for value in result.effective_masks[0])
    malformed_usable_mask = usable_mask[:-1] if length_delta < 0 else (*usable_mask, True)

    with pytest.raises(ValueError, match="effective mask length must equal corpus slice scored count"):
        replace(
            result,
            effective_masks=(malformed_usable_mask, result.effective_masks[1]),
        )


def test_warming_result_replay_reproduces_masks_exclusions_metrics_config_and_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(theta=25.0)
    job = _warming_replay_job(config)
    monkeypatch.setattr(fitting, "_score_identifiability", lambda _columns, _masks: 0.8)
    _pin_optimizer(monkeypatch, config, config)
    first = fit_segmented_grey(job)
    _pin_optimizer(monkeypatch, config, config)
    replayed = fit_segmented_grey(job)

    assert isinstance(first, GreyFitSuccess)
    assert isinstance(replayed, GreyFitSuccess)
    independently_excluded = tuple(
        corpus_slice.segment_id
        for corpus_slice, mask in zip(job.request.fit_corpus.slices, replayed.effective_masks, strict=True)
        if not any(bool(value) for value in mask)
    )
    assert replayed.config == first.config == config
    assert all(
        np.array_equal(first_mask, replayed_mask)
        for first_mask, replayed_mask in zip(first.effective_masks, replayed.effective_masks, strict=True)
    )
    assert replayed.warmup_excluded_segment_ids == first.warmup_excluded_segment_ids == independently_excluded
    assert replayed.metrics == first.metrics
    assert replayed.incumbent_metrics == first.incumbent_metrics
    assert replayed.result_digest == first.result_digest
    assert replayed.result_digest == _independent_result_digest(replayed, job.corpus)


def test_supplied_sequence_21_through_140_uses_warm_lineage_not_fabricated_zero_lags_and_pins_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(theta=50.0)
    loads = tuple((0.7, 0.7, 0.2, 0.9, 0.4)[index % 5] for index in range(120))
    segment = _segment(
        "supplied-21-140",
        "cook-supplied",
        config=config,
        sequence_start=21,
        scored_load=loads,
        pre_roll_load=(),
        initial_load=0.7,
        anchor_c=90.0,
    )
    corpus = _corpus((segment,))
    job = GreyFitJob(
        request=_request(21, 140, corpus),
        corpus=corpus,
        segments=(segment,),
        config=config,
    )
    _pin_optimizer(monkeypatch, config, config)

    result = fit_segmented_grey(job)

    assert isinstance(result, GreyFitSuccess)
    mask = _oracle_effective_mask(segment, config.theta)
    assert result.config == config
    assert tuple(result.effective_masks[0]) == (False,) * 8 + (True,) * 112
    assert result.metrics.pooled.sample_count == 112
    assert result.metrics.pooled.rmse_c == pytest.approx(0.0, abs=0.03)
    zero_state = replace(segment, initial_load=0.0)
    fabricated_zero_error = (_oracle_prediction(zero_state, config) - segment.scored_temperature_c)[mask]
    assert float(np.sqrt(np.mean(fabricated_zero_error**2))) > result.metrics.pooled.rmse_c + 0.2
    assert result.result_digest == _independent_result_digest(result, corpus)
    assert len(result.result_digest) == 64


def test_calibration_origin_changes_provenance_but_not_segmented_fit_math(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(theta=25.0)
    passive = _segment(
        "origin",
        "cook-origin",
        config=config,
        sequence_start=0,
        scored_load=(0.1, 0.8, 0.2, 0.7),
        calibration_origin=False,
        errors_c=(1.0, -1.0, 2.0, -2.0),
    )
    calibration = replace(passive, calibration_origin=(True,) * len(passive.scored_load))
    _pin_optimizer(monkeypatch, config, config)
    passive_result = fit_segmented_grey(_job((passive,), config))
    _pin_optimizer(monkeypatch, config, config)
    calibration_result = fit_segmented_grey(_job((calibration,), config))

    assert isinstance(passive_result, GreyFitSuccess)
    assert isinstance(calibration_result, GreyFitSuccess)
    assert calibration_result.config == passive_result.config
    assert calibration_result.metrics == passive_result.metrics


def test_segment_arrays_are_owned_read_only_and_legacy_continuous_job_contract_is_rejected() -> None:
    config = _config(theta=25.0)
    loads = [0.1, 0.8, 0.2]
    segment = _segment(
        "owned",
        "cook-owned",
        config=config,
        sequence_start=0,
        scored_load=tuple(loads),
    )
    loads.clear()

    assert isinstance(segment.scored_load, np.ndarray)
    assert segment.scored_load.tolist() == [0.1, 0.8, 0.2]
    assert segment.scored_load.flags.writeable is False
    with pytest.raises(ValueError):
        segment.scored_load[0] = 1.0
    job = _job((segment,), config)
    with pytest.raises(FrozenInstanceError):
        job.segments = ()  # type: ignore[misc]
    with pytest.raises(TypeError, match="observations"):
        GreyFitJob(request=job.request, observations=(), config=config)  # type: ignore[call-arg]
    assert not hasattr(job, "observations")
