"""Strict public views and private runner contracts for cloud authorization.

Private grant/access types must never be used as HTTP response models.
"""

from __future__ import annotations

import base64
import binascii
import unicodedata
from typing import Any, Literal
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

AuthProvider = Literal["baidu", "115"]
AuthMode = Literal["own_app", "openlist_builtin_import"]
AuthState = Literal[
    "creating",
    "awaiting_import",
    "verifying_import",
    "awaiting_confirmation",
    "authorized",
    "configuring",
    "test_pending",
    "ready",
    "applying",
    "applied",
    "expired",
    "cancelled",
    "failed",
]
AUTH_PROVIDERS = ("baidu", "115")
TERMINAL_AUTH_STATES = frozenset({"applied", "expired", "cancelled", "failed"})
AuthAction = Literal[
    "create_qr",
    "create_import",
    "verify_import",
    "poll",
    "prepare",
    "list_directories",
    "select_directory",
    "probe",
    "apply",
    "cleanup",
    "unbind",
]


class AuthContract(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class AuthActor(AuthContract):
    account_id: UUID
    login_session_id: UUID


class AuthSource(AuthContract):
    mode: AuthMode
    auth_config_id: str
    auth_config_revision: int = Field(ge=1, strict=True)
    app_revision: int | None = Field(ge=1, strict=True)

    @model_validator(mode="after")
    def frozen_source(self) -> AuthSource:
        prefix = "own-app:" if self.mode == "own_app" else "openlist-builtin-import-v1:"
        if self.auth_config_id not in {prefix + provider for provider in AUTH_PROVIDERS}:
            raise ValueError("invalid authorization source")
        if self.mode == "own_app":
            if self.app_revision is None or self.auth_config_revision != self.app_revision:
                raise ValueError("invalid authorization source revision")
        elif self.app_revision is not None or self.auth_config_revision != 1:
            raise ValueError("invalid builtin authorization source revision")
        return self


class AuthFence(AuthContract):
    protocol_version: Literal[1, 2] = 1
    mode: AuthMode = "own_app"
    auth_config_id: str | None = None
    auth_config_revision: int | None = Field(default=None, ge=1, strict=True)
    session_id: UUID
    session_revision: int = Field(ge=1)
    app_revision: int | None = Field(ge=1)
    target_revision: int = Field(ge=0)
    lease_id: UUID

    @model_validator(mode="after")
    def exact_protocol(self) -> AuthFence:
        if self.protocol_version == 1:
            if (
                self.mode != "own_app"
                or self.app_revision is None
                or self.auth_config_id is not None
                or self.auth_config_revision is not None
            ):
                raise ValueError("invalid legacy authorization fence")
        else:
            if self.auth_config_id is None or self.auth_config_revision is None:
                raise ValueError("authorization source is required for protocol v2")
            AuthSource(
                mode=self.mode,
                auth_config_id=self.auth_config_id,
                auth_config_revision=self.auth_config_revision,
                app_revision=self.app_revision,
            )
        return self


class CloudProbeAttemptView(AuthContract):
    operation_id: UUID
    status: Literal["pending", "cleanup_pending", "failed", "succeeded"]


class CloudAuthSessionView(AuthContract):
    id: UUID
    provider: AuthProvider
    mode: AuthMode = "own_app"
    state: AuthState
    revision: int = Field(ge=1)
    expires_at: AwareDatetime
    verified_at: AwareDatetime | None = None
    token_expires_at: AwareDatetime | None = None
    expiry_source: Literal["provider", "unreported"] | None = None
    account_label: str | None = Field(default=None, max_length=255)
    selected_directory: str | None = Field(default=None, max_length=1024)
    error_code: str | None = Field(default=None, max_length=64)
    next_poll_after_seconds: int = Field(default=2, ge=0, le=600)
    probe_attempt: CloudProbeAttemptView | None = None


class CloudImportOperationView(AuthContract):
    operation_id: UUID
    status: Literal["pending", "succeeded", "failed", "cancelled"]
    session: CloudAuthSessionView
    error_code: str | None = Field(default=None, max_length=64)


class CloudAppView(AuthContract):
    provider: AuthProvider
    client_id: str | None
    secret_present: bool
    revision: int = Field(ge=0)
    configured: bool
    prerequisites: list[str]


class CloudAppUpdate(AuthContract):
    client_id: str = Field(min_length=1, max_length=255)
    client_secret: SecretStr | None = Field(default=None, max_length=4096)
    clear_secret: bool = False
    expected_revision: int = Field(ge=0)

    @field_validator("client_id")
    @classmethod
    def safe_client_id(cls, value: str) -> str:
        value = value.strip()
        if not value or any(unicodedata.category(c).startswith("C") for c in value):
            raise ValueError("invalid application identity")
        return value

    @field_validator("client_secret", mode="before")
    @classmethod
    def blank_preserves_secret(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, SecretStr) and not value.get_secret_value().strip():
            return None
        return value

    @model_validator(mode="after")
    def explicit_secret_action(self) -> CloudAppUpdate:
        if self.clear_secret and self.client_secret is not None:
            raise ValueError("cannot clear and replace application secret together")
        return self


_OFFICIAL_QR_HOSTS = frozenset(
    {
        "openapi.baidu.com",
        "passport.baidu.com",
        "open.115.com",
        "115.com",
        "passportapi.115.com",
        "qrcodeapi.115.com",
    }
)


def _official_url(value: str) -> str:
    parsed = urlsplit(value)
    private_keys = {
        "access_token",
        "refresh_token",
        "client_secret",
        "code_verifier",
        "session_token",
        "password",
    }
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _OFFICIAL_QR_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or any(unicodedata.category(c).startswith("C") or c.isspace() for c in value)
        or "\\" in value
        or parsed.fragment
        or any(key.lower() in private_keys for key, _ in parse_qsl(parsed.query, max_num_fields=64))
    ):
        raise ValueError("invalid official authorization URL")
    return value


