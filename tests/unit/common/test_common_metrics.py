from common import common as c
from common import datastore


def test_replace_last_matches_oracle(ds, oracle):
    exp = oracle("metrics_replace_last")
    m = c.default_metrics()
    m["mode"] = "Startup"
    c.write_metrics(m, new_metric=True)
    m2 = c.default_metrics()
    m2["mode"] = "Hold"
    c.write_metrics(m2, new_metric=False)
    assert c.read_metrics()["mode"] == exp["last"]["mode"] == "Hold"
    assert len(c.read_metrics(all=True)) == exp["all_len"] == 1


def test_new_metric_without_existing_does_not_crash(ds):
    c.write_metrics(new_metric=True)  # regression: no metrics yet
    assert "starttime" in c.read_metrics()


def test_metrics_columns_queryable(ds):
    m = c.default_metrics()
    m["mode"] = "Startup"
    m["primary_setpoint"] = 225
    c.write_metrics(m, new_metric=True)

    conn = datastore.connection()
    row = conn.execute("SELECT mode, primary_setpoint FROM metrics").fetchone()
    assert row == ("Startup", 225)


def test_metrics_roundtrip_all_fields(ds):
    m = c.default_metrics()
    m["id"] = "distinct-id"
    m["starttime"] = 111.0
    m["starttime_c"] = "00:01:00"
    m["endtime"] = 222.0
    m["endtime_c"] = "00:02:00"
    m["timeinmode"] = "Active"
    m["mode"] = "Hold"
    m["augerontime"] = 12.5
    m["augerontime_c"] = "12 s"
    m["estusage_m"] = "5 grams"
    m["estusage_i"] = "0.01 pounds"
    m["fanontime"] = 33.0
    m["fanontime_c"] = "33 s"
    m["smokeplus"] = False
    m["primary_setpoint"] = 225
    m["smart_start_profile"] = 2
    m["startup_temp"] = 165
    m["p_mode"] = 3
    m["auger_cycle_time"] = 8
    m["pellet_level_start"] = 87
    m["pellet_level_end"] = 92
    m["pellet_brand_type"] = "Generic-Alder"

    c.write_metrics(m, new_metric=True)
    result = c.read_metrics()

    for key, _ in c.metrics_items:
        if key in ("starttime", "id"):
            continue  # stamped by new_metric=True
        assert result[key] == m[key], key
    assert isinstance(result["smokeplus"], bool)
    assert result["smokeplus"] is False

    # Regression: SQLite REAL-affinity columns silently coerce integer inputs
    # to floats on round-trip (87 -> 87.0), which then render as "87.0" in
    # the UI (cookfile detail page, CSV export, event totals). NUMERIC
    # affinity must preserve the input's Python type instead. `==` alone
    # does not catch this (87 == 87.0 is True in Python), so assert type.
    for key in ("pellet_level_start", "pellet_level_end", "primary_setpoint", "startup_temp"):
        assert isinstance(result[key], int), f"{key} should round-trip as int, got {type(result[key])}"

    # A genuinely-float field must still come back as float.
    m2 = c.default_metrics()
    m2["auger_cycle_time"] = 0.3
    c.write_metrics(m2, new_metric=True)
    result2 = c.read_metrics()
    assert isinstance(result2["auger_cycle_time"], float)
    assert result2["auger_cycle_time"] == 0.3
    assert isinstance(result2["starttime"], float)  # stamped by new_metric=True via time.time()
