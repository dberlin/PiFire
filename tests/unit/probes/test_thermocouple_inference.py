import json
from dataclasses import FrozenInstanceError

import pytest

from probes.thermocouple_health import (
    ThermocoupleEvidence,
    ThermocoupleFault,
    ThermocoupleHealthReport,
    ThermocoupleHealthState,
)
from probes.thermocouple_inference import (
    ThermocoupleExcitationContext,
    ThermocoupleInferencePolicy,
    ThermocoupleJunctionSample,
    ThermocoupleInferenceEngine,
    ThermocoupleWitnessSample,
    fuse_thermocouple_health,
)


def _confirmed_inference(now: float = 10.0) -> ThermocoupleHealthReport:
    return ThermocoupleHealthReport(
        state=ThermocoupleHealthState.CONFIRMED,
        faults=(ThermocoupleFault.MALFUNCTION,),
        evidence=(
            ThermocoupleEvidence.JUNCTION_COLLAPSE,
            ThermocoupleEvidence.EXCITATION_RESPONSE,
        ),
        temperature_valid=False,
        observed_at=now,
        detail={"channel": "slow"},
    )


def test_observed_inferred_primary_is_confirmed_but_remains_numeric():
    inferred = _confirmed_inference()

    fused = fuse_thermocouple_health(
        hardware=None,
        inferred=inferred,
        policy=ThermocoupleInferencePolicy.OBSERVE,
        is_primary=True,
    )

    assert fused.state is ThermocoupleHealthState.CONFIRMED
    assert fused.temperature_valid is True
    assert fused.detail == {
        "channel": "slow",
        "policy": "observe",
        "authority": "notify_only",
        "is_primary": True,
    }
    assert inferred.temperature_valid is False
    assert inferred.detail == {"channel": "slow"}


def test_enforced_inferred_primary_is_invalid_and_stop_authoritative():
    fused = fuse_thermocouple_health(
        hardware=None,
        inferred=_confirmed_inference(),
        policy=ThermocoupleInferencePolicy.ENFORCE,
        is_primary=True,
    )

    assert fused.confirmed
    assert fused.temperature_valid is False
    assert fused.detail["authority"] == "stop"


@pytest.mark.parametrize(
    "policy",
    [ThermocoupleInferencePolicy.OBSERVE, ThermocoupleInferencePolicy.ENFORCE],
)
def test_confirmed_inferred_secondary_is_invalid_and_notify_only(policy):
    fused = fuse_thermocouple_health(
        hardware=None,
        inferred=_confirmed_inference(),
        policy=policy,
        is_primary=False,
    )

    assert fused.confirmed
    assert fused.temperature_valid is False
    assert fused.detail["authority"] == "notify_only"
    assert fused.detail["is_primary"] is False


def test_confirmed_hardware_wins_even_when_inference_is_off():
    hardware = ThermocoupleHealthReport.confirmed_hardware((ThermocoupleFault.OPEN,), now=10.0, status=0x10)

    fused = fuse_thermocouple_health(
        hardware=hardware,
        inferred=None,
        policy=ThermocoupleInferencePolicy.OFF,
        is_primary=True,
    )

    assert fused is not hardware
    assert fused.as_dict()["detail"] == {"status": 0x10, "policy": "off"}


def test_nonconfirmed_inference_remains_valid_and_gains_effective_policy():
    inferred = ThermocoupleHealthReport(
        state=ThermocoupleHealthState.SUSPECTED,
        faults=(ThermocoupleFault.MALFUNCTION,),
        evidence=(ThermocoupleEvidence.JUNCTION_COLLAPSE,),
        observed_at=8.0,
        detail={"metric": 1.0},
    )

    fused = fuse_thermocouple_health(
        hardware=ThermocoupleHealthReport.healthy(8.0),
        inferred=inferred,
        policy=ThermocoupleInferencePolicy.ENFORCE,
        is_primary=True,
    )

    assert fused is not inferred
    assert fused.temperature_valid is True
    assert fused.detail == {"metric": 1.0, "policy": "enforce"}


def test_clean_hardware_does_not_override_confirmed_inference_or_evidence_order():
    inferred = _confirmed_inference()

    fused = fuse_thermocouple_health(
        hardware=ThermocoupleHealthReport.healthy(10.0),
        inferred=inferred,
        policy=ThermocoupleInferencePolicy.ENFORCE,
        is_primary=True,
    )

    assert fused.faults == (ThermocoupleFault.MALFUNCTION,)
    assert fused.evidence == (
        ThermocoupleEvidence.JUNCTION_COLLAPSE,
        ThermocoupleEvidence.EXCITATION_RESPONSE,
    )


