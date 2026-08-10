"""PID-SP composes the identifier and the predictor and models nothing itself."""

import importlib
import math
import time

import pytest

from controller.applied_output import AppliedOutput, OutputSource
from controller.pid_sp import STARTUP_REDUCTION
from grillplat.actuator_capabilities import AUGER_TIMING

CONFIG = {"PB": 60.0, "Ti": 180.0, "Td": 45.0, "stable_window": 12, "center_factor": 0.0010}
CYCLE_DATA = {}


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(time, "time", c)
    return c


def _controller(name, clock, units="F"):
    mod = importlib.import_module(f"controller.{name}")
    return mod.Controller(dict(CONFIG), units, dict(CYCLE_DATA))


def test_pid_sp_paces_its_guards_off_the_real_auger_frame(clock):
    """The three-cycle windows below are three control cycles. The control
    cycle is the auger's pulse frame, which is what actually paces the auger,
    and it comes from the platform rather than from any setting -- so an empty
    cycle_data gives the guards the frame they mean."""
    sp = _controller("pid_sp", clock)
    assert sp.cycle_time == AUGER_TIMING.frame_s


def test_the_startup_reduction_is_applied_to_the_new_output(clock):
    """Within the first three cycles after a setpoint change, u is the newly
    computed p+i+d scaled by STARTUP_REDUCTION -- not a stale prior output.

    Pinned against PID-SP's own p+i+d rather than another controller's output,
    so the assertion isolates the reduction rather than any difference in how
    the two seed their first derivative."""
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    clock.t += 20.0  # inside cycle_time * 3 of the setpoint change
    out_sp = sp.update(200.0)
    status = sp.get_status()
    assert out_sp == pytest.approx((status["p"] + status["i"] + status["d"]) * STARTUP_REDUCTION)


def test_the_reduction_stops_after_three_cycles(clock):
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    clock.t += 20.0 * 3 + 1
    out_sp = sp.update(200.0)
    status = sp.get_status()
    assert out_sp == pytest.approx(status["p"] + status["i"] + status["d"])


def test_the_reduction_stops_exactly_at_the_three_cycle_boundary(clock):
    """60 == cycle_time * 3 exactly: the guard's strict `<` must already read
    False on the boundary itself, not one tick early or late."""
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    clock.t += 20.0 * 3  # current_time - last_set_time == 60, not < 60
    out_sp = sp.update(200.0)
    status = sp.get_status()
    assert out_sp == pytest.approx(status["p"] + status["i"] + status["d"])


def test_repeated_identical_clock_values_keep_dt_floored_at_pid_sp_level(clock):
    """pid_sp calls _elapsed_since_last_update() at its own call site rather
    than inheriting a tested one; a raw subtraction here divides by exactly
    0.0 on a repeated clock reading and raises, rather than returning
    something merely inaccurate."""
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    sp.update(220.0)  # error == -5, else branch
    result = sp.update(221.0)  # same clock value, still else branch
    assert math.isfinite(result)


def test_a_trusted_model_makes_the_selected_temperature_diverge_from_measured(clock):
    """The first tick only anchors the clock; the correction appears on the
    second, once duty has been integrated across a real interval."""
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    sp.restore_model({"K": 800.0, "tau": 600.0, "theta": 40.0, "revision": 1})
    sp.set_output(AppliedOutput(0.9, OutputSource.CONTROLLER, clock.t))
    clock.t += 20.0
    assert sp.update(200.0) is not None
    assert sp.get_status()["predictor"]["active"] is True
    assert sp.get_status()["selected_temp"] == 200.0  # anchored, correction still zero
    clock.t += 20.0
    sp.update(200.0)
    status = sp.get_status()
    selected = status["selected_temp"]
    assert selected > 200.0  # x0 has moved, xd has not
    # P and error are computed from the corrected temperature, not the raw
    # probe reading -- the substitution this whole task exists to make.
    assert status["error"] == pytest.approx(selected - 225.0)
    assert status["p"] == pytest.approx(sp.kp * status["error"] + sp.center)


