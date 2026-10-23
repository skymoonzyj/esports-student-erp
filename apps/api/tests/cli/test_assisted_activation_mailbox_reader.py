from __future__ import annotations

import json
from uuid import UUID

import pytest

from app.cli.read_registration_test_code import read_registration_test_code

ALIAS = "dst_000000000000000000000001"


@pytest.fixture
def mailbox(tmp_path):
    tmp_path.chmod(0o700)
    sentinel = tmp_path / ".registration-test-mailbox"
    sentinel.write_text("self-service-registration-e2e\n")
    sentinel.chmod(0o600)
    rows = [
        {"challenge_id": str(UUID(int=1)), "destination_alias": ALIAS,
         "purpose": "assisted_activation", "code": "610428"},
        {"challenge_id": str(UUID(int=2)), "destination_alias": ALIAS,
         "purpose": "password_reset", "code": "700001"},
        {"challenge_id": str(UUID(int=3)), "destination_alias": "dst_000000000000000000000002",
         "purpose": "assisted_activation", "code": "700002"},
    ]
    path = tmp_path / "verification-mailbox.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows))
    path.chmod(0o600)
    return tmp_path


def test_activation_reader_selects_only_expected_recipient_and_purpose(mailbox):
    assert read_registration_test_code(mailbox, challenge_id=None,
        destination_alias=ALIAS, purpose="assisted_activation") == "610428"
    # Existing modes still require their exact challenge and cannot use this shortcut.
    with pytest.raises(LookupError):
        read_registration_test_code(mailbox, challenge_id=None,
            destination_alias=ALIAS, purpose="password_reset")
    assert read_registration_test_code(mailbox, challenge_id=UUID(int=2),
        destination_alias=ALIAS, purpose="password_reset") == "700001"


@pytest.mark.parametrize("unsafe", ["directory", "sentinel", "mailbox", "symlink"])
def test_activation_reader_rejects_nonprivate_or_symlink_mailboxes(mailbox, unsafe):
    if unsafe == "directory":
        mailbox.chmod(0o755)
    elif unsafe == "sentinel":
        (mailbox / ".registration-test-mailbox").chmod(0o644)
    elif unsafe == "mailbox":
        (mailbox / "verification-mailbox.jsonl").chmod(0o644)
    else:
        link = mailbox / "link"
        link.symlink_to(mailbox, target_is_directory=True)
        mailbox = link
    with pytest.raises(LookupError):
        read_registration_test_code(mailbox, challenge_id=None,
            destination_alias=ALIAS, purpose="assisted_activation")
