from dataclasses import dataclass, field


@dataclass
class CycleState:
    """Auger duty-cycle timing for the current work cycle."""

    ratio: float = 0.0
    raw_ratio: float = 0.0
    on_time: float = 0.0
    off_time: float = 0.0
    cycle_time: float = 0.0


@dataclass
class ControllerState:
    """Hold's PID/MPC controller output and its cycle-update bookkeeping."""

    output: float = 0.0
    fan_duty: float | None = None
    controls_fan: bool = False
    cycle_start: float = 0.0


@dataclass
class FanState:
    """Fan-assist / smoke-plus fan cycling and PWM ramp state."""

    assist: bool = False
    pwm_ramping: bool = False
    cycle_toggle_time: float = 0.0
    update_time: float = 0.0


@dataclass
class LidState:
    """Lid-open detection state (Hold mode)."""

    open_detected: bool = False
    expires: float = 0.0


@dataclass
class StartupState:
    """Startup/Reignite smart-start timing."""

    timer: float = 0.0
    raw_temp: float = 0.0


@dataclass
class PrimeState:
    """Prime mode auger-run sizing."""

    duration: float = 0.0
    amount: float = 0.0


@dataclass
class Timers:
    """Loop-wide toggle timestamps, all set from the same start_time at
    pre-loop setup and advanced independently per tick."""

    start_time: float = 0.0
    auger_toggle: float = 0.0
    display_toggle: float = 0.0
    hopper_toggle: float = 0.0
    eta_toggle: float = 0.0
    temp_toggle: float = 0.0


@dataclass
class WorkCycleState:
    """Loop-local state for one work cycle, grouped by concern.

    Sub-objects group the flat fields by the part of the loop that owns
    them (auger cycle timing, controller output, fan, lid, startup, prime,
    toggle timers); `target_temp_achieved`, `manual_override`, and `metrics`
    are used broadly enough across modes that they stay top-level.
    """

    cycle: CycleState = field(default_factory=CycleState)
    controller: ControllerState = field(default_factory=ControllerState)
    fan: FanState = field(default_factory=FanState)
    lid: LidState = field(default_factory=LidState)
    startup: StartupState = field(default_factory=StartupState)
    prime: PrimeState = field(default_factory=PrimeState)
    timers: Timers = field(default_factory=Timers)
    target_temp_achieved: bool = False
    manual_override: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