def test_the_derivative_never_mixes_a_measured_and_a_predicted_sample(clock):
    """Both terms of the derivative come from the selected series -- including
    the PREVIOUS one held in self.last, not just the current tick. The first
    tick anchors (selected == measured by construction), so a test that stops
    at tick two can't see self.last holding a raw measured value; it has to
    reach a third tick where the previous sample was itself corrected."""
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    sp.restore_model({"K": 800.0, "tau": 600.0, "theta": 40.0, "revision": 1})
    sp.set_output(AppliedOutput(0.9, OutputSource.CONTROLLER, clock.t))
    clock.t += 20.0
    sp.update(200.0)  # anchors: selected == measured
    clock.t += 20.0
    sp.update(205.0)
    second = sp.get_status()["selected_temp"]
    assert second != 205.0, "the predictor is not correcting; the test proves nothing"
    clock.t += 20.0
    sp.update(210.0)
    status = sp.get_status()
    third = status["selected_temp"]
    assert status["d"] == pytest.approx(sp.kd * (third - second) / 20.0)


def test_set_target_preserves_the_learned_model(clock):
    sp = _controller("pid_sp", clock)
    sp.restore_model({"K": 800.0, "tau": 600.0, "theta": 40.0, "revision": 1})
    sp.set_target(275.0)
    assert sp.get_model_snapshot()["K"] == 800.0
    assert sp.inter == 0.0  # but the target-dependent PID terms do reset


def test_no_tau_or_theta_config_is_read(clock):
    """A user-supplied tau=115 is outside the design's own trusted band.
    controller/controllers.json still advertises the options until Task 10
    removes them; what this pins is that pid_sp no longer reads them."""
    import controller.pid_sp as mod

    source = open(mod.__file__).read()
    assert 'config.get("tau"' not in source
    assert 'config.get("theta"' not in source
    assert "math.exp" not in source
    assert "self.roc" not in source


def test_the_first_update_computes_a_zero_derivative_regardless_of_starting_temperature(clock):
    """The first update's derivative is exactly zero regardless of the
    starting temperature, because self.last seeds from that same first
    reading rather than a fixed value.

    Both readings land inside the else branch (not the overshoot/undershoot
    short-circuits) so the derivative is actually computed, not left at its
    unexercised 0.0 default."""
    cold = _controller("pid_sp", clock)
    hot = _controller("pid_sp", clock)
    cold.set_target(150.0)
    hot.set_target(150.0)
    clock.t += 20.0
    cold.update(140.0)  # error == -10, inside the else branch
    hot.update(155.0)  # error == +5, inside the else branch
    assert cold.get_status()["d"] == 0.0
    assert hot.get_status()["d"] == 0.0


def test_start_change_temp_is_seeded_so_the_integral_guard_never_sees_none(clock):
    """set_target() records self.last as start_change_temp before any update
    has run, so a fresh construction leaves start_change_temp at None (self.last
    is also None until the first update seeds it). The integral-reset guard
    reaches `abs(self.start_change_temp - self.set_point)` once new_target is
    True, the 3-cycle delay has elapsed, and the error is inside the stable
    window but outside the +/-3 dead zone -- exactly the branch below. Without
    seeding start_change_temp this raises TypeError in the live control loop."""
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    clock.t += 20.0 * 3 + 5.0  # >= cycle_time * 3
    result = sp.update(220.0)  # abs(error) == 5: inside (3, stable_window]
    assert math.isfinite(result)


def test_derivative_is_not_suppressed_on_a_downward_set_point_change(clock):
    """D still reflects the selected-temperature rate of change on a downward
    setpoint change with new_target True and set_point below the current
    reading -- no suppression fires in that case."""
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    clock.t += 20.0
    sp.update(230.0)
    sp.set_target(200.0)  # downward: new_target True, set_point < last selected
    clock.t += 20.0
    sp.update(205.0)
    status = sp.get_status()
    assert status["d"] == pytest.approx(sp.kd * (205.0 - 230.0) / 20.0)
    assert status["d"] != 0.0


