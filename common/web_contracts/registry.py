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
from .learning import (
    ActivationLifecycle,
    ActivationReport,
    ActiveModelReport,
    CalibrationReport,
    CandidateAssessment,
    CandidateReport,
    CookRefitReport,
    EvidenceGate,
    EvidenceSummary,
    FitReport,
    FitWindowIdentity,
    FopdtPidSpCheckpoint,
    FopdtPidSpPredictor,
    GreyParameters,
    IpdtPidSpCheckpoint,
    IpdtPidSpPredictor,
    LearningFailure,
    ModelActionRejected,
    ModelActivationAccepted,
    ModelActivationAcknowledgement,
    ModelActivationRequest,
    ModelEvidenceReport,
    ModelIdentities,
    ModelRollbackAccepted,
    ModelRollbackAcknowledgement,
    ModelRollbackRequest,
    MpcCalibrationCommand,
    MpcCalibrationCommandResponse,
    MpcCalibrationCommandResponseData,
    PidSpConfirmationProgress,
    PidSpIdentifierReport,
    PidSpLearningFailure,
    PidSpLearningGate,
    PidSpLearningReport,
    PidSpPredictorReport,
)
from .settings import (
    BoolControllerOption,
    ControllerCatalog,
    ControllerConfigs,
    ControllerDefinition,
    ControllerMetadata,
    FloatControllerOption,
    IntControllerOption,
    ListControllerOption,
    ModeResponse,
    MpcConfig,
    PidConfig,
    PidSpConfig,
    SaveFieldError,
    SettingsResponse,
    SettingsUpdateRequest,
    SettingsUpdateResponse,
    StringControllerOption,
)
from common.settings_schema import SettingsSchema


@dataclass(frozen=True, slots=True)
class ContractBundle:
    name: str
    models: tuple[type[BaseModel], ...]
    typescript_output: str


@dataclass(frozen=True, slots=True)
class RootContract:
    name: str
    model: type[BaseModel]
    schema_output: str
    typescript_output: str


# Keep this explicit and sorted by bundle name. Contract additions must be
# reviewable registrations rather than side effects of module discovery.
WEB_CONTRACT_BUNDLES: tuple[ContractBundle, ...] = (
    ContractBundle(
        name="controller",
        models=(
            BoolControllerOption,
            ControllerCatalog,
            ControllerConfigs,
            ControllerDefinition,
            ControllerMetadata,
            FloatControllerOption,
            IntControllerOption,
            ListControllerOption,
            ModeResponse,
            MpcConfig,
            PidConfig,
            PidSpConfig,
            SaveFieldError,
            SettingsResponse,
            SettingsUpdateRequest,
            SettingsUpdateResponse,
            StringControllerOption,
        ),
        typescript_output="../settings/controllerTypes.gen.ts",
    ),
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
    ContractBundle(
        name="learning",
        models=(
            ActivationLifecycle,
            ActivationReport,
            ActiveModelReport,
            CalibrationReport,
            CandidateAssessment,
            CandidateReport,
            CookRefitReport,
            EvidenceGate,
            EvidenceSummary,
            FitReport,
            FitWindowIdentity,
            FopdtPidSpCheckpoint,
            FopdtPidSpPredictor,
            GreyParameters,
            IpdtPidSpCheckpoint,
            IpdtPidSpPredictor,
            LearningFailure,
            ModelActionRejected,
            ModelActivationAccepted,
            ModelActivationAcknowledgement,
            ModelActivationRequest,
            ModelEvidenceReport,
            ModelIdentities,
            ModelRollbackAccepted,
            ModelRollbackAcknowledgement,
            ModelRollbackRequest,
            MpcCalibrationCommand,
            MpcCalibrationCommandResponse,
            MpcCalibrationCommandResponseData,
            PidSpConfirmationProgress,
            PidSpIdentifierReport,
            PidSpLearningFailure,
            PidSpLearningGate,
            PidSpLearningReport,
            PidSpPredictorReport,
        ),
        typescript_output="learning.gen.ts",
    ),
)


WEB_ROOT_CONTRACTS: tuple[RootContract, ...] = (
    RootContract(
        name="settings",
        model=SettingsSchema,
        schema_output="../settings.schema.json",
        typescript_output="../settings/settingsTypes.gen.ts",
    ),
)
