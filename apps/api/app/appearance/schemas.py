from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Theme = Literal["clear", "command"]


class AppearanceView(BaseModel):
    theme: Theme
    revision: int


class AppearanceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: Theme
    expected_revision: int = Field(strict=True, ge=1, le=2147483647)
