"""Auger and fan duty as chart series.

`prepare_chartdata` builds the temperature datasets from probe_config; duty is
not a probe and has no per-probe configuration, so it is appended afterwards
with fixed colours, its own axis marker, and its values converted to percent so
all three duty series share one unit.
"""

import math
from itertools import pairwise

import pytest

from file_mgmt.cookfile import DUTY_AXIS, TEMP_AXIS, prepare_chartdata
from file_mgmt.downsample import select_indices

_PROBE_CONFIG = {
    "grill1": {
        "name": "Grill",
        "type": "Primary",
        "enabled": True,
        "bg_color": "#111",
        "line_color": "#222",
        "bg_color_target": "#333",
        "line_color_target": "#444",
        "bg_color_setpoint": "#555",
        "line_color_setpoint": "#666",
    },
}


def _history(n=10, **duty):
    base = {
        "T": list(range(n)),
        "PSP": [225] * n,
        "P": {"grill1": [224.0] * n},
        "F": {},
        "NT": {"grill1": [225] * n},
    }
    base.update(duty)
    return base


def _by_label(result):
    return {dataset["label"]: dataset for dataset in result["chart_data"]}


def test_duty_series_are_appended_after_the_probe_datasets():
    """probe_mapper indexes chart_data by slot, so duty must not shift them."""
    history = _history(CR=[0.2] * 10, FD=[65] * 10)

    with_duty = prepare_chartdata(_PROBE_CONFIG, num_items=0, reduce=False, data_points=0, history=history)
    without_duty = prepare_chartdata(_PROBE_CONFIG, num_items=0, reduce=False, data_points=0, history=_history())

    assert with_duty["probe_mapper"] == without_duty["probe_mapper"]
    for slot in with_duty["probe_mapper"]["probes"].values():
        assert with_duty["chart_data"][slot]["label"] == without_duty["chart_data"][slot]["label"]


def test_duty_rides_its_own_axis_and_temperatures_keep_theirs():
    """A 0-100% signal cannot share a scale with a 225-degree trace."""
    history = _history(CR=[0.2] * 10, FD=[65] * 10)

    datasets = _by_label(prepare_chartdata(_PROBE_CONFIG, num_items=0, reduce=False, data_points=0, history=history))

    assert datasets["Auger Duty"]["axis"] == DUTY_AXIS
    assert datasets["Fan Duty"]["axis"] == DUTY_AXIS
    assert datasets["Grill"]["axis"] == TEMP_AXIS
    assert datasets["Grill Set Point"]["axis"] == TEMP_AXIS


def test_cycle_ratio_is_converted_to_percent_and_fan_duty_is_not():
    """Both arrive on one axis in one unit, so no client has to know which scaled."""
    history = _history(CR=[0.25] * 10, FD=[65] * 10)

    datasets = _by_label(prepare_chartdata(_PROBE_CONFIG, num_items=0, reduce=False, data_points=0, history=history))

    assert [pt["y"] for pt in datasets["Auger Duty"]["data"]] == [25.0] * 10
    assert [pt["y"] for pt in datasets["Fan Duty"]["data"]] == [65] * 10


def test_duty_is_hidden_by_default():
    """It is a diagnostic overlay on a chart people open to read temperatures."""
    history = _history(CR=[0.2] * 10, FD=[65] * 10)

    datasets = _by_label(prepare_chartdata(_PROBE_CONFIG, num_items=0, reduce=False, data_points=0, history=history))

    assert datasets["Auger Duty"]["hidden"] is True
    assert datasets["Grill"]["hidden"] is False


def test_an_all_none_duty_column_produces_no_dataset():
    """A cook recorded before duty existed must not gain an empty toggle.

    This is also the state of the realized-duty column outside Hold, which is
    the only mode that measures what reached the auger.
    """
    history = _history(CR=[0.2] * 10, RCR=[None] * 10, FD=[65] * 10)

    labels = _by_label(prepare_chartdata(_PROBE_CONFIG, num_items=0, reduce=False, data_points=0, history=history))

    assert "Auger Delivered" not in labels
    assert "Auger Duty" in labels


def test_a_history_with_no_duty_keys_at_all_still_charts():
    """Cook files written before duty existed carry no such key."""
    result = prepare_chartdata(_PROBE_CONFIG, num_items=0, reduce=False, data_points=0, history=_history())

    assert "Auger Duty" not in _by_label(result)
    assert result["chart_data"][result["probe_mapper"]["probes"]["grill1"]]["data"]


def test_a_partially_recorded_duty_column_keeps_its_gaps():
    """None is a gap in the line, never a zero -- zero duty is a real reading."""
    history = _history(CR=[None, None, 0.0, 0.4] + [0.4] * 6, FD=[0] * 10)

    datasets = _by_label(prepare_chartdata(_PROBE_CONFIG, num_items=0, reduce=False, data_points=0, history=history))
    values = [pt["y"] for pt in datasets["Auger Duty"]["data"]]

    assert values[:4] == [None, None, 0.0, 40.0]
    assert values[2] is not None, "0% duty decoded as 'not recorded'"


