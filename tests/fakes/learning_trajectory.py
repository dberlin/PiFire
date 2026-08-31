from collections.abc import Callable
from hashlib import sha256
from math import ceil

from common.learning_trajectory import LearningTrajectorySegment
from controller.model_learning.contracts import FrameObservation
from controller.mpc_model import EstimatorSeed
from controller.runtime.learning_trajectory import TrajectoryBoundary


class ExactEstimatorSeedSource:
    """Production-shaped trajectory source for tests outside trajectory capture."""

    def __init__(self) -> None:
        self.trace_session_id: str | None = None

    def estimator_seed_anchor(self) -> tuple[int, float] | None:
        return None

    def seed_for(
        self,
        theta: float,
        n_delay: int,
        at_ms: int,
        measured_temp_c: float,
    ) -> EstimatorSeed:
        del at_ms
        required = 0 if n_delay == 0 else min(180, ceil(3.0 * theta / 20.0))
        digest = sha256(f"hold-test-seed:{theta!r}:{n_delay}".encode()).hexdigest()
        return EstimatorSeed(
            delay_states=(0.0,) * n_delay,
            chamber_temperature_c=measured_temp_c,
            disturbance=0.0,
            segment_id="hold-test-segment",
            pre_roll_digest=digest,
            pre_roll_frame_count=required,
            required_frame_count=required,
            status="exact",
        )

    def bind_trace_session(
        self,
        session_id: str,
        cook_id: str | None,
        publish_segment: Callable[[LearningTrajectorySegment], bool],
        *,
        failure_handler: Callable[[str], None] | None = None,
    ) -> bool:
        del cook_id, publish_segment, failure_handler
        self.trace_session_id = session_id
        return True

    def mark_trace_unavailable(self, reason: str) -> None:
        del reason

    def intervention(self, boundary: TrajectoryBoundary) -> None:
        del boundary

    def configuration_changed(self, boundary: TrajectoryBoundary) -> None:
        del boundary

    def observe_hold_frame(self, observation: FrameObservation, *, replay_only: bool = False) -> None:
        del observation, replay_only

    def barrier(self, timeout: float = 2.0) -> bool:
        del timeout
        return True