def test_off_without_hardware_returns_unmonitored_at_inferred_timestamp():
    fused = fuse_thermocouple_health(
        hardware=None,
        inferred=ThermocoupleHealthReport.healthy(12.0),
        policy=ThermocoupleInferencePolicy.OFF,
        is_primary=True,
    )

    assert fused.state is ThermocoupleHealthState.UNMONITORED
    assert fused.observed_at == 12.0
    assert fused.detail == {"policy": "off"}


def test_immutable_inputs_own_witnesses_and_validate_finite_numbers():
    source_witnesses = [ThermocoupleWitnessSample(("device", "port"), 42.0)]
    context = ThermocoupleExcitationContext(True, 120.0, 0.5, tuple(source_witnesses))
    sample = ThermocoupleJunctionSample(30.0, 25.0)

    source_witnesses.append(ThermocoupleWitnessSample(("other", "port"), 43.0))
    assert context.witnesses == (ThermocoupleWitnessSample(("device", "port"), 42.0),)
    assert json.dumps(
        {
            "hot": sample.hot_c,
            "cold": sample.cold_c,
            "setpoint": context.primary_setpoint_c,
        }
    )
    with pytest.raises(FrozenInstanceError):
        setattr(sample, "hot_c", 31.0)

    invalid_factories = (
        lambda: ThermocoupleJunctionSample(float("nan"), 1.0),
        lambda: ThermocoupleJunctionSample(1.0, float("inf")),
        lambda: ThermocoupleWitnessSample(("device", "port"), float("-inf")),
        lambda: ThermocoupleExcitationContext(True, float("nan"), 0.0, ()),
        lambda: ThermocoupleExcitationContext(True, 100.0, float("inf"), ()),
    )
    for factory in invalid_factories:
        with pytest.raises(ValueError):
            factory()


def _context(
    *,
    active: bool = True,
    setpoint: float = 120.0,
    heat: float = 0.0,
    witnesses: tuple[ThermocoupleWitnessSample, ...] = (),
) -> ThermocoupleExcitationContext:
    return ThermocoupleExcitationContext(active, setpoint, heat, witnesses)


def _sample(hot: float = 30.0, cold: float = 20.0) -> ThermocoupleJunctionSample:
    return ThermocoupleJunctionSample(hot, cold)


def test_one_second_admission_accumulates_heat_without_synthetic_samples():
    engine = ThermocoupleInferenceEngine()

    first = engine.observe(_sample(), _context(heat=0.25), is_primary=False, now=0.0)
    rejected = engine.observe(_sample(hot=31.0), _context(heat=0.5), is_primary=False, now=0.999)
    admitted = engine.observe(_sample(hot=32.0), _context(heat=0.75), is_primary=False, now=1.0)

    assert first.detail["sample_count"] == 1
    assert rejected is first
    assert admitted.detail["sample_count"] == 2
    assert admitted.detail["coverage_seconds"] == 1.0
    assert admitted.detail["heat_on_seconds"] == 1.5


def test_ring_is_fixed_at_301_real_admitted_samples():
    engine = ThermocoupleInferenceEngine()
    reports = []

    for second in range(302):
        reports.append(
            engine.observe(
                _sample(hot=30.0 + second),
                _context(),
                is_primary=False,
                now=float(second),
            )
        )

    assert reports[299].detail["sample_count"] == 300
    assert reports[300].detail["sample_count"] == 301
    assert reports[301].detail["sample_count"] == 301
    assert reports[301].detail["coverage_seconds"] == 300.0
    assert reports[301].detail["hot_span_c"] == 300.0


@pytest.mark.parametrize(
    "coverage, eligible",
    [(239.999, False), (240.0, True), (240.001, True)],
)
def test_slow_window_coverage_boundary_is_inclusive(coverage, eligible):
    engine = ThermocoupleInferenceEngine()
    now = 0.0
    while now + 1.0 < coverage:
        engine.observe(_sample(), _context(active=False), is_primary=False, now=now)
        now += 1.0
    report = engine.observe(_sample(), _context(active=False), is_primary=False, now=coverage)

    assert report.detail["coverage_seconds"] == pytest.approx(coverage)
    assert report.detail["slow_window_eligible"] is eligible


@pytest.mark.parametrize(
    "gap, eligible",
    [(29.999, True), (30.0, True), (30.001, False)],
)
def test_slow_window_maximum_gap_boundary_is_inclusive(gap, eligible):
    engine = ThermocoupleInferenceEngine()
    times = [0.0, gap]
    next_second = gap + 1.0
    while next_second < gap + 240.0:
        times.append(next_second)
        next_second += 1.0
    times.append(gap + 240.0)

    report = engine.current_report()
    for now in times:
        report = engine.observe(_sample(), _context(active=False), is_primary=False, now=now)

    assert report.detail["max_gap_seconds"] == pytest.approx(gap)
    assert report.detail["slow_window_eligible"] is eligible


