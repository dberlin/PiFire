from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

FiniteFloat = Annotated[float, Field(allow_inf_nan=False, strict=True)]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ExtensibleWireModel(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True, strict=True)
