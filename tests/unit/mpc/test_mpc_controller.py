import json
import os
import shutil
import time
from types import SimpleNamespace

import numpy as np
import pytest

import notify.mqtt_handler as mh
from common.modes import Mode
from controller.mpc import Controller, _DEFAULTS, _warn_about_model
from controller.runtime.runner import ThreadedControllerRunner
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


_NET_ARTIFACT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "controller", "mpc_policy_net.npz")


@pytest.fixture
def net_mpc_controller():
    """A Controller running the real net policy, not the NLP.

    Uses _DEFAULTS rather than CONFIG: the shipped artifact's calibration was
    fit against _DEFAULTS's physical parameters (see test_mpc_net_loop.py),
    and CONFIG's C_f/C_c/etc. would fail matches_config() and silently fall
    back to the NLP -- which is exactly how the net-side clamp test above
    used to skip on every run without the skip ever meaning "artifact
    missing".
    """
    if not os.path.exists(_NET_ARTIFACT):
        pytest.skip("net artifact not exported")
    cfg = dict(_DEFAULTS)
    cfg["policy"] = "net"
    c = Controller(cfg, "C", dict(CYCLE))
    assert c._net is not None, "net artifact present but failed to load or match this config"
    c.set_target(110.0)
    return c


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
    assert set(status) >= {"set_point", "set_point_c", "last_Q", "applied_Q", "policy", "x_hat", "cycle_data"}
    # a tuple, not a list: controller_state() copies the returned dict but not
    # its values, so a mutable x_hat would let one consumer's mutation reach
    # every other consumer reading the same control-period snapshot.
    assert isinstance(status["x_hat"], tuple)
    assert all(isinstance(v, float) for v in status["x_hat"])


def test_dunder_dict_is_not_json_safe():
    """The reason get_status exists; if this ever passes, revisit the fallback."""
    c = _make()
    c.update(200.0)
    with pytest.raises(TypeError):
        json.dumps(dict(c.__dict__))


def test_get_status_guards_against_non_finite_values():
    c = _make()
    c.set_target(225.0)
    c.update(200.0)
    c._x_hat = [float("nan"), float("inf"), 1.0]
    c._last_Q = float("nan")
    c.set_point = float("nan")  # e.g. a malformed setpoint, guarded like every other field
    c.cycle_data["u_min"] = float("nan")  # e.g. a malformed setting
    status = c.get_status()
    # allow_nan=False raises on a bare NaN; None is the safe substitute.
    json.dumps(status, allow_nan=False)
    assert status["x_hat"] == (None, None, 1.0)
    assert status["last_Q"] is None
    assert status["set_point"] is None
    assert status["cycle_data"]["u_min"] is None


def test_get_status_cycle_data_is_a_copy():
    """core.cycle_data is settings["cycle_data"] itself (_build_core passes it
    by reference); controller_state()'s contract is that the caller owns the
    mapping outright, so a consumer mutating the returned cycle_data must not
    reach live settings."""
    c = _make()
    c.set_target(225.0)
    c.update(200.0)
    status = c.get_status()
    assert status["cycle_data"] == CYCLE
    assert status["cycle_data"] is not c.cycle_data


def test_threaded_runner_seeds_from_get_status_before_first_solve():
    """The runner's __init__ used to snapshot core.__dict__ directly, so an MPC
    core published its do-mpc/estimator internals for the whole first control
    period (or forever, if the worker died inside update()). get_status() must
    seed the very first snapshot too, not just the post-solve ones in _loop."""
    c = _make()
    runner = ThreadedControllerRunner(c)
    try:
        state = runner.controller_state()
        json.dumps(state, allow_nan=False)  # would TypeError on a leaked estimator/mpc object
        assert "estimator" not in state
        assert "mpc" not in state
        assert state["policy"] == "nlp"
    finally:
        runner.stop()


