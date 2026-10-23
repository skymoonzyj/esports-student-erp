"""115 official device PKCE flow, checked 2026-10-06 (no AppSecret needed).

Official sources under https://open.115.com/doc/115开放平台/ (append ?raw=true):
接入指南/接入授权/手机扫码授权PKCE模式/获取设备码和二维码内容.md
接入指南/接入授权/手机扫码授权PKCE模式/轮询二维码状态.md
接入指南/接入授权/手机扫码授权PKCE模式/获取access_token.md
接入指南/授权错误码.md; API列表/用户管理/用户信息.md

Use official URL-safe SHA256 Base64, not standard Base64 from third-party SDKs.
The public docs omit QR lifetime/payload example/negative status enumeration.
We use a conservative 300s lifetime when omitted, cap supplied lifetime at 600s,
and reject unrecognized QR shapes. Negative statuses remain owner-app test items.
"""

import base64
import hashlib
import re
import secrets
from datetime import datetime, timedelta

from .http_client import SafeHttpClient, validate_115_qr
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

# Official terminal authorization codes.
TERMINAL_CODES = {
    40101017,
    40140100,
    40140104,
    40140106,
    40140107,
    40140108,
    40140109,
    40140110,
    40140111,
    40140113,
    40140114,
    40140115,
    40140116,
    40140118,
    40140119,
    40140120,
    40140122,
    40140123,
    40140124,
    40140125,
    40140126,
    40140133,
    40140134,
    40140135,
    40140136,
    40140137,
}


def _error(data: dict):
    code = data.get("code", 0)
    if type(code) is not int or type(data.get("state")) not in {int, bool}:
        raise ProviderError("provider_response_invalid")
    if code in TERMINAL_CODES:
        raise ProviderError("provider_reauthorize")
    if code in {40110000, 40140121}:
        raise ProviderError("provider_network_error", retry_after_seconds=15)
    if code == 40140117:
        raise ProviderError("provider_throttled", retry_after_seconds=60)
    if code:
        raise ProviderError("provider_response_invalid")


def _data(data: dict) -> dict:
    _error(data)
    if data.get("state") not in (1, True) or not isinstance(data.get("data"), dict):
        raise ProviderError("provider_response_invalid")
    return data["data"]


class Drive115Provider:
    def __init__(self, client=None):
        self.client = client if client is not None else SafeHttpClient()

    def start(self, client_id: str, client_secret: str | None, now: datetime) -> DeviceGrant:
        if not client_id:
            raise ProviderError("provider_app_not_configured")
        text_field({"id": client_id}, "id", maximum=256)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        response = self.client.request_json(
            "POST",
            "https://passportapi.115.com/open/authDeviceCode",
            params={
                "client_id": client_id,
                "code_challenge": challenge.decode().rstrip("="),
                "code_challenge_method": "sha256",
            },
        )
        data = _data(response)
        uid = text_field(data, "uid", maximum=1024)
        sign = text_field(data, "sign", maximum=1024)
        timestamp = integer_field(data, "time", maximum=100000000000)
        qr_content = validate_115_qr(data.get("qrcode"))
        seconds = min(600, integer_field(data, "expires_in")) if "expires_in" in data else 300
        interval = max(2, integer_field(data, "interval", maximum=600)) if "interval" in data else 2
        expires_at = now + timedelta(seconds=seconds)
        return DeviceGrant(
            {"uid": uid, "time": timestamp, "sign": sign, "code_verifier": verifier},
            QrPresentation("payload", qr_content, None, expires_at),
            expires_at,
            interval,
        )

    def poll(
        self, grant: DeviceGrant, client_id: str, client_secret: str | None, now: datetime
    ) -> PollResult:
        if now >= grant.expires_at:
            return PollResult("expired")
        params = {
            "uid": text_field(grant.secret_payload, "uid", maximum=1024),
            "time": integer_field(grant.secret_payload, "time", maximum=100000000000),
            "sign": text_field(grant.secret_payload, "sign", maximum=1024),
        }
        try:
            response = self.client.request_json(
                "GET", "https://qrcodeapi.115.com/get/status/", params=params, read_timeout=35
            )
            _error(response)
            if response.get("state") in (0, False):
                return PollResult("expired")
            if response.get("state") not in (1, True):
                raise ProviderError("provider_response_invalid")
            data = response.get("data")
            if data is None or data == {}:
                return PollResult("pending", grant.interval_seconds)
            if not isinstance(data, dict):
                raise ProviderError("provider_response_invalid")
            status = data.get("status")
            if type(status) is not int:
                raise ProviderError("provider_response_invalid")
            if status in (0, 1):
                return PollResult("pending", grant.interval_seconds)
            if status == -2:
                return PollResult("denied")
            if status == -1:
                return PollResult("expired")
            if status != 2:
                raise ProviderError("provider_response_invalid")
            verifier = text_field(grant.secret_payload, "code_verifier", maximum=128)
            if not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier):
                raise ProviderError("provider_response_invalid")
            tokens = self.client.request_json(
                "POST",
                "https://passportapi.115.com/open/deviceCodeToToken",
                params={"uid": params["uid"], "code_verifier": verifier},
            )
            return PollResult("authorized", tokens=token_grant(_data(tokens), now))
        except ProviderError as error:
            if error.code == "provider_reauthorize":
                return PollResult("denied")
            return retry_result(error, grant.interval_seconds)

    def get_identity(self, tokens: TokenGrant) -> AccountIdentity:
        return self.get_import_identity(tokens.access_token)

    def get_import_identity(self, access_token: str) -> AccountIdentity:
        response = self.client.request_json(
            "GET",
            "https://proapi.115.com/open/user/info",
            headers={"Authorization": "Bearer " + text_field({"token": access_token}, "token")},
        )
        try:
            data = _data(response)
        except ProviderError:
            raise ProviderError("provider_identity_unconfirmed") from None
        return account_identity("115", data, "user_id", "user_name")

    def refresh_import(self, refresh_token: str, *, now: datetime):
        response = self.client.request_json(
            "POST",
            "https://passportapi.115.com/open/refreshToken",
            params={"refresh_token": text_field({"token": refresh_token}, "token")},
        )
        return imported_token_grant("115", _data(response), now)
