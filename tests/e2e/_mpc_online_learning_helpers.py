"""Shared fixtures for end-to-end MPC learning tests."""

from __future__ import annotations

import logging
from typing import Any

from common.control_trace import ControllerType
from controller.grill_sim import MAKGrillSim
from controller.mpc_model import replay_delay_chain_arrays, simulate_grey_box_intervals
from controller.runtime.control_trace_session import TraceModelAuthority, TraceSessionContext

_FRAME_SECONDS = 20
_FIT_SAMPLES = 120
_LOAD_LEVELS = (0.20, 0.55, 0.90)
_LEVEL_DWELL_FRAMES = 8
_U_MAX = 0.90
_SETPOINT_C = 200.0
_CYCLE = {"u_min": 0.1, "u_max": _U_MAX}
_MAK_SUPPORT_PARAMETERS = {
    "C_c": MAKGrillSim.C_C,
    "K_Q": MAKGrillSim.HEAT_PER_UNIT * _U_MAX,
    "T_amb": MAKGrillSim.AMBIENT_C,
    "h_amb": 0.546,
    "n_delay": 8,
    "sigma": 1.4e-09,
    "theta": float(MAKGrillSim.DEADTIME),
}
_LOGGER = logging.getLogger("tests.e2e.test_mpc_online_learning_e2e")


class _TestLogger:
    def info(self, message: str) -> None:
        _LOGGER.info(message)

    def warning(self, message: str) -> None:
        _LOGGER.warning(message)

    def error(self, message: str) -> None:
        _LOGGER.error(message)


_TEST_LOGGER = _TestLogger()


def _mak_grey_corpus_rows() -> list[dict[str, Any]]:
    pre_roll_count = 21
    pre_roll_load = (0.2,) * pre_roll_count
    scored_load = tuple(
        _LOAD_LEVELS[(index // _LEVEL_DWELL_FRAMES) % len(_LOAD_LEVELS)] for index in range(_FIT_SAMPLES)
    )
    delay = replay_delay_chain_arrays(
        (_FRAME_SECONDS,) * pre_roll_count,
        pre_roll_load,
        theta=_MAK_SUPPORT_PARAMETERS["theta"],
        n_delay=int(_MAK_SUPPORT_PARAMETERS["n_delay"]),
        initial_load=pre_roll_load[0],
    )
    temperature = simulate_grey_box_intervals(
        (_FRAME_SECONDS,) * _FIT_SAMPLES,
        scored_load,
        (_MAK_SUPPORT_PARAMETERS["T_amb"],) * _FIT_SAMPLES,
        C_c=_MAK_SUPPORT_PARAMETERS["C_c"],
        h_amb=_MAK_SUPPORT_PARAMETERS["h_amb"],
        T0=20.0,
        K_Q=_MAK_SUPPORT_PARAMETERS["K_Q"],
        sigma=_MAK_SUPPORT_PARAMETERS["sigma"],
        theta=_MAK_SUPPORT_PARAMETERS["theta"],
        n_delay=int(_MAK_SUPPORT_PARAMETERS["n_delay"]),
        initial_delay_states=delay,
    )
    rows: list[dict[str, Any]] = []
    for index, load in enumerate(scored_load):
        sequence = pre_roll_count + index
        frame_start_ms = 20_000_000 + sequence * _FRAME_SECONDS * 1_000
        rows.append(
            {
                "frame_start_ms": frame_start_ms,
                "frame_end_ms": frame_start_ms + _FRAME_SECONDS * 1_000,
                "temp_c": float(temperature[index]),
                "ambient_c": _MAK_SUPPORT_PARAMETERS["T_amb"],
                "observation_sequence": sequence,
                "delivered_on_seconds": load * _U_MAX * _FRAME_SECONDS,
                "realized_combustion_load": load,
            }
        )
    return rows


def _trace_context(
    snapshot: dict[str, Any],
    config: dict[str, Any],
    cook_id: str,
    *,
    setpoint_c: float = _SETPOINT_C,
    ambient_c: float = MAKGrillSim.AMBIENT_C,
) -> TraceSessionContext:
    return TraceSessionContext(
        controller=ControllerType.MPC,
        controller_config=config,
        temperature_unit="C",
        control_period_seconds=float(config["control_period"]),
        fallback_model=TraceModelAuthority(snapshot, "runner"),
        runner_snapshot_fallback_safe=True,
        pulse_slot_seconds=1.0,
        pulse_frame_seconds=float(_FRAME_SECONDS),
        fan_authority=False,
        fan_pwm_capable=False,
        fan_min_duty=0.0,
        fan_max_duty=1.0,
        setpoint=setpoint_c,
        ambient_temperature=ambient_c,
        software_version="e2e",
        build_version="e2e",
        cook_id=cook_id,
        runner_generation=0,
    )
