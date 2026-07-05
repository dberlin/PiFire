# controller/runtime/context.py
"""Bundle of everything a control cycle needs. Passed instead of globals."""
from dataclasses import dataclass


@dataclass
class Devices:
	grill_platform: object
	probe_complex: object
	dist_device: object


@dataclass
class ControllerContext:
	devices: object            # Devices
	store: object              # Store
	notifications: object      # Notifier
	clock: object              # Clock
	event_log: object = None
	control_log: object = None
