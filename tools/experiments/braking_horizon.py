#!/usr/bin/env python3
"""Measure delayed post-cut coast through PiFire's production actuation path.

Every fixed-seed run starts an untouched production simulator at ambient, heats
with the shipped 0.9 auger authority through the production 2 s / 20 s pulse
scheduler, and cuts normalized combustion load to zero while still rising
through 225, 350, or 450 F.  MPC fan authority is disabled: the simulator keeps
its uncontrolled production baseline fan behavior before and after the cut.

Regenerate committed evidence with:

    python -m docs.superpowers.experiments.braking_horizon
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]

from controller.grill_sim import GrillSim, MAKGrillSim
from controller.model_promotion import T_FLOOR_C, T_HAZARD_C, braking_distance
from controller.mpc_allocator import allocate
from controller.mpc_config import DEFAULT_MPC_CONFIG
from controller.runtime.logic.pulse import PulseScheduler
from grillplat.actuator_capabilities import AUGER_TIMING

OUTPUT = REPO / "docs" / "superpowers" / "experiments" / "_braking_horizon.json"
EXPERIMENT_ID = "braking-horizon-coast-v2"
TEMPERATURE_SOURCE = "GrillSim.true_Tc (noise-free chamber state)"
REGENERATION_COMMAND = "python -m docs.superpowers.experiments.braking_horizon"
SEEDS = (0, 1, 2)
TARGETS_F = (225.0, 350.0, 450.0)
CUT_TARGET_TOLERANCE_F = 5.0
COAST_SECONDS = 3_600
SHIPPED_U_MAX = 0.9
MPC_FAN_AUTHORITY_ENABLED = False
UNCONTROLLED_FAN_FRACTION = 0.65
PLANTS = (("GrillSim", GrillSim), ("MAKGrillSim", MAKGrillSim))


def _to_celsius(fahrenheit: float) -> float:
    return (fahrenheit - 32.0) * 5.0 / 9.0


def _step(
    plant: GrillSim,
    scheduler: PulseScheduler,
    allocation_duty: float,
    *,
    at_s: int,
    actual_auger_on: bool,
) -> bool:
    """Advance one real pulse-scheduler tick and return observed auger state."""
    decision = scheduler.advance(allocation_duty, at_s=float(at_s), actual_auger_on=actual_auger_on)
    actual_auger_on = decision.command_on
    # Fan authority is intentionally disabled in allocate(). The simulator's
    # uncontrolled baseline fan is therefore the only fan behavior applied.
    plant.step(auger_on=actual_auger_on, fan_frac=UNCONTROLLED_FAN_FRACTION, lid_open=False)
    return actual_auger_on


def _run_coast(plant_type: type[GrillSim], seed: int, target_f: float) -> dict[str, float | int | str | bool]:
    """Cross one in-range target while rising, cut through scheduler, and coast."""
    plant = plant_type(seed=seed, fan_is_lever=False)
    preheat = allocate(
        1.0,
        u_max=SHIPPED_U_MAX,
        fan_min_pct=float(DEFAULT_MPC_CONFIG["fan_min_pct"]),
        fan_max_pct=float(DEFAULT_MPC_CONFIG["fan_max_pct"]),
        enable_fan=MPC_FAN_AUTHORITY_ENABLED,
    )
    cut = allocate(
        0.0,
        u_max=SHIPPED_U_MAX,
        fan_min_pct=float(DEFAULT_MPC_CONFIG["fan_min_pct"]),
        fan_max_pct=float(DEFAULT_MPC_CONFIG["fan_max_pct"]),
        enable_fan=MPC_FAN_AUTHORITY_ENABLED,
    )
    scheduler = PulseScheduler(maximum_request=preheat.u_max)
    actual_auger_on = False
    target_c = _to_celsius(target_f)
    previous_temperature = plant.true_Tc
    cut_slope_c_per_s = 0.0
    elapsed_s = 0

    # This upper bound only prevents an invalid simulator configuration from
    # looping forever; successful rows always cut at the target crossing.
    for elapsed_s in range(1, 36_001):
        actual_auger_on = _step(plant, scheduler, preheat.auger_duty, at_s=elapsed_s, actual_auger_on=actual_auger_on)
        cut_slope_c_per_s = plant.true_Tc - previous_temperature
        if previous_temperature < target_c <= plant.true_Tc and cut_slope_c_per_s > 0.0:
            break
        previous_temperature = plant.true_Tc
    else:
        raise ValueError(f"{plant_type.__name__}/{seed} did not cross {target_f:g} F while rising")

    cut_temperature = plant.true_Tc
    peak_temperature = cut_temperature
    seconds_to_peak = 0
    for coast_second in range(1, COAST_SECONDS + 1):
        elapsed_s += 1
        actual_auger_on = _step(plant, scheduler, cut.auger_duty, at_s=elapsed_s, actual_auger_on=actual_auger_on)
        if plant.true_Tc > peak_temperature:
            peak_temperature = plant.true_Tc
            seconds_to_peak = coast_second

    return {
        "plant": plant_type.__name__,
        "seed": seed,
        "target_f": target_f,
        "cut_temperature_c": cut_temperature,
        "peak_temperature_c": peak_temperature,
        "rise_c": peak_temperature - cut_temperature,
        "seconds_to_peak": seconds_to_peak,
        "cut_was_rising": cut_slope_c_per_s > 0.0,
    }


def _nominal_model_bound() -> float:
    """The retained model coast product recorded for comparison only."""
    return max(
        braking_distance(dict(DEFAULT_MPC_CONFIG), t_ref)
        for t_ref in (T_FLOOR_C, T_HAZARD_C)
        if t_ref > float(DEFAULT_MPC_CONFIG["T_amb"])
    )


def _validate(payload: dict[str, Any]) -> None:
    """Reject incomplete, non-production, or non-braking evidence."""
    required_payload_keys = {
        "experiment",
        "regeneration_command",
        "conditions",
        "nominal_model_bound_s",
        "rows",
        "maximum_measured_rise_c",
    }
    if set(payload) != required_payload_keys:
        raise ValueError("evidence envelope has missing or unexpected fields")
    if payload["experiment"] != EXPERIMENT_ID:
        raise ValueError("evidence must record the braking coast experiment identity")
    if payload["regeneration_command"] != REGENERATION_COMMAND:
        raise ValueError("evidence must record the canonical regeneration command")

    conditions = payload["conditions"]
    if not isinstance(conditions, dict):
        raise TypeError("evidence must record measurement conditions")
    expected_conditions = {
        "plants": [name for name, _ in PLANTS],
        "seeds": list(SEEDS),
        "targets_f": list(TARGETS_F),
        "cut_target_tolerance_f": CUT_TARGET_TOLERANCE_F,
        "coast_seconds": COAST_SECONDS,
        "temperature_source": TEMPERATURE_SOURCE,
        "calibration_mutations": False,
        "allocator": {
            "normalized_combustion_load": {"preheat": 1.0, "cut": 0.0},
            "u_max": SHIPPED_U_MAX,
            "fan_enabled": MPC_FAN_AUTHORITY_ENABLED,
            "fan_behavior": "uncontrolled",
        },
        "pulse_scheduler": {
            "pulse_seconds": float(AUGER_TIMING.pulse_s),
            "frame_seconds": float(AUGER_TIMING.frame_s),
            "actual_auger_feedback": "commanded",
        },
    }
    if conditions != expected_conditions:
        raise ValueError("evidence conditions do not match the canonical production coast experiment")

    required_row_keys = {
        "plant",
        "seed",
        "target_f",
        "cut_temperature_c",
        "peak_temperature_c",
        "rise_c",
        "seconds_to_peak",
        "cut_was_rising",
    }
    rows = payload.get("rows")
    if not isinstance(rows, list) or len(rows) != len(PLANTS) * len(SEEDS) * len(TARGETS_F):
        raise ValueError("evidence must contain every plant/seed/target condition")

    seen: set[tuple[str, int, float]] = set()
    positive_rise_seen = False
    for row in rows:
        if not isinstance(row, dict) or set(row) != required_row_keys:
            raise ValueError("evidence row has missing or unexpected fields")
        plant, seed, target_f = row["plant"], row["seed"], row["target_f"]
        if (
            not isinstance(plant, str)
            or not isinstance(seed, int)
            or not isinstance(target_f, (int, float))
            or (plant, seed, float(target_f)) in seen
        ):
            raise ValueError("evidence rows must have unique, typed plant/seed/target conditions")
        seen.add((plant, seed, float(target_f)))
        for key in required_row_keys - {"plant", "seed", "cut_was_rising"}:
            value = row[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"evidence row {plant}/{seed}/{target_f} has non-finite {key}")
        if row["cut_was_rising"] is not True:
            raise ValueError(f"evidence row {plant}/{seed}/{target_f} was not cut while rising")
        if abs((float(row["cut_temperature_c"]) * 9.0 / 5.0 + 32.0) - float(target_f)) > CUT_TARGET_TOLERANCE_F:
            raise ValueError(f"evidence row {plant}/{seed}/{target_f} cut away from target")
        rise = float(row["rise_c"])
        measured_rise = float(row["peak_temperature_c"]) - float(row["cut_temperature_c"])
        if rise < 0.0 or not math.isclose(rise, measured_rise, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError(f"evidence row {plant}/{seed}/{target_f} has invalid peak/rise arithmetic")
        seconds_to_peak = row["seconds_to_peak"]
        if (
            isinstance(seconds_to_peak, bool)
            or not isinstance(seconds_to_peak, int)
            or not 0 <= seconds_to_peak <= COAST_SECONDS
            or (rise > 0.0) != (seconds_to_peak > 0)
        ):
            raise ValueError(f"evidence row {plant}/{seed}/{target_f} has invalid time-to-peak")
        if rise > 0.0:
            positive_rise_seen = True
    expected_conditions = {(name, seed, target) for name, _ in PLANTS for seed in SEEDS for target in TARGETS_F}
    if seen != expected_conditions:
        raise ValueError("evidence rows must cover every required plant/seed/target condition")

    if not positive_rise_seen:
        raise ValueError("evidence must contain a positive post-cut rise with time-to-peak")
    nominal_bound = payload["nominal_model_bound_s"]
    maximum_rise = payload["maximum_measured_rise_c"]
    expected_nominal_bound = _nominal_model_bound()
    if (
        isinstance(nominal_bound, bool)
        or not isinstance(nominal_bound, (int, float))
        or not math.isfinite(nominal_bound)
        or not math.isclose(float(nominal_bound), expected_nominal_bound, rel_tol=0.0, abs_tol=1e-12)
    ):
        raise ValueError("nominal model braking bound does not match the shipped model")
    if (
        isinstance(maximum_rise, bool)
        or not isinstance(maximum_rise, (int, float))
        or not math.isfinite(maximum_rise)
        or maximum_rise < 0.0
    ):
        raise ValueError("maximum measured rise must be finite and non-negative")
    if not math.isclose(float(maximum_rise), max(float(row["rise_c"]) for row in rows), rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("maximum measured rise must equal the maximum row rise")


def measure() -> dict[str, Any]:
    """Produce self-describing coast evidence without mutating calibration."""
    rows = [
        _run_coast(plant_type, seed, target_f) for _, plant_type in PLANTS for seed in SEEDS for target_f in TARGETS_F
    ]
    payload: dict[str, Any] = {
        "experiment": EXPERIMENT_ID,
        "regeneration_command": REGENERATION_COMMAND,
        "conditions": {
            "plants": [name for name, _ in PLANTS],
            "seeds": list(SEEDS),
            "targets_f": list(TARGETS_F),
            "cut_target_tolerance_f": CUT_TARGET_TOLERANCE_F,
            "coast_seconds": COAST_SECONDS,
            "temperature_source": TEMPERATURE_SOURCE,
            "calibration_mutations": False,
            "allocator": {
                "normalized_combustion_load": {"preheat": 1.0, "cut": 0.0},
                "u_max": SHIPPED_U_MAX,
                "fan_enabled": MPC_FAN_AUTHORITY_ENABLED,
                "fan_behavior": "uncontrolled",
            },
            "pulse_scheduler": {
                "pulse_seconds": float(AUGER_TIMING.pulse_s),
                "frame_seconds": float(AUGER_TIMING.frame_s),
                "actual_auger_feedback": "commanded",
            },
        },
        "nominal_model_bound_s": _nominal_model_bound(),
        "rows": rows,
        "maximum_measured_rise_c": max(row["rise_c"] for row in rows),
    }
    _validate(payload)
    return payload


def main() -> None:
    payload = measure()
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    _validate(json.loads(serialized))
    OUTPUT.write_text(serialized, encoding="utf-8")
    print(f"wrote {OUTPUT}")
    print(f"nominal model braking bound: {payload['nominal_model_bound_s']:.3f} s")
    print(f"maximum measured rise: {payload['maximum_measured_rise_c']:.3f} C")
    for row in payload["rows"]:
        print(
            f"{row['plant']} seed={row['seed']} target={row['target_f']:.0f} F: "
            f"cut={row['cut_temperature_c']:.3f} C, peak={row['peak_temperature_c']:.3f} C, "
            f"rise={row['rise_c']:.3f} C, peak at {row['seconds_to_peak']} s"
        )


if __name__ == "__main__":
    main()