def test_clock_regression_resets_history_and_admits_new_sample():
    engine = ThermocoupleInferenceEngine()
    engine.observe(_sample(50.0, 20.0), _context(heat=1.0), is_primary=False, now=10.0)
    engine.observe(_sample(49.0, 20.0), _context(heat=1.0), is_primary=False, now=11.0)

    report = engine.observe(_sample(30.0, 20.0), _context(heat=0.25), is_primary=False, now=5.0)

    assert report.state is ThermocoupleHealthState.HEALTHY
    assert report.observed_at == 5.0
    assert report.detail["sample_count"] == 1
    assert report.detail["coverage_seconds"] == 0.0
    assert report.detail["heat_on_seconds"] == 0.25


def test_explicit_reset_returns_to_unmonitored_with_empty_history():
    engine = ThermocoupleInferenceEngine()
    engine.observe(_sample(), _context(), is_primary=False, now=1.0)

    engine.reset()

    assert engine.current_report() == ThermocoupleHealthReport.unmonitored(0.0)
    report = engine.observe(_sample(), _context(), is_primary=False, now=100.0)
    assert report.detail["sample_count"] == 1
    assert report.detail["coverage_seconds"] == 0.0


def test_diagnostics_are_complete_json_safe_and_use_immutable_witness_snapshot():
    engine = ThermocoupleInferenceEngine()
    witnesses = (ThermocoupleWitnessSample(("peer", "P0"), 35.0),)

    engine.observe(
        _sample(30.0, 20.0),
        _context(heat=1.0, witnesses=witnesses),
        is_primary=False,
        now=0.0,
    )
    report = engine.observe(
        _sample(31.0, 21.0),
        _context(
            heat=2.0,
            witnesses=(ThermocoupleWitnessSample(("peer", "P0"), 45.0),),
        ),
        is_primary=False,
        now=1.0,
    )

    assert report.detail == {
        "policy_version": 1,
        "sample_count": 2,
        "coverage_seconds": 1.0,
        "max_gap_seconds": 1.0,
        "hot_span_c": 1.0,
        "cold_span_c": 1.0,
        "delta_span_c": 0.0,
        "collapse_fraction": 0.0,
        "heat_on_seconds": 3.0,
        "witness_source": ("peer", "P0"),
        "witness_rise_c": 10.0,
        "asserted_channels": (),
        "slow_window_eligible": False,
        "fast_path_armed": False,
    }
    json.dumps(report.as_dict())


@pytest.mark.parametrize("now", [float("nan"), float("inf"), float("-inf")])
def test_observe_rejects_nonfinite_clock(now):
    with pytest.raises(ValueError):
        ThermocoupleInferenceEngine().observe(_sample(), _context(), is_primary=False, now=now)


def _slow_report(
    *,
    hot_values: tuple[float, ...],
    cold_values: tuple[float, ...],
    active: bool = True,
    deficit: float = 15.0,
    heat: float = 30.0,
    peer_rise: float | None = 10.0,
) -> ThermocoupleHealthReport:
    assert len(hot_values) == len(cold_values)
    engine = ThermocoupleInferenceEngine()
    final_index = len(hot_values) - 1
    report = engine.current_report()
    for index, (hot_c, cold_c) in enumerate(zip(hot_values, cold_values, strict=True)):
        progress = index / final_index
        witnesses = (
            ()
            if peer_rise is None
            else (
                ThermocoupleWitnessSample(
                    ("peer-device", "P0"),
                    20.0 + peer_rise * progress,
                ),
            )
        )
        report = engine.observe(
            _sample(hot_c, cold_c),
            _context(
                active=active,
                setpoint=hot_values[0] + deficit,
                heat=heat if index == final_index else 0.0,
                witnesses=witnesses,
            ),
            is_primary=False,
            now=240.0 * progress,
        )
    return report


@pytest.mark.parametrize(
    "collapsed_count, expected_state",
    [
        (18, ThermocoupleHealthState.SUSPECTED),
        (19, ThermocoupleHealthState.CONFIRMED),
        (20, ThermocoupleHealthState.CONFIRMED),
    ],
)
def test_slow_collapse_fraction_boundary(collapsed_count, expected_state):
    hot_values = (30.0, 31.5) + (30.0,) * 18
    deltas = (0.001,) * collapsed_count + (1.001,) * (20 - collapsed_count)
    cold_values = tuple(hot - delta for hot, delta in zip(hot_values, deltas, strict=True))

    report = _slow_report(hot_values=hot_values, cold_values=cold_values)

    assert report.detail["collapse_fraction"] == pytest.approx(collapsed_count / 20)
    assert report.state is expected_state


