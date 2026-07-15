import numpy as np
from controller.mpc_model import build_do_mpc_model, GreyBoxKF

PARAMS = dict(C_f=60.0, C_c=306.0, h_fc=2.0, h_amb=0.55, T_amb=20.0)


def test_model_builds():
    m = build_do_mpc_model(**PARAMS)
    assert set(m.x.keys()) >= {"T_f", "T_c", "d"}
    assert "Q" in m.u.keys()


def test_kf_offset_free_under_constant_disturbance():
    # Feed a measurement that is persistently biased above what the model
    # predicts for zero d; the estimated d must converge so the predicted
    # chamber temp matches the measurement (offset-free).
    kf = GreyBoxKF(t_step=25.0, q_temp=1e-2, q_dist=0.5, r_meas=0.04, x0=(100.0, 100.0, 0.0), **PARAMS)
    y = 100.0
    for _ in range(200):
        x = kf.update(Q_applied=49.5, y_measured=y)  # ~steady Q for 100C
    # estimated chamber temp tracks the measurement
    assert abs(x[1] - y) < 0.5
    # disturbance state is non-trivial (it absorbed the model mismatch)
    assert abs(x[2]) > 1e-6


def test_kf_tracks_measured_temperature():
    kf = GreyBoxKF(t_step=25.0, q_temp=1e-2, q_dist=0.5, r_meas=0.04, x0=(20.0, 20.0, 0.0), **PARAMS)
    x = None
    for _ in range(100):
        x = kf.update(Q_applied=49.5, y_measured=110.0)
    assert abs(x[1] - 110.0) < 1.0
