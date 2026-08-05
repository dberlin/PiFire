"""Contracts for Task 7's fixed-seed feed-forward shipment gate."""

from __future__ import annotations

import copy
import importlib.util
import json
import math
import sys
from itertools import product
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.defaults import default_settings
from controller.mpc import Controller
from controller.mpc_model import steady_combustion_load
from docs.superpowers.experiments import controller_matrix

EXPERIMENT = ROOT / "docs/superpowers/experiments/mpc_feed_forward.py"
EVIDENCE = EXPERIMENT.with_name("_mpc_feed_forward.json")

ARM_IDS = (
    "legacy_affine_fixed_25s",
    "normalized_framed_no_feed_forward",
    "normalized_framed_feed_forward",
)
CAPABILITY_SCENARIO = "capability_unreachable_high_2000"
SCENARIO_NAMES = (
    "steady_225",
    "steady_325",
    "steady_350",
    "steady_450",
    "step_225_275",
    "lid_open_225",
    CAPABILITY_SCENARIO,
)
SEEDS = (0, 1, 2, 3, 4)
PLANTS = ("GrillSim", "MAKGrillSim")

METRIC_FIELDS = {
    "rmse_f",
    "iae_f_seconds",
    "overshoot_f",
    "undershoot_f",
    "settle_time_s",
    "pct_within_band",
    "steady_peak_to_peak_f",
    "auger_on_time_s",
    "pellet_proxy",
    "requested_realized_load_error",
    "transitions_per_hour",
}
FINITE_METRIC_FIELDS = METRIC_FIELDS - {"settle_time_s"}


