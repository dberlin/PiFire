"""A probe with no reading, on the two on-device dashboards.

The web UI rendered `Math.round(null)` as a confident 0. The Qt card avoided
that only by accident -- `property real temp` REFUSES an undefined, so the
property kept its previous value while logging
"Unable to assign [undefined] to double" on every frame -- and the pygame card
coerced the None to 0, the web's bug in a third place.

All three resolve absence deliberately: retain the last real number, qualify
its age only in a trusted clock domain, and never call it live.
"""

import pytest
from PySide6.QtCore import QObject, QUrl
from PIL import Image
from PySide6.QtQml import QQmlComponent

from display.flexobject import ProbeCard, resolve_accent
from display.qtbackend import FoodProbeModel, PiFireBackend
from display.staleness import last_reading_age_s, resolve_reading
from tests.fakes.clock import clock_stamp

MINUTE = 60_000
NOW = 1_000_000_000_000


def test_a_reporting_probe_is_live_and_unmarked():
    # The carried entry disagrees on purpose: a live reading must win, so a
    # stale marker here would mean the branch was chosen on the wrong field.
    assert resolve_reading(212, {"temp": 147, "ts": NOW - MINUTE}, age_s=60) == (212.0, True, "")


def test_a_probe_with_no_reading_shows_its_last_one_with_the_age():
    temp, has_temp, stale = resolve_reading(None, {"temp": 147, "ts": NOW - 47_000}, age_s=47)
    assert temp == 147.0
    assert has_temp is False
    assert "47" in stale


def test_a_probe_that_has_never_reported_has_nothing_to_show():
    temp, has_temp, stale = resolve_reading(None, None)
    assert has_temp is False
    assert stale == ""
    # 0.0 is what a typed double gets when there is no number; `has_temp` is
    # what stops it being DRAWN as a temperature.
    assert temp == 0.0


def test_a_zero_reading_is_a_reading_and_not_an_absence():
    # 0 is falsy and a real temperature. A truthiness check here would report a
    # freezing probe as unavailable.
    assert resolve_reading(0, {"temp": 147, "ts": NOW - MINUTE}, age_s=60) == (0.0, True, "")


def _model(labels=("Probe1",)):
    return FoodProbeModel([{"name": f"Food {i}", "label": lbl} for i, lbl in enumerate(labels)])


def _row(model, index=0):
    return model._rows[index]


def test_food_model_carries_the_last_reading_for_a_null_probe():
    model = _model()
    model.update(
        {
            "F": {"Probe1": None},
            "NT": {},
            "LAST": {
                "Probe1": {
                    "temp": 147,
                    "ts": NOW - 47_000,
                    "clock_stamp": clock_stamp(monotonic_s=53).as_dict(),
                }
            },
        },
        current=clock_stamp(),
        heartbeat=clock_stamp(),
    )

    assert _row(model)["temp"] == 147.0
    assert _row(model)["hasTemp"] is False
    assert "47" in _row(model)["stale"]


def test_food_model_marks_a_probe_with_no_history_as_having_no_reading():
    model = _model()
    model.update({"F": {"Probe1": None}, "NT": {}, "LAST": {}}, current=clock_stamp(), heartbeat=clock_stamp())

    assert _row(model)["hasTemp"] is False
    assert _row(model)["stale"] == ""


def test_food_model_clears_the_marker_when_the_probe_recovers():
    model = _model()
    model.update(
        {"F": {"Probe1": None}, "NT": {}, "LAST": {"Probe1": {"temp": 147, "ts": NOW - 47_000}}},
        current=clock_stamp(),
        heartbeat=clock_stamp(),
    )
    model.update(
        {"F": {"Probe1": 152}, "NT": {}, "LAST": {"Probe1": {"temp": 152, "ts": NOW}}},
        current=clock_stamp(),
        heartbeat=clock_stamp(),
    )

    assert _row(model)["temp"] == 152.0
    assert _row(model)["hasTemp"] is True
    assert _row(model)["stale"] == ""


