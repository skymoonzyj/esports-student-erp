from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import cast
from uuid import UUID

from app.registration.delivery import (
    REGISTRATION_TEST_MAILBOX_FILE,
    REGISTRATION_TEST_MAILBOX_SENTINEL,
)
from app.registration.invitation_flow import is_guardian_invitation_handoff_token

_MAX_MAILBOX_BYTES = 1024 * 1024
_ALIAS_PATTERN = re.compile(r"^dst_[0-9a-f]{24}$")
_CODE_PATTERN = re.compile(r"^[0-9]{6}$")
_PURPOSES = {
    "registration",
    "password_reset",
    "guardian_invitation",
    "add_login_identifier",
    "assisted_activation",
}
_EXPECTED_KEYS = {
    "challenge_id",
    "destination_alias",
    "purpose",
    "code",
}


def _unavailable() -> LookupError:
    return LookupError("registration test code unavailable")


def _read_mailbox(mailbox_dir: Path) -> str:
    try:
        directory_stat = mailbox_dir.lstat()
        if not stat.S_ISDIR(directory_stat.st_mode) or mailbox_dir.is_symlink():
            raise _unavailable()
        sentinel = mailbox_dir / REGISTRATION_TEST_MAILBOX_SENTINEL
        sentinel_stat = sentinel.lstat()
        if not stat.S_ISREG(sentinel_stat.st_mode) or sentinel.is_symlink():
            raise _unavailable()
        if sentinel.read_text(encoding="utf-8").strip() != "self-service-registration-e2e":
            raise _unavailable()
        mailbox = mailbox_dir / REGISTRATION_TEST_MAILBOX_FILE
        mailbox_stat = mailbox.lstat()
        if (
            not stat.S_ISREG(mailbox_stat.st_mode)
            or mailbox.is_symlink()
            or stat.S_IMODE(mailbox_stat.st_mode) != 0o600
            or mailbox_stat.st_size > _MAX_MAILBOX_BYTES
        ):
            raise _unavailable()
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(mailbox, flags)
        try:
            opened_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or opened_stat.st_ino != mailbox_stat.st_ino
                or opened_stat.st_dev != mailbox_stat.st_dev
                or opened_stat.st_size > _MAX_MAILBOX_BYTES
            ):
                raise _unavailable()
            payload = os.read(descriptor, _MAX_MAILBOX_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(payload) > _MAX_MAILBOX_BYTES:
            raise _unavailable()
        return payload.decode("utf-8")
    except LookupError:
        raise
    except (OSError, UnicodeError) as exc:
        raise _unavailable() from exc


def read_registration_test_code(
    mailbox_dir: Path,
    *,
    challenge_id: UUID | None,
    destination_alias: str,
    purpose: str,
) -> str:
    if not _ALIAS_PATTERN.fullmatch(destination_alias) or purpose not in _PURPOSES:
        raise _unavailable()
    if challenge_id is None and purpose != "assisted_activation":
        raise _unavailable()
    if purpose == "assisted_activation":
        try:
            directory = mailbox_dir.lstat()
            sentinel = (mailbox_dir / REGISTRATION_TEST_MAILBOX_SENTINEL).lstat()
            if (stat.S_IMODE(directory.st_mode) != 0o700
                    or stat.S_IMODE(sentinel.st_mode) != 0o600
                    or directory.st_uid != os.getuid() or sentinel.st_uid != os.getuid()):
                raise _unavailable()
        except OSError as exc:
            raise _unavailable() from exc
    raw = _read_mailbox(mailbox_dir)
    try:
        rows = [json.loads(line) for line in raw.splitlines() if line]
    except (json.JSONDecodeError, TypeError) as exc:
        raise _unavailable() from exc
    for row in reversed(rows):
        if not isinstance(row, dict) or set(row) != _EXPECTED_KEYS:
            raise _unavailable()
        if not all(isinstance(value, str) for value in row.values()):
            raise _unavailable()
        valid_code = (
            is_guardian_invitation_handoff_token(row["code"])
            if row["purpose"] == "guardian_invitation"
            else _CODE_PATTERN.fullmatch(row["code"]) is not None
        )
        if not valid_code:
            raise _unavailable()
        if (
            (challenge_id is None or row["challenge_id"] == str(challenge_id))
            and row["destination_alias"] == destination_alias
            and row["purpose"] == purpose
        ):
            return cast(str, row["code"])
    raise _unavailable()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--challenge-id")
    parser.add_argument("--destination-alias", required=True)
    parser.add_argument("--purpose", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if os.environ.get("APP_ENV") != "test":
            raise _unavailable()
        mailbox_value = os.environ.get("REGISTRATION_TEST_MAILBOX_DIR")
        if mailbox_value is None:
            raise _unavailable()
        code = read_registration_test_code(
            Path(mailbox_value),
            challenge_id=UUID(arguments.challenge_id) if arguments.challenge_id else None,
            destination_alias=arguments.destination_alias,
            purpose=arguments.purpose,
        )
    except (LookupError, ValueError, SystemExit):
        print("registration test code unavailable", file=sys.stderr)
        return 1
    print(code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
