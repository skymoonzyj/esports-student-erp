from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from typing import Protocol

logger = logging.getLogger(__name__)


class RegistrationEmailSender(Protocol):
    def send_verification(
        self,
        *,
        recipient: str,
        code: str,
        expires_at: datetime,
    ) -> None: ...


@dataclass(frozen=True)
class SmtpEmailSender:
    host: str
    port: int
    sender: str
    username: str | None = None
    password: str | None = None
    use_tls: bool = True

    def send_verification(
        self,
        *,
        recipient: str,
        code: str,
        expires_at: datetime,
    ) -> None:
        message = EmailMessage()
        message["Subject"] = "竞学 ERP 注册验证码"
        message["From"] = self.sender
        message["To"] = recipient
        message.set_content(
            "你的竞学 ERP 注册验证码是："
            f"{code}\n\n验证码将在 {expires_at.astimezone().strftime('%Y-%m-%d %H:%M')} 后失效。"
            "如非本人操作，请忽略本邮件。"
        )
        with smtplib.SMTP(self.host, self.port, timeout=15) as client:
            if self.use_tls:
                client.starttls()
            if self.username:
                client.login(self.username, self.password or "")
            client.send_message(message)


class LoggingEmailSender:
    """Development fallback; production must configure SMTP."""

    def send_verification(
        self,
        *,
        recipient: str,
        code: str,
        expires_at: datetime,
    ) -> None:
        logger.warning(
            "registration verification email is not configured recipient=%s code=%s expires_at=%s",
            recipient,
            code,
            expires_at.isoformat(),
        )


@dataclass(frozen=True)
class ConfiguredSmtpEmailSender:
    """SMTP sender built from the administrator-managed mail settings."""

    host: str
    port: int
    sender: str
    username: str
    password: str
    tls_mode: str
    from_name: str
    reply_to: str

    def send_verification(
        self,
        *,
        recipient: str,
        code: str,
        expires_at: datetime,
    ) -> None:
        message = EmailMessage()
        message["Subject"] = "竞学 ERP 注册验证码"
        message["From"] = formataddr((self.from_name, self.sender))
        message["To"] = recipient
        if self.reply_to:
            message["Reply-To"] = self.reply_to
        message.set_content(
            "你的竞学 ERP 注册验证码是："
            f"{code}\n\n验证码将在 {expires_at.astimezone().strftime('%Y-%m-%d %H:%M')} 后失效。"
            "如非本人操作，请忽略本邮件。"
        )
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


class DisabledEmailSender:
    """Explicitly fail delivery when an administrator disabled sending."""

    def send_verification(
        self,
        *,
        recipient: str,
        code: str,
        expires_at: datetime,
    ) -> None:
        del recipient, code, expires_at
        raise RuntimeError("administrator disabled email delivery")
