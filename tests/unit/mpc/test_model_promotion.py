"""What may replace a model that is currently driving a fire."""

import pytest

from controller.model_promotion import PROMOTION_BOUNDS, evaluate

GOOD = dict(C_f=9.0, C_c=2520.0, h_fc=0.39, h_amb=0.224, T_amb=20.0, theta=93.0, n_delay=4, K_Q=6.95, sigma=1.4e-9)
INCUMBENT = dict(GOOD, C_c=2000.0, h_amb=0.30)  # tau 6667 vs candidate 11250
HORIZON = dict(n_horizon=144, t_step=25.0)


def _ev(candidate, incumbent=INCUMBENT, cand_rmse=2.0, inc_rmse=5.0, **kw):
    return evaluate(candidate, incumbent, candidate_rmse=cand_rmse, incumbent_rmse=inc_rmse, **{**HORIZON, **kw})


def test_a_better_fit_is_accepted():
    assert _ev(GOOD).accepted is True


def test_a_worse_fit_is_refused():
    v = _ev(GOOD, cand_rmse=9.0, inc_rmse=5.0)
    assert v.accepted is False
    assert "rmse" in v.reason.lower()


def test_the_first_model_is_accepted_when_there_is_no_incumbent():
    assert _ev(GOOD, incumbent=None, inc_rmse=None).accepted is True


def test_a_parameter_outside_its_physical_range_is_refused():
    v = _ev(dict(GOOD, C_c=-5.0))
    assert v.accepted is False
    assert "C_c" in v.reason


def test_a_non_finite_parameter_is_refused():
    assert _ev(dict(GOOD, K_Q=float("nan"))).accepted is False


def test_shrinking_tau_needs_more_evidence_than_raising_it():
    """The asymmetry is the safety argument. Believing the grill is more
    sluggish than it is makes the controller brake early and costs nothing;
    believing it is less sluggish is the 520 F incident."""
    slower = dict(GOOD, C_c=4000.0, h_amb=0.224)  # tau up
    faster = dict(GOOD, C_c=1000.0, h_amb=0.224)  # tau down
    margin = dict(cand_rmse=4.9, inc_rmse=5.0)  # barely better
    assert _ev(slower, **margin).accepted is True
    assert _ev(faster, **margin).accepted is False


def test_a_large_tau_reduction_is_accepted_on_strong_evidence():
    faster = dict(GOOD, C_c=1000.0, h_amb=0.224)
    assert _ev(faster, cand_rmse=0.5, inc_rmse=5.0).accepted is True


def test_a_model_needing_more_horizon_than_configured_reports_it():
    """tau 11250 s against a 24*25 = 600 s horizon."""
    v = _ev(GOOD, n_horizon=24, t_step=25.0)
    assert v.horizon_needed is not None
    assert v.horizon_needed > 24


def test_an_adequate_horizon_asks_for_nothing():
    assert _ev(GOOD, n_horizon=600, t_step=25.0).horizon_needed is None


def test_every_fitted_parameter_has_a_bound():
    for key in ("C_f", "C_c", "h_fc", "h_amb", "theta", "K_Q"):
        assert key in PROMOTION_BOUNDS
