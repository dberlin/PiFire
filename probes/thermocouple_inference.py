from collections import deque
from dataclasses import dataclass, replace
from enum import StrEnum
from math import isfinite

from probes.thermocouple_health import (
    ThermocoupleEvidence,
    ThermocoupleFault,
    ThermocoupleHealthReport,
    ThermocoupleHealthState,
)


class ThermocoupleInferencePolicy(StrEnum):
    OFF = "off"
    OBSERVE = "observe"
    ENFORCE = "enforce"


def _require_finite(name: str, value: float) -> None:
    if not isfinite(value):
        raise ValueError(f"{name} must be finite")


@dataclass(frozen=True, slots=True)
class ThermocoupleJunctionSample:
    hot_c: float
    cold_c: float

    def __post_init__(self) -> None:
        _require_finite("hot_c", self.hot_c)
        _require_finite("cold_c", self.cold_c)


@dataclass(frozen=True, slots=True)
class ThermocoupleWitnessSample:
    source: tuple[str, str]
    temperature_c: float

    def __post_init__(self) -> None:
        if len(self.source) != 2 or not all(isinstance(part, str) for part in self.source):
            raise ValueError("source must be a device and physical-port pair")
        object.__setattr__(self, "source", tuple(self.source))
        _require_finite("temperature_c", self.temperature_c)


@dataclass(frozen=True, slots=True)
class ThermocoupleExcitationContext:
    active_cook: bool
    primary_setpoint_c: float
    delivered_heat_on_s: float
    witnesses: tuple[ThermocoupleWitnessSample, ...] = ()

    def __post_init__(self) -> None:
        _require_finite("primary_setpoint_c", self.primary_setpoint_c)
        _require_finite("delivered_heat_on_s", self.delivered_heat_on_s)
        if self.delivered_heat_on_s < 0.0:
            raise ValueError("delivered_heat_on_s cannot be negative")
        object.__setattr__(self, "witnesses", tuple(self.witnesses))


@dataclass(frozen=True, slots=True)
class _AcceptedSample:
    now: float
    hot_c: float
    cold_c: float
    delta_c: float
    delivered_heat_on_s: float
    active_cook: bool
    primary_setpoint_c: float
    witnesses: tuple[ThermocoupleWitnessSample, ...]


@dataclass(frozen=True, slots=True)
class _SlowEvaluation:
    identification_eligible: bool
    state: ThermocoupleHealthState
    evidence: tuple[ThermocoupleEvidence, ...]
    witness_source: tuple[str, str] | None
    witness_rise_c: float | None


@dataclass(frozen=True, slots=True)
class _FastArm:
    event_at: float
    collapsed_samples: int


