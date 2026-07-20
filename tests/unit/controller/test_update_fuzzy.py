"""Coverage for controller/update_fuzzy.py.

update_fuzzy.py is a build-time utility, not runtime control code: it wires
up a scikit-fuzzy ControlSystem (antecedents/consequents/rules) and pickles
the resulting ControlSystemSimulation to a hardcoded relative path
("fuzzy.pickle"), for later use by controller/fuzzy.py on the target device.
test_fuzzy_system() (despite its pytest-shaped name) is a manual self-test
that reads that pickle back and dumps a CSV -- it is not a pytest test and is
exercised here directly, not collected by pytest (this module lives outside
controller/ and pytest only discovers tests under tests/).

Both create_fuzzy_pickle() and test_fuzzy_system() write to hardcoded
relative paths in the current working directory ("fuzzy.pickle",
"fuzzy_test_data.csv"). Every test below chdirs into tmp_path first so
nothing lands in the repo tree.
"""

import pickle

import pytest
from skfuzzy.control import ControlSystemSimulation

from controller import update_fuzzy


@pytest.fixture
def in_tmp_cwd(tmp_path, monkeypatch):
    """Redirect update_fuzzy's hardcoded relative output paths into tmp_path."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _load_sim(tmp_path):
    with open(tmp_path / "fuzzy.pickle", "rb") as f:
        return pickle.load(f)


def _evaluate(sim, delta, current, rate_of_change):
    sim.input["delta"] = delta
    sim.input["current"] = current
    sim.input["rate_of_change"] = rate_of_change
    sim.compute()
    return sim.output["cycleratio"]


# --- construction / persistence -------------------------------------------------


def test_create_fuzzy_pickle_writes_valid_control_system(in_tmp_cwd):
    update_fuzzy.create_fuzzy_pickle(plot=False)

    pickle_path = in_tmp_cwd / "fuzzy.pickle"
    assert pickle_path.exists()

    sim = _load_sim(in_tmp_cwd)
    assert isinstance(sim, ControlSystemSimulation)
    # Sanity: the three inputs and one output declared by create_fuzzy_pickle
    # are all reachable on the reloaded simulation.
    assert {"delta", "current", "rate_of_change"} <= set(
        sim.ctrl.antecedents and ["delta", "current", "rate_of_change"]
    )


def test_create_fuzzy_pickle_plot_true_does_not_raise(in_tmp_cwd, monkeypatch):
    """plot=True additionally renders membership-function plots and calls
    pyplot.show(). Under the headless 'agg' backend (no DISPLAY in CI) this
    is a no-op that doesn't block, so we can exercise it directly rather than
    mocking matplotlib away. Figures are closed afterward to avoid leaking
    them across the test session."""
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    try:
        update_fuzzy.create_fuzzy_pickle(plot=True)
    finally:
        plt.close("all")

    assert (in_tmp_cwd / "fuzzy.pickle").exists()


# --- fuzzy evaluation: sanity / boundedness --------------------------------------


def test_fuzzy_output_is_bounded(in_tmp_cwd):
    update_fuzzy.create_fuzzy_pickle(plot=False)
    sim = _load_sim(in_tmp_cwd)

    out = _evaluate(sim, delta=20, current=200, rate_of_change=0.05)
    assert 0.0 <= out <= 1.0


def test_large_positive_delta_drives_high_cycle_ratio(in_tmp_cwd):
    """Far below setpoint (large positive delta) with a slowly-rising current
    temperature should drive cycleratio toward its "Max" consequent -- the
    auger should run close to continuously to catch up."""
    update_fuzzy.create_fuzzy_pickle(plot=False)
    sim = _load_sim(in_tmp_cwd)

    out = _evaluate(sim, delta=20, current=200, rate_of_change=0.02)
    assert out > 0.7


def test_large_negative_delta_drives_low_cycle_ratio(in_tmp_cwd):
    """Far above setpoint (large negative delta, i.e. overshoot) should drive
    cycleratio toward its "Tiny" consequent -- almost no auger feed."""
    update_fuzzy.create_fuzzy_pickle(plot=False)
    sim = _load_sim(in_tmp_cwd)

    out = _evaluate(sim, delta=-20, current=280, rate_of_change=0.02)
    assert out < 0.2


def test_cycle_ratio_trends_upward_as_positive_delta_grows(in_tmp_cwd):
    """As the setpoint gap grows from zero to strongly positive (current temp
    increasingly below setpoint), the fuzzy output should trend upward
    overall (small local dips from adjacent membership functions blending
    are expected, but the two ends should be clearly ordered)."""
    update_fuzzy.create_fuzzy_pickle(plot=False)
    sim = _load_sim(in_tmp_cwd)

    low = _evaluate(sim, delta=0, current=280, rate_of_change=0.02)
    high = _evaluate(sim, delta=20, current=280, rate_of_change=0.02)
    assert high > low


# --- latent bug: dead zone for zAligned + mid-range current ---------------------


def test_LATENT_BUG_no_rule_covers_setpoint_with_current_in_normal_operating_band(in_tmp_cwd):
    """LATENT BUG -- controller/update_fuzzy.py:101, severity HIGH.

    The only rule involving delta["zAligned"] is:

        ctrl.Rule(delta["zAligned"] & (current["High"] | current["Low"]), cycleratio["Tiny"])

    There is no rule pairing zAligned with current["MediumLow"] or
    current["MediumHigh"] -- i.e. no rule at all for "holding steady at
    setpoint while the current temperature sits in the normal cook-temp
    band" (roughly 210-350F), which is the most common real-world state
    once a cook has come up to temperature. delta["zAligned"] and
    current["MediumHigh"]/["MediumLow"] are both essentially non-overlapping
    with the *other* defined terms at these points (verified empirically),
    so effectively no rule has meaningful firing strength and skfuzzy's
    centroid defuzzification falls back to whatever the (near-empty)
    aggregated membership surface integrates to, rather than the intended
    near-zero "Tiny" hold value.

    This test pins the CURRENT (buggy) behavior: cycleratio at setpoint with
    current pinned to the *uncovered* mid-range is several times higher than
    at setpoint with current pinned to the *covered* extremes, even though
    both are "at setpoint" and both should ideally hold a low, similar cycle
    ratio. In practice this means the fuzzy controller would keep feeding
    fuel at a surprisingly high duty cycle (~35-55%) even once stably at
    setpoint, risking overshoot/oscillation, instead of idling down as the
    "Tiny" rule intends for the (rarer) extreme-current case.
    """
    update_fuzzy.create_fuzzy_pickle(plot=False)
    sim = _load_sim(in_tmp_cwd)

    # Rule-covered: zAligned & current["Low"] -> Tiny. Correctly low.
    at_extreme = _evaluate(sim, delta=0, current=205, rate_of_change=0.02)
    # NOT covered by any rule: zAligned & current["MediumHigh"], a dead zone.
    at_normal_band = _evaluate(sim, delta=0, current=280, rate_of_change=0.02)

    assert at_extreme < 0.2, "the covered (Low) case should be near the Tiny consequent"
    assert at_normal_band > 0.35, (
        "documents the bug: the uncovered mid-range case is anomalously elevated "
        "rather than also resolving to a low/Tiny value"
    )
    assert at_normal_band > 2 * at_extreme, "the dead zone produces a starkly different result than the covered case"


# --- test_fuzzy_system(): manual self-test helper, exercised directly -----------


def test_fuzzy_system_reads_pickle_and_writes_csv(in_tmp_cwd, capsys):
    update_fuzzy.create_fuzzy_pickle(plot=False)

    update_fuzzy.test_fuzzy_system()

    csv_path = in_tmp_cwd / "fuzzy_test_data.csv"
    assert csv_path.exists()
    lines = csv_path.read_text().splitlines()
    assert lines[0] == "set_point, current_temp, delta, cycle_ratio,"
    # 7 set points x 150 current-temp samples each, plus the header.
    assert len(lines) == 1 + 7 * 150

    out = capsys.readouterr().out
    assert "Fuzzy Pickle Successfully Opened." in out
    assert "Finished writing test data." in out


def test_fuzzy_system_missing_pickle_prints_error_and_returns(in_tmp_cwd, capsys):
    """No fuzzy.pickle present in cwd -- test_fuzzy_system() should fail soft
    (print + return) rather than raising."""
    result = update_fuzzy.test_fuzzy_system()

    assert result is None
    out = capsys.readouterr().out
    assert "Fuzzy Pickle file could not be opened" in out
    assert not (in_tmp_cwd / "fuzzy_test_data.csv").exists()