@pytest.mark.parametrize("wall_delta", [-3600.0, 3600.0])
def test_food_model_ages_retained_reading_across_wall_steps(wall_delta):
    model = _model()
    last = {"Probe1": {"temp": 147, "ts": NOW, "clock_stamp": clock_stamp(monotonic_s=90).as_dict()}}
    model.update({"F": {"Probe1": None}, "NT": {}, "LAST": last}, current=clock_stamp(), heartbeat=clock_stamp())
    assert "10" in _row(model)["stale"]

    reader = clock_stamp(monotonic_s=140, wall_s=1_800_000_000 + wall_delta)
    model.update({"F": {"Probe1": None}, "NT": {}, "LAST": last}, current=reader, heartbeat=reader)
    assert "50" in _row(model)["stale"]
    assert _row(model)["temp"] == 147.0
    assert _row(model)["hasTemp"] is False
    assert last["Probe1"]["ts"] == NOW


def test_food_model_still_works_against_a_blob_written_before_LAST_existed():
    model = _model()
    model.update({"F": {"Probe1": 140}, "NT": {"Probe1": 165}}, current=clock_stamp(), heartbeat=clock_stamp())

    assert _row(model)["temp"] == 140.0
    assert _row(model)["hasTemp"] is True
    assert _row(model)["stale"] == ""


def test_confirmed_invalid_health_overrides_a_carried_food_reading_and_its_stale_copy():
    model = _model()
    model.update(
        {"F": {"Probe1": None}, "NT": {}, "LAST": {"Probe1": {"temp": 147, "ts": NOW - 47_000}}},
        current=clock_stamp(),
        heartbeat=clock_stamp(),
        invalid_labels={"Probe1"},
    )

    assert _row(model)["temp"] == 0.0
    assert _row(model)["hasTemp"] is False
    assert _row(model)["stale"] == ""


def _primary_health(*, state, temperature_valid, outcome, current):
    return {
        "device": "max31856",
        "port": "TC0",
        "label": "Grill",
        "displayName": "Grill",
        "role": "Primary",
        "report": {
            "state": state,
            "faults": ["open"] if state == "confirmed" else [],
            "evidence": ["hardware"],
            "temperatureValid": temperature_valid,
            "detail": {"policy": "observe"},
        },
        "detector": {"source": "hardware", "policy": "observe"},
        "outcome": outcome,
        "freshness": {
            "current": current,
            "lastReportedAgeS": 0.25 if current else 47.0,
            "reason": "current" if current else "stale",
        },
    }


def test_confirmed_invalid_health_beats_primary_last_value_even_when_health_transport_is_stale():
    backend = PiFireBackend(
        lambda: (
            {
                "P": {"Grill": None},
                "F": {},
                "AUX": {},
                "PSP": 250,
                "NT": {},
                "LAST": {"Grill": {"temp": 225, "ts": NOW - 47_000}},
            },
            {"mode": "Error", "units": "F", "outpins": {}},
        ),
        lambda c, d: None,
        {"primary": {"name": "Grill", "label": "Grill"}, "food": [], "aux": []},
        health_fetch_fn=lambda: [
            _primary_health(state="confirmed", temperature_valid=False, outcome="stopped", current=False)
        ],
    )
    backend._now = lambda: NOW / 1000

    backend.poll()

    assert backend.primaryTemp == 0.0
    assert backend.primaryHasTemp is False
    assert backend.primaryStale == ""
    assert backend.probeHealth.summary["highest"]["freshnessQualifier"] == "Last reported"


def test_suspected_health_retains_the_primary_numeric_reading():
    backend = PiFireBackend(
        lambda: (
            {"P": {"Grill": 225}, "F": {}, "AUX": {}, "PSP": 250, "NT": {}, "LAST": {}},
            {"mode": "Hold", "units": "F", "outpins": {}},
        ),
        lambda c, d: None,
        {"primary": {"name": "Grill", "label": "Grill"}, "food": [], "aux": []},
        health_fetch_fn=lambda: [
            _primary_health(state="suspected", temperature_valid=True, outcome="none", current=True)
        ],
    )
    backend._now = lambda: NOW / 1000

    backend.poll()

    assert backend.primaryTemp == 225.0
    assert backend.primaryHasTemp is True
    assert backend.probeHealth.summary["highest"]["headline"] == "CHECK PROBE"


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


