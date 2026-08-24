from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


class ThermocoupleHealthState(StrEnum):
    UNMONITORED = "unmonitored"
    HEALTHY = "healthy"
    SUSPECTED = "suspected"
    CONFIRMED = "confirmed"


class ThermocoupleFault(StrEnum):
    OPEN = "open"
    SHORT = "short"
    MALFUNCTION = "malfunction"


class ThermocoupleEvidence(StrEnum):
    HARDWARE = "hardware"
    JUNCTION_COLLAPSE = "junction-collapse"
    STUCK_RESPONSE = "stuck-response"
    EXCITATION_RESPONSE = "excitation-response"
    IMPLAUSIBLE_STEP = "implausible-step"


@dataclass(frozen=True, slots=True)
class ThermocoupleHealthReport:
    state: ThermocoupleHealthState
    faults: tuple[ThermocoupleFault, ...] = ()
    evidence: tuple[ThermocoupleEvidence, ...] = ()
    temperature_valid: bool = True
    observed_at: float = 0.0
    detail: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.state is ThermocoupleHealthState.CONFIRMED and self.temperature_valid:
            raise ValueError("confirmed thermocouple health cannot have a valid temperature")
        if self.state is not ThermocoupleHealthState.CONFIRMED and any(
            fault in (ThermocoupleFault.OPEN, ThermocoupleFault.SHORT) for fault in self.faults
        ):
            raise ValueError("open and short faults require confirmed thermocouple health")
        object.__setattr__(self, "detail", MappingProxyType(dict(self.detail)))

    @property
    def confirmed(self) -> bool:
        return self.state is ThermocoupleHealthState.CONFIRMED

    @classmethod
    def unmonitored(cls, now: float) -> "ThermocoupleHealthReport":
        return cls(state=ThermocoupleHealthState.UNMONITORED, observed_at=now)

    @classmethod
    def healthy(
        cls,
        now: float,
        evidence: tuple[ThermocoupleEvidence, ...] = (),
    ) -> "ThermocoupleHealthReport":
        return cls(
            state=ThermocoupleHealthState.HEALTHY,
            evidence=evidence,
            observed_at=now,
        )

    @classmethod
    def confirmed_hardware(
        cls,
        faults: tuple[ThermocoupleFault, ...],
        now: float,
        status: object,
    ) -> "ThermocoupleHealthReport":
        return cls(
            state=ThermocoupleHealthState.CONFIRMED,
            faults=faults,
            evidence=(ThermocoupleEvidence.HARDWARE,),
            temperature_valid=False,
            observed_at=now,
            detail={"status": status},
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "faults": [item.value for item in self.faults],
            "evidence": [item.value for item in self.evidence],
            "temperature_valid": self.temperature_valid,
            "observed_at": self.observed_at,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True, slots=True)
class ThermocoupleHealthTransition:
    label: str
    previous: ThermocoupleHealthReport
    current: ThermocoupleHealthReport


class HardwareFaultLatch:
    __slots__ = ("_clean_since", "_recovery_seconds", "_report")

    def __init__(self, recovery_seconds: float) -> None:
        self._recovery_seconds = recovery_seconds
        self._report = ThermocoupleHealthReport.unmonitored(0.0)
        self._clean_since: float | None = None

    def cancel_clean_recovery(self) -> None:
        self._clean_since = None

    def update(
        self,
        faults: tuple[ThermocoupleFault, ...],
        now: float,
        primary: bool,
        status: object = None,
    ) -> ThermocoupleHealthReport:
        if faults:
            self._report = ThermocoupleHealthReport.confirmed_hardware(faults, now, status)
            self._clean_since = None
            return self._report

        if not self._report.confirmed:
            self._report = ThermocoupleHealthReport.healthy(now)
            self._clean_since = None
            return self._report

        if primary:
            self._report = replace(self._report, observed_at=now)
            return self._report

        if self._clean_since is None:
            self._clean_since = now
        if now - self._clean_since >= self._recovery_seconds:
            self._report = ThermocoupleHealthReport.healthy(now)
            self._clean_since = None
        else:
            self._report = replace(self._report, observed_at=now)
        return self._report
