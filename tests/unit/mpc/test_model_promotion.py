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


def test_a_non_integral_delay_count_is_refused():
    assert _ev(dict(GOOD, n_delay=4.5)).accepted is False


def test_a_non_finite_incumbent_rmse_is_refused_not_accepted():
    """A NaN incumbent RMSE must not fail open: every RMSE comparison against
    it is False, so without an explicit guard a huge-error, tau-shrinking
    candidate would sail through the refusal branch and be adopted."""
    faster = dict(GOOD, C_c=1000.0, h_amb=0.224)
    v = _ev(faster, cand_rmse=1e6, inc_rmse=float("nan"))
    assert v.accepted is False


def test_a_partial_incumbent_is_refused_not_a_crash():
    v = _ev(GOOD, incumbent={"C_c": 2000.0})
    assert v.accepted is False


def test_a_zero_time_step_is_refused_not_a_crash():
    assert _ev(GOOD, n_horizon=144, t_step=0.0).accepted is False


def test_a_non_finite_time_step_does_not_report_the_horizon_as_adequate():
    v = _ev(GOOD, n_horizon=144, t_step=float("nan"))
    assert v.accepted is False


def test_a_negative_horizon_count_is_refused():
    assert _ev(GOOD, n_horizon=-1, t_step=25.0).accepted is False


def test_shrinking_tau_needs_more_evidence_than_raising_it():
    """The asymmetry is the safety argument. Believing the grill is more
    sluggish than it is makes the controller brake early and costs nothing;
    believing it is less sluggish is the 520 F incident."""
    slower = dict(GOOD, C_c=4000.0, h_amb=0.224)  # tau up
    faster = dict(GOOD, C_c=1000.0, h_amb=0.224)  # tau down
    margin = dict(cand_rmse=4.85, inc_rmse=5.0)  # clears the 2% bar, not the 50% bar
    assert _ev(slower, **margin).accepted is True
    assert _ev(faster, **margin).accepted is False


def test_a_large_tau_reduction_is_accepted_on_strong_evidence():
    faster = dict(GOOD, C_c=1000.0, h_amb=0.224)
    assert _ev(faster, cand_rmse=0.5, inc_rmse=5.0).accepted is True


def test_repeated_small_tau_cuts_cannot_walk_tau_down_without_clearing_the_wide_bar():
    """A chain of cuts just under the old 10% deadband, each individually
    plausible on the narrow margin, must never compound into a large,
    unproven tau reduction: every one of them must face the wide margin
    instead, or the incumbent never advances."""
    incumbent, incumbent_rmse = dict(GOOD), 5.0
    for _ in range(15):
        candidate = dict(incumbent, C_c=incumbent["C_c"] * 0.91)  # ~9% cut, just under the old deadband
        candidate_rmse = incumbent_rmse * 0.99  # clears the 2% bar, not the 50% bar
        v = evaluate(candidate, incumbent, candidate_rmse=candidate_rmse, incumbent_rmse=incumbent_rmse, **HORIZON)
        if v.accepted:
            incumbent, incumbent_rmse = candidate, candidate_rmse
    assert incumbent["C_c"] == GOOD["C_c"]


def test_the_rmse_margin_is_exactly_two_percent():
    """Pinned against a literal, not the module's own constant: importing
    _RMSE_MARGIN here would move this test's threshold with any mutation of
    it, so the test could never fail no matter what the constant became."""
    slower = dict(GOOD, C_c=4000.0, h_amb=0.224)  # longer tau: the narrow margin applies
    at_margin = _ev(slower, cand_rmse=4.9, inc_rmse=5.0)  # 5.0 * (1 - 0.02)
    just_worse = _ev(slower, cand_rmse=4.92, inc_rmse=5.0)
    assert at_margin.accepted is True
    assert just_worse.accepted is False


