"""What may replace a model that is currently driving a fire."""

import pytest

from controller.model_promotion import PROMOTION_BOUNDS, effective_tau, evaluate

GOOD = dict(C_f=9.0, C_c=2520.0, h_fc=0.39, h_amb=0.224, T_amb=20.0, theta=93.0, n_delay=4, K_Q=6.95, sigma=1.4e-9)
INCUMBENT = dict(GOOD, C_c=2000.0, h_amb=0.30)  # tau 6667 vs candidate 11250
HORIZON = dict(n_horizon=144, t_step=25.0)

#: Literals, not the module's own reference temperatures: 550 F is `maxtemp`
#: and 75 F is `minstartuptemp`, both from common/settings_schema.py. Importing
#: the module's constants here would move every expectation below along with
#: any mutation of them, so the tests could never fail.
_HAZARD_C = (550.0 - 32.0) * 5.0 / 9.0
_FLOOR_C = (75.0 - 32.0) * 5.0 / 9.0


def _ev(candidate, incumbent=INCUMBENT, cand_rmse=2.0, inc_rmse=5.0, **kw):
    return evaluate(candidate, incumbent, candidate_rmse=cand_rmse, incumbent_rmse=inc_rmse, **{**HORIZON, **kw})


def _crossing_at(incumbent, t_cross_c, ratio):
    """A candidate whose effective tau equals `incumbent`'s at `t_cross_c`.

    Same sigma, C_c scaled by `ratio`, and h_amb solved so the two effective-tau
    curves meet exactly there. ratio < 1 puts the candidate above the incumbent
    below the crossing and below it above; ratio > 1 does the reverse. Which
    side of the crossing a reference temperature falls on is therefore what the
    verdict reports, which is what makes these pin the reference temperatures.
    """
    conductance = 4.0 * float(incumbent["sigma"]) * (t_cross_c + 273.15) ** 3
    h_amb = ratio * float(incumbent["h_amb"]) + (ratio - 1.0) * conductance
    return dict(incumbent, C_c=float(incumbent["C_c"]) * ratio, h_amb=h_amb)


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


def test_a_live_incumbent_with_unrecorded_rmse_is_refused():
    """A real incumbent whose RMSE was never recorded is not the same thing
    as no incumbent at all: the comparison cannot be made, so this must
    refuse rather than take the first-model acceptance path."""
    faster = dict(GOOD, C_c=1000.0, h_amb=0.224)
    v = _ev(faster, incumbent=INCUMBENT, cand_rmse=1e12, inc_rmse=None)
    assert v.accepted is False


def test_a_partial_incumbent_is_refused_not_a_crash():
    v = _ev(GOOD, incumbent={"C_c": 2000.0})
    assert v.accepted is False


def test_a_non_numeric_parameter_is_refused_not_a_crash():
    assert _ev(dict(GOOD, C_f="nine")).accepted is False


def test_a_negative_candidate_rmse_is_refused():
    """A negative RMSE is nonsense, and left unchecked it beats every
    incumbent automatically since it is less than any positive threshold."""
    faster = dict(GOOD, C_c=1000.0, h_amb=0.224)
    assert _ev(faster, cand_rmse=-1.0, inc_rmse=5.0).accepted is False


def test_a_zero_incumbent_rmse_is_refused():
    """A perfect incumbent cannot be fairly beaten. Pinned on the reason
    text, not just accepted=False: with candidate_rmse guaranteed positive
    by the earlier guard, the final RMSE-margin comparison alone would also
    refuse a positive candidate RMSE against a zero incumbent RMSE (any
    positive number beats a zero-or-negative threshold), so only the reason
    distinguishes this explicit guard from being deleted."""
    faster = dict(GOOD, C_c=1000.0, h_amb=0.224)
    v = _ev(faster, cand_rmse=0.001, inc_rmse=0.0)
    assert v.accepted is False
    assert "incumbent rmse" in v.reason.lower()


