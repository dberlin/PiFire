"""What the MPC persists between cooks, and what it refuses to adopt."""

import json
from copy import deepcopy

import pytest

from controller.linear_mpc.adaptation import OnlineAdaptation
from controller.linear_mpc.arx import ScheduledARX
from controller.linear_mpc.contracts import FrameObservation
from controller.mpc import _DEFAULTS, Controller, _online_evaluation

#: The schema this controller writes and is the only one it will read back.
#: A literal, not `Controller._MODEL_SCHEMA`: importing it would move every
#: expectation below along with any change to it, so a bump could never be
#: caught here. Raising it is a deliberate edit, and the tests that pin what
#: happens to an OLDER record keep their own literals.
CURRENT_SCHEMA = 3

CYCLE = {"u_min": 0.1, "u_max": 0.9}
PARAMS = dict(
    C_c=2520.0,
    h_amb=0.224,
    T_amb=20.0,
    theta=93.0,
    n_delay=4,
    K_Q=695.0,
    sigma=1.4e-9,
)


def _c(**over):
    # Built at PARAMS' own n_delay, because a restore refuses a snapshot fitted
    # against a different lag chain outright
    # (pinned by test_a_snapshot_fitted_at_a_different_chain_length_is_refused
    # below). Every other test here is about some OTHER field of the snapshot,
    # so they must not trip that guard on the way to the thing they check.
    return Controller(dict(_DEFAULTS, policy="nlp", n_delay=PARAMS["n_delay"], **over), "C", dict(CYCLE))


def _state_space_frame(index: int) -> FrameObservation:
    loads = (0.1, 0.35, 0.7, 0.2, 0.85, 0.5, 0.25, 0.65)
    q = loads[index % len(loads)]
    return FrameObservation(
        index * 20.0,
        (index + 1) * 20.0,
        80.0 + 12.0 * loads[(index - 1) % len(loads)] + 4.0 * loads[(index - 2) % len(loads)],
        110.0,
        20.0,
        q,
        q,
        q,
        5.0,
        1.0,
        1.0,
        index,
        "controller",
        False,
        False,
        False,
        False,
        False,
        False,
        True,
        0,
    )


def test_an_unidentified_controller_snapshots_nothing():
    """Nothing learned, nothing to say. This keeps the store empty for the
    overwhelming majority of installs."""
    assert _c().get_model_snapshot() is None


def test_a_snapshot_is_json_safe_and_carries_its_provenance():
    c = _c()
    c._adopt_model(PARAMS, rmse=2.1, samples=1730, band_c=(40.0, 232.0))
    snap = c.get_model_snapshot()
    json.dumps(snap, allow_nan=False)
    assert snap["params"]["C_c"] == pytest.approx(2520.0)
    assert snap["rmse"] == pytest.approx(2.1)
    assert snap["samples"] == 1730
    assert tuple(snap["band_c"]) == (40.0, 232.0)
    assert isinstance(snap["revision"], int)


def test_the_revision_advances_on_each_adoption():
    c = _c()
    c._adopt_model(PARAMS, rmse=2.1, samples=10, band_c=(40.0, 232.0))
    first = c.get_model_snapshot()["revision"]
    c._adopt_model(dict(PARAMS, C_c=2600.0), rmse=2.0, samples=20, band_c=(40.0, 232.0))
    assert c.get_model_snapshot()["revision"] > first


def test_a_restored_revision_is_carried_forward_not_restarted():
    """controller_model_state.py rejects a non-advancing revision FOREVER once
    its counter falls behind, so a per-process counter would silently stop
    persisting after the first restart."""
    c = _c()
    assert (
        c.restore_model(
            {
                "version": CURRENT_SCHEMA,
                "revision": 41,
                "params": dict(PARAMS),
                "rmse": 2.0,
                "samples": 100,
                "band_c": [40.0, 232.0],
            }
        )
        is True
    )
    c._adopt_model(dict(PARAMS, C_c=2600.0), rmse=1.9, samples=200, band_c=(40.0, 232.0))
    assert c.get_model_snapshot()["revision"] == 42


def test_restore_applies_the_parameters_to_the_running_model():
    c = _c()
    c.restore_model(
        {
            "version": CURRENT_SCHEMA,
            "revision": 1,
            "params": dict(PARAMS),
            "rmse": 2.0,
            "samples": 100,
            "band_c": [40.0, 232.0],
        }
    )
    assert c.cfg["C_c"] == pytest.approx(2520.0)


