from __future__ import annotations

import base64
import binascii
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_URLSAFE_BASE64 = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")


def _repository_root() -> Path:
    """Find the checkout root in source and the application root in images.

    Source checkouts have ``apps/`` and ``migrations/`` at their root.  The
    production image intentionally copies only the runtime tree into
    ``/app``; in that layout the module's old fixed ``parents[4]`` lookup
    walked past the filesystem root during import.
    """

    module_path = Path(__file__).resolve()
    for candidate in module_path.parents:
        if (candidate / "apps").is_dir() and (candidate / "migrations").is_dir():
            return candidate

    working_directory = Path.cwd().resolve()
    if (working_directory / "migrations").is_dir():
        return working_directory

    # The Docker production image uses /app as WORKDIR and copies migrations
    # there.  Keep a deterministic fallback for launchers with another cwd.
    return Path("/app") if Path("/app/migrations").is_dir() else module_path.parents[2]


_REPOSITORY_ROOT = _repository_root()
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
    cohort_scheduler_interval_seconds: int = Field(default=30, ge=5, le=3600)
    payment_signature_tolerance_seconds: int = Field(default=300, gt=0)
    payment_nonce_ttl_hours: int = Field(default=24, gt=0)
    payment_import_requests_per_minute: int = Field(default=120, gt=0)
    field_encryption_master_key: SecretStr
    field_lookup_key: SecretStr
    session_token_pepper: SecretStr
    evaluation_tool_service_token: SecretStr
    erp_web_base_url: str = "http://127.0.0.1:5173"
    evaluation_tool_standalone_base_url: str = "http://localhost:8010"
    evaluation_tool_embedded_path: str = "/evaluation-tool"
    evaluation_login_request_ttl_seconds: int = Field(default=300, ge=60, le=900)
    evaluation_login_ticket_ttl_seconds: int = Field(default=60, ge=30, le=120)
    evaluation_media_root: Path = Path("runtime/evaluation-media")
    evaluation_media_quarantine_root: Path = Path("runtime/evaluation-quarantine")
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
    registration_code_ttl_minutes: int = Field(default=10, ge=5, le=30)
    registration_code_resend_interval_seconds: int = Field(default=60, ge=30, le=900)
    registration_smtp_host: str | None = None
    registration_smtp_port: int = Field(default=587, ge=1, le=65535)
    registration_smtp_username: str | None = None
    registration_smtp_password: SecretStr | None = None
    registration_smtp_sender: str = "admin@skymoonzyj.l.cd"
    registration_smtp_use_tls: bool = True

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
        for field_name in (
            "field_encryption_master_key",
            "field_lookup_key",
            "session_token_pepper",
        ):
            secret = getattr(self, field_name)
            raw = secret.get_secret_value() if isinstance(secret, SecretStr) else str(secret)
            if not _URLSAFE_BASE64.fullmatch(raw):
                raise ValueError(f"{field_name} must be URL-safe Base64")
            try:
                decoded = base64.b64decode(raw, altchars=b"-_", validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError(f"{field_name} must be URL-safe Base64") from exc
            if len(decoded) != 32:
                raise ValueError(f"{field_name} must decode to 32 bytes")
        service_secret = self.evaluation_tool_service_token
        service_token = (
            service_secret.get_secret_value()
            if isinstance(service_secret, SecretStr)
            else str(service_secret)
        )
        if len(service_token) < 32 or any(
            character.isspace() for character in service_token
        ):
            raise ValueError(
                "evaluation_tool_service_token must be at least 32 non-whitespace characters"
            )
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
