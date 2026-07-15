import numpy as np
from controller.update_mpc import simulate_chamber, fit_params

# C_f is held fixed during the fit (redundant with K_Q for the steady gain), so
# the synthetic truth uses the same C_f the fit is given.
TRUE = dict(K_Q=1.5, C_f=60.0, C_c=320.0, h_fc=1.8, h_amb=0.5)


def test_simulate_chamber_runs():
    t = np.arange(0, 3000, 25.0)
    Q = np.full_like(t, 49.5)
    temp = simulate_chamber(t, Q, T_amb=20.0, **TRUE, T0=20.0)
    assert temp.shape == t.shape
    assert temp[-1] > temp[0]  # heats up


def test_fit_recovers_params_on_synthetic_data():
    t = np.arange(0, 6000, 25.0)
    # excitation: step Q up then down so the gain and dynamics are identifiable
    Q = np.where(t < 3000, 60.0, 35.0)
    temp = simulate_chamber(t, Q, T_amb=20.0, **TRUE, T0=20.0)
    fitted = fit_params(t, temp, Q, T_amb=20.0, init=dict(K_Q=1.0, C_f=60.0, C_c=306.0, h_fc=2.0, h_amb=0.55))
    # the heat gain K_Q, loss h_amb, and chamber capacity should recover closely
    assert abs(fitted["K_Q"] - TRUE["K_Q"]) < 0.3
    assert abs(fitted["h_amb"] - TRUE["h_amb"]) < 0.1
    assert abs(fitted["C_c"] - TRUE["C_c"]) / TRUE["C_c"] < 0.25
