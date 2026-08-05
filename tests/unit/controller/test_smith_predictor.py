"""The predictor's two branches are exact first-order trajectories, and the
correction between them starts at exactly zero."""

import math

import pytest

from controller.applied_output import AppliedOutput, OutputSource
from controller.fopdt_identifier import DELAYS, DutyHistory
from controller.smith_predictor import (
    HISTORY_MARGIN_S,
    MAX_RESIDUAL_F,
    MAX_RESIDUAL_STREAK,
    TEMP_MAX_F,
    SmithPredictor,
)

MODEL = {"K": 800.0, "tau": 600.0, "theta": 40.0, "revision": 1}


def _predictor():
    return SmithPredictor()


def test_returns_measured_temperature_until_trusted():
    p = _predictor()
    p.record_output(AppliedOutput(0.4, OutputSource.CONTROLLER, 0.0))
    assert p.temperature(212.0, 20.0) == 212.0
    assert p.active is False


def test_the_correction_is_exactly_zero_at_the_moment_of_trust():
    p = _predictor()
    p.record_output(AppliedOutput(0.4, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 20.0)
    p.trust(MODEL)
    assert p.temperature(212.0, 20.0) == 212.0
    assert p.active is True


def test_the_undelayed_branch_follows_the_exact_first_order_solution():
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    p.temperature(212.0, 600.0)
    # x0 starts at 0 and is driven by u=0.5 for 600 s with tau=600
    expected = MODEL["K"] * 0.5 * (1.0 - math.exp(-600.0 / MODEL["tau"]))
    assert p.status()["x0"] == pytest.approx(expected, rel=1e-9)


def test_the_delayed_branch_lags_by_exactly_theta():
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    p.temperature(212.0, 40.0)
    # at t = theta the delayed branch has seen no input at all
    assert p.status()["xd"] == pytest.approx(0.0, abs=1e-9)


def test_integration_splits_at_a_duty_change_between_samples():
    """A duty change landing between two controller updates is integrated in two
    segments, not rounded to the sample interval."""
    p = _predictor()
    p.trust({"K": 800.0, "tau": 600.0, "theta": 0.0, "revision": 1})
    p.record_output(AppliedOutput(0.0, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    p.record_output(AppliedOutput(1.0, OutputSource.CONTROLLER, 10.0))
    p.temperature(212.0, 30.0)
    # 10 s at u=0 leaves x at 0; then 20 s at u=1
    expected = 800.0 * (1.0 - math.exp(-20.0 / 600.0))
    assert p.status()["x0"] == pytest.approx(expected, rel=1e-9)


def test_the_smith_equation_is_measured_plus_the_state_difference():
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    out = p.temperature(215.0, 300.0)
    status = p.status()
    assert out == pytest.approx(215.0 + status["x0"] - status["xd"])


def test_a_constant_offset_cancels_out_of_the_correction():
    """The unknown T_offset is not part of the persisted model, and must not be
    needed: the correction is a difference of two states driven by the same K."""
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    a = p.temperature(212.0, 300.0)

    q = _predictor()
    q.trust(MODEL)
    q.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    q.temperature(500.0, 0.0)
    b = q.temperature(500.0, 300.0)
    assert (a - 212.0) == pytest.approx(b - 500.0)


def test_reset_reinitializes_both_branches_equally():
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.9, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    p.temperature(212.0, 900.0)
    # a real divergence, not the floating-point noise an incidental same-duty
    # segment split can produce between two branches that are otherwise
    # numerically identical
    assert p.status()["x0"] - p.status()["xd"] > 1.0
    p.reset()
    assert p.status()["x0"] == p.status()["xd"]
    assert p.temperature(212.0, 920.0) == 212.0


def test_units_are_canonical_fahrenheit():
    """A gain identified in F means the same thing whatever the display units."""
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    p.temperature(212.0, 600.0)
    assert p.status()["x0"] == pytest.approx(800.0 * 0.5 * (1.0 - math.exp(-1.0)), rel=1e-9)


def test_the_delayed_branch_equals_the_undelayed_branch_shifted_by_theta():
    """xd(t) is defined as x0(t - theta). Drive a varying duty profile through a
    predictor with theta=40 and compare its xd at t=100 against the x0 of a twin
    predictor with theta=0 driven by the same profile up to t=60 (=100 - theta).
    A constant duty cannot distinguish a correctly clamped window from a
    back-filled one, since both look identical when every duty is the same
    value; a varying profile forces the two branches to integrate different
    numbers of duty changes if either window is wrong."""
    theta = 40.0
    model_delayed = {"K": 800.0, "tau": 600.0, "theta": theta, "revision": 1}
    model_undelayed = {"K": 800.0, "tau": 600.0, "theta": 0.0, "revision": 1}
    duties = [(0.0, 0.2), (15.0, 0.6), (35.0, 0.3), (55.0, 0.9), (75.0, 0.1)]
    t_final = 100.0

    p = _predictor()
    p.trust(model_delayed)
    for t, duty in duties:
        p.record_output(AppliedOutput(duty, OutputSource.CONTROLLER, t))
    p.temperature(212.0, 0.0)
    p.temperature(212.0, t_final)

    q = _predictor()
    q.trust(model_undelayed)
    for t, duty in duties:
        q.record_output(AppliedOutput(duty, OutputSource.CONTROLLER, t))
    q.temperature(212.0, 0.0)
    q.temperature(212.0, t_final - theta)

    assert p.status()["xd"] == pytest.approx(q.status()["x0"], rel=1e-9, abs=1e-12)


def test_the_undelayed_branch_is_also_clamped_to_recorded_history():
    """At theta=0 the two branches integrate the same window, so the
    correction must be exactly zero even when that window starts before any
    duty was ever recorded: nothing recorded before the first duty change is
    duty that happened, for either branch. This is normal startup, not
    truncation: nothing has been pruned yet, so it must not be counted."""
    model = {"K": 800.0, "tau": 600.0, "theta": 0.0, "revision": 1}
    p = _predictor()
    p.trust(model)
    p.temperature(212.0, 0.0)  # seed on empty history
    p.record_output(AppliedOutput(1.0, OutputSource.CONTROLLER, 10.0))
    assert p.temperature(212.0, 30.0) == 212.0
    assert p.status()["truncated"] == 0


def test_an_out_of_order_record_does_not_falsely_report_truncation():
    """DutyHistory.record rejects any non-advancing timestamp, so an
    out-of-order record_output contributes nothing to the history -- it must
    not be allowed to drag `_earliest_seen` backward either, or a rejected
    record alone (with nothing pruned and the history unchanged) can make the
    predictor declare truncation and discard both branches."""
    model = {"K": 800.0, "tau": 600.0, "theta": 40.0, "revision": 1}
    p = _predictor()
    p.trust(model)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 1000.0))
    p.temperature(212.0, 1000.0)
    p.record_output(AppliedOutput(0.9, OutputSource.CONTROLLER, 500.0))  # rejected: out of order
    p.temperature(212.0, 1025.0)
    assert p.status()["truncated"] == 0


def test_a_control_cycle_beyond_retention_margin_suppresses_the_correction_and_is_surfaced():
    """Nothing bounds HoldCycleTime, so a constant retention margin can only
    make truncation unlikely, never impossible: a control cycle whose
    interval plus theta exceeds retention must not silently answer with a
    plausible-looking wrong xd. The predictor must instead suppress the
    correction for that tick, exactly as the safety path does, and surface
    it via status() -- without becoming a sticky disable, since the
    condition can clear on its own once the cycle shrinks back to something
    retention covers."""
    theta = float(DELAYS.max())
    # comfortably exceeds retention regardless of HISTORY_MARGIN_S's value;
    # `prune` keeps the segment straddling its horizon, so effective
    # retention exceeds the nominal DELAYS.max() + HISTORY_MARGIN_S by up to
    # one segment interval -- the extra 500 s covers that with room to spare
    cycle = float(DELAYS.max()) + HISTORY_MARGIN_S + 500.0
    model = {"K": 800.0, "tau": 600.0, "theta": theta, "revision": 1}
    duties = [0.2, 0.6, 0.3, 0.9, 0.1]

    p = _predictor()
    p.trust(model)
    p.record_output(AppliedOutput(duties[0], OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    out = None
    for i in range(1, 4):
        t = i * cycle
        p.record_output(AppliedOutput(duties[i % len(duties)], OutputSource.CONTROLLER, t))
        out = p.temperature(212.0, t)

    status = p.status()
    assert status["truncated"] > 0
    assert status["x0"] == 0.0
    assert status["xd"] == 0.0
    assert out == 212.0
    assert status["disabled"] is False  # not sticky
    assert p.active is True


def test_delayed_branch_matches_the_unpruned_profile_across_a_realistic_control_cycle():
    """History retention must outlive the deepest delayed window by more than
    a single control cycle: the last recorded command and the tick that
    integrates it are never at the same instant, so retention equal to theta
    alone prunes duty this tick's window still needs. Tick at HoldCycleTime's
    default of 25 s, at the deepest identification candidate theta, and check
    xd against the exact x0(t - theta) an unpruned history of the same
    profile would give."""
    theta = float(DELAYS.max())
    K, tau = 800.0, 600.0
    cycle = 25.0
    model = {"K": K, "tau": tau, "theta": theta, "revision": 1}
    duties = [0.2, 0.6, 0.3, 0.9, 0.1, 0.5, 0.7, 0.4]
    profile = [(i * cycle, duties[i % len(duties)]) for i in range(20)]
    t_last = profile[-1][0]

    reference_history = DutyHistory(1.0e9)
    for t, duty in profile:
        reference_history.record(t, duty)
    x0_reference = 0.0
    for duration, duty in reference_history.segments(0.0, t_last - theta):
        x0_reference = SmithPredictor._step(x0_reference, duty, duration, {"form": "fopdt", "K": K, "tau": tau})

    p = _predictor()
    p.trust(model)
    p.record_output(AppliedOutput(profile[0][1], OutputSource.CONTROLLER, profile[0][0]))
    p.temperature(212.0, profile[0][0])
    for t, duty in profile[1:]:
        p.record_output(AppliedOutput(duty, OutputSource.CONTROLLER, t))
        p.temperature(212.0, t)

    assert p.status()["xd"] == pytest.approx(x0_reference, rel=1e-9)


def test_delayed_branch_matches_the_unpruned_profile_at_a_large_but_realistic_cycle():
    """A 600 s cycle is a plausible HoldCycleTime, not an adversarial one; at
    theta = DELAYS.max() that needs 720 s of history behind the current tick,
    and any retention below that makes the predictor decline instead of
    correcting."""
    theta = float(DELAYS.max())
    K, tau = 800.0, 600.0
    cycle = 600.0
    model = {"K": K, "tau": tau, "theta": theta, "revision": 1}
    duties = [0.2, 0.6, 0.3, 0.9, 0.1, 0.5, 0.7, 0.4]
    profile = [(i * cycle, duties[i % len(duties)]) for i in range(10)]
    t_last = profile[-1][0]

    reference_history = DutyHistory(1.0e9)
    for t, duty in profile:
        reference_history.record(t, duty)
    x0_reference = 0.0
    for duration, duty in reference_history.segments(0.0, t_last - theta):
        x0_reference = SmithPredictor._step(x0_reference, duty, duration, {"form": "fopdt", "K": K, "tau": tau})

    p = _predictor()
    p.trust(model)
    p.record_output(AppliedOutput(profile[0][1], OutputSource.CONTROLLER, profile[0][0]))
    p.temperature(212.0, profile[0][0])
    for t, duty in profile[1:]:
        p.record_output(AppliedOutput(duty, OutputSource.CONTROLLER, t))
        p.temperature(212.0, t)

    assert p.status()["truncated"] == 0
    assert p.status()["xd"] == pytest.approx(x0_reference, rel=1e-9)


#: A model with no dead time and no recorded duty: x0 and xd both stay exactly
#: 0, so `predicted == measured` and the safety envelope can be exercised in
#: isolation from the FOPDT dynamics under test above.
UNCORRECTED_MODEL = {"K": 800.0, "tau": 600.0, "theta": 0.0, "revision": 1}


def test_a_residual_streak_disables_the_predictor():
    """Four consecutive large swings the model's own one-step forecast cannot
    explain trip the residual-streak guard; the call that trips it returns
    the raw measurement, and the streak is preserved so status() can show
    why the predictor disabled."""
    p = _predictor()
    p.trust(UNCORRECTED_MODEL)
    p.temperature(212.0, 0.0)
    swings = [362.0, 212.0, 362.0, 212.0]
    out = None
    for i, value in enumerate(swings):
        out = p.temperature(value, float(i + 1))
    assert p.status()["disabled"] is True
    assert p.status()["residual_streak"] == 4
    assert p.active is False
    assert out == swings[-1]


def test_a_broken_streak_does_not_accumulate_toward_the_disable():
    """A single acceptable residual between two runs of large swings must reset
    the streak count, not merely fail to advance it: three bad swings, one
    quiet sample, then two more bad swings must NOT disable, even though
    3 + 2 would exceed the real streak threshold if the count were not reset
    by the quiet sample in between."""
    p = _predictor()
    p.trust(UNCORRECTED_MODEL)
    p.temperature(212.0, 0.0)
    p.temperature(362.0, 1.0)
    p.temperature(212.0, 2.0)
    p.temperature(362.0, 3.0)
    p.temperature(362.0, 4.0)  # quiet: matches the model's own forecast exactly
    p.temperature(212.0, 5.0)
    p.temperature(362.0, 6.0)
    assert p.status()["disabled"] is False
    assert p.active is True


def test_a_prediction_outside_the_temperature_band_disables_the_predictor():
    p = _predictor()
    p.trust(UNCORRECTED_MODEL)
    p.temperature(212.0, 0.0)
    out = p.temperature(1300.0, 1.0)
    assert p.status()["disabled"] is True
    assert p.active is False
    assert out == 1300.0


def test_a_non_finite_measurement_disables_the_predictor():
    p = _predictor()
    p.trust(UNCORRECTED_MODEL)
    p.temperature(212.0, 0.0)
    out = p.temperature(math.inf, 1.0)
    assert p.status()["disabled"] is True
    assert p.active is False
    assert out == math.inf


def test_a_sticky_disable_survives_retrust_of_the_same_model_but_clears_on_a_new_one():
    """PID-SP re-asserts the trusted model every tick; trust() must not let
    that undo a safety disable. Only a genuinely different model (the
    identifier revising K, tau or theta) clears it, via reset()."""
    p = _predictor()
    p.trust(UNCORRECTED_MODEL)
    p.temperature(212.0, 0.0)
    p.temperature(1300.0, 1.0)
    assert p.status()["disabled"] is True

    p.trust(UNCORRECTED_MODEL)
    assert p.status()["disabled"] is True
    assert p.active is False

    revised_model = {"K": 900.0, "tau": 600.0, "theta": 0.0, "revision": 2}
    p.trust(revised_model)
    assert p.status()["disabled"] is False
    assert p.active is True
    # clearing the disable is not enough: the genuinely different model that
    # triggered the clear must actually be the one now in effect
    assert p.status()["model"]["K"] == 900.0


def _exact_xd(t, theta, K, tau):
    """The exact delayed-branch state for a full, constant duty applied from
    t=0: 0 until the dead time elapses, then the first-order step response."""
    return 0.0 if t < theta else K * (1.0 - math.exp(-(t - theta) / tau))


def test_a_large_correction_from_a_correct_model_does_not_disable():
    """The residual compares the measurement against the delayed branch's own
    one-step forecast of it, not against the Smith output: a perfect model
    produces zero residual even while the correction (x0 - xd) grows large,
    because the correction's size has nothing to do with plant/model
    agreement."""
    theta = float(DELAYS.max())
    K, tau = 800.0, 600.0
    offset = 70.0
    model = {"K": K, "tau": tau, "theta": theta, "revision": 1}

    p = _predictor()
    p.trust(model)
    p.record_output(AppliedOutput(1.0, OutputSource.CONTROLLER, 0.0))
    p.temperature(offset + _exact_xd(0.0, theta, K, tau), 0.0)
    for t in range(10, 241, 10):
        p.temperature(offset + _exact_xd(float(t), theta, K, tau), float(t))

    status = p.status()
    assert status["disabled"] is False
    assert p.active is True
    # the correction is genuinely large, not merely never exercised
    assert status["x0"] - status["xd"] > 50.0


def _longest_run(flags):
    """Length of the longest run of consecutive True values."""
    best = current = 0
    for flag in flags:
        current = current + 1 if flag else 0
        best = max(best, current)
    return best


def test_a_legitimate_fast_temperature_change_does_not_trip_the_envelope():
    """The residual must track the delayed branch's own forecast INCREMENT,
    not the raw per-tick measured change: a plant that is genuinely moving
    faster than MAX_RESIDUAL_F per tick, exactly as a correct model predicts,
    must not be mistaken for a diverging one — dropping the (xd - prev_xd)
    term collapses the residual to abs(measured - last_measured), which is
    indistinguishable from a legitimate fast warm-up.

    theta=0 so the delayed branch tracks the measured signal one-for-one; the
    per-tick change during an early K=1600 warm-up exceeds MAX_RESIDUAL_F for
    more than MAX_RESIDUAL_STREAK consecutive ticks while a correct model
    predicts every one of them exactly.

    Two things are asserted about the SCENARIO ITSELF, not just the outcome,
    because a scenario can be retuned into vacuity without any code changing:
    the deltas must genuinely stress the residual streak (enough consecutive
    ones above MAX_RESIDUAL_F to trip it if nothing were tracking them
    correctly), and the run must stay inside the temperature band (so this
    cannot silently become a band test instead of a residual test)."""
    K, tau, theta = 1600.0, 600.0, 0.0
    offset = 70.0
    model = {"K": K, "tau": tau, "theta": theta, "revision": 1}

    p = _predictor()
    p.trust(model)
    p.record_output(AppliedOutput(1.0, OutputSource.CONTROLLER, 0.0))
    measurements = [offset + _exact_xd(0.0, theta, K, tau)]
    p.temperature(measurements[0], 0.0)
    for i in range(1, 11):
        t = i * 60.0
        measured = offset + _exact_xd(t, theta, K, tau)
        measurements.append(measured)
        p.temperature(measured, t)

    deltas = [b - a for a, b in zip(measurements, measurements[1:])]
    assert _longest_run([d > MAX_RESIDUAL_F for d in deltas]) >= MAX_RESIDUAL_STREAK
    assert max(measurements) < TEMP_MAX_F  # a residual test, not a band test

    status = p.status()
    assert status["residual_streak"] == 0
    assert status["disabled"] is False
    assert p.active is True


def test_a_genuine_plant_model_divergence_disables_the_predictor():
    """A measurement the model's own one-step forecast cannot explain, held
    for enough consecutive samples, is exactly what the residual streak
    exists to catch — as opposed to the correction's own magnitude, which the
    previous test shows it must not react to."""
    theta = float(DELAYS.max())
    K, tau = 800.0, 600.0
    offset = 70.0
    model = {"K": K, "tau": tau, "theta": theta, "revision": 1}

    p = _predictor()
    p.trust(model)
    p.record_output(AppliedOutput(1.0, OutputSource.CONTROLLER, 0.0))
    p.temperature(offset + _exact_xd(0.0, theta, K, tau), 0.0)
    for t in range(10, 121, 10):
        p.temperature(offset + _exact_xd(float(t), theta, K, tau), float(t))
    assert p.status()["disabled"] is False  # still faithfully tracking a correct model

    for i, t in enumerate(range(130, 170, 10)):
        swing = 300.0 if i % 2 == 0 else -300.0
        p.temperature(offset + _exact_xd(float(t), theta, K, tau) + swing, float(t))
    assert p.status()["disabled"] is True
    assert p.active is False


def test_first_trust_reinitializes_the_predictor_even_with_prior_untrusted_history():
    """trust()'s first adoption must reset _last_t along with the states: an
    untrusted temperature() call still advances _last_t, and without the
    reset the first trusted call would integrate that stale pre-trust window
    instead of seeding, stepping the corrected output away from the raw
    measurement at the moment trust begins."""
    p = _predictor()
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 20.0)  # untrusted, but _last_t is now set
    p.trust(MODEL)
    assert p.temperature(212.0, 30.0) == 212.0


def test_reset_clears_last_t_so_the_very_next_call_seeds():
    """reset() must clear _last_t, not only the states: otherwise the next
    call integrates from the stale pre-reset timestamp instead of seeding,
    which a constant duty profile cannot reveal since identical duty makes
    any window length integrate to the same state on both branches."""
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.9, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    p.temperature(212.0, 900.0)
    p.reset()
    p.record_output(AppliedOutput(0.2, OutputSource.CONTROLLER, 905.0))
    assert p.temperature(212.0, 920.0) == 212.0


def test_a_theta_revision_reinitializes_the_predictor():
    """The delayed state was accumulated under the old delay and no longer
    means what it did once theta changes, so a theta revision must reset --
    and the NEW theta must actually govern afterward, not merely be
    bookkept: a seed-then-return-212.0 check alone cannot tell a real theta
    switch from a reset that silently kept the old one, since a fresh seed
    returns the raw measurement regardless of which theta is in effect.

    Recorded duty is never erased by a revision (only the accumulators are),
    so the delayed branch's very first post-revision window still reaches
    back into pre-revision history by construction -- there is no timestamp
    at which it is cleanly zero. Compute the expected xd directly from the
    real duty history for both the new and the old theta and confirm the
    predictor's actual xd matches the new one, not the old."""
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    p.temperature(212.0, 300.0)
    assert p.status()["x0"] - p.status()["xd"] > 1.0  # a real, established divergence

    new_theta = 90.0
    revised = dict(MODEL)
    revised["theta"] = new_theta
    p.trust(revised)
    assert p.status()["x0"] == p.status()["xd"] == 0.0
    assert p.status()["model"]["theta"] == new_theta

    seed_t = 320.0
    assert p.temperature(212.0, seed_t) == 212.0  # seed after reset
    p.record_output(AppliedOutput(0.7, OutputSource.CONTROLLER, seed_t))
    t_final = seed_t + 100.0
    p.temperature(212.0, t_final)

    reference_history = DutyHistory(1.0e9)
    reference_history.record(0.0, 0.5)
    reference_history.record(seed_t, 0.7)

    def expected_xd(theta):
        x = 0.0
        for duration, duty in reference_history.segments(max(seed_t - theta, 0.0), max(t_final - theta, 0.0)):
            x = SmithPredictor._step(x, duty, duration, {"form": "fopdt", "K": MODEL["K"], "tau": MODEL["tau"]})
        return x

    old_theta_value = expected_xd(MODEL["theta"])
    new_theta_value = expected_xd(new_theta)
    assert abs(old_theta_value - new_theta_value) > 1.0  # the two hypotheses are genuinely distinguishable
    assert p.status()["xd"] == pytest.approx(new_theta_value, rel=1e-9)


def test_a_same_theta_revision_on_an_enabled_predictor_keeps_the_states():
    """A later revision of K or tau, with theta unchanged, updates the model
    in place and keeps the accumulated states: the identifier only revises
    after a confirmation window, and snapping a live correction back to zero
    would be the very control step equal-state initialization exists to
    avoid. Exercised on an ENABLED predictor, since a disabled one takes a
    different branch in trust()."""
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    p.temperature(212.0, 300.0)
    before = p.status()

    revised = dict(MODEL)
    revised["K"] = 900.0
    p.trust(revised)
    after = p.status()
    assert after["x0"] == before["x0"]
    assert after["xd"] == before["xd"]
    assert p.active is True
    # the whole point of a revision: the identifier's refined gain must
    # actually be adopted, not merely tolerated without resetting the states
    assert after["model"]["K"] == 900.0
    assert after["model"]["tau"] == MODEL["tau"]


def test_reset_clears_the_residual_streak():
    """A normal reset() (as opposed to the disable path, which deliberately
    preserves the streak for diagnosis) must clear it: otherwise a predictor
    re-trusted with a revised model would disable on the very first
    violation instead of the fourth."""
    p = _predictor()
    p.trust(UNCORRECTED_MODEL)
    p.temperature(212.0, 0.0)
    p.temperature(362.0, 1.0)  # one violation, streak=1, well short of disabling
    assert p.status()["residual_streak"] == 1
    p.reset()
    assert p.status()["residual_streak"] == 0


def test_a_residual_just_under_the_threshold_does_not_count_as_a_violation():
    p = _predictor()
    p.trust(UNCORRECTED_MODEL)
    p.temperature(212.0, 0.0)
    p.temperature(311.5, 1.0)  # |311.5 - 212.0| = 99.5, just under the real threshold
    assert p.status()["residual_streak"] == 0
    assert p.active is True


def test_a_residual_just_over_the_threshold_counts_as_a_violation():
    p = _predictor()
    p.trust(UNCORRECTED_MODEL)
    p.temperature(212.0, 0.0)
    p.temperature(312.5, 1.0)  # |312.5 - 212.0| = 100.5, just over the real threshold
    assert p.status()["residual_streak"] == 1
    assert p.active is True  # a single violation alone does not yet disable


def test_a_prediction_just_inside_the_low_temperature_band_is_accepted():
    p = _predictor()
    p.trust(UNCORRECTED_MODEL)
    p.temperature(212.0, 0.0)
    out = p.temperature(-99.5, 1.0)  # just inside TEMP_MIN_F
    assert out == -99.5
    assert p.active is True


def test_a_prediction_just_outside_the_low_temperature_band_disables():
    p = _predictor()
    p.trust(UNCORRECTED_MODEL)
    p.temperature(212.0, 0.0)
    out = p.temperature(-100.5, 1.0)  # just outside TEMP_MIN_F
    assert p.status()["disabled"] is True
    assert out == -100.5


def test_a_non_finite_model_state_disables_prediction():
    """A corrupted accumulated state disables prediction, distinct from a
    corrupted incoming MEASUREMENT. NaN propagates through the correction into
    the prediction, so the band check is what rejects it; the isfinite clauses on
    x0/xd only decide when the prediction is computed some other way."""
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    p._x0 = float("nan")
    assert p.temperature(212.0, 20.0) == 212.0
    assert p.active is False
    assert p.status()["disabled"] is True


def test_the_last_valid_parameters_stay_observable_after_a_disable():
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    p._x0 = float("inf")
    p.temperature(212.0, 20.0)
    assert p.status()["model"]["K"] == MODEL["K"]


def test_re_trusting_the_same_model_does_not_restart_the_states():
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    p.temperature(212.0, 600.0)
    x0 = p.status()["x0"]
    p.trust(dict(MODEL))
    assert p.status()["x0"] == x0
