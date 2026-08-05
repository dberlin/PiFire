"""Compatibility adapter for the production scheduled-ARX implementation."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from controller.linear_mpc.arx import ScheduledARX as _ProductionScheduledARX
from controller.linear_mpc.arx import ScheduledARXConfig as ARXConfig
from controller.linear_mpc.contracts import FrameObservation

from .contracts import FloatArray, Observation, SignalRecord, UpdateOutcome

TEMPERATURE_KNOTS_C = (82.2, 162.8, 232.2, 315.6)


class ScheduledARX(_ProductionScheduledARX):
    """Adapt legacy bake-off records to the production frame contract."""

    def fit(self, record: SignalRecord) -> None:
        super().fit(_record_frames(record))

    def forecast(
        self,
        prefix: SignalRecord,
        q_future: FloatArray,
        ambient_future: FloatArray,
    ) -> FloatArray:
        return super().forecast(_record_frames(prefix), q_future, ambient_future)

    def observe(self, observation: Observation) -> UpdateOutcome:
        return super().observe(_observation_frame(observation))

    def track(self, observation: Observation) -> UpdateOutcome:
        return super().track(_observation_frame(observation))

    def snapshot(self) -> Mapping[str, object]:
        """Present the frozen v1 evidence view required by historical tests."""
        snapshot = super().snapshot()
        status = snapshot["status"]
        assert isinstance(status, dict)
        legacy = {
            "schema": "scheduled-arx/v1",
            "order": {"na": self.config.na, "nb": self.config.nb},
            "delay_steps": snapshot["active_delay"],
            "delay_seconds": float(snapshot["active_delay"]) * 20.0,
            "steady_gain": status["steady_gain"],
            "knots_c": status["knots_c"],
            "regions": status["regions"],
            "plausibility_bounds": {
                "max_dc_gain_c_per_q": status["max_dc_gain_c_per_q"],
                "max_ar_pole": status["max_ar_pole"],
            },
            "update_timing": {
                "last_observation_time_s": status["last_observation_time_s"],
                "refreshes": status["refreshes"],
                "max_forecast_deviation_c": status["max_forecast_deviation_c"],
                "last_refresh_sample": status["last_refresh_sample"],
            },
        }
        return _freeze(legacy)


def _record_frames(record: SignalRecord) -> tuple[FrameObservation, ...]:
    if not (record.time_s.size == record.temp_c.size == record.q.size == record.ambient_c.size):
        raise ValueError("record signal arrays must have equal lengths")
    return tuple(
        _frame(float(time_s), float(temp_c), float(q), float(ambient_c))
        for time_s, temp_c, q, ambient_c in zip(
            record.time_s, record.temp_c, record.q, record.ambient_c, strict=True
        )
    )


def _observation_frame(observation: Observation) -> FrameObservation:
    return _frame(observation.time_s, observation.temp_c, observation.q, observation.ambient_c)


def _frame(frame_end_s: float, temp_c: float, q: float, ambient_c: float) -> FrameObservation:
    return FrameObservation(
        frame_start_s=frame_end_s - 20.0,
        frame_end_s=frame_end_s,
        temp_c=temp_c,
        setpoint_c=temp_c,
        ambient_c=ambient_c,
        requested_q=q,
        realized_q=q,
        requested_auger_duty=q,
        delivered_on_s=q * 20.0,
        requested_fan_duty=None,
        actual_fan_duty=None,
        result_revision=0,
        output_source="bakeoff-adapter",
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=True,
        role_generation=0,
    )


def _freeze(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value