def test_a_last_selected_temperature_of_exactly_zero_is_repaired_on_a_new_target(clock):
    """In the untrusted regime, self.last is None only at startup; a selected
    temperature of exactly 0.0 in native units is a distinct case the None-seed
    does not cover. Without the repair, a setpoint change following that reading
    computes the derivative against a temperature that was never real.

    Asserted on the derivative directly rather than against another controller:
    the repair re-seeds self.last from the current selected temperature, so the
    derivative on that tick is exactly zero. Un-repaired it would be
    kd * 220.0 / dt, which is nowhere near it -- the pinned value cannot be
    reached by both paths.
    """
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    clock.t += 20.0
    sp.update(200.0)
    clock.t += 20.0
    sp.update(0.0)  # a reading of exactly 0.0 in native units: self.last becomes exactly 0.0
    assert sp.last == 0.0
    sp.set_target(225.0)  # any setpoint change: new_target True
    # Past the startup-reduction window (>= cycle_time * 3), so the reduction
    # cannot be confused with this repair.
    dt = 20.0 * 3 + 1
    clock.t += dt
    sp.update(220.0)

    assert sp.get_status()["d"] == pytest.approx(0.0)
    # The value the same tick would have produced had the repair not fired.
    assert sp.kd * 220.0 / dt != pytest.approx(0.0)


def test_a_first_approach_that_misses_the_band_still_clears_new_target(clock):
    """Reaching the set point is a crossing, not a band.

    `new_target` gates the integral-reset rule below it. While it is set, that
    rule fires on every tick and the integral is wiped before it can accumulate,
    so the loop has no way to remove a standing offset. A chamber that steps
    over a narrow band on its way up -- 20 F per tick here, so the closest
    approach either side of the set point is 5 F -- would latch `new_target` for
    the rest of the cook and park at that offset permanently. Measured on the
    MAK plant before this was fixed: two cooks differing only in starting
    temperature settled 8 F apart, one of them never once inside the band.
    """
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    # Every sample sits at least 5 F from the set point, so `abs(error) <= 3`
    # is never satisfied at any point on the approach.
    for temp in (200.0, 220.0, 230.0):
        clock.t += 20.0
        sp.update(temp)
        assert abs(temp - 225.0) >= 5.0

    assert not sp.new_target


def test_the_integral_accumulates_once_the_set_point_has_been_crossed(clock):
    """The consequence of the latch, pinned on the accumulator itself.

    While `new_target` is set the reset fires every tick, so the accumulator
    only ever holds the single tick added after it -- it cannot grow, whatever
    the error is. Past the crossing it integrates, which is what lets the loop
    pull out a standing offset. Asserted as growth between ticks rather than an
    absolute value, because the approach leaves it wherever it leaves it.
    """
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    for temp in (200.0, 220.0, 230.0):
        clock.t += 20.0
        sp.update(temp)

    clock.t += 20.0
    sp.update(233.0)
    after_one = sp.inter
    for _ in range(2):
        clock.t += 20.0
        sp.update(233.0)

    # Two further ticks at +8 F over 20 s each.
    assert sp.inter - after_one == pytest.approx(2 * 8.0 * 20.0)


@pytest.mark.parametrize("bias_from_model", [True, False], ids=["bias", "integral_seed"])
def test_the_identified_hold_duty_becomes_the_loops_zero_error_output(clock, bias_from_model):
    """`center` is where the loop sits at zero error and is a heuristic -- 0.225
    at a 225 F set point, where the grill holds near 0.07. Either route puts the
    loop at the identified duty instead; what differs is when.

    The seed can only fire inside the stable window, because outside it the
    integral reset wipes it on the same update that places it -- so on the
    approach, where the overshoot is made, the loop is still running against
    0.225. Supplying it as the proportional bias covers the whole climb, which
    is worth 7.6 F of overshoot down to 2.6 F at 225 F on MAKGrillSim.
    """
    config = {**CONFIG, "bias_from_model": bias_from_model}
    import controller.pid_sp as mod

    sp = mod.Controller(config, "F", dict(CYCLE_DATA))
    sp.set_target(225.0)
    held = 0.07
    sp.identifier.hold_duty = lambda u_max=1.0, target_f=None: held

    # Inside the stable window, so the seeding route is reachable at all.
    clock.t += 20.0
    sp.update(220.0)

    # u = bias + ki*inter + kd*derv, so this is the output at zero error.
    assert sp._bias() + sp.ki * sp.inter == pytest.approx(held)
    assert sp._integral_seeded
    assert sp.feed_forward == pytest.approx(held)