class QrPresentation(AuthContract):
    kind: Literal["image", "payload"]
    content: str = Field(min_length=1, max_length=48000, repr=False)
    official_open_url: str | None = Field(default=None, max_length=4096, repr=False)
    expires_at: AwareDatetime

    @field_validator("official_open_url")
    @classmethod
    def official_link(cls, value: str | None) -> str | None:
        return _official_url(value) if value is not None else None

    @model_validator(mode="after")
    def safe_content(self) -> QrPresentation:
        if self.kind == "payload":
            _official_url(self.content)
        else:
            prefix = "data:image/png;base64,"
            if not self.content.startswith(prefix):
                raise ValueError("QR images must be bounded PNG data URIs")
            try:
                png = base64.b64decode(self.content[len(prefix) :], validate=True)
            except (ValueError, binascii.Error):
                raise ValueError("invalid QR image") from None
            if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("invalid QR image")
        return self


class DeviceGrant(AuthContract):
    secret_payload: dict[str, Any] = Field(repr=False)
    qr: QrPresentation = Field(repr=False)
    expires_at: AwareDatetime
    interval_seconds: int = Field(ge=1, le=600)


class TokenGrant(AuthContract):
    access_token: str = Field(min_length=1, max_length=16384, repr=False)
    refresh_token: str = Field(min_length=1, max_length=16384, repr=False)
    expires_at: AwareDatetime


class ImportedTokenGrant(AuthContract):
    model_config = ConfigDict(hide_input_in_errors=True)
    provider: AuthProvider
    mode: Literal["openlist_builtin_import"] = "openlist_builtin_import"
    access_token: str = Field(min_length=1, max_length=16384, repr=False)
    refresh_token: str = Field(min_length=1, max_length=16384, repr=False)
    expires_at: AwareDatetime | None
    expiry_source: Literal["provider", "unreported"]
    refreshed_at: AwareDatetime

    @model_validator(mode="after")
    def explicit_expiry(self) -> ImportedTokenGrant:
        if self.expiry_source == "unreported":
            if self.provider != "baidu" or self.expires_at is not None:
                raise ValueError("invalid unreported token expiry")
        elif self.expires_at is None or self.expires_at <= self.refreshed_at:
            raise ValueError("invalid provider token expiry")
        return self


class PollResult(AuthContract):
    status: Literal["pending", "authorized", "denied", "expired", "throttled"]
    retry_after_seconds: int | None = Field(default=None, ge=1, le=600)
    tokens: TokenGrant | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def tokens_only_on_success(self) -> PollResult:
        if (self.status == "authorized") != (self.tokens is not None):
            raise ValueError("tokens are required exclusively for authorized results")
        return self


class CandidateRef(AuthContract):
    operation_id: UUID
    storage_id: str = Field(min_length=1, max_length=255)
    webdav_user_id: str = Field(min_length=1, max_length=255)
    mount_path: str = Field(min_length=2, max_length=1024)
    account_fingerprint: str = Field(min_length=1, max_length=255)


class CandidateAccess(AuthContract):
    ref: CandidateRef
    username: str = Field(min_length=1, max_length=255, repr=False)
    password: str = Field(min_length=1, max_length=4096, repr=False)


class ProbeEvidence(AuthContract):
    operation_id: UUID
    candidate_id: UUID
    config_revision: int = Field(ge=1)
    directory: str = Field(min_length=2, max_length=1024)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verified_at: AwareDatetime