def test_a_non_numeric_delay_count_is_refused_not_a_crash():
    """n_delay="4.5" parses as finite (4.5) and passes the range check, then
    fails is_integer(); the refusal message must format the parsed number,
    not re-index the raw (still-a-string) candidate value."""
    assert _ev(dict(GOOD, n_delay="4.5")).accepted is False


def test_theta_zero_with_an_active_delay_chain_is_refused():
    """theta=0 with n_delay>=1 divides by zero building the transport-lag
    chain's per-stage time constant (theta/n_delay) in GreyBoxKF/GreyBoxEKF;
    a promoted model must be one the controller can actually run."""
    v = _ev(dict(GOOD, theta=0.0))  # n_delay stays 4: the chain is active
    assert v.accepted is False


def test_the_theta_bound_is_enforced():
    assert _ev(dict(GOOD, theta=-1e-9)).accepted is False
    assert _ev(dict(GOOD, theta=0.0, n_delay=0)).accepted is True
    assert _ev(dict(GOOD, theta=1200.0)).accepted is True
    assert _ev(dict(GOOD, theta=1200.0 + 1e-3)).accepted is False


def test_an_incumbent_missing_n_delay_is_refused_not_a_crash():
    incumbent = dict(C_c=2000.0, h_amb=0.30, theta=93.0, sigma=1.4e-9)  # n_delay omitted
    assert _ev(GOOD, incumbent=incumbent).accepted is False


def test_an_incumbent_missing_sigma_is_refused_not_a_crash():
    incumbent = dict(C_c=2000.0, h_amb=0.30, theta=93.0, n_delay=4)  # sigma omitted
    assert _ev(GOOD, incumbent=incumbent).accepted is False


def test_h_amb_alone_can_shrink_tau():
    """Raising h_amb alone (C_c unchanged) shortens tau exactly like cutting
    C_c does, so it must face the same wide margin -- pins h_amb's presence
    in the tau computation, not just C_c's."""
    faster = dict(GOOD, C_c=2000.0, h_amb=0.30 * 3)  # incumbent's own C_c, h_amb tripled
    v = _ev(faster, cand_rmse=4.85, inc_rmse=5.0)  # clears the 2% bar, not the 50% bar
    assert v.accepted is False


def test_n_delay_equal_to_one_keeps_the_delay_chain_active():
    """n_delay=1 is the smallest value that keeps the transport-delay chain
    on -- theta must still contribute, unlike n_delay=0."""
    one_delay = dict(GOOD, n_delay=1)
    v = _ev(one_delay, cand_rmse=4.85, inc_rmse=5.0)  # clears the 2% bar, not the 50% bar; theta unchanged
    assert v.accepted is True


def test_raising_sigma_needs_the_same_wide_margin_as_shortening_tau():
    """sigma prices into the effective tau via its linearized radiative
    conductance: raising it shortens the true braking distance even though C_c
    and h_amb are unchanged, and must face the same asymmetric bar."""
    incumbent = dict(GOOD, sigma=0.0)  # no radiative correction: effective tau == C_c/h_amb
    hotter_sigma = dict(GOOD, sigma=1e-8)  # at PROMOTION_BOUNDS' cap
    v = _ev(hotter_sigma, incumbent=incumbent, cand_rmse=4.85, inc_rmse=5.0)  # clears 2%, not 50%
    assert v.accepted is False


def test_raising_sigma_is_accepted_on_strong_evidence():
    incumbent = dict(GOOD, sigma=0.0)
    hotter_sigma = dict(GOOD, sigma=1e-8)
    assert _ev(hotter_sigma, incumbent=incumbent, cand_rmse=0.5, inc_rmse=5.0).accepted is True


def test_the_sigma_bound_is_enforced():
    assert _ev(dict(GOOD, sigma=-1e-12)).accepted is False
    assert _ev(dict(GOOD, sigma=0.0)).accepted is True
    assert _ev(dict(GOOD, sigma=1e-8)).accepted is True
    assert _ev(dict(GOOD, sigma=1e-8 + 1e-15)).accepted is False


