import json
import math
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
from common.control_trace import ActuationMode

CONFIG = dict(
    n_horizon=20,
    t_step=25.0,
    control_period=1.0,
    Q_w=1.0,
    R_dQ=0.02,
    C_c=306.0,
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
    assert 0.0 <= out["cycle_ratio"] <= 0.9
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


def test_mpc_advertises_framed_pulse_actuation_and_typed_fresh_diagnostics():
    controller = _make()
    controller.update(100.0)
    diagnostics = controller.trace_diagnostics()

    assert controller.actuation_mode() is ActuationMode.FRAMED_PULSE
    assert math.isfinite(diagnostics.solve_duration_seconds)
    assert diagnostics.result_age_seconds == 0.0
    assert diagnostics.deadline_miss_count == diagnostics.consecutive_deadline_miss_count == 0
    assert diagnostics.stale_state.value == "fresh"
    assert diagnostics.recovered is False


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
    assert set(status) >= {
        "set_point",
        "set_point_c",
        "last_combustion_load",
        "applied_combustion_load",
        "policy",
        "x_hat",
        "cycle_data",
    }
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
    c._last_combustion_load = float("nan")
    c.set_point = float("nan")  # e.g. a malformed setpoint, guarded like every other field
    c.cycle_data["u_min"] = float("nan")  # e.g. a malformed setting
    status = c.get_status()
    # allow_nan=False raises on a bare NaN; None is the safe substitute.
    json.dumps(status, allow_nan=False)
    assert status["x_hat"] == (None, None, 1.0)
    assert status["last_combustion_load"] is None
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
        "last_combustion_load": status["last_combustion_load"],
        "applied_combustion_load": status["applied_combustion_load"],
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


def test_set_output_inverts_measured_auger_duty_to_normalized_applied_load(mpc_controller):
    from controller.mpc_allocator import allocate

    cfg = mpc_controller.cfg
    for load in (0.0, 0.5, 1.0):
        allocation = allocate(
            load,
            u_max=mpc_controller.u_max,
            fan_min_pct=cfg["fan_min_pct"],
            fan_max_pct=cfg["fan_max_pct"],
            enable_fan=bool(cfg["enable_fan_input"]),
        )
        mpc_controller.set_output(AppliedOutput(allocation.auger_duty, OutputSource.CONTROLLER, 1.0))
        assert mpc_controller._applied_combustion_load == pytest.approx(load)


def test_a_lid_open_report_recovers_zero_normalized_load(mpc_controller):
    mpc_controller.set_output(AppliedOutput(0.0, OutputSource.LID_OPEN, 1.0))
    assert mpc_controller._applied_combustion_load == 0.0


def test_the_estimator_and_history_receive_the_applied_normalized_load(mpc_controller, monkeypatch):
    seen = []
    real = mpc_controller.estimator.update
    monkeypatch.setattr(mpc_controller.estimator, "update", lambda u, y: (seen.append(u), real(u, y))[1])
    mpc_controller.set_target(225.0)
    mpc_controller.update(200.0)
    mpc_controller.set_output(AppliedOutput(0.0, OutputSource.LID_OPEN, 1.0))
    applied = mpc_controller._applied_combustion_load
    mpc_controller.update(200.0)
    assert seen[-1] == pytest.approx(applied)
    assert mpc_controller.cook_history()[-1][-1] == pytest.approx(applied)


def test_no_output_report_assumes_the_bounded_command_was_applied(mpc_controller, monkeypatch):
    seen = []
    real = mpc_controller.estimator.update
    monkeypatch.setattr(mpc_controller.estimator, "update", lambda u, y: (seen.append(u), real(u, y))[1])
    mpc_controller.set_target(225.0)
    mpc_controller.update(200.0)
    commanded = mpc_controller._last_combustion_load
    mpc_controller.update(200.0)
    assert seen[-1] == pytest.approx(commanded)


def test_nlp_decision_is_a_residual_with_a_baseline_tvp_and_physical_total_bounds(mpc_controller):
    mpc = mpc_controller.mpc
    assert mpc is not None
    assert set(mpc.model.u.keys()) - {"default"} == {"combustion_residual"}
    assert set(mpc.model.tvp.keys()) >= {"T_set", "equilibrium_load"}


def test_partial_measured_duty_recovers_the_same_normalized_fraction(mpc_controller):
    duty = mpc_controller.u_max / 2.0
    mpc_controller.set_output(AppliedOutput(duty, OutputSource.LID_OPEN, 1.0))
    assert mpc_controller._applied_combustion_load == pytest.approx(0.5)


def test_measured_duty_above_the_actuator_ceiling_is_bounded_at_full_load(mpc_controller):
    mpc_controller.set_output(AppliedOutput(mpc_controller.u_max * 2.0, OutputSource.CONTROLLER, 1.0))
    assert mpc_controller._applied_combustion_load == 1.0


def test_a_solve_failure_during_a_pause_holds_the_command_not_the_applied_value(mpc_controller, monkeypatch):
    calls = {"n": 0}

    def fake_make_step(x):
        calls["n"] += 1
        if calls["n"] == 1:
            return np.array([[0.4]])
        raise RuntimeError("solver failure")

    monkeypatch.setattr(mpc_controller.mpc, "make_step", fake_make_step)
    mpc_controller.set_target(225.0)
    mpc_controller.update(200.0)
    commanded = mpc_controller._last_combustion_load
    assert 0.0 <= commanded <= 1.0
    mpc_controller.set_output(AppliedOutput(0.0, OutputSource.LID_OPEN, 1.0))
    mpc_controller.update(200.0)
    assert mpc_controller._last_combustion_load == pytest.approx(commanded)


def test_shipped_defaults_are_reported_as_uncalibrated(capsys):
    _warn_about_model(dict(_DEFAULTS))
    out = capsys.readouterr().out
    assert "uncalibrated" in out.lower()
    assert "update_mpc" in out


def test_calibrated_params_are_not_reported_as_uncalibrated(capsys):
    cfg = dict(_DEFAULTS)
    cfg.update(C_c=11000.0, h_amb=2.7, K_Q=3200.0, theta=110.0, n_horizon=200)
    _warn_about_model(cfg)
    assert "uncalibrated" not in capsys.readouterr().out.lower()


def test_a_two_lump_settings_record_still_starts_and_says_the_keys_do_nothing(capsys):
    """Every install that ran the two-lump model arrives carrying C_f and h_fc.

    They name nothing now, so no value could be right and there is nothing to
    migrate them into -- but refusing to start a grill over an obsolete key is
    worse than running and saying the key does nothing. Both are asserted here:
    a Controller built from such a record works, and the message names them.
    """
    c = Controller(dict(CONFIG, C_f=9.0, h_fc=1.3), "C", dict(CYCLE))
    out = capsys.readouterr().out
    assert "C_f" in out and "h_fc" in out
    assert "ignoring" in out.lower()

    # Ignored, not absorbed: they reach neither the estimator's state vector nor
    # anything the controller plans with.
    c.set_target(110.0)
    result = c.update(100.0)
    assert 0.0 <= result["cycle_ratio"] <= 1.0
    assert c.estimator.n == int(CONFIG.get("n_delay", _DEFAULTS["n_delay"])) + 2


def test_a_record_without_the_retired_keys_says_nothing_about_them(capsys):
    """The negative control for the message above -- otherwise it could be
    unconditional and the test would not notice."""
    Controller(dict(CONFIG), "C", dict(CYCLE))
    assert "ignoring" not in capsys.readouterr().out.lower()


def test_a_policy_that_always_raises_does_not_freeze_the_output_in_silence(capsys):
    """The failure mode the net's width check exists to prevent, pinned directly.

    `update()` catches every exception from the policy and holds the previous
    firing rate. For one bad solve that is right: the control loop must not
    break and the last move is the best guess for the next few seconds. Held
    forever it means nothing is steering the fire, and the `except` is exactly
    what would make that invisible -- which is how a stale policy artifact
    could have run a grill on a frozen command with nothing in the log.

    So the property is not that the output changes; it cannot, there is no
    answer to compute. It is that the condition ANNOUNCES itself, in the log
    and in the status a caller can read without one.
    """
    c = _make()

    class _AlwaysRaises:
        def firing_rate(self, *a, **k):
            raise RuntimeError("simulated policy failure")

    c._net = _AlwaysRaises()
    c.mpc = None
    capsys.readouterr()

    outs = [c.update(110.0) for _ in range(60)]
    log = capsys.readouterr().out

    # It really is frozen -- otherwise this test proves nothing about silence.
    assert len({o["cycle_ratio"] for o in outs}) == 1

    # It said so, on the first step and again as the run went on, and not once
    # per step (which on a 5 s loop would bury the first message).
    assert log.count("policy has failed") >= 2
    assert log.count("policy has failed") <= 10
    assert "1 consecutive step" in log
    assert "not being controlled to setpoint" in log
    assert "simulated policy failure" in log

    # And it is visible without reading the log at all.
    assert c.get_status()["policy_failures"] == 60


def test_a_recovering_policy_clears_the_frozen_output_report(capsys):
    """The negative control: a working policy must never claim to be frozen.

    Without this, a `policy_failures` that only ever counted up and a message
    that fired unconditionally would both pass the test above.
    """
    c = _make()
    assert c.get_status()["policy_failures"] == 0

    class _FailsTwice:
        def __init__(self):
            self.calls = 0

        def firing_rate(self, *a, **k):
            self.calls += 1
            if self.calls <= 2:
                raise RuntimeError("transient")
            return 0.42

    c._net = _FailsTwice()
    c.mpc = None
    capsys.readouterr()

    for _ in range(4):
        c.update(110.0)
    log = capsys.readouterr().out

    assert c.get_status()["policy_failures"] == 0
    assert "recovered after 2 failed step(s)" in log
    # A healthy controller says nothing about failures at all.
    c2 = _make()
    capsys.readouterr()
    c2.update(110.0)
    assert "policy has failed" not in capsys.readouterr().out


def test_a_horizon_shorter_than_the_braking_distance_is_reported(capsys):
    cfg = dict(_DEFAULTS)
    # 360 s of coast after a fuel cut, against a 24*25 = 600 s horizon... which
    # covers it. n_horizon is cut so the horizon is genuinely the short one.
    cfg.update(C_c=11000.0, h_amb=2.7, K_Q=3200.0, theta=110.0, n_horizon=8)
    _warn_about_model(cfg)
    out = capsys.readouterr().out
    assert "horizon" in out.lower()
    assert "200" in out  # 8 * 25 s
    assert "fuel cut" in out


def test_an_adequate_horizon_is_not_reported(capsys):
    cfg = dict(_DEFAULTS)
    cfg.update(C_c=11000.0, h_amb=2.7, K_Q=3200.0, theta=110.0, n_horizon=200, t_step=25.0)
    _warn_about_model(cfg)
    assert "horizon" not in capsys.readouterr().out.lower()


def test_a_coast_with_no_end_is_reported_as_one_no_horizon_reaches(capsys):
    """The one config raising the horizon cannot rescue.

    A model with no firing-rate gain never predicts the chamber stops rising,
    so the longest horizon this controller will build is still short of the
    coast. The controller runs anyway -- every condition here is advisory --
    which is why saying so is the whole of what it can do. No setting is
    offered, because an endless coast is not a shortfall any setting closes.
    """
    from controller.model_promotion import longest_braking_distance

    cfg = dict(_DEFAULTS)
    cfg.update(C_c=11000.0, h_amb=2.7, theta=110.0, K_Q=0.0, n_horizon=8)
    assert longest_braking_distance(cfg) == math.inf

    _warn_about_model(cfg)
    out = capsys.readouterr().out
    assert "no end this model predicts" in out
    assert "No setting reaches the end of this coast" in out
    assert "Raise" not in out
    assert "inf s" not in out  # a coast with no end has no number to print


#: A model whose chamber goes on rising for about 2015 s after a fuel cut --
#: past CONFIG's 20 * 25 = 500 s horizon and well inside the cap. Written out
#: rather than derived so the numbers the tests below assert are pinned to
#: parameters and not to whatever the module currently computes.
_SLOW_COAST = dict(C_c=2520.0, h_amb=0.224, T_amb=20.0, theta=600.0, n_delay=8, K_Q=695.0, sigma=1.4e-9)
_QUICK_COAST = dict(_SLOW_COAST, theta=50.0)


def test_a_coast_the_configured_horizon_cannot_hold_is_BUILT_at_the_longer_one(capsys):
    """The configured n_horizon is a floor; what gets built is what the coast needs.

    Both the intention and the NLP are asserted: the whole defect was a
    controller that computed a horizon requirement, said it out loud, and then
    built the short horizon anyway.
    """
    from controller.model_promotion import longest_braking_distance

    cfg = dict(CONFIG, **_SLOW_COAST)
    brake = longest_braking_distance(cfg)
    needed = math.ceil(brake / cfg["t_step"])
    assert needed > cfg["n_horizon"]

    c = Controller(cfg, "C", dict(CYCLE))
    assert c._built_n_horizon == needed
    assert c.mpc.settings.n_horizon == needed
    assert c.cfg["n_horizon"] == CONFIG["n_horizon"]  # the operator's setting is untouched

    out = capsys.readouterr().out
    assert f"{needed} steps" in out
    assert "t_step" not in out  # only the step count is on offer


def test_a_coast_the_configured_horizon_covers_is_built_at_the_configured_one():
    """The negative control: without it the raise above could be unconditional."""
    from controller.model_promotion import longest_braking_distance

    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    assert longest_braking_distance(c.cfg) < CONFIG["n_horizon"] * CONFIG["t_step"]
    assert c._built_n_horizon == CONFIG["n_horizon"]
    assert c.mpc.settings.n_horizon == CONFIG["n_horizon"]


def test_a_horizon_the_step_bound_cuts_short_offers_the_setting_that_helps(capsys):
    """A shortfall a reachable setting repairs is reported with that setting.

    `_HORIZON_CAP_STEPS` truncates where `t_step` is fine enough to turn an
    ordinary coast into an extraordinary NLP, and a longer `t_step` spans the
    same seconds in fewer steps -- so the window grows and the solve does not.
    Running short in silence is the failure this message exists to prevent, and
    advice that cannot work is the same failure wearing a message.
    """
    from controller.model_promotion import _HORIZON_CAP_STEPS, _MAX_CONFIGURABLE_HORIZON_S, longest_braking_distance

    cfg = dict(CONFIG, **_SLOW_COAST, t_step=1.0, n_horizon=60)
    brake = longest_braking_distance(cfg)
    assert brake > _HORIZON_CAP_STEPS * cfg["t_step"]  # the step bound bites
    assert brake <= _MAX_CONFIGURABLE_HORIZON_S  # and a setting still reaches it

    c = Controller(cfg, "C", dict(CYCLE))
    assert c._built_n_horizon == _HORIZON_CAP_STEPS
    assert c.mpc.settings.n_horizon == _HORIZON_CAP_STEPS

    out = capsys.readouterr().out
    assert "does not reach the end of that coast" in out
    assert f"product reaches {brake:.0f} s" in out  # how far, not just that it is short
    assert "t_step is the cheaper of the two" in out
    assert "out of range" not in out


def test_a_coast_inside_some_reachable_setting_is_never_called_out_of_range(capsys):
    """The caps hold down the raise, so they do not bound what a config reaches.

    A coast past both caps can still sit inside a horizon somebody configures,
    because `built_n_horizon` takes an outer max against `n_horizon`. Reading a
    cap as the limit tells an operator who could fix this that their model is
    out of range, and sends them off to refit something that is not the problem.
    The fixture is deliberately in that gap: past the 2400 s cap, inside the
    3600 s the settings reach, at an `n_horizon` high enough that the floor is
    not what covers it.
    """
    from controller.model_promotion import _HORIZON_CAP_S, _MAX_CONFIGURABLE_HORIZON_S, longest_braking_distance

    cfg = dict(CONFIG, **dict(_SLOW_COAST, theta=900.0), t_step=25.0, n_horizon=50)
    brake = longest_braking_distance(cfg)
    assert _HORIZON_CAP_S < brake <= _MAX_CONFIGURABLE_HORIZON_S  # the gap

    c = Controller(cfg, "C", dict(CYCLE))
    assert c._built_n_horizon * cfg["t_step"] < brake  # this config is short

    out = capsys.readouterr().out
    assert "out of range" not in out  # it is not; a setting covers it
    assert f"product reaches {brake:.0f} s" in out

    # And the advice works: a setting inside controllers.json's ranges whose
    # product clears the coast really does cover it.
    fixed = Controller(dict(cfg, n_horizon=51, t_step=60.0), "C", dict(CYCLE))
    assert 51 * 60.0 >= brake
    assert fixed._built_n_horizon * 60.0 >= brake
    assert "keeps rising" not in capsys.readouterr().out  # nothing left to warn about


def test_a_coast_past_every_reachable_setting_offers_no_lever(capsys):
    """A shortfall no configuration repairs must not pretend otherwise.

    Past `_MAX_CONFIGURABLE_HORIZON_S` there is nothing to raise: that product
    is already both settings at their maxima. Naming a lever here would be
    advice that cannot work, which is the same failure as saying nothing.
    """
    from controller.model_promotion import _MAX_CONFIGURABLE_HORIZON_S, longest_braking_distance

    cfg = dict(CONFIG, **dict(_SLOW_COAST, theta=1200.0), t_step=60.0, n_horizon=60)  # settings maxima
    brake = longest_braking_distance(cfg)
    assert brake > _MAX_CONFIGURABLE_HORIZON_S  # past every configuration

    c = Controller(cfg, "C", dict(CYCLE))
    assert c._built_n_horizon * cfg["t_step"] == _MAX_CONFIGURABLE_HORIZON_S  # everything on offer

    out = capsys.readouterr().out
    assert "No setting reaches the end of this coast" in out
    assert "out of range" in out
    assert "Raise" not in out  # no lever, because none of them move this


def test_adopting_a_slow_model_then_a_quick_one_brings_the_horizon_back_down():
    """The property the derived-not-stored horizon exists to guarantee.

    Writing an adopted model's demand into n_horizon would leave the horizon
    only able to grow: this grill's next model would be sized against the last
    one's coast rather than against what the operator set, and the solve cost
    would rise once and stay. Each build is asked afresh here, which is what a
    later refactor to a stored value would fail.
    """
    floor = CONFIG["n_horizon"]
    c = Controller(dict(CONFIG), "C", dict(CYCLE))

    c._adopt_model(_SLOW_COAST, rmse=2.0, samples=100, band_c=(20.0, 200.0))
    assert c.cfg["n_horizon"] == floor
    assert Controller(dict(c.cfg), "C", dict(CYCLE))._built_n_horizon > floor

    c._adopt_model(_QUICK_COAST, rmse=1.0, samples=100, band_c=(20.0, 200.0))
    assert c.cfg["n_horizon"] == floor
    assert Controller(dict(c.cfg), "C", dict(CYCLE))._built_n_horizon == floor


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
    cfg.update(C_c=11000.0, h_amb=2.7, K_Q=3200.0, theta=110.0, t_step=25.0)
    brake = longest_braking_distance(cfg)
    discredited = cfg["C_c"] / cfg["h_amb"]
    assert brake == pytest.approx(353.3, abs=5.0)
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
            cfg,
            None,
            candidate_rmse=2.0,
            incumbent_rmse=None,
            # Clear of the floor, so what varies across this loop is the
            # horizon and nothing else.
            identifiability=2.0,
            n_horizon=n_horizon,
            t_step=25.0,
        ).horizon_needed
        assert warned is (demanded is not None), f"n_horizon={n_horizon} disagreed"
        assert warned is (n_horizon * 25.0 < brake), f"n_horizon={n_horizon} did not follow the braking distance"


