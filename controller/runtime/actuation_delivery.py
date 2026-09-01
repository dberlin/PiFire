"""Transparent observation of delivered auger, fan, and PWM actuation."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from numbers import Real
from typing import Any

from common.learning_trajectory import FrameDeliveryCertainty

ACTUATION_EDGE_CAPACITY = 512


class ActuatorChannel(StrEnum):
    """A physical output channel represented in trajectory evidence."""

    AUGER = "auger"
    FAN = "fan"
    FAN_PWM = "fan-pwm"


@dataclass(frozen=True, slots=True)
class DeliveredActuationEdge:
    """One exact physical output transition observed around an actuator call."""

    channel: ActuatorChannel
    previous_value: bool | float
    current_value: bool | float
    monotonic_ms: int
    wall_ms: int
    certainty: FrameDeliveryCertainty
    semantic_source: str


@dataclass(frozen=True, slots=True)
class DeliveredActuationIntegral:
    """Piecewise-constant delivered actuation over ``[start, end)``."""

    monotonic_start_ms: int
    monotonic_end_ms: int
    auger_on_seconds: float
    fan_on_seconds: float
    fan_duty_integral_seconds: float
    auger_start_active: bool
    auger_end_active: bool
    fan_start_active: bool
    fan_end_active: bool
    pwm_start: float
    pwm_end: float
    auger_certainty: FrameDeliveryCertainty
    fan_certainty: FrameDeliveryCertainty
    unknown_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _TimelineEvent:
    monotonic_ms: int
    value: bool | float | None
    reason: str | None

    @property
    def exact(self) -> bool:
        return self.reason is None


@dataclass(slots=True)
class _Cursor:
    value: bool | float
    exact: bool
    reason: str | None


class ActuationDeliveryJournal:
    """Bounded, thread-safe history of exact delivered actuator states."""

    def __init__(
        self,
        *,
        monotonic_clock: Callable[[], int] | None = None,
        wall_clock: Callable[[], int] | None = None,
        capacity: int = ACTUATION_EDGE_CAPACITY,
    ) -> None:
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("actuation edge capacity must be a positive integer")
        self._monotonic_clock = monotonic_clock or _monotonic_ms
        self._wall_clock = wall_clock or _wall_ms
        self._capacity = capacity
        self._command_lock = threading.RLock()
        self._lock = threading.RLock()
        self._edges: deque[DeliveredActuationEdge] = deque(maxlen=capacity)
        self._timelines: dict[ActuatorChannel, deque[_TimelineEvent]] = {
            channel: deque(maxlen=capacity) for channel in ActuatorChannel
        }
        self._evicted_before: dict[ActuatorChannel, int | None] = dict.fromkeys(ActuatorChannel)
        self._last_monotonic_ms: int | None = None
        self._last_wall_ms: int | None = None

    @property
    def edges(self) -> tuple[DeliveredActuationEdge, ...]:
        """Return an immutable, owned snapshot of retained physical edges."""

        with self._lock:
            return tuple(self._edges)

    def mark_uncertain(
        self,
        reason: str,
        at_ms: int,
        *,
        channel: ActuatorChannel | None = None,
    ) -> None:
        """Make one or every channel unknown from ``at_ms`` until exact readback."""

        if type(reason) is not str or not reason.strip():
            raise ValueError("actuation uncertainty reason must be non-empty")
        instant = _milliseconds(at_ms, "uncertainty timestamp")
        with self._command_lock, self._lock:
            self._accept_monotonic_locked(instant)
            channels = tuple(ActuatorChannel) if channel is None else (ActuatorChannel(channel),)
            self._mark_uncertain_locked(reason, instant, channels)

    def integrate(self, start_ms: int, end_ms: int) -> DeliveredActuationIntegral:
        """Integrate exact piecewise-constant states over ``[start_ms, end_ms)``."""

        start = _milliseconds(start_ms, "integration start")
        end = _milliseconds(end_ms, "integration end")
        if end <= start:
            raise ValueError("actuation integration interval must be positive and monotonic")

        with self._lock:
            cursors: dict[ActuatorChannel, _Cursor] = {}
            future: list[tuple[int, ActuatorChannel, _TimelineEvent]] = []
            for channel in ActuatorChannel:
                cursor, channel_future = self._cursor_locked(channel, start, end)
                cursors[channel] = cursor
                future.extend((event.monotonic_ms, channel, event) for event in channel_future)

            auger_start = bool(cursors[ActuatorChannel.AUGER].value)
            fan_start = bool(cursors[ActuatorChannel.FAN].value)
            pwm_start = float(cursors[ActuatorChannel.FAN_PWM].value)
            auger_integral_ms = 0.0
            fan_integral_ms = 0.0
            duty_integral_ms = 0.0
            auger_exact = True
            fan_exact = True
            reasons: list[str] = []
            seen_reasons: set[str] = set()

            def accumulate(until_ms: int, from_ms: int) -> None:
                nonlocal auger_integral_ms, fan_integral_ms, duty_integral_ms, auger_exact, fan_exact
                duration_ms = until_ms - from_ms
                if duration_ms <= 0:
                    return
                auger = cursors[ActuatorChannel.AUGER]
                fan = cursors[ActuatorChannel.FAN]
                pwm = cursors[ActuatorChannel.FAN_PWM]
                if bool(auger.value):
                    auger_integral_ms += duration_ms
                if bool(fan.value):
                    fan_integral_ms += duration_ms
                    duty_integral_ms += duration_ms * float(pwm.value)
                if not auger.exact:
                    auger_exact = False
                    _append_reason(auger.reason, reasons, seen_reasons)
                if not fan.exact or not pwm.exact:
                    fan_exact = False
                    if not fan.exact:
                        _append_reason(fan.reason, reasons, seen_reasons)
                    if not pwm.exact:
                        _append_reason(pwm.reason, reasons, seen_reasons)

            cursor_ms = start
            future.sort(key=lambda item: item[0])
            index = 0
            while index < len(future):
                event_ms = future[index][0]
                accumulate(event_ms, cursor_ms)
                while index < len(future) and future[index][0] == event_ms:
                    _event_ms, channel, event = future[index]
                    self._apply_event(cursors[channel], event)
                    index += 1
                cursor_ms = event_ms
            accumulate(end, cursor_ms)

            return DeliveredActuationIntegral(
                monotonic_start_ms=start,
                monotonic_end_ms=end,
                auger_on_seconds=auger_integral_ms / 1_000.0,
                fan_on_seconds=fan_integral_ms / 1_000.0,
                fan_duty_integral_seconds=duty_integral_ms / 1_000.0,
                auger_start_active=auger_start,
                auger_end_active=bool(cursors[ActuatorChannel.AUGER].value),
                fan_start_active=fan_start,
                fan_end_active=bool(cursors[ActuatorChannel.FAN].value),
                pwm_start=pwm_start,
                pwm_end=float(cursors[ActuatorChannel.FAN_PWM].value),
                auger_certainty=(FrameDeliveryCertainty.EXACT if auger_exact else FrameDeliveryCertainty.UNKNOWN),
                fan_certainty=(FrameDeliveryCertainty.EXACT if fan_exact else FrameDeliveryCertainty.UNKNOWN),
                unknown_reasons=tuple(reasons),
            )

    def _capture_timestamp(self) -> tuple[int, int]:
        monotonic_ms = _milliseconds(self._monotonic_clock(), "monotonic clock")
        wall_ms = _milliseconds(self._wall_clock(), "wall clock")
        with self._lock:
            if self._last_monotonic_ms is not None and monotonic_ms < self._last_monotonic_ms:
                raise ValueError("monotonic clock regressed")
            if self._last_wall_ms is not None and wall_ms < self._last_wall_ms:
                raise ValueError("wall clock regressed")
            self._last_monotonic_ms = monotonic_ms
            self._last_wall_ms = wall_ms
        return monotonic_ms, wall_ms

    def _record_success(
        self,
        *,
        channels: tuple[ActuatorChannel, ...],
        before: Mapping[ActuatorChannel, bool | float],
        after: Mapping[ActuatorChannel, bool | float],
        invalid_after: Mapping[ActuatorChannel, str],
        monotonic_ms: int,
        wall_ms: int,
        semantic_source: str,
    ) -> None:
        with self._lock:
            for channel in channels:
                if channel in before:
                    self._establish_pre_locked(channel, before[channel], monotonic_ms)
            for channel in channels:
                if channel not in after:
                    continue
                value = after[channel]
                previous = before.get(channel)
                if previous is not None and previous != value:
                    self._append_timeline_locked(channel, _TimelineEvent(monotonic_ms, value, None))
                    self._edges.append(
                        DeliveredActuationEdge(
                            channel=channel,
                            previous_value=previous,
                            current_value=value,
                            monotonic_ms=monotonic_ms,
                            wall_ms=wall_ms,
                            certainty=FrameDeliveryCertainty.EXACT,
                            semantic_source=str(semantic_source),
                        )
                    )
                else:
                    self._establish_pre_locked(channel, value, monotonic_ms)
            for channel, reason in invalid_after.items():
                self._mark_uncertain_locked(reason, monotonic_ms, (channel,))

    def _mark_uncertain_at_captured_time(
        self,
        reason: str,
        monotonic_ms: int,
        channels: tuple[ActuatorChannel, ...],
    ) -> None:
        with self._lock:
            self._mark_uncertain_locked(reason, monotonic_ms, channels)

    def _mark_uncertain_at_boundary(
        self,
        reason: str,
        channels: tuple[ActuatorChannel, ...],
    ) -> None:
        with self._lock:
            boundary = self._last_monotonic_ms if self._last_monotonic_ms is not None else 0
            self._mark_uncertain_locked(reason, boundary, channels)


    def _accept_monotonic_locked(self, monotonic_ms: int) -> None:
        if self._last_monotonic_ms is not None and monotonic_ms < self._last_monotonic_ms:
            raise ValueError("monotonic clock regressed")
        self._last_monotonic_ms = monotonic_ms

    def _mark_uncertain_locked(
        self,
        reason: str,
        monotonic_ms: int,
        channels: tuple[ActuatorChannel, ...],
    ) -> None:
        owned_reason = str(reason)
        for channel in channels:
            self._append_timeline_locked(channel, _TimelineEvent(monotonic_ms, None, owned_reason))

    def _establish_pre_locked(
        self,
        channel: ActuatorChannel,
        value: bool | float,
        monotonic_ms: int,
    ) -> None:
        timeline = self._timelines[channel]
        latest = timeline[-1] if timeline else None
        if latest is None or not latest.exact:
            self._append_timeline_locked(channel, _TimelineEvent(monotonic_ms, value, None))
            return
        if latest.value != value:
            self._append_timeline_locked(
                channel,
                _TimelineEvent(monotonic_ms, None, f"{channel.value} pre-readback disagreed with retained state"),
            )
            self._append_timeline_locked(channel, _TimelineEvent(monotonic_ms, value, None))

    def _append_timeline_locked(self, channel: ActuatorChannel, event: _TimelineEvent) -> None:
        timeline = self._timelines[channel]
        if timeline and event.monotonic_ms < timeline[-1].monotonic_ms:
            raise ValueError("actuation event clock regressed")
        if len(timeline) == self._capacity:
            if len(timeline) > 1:
                earliest_retained = timeline[1].monotonic_ms
            else:
                earliest_retained = event.monotonic_ms
            previous_boundary = self._evicted_before[channel]
            self._evicted_before[channel] = (
                earliest_retained if previous_boundary is None else max(previous_boundary, earliest_retained)
            )
        timeline.append(event)

    def _cursor_locked(
        self,
        channel: ActuatorChannel,
        start_ms: int,
        end_ms: int,
    ) -> tuple[_Cursor, tuple[_TimelineEvent, ...]]:
        default: bool | float = 0.0 if channel is ActuatorChannel.FAN_PWM else False
        boundary = self._evicted_before[channel]
        if boundary is not None and start_ms < boundary:
            cursor = _Cursor(default, False, f"{channel.value} history evicted before {boundary}ms")
        else:
            cursor = _Cursor(default, False, f"initial {channel.value} state is unknown")
        future: list[_TimelineEvent] = []
        for event in self._timelines[channel]:
            if event.monotonic_ms <= start_ms:
                self._apply_event(cursor, event)
            elif event.monotonic_ms <= end_ms:
                future.append(event)
        return cursor, tuple(future)

    @staticmethod
    def _apply_event(cursor: _Cursor, event: _TimelineEvent) -> None:
        if event.exact:
            assert event.value is not None
            cursor.value = event.value
            cursor.exact = True
            cursor.reason = None
        else:
            cursor.exact = False
            cursor.reason = event.reason


class DeliveredGrillPlatform:
    """Transparent grill-platform proxy that journals actual readback transitions."""

    __slots__ = (
        "_journal",
        "_platform",
        "_pwm_capable",
        "_readback_authoritative",
    )
    _journal: ActuationDeliveryJournal
    _platform: object
    _pwm_capable: bool | None
    _readback_authoritative: bool


    def __init__(
        self,
        platform: object,
        *,
        journal: ActuationDeliveryJournal | None = None,
        readback_authoritative: bool = False,
    ) -> None:
        if type(readback_authoritative) is not bool:
            raise ValueError("readback_authoritative must be a bool")
        object.__setattr__(self, "_platform", platform)
        object.__setattr__(self, "_journal", journal or ActuationDeliveryJournal())
        object.__setattr__(self, "_readback_authoritative", readback_authoritative)
        object.__setattr__(self, "_pwm_capable", _pwm_capability(platform))

    @property
    def journal(self) -> ActuationDeliveryJournal:
        return self._journal

    def auger_on(self, *args: Any, **kwargs: Any) -> Any:
        return self._invoke("auger_on", (ActuatorChannel.AUGER,), args, kwargs)

    def auger_off(self, *args: Any, **kwargs: Any) -> Any:
        return self._invoke("auger_off", (ActuatorChannel.AUGER,), args, kwargs)

    def fan_on(self, *args: Any, **kwargs: Any) -> Any:
        return self._invoke("fan_on", (ActuatorChannel.FAN, ActuatorChannel.FAN_PWM), args, kwargs)

    def fan_off(self, *args: Any, **kwargs: Any) -> Any:
        return self._invoke(
            "fan_off",
            (ActuatorChannel.FAN, ActuatorChannel.FAN_PWM),
            args,
            kwargs,
            observed=(ActuatorChannel.FAN,),
        )

    def fan_toggle(self, *args: Any, **kwargs: Any) -> Any:
        return self._invoke(
            "fan_toggle",
            (ActuatorChannel.FAN, ActuatorChannel.FAN_PWM),
            args,
            kwargs,
            observed=(ActuatorChannel.FAN,),
        )

    def set_duty_cycle(self, *args: Any, **kwargs: Any) -> Any:
        override_ramping = args[1] if len(args) > 1 else kwargs.get("override_ramping", True)
        if override_ramping is False:
            return self._invoke(
                "set_duty_cycle",
                (ActuatorChannel.FAN_PWM,),
                args,
                kwargs,
                observed=(),
                post_uncertain=(ActuatorChannel.FAN_PWM,),
            )
        return self._invoke(
            "set_duty_cycle",
            (ActuatorChannel.FAN_PWM,),
            args,
            kwargs,
            observed=(ActuatorChannel.FAN, ActuatorChannel.FAN_PWM),
        )

    def pwm_fan_ramp(self, *args: Any, **kwargs: Any) -> Any:
        return self._invoke(
            "pwm_fan_ramp",
            (ActuatorChannel.FAN, ActuatorChannel.FAN_PWM),
            args,
            kwargs,
            asynchronous=True,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._platform, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self.__slots__:
            object.__setattr__(self, name, value)
            return
        if name == "journal":
            raise AttributeError("journal is read-only")
        setattr(self._platform, name, value)

    def __delattr__(self, name: str) -> None:
        if name in self.__slots__ or name == "journal":
            raise AttributeError(f"cannot delete {name}")
        delattr(self._platform, name)

    def __dir__(self) -> list[str]:
        return sorted(set(object.__dir__(self)) | set(dir(self._platform)))

    def _invoke(
        self,
        semantic_source: str,
        affected: tuple[ActuatorChannel, ...],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        *,
        asynchronous: bool = False,
        observed: tuple[ActuatorChannel, ...] | None = None,
        post_uncertain: tuple[ActuatorChannel, ...] = (),
    ) -> Any:
        with self._journal._command_lock:
            monotonic_ms: int | None = None
            wall_ms: int | None = None
            capture_failure: str | None = None
            try:
                monotonic_ms, wall_ms = self._journal._capture_timestamp()
            except BaseException as exc:
                capture_failure = f"actuation clock capture failed: {type(exc).__name__}: {exc}"

            before, _before_errors = self._readback()
            try:
                result = getattr(self._platform, semantic_source)(*args, **kwargs)
            except BaseException as exc:
                reason = f"{semantic_source} failed: {type(exc).__name__}: {exc}"
                if capture_failure is not None:
                    reason = f"{capture_failure}; {reason}"
                self._mark_observation_uncertain(reason, affected, monotonic_ms)
                raise

            after, after_errors = self._readback()
            if capture_failure is not None:
                self._mark_observation_uncertain(capture_failure, affected, None)
            elif asynchronous:
                self._mark_observation_uncertain(
                    "pwm_fan_ramp delivery is asynchronous and has no exact completion callback",
                    affected,
                    monotonic_ms,
                )
            elif not self._readback_authoritative:
                self._mark_observation_uncertain(
                    f"{semantic_source} readback is not certified authoritative",
                    affected,
                    monotonic_ms,
                )
            else:
                assert monotonic_ms is not None
                assert wall_ms is not None
                observed_channels = affected if observed is None else observed
                invalid_after = {
                    channel: after_errors.get(channel, f"{channel.value} missing from post-call readback")
                    for channel in observed_channels
                    if channel not in after
                }
                try:
                    self._journal._record_success(
                        channels=observed_channels,
                        before=before,
                        after=after,
                        invalid_after=invalid_after,
                        monotonic_ms=monotonic_ms,
                        wall_ms=wall_ms,
                        semantic_source=semantic_source,
                    )
                except BaseException as exc:
                    self._mark_observation_uncertain(
                        f"{semantic_source} journal update failed: {type(exc).__name__}: {exc}",
                        affected,
                        monotonic_ms,
                    )
                if post_uncertain:
                    self._mark_observation_uncertain(
                        f"{semantic_source} did not stop an active asynchronous ramp",
                        post_uncertain,
                        monotonic_ms,
                    )
            return result

    def _mark_observation_uncertain(
        self,
        reason: str,
        channels: tuple[ActuatorChannel, ...],
        monotonic_ms: int | None,
    ) -> None:
        try:
            if monotonic_ms is None:
                self._journal._mark_uncertain_at_boundary(reason, channels)
            else:
                self._journal._mark_uncertain_at_captured_time(reason, monotonic_ms, channels)
        except BaseException:
            return

    def _readback(self) -> tuple[dict[ActuatorChannel, bool | float], dict[ActuatorChannel, str]]:
        try:
            raw = self._platform.get_output_status()  # type: ignore[attr-defined]
            if not isinstance(raw, Mapping):
                reason = "output readback is not a mapping"
                return {}, {channel: reason for channel in ActuatorChannel}

            values: dict[ActuatorChannel, bool | float] = {}
            errors: dict[ActuatorChannel, str] = {}
            for channel, key in ((ActuatorChannel.AUGER, "auger"), (ActuatorChannel.FAN, "fan")):
                value = raw.get(key)
                if type(value) is bool:
                    values[channel] = value
                else:
                    errors[channel] = f"{key} readback must be an exact bool"

            pwm = raw.get("pwm")
            if pwm is None and self._pwm_capable is False:
                values[ActuatorChannel.FAN_PWM] = 1.0
            elif isinstance(pwm, Real) and not isinstance(pwm, bool):
                normalized = float(pwm)
                if math.isfinite(normalized) and 0.0 <= normalized <= 100.0:
                    values[ActuatorChannel.FAN_PWM] = normalized / 100.0
                else:
                    errors[ActuatorChannel.FAN_PWM] = "pwm readback must be finite percent in [0, 100]"
            else:
                errors[ActuatorChannel.FAN_PWM] = "pwm readback must be numeric percent"
            return values, errors
        except BaseException as exc:
            reason = f"output readback failed: {type(exc).__name__}: {exc}"
            return {}, {channel: reason for channel in ActuatorChannel}


def _pwm_capability(platform: object) -> bool | None:
    for attribute in ("dc_fan", "pwm_fan"):
        value = getattr(platform, attribute, None)
        if type(value) is bool:
            return value
    return None


def _append_reason(reason: str | None, ordered: list[str], seen: set[str]) -> None:
    owned = reason or "actuation state is unknown"
    if owned not in seen:
        seen.add(owned)
        ordered.append(owned)


def _milliseconds(value: object, context: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{context} must be a non-negative integer millisecond value")
    return value


def _monotonic_ms() -> int:
    return time.monotonic_ns() // 1_000_000


def _wall_ms() -> int:
    return time.time_ns() // 1_000_000