def test_an_unphysical_snapshot_is_refused():
    c = _c()
    bad = dict(PARAMS, C_c=-1.0)
    assert (
        c.restore_model(
            {
                "version": CURRENT_SCHEMA,
                "revision": 1,
                "params": bad,
                "rmse": 2.0,
                "samples": 100,
                "band_c": [40.0, 232.0],
            }
        )
        is False
    )
    assert c.cfg["C_c"] == pytest.approx(_DEFAULTS["C_c"])


def test_a_snapshot_from_a_future_schema_is_refused():
    c = _c()
    assert (
        c.restore_model(
            {"version": 99, "revision": 1, "params": dict(PARAMS), "rmse": 2.0, "samples": 100, "band_c": [40.0, 232.0]}
        )
        is False
    )


def test_a_malformed_snapshot_is_refused_rather_than_raising():
    c = _c()
    for junk in (None, {}, {"version": CURRENT_SCHEMA}, {"version": CURRENT_SCHEMA, "revision": "x", "params": {}}):
        assert c.restore_model(junk) is False


def test_a_two_lump_snapshot_is_refused_and_says_why(capsys):
    """The record a grill that has been learning all season arrives with.

    Version 2 described the prior firing-rate-scale model. Its C_c, h_amb and
    K_Q values must not be read as normalized-load parameters, so the subset
    whose keys still match must not be applied. A full old record includes
    retired C_f/h_fc fields to prove extras cannot disguise the stale schema.

    It is also said out loud. The operator's model goes back to the shipped
    defaults exactly once, at the upgrade, and finding that out from the
    overshoot instead of the log is the outcome the message exists to prevent.
    """
    c = _c()
    v1 = {
        "version": 2,
        "revision": 12,
        "params": dict(PARAMS, C_f=9.0, h_fc=0.39),
        "rmse": 2.0,
        "samples": 1730,
        "band_c": [40.0, 232.0],
    }
    assert c.restore_model(v1) is False
    # Nothing crossed into the running model, and the revision did not advance.
    assert c.cfg["C_c"] == pytest.approx(_DEFAULTS["C_c"])
    assert c.get_model_snapshot() is None
    # Both versions named, so the reader can tell which end is out of date.
    out = capsys.readouterr().out
    assert "version 2" in out
    assert f"version {CURRENT_SCHEMA}" in out
    assert "snapshot" in out.lower()


def test_a_non_integer_revision_is_refused_even_with_otherwise_valid_params():
    """Pairs a fully valid `params` with a bad `revision` so this exercises the
    revision type-check itself, not the params/bounds validation that an empty
    `params` dict would trip on its own."""
    c = _c()
    for bad_revision in ("x", 1.5, None):
        assert (
            c.restore_model(
                {
                    "version": CURRENT_SCHEMA,
                    "revision": bad_revision,
                    "params": dict(PARAMS),
                    "rmse": 2.0,
                    "samples": 100,
                    "band_c": [40.0, 232.0],
                }
            )
            is False
        )


def test_a_non_dict_or_missing_params_is_refused_even_with_a_valid_revision():
    """Mirrors test_a_non_integer_revision_is_refused_even_with_otherwise_valid_params
    for the OTHER half of the composite guard `not isinstance(params, dict) or
    not isinstance(revision, int)`. Pairing a bad `params` with a valid
    `revision` isolates `isinstance(params, dict)` on its own: without it, a
    non-dict `params` would reach the PROMOTION_BOUNDS loop's `params.get(key)`
    and raise AttributeError instead of returning False."""
    c = _c()
    for bad_params in (None, "not-a-dict", [1, 2, 3], 4):
        assert (
            c.restore_model(
                {
                    "version": CURRENT_SCHEMA,
                    "revision": 1,
                    "params": bad_params,
                    "rmse": 2.0,
                    "samples": 100,
                    "band_c": [40.0, 232.0],
                }
            )
            is False
        )
    # params omitted from the snapshot entirely behaves the same as params=None.
    assert (
        c.restore_model(
            {"version": CURRENT_SCHEMA, "revision": 1, "rmse": 2.0, "samples": 100, "band_c": [40.0, 232.0]}
        )
        is False
    )


def test_a_non_numeric_parameter_value_is_refused_without_raising():
    """Pairs an otherwise fully valid snapshot with a single parameter value
    that cannot be converted to float, isolating the try/float()/except guard
    inside the PROMOTION_BOUNDS loop -- not merely reached as a side effect of
    an out-of-range value or a non-dict params (both tested elsewhere)."""
    c = _c()
    bad = dict(PARAMS, h_amb="not-a-number")
    assert (
        c.restore_model(
            {
                "version": CURRENT_SCHEMA,
                "revision": 1,
                "params": bad,
                "rmse": 2.0,
                "samples": 100,
                "band_c": [40.0, 232.0],
            }
        )
        is False
    )


