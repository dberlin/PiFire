from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from .content import (
    CookFileCommentAddRequest,
    CookFileCommentAssetsRequest,
    CookFileCommentDeleteRequest,
    CookFileCommentUpdateRequest,
    CookFileLabelRequest,
    CookFileRecoverRequest,
    CookFileThumbnailRequest,
    CookFileTitleRequest,
    CookFileChartData,
    CookFileDetail,
    EmptyContentRequest,
    FileAssetsRequest,
    FileRequest,
    FileListing,
    HistoryChartData,
    MetricsPayload,
    RecipeDetail,
    RecipeIndexedAssetAssignmentRequest,
    RecipeIngredientAddRequest,
    RecipeIngredientDeleteRequest,
    RecipeIngredientUpdateRequest,
    RecipeInstructionAddRequest,
    RecipeInstructionDeleteRequest,
    RecipeInstructionUpdateRequest,
    RecipeMetadataUpdateRequest,
    RecipeSplashAssetAssignmentRequest,
    RecipeStepDeleteRequest,
    RecipeStepInsertRequest,
    RecipeStepUpdateRequest,
)
from .control import (
    ControlPatchRequest,
    ControlPatchResponse,
    ManualOutputCommandRequest,
    ManualPwmCommandRequest,
    PelletActionRequest,
    PelletActionResponse,
    PrimeCommandRequest,
    SetModeCommandRequest,
    SetPModeCommandRequest,
    SetPrimarySetpointCommandRequest,
    SetSmokePlusCommandRequest,
    SetUnitsCommandRequest,
    SystemCommandRequest,
    TimerKeepWarmCommandRequest,
    TimerPauseCommandRequest,
    TimerShutdownCommandRequest,
    TimerStartCommandRequest,
    TimerStartWithOptionsCommandRequest,
    TimerStopCommandRequest,
    WledActionResponse,
    WledDiscoverResponse,
    WledPushProfilesRequest,
    WledTestProfileRequest,
)
from .core import (
    ApiEnvelope,
    CommandResponse,
    ControlHealthResponse,
    DashSocketPayload,
    DismissWarningsRequest,
    DismissWarningsResponse,
    PelletSocketPayload,
    WebUiBuildResponse,
)
from .learning import (
    ModelActivationAcknowledgement,
    ModelActivationRequest,
    ModelEvidenceReport,
    ModelRollbackAcknowledgement,
    ModelRollbackRequest,
    MpcCalibrationCommand,
    MpcCalibrationCommandResponse,
    PidSpLearningReport,
)
from .operations import (
    AdminSettingsUpdate,
    AdminState,
    AutoStatus,
    AutoStatusRequest,
    BackupCreateRequest,
    BackupCreated,
    BackupListing,
    BackupRestoreRequest,
    BackupRestored,
    BuildLog,
    Coefficients,
    CoefficientsRequest,
    EmptyOperationRequest,
    FactoryResetResponse,
    LogsDeleted,
    LogsMetadata,
    MaintenanceActionRequest,
    MaintenanceActionResponse,
    ProfileInput,
    SavedProfile,
    SystemActionRequest,
    SystemActionResponse,
    TrReading,
    TunerSession,
    TunerSessionRequest,
    UpdateBranchRequest,
    UpdateCheck,
    UpdateLog,
    UpdateStarted,
    UpdateState,
    UpdateStatus,
)
from .settings import ControllerCatalog, ModeResponse, SettingsResponse, SettingsUpdateRequest, SettingsUpdateResponse
from .wizard import (
    BtRowsResult,
    BusKindsValidationRequest,
    BusKindsValidationResponse,
    EmptyWizardRequest,
    InstallLog,
    InstallStatus,
    ModuleValues,
    ModuleValuesRequest,
    ProbeMapResponse,
    ProbeMapRequest,
    ProbeModuleCatalog,
    RowsResult,
    ScanRequest,
    ScanResult,
    ThermoworksRowsResult,
    ThermoworksRequest,
    WizardDraftRequest,
    WizardFinishRequest,
    WizardState,
    WizardActionResponse,
)


@dataclass(frozen=True, slots=True)
class JsonWebContract:
    transport: Literal["http", "socketio"]
    name: str
    request: type[BaseModel] | None
    response: type[BaseModel]
    bundle: str


@dataclass(frozen=True, slots=True)
class NonJsonWebTransport:
    name: str
    transport: Literal["browser", "http"]
    reason: str


