"""A probe with no reading, on the two on-device dashboards.

The web UI rendered `Math.round(null)` as a confident 0. The Qt card avoided
that only by accident -- `property real temp` REFUSES an undefined, so the
property kept its previous value while logging
"Unable to assign [undefined] to double" on every frame -- and the pygame card
coerced the None to 0, the web's bug in a third place.

All three now resolve the absence deliberately: the last real reading, marked
with its age. These pin the resolution and the wording; the web side pins the
same table in tests/unit/helpers/dashboard/deriveView.test.ts.
"""

import pytest
from PySide6.QtCore import QObject, QUrl
from PySide6.QtQml import QQmlComponent

from display.qtbackend import FoodProbeModel
from display.staleness import resolve_reading, stale_label

MINUTE = 60_000
NOW = 1_000_000_000_000


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "last data 0s ago"),
        (47, "last data 47s ago"),
        (59, "last data 59s ago"),
        (60, "last data 1m ago"),
        (3599, "last data 59m ago"),
        (3600, "last data 1h ago"),
        (7260, "last data 2h ago"),
    ],
)
def test_stale_label_counts_seconds_then_minutes_then_hours(seconds, expected):
    assert stale_label(seconds) == expected


def test_stale_label_never_reports_a_negative_age():
    # Clocks step. An age computed across one must not read "last data -3s ago".
    assert stale_label(-3) == "last data 0s ago"


def test_a_reporting_probe_is_live_and_unmarked():
    # The carried entry disagrees on purpose: a live reading must win, so a
    # stale marker here would mean the branch was chosen on the wrong field.
    assert resolve_reading(212, {"temp": 147, "ts": NOW - MINUTE}, NOW) == (212.0, True, "")


def test_a_probe_with_no_reading_shows_its_last_one_with_the_age():
    assert resolve_reading(None, {"temp": 147, "ts": NOW - 47_000}, NOW) == (
        147.0,
        True,
        "last data 47s ago",
    )


def test_a_probe_that_has_never_reported_has_nothing_to_show():
    temp, has_temp, stale = resolve_reading(None, None, NOW)
    assert has_temp is False
    assert stale == ""
    # 0.0 is what a typed double gets when there is no number; `has_temp` is
    # what stops it being DRAWN as a temperature.
    assert temp == 0.0


def test_a_zero_reading_is_a_reading_and_not_an_absence():
    # 0 is falsy and a real temperature. A truthiness check here would report a
    # freezing probe as unavailable.
    assert resolve_reading(0, {"temp": 147, "ts": NOW - MINUTE}, NOW) == (0.0, True, "")


def _model(labels=("Probe1",)):
    return FoodProbeModel([{"name": f"Food {i}", "label": lbl} for i, lbl in enumerate(labels)])


def _row(model, index=0):
    return model._rows[index]


def test_food_model_carries_the_last_reading_for_a_null_probe():
    model = _model()
    model.update(
        {"F": {"Probe1": None}, "NT": {}, "LAST": {"Probe1": {"temp": 147, "ts": NOW - 47_000}}},
        NOW,
    )

    assert _row(model)["temp"] == 147.0
    assert _row(model)["hasTemp"] is True
    assert _row(model)["stale"] == "last data 47s ago"


def test_food_model_marks_a_probe_with_no_history_as_having_no_reading():
    model = _model()
    model.update({"F": {"Probe1": None}, "NT": {}, "LAST": {}}, NOW)

    assert _row(model)["hasTemp"] is False
    assert _row(model)["stale"] == ""


def test_food_model_clears_the_marker_when_the_probe_recovers():
    model = _model()
    model.update(
        {"F": {"Probe1": None}, "NT": {}, "LAST": {"Probe1": {"temp": 147, "ts": NOW - 47_000}}},
        NOW,
    )
    model.update({"F": {"Probe1": 152}, "NT": {}, "LAST": {"Probe1": {"temp": 152, "ts": NOW}}}, NOW)

    assert _row(model)["temp"] == 152.0
    assert _row(model)["stale"] == ""


def test_food_model_ages_the_marker_while_the_probe_stays_quiet():
    # The reading does not change from frame to frame while a probe is down, so
    # a model that compared only the readings would freeze the age at whatever
    # it was when the probe went quiet.
    model = _model()
    last = {"Probe1": {"temp": 147, "ts": NOW - 10_000}}
    model.update({"F": {"Probe1": None}, "NT": {}, "LAST": last}, NOW)
    assert _row(model)["stale"] == "last data 10s ago"

    model.update({"F": {"Probe1": None}, "NT": {}, "LAST": last}, NOW + 40_000)
    assert _row(model)["stale"] == "last data 50s ago"


def test_food_model_still_works_against_a_blob_written_before_LAST_existed():
    model = _model()
    model.update({"F": {"Probe1": 140}, "NT": {"Probe1": 165}}, NOW)

    assert _row(model)["temp"] == 140.0
    assert _row(model)["hasTemp"] is True
    assert _row(model)["stale"] == ""


def _texts(item):
    """Every string a QML item actually shows, in construction order.

    Filtered on `visible`, not merely on having a `text`: the staleness line
    exists in the tree at all times and is hidden when there is nothing to
    say, so collecting text alone would pass against a line that never draws.
    """
    return [
        c.property("text")
        for c in item.findChildren(QObject)
        if c.metaObject().className().startswith("QQuickText") and c.property("visible") is True
    ]


def _probe_card(engine, **props):
    comp = QQmlComponent(engine, QUrl.fromLocalFile("display/qml/components/ProbeCard.qml"))
    obj = comp.create()
    assert obj is not None, comp.errorString()
    obj.setParent(engine)
    for key, value in props.items():
        obj.setProperty(key, value)
    return obj


def test_qml_card_draws_the_number_and_the_age_for_a_stale_probe(qml_engine):
    card = _probe_card(qml_engine, temp=147, hasTemp=True, stale="last data 47s ago")

    texts = _texts(card)
    assert "147" in texts
    assert "last data 47s ago" in texts


def test_qml_card_draws_a_dash_not_a_zero_when_there_is_no_reading(qml_engine):
    card = _probe_card(qml_engine, temp=0, hasTemp=False, stale="")

    texts = _texts(card)
    assert "—" in texts
    assert "0" not in texts
    assert not any(t.startswith("last data") for t in texts)


def test_qml_card_shows_no_age_line_while_the_probe_reports(qml_engine):
    card = _probe_card(qml_engine, temp=212, hasTemp=True, stale="")

    texts = _texts(card)
    assert "212" in texts
    assert not any(t.startswith("last data") for t in texts)
