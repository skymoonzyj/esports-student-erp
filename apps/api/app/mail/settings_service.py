from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.core.transactions import write_transaction
from app.mail.models import MailDeliverySettings
from app.mail.settings_schemas import (
    MailDeliverySettingsResponse,
    MailDeliverySettingsUpdate,
    MailTestStatus,
    TlsMode,
)
from app.security.encryption import EncryptedFieldError, EnvelopeCipher


class MailSettingsError(ValueError):
    code = "mail_settings_error"


class MailSettingsNotConfigured(MailSettingsError):
    code = "mail_settings_not_configured"


@dataclass(frozen=True)
class MailDeliveryCredentials:
    host: str
    port: int
    tls_mode: str
    username: str
    password: str
    sender: str
    from_name: str
    reply_to: str
    enabled: bool


def _mask(value: str | None) -> str | None:
    if value is None:
        return None
    if "@" in value:
        local, domain = value.rsplit("@", 1)
        if len(local) <= 1:
            masked_local = "*"
        elif len(local) == 2:
            masked_local = f"{local[0]}*"
        else:
            masked_local = f"{local[0]}***{local[-1]}"
        return f"{masked_local}@{domain}"
    if len(value) <= 2:
        return "*" * len(value)
    return f"{value[0]}***{value[-1]}"


