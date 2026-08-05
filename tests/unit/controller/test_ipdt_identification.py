"""The integrating model form, for a chamber too slow to fit as first order.

A first-order fit reports its time constant as the reciprocal of a coefficient
whose true value approaches zero as the chamber slows relative to the
observation window. Noise across that zero inverts the sign and drags the gain
with it, so the fit returns a negative gain and a negative time constant
together -- confidently impossible, and rejected, leaving the controller with
nothing. Measured on a plant fitted to a real MAK, that happened at 25 of 25
delay candidates.
"""

import numpy as np
import pytest

from controller.fopdt_identifier import (
    FORM_FOPDT,
    FORM_IPDT,
    GAIN_RATE_MAX,
    GAIN_RATE_MIN,
    RSE_GAIN_RATE_MAX,
    RLSBank,
    integrating_gate_mask,
    recover_integrating_parameters,
)
from controller.smith_predictor import SmithPredictor


def test_the_fitted_coefficients_are_the_parameters_themselves():
    """No unscaling: the regressand is a rate and the duty column is unscaled,
    so the coefficient IS F per second per unit duty and the intercept IS the
    loss rate. That directness is why this form is better conditioned."""
    params = recover_integrating_parameters(np.array([[-0.07, 0.51]]))
    assert params["c0"][0] == pytest.approx(-0.07)
    assert params["K_i"][0] == pytest.approx(0.51)


def test_a_plausible_grill_passes_the_gate():
    # The MAK plant's own identified values: 1842 F/hr per duty, losing 257 F/hr.
    params = {"K_i": np.array([0.512]), "c0": np.array([-0.0714])}
    assert integrating_gate_mask(params, np.array([0.05]))[0]


@pytest.mark.parametrize(
    ("K_i", "c0", "rse", "why"),
    [
        (-0.5, -0.07, 0.05, "a negative gain is the sign error this form exists to avoid"),
        (GAIN_RATE_MIN / 2, -0.07, 0.05, "too slow to be a grill"),
        (GAIN_RATE_MAX * 2, -0.07, 0.05, "too fast to be a grill"),
        (0.5, 0.07, 0.05, "a chamber loses heat to ambient; it cannot gain it"),
        (0.5, -0.07, RSE_GAIN_RATE_MAX * 2, "too uncertain to act on"),
        (np.nan, -0.07, 0.05, "non-finite"),
    ],
)
def test_the_gate_rejects_what_is_not_a_grill(K_i, c0, rse, why):
    params = {"K_i": np.array([K_i]), "c0": np.array([c0])}
    assert not integrating_gate_mask(params, np.array([rse]))[0], why


def test_the_bank_carries_two_regressors_for_this_form():
    bank = RLSBank(4, n_params=2)
    assert bank.Theta.shape == (4, 2)
    assert bank.P.shape == (4, 2, 2)


def test_the_two_regressor_bank_recovers_a_known_rate():
    """Drive a synthetic chamber whose rate is exactly K_i*u + c0 and check the
    bank converges on the parameters that generated it."""
    K_i, c0 = 0.5, -0.08
    bank = RLSBank(1, n_params=2)
    rng = np.random.default_rng(0)
    for _ in range(400):
        u = float(rng.uniform(0.0, 0.6))
        bank.update(np.array([[1.0, u]]), K_i * u + c0)
    params = recover_integrating_parameters(bank.Theta)
    assert params["K_i"][0] == pytest.approx(K_i, abs=1e-3)
    assert params["c0"][0] == pytest.approx(c0, abs=1e-3)


def test_the_predictor_integrates_the_rate_for_this_form():
    """An integrating chamber has no state to decay toward, so a constant input
    over dt moves the state by exactly the rate times the interval."""
    model = {"form": FORM_IPDT, "K_i": 0.5, "c0": -0.08, "theta": 100.0}
    assert SmithPredictor._step(10.0, 0.4, 20.0, model) == pytest.approx(10.0 + (0.5 * 0.4 - 0.08) * 20.0)


def test_the_first_order_step_is_unchanged():
    """The other form must keep decaying toward its steady value."""
    model = {"form": FORM_FOPDT, "K": 400.0, "tau": 600.0, "theta": 100.0}
    decay = np.exp(-20.0 / 600.0)
    assert SmithPredictor._step(10.0, 0.4, 20.0, model) == pytest.approx(10.0 * decay + 400.0 * 0.4 * (1 - decay))


def test_an_integrating_model_reaches_the_predictor_intact():
    p = SmithPredictor()
    p.trust({"form": FORM_IPDT, "K_i": 0.5, "c0": -0.08, "theta": 100.0, "revision": 1})
    assert p._model == {"form": FORM_IPDT, "K_i": 0.5, "c0": -0.08, "theta": 100.0}


class TestHoldDuty:
    """The duty that holds the operating point, which needs no dead time.

    Promotion is about telling one dead time from another and takes an hour of
    cook to earn. The gain and the loss rate come from the duty/rate relation
    alone, so the duty they imply is available long before that.
    """

    def _identifier(self, *, rate, duty, n=200, dt=20.0):
        from controller.applied_output import AppliedOutput, OutputSource
        from controller.fopdt_identifier import FOPDTIdentifier

        ident = FOPDTIdentifier()
        temp, t = 200.0, 0.0
        rng = np.random.default_rng(0)
        for i in range(n):
            # Vary the duty so the excitation gate can clear, and move the
            # chamber at the rate that duty implies.
            u = float(duty(i, rng))
            ident.record_output(AppliedOutput(u, OutputSource.CONTROLLER, t))
            t += dt
            temp += rate(u) * dt
            ident.observe(temp, t)
        return ident

    def test_nothing_is_reported_before_enough_observations(self):
        from controller.fopdt_identifier import FOPDTIdentifier

        ident = self._identifier(
            rate=lambda u: 0.5 * u - 0.05,
            duty=lambda i, rng: 0.1 + 0.1 * (i % 2),
            n=FOPDTIdentifier.MIN_HOLD_DUTY_SAMPLES // 4,
        )
        assert ident.hold_duty() is None

    def test_it_recovers_the_duty_that_holds_a_synthetic_chamber(self):
        # dT/dt = 0.5*u - 0.05 is still at u = 0.1.
        ident = self._identifier(
            rate=lambda u: 0.5 * u - 0.05,
            duty=lambda i, rng: 0.05 if (i // 6) % 2 else 0.35,
        )
        held = ident.hold_duty()
        assert held is not None
        assert held == pytest.approx(0.1, abs=0.02)

    def test_a_duty_beyond_the_actuator_is_not_reported(self):
        # dT/dt = 0.5*u - 0.4 would need u = 0.8 to hold, which is past the cap.
        ident = self._identifier(
            rate=lambda u: 0.5 * u - 0.4,
            duty=lambda i, rng: 0.05 if (i // 6) % 2 else 0.35,
        )
        assert ident.hold_duty(u_max=0.5) is None
