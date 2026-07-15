from common import common as c

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
    assert set(items[0]) == {"T", "P", "F", "PSP", "NT", "AUX"}
    assert items[0]["P"] == {"Grill": 225}
    assert items[0]["PSP"] == 225


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