def test_a_params_dict_missing_a_required_key_is_refused():
    """A params dict that IS a genuine dict but omits one of the
    PROMOTION_BOUNDS keys reaches `params.get(key)` -> None inside the loop,
    which float() cannot convert. Same guard as the non-numeric-value test
    above, triggered by a missing key rather than an unconvertible value."""
    c = _c()
    bad = dict(PARAMS)
    del bad["theta"]
    assert (
        c.restore_model(
            {
                "version": CURRENT_SCHEMA,
                "revision": 1,
                "params": bad,
                "rmse": 2.0,
                "samples": 100,
                "band_c": [40.0, 232.0],
            }
        )
        is False
    )


def test_a_non_integer_n_delay_is_refused():
    """PROMOTION_BOUNDS alone would admit n_delay=4.5 -- it lies inside (0, 50)
    -- but a fractional lag-state count cannot size the estimator's state
    vector. restore_model must apply the same integrality rule
    model_promotion.evaluate() does, not merely the numeric range."""
    c = _c()
    bad = dict(PARAMS, n_delay=4.5)
    assert (
        c.restore_model(
            {
                "version": CURRENT_SCHEMA,
                "revision": 1,
                "params": bad,
                "rmse": 2.0,
                "samples": 100,
                "band_c": [40.0, 232.0],
            }
        )
        is False
    )
    assert c.cfg["n_delay"] == PARAMS["n_delay"]


def test_a_float_valued_whole_number_n_delay_is_accepted():
    """A snapshot round-tripped through JSON stores n_delay as a plain number,
    e.g. 4.0 rather than 4. That is a whole number and must be accepted, not
    rejected merely for arriving as a float."""
    c = _c()
    whole = dict(PARAMS, n_delay=4.0)
    assert (
        c.restore_model(
            {
                "version": CURRENT_SCHEMA,
                "revision": 1,
                "params": whole,
                "rmse": 2.0,
                "samples": 100,
                "band_c": [40.0, 232.0],
            }
        )
        is True
    )
    assert c.cfg["n_delay"] == pytest.approx(4.0)


def test_a_snapshot_fitted_at_a_different_chain_length_is_refused_and_says_why(capsys):
    """An install that learned at one n_delay and was then reconfigured to another.

    n_delay is the one parameter in a snapshot that no refit ever learned: the
    fitter is handed the configured chain length and fits the rest against it.
    A snapshot that disagrees with the configured length therefore describes an
    install that has since been reconfigured, and the whole record goes --
    adopting the count would override the operator's deliberate setting, and
    adopting the parameters without it would put a fit made against one lag
    chain onto a different one.

    The value used here is inside PROMOTION_BOUNDS and a whole number, so it
    passes every other check in the method: what refuses it is the comparison
    against the built chain and nothing else.
    """
    c = _c()
    elsewhere = dict(PARAMS, n_delay=PARAMS["n_delay"] + 4)
    assert (
        c.restore_model(
            {
                "version": CURRENT_SCHEMA,
                "revision": 7,
                "params": elsewhere,
                "rmse": 2.0,
                "samples": 100,
                "band_c": [40.0, 232.0],
            }
        )
        is False
    )
    # Nothing crossed into the running model -- not n_delay, and not the other
    # parameters that arrived with it and would otherwise have been adopted.
    assert c.cfg["n_delay"] == PARAMS["n_delay"]
    assert c.cfg["C_c"] == pytest.approx(_DEFAULTS["C_c"])
    assert c.get_model_snapshot() is None
    # Said out loud, naming both lengths, in the same shape as the schema
    # mismatch: the operator is owed the reason the season's model went away.
    out = capsys.readouterr().out
    assert str(elsewhere["n_delay"]) in out
    assert str(PARAMS["n_delay"]) in out
    assert "snapshot" in out.lower()


#: A model whose chamber coasts far past the shipped one's: 1800 s of rise
#: after a fuel cut against the shipped model's 312 s, which is more than the
#: 600 s a default-configured horizon plans over. Every value is inside
#: PROMOTION_BOUNDS and its n_delay is PARAMS' own, so the only thing that can
#: refuse it is a defect.
SLOW_PARAMS = dict(PARAMS, C_c=31000.0, theta=536.0)


def _snapshot(params, revision=3):
    return {
        "version": CURRENT_SCHEMA,
        "revision": revision,
        "params": dict(params),
        "rmse": 2.0,
        "samples": 900,
        "band_c": [40.0, 232.0],
    }


