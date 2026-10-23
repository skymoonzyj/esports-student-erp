from __future__ import annotations

from pathlib import Path

import pytest
from starlette.requests import Request

from app.core.client_ip import request_source_ip
from app.core.config import Settings


def _request(
    *,
    peer: str | None,
    forwarded: str | tuple[str, ...] | None,
    x_forwarded_for: str | None = None,
) -> Request:
    forwarded_values = (
        ()
        if forwarded is None
        else (forwarded,)
        if isinstance(forwarded, str)
        else forwarded
    )
    headers = [(b"x-real-ip", value.encode()) for value in forwarded_values]
    if x_forwarded_for is not None:
        headers.append((b"x-forwarded-for", x_forwarded_for.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": headers,
            "client": None if peer is None else (peer, 43123),
        }
    )


def test_untrusted_peer_cannot_override_socket_source() -> None:
    request = _request(peer="198.51.100.20", forwarded="203.0.113.10")

    assert request_source_ip(request, trusted_proxy_cidrs="127.0.0.1/32") == (
        "198.51.100.20"
    )


def test_trusted_proxy_can_supply_one_valid_client_ip() -> None:
    request = _request(peer="127.0.0.1", forwarded="2001:0db8::1")

    assert request_source_ip(request, trusted_proxy_cidrs="127.0.0.1/32") == (
        "2001:db8::1"
    )


def test_trusted_proxy_cidr_list_accepts_ipv6_peer_with_edge_whitespace() -> None:
    request = _request(
        peer="2001:0db8:0000:0000:0000:0000:0000:10",
        forwarded="192.0.2.45",
    )

    assert request_source_ip(
        request,
        trusted_proxy_cidrs=" 198.51.100.0/24, 2001:db8::/32 ",
    ) == "192.0.2.45"


def test_socket_source_is_normalized_when_proxy_is_not_trusted() -> None:
    request = _request(
        peer="2001:0db8:0000:0000:0000:0000:0000:20",
        forwarded="203.0.113.10",
    )

    assert request_source_ip(request, trusted_proxy_cidrs="") == "2001:db8::20"


def test_invalid_or_chained_forwarded_value_falls_back_to_proxy_source() -> None:
    for forwarded in ("", "   ", "not-an-ip", "203.0.113.1, 203.0.113.2"):
        request = _request(peer="127.0.0.1", forwarded=forwarded)

        assert request_source_ip(
            request,
            trusted_proxy_cidrs="127.0.0.1/32",
        ) == "127.0.0.1"


def test_repeated_x_real_ip_headers_fall_back_to_proxy_source() -> None:
    request = _request(
        peer="127.0.0.1",
        forwarded=("203.0.113.10", "203.0.113.11"),
    )

    assert request_source_ip(
        request,
        trusted_proxy_cidrs="127.0.0.1/32",
    ) == "127.0.0.1"


def test_missing_socket_client_is_unknown_and_never_trusts_headers() -> None:
    request = _request(peer=None, forwarded="203.0.113.10")

    assert request_source_ip(
        request,
        trusted_proxy_cidrs="0.0.0.0/0, ::/0",
    ) == "unknown"


def test_trusted_proxy_can_supply_a_forwarded_chain() -> None:
    request = _request(
        peer="127.0.0.1",
        forwarded=None,
        x_forwarded_for="203.0.113.10",
    )

    assert request_source_ip(
        request,
        trusted_proxy_cidrs="127.0.0.1/32",
    ) == "203.0.113.10"


@pytest.mark.parametrize("client_ip", ["203.0.113.10", "198.51.100.25", "2001:db8::25"])
def test_nas_proxy_chain_retains_each_clients_source(client_ip: str) -> None:
    request = _request(
        peer="172.31.0.6", forwarded="172.31.0.7",
        x_forwarded_for=f"{client_ip}, 172.31.0.7",
    )
    assert request_source_ip(request, trusted_proxy_cidrs="172.31.0.0/16") == client_ip


def test_client_cannot_prepend_a_fake_ip_to_trusted_proxy_chain() -> None:
    request = _request(
        peer="172.31.0.6", forwarded="192.0.2.99",
        x_forwarded_for="192.0.2.99, 203.0.113.10, 172.31.0.7",
    )
    assert request_source_ip(request, trusted_proxy_cidrs="172.31.0.0/16") == "203.0.113.10"


