import numpy as np

from controller.mpc_model import build_do_mpc_model, GreyBoxKF, simulate_grey_box

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


_P = dict(C_f=9.0, C_c=320.0, h_fc=1.3, h_amb=0.5, T_amb=20.0, K_Q=3.5)


def test_output_starts_at_t0_and_is_aligned_with_the_time_grid():
    t = np.arange(0.0, 60.0, 5.0)
    out = simulate_grey_box(t, np.full(t.shape, 50.0), T0=20.0, **_P)
    assert out.shape == t.shape
    assert out[0] == 20.0


def test_zero_firing_rate_decays_toward_ambient():
    t = np.arange(0.0, 3000.0, 5.0)
    out = simulate_grey_box(t, np.zeros(t.shape), T0=200.0, **_P)
    assert out[-1] < out[0]
    assert out[-1] > _P["T_amb"] - 1.0


def test_radiative_loss_lowers_the_trajectory():
    t = np.arange(0.0, 1200.0, 5.0)
    Q = np.full(t.shape, 100.0)
    linear = simulate_grey_box(t, Q, T0=20.0, sigma=0.0, **_P)
    radiative = simulate_grey_box(t, Q, T0=20.0, sigma=1.4e-9, **_P)
    assert radiative[-1] < linear[-1]


def test_transport_delay_postpones_the_response():
    t = np.arange(0.0, 600.0, 5.0)
    Q = np.full(t.shape, 100.0)
    prompt = simulate_grey_box(t, Q, T0=20.0, theta=0.0, n_delay=0, **_P)
    delayed = simulate_grey_box(t, Q, T0=20.0, theta=100.0, n_delay=4, **_P)
    # Delay only postpones; it removes no energy, so early samples lag and the
    # gap closes as the chain fills.
    assert delayed[10] < prompt[10]
    assert delayed[-1] < prompt[-1]


def test_substepping_keeps_a_coarse_log_grid_stable():
    # The firepot time constant C_f/h_fc is ~7 s, so an Euler step taken at a
    # 20 s log cadence is past the stability limit and runs away by orders of
    # magnitude. Integrating at max_dt instead of the sample spacing makes the
    # result independent of how often the log happened to be written.
    #
    # Both grids END at the same instant, or the comparison would measure the
    # trajectory's slope across the gap rather than the integrator's stability.
    t_coarse = np.arange(0.0, 601.0, 20.0)
    t_fine = np.arange(0.0, 601.0, 1.0)
    coarse = simulate_grey_box(t_coarse, np.full(t_coarse.shape, 100.0), T0=20.0, **_P)
    fine = simulate_grey_box(t_fine, np.full(t_fine.shape, 100.0), T0=20.0, **_P)
    assert abs(coarse[-1] - fine[-1]) < 1.0
