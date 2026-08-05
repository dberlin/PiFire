#!/usr/bin/env python3
"""Compare coupled MPC allocation curves and auger realization strategies.

This is design evidence, not production control code.  It deliberately drives
both shipped nonlinear test plants open-loop so allocator behavior is measured
without confounding it with a particular controller tuning.  It also refits the
recorded MAK cook under equivalent input representations to establish what that
record can and cannot identify.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))


import numpy as np

from controller.grill_sim import GrillSim, MAKGrillSim
from controller.mpc import _DEFAULTS
from controller.update_mpc import fit_params, fit_quality

U_MIN = 0.10
U_MAX = 0.90
FAN_MIN = 0.40
FAN_MAX = 1.00
Q_FLOOR = 0.05
PULSE_SLOT_S = 1.0
FIXED_CYCLE_S = 25
PULSE_QUANTUM_S = 2.0
PULSE_FRAME_S = 20.0
LEVELS = (0.01, 0.025, 0.05, 0.10, 0.25, 0.50, 0.75, 1.00)
PLANTS = {"GrillSim": GrillSim, "MAKGrillSim": MAKGrillSim}
DURATIONS = {"GrillSim": 4 * 3600, "MAKGrillSim": 8 * 3600}
TAIL_S = 30 * 60
ROOT = Path(__file__).resolve().parents[3]
CALIBRATION = ROOT / "tests/unit/mpc/fixtures/mak_cook_2026-08-02.csv"
OUT = Path(__file__).with_name("_mpc_pulse_allocator.json")


def _plant_calibration(plant):
    return {
        "C_c": float(plant.C_c),
        "H": float(plant.H),
        "T_amb": float(plant.T_amb),
        "deadtime_s": len(plant.transit),
        "fan_is_lever": bool(plant.fan_is_lever),
        "fixed_fan": plant.fixed_fan,
        "h_lid": float(plant.h_lid),
        "probe_tau_s": float(plant.probe_tau),
        "sigma": float(plant.sigma),
    }


def _plant_calibrations():
    return {name: _plant_calibration(factory(seed=0)) for name, factory in PLANTS.items()}


@dataclass(frozen=True)
class Command:
    auger_duty: float
    fan_frac: float


@dataclass(frozen=True)
class Row:
    plant: str
    arm: str
    q: float
    mean_temp_f: float
    band_f: float
    mean_duty: float
    duty_error: float
    switches_per_hour: float
    median_afr: float


def _old_affine(q: float) -> Command:
    frac = np.clip((q - Q_FLOOR) / (1.0 - Q_FLOOR), 0.0, 1.0)
    return Command(U_MIN + float(frac) * (U_MAX - U_MIN), FAN_MIN + float(frac) * (FAN_MAX - FAN_MIN))


def _linear(q: float) -> Command:
    q = float(np.clip(q, 0.0, 1.0))
    return Command(U_MAX * q, FAN_MIN + (FAN_MAX - FAN_MIN) * q)


def _low_fan(q: float) -> Command:
    q = float(np.clip(q, 0.0, 1.0))
    knee = 0.10
    fan_frac = 0.0 if q <= knee else (q - knee) / (1.0 - knee)
    return Command(U_MAX * q, FAN_MIN + (FAN_MAX - FAN_MIN) * fan_frac)


class FixedCycle:
    def __init__(self, duty: float):
        self.duty = duty

    def output(self, second: int) -> bool:
        phase = second % FIXED_CYCLE_S
        return phase < self.duty * FIXED_CYCLE_S


class PulseDensity:
    """One error-diffused decision per one-second hardware slot."""

    def __init__(self, duty: float):
        self.duty = duty
        self.balance = 0.0

    def output(self, second: int) -> bool:
        del second
        self.balance += self.duty
        on = self.balance >= 1.0 - 1e-12
        if on:
            self.balance -= 1.0
        return on


class FramedPulseDensity:
    """Contiguous quantized pulses with fractional on-time carried across frames."""

    def __init__(self, duty: float, frame_s: int, slot_s: int = 1):
        if frame_s % slot_s:
            raise ValueError("frame_s must be an integer multiple of slot_s")
        self.duty = duty
        self.frame_s = frame_s
        self.slot_s = slot_s
        self.balance_s = 0.0
        self.on_s = 0

    def output(self, second: int) -> bool:
        phase = second % self.frame_s
        if phase == 0:
            self.balance_s += self.duty * self.frame_s
            quanta = int((self.balance_s + 1e-12) / self.slot_s)
            self.on_s = min(self.frame_s, quanta * self.slot_s)
            self.balance_s -= self.on_s
        return phase < self.on_s


ARMS = {
    "old_affine_fixed_25s": (_old_affine, FixedCycle),
    "old_affine_pulse_1s": (_old_affine, PulseDensity),
    "linear_coupled_pulse_1s": (_linear, PulseDensity),
    "linear_coupled_1s_frame_5s": (_linear, lambda duty: FramedPulseDensity(duty, 5)),
    "linear_coupled_1s_frame_10s": (_linear, lambda duty: FramedPulseDensity(duty, 10)),
    "linear_coupled_1s_frame_15s": (_linear, lambda duty: FramedPulseDensity(duty, 15)),
    "linear_coupled_1s_frame_25s": (_linear, lambda duty: FramedPulseDensity(duty, 25)),
    "linear_coupled_2s_frame_10s": (_linear, lambda duty: FramedPulseDensity(duty, 10, 2)),
    "linear_coupled_2s_frame_20s": (_linear, lambda duty: FramedPulseDensity(duty, 20, 2)),
    "linear_coupled_2s_frame_30s": (_linear, lambda duty: FramedPulseDensity(duty, 30, 2)),
    "linear_coupled_2s_frame_50s": (_linear, lambda duty: FramedPulseDensity(duty, 50, 2)),
    "low_fan_piecewise_1s_frame_10s": (_low_fan, lambda duty: FramedPulseDensity(duty, 10)),
}


def _f_to_c(value: float) -> float:
    return (value - 32.0) * 5.0 / 9.0


def _c_to_f(value: float) -> float:
    return value * 9.0 / 5.0 + 32.0


def run_open_loop() -> list[Row]:
    rows: list[Row] = []
    for plant_name, plant_type in PLANTS.items():
        duration = DURATIONS[plant_name]
        for arm_name, (allocate, scheduler_type) in ARMS.items():
            for q in LEVELS:
                command = allocate(q)
                scheduler = scheduler_type(command.auger_duty)
                plant = plant_type(seed=0)
                temps: list[float] = []
                duties: list[float] = []
                afrs: list[float] = []
                switches = 0
                previous = False
                for second in range(duration):
                    auger_on = scheduler.output(second)
                    switches += int(auger_on != previous)
                    previous = auger_on
                    plant.step(auger_on=auger_on, fan_frac=command.fan_frac)
                    if second >= duration - TAIL_S:
                        temps.append(_c_to_f(plant.true_Tc))
                        duties.append(float(auger_on))
                        afrs.append(float(plant.afr))
                rows.append(
                    Row(
                        plant=plant_name,
                        arm=arm_name,
                        q=q,
                        mean_temp_f=float(np.mean(temps)),
                        band_f=float(np.max(temps) - np.min(temps)),
                        mean_duty=float(np.mean(duties)),
                        duty_error=float(np.mean(duties) - command.auger_duty),
                        switches_per_hour=switches / (duration / 3600.0),
                        median_afr=float(np.median(afrs)),
                    )
                )
    return rows


def _fit(t: np.ndarray, temp: np.ndarray, input_signal: np.ndarray) -> dict[str, float | bool]:
    init = {key: float(_DEFAULTS[key]) for key in ("C_c", "h_amb", "K_Q", "theta")}
    result = fit_params(
        t,
        temp,
        input_signal,
        T_amb=float(_DEFAULTS["T_amb"]),
        init=init,
        sigma=float(_DEFAULTS["sigma"]),
        n_delay=int(_DEFAULTS["n_delay"]),
    )
    rmse, max_abs_error = fit_quality(
        t,
        temp,
        input_signal,
        result,
        T_amb=float(_DEFAULTS["T_amb"]),
    )
    summary = {
        key: (bool(value) if key == "converged" else float(value))
        for key, value in result.items()
        if key in {"C_c", "h_amb", "K_Q", "theta", "sigma", "converged", "nfev"}
    }
    summary.update(rmse=rmse, max_abs_error=max_abs_error)
    return summary


def run_calibration_fits() -> dict[str, dict[str, float | bool]]:
    with CALIBRATION.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    t = np.asarray([float(row["time_s"]) for row in rows])
    t -= t[0]
    temp = np.asarray([float(row["temp_c"]) for row in rows])
    q_percent = np.asarray([float(row["Q"]) for row in rows])
    q = q_percent / 100.0
    old_auger = U_MIN + np.clip((q - Q_FLOOR) / (1.0 - Q_FLOOR), 0.0, 1.0) * (U_MAX - U_MIN)
    proposed_auger = U_MAX * q
    return {
        "recorded_Q_percent": _fit(t, temp, q_percent),
        "normalized_q": _fit(t, temp, q),
        "inferred_old_auger_duty": _fit(t, temp, old_auger),
        "proposed_linear_auger_duty": _fit(t, temp, proposed_auger),
    }


def summarize(rows: list[Row], fits: dict[str, dict[str, float | bool]]) -> None:
    print("OPEN_LOOP")
    for plant in PLANTS:
        print(plant)
        for arm in ARMS:
            selected = [row for row in rows if row.plant == plant and row.arm == arm]
            floor = next(row for row in selected if row.q == 0.01)
            mid = next(row for row in selected if row.q == 0.50)
            print(
                f"  {arm}: q=.01 T={floor.mean_temp_f:.1f}F duty={floor.mean_duty:.3f}; "
                f"q=.50 T={mid.mean_temp_f:.1f}F band={mid.band_f:.2f}F "
                f"switches/h={mid.switches_per_hour:.1f} duty_err={mid.duty_error:+.4f}"
            )
    print("CALIBRATION_FITS")
    for name, fit in fits.items():
        print(
            f"  {name}: rmse={fit['rmse']:.4f}C K_Q={fit['K_Q']:.6g} "
            f"theta={fit['theta']:.2f}s converged={fit['converged']} nfev={fit['nfev']:.0f}"
        )


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    rows = run_open_loop()
    fits = run_calibration_fits()
    payload = {
        "header": {
            "format_version": 1,
            "regeneration_command": (
                "uv run --no-sync python docs/superpowers/experiments/mpc_pulse_allocator.py "
                "--out docs/superpowers/experiments/_mpc_pulse_allocator.json"
            ),
        },
        "conditions": {
            "levels": LEVELS,
            "pulse_slot_s": PULSE_SLOT_S,
            "selected_scheduler": {"pulse_quantum_s": PULSE_QUANTUM_S, "frame_s": PULSE_FRAME_S},
            "plant_calibration": _plant_calibrations(),
            "fixed_cycle_s": FIXED_CYCLE_S,
            "fan_range": (FAN_MIN, FAN_MAX),
            "auger_range": (0.0, U_MAX),
            "seeds": (0,),
            "durations_s": DURATIONS,
            "tail_s": TAIL_S,
            "calibration": str(CALIBRATION.relative_to(ROOT)),
            "calibration_fan": "100% throughout; allocator fan curves are not identifiable from this record",
        },
        "open_loop": [asdict(row) for row in rows],
        "calibration_fits": fits,
    }
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    summarize(rows, fits)
    print(f"WROTE {args.out}")


if __name__ == "__main__":
    main()
