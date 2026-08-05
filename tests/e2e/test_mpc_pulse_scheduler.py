"""Contracts for Task 7 pulse evidence and delayed-solver safety evidence."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from controller.grill_sim import GrillSim, MAKGrillSim

PULSE_EXPERIMENT = ROOT / "docs/superpowers/experiments/mpc_pulse_allocator.py"
PULSE_EVIDENCE = PULSE_EXPERIMENT.with_name("_mpc_pulse_allocator.json")
FEED_FORWARD_EVIDENCE = ROOT / "docs/superpowers/experiments/_mpc_feed_forward.json"


def _load_module(path: Path, name: str):
    """Resolve experiment modules only while a test is executing."""
    assert path.is_file(), f"Task 7 experiment is missing: {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


def test_pulse_evidence_pins_the_approved_quantum_frame_and_plant_calibration():
    """The 2 s/20 s point is a fixed measured condition, not a selectable sweep default."""
    experiment = _load_module(PULSE_EXPERIMENT, "task7_mpc_pulse_allocator")
    assert PULSE_EVIDENCE.is_file(), f"Task 7 pulse evidence is missing: {PULSE_EVIDENCE}"
    payload = json.loads(PULSE_EVIDENCE.read_text())

    assert experiment.PULSE_QUANTUM_S == 2.0
    assert experiment.PULSE_FRAME_S == 20.0
    assert experiment.FIXED_CYCLE_S == 25.0
    assert payload["header"]["format_version"] >= 1
    assert "mpc_pulse_allocator.py" in payload["header"]["regeneration_command"]
    assert payload["conditions"]["selected_scheduler"] == {
        "pulse_quantum_s": 2.0,
        "frame_s": 20.0,
    }
    assert payload["conditions"]["plant_calibration"] == {
        "GrillSim": _plant_calibration(GrillSim(seed=0)),
        "MAKGrillSim": _plant_calibration(MAKGrillSim(seed=0)),
    }

    selected_rows = [
        row for row in payload["open_loop"] if row["arm"] == "linear_coupled_2s_frame_20s" and row["q"] == 0.5
    ]
    assert {row["plant"] for row in selected_rows} == {"GrillSim", "MAKGrillSim"}
    assert all(row["switches_per_hour"] == 360.0 for row in selected_rows)
    assert all(math.isfinite(float(row["band_f"])) and math.isfinite(float(row["duty_error"])) for row in selected_rows)


def test_delayed_solver_evidence_preserves_scheduler_safety_and_single_revision_authority():
    """Slow solves must leave last accepted framed actuation safe and observable."""
    assert FEED_FORWARD_EVIDENCE.is_file(), f"Task 7 evidence is missing: {FEED_FORWARD_EVIDENCE}"
    payload = json.loads(FEED_FORWARD_EVIDENCE.read_text())
    cases = payload["delayed_solver_cases"]

    assert len(cases) == 6
    assert {(case["plant"], case["delay_seconds"]) for case in cases} == {
        (plant, delay) for plant in ("GrillSim", "MAKGrillSim") for delay in (0, 1, 2)
    }
    for case in cases:
        assert case["trace_session_ids"] == [case["trace_session_summary"]["session_id"]]
        assert case["trace_session_summary"]["record_count"] == sum(
            case["trace_session_summary"]["event_counts"].values()
        )
        intervals = case["trace_session_summary"]["applied_interval_summary"]
        assert intervals["record_count"] > 0
        assert intervals["complete_record_count"] > 0
        assert intervals["positive_duration"] is True
        assert intervals["contiguous"] is True
        assert intervals["overlap_count"] == intervals["gap_count"] == 0
        assert intervals["total_duration_s"] > 0.0
        assert 0.0 <= intervals["mean_auger_duty"] <= 1.0
        assert 0.0 <= intervals["mean_combustion_load"] <= 1.0
        assert intervals["normalized_load_inverted"] is True
        assert case["observed_control_period_s"] == 0.25
        assert case["hold_cadence_normal"] is True
        assert case["delay_periods"] == case["delay_seconds"] / case["observed_control_period_s"]
        assert case["frame_actualizations"] and all(
            actualization["at_s"] % 20.0 == 0.0 and actualization["pulse_quantum_s"] == 2.0
            for actualization in case["frame_actualizations"]
        )
        accepted = case["accepted_revisions"]
        assert accepted == sorted(set(accepted))
        assert case["single_revision_authority"] is True
        assert 0 <= case["max_stale_authority_periods"] <= case["delay_periods"]
        assert all(
            evidence["command_on_after_preemption"] is False
            and evidence["observed_at_s"] > 0.0
            and evidence["runtime_event"] in {"stop", "lid_detected", "manual_takeover"}
            and evidence["recorder_safety_events"] >= 1
            and evidence["scheduler_reset_observed"] is True
            and evidence["interrupted_frame"]
            for evidence in case["preemptions"].values()
        )
        if case["delay_seconds"] == 0:
            assert case["stale_protection_observed"] is False
        else:
            assert case["stale_protection_observed"] is True
            assert case["warning_recovery"]["stale_advisories"] >= 1
            assert case["warning_recovery"]["recovered"] is True
        assert case["deadline_misses"] >= 0