@pytest.mark.parametrize(
    "delta, expected_state",
    [
        (0.999, ThermocoupleHealthState.CONFIRMED),
        (1.0, ThermocoupleHealthState.CONFIRMED),
        (1.001, ThermocoupleHealthState.SUSPECTED),
    ],
)
def test_slow_absolute_delta_boundary(delta, expected_state):
    hot_values = (30.0, 31.5) + (30.0,) * 18
    cold_values = tuple(hot - delta for hot in hot_values)

    report = _slow_report(hot_values=hot_values, cold_values=cold_values)

    assert report.state is expected_state


@pytest.mark.parametrize(
    "span, expected_state",
    [
        (0.999, ThermocoupleHealthState.CONFIRMED),
        (1.0, ThermocoupleHealthState.CONFIRMED),
        (1.001, ThermocoupleHealthState.SUSPECTED),
    ],
)
def test_slow_delta_span_boundary(span, expected_state):
    hot_values = (30.0, 31.5) + (30.0,) * 18
    deltas = (0.0,) * 19 + (span,)
    cold_values = tuple(hot - delta for hot, delta in zip(hot_values, deltas, strict=True))

    report = _slow_report(hot_values=hot_values, cold_values=cold_values)

    assert report.detail["delta_span_c"] == pytest.approx(span)
    assert report.state is expected_state


@pytest.mark.parametrize(
    "span, expected_state",
    [
        (0.999, ThermocoupleHealthState.CONFIRMED),
        (1.0, ThermocoupleHealthState.CONFIRMED),
        (1.001, ThermocoupleHealthState.SUSPECTED),
    ],
)
def test_slow_stuck_hot_span_boundary(span, expected_state):
    hot_values = (30.0, 30.0 + span) + (30.0,) * 18
    cold_values = tuple(hot - 5.0 for hot in hot_values)

    report = _slow_report(hot_values=hot_values, cold_values=cold_values)

    assert report.detail["hot_span_c"] == pytest.approx(span)
    assert report.state is expected_state


@pytest.mark.parametrize(
    "deficit, expected_state",
    [
        (14.999, ThermocoupleHealthState.HEALTHY),
        (15.0, ThermocoupleHealthState.CONFIRMED),
        (15.001, ThermocoupleHealthState.CONFIRMED),
    ],
)
def test_slow_setpoint_deficit_boundary(deficit, expected_state):
    report = _slow_report(
        hot_values=(30.0,) * 20,
        cold_values=(30.0,) * 20,
        deficit=deficit,
    )

    assert report.state is expected_state


@pytest.mark.parametrize(
    "heat, expected_state",
    [
        (29.999, ThermocoupleHealthState.HEALTHY),
        (30.0, ThermocoupleHealthState.CONFIRMED),
        (30.001, ThermocoupleHealthState.CONFIRMED),
    ],
)
def test_slow_delivered_heat_boundary(heat, expected_state):
    report = _slow_report(
        hot_values=(30.0,) * 20,
        cold_values=(30.0,) * 20,
        heat=heat,
    )

    assert report.state is expected_state


@pytest.mark.parametrize(
    "peer_rise, expected_state",
    [
        (9.999, ThermocoupleHealthState.HEALTHY),
        (10.0, ThermocoupleHealthState.CONFIRMED),
        (10.001, ThermocoupleHealthState.CONFIRMED),
    ],
)
def test_slow_peer_witness_rise_boundary(peer_rise, expected_state):
    report = _slow_report(
        hot_values=(30.0,) * 20,
        cold_values=(30.0,) * 20,
        peer_rise=peer_rise,
    )

    assert report.state is expected_state


@pytest.mark.parametrize(
    "cold_rise, expected_state",
    [
        (2.999, ThermocoupleHealthState.HEALTHY),
        (3.0, ThermocoupleHealthState.CONFIRMED),
        (3.001, ThermocoupleHealthState.CONFIRMED),
    ],
)
def test_slow_cold_witness_rise_boundary(cold_rise, expected_state):
    cold_values = tuple(30.0 + cold_rise * index / 19 for index in range(20))

    report = _slow_report(
        hot_values=cold_values,
        cold_values=cold_values,
        peer_rise=None,
    )

    assert report.state is expected_state
    if cold_rise >= 3.0:
        assert report.detail["witness_source"] == ("cold_junction", "internal")


@pytest.mark.parametrize(
    "candidate_rise, expected_state",
    [
        (2.999, ThermocoupleHealthState.CONFIRMED),
        (3.0, ThermocoupleHealthState.SUSPECTED),
    ],
)
def test_slow_peer_candidate_response_is_strict(candidate_rise, expected_state):
    hot_values = tuple(30.0 + candidate_rise * index / 19 for index in range(20))
    cold_values = tuple(29.5 + candidate_rise * index / 19 for index in range(20))

    report = _slow_report(hot_values=hot_values, cold_values=cold_values)

    assert report.state is expected_state


