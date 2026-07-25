"""Metrics-safety fix wave, item 2: `common/app.py::prepare_annotations()`
consumes `read_metrics(all=True)` rows directly, without the None guards
`process_metrics()` gained (f3d5a83, extended into `guard_none_metric_field`
in this fix wave) -- a poisoned row (None starttime, the same shape that DID
exist in a real datastore -- see update_metrics' "amend last record"
partial-dict hazard, item 1 of this same fix wave) crashes the History page
path on `metrics_data[index]["starttime"] > displayed_starttime` (comparing
None to an int raises TypeError in Python 3).

The fix guards `starttime` the same warn-and-default way process_metrics does
(shared via `guard_none_metric_field`), so a poisoned row is skipped/defaulted
instead of crashing, and annotations for healthy rows are still produced.
"""

from common.app import prepare_annotations
from common.defaults import default_metrics
from common.modes import Mode


def test_prepare_annotations_none_starttime_does_not_crash():
    poisoned = dict(default_metrics(), mode=Mode.SMOKE, starttime=None)
    healthy = dict(default_metrics(), mode=Mode.HOLD, starttime=100000)

    # pre-fix: TypeError: '>' not supported between instances of 'NoneType' and 'int'
    result = prepare_annotations(0, [poisoned, healthy])

    # The healthy row still gets an annotation.
    assert any(a["label"]["content"] == Mode.HOLD for a in result.values())


def test_prepare_annotations_none_starttime_defaults_to_zero_and_is_windowed_out():
    # The safe default (0) means a poisoned row falls before any positive
    # displayed_starttime window and is excluded, same as a genuinely-old event.
    poisoned = dict(default_metrics(), mode=Mode.SMOKE, starttime=None)

    result = prepare_annotations(1, [poisoned])

    assert result == {}


def test_prepare_annotations_healthy_rows_unaffected():
    healthy = dict(default_metrics(), mode=Mode.STARTUP, starttime=500)

    result = prepare_annotations(0, [healthy])

    assert len(result) == 1
    annotation = next(iter(result.values()))
    assert annotation["label"]["content"] == Mode.STARTUP
    assert annotation["xMin"] == 500