def _http(
    name: str,
    response: type[BaseModel],
    bundle: str,
    request: type[BaseModel] | None = None,
) -> JsonWebContract:
    return JsonWebContract("http", name, request, response, bundle)


JSON_WEB_CONTRACT_INVENTORY: tuple[JsonWebContract, ...] = (
    # Core HTTP and Socket.IO payloads.
    _http("GET /api/webui", WebUiBuildResponse, "core"),
    _http("GET /api/sys/check_alive", ControlHealthResponse, "core"),
    _http("POST /api/dismiss_warnings", DismissWarningsResponse, "core", DismissWarningsRequest),
    JsonWebContract("socketio", "socket_dash_data", None, DashSocketPayload, "core"),
    JsonWebContract("socketio", "socket_pellet_data", None, PelletSocketPayload, "control"),
    # Command paths encode their validated request in path segments and return
    # the common command envelope.
    _http("POST /api/set/mode/<mode>", CommandResponse, "control", SetModeCommandRequest),
    _http("POST /api/set/psp/<temperature>", CommandResponse, "control", SetPrimarySetpointCommandRequest),
    _http("POST /api/set/splus/<enabled>", CommandResponse, "control", SetSmokePlusCommandRequest),
    _http("POST /api/set/pmode/<value>", CommandResponse, "control", SetPModeCommandRequest),
    _http("POST /api/set/mode/prime/<grams>[/<next_mode>]", CommandResponse, "control", PrimeCommandRequest),
    _http("POST /api/set/timer/start/<seconds>", CommandResponse, "control", TimerStartCommandRequest),
    _http(
        "POST /api/set/timer/start/<seconds>/<options>",
        CommandResponse,
        "control",
        TimerStartWithOptionsCommandRequest,
    ),
    _http("POST /api/set/timer/pause", CommandResponse, "control", TimerPauseCommandRequest),
    _http("POST /api/set/timer/stop", CommandResponse, "control", TimerStopCommandRequest),
    _http("POST /api/set/timer/shutdown/<enabled>", CommandResponse, "control", TimerShutdownCommandRequest),
    _http("POST /api/set/timer/keep_warm/<enabled>", CommandResponse, "control", TimerKeepWarmCommandRequest),
    _http("POST /api/cmd/<command>", CommandResponse, "control", SystemCommandRequest),
    _http("POST /api/set/units/<units>", CommandResponse, "control", SetUnitsCommandRequest),
    _http("POST /api/set/manual/<output>/<action>", CommandResponse, "control", ManualOutputCommandRequest),
    _http("POST /api/set/manual/pwm/<duty>", CommandResponse, "control", ManualPwmCommandRequest),
    # Control, pellets, and WLED.
    _http("POST /api/control", ControlPatchResponse, "control", ControlPatchRequest),
    _http("POST /api/pellets", PelletActionResponse, "control", PelletActionRequest),
    _http("GET /api/wled_discover", WledDiscoverResponse, "control"),
    _http("POST /api/wled_push_profiles", WledActionResponse, "control", WledPushProfilesRequest),
    _http("POST /api/wled_test_profile", WledActionResponse, "control", WledTestProfileRequest),
    # Settings and controller metadata. SettingsSchema itself remains the
    # registered root artifact used by SettingsResponse.settings.
    _http("GET /api/settings", SettingsResponse, "settings"),
    _http("GET /api/controller_metadata", ControllerCatalog, "controller"),
    _http("GET /api/get/mode", ModeResponse, "controller"),
    _http("POST /api/settings_update", SettingsUpdateResponse, "controller", SettingsUpdateRequest),
    # Learning reports and revision-fenced actions.
    _http("GET /api/model-evidence/report", ModelEvidenceReport, "learning"),
    _http("GET /api/pid-sp-learning/report", PidSpLearningReport, "learning"),
    _http(
        "POST /api/model-evidence/activate",
        ModelActivationAcknowledgement,
        "learning",
        ModelActivationRequest,
    ),
    _http(
        "POST /api/model-evidence/rollback",
        ModelRollbackAcknowledgement,
        "learning",
        ModelRollbackRequest,
    ),
    _http("POST /api/set_mpc_calibration", MpcCalibrationCommandResponse, "learning", MpcCalibrationCommand),
    # Wizard and live probe-map endpoints.
    _http("GET /api/wizard/state", WizardState, "wizard"),
    _http("POST /api/wizard/draft", WizardActionResponse, "wizard", WizardDraftRequest),
    _http("POST /api/wizard/cancel", WizardActionResponse, "wizard", EmptyWizardRequest),
    _http("POST /api/wizard/scan", ScanResult, "wizard", ScanRequest),
    _http("POST /api/wizard/module-values", ModuleValues, "wizard", ModuleValuesRequest),
    _http("POST /api/wizard/finish", WizardActionResponse, "wizard", WizardFinishRequest),
    _http("GET /api/wizard/installstatus", InstallStatus, "wizard"),
    _http("GET /api/wizard/installlog", InstallLog, "wizard"),
    _http("POST /api/wizard/scan/bluetooth", BtRowsResult, "wizard", EmptyWizardRequest),
    _http(
        "POST /api/wizard/scan/thermoworks",
        ThermoworksRowsResult,
        "wizard",
        ThermoworksRequest,
    ),
    _http(
        "POST /api/wizard/probes/validate-bus-kinds",
        BusKindsValidationResponse,
        "wizard",
        BusKindsValidationRequest,
    ),
    _http("GET /api/probe_modules", ProbeModuleCatalog, "wizard"),
    _http("POST /api/probe_map", ProbeMapResponse, "wizard", ProbeMapRequest),
    # Managed JSON content. Generic write envelopes normalize through
    # ApiEnvelope; action-specific bodies are listed where they are shared.
    _http("GET /api/files/cookfiles", FileListing, "content"),
    _http("GET /api/files/recipes", FileListing, "content"),
    _http("GET /api/files/cookfiles/detail", CookFileDetail, "content"),
    _http("GET /api/files/cookfiles/chart", CookFileChartData, "content"),
    _http("GET /api/files/recipes/detail", RecipeDetail, "content"),
    _http("POST /api/files/cookfiles/delete", ApiEnvelope, "content", FileRequest),
    _http("POST /api/files/cookfiles/title", ApiEnvelope, "content", CookFileTitleRequest),
    _http("POST /api/files/cookfiles/label", ApiEnvelope, "content", CookFileLabelRequest),
    _http("POST /api/files/cookfiles/recover", ApiEnvelope, "content", CookFileRecoverRequest),
    _http(
        "POST /api/files/cookfiles/comments [action=add]",
        ApiEnvelope,
        "content",
        CookFileCommentAddRequest,
    ),
    _http(
        "POST /api/files/cookfiles/comments [action=update]",
        ApiEnvelope,
        "content",
        CookFileCommentUpdateRequest,
    ),
    _http(
        "POST /api/files/cookfiles/comments [action=delete]",
        ApiEnvelope,
        "content",
        CookFileCommentDeleteRequest,
    ),
    _http(
        "POST /api/files/cookfiles/comments/assets",
        ApiEnvelope,
        "content",
        CookFileCommentAssetsRequest,
    ),
    _http("POST /api/files/cookfiles/assets/delete", ApiEnvelope, "content", FileAssetsRequest),
    _http("POST /api/files/cookfiles/thumbnail", ApiEnvelope, "content", CookFileThumbnailRequest),
    _http("POST /api/files/recipes/assets/delete", ApiEnvelope, "content", FileAssetsRequest),
    _http("POST /api/files/recipes/create", ApiEnvelope, "content", EmptyContentRequest),
    _http("POST /api/files/recipes/delete", ApiEnvelope, "content", FileRequest),
    _http("POST /api/files/recipes/run", ApiEnvelope, "content", FileRequest),
    _http("POST /api/files/recipes/metadata", ApiEnvelope, "content", RecipeMetadataUpdateRequest),
    _http("POST /api/files/recipes/ingredients [action=add]", ApiEnvelope, "content", RecipeIngredientAddRequest),
    _http("POST /api/files/recipes/ingredients [action=update]", ApiEnvelope, "content", RecipeIngredientUpdateRequest),
    _http("POST /api/files/recipes/ingredients [action=delete]", ApiEnvelope, "content", RecipeIngredientDeleteRequest),
    _http("POST /api/files/recipes/instructions [action=add]", ApiEnvelope, "content", RecipeInstructionAddRequest),
    _http(
        "POST /api/files/recipes/instructions [action=update]",
        ApiEnvelope,
        "content",
        RecipeInstructionUpdateRequest,
    ),
    _http(
        "POST /api/files/recipes/instructions [action=delete]",
        ApiEnvelope,
        "content",
        RecipeInstructionDeleteRequest,
    ),
    _http("POST /api/files/recipes/steps [action=insert]", ApiEnvelope, "content", RecipeStepInsertRequest),
    _http("POST /api/files/recipes/steps [action=update]", ApiEnvelope, "content", RecipeStepUpdateRequest),
    _http("POST /api/files/recipes/steps [action=delete]", ApiEnvelope, "content", RecipeStepDeleteRequest),
    _http(
        "POST /api/files/recipes/assets [section=splash]",
        ApiEnvelope,
        "content",
        RecipeSplashAssetAssignmentRequest,
    ),
    _http(
        "POST /api/files/recipes/assets [section=indexed]",
        ApiEnvelope,
        "content",
        RecipeIndexedAssetAssignmentRequest,
    ),
    _http("GET /api/history/chart", HistoryChartData, "content"),
    _http("GET /api/metrics", MetricsPayload, "content"),
    # Administration and log-family metadata.
    _http("GET /api/admin/state", AdminState, "operations"),
    _http("POST /api/admin/system", SystemActionResponse, "operations", SystemActionRequest),
    _http("POST /api/admin/factory-reset", FactoryResetResponse, "operations", EmptyOperationRequest),
    _http(
        "POST /api/admin/maintenance",
        MaintenanceActionResponse,
        "operations",
        MaintenanceActionRequest,
    ),
    _http("POST /api/admin/settings", AdminSettingsUpdate, "operations", AdminSettingsUpdate),
    _http("GET /api/admin/backups", BackupListing, "operations"),
    _http("POST /api/admin/backups/create", BackupCreated, "operations", BackupCreateRequest),
    _http("POST /api/admin/backups/restore", BackupRestored, "operations", BackupRestoreRequest),
    _http("GET /api/admin/logs", LogsMetadata, "operations"),
    _http("POST /api/admin/logs/delete", LogsDeleted, "operations", EmptyOperationRequest),
    # Updater JSON endpoints; build-log downloads are explicitly non-JSON.
    _http("GET /api/update/state", UpdateState, "operations"),
    _http("GET /api/update/check", UpdateCheck, "operations"),
    _http("GET /api/update/log", UpdateLog, "operations"),
    _http("GET /api/update/status", UpdateStatus, "operations"),
    _http("POST /api/update/branches/refresh", UpdateStarted, "operations", EmptyOperationRequest),
    _http("POST /api/update/branch", UpdateStarted, "operations", UpdateBranchRequest),
    _http("POST /api/update/pull", UpdateStarted, "operations", EmptyOperationRequest),
    _http("POST /api/update/upgrade", UpdateStarted, "operations", EmptyOperationRequest),
    _http("POST /api/update/rebuild-web-ui", UpdateStarted, "operations", EmptyOperationRequest),
    _http("POST /api/update/rebuild-acados", UpdateStarted, "operations", EmptyOperationRequest),
    _http("GET /api/update/buildlog", BuildLog, "operations"),
    # Tuner JSON endpoints.
    _http("POST /api/tuner/session [open=true]", TunerSession, "operations", TunerSessionRequest),
    _http("POST /api/tuner/session [open=false]", TunerSession, "operations", TunerSessionRequest),
    _http("GET /api/tuner/tr", TrReading, "operations"),
    _http("POST /api/tuner/coefficients", Coefficients, "operations", CoefficientsRequest),
    _http("POST /api/tuner/profile", SavedProfile, "operations", ProfileInput),
    _http("POST /api/tuner/auto-status", AutoStatus, "operations", AutoStatusRequest),
)


NON_JSON_WEB_TRANSPORTS: tuple[NonJsonWebTransport, ...] = (
    NonJsonWebTransport(
        "browser_file_handles",
        "browser",
        "File objects and handles are browser capabilities, not Python JSON values.",
    ),
    NonJsonWebTransport(
        "downloaded_bytes",
        "http",
        "Archive, image, build-log, and export downloads are opaque response bytes owned by the browser.",
    ),
    NonJsonWebTransport(
        "multipart_form_data",
        "http",
        "Uploads use browser-authored multipart boundaries and FormData rather than JSON request bodies.",
    ),
    NonJsonWebTransport(
        "text_range_streams",
        "http",
        "Log tailing preserves text bytes, Range requests, and Content-Range parsing outside JSON contracts.",
    ),
)
