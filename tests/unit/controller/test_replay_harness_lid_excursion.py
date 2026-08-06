"""Pins the net-versus-NLP replay's lid window to one Hold's detector fires on.

The replay's lid-window figures gate a shipping decision (the plan's Task 14
Step 4), so the window has to be one production can reach: a chamber losing heat
for as long as the lid is open, with the actuators surrendered for only
`LidOpenPauseTime` of that. Two things can each make it unreachable, and they
fail in opposite directions -- a plant that leaks no heat leaves the chamber near
set point, while a pause held for the whole opening digs a trough *deeper* than
production and misrepresents how long control was given up. Depth and recovery
are therefore pinned together, each against its own negative control, because
either alone is satisfied by the failure the other catches.

A full replay is a 3 h NLP run at ~90 s per seed, too slow to repeat per
assertion. The fast tests below check the window arithmetic against the
harness's own `_lid_windows`, and the thermal excursion by driving the real
`GrillSim` through the schedule that helper produces. They measure a related but
distinct quantity from the artifact's: they run a hand-rolled proportional law
off the noiseless `true_Tc`, where the harness's `lid_min_temp_f` comes from
`measured()` under the MPC. So they pin `GrillSim`'s lid response near the
replay's operating point rather than the replay's own response.

Only the `slow` group drives `replay()` itself. Everything the fast tests assert
is reconstructed from `_lid_windows` rather than read out of the harness, so a
`replay()` that stopped honouring the split -- or stopped opening the lid on the
plant at all -- would leave every one of them green. The `slow` group reads the
plant's recorded actuator sequence and the row's own recovery figure, which is
what makes it worth its 90 seconds.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

import docs.superpowers.experiments.net_vs_nlp_replay as replay_mod
from common.defaults import default_settings
from controller.applied_output import AppliedOutput, OutputSource
from controller.grill_sim import GrillSim
from controller.mpc import Controller
from controller.runtime.logic.pulse import PulseResetReason, PulseScheduler

LID_OPEN_AT = 2 * 3600
LID_OPEN_FOR = 120
SETPOINT_F = 225.0
THRESHOLD_PCT = default_settings()["cycle_data"]["LidOpenThreshold"]
# hold.py:241's condition, spelled the same way.
TRIGGER_F = SETPOINT_F * ((100 - THRESHOLD_PCT) / 100)
SEEDS = (0, 1, 2)
# The release must happen on the configured pause timer rather than at the
# physical lid close. The current framed replay recovers at 198 s; extending
# the pause through the 120 s physical window recovers at 204 s, so this bound
# separates the production path while leaving a small measurement margin.
MAX_RECOVERY_S = 200


def test_the_actuator_pause_is_shorter_than_the_lid_is_open():
    """If these were equal the split would be vacuous and every assertion below
    would hold for the wrong reason."""
    assert replay_mod.LID_PAUSE_S < LID_OPEN_FOR


def test_the_recovery_bound_fits_inside_the_window_depth_is_measured_over():
    """Ties `LID_RECOVERY_WINDOW_S` to the recovery bound instead of leaving it
    an unmotivated constant: any recovery the bound below admits has to complete
    while `lid_min_temp_f` is still watching, or the trough that figure reports
    is an artifact of where the watch stopped rather than of the lid."""
    assert MAX_RECOVERY_S <= LID_OPEN_FOR + replay_mod.LID_RECOVERY_WINDOW_S


def test_the_actuators_resume_while_the_lid_is_still_open():
    """The interval that does not exist while one flag drives both windows."""
    at_resume = replay_mod._lid_windows(LID_OPEN_AT + replay_mod.LID_PAUSE_S, LID_OPEN_AT, LID_OPEN_FOR)

    assert at_resume == (True, False), (
        f"the lid must still be open once the actuator pause expires; got (lid, lid_paused)={at_resume}"
    )


def test_both_windows_open_together_and_the_lid_closes_last():
    assert replay_mod._lid_windows(LID_OPEN_AT, LID_OPEN_AT, LID_OPEN_FOR) == (True, True)
    assert replay_mod._lid_windows(LID_OPEN_AT - 1, LID_OPEN_AT, LID_OPEN_FOR) == (False, False)
    assert replay_mod._lid_windows(LID_OPEN_AT + LID_OPEN_FOR, LID_OPEN_AT, LID_OPEN_FOR) == (False, False)


def _coldest_reading_f(seed, h_lid=None):
    """Drive the plant through the replay's framed lid schedule and return the
    coldest chamber reading over the window and the recovery that follows it."""
    kwargs = {"seed": seed} if h_lid is None else {"seed": seed, "h_lid": h_lid}
    plant = GrillSim(**kwargs)
    scheduler = PulseScheduler()
    actual_auger_on = False
    set_c = (SETPOINT_F - 32.0) / 1.8

    def _requested_duty():
        return min(max(0.02 * (set_c - plant.true_Tc), 0.0), replay_mod.CYCLE_DATA["u_max"])

    readings = []
    end = LID_OPEN_AT + LID_OPEN_FOR + replay_mod.LID_RECOVERY_WINDOW_S
    for t in range(end):
        lid, lid_paused = replay_mod._lid_windows(t, LID_OPEN_AT, LID_OPEN_FOR)
        if lid_paused and t == LID_OPEN_AT:
            scheduler.advance(_requested_duty(), float(t), actual_auger_on)
            scheduler.reset(PulseResetReason.LID)
            actual_auger_on = False
        elif lid_paused:
            actual_auger_on = False
        else:
            decision = scheduler.advance(_requested_duty(), float(t), actual_auger_on)
            actual_auger_on = decision.command_on
        reading = plant.step(
            auger_on=float(actual_auger_on),
            fan_frac=0.0 if lid_paused else 1.0,
            lid_open=lid,
        )
        if t >= LID_OPEN_AT:
            readings.append(reading)
    return min(readings) * 1.8 + 32.0


def test_applied_feedback_liveness_uses_a_nonzero_normalized_measurement():
    probe = Controller({"policy": "nlp"}, "F", dict(replay_mod.CYCLE_DATA))
    before = probe._applied_combustion_load

    assert before == 0.0
    probe.set_output(AppliedOutput(probe.u_max, OutputSource.CONTROLLER, 0.0))
    assert probe._applied_combustion_load == 1.0
    assert replay_mod._split_is_live(replay_mod.CYCLE_DATA)


def test_coldest_reading_helper_resets_and_starts_fresh_after_lid_pause(monkeypatch):
    schedulers = []
    real_scheduler = PulseScheduler

    class _RecordingScheduler(real_scheduler):
        def __init__(self, *args, **kwargs):
            self.advanced_at = []
            self.resets = []
            super().__init__(*args, **kwargs)
            schedulers.append(self)

        def advance(self, request, at_s, actual_auger_on):
            decision = super().advance(request, at_s, actual_auger_on)
            self.advanced_at.append((at_s, decision))
            return decision

        def reset(self, reason):
            self.resets.append(reason)
            return super().reset(reason)

    monkeypatch.setattr(sys.modules[__name__], "PulseScheduler", _RecordingScheduler)
    _coldest_reading_f(seed=0)
    scheduler = schedulers[0]
    pause_end = LID_OPEN_AT + replay_mod.LID_PAUSE_S
    release = next(decision for at_s, decision in scheduler.advanced_at if at_s == pause_end)

    assert scheduler.resets == [PulseResetReason.LID]
    assert not any(LID_OPEN_AT < at_s < pause_end for at_s, _ in scheduler.advanced_at)
    assert release.reset_reason is PulseResetReason.LID
    assert release.frame_start_s == pause_end


def test_the_lid_window_crosses_the_lid_open_threshold():
    mins = [_coldest_reading_f(seed) for seed in SEEDS]

    assert all(t < TRIGGER_F for t in mins), (
        f"the replay's lid window must produce an excursion the lid detector would "
        f"fire on (below {TRIGGER_F:.2f} F at a {SETPOINT_F:.0f} F setpoint); "
        f"coldest readings were {mins}"
    )


def test_a_lidless_plant_misses_the_threshold():
    """Negative control for depth, on the one parameter that carries the lid's
    heat loss: `h_lid=0.0` removes the `h_amb += self.h_lid` term and nothing
    else, so both branches still take the same `plant.step(..., lid_open=lid)`
    call path. It is a conservative control rather than an exact model of a
    lidless replay -- the fan is still cut for the pause, and fan-off *lowers*
    `h_amb`, so this loses marginally more heat than a lidless run would and the
    threshold it has to miss is that much harder to miss."""
    mins = [_coldest_reading_f(seed, h_lid=0.0) for seed in SEEDS]

    assert all(t > TRIGGER_F for t in mins), (
        f"the no-heat-leak plant was expected to miss the {TRIGGER_F:.2f} F trigger, "
        f"so the assertion above measures the lid model; coldest readings were {mins}"
    )
    # The framed controller may re-enter its thermal band between pulses; the
    # relevant negative control is the detector's actual threshold above.


class _RecordingGrillSim(GrillSim):
    """The real plant, plus the actuator commands the harness drove it with.

    Physics is unmodified -- the row this produces is the harness's own output,
    not a reconstruction -- so the sequence and the thermal result below are
    measured on the same run.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.lid_opens = []
        self.fan_fracs = []
        self.auger_fracs = []

    def step(self, auger_on, fan_frac, lid_open=False):
        self.lid_opens.append(bool(lid_open))
        self.fan_fracs.append(float(fan_frac))
        self.auger_fracs.append(float(auger_on))
        return super().step(auger_on=auger_on, fan_frac=fan_frac, lid_open=lid_open)


