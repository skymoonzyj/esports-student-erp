"""Baidu official DeviceCode/DeviceToken flow, checked 2026-10-06.

Sources (Baidu-maintained SDK):
https://github.com/baidu-netdisk/baidu-drive-sdk-go/blob/main/baidudriver/api/auth_device_code.go
https://github.com/baidu-netdisk/baidu-drive-sdk-go/blob/main/baidudriver/api/auth_device_token.go
https://github.com/baidu-netdisk/baidu-drive-sdk-go/blob/main/baidudriver/api/nas_uinfo.go
QR image/open URL shapes still require validation using the owner's approved application.
"""

import base64
from datetime import datetime, timedelta

from .http_client import (
    SafeHttpClient,
    validate_baidu_open_url,
    validate_baidu_qr,
    validate_png,
)
from .provider_types import (
    AccountIdentity,
    DeviceGrant,
    PollResult,
    ProviderError,
    QrPresentation,
    TokenGrant,
    account_identity,
    imported_token_grant,
    integer_field,
    retry_result,
    text_field,
    token_grant,
)


class BaiduProvider:
    def __init__(self, client=None):
        self.client = client if client is not None else SafeHttpClient()

    @staticmethod
    def _app(client_id, client_secret):
        if not client_id or not client_secret:
            raise ProviderError("provider_app_not_configured")
        text_field({"id": client_id}, "id", maximum=256)
        text_field({"secret": client_secret}, "secret", maximum=1024)

    def start(self, client_id: str, client_secret: str | None, now: datetime) -> DeviceGrant:
        self._app(client_id, client_secret)
        data = self.client.request_json(
            "GET",
            "https://openapi.baidu.com/oauth/2.0/device/code",
            params={
                "response_type": "device_code",
                "client_id": client_id,
                "scope": "basic,netdisk",
            },
        )
        if "error" in data:
            raise ProviderError("provider_reauthorize")
        device_code = text_field(data, "device_code", maximum=1024)
        text_field(data, "user_code", maximum=128)
        expires_at = now + timedelta(seconds=min(600, integer_field(data, "expires_in")))
        interval = max(2, integer_field(data, "interval", maximum=600))
        qr_url = validate_baidu_qr(data.get("qrcode_url"))
        open_url = validate_baidu_open_url(data.get("verification_url"))
        png = validate_png(self.client.request_png(qr_url))
        qr = QrPresentation(
            "image", "data:image/png;base64," + base64.b64encode(png).decode(), open_url, expires_at
        )
        return DeviceGrant({"device_code": device_code}, qr, expires_at, interval)

    def poll(
        self, grant: DeviceGrant, client_id: str, client_secret: str | None, now: datetime
    ) -> PollResult:
        if now >= grant.expires_at:
            return PollResult("expired")
        self._app(client_id, client_secret)
        try:
            data = self.client.request_json(
                "GET",
                "https://openapi.baidu.com/oauth/2.0/token",
                params={
                    "grant_type": "device_token",
                    "code": text_field(grant.secret_payload, "device_code", maximum=1024),
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
        except ProviderError as error:
            return retry_result(error, grant.interval_seconds)
        error = data.get("error")
        if error is not None and not isinstance(error, str):
            raise ProviderError("provider_response_invalid")
        if error == "authorization_pending":
            return PollResult("pending", grant.interval_seconds)
        if error == "slow_down":
            return retry_result(
                ProviderError("provider_throttled", retry_after_seconds=grant.interval_seconds + 5),
                grant.interval_seconds,
            )
        if error in {
            "access_denied",
            "authorization_declined",
            "invalid_grant",
            "invalid_client",
            "invalid_client_secret",
        }:
            return PollResult("denied")
        if error in {"expired_token", "code_expired"}:
            return PollResult("expired")
        if error:
            raise ProviderError("provider_response_invalid")
        return PollResult("authorized", tokens=token_grant(data, now))

    def get_identity(self, tokens: TokenGrant) -> AccountIdentity:
        return self.get_import_identity(tokens.access_token)

    def get_import_identity(self, access_token: str) -> AccountIdentity:
        data = self.client.request_json(
            "GET",
            "https://pan.baidu.com/rest/2.0/xpan/nas",
            params={
                "method": "uinfo",
                "access_token": text_field({"token": access_token}, "token"),
            },
        )
        if data.get("errno") != 0 or type(data.get("errno")) is not int:
            raise ProviderError("provider_identity_unconfirmed")
        return account_identity("baidu", data, "uk", "netdisk_name")

    def refresh_import(self, refresh_token: str, *, now: datetime):
        data = self.client.request_json(
            "GET",
            "https://api.oplist.org/baiduyun/renewapi",
            params={
                "server_use": "true",
                "driver_txt": "baiduyun_go",
                "refresh_ui": text_field({"token": refresh_token}, "token"),
            },
        )
        return imported_token_grant("baidu", data, now)
