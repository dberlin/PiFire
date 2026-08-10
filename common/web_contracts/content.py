from __future__ import annotations

from typing import Annotated, Literal, TypeVar

from pydantic import BaseModel, Field
from typing_extensions import TypeAliasType

from .base import ExtensibleWireModel, FiniteFloat, WireModel

FiniteNumber = TypeAliasType("FiniteNumber", int | FiniteFloat)
NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]
PositiveInt = Annotated[int, Field(gt=0, strict=True)]
TWire = TypeVar("TWire", bound=BaseModel)


class FileListItem(WireModel):
    filename: str
    title: str
    thumbnail: str


class FileListing(WireModel):
    items: list[FileListItem]
    page: PositiveInt
    last_page: PositiveInt
    per_page: PositiveInt
    reverse: bool
    total: NonNegativeInt


class FileErrorDetail(WireModel):
    status: NonNegativeInt
    message: str
    errortype: Literal["version", "asset", "other"] | None


class ContentErrorData(ExtensibleWireModel):
    field: str = ""
    errortype: Literal["version", "asset", "other"] | None = None
    kind: str = ""
    mode: str | None = None


class ContentErrorEnvelope(WireModel):
    data: ContentErrorData | None
    result: Literal["Error"]
    message: str


class FilenameData(WireModel):
    filename: str


class EmptyContentRequest(WireModel):
    pass


class FileRequest(WireModel):
    file: str


class FileAssetsRequest(FileRequest):
    assets: list[str]


class CookFileTitleRequest(FileRequest):
    title: str


class CookFileLabelRequest(FileRequest):
    old_label: str
    new_label: str


class CookFileRecoverRequest(FileRequest):
    action: Literal["upgrade", "repair"]


class CookFileCommentAddRequest(FileRequest):
    action: Literal["add"]
    text: str


class CookFileCommentUpdateRequest(FileRequest):
    action: Literal["update"]
    id: str
    text: str


class CookFileCommentDeleteRequest(FileRequest):
    action: Literal["delete"]
    id: str


class CookFileCommentAssetsRequest(FileRequest):
    id: str
    assets: list[str]


class CookFileThumbnailRequest(FileRequest):
    asset: str


class AssetNamesData(WireModel):
    assets: list[str]


class CookFileLabelData(WireModel):
    new_label_safe: str


class CookFileMetadata(ExtensibleWireModel):
    title: str
    units: str
    thumbnail: str
    id: str
    version: str
    starttime: str
    endtime: str
    starttime_epoch: FiniteNumber | str
    endtime_epoch: FiniteNumber | str


class CookFileEvent(ExtensibleWireModel):
    id: str | int
    mode: str
    starttime_c: str | Literal[0]
    endtime_c: str | Literal[0]
    augerontime_c: str | Literal[0]
    estusage_m: str
    estusage_i: str
    pellet_level_start: FiniteNumber
    pellet_level_end: FiniteNumber
    timeinmode: str | Literal[0]


class CookFileTotals(WireModel):
    augerontime: str
    estusage_m: str
    estusage_i: str
    cooktime: str
    pellet_level_start: FiniteNumber
    pellet_level_end: FiniteNumber


class EmptyCookFileTotals(WireModel):
    pass


class CookFileComment(ExtensibleWireModel):
    id: str
    text: str
    date: str
    time: str
    edited: str
    assets: list[str]


class CookFileAsset(ExtensibleWireModel):
    id: str
    filename: str
    type: str


class CookFileLabels(ExtensibleWireModel):
    probes: dict[str, str]
    targets: dict[str, str]
    primarysp: dict[str, str]


class CookFileDetail(WireModel):
    filename: str
    metadata: CookFileMetadata
    graph_labels: CookFileLabels
    events: list[CookFileEvent]
    event_totals: CookFileTotals | EmptyCookFileTotals
    comments: list[CookFileComment]
    assets: list[CookFileAsset]


class RecipeMetadata(ExtensibleWireModel):
    author: str
    username: str
    id: str
    title: str
    description: str
    image: str
    thumbnail: str
    units: str
    prep_time: int
    cook_time: int
    rating: int
    difficulty: str
    version: str
    food_probes: int


class Ingredient(ExtensibleWireModel):
    name: str
    quantity: str
    assets: list[str]


class Instruction(ExtensibleWireModel):
    text: str
    ingredients: list[str]
    assets: list[str]
    step: int


class RecipeTriggerTemperatures(WireModel):
    primary: FiniteNumber
    food: list[FiniteNumber]


class RecipeStep(ExtensibleWireModel):
    mode: Literal["Smoke", "Hold", "Startup", "Shutdown"]
    hold_temp: FiniteNumber
    timer: FiniteNumber
    notify: bool
    message: str
    pause: bool
    trigger_temps: RecipeTriggerTemperatures


class RecipeAsset(ExtensibleWireModel):
    id: str
    filename: str
    type: str


class CookFileAssetsData(WireModel):
    assets: list[CookFileAsset]


class RecipeAssetsData(WireModel):
    assets: list[RecipeAsset]


class RecipeBody(ExtensibleWireModel):
    ingredients: list[Ingredient]
    instructions: list[Instruction]
    steps: list[RecipeStep]


class RecipeDetail(WireModel):
    filename: str
    metadata: RecipeMetadata
    recipe: RecipeBody
    assets: list[RecipeAsset]