@pytest.mark.parametrize("delta_growth, asserted", [(1.999, True), (2.0, False)])
def test_slow_cold_delta_growth_response_is_strict(delta_growth, asserted):
    cold_values = tuple(30.0 + 3.0 * index / 19 for index in range(20))
    hot_values = tuple(30.0 + (3.0 + delta_growth) * index / 19 for index in range(20))

    report = _slow_report(
        hot_values=hot_values,
        cold_values=cold_values,
        peer_rise=None,
    )

    asserted_channels = report.detail["asserted_channels"]
    assert isinstance(asserted_channels, tuple)
    assert (ThermocoupleEvidence.EXCITATION_RESPONSE.value in asserted_channels) is asserted


def test_valid_ramp_stays_healthy():
    hot_values = tuple(30.0 + 20.0 * index / 19 for index in range(20))
    cold_values = tuple(20.0 + 3.0 * index / 19 for index in range(20))

    report = _slow_report(hot_values=hot_values, cold_values=cold_values)

    assert report.state is ThermocoupleHealthState.HEALTHY
    assert report.detail["asserted_channels"] == ()


@pytest.mark.parametrize(
    "active, deficit",
    [(False, 15.0), (True, 14.999)],
)
def test_diagnostic_collapse_outside_identification_opportunity_stays_healthy(active, deficit):
    report = _slow_report(
        hot_values=(30.0,) * 20,
        cold_values=(30.0,) * 20,
        active=active,
        deficit=deficit,
    )

    assert report.detail["collapse_fraction"] == 1.0
    assert report.state is ThermocoupleHealthState.HEALTHY
    assert report.detail["asserted_channels"] == ()


def test_commanded_heat_without_warming_witness_is_insufficient_evidence():
    report = _slow_report(
        hot_values=(30.0,) * 20,
        cold_values=(30.0,) * 20,
        peer_rise=None,
    )

    assert report.state is ThermocoupleHealthState.HEALTHY
    assert report.detail["asserted_channels"] == ()


def test_peer_witness_wins_over_qualifying_cold_fallback():
    cold_values = tuple(30.0 + 3.0 * index / 19 for index in range(20))

    report = _slow_report(
        hot_values=cold_values,
        cold_values=cold_values,
        peer_rise=10.0,
    )

    assert report.state is ThermocoupleHealthState.SUSPECTED
    assert report.detail["witness_source"] == ("peer-device", "P0")
    assert report.detail["witness_rise_c"] == 10.0


def _fast_sequence(
    *,
    prior_delta: float = 15.0,
    fall: float = 20.0,
    event_interval: float = 10.0,
    active: bool = True,
    event_delta: float = 0.0,
    subsequent_collapsed: int = 5,
    is_primary: bool = False,
) -> list[ThermocoupleHealthReport]:
    engine = ThermocoupleInferenceEngine()
    prior_hot = 50.0
    reports = [
        engine.observe(
            _sample(prior_hot, prior_hot - prior_delta),
            _context(active=active),
            is_primary=is_primary,
            now=0.0,
        )
    ]
    event_hot = prior_hot - fall
    reports.append(
        engine.observe(
            _sample(event_hot, event_hot - event_delta),
            _context(active=active),
            is_primary=is_primary,
            now=event_interval,
        )
    )
    for offset in range(1, subsequent_collapsed + 1):
        reports.append(
            engine.observe(
                _sample(event_hot, event_hot),
                _context(active=active),
                is_primary=is_primary,
                now=event_interval + offset,
            )
        )
    return reports


@pytest.mark.parametrize(
    "prior_delta, armed",
    [(14.999, False), (15.0, True)],
)
def test_fast_path_prior_separation_boundary_is_inclusive(prior_delta, armed):
    reports = _fast_sequence(prior_delta=prior_delta, subsequent_collapsed=0)

    assert reports[-1].detail["fast_path_armed"] is armed


@pytest.mark.parametrize(
    "fall, armed",
    [(19.999, False), (20.0, True)],
)
def test_fast_path_fall_boundary_is_inclusive(fall, armed):
    reports = _fast_sequence(fall=fall, subsequent_collapsed=0)

    assert reports[-1].detail["fast_path_armed"] is armed


@pytest.mark.parametrize(
    "event_interval, armed",
    [(9.999, True), (10.0, True), (10.001, False)],
)
def test_fast_path_event_interval_boundary_is_inclusive(event_interval, armed):
    reports = _fast_sequence(
        event_interval=event_interval,
        subsequent_collapsed=0,
    )

    assert reports[-1].detail["fast_path_armed"] is armed