class MailSettingsService:
    _FIELD_NAMES: ClassVar[dict[str, str]] = {
        "username": "mail-settings-username",
        "password": "mail-settings-password",
        "sender": "mail-settings-sender",
        "reply_to": "mail-settings-reply-to",
    }

    def __init__(self, db: Session, *, cipher: EnvelopeCipher) -> None:
        self.db = db
        self.cipher = cipher

    def _row(self, *, lock: bool = False) -> MailDeliverySettings | None:
        statement = select(MailDeliverySettings).where(
            MailDeliverySettings.singleton_key == "default"
        )
        if lock:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def _decrypt(self, row: MailDeliverySettings) -> dict[str, str]:
        try:
            return {
                field: self.cipher.decrypt(
                    getattr(row, f"{field}_ciphertext"),
                    field_name=self._FIELD_NAMES[field],
                )
                for field in self._FIELD_NAMES
            }
        except (EncryptedFieldError, ValueError, TypeError) as exc:
            raise MailSettingsError("邮件配置无法解密") from exc

    def public(self) -> MailDeliverySettingsResponse:
        row = self._row()
        if row is None:
            return MailDeliverySettingsResponse(
                configured=False,
                enabled=False,
                smtp_host=None,
                smtp_port=None,
                tls_mode=None,
                username_masked=None,
                sender_masked=None,
                from_name=None,
                reply_to_masked=None,
                authorization_code_configured=False,
                last_test_status="never",
                last_test_at=None,
                updated_at=None,
            )
        values = self._decrypt(row)
        return MailDeliverySettingsResponse(
            configured=True,
            enabled=row.enabled,
            smtp_host=row.smtp_host,
            smtp_port=row.smtp_port,
            tls_mode=cast(TlsMode, row.tls_mode),
            username_masked=_mask(values["username"]),
            sender_masked=_mask(values["sender"]),
            from_name=row.from_name,
            reply_to_masked=_mask(values["reply_to"]),
            authorization_code_configured=bool(values["password"]),
            last_test_status=cast(MailTestStatus, row.last_test_status),
            last_test_at=row.last_test_at,
            updated_at=row.updated_at,
        )

    def credentials(self) -> MailDeliveryCredentials | None:
        row = self._row()
        if row is None:
            return None
        values = self._decrypt(row)
        return MailDeliveryCredentials(
            host=row.smtp_host,
            port=row.smtp_port,
            tls_mode=row.tls_mode,
            username=values["username"],
            password=values["password"],
            sender=values["sender"],
            from_name=row.from_name,
            reply_to=values["reply_to"],
            enabled=row.enabled,
        )

    def save(
        self,
        input: MailDeliverySettingsUpdate,
        *,
        actor_user_id: UUID,
        request_id: str,
    ) -> MailDeliverySettingsResponse:
        with write_transaction(self.db):
            row = self._row(lock=True)
            existing_values = self._decrypt(row) if row is not None else {}
            sender = (
                str(input.sender) if input.sender is not None else existing_values.get("sender", "")
            )
            values = {
                "username": input.username or existing_values.get("username", ""),
                "password": (
                    input.authorization_code.get_secret_value()
                    if input.authorization_code is not None
                    else existing_values.get("password", "")
                ),
                "sender": sender,
                "reply_to": (
                    str(input.reply_to)
                    if input.reply_to is not None
                    else existing_values.get("reply_to", sender)
                ),
            }
            if any(not value for value in values.values()):
                message = (
                    "首次保存必须填写 SMTP 账号、发件地址和授权码"
                    if row is None
                    else "已保存配置缺少必要凭据，请重新填写"
                )
                raise MailSettingsError(message)
            encrypted = {
                field: self.cipher.encrypt(value, field_name=self._FIELD_NAMES[field])
                for field, value in values.items()
            }
            if row is None:
                row = MailDeliverySettings(
                    smtp_host=input.smtp_host,
                    smtp_port=input.smtp_port,
                    tls_mode=input.tls_mode,
                    username_ciphertext=encrypted["username"],
                    password_ciphertext=encrypted["password"],
                    sender_ciphertext=encrypted["sender"],
                    from_name=input.from_name,
                    reply_to_ciphertext=encrypted["reply_to"],
                    enabled=input.enabled,
                    updated_by_user_id=actor_user_id,
                )
                self.db.add(row)
                self.db.flush()
            else:
                row.smtp_host = input.smtp_host
                row.smtp_port = input.smtp_port
                row.tls_mode = input.tls_mode
                row.username_ciphertext = encrypted["username"]
                row.password_ciphertext = encrypted["password"]
                row.sender_ciphertext = encrypted["sender"]
                row.from_name = input.from_name
                row.reply_to_ciphertext = encrypted["reply_to"]
                row.enabled = input.enabled
                row.updated_by_user_id = actor_user_id
                row.last_test_status = "never"
                row.last_test_at = None
            AuditService(self.db).append(
                actor_user_id=actor_user_id,
                action="mail.settings.update",
                resource_type="mail_settings",
                resource_id=row.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={},
            )
        return self.public()

    def test_connection(
        self,
        *,
        actor_user_id: UUID,
        request_id: str,
    ) -> MailTestStatus:
        row = self._row()
        if row is None:
            raise MailSettingsNotConfigured("邮件配置尚未保存")
        values = self._decrypt(row)
        status: MailTestStatus = "failed"
        try:
            self._probe(
                host=row.smtp_host,
                port=row.smtp_port,
                tls_mode=row.tls_mode,
                username=values["username"],
                password=values["password"],
            )
            status = "passed"
        except (OSError, smtplib.SMTPException, ValueError):
            status = "failed"
        with write_transaction(self.db):
            locked = self._row(lock=True)
            if locked is not None:
                locked.last_test_status = status
                locked.last_test_at = datetime.now(UTC)
                AuditService(self.db).append(
                    actor_user_id=actor_user_id,
                    action="mail.settings.test",
                    resource_type="mail_settings",
                    resource_id=locked.id,
                    purpose=None,
                    outcome="allowed" if status == "passed" else "failed",
                    request_id=request_id,
                    metadata={},
                )
        return status

    @staticmethod
    def _probe(*, host: str, port: int, tls_mode: str, username: str, password: str) -> None:
        context = ssl.create_default_context()
        if tls_mode == "implicit_tls":
            with smtplib.SMTP_SSL(host, port, timeout=15, context=context) as client:
                client.ehlo()
                client.login(username, password)
            return
        if tls_mode == "starttls":
            with smtplib.SMTP(host, port, timeout=15) as client:
                client.ehlo()
                client.starttls(context=context)
                client.ehlo()
                client.login(username, password)
            return
        raise MailSettingsError("不支持的 SMTP 加密方式")
