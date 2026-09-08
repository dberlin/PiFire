# controller/runtime/context.py
"""Bundle of everything a control cycle needs. Passed instead of globals."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from common.clock_domain import ClockStamp, RuntimeClockDomain
from common.persistence.learning_trajectory import LearningTrajectoryRepository
from common.persistence.protocols import ControllerStore
from controller.runtime.actuation_delivery import DeliveredGrillPlatform
from controller.runtime.clock import Clock
from controller.runtime.learning_trajectory import LearningTrajectoryRuntime
from controller.runtime.model_persistence import ModelPersistenceWorker

if TYPE_CHECKING:
    from controller.model_learning.grey_runtime import GreyLearningProcessOwner

#: The two operator-visible logger names. `create_logger` configures their
#: handlers, level and file once at process startup (control.py); everything
#: inside the controller runtime acquires them through the context below, so a
#: test can substitute either without touching global state.
EVENT_LOG_NAME = "events"
CONTROL_LOG_NAME = "control"


@dataclass
class Devices:
    grill_platform: DeliveredGrillPlatform
    probe_complex: object
    dist_device: object


@dataclass
class ControllerContext:
    devices: object  # Devices
    store: ControllerStore
    notifications: object  # Notifier
    clock: Clock
    #  Defaulted to the named loggers rather than None so every context carries
    #  a usable logger: no call site needs a None check or a try/except around a
    #  log call, and an un-injected context still reaches the operator's log
    #  files instead of silently discarding the message.
    event_log: object = field(default_factory=lambda: logging.getLogger(EVENT_LOG_NAME))
    control_log: object = field(default_factory=lambda: logging.getLogger(CONTROL_LOG_NAME))
    trajectory_repository: LearningTrajectoryRepository | None = None
    model_persistence: ModelPersistenceWorker | None = None
    grey_learning_process: GreyLearningProcessOwner | None = None
    learning_trajectory: LearningTrajectoryRuntime | None = None
    trajectory_next_effective_mode: str | None = None
    clock_domain: RuntimeClockDomain | None = None
    last_clock_stamp: ClockStamp | None = None
    cook_id: str | None = None
    cook_elapsed_seconds: float | None = None
    hopper_cooldowns: dict[tuple[str, str], float] = field(default_factory=dict)

    def admitted_stamp(self) -> ClockStamp:
        if self.last_clock_stamp is None:
            raise RuntimeError("Timer operations require an admitted control tick")
        return self.last_clock_stamp

    def get_clock_domain(self) -> RuntimeClockDomain:
        if self.clock_domain is None:
            self.clock_domain = RuntimeClockDomain.for_system(
                monotonic=lambda: self.clock.monotonic(),
                wall_time=lambda: self.clock.wall_time(),
            )
        return self.clock_domain
