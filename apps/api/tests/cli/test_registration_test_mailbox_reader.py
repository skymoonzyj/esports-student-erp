from __future__ import annotations

import importlib
import stat
from pathlib import Path
from uuid import UUID

import pytest

from app.core.config import Settings
from app.main import create_app
from app.registration.delivery import (
    REGISTRATION_TEST_MAILBOX_FILE,
    REGISTRATION_TEST_MAILBOX_SENTINEL,
    registration_test_destination_alias,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
READER_PATH = REPOSITORY_ROOT / "apps/api/app/cli/read_registration_test_code.py"


def test_registration_test_code_reader_requires_exact_safe_mailbox_record(
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    assert READER_PATH.is_file()
    reader_module = importlib.import_module("app.cli.read_registration_test_code")
    mailbox_dir = tmp_path / "mailbox"
    mailbox_dir.mkdir(mode=0o700)
    (mailbox_dir / REGISTRATION_TEST_MAILBOX_SENTINEL).write_text(
        "self-service-registration-e2e\n",
        encoding="utf-8",
    )
    settings = test_settings.model_copy(
        update={
            "registration_test_mailbox_dir": mailbox_dir,
            "registration_test_server_host": "127.0.0.1",
        }
    )
    sender = create_app(settings).state.registration_verification_sender
    challenge_id = UUID("f7000000-0000-4000-8000-000000000001")
    destination = "registration-reader@example.com"
    alias = registration_test_destination_alias(destination)
    sender.send_code(
        challenge_id=challenge_id,
        destination=destination,
        code="610428",
        purpose="password_reset",
    )

    assert reader_module.read_registration_test_code(
        mailbox_dir,
        challenge_id=challenge_id,
        destination_alias=alias,
        purpose="password_reset",
    ) == "610428"

    invitation_id = UUID("f7000000-0000-4000-8000-000000000002")
    invitation_token = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
    guardian_destination = "13600009103"
    guardian_alias = registration_test_destination_alias(guardian_destination)
    sender.send_code(
        challenge_id=invitation_id,
        destination=guardian_destination,
        code=invitation_token,
        purpose="guardian_invitation",
    )
    assert reader_module.read_registration_test_code(
        mailbox_dir,
        challenge_id=invitation_id,
        destination_alias=guardian_alias,
        purpose="guardian_invitation",
    ) == invitation_token
    with pytest.raises(LookupError, match="registration test code unavailable"):
        reader_module.read_registration_test_code(
            mailbox_dir,
            challenge_id=challenge_id,
            destination_alias="dst_000000000000000000000000",
            purpose="password_reset",
        )

    mailbox = mailbox_dir / REGISTRATION_TEST_MAILBOX_FILE
    mailbox.chmod(0o644)
    assert stat.S_IMODE(mailbox.stat().st_mode) == 0o644
    with pytest.raises(LookupError, match="registration test code unavailable"):
        reader_module.read_registration_test_code(
            mailbox_dir,
            challenge_id=challenge_id,
            destination_alias=alias,
            purpose="password_reset",
        )