def test_the_integral_seed_is_a_seed_and_not_a_control_law(clock):
    """Placed once; after that the accumulator is the loop's own correction and
    overwriting it would discard what it exists to make."""
    config = {**CONFIG, "bias_from_model": False}
    import controller.pid_sp as mod

    sp = mod.Controller(config, "F", dict(CYCLE_DATA))
    sp.set_target(225.0)
    held = 0.07
    sp.identifier.hold_duty = lambda u_max=1.0, target_f=None: held

    clock.t += 20.0
    sp.update(220.0)
    assert sp.inter == pytest.approx((held - sp.center) / sp.ki)

    sp.inter = 0.0
    clock.t += 20.0
    sp.update(222.0)
    assert sp.inter != pytest.approx((held - sp.center) / sp.ki)


def test_no_identified_hold_duty_leaves_the_integral_alone(clock):
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    sp.identifier.hold_duty = lambda u_max=1.0, target_f=None: None

    clock.t += 20.0
    sp.update(220.0)

    assert not sp._integral_seeded
    assert sp.feed_forward == sp.center


def test_set_output_feeds_the_identifier_as_well_as_the_predictor(clock):
    """Dropping predictor.record_output is caught by the divergence tests
    above; dropping identifier.record_output is not caught anywhere else.
    Without duty history the excitation gate can never clear, so a fresh
    install would silently stay a plain PID forever -- no error, no
    diagnostic, exactly the failure mode this plan exists to prevent."""
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    sp.set_output(AppliedOutput(0.9, OutputSource.CONTROLLER, clock.t))
    clock.t += 20.0
    sp.update(200.0)
    assert sp.get_status()["identifier"]["duty_segments"] > 0


def test_a_celsius_install_scales_error_and_corrections_from_fahrenheit(clock):
    """_to_f / _from_f are new code this task introduced, and every other
    test constructs with units='F', so either could be replaced by the
    identity and nothing would fail. Runs the identical schedule in C and in
    F and checks the C instance's deltas are the F instance's deltas scaled
    by 5/9 -- the round trip a Celsius install's correctness rests on."""
    sp_c = _controller("pid_sp", clock, units="C")
    sp_f = _controller("pid_sp", clock, units="F")
    sp_c.set_target(107.0)
    sp_f.set_target(107.0 * 9 / 5 + 32)
    clock.t += 20.0
    sp_c.update(100.0)
    sp_f.update(100.0 * 9 / 5 + 32)
    error_c = sp_c.get_status()["error"]
    error_f = sp_f.get_status()["error"]
    assert error_c == pytest.approx(error_f * 5 / 9)

    model = {"K": 800.0, "tau": 600.0, "theta": 40.0, "revision": 1}
    sp_c.restore_model(model)
    sp_f.restore_model(model)
    sp_c.set_output(AppliedOutput(0.9, OutputSource.CONTROLLER, clock.t))
    sp_f.set_output(AppliedOutput(0.9, OutputSource.CONTROLLER, clock.t))
    clock.t += 20.0
    sp_c.update(100.0)
    sp_f.update(100.0 * 9 / 5 + 32)
    before_c = sp_c.get_status()["selected_temp"]
    before_f = sp_f.get_status()["selected_temp"]
    clock.t += 20.0
    sp_c.update(100.0)
    sp_f.update(100.0 * 9 / 5 + 32)
    after_c = sp_c.get_status()["selected_temp"]
    after_f = sp_f.get_status()["selected_temp"]
    correction_c = after_c - before_c
    correction_f = after_f - before_f
    assert correction_f != 0.0, "the predictor is not correcting; the test proves nothing"
    assert correction_c == pytest.approx(correction_f * 5 / 9)


