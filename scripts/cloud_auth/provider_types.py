"""Private host contracts. These objects must never be serialized into public API views."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal


class ProviderError(Exception):
    """Stable errors only; never retain vendor bodies, request URLs or causes."""

    def __init__(self, code: str, *, retry_after_seconds: int | None = None):
        allowed = {
            "provider_app_not_configured",
            "provider_url_invalid",
            "provider_qr_invalid",
            "provider_redirect_rejected",
            "provider_response_too_large",
            "provider_response_invalid",
            "provider_network_error",
            "provider_throttled",
            "provider_reauthorize",
            "provider_identity_unconfirmed",
        }
        self.code = code if code in allowed else "provider_response_invalid"
        self.retry_after_seconds = retry_after_seconds
        super().__init__(self.code)


@dataclass(frozen=True)
class QrPresentation:
    kind: Literal["image", "payload"]
    content: str = field(repr=False)
    official_open_url: str | None = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True)
class DeviceGrant:
    secret_payload: dict = field(repr=False)
    qr: QrPresentation
    expires_at: datetime
    interval_seconds: int


@dataclass(frozen=True)
class TokenGrant:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True)
class ImportedTokenGrant:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: datetime | None
    expiry_source: Literal["provider", "unreported"]
    refreshed_at: datetime
    provider: Literal["baidu", "115"]
    mode: Literal["openlist_builtin_import"] = "openlist_builtin_import"

    def __post_init__(self):
        if self.provider not in ("baidu", "115") or self.mode != "openlist_builtin_import":
            raise ProviderError("provider_response_invalid")
        if (
            not isinstance(self.refreshed_at, datetime)
            or self.refreshed_at.tzinfo is None
            or self.refreshed_at.utcoffset() is None
        ):
            raise ProviderError("provider_response_invalid")
        if self.expiry_source == "unreported":
            if self.provider != "baidu" or self.expires_at is not None:
                raise ProviderError("provider_response_invalid")
        elif (
            self.expiry_source != "provider"
            or not isinstance(self.expires_at, datetime)
            or self.expires_at.tzinfo is None
            or self.expires_at.utcoffset() is None
            or self.expires_at <= self.refreshed_at
        ):
            raise ProviderError("provider_response_invalid")


def imported_token_grant(
    provider: Literal["baidu", "115"], data: dict, now: datetime
) -> ImportedTokenGrant:
    expires_at = None
    if "expires_in" in data:
        expires_at = now + timedelta(seconds=integer_field(data, "expires_in"))
    elif provider != "baidu":
        raise ProviderError("provider_response_invalid")
    return ImportedTokenGrant(
        text_field(data, "access_token"),
        text_field(data, "refresh_token"),
        expires_at,
        "provider" if expires_at else "unreported",
        now,
        provider,
    )


@dataclass(frozen=True)
class PollResult:
    status: Literal["pending", "authorized", "denied", "expired", "throttled"]
    retry_after_seconds: int | None = None
    tokens: TokenGrant | None = field(default=None, repr=False)


@dataclass(frozen=True)
class AccountIdentity:
    provider: Literal["baidu", "115"]
    account_id: str = field(repr=False)
    display_name: str | None = field(default=None, repr=False)


def text_field(data: dict, key: str, *, maximum: int = 4096) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ProviderError("provider_response_invalid")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ProviderError("provider_response_invalid")
    return value


def integer_field(data: dict, key: str, *, maximum: int = 315360000) -> int:
    value = data.get(key)
    if type(value) is not int or not 0 < value <= maximum:
        raise ProviderError("provider_response_invalid")
    return value


def token_grant(data: dict, now: datetime) -> TokenGrant:
    return TokenGrant(
        text_field(data, "access_token"),
        text_field(data, "refresh_token"),
        now + timedelta(seconds=integer_field(data, "expires_in")),
    )


def retry_result(error: ProviderError, interval: int) -> PollResult:
    if error.code not in {"provider_network_error", "provider_throttled"}:
        raise error from None
    delay = max(2, interval, error.retry_after_seconds or 15)
    # Grants live at most 600s. A longer wait cannot fit this grant or the peer
    # scheduling contract: end it, never clip the vendor's required delay.
    if delay > 600:
        return PollResult("expired")
    return PollResult("throttled", delay)


def account_identity(
    provider: Literal["baidu", "115"], data: dict, id_key: str, name_key: str
) -> AccountIdentity:
    account_id = data.get(id_key)
    if type(account_id) is int and account_id > 0:
        account_id = str(account_id)
    if not isinstance(account_id, str) or not account_id.isascii() or not account_id.isdecimal():
        raise ProviderError("provider_identity_unconfirmed")
    if len(account_id) > 32 or int(account_id) <= 0:
        raise ProviderError("provider_identity_unconfirmed")
    name = data.get(name_key)
    if name is not None and (
        not isinstance(name, str)
        or len(name) > 128
        or any(ord(c) < 32 or ord(c) == 127 for c in name)
    ):
        name = None
    return AccountIdentity(provider, account_id, name or None)