class _FakeMqttClient:
    """Minimal stand-in for paho.mqtt.client.Client -- just enough of the
    surface notify/mqtt_handler.py drives to prove what actually reaches the
    wire, without a real broker."""

    def __init__(self, *args, **kwargs):
        self.publish_calls = []
        self._connected = False

    def will_set(self, *args, **kwargs):
        pass

    def username_pw_set(self, *args, **kwargs):
        pass

    def connect(self, host, port, keepalive):
        self._connected = True
        return 0

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        self._connected = False

    def is_connected(self):
        return self._connected

    def publish(self, topic, payload, qos=0, retain=False, properties=None):
        self.publish_calls.append({"topic": topic, "payload": payload})
        return SimpleNamespace(rc=0)

    def subscribe(self, topic):
        pass


def _mqtt_handler(monkeypatch):
    """A real MqttNotificationHandler wired to _FakeMqttClient -- exercises the
    actual whitelist/recursion/zero-out logic in notify/mqtt_handler.py rather
    than a mock of it."""
    monkeypatch.setattr(mh.mqtt, "Client", _FakeMqttClient)
    monkeypatch.setattr(mh, "getfqdn", lambda: "test.local")
    settings = {
        "globals": {"debug_mode": False, "grill_name": "", "units": "C"},
        "modules": {"grillplat": "prototype"},
        "probe_settings": {"probe_map": {"probe_info": []}},
        "notify_services": {
            "mqtt": {
                "broker": "test.broker",
                "enabled": True,
                "homeassistant_autodiscovery_topic": "",  # skip HA autodiscover; not under test here
                "id": "PiFireTest",
                "password": "",
                "port": "1883",
                "update_sec": "30",
                "username": "",
            }
        },
    }
    handler = mh.MqttNotificationHandler(settings)
    handler.last_conn_time = 0  # defeat the post-connect publish throttle
    return handler


def test_mpc_status_survives_the_mqtt_publish_boundary(monkeypatch):
    """Pins the cross-process seam: get_status()'s keys must actually clear
    mqtt_handler's per-context whitelist to reach the wire, and the
    pid_cycle_data topic the __dict__ fallback used to feed (via notify()'s
    nested-dict recursion over the cycle_data attribute) must still appear.
    Asserts the full published set, not a few named keys, so a future key
    nobody thought to check is not silently dropped or silently let through.
    """
    handler = _mqtt_handler(monkeypatch)

    c = _make()
    c.set_target(225.0)
    c.update(200.0)
    status = c.get_status()
    status["cycle_ratio"] = 0.5  # HoldMode adds this before publishing (hold.py)

    handler.notify("pid", status)

    pid_payloads = [call["payload"] for call in handler.client.publish_calls if call["topic"] == "PiFireTest/pid"]
    assert pid_payloads
    published = json.loads(pid_payloads[-1])
    # policy (a string) and x_hat (a list) are deliberately not in PID_SENSORS
    # -- see the comment there -- so only the scalar numeric fields clear the
    # whitelist gate.
    assert published == {
        "cycle_ratio": 0.5,
        "set_point": status["set_point"],
        "set_point_c": status["set_point_c"],
        "last_Q": status["last_Q"],
        "applied_Q": status["applied_Q"],
    }

    cycle_payloads = [
        call["payload"] for call in handler.client.publish_calls if call["topic"] == "PiFireTest/pid_cycle_data"
    ]
    assert cycle_payloads  # the topic __dict__ used to feed must survive get_status()
    cycle_published = json.loads(cycle_payloads[-1])
    assert cycle_published == CYCLE  # byte-identical to the legacy __dict__-fed payload


def test_stop_to_startup_transition_zeroes_only_numeric_pid_sensors(monkeypatch):
    """N1 regression: notify()'s "zero the PID data if not controlling" loop
    (fires on every non-Hold mode, e.g. every Stop->Startup at the start of a
    cook) iterates the very same PID_SENSORS list the "pid" topic publishes
    from. A non-numeric key there gets zeroed to int 0 -- which _create_auto
    discover then registers as a `state_class: measurement` numeric sensor --
    and the real string/list value later lands on a sensor Home Assistant
    already believes is numeric."""
    handler = _mqtt_handler(monkeypatch)

    handler.notify("control", {"mode": Mode.STARTUP})

    pid_payloads = [call["payload"] for call in handler.client.publish_calls if call["topic"] == "PiFireTest/pid"]
    assert pid_payloads
    zeroed = json.loads(pid_payloads[-1])
    assert "policy" not in zeroed
    assert "x_hat" not in zeroed
    assert zeroed and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in zeroed.values())


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


