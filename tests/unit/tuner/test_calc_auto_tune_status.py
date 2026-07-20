# tests/unit/tuner/test_calc_auto_tune_status.py
#
# Pins the pure math in calc_auto_tune_status(data, units, status_data).
#
# NOTE: the production caller (blueprints/tuner/routes.py) guards the call with
# `if len(data) > 10:` before invoking calc_auto_tune_status. The function itself
# performs no length check, so these tests call it directly with small,
# hand-computed lists -- that is valid per the function's own contract.
from blueprints.tuner.tuner import calc_auto_tune_status


def make_status_data():
    # Mirrors the 9-key dict the route initializes before calling
    # calc_auto_tune_status (blueprints/tuner/routes.py read_auto_status).
    return {
        "current_tr": 0,
        "current_temp": 0,
        "high_tr": 0,
        "high_temp": 0,
        "medium_tr": 0,
        "medium_temp": 0,
        "low_tr": 0,
        "low_temp": 0,
        "ready": False,
    }


def test_basic_selection():
    # high=200 (tr 500), low=100 (tr 1000)
    # target = ((200-100)//2)+100 = 150 -> exact match at ref_T=150 (tr 700)
    # spread = 100 >= 25 (Celsius min_range) -> ready True
    data = [
        {"ref_T": 100, "probe_Tr": 1000},
        {"ref_T": 200, "probe_Tr": 500},
        {"ref_T": 150, "probe_Tr": 700},
    ]
    status_data = calc_auto_tune_status(data, "C", make_status_data())

    assert status_data["high_temp"] == 200
    assert status_data["high_tr"] == 500
    assert status_data["low_temp"] == 100
    assert status_data["low_tr"] == 1000
    assert status_data["medium_temp"] == 150
    assert status_data["medium_tr"] == 700
    assert status_data["ready"] is True


def test_dedup_overwrite_last_datapoint_wins():
    # ref_T=100 appears twice; the second occurrence's probe_Tr (1111) must
    # overwrite the first (1000) in place, per the "last wins" dedup rule.
    # temp_list ends up [100, 200] (100 kept at its original index, tr updated).
    data = [
        {"ref_T": 100, "probe_Tr": 1000},
        {"ref_T": 200, "probe_Tr": 500},
        {"ref_T": 100, "probe_Tr": 1111},  # overwrites the ref_T=100 slot
    ]
    status_data = calc_auto_tune_status(data, "C", make_status_data())

    # low_temp is still 100 (it appears once), but its tr is the LATER value.
    assert status_data["low_temp"] == 100
    assert status_data["low_tr"] == 1111
    assert status_data["high_temp"] == 200
    assert status_data["high_tr"] == 500


def test_medium_best_fit_non_exact_with_tie_break_prefers_first_index():
    # low=100 (idx2), high=201 (idx3)
    # target = ((201-100)//2)+100 = (101//2)+100 = 50+100 = 150 -- not in temp_list
    # distances to target 150: |140-150|=10, |160-150|=10, |100-150|=50, |201-150|=51
    # 140 (idx0) and 160 (idx1) tie at delta=10; strict "<" means the FIRST
    # (lower index, 140) wins the comparison and is never displaced by 160.
    data = [
        {"ref_T": 140, "probe_Tr": 1400},
        {"ref_T": 160, "probe_Tr": 1600},
        {"ref_T": 100, "probe_Tr": 1000},
        {"ref_T": 201, "probe_Tr": 2010},
    ]
    status_data = calc_auto_tune_status(data, "C", make_status_data())

    assert status_data["high_temp"] == 201
    assert status_data["low_temp"] == 100
    assert status_data["medium_temp"] == 140
    assert status_data["medium_tr"] == 1400


def test_min_range_ready_differs_by_units_same_spread():
    # spread = high - low = 140 - 100 = 40
    # Celsius min_range is 25 -> 40 >= 25 -> ready True
    # Fahrenheit min_range is 50 -> 40 < 50 -> ready stays False (never reset)
    data = [
        {"ref_T": 100, "probe_Tr": 1000},
        {"ref_T": 140, "probe_Tr": 1400},
    ]

    status_c = calc_auto_tune_status(data, "C", make_status_data())
    assert status_c["ready"] is True

    status_f = calc_auto_tune_status(data, "F", make_status_data())
    assert status_f["ready"] is False


def test_min_range_ready_true_under_fahrenheit_when_spread_meets_50():
    # spread = 160 - 100 = 60 >= 50 (Fahrenheit min_range) -> ready True
    data = [
        {"ref_T": 100, "probe_Tr": 1000},
        {"ref_T": 160, "probe_Tr": 1600},
    ]
    status_data = calc_auto_tune_status(data, "F", make_status_data())
    assert status_data["ready"] is True


def test_mutates_in_place_and_returns_same_object_leaving_current_fields_alone():
    data = [
        {"ref_T": 100, "probe_Tr": 1000},
        {"ref_T": 200, "probe_Tr": 500},
    ]
    status_data = make_status_data()
    status_data["current_tr"] = 42
    status_data["current_temp"] = 99

    result = calc_auto_tune_status(data, "C", status_data)

    # Same object identity: the function mutates and returns its own argument.
    assert result is status_data
    # Pre-existing, unrelated keys are left untouched by this function.
    assert status_data["current_tr"] == 42
    assert status_data["current_temp"] == 99
