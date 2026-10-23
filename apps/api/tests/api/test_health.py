from __future__ import annotations

import base64
from typing import Annotated
from uuid import UUID

import pytest
from fastapi import Query

from app.core.config import Settings
from app.evaluations.config import EvaluationBridgeConfig

_RAW_SECRET_SUFFIX = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
_LOWERCASE_RAW_SECRET_SUFFIX = "abcdefghij0123456789"


def test_liveness(client) -> None:
    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_route_uses_problem_details(client) -> None:
    response = client.get("/api/v1/missing")

    assert response.status_code == 404
    assert response.json() == {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "Route not found",
        "code": "route_not_found",
        "request_id": response.headers["x-request-id"],
    }


@pytest.mark.parametrize(
    "unsafe_request_id",
    ["13800138000", "11010519491231002X", "trace-11010519491231002Xsuffix"],
)
def test_sensitive_request_id_is_regenerated(client, unsafe_request_id: str) -> None:
    response = client.get(
        "/api/v1/health/live",
        headers={"X-Request-ID": unsafe_request_id},
    )

    generated = response.headers["x-request-id"]
    assert generated != unsafe_request_id
    assert str(UUID(generated)) == generated


def _assert_secret_shaped_request_id_is_regenerated(
    client,
    prefix: str,
    *,
    lowercase: bool = False,
) -> None:
    suffix = _LOWERCASE_RAW_SECRET_SUFFIX if lowercase else _RAW_SECRET_SUFFIX
    response = client.get(
        "/api/v1/health/live",
        headers={"X-Request-ID": prefix + suffix},
    )

    try:
        generated = UUID(response.headers["x-request-id"])
    except ValueError:
        pytest.fail("secret-shaped request id was not regenerated", pytrace=False)
    assert str(generated) == response.headers["x-request-id"]


def test_ticket_shaped_request_id_is_regenerated(client) -> None:
    _assert_secret_shaped_request_id_is_regenerated(client, "Ticket_")


def test_state_shaped_request_id_is_regenerated(client) -> None:
    _assert_secret_shaped_request_id_is_regenerated(client, "State_")


def test_lowercase_secret_shaped_request_id_is_regenerated(client) -> None:
    _assert_secret_shaped_request_id_is_regenerated(client, "ticket_", lowercase=True)


@pytest.mark.parametrize(
    "request_id",
    [
        "state-0123456789abcdef0123456789abcdef",
        "ticket_abcdefghij_0123456789",
        "ticket-aaaaaaaa-bbbbbbbb-cccccccc",
        "service-credential-12345678-abcdefgh",
        "state.a-a-a-a-a-a-a-a-a-a-a-a-a-a",
        "a" * 43,
        "oauth-state_abcdefghij-0123456789",
        "oauth-state-aaaaaaaa-bbbbbbbb-cccccccc",
        "integration-local-service-token-2026-rotate-now",
    ],
)
def test_structured_secret_shaped_request_id_is_regenerated(
    client,
    request_id: str,
) -> None:
    response = client.get(
        "/api/v1/health/live",
        headers={"X-Request-ID": request_id},
    )

    generated = response.headers["x-request-id"]
    assert generated != request_id
    assert str(UUID(generated)) == generated


@pytest.mark.parametrize(
    "request_id",
    [
        "00000000-0000-0000-0000-000000000000",
        "019a2b3c-4d5e-7f60-8a9b-0c1d2e3f4a5b",
        "batch-import.2026_07-27",
        "build-10000000000",
        "build-1101051949123100217",
        "scheduler-019a2b3c-4d5e-7f60-8a9b-0c1d2e3f4a5b",
        "scheduler-019A2B3C-4D5E-7F60-8A9B-0C1D2E3F4A5B",
        "ready-019a2b3c4d5e7f608a9b0c1d2e3f4a5b",
        "ready-019A2B3C4D5E7F608A9B0C1D2E3F4A5B",
        "ready-689146567309405985cfbb87f005ce07",
        "e2e-full-class-confirmed",
    ],
)
def test_safe_request_id_is_preserved(client, request_id: str) -> None:
    response = client.get("/api/v1/health/live", headers={"X-Request-ID": request_id})

    assert response.headers["x-request-id"] == request_id