class ThermocoupleInferenceEngine:
    __slots__ = (
        "_history",
        "_last_admitted_at",
        "_last_observed_at",
        "_pending_heat_on_s",
        "_fast_arm",
        "_confirmation_path",
        "_primary_latched",
        "_recovery_since",
        "_recovery_last_at",
        "_report",
    )

    def __init__(self) -> None:
        self._history: deque[_AcceptedSample] = deque(maxlen=301)
        self._last_admitted_at: float | None = None
        self._fast_arm: _FastArm | None = None
        self._confirmation_path: str | None = None
        self._primary_latched = False
        self._recovery_since: float | None = None
        self._recovery_last_at: float | None = None
        self._last_observed_at: float | None = None
        self._pending_heat_on_s = 0.0
        self._report = ThermocoupleHealthReport.unmonitored(0.0)

    def current_report(self) -> ThermocoupleHealthReport:
        return self._report

    def reset(self) -> None:
        self._history.clear()
        self._last_admitted_at = None
        self._fast_arm = None
        self._confirmation_path = None
        self._primary_latched = False
        self._recovery_since = None
        self._recovery_last_at = None
        self._last_observed_at = None
        self._pending_heat_on_s = 0.0
        self._report = ThermocoupleHealthReport.unmonitored(0.0)

    def observe(
        self,
        sample: ThermocoupleJunctionSample,
        excitation: ThermocoupleExcitationContext,
        is_primary: bool,
        now: float,
    ) -> ThermocoupleHealthReport:
        _require_finite("now", now)
        if self._last_observed_at is not None and now < self._last_observed_at:
            self.reset()
        self._last_observed_at = now
        self._pending_heat_on_s += excitation.delivered_heat_on_s
        if self._last_admitted_at is not None and now - self._last_admitted_at < 1.0:
            return self._report

        self._history.append(
            _AcceptedSample(
                now=now,
                hot_c=sample.hot_c,
                cold_c=sample.cold_c,
                delta_c=sample.hot_c - sample.cold_c,
                delivered_heat_on_s=self._pending_heat_on_s,
                active_cook=excitation.active_cook,
                primary_setpoint_c=excitation.primary_setpoint_c,
                witnesses=tuple(excitation.witnesses),
            )
        )
        self._pending_heat_on_s = 0.0
        self._last_admitted_at = now
        history = tuple(self._history)
        self._fast_arm, fast_evidence = _advance_fast_path(self._fast_arm, history)
        slow = _evaluate_slow_channels(history)
        asserted_evidence = fast_evidence or slow.evidence
        detail = self._diagnostics(history, slow, asserted_evidence)
        if self._report.confirmed:
            self._report = self._advance_confirmed_recovery(
                history[-1],
                slow,
                fast_evidence,
                is_primary,
                now,
                detail,
            )
        elif fast_evidence:
            self._report = self._begin_confirmation(
                evidence=fast_evidence,
                path="fast",
                is_primary=is_primary,
                now=now,
                detail=detail,
            )
        elif slow.identification_eligible:
            if slow.state is ThermocoupleHealthState.CONFIRMED:
                self._report = self._begin_confirmation(
                    evidence=slow.evidence,
                    path="slow",
                    is_primary=is_primary,
                    now=now,
                    detail=detail,
                )
            else:
                self._report = ThermocoupleHealthReport(
                    state=slow.state,
                    faults=(
                        (ThermocoupleFault.MALFUNCTION,) if slow.state is ThermocoupleHealthState.SUSPECTED else ()
                    ),
                    evidence=slow.evidence,
                    observed_at=now,
                    detail=detail,
                )
        elif self._report.state is ThermocoupleHealthState.UNMONITORED:
            self._report = ThermocoupleHealthReport(
                state=ThermocoupleHealthState.HEALTHY,
                observed_at=now,
                detail=detail,
            )
        else:
            self._report = replace(self._report, observed_at=now, detail=detail)
        return self._report

    def _begin_confirmation(
        self,
        *,
        evidence: tuple[ThermocoupleEvidence, ...],
        path: str,
        is_primary: bool,
        now: float,
        detail: dict[str, object],
    ) -> ThermocoupleHealthReport:
        self._confirmation_path = path
        self._primary_latched = is_primary
        self._recovery_since = None
        self._recovery_last_at = None
        return ThermocoupleHealthReport(
            state=ThermocoupleHealthState.CONFIRMED,
            faults=(ThermocoupleFault.MALFUNCTION,),
            evidence=evidence,
            temperature_valid=False,
            observed_at=now,
            detail=detail,
        )

    def _advance_confirmed_recovery(
        self,
        current: _AcceptedSample,
        slow: _SlowEvaluation,
        fast_evidence: tuple[ThermocoupleEvidence, ...],
        is_primary: bool,
        now: float,
        detail: dict[str, object],
    ) -> ThermocoupleHealthReport:
        self._primary_latched = self._primary_latched or is_primary
        if self._primary_latched:
            self._reset_recovery()
            return replace(self._report, observed_at=now, detail=detail)

        if self._confirmation_path == "slow":
            clean = (
                slow.identification_eligible
                and slow.state is ThermocoupleHealthState.HEALTHY
                and not fast_evidence
                and self._fast_arm is None
            )
        else:
            clean = (
                not fast_evidence
                and self._fast_arm is None
                and abs(current.delta_c) > 1.0
                and (not slow.identification_eligible or slow.state is ThermocoupleHealthState.HEALTHY)
            )
        if not clean:
            self._reset_recovery()
            return replace(self._report, observed_at=now, detail=detail)

        if self._recovery_since is None or self._recovery_last_at is None or now - self._recovery_last_at > 30.0:
            self._recovery_since = now
        self._recovery_last_at = now
        if now - self._recovery_since < 60.0:
            return replace(self._report, observed_at=now, detail=detail)

        self._confirmation_path = None
        self._recovery_since = None
        self._recovery_last_at = None
        return ThermocoupleHealthReport(
            state=ThermocoupleHealthState.HEALTHY,
            observed_at=now,
            detail=detail,
        )

    def _reset_recovery(self) -> None:
        self._recovery_since = None
        self._recovery_last_at = None

    def _diagnostics(
        self,
        history: tuple[_AcceptedSample, ...],
        slow: _SlowEvaluation,
        asserted_evidence: tuple[ThermocoupleEvidence, ...],
    ) -> dict[str, object]:
        first = history[0]
        last = history[-1]
        gaps = tuple(current.now - previous.now for previous, current in zip(history, history[1:], strict=False))
        hot_values = tuple(entry.hot_c for entry in history)
        cold_values = tuple(entry.cold_c for entry in history)
        delta_values = tuple(entry.delta_c for entry in history)
        witness_source, witness_rise_c = _greatest_witness_rise(first, last)
        if slow.witness_source is not None:
            witness_source = slow.witness_source
            witness_rise_c = slow.witness_rise_c
        coverage_seconds = last.now - first.now
        max_gap_seconds = max(gaps, default=0.0)
        return {
            "policy_version": 1,
            "sample_count": len(history),
            "coverage_seconds": coverage_seconds,
            "max_gap_seconds": max_gap_seconds,
            "hot_span_c": max(hot_values) - min(hot_values),
            "cold_span_c": max(cold_values) - min(cold_values),
            "delta_span_c": max(delta_values) - min(delta_values),
            "collapse_fraction": sum(abs(value) <= 1.0 for value in delta_values) / len(delta_values),
            "heat_on_seconds": sum(entry.delivered_heat_on_s for entry in history),
            "witness_source": list(witness_source) if witness_source is not None else None,
            "witness_rise_c": witness_rise_c,
            "asserted_channels": [item.value for item in asserted_evidence],
            "slow_window_eligible": coverage_seconds >= 240.0 and max_gap_seconds <= 30.0,
            "fast_path_armed": self._fast_arm is not None,
        }


