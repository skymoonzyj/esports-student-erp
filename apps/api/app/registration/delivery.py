from __future__ import annotations

import fcntl
import hashlib
import ipaddress
import json
import os
import smtplib
import ssl
import stat
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.mail.template_service import VerificationMailTemplate, render_verification_template
from app.registration.invitation_flow import is_guardian_invitation_handoff_token
from app.registration.models import ChallengePurpose


class VerificationSender(Protocol):
    def send_code(
        self,
        *,
        challenge_id: UUID,
        destination: str,
        code: str,
        purpose: ChallengePurpose,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class CapturedVerification:
    challenge_id: UUID
    destination: str = field(repr=False)
    code: str = field(repr=False)
    purpose: ChallengePurpose


@dataclass
class CapturingVerificationSender:
    messages: list[CapturedVerification]

    def send_code(
        self,
        *,
        challenge_id: UUID,
        destination: str,
        code: str,
        purpose: ChallengePurpose,
    ) -> None:
        self.messages.append(
            CapturedVerification(
                challenge_id=challenge_id,
                destination=destination,
                code=code,
                purpose=purpose,
            )
        )

    def message_for(self, challenge_id: UUID) -> CapturedVerification:
        try:
            return next(
                message
                for message in reversed(self.messages)
                if message.challenge_id == challenge_id
            )
        except StopIteration as exc:
            raise LookupError("verification message was not captured") from exc

    def code_for(self, challenge_id: UUID) -> str:
        return self.message_for(challenge_id).code


@dataclass(frozen=True, slots=True)
class ConfiguredSmtpVerificationSender:
    """Send verification messages using the administrator-managed SMTP row."""

    host: str
    port: int
    username: str
    password: str
    sender: str
    from_name: str
    reply_to: str
    tls_mode: str
    template: VerificationMailTemplate
    password_reset_template: VerificationMailTemplate | None = None

    def send_code(
        self,
        *,
        challenge_id: UUID,
        destination: str,
        code: str,
        purpose: ChallengePurpose,
    ) -> None:
        del challenge_id
        labels = {
            "registration": "注册邮箱",
            "password_reset": "密码重置",
            "guardian_invitation": "监护人邀请",
            "add_login_identifier": "添加登录邮箱",
            "assisted_activation": "档案账号激活",
            "profile_claim": "本人档案认领邮箱验证",
        }
        label = labels.get(purpose, "账号安全")
        template = (
            self.password_reset_template
            if purpose == "password_reset" and self.password_reset_template is not None
            else self.template
        )
        rendered = render_verification_template(
            template,
            site_name="竞学 ERP",
            operation_name=label,
            verification_code=code,
            expires_in_minutes=10,
        )
        message = EmailMessage()
        message["Subject"] = rendered.subject
        message["From"] = formataddr((self.from_name, self.sender))
        message["To"] = destination
        if self.reply_to:
            message["Reply-To"] = self.reply_to
        message.set_content(rendered.plain_text)
        message.add_alternative(rendered.html, subtype="html")
        context = ssl.create_default_context()
        if self.tls_mode == "implicit_tls":
            with smtplib.SMTP_SSL(self.host, self.port, timeout=15, context=context) as client:
                client.ehlo()
                client.login(self.username, self.password)
                client.send_message(message)
            return
        if self.tls_mode == "starttls":
            with smtplib.SMTP(self.host, self.port, timeout=15) as client:
                client.ehlo()
                client.starttls(context=context)
                client.ehlo()
                client.login(self.username, self.password)
                client.send_message(message)
            return
        raise ValueError("unsupported SMTP TLS mode")


REGISTRATION_TEST_MAILBOX_SENTINEL = ".registration-test-mailbox"
REGISTRATION_TEST_MAILBOX_FILE = "verification-mailbox.jsonl"
_REGISTRATION_TEST_MAILBOX_MARKER = "self-service-registration-e2e"


def registration_test_destination_alias(destination: str) -> str:
    digest = hashlib.sha256(
        f"registration-test-destination\x00{destination}".encode()
    ).hexdigest()
    return f"dst_{digest[:24]}"


@dataclass(frozen=True, slots=True)
class TestMailboxVerificationSender:
    mailbox_dir: Path = field(repr=False)

    def __post_init__(self) -> None:
        mailbox_dir = self.mailbox_dir
        try:
            directory_stat = mailbox_dir.lstat()
        except OSError as exc:
            raise ValueError("registration test mailbox directory is unavailable") from exc
        if not stat.S_ISDIR(directory_stat.st_mode) or mailbox_dir.is_symlink():
            raise ValueError("registration test mailbox directory must be a real directory")
        sentinel = mailbox_dir / REGISTRATION_TEST_MAILBOX_SENTINEL
        try:
            sentinel_stat = sentinel.lstat()
            marker = sentinel.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError("registration test mailbox sentinel is unavailable") from exc
        if (
            not stat.S_ISREG(sentinel_stat.st_mode)
            or sentinel.is_symlink()
            or marker != _REGISTRATION_TEST_MAILBOX_MARKER
        ):
            raise ValueError("registration test mailbox sentinel is invalid")
        allowed = {
            REGISTRATION_TEST_MAILBOX_SENTINEL,
            REGISTRATION_TEST_MAILBOX_FILE,
        }
        if any(entry.name not in allowed for entry in mailbox_dir.iterdir()):
            raise ValueError("registration test mailbox directory is not empty")

    @property
    def mailbox_path(self) -> Path:
        return self.mailbox_dir / REGISTRATION_TEST_MAILBOX_FILE

    def send_code(
        self,
        *,
        challenge_id: UUID,
        destination: str,
        code: str,
        purpose: ChallengePurpose,
    ) -> None:
        valid_code = (
            is_guardian_invitation_handoff_token(code)
            if purpose == "guardian_invitation"
            else code.isascii() and code.isdigit() and len(code) == 6
        )
        if not valid_code:
            raise ValueError("registration test mailbox code shape is invalid")
        row = {
            "challenge_id": str(challenge_id),
            "destination_alias": registration_test_destination_alias(destination),
            "purpose": purpose,
            "code": code,
        }
        payload = (json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.mailbox_path, flags, 0o600)
        except OSError as exc:
            raise ValueError("registration test mailbox file is unavailable") from exc
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError("registration test mailbox file must be regular")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            written = os.write(descriptor, payload)
            if written != len(payload):
                raise OSError("short registration test mailbox write")
            os.fsync(descriptor)
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def create_test_mailbox_sender(
    *,
    app_env: str,
    server_host: str | None,
    mailbox_dir: Path | None,
) -> TestMailboxVerificationSender | None:
    if mailbox_dir is None and server_host is None:
        return None
    if mailbox_dir is None or server_host is None:
        raise ValueError("registration test mailbox configuration is incomplete")
    if app_env != "test":
        raise ValueError("registration test mailbox requires APP_ENV=test")
    try:
        address = ipaddress.ip_address(server_host.strip())
    except ValueError as exc:
        raise ValueError("registration test mailbox host must be loopback") from exc
    if not address.is_loopback:
        raise ValueError("registration test mailbox host must be loopback")
    return TestMailboxVerificationSender(mailbox_dir.expanduser())