def test_fast_path_requires_exactly_five_strictly_subsequent_collapsed_samples():
    reports = _fast_sequence()

    assert reports[1].state is ThermocoupleHealthState.HEALTHY
    assert reports[1].detail["fast_path_armed"] is True
    assert all(not report.confirmed for report in reports[2:6])
    assert reports[6].confirmed
    assert reports[6].faults == (ThermocoupleFault.MALFUNCTION,)
    assert reports[6].evidence == (
        ThermocoupleEvidence.IMPLAUSIBLE_STEP,
        ThermocoupleEvidence.JUNCTION_COLLAPSE,
    )
    assert reports[6].detail["asserted_channels"] == (
        "implausible-step",
        "junction-collapse",
    )


def _fast_sequence_with_followup_times(
    followup_times: tuple[float, ...],
) -> list[ThermocoupleHealthReport]:
    engine = ThermocoupleInferenceEngine()
    reports = [
        engine.observe(
            _sample(50.0, 30.0),
            _context(),
            is_primary=False,
            now=0.0,
        ),
        engine.observe(
            _sample(30.0, 30.0),
            _context(),
            is_primary=False,
            now=1.0,
        ),
    ]
    reports.extend(
        engine.observe(
            _sample(30.0, 30.0),
            _context(),
            is_primary=False,
            now=now,
        )
        for now in followup_times
    )
    return reports


def test_fast_path_allows_realistic_one_point_zero_five_second_admission_cadence():
    reports = _fast_sequence_with_followup_times(
        (2.05, 3.10, 4.15, 5.20, 6.25),
    )

    assert all(not report.confirmed for report in reports[:-1])
    assert reports[-1].confirmed
    assert reports[-1].evidence == (
        ThermocoupleEvidence.IMPLAUSIBLE_STEP,
        ThermocoupleEvidence.JUNCTION_COLLAPSE,
    )


@pytest.mark.parametrize(
    ("fifth_followup_at", "confirmed"),
    [(7.0, True), (7.001, False)],
)
def test_fast_path_six_second_expiry_boundary_is_inclusive(
    fifth_followup_at,
    confirmed,
):
    reports = _fast_sequence_with_followup_times(
        (2.0, 3.0, 4.0, 5.0, fifth_followup_at),
    )

    assert reports[-1].confirmed is confirmed
    assert reports[-1].detail["fast_path_armed"] is False


def test_fast_path_does_not_arm_during_inactive_cook():
    reports = _fast_sequence(active=False)

    assert all(report.state is ThermocoupleHealthState.HEALTHY for report in reports)
    assert all(report.detail["fast_path_armed"] is False for report in reports)


def test_fast_path_does_not_arm_for_steady_maintenance():
    reports = _fast_sequence(fall=0.0)

    assert all(not report.confirmed for report in reports)
    assert reports[1].detail["fast_path_armed"] is False


def test_lid_open_drop_without_junction_collapse_does_not_arm():
    reports = _fast_sequence(event_delta=5.0, subsequent_collapsed=0)

    assert reports[-1].state is ThermocoupleHealthState.HEALTHY
    assert reports[-1].detail["fast_path_armed"] is False


def test_noncollapsed_followup_cancels_fast_path_arm():
    engine = ThermocoupleInferenceEngine()
    engine.observe(_sample(50.0, 30.0), _context(), is_primary=False, now=0.0)
    armed = engine.observe(_sample(30.0, 30.0), _context(), is_primary=False, now=1.0)
    cancelled = engine.observe(_sample(30.0, 25.0), _context(), is_primary=False, now=2.0)

    assert armed.detail["fast_path_armed"] is True
    assert cancelled.detail["fast_path_armed"] is False
    assert cancelled.state is ThermocoupleHealthState.HEALTHY


def test_clock_regression_clears_fast_path_arm():
    engine = ThermocoupleInferenceEngine()
    engine.observe(_sample(50.0, 30.0), _context(), is_primary=False, now=9.0)
    armed = engine.observe(_sample(30.0, 30.0), _context(), is_primary=False, now=10.0)

    reset_report = engine.observe(_sample(30.0, 30.0), _context(), is_primary=False, now=5.0)

    assert armed.detail["fast_path_armed"] is True
    assert reset_report.detail["fast_path_armed"] is False
    assert reset_report.detail["sample_count"] == 1


def _confirmed_fast_engine(*, is_primary: bool) -> tuple[ThermocoupleInferenceEngine, float]:
    engine = ThermocoupleInferenceEngine()
    engine.observe(_sample(50.0, 30.0), _context(), is_primary=is_primary, now=0.0)
    engine.observe(_sample(30.0, 30.0), _context(), is_primary=is_primary, now=1.0)
    report = engine.current_report()
    for now in range(2, 7):
        report = engine.observe(
            _sample(30.0, 30.0),
            _context(),
            is_primary=is_primary,
            now=float(now),
        )
    assert report.confirmed
    return engine, 6.0


