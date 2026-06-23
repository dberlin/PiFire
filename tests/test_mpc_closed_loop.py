import numpy as np
from controller.mpc import Controller
from controller.grill_sim import GrillSim

CONFIG = dict(
    n_horizon=20, t_step=25.0, control_period=1.0, Q_w=1.0, R_dQ=0.02,
    Q_min=5.0, Q_max=100.0, C_f=60.0, C_c=306.0, h_fc=2.0, h_amb=0.55,
    T_amb=20.0, fan_min_pct=40.0, fan_max_pct=100.0, enable_fan_input=True,
    est_q_temp=1e-2, est_q_dist=0.5, est_r_meas=0.04,
)
CYCLE = {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25}


def test_closed_loop_holds_one_degree_band():
    c = Controller(dict(CONFIG), 'C', dict(CYCLE))
    c.set_target(110.0)
    sim = GrillSim(seed=0)
    ts, temps = [], []
    for k in range(288):                       # 2 h at 25 s
        t = k * 25.0
        y = sim.measured()
        out = c.update(y)
        # map cycle_ratio back to a firing rate so the plant sees the allocation
        sim.step_from_allocation(out['cycle_ratio'], out['fan']['duty'])
        ts.append(t); temps.append(sim.true_Tc)
    ts = np.array(ts); temps = np.array(temps)
    # steady window: settled hold, excluding warmup and the lid event
    sm = (ts >= 1500) & (ts < 2900)
    err = temps[sm] - 110.0
    assert np.max(np.abs(err)) <= 1.0          # the +-1.0 C gate
    assert np.mean(np.abs(err) <= 1.0) >= 0.95


def test_lid_open_recovers():
    c = Controller(dict(CONFIG), 'C', dict(CYCLE))
    c.set_target(110.0)
    sim = GrillSim(seed=0)
    dipped = False
    recovered_after_dip = False
    for k in range(288):
        y = sim.measured()
        out = c.update(y)
        sim.step_from_allocation(out['cycle_ratio'], out['fan']['duty'])
        t = k * 25.0
        if 3000 <= t < 3200 and sim.true_Tc < 105.0:
            dipped = True
        if dipped and t > 3400 and abs(sim.true_Tc - 110.0) <= 1.0:
            recovered_after_dip = True
    assert dipped and recovered_after_dip