def test_the_radiative_conductance_is_four_sigma_at_absolute_temperature():
    """The linearization of sigma*(T_c+273.15)**4 that GreyBoxEKF._discretize
    actually applies, pinned against literals: the factor 4 and the Kelvin
    offset both change how much braking distance a given sigma is charged
    for, and nothing else in this file can tell either of them apart from a
    different value."""
    params = dict(GOOD, C_c=2000.0, h_amb=0.30, sigma=1.4e-9)
    expected = 2000.0 / (0.30 + 4.0 * 1.4e-9 * (200.0 + 273.15) ** 3)
    assert effective_tau(params, 200.0) == pytest.approx(expected, rel=1e-12)


def test_trading_sigma_against_h_amb_cannot_launder_a_shortening_past_the_hot_end():
    """h_amb is flat in temperature while the radiative conductance grows as
    T**3, so a candidate can cut sigma and raise h_amb by amounts that leave
    the effective tau untouched at the hottest permitted temperature while
    genuinely shortening it everywhere below. Read at one temperature that is
    invisible; the whole operating range must be charged for."""
    incumbent = dict(GOOD, C_c=2000.0, h_amb=0.30, sigma=1.4e-9)
    cooler_sigma = 0.4e-9
    laundered = dict(
        incumbent,
        sigma=cooler_sigma,
        h_amb=0.30 + 4.0 * (1.4e-9 - cooler_sigma) * (_HAZARD_C + 273.15) ** 3,
    )
    assert effective_tau(laundered, _HAZARD_C) == pytest.approx(effective_tau(incumbent, _HAZARD_C), rel=1e-12)
    assert effective_tau(laundered, _FLOOR_C) < effective_tau(incumbent, _FLOOR_C)

    v = _ev(laundered, incumbent=incumbent, cand_rmse=4.85, inc_rmse=5.0)  # clears 2%, not 50%
    assert v.accepted is False
    assert "shorter tau" in v.reason.lower()


def test_the_laundered_shortening_is_still_accepted_on_strong_evidence():
    incumbent = dict(GOOD, C_c=2000.0, h_amb=0.30, sigma=1.4e-9)
    cooler_sigma = 0.4e-9
    laundered = dict(
        incumbent,
        sigma=cooler_sigma,
        h_amb=0.30 + 4.0 * (1.4e-9 - cooler_sigma) * (_HAZARD_C + 273.15) ** 3,
    )
    assert _ev(laundered, incumbent=incumbent, cand_rmse=0.5, inc_rmse=5.0).accepted is True


def test_repeated_sigma_for_h_amb_trades_cannot_walk_the_cool_end_tau_down():
    """The single-step trade compounds: each step is invisible at the hot end
    and small at the cool end, so on the narrow bar a chain of them deletes
    the modelled radiative loss outright and halves the true time constant at
    a low-and-slow hold."""
    incumbent, incumbent_rmse = dict(GOOD, C_c=2000.0, h_amb=0.30, sigma=1.4e-9), 5.0
    for _ in range(25):
        step = incumbent["sigma"] * 0.05
        candidate = dict(
            incumbent,
            sigma=incumbent["sigma"] - step,
            h_amb=incumbent["h_amb"] + 4.0 * step * (_HAZARD_C + 273.15) ** 3,
        )
        candidate_rmse = incumbent_rmse * 0.97  # clears the 2% bar decisively, not the 50% bar
        v = evaluate(candidate, incumbent, candidate_rmse=candidate_rmse, incumbent_rmse=incumbent_rmse, **HORIZON)
        if v.accepted:
            incumbent, incumbent_rmse = candidate, candidate_rmse
    assert incumbent["sigma"] == GOOD["sigma"]
    assert incumbent["h_amb"] == 0.30