def test_set_target_keeps_the_applied_normalized_load_history():
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    c._last_combustion_load = 0.875
    c._applied_combustion_load = 0.84
    c.set_target(300)
    assert c._last_combustion_load == 0.875
    assert c._applied_combustion_load == 0.84


def test_set_target_still_updates_the_target():
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    c.set_target(300)
    assert c.set_point == 300
    assert c._set_point_c == 300  # units are "C" here, so no conversion


def _residual_controller(*, feed_forward=False):
    controller = Controller(
        dict(
            CONFIG,
            n_delay=0,
            theta=0.0,
            K_Q=100.0,
            h_amb=0.5,
            sigma=0.0,
            estimator="kf",
            feed_forward=feed_forward,
        ),
        "C",
        dict(CYCLE),
    )
    controller.set_target(120.0)
    controller.estimator.update = lambda applied, measured: np.asarray((measured, 10.0))
    return controller


def test_policy_residual_is_composed_with_raw_equilibrium_and_traced_without_intermediate_clamping():
    controller = _residual_controller()
    controller._policy_residual = lambda x_hat, previous_load, equilibrium_load: 0.2
    controller._adopt_model(
        dict(controller.cfg),
        rmse=1.0,
        samples=120,
        band_c=(20.0, 140.0),
    )

    controller.update(100.0)
    diagnostics = controller.trace_diagnostics()

    assert diagnostics.equilibrium_feed_forward == pytest.approx(0.4)
    assert diagnostics.residual_move == pytest.approx(0.2)
    assert diagnostics.raw_policy_firing_load == pytest.approx(0.6)
    assert diagnostics.bounded_firing_load == pytest.approx(0.6)
    assert controller.get_status()["last_equilibrium_load"] == pytest.approx(0.4)
    assert controller.get_status()["last_residual_load"] == pytest.approx(0.2)
    assert controller.get_status()["last_raw_combustion_load"] == pytest.approx(0.6)
    assert diagnostics.feasibility.state.value == "reachable"
    assert controller.get_status()["feasibility"]["state"] == "reachable"

    controller.set_target(140.0)
    controller.update(100.0)
    assert controller.trace_diagnostics().equilibrium_feed_forward == pytest.approx(0.5)
    assert controller.trace_diagnostics().raw_policy_firing_load == pytest.approx(0.7)

    controller.estimator.update = lambda applied, measured: np.asarray((measured, 20.0))
    controller.update(100.0)
    assert controller.trace_diagnostics().equilibrium_feed_forward == pytest.approx(0.4)
    assert controller.trace_diagnostics().raw_policy_firing_load == pytest.approx(0.6)