def test_validation_error_uses_problem_details(client) -> None:
    @client.app.get("/api/v1/_test/validated")
    def validated(limit: Annotated[int, Query(ge=1)]) -> dict[str, int]:
        return {"limit": limit}

    response = client.get("/api/v1/_test/validated", params={"limit": 0})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["status"] == 422
    assert body["request_id"] == response.headers["x-request-id"]
    assert body["errors"] == [
        {"field": "query.limit", "message": "Input should be greater than or equal to 1"}
    ]


def test_production_rejects_insecure_session_cookie() -> None:
    key = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")
    settings = Settings(
        app_env="production",
        database_url="postgresql+psycopg://erp:erp@db:5432/erp",
        session_cookie_secure=False,
        field_encryption_master_key=key,
        field_lookup_key=key,
        session_token_pepper=key,
        evaluation_tool_service_token="evaluation-local-service-token-2026-change-me",
    )

    with pytest.raises(ValueError, match="production requires secure session cookies"):
        settings.validate_runtime_secrets()


def test_settings_reads_file_environment_fallback(monkeypatch, tmp_path) -> None:
    key = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")
    key_file = tmp_path / "field-key"
    key_file.write_text(key + "\n", encoding="utf-8")
    monkeypatch.delenv("FIELD_ENCRYPTION_MASTER_KEY", raising=False)
    monkeypatch.setenv("FIELD_ENCRYPTION_MASTER_KEY_FILE", str(key_file))

    settings = Settings(
        app_env="test",
        database_url="postgresql+psycopg://erp:erp@localhost:5432/erp",
        session_cookie_secure=False,
        field_lookup_key=key,
        session_token_pepper=key,
        evaluation_tool_service_token="evaluation-local-service-token-2026-change-me",
    )

    assert settings.field_encryption_master_key.get_secret_value() == key


def test_runtime_secret_must_decode_to_exactly_32_bytes(test_settings: Settings) -> None:
    invalid = test_settings.model_copy(
        update={"field_lookup_key": base64.urlsafe_b64encode(b"too-short").decode("ascii")}
    )

    with pytest.raises(ValueError, match="field_lookup_key must decode to 32 bytes"):
        invalid.validate_runtime_secrets()


def test_runtime_secret_rejects_non_url_safe_base64(test_settings: Settings) -> None:
    invalid = test_settings.model_copy(update={"session_token_pepper": "/" * 43 + "="})

    with pytest.raises(ValueError, match="session_token_pepper must be URL-safe Base64"):
        invalid.validate_runtime_secrets()


def evaluation_settings(
    test_settings: Settings,
    **updates: object,
) -> Settings:
    return test_settings.model_copy(
        update={
            "evaluation_tool_service_token": "evaluation-service-token-2026-change-me",
            "erp_web_base_url": "http://127.0.0.1:5173",
            "evaluation_tool_standalone_base_url": "http://localhost:8010",
            "evaluation_tool_embedded_path": "/evaluation-tool",
            "evaluation_login_request_ttl_seconds": 300,
            "evaluation_login_ticket_ttl_seconds": 60,
            **updates,
        }
    )


