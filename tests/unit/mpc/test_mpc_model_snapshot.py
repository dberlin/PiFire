"""What the MPC persists between cooks, and what it refuses to adopt."""

import json

import pytest

from controller.mpc import _DEFAULTS, Controller

CYCLE = {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}
PARAMS = dict(
    C_f=9.0,
    C_c=2520.0,
    h_fc=0.39,
    h_amb=0.224,
    T_amb=20.0,
    theta=93.0,
    n_delay=4,
    K_Q=6.95,
    sigma=1.4e-9,
)


def _c(**over):
    return Controller(dict(_DEFAULTS, policy="nlp", **over), "C", dict(CYCLE))


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
                "version": 1,
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
            "version": 1,
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
            {"version": 1, "revision": 1, "params": bad, "rmse": 2.0, "samples": 100, "band_c": [40.0, 232.0]}
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
    for junk in (None, {}, {"version": 1}, {"version": 1, "revision": "x", "params": {}}):
        assert c.restore_model(junk) is False


def test_a_non_integer_revision_is_refused_even_with_otherwise_valid_params():
    """Pairs a fully valid `params` with a bad `revision` so this exercises the
    revision type-check itself, not the params/bounds validation that an empty
    `params` dict would trip on its own."""
    c = _c()
    for bad_revision in ("x", 1.5, None):
        assert (
            c.restore_model(
                {
                    "version": 1,
                    "revision": bad_revision,
                    "params": dict(PARAMS),
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
            {"version": 1, "revision": 1, "params": bad, "rmse": 2.0, "samples": 100, "band_c": [40.0, 232.0]}
        )
        is False
    )
    assert c.cfg["n_delay"] == _DEFAULTS["n_delay"]


def test_a_float_valued_whole_number_n_delay_is_accepted():
    """A snapshot round-tripped through JSON stores n_delay as a plain number,
    e.g. 4.0 rather than 4. That is a whole number and must be accepted, not
    rejected merely for arriving as a float."""
    c = _c()
    whole = dict(PARAMS, n_delay=4.0)
    assert (
        c.restore_model(
            {"version": 1, "revision": 1, "params": whole, "rmse": 2.0, "samples": 100, "band_c": [40.0, 232.0]}
        )
        is True
    )
    assert c.cfg["n_delay"] == pytest.approx(4.0)


def test_status_reports_the_identified_band(capsys):
    c = _c()
    c.set_target(225.0)
    assert c.get_status()["model"] is None
    c._adopt_model(PARAMS, rmse=2.1, samples=100, band_c=(40.0, 232.0))
    model = c.get_status()["model"]
    assert model["band_c"] == [40.0, 232.0]
    assert model["rmse"] == pytest.approx(2.1)
    json.dumps(model, allow_nan=False)