def test_the_net_sees_the_applied_input_clamped_to_its_trained_span(net_mpc_controller, monkeypatch):
    seen = []
    monkeypatch.setattr(net_mpc_controller._net, "firing_rate", lambda x, u_prev, sp: (seen.append(u_prev), 50.0)[1])
    net_mpc_controller.set_target(225.0)
    net_mpc_controller.set_output(AppliedOutput(0.0, OutputSource.LID_OPEN, 1.0))
    net_mpc_controller.update(200.0)
    assert seen[-1] == pytest.approx(net_mpc_controller.cfg["Q_min"])


def test_a_degenerate_actuator_span_is_ignored(mpc_controller):
    before = mpc_controller._applied_Q
    mpc_controller.u_max = mpc_controller.u_min
    mpc_controller.set_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 1.0))
    assert mpc_controller._applied_Q == before


def test_zero_duty_is_zero_firing_not_negative(mpc_controller):
    """A paused auger delivers no fuel, not negative fuel: mpc_model.py's
    heat_in = K_Q * Q has no offset, so the affine inverse of allocate() --
    which does have an offset, Q_min -> u_min -- must not be extrapolated
    past u_min down to duty 0."""
    mpc_controller.set_output(AppliedOutput(0.0, OutputSource.LID_OPEN, 1.0))
    assert mpc_controller._applied_Q == pytest.approx(0.0)


def test_a_partial_floor_crossing_lands_strictly_between_zero_and_q_min(mpc_controller):
    ratio = mpc_controller.u_min / 2.0
    mpc_controller.set_output(AppliedOutput(ratio, OutputSource.LID_OPEN, 1.0))
    assert 0.0 < mpc_controller._applied_Q < mpc_controller.cfg["Q_min"]


def test_the_floor_blend_is_continuous_at_u_min(mpc_controller):
    """The boundary-value tests alone (duty 0 -> Q 0, duty u_min -> Q_min) are
    satisfied by any k*Q_min*ratio/u_min blend, not just k=1 -- a k<1 slope
    leaves a jump exactly at u_min, which duty crosses routinely, not rarely.
    Continuity across the seam is the assertion that actually pins the slope."""
    u_min = mpc_controller.u_min
    mpc_controller.set_output(AppliedOutput(u_min - 1e-9, OutputSource.LID_OPEN, 1.0))
    just_below = mpc_controller._applied_Q
    mpc_controller.set_output(AppliedOutput(u_min, OutputSource.CONTROLLER, 1.0))
    at_floor = mpc_controller._applied_Q
    assert just_below == pytest.approx(mpc_controller.cfg["Q_min"])
    assert at_floor == pytest.approx(mpc_controller.cfg["Q_min"])
    assert just_below == pytest.approx(at_floor)


def test_a_degenerate_q_span_matches_allocates_own_guard(mpc_controller):
    """allocate() falls back to span=1.0 when Q_max<=Q_min; the inverse must
    use the same fallback so the two maps agree instead of this one flipping
    sign on a nonsense config."""
    mpc_controller.cfg["Q_max"] = mpc_controller.cfg["Q_min"]
    mpc_controller.set_output(AppliedOutput(mpc_controller.u_max, OutputSource.CONTROLLER, 1.0))
    assert mpc_controller._applied_Q == pytest.approx(mpc_controller.cfg["Q_min"] + 1.0)


def test_a_solve_failure_during_a_pause_holds_the_command_not_the_applied_value(mpc_controller, monkeypatch):
    """The except-fallback holds the previous COMMAND (_last_Q), even mid-pause.
    Conflating it with the paused _applied_Q would drop the auger to Q_min on
    a transient solver failure instead of holding the prior command steady."""
    calls = {"n": 0}

    def fake_make_step(x):
        calls["n"] += 1
        if calls["n"] == 1:
            return np.array([[40.0]])
        raise RuntimeError("solver failure")

    monkeypatch.setattr(mpc_controller.mpc, "make_step", fake_make_step)
    mpc_controller.set_target(225.0)
    mpc_controller.update(200.0)
    commanded = mpc_controller._last_Q
    assert commanded == pytest.approx(40.0)
    mpc_controller.set_output(AppliedOutput(0.0, OutputSource.LID_OPEN, 1.0))
    mpc_controller.update(200.0)
    assert mpc_controller._last_Q == pytest.approx(commanded)


