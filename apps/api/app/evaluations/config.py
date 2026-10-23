from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit
from uuid import UUID

if TYPE_CHECKING:
    from app.core.config import Settings


def _validated_origin(value: str, *, production: bool) -> str:
    if "%" in value or "\\" in value or any(character.isspace() for character in value):
        raise ValueError("invalid evaluation URL")
    if "?" in value or "#" in value:
        raise ValueError("evaluation URL cannot contain query or fragment markers")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid evaluation URL port") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("evaluation URL must be absolute HTTP(S)")
    if port == 0 or (port is None and parsed.netloc.endswith(":")):
        raise ValueError("invalid evaluation URL port")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("evaluation URL cannot contain credentials, query, or fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("evaluation base URL cannot contain a path")
    if production and parsed.scheme != "https":
        raise ValueError("production evaluation URLs require HTTPS")
    return value.rstrip("/")


def _validated_embedded_path(value: str) -> str:
    if (
        not value.startswith("/")
        or value.startswith("//")
        or ".." in value
        or "%" in value
        or "?" in value
        or "#" in value
        or "\\" in value
        or any(character.isspace() for character in value)
    ):
        raise ValueError("invalid evaluation embedded path")
    normalized = value.rstrip("/")
    if not normalized:
        raise ValueError("invalid evaluation embedded path")
    return normalized


@dataclass(frozen=True)
class EvaluationBridgeConfig:
    erp_web_base_url: str
    standalone_base_url: str
    embedded_path: str
    request_ttl_seconds: int
    ticket_ttl_seconds: int

    @classmethod
    def from_settings(cls, settings: Settings) -> EvaluationBridgeConfig:
        production = settings.app_env == "production"
        return cls(
            erp_web_base_url=_validated_origin(
                settings.erp_web_base_url,
                production=production,
            ),
            standalone_base_url=_validated_origin(
                settings.evaluation_tool_standalone_base_url,
                production=production,
            ),
            embedded_path=_validated_embedded_path(
                settings.evaluation_tool_embedded_path
            ),
            request_ttl_seconds=settings.evaluation_login_request_ttl_seconds,
            ticket_ttl_seconds=settings.evaluation_login_ticket_ttl_seconds,
        )

    def authorize_url(self, request_id: UUID) -> str:
        return f"{self.erp_web_base_url}/evaluation-connect/{request_id}"

    def callback_url(self, mode: str) -> str:
        if mode == "standalone":
            return f"{self.standalone_base_url}/auth/erp/callback"
        if mode == "embedded":
            return f"{self.erp_web_base_url}{self.embedded_path}/auth/erp/callback"
        raise ValueError("unsupported evaluation tool mode")

    def start_url(self, mode: str) -> str:
        query = "mode=" + mode + "&return_path=" + quote("/", safe="")
        if mode == "standalone":
            return f"{self.standalone_base_url}/auth/erp/start?{query}"
        if mode == "embedded":
            return f"{self.embedded_path}/auth/erp/start?{query}"
        raise ValueError("unsupported evaluation tool mode")