def test_a_restored_model_reaches_the_estimator_and_the_configured_horizon():
    """A restored model rebuilds thermal state without changing configuration."""
    c = _c()
    reference = _c()
    for each in (c, reference):
        each.set_target(110.0)

    assert c.restore_model(_snapshot(SLOW_PARAMS)) is True

    # The estimator predicts with the restored parameters, not the ones it was
    # constructed from.
    assert c.estimator.C_c == pytest.approx(SLOW_PARAMS["C_c"])
    assert not hasattr(c, "_built_n_horizon")
    # And the policy solving is the restored one: same setpoint, same
    # measurement, different firing rate from the controller that did not
    # restore.
    assert c.update(40.0)["cycle_ratio"] != pytest.approx(reference.update(40.0)["cycle_ratio"])


def test_a_model_that_cannot_be_built_leaves_the_running_one_alone():
    """A restore replaces the estimator and the policy, so it can fail where a
    config update could not. Half a controller is worse than an old one: the
    parts are built before anything is committed, and a build that raises
    leaves the model that was already solving in place, refused rather than
    raised through the worker thread that called it."""
    c = _c()
    before = dict(c.cfg)
    estimator = c.estimator

    def boom(cfg):
        raise RuntimeError("no solver today")

    c._build_for = boom
    assert c.restore_model(_snapshot(SLOW_PARAMS)) is False
    assert c.cfg == before
    assert c.estimator is estimator
    assert c.get_model_snapshot() is None


def test_status_reports_the_identified_band(capsys):
    c = _c()
    c.set_target(225.0)
    assert c.get_status()["model"] is None
    c._adopt_model(PARAMS, rmse=2.1, samples=100, band_c=(40.0, 232.0))
    model = c.get_status()["model"]
    assert model["band_c"] == [40.0, 232.0]
    assert model["rmse"] == pytest.approx(2.1)
    json.dumps(model, allow_nan=False)


def _adopted_online_controller():
    controller = _c(enable_online_adaptation=True)
    controller._adopt_model(PARAMS, rmse=2.1, samples=1730, band_c=(40.0, 232.0))
    return controller


def _active_online_snapshot():
    source = _adopted_online_controller()
    coordinator = source._online
    fallback = coordinator.incumbent
    active_arx = source._new_scheduled_arx()
    coordinator.incumbent = active_arx
    coordinator.challenger = source._new_scheduled_arx()
    coordinator._previous_incumbent = fallback
    coordinator._previous_incumbent_snapshot = deepcopy(fallback.snapshot())
    coordinator._previous_incumbent_digest = OnlineAdaptation.model_digest(fallback)
    coordinator._role_generation = 1
    return source.get_model_snapshot()


def _completed_evaluation():
    return {
        "decision_id": "generation-1-evaluation-1",
        "evaluated_at_s": 400.0,
        "role_generation": 1,
        "promoted": False,
        "committed": False,
        "consecutive_wins": 1,
        "rejection_reasons": (),
        "incumbent_prediction_score": 1.0,
        "challenger_prediction_score": 1.2,
        "incumbent_braking_score": None,
        "challenger_braking_score": None,
        "sample_count": 2,
        "prospective_digest": None,
        "window_start_s": 20.0,
        "window_end_s": 340.0,
        "incumbent_digest": "a" * 64,
        "challenger_digest": "b" * 64,
        "completed_origins": [
            {
                "origin_time_s": 20.0,
                "completion_time_s": 80.0,
                "horizon_steps": 3,
                "generation": 1,
                "observed_temperature_c": 110.0,
                "incumbent_error_c": 2.0,
                "challenger_error_c": 1.0,
                "braking": True,
                "observation_sequence": 0,
                "incumbent_digest": "a" * 64,
                "challenger_digest": "b" * 64,
                "incumbent_prediction_c": 108.0,
                "challenger_prediction_c": 109.0,
                "temperature_band": "middle",
                "ambient_source": "configured",
            },
            {
                "origin_time_s": 40.0,
                "completion_time_s": 340.0,
                "horizon_steps": 15,
                "generation": 1,
                "observed_temperature_c": 115.0,
                "incumbent_error_c": -3.0,
                "challenger_error_c": -4.0,
                "braking": False,
                "observation_sequence": 1,
                "incumbent_digest": "a" * 64,
                "challenger_digest": "b" * 64,
                "incumbent_prediction_c": 118.0,
                "challenger_prediction_c": 119.0,
                "temperature_band": "middle",
                "ambient_source": "configured",
            },
        ],
        "horizon_scores": [
            {
                "horizon_steps": 3,
                "incumbent_rmse_c": 2.0,
                "challenger_rmse_c": 1.0,
                "sample_count": 1,
            },
            {
                "horizon_steps": 15,
                "incumbent_rmse_c": 3.0,
                "challenger_rmse_c": 4.0,
                "sample_count": 1,
            },
            {
                "horizon_steps": 45,
                "incumbent_rmse_c": None,
                "challenger_rmse_c": None,
                "sample_count": 0,
            },
            {
                "horizon_steps": 90,
                "incumbent_rmse_c": None,
                "challenger_rmse_c": None,
                "sample_count": 0,
            },
            {
                "horizon_steps": 180,
                "incumbent_rmse_c": None,
                "challenger_rmse_c": None,
                "sample_count": 0,
            },
        ],
        "evaluation_duration_ms": 7.5,
    }


