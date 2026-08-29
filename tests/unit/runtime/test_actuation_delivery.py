from __future__ import annotations

import importlib
import importlib.abc
import sys
import threading
from collections import Counter, deque
from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest

from common.learning_trajectory import FrameDeliveryCertainty
from controller.runtime.actuation_delivery import (
    ACTUATION_EDGE_CAPACITY,
    ActuationDeliveryJournal,
    ActuatorChannel,
    DeliveredActuationEdge,
    DeliveredActuationIntegral,
    DeliveredGrillPlatform,
)


class FakeClock:
    def __init__(self, monotonic_ms: int = 0, wall_offset_ms: int = 1_000_000) -> None:
        self._lock = threading.Lock()
        self._monotonic_ms = monotonic_ms
        self._wall_offset_ms = wall_offset_ms

    def monotonic_clock(self) -> int:
        with self._lock:
            return self._monotonic_ms

    def wall_clock(self) -> int:
        with self._lock:
            return self._wall_offset_ms + self._monotonic_ms

    def set(self, monotonic_ms: int) -> None:
        with self._lock:
            self._monotonic_ms = monotonic_ms

    def advance(self, milliseconds: int) -> int:
        with self._lock:
            self._monotonic_ms += milliseconds
            return self._monotonic_ms


class ControlledGrillPlatform:
    """Driver fake whose commanded and physically read states are independent."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.commanded: dict[str, bool | float | tuple[Any, ...]] = {
            "auger": False,
            "fan": False,
            "pwm": 0.0,
        }
        self.actual: dict[str, Any] = {
            "auger": False,
            "fan": False,
            "pwm": 0.0,
            "igniter": False,
            "power": False,
        }
        self.actual_effects: dict[str, dict[str, Any]] = {}
        self.failures: dict[str, BaseException] = {}
        self.results: dict[str, object] = {}
        self.readbacks: deque[dict[str, Any] | BaseException] = deque()
        self.share_readback = False
        self.input_status = object()
        self.capability = object()
        self.command_report = object()
        self._lock = threading.RLock()

    def effect(self, method: str, **actual_values: Any) -> None:
        self.actual_effects[method] = actual_values

    def queue_readbacks(self, *values: dict[str, Any] | BaseException) -> None:
        self.readbacks.extend(values)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self.actual)

    def _call(
        self,
        method: str,
        *args: Any,
        command_updates: dict[str, bool | float | tuple[Any, ...]] | None = None,
        **kwargs: Any,
    ) -> object | None:
        with self._lock:
            self.counts[method] += 1
            self.calls.append((method, args, dict(kwargs)))
            if command_updates:
                self.commanded.update(command_updates)
            failure = self.failures.get(method)
            if failure is not None:
                raise failure
            self.actual.update(self.actual_effects.get(method, {}))
            return self.results.get(method)

    def get_output_status(self) -> dict[str, Any]:
        with self._lock:
            self.counts["get_output_status"] += 1
            self.calls.append(("get_output_status", (), {}))
            if self.readbacks:
                readback = self.readbacks.popleft()
                if isinstance(readback, BaseException):
                    raise readback
                return readback
            return self.actual if self.share_readback else dict(self.actual)

    def auger_on(self) -> object | None:
        return self._call("auger_on", command_updates={"auger": True})

    def auger_off(self) -> object | None:
        return self._call("auger_off", command_updates={"auger": False})

    def fan_on(self, duty_cycle: float = 100) -> object | None:
        return self._call("fan_on", duty_cycle, command_updates={"fan": True})

    def fan_off(self) -> object | None:
        return self._call("fan_off", command_updates={"fan": False})

    def fan_toggle(self) -> object | None:
        commanded = not bool(self.commanded["fan"])
        return self._call("fan_toggle", command_updates={"fan": commanded})

    def set_duty_cycle(self, percent: float) -> object | None:
        return self._call("set_duty_cycle", percent, command_updates={"pwm": percent})

    def pwm_fan_ramp(
        self,
        on_time: float = 5,
        min_duty_cycle: float = 20,
        max_duty_cycle: float = 100,
    ) -> object | None:
        ramp = (on_time, min_duty_cycle, max_duty_cycle)
        return self._call("pwm_fan_ramp", *ramp, command_updates={"fan": True, "pwm": ramp})

    def igniter_on(self) -> object | None:
        return self._call("igniter_on", command_updates=None)

    def power_on(self) -> object | None:
        return self._call("power_on", command_updates=None)

    def cleanup(self) -> object | None:
        return self._call("cleanup", command_updates=None)

    def get_input_status(self) -> object:
        self.counts["get_input_status"] += 1
        return self.input_status

    def auger_timing(self) -> object:
        self.counts["auger_timing"] += 1
        return self.capability

    def supported_commands(self, payload: object) -> object:
        self.counts["supported_commands"] += 1
        assert payload == "request"
        return self.command_report


def _platform(
    *,
    capacity: int = ACTUATION_EDGE_CAPACITY,
    readback_authoritative: bool = True,
) -> tuple[DeliveredGrillPlatform, ControlledGrillPlatform, FakeClock]:
    clock = FakeClock()
    raw = ControlledGrillPlatform()
    journal = ActuationDeliveryJournal(
        monotonic_clock=clock.monotonic_clock,
        wall_clock=clock.wall_clock,
        capacity=capacity,
    )
    delivered = DeliveredGrillPlatform(
        raw,
        journal=journal,
        readback_authoritative=readback_authoritative,
    )
    return delivered, raw, clock


def _establish_all_channels(
    delivered: DeliveredGrillPlatform,
    raw: ControlledGrillPlatform,
    clock: FakeClock,
    *,
    at_ms: int = 0,
) -> None:
    clock.set(at_ms)
    raw.effect("auger_off", auger=False)
    raw.effect("fan_off", fan=False)
    raw.effect("set_duty_cycle", pwm=0.0)
    delivered.auger_off()
    delivered.fan_off()
    delivered.set_duty_cycle(0.0)


def test_exact_auger_edges_and_piecewise_integral() -> None:
    delivered, raw, clock = _platform()

    clock.set(1_000)
    raw.effect("auger_on", auger=True)
    delivered.auger_on()
    clock.set(2_500)
    raw.effect("auger_off", auger=False)
    delivered.auger_off()
    clock.set(4_000)

    assert delivered.journal.edges == (
        DeliveredActuationEdge(
            channel=ActuatorChannel.AUGER,
            previous_value=False,
            current_value=True,
            monotonic_ms=1_000,
            wall_ms=1_001_000,
            certainty=FrameDeliveryCertainty.EXACT,
            semantic_source="auger_on",
        ),
        DeliveredActuationEdge(
            channel=ActuatorChannel.AUGER,
            previous_value=True,
            current_value=False,
            monotonic_ms=2_500,
            wall_ms=1_002_500,
            certainty=FrameDeliveryCertainty.EXACT,
            semantic_source="auger_off",
        ),
    )
    integral = delivered.journal.integrate(1_000, 4_000)
    assert integral.monotonic_start_ms == 1_000
    assert integral.monotonic_end_ms == 4_000
    assert integral.auger_on_seconds == pytest.approx(1.5)
    assert integral.auger_start_active is True
    assert integral.auger_end_active is False
    assert integral.auger_certainty is FrameDeliveryCertainty.EXACT


def test_exact_fan_on_off_and_normalized_pwm_integral() -> None:
    delivered, raw, clock = _platform()
    _establish_all_channels(delivered, raw, clock)

    clock.set(1_000)
    raw.effect("fan_on", fan=True)
    delivered.fan_on()
    raw.effect("set_duty_cycle", pwm=25.0)
    delivered.set_duty_cycle(25.0)
    clock.set(3_000)
    raw.effect("set_duty_cycle", pwm=75.0)
    delivered.set_duty_cycle(75.0)
    clock.set(4_000)
    raw.effect("fan_off", fan=False)
    delivered.fan_off()
    clock.set(5_000)

    integral = delivered.journal.integrate(0, 5_000)
    assert integral.fan_on_seconds == pytest.approx(3.0)
    assert integral.fan_duty_integral_seconds == pytest.approx(1.25)
    assert integral.fan_start_active is False
    assert integral.fan_end_active is False
    assert integral.pwm_start == pytest.approx(0.0)
    assert integral.pwm_end == pytest.approx(0.75)
    assert integral.fan_certainty is FrameDeliveryCertainty.EXACT
    assert [edge.current_value for edge in delivered.journal.edges if edge.channel is ActuatorChannel.FAN_PWM] == [
        0.25,
        0.75,
    ]
    assert raw.commanded["pwm"] == 75.0


def test_repeated_noop_command_does_not_append_an_edge() -> None:
    delivered, raw, clock = _platform()
    raw.effect("auger_off", auger=False)

    delivered.auger_off()
    clock.set(100)
    delivered.auger_off()
    clock.set(200)

    assert raw.counts["auger_off"] == 2
    assert delivered.journal.edges == ()
    integral = delivered.journal.integrate(0, 200)
    assert integral.auger_on_seconds == 0.0
    assert integral.auger_certainty is FrameDeliveryCertainty.EXACT


@pytest.mark.parametrize(
    ("method", "args", "effect"),
    [
        ("auger_on", (), {"auger": True}),
        ("auger_off", (), {"auger": False}),
        ("fan_on", (43.0,), {"fan": True}),
        ("fan_off", (), {"fan": False}),
        ("fan_toggle", (), {"fan": True}),
        ("set_duty_cycle", (37.5,), {"pwm": 37.5}),
        ("pwm_fan_ramp", (5.0, 20.0, 90.0), {"fan": True, "pwm": 20.0}),
    ],
)
def test_each_wrapped_method_calls_driver_once_and_preserves_return(
    method: str,
    args: tuple[float, ...],
    effect: dict[str, Any],
) -> None:
    delivered, raw, _clock = _platform()
    token = object()
    raw.results[method] = token
    raw.effect(method, **effect)

    result = getattr(delivered, method)(*args)

    assert result is token
    assert raw.counts[method] == 1
    assert [call for call in raw.calls if call[0] == method] == [(method, args, {})]
    assert raw.counts["get_output_status"] == 2


def test_underlying_exception_identity_is_preserved_and_channel_becomes_uncertain() -> None:
    delivered, raw, clock = _platform()
    failure = RuntimeError("relay write failed")
    raw.failures["auger_on"] = failure
    clock.set(500)

    with pytest.raises(RuntimeError) as caught:
        delivered.auger_on()

    assert caught.value is failure
    assert raw.counts["auger_on"] == 1
    clock.set(1_000)
    integral = delivered.journal.integrate(500, 1_000)
    assert integral.auger_certainty is FrameDeliveryCertainty.UNKNOWN
    assert integral.unknown_reasons


@pytest.mark.parametrize(
    "post_readback",
    [
        {},
        {"auger": "on", "fan": False, "pwm": 0.0},
        RuntimeError("readback failed"),
    ],
)
def test_missing_malformed_or_failed_post_readback_marks_affected_channel_uncertain(
    post_readback: dict[str, Any] | BaseException,
) -> None:
    delivered, raw, clock = _platform()
    before = raw.snapshot()
    raw.queue_readbacks(before, post_readback)
    raw.effect("auger_on", auger=True)
    token = object()
    raw.results["auger_on"] = token
    clock.set(250)

    assert delivered.auger_on() is token
    clock.set(500)

    integral = delivered.journal.integrate(250, 500)
    assert integral.auger_certainty is FrameDeliveryCertainty.UNKNOWN
    assert integral.unknown_reasons


def test_command_echo_readback_is_unknown_until_explicitly_certified() -> None:
    delivered, raw, clock = _platform(readback_authoritative=False)
    raw.effect("auger_on", auger=True)
    clock.set(100)

    delivered.auger_on()
    clock.set(200)

    assert raw.commanded["auger"] is True
    assert raw.actual["auger"] is True
    assert delivered.journal.integrate(100, 200).auger_certainty is FrameDeliveryCertainty.UNKNOWN


def test_asynchronous_ramp_is_not_interpolated_and_exact_command_closes_uncertainty() -> None:
    delivered, raw, clock = _platform()
    _establish_all_channels(delivered, raw, clock)
    clock.set(1_000)
    raw.effect("pwm_fan_ramp", fan=True, pwm=20.0)

    delivered.pwm_fan_ramp(5.0, 20.0, 80.0)
    edges_at_ramp_start = delivered.journal.edges
    clock.set(4_000)

    assert delivered.journal.edges == edges_at_ramp_start
    assert delivered.journal.integrate(1_000, 4_000).fan_certainty is FrameDeliveryCertainty.UNKNOWN

    clock.set(5_000)
    raw.effect("set_duty_cycle", pwm=60.0)
    delivered.set_duty_cycle(60.0)
    clock.set(7_000)
    recovered = delivered.journal.integrate(5_000, 7_000)
    assert recovered.fan_certainty is FrameDeliveryCertainty.EXACT
    assert recovered.fan_duty_integral_seconds == pytest.approx(1.2)
    assert not [
        edge
        for edge in delivered.journal.edges
        if edge.channel is ActuatorChannel.FAN_PWM and 1_000 < edge.monotonic_ms < 5_000
    ]


def test_ramp_uncertainty_survives_unrelated_exact_auger_readback() -> None:
    delivered, raw, clock = _platform()
    _establish_all_channels(delivered, raw, clock)
    clock.set(1_000)
    raw.effect("pwm_fan_ramp", fan=True, pwm=20.0)
    delivered.pwm_fan_ramp(5.0, 20.0, 80.0)

    clock.set(2_000)
    raw.effect("auger_on", auger=True)
    delivered.auger_on()
    clock.set(3_000)

    integral = delivered.journal.integrate(2_000, 3_000)
    assert integral.auger_certainty is FrameDeliveryCertainty.EXACT
    assert integral.auger_on_seconds == pytest.approx(1.0)
    assert integral.fan_certainty is FrameDeliveryCertainty.UNKNOWN


def test_fan_off_recovers_fan_state_but_pwm_ramp_remains_uncertain_until_replaced() -> None:
    delivered, raw, clock = _platform()
    _establish_all_channels(delivered, raw, clock)
    clock.set(1_000)
    raw.effect("pwm_fan_ramp", fan=True, pwm=20.0)
    delivered.pwm_fan_ramp(5.0, 20.0, 80.0)

    clock.set(2_000)
    raw.effect("fan_off", fan=False)
    delivered.fan_off()
    clock.set(3_000)
    after_fan_off = delivered.journal.integrate(2_000, 3_000)
    assert after_fan_off.fan_end_active is False
    assert after_fan_off.fan_certainty is FrameDeliveryCertainty.UNKNOWN

    clock.set(4_000)
    raw.effect("set_duty_cycle", pwm=60.0)
    delivered.set_duty_cycle(60.0)
    clock.set(5_000)
    replaced = delivered.journal.integrate(4_000, 5_000)
    assert replaced.fan_certainty is FrameDeliveryCertainty.EXACT
    assert replaced.fan_end_active is False
    assert replaced.fan_duty_integral_seconds == 0.0


def test_uncertainty_does_not_fabricate_state_and_exact_readback_recovers_forward_only() -> None:
    delivered, raw, clock = _platform()
    _establish_all_channels(delivered, raw, clock)
    delivered.journal.mark_uncertain("external-observation-gap", 1_000, channel=ActuatorChannel.AUGER)
    clock.set(2_000)

    uncertain = delivered.journal.integrate(0, 2_000)
    assert uncertain.auger_certainty is FrameDeliveryCertainty.UNKNOWN
    assert "external-observation-gap" in uncertain.unknown_reasons

    clock.set(2_000)
    raw.effect("auger_on", auger=True)
    delivered.auger_on()
    clock.set(3_000)
    recovered = delivered.journal.integrate(2_000, 3_000)
    assert recovered.auger_certainty is FrameDeliveryCertainty.EXACT
    assert recovered.auger_on_seconds == pytest.approx(1.0)
    assert delivered.journal.integrate(500, 3_000).auger_certainty is FrameDeliveryCertainty.UNKNOWN


def test_bounded_edge_eviction_marks_only_spanning_queries_uncertain() -> None:
    assert isinstance(ACTUATION_EDGE_CAPACITY, int)
    assert ACTUATION_EDGE_CAPACITY > 180
    delivered, raw, clock = _platform(capacity=4)
    _establish_all_channels(delivered, raw, clock)

    for index, active in enumerate((True, False, True, False, True, False), start=1):
        clock.set(index)
        method = "auger_on" if active else "auger_off"
        raw.effect(method, auger=active)
        getattr(delivered, method)()
    clock.set(7)

    spanning = delivered.journal.integrate(0, 7)
    retained = delivered.journal.integrate(3, 7)
    assert spanning.auger_certainty is FrameDeliveryCertainty.UNKNOWN
    assert any("evict" in reason.lower() for reason in spanning.unknown_reasons)
    assert retained.auger_certainty is FrameDeliveryCertainty.EXACT


def test_concurrent_edge_writes_and_integrations_are_deterministic_and_thread_safe() -> None:
    delivered, raw, clock = _platform(capacity=64)
    _establish_all_channels(delivered, raw, clock)
    rendezvous = threading.Barrier(2)
    failures: list[BaseException] = []
    observed: list[DeliveredActuationIntegral] = []

    def writer() -> None:
        try:
            for index in range(1, 21):
                active = index % 2 == 1
                clock.set(index * 10)
                method = "auger_on" if active else "auger_off"
                raw.effect(method, auger=active)
                getattr(delivered, method)()
                rendezvous.wait()
                rendezvous.wait()
        except BaseException as exc:
            failures.append(exc)
            rendezvous.abort()

    def reader() -> None:
        try:
            for index in range(1, 21):
                rendezvous.wait()
                observed.append(delivered.journal.integrate(0, index * 10))
                rendezvous.wait()
        except BaseException as exc:
            failures.append(exc)
            rendezvous.abort()

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)

    assert not [thread for thread in threads if thread.is_alive()]
    assert failures == []
    assert len(observed) == 20
    assert all(result.auger_certainty is FrameDeliveryCertainty.EXACT for result in observed)
    clock.set(210)
    final = delivered.journal.integrate(0, 210)
    assert final.auger_on_seconds == pytest.approx(0.1)


def test_invalid_intervals_and_clock_regression_latches_uncertainty_without_suppressing_driver() -> None:
    delivered, raw, clock = _platform()
    for start_ms, end_ms in ((0, 0), (2, 1), (-1, 1)):
        with pytest.raises(ValueError):
            delivered.journal.integrate(start_ms, end_ms)

    clock.set(100)
    raw.effect("auger_off", auger=False)
    delivered.auger_off()
    token = object()
    raw.results["auger_on"] = token
    clock.set(99)
    raw.effect("auger_on", auger=True)

    assert delivered.auger_on() is token
    assert raw.counts["auger_on"] == 1
    clock.set(200)
    uncertain = delivered.journal.integrate(100, 200)
    assert uncertain.auger_certainty is FrameDeliveryCertainty.UNKNOWN
    assert any("clock" in reason for reason in uncertain.unknown_reasons)


def test_driver_exception_has_precedence_over_clock_capture_failure() -> None:
    delivered, raw, clock = _platform()
    clock.set(100)
    raw.effect("auger_off", auger=False)
    delivered.auger_off()
    failure = RuntimeError("actuator failed after clock regression")
    raw.failures["auger_on"] = failure
    clock.set(99)

    with pytest.raises(RuntimeError) as caught:
        delivered.auger_on()

    assert caught.value is failure
    assert raw.counts["auger_on"] == 1
    clock.set(200)
    assert delivered.journal.integrate(100, 200).auger_certainty is FrameDeliveryCertainty.UNKNOWN


def test_edges_and_integrals_are_immutable_owned_snapshots() -> None:
    delivered, raw, clock = _platform()
    raw.share_readback = True
    raw.effect("auger_on", auger=True)
    clock.set(100)

    delivered.auger_on()
    clock.set(200)
    edge = delivered.journal.edges[0]
    integral = delivered.journal.integrate(100, 200)
    raw.actual["auger"] = False

    assert edge.current_value is True
    assert delivered.journal.edges[0].current_value is True
    assert type(delivered.journal.edges) is tuple
    assert type(integral.unknown_reasons) is tuple
    with pytest.raises(FrozenInstanceError):
        edge.current_value = False  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        integral.auger_on_seconds = 0.0  # type: ignore[misc]


def test_unrelated_platform_apis_delegate_without_creating_trajectory_edges() -> None:
    delivered, raw, _clock = _platform()
    cleanup_result = object()
    raw.results["cleanup"] = cleanup_result

    assert delivered.get_input_status() is raw.input_status
    assert delivered.auger_timing() is raw.capability
    assert delivered.supported_commands("request") is raw.command_report
    assert delivered.igniter_on() is None
    assert delivered.power_on() is None
    assert delivered.cleanup() is cleanup_result
    assert raw.counts["get_input_status"] == 1
    assert raw.counts["auger_timing"] == 1
    assert raw.counts["supported_commands"] == 1
    assert raw.counts["igniter_on"] == 1
    assert raw.counts["power_on"] == 1
    assert raw.counts["cleanup"] == 1
    assert delivered.journal.edges == ()


def test_build_devices_returns_delivered_wrapper_and_preserves_platform_behavior(ds) -> None:
    from controller.runtime.devices import build_devices
    from tests.unit.runtime._device_helpers import _RecordingLogger, _settings

    devices, errors = build_devices(
        _settings(),
        errors=[],
        event_log=_RecordingLogger(),
        control_log=_RecordingLogger(),
    )

    assert errors == []
    grill = cast(DeliveredGrillPlatform, devices.grill_platform)
    assert isinstance(grill, DeliveredGrillPlatform)
    assert grill.journal is not None
    timing = grill.auger_timing()
    assert timing is not None
    grill.auger_on()
    assert grill.get_output_status()["auger"] is True
    grill.auger_off()
    assert grill.get_output_status()["auger"] is False


class _ForbiddenDependencyFinder(importlib.abc.MetaPathFinder):
    def __init__(self, roots: tuple[str, ...]) -> None:
        self.roots = roots
        self.attempts: list[str] = []

    def find_spec(self, fullname: str, path: object = None, target: object = None) -> None:
        if any(fullname == root or fullname.startswith(f"{root}.") for root in self.roots):
            self.attempts.append(fullname)
            raise AssertionError(f"actuation delivery imported forbidden dependency {fullname}")


def test_module_load_has_no_mpc_model_learning_runtime_or_persistence_dependency(monkeypatch) -> None:
    module_name = "controller.runtime.actuation_delivery"
    forbidden = (
        "common.persistence",
        "controller.mpc",
        "controller.model_learning",
        "controller.runtime.learning_trajectory",
        "controller.runtime.model_persistence",
    )
    for loaded_name in tuple(sys.modules):
        if loaded_name == module_name or any(
            loaded_name == root or loaded_name.startswith(f"{root}.") for root in forbidden
        ):
            monkeypatch.delitem(sys.modules, loaded_name, raising=False)
    finder = _ForbiddenDependencyFinder(forbidden)
    monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])

    loaded = importlib.import_module(module_name)

    assert loaded.ActuationDeliveryJournal is not None
    assert finder.attempts == []
