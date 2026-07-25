from common import datastore_accessors as c
from common import defaults
from common import datastore


def test_replace_last_matches_oracle(ds, oracle):
    exp = oracle("metrics_replace_last")
    m = defaults.default_metrics()
    m["mode"] = "Startup"
    c.append_metric(m)
    m2 = defaults.default_metrics()
    m2["mode"] = "Hold"
    c.update_metrics(m2)
    assert c.read_metrics()["mode"] == exp["last"]["mode"] == "Hold"
    assert len(c.read_metrics(all=True)) == exp["all_len"] == 1


def test_replace_last_partial_dict_preserves_other_columns(ds):
    # Root hazard (partial-dict blast): update_metrics(metrics)
    # with a dict that only sets a FEW keys must not null out every column the
    # caller didn't mention -- it should update only the keys present and leave
    # the rest of the last row untouched.
    m = defaults.default_metrics()
    m["mode"] = "Startup"
    m["primary_setpoint"] = 225
    m["pellet_brand_type"] = "Generic-Alder"
    c.append_metric(m)

    c.update_metrics({"mode": "Hold"})

    result = c.read_metrics()
    assert result["mode"] == "Hold"  # the key that was actually provided
    # Everything else must survive untouched, not get nulled to None/0/"".
    assert result["primary_setpoint"] == 225
    assert result["pellet_brand_type"] == "Generic-Alder"


def test_replace_last_full_dict_still_replaces_everything(ds):
    # A full dict (every METRIC_COLUMNS key present) must behave exactly as
    # before: every column gets set from the dict.
    m = defaults.default_metrics()
    m["mode"] = "Startup"
    m["primary_setpoint"] = 225
    c.append_metric(m)

    m2 = defaults.default_metrics()
    m2["mode"] = "Hold"
    m2["primary_setpoint"] = 0  # explicit reset, present in the full dict
    c.update_metrics(m2)

    result = c.read_metrics()
    assert result["mode"] == "Hold"
    assert result["primary_setpoint"] == 0


def test_replace_last_explicit_none_nulls_the_column(ds):
    # Presence, not truthiness, decides: a caller that explicitly wants to
    # clear a column passes {"col": None} and it takes effect.
    m = defaults.default_metrics()
    m["mode"] = "Startup"
    m["pellet_brand_type"] = "Generic-Alder"
    c.append_metric(m)

    c.update_metrics({"pellet_brand_type": None})

    result = c.read_metrics()
    assert result["pellet_brand_type"] is None
    assert result["mode"] == "Startup"  # untouched


def test_replace_last_unknown_keys_ignored(ds):
    # Unknown keys in a partial dict are ignored (same as today's full-dict
    # behavior filtering through METRIC_COLUMNS).
    m = defaults.default_metrics()
    m["mode"] = "Startup"
    c.append_metric(m)

    c.update_metrics({"mode": "Hold", "not_a_real_column": "whatever"})

    result = c.read_metrics()
    assert result["mode"] == "Hold"
    assert "not_a_real_column" not in result


def test_new_metric_without_existing_does_not_crash(ds):
    c.append_metric()  # regression: no metrics yet
    assert "starttime" in c.read_metrics()


def test_metrics_columns_queryable(ds):
    m = defaults.default_metrics()
    m["mode"] = "Startup"
    m["primary_setpoint"] = 225
    c.append_metric(m)

    conn = datastore.connection()
    row = conn.execute("SELECT mode, primary_setpoint FROM metrics").fetchone()
    assert row == ("Startup", 225)


def test_metrics_roundtrip_all_fields(ds):
    m = defaults.default_metrics()
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

    c.append_metric(m)
    result = c.read_metrics()

    for key, _ in defaults.metrics_items:
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
    m2 = defaults.default_metrics()
    m2["auger_cycle_time"] = 0.3
    c.append_metric(m2)
    result2 = c.read_metrics()
    assert isinstance(result2["auger_cycle_time"], float)
    assert result2["auger_cycle_time"] == 0.3
    assert isinstance(result2["starttime"], float)  # stamped by new_metric=True via time.time()
