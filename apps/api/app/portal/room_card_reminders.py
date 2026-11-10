from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select, true
from sqlalchemy.orm import Session

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.time import BUSINESS_TIME_ZONE, business_date_at
from app.housing.access import HousingAccessService
from app.housing.api_support import PrivateHousingRoute
from app.housing.models import HousingBuilding, HousingRoom, HousingStay
from app.housing.room_card_models import HousingRoomCardRecord
from app.housing.room_card_service import current_card_stay_criteria
from app.identity.models import Person

router = APIRouter(tags=["portal"], route_class=PrivateHousingRoute)


def reminder_clock(observed_at: datetime) -> tuple[date, date, datetime]:
    today = business_date_at(observed_at)
    return (
        today,
        today + timedelta(days=7),
        datetime.combine(today + timedelta(days=1), time.min, tzinfo=BUSINESS_TIME_ZONE),
    )


class RoomCardReminderItem(BaseModel):
    stay_id: UUID
    person_id: UUID
    person_name: str
    nickname: str | None
    room_number: str
    building_name: str
    issued_on: date
    expires_on: date
    days_remaining: int


class RoomCardRemindersResponse(BaseModel):
    available: bool
    observed_at: datetime
    business_date: date
    next_refresh_at: datetime
    items: list[RoomCardReminderItem]
    total: int
    page: int
    page_size: int


def list_room_card_reminders(
    db: Session,
    *,
    account_id: UUID,
    person_id: UUID,
    observed_at: datetime,
    page: int,
    page_size: int,
) -> RoomCardRemindersResponse:
    today, cutoff, refresh = reminder_clock(observed_at)
    access = HousingAccessService(db, account_id, observed_at)
    scope = HousingStay.person_id == person_id
    if access.has_global_room_card_role:
        scope = or_(scope, access.room_permission_criteria("housing.read"))
    query = (
        select(
            HousingStay.id.label("stay_id"),
            Person.id.label("person_id"),
            Person.full_name.label("person_name"),
            Person.nickname,
            HousingRoom.room_number,
            HousingBuilding.name.label("building_name"),
            HousingRoomCardRecord.issued_on,
            HousingRoomCardRecord.expires_on,
        )
        .select_from(HousingStay)
        .join(Person, Person.id == HousingStay.person_id)
        .join(HousingRoom, HousingRoom.id == HousingStay.room_id)
        .join(HousingBuilding, HousingBuilding.id == HousingRoom.building_id)
        .join(HousingRoomCardRecord, HousingRoomCardRecord.stay_id == HousingStay.id)
        .where(
            *current_card_stay_criteria(observed_at),
            HousingRoomCardRecord.expires_on <= cutoff,
            scope,
        )
    )
    # Count and bounded page share one SQL snapshot, including empty pages.
    candidates = query.cte("card_candidates")
    counts = select(func.count().label("total")).select_from(candidates).subquery()
    paged = (
        select(candidates)
        .order_by(candidates.c.expires_on, candidates.c.stay_id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .subquery()
    )
    rows = (
        db.execute(
            select(counts.c.total, *paged.c)
            .select_from(counts.outerjoin(paged, true()))
            .order_by(paged.c.expires_on, paged.c.stay_id)
        )
        .mappings()
        .all()
    )
    return RoomCardRemindersResponse(
        available=True,
        observed_at=observed_at,
        business_date=today,
        next_refresh_at=refresh,
        items=[
            RoomCardReminderItem(
                **{key: value for key, value in row.items() if key != "total"},
                days_remaining=(row["expires_on"] - today).days,
            )
            for row in rows
            if row["stay_id"] is not None
        ],
        total=rows[0]["total"],
        page=page,
        page_size=page_size,
    )


@router.get("/portal/room-card-reminders", response_model=RoomCardRemindersResponse)
def get_room_card_reminders(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RoomCardRemindersResponse:
    return list_room_card_reminders(
        db,
        account_id=context.account.id,
        person_id=context.person.id,
        observed_at=datetime.now(UTC),
        page=page,
        page_size=page_size,
    )