def _state_space_refresh_evidence():
    return {
        "accepted": True,
        "terminal_reason": None,
        "attempts": (
            {
                "order": 2,
                "delay": 3,
                "sample_count": 48,
                "hankel_shape": (8, 33),
                "singular_values": (8.0, 2.0),
                "effective_rank": 2,
                "alignment_error_c": 0.25,
                "rejection_reasons": (),
                "elapsed_ms": 4.0,
            },
        ),
        "refresh_duration_ms": 4.0,
        "state_space_digest": "c" * 64,
        "order": 2,
        "delay": 3,
        "singular_values": (8.0, 2.0),
        "effective_rank": 2,
        "alignment_error_c": 0.25,
        "max_pole_magnitude": 0.8,
        "process_covariance_trace": 0.2,
        "measurement_covariance": 0.1,
    }


def _snapshot_with_completed_evaluation():
    snapshot = _active_online_snapshot()
    snapshot["online_adaptation"]["last_evaluation"] = _completed_evaluation()
    return snapshot


def test_snapshot_restore_round_trips_state_space_evaluation_evidence_exactly():
    snapshot = _snapshot_with_completed_evaluation()
    evaluation = snapshot["online_adaptation"]["last_evaluation"]
    evaluation.update(
        challenger_model_kind="innovation-state-space",
        state_space_refresh=_state_space_refresh_evidence(),
    )
    restored = _c(enable_online_adaptation=True)

    assert restored.restore_model(snapshot) is True
    assert restored.get_model_snapshot()["online_adaptation"]["last_evaluation"] == evaluation


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("challenger_model_kind", "grey-box"),
        ("state_space_refresh", {"accepted": True}),
    ),
)
def test_completed_evaluation_rejects_malformed_state_space_extension(field, invalid):
    evaluation = _completed_evaluation()
    evaluation.update(
        challenger_model_kind="innovation-state-space",
        state_space_refresh=_state_space_refresh_evidence(),
    )
    evaluation[field] = invalid

    with pytest.raises(ValueError, match="evaluation"):
        _online_evaluation(evaluation)


def test_snapshot_restore_round_trips_an_exact_completed_evaluation_audit_trail():
    snapshot = _snapshot_with_completed_evaluation()
    restored = _c(enable_online_adaptation=True)

    assert restored.restore_model(snapshot) is True
    assert (
        restored.get_model_snapshot()["online_adaptation"]["last_evaluation"]
        == snapshot["online_adaptation"]["last_evaluation"]
    )


def test_snapshot_restore_preserves_a_win_across_incomplete_horizon_evidence():
    snapshot = _snapshot_with_completed_evaluation()
    evaluation = snapshot["online_adaptation"]["last_evaluation"]
    short_origin = evaluation["completed_origins"][:1]
    evaluation.update(
        {
            "consecutive_wins": 1,
            "rejection_reasons": ("prediction",),
            "sample_count": 1,
            "completed_origins": short_origin,
            "window_end_s": short_origin[0]["completion_time_s"],
        }
    )
    evaluation["horizon_scores"][1].update(
        {
            "incumbent_rmse_c": None,
            "challenger_rmse_c": None,
            "sample_count": 0,
        }
    )

    restored = _c(enable_online_adaptation=True)
    restored.set_target(110.0)
    assert restored.restore_model(snapshot) is True
    restored_evaluation = restored.get_status()["adaptation"]["last_evaluation_outcome"]
    assert restored_evaluation["rejection_reasons"] == ("prediction",)
    assert restored_evaluation["consecutive_wins"] == 1


