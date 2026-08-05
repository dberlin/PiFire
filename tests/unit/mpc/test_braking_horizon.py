"""Contract tests for the committed production-path coast experiment."""

from __future__ import annotations
import json

from typing import Any

import pytest

from docs.superpowers.experiments import braking_horizon


TARGETS_F = (225, 350, 450)
SEEDS = (0, 1, 2)
PLANTS = ("GrillSim", "MAKGrillSim")


def _celsius(fahrenheit: float) -> float:
    return (fahrenheit - 32.0) * 5.0 / 9.0


def _nominal_model_bound() -> float:
    return max(
        braking_horizon.braking_distance(dict(braking_horizon._DEFAULTS), reference)
        for reference in (braking_horizon.T_FLOOR_C, braking_horizon.T_HAZARD_C)
        if reference > float(braking_horizon._DEFAULTS["T_amb"])
    )


def _payload() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for plant in PLANTS:
        for seed in SEEDS:
            for target_f in TARGETS_F:
                cut_temperature_c = _celsius(target_f) - 0.5
                rise_c = 1.0 if (plant, seed, target_f) == ("GrillSim", 0, 225) else 0.0
                rows.append(
                    {
                        "plant": plant,
                        "seed": seed,
                        "target_f": target_f,
                        "cut_temperature_c": cut_temperature_c,
                        "peak_temperature_c": cut_temperature_c + rise_c,
                        "rise_c": rise_c,
                        "seconds_to_peak": 12 if rise_c else 0,
                        "cut_was_rising": True,
                    }
                )
    return {
        "experiment": "braking-horizon-coast-v2",
        "regeneration_command": "python -m docs.superpowers.experiments.braking_horizon",
        "conditions": {
            "plants": list(PLANTS),
            "seeds": list(SEEDS),
            "targets_f": list(TARGETS_F),
            "cut_target_tolerance_f": 5.0,
            "coast_seconds": 3600,
            "temperature_source": "GrillSim.true_Tc (noise-free chamber state)",
            "calibration_mutations": False,
            "allocator": {
                "normalized_combustion_load": {"preheat": 1.0, "cut": 0.0},
                "u_max": 0.9,
                "fan_enabled": False,
                "fan_behavior": "uncontrolled",
            },
            "pulse_scheduler": {
                "pulse_seconds": 2.0,
                "frame_seconds": 20.0,
                "actual_auger_feedback": "commanded",
            },
        },
        "nominal_model_bound_s": _nominal_model_bound(),
        "maximum_measured_rise_c": 1.0,
        "rows": rows,
    }


def test_coast_measurement_contract_uses_shipped_actuation_path_and_rising_cuts():
    """Evidence must be an in-range, production-actuated braking observation."""
    payload = _payload()

    braking_horizon._validate(payload)

    conditions = payload["conditions"]
    assert conditions["allocator"] == {
        "normalized_combustion_load": {"preheat": 1.0, "cut": 0.0},
        "u_max": 0.9,
        "fan_enabled": False,
        "fan_behavior": "uncontrolled",
    }
    assert conditions["pulse_scheduler"] == {
        "pulse_seconds": 2.0,
        "frame_seconds": 20.0,
        "actual_auger_feedback": "commanded",
    }
    assert conditions["targets_f"] == list(TARGETS_F)

    rows = payload["rows"]
    assert len(rows) == len(PLANTS) * len(SEEDS) * len(TARGETS_F)
    assert {(row["plant"], row["seed"], row["target_f"]) for row in rows} == {
        (plant, seed, target_f) for plant in PLANTS for seed in SEEDS for target_f in TARGETS_F
    }
    assert all(row["cut_was_rising"] for row in rows)
    assert all(
        abs((row["cut_temperature_c"] * 9.0 / 5.0 + 32.0) - row["target_f"]) <= conditions["cut_target_tolerance_f"]
        for row in rows
    )
    assert any(row["rise_c"] > 0.0 and row["seconds_to_peak"] > 0 for row in rows)


def test_coast_measurement_rejects_a_non_braking_equilibrium_cut():
    """A zero-rise cooldown has not observed the delayed braking it claims."""
    payload = _payload()
    for row in payload["rows"]:
        row["peak_temperature_c"] = row["cut_temperature_c"]
        row["rise_c"] = 0.0
        row["seconds_to_peak"] = 0
    payload["maximum_measured_rise_c"] = 0.0

    with pytest.raises(ValueError, match="positive post-cut rise"):
        braking_horizon._validate(payload)


def test_coast_measurement_rejects_a_cut_that_is_not_rising_or_near_its_target():
    """An off-target or falling cut cannot support a braking-horizon measurement."""
    payload = _payload()
    row = payload["rows"][0]
    row["cut_was_rising"] = False

    with pytest.raises(ValueError, match="rising"):
        braking_horizon._validate(payload)

    payload = _payload()
    row = payload["rows"][0]
    row["cut_temperature_c"] = _celsius(row["target_f"]) + 10.0
    row["peak_temperature_c"] = row["cut_temperature_c"] + row["rise_c"]

    with pytest.raises(ValueError, match="target"):
        braking_horizon._validate(payload)


@pytest.mark.parametrize(
    ("container", "key", "invalid"),
    [
        ("payload", "experiment", "foreign-experiment"),
        ("payload", "regeneration_command", "python foreign.py"),
        ("conditions", "coast_seconds", 60),
        ("conditions", "temperature_source", "measured noisy probe"),
    ],
)
def test_coast_measurement_rejects_foreign_provenance(container, key, invalid):
    payload = _payload()
    target = payload if container == "payload" else payload["conditions"]
    target[key] = invalid

    with pytest.raises(ValueError, match="evidence"):
        braking_horizon._validate(payload)


def test_coast_measurement_rejects_a_foreign_nominal_bound():
    payload = _payload()
    payload["nominal_model_bound_s"] += 1.0

    with pytest.raises(ValueError, match="nominal model braking bound"):
        braking_horizon._validate(payload)


def test_coast_measurement_rejects_inconsistent_rise_or_peak_time():
    payload = _payload()
    row = payload["rows"][0]
    row["rise_c"] += 0.25

    with pytest.raises(ValueError, match="peak/rise arithmetic"):
        braking_horizon._validate(payload)

    payload = _payload()
    payload["rows"][0]["seconds_to_peak"] = braking_horizon.COAST_SECONDS + 1

    with pytest.raises(ValueError, match="time-to-peak"):
        braking_horizon._validate(payload)


def test_committed_coast_evidence_satisfies_the_full_validator():
    payload = json.loads(braking_horizon.OUTPUT.read_text(encoding="utf-8"))

    braking_horizon._validate(payload)
