"""Strict contracts for durable cumulative-learning trajectories and lineage."""

import json
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from math import inf, nan
from operator import setitem
from typing import Any

import pytest
from pydantic import ValidationError

from common.learning_trajectory import (
    FitCorpusIdentity,
    FitCorpusSlice,
    FrameDeliveryCertainty,
    FrozenJsonArray,
    FrozenJsonObject,
    HoldEntrySample,
    LearningTrajectoryFrame,
    LearningTrajectorySegment,
    ModelFitLineage,
    TrajectoryBreakReason,
    canonical_trajectory_digest,
    trajectory_json_value,
)


def _digest(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()

_UNSET_BOUNDARY = object()


def _frame(
    sequence: int,
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
    partial: bool = False,
    boundary_reason: object = _UNSET_BOUNDARY,
    **overrides: Any,
) -> LearningTrajectoryFrame:
    start_ms = sequence * 20_000 if start_ms is None else start_ms
    end_ms = start_ms + 20_000 if end_ms is None else end_ms
    duration_seconds = (end_ms - start_ms) / 1_000
    values: dict[str, Any] = {
        "sequence": sequence,
        "monotonic_start_ms": start_ms,
        "monotonic_end_ms": end_ms,
        "wall_start_ms": 1_000_000 + start_ms,
        "wall_end_ms": 1_000_000 + end_ms,
        "chamber_temperature_c": 110.0 + sequence,
        "temperature_sample_monotonic_ms": end_ms,
        "temperature_sample_wall_ms": 1_000_000 + end_ms,
        "temperature_sample_age_ms": 0,
        "temperature_sample_wall_age_ms": 0,
        "temperature_sample_clock_skew_ms": 0,
        "source_temperature_units": "C",
        "settings_revision": 7,
        "probe_valid": True,
        "probe_source": "grill-probe-1",
        "ambient_temperature_c": 24.0,
        "ambient_source": "configured",
        "ambient_uncertainty_c": 1.5,
        "delivered_auger_on_seconds": duration_seconds * 0.4,
        "realized_auger_duty": 0.4,
        "normalized_combustion_load": 0.4,
        "delivered_fan_on_seconds": duration_seconds,
        "fan_duty_integral_seconds": duration_seconds * 0.5,
        "mean_actual_fan_duty": 0.5,
        "auger_delivery_certainty": FrameDeliveryCertainty.EXACT,
        "fan_delivery_certainty": FrameDeliveryCertainty.EXACT,
        "effective_mode": "Hold",
        "recipe_step_id": None,
        "complete": not partial,
        "continuous": True,
        "partial": partial,
        "boundary_reason": (
            TrajectoryBreakReason.LEFT_HOLD
            if partial and boundary_reason is _UNSET_BOUNDARY
            else None if boundary_reason is _UNSET_BOUNDARY else boundary_reason
        ),
    }
    values.update(overrides)
    return LearningTrajectoryFrame(**values)


def _hold_entry(at_ms: int = 40_000) -> HoldEntrySample:
    return HoldEntrySample(
        monotonic_ms=at_ms,
        wall_ms=1_000_000 + at_ms,
        chamber_temperature_c=112.0,
        probe_valid=True,
        probe_source="grill-probe-1",
    )


def _segment(**overrides: Any) -> LearningTrajectorySegment:
    pre_roll_frames = overrides.pop("pre_roll_frames", (_frame(0), _frame(1)))
    scored_hold_frames = overrides.pop("scored_hold_frames", (_frame(2), _frame(3)))
    all_frames = (*pre_roll_frames, *scored_hold_frames)
    if not all_frames:
        raise AssertionError("the test segment requires at least one frame")
    hold_entry = overrides.pop(
        "hold_entry",
        _hold_entry(
            scored_hold_frames[0].monotonic_start_ms
            if scored_hold_frames
            else pre_roll_frames[-1].monotonic_end_ms
        ),
    )
    values: dict[str, Any] = {
        "schema_version": 1,
        "observation_schema_version": 2,
        "segment_id": "segment-1",
        "cook_id": "cook-1",
        "trajectory_session_id": "trajectory-session-1",
        "trace_session_ids": ("trace-session-1",),
        "collection_provenance": {
            "origin": "passive-online",
            "incumbent_generation": 2,
            "candidate_generation": 3,
            "role_generation": 4,
            "C_c": 8_500.0,
            "K_Q": 0.18,
            "theta": 45.0,
        },
        "configuration_provenance": {"controller": "MPC", "revision": 7},
        "cadence_digest": _digest("cadence-20-seconds-v1"),
        "model_structure_digest": _digest("grey-one-zone-erlang-v1"),
        "held_physics_digest": _digest("held-grey-physics-v1"),
        "delay_input_mapping_digest": _digest("normalized-combustion-load-v1"),
        "actuation_mapping_digest": _digest("framed-pulse-v1"),
        "scored_fan_regime_digest": _digest("fan-regime-v1"),
        "ambient_semantics_digest": _digest("ambient-configured-celsius-v1"),
        "pre_roll_frames": pre_roll_frames,
        "hold_entry": hold_entry,
        "scored_hold_frames": scored_hold_frames,
        "generation_audit_ranges": (
            {
                "start_sequence": all_frames[0].sequence,
                "end_sequence": all_frames[-1].sequence,
                "role_generation": 4,
            },
        ),
        "start_monotonic_ms": all_frames[0].monotonic_start_ms,
        "end_monotonic_ms": all_frames[-1].monotonic_end_ms,
        "start_wall_ms": all_frames[0].wall_start_ms,
        "end_wall_ms": all_frames[-1].wall_end_ms,
        "start_sequence": all_frames[0].sequence,
        "end_sequence": all_frames[-1].sequence,
        "pre_roll_end_reason": None,
        "terminal_break_reason": TrajectoryBreakReason.STOP,
        "state": "finalized",
        "source_trace_digest": _digest("source-trace"),
        "source_schema_version": 7,
        "source_row_digest": _digest("source-rows"),
        "build_provenance": {"builder": "trajectory-runtime", "revision": 1},
    }
    values.update(overrides)
    return LearningTrajectorySegment(**values)


def _slice(
    segment_id: str = "segment-1",
    *,
    through_ordinal: int = 3,
    pre_roll_count: int = 2,
    scored_count: int = 2,
    prefix_digest: str | None = None,
) -> FitCorpusSlice:
    return FitCorpusSlice(
        segment_id=segment_id,
        through_ordinal=through_ordinal,
        prefix_digest=prefix_digest or _digest(f"{segment_id}:{through_ordinal}"),
        pre_roll_count=pre_roll_count,
        scored_count=scored_count,
    )


def _corpus_payload(
    *,
    schema_version: int,
    corpus_revision: int,
    fit_partition_digest: str,
    slices: tuple[FitCorpusSlice, ...],
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "corpus_revision": corpus_revision,
        "fit_partition_digest": fit_partition_digest,
        "slices": [
            {
                "segment_id": item.segment_id,
                "through_ordinal": item.through_ordinal,
                "prefix_digest": item.prefix_digest,
                "pre_roll_count": item.pre_roll_count,
                "scored_count": item.scored_count,
            }
            for item in slices
        ],
    }


def _corpus_identity(
    *,
    slices: tuple[FitCorpusSlice, ...] = (_slice(),),
    schema_version: int = 1,
    corpus_revision: int = 4,
    fit_partition_digest: str | None = None,
) -> FitCorpusIdentity:
    partition = fit_partition_digest or _segment().fit_partition_digest
    payload = _corpus_payload(
        schema_version=schema_version,
        corpus_revision=corpus_revision,
        fit_partition_digest=partition,
        slices=slices,
    )
    return FitCorpusIdentity(
        schema_version=schema_version,
        corpus_revision=corpus_revision,
        fit_partition_digest=partition,
        slices=slices,
        corpus_digest=canonical_trajectory_digest(payload),
    )


def test_contract_values_are_frozen_slots_based_and_deeply_owned() -> None:
    frame = _frame(0)
    segment = _segment()
    corpus = _corpus_identity()
    lineage = ModelFitLineage(
        request_id="fit-request-1",
        parent_incumbent_digest=_digest("incumbent"),
        parent_incumbent_generation=2,
        candidate_generation=3,
        fit_corpus=corpus,
        fit_corpus_digest=corpus.corpus_digest,
        trigger_origin="passive-online",
        result_status="succeeded",
        candidate_digest=_digest("candidate"),
    )

    frozen_attributes = (
        (frame, "sequence"),
        (segment, "schema_version"),
        (corpus, "schema_version"),
        (lineage, "candidate_generation"),
    )
    for value, attribute in frozen_attributes:
        assert value.__class__.__slots__
        with pytest.raises(FrozenInstanceError):
            setattr(value, attribute, 99)

    assert isinstance(segment.trace_session_ids, tuple)
    assert isinstance(segment.pre_roll_frames, tuple)
    assert isinstance(segment.scored_hold_frames, tuple)
    assert isinstance(corpus.slices, tuple)
    assert lineage.fit_corpus is corpus


@pytest.mark.parametrize(
    "factory",
    [
        pytest.param(lambda: replace(_frame(0), chamber_temperature_c=nan), id="nan-temperature"),
        pytest.param(lambda: replace(_frame(0), ambient_temperature_c=inf), id="infinite-ambient"),
        pytest.param(lambda: replace(_frame(0), mean_actual_fan_duty=-inf), id="infinite-fan"),
        pytest.param(lambda: replace(_frame(0), realized_auger_duty=1.01), id="auger-duty-bound"),
        pytest.param(
            lambda: replace(_frame(0), delivered_auger_on_seconds=20.001),
            id="delivery-exceeds-frame",
        ),
        pytest.param(lambda: replace(_frame(0), sequence="0"), id="strict-sequence-type"),
        pytest.param(lambda: replace(_frame(0), probe_valid="true"), id="strict-bool-type"),
        pytest.param(
            lambda: replace(_hold_entry(), chamber_temperature_c=nan),
            id="non-finite-hold-anchor",
        ),
        pytest.param(lambda: replace(_slice(), pre_roll_count=-1), id="negative-count"),
    ],
)
def test_contracts_reject_non_finite_out_of_bounds_and_coercible_types(factory) -> None:
    with pytest.raises(ValidationError):
        factory()


def test_frame_requires_twenty_seconds_unless_it_is_a_short_partial_boundary() -> None:
    full = _frame(0)
    partial = _frame(
        0,
        end_ms=10_000,
        partial=True,
        boundary_reason=TrajectoryBreakReason.LEFT_HOLD,
    )

    assert full.monotonic_end_ms - full.monotonic_start_ms == 20_000
    assert full.complete is True
    assert full.partial is False
    assert full.boundary_reason is None
    assert partial.monotonic_end_ms - partial.monotonic_start_ms == 10_000
    assert partial.complete is False
    assert partial.partial is True
    assert partial.boundary_reason is TrajectoryBreakReason.LEFT_HOLD

    with pytest.raises(ValidationError):
        _frame(0, end_ms=10_000)
    with pytest.raises(ValidationError):
        _frame(0, partial=True, boundary_reason=TrajectoryBreakReason.LEFT_HOLD)
    with pytest.raises(ValidationError):
        _frame(0, end_ms=10_000, partial=True, boundary_reason=None)


def test_frame_retains_actual_temperature_timestamp_age_units_and_settings_revision() -> None:
    frame = _frame(
        0,
        temperature_sample_monotonic_ms=19_949,
        temperature_sample_wall_ms=1_019_951,
        temperature_sample_age_ms=51,
        temperature_sample_wall_age_ms=49,
        temperature_sample_clock_skew_ms=-2,
        source_temperature_units="F",
        settings_revision=12,
    )

    assert frame.temperature_sample_monotonic_ms == 19_949
    assert frame.temperature_sample_wall_ms == 1_019_951
    assert frame.temperature_sample_age_ms == 51
    assert frame.temperature_sample_wall_age_ms == 49
    assert frame.temperature_sample_clock_skew_ms == -2
    assert frame.source_temperature_units == "F"
    assert frame.settings_revision == 12

    with pytest.raises(ValidationError, match="sample.*age|age.*sample"):
        _frame(
            0,
            temperature_sample_monotonic_ms=19_949,
            temperature_sample_wall_ms=1_019_949,
            temperature_sample_age_ms=50,
        )
    with pytest.raises(ValidationError, match="sample.*frame|frame.*sample"):
        _frame(
            0,
            temperature_sample_monotonic_ms=20_001,
            temperature_sample_wall_ms=1_020_001,
            temperature_sample_age_ms=0,
        )
    with pytest.raises(ValidationError, match="wall.*age|age.*wall"):
        _frame(
            0,
            temperature_sample_wall_ms=1_019_949,
            temperature_sample_wall_age_ms=50,
        )
    with pytest.raises(ValidationError, match="skew"):
        _frame(
            0,
            temperature_sample_monotonic_ms=19_949,
            temperature_sample_wall_ms=1_019_949,
            temperature_sample_age_ms=51,
            temperature_sample_wall_age_ms=51,
            temperature_sample_clock_skew_ms=1,
        )


def test_hold_entry_retains_first_valid_measurement_inside_first_scored_interval() -> None:
    anchor = HoldEntrySample(
        monotonic_ms=40_025,
        wall_ms=1_040_025,
        chamber_temperature_c=106.5,
        probe_valid=True,
        probe_source="grill-probe-1",
    )

    segment = _segment(hold_entry=anchor)

    assert segment.hold_entry == anchor
    assert segment.hold_entry.monotonic_ms != segment.scored_hold_frames[0].monotonic_start_ms

    with pytest.raises(ValidationError, match="Hold-entry.*interval|interval.*Hold-entry"):
        _segment(hold_entry=replace(anchor, monotonic_ms=39_999, wall_ms=1_039_999))
    with pytest.raises(ValidationError, match="Hold-entry.*sample|sample.*Hold-entry"):
        _segment(hold_entry=replace(anchor, monotonic_ms=60_001, wall_ms=1_060_001))


def test_partial_frame_is_forbidden_in_scored_hold_observations() -> None:
    partial = _frame(
        3,
        end_ms=70_000,
        partial=True,
        boundary_reason=TrajectoryBreakReason.LEFT_HOLD,
    )

    with pytest.raises(ValidationError, match="partial.*scored|scored.*partial"):
        _segment(scored_hold_frames=(_frame(2), partial))

    pre_roll_segment = _segment(
        pre_roll_frames=(_frame(0), _frame(1, end_ms=30_000, partial=True)),
        scored_hold_frames=(_frame(2), _frame(3)),
    )
    assert pre_roll_segment.pre_roll_frames[-1].partial is True


@pytest.mark.parametrize(
    ("pre_roll", "scored"),
    [
        ((_frame(0), _frame(2)), (_frame(3), _frame(4))),
        (
            (_frame(0), _frame(1, start_ms=10_000, end_ms=30_000)),
            (_frame(2), _frame(3)),
        ),
        ((_frame(0), _frame(1)), (_frame(2), _frame(4))),
        (
            (_frame(0), _frame(1)),
            (_frame(2), _frame(3, start_ms=50_000, end_ms=70_000)),
        ),
        ((_frame(0), _frame(1)), (_frame(1), _frame(2))),
        ((_frame(1), _frame(0)), (_frame(2), _frame(3))),
    ],
    ids=(
        "pre-roll-gap",
        "pre-roll-overlap",
        "scored-gap",
        "scored-overlap",
        "pre-roll-scored-overlap",
        "non-increasing-pre-roll",
    ),
)
def test_segment_rejects_non_contiguous_overlapping_or_non_chronological_frames(
    pre_roll: tuple[LearningTrajectoryFrame, ...],
    scored: tuple[LearningTrajectoryFrame, ...],
) -> None:
    with pytest.raises(ValidationError, match="contiguous|overlap|chronolog"):
        _segment(pre_roll_frames=pre_roll, scored_hold_frames=scored)


def test_pre_roll_requires_continuity_and_exact_scalar_delay_input() -> None:
    uncertain_auger = replace(
        _frame(0),
        auger_delivery_certainty=FrameDeliveryCertainty.UNKNOWN,
    )
    discontinuous = replace(_frame(0), continuous=False)

    with pytest.raises(ValidationError, match="pre-roll.*continuous|continuous.*pre-roll"):
        _segment(pre_roll_frames=(discontinuous, _frame(1)))
    with pytest.raises(ValidationError, match="pre-roll.*auger|auger.*pre-roll"):
        _segment(pre_roll_frames=(uncertain_auger, _frame(1)))

    uncertain_fan = replace(
        _frame(0),
        fan_delivery_certainty=FrameDeliveryCertainty.UNKNOWN,
    )
    with pytest.raises(ValidationError, match="pre-roll.*fan|fan.*pre-roll"):
        _segment(pre_roll_frames=(uncertain_fan, _frame(1)))


def test_scored_observations_require_effective_hold_mode() -> None:
    smoke = replace(_frame(2), effective_mode="Smoke")

    with pytest.raises(ValidationError, match="scored.*Hold|Hold.*scored"):
        _segment(scored_hold_frames=(smoke, _frame(3)))


def test_trajectory_break_and_delivery_certainty_enums_have_exact_typed_members() -> None:
    assert {name: member.value for name, member in TrajectoryBreakReason.__members__.items()} == {
        "MANUAL": "manual",
        "LID_OPEN": "lid-open",
        "SAFETY": "safety",
        "RESET": "reset",
        "STOP": "stop",
        "ERROR": "error",
        "PROCESS_RESTART": "process-restart",
        "COOK_ROTATED": "cook-rotated",
        "HISTORY_CLEARED": "history-cleared",
        "PROBE_GAP": "probe-gap",
        "ACTUATION_UNKNOWN": "actuation-unknown",
        "RECORDER_GAP": "recorder-gap",
        "CLOCK_DISCONTINUITY": "clock-discontinuity",
        "UNITS_CHANGED": "units-changed",
        "STRUCTURE_CHANGED": "structure-changed",
        "ACTUATION_MAPPING_CHANGED": "actuation-mapping-changed",
        "FAN_MAPPING_CHANGED": "fan-mapping-changed",
        "AMBIENT_SEMANTICS_CHANGED": "ambient-semantics-changed",
        "MODE_TRANSITION": "mode-transition",
        "LEFT_HOLD": "left-hold",
        "UNCLEAN_RESTART": "unclean-restart",
        "RETENTION_ROLLOVER": "retention-rollover",
    }
    assert {name: member.value for name, member in FrameDeliveryCertainty.__members__.items()} == {
        "EXACT": "exact",
        "UNKNOWN": "unknown",
    }


def test_canonical_digest_is_deterministic_and_hashes_exact_sorted_compact_json() -> None:
    left = {"z": [3, {"b": 2, "a": 1}], "a": "°C"}
    right = {"a": "°C", "z": [3, {"a": 1, "b": 2}]}
    canonical_bytes = json.dumps(
        right,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    assert canonical_trajectory_digest(left) == canonical_trajectory_digest(right)
    assert canonical_trajectory_digest(left) == sha256(canonical_bytes).hexdigest()

    for invalid in ({"value": nan}, {"value": inf}, {1: "non-string key"}, {"value": object()}):
        with pytest.raises((TypeError, ValueError)):
            canonical_trajectory_digest(invalid)


def test_generation_and_learned_free_parameter_changes_do_not_change_fit_partition() -> None:
    original = _segment()
    changed = _segment(
        collection_provenance={
            "origin": "operator-calibration",
            "incumbent_generation": 200,
            "candidate_generation": 300,
            "role_generation": 400,
            "C_c": 12_000.0,
            "K_Q": 0.31,
            "theta": 90.0,
        },
        generation_audit_ranges=(
            {"start_sequence": 0, "end_sequence": 3, "role_generation": 400},
        ),
    )

    assert changed.fit_partition_digest == original.fit_partition_digest


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("schema_version", 2),
        ("held_physics_digest", _digest("held-grey-physics-v2")),
        ("model_structure_digest", _digest("grey-two-zone-v2")),
        ("cadence_digest", _digest("different-cadence-semantics")),
        ("delay_input_mapping_digest", _digest("temperature-error-input-v2")),
        ("actuation_mapping_digest", _digest("pwm-actuation-v2")),
        ("scored_fan_regime_digest", _digest("variable-fan-v2")),
        ("ambient_semantics_digest", _digest("measured-ambient-v2")),
    ],
)
def test_fit_partition_changes_with_schema_physics_structure_cadence_and_input_semantics(
    field: str, changed_value: object
) -> None:
    original = _segment()
    changed = _segment(**{field: changed_value})

    assert changed.fit_partition_digest != original.fit_partition_digest