def _load_experiment():
    """Load the planned standalone experiment during the test, never collection."""
    assert EXPERIMENT.is_file(), f"Task 7 experiment is missing: {EXPERIMENT}"
    spec = importlib.util.spec_from_file_location("task7_mpc_feed_forward", EXPERIMENT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _evidence_payload():
    assert EVIDENCE.is_file(), f"Task 7 evidence is missing: {EVIDENCE}"
    return json.loads(EVIDENCE.read_text())


def _row_key(row):
    return row["arm"], row["plant"], row["scenario"], row["seed"]


def _decision_inputs(payload):
    return payload["rows"], payload["delayed_solver_cases"], payload["pulse_evidence"]


def test_fixed_matrix_reuses_the_canonical_harness_conditions():
    """A duplicate scenario/default map would let one arm silently drift."""
    experiment = _load_experiment()

    assert experiment.PLANTS is controller_matrix.PLANTS
    assert experiment.SCENARIOS is controller_matrix.SCENARIOS
    assert tuple(experiment.SCENARIO_NAMES) == SCENARIO_NAMES
    assert tuple(experiment.SEEDS) == SEEDS
    assert tuple(experiment.ARM_IDS) == ARM_IDS
    assert tuple(experiment.PULSE_TIMING) == (2.0, 20.0)
    assert experiment.LEGACY_CONTROL_PERIOD_S == 25.0
    assert controller_matrix.CAPABILITY_UNREACHABLE_HIGH_SCENARIO == CAPABILITY_SCENARIO
    assert controller_matrix.SCENARIOS[CAPABILITY_SCENARIO].setpoints == [(0, 2_000.0)]


def test_each_arm_uses_only_the_private_equilibrium_injection_seam():
    """The matrix defines its baseline explicitly; it never assumes a shipping default."""
    experiment = _load_experiment()

    for arm in ARM_IDS[:2]:
        controller = Controller({}, "Task7", dict(default_settings()["cycle_data"]))
        controller.set_target(120.0)
        original_cfg = dict(controller.cfg)
        experiment.configure_arm(controller, arm)

        assert controller.cfg == original_cfg
        assert controller._equilibrium_load(120.0, 0.0) == 0.0

    controller = Controller({}, "Task7", dict(default_settings()["cycle_data"]))
    controller.set_target(120.0)
    original_cfg = dict(controller.cfg)
    experiment.configure_arm(controller, experiment.FEED_FORWARD_ARM)

    assert controller.cfg == original_cfg
    assert controller._equilibrium_load(120.0, 0.0) == pytest.approx(steady_combustion_load(controller.cfg, 120.0, 0.0))


@pytest.mark.slow
def test_committed_matrix_is_complete_finite_and_self_describing():
    """Every arm/plant/scenario/seed row is evidence, including losing rows."""
    payload = _evidence_payload()
    rows = payload["rows"]

    assert payload["header"]["format_version"] >= 3
    assert "mpc_feed_forward.py" in payload["header"]["regeneration_command"]
    assert set(payload) >= {"rows", "delayed_solver_cases", "pulse_evidence", "summary"}
    assert payload["summary"]["run_count"] == len(rows)
    expected = set(product(ARM_IDS, PLANTS, SCENARIO_NAMES, SEEDS))
    assert {_row_key(row) for row in rows} == expected
    assert len(rows) == len(expected)

    for row in rows:
        assert set(row) >= {
            "arm",
            "plant",
            "scenario",
            "seed",
            "reachability",
            "trace_session_ids",
            "metrics",
            "solver",
            "safety",
            "effective_configuration",
        }
        assert row["reachability"] in {"reachable", "unreachable_high", "unknown_authority"}
        assert row["trace_session_ids"] and all(isinstance(value, str) and value for value in row["trace_session_ids"])
        summary = row["trace_session_summary"]
        assert row["trace_session_ids"] == [summary["session_id"]]
        assert summary["record_count"] == sum(summary["event_counts"].values())
        for field in ("requested_revisions", "applied_revisions", "diagnostic_revisions"):
            revisions = summary[field]
            assert revisions["record_count"] >= revisions["unique_count"] > 0
            assert 1 <= revisions["first"] <= revisions["last"]
            assert revisions["contiguous"] is True
        assert summary["requested_revisions"] == summary["diagnostic_revisions"]
        assert summary["applied_revisions"]["last"] <= summary["requested_revisions"]["last"]
        assert set(row["metrics"]) >= METRIC_FIELDS
        assert all(math.isfinite(float(row["metrics"][field])) for field in FINITE_METRIC_FIELDS)
        settle_time = row["metrics"]["settle_time_s"]
        assert settle_time is None or (math.isfinite(float(settle_time)) and settle_time >= 0)
        assert set(row["solver"]) >= {"duration_s", "deadline_misses", "stale_result_episodes"}
        assert set(row["solver"]["duration_s"]) >= {"min", "mean", "max"}
        assert all(math.isfinite(float(value)) for value in row["solver"]["duration_s"].values())
        assert row["solver"]["deadline_misses"] >= 0
        assert row["solver"]["stale_result_episodes"] >= 0
        assert "outcome" in row["safety"]
        json.dumps(row["effective_configuration"], allow_nan=False, sort_keys=True)

        scheduler = row["effective_configuration"]["scheduler"]
        if row["arm"] == ARM_IDS[0]:
            assert scheduler == {"kind": "fixed_cycle", "control_period_s": 25.0}
        else:
            assert scheduler == {"kind": "framed_pulse", "pulse_quantum_s": 2.0, "frame_s": 20.0}
        if row["arm"] in ARM_IDS[:2]:
            assert row["effective_configuration"]["experiment_seams"] == {"Controller._equilibrium_load": "zero"}
            assert "feed_forward" not in row["effective_configuration"]["controller_config"]
        else:
            assert row["effective_configuration"]["experiment_seams"] == {
                "Controller._equilibrium_load": "steady_combustion_load"
            }


@pytest.mark.slow
def test_committed_capability_rows_are_upper_unreachable_and_excluded_from_rankings():
    """A reachable authority probe would contaminate the scheduler comparison."""
    experiment = _load_experiment()
    payload = _evidence_payload()
    rows, delayed_cases, pulse_evidence = _decision_inputs(payload)

    capability_rows = [row for row in rows if row["scenario"] == CAPABILITY_SCENARIO]
    assert len(capability_rows) == len(ARM_IDS) * len(PLANTS) * len(SEEDS)
    assert {row["reachability"] for row in capability_rows} == {"unreachable_high"}

    decision = experiment.decision_from_rows(rows, delayed_cases, pulse_evidence)
    assert decision["ranked_reachable_pairs"] == 60


@pytest.mark.slow
def test_shipment_decision_rejects_incomplete_or_foreign_evidence():
    """A verdict must never be derived from a partial or substituted evidence set."""
    experiment = _load_experiment()
    payload = _evidence_payload()
    rows, delayed_cases, pulse_evidence = _decision_inputs(payload)

    for invalid_rows in (rows[:-1], rows + [copy.deepcopy(rows[0])]):
        with pytest.raises(ValueError, match="complete|duplicate|matrix"):
            experiment.decision_from_rows(invalid_rows, delayed_cases, pulse_evidence)

    foreign_rows = copy.deepcopy(rows)
    foreign_rows[0]["scenario"] = "foreign"
    with pytest.raises(ValueError, match="complete|foreign|matrix"):
        experiment.decision_from_rows(foreign_rows, delayed_cases, pulse_evidence)

    for invalid_cases in (delayed_cases[:-1], delayed_cases + [copy.deepcopy(delayed_cases[0])]):
        with pytest.raises(ValueError, match="delayed|duplicate|complete"):
            experiment.decision_from_rows(rows, invalid_cases, pulse_evidence)

    foreign_cases = copy.deepcopy(delayed_cases)

    incomplete_delayed_case = copy.deepcopy(delayed_cases)
    del incomplete_delayed_case[0]["preemptions"]["stop"]["runtime_event"]
    with pytest.raises(ValueError, match="incomplete delayed"):
        experiment.decision_from_rows(rows, incomplete_delayed_case, pulse_evidence)
    foreign_cases[0]["delay_seconds"] = 99
    with pytest.raises(ValueError, match="delayed|foreign|complete"):
        experiment.decision_from_rows(rows, foreign_cases, pulse_evidence)

    incomplete_pulse_evidence = copy.deepcopy(pulse_evidence)
    incomplete_pulse_evidence["open_loop"] = [
        row
        for row in incomplete_pulse_evidence["open_loop"]
        if not (row["arm"] == "linear_coupled_2s_frame_20s" and row["q"] == 0.01)
    ]
    with pytest.raises(ValueError, match="pulse|low-fire|complete"):
        experiment.decision_from_rows(rows, delayed_cases, incomplete_pulse_evidence)

    duplicate_pulse_evidence = copy.deepcopy(pulse_evidence)
    duplicate_pulse_evidence["open_loop"].append(
        next(
            row
            for row in duplicate_pulse_evidence["open_loop"]
            if row["arm"] == "linear_coupled_2s_frame_20s" and row["q"] == 0.01
        )
    )
    with pytest.raises(ValueError, match="pulse|complete"):
        experiment.decision_from_rows(rows, delayed_cases, duplicate_pulse_evidence)

    foreign_pulse_evidence = copy.deepcopy(pulse_evidence)
    foreign_pulse_evidence["open_loop"].append(
        {**foreign_pulse_evidence["open_loop"][0], "arm": "foreign_pulse_arm"}
    )
    with pytest.raises(ValueError, match="pulse|complete"):
        experiment.decision_from_rows(rows, delayed_cases, foreign_pulse_evidence)


@pytest.mark.slow
@pytest.mark.parametrize(
    ("component", "mutate"),
    (
        (
            "selected_pulse_timing",
            lambda _rows, _cases, pulse: pulse["conditions"]["selected_scheduler"].update(frame_s=10.0),
        ),
        (
            "low_fire_floor_removed",
            lambda rows, _cases, pulse: next(
                row
                for row in pulse["open_loop"]
                if row["arm"] == "linear_coupled_2s_frame_20s" and row["q"] == 0.01
            ).update(mean_duty=0.12),
        ),
        (
            "pulse_transition_envelope_respected",
            lambda rows, _cases, pulse: next(
                row
                for row in pulse["open_loop"]
                if row["arm"] == "linear_coupled_2s_frame_20s" and row["q"] == 0.5
            ).update(switches_per_hour=361.0),
        ),
        (
            "lid_recovery_preserved",
            lambda rows, _cases, _pulse: next(
                row
                for row in rows
                if row["arm"] == ARM_IDS[1]
                and row["scenario"] == "lid_open_225"
                and row["safety"]["outcome"]["lid_recovery_s"] is not None
            )["safety"]["outcome"].update(lid_recovery_s=None),
        ),
        (
            "normalized_deadlines_clear",
            lambda rows, _cases, _pulse: next(
                row for row in rows if row["arm"] == ARM_IDS[1] and row["scenario"] != CAPABILITY_SCENARIO
            )["solver"].update(deadline_misses=1),
        ),
        (
            "normalized_staleness_clear",
            lambda rows, _cases, _pulse: next(
                row for row in rows if row["arm"] == ARM_IDS[1] and row["scenario"] != CAPABILITY_SCENARIO
            )["solver"].update(stale_result_episodes=1),
        ),
        (
            "quality_comparable_or_improved",
            lambda rows, _cases, _pulse: [
                normalized["metrics"].update(rmse_f=legacy["metrics"]["rmse_f"] + 10.0)
                for legacy, normalized in (
                    (
                        next(
                            row
                            for row in rows
                            if (row["arm"], row["plant"], row["scenario"], row["seed"])
                            == (ARM_IDS[0], plant, scenario, seed)
                        ),
                        next(
                            row
                            for row in rows
                            if (row["arm"], row["plant"], row["scenario"], row["seed"])
                            == (ARM_IDS[1], plant, scenario, seed)
                        ),
                    )
                    for plant, scenario, seed in product(PLANTS, SCENARIO_NAMES[:-1], SEEDS)
                )
            ],
        ),
        (
            "applied_load_fidelity_improved",
            lambda rows, _cases, _pulse: [
                normalized["metrics"].update(
                    requested_realized_load_error=legacy["metrics"]["requested_realized_load_error"]
                )
                for legacy, normalized in (
                    (
                        next(
                            row
                            for row in rows
                            if (row["arm"], row["plant"], row["scenario"], row["seed"])
                            == (ARM_IDS[0], plant, scenario, seed)
                        ),
                        next(
                            row
                            for row in rows
                            if (row["arm"], row["plant"], row["scenario"], row["seed"])
                            == (ARM_IDS[1], plant, scenario, seed)
                        ),
                    )
                    for plant, scenario, seed in product(PLANTS, SCENARIO_NAMES[:-1], SEEDS)
                )
            ],
        ),
        (
            "capability_rows_upper_unreachable",
            lambda rows, _cases, _pulse: next(
                row for row in rows if row["scenario"] == CAPABILITY_SCENARIO
            ).update(reachability="reachable"),
        ),
        (
            "ranked_reachable_pairs_complete",
            lambda rows, _cases, _pulse: next(
                row
                for row in rows
                if row["arm"] == ARM_IDS[2] and row["scenario"] == "steady_225"
            ).update(reachability="unknown_authority"),
        ),
        (
            "delayed_hold_cadence",
            lambda _rows, cases, _pulse: cases[0].update(hold_cadence_normal=False),
        ),
        (
            "delayed_frame_actualization",
            lambda _rows, cases, _pulse: cases[0].update(frame_actualizations=[]),
        ),
        (
            "delayed_single_revision_authority",
            lambda _rows, cases, _pulse: cases[0].update(single_revision_authority=False),
        ),
        (
            "delayed_stale_authority_bounded",
            lambda _rows, cases, _pulse: next(
                case for case in cases if case["delay_seconds"] == 1
            ).update(max_stale_authority_periods=99.0),
        ),
        (
            "delayed_deadline_stale_sequence",
            lambda _rows, cases, _pulse: next(
                case for case in cases if case["delay_seconds"] == 1
            ).update(deadline_misses=0),
        ),
        (
            "delayed_deadline_stale_sequence",
            lambda _rows, cases, _pulse: next(
                case for case in cases if case["delay_seconds"] == 1
            ).update(stale_protection_observed=False),
        ),
        (
            "delayed_warning_recovery",
            lambda _rows, cases, _pulse: next(
                case for case in cases if case["delay_seconds"] == 1
            )["warning_recovery"].update(recovered=False),
        ),
        (
            "delayed_stop_lid_manual_preemption",
            lambda _rows, cases, _pulse: cases[0]["preemptions"]["manual"].update(
                command_on_after_preemption=True
            ),
        ),
    ),
)
def test_each_scheduler_shipment_component_is_a_veto(component, mutate):
    """Removing any required §16–17 evidence must veto scheduler shipment."""
    experiment = _load_experiment()
    payload = _evidence_payload()
    rows, delayed_cases, pulse_evidence = copy.deepcopy(_decision_inputs(payload))

    mutate(rows, delayed_cases, pulse_evidence)
    decision = experiment.decision_from_rows(rows, delayed_cases, pulse_evidence)

    assert decision["components"][component] is False
    assert decision["ship_normalized_scheduler"] is False


@pytest.mark.slow
def test_shipment_decision_is_recomputed_from_complete_ranked_rows():
    """Changing a retained reachable result changes the gate; a stored verdict cannot win."""
    experiment = _load_experiment()
    payload = _evidence_payload()
    rows, delayed_cases, pulse_evidence = _decision_inputs(payload)

    assert payload["summary"]["decision"] == experiment.decision_from_rows(rows, delayed_cases, pulse_evidence)
    with pytest.raises(ValueError, match="complete|missing|matrix"):
        experiment.decision_from_rows(rows[:-1], delayed_cases, pulse_evidence)

    made_worse = copy.deepcopy(rows)
    by_key = {(row["plant"], row["scenario"], row["seed"], row["arm"]): row for row in made_worse}
    for plant, scenario, seed in product(PLANTS, SCENARIO_NAMES, SEEDS):
        without_feed_forward = by_key[(plant, scenario, seed, ARM_IDS[1])]
        with_feed_forward = by_key[(plant, scenario, seed, ARM_IDS[2])]
        if without_feed_forward["reachability"] == "reachable":
            with_feed_forward["metrics"]["rmse_f"] = without_feed_forward["metrics"]["rmse_f"] + 10.0

    made_worse_decision = experiment.decision_from_rows(made_worse, delayed_cases, pulse_evidence)
    assert made_worse_decision["components"]["feed_forward_paired_improvement"] is False
    assert made_worse_decision["ship_feed_forward"] is False


@pytest.mark.slow
def test_production_equilibrium_provider_follows_the_computed_shipment_decision():
    """Production has no feed-forward setting; its default follows retained evidence only."""
    experiment = _load_experiment()
    payload = _evidence_payload()
    decision = experiment.decision_from_rows(*_decision_inputs(payload))
    controller = Controller({}, "Task7-production", dict(default_settings()["cycle_data"]))
    controller.set_target(120.0)

    expected = steady_combustion_load(controller.cfg, 120.0, 0.0) if decision["ship_feed_forward"] else 0.0
    assert controller._equilibrium_load(120.0, 0.0) == pytest.approx(expected)
