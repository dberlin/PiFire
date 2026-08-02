import json
import os
import shutil
import time
import pytest
from controller.mpc import Controller, _DEFAULTS
from controller.applied_output import AppliedOutput, OutputSource

CONFIG = dict(
    n_horizon=20,
    t_step=25.0,
    control_period=1.0,
    Q_w=1.0,
    R_dQ=0.02,
    Q_min=5.0,
    Q_max=100.0,
    C_f=60.0,
    C_c=306.0,
    h_fc=2.0,
    h_amb=0.55,
    T_amb=20.0,
    fan_min_pct=40.0,
    fan_max_pct=100.0,
    enable_fan_input=True,
    est_q_temp=1e-2,
    est_q_dist=0.5,
    est_r_meas=0.04,
)
CYCLE = {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}


def _make():
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    c.set_target(110.0)
    return c


@pytest.fixture
def mpc_controller():
    return _make()


def test_update_returns_dict_contract():
    c = _make()
    out = c.update(100.0)
    assert isinstance(out, dict)
    assert 0.1 <= out["cycle_ratio"] <= 0.9
    assert "fan" in out and "duty" in out["fan"]
    assert 40.0 <= out["fan"]["duty"] <= 100.0


def test_below_setpoint_demands_more_than_at_setpoint():
    # settle the estimator at each measured temperature before comparing
    c = _make()
    for _ in range(5):
        cold = c.update(80.0)["cycle_ratio"]
    c2 = _make()
    for _ in range(5):
        hot = c2.update(140.0)["cycle_ratio"]
    assert cold > hot  # colder than target -> more auger


def test_control_period_advertised():
    assert _make().get_control_period() == 1.0


def test_fahrenheit_setpoint_converted():
    c = Controller(dict(CONFIG), "F", dict(CYCLE))
    c.set_target(230.0)  # 230 F = 110 C
    assert abs(c._set_point_c - 110.0) < 0.6


def test_warm_solve_under_budget():
    c = _make()
    c.update(100.0)  # cold
    t0 = time.perf_counter()
    for _ in range(20):
        c.update(100.0)
    avg_ms = (time.perf_counter() - t0) / 20 * 1e3
    assert avg_ms < 200.0  # >=1 Hz with wide margin (x86 ~8 ms)


_SHIPPED = os.path.join(os.path.dirname(__file__), "..", "..", "..", "controller", "mpc_policy_net.npz")


@pytest.mark.skipif(not os.path.exists(_SHIPPED), reason="shipped net artifact absent")
def test_fan_on_derives_fan_suffixed_path_and_falls_back(tmp_path, capsys):
    # Hermetic: copy the valid fan-off artifact to an isolated base whose _fan
    # sibling does not exist, so the test is independent of what ships in
    # controller/ (a real fan-on artifact now exists there).
    base = tmp_path / "mpc_policy_net.npz"
    shutil.copy(_SHIPPED, base)  # valid fan-off artifact at base path
    # NB: no tmp_path/mpc_policy_net_fan.npz is created
    cfg = {**_DEFAULTS, "policy": "net", "enable_fan_input": True, "policy_net_path": str(base)}
    c = Controller(cfg, "C", dict(CYCLE))
    assert c._net is None  # fan-on sibling absent -> NLP fallback
    out = capsys.readouterr().out
    assert "_fan.npz" in out  # tried the fan-on path, not the base
    c.set_target(150.0)
    assert c.update(150.0)["fan"]["duty"] is not None


def test_get_status_is_json_safe():
    c = _make()
    c.set_target(225.0)
    c.update(200.0)
    status = c.get_status()
    # the real bar: it survives the MQTT encoder
    encoded = json.dumps(status, allow_nan=False)
    assert "do_mpc" not in encoded
    assert set(status) >= {"set_point", "set_point_c", "last_Q", "applied_Q", "policy", "x_hat"}
    assert isinstance(status["x_hat"], list)
    assert all(isinstance(v, float) for v in status["x_hat"])


def test_dunder_dict_is_not_json_safe():
    """The reason get_status exists; if this ever passes, revisit the fallback."""
    c = _make()
    c.update(200.0)
    with pytest.raises(TypeError):
        json.dumps(dict(c.__dict__))


def test_set_output_inverts_the_allocation_exactly(mpc_controller):
    """allocate() is affine, so applied ratio -> applied Q round-trips."""
    from controller.mpc_allocator import allocate

    cfg = mpc_controller.cfg
    for q in (cfg["Q_min"], 0.5 * (cfg["Q_min"] + cfg["Q_max"]), cfg["Q_max"]):
        auger, _ = allocate(
            q,
            Q_min=cfg["Q_min"],
            Q_max=cfg["Q_max"],
            u_min=mpc_controller.u_min,
            u_max=mpc_controller.u_max,
            fan_min_pct=cfg["fan_min_pct"],
            fan_max_pct=cfg["fan_max_pct"],
            enable_fan=bool(cfg["enable_fan_input"]),
        )
        mpc_controller.set_output(AppliedOutput(auger, OutputSource.CONTROLLER, 1.0))
        assert mpc_controller._applied_Q == pytest.approx(q)


def test_a_lid_open_report_goes_below_q_min(mpc_controller):
    """The estimator gets the honest input; being told Q_min for a pause it did
    not take is the defect being fixed."""
    mpc_controller.set_output(AppliedOutput(0.0, OutputSource.LID_OPEN, 1.0))
    assert mpc_controller._applied_Q < mpc_controller.cfg["Q_min"]


def test_the_estimator_is_driven_by_the_applied_input(mpc_controller, monkeypatch):
    seen = []
    real = mpc_controller.estimator.update
    monkeypatch.setattr(mpc_controller.estimator, "update", lambda u, y: (seen.append(u), real(u, y))[1])
    mpc_controller.set_target(225.0)
    mpc_controller.update(200.0)
    mpc_controller.set_output(AppliedOutput(0.0, OutputSource.LID_OPEN, 1.0))
    applied = mpc_controller._applied_Q
    mpc_controller.update(200.0)
    assert seen[-1] == pytest.approx(applied)


def test_with_no_report_the_command_is_assumed_applied(mpc_controller, monkeypatch):
    """Preserves today's behavior for the sync path and controller-only tests."""
    seen = []
    real = mpc_controller.estimator.update
    monkeypatch.setattr(mpc_controller.estimator, "update", lambda u, y: (seen.append(u), real(u, y))[1])
    mpc_controller.set_target(225.0)
    mpc_controller.update(200.0)
    commanded = mpc_controller._last_Q
    mpc_controller.update(200.0)
    assert seen[-1] == pytest.approx(commanded)


def test_the_net_sees_the_applied_input_clamped_to_its_trained_span(mpc_controller, monkeypatch):
    if mpc_controller._net is None:
        pytest.skip("net policy not loaded")
    seen = []
    monkeypatch.setattr(mpc_controller._net, "firing_rate", lambda x, u_prev, sp: (seen.append(u_prev), 50.0)[1])
    mpc_controller.set_target(225.0)
    mpc_controller.set_output(AppliedOutput(0.0, OutputSource.LID_OPEN, 1.0))
    mpc_controller.update(200.0)
    assert seen[-1] == pytest.approx(mpc_controller.cfg["Q_min"])


def test_a_degenerate_actuator_span_is_ignored(mpc_controller):
    before = mpc_controller._applied_Q
    mpc_controller.u_max = mpc_controller.u_min
    mpc_controller.set_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 1.0))
    assert mpc_controller._applied_Q == before