class RecipeMetadataFields(WireModel):
    title: str = ""
    author: str = ""
    description: str = ""
    difficulty: str = ""
    units: str = ""
    prep_time: int = 0
    cook_time: int = 0
    rating: int = 0
    food_probes: int = 0


class RecipeMetadataUpdateRequest(WireModel):
    file: str
    fields: RecipeMetadataFields


class RecipeIngredientAddRequest(WireModel):
    file: str
    action: Literal["add"]


class RecipeIngredientUpdateRequest(WireModel):
    file: str
    action: Literal["update"]
    index: int
    name: str
    quantity: str


class RecipeIngredientDeleteRequest(WireModel):
    file: str
    action: Literal["delete"]
    index: int


class RecipeInstructionAddRequest(WireModel):
    file: str
    action: Literal["add"]


class RecipeInstructionUpdateRequest(WireModel):
    file: str
    action: Literal["update"]
    index: int
    text: str
    ingredients: list[str]
    step: int


class RecipeInstructionDeleteRequest(WireModel):
    file: str
    action: Literal["delete"]
    index: int


class RecipeStepInsertRequest(WireModel):
    file: str
    action: Literal["insert"]
    index: int


class RecipeStepUpdateRequest(WireModel):
    file: str
    action: Literal["update"]
    index: int
    step: RecipeStep


class RecipeStepDeleteRequest(WireModel):
    file: str
    action: Literal["delete"]
    index: int


class RecipeIndexedAssetAssignmentRequest(WireModel):
    file: str
    section: Literal["ingredients", "instructions"]
    index: int
    assets: list[str]


class RecipeSplashAssetAssignmentRequest(WireModel):
    file: str
    section: Literal["splash"]
    assets: list[str]


class HistoryPoint(WireModel):
    x: FiniteNumber
    y: FiniteNumber | None


class HistoryDataset(ExtensibleWireModel):
    label: str
    data: list[HistoryPoint]
    fill: bool = False
    lineTension: FiniteNumber = 0.1
    backgroundColor: str = ""
    borderColor: str = ""
    borderCapStyle: str = "butt"
    borderDash: list[FiniteNumber] = Field(default_factory=list)
    borderDashOffset: FiniteNumber = 0.0
    borderJoinStyle: str = "miter"
    pointBorderColor: str = ""
    pointBackgroundColor: str = "#fff"
    pointBorderWidth: NonNegativeInt = 1
    pointHoverRadius: NonNegativeInt = 10
    pointHoverBackgroundColor: str = ""
    pointHoverBorderColor: str = ""
    pointHoverBorderWidth: NonNegativeInt = 2
    pointRadius: NonNegativeInt = 1
    pointHitRadius: NonNegativeInt = 10
    pointStyle: str = "line"
    spanGaps: bool = False
    hidden: bool = False


class HistoryProbeMapper(ExtensibleWireModel):
    probes: dict[str, NonNegativeInt]
    targets: dict[str, NonNegativeInt]
    primarysp: dict[str, NonNegativeInt]


class HistoryGraphLabels(ExtensibleWireModel):
    probes: dict[str, str]
    targets: dict[str, str]
    primarysp: dict[str, str]


class HistoryAnnotationLabel(ExtensibleWireModel):
    backgroundColor: str = ""
    borderColor: str = ""
    color: str = ""
    content: str = ""
    enabled: bool = True
    position: str = "end"
    rotation: FiniteNumber = 0


class HistoryAnnotation(ExtensibleWireModel):
    type: str
    xMin: FiniteNumber
    xMax: FiniteNumber
    borderColor: str
    borderWidth: FiniteNumber = 2
    label: HistoryAnnotationLabel | None = None
    display: bool = True


class HistoryChartData(WireModel):
    time_labels: list[FiniteNumber]
    chart_data: list[HistoryDataset]
    probe_mapper: HistoryProbeMapper
    graph_labels: HistoryGraphLabels
    annotations: dict[str, HistoryAnnotation]
    minutes: PositiveInt


class CookFileChartData(WireModel):
    time_labels: list[FiniteNumber | str]
    chart_data: list[HistoryDataset]
    probe_mapper: HistoryProbeMapper
    annotations: dict[str, HistoryAnnotation]


class MetricRecord(WireModel):
    id: str
    starttime: FiniteNumber
    starttime_c: str
    endtime: FiniteNumber
    endtime_c: str | Literal[0]
    timeinmode: str
    mode: str
    augerontime: FiniteNumber
    augerontime_c: str
    estusage_m: str
    estusage_i: str
    fanontime: FiniteNumber
    fanontime_c: str | None
    smokeplus: bool
    primary_setpoint: FiniteNumber
    smart_start_profile: int
    startup_temp: FiniteNumber
    p_mode: int
    auger_cycle_time: FiniteNumber
    pellet_level_start: FiniteNumber
    pellet_level_end: FiniteNumber
    pellet_brand_type: str


class MetricsPayload(WireModel):
    metrics: list[MetricRecord]
    units: Literal["F", "C"]
    augerrate: FiniteNumber


def validated_content_json(model: type[TWire], payload: object) -> dict:
    """Strictly validate a JSON payload and preserve absent optional members."""
    validated = model.model_validate(payload, strict=True)
    return validated.model_dump(mode="json", by_alias=True, exclude_unset=True)
