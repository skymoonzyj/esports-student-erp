from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    field_validator,
    model_validator,
)

from app.operations.cloud_auth_schemas import AuthMode

Provider = Literal["baidu", "115", "quark"]
PROVIDERS = ("baidu", "115", "quark")
BACKUP_ID_PATTERN = re.compile(r"backup-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}")


def validate_backup_id(value: str) -> str:
    if not isinstance(value, str) or not BACKUP_ID_PATTERN.fullmatch(value):
        raise ValueError("invalid backup identity")
    return value


class CloudCopyRouting(BaseModel):
    """Private claim-only identity. Never accepts an arbitrary origin."""

    model_config = ConfigDict(extra="forbid")
    managed_auth: StrictBool = False
    connector_id: UUID | None = None

    @model_validator(mode="after")
    def exact_identity(self) -> CloudCopyRouting:
        if self.managed_auth != (self.connector_id is not None):
            raise ValueError("managed copy requires exact connector identity")
        return self


class CloudTargetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    remote_path: str = Field(min_length=1, max_length=1024)
    username: str = Field(min_length=1, max_length=255)
    password: SecretStr | None = Field(default=None, max_length=4096)
    enabled: bool = False

    @field_validator("remote_path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        # Service accepts root only for an unchanged, dedicated managed user.
        if value == "/":
            return value
        segments = value.split("/")[1:]
        if (
            not value.startswith("/")
            or any(c in value for c in "\\?#%")
            or any(unicodedata.category(c).startswith("C") for c in value)
            or len(segments) > 16
            or any(not s or s in (".", "..") or len(s.encode("utf-8")) > 255 for s in segments)
        ):
            raise ValueError("请填写无路径穿越的绝对网盘目录")
        return value

    @field_validator("username")
    @classmethod
    def safe_username(cls, value: str) -> str:
        if (
            ":" in value
            or not value.strip()
            or any(unicodedata.category(c).startswith("C") for c in value)
        ):
            raise ValueError("连接器账号格式无效")
        return value


class CloudTargetResponse(BaseModel):
    provider: Provider
    enabled: bool
    configured: bool
    remote_path: str | None
    username: str | None
    credential_present: bool
    revision: int
    connection_status: Literal["untested", "ok", "failed"]
    last_tested_at: datetime | None
    last_success_at: datetime | None
    last_backup_id: str | None
    last_error_code: str | None
    managed: bool = False
    authorization_status: Literal["manual", "valid", "reauthorization_required"] = "manual"
    authorization_mode: AuthMode | None = None
    selected_directory: str | None = None


class CloudJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    provider: Provider
    kind: Literal["test", "copy"]
    status: Literal["queued", "running", "succeeded", "failed"]
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    backup_id: str | None
    error_code: str | None


class CloudBackupResponse(BaseModel):
    targets: list[CloudTargetResponse]
    jobs: list[CloudJobResponse]
    worker_last_seen_at: datetime | None