@pytest.mark.parametrize(
    ("residual", "raw_total", "bounded_total"),
    [(-0.8, -0.4, 0.0), (0.9, 1.3, 1.0)],
)
def test_residual_policy_clamps_only_the_combined_load_once(residual, raw_total, bounded_total):
    controller = _residual_controller()
    controller._policy_residual = lambda x_hat, previous_load, equilibrium_load: residual

    controller.update(100.0)
    diagnostics = controller.trace_diagnostics()

    assert diagnostics.equilibrium_feed_forward == pytest.approx(0.4)
    assert diagnostics.residual_move == pytest.approx(residual)
    assert diagnostics.raw_policy_firing_load == pytest.approx(raw_total)
    assert diagnostics.bounded_firing_load == pytest.approx(bounded_total)


def test_feed_forward_remains_active_when_unrecognized_production_config_requests_it_disabled():
    controller = _residual_controller(feed_forward=False)
    controller._policy_residual = lambda x_hat, previous_load, equilibrium_load: 0.0

    controller.update(100.0)

    assert controller.trace_diagnostics().equilibrium_feed_forward == pytest.approx(0.4)
    assert controller.trace_diagnostics().bounded_firing_load == pytest.approx(0.4)


def test_nondefault_configured_model_is_reachability_known_while_exact_defaults_remain_unknown():
    configured = _residual_controller()
    configured._policy_residual = lambda x_hat, previous_load, equilibrium_load: 0.0
    configured.update(100.0)

    configured_report = configured.trace_diagnostics().feasibility
    assert configured_report.state.value == "reachable"
    assert (configured_report.model_revision, configured_report.model_provenance) == (0, "configured")

    defaults = Controller(dict(_DEFAULTS), "C", dict(CYCLE))
    defaults.set_target(120.0)
    defaults.estimator.update = lambda applied, measured: np.zeros(int(defaults.cfg["n_delay"]) + 2)
    defaults._policy_residual = lambda x_hat, previous_load, equilibrium_load: 0.0
    defaults.update(100.0)

    default_report = defaults.trace_diagnostics().feasibility
    assert default_report.state.value == "unknown_model"