@pytest.mark.parametrize("noncurrent_schema", [1, 3], ids=["older", "future"])
def test_noncurrent_observation_schema_is_non_scoreable_for_pre_roll_only_segment(
    noncurrent_schema: int,
) -> None:
    smoke_pre_roll = (
        replace(_frame(0), effective_mode="Smoke"),
        replace(_frame(1), effective_mode="Smoke"),
    )
    current = _segment(
        observation_schema_version=2,
        pre_roll_frames=smoke_pre_roll,
        scored_hold_frames=(),
        hold_entry=None,
    )

    assert current.observation_schema_version == 2
    with pytest.raises(ValidationError, match="observation schema.*non-scoreable"):
        _segment(
            observation_schema_version=noncurrent_schema,
            pre_roll_frames=smoke_pre_roll,
            scored_hold_frames=(),
            hold_entry=None,
        )


def test_corpus_slices_are_ordered_immutable_and_validate_counts_ordinals_and_digests() -> None:
    first = _slice("segment-a", through_ordinal=3, pre_roll_count=2, scored_count=2)
    second = _slice("segment-b", through_ordinal=5, pre_roll_count=2, scored_count=4)
    identity = _corpus_identity(slices=(first, second))
    reversed_identity = _corpus_identity(slices=(second, first))

    assert identity.slices == (first, second)
    assert reversed_identity.corpus_digest != identity.corpus_digest
    with pytest.raises(FrozenInstanceError):
        identity.slices = ()

    invalid_slice_factories = (
        lambda: _slice(through_ordinal=-1),
        lambda: _slice(pre_roll_count=-1),
        lambda: _slice(scored_count=-1),
        lambda: _slice(through_ordinal=4, pre_roll_count=2, scored_count=2),
        lambda: _slice(prefix_digest="A" * 64),
    )
    for factory in invalid_slice_factories:
        with pytest.raises(ValidationError):
            factory()

    with pytest.raises(ValidationError):
        FitCorpusIdentity(
            schema_version=1,
            corpus_revision=4,
            fit_partition_digest=identity.fit_partition_digest,
            slices=[first, second],
            corpus_digest=identity.corpus_digest,
        )
    with pytest.raises(ValidationError, match="duplicate|segment"):
        _corpus_identity(slices=(first, first))
    with pytest.raises(ValidationError, match="corpus.*digest|digest.*corpus"):
        replace(identity, corpus_digest="0" * 64)