import json

from common.controller_model_state import MAX_SNAPSHOT_BYTES, ControllerModelStore


class _FakeBlobs:
    def __init__(self):
        self.blobs = {}

    def read(self, key):
        # An absent key raises TypeError, matching the real reader's contract
        # (controller/runtime/store.py:read_generic_key): ControllerModelStore
        # catches precisely TypeError to mean "nothing stored yet".
        return json.loads(self.blobs[key]) if key in self.blobs else json.loads(None)

    def write(self, key, value):
        self.blobs[key] = json.dumps(value)


def test_a_snapshot_survives_the_store_round_trip(clock):
    sp = _controller("pid_sp", clock)
    sp.restore_model({"K": 800.0, "tau": 600.0, "theta": 40.0, "revision": 3})
    blobs = _FakeBlobs()
    store = ControllerModelStore(reader=blobs.read, writer=blobs.write)

    assert store.save("pid_sp", sp.get_model_snapshot()) is True

    fresh = _controller("pid_sp", clock)
    assert fresh.get_model_snapshot() is None
    assert fresh.restore_model(store.load("pid_sp")) is True
    assert fresh.get_model_snapshot() == {
        "form": "fopdt",
        "K": 800.0,
        "tau": 600.0,
        "theta": 40.0,
        "revision": 3,
        "setpoint_f": 0.0,
    }


def test_the_snapshot_round_trips_setpoint_f(clock):
    """setpoint_f is stamped from the controller's own target at snapshot
    time, via the same _to_f every other temperature in this module uses."""
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    sp.restore_model({"K": 800.0, "tau": 600.0, "theta": 40.0, "revision": 3})
    assert sp.get_model_snapshot()["setpoint_f"] == 225.0


def test_setpoint_f_is_stamped_in_fahrenheit_regardless_of_install_units(clock):
    sp = _controller("pid_sp", clock, units="C")
    sp.set_target(100.0)  # 100 C == 212 F
    sp.restore_model({"K": 800.0, "tau": 600.0, "theta": 40.0, "revision": 3})
    assert sp.get_model_snapshot()["setpoint_f"] == pytest.approx(212.0)


def test_restore_does_not_refuse_on_a_setpoint_f_mismatch(clock):
    """setpoint_f is provenance only -- restore() must not gate on it, even
    when it plainly does not match the controller's current target. A K/tau
    fit near one setpoint is still a reasonable starting estimate at another;
    relaxing the bar to STOP trusting a model is safe, relaxing it to START
    trusting is not, so this is deliberately not a refusal condition."""
    sp = _controller("pid_sp", clock)
    sp.set_target(600.0)  # wildly different from the snapshot's provenance
    assert sp.restore_model({"K": 800.0, "tau": 600.0, "theta": 40.0, "revision": 3, "setpoint_f": 225.0}) is True
    assert sp.get_model_snapshot()["K"] == 800.0


def test_a_restored_model_is_active_on_the_first_tick(clock):
    """From the second cook onward there is no hour of plain PID."""
    blobs = _FakeBlobs()
    store = ControllerModelStore(reader=blobs.read, writer=blobs.write)
    store.save("pid_sp", {"K": 800.0, "tau": 600.0, "theta": 40.0, "revision": 3})

    sp = _controller("pid_sp", clock)
    sp.restore_model(store.load("pid_sp"))
    sp.set_target(225.0)
    clock.t += 20.0
    sp.update(200.0)
    assert sp.get_status()["predictor"]["active"] is True