def test_runtime_requires_long_evaluation_service_token(
    test_settings: Settings,
) -> None:
    invalid = evaluation_settings(
        test_settings,
        evaluation_tool_service_token="too-short",
    )

    with pytest.raises(ValueError, match="evaluation_tool_service_token"):
        invalid.validate_runtime_secrets()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("erp_web_base_url", "http://127.0.0.1:99999", "port"),
        (
            "evaluation_tool_standalone_base_url",
            "http://localhost:8010/private",
            "cannot contain a path",
        ),
        (
            "evaluation_tool_embedded_path",
            "/evaluation-tool/%2e%2e/private",
            "embedded path",
        ),
        (
            "evaluation_tool_embedded_path",
            "/evaluation-tool/%2Fprivate",
            "embedded path",
        ),
        ("evaluation_tool_embedded_path", "//evil.example", "embedded path"),
    ],
)
def test_evaluation_bridge_config_rejects_unsafe_urls_and_paths(
    test_settings: Settings,
    field: str,
    value: str,
    message: str,
) -> None:
    invalid = evaluation_settings(test_settings, **{field: value})

    with pytest.raises(ValueError, match=message):
        EvaluationBridgeConfig.from_settings(invalid)


@pytest.mark.parametrize(
    ("field", "origin"),
    [
        pytest.param("erp_web_base_url", "https://erp.example:", id="erp-web"),
        pytest.param(
            "evaluation_tool_standalone_base_url",
            "https://score.example:",
            id="score-tool-standalone",
        ),
        pytest.param("erp_web_base_url", "https://[::1]:", id="ipv6"),
    ],
)
def test_evaluation_bridge_config_rejects_explicit_empty_origin_ports(
    test_settings: Settings,
    field: str,
    origin: str,
) -> None:
    invalid = evaluation_settings(test_settings, **{field: origin})

    with pytest.raises(ValueError, match="port"):
        EvaluationBridgeConfig.from_settings(invalid)


def test_evaluation_bridge_config_accepts_bracketed_ipv6_without_port(
    test_settings: Settings,
) -> None:
    config = EvaluationBridgeConfig.from_settings(
        evaluation_settings(test_settings, erp_web_base_url="https://[::1]")
    )

    assert config.erp_web_base_url == "https://[::1]"


@pytest.mark.parametrize(
    ("field", "origin"),
    [
        ("erp_web_base_url", "https://erp.example?"),
        ("erp_web_base_url", "https://erp.example#"),
        ("evaluation_tool_standalone_base_url", "https://score.example?"),
        ("evaluation_tool_standalone_base_url", "https://score.example#"),
    ],
)
def test_evaluation_bridge_config_rejects_explicit_empty_query_or_fragment(
    test_settings: Settings,
    field: str,
    origin: str,
) -> None:
    updates: dict[str, object] = {
        "app_env": "production",
        "session_cookie_secure": True,
        "erp_web_base_url": "https://erp.example",
        "evaluation_tool_standalone_base_url": "https://score.example",
    }
    updates[field] = origin
    invalid = evaluation_settings(test_settings, **updates)

    with pytest.raises(ValueError, match="query|fragment"):
        EvaluationBridgeConfig.from_settings(invalid)


def test_evaluation_bridge_config_derives_only_fixed_urls(
    test_settings: Settings,
) -> None:
    config = EvaluationBridgeConfig.from_settings(evaluation_settings(test_settings))
    request_id = UUID("71000000-0000-4000-8000-000000000001")

    assert config.authorize_url(request_id) == (
        "http://127.0.0.1:5173/evaluation-connect/"
        "71000000-0000-4000-8000-000000000001"
    )
    assert config.callback_url("standalone") == (
        "http://localhost:8010/auth/erp/callback"
    )
    assert config.callback_url("embedded") == (
        "http://127.0.0.1:5173/evaluation-tool/auth/erp/callback"
    )
    assert config.start_url("standalone") == (
        "http://localhost:8010/auth/erp/start?mode=standalone&return_path=%2F"
    )
    assert config.start_url("embedded") == (
        "/evaluation-tool/auth/erp/start?mode=embedded&return_path=%2F"
    )


def test_production_requires_https_evaluation_urls(test_settings: Settings) -> None:
    invalid = evaluation_settings(
        test_settings,
        app_env="production",
        session_cookie_secure=True,
    )

    with pytest.raises(ValueError, match="require HTTPS"):
        invalid.validate_runtime_secrets()
