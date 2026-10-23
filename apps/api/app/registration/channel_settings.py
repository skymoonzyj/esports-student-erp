from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.core.transactions import write_transaction
from app.registration.models import RegistrationChannelSettings
from app.registration.schemas import RegistrationChannelSettingsUpdate


class RegistrationChannelSettingsError(ValueError):
    code = "registration_channel_settings_error"


class RegistrationPhoneDeploymentDisabled(RegistrationChannelSettingsError):
    code = "registration_phone_deployment_disabled"


@dataclass(frozen=True)
class RegistrationChannelState:
    phone_enabled: bool
    email_enabled: bool
    phone_signup_allowed_by_deployment: bool
    updated_at: datetime | None


class RegistrationChannelSettingsService:
    """Read and persist the singleton public registration policy."""

    def __init__(self, db: Session, *, phone_signup_allowed_by_deployment: bool) -> None:
        self.db = db
        self.phone_signup_allowed_by_deployment = phone_signup_allowed_by_deployment

    def _row(self, *, lock: bool = False) -> RegistrationChannelSettings | None:
        statement = select(RegistrationChannelSettings).where(
            RegistrationChannelSettings.singleton_key == "default"
        )
        if lock:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def state(self) -> RegistrationChannelState:
        row = self._row()
        if row is None:
            return RegistrationChannelState(
                phone_enabled=self.phone_signup_allowed_by_deployment,
                email_enabled=True,
                phone_signup_allowed_by_deployment=self.phone_signup_allowed_by_deployment,
                updated_at=None,
            )
        return RegistrationChannelState(
            phone_enabled=(
                row.phone_enabled and self.phone_signup_allowed_by_deployment
            ),
            email_enabled=row.email_enabled,
            phone_signup_allowed_by_deployment=self.phone_signup_allowed_by_deployment,
            updated_at=row.updated_at,
        )

    def save(
        self,
        input: RegistrationChannelSettingsUpdate,
        *,
        actor_user_id: UUID,
        request_id: str,
    ) -> RegistrationChannelState:
        if input.phone_enabled and not self.phone_signup_allowed_by_deployment:
            raise RegistrationPhoneDeploymentDisabled(
                "当前部署未开放手机号申请，请先启用手机号注册能力"
            )
        with write_transaction(self.db):
            row = self._row(lock=True)
            if row is None:
                row = RegistrationChannelSettings(
                    phone_enabled=input.phone_enabled,
                    email_enabled=input.email_enabled,
                    updated_by_user_id=actor_user_id,
                )
                self.db.add(row)
                self.db.flush()
            else:
                row.phone_enabled = input.phone_enabled
                row.email_enabled = input.email_enabled
                row.updated_by_user_id = actor_user_id
            AuditService(self.db).append(
                actor_user_id=actor_user_id,
                action="registration.channel_settings.update",
                resource_type="registration_channel_settings",
                resource_id=row.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={},
            )
        return self.state()
