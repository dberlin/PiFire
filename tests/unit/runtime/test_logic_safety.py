from controller.runtime.logic.safety import startup_temp_bounds, evaluate_flameout, over_max_temp, SafetyVerdict


def test_startup_temp_bounds_clamps_to_min_and_max():
    s = {"minstartuptemp": 100, "maxstartuptemp": 200}
    assert startup_temp_bounds(50, s) == 100  # 0.9*50=45 -> min 100
    assert startup_temp_bounds(1000, s) == 200  # 0.9*1000=900 -> max 200
    assert startup_temp_bounds(150, s) == 135  # 0.9*150=135 within range


def test_evaluate_flameout():
    assert evaluate_flameout(210, 200, 0) is SafetyVerdict.OK
    assert evaluate_flameout(180, 200, 0) is SafetyVerdict.ERROR
    assert evaluate_flameout(180, 200, 2) is SafetyVerdict.REIGNITE


def test_evaluate_flameout_at_exact_boundary_is_ok():
    # ptemp exactly == startup_temp must be OK (pins the >= vs > edge, so a
    # future flip to > would be caught rather than silently changing behavior).
    assert evaluate_flameout(200, 200, 0) is SafetyVerdict.OK
    assert evaluate_flameout(200, 200, 5) is SafetyVerdict.OK


def test_over_max_temp():
    assert over_max_temp(501, {"maxtemp": 500}) is True
    assert over_max_temp(500, {"maxtemp": 500}) is False
