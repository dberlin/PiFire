from common.persistence import history as c

SAMPLE = {
    "probe_history": {"primary": {"Grill": 225}, "food": {"P1": 145}, "aux": {}},
    "primary_setpoint": 225,
    "notify_targets": {"Grill": 0},
}


def test_history_cap_matches_oracle(ds, oracle):
    exp = oracle("history_cap")
    for _ in range(5):
        c.write_history(SAMPLE, maxsizelines=3)
    items = c.read_history()
    assert len(items) == exp["len"] == 3  # capped
    # each reconstructed row carries the expected dict keys
    assert set(items[0]) == {"T", "P", "F", "PSP", "NT", "AUX", "CR", "RCR", "FD"}
    assert items[0]["P"] == {"Grill": 225}
    assert items[0]["PSP"] == 225


def test_duty_keys_are_present_even_when_no_duty_was_recorded(ds):
    """The duty keys must appear on EVERY row, holding None when unrecorded.

    `unpack_history` builds its key set from row 0 of the window, so a key that
    is present on only some rows is dropped for the whole read whenever the
    first row lacks it. A history window spanning the v8 migration -- old rows
    with NULL duty, new rows with real duty -- would silently lose the duty
    series entirely if these keys were emitted conditionally the way EXD is.
    """
    c.write_history(SAMPLE)  # no "duty" key at all, as pre-v8 rows have
    row = c.read_history()[0]

    assert {"CR", "RCR", "FD"} <= set(row)
    assert row["CR"] is None
    assert row["RCR"] is None
    assert row["FD"] is None


def test_duty_roundtrips_and_zero_is_distinguishable_from_unrecorded(ds):
    """0% duty is a real reading and must not decode as "not recorded"."""
    c.write_history(dict(SAMPLE, duty={"cycle_ratio": 0.0, "fan_duty": 0}))
    row = c.read_history()[0]

    assert row["CR"] == 0.0
    assert row["FD"] == 0
    assert row["CR"] is not None and row["FD"] is not None


def test_fan_duty_roundtrips_as_int(ds):
    """fan_duty is a whole percent: NUMERIC affinity, so 65 must not become 65.0.

    Same reasoning as psp below -- REAL affinity would coerce it, and the chart
    would render a "65.0%" axis reading.
    """
    c.write_history(dict(SAMPLE, duty={"cycle_ratio": 0.25, "fan_duty": 65}))
    row = c.read_history()[0]

    assert isinstance(row["FD"], int)
    assert row["FD"] == 65


def test_ext_data_flag_without_a_payload_does_not_raise_or_store_null(ds):
    """The control loop no longer populates ext_data; the setting can still be on.

    Before duty became first-class columns, `settings.globals.ext_data` was the
    only thing that wrote to that column, and it wrote hardcoded zeros. With
    that producer gone the flag can be true while `in_data` carries no
    "ext_data" key at all -- which must not raise, and must not store the JSON
    literal `null` (which would decode into an EXD key holding nothing on every
    single row).
    """
    c.write_history(SAMPLE, ext_data=True)
    row = c.read_history()[0]

    assert "EXD" not in row


def test_history_cap_evicts_oldest_not_newest(ds):
    """Eviction must drop the OLDEST rows, keeping the NEWEST -- not merely
    keep the count at the cap. Write more rows than maxsizelines with
    distinct primary_setpoint values so survivors are identifiable by value,
    then assert the surviving PSPs are the highest (most-recently-written)
    ones, in write order."""
    for psp in range(10):
        c.write_history(dict(SAMPLE, primary_setpoint=psp), maxsizelines=3)
    items = c.read_history()
    assert len(items) == 3
    # The 3 survivors must be the 3 most recently written (psp 7, 8, 9), in
    # write order (oldest-of-the-survivors first, since read_history is
    # ORDER BY id ascending).
    assert [item["PSP"] for item in items] == [7, 8, 9]


def test_history_ext_data_roundtrip(ds):
    d = dict(SAMPLE, ext_data={"k": 1})
    c.write_history(d, ext_data=True)
    row = c.read_history()[0]
    assert row["EXD"] == {"k": 1}


def test_history_psp_roundtrips_as_int(ds):
    """Regression: history.psp must use NUMERIC (not REAL) affinity so an
    integer primary_setpoint (e.g. 225) round-trips as an int, not 225.0.
    This mirrors the metrics REAL-affinity bug fixed earlier, applied to the
    history table's psp column (the history chart's setpoint series)."""
    c.write_history(dict(SAMPLE, primary_setpoint=225))
    row = c.read_history()[0]
    assert isinstance(row["PSP"], int)
    assert row["PSP"] == 225