def test_v2_active_arx_snapshot_accepts_a_digest_valid_arx_rollback_owner():
    snapshot = _active_online_snapshot()
    ownership = snapshot["online_adaptation"]
    rollback_snapshot = deepcopy(ownership["challenger"])
    rollback_owner = ScheduledARX.from_snapshot(rollback_snapshot)
    ownership["previous_incumbent"] = rollback_snapshot
    ownership["previous_incumbent_digest"] = OnlineAdaptation.model_digest(rollback_owner)

    restored = _c(enable_online_adaptation=True)
    restored.set_target(110.0)
    assert restored.restore_model(snapshot) is True
    assert restored.get_status()["adaptation"]["active_model_kind"] == "scheduled-arx"
    assert isinstance(restored._online._previous_incumbent, ScheduledARX)
    assert restored._online.rollback() is True
    assert isinstance(restored._online.incumbent, ScheduledARX)
    assert restored._online.lag_warmup_remaining == restored._online.policy.max_delay_steps


@pytest.mark.parametrize(
    ("origin_index", "field", "invalid"),
    [
        (origin_index, field, invalid)
        for origin_index in (0, 1)
        for field in ("observed_temperature_c", "incumbent_error_c", "challenger_error_c")
        for invalid in (None, True, "not-a-number", float("nan"), float("inf"), -float("inf"))
    ],
)
def test_snapshot_restore_rejects_non_finite_or_non_numeric_completed_origin_evidence_to_safe_grey_box(
    origin_index, field, invalid
):
    snapshot = _snapshot_with_completed_evaluation()
    snapshot["online_adaptation"]["last_evaluation"]["completed_origins"][origin_index][field] = invalid

    restored = _c(enable_online_adaptation=True)
    restored.set_target(110.0)

    assert restored.restore_model(snapshot) is True
    assert restored.cfg["C_c"] == pytest.approx(PARAMS["C_c"])
    assert restored.get_status()["adaptation"]["active_model_kind"] == "grey-box"
    assert restored.get_status()["adaptation"]["last_evaluation_outcome"] is None
    assert restored.get_model_snapshot()["online_adaptation"]["last_evaluation"] is None


@pytest.mark.parametrize(
    ("score_index", "field", "inconsistent"),
    [
        (0, "incumbent_rmse_c", 2.000_001),
        (0, "challenger_rmse_c", 0.999_999),
        (1, "incumbent_rmse_c", 3.000_001),
        (1, "challenger_rmse_c", 3.999_999),
        (0, "incumbent_rmse_c", -2.0),
        (1, "challenger_rmse_c", -4.0),
    ],
)
def test_snapshot_restore_rejects_inconsistent_horizon_rmse_to_safe_grey_box(score_index, field, inconsistent):
    snapshot = _snapshot_with_completed_evaluation()
    snapshot["online_adaptation"]["last_evaluation"]["horizon_scores"][score_index][field] = inconsistent

    restored = _c(enable_online_adaptation=True)
    restored.set_target(110.0)

    assert restored.restore_model(snapshot) is True
    assert restored.cfg["C_c"] == pytest.approx(PARAMS["C_c"])
    assert restored.get_status()["adaptation"]["active_model_kind"] == "grey-box"
    assert restored.get_status()["adaptation"]["last_evaluation_outcome"] is None
    assert restored.get_model_snapshot()["online_adaptation"]["last_evaluation"] is None


def test_pre_ownership_change_promoted_v1_snapshot_restores_a_cloned_arx_challenger():
    snapshot = _active_online_snapshot()
    ownership = snapshot["online_adaptation"]
    ownership["schema"] = "online-adaptation/v1"

    active_arx = deepcopy(ownership["incumbent"])
    rollback_owner = deepcopy(ownership["previous_incumbent"])
    rollback_digest = ownership["previous_incumbent_digest"]
    ownership["challenger"] = deepcopy(rollback_owner)

    restored = _c(enable_online_adaptation=True)
    restored.set_target(110.0)
    assert restored.restore_model(snapshot) is True

    restored_ownership = restored.get_model_snapshot()["online_adaptation"]
    assert restored.get_status()["adaptation"]["active_model_kind"] == "scheduled-arx"
    assert restored_ownership["incumbent"] == active_arx
    assert restored_ownership["challenger"]["schema"] == "scheduled-arx/v2"
    assert restored_ownership["challenger"]["candidates"] == active_arx["candidates"]
    assert restored_ownership["previous_incumbent"] == rollback_owner
    assert restored_ownership["previous_incumbent_digest"] == rollback_digest


