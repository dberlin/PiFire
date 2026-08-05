import numpy as np

from controller.mpc_model import build_do_mpc_model, GreyBoxKF, simulate_grey_box

PARAMS = dict(C_c=306.0, h_amb=0.55, T_amb=20.0)


def test_model_exposes_one_normalized_combustion_load_input():
    m = build_do_mpc_model(**PARAMS)
    assert set(m.x.keys()) >= {"T_c", "d"}
    assert set(m.u.keys()) - {"default"} == {"combustion_load"}


def test_kf_rejects_an_applied_load_outside_the_normalized_domain():
    kf = GreyBoxKF(t_step=25.0, q_temp=1e-2, q_dist=0.5, r_meas=0.04, x0=(20.0, 0.0), **PARAMS)
    with np.testing.assert_raises_regex(ValueError, "normalized combustion load"):
        kf.update(1.01, 100.0)


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
        x = kf.update(normalized_combustion_load=0.495, y_measured=y)  # ~steady load for 100C
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
        x = kf.update(normalized_combustion_load=0.495, y_measured=110.0)
    assert abs(x[0] - 110.0) < 1.0


_P = dict(C_c=320.0, h_amb=0.5, T_amb=20.0, K_Q=350.0)


def test_output_starts_at_t0_and_is_aligned_with_the_time_grid():
    t = np.arange(0.0, 60.0, 5.0)
    out = simulate_grey_box(t, np.full(t.shape, 0.5), T0=20.0, **_P)
    assert out.shape == t.shape
    assert out[0] == 20.0


def test_zero_firing_rate_decays_toward_ambient():
    t = np.arange(0.0, 3000.0, 5.0)
    out = simulate_grey_box(t, np.zeros(t.shape), T0=200.0, **_P)
    assert out[-1] < out[0]
    assert out[-1] > _P["T_amb"] - 1.0


def test_radiative_loss_lowers_the_trajectory():
    t = np.arange(0.0, 1200.0, 5.0)
    combustion_load = np.full(t.shape, 1.0)
    linear = simulate_grey_box(t, combustion_load, T0=20.0, sigma=0.0, **_P)
    radiative = simulate_grey_box(t, combustion_load, T0=20.0, sigma=1.4e-9, **_P)
    assert radiative[-1] < linear[-1]


def test_transport_delay_postpones_the_response():
    t = np.arange(0.0, 600.0, 5.0)
    combustion_load = np.full(t.shape, 1.0)
    prompt = simulate_grey_box(t, combustion_load, T0=20.0, theta=0.0, n_delay=0, **_P)
    delayed = simulate_grey_box(t, combustion_load, T0=20.0, theta=100.0, n_delay=4, **_P)
    # Delay only postpones; it removes no energy, so early samples lag and the
    # gap closes as the chain fills.
    assert delayed[10] < prompt[10]
    assert delayed[-1] < prompt[-1]


def test_substepping_makes_the_answer_independent_of_the_log_cadence():
    # A log is written at whatever cadence the controller happened to run at,
    # and the same grill must fit to the same parameters either way. Sub-
    # stepping to max_dt is what decouples the two: without it the chamber's
    # Euler step is the sample spacing, and a 20 s spacing is 160 times the
    # shipped sub-step.
    #
    # Both grids END at the same instant, or the comparison would measure the
    # trajectory's slope across the gap rather than the integrator.
    fast = dict(_P, theta=20.0, n_delay=4)
    t_coarse = np.arange(0.0, 601.0, 20.0)
    t_fine = np.arange(0.0, 601.0, 1.0)
    coarse = simulate_grey_box(t_coarse, np.full(t_coarse.shape, 1.0), T0=20.0, **fast)
    fine = simulate_grey_box(t_fine, np.full(t_fine.shape, 1.0), T0=20.0, **fast)
    assert abs(coarse[-1] - fine[-1]) < 0.05
    # The negative control: the same coarse grid integrated AT the sample
    # spacing, which is the log cadence leaking into the answer.
    cadence_bound = simulate_grey_box(t_coarse, np.full(t_coarse.shape, 1.0), T0=20.0, max_dt=20.0, **fast)
    assert abs(cadence_bound[-1] - fine[-1]) > 5.0


def test_a_short_deadtime_simulates_instead_of_overflowing():
    # theta = 3 s over 8 stages is a 0.375 s lag stage. Integrating the chain
    # with explicit Euler is stable only below 2 * theta / n_delay = 0.75 s, so
    # at the 1 s sub-step this function used to take, this record overflowed to
    # inf and the calibration solve consumed it as data. theta is FITTED, with
    # no lower bound worth the name, so nothing upstream can promise it away.
    #
    # The chain is now advanced in closed form, which has no stability limit at
    # all -- so the assertion is the stronger one: not merely finite, but
    # correct against a converged reference.
    fast = dict(_P, theta=3.0, n_delay=8)
    t = np.arange(0.0, 1200.0, 5.0)
    combustion_load = np.where(t < 600.0, 1.0, 0.2)
    out = simulate_grey_box(t, combustion_load, T0=20.0, **fast)
    assert np.all(np.isfinite(out))
    # A converged reference: a sub-step 60x shorter than the shipped one, which
    # is far enough down the first-order error curve to be the answer.
    converged = simulate_grey_box(t, combustion_load, T0=20.0, max_dt=0.002, **fast)
    # 0.044 C as shipped; an Euler chain given a sub-step short enough to be
    # stable here at all still lands at 0.57 C.
    assert float(np.sqrt(np.mean((out - converged) ** 2))) < 0.1


def test_the_delay_chain_carries_no_discretization_error_of_its_own():
    # The chain is linear and its input is constant across a sample interval,
    # so its state is exact at any sub-step. That is what removed the bias the
    # solve used to pay for by inflating theta: an Euler chain under-delays by
    # about half a sub-step per stage, which at n_delay=8 and a 1 s sub-step is
    # 8 s of deadtime the grill does not have.
    #
    # Measured as the whole simulation's sensitivity to the sub-step at a LONG
    # theta, where the chamber contributes least: a 16x coarser sub-step must
    # not move the trajectory by more than the chamber's own first-order error
    # over that step, and nothing like the seconds of delay an Euler chain
    # would shift it by.
    slow = dict(_P, theta=160.0, n_delay=8)
    t = np.arange(0.0, 2400.0, 5.0)
    combustion_load = np.where(t < 1200.0, 1.0, 0.0)
    shipped = simulate_grey_box(t, combustion_load, T0=20.0, **slow)
    coarse = simulate_grey_box(t, combustion_load, T0=20.0, max_dt=2.0, **slow)
    # 0.81 C as shipped, all of it the chamber's; an Euler chain moves 10.9 C
    # over the same change of sub-step, which is the 8 s of delay it loses.
    assert float(np.max(np.abs(coarse - shipped))) < 2.0