def test_private_equilibrium_provider_seam_can_run_a_no_feed_forward_experiment():
    controller = _residual_controller()
    controller._equilibrium_load = lambda target, disturbance: 0.0
    controller._policy_residual = lambda x_hat, previous_load, equilibrium_load: 0.25

    controller.update(100.0)

    diagnostics = controller.trace_diagnostics()
    assert diagnostics.equilibrium_feed_forward == 0.0
    assert diagnostics.residual_move == 0.25
    assert diagnostics.raw_policy_firing_load == 0.25
    assert diagnostics.bounded_firing_load == 0.25


def test_radiation_only_model_keeps_controller_equilibrium_finite():
    controller = Controller(
        dict(CONFIG, n_delay=0, theta=0.0, h_amb=0.0, K_Q=100.0, sigma=1.4e-9, estimator="ekf"),
        "C",
        dict(CYCLE),
    )
    controller.set_target(240.0)
    controller._policy_residual = lambda x_hat, previous_load, equilibrium_load: 0.0

    controller.update(100.0)

    assert np.isfinite(controller.trace_diagnostics().equilibrium_feed_forward)


def test_nlp_reaches_upper_authority_when_raw_equilibrium_exceeds_two_without_failing_policy():
    controller = Controller(
        dict(
            CONFIG,
            n_horizon=2,
            t_step=5.0,
            control_period=1.0,
            n_delay=0,
            theta=0.0,
            h_amb=0.5,
            K_Q=10.0,
            sigma=0.0,
            estimator="kf",
            policy="nlp",
        ),
        "C",
        dict(CYCLE),
    )
    controller.set_target(100.0)

    controller.update(20.0)

    diagnostics = controller.trace_diagnostics()
    assert diagnostics.equilibrium_feed_forward > 2.0
    assert diagnostics.residual_move == pytest.approx(1.0 - diagnostics.equilibrium_feed_forward, abs=1e-6)
    assert diagnostics.raw_policy_firing_load == pytest.approx(1.0, abs=1e-6)
    assert diagnostics.bounded_firing_load == pytest.approx(1.0)
    assert diagnostics.failure_state.value == "success"
    assert controller.get_status()["policy_failures"] == 0
