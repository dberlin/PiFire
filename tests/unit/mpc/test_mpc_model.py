import numpy as np

from controller.mpc_model import build_do_mpc_model, GreyBoxKF, simulate_grey_box

PARAMS = dict(C_c=306.0, h_amb=0.55, T_amb=20.0)


def test_model_builds():
    m = build_do_mpc_model(**PARAMS)
    assert set(m.x.keys()) >= {"T_c", "d"}
    assert "Q" in m.u.keys()


def test_the_model_has_no_firepot_state():
    """The chamber is the only thermal mass.

    Asserted against the built model rather than the source, because a firepot
    left in the do-mpc model but out of the estimators would be a state the NLP
    plans over and the EKF never estimates -- the two would disagree about what
    x_hat's slots mean, which is exactly the shape of failure the state-vector
    change can produce and which no dimension check elsewhere would catch.
    """
    m = build_do_mpc_model(**PARAMS, n_delay=3, theta=60.0)
    assert set(m.x.keys()) == {"q0", "q1", "q2", "T_c", "d"}


def test_kf_offset_free_under_constant_disturbance():
    # Feed a measurement that is persistently biased above what the model
    # predicts for zero d; the estimated d must converge so the predicted
    # chamber temp matches the measurement (offset-free).
    kf = GreyBoxKF(t_step=25.0, q_temp=1e-2, q_dist=0.5, r_meas=0.04, x0=(100.0, 0.0), **PARAMS)
    y = 100.0
    for _ in range(200):
        x = kf.update(Q_applied=49.5, y_measured=y)  # ~steady Q for 100C
    # estimated chamber temp tracks the measurement
    assert abs(x[0] - y) < 0.5
    # disturbance state is non-trivial (it absorbed the model mismatch)
    assert abs(x[1]) > 1e-6


def test_the_estimator_state_is_the_lag_chain_then_the_chamber_then_d():
    """The slot layout every consumer indexes into, pinned once.

    controller/mpc_net.py reads the disturbance out of x_hat by position, and
    controller/mpc.py hands x_hat straight to the NLP, so a wrong slot count is
    a silent misread rather than an error. The default x0 is the cheapest place
    to state the layout, since it is the only place the estimator writes a
    value into every slot by name.
    """
    kf = GreyBoxKF(t_step=5.0, q_temp=1e-2, q_dist=0.5, r_meas=0.04, theta=60.0, n_delay=4, **PARAMS)
    assert kf.n == 6
    assert list(kf.x) == [0.0, 0.0, 0.0, 0.0, PARAMS["T_amb"], 0.0]


def test_kf_tracks_measured_temperature():
    kf = GreyBoxKF(t_step=25.0, q_temp=1e-2, q_dist=0.5, r_meas=0.04, x0=(20.0, 0.0), **PARAMS)
    x = None
    for _ in range(100):
        x = kf.update(Q_applied=49.5, y_measured=110.0)
    assert abs(x[0] - 110.0) < 1.0


_P = dict(C_c=320.0, h_amb=0.5, T_amb=20.0, K_Q=3.5)


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
    # The fastest state is a transport lag stage, theta/n_delay -- 5 s for the
    # pair below -- so an Euler step taken at a 20 s log cadence is four times
    # past that stage's stability limit and runs away by orders of magnitude.
    # Integrating at max_dt instead of the sample spacing makes the result
    # independent of how often the log happened to be written.
    #
    # The chamber alone would NOT show this: C_c/h_amb is 640 s, stable at any
    # cadence a log is written at, so a version of this test without the delay
    # chain passes whether or not the sub-stepping exists.
    #
    # Both grids END at the same instant, or the comparison would measure the
    # trajectory's slope across the gap rather than the integrator's stability.
    fast = dict(_P, theta=20.0, n_delay=4)
    t_coarse = np.arange(0.0, 601.0, 20.0)
    t_fine = np.arange(0.0, 601.0, 1.0)
    coarse = simulate_grey_box(t_coarse, np.full(t_coarse.shape, 100.0), T0=20.0, **fast)
    fine = simulate_grey_box(t_fine, np.full(t_fine.shape, 100.0), T0=20.0, **fast)
    assert abs(coarse[-1] - fine[-1]) < 1.0
    # The negative control: the same coarse grid integrated AT the sample
    # spacing is the divergence the sub-stepping exists to prevent.
    unstable = simulate_grey_box(t_coarse, np.full(t_coarse.shape, 100.0), T0=20.0, max_dt=20.0, **fast)
    assert abs(unstable[-1] - fine[-1]) > 100.0