def test_an_integrating_model_survives_the_store(clock):
    """An integrating chamber is the form a real grill actually identifies, so
    the cook boundary has to carry it. It reached the predictor within a cook
    long before it could be persisted, which is why the FOPDT-shaped tests above
    could all pass while a real grill relearned its chamber every cook."""
    blobs = _FakeBlobs()
    store = ControllerModelStore(reader=blobs.read, writer=blobs.write)
    identified = {"form": "ipdt", "K_i": 0.46, "c0": -0.033, "theta": 90.0, "revision": 3}
    store.save("pid_sp", identified)

    sp = _controller("pid_sp", clock)
    assert sp.restore_model(store.load("pid_sp")) is True
    sp.set_target(225.0)
    clock.t += 20.0
    sp.update(200.0)
    assert sp.get_status()["predictor"]["active"] is True
    assert sp.get_model_snapshot()["K_i"] == 0.46


def test_the_snapshot_satisfies_the_store_s_envelope_rules(clock):
    sp = _controller("pid_sp", clock)
    sp.restore_model({"K": 800.0, "tau": 600.0, "theta": 40.0, "revision": 3})
    snapshot = sp.get_model_snapshot()
    encoded = json.dumps(snapshot, allow_nan=False)
    assert len(encoded.encode("utf-8")) <= MAX_SNAPSHOT_BYTES
    # bool is an int subclass, and the store rejects it separately, so an
    # isinstance check alone would accept a revision the store refuses.
    assert isinstance(snapshot["revision"], int) and not isinstance(snapshot["revision"], bool)


def test_an_untrusted_controller_offers_nothing_to_persist(clock):
    assert _controller("pid_sp", clock).get_model_snapshot() is None


def test_get_status_projects_one_identifier_and_predictor_snapshot(clock, monkeypatch):
    sp = _controller("pid_sp", clock)
    identifier = {
        "accepted": 0,
        "accepted_seconds": 0.0,
        "duty_std": 0.0,
        "temp_span": 0.0,
        "transition_seen": False,
        "duty_segments": 0,
        "best_residual": 0.0,
        "runner_up_residual": 0.0,
        "candidates_passing": 0,
        "confirming": None,
        "trusted": None,
        "distrust_count": 0,
        "distrust_ratio": 0.0,
    }
    predictor = {
        "active": False,
        "disabled": False,
        "x0": 0.0,
        "xd": 0.0,
        "residual_streak": 0,
        "truncated": 0,
        "model": None,
    }
    calls = {"identifier": 0, "predictor": 0}

    def identifier_status():
        calls["identifier"] += 1
        return identifier

    def predictor_status():
        calls["predictor"] += 1
        return predictor

    monkeypatch.setattr(sp.identifier, "status", identifier_status)
    monkeypatch.setattr(sp.predictor, "status", predictor_status)

    status = sp.get_status()

    assert calls == {"identifier": 1, "predictor": 1}
    assert status["identifier"] is identifier
    assert status["predictor"] is predictor
    assert status["learning"] == {
        "schema_version": 1,
        "controller": "pid_sp",
        "status": "collecting",
        "identifier": identifier,
        "predictor": predictor,
        "gates": [
            {
                "name": "accepted_samples",
                "passed": False,
                "observed": 0,
                "required": 25,
                "unit": "samples",
            },
            {
                "name": "accepted_duration",
                "passed": False,
                "observed": 0.0,
                "required": 500.0,
                "unit": "seconds",
            },
            {
                "name": "duty_standard_deviation",
                "passed": False,
                "observed": 0.0,
                "required": 0.05,
                "unit": "ratio",
            },
            {
                "name": "duty_transition",
                "passed": False,
                "observed": False,
                "required": True,
                "unit": None,
            },
            {
                "name": "temperature_span",
                "passed": False,
                "observed": 0.0,
                "required": 15.0,
                "unit": "°F",
            },
        ],
    }


def test_get_status_survives_the_mqtt_encoder(clock):
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    clock.t += 20.0
    sp.update(200.0)
    json.dumps(sp.get_status(), allow_nan=False)