def test_mutating_content_sources_after_construction_cannot_change_object_or_digest() -> None:
    collection_nested = {"role_generation": 2}
    collection = {"origin": "passive-online", "nested": collection_nested}
    configuration_settings = [{"key": "n_delay", "value": 4}]
    configuration = {"controller": "MPC", "settings": configuration_settings}
    audit_range = {"start_sequence": 0, "end_sequence": 3, "role_generation": 2}
    build_inputs = ["trace", "journal"]
    build = {"builder": "trajectory-runtime", "inputs": build_inputs}
    segment = _segment(
        collection_provenance=collection,
        configuration_provenance=configuration,
        generation_audit_ranges=(audit_range,),
        build_provenance=build,
    )
    original_digest = segment.content_digest

    collection_nested["role_generation"] = 999
    configuration_settings[0]["value"] = 99
    audit_range["end_sequence"] = 999
    build_inputs.append("tampered")

    assert segment.collection_provenance["nested"]["role_generation"] == 2
    assert segment.configuration_provenance["settings"][0]["value"] == 4
    assert segment.generation_audit_ranges[0]["end_sequence"] == 3
    assert segment.build_provenance["inputs"] == ("trace", "journal")
    assert isinstance(segment.collection_provenance, FrozenJsonObject)
    assert isinstance(segment.configuration_provenance["settings"], FrozenJsonArray)
    assert segment.content_digest == original_digest

    nested = segment.collection_provenance["nested"]
    settings = segment.configuration_provenance["settings"]
    with pytest.raises(TypeError):
        setitem(segment.collection_provenance, "tampered", True)
    with pytest.raises(TypeError):
        setitem(nested, "role_generation", 999)
    with pytest.raises(AttributeError):
        settings.append({"key": "tampered", "value": True})
    with pytest.raises(TypeError):
        setitem(segment.generation_audit_ranges[0], "end_sequence", 999)


