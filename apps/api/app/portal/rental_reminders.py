"""Read-only, freshly authorized supplier-lease reminders (not custody returns)."""

import base64
from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, and_, func, select
from sqlalchemy.orm import Session

from app.asset_management.models import Asset, AssetAssignment
from app.auth.account_readiness import AccountAccessReadinessPolicy
from app.auth.dependencies import require_standard_account
from app.auth.models import AuthSession, UserAccount
from app.auth.service import AuthContext
from app.core.config import Settings
from app.core.database import get_db
from app.core.errors import ProblemError
from app.core.time import business_date_at
from app.identity.models import Person
from app.portal.departure_dates import next_business_midnight
from app.rbac.service import RbacService
from app.security.encryption import EnvelopeCipher, LookupDigest


class RentalReminderItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: UUID
    asset_code: str
    name: str
    location: str | None
    person_name: str | None
    rental_expires_on: date
    days_remaining: int
    rental_revision: int = Field(ge=0)


class RentalRemindersResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    can_manage: bool
    observed_at: datetime
    business_date: date
    next_refresh_at: datetime
    items: list[RentalReminderItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class PortalRentalReminderService:
    def __init__(self, db: Session, *, cipher: EnvelopeCipher, lookup: LookupDigest):
        self.db, self.cipher, self.lookup = db, cipher, lookup

    def list_reminders(
        self, *, actor: AuthContext, now: datetime, page: int, page_size: int
    ) -> RentalRemindersResponse:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("rental reminder datetime must be timezone-aware")
        today = business_date_at(now)
        bind = self.db.get_bind()
        engine = bind.engine if isinstance(bind, Connection) else bind
        # Never commit/autoflush the auth caller's transaction. Count, page,
        # current identity/readiness and permissions share this one snapshot.
        with (
            engine.connect().execution_options(
                isolation_level="REPEATABLE READ", postgresql_readonly=True
            ) as connection,
            connection.begin(),
            Session(bind=connection, autoflush=False) as snapshot,
        ):
            current = snapshot.execute(
                select(AuthSession, UserAccount, Person)
                .join(UserAccount, UserAccount.id == AuthSession.user_id)
                .join(Person, Person.id == UserAccount.person_id)
                .where(
                    AuthSession.id == actor.session.id,
                    UserAccount.id == actor.account.id,
                    Person.id == actor.person.id,
                    AuthSession.revoked_at.is_(None),
                    AuthSession.expires_at > now,
                    UserAccount.status == "active",
                    Person.deleted_at.is_(None),
                )
            ).one_or_none()
            if current is None:
                raise ProblemError(status=401, code="invalid_session", detail="登录已失效，请重新登录")
            _session, account, _person = current
            readiness = AccountAccessReadinessPolicy(
                snapshot, cipher=self.cipher, lookup=self.lookup
            ).evaluate_one(account, reference_date=today)
            if readiness.missing:
                raise ProblemError(status=401, code="invalid_session", detail="登录已失效，请重新登录")
            if account.force_password_change:
                raise ProblemError(status=403, code="force_password_change_required", detail="请先修改临时密码")
            rbac = RbacService(snapshot)
            available = rbac.decide(
                user_id=account.id, permission="assets.read", resource_tokens={"global"}
            ).allowed
            can_manage = available and rbac.decide(
                user_id=account.id, permission="assets.manage", resource_tokens={"global"}
            ).allowed
            response = RentalRemindersResponse(
                available=available, can_manage=can_manage, observed_at=now.astimezone(UTC),
                business_date=today, next_refresh_at=next_business_midnight(now).astimezone(UTC),
                items=[], total=0, page=page, page_size=page_size,
            )
            if not available:
                return response
            last_day = date.fromordinal(min(date.max.toordinal(), today.toordinal() + 7))
            criteria = (
                Asset.ownership_type == "rented",
                Asset.rental_returned_on.is_(None),
                Asset.status != "retired",
                Asset.rental_started_on <= today,
                Asset.rental_expires_on <= last_day,
            )
            response.total = snapshot.scalar(select(func.count()).select_from(Asset).where(*criteria)) or 0
            # Select only DTO columns; one outer join supplies all visible names.
            # No ORM assets/photos, historical assignments or per-row lookups.
            rows = snapshot.execute(
                select(
                    Asset.id, Asset.asset_code, Asset.name, Asset.location,
                    Person.full_name, Asset.rental_expires_on, Asset.rental_revision,
                )
                .outerjoin(AssetAssignment, and_(
                    AssetAssignment.asset_id == Asset.id,
                    AssetAssignment.returned_at.is_(None),
                ))
                .outerjoin(Person, and_(Person.id == AssetAssignment.person_id, Person.deleted_at.is_(None)))
                .where(*criteria)
                .order_by(Asset.rental_expires_on, Asset.asset_code, Asset.id)
                .offset((page - 1) * page_size).limit(page_size)
            )
            response.items = [
                RentalReminderItem(
                    asset_id=row.id, asset_code=row.asset_code, name=row.name,
                    location=row.location, person_name=row.full_name,
                    rental_expires_on=row.rental_expires_on,
                    days_remaining=(row.rental_expires_on - today).days,
                    rental_revision=row.rental_revision,
                )
                for row in rows
            ]
            return response


def get_rental_reminder_service(
    request: Request, db: Annotated[Session, Depends(get_db)]
) -> PortalRentalReminderService:
    settings: Settings = request.app.state.settings
    return PortalRentalReminderService(
        db,
        cipher=EnvelopeCipher(base64.b64decode(
            settings.field_encryption_master_key.get_secret_value(), altchars=b"-_", validate=True
        )),
        lookup=LookupDigest(base64.b64decode(
            settings.field_lookup_key.get_secret_value(), altchars=b"-_", validate=True
        )),
    )


router = APIRouter(tags=["portal"])


@router.get("/portal/rental-reminders", response_model=RentalRemindersResponse)
def list_rental_reminders(
    actor: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[PortalRentalReminderService, Depends(get_rental_reminder_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RentalRemindersResponse:
    return service.list_reminders(actor=actor, now=datetime.now(UTC), page=page, page_size=page_size)
