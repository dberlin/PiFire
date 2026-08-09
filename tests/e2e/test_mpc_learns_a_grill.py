"""Successive cooks on a grill the controller has never seen must get better.

This is the acceptance test the online-identification slice exists to pass. It
drives the whole pipeline closed-loop -- cook, refit, promotion gate, adopt,
cook again -- against `MAKGrillSim`, which carries a real MAK's identified
parameters (C_c=3115.9, H=958.8, 100 s deadtime; fitted RMSE 2.3 C, peak 519 F
against 520 F measured). That plant is a chamber roughly ten times slower than
the shipped `_DEFAULTS` model, and it is the grill that started this work.

WHY 450 F. The incident was a 450 F Hold that overshot by ~70 F, and the MAK's
parameters were identified from that very cook, so 450 F is the one setpoint at
which this plant is being used inside the range it was measured over. It is
also where the slice's braking-distance work has to earn its keep: a coast that
starts from a hotter chamber is longer, so a controller that plans with the
wrong thermal mass has further to be wrong. Task A9a measured the same pipeline
at 325 F (peak 407.7 -> 367.4 F, overshoot -49%, 12 of 18 promotions accepted)
because that is what it was asked for; those numbers anchor the *shape* expected
here -- a large fall, concentrated in the first promotion -- but they are not
imported as thresholds, because a 450 F cook is a different operating point.

THE ACTUATOR FLOOR. `u_min` is the least fuel the auger is allowed to burn, so
it fixes a temperature the chamber cannot be driven below no matter what the
controller does -- on this plant, 435 F at the experiment harness's `u_min=0.15`
against 342 F at the 0.1 the product ships. Below that floor "overshoot" is a
reading of the actuator and every controller looks identical, so these tests run
the shipped `cycle_data` rather than the harness's, and assert the setpoint
clears the floor by more than the fall they claim to measure.

WHAT IS DELIBERATELY NOT DONE HERE. `n_horizon` is never raised by hand. A14
derives the effective horizon at build time from the model's own braking
distance and deliberately does not store it, because a stored horizon is a
ratchet: it can only grow, and a later, faster model could never bring it down.
What the test asserts is that no adopted model smuggles an `n_horizon` into the
carried config, which is how that ratchet would come back. It does not check the
derived value itself: on this plant every model's braking distance (150 s at the
defaults, 352 s learned) sits well under the 600 s the configured horizon
already covers, so the derivation returns the configured 24 steps for everything
these cooks can produce, and an assertion on it could not fail.

HOW IDENTIFICATION IS SWITCHED ON. Not by the `enable_identification` config
key: that is a settings-surface flag consumed on Hold's teardown path
(`hold.py:467`), gating whether the runner is asked to refit at all --
`controller/mpc.py` has never heard of it, which
`test_identification_off_is_invisible` asserts directly so that neither arm
below can be silently the same arm. What runs a refit is calling
`Controller.refit_from_cook()`, which fits the cook's own history, asks
`model_promotion.evaluate` for a verdict, and adopts only on acceptance. The
harness calls it once per cook, after the run has already been scored, so a
refit never touches the run it is measured on -- exactly as production does it,
where an adopted model reaches the grill through the NEXT cook's build.
`tests/unit/runtime/test_hold_refit_trigger.py` pins the flag end of that seam;
these tests pin the closed-loop end.

A REFUSAL IS AN OUTCOME, NOT A HANG. A12's identifiability floor refuses a cook
that does not determine the model however well it fits, and the gate refuses a
candidate that cannot beat the incumbent by the required margin. Every verdict
is captured and carried into the assertion messages, so a run that stops
improving says which gate stopped it.

These tests are marked `slow`: they run five three-and-a-half-hour cooks in
simulation and take minutes, not seconds.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import docs.superpowers.experiments.controller_matrix as controller_matrix
from common.defaults import default_settings
from controller.mpc import _DEFAULTS, Controller

MODEL_KEYS = Controller._MODEL_PARAM_KEYS

SCENARIO = controller_matrix.SCENARIOS["steady_450"]
SETPOINT_F = SCENARIO.setpoints[0][1]
PLANT = "MAKGrillSim"
SEED = 1
COOKS = 3


#: The cycle data the product ships. `u_min` is the auger's floor, and a floor
#: sets a temperature the chamber cannot be driven below however the controller
#: behaves -- on this plant, a setpoint near that floor would make "overshoot"
#: a reading of the actuator rather than of the controller.
def _shipped_cycle_data():
    settings = default_settings()["cycle_data"]
    return {key: settings[key] for key in ("u_min", "u_max", "PMode")}


#: Long enough past this plant's ~2 h time constant that the chamber has
#: stopped moving; the floor below is an equilibrium, not a transient.
_EQUILIBRIUM_S = 100_000

#: The fall this asserts must be worth having. A9a measured 40.3 F on this
#: plant at 325 F and a seed-to-seed spread far below that; a quarter of it is
#: comfortably outside the noise while still being a real improvement rather
#: than a rounding win, which is what an assertion that only demanded `<` would
#: accept.
MIN_PEAK_DROP_F = 10.0


def _cook(config, *, refit):
    """One cook, scored, with the end-of-cook refit run afterwards if asked.

    `config` is the model the previous cook got promoted, merged over the
    shipped defaults when the core is built.

    That is NOT the path production takes. Production persists the promoted
    model to the store and the next Hold builds its core from settings alone,
    then hands the snapshot to `Controller.restore_model`, which rebuilds the
    estimator, the horizon and the policy against it. Both routes end with the
    learned model built into the objects that solve, which is what this test
    measures; the seam between them is pinned by
    tests/unit/mpc/test_mpc_model_snapshot.py's
    test_a_restored_model_reaches_the_estimator_the_horizon_and_the_solve, and
    a cook here would not notice if the restore stopped carrying it.
    """
    row = controller_matrix.run_scenario(
        "mpc", SCENARIO, SEED, plant=PLANT, config=dict(config), cycle_config=_shipped_cycle_data(), refit=refit
    )
    row["peak_temp_f"] = SETPOINT_F + row["overshoot_f"]
    return row


def _min_firing_equilibrium_f(fan):
    """Where the plant settles with the auger pinned at its floor.

    No controller can hold below this, because it is what the grill does with
    the least fuel it is allowed to burn. It is the number that decides whether
    a setpoint measures the controller or measures the actuator.
    """
    plant = controller_matrix.MAKGrillSim(seed=SEED)
    for _ in range(_EQUILIBRIUM_S):
        plant.step(_shipped_cycle_data()["u_min"], fan)
    return plant.T_c * 9 / 5 + 32


def _verdict_lines(verdicts):
    return "\n".join(
        f"  cook {i}: accepted={v['accepted']} rmse={v['rmse']} :: {v['reason']}" for i, v in enumerate(verdicts, 1)
    )


@pytest.mark.slow
def test_overshoot_falls_across_successive_cooks():
    """Three cooks from the shipped defaults, each offering its refit to the gate."""
    # Cheap pre-flight: a setpoint at or under the auger's floor makes the whole
    # run meaningless, and it costs ten minutes to find that out from the cooks.
    # The real check is against the measured fall, after the cooks, below.
    floor_f = max(_min_firing_equilibrium_f(0.0), _min_firing_equilibrium_f(1.0))
    authority_f = SETPOINT_F - floor_f
    assert authority_f > MIN_PEAK_DROP_F, (
        f"the {SETPOINT_F:.0f} F setpoint is only {authority_f:.1f} F above this plant's "
        f"minimum-firing equilibrium ({floor_f:.1f} F at u_min={_shipped_cycle_data()['u_min']}), so overshoot "
        "here reads the actuator floor rather than the controller"
    )

    config, rows, verdicts = {}, [], []
    for _ in range(COOKS):
        row = _cook(config, refit=True)
        verdict = row["refit"]
        rows.append(row)
        verdicts.append(verdict)

        # A14 keeps the horizon derived at build time and deliberately does not
        # store it, because a stored horizon is a ratchet: it can only grow, and
        # a later, faster model could never bring it down. Promotion carrying an
        # `n_horizon` across is exactly how that ratchet would reappear.
        assert "n_horizon" not in config, "an adopted model stored a horizon; that is the ratchet A14 removed"

        if verdict["accepted"]:
            config = dict(verdict["params"])

    peaks = [row["peak_temp_f"] for row in rows]
    fall_f = peaks[0] - peaks[-1]
    report = (
        f"peaks {[round(p, 1) for p in peaks]} F against a {SETPOINT_F:.0f} F setpoint\n"
        f"fall {fall_f:.1f} F, control authority {authority_f:.1f} F above a {floor_f:.1f} F floor\n"
        f"{_verdict_lines(verdicts)}"
    )
    # Visible on success too: which cooks were promoted and why one was refused
    # is the story of the run, and a run that stopped improving because the gate
    # started refusing should not have to be re-run to find that out.
    print(report)

    assert any(v["accepted"] for v in verdicts), f"the gate refused every cook, so nothing could improve:\n{report}"
    assert peaks[1] < peaks[0], f"the second cook did not improve on the first:\n{report}"
    for i in range(1, COOKS):
        assert peaks[i] <= peaks[i - 1], f"cook {i + 1} regressed against cook {i}:\n{report}"
    assert fall_f >= MIN_PEAK_DROP_F, (
        f"the peak fell by less than the {MIN_PEAK_DROP_F:.0f} F this claims to buy:\n{report}"
    )
    # The whole fall has to fit inside the range the controller actually had.
    # Sized to what was measured rather than to the 10 F floor above, because a
    # configuration can clear that floor and still spend most of the fall
    # arriving somewhere it could not have gone below (u_min=0.15 leaves 14.7 F
    # of authority here, and would pass the pre-flight while failing this).
    assert fall_f < authority_f, (
        f"the {fall_f:.1f} F fall is not smaller than the {authority_f:.1f} F of authority above the "
        f"{floor_f:.1f} F minimum-firing floor, so it is partly the actuator and not the controller:\n{report}"
    )


@pytest.mark.slow
def test_identification_off_is_invisible():
    """A grill that never identified behaves exactly as it does today.

    The learning arm's cook 1 is run first, so there is demonstrably a model to
    carry -- a control that forgets nothing proves nothing. The second cook is
    then run the way production runs it when `enable_identification` is off:
    Hold's teardown never asks for a refit, so nothing is persisted and the next
    cook builds from the shipped defaults again. Its numbers must be the first
    cook's, exactly.

    That the two arms are genuinely different arms is the other half, and it is
    checked at both ends: `mpc.py` ignores `enable_identification` entirely (so
    the flag cannot be what these arms differ by), and
    `test_overshoot_falls_across_successive_cooks` above shows that carrying the
    model -- the thing the flag gates -- changes the cook.

    The shipped default is asserted here too, and it is on. This test is what
    an operator gets by turning learning off, so it has to keep working after
    the default stopped being the thing it describes.
    """
    assert default_settings()["controller"]["config"]["mpc"]["enable_identification"] is True, (
        "identification is meant to ship on"
    )

    # The flag is not a controller option: a core built with it set is the same
    # core. If this ever stops holding, the negative control below is testing
    # the wrong switch and must be rewritten around the new one.
    flagged = Controller({"enable_identification": True}, "F", _shipped_cycle_data())
    plain = Controller({}, "F", _shipped_cycle_data())
    assert {k: flagged.cfg[k] for k in MODEL_KEYS} == {k: plain.cfg[k] for k in MODEL_KEYS}
    assert flagged.mpc.settings.n_horizon == plain.mpc.settings.n_horizon == flagged.cfg["n_horizon"]

    learned = _cook({}, refit=True)
    verdict = learned["refit"]
    assert verdict["accepted"], f"nothing was learned, so there is nothing to be invisible: {verdict['reason']}"
    assert {k: verdict["params"][k] for k in ("C_c", "theta", "K_Q")} != {
        k: _DEFAULTS[k] for k in ("C_c", "theta", "K_Q")
    }, "the promoted model is the shipped one; the control below would be vacuous"

    forgetful = _cook({}, refit=False)
    assert "built_n_horizon" not in forgetful
    assert "built_n_horizon" not in learned
    for key in ("overshoot_f", "undershoot_f", "iae", "pct_within_5f", "settle_s", "configured_n_horizon"):
        assert forgetful[key] == learned[key], (
            f"{key} moved without identification: {forgetful[key]} against {learned[key]}"
        )
