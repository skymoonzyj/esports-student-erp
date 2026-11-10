"""Shared computer facts; nullable for historical inventory, complete on new computers."""

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

COMPUTER_CATEGORIES = frozenset({"台式电脑", "笔记本电脑"})
HARDWARE_FIELDS = ("cpu_model", "memory_capacity", "gpu_model", "disk_capacity")


class AssetHardware(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    cpu_model: str | None = Field(default=None, max_length=120)
    memory_capacity: str | None = Field(default=None, max_length=80)
    gpu_model: str | None = Field(default=None, max_length=120)
    disk_capacity: str | None = Field(default=None, max_length=120)

    @field_validator(*HARDWARE_FIELDS, mode="before")
    @classmethod
    def normalize_hardware(cls, value: object) -> object:
        return value.strip() or None if isinstance(value, str) else value


def validate_computer_hardware(category: str | None, values: Mapping[str, object]) -> None:
    if category in COMPUTER_CATEGORIES and any(not values.get(key) for key in HARDWARE_FIELDS):
        raise ValueError("电脑资产必须填写 CPU、内存、GPU 和硬盘容量")
