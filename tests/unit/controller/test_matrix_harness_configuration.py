"""Observable contracts for the shipped-controller matrix harness.

These tests name regressions in the experiment itself: freezing defaults at import,
feeding MPC its requested rather than realized firing, retaining scheduler credit
through an inhibit, and letting an unreachable row win a score comparison.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

import common.defaults as defaults
import docs.superpowers.experiments.controller_matrix as matrix
from common.control_trace import ActuationMode
from controller.applied_output import OutputSource
from controller.runtime.logic.pulse import PulseScheduler as ProductionPulseScheduler


def _settings(*, config=None, cycle=None):
    return {
        "controller": {"config": {"_matrix_probe": dict(config or {})}},
        "cycle_data": {
            "HoldCycleTime": 20,
            "u_min": 0.1,
            "u_max": 0.9,
            "PMode": 2,
            "LidOpenPauseTime": 6,
            **(cycle or {}),
        },
    }


class _Plant:
    instances = []

    def __init__(self, *, seed=0):
        self.seed = seed
        self.steps = []
        _Plant.instances.append(self)

    def measured(self):
        return 20.0

    def step(self, auger_on, fan_frac, lid_open=False):
        self.steps.append((float(auger_on), float(fan_frac), bool(lid_open)))


class _AuthorityPlant(_Plant):
    def maximum_reachable_temperature_f(self, maximum_duty):
        del maximum_duty
        return 130.0


class _FixedCore:
    instances = []

    def __init__(self, config, units, cycle_data):
        self.config = config
        self.cycle_data = cycle_data
        self.reports = []
        _FixedCore.instances.append(self)

    def set_target(self, target):
        self.target = target

    def get_control_period(self):
        return float(self.config.get("period", self.cycle_data["HoldCycleTime"]))

    def update(self, temperature):
        del temperature
        return float(self.config.get("ratio", 0.25))

    def set_output(self, applied):
        self.reports.append(applied)

    def actuation_mode(self):
        return ActuationMode.FIXED_CYCLE


class _FramedCore(_FixedCore):
    def actuation_mode(self):
        return ActuationMode.FRAMED_PULSE


def _install(monkeypatch, core):
    module = types.ModuleType("controller._matrix_probe")
    module.Controller = core
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(matrix, "GrillSim", _Plant)
    _Plant.instances.clear()
    core.instances.clear()


def test_next_run_resolves_patched_defaults_and_records_an_independent_effective_run(monkeypatch):
    _install(monkeypatch, _FixedCore)
    shipped = _settings(config={"ratio": 0.2}, cycle={"HoldCycleTime": 10, "u_min": 0.05})
    monkeypatch.setattr(defaults, "default_settings", lambda: shipped)

    row = matrix.run_scenario("_matrix_probe", matrix.Scenario("one", 11, [(0, 120.0)]), seed=7)

    core = _FixedCore.instances[-1]
    assert core.config == {"ratio": 0.2}
    assert core.cycle_data["HoldCycleTime"] == 10
    assert row["effective_run"] == {
        "controller_config": {"ratio": 0.2},
        "cycle_config": {
            "HoldCycleTime": 10,
            "u_min": 0.05,
            "u_max": 0.9,
            "PMode": 2,
            "LidOpenPauseTime": 6,
        },
        "actuation_mode": "fixed_cycle",
        "pulse_timing": None,
        "plant": "GrillSim",
        "seed": 7,
        "scenario": "one",
        "overrides": {"controller": {}, "cycle": {}},
    }

    # A later defaults mutation must not rewrite evidence already recorded.
    shipped["controller"]["config"]["_matrix_probe"]["ratio"] = 0.8
    shipped["cycle_data"]["HoldCycleTime"] = 99
    assert row["effective_run"]["controller_config"]["ratio"] == 0.2
    assert row["effective_run"]["cycle_config"]["HoldCycleTime"] == 10


def test_next_run_resolves_a_manifest_substituted_after_matrix_import(monkeypatch):
    _install(monkeypatch, _FixedCore)
    real_read = defaults.read_generic_json
    manifest = {
        "metadata": {
            "_matrix_probe": {
                "config": [{"option_name": "ratio", "option_default": 0.37}],
            }
        }
    }

    def _read_manifest(path, *args, **kwargs):
        if path == "./controller/controllers.json":
            return manifest
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(defaults, "read_generic_json", _read_manifest)

    row = matrix.run_scenario("_matrix_probe", matrix.Scenario("manifest", 2, [(0, 120.0)]), seed=2)

    assert _FixedCore.instances[-1].config == {"ratio": 0.37}
    assert row["effective_run"]["controller_config"] == {"ratio": 0.37}


def test_explicit_overrides_win_over_current_shipped_defaults_and_are_retained(monkeypatch):
    _install(monkeypatch, _FixedCore)
    monkeypatch.setattr(defaults, "default_settings", lambda: _settings(config={"ratio": 0.1}, cycle={"u_max": 0.4}))

    row = matrix.run_scenario(
        "_matrix_probe",
        matrix.Scenario("override", 4, [(0, 120.0)]),
        seed=3,
        config={"ratio": 0.3},
        cycle_config={"HoldCycleTime": 4, "u_max": 0.7},
    )

    assert _FixedCore.instances[-1].config == {"ratio": 0.3}
    assert _FixedCore.instances[-1].cycle_data["HoldCycleTime"] == 4
    assert row["effective_run"]["overrides"] == {
        "controller": {"ratio": 0.3},
        "cycle": {"HoldCycleTime": 4, "u_max": 0.7},
    }
    assert row["effective_run"]["cycle_config"]["u_max"] == 0.7


def test_mpc_framed_path_uses_the_production_scheduler_and_reports_completed_delivery(monkeypatch):
    _install(monkeypatch, _FramedCore)
    monkeypatch.setattr(defaults, "default_settings", lambda: _settings(config={"ratio": 0.05, "period": 20}))

    row = matrix.run_scenario("_matrix_probe", matrix.Scenario("framed", 41, [(0, 120.0)]), seed=0)

    core = _FramedCore.instances[-1]
    reports = [report for report in core.reports if report.source is OutputSource.CONTROLLER]
    assert matrix.PulseScheduler is ProductionPulseScheduler
    assert row["effective_run"]["actuation_mode"] == ActuationMode.FRAMED_PULSE.value
    assert row["effective_run"]["pulse_timing"] == {"frame_seconds": 20.0, "pulse_seconds": 2.0}
    # 5% duty accumulates one second of credit in the first frame and delivers
    # a two-second pulse in the next, so the core sees measured delivery, not
    # the requested 5%, only once the producing interval has completed.
    assert [report.timestamp for report in reports] == [20.0, 40.0]
    assert [report.ratio for report in reports] == pytest.approx([0.0, 0.1])
    assert any(0.0 < auger_on <= 1.0 for auger_on, _, _ in _Plant.instances[-1].steps)


def test_fixed_cycle_manual_inhibit_reports_once_and_resumes_from_its_existing_phase(monkeypatch):
    _install(monkeypatch, _FixedCore)
    monkeypatch.setattr(defaults, "default_settings", lambda: _settings(config={"ratio": 0.25}))
    scenario = matrix.Scenario("fixed_manual", 35, [(0, 120.0)], manual_inhibit=[(20, 4)])

    matrix.run_scenario("_matrix_probe", scenario, seed=0)

    core = _FixedCore.instances[-1]
    reports = [(report.timestamp, report.ratio, report.source) for report in core.reports]
    delivery = [step[0] for step in _Plant.instances[-1].steps]
    assert reports == [
        (0.0, 0.1, OutputSource.SEED),
        (20.0, 0.0, OutputSource.MANUAL_OVERRIDE),
    ]
    assert delivery[20:24] == [0.0] * 4
    assert delivery[24:30] == [0.0] * 6
    assert delivery[30] == 1.0


def test_framed_reset_feedback_excludes_delivery_before_the_reset(monkeypatch):
    _install(monkeypatch, _FramedCore)
    monkeypatch.setattr(defaults, "default_settings", lambda: _settings(config={"ratio": 0.5, "period": 20}))
    scenario = matrix.Scenario("framed_reset", 21, [(0, 120.0)], lid_open=[(3, 2)])

    matrix.run_scenario("_matrix_probe", scenario, seed=0)

    plant = _Plant.instances[-1]
    reports = [report for report in _FramedCore.instances[-1].reports if report.source is OutputSource.CONTROLLER]
    # Physical lid closure at t=5 does not release Hold's 6-second pause
    # armed at t=3. The auger remains off through t=8, then the restarted
    # 0.5 frame delivers ten on-seconds before the due solve closes at t=20.
    assert [step[0] for step in plant.steps[3:9]] == [0.0] * 6
    assert [report.timestamp for report in reports] == [20.0]
    assert [report.ratio for report in reports] == pytest.approx([10.0 / 17.0])


def test_framed_lid_and_manual_inhibits_discard_credit(monkeypatch):
    _install(monkeypatch, _FramedCore)
    monkeypatch.setattr(defaults, "default_settings", lambda: _settings(config={"ratio": 0.05, "period": 20}))
    scenario = matrix.Scenario(
        "inhibits",
        51,
        [(0, 120.0)],
        lid_open=[(3, 2)],
        manual_inhibit=[(27, 2)],
    )

    matrix.run_scenario("_matrix_probe", scenario, seed=0)

    core = _FramedCore.instances[-1]
    reports = [report for report in core.reports if report.source is OutputSource.CONTROLLER]
    # Hold reports every positive elapsed interval at the due solve. Both
    # resets land between solves, so their first post-reset measurements are
    # partial intervals but valid measured delivery, not discarded credit.
    assert [report.timestamp for report in reports] == [20.0, 40.0]
    assert [report.ratio for report in reports] == pytest.approx([0.0, 0.0])
    assert all(step[0] == 0.0 for step in _Plant.instances[-1].steps[3:5])
    assert all(step[0] == 0.0 for step in _Plant.instances[-1].steps[27:29])


def test_unreachable_high_row_retains_binding_authority(monkeypatch):
    _install(monkeypatch, _FixedCore)
    monkeypatch.setattr(matrix, "GrillSim", _AuthorityPlant)
    monkeypatch.setattr(defaults, "default_settings", lambda: _settings(config={"ratio": 0.2}))

    row = matrix.run_scenario("_matrix_probe", matrix.Scenario("too_hot", 2, [(0, 131.0)]), seed=0)

    assert row["reachability"] == matrix.ReachabilityState.UNREACHABLE_HIGH.value
    assert row["max_authority"]["target_f"] == 131.0
    assert row["max_authority"]["plant_max_temp_f"] == 130.0
    assert row["max_authority"]["binding"] == "plant_max_temperature"


def test_main_writes_a_deterministic_header_rows_and_summary_envelope(monkeypatch, tmp_path):
    effective_run = {
        "controller_config": {"ratio": 0.2},
        "cycle_config": {"HoldCycleTime": 20, "u_min": 0.1, "u_max": 0.9, "PMode": 2, "LidOpenPauseTime": 6},
        "actuation_mode": ActuationMode.FRAMED_PULSE.value,
        "pulse_timing": {"frame_seconds": 20.0, "pulse_seconds": 2.0},
        "plant": "GrillSim",
        "seed": 7,
        "scenario": "steady_225",
        "overrides": {"controller": {}, "cycle": {}},
    }
    expected_effective_run = json.loads(json.dumps(effective_run))
    row = {"effective_run": effective_run, "reachability": matrix.ReachabilityState.REACHABLE.value}

    class _Pool:
        def __init__(self, workers):
            self.workers = workers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def map(self, function, jobs):
            return [function(job) for job in jobs]

    monkeypatch.setattr(matrix, "Pool", _Pool)
    monkeypatch.setattr(matrix, "run_scenario", lambda *args, **kwargs: row)
    output = tmp_path / "matrix.json"

    matrix.main(
        [
            "--controllers",
            "mpc",
            "--scenarios",
            "steady_225",
            "--seeds",
            "7",
            "--plants",
            "GrillSim",
            "--out",
            str(output),
        ]
    )
    row["effective_run"]["controller_config"]["ratio"] = 0.9
    written = json.loads(output.read_text())

    assert written["header"]["effective_runs"] == [expected_effective_run]
    assert written["rows"][0]["effective_run"] == written["header"]["effective_runs"][0]
    assert written["summary"] == {
        "run_count": 1,
        "reachable_count": 1,
        "unreachable_high_count": 0,
        "unknown_authority_count": 0,
    }


def test_infeasible_metric_trap_cannot_win_reachable_ranking():
    rows = [
        {"controller": "mpc", "iae": 1.0, "reachability": matrix.ReachabilityState.UNREACHABLE_HIGH.value},
        {"controller": "pid_sp", "iae": 9.0, "reachability": matrix.ReachabilityState.REACHABLE.value},
    ]

    assert matrix.rank_reachable_rows(rows, key="iae") == [rows[1]]