def test_shipped_defaults_are_reported_as_uncalibrated(capsys):
    _warn_about_model(dict(_DEFAULTS))
    out = capsys.readouterr().out
    assert "uncalibrated" in out.lower()
    assert "update_mpc" in out


def test_calibrated_params_are_not_reported_as_uncalibrated(capsys):
    cfg = dict(_DEFAULTS)
    cfg.update(C_c=11000.0, h_amb=2.7, K_Q=32.0, theta=110.0, n_horizon=200)
    _warn_about_model(cfg)
    assert "uncalibrated" not in capsys.readouterr().out.lower()


def test_a_horizon_shorter_than_the_braking_distance_is_reported(capsys):
    cfg = dict(_DEFAULTS)
    # 360 s of coast after a fuel cut, against a 24*25 = 600 s horizon... which
    # covers it. n_horizon is cut so the horizon is genuinely the short one.
    cfg.update(C_c=11000.0, h_amb=2.7, K_Q=32.0, theta=110.0, n_horizon=8)
    _warn_about_model(cfg)
    out = capsys.readouterr().out
    assert "horizon" in out.lower()
    assert "200" in out  # 8 * 25 s
    assert "fuel cut" in out


def test_an_adequate_horizon_is_not_reported(capsys):
    cfg = dict(_DEFAULTS)
    cfg.update(C_c=11000.0, h_amb=2.7, K_Q=32.0, theta=110.0, n_horizon=200, t_step=25.0)
    _warn_about_model(cfg)
    assert "horizon" not in capsys.readouterr().out.lower()


def test_the_running_warning_and_the_promotion_policy_size_the_horizon_alike(capsys):
    """One model, one answer, whichever code path says it.

    This warning used to compute C_c/h_amb inline while `evaluate` sized its
    demand from the braking distance, so a refit could print an adequate
    horizon and the controller could call the same horizon short. Both read
    the same function now, and this pins that: the warning fires exactly when
    the promotion policy asks for more steps than the config has.
    """
    from controller.model_promotion import evaluate, longest_braking_distance

    cfg = dict(_DEFAULTS)
    cfg.update(C_c=11000.0, h_amb=2.7, K_Q=32.0, theta=110.0, t_step=25.0)
    brake = longest_braking_distance(cfg)
    discredited = cfg["C_c"] / cfg["h_amb"]
    assert brake == pytest.approx(360.0, abs=5.0)
    assert discredited == pytest.approx(4074.0, abs=5.0)

    # 24 steps of 25 s is the shipped horizon and it sits BETWEEN the two
    # quantities: past the braking distance, far short of C_c/h_amb. It is the
    # only kind of point that can tell the two rules apart, so it has to be in
    # here -- 8 and 200 steps agree under either rule and prove nothing.
    assert brake < 24 * 25.0 < discredited

    for n_horizon in (8, 24, 200):
        cfg["n_horizon"] = n_horizon
        _warn_about_model(cfg)
        warned = "horizon" in capsys.readouterr().out.lower()
        demanded = evaluate(
            cfg, None, candidate_rmse=2.0, incumbent_rmse=None, n_horizon=n_horizon, t_step=25.0
        ).horizon_needed
        assert warned is (demanded is not None), f"n_horizon={n_horizon} disagreed"
        assert warned is (n_horizon * 25.0 < brake), f"n_horizon={n_horizon} did not follow the braking distance"


def test_set_target_keeps_the_applied_firing_rate_history():
    """_applied_Q is the rate the grill actually ran at (recovered by
    set_output) and is what the estimator is given as its known input;
    _last_Q is the last command, held over on a solve failure. Both describe
    the grill, not the target, so a new setpoint must not rewrite either."""
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    c._last_Q = 87.5
    c._applied_Q = 84.0
    c.set_target(300)
    assert c._last_Q == 87.5
    assert c._applied_Q == 84.0


def test_set_target_still_updates_the_target():
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    c.set_target(300)
    assert c.set_point == 300
    assert c._set_point_c == 300  # units are "C" here, so no conversion
