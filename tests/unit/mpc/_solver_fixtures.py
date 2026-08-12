"""Shared fixtures for exercising controller.mpc against a fake native solver.

Extracted from test_mpc_solver_options.py so other test modules (e.g.
test_model_activation.py) can reuse this fixture-construction machinery
without importing a test module directly (importing a test module runs its
module-level code and collection side effects, and couples the two files).
"""

CYCLE = {"u_min": 0.1, "u_max": 0.9}


def _config(**overrides):
    config = {
        "n_horizon": 12,
        "control_period": 3.5,
        "Q_w": 2.25,
        "R_dQ": 0.35,
        "C_c": 410.0,
        "h_amb": 0.65,
        "T_amb": 18.0,
        "theta": 62.0,
        "K_Q": 390.0,
        "sigma": 1.1e-9,
        "estimator": "ekf",
        "est_q_temp": 0.02,
        "est_q_dist": 0.07,
        "est_r_meas": 0.05,
        "enable_fan_input": True,
        "fan_min_pct": 42.0,
        "fan_max_pct": 93.0,
        "enable_online_adaptation": False,
    }
    config.update(overrides)
    return config


class _Estimator:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def update(self, _load, temperature):
        return [0.0] * 8 + [float(temperature), 0.0]


class _Solver:
    created = []

    def __init__(self, config):
        self.config = config
        self.closed = False
        self.created.append(self)

    def close(self):
        self.closed = True
