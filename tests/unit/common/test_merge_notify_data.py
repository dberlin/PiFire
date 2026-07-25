"""Unit contract for common.common.merge_notify_data.

The behavioural case -- two real writers losing each other's changes -- lives
in tests/characterization/test_control_writes_cross_writer.py. This file pins
the merge function's own edges, in particular the conditions under which it
declines to merge and falls back to the wholesale array replacement json_patch
would have done.
"""

from common.common import merge_notify_data, notify_data_key


def _entry(label, type_, **fields):
    return {"label": label, "type": type_, **fields}


def _base():
    return [
        _entry("Grill", "probe", req=False, target=0),
        _entry("Grill", "probe_limit_high", req=False, target=0),
        _entry("Timer", "timer", req=False, shutdown=False),
    ]


def test_key_is_label_and_type():
    assert notify_data_key({"label": "Grill", "type": "probe"}) == ("Grill", "probe")


def test_untouched_fields_are_not_imposed_on_current():
    base = _base()
    current = _base()
    current[0]["target"] = 203  # an earlier writer already landed this
    incoming = _base()
    incoming[2]["shutdown"] = True  # this writer only touched the timer flag

    merged = merge_notify_data(base, current, incoming)

    assert merged[0]["target"] == 203
    assert merged[2]["shutdown"] is True


def test_changed_fields_are_applied():
    merged = merge_notify_data(_base(), _base(), [_entry("Grill", "probe", req=True, target=225)] + _base()[1:])
    assert merged[0] == {"label": "Grill", "type": "probe", "req": True, "target": 225}


def test_same_label_different_type_are_independent():
    incoming = _base()
    incoming[1]["target"] = 350
    merged = merge_notify_data(_base(), _base(), incoming)
    assert merged[0]["target"] == 0
    assert merged[1]["target"] == 350


def test_fields_absent_from_incoming_are_never_deleted():
    current = _base()
    current[0]["eta"] = 42
    incoming = _base()  # no 'eta' member at all
    merged = merge_notify_data(_base(), current, incoming)
    assert merged[0]["eta"] == 42


def test_explicit_none_is_applied_when_it_differs_from_base():
    # strip_null_members deliberately leaves list-nested nulls alone, so a
    # writer CAN clear an eta back to None; that must still work.
    base = _base()
    base[0]["eta"] = 42
    current = _base()
    current[0]["eta"] = 42
    incoming = _base()
    incoming[0]["eta"] = None
    assert merge_notify_data(base, current, incoming)[0]["eta"] is None


def test_entry_dropped_by_the_writer_is_removed():
    incoming = [e for e in _base() if e["type"] != "probe_limit_high"]
    merged = merge_notify_data(_base(), _base(), incoming)
    assert [notify_data_key(e) for e in merged] == [("Grill", "probe"), ("Timer", "timer")]


def test_entry_added_by_the_writer_is_appended():
    incoming = _base() + [_entry("Probe9", "probe", req=True, target=99)]
    merged = merge_notify_data(_base(), _base(), incoming)
    assert notify_data_key(merged[-1]) == ("Probe9", "probe")
    assert merged[-1]["target"] == 99


def test_entry_added_by_an_earlier_patch_is_not_removed_by_a_later_one():
    # The later writer's baseline never had it, so its absence from that
    # writer's payload is an omission, not a deletion.
    current = _base() + [_entry("Probe9", "probe", req=True)]
    merged = merge_notify_data(_base(), current, _base())
    assert ("Probe9", "probe") in [notify_data_key(e) for e in merged]


def test_current_ordering_is_preserved():
    assert [notify_data_key(e) for e in merge_notify_data(_base(), _base(), _base())] == [
        notify_data_key(e) for e in _base()
    ]


def test_merge_does_not_alias_the_inputs():
    base, current, incoming = _base(), _base(), _base()
    incoming[0]["target"] = 203
    merged = merge_notify_data(base, current, incoming)
    merged[0]["target"] = 999
    assert current[0]["target"] == 0
    assert incoming[0]["target"] == 203


# --- fallback: anything unkeyable replaces wholesale, as json_patch did ------


def test_unkeyable_entries_fall_back_to_wholesale_replacement():
    for bad in (
        [{"eta": 0}],  # no label/type
        [{"label": "Grill"}],  # no type
        [_entry("Grill", "probe"), _entry("Grill", "probe")],  # duplicate key
        ["not-a-dict"],
        "not-a-list",
        None,
    ):
        assert merge_notify_data(_base(), _base(), bad) == bad
        # Unkeyable on the base/current side is equally disqualifying: the
        # result is the incoming array verbatim.
        assert merge_notify_data(bad, _base(), _base()) == _base()
        assert merge_notify_data(_base(), bad, _base()) == _base()