def test_qml_card_draws_retained_number_but_cannot_complete_target(qml_engine):
    model = _model()
    model.update(
        {
            "F": {"Probe1": None},
            "NT": {"Probe1": 140},
            "LAST": {
                "Probe1": {
                    "temp": 147,
                    "ts": NOW,
                    "clock_stamp": clock_stamp(monotonic_s=53).as_dict(),
                }
            },
        },
        current=clock_stamp(),
        heartbeat=clock_stamp(),
    )
    row = _row(model)
    card = _probe_card(qml_engine, temp=row["temp"], hasTemp=row["hasTemp"], stale=row["stale"], target=row["target"])
    texts = _texts(card)
    assert "147" in texts
    assert row["stale"] in texts
    assert card.property("hasTemp") is False
    assert card.property("done") is False


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


@pytest.mark.parametrize(
    "entry",
    [
        {"temp": 147, "ts": NOW - 47_000},
        {
            "temp": 147,
            "ts": NOW - 47_000,
            "clock_stamp": clock_stamp(boot_id="f5398d50-5456-4ba8-8e8c-5baf55bf7381").as_dict(),
        },
    ],
)
def test_historical_or_previous_boot_reading_keeps_number_with_unknown_age(entry, qml_engine):
    age = last_reading_age_s(entry, current=clock_stamp(), heartbeat=clock_stamp())
    assert age is None
    model = _model()
    model.update(
        {"F": {"Probe1": None}, "NT": {"Probe1": 140}, "LAST": {"Probe1": entry}},
        current=clock_stamp(),
        heartbeat=clock_stamp(),
    )
    row = _row(model)
    assert row["temp"] == 147.0
    assert row["hasTemp"] is False
    assert row["stale"]
    assert not any(char.isdigit() for char in row["stale"])
    card = _probe_card(qml_engine, temp=row["temp"], hasTemp=row["hasTemp"], stale=row["stale"], target=row["target"])
    assert "147" in _texts(card)
    assert row["stale"] in _texts(card)
    assert card.property("done") is False


@pytest.mark.parametrize("age_s", [47.0, None])
def test_flex_card_draws_retained_number_without_live_completion(monkeypatch, age_s):
    drawn = []
    original_draw_text = ProbeCard._draw_text

    def draw_text(self, text, font_name, font_point_size, color, rect=False, bg_fill=None):
        drawn.append((str(text), color))
        return original_draw_text(self, text, font_name, font_point_size, color, rect, bg_fill)

    monkeypatch.setattr(ProbeCard, "_draw_text", draw_text)
    temp, has_temp, stale = resolve_reading(None, {"temp": 147, "ts": NOW}, age_s=age_s)
    data = {
        "name": "probe_card",
        "type": "probe_card",
        "position": [0, 0],
        "size": [400, 220],
        "animation_enabled": False,
        "accent": resolve_accent("Ember"),
        "units": "F",
        "data": {"name": "Food", "temp": temp, "hasTemp": has_temp, "stale": stale, "target": 140},
        "touch_areas": [],
    }
    card = ProbeCard("probe_card", data, Image.new("RGBA", (400, 220)))
    assert "147" in [text for text, _ in drawn]
    assert stale in [text for text, _ in drawn]
    retained_target_color = next(color for text, color in drawn if text == "140°")
    retained_progress = card.get_object_canvas().getpixel((200, 183))

    drawn.clear()
    data["data"] = {**data["data"], "hasTemp": True, "stale": ""}
    card.update_object_data(data)
    live_target_color = next(color for text, color in drawn if text == "140°")
    assert live_target_color != retained_target_color
    assert card.get_object_canvas().getpixel((200, 183)) != retained_progress
