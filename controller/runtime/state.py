from dataclasses import dataclass, field


@dataclass
class WorkCycleState:
	"""Loop-local state for work cycle execution."""

	# Scalar float defaults
	cycle_ratio: float = 0.0
	raw_cycle_ratio: float = 0.0
	on_time: float = 0.0
	off_time: float = 0.0
	cycle_time: float = 0.0
	controller_output: float = 0.0
	lid_open_expires: float = 0.0
	prime_duration: float = 0.0
	prime_amount: float = 0.0
	startup_timer: float = 0.0
	raw_startup_temp: float = 0.0
	ptemp: float = 0.0

	# Optional default
	controller_fan_duty: float | None = None

	# Boolean defaults
	fan_assist: bool = False
	lid_open_detect: bool = False
	target_temp_achieved: bool = False
	pwm_fan_ramping: bool = False
	mpc_fan_active: bool = False

	# Toggle timestamps (all float 0.0)
	start_time: float = 0.0
	auger_toggle_time: float = 0.0
	display_toggle_time: float = 0.0
	fan_cycle_toggle_time: float = 0.0
	hopper_toggle_time: float = 0.0
	fan_update_time: float = 0.0
	eta_toggle_time: float = 0.0
	temp_toggle_time: float = 0.0
	controller_cycle_start: float = 0.0

	# Mutable defaults via field(default_factory=dict)
	manual_override: dict = field(default_factory=dict)
	metrics: dict = field(default_factory=dict)
