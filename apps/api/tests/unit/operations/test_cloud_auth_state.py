from datetime import UTC, datetime, timedelta
from importlib import import_module, util
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.security.encryption import EnvelopeCipher

NOW = datetime(2026, 10, 6, 3, tzinfo=UTC)


def contracts():
    # A missing foundation is an explicit contract failure, not a collection error.
    assert util.find_spec("app.operations.cloud_auth_state") is not None
    assert util.find_spec("app.operations.cloud_auth_schemas") is not None
    return (
        import_module("app.operations.cloud_auth_state"),
        import_module("app.operations.cloud_auth_schemas"),
    )


def test_599_seconds_allows_apply_but_exact_deadline_rejects_it():
    state, _ = contracts()
    deadline = NOW + timedelta(seconds=600)
    assert (
        state.transition("ready", "apply", now=NOW + timedelta(seconds=599), expires_at=deadline)
        == "applying"
    )
    with pytest.raises(state.AuthExpired):
        state.transition("ready", "apply", now=deadline, expires_at=deadline)


def test_authorized_configuration_gets_separate_nonrenewing_deadline():
    state, _ = contracts()
    authorized_at = NOW + timedelta(seconds=599)
    assert state.scan_deadline(
        now=NOW, provider_expires_at=NOW + timedelta(seconds=900)
    ) == NOW + timedelta(seconds=600)
    assert state.scan_deadline(
        now=NOW, provider_expires_at=NOW + timedelta(seconds=90)
    ) == NOW + timedelta(seconds=90)
    assert (
        state.transition(
            "awaiting_confirmation",
            "authorized",
            now=authorized_at,
            expires_at=NOW + timedelta(seconds=600),
        )
        == "authorized"
    )
    deadline = state.configuration_deadline(authorized_at)
    assert deadline == NOW + timedelta(seconds=1199)
    assert (
        state.transition(
            "authorized", "configure", now=NOW + timedelta(seconds=601), expires_at=deadline
        )
        == "configuring"
    )
    with pytest.raises(state.AuthExpired):
        state.transition("ready", "apply", now=deadline, expires_at=deadline)


@pytest.mark.parametrize("terminal", ["applied", "expired", "cancelled", "failed"])
@pytest.mark.parametrize("event", ["created", "authorized", "configure", "apply", "cancel"])
def test_terminal_state_cannot_be_revived(terminal, event):
    state, _ = contracts()
    with pytest.raises(state.AuthTransitionError):
        state.transition(terminal, event, now=NOW, expires_at=NOW + timedelta(minutes=10))


def test_invalid_sequence_and_naive_dates_are_rejected():
    state, _ = contracts()
    with pytest.raises(state.AuthTransitionError):
        state.transition(
            "awaiting_confirmation", "apply", now=NOW, expires_at=NOW + timedelta(minutes=10)
        )
    with pytest.raises(ValueError):
        state.configuration_deadline(NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        state.transition("ready", "apply", now=NOW.replace(tzinfo=None), expires_at=NOW)


def test_expired_session_can_only_enter_safe_terminal_cleanup_states():
    state, _ = contracts()
    for event, want in [("expire", "expired"), ("cancel", "cancelled"), ("fail", "failed")]:
        assert state.transition("configuring", event, now=NOW, expires_at=NOW) == want


def test_ciphertext_is_bound_to_provider_record_and_field():
    state, _ = contracts()
    cipher = EnvelopeCipher(bytes(range(32)))
    record_id = uuid4()
    value = cipher.encrypt("private-token", field_name=state.aad("baidu", record_id, "token"))
    assert (
        cipher.decrypt(value, field_name=state.aad("baidu", record_id, "token")) == "private-token"
    )
    for provider, record, field in [
        ("115", record_id, "token"),
        ("baidu", uuid4(), "token"),
        ("baidu", record_id, "device"),
    ]:
        with pytest.raises(ValueError):
            cipher.decrypt(value, field_name=state.aad(provider, record, field))
    with pytest.raises(ValueError):
        state.aad("quark", record_id, "token")


def test_public_session_rejects_credentials_and_qr():
    _, schemas = contracts()
    payload = {
        "id": uuid4(),
        "provider": "baidu",
        "state": "ready",
        "revision": 1,
        "expires_at": NOW,
        "next_poll_after_seconds": 2,
    }
    assert schemas.CloudAuthSessionView(**payload).model_dump()["state"] == "ready"
    for key in ["qr", "access_token", "refresh_token", "device_code", "pkce_verifier"]:
        with pytest.raises(ValidationError):
            schemas.CloudAuthSessionView(**payload, **{key: "private-token"})


def test_app_secret_is_write_only_blank_preserves_and_clear_conflicts():
    _, schemas = contracts()
    assert (
        schemas.CloudAppUpdate(client_id="123", client_secret="", expected_revision=1).client_secret
        is None
    )
    with pytest.raises(ValidationError):
        schemas.CloudAppUpdate(
            client_id="123", client_secret="secret", clear_secret=True, expected_revision=1
        )
    with pytest.raises(ValidationError):
        schemas.CloudAppView(
            provider="baidu",
            client_id="123",
            secret_present=True,
            revision=1,
            configured=True,
            prerequisites=[],
            client_secret="secret",
        )


@pytest.mark.parametrize(
    "content",
    [
        "https://evil.example/qr",
        "http://openapi.baidu.com/qr",
        "https://openapi.baidu.com@evil.example/qr",
        "data:image/png;base64,not-png",
        "private-token",
        "x" * 48001,
    ],
    ids=[
        "untrusted-host",
        "insecure-http",
        "url-userinfo",
        "invalid-png",
        "raw-secret",
        "oversized",
    ],
)
def test_qr_presentation_rejects_unbounded_or_untrusted_content(content):
    _, schemas = contracts()
    with pytest.raises(ValidationError):
        schemas.QrPresentation(
            kind="image" if content.startswith("data:") else "payload",
            content=content,
            expires_at=NOW,
        )


def test_poll_result_requires_tokens_only_for_authorized_status():
    _, schemas = contracts()
    tokens = {"access_token": "secret", "refresh_token": "secret", "expires_at": NOW}
    with pytest.raises(ValidationError):
        schemas.PollResult(status="authorized")
    with pytest.raises(ValidationError):
        schemas.PollResult(status="pending", tokens=tokens)
    assert "secret" not in repr(schemas.PollResult(status="authorized", tokens=tokens))


@pytest.mark.parametrize(
    "query",
    [
        "access_token=secret",
        "refresh_token=secret",
        "client_secret=secret",
        "%61ccess_token=secret",
        "code_verifier=secret",
    ],
)
def test_official_qr_url_cannot_carry_long_lived_credentials(query):
    _, schemas = contracts()
    with pytest.raises(ValidationError):
        schemas.QrPresentation(
            kind="payload", content=f"https://openapi.baidu.com/device?{query}", expires_at=NOW
        )


def test_qr_supports_official_payload_and_bounded_png_image():
    _, schemas = contracts()
    assert (
        schemas.QrPresentation(
            kind="payload",
            content="https://openapi.baidu.com/device?user_code=short-lived",
            expires_at=NOW,
        ).kind
        == "payload"
    )
    assert (
        schemas.QrPresentation(
            kind="image",
            content="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aMZQAAAAASUVORK5CYII=",
            expires_at=NOW,
        ).kind
        == "image"
    )
