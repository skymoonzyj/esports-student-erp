"""Public authorization commands; no device grants or connector credentials."""

import unicodedata
from typing import Literal
from urllib.parse import unquote
from uuid import UUID

from pydantic import ConfigDict, Field, SecretStr, field_validator

from app.operations.cloud_auth_schemas import AuthContract, AuthMode


def safe_directory(value: str) -> str:
    """Keep Unicode/spaces/percent names; forbid decoded traversal and escapes."""
    if not value.startswith("/") or "\\" in value or len(value) > 1024:
        raise ValueError("目录路径无效")
    decoded = value
    for _ in range(4):
        if any(unicodedata.category(c).startswith("C") for c in decoded):
            raise ValueError("目录路径无效")
        if "\\" in decoded or any(segment in (".", "..") for segment in decoded.split("/")):
            raise ValueError("目录路径无效")
        newer = unquote(decoded)
        if newer == decoded:
            break
        decoded = newer
    else:
        raise ValueError("目录路径编码过多")
    return value


class CloudSessionCreate(AuthContract):
    operation_id: UUID
    mode: AuthMode = "own_app"


class CloudRevisionCommand(AuthContract):
    expected_revision: int = Field(ge=1)


class CloudOperationCommand(CloudRevisionCommand):
    operation_id: UUID


class CloudImportCommand(CloudOperationCommand):
    model_config = ConfigDict(hide_input_in_errors=True)
    access_token: SecretStr = Field(repr=False)
    refresh_token: SecretStr = Field(repr=False)

    @field_validator("access_token", "refresh_token", mode="before")
    @classmethod
    def exact_token(cls, value: object) -> SecretStr:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if not isinstance(value, str):
            # Pydantic v2 does not translate TypeError into a validation error.
            raise ValueError("授权令牌格式无效")  # noqa: TRY004
        value = value.strip()
        lower = value.lower()
        if (
            not value
            or len(value) > 4096
            or value.isdecimal()
            or any(c.isspace() or unicodedata.category(c).startswith("C") for c in value)
            or any(c in value for c in '<>"{}[];\\')
            or "://" in value
            or lower.startswith(
                (
                    "uid=",
                    "uid:",
                    "cookie:",
                    "cookie=",
                    "appkey=",
                    "appkey:",
                    "appid=",
                    "access_token=",
                    "refresh_token=",
                )
            )
        ):
            raise ValueError("授权令牌格式无效")
        return SecretStr(value)


class CloudDirectoryRequest(CloudOperationCommand):
    parent: str = Field(default="/", min_length=1, max_length=1024)

    _validate_parent = field_validator("parent")(safe_directory)


class CloudDirectorySelection(CloudOperationCommand):
    directory: str = Field(min_length=2, max_length=1024)

    _validate_directory = field_validator("directory")(safe_directory)


class CloudDirectoryEntry(AuthContract):
    name: str = Field(min_length=1, max_length=255)
    path: str = Field(min_length=1, max_length=1024)
    selectable: bool = True

    _validate_path = field_validator("path")(safe_directory)


class CloudDirectoryResult(AuthContract):
    operation_id: UUID
    status: Literal["pending", "ready", "failed"]
    entries: list[CloudDirectoryEntry] = Field(default_factory=list, max_length=500)
    error_code: str | None = None


class CloudUnbindCommand(AuthContract):
    operation_id: UUID
    expected_revision: int = Field(ge=1)


class CloudUnbindResponse(AuthContract):
    operation_id: UUID
    session_id: UUID
    status: Literal["pending"] = "pending"
    official_revoke_required: bool = True
