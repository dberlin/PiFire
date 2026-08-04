"""Deterministic, framed simulator records for linear-model calibration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np

from controller.grill_sim import GrillSim, MAKGrillSim

from .contracts import SignalRecord
from .data import resample_record, validate_record
FRAME_S: Final = 20




@dataclass(frozen=True, slots=True)
class ProgramSegment:
    """One plateau with binary pseudo-random input perturbations."""

    duration_s: int
    center_q: float
    perturbation_q: float
    dwell_s: int

    def __post_init__(self) -> None:
        if self.duration_s <= 0 or self.dwell_s <= 0:
            raise ValueError("segment duration and dwell must be positive")
        if self.duration_s % self.dwell_s:
            raise ValueError("segment duration must contain complete dwells")
        if not 0.0 <= self.center_q <= 1.0:
            raise ValueError("segment center_q must be within [0, 1]")
        if self.perturbation_q < 0.0:
            raise ValueError("segment perturbation_q must be non-negative")
        if not 0.0 <= self.center_q - self.perturbation_q:
            raise ValueError("segment perturbation drives q below zero")
        if not self.center_q + self.perturbation_q <= 1.0:
            raise ValueError("segment perturbation drives q above one")


@dataclass(frozen=True, slots=True)
class CalibrationProgram:
    """Immutable input program with the bake-off's fixed 20-second frames."""

    segments: tuple[ProgramSegment, ...]
    coast_duration_s: int
    frame_s: int = FRAME_S

    def __post_init__(self) -> None:
        object.__setattr__(self, "segments", tuple(self.segments))
        if not self.segments:
            raise ValueError("calibration program needs at least one segment")
        if self.coast_duration_s <= 0:
            raise ValueError("coast_duration_s must be positive")
        if self.frame_s != FRAME_S:
            raise ValueError("frame_s must be exactly 20 seconds")
        if self.coast_duration_s % FRAME_S:
            raise ValueError("coast_duration_s must end on a complete frame")
        if any(segment.duration_s % FRAME_S for segment in self.segments):
            raise ValueError("each segment must end on a complete frame")


DEFAULT_CALIBRATION_PROGRAM: Final = CalibrationProgram(
    segments=(
        ProgramSegment(720, 0.15, 0.08, 120),
        ProgramSegment(720, 0.35, 0.08, 120),
        ProgramSegment(720, 0.65, 0.08, 120),
    ),
    coast_duration_s=600,
)
"""The standard three-plateau program for the generic simulated grill."""

MAK_CALIBRATION_PROGRAM: Final = CalibrationProgram(
    segments=(
        ProgramSegment(2_400, 0.15, 0.08, 120),
        ProgramSegment(2_400, 0.35, 0.08, 120),
        ProgramSegment(2_400, 0.65, 0.08, 120),
    ),
    coast_duration_s=1_200,
)
"""Longer program for the MAK simulator's slow transport and chamber dynamics."""


def _program_input(program: CalibrationProgram, seed: int) -> np.ndarray:
    """Expand a plateau/PRBS program to one requested input per second."""
    rng = np.random.default_rng(seed)
    values: list[float] = []
    for segment in program.segments:
        dwells = segment.duration_s // segment.dwell_s
        values.extend([segment.center_q] * segment.dwell_s)
        for _ in range(1, dwells):
            sign = 1.0 if rng.integers(2) else -1.0
            q = segment.center_q + sign * segment.perturbation_q
            values.extend([q] * segment.dwell_s)
    values.extend([0.0] * program.coast_duration_s)
    return np.asarray(values, dtype=np.float64)


def _simulator(plant_name: str, seed: int) -> GrillSim:
    simulators: dict[str, type[GrillSim]] = {
        "GrillSim": GrillSim,
        "MAKGrillSim": MAKGrillSim,
    }
    try:
        simulator_type = simulators[plant_name]
    except KeyError as error:
        raise ValueError(f"unknown calibration plant: {plant_name}") from error
    return simulator_type(seed=seed, fixed_fan=1.0)


def generate_calibration_record(
    plant_name: str,
    seed: int,
    config: CalibrationProgram,
) -> SignalRecord:
    """Simulate a fixed-fan calibration program and return only complete frames."""
    requested_q = _program_input(config, seed)
    plant = _simulator(plant_name, seed)
    time_s = np.arange(requested_q.size + 1, dtype=np.float64)
    temp_c = np.empty_like(time_s)
    ambient_c = np.full_like(time_s, plant.T_amb)
    temp_c[0] = plant.measured()

    pellet_accumulator = 0.0
    realized_q = np.empty_like(requested_q)
    for index, q in enumerate(requested_q):
        pellet_accumulator += q
        auger_on = pellet_accumulator >= 1.0
        if auger_on:
            pellet_accumulator -= 1.0
        realized_q[index] = float(auger_on)
        plant.step(auger_on, fan_frac=1.0)
        temp_c[index + 1] = plant.measured()

    raw = SignalRecord(
        time_s=time_s,
        temp_c=temp_c,
        q=np.append(realized_q, realized_q[-1]),
        ambient_c=ambient_c,
        provenance="simulator-calibration",
        metadata={
            "plant": plant_name,
            "seed": seed,
            "fan_frac": 1.0,
            "integration_frame_s": 1,
            "output_frame_s": config.frame_s,
        },
    )
    validate_record(raw, expected_frame_s=1.0)
    framed = resample_record(raw, config.frame_s)
    validate_record(framed, expected_frame_s=config.frame_s)
    return framed
