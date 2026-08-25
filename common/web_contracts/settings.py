from __future__ import annotations

import copy
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from pydantic import (
    ConfigDict,
    Field,
    RootModel,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    create_model,
    model_serializer,
)

from common.settings_schema import SettingsSchema

from .base import FiniteFloat, WireModel
from .core import ApiEnvelope

CONTROLLER_CATALOG_PATH = Path(__file__).resolve().parents[2] / "controller" / "controllers.json"
ControllerScalar = FiniteFloat | int | bool | str


class SettingsResponse(WireModel):
    settings: SettingsSchema


type SettingsFlag = Literal[
    "settings_update",
    "controller_update",
    "distance_update",
    "probe_profile_update",
]


class SettingsUpdateRequest(WireModel):
    settings: dict[str, Any] = Field(default_factory=dict)
    flags: list[SettingsFlag] = Field(default_factory=list)


class SaveFieldError(WireModel):
    path: str
    message: str


class SettingsUpdateResponse(WireModel):
    result: Literal["success", "error"]
    message: str
    errors: list[SaveFieldError]
    data: SettingsSchema | dict[str, Any]


class ModeData(WireModel):
    mode: str


class ModeResponse(ApiEnvelope[ModeData]):
    pass


class _SparseWireModel(WireModel):
    """Retain the manifest distinction between an absent key and null."""

    @model_serializer(mode="wrap")
    def _omit_unset_fields(
        self,
        handler: SerializerFunctionWrapHandler,
        info: SerializationInfo,
    ):
        serialized = handler(self)
        for name, field in type(self).model_fields.items():
            if name not in self.model_fields_set:
                key = field.serialization_alias if info.by_alias and field.serialization_alias else name
                serialized.pop(key, None)
        return serialized


class _ControllerOptionBase(_SparseWireModel):
    option_name: str
    option_friendly_name: str
    option_description: str
    hidden: bool = False
    option_min: FiniteFloat | None = None
    option_max: FiniteFloat | None = None
    option_step: FiniteFloat | None = None


class FloatControllerOption(_ControllerOptionBase):
    option_type: Literal["float"]
    option_default: FiniteFloat


class IntControllerOption(_ControllerOptionBase):
    option_type: Literal["int"]
    option_default: int
    option_min: int | None = None
    option_max: int | None = None
    option_step: int | None = None


class BoolControllerOption(_ControllerOptionBase):
    option_type: Literal["bool"]
    option_default: bool


class ListControllerOption(_ControllerOptionBase):
    option_type: Literal["list"]
    option_default: ControllerScalar
    list_values: list[ControllerScalar]
    list_labels: list[str] | None = None


class StringControllerOption(_ControllerOptionBase):
    option_type: Literal["string"]
    option_default: str


ControllerOption = Annotated[
    FloatControllerOption | IntControllerOption | BoolControllerOption | ListControllerOption | StringControllerOption,
    Field(discriminator="option_type"),
]


class ControllerCycleRecommendation(WireModel):
    cycle_ratio_max: FiniteFloat


class ControllerRecommendations(WireModel):
    cycle: ControllerCycleRecommendation


class ControllerDependencies(_SparseWireModel):
    modules: list[str] = Field(default_factory=list)
    extra: str | None = None


class ControllerDefinition(_SparseWireModel):
    friendly_name: str
    module_name: str
    image: str
    description: str
    author: str
    link: str
    contributors: list[str]
    attributions: list[str]
    recommendations: ControllerRecommendations | None = None
    dependencies: ControllerDependencies | None = None
    config: list[ControllerOption]


class ControllerMetadata(RootModel[dict[str, ControllerDefinition]]):
    model_config = ConfigDict(frozen=True, strict=True)


class ControllerCatalog(WireModel):
    metadata: ControllerMetadata


def _load_controller_catalog() -> ControllerCatalog:
    return ControllerCatalog.model_validate_json(CONTROLLER_CATALOG_PATH.read_text(), strict=True)


def _pascal_case(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


def _config_annotation(option: ControllerOption) -> Any:
    if option.option_type == "float":
        return float
    if option.option_type == "int":
        return int
    if option.option_type == "bool":
        return bool
    if option.option_type == "string":
        return str
    return Literal[tuple(option.list_values)]


def _create_wire_model(name: str, fields: dict[str, Any]) -> type[WireModel]:
    return cast(type[WireModel], create_model(name, __base__=WireModel, **fields))


CONTROLLER_CATALOG = _load_controller_catalog()
CONTROLLER_CONFIG_MODELS: dict[str, type[WireModel]] = {
    name: _create_wire_model(
        f"{_pascal_case(name)}Config",
        {
            option.option_name: (
                _config_annotation(option),
                copy.deepcopy(option.option_default),
            )
            for option in definition.config
        },
    )
    for name, definition in CONTROLLER_CATALOG.metadata.root.items()
}

ControllerConfigs = _create_wire_model(
    "ControllerConfigs",
    {name: (model, Field(default_factory=model)) for name, model in CONTROLLER_CONFIG_MODELS.items()},
)

PidConfig = CONTROLLER_CONFIG_MODELS["pid"]
PidSpConfig = CONTROLLER_CONFIG_MODELS["pid_sp"]
MpcConfig = CONTROLLER_CONFIG_MODELS["mpc"]