@pytest.fixture(scope="module")
def real_replay():
    """One replay, shared by the assertions below.

    Everything the fast tests check is reconstructed from `_lid_windows`; these
    read what `replay()` actually did, which is the only place the two can be
    caught disagreeing.
    """
    plants = []
    controllers = []
    schedulers = []
    real_plant = replay_mod.GrillSim
    real_controller = replay_mod.Controller
    real_scheduler = replay_mod.PulseScheduler

    class _RecordingController(real_controller):
        def __init__(self, *args, **kwargs):
            self.applied_outputs = []
            self.applied_loads_after_output = []
            self.applied_loads_at_update = []
            self.feedback_events = []
            super().__init__(*args, **kwargs)
            controllers.append(self)

        def set_output(self, output):
            result = super().set_output(output)
            self.applied_outputs.append(output)
            self.applied_loads_after_output.append(self._applied_combustion_load)
            self.feedback_events.append("output")
            return result

        def update(self, *args, **kwargs):
            self.applied_loads_at_update.append(self._applied_combustion_load)
            self.feedback_events.append("update")
            return super().update(*args, **kwargs)

    class _RecordingScheduler(real_scheduler):
        def __init__(self, *args, **kwargs):
            self.advances = []
            self.resets = []
            super().__init__(*args, **kwargs)
            schedulers.append(self)

        def advance(self, request, at_s, actual_auger_on):
            decision = super().advance(request, at_s, actual_auger_on)
            self.advances.append(decision)
            return decision

        def reset(self, reason):
            self.resets.append(reason)
            return super().reset(reason)

    def _recording(**kwargs):
        plant = _RecordingGrillSim(**kwargs)
        plants.append(plant)
        return plant

    replay_mod.GrillSim = _recording
    replay_mod.Controller = _RecordingController
    replay_mod.PulseScheduler = _RecordingScheduler
    try:
        row = replay_mod.replay(seed=0)
    finally:
        replay_mod.GrillSim = real_plant
        replay_mod.Controller = real_controller
        replay_mod.PulseScheduler = real_scheduler
    plant = plants[-1]
    controller = controllers[0]
    plant.applied_outputs = controller.applied_outputs
    plant.applied_loads_after_output = controller.applied_loads_after_output
    plant.applied_loads_at_update = controller.applied_loads_at_update
    plant.feedback_events = controller.feedback_events
    plant.scheduler = schedulers[0]
    return row, plant

