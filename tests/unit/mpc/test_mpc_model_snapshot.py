"""What the MPC persists between cooks, and what it refuses to adopt."""

import json

import pytest

from controller.mpc import _DEFAULTS, Controller

#: The schema this controller writes and is the only one it will read back.
#: A literal, not `Controller._MODEL_SCHEMA`: importing it would move every
#: expectation below along with any change to it, so a bump could never be
#: caught here. Raising it is a deliberate edit, and the tests that pin what
#: happens to an OLDER record keep their own literals.
CURRENT_SCHEMA = 2

CYCLE = {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}
PARAMS = dict(
    C_c=2520.0,
    h_amb=0.224,
    T_amb=20.0,
    theta=93.0,
    n_delay=4,
    K_Q=6.95,
    sigma=1.4e-9,
)


def _c(**over):
    # Built at PARAMS' own n_delay, because a restore does not rebuild the lag
    # chain and refuses a snapshot that disagrees with the one already built
    # (pinned by test_a_snapshot_fitted_at_a_different_chain_length_is_refused
    # below). Every other test here is about some OTHER field of the snapshot,
    # so they must not trip that guard on the way to the thing they check.
    return Controller(dict(_DEFAULTS, policy="nlp", n_delay=PARAMS["n_delay"], **over), "C", dict(CYCLE))


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

    Version 1 described a model with a firepot state. Its C_c, h_amb and K_Q
    have the same names as this model's and are not the same quantities -- they
    were fitted against a chamber fed through a firepot -- so the subset whose
    keys still match must not be applied. A full version 1 record is used here,
    C_f and h_fc included, because the failure this guards against is precisely
    that those two extra keys are ignored and the other seven look valid.

    It is also said out loud. The operator's model goes back to the shipped
    defaults exactly once, at the upgrade, and finding that out from the
    overshoot instead of the log is the outcome the message exists to prevent.
    """
    c = _c()
    v1 = {
        "version": 1,
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
    # Asserted as the rendered phrases rather than a bare "1" in the output,
    # which the schema number alone would satisfy.
    out = capsys.readouterr().out
    assert "version 1" in out
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

    n_delay sizes the estimator, the NLP and the net policy, and all three are
    built once in __init__ -- `restore_model` runs afterwards, from
    controller/runtime/modes/hold.py, and rebuilds none of them. Writing the
    snapshot's n_delay into cfg would therefore leave the config describing a
    chain the running model does not have, and everything that reads it back --
    longest_braking_distance, the promotion gate's horizon requirement,
    _warn_about_model -- would size against the snapshot rather than against
    what is actually solving.

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


def test_status_reports_the_identified_band(capsys):
    c = _c()
    c.set_target(225.0)
    assert c.get_status()["model"] is None
    c._adopt_model(PARAMS, rmse=2.1, samples=100, band_c=(40.0, 232.0))
    model = c.get_status()["model"]
    assert model["band_c"] == [40.0, 232.0]
    assert model["rmse"] == pytest.approx(2.1)
    json.dumps(model, allow_nan=False)
