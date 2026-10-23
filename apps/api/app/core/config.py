from __future__ import annotations

import base64
import binascii
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.client_ip import trusted_proxy_networks

_URLSAFE_BASE64 = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")


def _runtime_root_from_config_path(config_path: Path) -> Path:
    resolved = config_path.resolve()
    parents = resolved.parents
    if (
        len(parents) >= 5
        and parents[1].name == "app"
        and parents[2].name == "api"
        and parents[3].name == "apps"
    ):
        return parents[4]
    if len(parents) >= 3 and parents[1].name == "app":
        return parents[2]
    raise RuntimeError("unable to determine application runtime root")


_REPOSITORY_ROOT = _runtime_root_from_config_path(Path(__file__))
_IGNORED_RUNTIME_ROOT = (_REPOSITORY_ROOT / "runtime").resolve()
_FORBIDDEN_MEDIA_ROOTS = tuple(
    (_REPOSITORY_ROOT / relative).resolve()
    for relative in ("apps/web", "static", "public")
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    app_env: Literal["development", "test", "production"]
    database_url: str = Field(min_length=1)
    session_cookie_secure: bool = True
    session_ttl_minutes: int = Field(default=480, gt=0)
    trusted_proxy_cidrs: str = ""
    cohort_scheduler_interval_seconds: int = Field(default=30, ge=5, le=3600)
    operations_monitor_enabled: bool = True
    operations_flush_interval_seconds: int = Field(default=15, ge=1, le=300)
    operations_monitor_interval_seconds: int = Field(default=60, ge=10, le=3600)
    operations_retention_days: int = Field(default=30, ge=1, le=365)
    operations_backup_status_path: Path = Path("/app/var/backup-status.json")
    operations_disk_path: Path = Path("/app/var")
    operations_certificate_host: str = ""
    operations_certificate_port: int = Field(default=18080, ge=1, le=65535)
    payment_signature_tolerance_seconds: int = Field(default=300, gt=0)
    payment_nonce_ttl_hours: int = Field(default=24, gt=0)
    payment_import_requests_per_minute: int = Field(default=120, gt=0)
    field_encryption_master_key: SecretStr
    field_lookup_key: SecretStr
    session_token_pepper: SecretStr
    registration_verification_pepper: SecretStr | None = None
    registration_session_token_pepper: SecretStr | None = None
    # Public account applications currently use email verification only. Keep
    # this configurable so the phone channel can be reopened without a code
    # change when the operational mailbox is ready.
    registration_phone_signup_enabled: bool = False
    profile_claim_email_pending_limit: int = Field(default=3, ge=1, le=1000)
    profile_claim_identity_pending_limit: int = Field(default=5, ge=1, le=1000)
    profile_claim_global_pending_limit: int = Field(default=1000, ge=1, le=100000)
    registration_test_mailbox_dir: Path | None = None
    registration_test_server_host: str | None = None
    evaluation_tool_service_token: SecretStr
    evaluation_tool_web_service_token: SecretStr
    evaluation_tool_worker_service_token: SecretStr
    erp_web_base_url: str = "http://127.0.0.1:5173"
    evaluation_tool_standalone_base_url: str = "http://localhost:8010"
    evaluation_tool_embedded_path: str = "/evaluation-tool"
    evaluation_login_request_ttl_seconds: int = Field(default=300, ge=60, le=900)
    evaluation_login_ticket_ttl_seconds: int = Field(default=60, ge=30, le=120)
    evaluation_media_root: Path = Path("runtime/evaluation-media")
    evaluation_media_quarantine_root: Path = Path("runtime/evaluation-quarantine")
    evaluation_media_store_id: UUID | None = None
    evaluation_media_max_bytes: int = Field(
        default=20 * 1024 * 1024,
        ge=1,
        le=20 * 1024 * 1024,
    )
    evaluation_media_max_pixels: int = Field(
        default=50_000_000,
        ge=1,
        le=50_000_000,
    )
    evaluation_media_preview_max_edge: int = Field(default=1600, ge=1, le=1600)
    evaluation_upload_max_open_sessions_per_account: int = Field(default=5, ge=1, le=100)
    evaluation_upload_max_open_sessions_total: int = Field(default=500, ge=1, le=100_000)

    @field_validator(
        "registration_verification_pepper",
        "registration_session_token_pepper",
        "registration_test_mailbox_dir",
        "registration_test_server_host",
        mode="before",
    )
    @classmethod
    def blank_registration_secret_is_unconfigured(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def validate_trusted_proxy_cidrs(cls, value: str) -> str:
        try:
            networks = trusted_proxy_networks(value)
        except ValueError as exc:
            raise ValueError("trusted_proxy_cidrs contains invalid CIDR") from exc
        return ",".join(str(network) for network in networks)

    def __init__(self, **values: Any) -> None:
        resolved = dict(values)
        for field_name in type(self).model_fields:
            env_name = field_name.upper()
            if field_name in resolved or env_name in os.environ:
                continue
            file_path = os.environ.get(f"{env_name}_FILE")
            if not file_path:
                continue
            try:
                resolved[field_name] = Path(file_path).read_text(encoding="utf-8").rstrip("\r\n")
            except OSError as exc:
                raise ValueError(f"unable to read {env_name}_FILE") from exc
        super().__init__(**resolved)

    def validate_runtime_secrets(self) -> None:
        if self.app_env == "production" and any(
            network.prefixlen == 0
            for network in trusted_proxy_networks(self.trusted_proxy_cidrs)
        ):
            raise ValueError("production forbids catch-all trusted proxy CIDRs")
        registration_fields = (
            "registration_verification_pepper",
            "registration_session_token_pepper",
        )
        if self.app_env == "production" and any(
            getattr(self, field_name) is None for field_name in registration_fields
        ):
            raise ValueError("production requires registration secrets")

        decoded_secrets: dict[str, bytes] = {}
        for field_name in (
            "field_encryption_master_key",
            "field_lookup_key",
            "session_token_pepper",
            *registration_fields,
        ):
            secret = getattr(self, field_name)
            if secret is None:
                continue
            raw = secret.get_secret_value() if isinstance(secret, SecretStr) else str(secret)
            if not _URLSAFE_BASE64.fullmatch(raw):
                raise ValueError(f"{field_name} must be URL-safe Base64")
            try:
                decoded = base64.b64decode(raw, altchars=b"-_", validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError(f"{field_name} must be URL-safe Base64") from exc
            if len(decoded) != 32:
                raise ValueError(f"{field_name} must decode to 32 bytes")
            decoded_secrets[field_name] = decoded
        session_peppers = [
            decoded_secrets[field_name]
            for field_name in (
                "session_token_pepper",
                "registration_verification_pepper",
                "registration_session_token_pepper",
            )
            if field_name in decoded_secrets
        ]
        if len(session_peppers) != len(set(session_peppers)):
            raise ValueError("session peppers must be distinct")
        service_tokens: dict[str, str] = {}
        for field_name in (
            "evaluation_tool_service_token",
            "evaluation_tool_web_service_token",
            "evaluation_tool_worker_service_token",
        ):
            service_secret = getattr(self, field_name)
            service_token = (
                service_secret.get_secret_value()
                if isinstance(service_secret, SecretStr)
                else str(service_secret)
            )
            if len(service_token) < 32 or any(
                character.isspace() for character in service_token
            ):
                raise ValueError(
                    f"{field_name} must be at least 32 non-whitespace characters"
                )
            service_tokens[field_name] = service_token
        if len(set(service_tokens.values())) != len(service_tokens):
            raise ValueError("evaluation service tokens must be distinct")
        if self.app_env == "production" and not self.session_cookie_secure:
            raise ValueError("production requires secure session cookies")
        from app.evaluations.config import EvaluationBridgeConfig

        EvaluationBridgeConfig.from_settings(self)
        self.evaluation_media_paths()

    def evaluation_media_paths(self) -> tuple[Path, Path]:
        raw_roots = (
            self.evaluation_media_root,
            self.evaluation_media_quarantine_root,
        )
        if self.app_env == "production" and any(
            not root.expanduser().is_absolute() for root in raw_roots
        ):
            raise ValueError("production evaluation media roots must be absolute")

        resolved: list[Path] = []
        for raw_root in raw_roots:
            expanded = raw_root.expanduser()
            if expanded.is_absolute():
                root = expanded.resolve()
            else:
                root = (_REPOSITORY_ROOT / expanded).resolve()
                if not root.is_relative_to(_IGNORED_RUNTIME_ROOT):
                    raise ValueError(
                        "relative evaluation media roots must use the Git-ignored runtime directory"
                    )
            if any(
                root == forbidden or root.is_relative_to(forbidden)
                for forbidden in _FORBIDDEN_MEDIA_ROOTS
            ):
                raise ValueError(
                    "evaluation media roots must use private application storage"
                )
            resolved.append(root)

        media_root, quarantine_root = resolved
        if media_root == quarantine_root:
            raise ValueError("evaluation media and quarantine roots must be distinct")
        return media_root, quarantine_root


@lru_cache
def get_settings() -> Settings:
    return Settings()
