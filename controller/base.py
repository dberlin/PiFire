#!/usr/bin/env python3

"""
*****************************************
 PiFire PID Controller Base Class
*****************************************

 Description: Base class for the controller.  Inherited by all controller
 modules in this package.

*****************************************
"""

"""
Imported Libraries
"""
import logging
import math
import time
from collections.abc import Mapping

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, TypeAlias, cast

from common.control_trace import ActuationMode, ControllerBranch, MpcFailureState, ResultStaleState
from controller.runtime.context import EVENT_LOG_NAME
from controller.mpc_allocator import AllocationResult
from common.persistence.protocols import JsonValue

if TYPE_CHECKING:
    from controller.model_promotion import FeasibilityReport


def _freeze_learning_json(value: JsonValue) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("learning diagnostics numbers must be finite")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("learning diagnostics object keys must be strings")
            frozen[key] = _freeze_learning_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_learning_json(item) for item in value)
    raise TypeError(f"unsupported learning diagnostics value: {type(value).__name__}")


def _thaw_learning_json(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: _thaw_learning_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_learning_json(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ControllerLearningDiagnostics:
    schema_version: int
    state: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int) or self.schema_version < 1:
            raise ValueError("learning diagnostics schema_version must be positive")
        object.__setattr__(self, "state", _freeze_learning_json(dict(self.state)))

    def as_json(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], _thaw_learning_json(self.state))


@dataclass(frozen=True, slots=True)
class PidTraceDiagnostics:
    observed_dt_seconds: float
    error: float
    proportional_term: float
    integral_term: float
    derivative_term: float
    integral_accumulator: float
    integral_clamped: bool
    derivative_input: float
    derivative_state: float
    proportional_band: float
    kp: float
    ki: float
    kd: float
    center: float
    previous_temperature: float
    previous_update_time: float
    raw_output: float
    final_output: float


@dataclass(frozen=True, slots=True)
class PidSpTraceDiagnostics(PidTraceDiagnostics):
    measured_rate: float
    predicted_temperature: float
    predicted_error: float
    tau_seconds: float
    theta_seconds: float
    stable_window_seconds: float
    center_factor: float
    new_target_before: bool
    new_target_after: bool
    target_change_temperature: float
    target_change_time: float
    branch: ControllerBranch


@dataclass(frozen=True, slots=True)
class MpcTraceDiagnostics:
    state_names: tuple[str, ...]
    state_values: tuple[float, ...]
    disturbance_estimate: float
    model_revision: int
    model_provenance: str
    raw_policy_firing_load: float | None
    equilibrium_feed_forward: float | None
    residual_move: float | None
    bounded_firing_load: float
    applied_combustion_load: float
    policy_kind: str
    failure_state: MpcFailureState
    consecutive_policy_failures: int
    solve_start_monotonic: float
    solve_end_monotonic: float
    solve_duration_seconds: float
    result_age_seconds: float = 0.0
    deadline_miss_count: int = 0
    consecutive_deadline_miss_count: int = 0
    stale_state: ResultStaleState = ResultStaleState.FRESH
    recovered: bool = False
    feasibility: "FeasibilityReport | None" = None
    model_lifecycle: Mapping[str, object] | None = None


ControllerTraceDiagnostics: TypeAlias = PidTraceDiagnostics | PidSpTraceDiagnostics | MpcTraceDiagnostics

"""
Class Definition
"""


class ControllerBase:
    def __init__(self, config, units, cycle_data, *, logger=None):
        self.config = config
        self.units = units
        self.cycle_data = cycle_data
        #: build_runner injects ControllerContext's event log here. No
        #: context reaches a controller core, so this parameter is how it
        #: arrives; the default is the name the context itself defaults to.
        self._logger = logging.getLogger(EVENT_LOG_NAME) if logger is None else logger

    def update(self, current):
        """
            Input:
            current :: Current temperature
        Output:
        cycle_ratio(u) :: Raw Cycle Ratio
        """
        return 0.0

    def set_target(self, set_point):
        """
        Input:
        set_point :: Temperature Target
        """
        self.set_point = set_point
        self.last_update = time.time()

    def set_safety_ceiling_c(self, ceiling_c):
        """The grill's configured maximum temperature, in Celsius.

        There is no separate limit for a controller: this is
        settings['safety']['maxtemp'], pushed down as it changes. A controller
        that never drives the plant beyond what it was asked for ignores it.
        """

    def get_control_period(self):
        """
        Desired re-solve / actuation period in seconds. Return None to delegate
        to Hold's framed duration. Controllers that run faster than the auger
        pulse frame (e.g. MPC) return a fixed period such as 5.0.
        """
        return None

    def actuation_mode(self) -> ActuationMode:
        """Use framed pulses for every controller request."""
        return ActuationMode.FRAMED_PULSE

    def commands_fan(self):
        """Whether this controller issues fan duty commands (vs. auger-only)."""
        return False

    def wants_async(self):
        """Whether this controller's update() should run on a background thread
        (expensive solve) rather than inline in the control loop."""
        return False

    def set_output(self, applied):
        """Report the duty that actually reached the auger.

        ``applied`` is a controller.applied_output.AppliedOutput. Controllers
        that model the plant use it so their model follows the grill rather than
        the request.
        """

    def request_calibration(self, command) -> None:
        """Queue one immutable calibration command, if this core supports it."""
        raise NotImplementedError("controller does not support calibration")

    def cancel_calibration(self, reason: str) -> None:
        """Abort calibration for a safety boundary without creating an operator revision."""
        raise NotImplementedError("controller does not support calibration")

    def get_status(self):
        """JSON-safe diagnostics for the MQTT payload, if this core exposes it."""
        if hasattr(self, "set_point"):
            return {"set_point": self.set_point}
        return None

    def get_learning_diagnostics(self) -> ControllerLearningDiagnostics | None:
        """Return an owned learning snapshot when this controller learns."""
        return None

    def trace_diagnostics(self) -> ControllerTraceDiagnostics | None:
        """Immutable typed diagnostics from the most recent completed update."""
        return None

    def trace_allocation(self) -> AllocationResult | None:
        """Immutable allocation corresponding to the most recent MPC update."""
        return None

    def get_model_snapshot(self):
        """A JSON-encodable record of learned plant parameters, or None.

        Must carry an integer `revision` that increases whenever the model
        changes; the store uses it to skip writes that would learn nothing.
        """
        return None

    def restore_model(self, snapshot):
        """Adopt a persisted snapshot. True when it was adopted.

        The store validates that a snapshot is a bounded, JSON-safe record; the
        controller validates that its numbers describe a possible grill.
        """
        return False

    def close(self) -> None:
        """Release controller-owned resources after all worker calls drain."""


def normalize_controller_output(output):
    """
    Normalize a controller's update() return into (cycle_ratio, fan).

    Legacy controllers return a float cycle ratio; the MPC controller returns
    {'cycle_ratio': float, 'fan': {'duty': pct or None}}. fan is returned only
    when a duty is present.
    """
    if isinstance(output, dict):
        ratio = float(output.get("cycle_ratio", 0.0))
        fan = output.get("fan")
        if isinstance(fan, dict) and fan.get("duty") is not None:
            return ratio, fan
        return ratio, None
    return float(output), None