@pytest.mark.slow
def test_the_real_replay_reports_a_lid_excursion(real_replay):
    """Depth, through the harness rather than through a reconstruction of its
    schedule."""
    row, _ = real_replay

    assert row["lid_min_temp_f"] < TRIGGER_F, (
        f"the replay's own lid window must cross {TRIGGER_F:.2f} F; lid_min_temp_f was {row['lid_min_temp_f']:.2f}"
    )


@pytest.mark.slow
def test_the_real_replay_recovers_on_the_pause_timer_not_the_lid_window(real_replay):
    """Width of the same excursion, and the only assertion here that a deeper
    trough cannot satisfy. The chamber cannot re-enter the 5 F band while the
    auger is pinned, so recovery outlasts the pause; it comes back well inside
    `MAX_RECOVERY_S` because Hold hands control back on the timer rather than
    when the lid shuts. A pause running the whole lid window recovers past that
    bound while digging a trough that passes the depth assertion above."""
    row, _ = real_replay
    recovery = row["lid_recovery_s"]

    assert recovery is not None and replay_mod.LID_PAUSE_S < recovery < MAX_RECOVERY_S, (
        f"recovery must fall between the {replay_mod.LID_PAUSE_S} s pause and {MAX_RECOVERY_S} s; got {recovery}"
    )