def _advance_fast_path(
    arm: _FastArm | None,
    history: tuple[_AcceptedSample, ...],
) -> tuple[_FastArm | None, tuple[ThermocoupleEvidence, ...]]:
    current = history[-1]
    if arm is not None:
        if not current.active_cook or abs(current.delta_c) > 1.0 or current.now - arm.event_at > 6.0:
            arm = None
        else:
            collapsed_samples = arm.collapsed_samples + 1
            if collapsed_samples == 5:
                return (
                    None,
                    (
                        ThermocoupleEvidence.IMPLAUSIBLE_STEP,
                        ThermocoupleEvidence.JUNCTION_COLLAPSE,
                    ),
                )
            return (
                _FastArm(
                    event_at=arm.event_at,
                    collapsed_samples=collapsed_samples,
                ),
                (),
            )

    if len(history) < 2:
        return None, ()
    previous = history[-2]
    event = (
        current.active_cook
        and previous.delta_c >= 15.0
        and current.now - previous.now <= 10.0
        and previous.hot_c - current.hot_c >= 20.0
        and abs(current.delta_c) <= 1.0
    )
    if not event:
        return None, ()
    return (
        _FastArm(
            event_at=current.now,
            collapsed_samples=0,
        ),
        (),
    )


def _evaluate_slow_channels(
    history: tuple[_AcceptedSample, ...],
) -> _SlowEvaluation:
    first = history[0]
    last = history[-1]
    gaps = (current.now - previous.now for previous, current in zip(history, history[1:], strict=False))
    temporal_eligible = last.now - first.now >= 240.0 and max(gaps, default=0.0) <= 30.0
    if not temporal_eligible:
        return _SlowEvaluation(
            identification_eligible=False,
            state=ThermocoupleHealthState.HEALTHY,
            evidence=(),
            witness_source=None,
            witness_rise_c=None,
        )

    peer_source, peer_rise_c = _greatest_witness_rise(first, last)
    cold_rise_c = last.cold_c - first.cold_c
    if peer_rise_c is not None and peer_rise_c >= 10.0:
        witness_source = peer_source
        witness_rise_c = peer_rise_c
        peer_witness = True
    elif cold_rise_c >= 3.0:
        witness_source = ("cold_junction", "internal")
        witness_rise_c = cold_rise_c
        peer_witness = False
    else:
        witness_source = None
        witness_rise_c = None
        peer_witness = False

    heat_on_seconds = sum(entry.delivered_heat_on_s for entry in history)
    identification_eligible = (
        all(entry.active_cook for entry in history)
        and first.primary_setpoint_c - first.hot_c >= 15.0
        and heat_on_seconds >= 30.0
        and witness_source is not None
    )
    if not identification_eligible:
        return _SlowEvaluation(
            identification_eligible=False,
            state=ThermocoupleHealthState.HEALTHY,
            evidence=(),
            witness_source=witness_source,
            witness_rise_c=witness_rise_c,
        )

    hot_values = tuple(entry.hot_c for entry in history)
    delta_values = tuple(entry.delta_c for entry in history)
    collapse_fraction = sum(abs(value) <= 1.0 for value in delta_values) / len(delta_values)
    junction_collapse = collapse_fraction >= 0.95 and max(delta_values) - min(delta_values) <= 1.0
    stuck_response = max(hot_values) - min(hot_values) <= 1.0
    candidate_hot_rise_c = last.hot_c - first.hot_c
    excitation_response = candidate_hot_rise_c < 3.0 if peer_witness else last.delta_c - first.delta_c < 2.0

    evidence = (
        *((ThermocoupleEvidence.JUNCTION_COLLAPSE,) if junction_collapse else ()),
        *((ThermocoupleEvidence.STUCK_RESPONSE,) if stuck_response else ()),
        *((ThermocoupleEvidence.EXCITATION_RESPONSE,) if excitation_response else ()),
    )
    internal_anomaly = junction_collapse or stuck_response
    if internal_anomaly and excitation_response:
        state = ThermocoupleHealthState.CONFIRMED
    elif internal_anomaly or excitation_response:
        state = ThermocoupleHealthState.SUSPECTED
    else:
        state = ThermocoupleHealthState.HEALTHY
    return _SlowEvaluation(
        identification_eligible=True,
        state=state,
        evidence=evidence,
        witness_source=witness_source,
        witness_rise_c=witness_rise_c,
    )