# ---------------------------------------------------------------------------
# Retaining duty across downsampling.
#
# `select_indices` measures fidelity under LINEAR interpolation. Duty is a step
# function the chart draws as steps, so its transitions -- and only its
# transitions -- are what has to survive.
# ---------------------------------------------------------------------------

_N = 30_000
_STEP_START, _STEP_END = 12_000, 12_600


def _flat_cook_with_a_duty_step():
    """A thermally quiet hold across which duty steps hard and back.

    The temperature deliberately barely moves: nothing about the TEMPERATURE
    shape justifies keeping the samples at the duty edges, so anything that
    keeps them did so because of the duty series. This is the duty-floor case
    the series exists to show -- the controller backing off while the grill
    holds steady.
    """
    grill = [225.0 + 1.5 * math.sin(i / 900.0) for i in range(_N)]
    ratio = [0.55 if _STEP_START <= i < _STEP_END else 0.12 for i in range(_N)]
    return grill, ratio


def _step_error(values, kept):
    """Worst gap between the drawn line and the samples, under STEP rendering.

    Between two kept samples a stepped line holds the earlier value, so the
    error at each skipped sample is measured against that -- not against a
    linear interpolation the chart never draws.
    """
    worst = 0.0
    for a, b in pairwise(kept):
        for j in range(a + 1, b):
            worst = max(worst, abs(values[j] - values[a]))
    return worst


def _chart(history):
    return prepare_chartdata(_PROBE_CONFIG, num_items=_N, reduce=True, data_points=1_000, history=history)


def _duty_history(ratio, grill):
    return {
        "T": list(range(_N)),
        "PSP": [225] * _N,
        "P": {"grill1": grill},
        "F": {},
        "NT": {"grill1": [225] * _N},
        "CR": ratio,
    }


def test_a_duty_step_survives_reduction_exactly():
    grill, ratio = _flat_cook_with_a_duty_step()

    result = _chart(_duty_history(ratio, grill))

    drawn = _by_label(result)["Auger Duty"]["data"]
    values = [pt["y"] for pt in drawn]
    assert len(values) < _N, "nothing was reduced, so this proves nothing about reduction"
    assert max(values) == pytest.approx(55.0), "the duty step was reduced away"
    assert min(values) == pytest.approx(12.0)


def test_negative_control_temperature_shape_alone_would_lose_the_step():
    """Proof the test above measures duty retention and not luck.

    This is what the reducer does when only the temperature series is offered
    -- the behaviour before duty was retained at all. On a flat trace it keeps
    an evenly spread thousand samples, and the duty step drawn through them is
    wrong by tens of percentage points. If this ever starts passing, the
    assertion above has stopped proving anything.
    """
    grill, ratio = _flat_cook_with_a_duty_step()
    times = [float(t) for t in range(_N)]

    temperature_only = select_indices([grill], times, tolerance=2.0, min_points=1_000)

    percent = [value * 100 for value in ratio]
    assert _step_error(percent, temperature_only) > 20.0, (
        "temperature shape alone happened to retain the duty step; pick a harder case"
    )


def test_the_step_edges_themselves_are_the_samples_kept():
    """Exactness comes from keeping transitions, so the transitions must be there."""
    grill, ratio = _flat_cook_with_a_duty_step()

    result = _chart(_duty_history(ratio, grill))

    stamps = {pt["x"] for pt in _by_label(result)["Auger Duty"]["data"]}
    assert _STEP_START in stamps, "the rising edge of the duty step was dropped"
    assert _STEP_END in stamps, "the falling edge of the duty step was dropped"


def test_retaining_duty_adds_only_its_own_transitions():
    """A step is cheap to keep exactly: its edges, not a denser everything.

    Guards the regression that made this approach necessary. Handing duty to
    the tolerance check instead reproduced the same step perfectly while
    keeping all 30,000 samples -- which would defeat downsampling on every
    history request, for a series that is off by default.

    Asserted as a MARGINAL cost against the same cook without a duty column,
    because the absolute count is set by the temperature, target and setpoint
    series and would move for reasons that have nothing to do with duty.
    """
    grill, ratio = _flat_cook_with_a_duty_step()

    with_duty = _chart(_duty_history(ratio, grill))
    history_without = _duty_history(ratio, grill)
    del history_without["CR"]
    without_duty = _chart(history_without)

    added = len(with_duty["time_labels"]) - len(without_duty["time_labels"])
    assert 0 < added <= 8, f"duty retention added {added} samples; a step has two edges"


def test_a_gaps_boundary_is_retained():
    """Where duty stops being recorded is exactly where the line must stop."""
    grill, _ = _flat_cook_with_a_duty_step()
    ratio = [None if i < 8_000 else 0.30 for i in range(_N)]

    result = _chart(_duty_history(ratio, grill))

    points = _by_label(result)["Auger Duty"]["data"]
    at_boundary = [pt for pt in points if pt["x"] == 8_000]
    assert at_boundary, "the first recorded sample after the gap was dropped"
    assert at_boundary[0]["y"] == pytest.approx(30.0)