def test_a_crossing_above_the_hot_end_leaves_the_candidate_longer_throughout():
    """Pins the hot reference from above. The two effective-tau curves meet
    half a degree past the hard shutoff, so the candidate is the slower model
    everywhere the grill may actually run and the narrow bar applies. A
    reference read any hotter than the crossing would see a shortening that
    the grill can never reach."""
    candidate = _crossing_at(INCUMBENT, _HAZARD_C + 0.5, ratio=0.9)
    assert _ev(candidate, cand_rmse=4.85, inc_rmse=5.0).accepted is True


def test_a_crossing_below_the_hot_end_is_charged_the_wide_bar():
    """Pins the hot reference from below. The curves meet half a degree under
    the hard shutoff, so the candidate is quicker at the top of the range and
    slower everywhere else -- invisible to any reference read cooler than the
    crossing."""
    candidate = _crossing_at(INCUMBENT, _HAZARD_C - 0.5, ratio=0.9)
    assert _ev(candidate, cand_rmse=4.85, inc_rmse=5.0).accepted is False


def test_a_crossing_below_the_cool_end_leaves_the_candidate_longer_throughout():
    """Pins the cool reference from below. The curves meet half a degree under
    the flameout floor, so the candidate is the slower model throughout the
    range the controller drives, and the narrow bar applies."""
    candidate = _crossing_at(INCUMBENT, _FLOOR_C - 0.5, ratio=1.1)
    assert _ev(candidate, cand_rmse=4.85, inc_rmse=5.0).accepted is True


def test_a_crossing_above_the_cool_end_is_charged_the_wide_bar():
    """Pins the cool reference from above. The curves meet half a degree over
    the flameout floor, so the candidate is quicker at the bottom of the range
    and slower everywhere else -- invisible to any reference read hotter than
    the crossing."""
    candidate = _crossing_at(INCUMBENT, _FLOOR_C + 0.5, ratio=1.1)
    assert _ev(candidate, cand_rmse=4.85, inc_rmse=5.0).accepted is False


def test_repeated_small_sigma_increases_cannot_walk_true_tau_down_without_clearing_the_wide_bar():
    """sigma raises the effective tau's radiative conductance the same way a
    C_c/h_amb cut lowers it directly; a chain of small sigma increases must
    face the same wide margin as any other tau-shrinking route."""
    incumbent, incumbent_rmse = dict(GOOD, sigma=0.0), 5.0
    for _ in range(15):
        candidate = dict(incumbent, sigma=incumbent["sigma"] + 1e-10)  # small step toward the 2e-9 cap
        candidate_rmse = incumbent_rmse * 0.97  # clears the 2% bar decisively, not the 50% bar
        v = evaluate(candidate, incumbent, candidate_rmse=candidate_rmse, incumbent_rmse=incumbent_rmse, **HORIZON)
        if v.accepted:
            incumbent, incumbent_rmse = candidate, candidate_rmse
    assert incumbent["sigma"] == 0.0


def test_repeated_joint_sigma_and_tau_cuts_cannot_walk_true_tau_down_without_clearing_the_wide_bar():
    """A candidate can cut C_c and raise sigma in the same promotion; the
    guard must catch the combined effect on true tau, not just each
    parameter considered alone."""
    incumbent, incumbent_rmse = dict(GOOD, sigma=0.0), 5.0
    for _ in range(15):
        candidate = dict(
            incumbent,
            C_c=incumbent["C_c"] * 0.97,  # small cut, well under the old 10% deadband
            sigma=incumbent["sigma"] + 5e-11,  # small step, well under the 2e-9 cap
        )
        candidate_rmse = incumbent_rmse * 0.97  # clears the 2% bar decisively, not the 50% bar
        v = evaluate(candidate, incumbent, candidate_rmse=candidate_rmse, incumbent_rmse=incumbent_rmse, **HORIZON)
        if v.accepted:
            incumbent, incumbent_rmse = candidate, candidate_rmse
    assert incumbent["C_c"] == GOOD["C_c"]
    assert incumbent["sigma"] == 0.0


