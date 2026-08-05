"""Passive online update gating, replay balancing, and model promotion policy."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from random import Random
from threading import Lock
from types import MappingProxyType
from typing import Final

from .contracts import AdaptiveLinearModel, Observation, UpdateOutcome


class UpdateRejectionReason(StrEnum):
    """Reasons an ordinary-cook frame cannot update either model arm."""

    LID_OPEN = "lid-open"
    SAFETY_OVERRIDE = "safety"
    MANUAL_OVERRIDE = "manual"
    STALE_PROBE = "stale-probe"
    UNKNOWN_ACTUATION = "unknown-actuation"
    INSUFFICIENT_EXCITATION = "unexcited"
    UNTRUSTED_PROVENANCE = "untrusted-provenance"


class PromotionRejectionReason(StrEnum):
    """Reasons a challenger may not replace the current incumbent."""

    NOT_BETTER = "not-better"
    UNSTABLE_DYNAMICS = "unstable-dynamics"
    IMPLAUSIBLE_GAIN = "implausible-gain"
    IMPLAUSIBLE_DELAY = "implausible-delay"
    INSUFFICIENT_SAMPLES = "insufficient-samples"
    WORSE_BRAKING = "worse-braking"
    STATE_ALIGNMENT = "state-alignment"


class OperatingState(StrEnum):
    """Cook state used alongside temperature and duty replay strata."""

    TRANSIENT = "transient"
    HOLD = "hold"
    COAST = "coast"


class AlignmentEvidence(StrEnum):
    """Whether a model arm must report state-alignment error."""

    MEASURED = "measured"
    NOT_APPLICABLE = "not-applicable"


@dataclass(frozen=True, slots=True)
class AdaptationPolicy:
    """Conservative thresholds for passive updates and challenger promotion."""

    max_probe_age_s: float = 60.0
    excitation_window: int = 12
    min_input_variance: float = 1e-3
    min_input_levels: int = 2
    min_effective_samples: int = 20
    min_gain: float = 1e-6
    max_gain: float = 10.0
    max_delay_steps: int = 15
    max_pole_magnitude: float = 1.0
    max_alignment_error_c: float = 2.0
    braking_tolerance: float = 0.0

    def __post_init__(self) -> None:
        if self.max_probe_age_s < 0.0:
            raise ValueError("max_probe_age_s must be non-negative")
        if self.excitation_window < 2:
            raise ValueError("excitation_window must be at least two")
        if self.min_input_variance < 0.0:
            raise ValueError("min_input_variance must be non-negative")
        if self.min_input_levels < 2:
            raise ValueError("min_input_levels must be at least two")
        if self.min_effective_samples < 1:
            raise ValueError("min_effective_samples must be positive")
        if not 0.0 <= self.min_gain <= self.max_gain:
            raise ValueError("gain bounds must be finite and ordered")
        if self.max_delay_steps < 0:
            raise ValueError("max_delay_steps must be non-negative")
        if not 0.0 < self.max_pole_magnitude <= 1.0:
            raise ValueError("max_pole_magnitude must be in (0, 1]")
        if self.max_alignment_error_c < 0.0:
            raise ValueError("max_alignment_error_c must be non-negative")
        if self.braking_tolerance < 0.0:
            raise ValueError("braking_tolerance must be non-negative")


@dataclass(frozen=True, slots=True)
class WindowScores:
    """Prediction and braking evidence evaluated on one untouched window."""

    window_id: str
    candidate_prediction_score: float
    incumbent_prediction_score: float
    candidate_braking_score: float | None
    incumbent_braking_score: float | None

    def __post_init__(self) -> None:
        if not self.window_id:
            raise ValueError("window_id must not be empty")

@dataclass(frozen=True, slots=True)
class UpdateGate:
    """Immutable audit of the update eligibility decision for one frame."""

    permitted: bool
    reasons: tuple[UpdateRejectionReason, ...]
    input_variance: float
    input_levels: int


@dataclass(frozen=True, slots=True)
class AdaptationOutcome:
    """Result of routing one passive frame without exposing mutable model state."""

    updated: bool
    gate: UpdateGate
    incumbent: UpdateOutcome | None
    challenger: UpdateOutcome | None


@dataclass(frozen=True, slots=True)
class ReplaySample:
    """An admitted passive observation with its operating-mode classification."""

    observation: Observation
    state: OperatingState


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    """Immutable complete record of one challenger evaluation."""

    promoted: bool
    reasons: tuple[PromotionRejectionReason, ...]
    window_id: str
    candidate_prediction_score: float
    incumbent_prediction_score: float
    candidate_braking_score: float | None
    incumbent_braking_score: float | None
    stable_dynamics: bool
    plausible_gain: bool
    plausible_delay: bool
    sufficient_effective_samples: bool
    challenger_effective_updates: int
    braking_not_worse: bool
    state_aligned: bool
    consecutive_wins: int
    challenger_alignment: AlignmentEvidence
    incumbent_alignment: AlignmentEvidence
    candidate_snapshot: Mapping[str, object]
    incumbent_snapshot: Mapping[str, object]


_TEMPERATURE_STRATA: Final[tuple[tuple[str, float], ...]] = (
    ("low", 75.0),
    ("mid", 110.0),
    ("high", 160.0),
)
_Q_STRATA: Final[tuple[tuple[str, float], ...]] = (
    ("low", 1.0 / 3.0),
    ("mid", 2.0 / 3.0),
)


class StratifiedReplay:
    """Bounded deterministic replay that protects underrepresented cook regimes."""

    def __init__(self, capacity: int, seed: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._random = Random(seed)
        self._buckets: dict[str, list[ReplaySample]] = {}
        self._seen: dict[str, int] = {}

    def __len__(self) -> int:
        return sum(len(bucket) for bucket in self._buckets.values())

    @property
    def samples(self) -> tuple[ReplaySample, ...]:
        """Return a deterministic, immutable view of all retained observations."""
        return tuple(
            sample
            for stratum in sorted(self._buckets)
            for sample in self._buckets[stratum]
        )

    def count(self, *, stratum: str | None = None) -> int:
        """Count one full stratum or a ``q-band-state`` aggregate."""
        if stratum is None:
            return len(self)
        if stratum in self._buckets:
            return len(self._buckets[stratum])
        suffix = f"-{stratum}"
        return sum(
            len(bucket)
            for key, bucket in self._buckets.items()
            if key.endswith(suffix)
        )

    def extend(self, samples: Iterable[ReplaySample]) -> None:
        """Admit samples in arrival order with deterministic reservoir replacement."""
        for sample in samples:
            self.add(sample)

    def add(self, sample: ReplaySample) -> None:
        """Admit one sample without allowing common regimes to consume all capacity."""
        stratum = _stratum(sample)
        bucket = self._buckets.setdefault(stratum, [])
        seen = self._seen.get(stratum, 0) + 1
        self._seen[stratum] = seen
        if len(self) < self._capacity:
            bucket.append(sample)
            return

        dominant = min(
            self._buckets,
            key=lambda key: (-len(self._buckets[key]), key),
        )
        if len(bucket) < len(self._buckets[dominant]):
            evicted = self._buckets[dominant]
            del evicted[self._random.randrange(len(evicted))]
            bucket.append(sample)
            return

        replacement = self._random.randrange(seen)
        if replacement < len(bucket):
            bucket[replacement] = sample


class AdaptationManager:
    """Own incumbent/challenger updates and atomically promote proven candidates."""

    def __init__(
        self,
        *,
        incumbent: AdaptiveLinearModel,
        challenger: AdaptiveLinearModel,
        policy: AdaptationPolicy | None = None,
        incumbent_alignment: AlignmentEvidence = AlignmentEvidence.MEASURED,
        challenger_alignment: AlignmentEvidence = AlignmentEvidence.MEASURED,
        replay_capacity: int = 360,
        replay_seed: int = 0,
    ) -> None:
        if incumbent is challenger:
            raise ValueError("incumbent and challenger must be separate model objects")
        self._policy = policy or AdaptationPolicy()
        self._incumbent = incumbent
        self._challenger = challenger
        self._incumbent_alignment = incumbent_alignment
        self._challenger_alignment = challenger_alignment
        self._inputs: deque[float] = deque(maxlen=self._policy.excitation_window)
        self._replay = StratifiedReplay(replay_capacity, replay_seed)
        self._challenger_effective_updates = 0
        self._consecutive_wins = 0
        self._lock = Lock()

    @property
    def incumbent(self) -> AdaptiveLinearModel:
        """Return the current incumbent model object."""
        with self._lock:
            return self._incumbent

    @property
    def challenger(self) -> AdaptiveLinearModel:
        """Return the separately maintained challenger model object."""
        with self._lock:
            return self._challenger

    @property
    def replay(self) -> StratifiedReplay:
        """Return the manager-owned replay store."""
        return self._replay

    def observe(
        self,
        observation: Observation,
        *,
        state: OperatingState = OperatingState.TRANSIENT,
        provenance: str = "ordinary-cook",
        lid_open: bool = False,
        safety_override: bool = False,
        manual_override: bool = False,
        probe_age_s: float = 0.0,
        actuation_known: bool = True,
    ) -> AdaptationOutcome:
        """Score first, track the incumbent, and learn only on the shadow arm."""
        with self._lock:
            hard_rejection = _hard_update_rejection(
                provenance=provenance,
                lid_open=lid_open,
                safety_override=safety_override,
                manual_override=manual_override,
                probe_age_s=probe_age_s,
                actuation_known=actuation_known,
                max_probe_age_s=self._policy.max_probe_age_s,
            )
            if hard_rejection is not None:
                variance, levels = _excitation(self._inputs)
                gate = UpdateGate(False, (hard_rejection,), variance, levels)
                return AdaptationOutcome(False, gate, None, None)

            candidate_inputs = (*self._inputs, observation.q)
            variance, levels = _excitation(candidate_inputs)
            self._inputs.append(observation.q)
            gate = self._excitation_gate(variance, levels)
            if not gate.permitted:
                return AdaptationOutcome(False, gate, None, None)

            incumbent_outcome = self._incumbent.track(observation)
            challenger_outcome = self._challenger.observe(observation)
            if challenger_outcome.updated:
                self._challenger_effective_updates += 1
            self._replay.add(ReplaySample(observation, state))
            return AdaptationOutcome(
                challenger_outcome.updated,
                gate,
                incumbent_outcome,
                challenger_outcome,
            )

    def evaluate(self, scores: WindowScores) -> PromotionDecision:
        """Evaluate one immutable score bundle and atomically swap on win two."""
        with self._lock:
            candidate_snapshot = _freeze_mapping(self._challenger.snapshot())
            incumbent_snapshot = _freeze_mapping(self._incumbent.snapshot())
            challenger_alignment = self._challenger_alignment
            incumbent_alignment = self._incumbent_alignment
            stable = _stable_dynamics(
                candidate_snapshot, self._policy
            ) and _stable_dynamics(incumbent_snapshot, self._policy)
            plausible_gain = _plausible_gain(candidate_snapshot, self._policy)
            plausible_delay = _plausible_delay(candidate_snapshot, self._policy)
            effective_updates = self._challenger_effective_updates
            sufficient_samples = effective_updates >= self._policy.min_effective_samples
            braking_not_worse = _not_worse(
                scores.candidate_braking_score,
                scores.incumbent_braking_score,
                self._policy.braking_tolerance,
            )
            state_aligned = _state_aligned(
                candidate_snapshot,
                challenger_alignment,
                self._policy,
            )
            score_win = _strict_score_win(
                scores.candidate_prediction_score,
                scores.incumbent_prediction_score,
            )

            reasons: list[PromotionRejectionReason] = []
            if not score_win:
                reasons.append(PromotionRejectionReason.NOT_BETTER)
            if not stable:
                reasons.append(PromotionRejectionReason.UNSTABLE_DYNAMICS)
            if not plausible_gain:
                reasons.append(PromotionRejectionReason.IMPLAUSIBLE_GAIN)
            if not plausible_delay:
                reasons.append(PromotionRejectionReason.IMPLAUSIBLE_DELAY)
            if not sufficient_samples:
                reasons.append(PromotionRejectionReason.INSUFFICIENT_SAMPLES)
            if not braking_not_worse:
                reasons.append(PromotionRejectionReason.WORSE_BRAKING)
            if not state_aligned:
                reasons.append(PromotionRejectionReason.STATE_ALIGNMENT)

            if reasons:
                self._consecutive_wins = 0
                decision_streak = 0
                promoted = False
            else:
                self._consecutive_wins += 1
                decision_streak = self._consecutive_wins
                promoted = decision_streak >= 2
                if promoted:
                    self._incumbent, self._challenger = (
                        self._challenger,
                        self._incumbent,
                    )
                    self._challenger_effective_updates = 0
                    self._incumbent_alignment, self._challenger_alignment = (
                        self._challenger_alignment,
                        self._incumbent_alignment,
                    )
                    self._consecutive_wins = 0

            return PromotionDecision(
                promoted=promoted,
                reasons=tuple(reasons),
                window_id=scores.window_id,
                candidate_prediction_score=float(scores.candidate_prediction_score),
                incumbent_prediction_score=float(scores.incumbent_prediction_score),
                candidate_braking_score=scores.candidate_braking_score,
                incumbent_braking_score=scores.incumbent_braking_score,
                stable_dynamics=stable,
                plausible_gain=plausible_gain,
                plausible_delay=plausible_delay,
                sufficient_effective_samples=sufficient_samples,
                challenger_effective_updates=effective_updates,
                braking_not_worse=braking_not_worse,
                state_aligned=state_aligned,
                consecutive_wins=decision_streak,
                challenger_alignment=challenger_alignment,
                incumbent_alignment=incumbent_alignment,
                candidate_snapshot=candidate_snapshot,
                incumbent_snapshot=incumbent_snapshot,
            )

    def _excitation_gate(self, variance: float, levels: int) -> UpdateGate:
        if (
            len(self._inputs) < self._policy.excitation_window
            or variance < self._policy.min_input_variance
            or levels < self._policy.min_input_levels
        ):
            return UpdateGate(
                False,
                (UpdateRejectionReason.INSUFFICIENT_EXCITATION,),
                variance,
                levels,
            )
        return UpdateGate(True, (), variance, levels)


def _hard_update_rejection(
    *,
    provenance: str,
    lid_open: bool,
    safety_override: bool,
    manual_override: bool,
    probe_age_s: float,
    actuation_known: bool,
    max_probe_age_s: float,
) -> UpdateRejectionReason | None:
    if lid_open:
        return UpdateRejectionReason.LID_OPEN
    if safety_override:
        return UpdateRejectionReason.SAFETY_OVERRIDE
    if manual_override:
        return UpdateRejectionReason.MANUAL_OVERRIDE
    if not isfinite(probe_age_s) or probe_age_s < 0.0 or probe_age_s > max_probe_age_s:
        return UpdateRejectionReason.STALE_PROBE
    if not actuation_known:
        return UpdateRejectionReason.UNKNOWN_ACTUATION
    if provenance != "ordinary-cook":
        return UpdateRejectionReason.UNTRUSTED_PROVENANCE
    return None


def _excitation(inputs: Iterable[float]) -> tuple[float, int]:
    values = tuple(float(value) for value in inputs)
    if not values or not all(isfinite(value) for value in values):
        return 0.0, 0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return variance, len(set(values))


def _stratum(sample: ReplaySample) -> str:
    temperature = "very-high"
    for name, upper in _TEMPERATURE_STRATA:
        if sample.observation.temp_c < upper:
            temperature = name
            break
    q_band = "high"
    for name, upper in _Q_STRATA:
        if sample.observation.q < upper:
            q_band = name
            break
    return f"{temperature}-{q_band}-{sample.state.value}"


def _strict_score_win(candidate: float, incumbent: float) -> bool:
    return isfinite(candidate) and isfinite(incumbent) and candidate < incumbent


def _not_worse(
    candidate: float | None, incumbent: float | None, tolerance: float
) -> bool:
    return (
        candidate is not None
        and incumbent is not None
        and isfinite(candidate)
        and isfinite(incumbent)
        and candidate <= incumbent + tolerance
    )


def _stable_dynamics(snapshot: Mapping[str, object], policy: AdaptationPolicy) -> bool:
    poles = _values_for_key(snapshot, "poles")
    if not poles:
        poles = _values_for_key(snapshot, "pole")
    return bool(poles) and all(
        _finite_complex(pole) and abs(pole) < policy.max_pole_magnitude
        for pole in poles
    )


def _plausible_gain(snapshot: Mapping[str, object], policy: AdaptationPolicy) -> bool:
    gains = (
        _values_for_key(snapshot, "steady_gain")
        + _values_for_key(snapshot, "dc_gain")
        + _values_for_key(snapshot, "final_gain")
    )
    return bool(gains) and all(
        _finite_real(gain) and policy.min_gain <= gain <= policy.max_gain
        for gain in gains
    )


def _plausible_delay(snapshot: Mapping[str, object], policy: AdaptationPolicy) -> bool:
    delays = _values_for_key(snapshot, "delay_steps")
    return bool(delays) and all(
        _finite_real(delay) and 0.0 <= delay <= policy.max_delay_steps
        for delay in delays
    )


def _state_aligned(
    snapshot: Mapping[str, object],
    evidence: AlignmentEvidence,
    policy: AdaptationPolicy,
) -> bool:
    if evidence is AlignmentEvidence.NOT_APPLICABLE:
        return snapshot.get("schema") in {
            "scheduled-arx/v1",
            "laguerre-dmc/v1",
        }
    errors = _values_for_key(snapshot, "alignment_error_c")
    return bool(errors) and all(
        _finite_real(error) and abs(error) <= policy.max_alignment_error_c
        for error in errors
    )


def _values_for_key(value: object, key: str) -> list[float | complex]:
    values: list[float | complex] = []
    if isinstance(value, Mapping):
        for nested_key, nested_value in value.items():
            if nested_key == key:
                values.extend(_numeric_values(nested_value))
            else:
                values.extend(_values_for_key(nested_value, key))
    elif isinstance(value, (tuple, list)):
        for nested_value in value:
            values.extend(_values_for_key(nested_value, key))
    return values


def _numeric_values(value: object) -> list[float | complex]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float, complex)):
        return [value]
    if isinstance(value, Mapping):
        return [number for nested in value.values() for number in _numeric_values(nested)]
    if isinstance(value, (tuple, list)):
        return [number for nested in value for number in _numeric_values(nested)]
    return []


def _finite_real(value: float | complex) -> bool:
    return not isinstance(value, complex) and isfinite(value)


def _finite_complex(value: float | complex) -> bool:
    return isfinite(value.real) and isfinite(value.imag)


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({key: _freeze_value(nested) for key, nested in value.items()})


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, list):
        return tuple(_freeze_value(nested) for nested in value)
    if isinstance(value, tuple):
        return tuple(_freeze_value(nested) for nested in value)
    return value