def test_pre_audit_v1_non_null_evaluation_migrates_without_losing_learned_online_state():
    snapshot = _active_online_snapshot()
    ownership = snapshot["online_adaptation"]
    ownership["schema"] = "online-adaptation/v1"

    ownership.update(
        {
            "eligible_updates": 37,
            "rejected_updates": 11,
            "promotion_count": 7,
            "rollback_count": 2,
            "last_lifecycle_reason": "promotion",
            "last_lifecycle": None,
            "last_evaluation": {
                "decision_id": "legacy-decision",
                "evaluated_at_s": 600.0,
                "role_generation": 1,
                "promoted": False,
                "committed": False,
                "consecutive_wins": 0,
                "rejection_reasons": [],
                "incumbent_prediction_score": 1.1,
                "challenger_prediction_score": 1.2,
                "incumbent_braking_score": 1.3,
                "challenger_braking_score": 1.4,
                "sample_count": 0,
                "prospective_digest": None,
            },
        }
    )
    learned_state = {
        key: deepcopy(ownership[key])
        for key in (
            "incumbent",
            "previous_incumbent",
            "previous_incumbent_digest",
            "role_generation",
            "effective_updates",
            "consecutive_wins",
        )
    }

    restored = _c(enable_online_adaptation=True)
    restored.set_target(110.0)
    assert restored.restore_model(snapshot) is True

    restored_ownership = restored.get_model_snapshot()["online_adaptation"]
    restored_status = restored.get_status()["adaptation"]
    for key, value in learned_state.items():
        assert restored_ownership[key] == value
    assert restored_status["active_model_kind"] == "scheduled-arx"
    assert restored_status["eligible_updates"] == 37
    assert restored_status["rejected_updates"] == 11
    assert restored_status["promotion_count"] == 7
    assert restored_status["rollback_count"] == 2
    assert restored_status["last_evaluation_outcome"] is None


@pytest.mark.parametrize("shape", ("missing-owner", "digest-mismatch", "grey-with-owner"))
def test_invalid_active_online_ownership_falls_back_without_discarding_outer_grey(shape):
    snapshot = _active_online_snapshot()
    nested = snapshot["online_adaptation"]
    if shape == "missing-owner":
        nested["previous_incumbent"] = None
        nested["previous_incumbent_digest"] = None
    elif shape == "digest-mismatch":
        nested["previous_incumbent_digest"] = "0" * 64
    else:
        nested["incumbent"], nested["challenger"] = (
            deepcopy(nested["challenger"]),
            deepcopy(nested["incumbent"]),
        )
        nested["active_model_kind"] = "grey-box"

    restored = _c(enable_online_adaptation=True)
    restored.set_target(110.0)
    assert restored.restore_model(snapshot) is True
    assert restored.cfg["C_c"] == pytest.approx(PARAMS["C_c"])
    assert restored.get_status()["adaptation"]["active_model_kind"] == "grey-box"


def test_enabled_controller_persists_an_independently_owned_online_member():
    source = _adopted_online_controller()
    snapshot = source.get_model_snapshot()
    json.dumps(snapshot, allow_nan=False)
    assert snapshot["online_adaptation"]["schema"] == "online-adaptation/v3"

    restored = _c(enable_online_adaptation=True)
    restored.set_target(110.0)
    assert restored.restore_model(deepcopy(snapshot)) is True
    assert restored.cfg["C_c"] == pytest.approx(PARAMS["C_c"])
    assert restored.get_status()["adaptation"]["enabled"] is True

    nested = snapshot["online_adaptation"]
    nested["role_generation"] = 99
    assert source.get_model_snapshot()["online_adaptation"]["role_generation"] != 99


def test_absent_or_malformed_online_member_does_not_discard_valid_grey_box_model():
    source = _adopted_online_controller()
    snapshot = source.get_model_snapshot()

    legacy = deepcopy(snapshot)
    legacy.pop("online_adaptation")
    restored_legacy = _c(enable_online_adaptation=True)
    restored_legacy.set_target(110.0)
    assert restored_legacy.restore_model(legacy) is True
    assert restored_legacy.cfg["C_c"] == pytest.approx(PARAMS["C_c"])
    assert restored_legacy.get_status()["adaptation"]["active_model_kind"] == "grey-box"

    malformed = deepcopy(snapshot)
    malformed["online_adaptation"] = {"schema": "wrong/v1"}
    restored_malformed = _c(enable_online_adaptation=True)
    restored_malformed.set_target(110.0)
    assert restored_malformed.restore_model(malformed) is True
    assert restored_malformed.cfg["C_c"] == pytest.approx(PARAMS["C_c"])
    assert restored_malformed.get_status()["adaptation"]["active_model_kind"] == "grey-box"