def _populate_slow_engine(
    engine: ThermocoupleInferenceEngine,
    *,
    hot_values: tuple[float, ...],
    cold_values: tuple[float, ...],
    is_primary: bool,
) -> ThermocoupleHealthReport:
    report = engine.current_report()
    for index, (hot_c, cold_c) in enumerate(zip(hot_values, cold_values, strict=True)):
        progress = index / (len(hot_values) - 1)
        report = engine.observe(
            _sample(hot_c, cold_c),
            _context(
                heat=30.0 if index == len(hot_values) - 1 else 0.0,
                witnesses=(
                    ThermocoupleWitnessSample(
                        ("peer-device", "P0"),
                        20.0 + 10.0 * progress,
                    ),
                ),
            ),
            is_primary=is_primary,
            now=240.0 * progress,
        )
    return report


def _feed_clean_slow_ramp(
    engine: ThermocoupleInferenceEngine,
    *,
    first_now: int,
    last_now: int,
    is_primary: bool = False,
) -> ThermocoupleHealthReport:
    report = engine.current_report()
    for now in range(first_now, last_now + 1):
        offset = now - 241
        report = engine.observe(
            _sample(40.0 + 0.1 * offset, 20.0 + 0.01 * offset),
            _context(
                setpoint=120.0,
                heat=0.1,
                witnesses=(
                    ThermocoupleWitnessSample(
                        ("peer-device", "P0"),
                        20.0 + 0.1 * offset,
                    ),
                ),
            ),
            is_primary=is_primary,
            now=float(now),
        )
    return report


def _feed_delayed_witness_recovery(
    engine: ThermocoupleInferenceEngine,
    *,
    first_now: int,
    last_now: int,
) -> ThermocoupleHealthReport:
    report = engine.current_report()
    for now in range(first_now, last_now + 1):
        offset = now - 241
        report = engine.observe(
            _sample(40.0 + 0.1 * offset, 20.0 + 0.01 * offset),
            _context(
                setpoint=120.0,
                heat=0.1,
                witnesses=(
                    ThermocoupleWitnessSample(
                        ("peer-device", "P0"),
                        20.0 if now < 541 else 30.0,
                    ),
                ),
            ),
            is_primary=False,
            now=float(now),
        )
    return report


def test_primary_fast_confirmation_remains_latched_until_reset():
    engine, confirmed_at = _confirmed_fast_engine(is_primary=True)

    report = engine.current_report()
    for offset in range(1, 62):
        report = engine.observe(
            _sample(40.0, 30.0),
            _context(active=False),
            is_primary=True,
            now=confirmed_at + offset,
        )

    assert report.confirmed
    engine.reset()
    assert engine.current_report().state is ThermocoupleHealthState.UNMONITORED


def test_slow_suspected_clears_only_on_later_eligible_clean_window():
    engine = ThermocoupleInferenceEngine()
    hot_values = (30.0, 31.5) + (30.0,) * 18
    suspected = _populate_slow_engine(
        engine,
        hot_values=hot_values,
        cold_values=tuple(hot - 1.001 for hot in hot_values),
        is_primary=False,
    )
    ineligible = engine.observe(
        _sample(40.0, 20.0),
        _context(active=False),
        is_primary=False,
        now=300.0,
    )
    clean = _feed_clean_slow_ramp(
        engine,
        first_now=301,
        last_now=601,
    )

    assert suspected.state is ThermocoupleHealthState.SUSPECTED
    assert ineligible.state is ThermocoupleHealthState.SUSPECTED
    assert clean.state is ThermocoupleHealthState.HEALTHY


def test_slow_secondary_confirmation_recovers_at_exactly_sixty_clean_eligible_seconds():
    engine = ThermocoupleInferenceEngine()
    confirmed = _populate_slow_engine(
        engine,
        hot_values=(30.0,) * 20,
        cold_values=(30.0,) * 20,
        is_primary=False,
    )
    first_clean = _feed_delayed_witness_recovery(engine, first_now=241, last_now=541)
    before_boundary = _feed_delayed_witness_recovery(engine, first_now=542, last_now=600)
    boundary = _feed_delayed_witness_recovery(engine, first_now=601, last_now=601)

    assert confirmed.confirmed
    assert first_clean.confirmed
    assert before_boundary.confirmed
    assert boundary.state is ThermocoupleHealthState.HEALTHY


def test_slow_secondary_recovery_resets_on_anomaly_and_ineligible_gap():
    engine = ThermocoupleInferenceEngine()
    _populate_slow_engine(
        engine,
        hot_values=(30.0,) * 20,
        cold_values=(30.0,) * 20,
        is_primary=False,
    )
    _feed_delayed_witness_recovery(engine, first_now=241, last_now=541)
    anomalous = engine.observe(
        _sample(50.0, 50.0),
        _context(),
        is_primary=False,
        now=542.0,
    )
    ineligible = engine.observe(
        _sample(60.0, 30.0),
        _context(),
        is_primary=False,
        now=573.0,
    )

    assert anomalous.confirmed
    assert ineligible.confirmed


