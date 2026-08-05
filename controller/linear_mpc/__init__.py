"""Online linear-model learning primitives for PiFire controllers."""

from .arx import ScheduledARX, ScheduledARXConfig
from .contracts import AffinePrediction, FrameObservation, ModelUpdate
from .grey_box import GreyBoxPredictionAdapter
from .policy import LinearMPC, LinearMPCConfig, LinearSolve

__all__ = (
    "AffinePrediction",
    "FrameObservation",
    "GreyBoxPredictionAdapter",
    "LinearMPC",
    "LinearMPCConfig",
    "LinearSolve",
    "ModelUpdate",
    "ScheduledARX",
    "ScheduledARXConfig",
)