def test_an_incumbent_with_no_positive_reference_always_faces_the_wide_margin():
    """A zero effective dead time on the incumbent side cannot be safely
    divided into or compared against, so it is treated as the risky case
    rather than silently taking the narrow bar."""
    incumbent = dict(GOOD, C_c=2000.0, h_amb=0.30, theta=0.0, n_delay=0)
    v = _ev(GOOD, incumbent=incumbent, cand_rmse=4.85, inc_rmse=5.0)  # clears the 2% bar, not the 50% bar
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
        candidate_rmse = incumbent_rmse * 0.97  # clears the 2% bar decisively, not the 50% bar
        v = evaluate(candidate, incumbent, candidate_rmse=candidate_rmse, incumbent_rmse=incumbent_rmse, **HORIZON)
        if v.accepted:
            incumbent, incumbent_rmse = candidate, candidate_rmse
    assert incumbent["C_c"] == GOOD["C_c"]


def test_setting_n_delay_to_zero_needs_the_same_wide_margin_as_shortening_theta():
    """n_delay=0 removes the transport-lag chain outright, so it cuts the
    effective dead time to zero even if theta itself is untouched. That must
    face the same asymmetric bar as shortening theta directly, not the
    narrow one theta's own unchanged value would otherwise suggest."""
    no_delay = dict(GOOD, n_delay=0)
    v = _ev(no_delay, cand_rmse=4.85, inc_rmse=5.0)  # clears the 2% bar, not the 50% bar
    assert v.accepted is False


def test_setting_n_delay_to_zero_is_accepted_on_strong_evidence():
    no_delay = dict(GOOD, n_delay=0)
    assert _ev(no_delay, cand_rmse=0.5, inc_rmse=5.0).accepted is True


def test_the_n_delay_bound_is_enforced():
    assert _ev(dict(GOOD, n_delay=-1e-9)).accepted is False
    assert _ev(dict(GOOD, n_delay=50.0)).accepted is True
    assert _ev(dict(GOOD, n_delay=50.0 + 1e-6)).accepted is False


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


def test_the_horizon_is_sized_from_the_radiation_free_tau_and_tracks_h_amb():
    """The horizon is sized from C_c/h_amb, which bounds the effective tau
    from above at every temperature, so one horizon stays adequate across the
    whole range rather than only at the end it was measured at. Pinned on
    exact step counts: sizing it from either operating-range endpoint instead
    would ask for fewer steps, and halving h_amb must double the request."""
    v = _ev(dict(GOOD, C_c=2000.0, h_amb=0.5), n_horizon=10, t_step=25.0)
    assert v.horizon_needed == 160  # 2000/0.5 = 4000 s over a 25 s step

    halved = _ev(dict(GOOD, C_c=2000.0, h_amb=0.25), n_horizon=10, t_step=25.0)
    assert halved.horizon_needed == 320


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
    "sigma": (0.0, 1e-8),
}


def test_every_bound_is_pinned_by_a_literal():
    assert _EXPECTED_BOUNDS == PROMOTION_BOUNDS


#: n_delay and theta are covered by their own dedicated tests instead: n_delay's
#: lower edge (0) interacts with theta through the effective-dead-time rule, and
#: theta's lower edge (0) is only valid when n_delay is also 0 (otherwise the
#: transport-delay chain divides by n_delay into a zero theta) -- both depend on
#: the OTHER parameter's value, not just on whether the bare value is in range.
@pytest.mark.parametrize(
    "key,lo,hi", [(k, *bounds) for k, bounds in _EXPECTED_BOUNDS.items() if k not in ("n_delay", "theta")]
)
def test_each_bound_is_enforced_at_its_edge(key, lo, hi):
    assert _ev(dict(GOOD, **{key: lo})).accepted is True
    assert _ev(dict(GOOD, **{key: lo - abs(lo) * 1e-6 - 1e-9})).accepted is False

    assert _ev(dict(GOOD, **{key: hi})).accepted is True
    assert _ev(dict(GOOD, **{key: hi + abs(hi) * 1e-6 + 1e-9})).accepted is False