@pytest.mark.slow
def test_the_real_replay_reports_realized_feedback_at_every_solve(real_replay):
    """Each output arrives before its solve, and lid-pause outputs carry the
    zero delivery that the applied estimator consumes."""
    row, plant = real_replay
    pause_end = LID_OPEN_AT + replay_mod.LID_PAUSE_S
    paused_outputs = [
        output
        for output in plant.applied_outputs
        if LID_OPEN_AT < output.timestamp < pause_end
    ]

    assert len(plant.applied_outputs) == row["n"]
    assert plant.feedback_events == ["output", "update"] * row["n"]
    assert plant.applied_loads_after_output == plant.applied_loads_at_update
    assert paused_outputs
    assert all(output.source is OutputSource.LID_OPEN and output.ratio == 0.0 for output in paused_outputs)


@pytest.mark.slow
def test_the_lid_reset_releases_a_fresh_frame_without_pause_catchup(real_replay):
    _, plant = real_replay
    pause_end = LID_OPEN_AT + replay_mod.LID_PAUSE_S
    scheduler = plant.scheduler
    release = next(decision for decision in scheduler.advances if decision.frame_start_s == pause_end)

    assert scheduler.resets == [PulseResetReason.LID]
    assert not any(LID_OPEN_AT < decision.frame_start_s < pause_end for decision in scheduler.advances)
    assert release.reset_reason is PulseResetReason.LID
    assert release.frame_start_s == pause_end
@pytest.mark.slow
def test_the_real_replay_drives_the_two_windows_at_their_own_lengths(real_replay):
    """The sequence the plant was actually driven with. The lid stays open for
    the full physical window while the fan is cut for the pause only, so the
    third phase -- lid open, actuators released -- is exercised rather than
    skipped."""
    _, plant = real_replay
    lid_end = LID_OPEN_AT + LID_OPEN_FOR
    pause_end = LID_OPEN_AT + replay_mod.LID_PAUSE_S

    assert plant.lid_opens[LID_OPEN_AT:lid_end] == [True] * LID_OPEN_FOR, (
        "the plant was not told the lid was open for the whole physical window"
    )
    assert not any(plant.lid_opens[:LID_OPEN_AT])
    assert not any(plant.lid_opens[lid_end:])

    assert plant.fan_fracs[LID_OPEN_AT:pause_end] == [0.0] * replay_mod.LID_PAUSE_S, (
        "hold.py:263 cuts the fan at detection, so it must be off for the whole pause"
    )
    assert all(frac > 0.0 for frac in plant.fan_fracs[pause_end:lid_end]), (
        "the fan never restarted while the lid was still open -- the actuator pause is "
        "being modelled as lasting the whole lid window rather than LidOpenPauseTime"
    )
    assert all(frac > 0.0 for frac in plant.fan_fracs[:LID_OPEN_AT])
