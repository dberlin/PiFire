from dataclasses import dataclass

from pydantic import BaseModel
from .core import (
    ApiEnvelope,
    CommandResponse,
    CommandResponseData,
    ControlHealthResponse,
    ControlHealthTimeoutData,
    DashSocketPayload,
    DismissWarningsRequest,
    DismissWarningsResponse,
    EmptyResponseData,
    MpcCalibrationCommandPayload,
    MpcCalibrationCommandResponse,
    MpcCalibrationCommandResponseData,
    OutputPayload,
    PelletCurrentPayload,
    PelletDatabasePayload,
    PelletLastUpdatedPayload,
    PelletLogEntryPayload,
    PelletProfilePayload,
    PelletSocketPayload,
    ProbeDataPayload,
    ProbeStatusPayload,
    RecipeStatusPayload,
    TimerPayload,
    WebUiBuildResponse,
)


@dataclass(frozen=True, slots=True)
class ContractBundle:
    name: str
    models: tuple[type[BaseModel], ...]
    typescript_output: str


# Keep this explicit and sorted by bundle name. Contract additions must be
# reviewable registrations rather than side effects of module discovery.
WEB_CONTRACT_BUNDLES: tuple[ContractBundle, ...] = (
    ContractBundle(
        name="core",
        models=(
            ApiEnvelope,
            CommandResponse,
            CommandResponseData,
            ControlHealthResponse,
            ControlHealthTimeoutData,
            DashSocketPayload,
            DismissWarningsRequest,
            DismissWarningsResponse,
            EmptyResponseData,
            MpcCalibrationCommandPayload,
            MpcCalibrationCommandResponse,
            MpcCalibrationCommandResponseData,
            OutputPayload,
            PelletCurrentPayload,
            PelletDatabasePayload,
            PelletLastUpdatedPayload,
            PelletLogEntryPayload,
            PelletProfilePayload,
            PelletSocketPayload,
            ProbeDataPayload,
            ProbeStatusPayload,
            RecipeStatusPayload,
            TimerPayload,
            WebUiBuildResponse,
        ),
        typescript_output="core.gen.ts",
    ),
)
