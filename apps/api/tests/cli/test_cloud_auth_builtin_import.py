"""Synthetic fixed-protocol import fixtures; never performs real authorization."""

import importlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))
NOW = datetime(2026, 10, 10, tzinfo=UTC)


class Wire:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def imports():
    try:
        return importlib.import_module("cloud_auth.builtin_import")
    except ModuleNotFoundError:
        pytest.fail("fixed builtin import adapters are missing")


@pytest.mark.parametrize(
    "provider,response,method,url,params",
    [
        (
            "baidu",
            {"access_token": "new-access", "refresh_token": "new-refresh"},
            "GET",
            "https://api.oplist.org/baiduyun/renewapi",
            {"server_use": "true", "driver_txt": "baiduyun_go", "refresh_ui": "old-refresh"},
        ),
        (
            "115",
            {
                "state": 1,
                "code": 0,
                "data": {
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "expires_in": 7200,
                },
            },
            "POST",
            "https://passportapi.115.com/open/refreshToken",
            {"refresh_token": "old-refresh"},
        ),
    ],
)
def test_builtin_refresh_uses_only_frozen_protocol(provider, response, method, url, params):
    module = imports()
    wire = Wire(response)
    tokens = module.refresh_import(provider, "old-refresh", now=NOW, client=wire)
    assert wire.calls == [(method, url, {"params": params})]
    assert (tokens.access_token, tokens.refresh_token, tokens.refreshed_at) == (
        "new-access",
        "new-refresh",
        NOW,
    )
    assert tokens.provider == provider and tokens.mode == "openlist_builtin_import"
    assert (tokens.expires_at, tokens.expiry_source) == (
        (None, "unreported") if provider == "baidu" else (NOW + timedelta(seconds=7200), "provider")
    )
    assert "new-access" not in repr(tokens) and "new-refresh" not in repr(tokens)


@pytest.mark.parametrize(
    "provider,response",
    [
        ("baidu", {"refresh_token": "rotated", "text": "SECRET_BODY"}),
        ("baidu", {"access_token": "rotated", "refresh_token": "rotated", "expires_in": 0}),
        ("baidu", {"access_token": "rotated", "refresh_token": "rotated", "expires_in": "7200"}),
        ("115", {"state": 1, "data": {"access_token": "rotated", "refresh_token": "rotated"}}),
        ("115", {"state": 0, "code": 40140113, "message": "SECRET_BODY"}),
    ],
)
def test_invalid_refresh_never_invents_expiry_or_echoes_body(provider, response):
    from cloud_auth.provider_types import ProviderError

    with pytest.raises(ProviderError) as error:
        imports().refresh_import(provider, "SECRET_TOKEN", now=NOW, client=Wire(response))
    assert "SECRET" not in str(error.value) and "SECRET" not in repr(error.value)


@pytest.mark.parametrize(
    "provider,response,expected",
    [
        ("baidu", {"errno": 0, "uk": 12345, "netdisk_name": "name"}, "12345"),
        ("115", {"state": 1, "data": {"user_id": 67890, "user_name": "name"}}, "67890"),
    ],
)
def test_import_identity_requires_no_fabricated_token_expiry(provider, response, expected):
    wire = Wire(response)
    identity = imports().get_import_identity(provider, "access-fixture", client=wire)
    assert identity.account_id == expected and identity.provider == provider
    assert len(wire.calls) == 1


@pytest.mark.parametrize(
    "field,value", [("refreshed_at", None), ("refreshed_at", "date"), ("expires_at", "date")]
)
def test_imported_grant_rejects_non_datetime_with_stable_error(field, value):
    from cloud_auth.provider_types import ImportedTokenGrant, ProviderError

    values = {
        "provider": "115",
        "access_token": "a",
        "refresh_token": "r",
        "expiry_source": "provider",
        "refreshed_at": NOW,
        "expires_at": NOW + timedelta(hours=1),
    }
    values[field] = value
    with pytest.raises(ProviderError, match="provider_response_invalid"):
        ImportedTokenGrant(**values)


@pytest.mark.parametrize("provider", ["baidu", "115"])
def test_import_refresh_transport_error_does_not_echo_secret(provider, monkeypatch):
    from cloud_auth import http_client
    from cloud_auth.provider_types import ProviderError

    monkeypatch.setattr(
        http_client,
        "_connection",
        lambda host: (_ for _ in ()).throw(OSError("SECRET_NETWORK_BODY")),
    )
    with pytest.raises(ProviderError, match="provider_network_error") as error:
        imports().refresh_import(provider, "SECRET_TOKEN", now=NOW)
    assert error.value.__context__ is None


def test_import_fixed_endpoints_keep_redirect_dns_and_size_guards(monkeypatch):
    from cloud_auth import http_client
    from cloud_auth.provider_types import ProviderError

    client = http_client.SafeHttpClient()
    with pytest.raises(ProviderError, match="provider_url_invalid"):
        client.request_json("GET", "https://evil.test/baiduyun/renewapi")
    with pytest.raises(ProviderError, match="provider_url_invalid"):
        client.request_json("GET", "https://api.oplist.org/baiduyun/renewapi?refresh_ui=SECRET")
    monkeypatch.setattr(
        http_client.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("127.0.0.1", 443))]
    )
    with pytest.raises(ProviderError, match="provider_url_invalid"):
        imports().refresh_import("baidu", "secret", now=NOW)


@pytest.mark.parametrize("provider", ["baidu", "115"])
@pytest.mark.parametrize(
    "status,body,headers,code",
    [
        (302, b"SECRET", {"Location": "https://evil.test/stolen"}, "provider_redirect_rejected"),
        (200, b"x" * 65537, {}, "provider_response_too_large"),
        (500, b"SECRET", {}, "provider_network_error"),
    ],
)
def test_builtin_endpoints_retain_transport_bounds_without_fallback(
    monkeypatch, provider, status, body, headers, code
):
    from cloud_auth import http_client
    from cloud_auth.provider_types import ProviderError

    from tests.cli.test_cloud_auth_providers import install_http

    connection = install_http(monkeypatch, http_client, status=status, body=body, headers=headers)
    with pytest.raises(ProviderError, match=code) as error:
        imports().refresh_import(provider, "SECRET_REFRESH", now=NOW)
    assert "SECRET" not in str(error.value)
    assert connection.request.call_count == 1