def test_fast_secondary_confirmation_recovers_at_exactly_sixty_clean_seconds():
    engine, confirmed_at = _confirmed_fast_engine(is_primary=False)
    first_clean = engine.observe(
        _sample(40.0, 30.0),
        _context(active=False),
        is_primary=False,
        now=confirmed_at + 1.0,
    )
    before_boundary = engine.current_report()
    for offset in range(2, 61):
        before_boundary = engine.observe(
            _sample(40.0, 30.0),
            _context(active=False),
            is_primary=False,
            now=confirmed_at + offset,
        )
    boundary = engine.observe(
        _sample(40.0, 30.0),
        _context(active=False),
        is_primary=False,
        now=confirmed_at + 61.0,
    )

    assert first_clean.confirmed
    assert before_boundary.confirmed
    assert boundary.state is ThermocoupleHealthState.HEALTHY


def _feed_slow_anomaly_during_fast_confirmation(
    engine: ThermocoupleInferenceEngine,
    *,
    last_now: int,
    suspected: bool,
) -> ThermocoupleHealthReport:
    report = engine.current_report()
    for now in range(7, last_now + 1):
        hot_c = 30.0 if not suspected or now % 40 < 20 else 32.0
        cold_c = hot_c if now in (60, 120, 180, 240, 300) else 20.0
        report = engine.observe(
            _sample(hot_c, cold_c),
            _context(
                heat=0.1,
                witnesses=(
                    ThermocoupleWitnessSample(
                        ("peer-device", "P0"),
                        20.0 + (now - 7) / 30.0,
                    ),
                ),
            ),
            is_primary=False,
            now=float(now),
        )
    return report


@pytest.mark.parametrize(
    ("suspected", "expected_channels"),
    [
        (
            False,
            (
                ThermocoupleEvidence.STUCK_RESPONSE.value,
                ThermocoupleEvidence.EXCITATION_RESPONSE.value,
            ),
        ),
        (True, (ThermocoupleEvidence.EXCITATION_RESPONSE.value,)),
    ],
)
def test_fast_secondary_recovery_is_blocked_by_active_slow_anomaly(
    suspected,
    expected_channels,
):
    engine, _ = _confirmed_fast_engine(is_primary=False)

    report = _feed_slow_anomaly_during_fast_confirmation(
        engine,
        last_now=361,
        suspected=suspected,
    )

    assert report.confirmed
    assert report.detail["asserted_channels"] == expected_channels


def test_fast_secondary_requires_fresh_full_recovery_after_slow_anomaly_clears():
    engine, _ = _confirmed_fast_engine(is_primary=False)
    anomalous = _feed_slow_anomaly_during_fast_confirmation(
        engine,
        last_now=361,
        suspected=False,
    )

    recovery_reports = []
    for now in range(362, 423):
        recovery_reports.append(
            engine.observe(
                _sample(35.0 + 0.1 * (now - 362), 20.0),
                _context(
                    heat=0.1,
                    witnesses=(
                        ThermocoupleWitnessSample(
                            ("peer-device", "P0"),
                            20.0 + (now - 7) / 30.0,
                        ),
                    ),
                ),
                is_primary=False,
                now=float(now),
            )
        )

    assert anomalous.confirmed
    assert recovery_reports[0].confirmed
    assert recovery_reports[-2].confirmed
    assert recovery_reports[-1].state is ThermocoupleHealthState.HEALTHY


def test_fast_secondary_recovery_restarts_after_collapsed_observation():
    engine, confirmed_at = _confirmed_fast_engine(is_primary=False)
    for offset in range(1, 32):
        engine.observe(
            _sample(40.0, 30.0),
            _context(active=False),
            is_primary=False,
            now=confirmed_at + offset,
        )
    engine.observe(
        _sample(30.0, 30.0),
        _context(active=False),
        is_primary=False,
        now=confirmed_at + 32.0,
    )
    before_boundary = engine.current_report()
    for offset in range(33, 93):
        before_boundary = engine.observe(
            _sample(40.0, 30.0),
            _context(active=False),
            is_primary=False,
            now=confirmed_at + offset,
        )
    boundary = engine.observe(
        _sample(40.0, 30.0),
        _context(active=False),
        is_primary=False,
        now=confirmed_at + 93.0,
    )

    assert before_boundary.confirmed
    assert boundary.state is ThermocoupleHealthState.HEALTHY


def test_elapsed_time_without_contiguous_clean_observations_never_clears_confirmation():
    engine, confirmed_at = _confirmed_fast_engine(is_primary=False)

    report = engine.observe(
        _sample(40.0, 30.0),
        _context(active=False),
        is_primary=False,
        now=confirmed_at + 1000.0,
    )

    assert report.confirmed
