from __future__ import annotations

import json
import stat
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import get_db
from app.main import create_app
from app.registration.invitation_flow import issue_guardian_invitation_handoff_token

SENTINEL_NAME = ".registration-test-mailbox"
MAILBOX_NAME = "verification-mailbox.jsonl"


def _mailbox_settings(
    test_settings: Settings,
    mailbox_dir: Path,
    *,
    app_env: str = "test",
    host: str = "127.0.0.1",
) -> Settings:
    return test_settings.model_copy(
        update={
            "app_env": app_env,
            "session_cookie_secure": app_env == "production",
            "registration_test_mailbox_dir": mailbox_dir,
            "registration_test_server_host": host,
        }
    )


def _prepare_mailbox(tmp_path: Path) -> Path:
    mailbox_dir = tmp_path / "mailbox"
    mailbox_dir.mkdir(mode=0o700)
    (mailbox_dir / SENTINEL_NAME).write_text("self-service-registration-e2e\n")
    return mailbox_dir


def test_test_mailbox_sender_is_enabled_only_for_explicit_safe_runtime(
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    mailbox_dir = _prepare_mailbox(tmp_path)

    app = create_app(_mailbox_settings(test_settings, mailbox_dir))

    sender = getattr(app.state, "registration_verification_sender", None)
    assert sender is not None
    assert type(sender).__name__ == "TestMailboxVerificationSender"


@pytest.mark.parametrize(
    ("app_env", "host"),
    [
        ("development", "127.0.0.1"),
        ("test", "0.0.0.0"),
        ("test", "192.168.1.8"),
    ],
)
def test_test_mailbox_sender_refuses_non_test_or_non_loopback_runtime(
    test_settings: Settings,
    tmp_path: Path,
    app_env: str,
    host: str,
) -> None:
    mailbox_dir = _prepare_mailbox(tmp_path)

    with pytest.raises(ValueError, match="registration test mailbox"):
        create_app(
            _mailbox_settings(
                test_settings,
                mailbox_dir,
                app_env=app_env,
                host=host,
            )
        )


def test_test_mailbox_sender_requires_runner_sentinel(
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    mailbox_dir = tmp_path / "mailbox"
    mailbox_dir.mkdir(mode=0o700)

    with pytest.raises(ValueError, match="registration test mailbox"):
        create_app(_mailbox_settings(test_settings, mailbox_dir))


def test_test_mailbox_sender_writes_only_safe_alias_fields_with_mode_0600(
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    mailbox_dir = _prepare_mailbox(tmp_path)
    app = create_app(_mailbox_settings(test_settings, mailbox_dir))
    sender = app.state.registration_verification_sender
    challenge_id = UUID("f1000000-0000-4000-8000-000000000001")
    destination = "13800009991"

    sender.send_code(
        challenge_id=challenge_id,
        destination=destination,
        code="482913",
        purpose="registration",
    )

    mailbox = mailbox_dir / MAILBOX_NAME
    assert stat.S_IMODE(mailbox.stat().st_mode) == 0o600
    raw = mailbox.read_text(encoding="utf-8")
    row = json.loads(raw)
    assert set(row) == {
        "challenge_id",
        "destination_alias",
        "purpose",
        "code",
    }
    assert row["challenge_id"] == str(challenge_id)
    assert row["purpose"] == "registration"
    assert row["code"] == "482913"
    assert row["destination_alias"].startswith("dst_")
    assert destination not in raw


def test_test_mailbox_sender_preserves_guardian_invitation_token_shape(
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    mailbox_dir = _prepare_mailbox(tmp_path)
    sender = create_app(
        _mailbox_settings(test_settings, mailbox_dir)
    ).state.registration_verification_sender
    invitation_id = UUID("f1000000-0000-4000-8000-000000000002")
    invitation_token = issue_guardian_invitation_handoff_token()

    sender.send_code(
        challenge_id=invitation_id,
        destination="13600009103",
        code=invitation_token,
        purpose="guardian_invitation",
    )

    row = json.loads((mailbox_dir / MAILBOX_NAME).read_text(encoding="utf-8"))
    assert row["purpose"] == "guardian_invitation"
    assert row["code"] == invitation_token


def test_test_mailbox_sender_rejects_non_loopback_request_even_if_runtime_claims_loopback(
    test_settings: Settings,
    db: Session,
    tmp_path: Path,
) -> None:
    mailbox_dir = _prepare_mailbox(tmp_path)
    app = create_app(_mailbox_settings(test_settings, mailbox_dir))

    def override_db() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app, client=("192.0.2.10", 50123)) as client:
        response = client.post(
            "/api/v1/registration/applications",
            json={
                "primary_purpose": "student",
                "identifier_kind": "phone",
                "identifier": "13600009998",
            },
        )

    assert response.status_code == 503
    assert response.json()["code"] == "registration_unavailable"
    assert not (mailbox_dir / MAILBOX_NAME).exists()