def test_public_frozen_json_contract_round_trips_nested_values_and_digest() -> None:
    plain = {
        "object": {
            "array": [1, 2.5, True, None, "value"],
            "scalar": "celsius",
        }
    }
    segment = _segment(collection_provenance=plain)
    frozen = segment.collection_provenance

    assert isinstance(frozen, FrozenJsonObject)
    nested = frozen["object"]
    assert isinstance(nested, FrozenJsonObject)
    assert isinstance(nested["array"], FrozenJsonArray)
    assert trajectory_json_value(frozen) == plain
    assert canonical_trajectory_digest(frozen) == canonical_trajectory_digest(plain)
    for scalar in (None, True, 3, 2.5, "value"):
        assert trajectory_json_value(scalar) == scalar



def test_public_frozen_json_constructors_own_validate_and_canonicalize_nested_values() -> None:
    nested_list = [{"z": 3, "array": [1, 2]}]
    nested_object = {"list": nested_list}
    frozen_array = FrozenJsonArray((nested_object,))
    frozen_object = FrozenJsonObject((("z", nested_list), ("a", frozen_array)))
    original = trajectory_json_value(frozen_object)
    original_digest = canonical_trajectory_digest(frozen_object)

    nested_list[0]["z"] = 999
    nested_list[0]["array"].append(3)
    nested_object["extra"] = True

    assert tuple(frozen_object) == ("a", "z")
    assert trajectory_json_value(frozen_object) == original
    assert canonical_trajectory_digest(frozen_object) == original_digest
    assert isinstance(frozen_object["a"], FrozenJsonArray)
    assert isinstance(frozen_object["z"], FrozenJsonArray)

    with pytest.raises(ValueError, match="unique"):
        FrozenJsonObject((("duplicate", 1), ("duplicate", 2)))
    with pytest.raises(ValueError, match="keys.*strings|strings.*keys"):
        FrozenJsonObject(((1, "not-a-string-key"),))
    with pytest.raises(ValueError, match="finite"):
        FrozenJsonArray((nan,))
    with pytest.raises(ValueError, match="JSON"):
        FrozenJsonObject((("unsupported", object()),))


