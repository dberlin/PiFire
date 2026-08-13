"""Shared fixtures for exercising controller.mpc against a fake native solver.

Extracted from test_mpc_solver_options.py so other test modules (e.g.
test_model_activation.py) can reuse this fixture-construction machinery
without importing a test module directly (importing a test module runs its
module-level code and collection side effects, and couples the two files).
"""

from controller.acados import GreyBoxMPCConfig
from controller.model_learning.calibration import CalibrationDecision, CalibrationProgress
from controller.mpc_config import DEFAULT_MPC_CONFIG
from controller.mpc_core import MpcCore
from controller.mpc_factory import MpcPairConfiguration, MpcPairFactory, OwnedMpcPair

CYCLE = {"u_min": 0.1, "u_max": 0.9}

def inactive_calibration(_load, _temperature, _forecast) -> CalibrationDecision:
    return CalibrationDecision(False, 0.0, None, CalibrationProgress())


def owned_pair(descriptor, estimator, solver) -> OwnedMpcPair:
    native = getattr(solver, "config", None)
    if isinstance(native, GreyBoxMPCConfig):
        pair_factory = MpcPairFactory(
            DEFAULT_MPC_CONFIG,
            "C",
            CYCLE,
            advance_calibration=inactive_calibration,
            model_authority=lambda: (0, None),
            on_policy_failure=lambda _error: None,
        )
        pair = pair_factory.adopt(
            MpcPairConfiguration(
                settings=pair_factory._settings_from_descriptor(
                    descriptor,
                    pair_factory._native_from_descriptor(descriptor),
                    descriptor.estimator_kind,
                ),
                estimator_kind=descriptor.estimator_kind,
                candidate_generation=descriptor.candidate_generation,
                role_generation=descriptor.role_generation,
                model_identified=native.residual_weight > 0.0,
            ),
            estimator,
            solver,
            authorized=False,
        )
        if pair.descriptor != descriptor:
            pair.close()
            raise ValueError("test pair descriptor does not match solver config")
        return pair
    core = MpcCore(
        DEFAULT_MPC_CONFIG,
        "C",
        CYCLE,
        components=(estimator, solver),
    )
    return OwnedMpcPair(core, descriptor)



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