def test_untrusted_peer_cannot_supply_a_forwarded_chain() -> None:
    request = _request(
        peer="198.51.100.20", forwarded="203.0.113.10",
        x_forwarded_for="203.0.113.10, 172.31.0.7",
    )
    assert request_source_ip(request, trusted_proxy_cidrs="172.31.0.0/16") == "198.51.100.20"


@pytest.mark.parametrize("chain", ["", "not-an-ip", "203.0.113.10,", "172.31.0.7",
                                     ",".join(["203.0.113.10"] * 33)])
def test_invalid_or_unattributable_chain_falls_back_to_socket(chain: str) -> None:
    request = _request(peer="172.31.0.6", forwarded="203.0.113.10", x_forwarded_for=chain)
    assert request_source_ip(request, trusted_proxy_cidrs="172.31.0.0/16") == "172.31.0.6"


def test_duplicate_forwarded_chain_headers_fall_back_to_socket() -> None:
    request = _request(peer="172.31.0.6", forwarded=None, x_forwarded_for="203.0.113.10")
    request.scope["headers"].append((b"x-forwarded-for", b"198.51.100.20"))
    assert request_source_ip(request, trusted_proxy_cidrs="172.31.0.0/16") == "172.31.0.6"


def test_settings_reject_invalid_trusted_proxy_cidr(test_settings: Settings) -> None:
    with pytest.raises(ValueError, match="trusted_proxy_cidrs contains invalid CIDR"):
        Settings(
            **{
                **test_settings.model_dump(),
                "trusted_proxy_cidrs": "127.0.0.1/32, definitely-not-a-cidr",
            }
        )


def test_settings_normalizes_trusted_proxy_cidr_list(test_settings: Settings) -> None:
    settings = Settings(
        **{
            **test_settings.model_dump(),
            "trusted_proxy_cidrs": " 127.0.0.1/32, 2001:0db8::/32 ",
        }
    )

    assert settings.trusted_proxy_cidrs == "127.0.0.1/32,2001:db8::/32"


def test_settings_trusts_no_proxy_by_default(test_settings: Settings) -> None:
    assert test_settings.trusted_proxy_cidrs == ""


@pytest.mark.parametrize("cidr", ["0.0.0.0/0", "::/0"])
def test_production_rejects_catch_all_trusted_proxy_cidr(
    test_settings: Settings,
    cidr: str,
) -> None:
    settings = test_settings.model_copy(
        update={
            "app_env": "production",
            "session_cookie_secure": True,
            "trusted_proxy_cidrs": cidr,
            "erp_web_base_url": "https://erp.example.test",
            "evaluation_tool_standalone_base_url": "https://score.example.test",
            "evaluation_media_root": Path("/tmp/erp-evaluation-media"),
            "evaluation_media_quarantine_root": Path(
                "/tmp/erp-evaluation-quarantine"
            ),
        }
    )

    with pytest.raises(ValueError, match="production forbids catch-all trusted proxy"):
        settings.validate_runtime_secrets()


def test_settings_reads_trusted_proxy_cidrs_from_automatic_environment_name(
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = test_settings.model_dump()
    values.pop("trusted_proxy_cidrs")
    monkeypatch.setenv(
        "TRUSTED_PROXY_CIDRS",
        " 127.0.0.1/32, 2001:0db8::/32 ",
    )

    settings = Settings(**values)

    assert settings.trusted_proxy_cidrs == "127.0.0.1/32,2001:db8::/32"


def test_nginx_overwrites_x_real_ip_for_api_and_web() -> None:
    repository_root = Path(__file__).resolve().parents[5]
    nginx_config = (repository_root / "infra/nginx/nginx.conf").read_text(
        encoding="utf-8"
    )

    assert "map $http_x_real_ip $client_real_ip" not in nginx_config
    assert "$client_real_ip" not in nginx_config
    assert nginx_config.count("proxy_set_header X-Real-IP $remote_addr;") == 2


def test_nginx_does_not_restore_disabled_production_api_docs_via_spa() -> None:
    repository_root = Path(__file__).resolve().parents[5]
    nginx_config = (repository_root / "infra/nginx/nginx.conf").read_text(
        encoding="utf-8"
    )

    for location in ("/docs", "/redoc", "/openapi.json"):
        assert f"location = {location} {{\n            return 404;\n        }}" in nginx_config
    for location in ("/docs/", "/redoc/"):
        assert f"location ^~ {location} {{\n            return 404;\n        }}" in nginx_config