def test_segment_refreezes_existing_public_provenance_instances() -> None:
    mutable_nested = [{"value": 1}]
    bypassed = FrozenJsonObject((("safe", True),))
    object.__setattr__(bypassed, "_items", (("nested", mutable_nested),))
    segment = _segment(collection_provenance=bypassed)
    original_digest = segment.content_digest

    mutable_nested[0]["value"] = 999

    assert segment.collection_provenance is not bypassed
    assert trajectory_json_value(segment.collection_provenance) == {
        "nested": [{"value": 1}]
    }
    assert segment.content_digest == original_digest

def test_hold_entry_matches_both_clocks_and_supports_entry_before_first_score() -> None:
    mismatched_wall_anchor = replace(_hold_entry(), wall_ms=1_040_001)
    with pytest.raises(ValidationError, match="Hold-entry.*wall|wall.*Hold-entry"):
        _segment(hold_entry=mismatched_wall_anchor)
    with pytest.raises(ValidationError, match="scored.*anchor|anchor.*scored"):
        _segment(hold_entry=None)

    smoke_pre_roll = (
        replace(_frame(0), effective_mode="Smoke"),
        replace(_frame(1), effective_mode="Smoke"),
    )
    smoke_only = _segment(
        pre_roll_frames=smoke_pre_roll,
        scored_hold_frames=(),
        hold_entry=None,
    )
    assert smoke_only.hold_entry is None
    assert smoke_only.scored_hold_frames == ()

    hold_entered_finalized = _segment(
        pre_roll_frames=smoke_pre_roll,
        scored_hold_frames=(),
        hold_entry=_hold_entry(),
    )
    hold_entered_open = _segment(
        pre_roll_frames=smoke_pre_roll,
        scored_hold_frames=(),
        hold_entry=_hold_entry(),
        state="open",
        terminal_break_reason=None,
    )
    assert hold_entered_finalized.hold_entry == _hold_entry()
    assert hold_entered_open.hold_entry == _hold_entry()

    with pytest.raises(ValidationError, match="Hold-entry.*monotonic|monotonic.*Hold-entry"):
        _segment(
            pre_roll_frames=smoke_pre_roll,
            scored_hold_frames=(),
            hold_entry=replace(_hold_entry(), monotonic_ms=40_001),
        )
    with pytest.raises(ValidationError, match="Hold-entry.*wall|wall.*Hold-entry"):
        _segment(
            pre_roll_frames=smoke_pre_roll,
            scored_hold_frames=(),
            hold_entry=replace(_hold_entry(), wall_ms=1_040_001),
        )


def test_oversized_metadata_and_provenance_are_rejected() -> None:
    oversized = "x" * 65_536

    with pytest.raises((TypeError, ValueError), match="size|large|metadata|provenance|65536"):
        canonical_trajectory_digest({"metadata": oversized})
    with pytest.raises(ValidationError, match="size|large|metadata|provenance|65536"):
        _segment(collection_provenance={"payload": oversized})
    with pytest.raises(ValidationError, match="size|large|metadata|provenance|65536"):
        _segment(build_provenance={"payload": oversized})
    with pytest.raises(ValidationError, match="size|large|metadata|provenance|65536"):
        _segment(
            generation_audit_ranges=(
                {"payload": "x" * 40_000},
                {"payload": "y" * 40_000},
            )
        )
