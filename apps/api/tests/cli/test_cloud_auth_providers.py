"""Official protocol fixtures; no actual AppID, user authorization or network calls."""

import base64
import importlib
import json
import ssl
import struct
import sys
import zlib
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "scripts"))
NOW = datetime(2026, 10, 6, tzinfo=UTC)
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGNgAAAAAgAB"
    "SK+kcQAAAABJRU5ErkJggg=="
)


def modules():
    try:
        return tuple(importlib.import_module("cloud_auth." + n) for n in (
            "baidu", "drive115", "http_client", "provider_types"
        ))
    except ModuleNotFoundError:
        pytest.fail("Official cloud authorization adapters are not implemented")


class Wire:
    """Only external HTTP I/O is substituted; provider parsing remains real."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request_json(self, method, url, *, params=None, headers=None, read_timeout=15):
        self.calls.append((method, url, params, headers, read_timeout))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def request_png(self, url):
        self.calls.append(("PNG", url))
        return PNG


def baidu_start(**updates):
    return {
        "device_code": "fixture-device", "user_code": "fixture-user",
        "verification_url": "https://openapi.baidu.com/device",
        "qrcode_url": "https://openapi.baidu.com/device/qrcode/fixture/fixture-user",
        "expires_in": 1800, "interval": 1, **updates,
    }


def drive_start(**updates):
    return {"state": 1, "code": 0, "message": "", "data": {
        "uid": "fixture-device", "time": 1791244800, "sign": "fixture-sign",
        "qrcode": "https://115.com/scan/dg-fixture-device", **updates,
    }}


def test_poll_pending_has_no_tokens():
    baidu, _, _, _ = modules()
    wire = Wire(baidu_start(), {"error": "authorization_pending", "access_token": "LEAK"})
    provider = baidu.BaiduProvider(wire)
    grant = provider.start("owned-app", "owned-secret", NOW)
    result = provider.poll(grant, "owned-app", "owned-secret", NOW)
    assert result.status == "pending" and result.tokens is None
    assert grant.interval_seconds == 2
    assert grant.expires_at == NOW + timedelta(minutes=10)
    assert grant.qr.kind == "image" and grant.qr.content.startswith("data:image/png;base64,")
    assert "fixture-device" not in repr(grant)
    assert "LEAK" not in repr(result)


@pytest.mark.parametrize("error,status,retry", [
    ("slow_down", "throttled", 7), ("access_denied", "denied", None),
    ("expired_token", "expired", None), ("invalid_grant", "denied", None),
])
def test_baidu_poll_status(error, status, retry):
    baidu, _, _, _ = modules()
    provider = baidu.BaiduProvider(Wire(baidu_start(), {"error": error}))
    grant = provider.start("owned-app", "owned-secret", NOW)
    result = provider.poll(grant, "owned-app", "owned-secret", NOW)
    assert (result.status, result.retry_after_seconds, result.tokens) == (status, retry, None)


def test_baidu_token_exchange_keeps_secrets_private():
    baidu, _, _, _ = modules()
    wire = Wire(baidu_start(), {
        "access_token": "ACCESS_FIXTURE", "refresh_token": "REFRESH_FIXTURE",
        "expires_in": 7200, "session_secret": "IGNORE_SECRET",
    })
    provider = baidu.BaiduProvider(wire)
    grant = provider.start("owned-app", "owned-secret", NOW)
    result = provider.poll(grant, "owned-app", "owned-secret", NOW)
    assert result.status == "authorized"
    assert result.tokens.access_token == "ACCESS_FIXTURE"
    assert result.tokens.refresh_token == "REFRESH_FIXTURE"
    assert result.tokens.expires_at == NOW + timedelta(seconds=7200)
    assert "ACCESS_FIXTURE" not in repr(result) and "REFRESH_FIXTURE" not in repr(result)
    assert wire.calls[-1][2] == {
        "grant_type": "device_token", "code": "fixture-device",
        "client_id": "owned-app", "client_secret": "owned-secret",
    }


@pytest.mark.parametrize("provider_index", [0, 1])
def test_expired_grant_never_calls_vendor(provider_index):
    baidu, drive, _, _ = modules()
    wire = Wire(baidu_start() if provider_index == 0 else drive_start())
    provider = (baidu.BaiduProvider if provider_index == 0 else drive.Drive115Provider)(wire)
    grant = provider.start("owned-app", "owned-secret", NOW)
    calls = len(wire.calls)
    assert provider.poll(grant, "owned-app", "owned-secret", grant.expires_at).status == "expired"
    assert len(wire.calls) == calls


def test_115_challenge_is_urlsafe(monkeypatch):
    _, drive, _, _ = modules()
    # SHA256 of this verifier has both '+' and '/' in standard Base64.
    verifier = "0" + "a" * 43
    monkeypatch.setattr(drive.secrets, "token_urlsafe", lambda n: verifier)
    wire = Wire(drive_start())
    grant = drive.Drive115Provider(wire).start("owned-app", None, NOW)
    assert wire.calls[0][2] == {
        "client_id": "owned-app", "code_challenge_method": "sha256",
        "code_challenge": "zPVrxjf5C-MzToGd_3YYbtQ2LOe4ZT3q-LPgGrDza4A",
    }
    assert grant.secret_payload["code_verifier"] == verifier
    assert "a" * 43 not in repr(grant)
    assert grant.qr.kind == "payload" and grant.qr.official_open_url is None


@pytest.mark.parametrize("status", [0, 1])
def test_115_waiting_does_not_exchange_tokens(status):
    _, drive, _, _ = modules()
    wire = Wire(drive_start(), {"state": 1, "data": {"status": status, "access_token": "LEAK"}})
    provider = drive.Drive115Provider(wire)
    grant = provider.start("owned-app", None, NOW)
    result = provider.poll(grant, "owned-app", None, NOW)
    assert result.status == "pending" and result.tokens is None
    assert len(wire.calls) == 2 and wire.calls[-1][-1] == 35


def test_115_confirmed_exchanges_only_uid_and_verifier():
    _, drive, _, _ = modules()
    wire = Wire(drive_start(), {"state": 1, "data": {"status": 2}}, {
        "state": 1, "data": {"access_token": "ACCESS_FIXTURE",
        "refresh_token": "REFRESH_FIXTURE", "expires_in": 7200},
    })
    provider = drive.Drive115Provider(wire)
    grant = provider.start("owned-app", None, NOW)
    result = provider.poll(grant, "owned-app", None, NOW)
    assert result.status == "authorized" and result.tokens.access_token == "ACCESS_FIXTURE"
    assert wire.calls[-1][2] == {
        "uid": "fixture-device", "code_verifier": grant.secret_payload["code_verifier"]
    }
    assert "client_secret" not in wire.calls[-1][2]


@pytest.mark.parametrize("response,status", [
    ({"state": 0, "code": 0}, "expired"),
    ({"state": 1, "data": {"status": -2}}, "denied"),
    ({"state": 0, "code": 40140113, "message": "LEAK"}, "denied"),
    ({"state": 0, "code": 40140137, "message": "LEAK"}, "denied"),
])
def test_115_terminal_status(response, status):
    _, drive, _, _ = modules()
    provider = drive.Drive115Provider(Wire(drive_start(), response))
    grant = provider.start("owned-app", None, NOW)
    result = provider.poll(grant, "owned-app", None, NOW)
    assert result.status == status and result.tokens is None
    assert "LEAK" not in repr(result)


@pytest.mark.parametrize("url", [
    "http://115.com/scan/dg-fixture", "https://127.0.0.1/scan/dg-fixture",
    "https://115.com.evil.test/scan/dg-fixture", "https://user@115.com/scan/dg-fixture",
    "https://115.com:444/scan/dg-fixture", "<svg onload='LEAK'>",
    "https://115.com/scan/dg-fixture?access_token=LEAK",
    "https://115.com/scan/dg-%2fprivate", "https://115.com/scan/dg-fixture#LEAK",
    "https://115.com/scan/dg-fixture\n", "https://115.com/scan/dg-" + "x" * 3000,
])
def test_115_rejects_unsafe_qr(url):
    _, drive, _, types = modules()
    with pytest.raises(types.ProviderError, match="provider_qr_invalid"):
        drive.Drive115Provider(Wire(drive_start(qrcode=url))).start("owned-app", None, NOW)


@pytest.mark.parametrize("url", [
    "http://openapi.baidu.com/device/qrcode/a/b", "https://127.0.0.1/device/qrcode/a/b",
    "https://openapi.baidu.com/device/qrcode/%2e%2e/a",
    "https://openapi.baidu.com/device/qrcode/a/b?access_token=LEAK",
])
def test_baidu_qr_rejected_before_fetch(url):
    baidu, _, _, types = modules()
    wire = Wire(baidu_start(qrcode_url=url))
    with pytest.raises(types.ProviderError, match="provider_qr_invalid"):
        baidu.BaiduProvider(wire).start("owned-app", "owned-secret", NOW)
    assert all(call[0] != "PNG" for call in wire.calls)


@pytest.mark.parametrize("provider_index", [0, 1])
def test_network_and_retry_after_are_secret_free(provider_index):
    baidu, drive, _, types = modules()
    for code, retry in [("provider_network_error", 15), ("provider_throttled", 45)]:
        wire = Wire(baidu_start() if provider_index == 0 else drive_start(),
                    types.ProviderError(code, retry_after_seconds=retry))
        provider = (baidu.BaiduProvider if provider_index == 0 else drive.Drive115Provider)(wire)
        grant = provider.start("owned-app", "owned-secret", NOW)
        result = provider.poll(grant, "owned-app", "owned-secret", NOW)
        assert result.status == "throttled" and result.retry_after_seconds == retry
        assert result.tokens is None


@pytest.mark.parametrize("payload", [
    {"error": "LEAK", "error_description": "ACCESS_FIXTURE"},
    {"access_token": "ACCESS_FIXTURE", "expires_in": 7200},
])
def test_vendor_body_never_appears_in_errors(payload):
    baidu, _, _, types = modules()
    provider = baidu.BaiduProvider(Wire(baidu_start(), payload))
    grant = provider.start("owned-app", "owned-secret", NOW)
    with pytest.raises(types.ProviderError) as error:
        provider.poll(grant, "owned-app", "owned-secret", NOW)
    assert "LEAK" not in str(error.value) and "ACCESS_FIXTURE" not in repr(error.value)
    assert error.value.__cause__ is None


def install_http(monkeypatch, module, *, status=200, body=b"{}", headers=None, error=None):
    response = Mock(status=status)
    response.getheaders.return_value = list((headers or {}).items())
    response.read.side_effect = lambda n: body[:n]
    connection = Mock()
    connection.getresponse.return_value = response
    if error:
        connection.connect.side_effect = error
    monkeypatch.setattr(module, "_connection", lambda host: connection)
    return connection


@pytest.mark.parametrize("url", [
    "http://openapi.baidu.com/oauth/2.0/token", "https://127.0.0.1/oauth/2.0/token",
    "https://openapi.baidu.com/other", "https://openapi.baidu.com:444/oauth/2.0/token",
    "https://openapi.baidu.com/oauth/2.0/token?x=1", "https://user@openapi.baidu.com/oauth/2.0/token",
])
def test_transport_rejects_unlisted_requests(url, monkeypatch):
    _, _, http, types = modules()
    connection = install_http(monkeypatch, http)
    with pytest.raises(types.ProviderError, match="provider_url_invalid"):
        http.SafeHttpClient().request_json("GET", url)
    connection.connect.assert_not_called()


@pytest.mark.parametrize("status,body,headers,code", [
    (302, b"LEAK", {"Location": "https://127.0.0.1/stolen"}, "provider_redirect_rejected"),
    (200, b"x" * 65537, {}, "provider_response_too_large"),
    (200, b"LEAK", {}, "provider_response_invalid"),
    (500, b"LEAK", {}, "provider_network_error"),
    (429, b"LEAK", {"Retry-After": "42"}, "provider_throttled"),
])
def test_transport_bounds_and_redacts(status, body, headers, code, monkeypatch):
    _, _, http, types = modules()
    connection = install_http(monkeypatch, http, status=status, body=body, headers=headers)
    with pytest.raises(types.ProviderError) as error:
        http.SafeHttpClient().request_json("GET", "https://openapi.baidu.com/oauth/2.0/token")
    assert error.value.code == code and "LEAK" not in str(error.value)
    if status == 429:
        assert error.value.retry_after_seconds == 42
    connection.close.assert_called_once()


def test_transport_timeout_discards_raw_exception(monkeypatch):
    _, _, http, types = modules()
    install_http(monkeypatch, http, error=TimeoutError("LEAK token"))
    with pytest.raises(types.ProviderError, match="provider_network_error") as error:
        http.SafeHttpClient().request_json("GET", "https://openapi.baidu.com/oauth/2.0/token")
    assert error.value.__cause__ is None and "LEAK" not in str(error.value)


def test_transport_read_limits_and_get_form_encoding(monkeypatch):
    _, _, http, _ = modules()
    connection = install_http(monkeypatch, http, body=json.dumps({"state": 1}).encode())
    result = http.SafeHttpClient().request_json("GET", "https://qrcodeapi.115.com/get/status/",
                                             params={"uid": "a+b/c"}, read_timeout=35)
    assert result == {"state": 1}
    assert connection.request.call_args.args[:2] == ("GET", "/get/status/?uid=a%2Bb%2Fc")
    connection.sock.settimeout.assert_called_once_with(35)


@pytest.mark.parametrize("body", [b"<svg/>", b"<html>LEAK</html>", PNG + b"<svg/>"])
def test_transport_rejects_non_png_qr(monkeypatch, body):
    _, _, http, types = modules()
    install_http(monkeypatch, http, body=body, headers={"Content-Type": "image/png"})
    with pytest.raises(types.ProviderError, match="provider_qr_invalid"):
        http.SafeHttpClient().request_png("https://openapi.baidu.com/device/qrcode/a/b")


def test_transport_accepts_bounded_valid_png(monkeypatch):
    _, _, http, _ = modules()
    install_http(monkeypatch, http, body=PNG, headers={"Content-Type": "image/png"})
    assert http.SafeHttpClient().request_png("https://openapi.baidu.com/device/qrcode/a/b") == PNG


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["10.0.0.1"], ["::1"],
                                       ["8.8.8.8", "192.168.1.1"]])
def test_transport_rejects_private_dns(monkeypatch, addresses):
    _, _, http, types = modules()
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda *a, **k: [
        (http.socket.AF_INET, http.socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in addresses
    ])
    with pytest.raises(types.ProviderError, match="provider_url_invalid"):
        http._connection("openapi.baidu.com").connect()


@pytest.mark.parametrize("provider_index", [0, 1])
def test_identity_uses_only_token_bound_official_endpoint(provider_index):
    baidu, drive, _, types = modules()
    wire = Wire({"errno": 0, "uk": 123456, "netdisk_name": "测试账号"} if provider_index == 0
                else {"state": True, "data": {"user_id": 123456, "user_name": "测试账号"}})
    provider = (baidu.BaiduProvider if provider_index == 0 else drive.Drive115Provider)(wire)
    tokens = types.TokenGrant("ACCESS_FIXTURE", "REFRESH_FIXTURE", NOW + timedelta(hours=2))
    identity = provider.get_identity(tokens)
    assert identity.account_id == "123456" and identity.display_name == "测试账号"
    assert identity.provider == ("baidu" if provider_index == 0 else "115")
    assert "123456" not in repr(identity)
    if provider_index == 0:
        assert wire.calls[0][1:3] == ("https://pan.baidu.com/rest/2.0/xpan/nas",
                                     {"method": "uinfo", "access_token": "ACCESS_FIXTURE"})
    else:
        assert wire.calls[0][1] == "https://proapi.115.com/open/user/info"
        assert wire.calls[0][3] == {"Authorization": "Bearer ACCESS_FIXTURE"}


@pytest.mark.parametrize("provider_index", [0, 1])
def test_identity_missing_id_or_failed_response_is_not_fabricated(provider_index):
    baidu, drive, _, types = modules()
    provider = (baidu.BaiduProvider if provider_index == 0 else drive.Drive115Provider)(Wire(
        {"errno": 0, "netdisk_name": "LEAK"} if provider_index == 0 else
        {"state": False, "message": "LEAK", "data": {"user_id": 123}}
    ))
    with pytest.raises(types.ProviderError) as error:
        provider.get_identity(types.TokenGrant("ACCESS_FIXTURE", "REFRESH_FIXTURE", NOW))
    assert "LEAK" not in str(error.value)


@pytest.mark.parametrize("provider_index,response", [
    (0, {"error": {"LEAK": "ACCESS_FIXTURE"}}),
    (1, {"state": 0, "code": {"LEAK": "ACCESS_FIXTURE"}}),
    (1, {"state": "1", "code": 0, "data": {"status": 2}}),
    (1, {"state": 1.0, "code": 0, "data": {"status": 2}}),
])
def test_malformed_vendor_types_have_only_stable_errors(provider_index, response):
    baidu, drive, _, types = modules()
    provider = (baidu.BaiduProvider if provider_index == 0 else drive.Drive115Provider)(Wire(
        baidu_start() if provider_index == 0 else drive_start(), response,
    ))
    grant = provider.start("owned-app", "owned-secret", NOW)
    with pytest.raises(types.ProviderError) as error:
        provider.poll(grant, "owned-app", "owned-secret", NOW)
    assert "LEAK" not in repr(error.value)


def test_tls_verifies_official_hostname_and_does_not_use_ambient_proxy(monkeypatch):
    _, _, http, _ = modules()
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9999")
    connection = http._connection("openapi.baidu.com")
    assert connection._context.check_hostname
    assert connection._context.verify_mode == ssl.CERT_REQUIRED
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda *a, **k: [
        (http.socket.AF_INET, http.socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))
    ])
    raw = Mock()
    monkeypatch.setattr(http.socket, "socket", lambda *a: raw)
    context = Mock()
    connection._context = context
    connection.connect()
    raw.connect.assert_called_once_with(("8.8.8.8", 443))
    raw.settimeout.assert_called_once_with(5)
    context.wrap_socket.assert_called_once_with(raw, server_hostname="openapi.baidu.com")


def test_115_pkce_verifier_rejects_short_corrupted_saved_material():
    _, drive, _, types = modules()
    provider = drive.Drive115Provider(Wire(drive_start(), {"state": 1, "data": {"status": 2}}))
    grant = provider.start("owned-app", None, NOW)
    grant.secret_payload["code_verifier"] = "x"
    with pytest.raises(types.ProviderError, match="provider_response_invalid"):
        provider.poll(grant, "owned-app", None, NOW)


def rgb_png(width, height, idat_chunks):
    """Valid uncompressed RGB PNG, with IDAT split to exercise exact byte boundaries."""
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data)))

    compressed = zlib.compress((b"\0" + b"\0" * (width * 3)) * height, level=0)
    stride = len(compressed) // idat_chunks
    parts = [compressed[i * stride:(i + 1) * stride] for i in range(idat_chunks - 1)]
    parts.append(compressed[(idat_chunks - 1) * stride:])
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + b"".join(chunk(b"IDAT", part) for part in parts) + chunk(b"IEND", b""))


def test_baidu_largest_qr_round_trips_actual_bridge_contract():
    from app.operations.cloud_auth_bridge import CompleteRequest

    baidu, _, _, _ = modules()
    image = rgb_png(111, 103, 127)
    assert len(image) == 35982
    wire = Wire(baidu_start(device_code="d" * 1024))
    wire.request_png = lambda url: image
    grant = baidu.BaiduProvider(wire).start("owned-app", "owned-secret", NOW)
    request = CompleteRequest.model_validate({
        "action": "complete", "fence": {
            "session_id": UUID(int=1), "session_revision": 1, "app_revision": 1,
            "target_revision": 0, "lease_id": UUID(int=2),
        }, "result": {"action": "create_qr", "grant": asdict(grant)},
    })
    raw = request.model_dump_json().encode()
    # Keep at least 14 KiB free for the surrounding 64-KiB private RPC envelope.
    assert len(raw) <= 50 * 1024
    parsed = CompleteRequest.model_validate_json(raw)
    assert parsed.result.grant.qr.content == grant.qr.content
    assert len(grant.qr.content) == 47998


@pytest.mark.parametrize("width,height,chunks,size", [
    (110, 101, 208, 35983), (111, 111, 1, 37142),
])
def test_baidu_rejects_qr_that_would_overflow_shared_content(width, height, chunks, size):
    baidu, _, _, types = modules()
    image = rgb_png(width, height, chunks)
    assert len(image) == size
    wire = Wire(baidu_start())
    wire.request_png = lambda url: image
    with pytest.raises(types.ProviderError, match="provider_qr_invalid"):
        baidu.BaiduProvider(wire).start("owned-app", "owned-secret", NOW)


@pytest.mark.parametrize("provider_index", [0, 1])
@pytest.mark.parametrize("delay,status,retry", [
    (600, "throttled", 600), (601, "expired", None), (99999999, "expired", None),
    (999999999, "expired", None),
])
@pytest.mark.parametrize("date_header", [False, True])
def test_retry_after_above_bridge_bound_ends_grant_without_early_retry(
        monkeypatch, provider_index, delay, status, retry, date_header):
    from app.operations.cloud_auth_schemas import PollResult

    baidu, drive, http, _ = modules()

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(http, "datetime", Clock)
    wire = Wire(baidu_start() if provider_index == 0 else drive_start())
    provider = (baidu.BaiduProvider if provider_index == 0 else drive.Drive115Provider)(wire)
    grant = provider.start("owned-app", "owned-secret", NOW)
    header = format_datetime(NOW + timedelta(seconds=delay), usegmt=True) if date_header else str(delay)
    connection = install_http(monkeypatch, http, status=429, body=b"LEAK",
                              headers={"Retry-After": header})
    provider.client = http.SafeHttpClient()
    result = provider.poll(grant, "owned-app", "owned-secret", NOW)
    assert (result.status, result.retry_after_seconds, result.tokens) == (status, retry, None)
    assert PollResult.model_validate_json(
        PollResult.model_validate(asdict(result)).model_dump_json()
    ).status == status
    connection.request.assert_called_once()


def test_retry_after_http_date_never_rounds_down(monkeypatch):
    baidu, _, http, _ = modules()

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW + timedelta(milliseconds=500)

    monkeypatch.setattr(http, "datetime", Clock)
    provider = baidu.BaiduProvider(Wire(baidu_start()))
    grant = provider.start("owned-app", "owned-secret", NOW)
    install_http(monkeypatch, http, status=429, headers={
        "Retry-After": format_datetime(NOW + timedelta(seconds=601), usegmt=True),
    })
    provider.client = http.SafeHttpClient()
    result = provider.poll(grant, "owned-app", "owned-secret", NOW)
    assert result.status == "expired" and result.retry_after_seconds is None


@pytest.mark.parametrize("interval,status,retry", [
    (595, "throttled", 600), (596, "expired", None), (600, "expired", None),
])
def test_baidu_slow_down_never_overflows_bridge_retry_contract(interval, status, retry):
    from app.operations.cloud_auth_schemas import PollResult

    baidu, _, _, _ = modules()
    provider = baidu.BaiduProvider(Wire(baidu_start(interval=interval), {"error": "slow_down"}))
    grant = provider.start("owned-app", "owned-secret", NOW)
    result = provider.poll(grant, "owned-app", "owned-secret", NOW)
    assert (result.status, result.retry_after_seconds, result.tokens) == (status, retry, None)
    assert PollResult.model_validate(asdict(result)).status == status


def test_nested_json_recursion_is_stable_error_without_raw_context(monkeypatch):
    _, _, http, types = modules()
    body = b'{"LEAK":' + b"[" * 10000 + b"0" + b"]" * 10000 + b"}"
    connection = install_http(monkeypatch, http, body=body)
    with pytest.raises(types.ProviderError, match="provider_response_invalid") as error:
        http.SafeHttpClient().request_json("GET", "https://openapi.baidu.com/oauth/2.0/token")
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert error.value.args == ("provider_response_invalid",)
    assert "LEAK" not in repr(error.value)
    connection.close.assert_called_once()
