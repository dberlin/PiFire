"""Contracts for the normalized PID-SP live-learning projection."""

from copy import deepcopy

import pytest

from controller.fopdt_identifier import (
    CONFIRM_WINDOW,
    MIN_ACCEPTED,
    MIN_ACCEPTED_SECONDS,
    MIN_DUTY_STD,
    MIN_TEMP_SPAN_F,
)
from controller.pid_sp_learning import build_pid_sp_live_learning


def _identifier_status(**overrides):
    status = {
        "accepted": MIN_ACCEPTED,
        "accepted_seconds": MIN_ACCEPTED_SECONDS,
        "duty_std": MIN_DUTY_STD,
        "temp_span": MIN_TEMP_SPAN_F,
        "transition_seen": True,
        "duty_segments": 3,
        "best_residual": 0.5,
        "runner_up_residual": 1.0,
        "candidates_passing": 1,
        "confirming": CONFIRM_WINDOW,
        "trusted": None,
        "distrust_count": 0,
        "distrust_ratio": 0.0,
    }
    status.update(overrides)
    return status


def _predictor_status(**overrides):
    status = {
        "active": False,
        "disabled": False,
        "x0": 225.0,
        "xd": 224.0,
        "residual_streak": 0,
        "truncated": 0,
        "model": None,
    }
    status.update(overrides)
    return status


def test_projection_publishes_the_five_backend_owned_excitation_gates():
    projection = build_pid_sp_live_learning(
        _identifier_status(
            accepted=MIN_ACCEPTED + 2,
            accepted_seconds=MIN_ACCEPTED_SECONDS + 20.0,
            duty_std=MIN_DUTY_STD + 0.01,
            temp_span=MIN_TEMP_SPAN_F + 1.0,
        ),
        _predictor_status(),
    )

    assert projection["gates"] == [
        {
            "name": "accepted_samples",
            "passed": True,
            "observed": MIN_ACCEPTED + 2,
            "required": MIN_ACCEPTED,
            "unit": "samples",
        },
        {
            "name": "accepted_duration",
            "passed": True,
            "observed": MIN_ACCEPTED_SECONDS + 20.0,
            "required": MIN_ACCEPTED_SECONDS,
            "unit": "seconds",
        },
        {
            "name": "duty_standard_deviation",
            "passed": True,
            "observed": MIN_DUTY_STD + 0.01,
            "required": MIN_DUTY_STD,
            "unit": "ratio",
        },
        {
            "name": "duty_transition",
            "passed": True,
            "observed": True,
            "required": True,
            "unit": None,
        },
        {
            "name": "temperature_span",
            "passed": True,
            "observed": MIN_TEMP_SPAN_F + 1.0,
            "required": MIN_TEMP_SPAN_F,
            "unit": "°F",
        },
    ]


def test_projection_publishes_backend_owned_confirmation_progress():
    projection = build_pid_sp_live_learning(
        _identifier_status(confirming=CONFIRM_WINDOW - 3),
        _predictor_status(),
    )

    assert projection["confirmation"] == {
        "observed": CONFIRM_WINDOW - 3,
        "required": CONFIRM_WINDOW,
    }


@pytest.mark.parametrize(
    ("identifier", "predictor", "expected"),
    [
        pytest.param(
            _identifier_status(accepted=MIN_ACCEPTED - 1),
            _predictor_status(),
            "collecting",
            id="collecting-before-the-minimum-history",
        ),
        pytest.param(
            _identifier_status(duty_std=MIN_DUTY_STD - 0.001),
            _predictor_status(),
            "insufficient-excitation",
            id="history-ready-but-excitation-missing",
        ),
        pytest.param(
            _identifier_status(),
            _predictor_status(),
            "evaluating",
            id="all-excitation-gates-pass",
        ),
        pytest.param(
            _identifier_status(trusted={"form": "fopdt", "K": 800.0, "tau": 600.0, "theta": 40.0}),
            _predictor_status(active=True),
            "active",
            id="trusted-model-and-active-predictor",
        ),
        pytest.param(
            _identifier_status(),
            _predictor_status(disabled=True),
            "fallback",
            id="disabled-predictor",
        ),
    ],
)
def test_projection_state_precedence(identifier, predictor, expected):
    assert build_pid_sp_live_learning(identifier, predictor)["status"] == expected


def test_fallback_wins_over_an_active_looking_trusted_model():
    identifier = _identifier_status(trusted={"form": "fopdt", "K": 800.0, "tau": 600.0, "theta": 40.0})
    predictor = _predictor_status(active=True, disabled=True)

    assert build_pid_sp_live_learning(identifier, predictor)["status"] == "fallback"


@pytest.mark.parametrize(
    ("trusted", "active"),
    [
        pytest.param(None, True, id="active-predictor-without-trusted-model"),
        pytest.param(
            {"form": "fopdt", "K": 800.0, "tau": 600.0, "theta": 40.0},
            False,
            id="trusted-model-without-active-predictor",
        ),
    ],
)
def test_active_requires_both_a_trusted_model_and_an_active_predictor(trusted, active):
    projection = build_pid_sp_live_learning(
        _identifier_status(trusted=trusted),
        _predictor_status(active=active),
    )

    assert projection["status"] == "evaluating"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numeric_gate_observations_are_rejected(value):
    with pytest.raises(ValueError, match="accepted_seconds"):
        build_pid_sp_live_learning(
            _identifier_status(accepted_seconds=value),
            _predictor_status(),
        )


@pytest.mark.parametrize("field", ["accepted", "accepted_seconds", "duty_std", "temp_span"])
def test_booleans_are_not_accepted_as_numeric_gate_observations(field):
    with pytest.raises(ValueError, match=field):
        build_pid_sp_live_learning(
            _identifier_status(**{field: True}),
            _predictor_status(),
        )


def test_arbitrarily_large_integer_observations_remain_valid_numbers():
    accepted = 10**1000

    projection = build_pid_sp_live_learning(
        _identifier_status(accepted=accepted),
        _predictor_status(),
    )

    assert projection["gates"][0]["observed"] == accepted


def test_projection_defensively_copies_nested_identifier_and_predictor_mappings():
    identifier = _identifier_status(trusted={"form": "fopdt", "K": 800.0, "tau": 600.0, "theta": 40.0})
    predictor = _predictor_status(model={"form": "fopdt", "K": 800.0, "tau": 600.0, "theta": 40.0})
    original_identifier = deepcopy(identifier)
    original_predictor = deepcopy(predictor)

    projection = build_pid_sp_live_learning(identifier, predictor)
    projection["identifier"]["trusted"]["K"] = 999.0
    projection["predictor"]["model"]["theta"] = 999.0

    assert identifier == original_identifier
    assert predictor == original_predictor
