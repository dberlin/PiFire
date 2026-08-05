"""Online linear-model learning primitives for PiFire controllers."""

from .arx import ScheduledARX, ScheduledARXConfig
from .adaptation import (
    AdaptationPolicy,
    EvaluationDecision,
    ObservationOutcome,
    OnlineAdaptation,
    UpdateGate,
)
from .contracts import AffinePrediction, FrameObservation, ModelUpdate
from .grey_box import GreyBoxPredictionAdapter
from .policy import LinearMPC, LinearMPCConfig, LinearSolve

__all__ = (
    "AffinePrediction",
    "AdaptationPolicy",
    "FrameObservation",
    "EvaluationDecision",
    "GreyBoxPredictionAdapter",
    "LinearMPC",
    "LinearMPCConfig",
    "LinearSolve",
    "ModelUpdate",
    "ObservationOutcome",
    "OnlineAdaptation",
    "ScheduledARX",
    "ScheduledARXConfig",
    "UpdateGate",
)