def test_malformed_nested_state_space_member_preserves_outer_grey_box_restore():
    """A corrupt optional challenger must not discard the independently valid model."""
    snapshot = deepcopy(_active_online_snapshot())
    snapshot["online_adaptation"]["challenger"] = {
        "schema": "innovation-state-space/v2",
        "model": {"A": [[float("nan")]]},
    }

    restored = _c(enable_online_adaptation=True)
    restored.set_target(110.0)

    assert restored.restore_model(snapshot) is True
    assert restored.cfg["C_c"] == pytest.approx(PARAMS["C_c"])
    assert restored.get_status()["adaptation"]["active_model_kind"] == "grey-box"


def test_fitted_state_space_challenger_restores_as_a_session_resettable_shadow():
    source = _adopted_online_controller()
    shadow = source._new_state_space_challenger()
    for index in range(20):
        shadow.observe(_state_space_frame(index))
    assert shadow.snapshot()["schema"] == "innovation-state-space/v2"
    source._online.challenger = shadow
    snapshot = source.get_model_snapshot()

    restored = _c(enable_online_adaptation=True)

    assert restored.restore_model(snapshot) is True
    restored_shadow = restored._online.challenger
    assert restored.get_status()["adaptation"]["active_model_kind"] == "grey-box"
    assert restored_shadow.snapshot()["schema"] == "innovation-state-space-shadow/v1"
    assert restored_shadow.snapshot()["effective_samples"] == 0


def test_state_space_snapshot_cannot_restore_as_an_incumbent():
    source = _adopted_online_controller()
    shadow = source._new_state_space_challenger()
    for index in range(20):
        shadow.observe(_state_space_frame(index))
    snapshot = source.get_model_snapshot()
    snapshot["online_adaptation"]["incumbent"] = shadow.snapshot()

    restored = _c(enable_online_adaptation=True)

    assert restored.restore_model(snapshot) is True
    assert restored._online.incumbent.snapshot()["schema"] == "grey-box-adapter/v1"
    assert restored._online.challenger.snapshot()["schema"] == "scheduled-arx/v2"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("eligible_updates", True),
        ("rejected_updates", 1.5),
        ("promotion_count", -1),
        ("rollback_count", False),
        ("last_evaluation", {"decision_id": "incomplete"}),
    ],
)
def test_malformed_controller_owned_online_metadata_falls_back_atomically_to_grey_box(field, value):
    source = _adopted_online_controller()
    snapshot = source.get_model_snapshot()
    snapshot["online_adaptation"][field] = value

    restored = _c(enable_online_adaptation=True)
    restored.set_target(110.0)
    assert restored.restore_model(snapshot) is True
    assert restored.cfg["C_c"] == pytest.approx(PARAMS["C_c"])

    adaptation = restored.get_status()["adaptation"]
    assert adaptation["active_model_kind"] == "grey-box"
    assert adaptation["role_generation"] == 0
    assert adaptation["eligible_updates"] == 0
    assert adaptation["rejected_updates"] == 0
    assert adaptation["promotion_count"] == 0
    assert adaptation["rollback_count"] == 0
    assert adaptation["last_evaluation_outcome"] is None


@pytest.mark.parametrize(
    "online_member",
    [
        {"schema": "online-adaptation/v1", "bad": float("nan")},
        {"schema": "online-adaptation/v1", "payload": "x" * 65_536},
    ],
)
def test_invalid_composite_online_member_returns_the_previous_valid_snapshot(monkeypatch, online_member):
    controller = _adopted_online_controller()
    prior = controller.get_model_snapshot()
    monkeypatch.setattr(controller._online, "snapshot", lambda: online_member)

    assert controller.get_model_snapshot() == prior


def test_online_teardown_checkpoint_advances_a_restored_revision():
    source = _adopted_online_controller()
    snapshot = source.get_model_snapshot()
    snapshot["revision"] = 41

    restored = _c(enable_online_adaptation=True)
    assert restored.restore_model(snapshot) is True
    restored.refit_from_cook([])
    assert restored.get_model_snapshot()["revision"] == 42


def test_disabled_online_adaptation_keeps_the_legacy_snapshot_byte_for_byte():
    implicit = _c()
    explicit = _c(enable_online_adaptation=False)
    for controller in (implicit, explicit):
        controller.set_target(110.0)
        controller._adopt_model(PARAMS, rmse=2.1, samples=1730, band_c=(40.0, 232.0))

    assert json.dumps(implicit.get_model_snapshot(), allow_nan=False) == json.dumps(
        explicit.get_model_snapshot(), allow_nan=False
    )
    assert implicit.get_status()["adaptation"] == explicit.get_status()["adaptation"]