def test_the_faster_rmse_margin_is_exactly_fifty_percent():
    """Pinned against a literal for the same reason as the test above."""
    faster = dict(GOOD, C_c=1000.0, h_amb=0.224)  # shorter tau: the wide margin applies
    at_margin = _ev(faster, cand_rmse=2.5, inc_rmse=5.0)  # 5.0 * (1 - 0.50)
    just_worse = _ev(faster, cand_rmse=2.52, inc_rmse=5.0)
    assert at_margin.accepted is True
    assert just_worse.accepted is False


def test_a_small_tau_increase_is_reported_as_unchanged_not_longer():
    """theta is held equal to the incumbent so its own "unchanged" label
    cannot make this pass regardless of tau's label -- the assertion checks
    the tau phrase specifically."""
    within_deadband = dict(GOOD, C_c=2000.0 * 1.05, h_amb=0.30)  # +5% tau: inside the 10% deadband
    v = _ev(within_deadband, cand_rmse=9.0, inc_rmse=5.0)  # deliberately refused; inspect the label
    assert v.accepted is False
    assert "unchanged tau" in v.reason.lower()


def test_a_material_tau_increase_is_reported_as_longer():
    beyond_deadband = dict(GOOD, C_c=2000.0 * 1.20, h_amb=0.30)  # +20% tau: past the 10% deadband
    v = _ev(beyond_deadband, cand_rmse=9.0, inc_rmse=5.0)
    assert v.accepted is False
    assert "longer tau" in v.reason.lower()


def test_shortening_dead_time_needs_the_same_wide_margin_as_tau():
    """theta shortening under-anticipates the transport delay the same way a
    short tau under-anticipates the chamber lag: both make the controller
    over-feed. It must face the same asymmetric bar."""
    shorter_theta = dict(GOOD, theta=10.0)
    v = _ev(shorter_theta, cand_rmse=4.85, inc_rmse=5.0)  # clears the 2% bar, not the 50% bar
    assert v.accepted is False


def test_a_model_needing_more_horizon_than_configured_reports_it():
    """tau 11250 s against a 24*25 = 600 s horizon."""
    v = _ev(GOOD, n_horizon=24, t_step=25.0)
    assert v.horizon_needed is not None
    assert v.horizon_needed > 24


def test_an_adequate_horizon_asks_for_nothing():
    assert _ev(GOOD, n_horizon=600, t_step=25.0).horizon_needed is None


def test_every_fitted_parameter_has_a_bound():
    for key in GOOD:
        assert key in PROMOTION_BOUNDS


#: Literal, not derived from PROMOTION_BOUNDS: reading the bounds under test
#: from the dict they are meant to pin would move the expected edge along
#: with any mutation of it, so the test could never fail no matter what the
#: bound became.
_EXPECTED_BOUNDS = {
    "C_f": (0.1, 1e4),
    "C_c": (1.0, 1e6),
    "h_fc": (1e-3, 1e3),
    "h_amb": (1e-4, 1e3),
    "T_amb": (-40.0, 60.0),
    "theta": (0.0, 1200.0),
    "n_delay": (0.0, 50.0),
    "K_Q": (1e-3, 1e4),
    "sigma": (0.0, 1e-6),
}


def test_every_bound_is_pinned_by_a_literal():
    assert _EXPECTED_BOUNDS == PROMOTION_BOUNDS


@pytest.mark.parametrize("key,lo,hi", [(k, *bounds) for k, bounds in _EXPECTED_BOUNDS.items()])
def test_each_bound_is_enforced_at_its_edge(key, lo, hi):
    assert _ev(dict(GOOD, **{key: lo})).accepted is True
    assert _ev(dict(GOOD, **{key: lo - abs(lo) * 1e-6 - 1e-9})).accepted is False

    assert _ev(dict(GOOD, **{key: hi})).accepted is True
    assert _ev(dict(GOOD, **{key: hi + abs(hi) * 1e-6 + 1e-9})).accepted is False
