import os
import numpy as np
import pytest
from controller.applied_output import AppliedOutput, OutputSource
from controller.mpc import Controller, _DEFAULTS
from controller.grill_sim import GrillSim
from controller.mpc_net import NetPolicy, net_path_for

ART = os.path.join(os.path.dirname(__file__), "..", "..", "..", "controller", "mpc_policy_net.npz")
CYCLE = {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}
TS = 25.0

needs_art = pytest.mark.skipif(not os.path.exists(ART), reason="net artifact not exported")

_FAN_ART = net_path_for(ART, True)


def _usable(path, cfg):
    """Whether an artifact on disk is one THIS model's state vector can drive.

    The tests below need a net the controller will actually adopt, and an
    artifact trained against a different state vector is not that even though
    the file is present. Asking `os.path.exists` alone turns "this artifact
    predates the current model" into a red suite rather than a skip, and the
    two want different responses: a missing artifact is a build that did not
    run, a mismatched one is a regeneration that has not been done yet.
    """
    if not os.path.exists(path):
        return False
    try:
        return NetPolicy.load(path).matches_config(cfg)
    except Exception:
        return False


needs_fan_art = pytest.mark.skipif(
    not _usable(_FAN_ART, {**_DEFAULTS, "enable_fan_input": True}),
    reason="fan-on net artifact missing, or trained against a different state vector "
    "(regenerate: tools/regenerate_mpc_net.py --mode fan-on)",
)


def _run(cfg, setpoint, seed=0, minutes=90, minimum_ratio=0.0):
    # This harness advances the plant TS seconds per update(), so the estimator
    # discretization (control_period) must equal TS regardless of the shipped
    # default (which is 5.0 for a faster production re-solve cadence).
    c = Controller({**cfg, "control_period": TS}, "C", dict(CYCLE))
    c.set_target(setpoint)
    plant = GrillSim(seed=seed)
    ts, temps = [], []
    for w in range(int(minutes * 60 / TS)):
        out = c.update(plant.measured())
        ratio = float(np.clip(out["cycle_ratio"], minimum_ratio, CYCLE["u_max"]))
        fan = out["fan"]["duty"] if out["fan"]["duty"] is not None else 100.0
        on = int(round(ratio * TS))
        for s in range(int(TS)):
            plant.step(auger_on=(s < on), fan_frac=fan / 100.0)
            ts.append(w * TS + s)
            temps.append(plant.true_Tc)
        c.set_output(
            AppliedOutput(
                ratio=on / TS,
                source=OutputSource.CONTROLLER,
                timestamp=(w + 1) * TS,
                requested=ratio,
            )
        )
    return c, np.array(ts), np.array(temps)


def _quality(ts, temps, setpoint, *, after):
    error = temps[ts >= after] - setpoint
    return {
        "bias": abs(float(np.mean(error))),
        "rmse": float(np.sqrt(np.mean(error**2))),
        "maximum": float(np.max(np.abs(error))),
        "within_2_5c": float(np.mean(np.abs(error) <= 2.5)),
    }


@needs_art
def test_net_policy_active_and_no_nlp_built():
    cfg = dict(_DEFAULTS)
    cfg["policy"] = "net"
    c = Controller(cfg, "C", dict(CYCLE))
    assert c._net is not None  # net policy loaded
    assert c.mpc is None  # NLP (do_mpc/IPOPT) never built


@needs_art
def test_net_policy_tracks_the_configured_nlp_at_low_setpoint():
    """Artifact quality follows the configured policy, not the removed derived horizon."""
    net_cfg = {**_DEFAULTS, "policy": "net"}
    nlp_cfg = {**_DEFAULTS, "policy": "nlp"}
    _, net_ts, net_temps = _run(net_cfg, 110.0)
    _, nlp_ts, nlp_temps = _run(nlp_cfg, 110.0)
    net = _quality(net_ts, net_temps, 110.0, after=1800)
    nlp = _quality(nlp_ts, nlp_temps, 110.0, after=1800)

    assert net["rmse"] <= nlp["rmse"] + 1.0
    assert net["bias"] <= nlp["bias"] + 1.5
    assert net["maximum"] <= nlp["maximum"] + 3.0  # integer-second delivery adds bounded peak quantization
    assert net["within_2_5c"] >= nlp["within_2_5c"] - 0.1


@needs_art
def test_net_policy_holds_band_high_setpoint():
    # 220C (~428F): band is slightly wider at high fire, but still tight + offset-free
    cfg = dict(_DEFAULTS)
    cfg["policy"] = "net"
    _, ts, temps = _run(cfg, 220.0)
    sm = ts >= 2400
    err = temps[sm] - 220.0
    assert np.sqrt(np.mean(err**2)) <= 3.5  # measured ~1.4C
    assert np.max(np.abs(err)) <= 8.0
    assert abs(np.mean(err)) <= 1.5


def test_net_missing_artifact_falls_back_to_nlp():
    cfg = dict(_DEFAULTS)
    cfg.update(policy="net", policy_net_path="./controller/_does_not_exist.npz")
    c = Controller(cfg, "C", dict(CYCLE))
    c.set_target(110.0)
    assert c._net is None  # fell back
    assert c.mpc is not None  # NLP built
    out = c.update(110.0)  # still controls
    assert "cycle_ratio" in out


@needs_art
def test_net_calibration_mismatch_falls_back_to_nlp():
    # a recalibration (different K_Q) must NOT silently use the stale net
    cfg = dict(_DEFAULTS)
    cfg["policy"] = "net"
    cfg["K_Q"] = _DEFAULTS["K_Q"] * 1.4
    c = Controller(cfg, "C", dict(CYCLE))
    assert c._net is None
    assert c.mpc is not None


@needs_fan_art
def test_fan_on_net_matches_or_improves_the_configured_nlp():
    """The fan-on artifact must preserve the source policy's closed-loop quality."""
    net_cfg = {**_DEFAULTS, "policy": "net", "enable_fan_input": True}
    nlp_cfg = {**_DEFAULTS, "policy": "nlp", "enable_fan_input": True}
    _, net_ts, net_temps = _run(net_cfg, 190.0, minutes=75, minimum_ratio=CYCLE["u_min"])
    _, nlp_ts, nlp_temps = _run(nlp_cfg, 190.0, minutes=75, minimum_ratio=CYCLE["u_min"])
    net = _quality(net_ts, net_temps, 190.0, after=1800)
    nlp = _quality(nlp_ts, nlp_temps, 190.0, after=1800)

    assert net["bias"] <= nlp["bias"]
    assert net["rmse"] <= nlp["rmse"]
    assert net["maximum"] <= nlp["maximum"]