def _greatest_witness_rise(
    first: _AcceptedSample,
    last: _AcceptedSample,
) -> tuple[tuple[str, str] | None, float | None]:
    start_by_source = {witness.source: witness.temperature_c for witness in first.witnesses}
    rises = [
        (witness.temperature_c - start_by_source[witness.source], witness.source)
        for witness in last.witnesses
        if witness.source in start_by_source
    ]
    if not rises:
        return None, None
    rise, source = min(rises, key=lambda item: (-item[0], item[1]))
    return source, rise


def fuse_thermocouple_health(
    hardware: ThermocoupleHealthReport | None,
    inferred: ThermocoupleHealthReport | None,
    policy: ThermocoupleInferencePolicy,
    is_primary: bool,
) -> ThermocoupleHealthReport:
    if hardware is not None and hardware.confirmed:
        fused = hardware
    elif policy is ThermocoupleInferencePolicy.OFF or inferred is None:
        if hardware is not None:
            fused = hardware
        else:
            observed_at = inferred.observed_at if inferred is not None else 0.0
            fused = ThermocoupleHealthReport.unmonitored(observed_at)
    elif not inferred.confirmed:
        fused = inferred
    else:
        detail = dict(inferred.detail)
        detail.update(
            {
                "policy": policy.value,
                "authority": (
                    "stop" if policy is ThermocoupleInferencePolicy.ENFORCE and is_primary else "notify_only"
                ),
                "is_primary": is_primary,
            }
        )
        fused = replace(
            inferred,
            temperature_valid=(policy is ThermocoupleInferencePolicy.OBSERVE and is_primary),
            detail=detail,
        )

    detail = dict(fused.detail)
    detail["policy"] = policy.value
    return replace(fused, detail=detail)
