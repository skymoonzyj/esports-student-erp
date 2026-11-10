from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from app.core.errors import ProblemError
from app.housing.access import HousingAccessService
from app.housing.history_validity import effective_stay_clause
from app.housing.models import HousingRoom, HousingStay
from app.housing.room_card_models import HousingRoomCardRecord
from app.housing.room_card_schemas import RoomCardItem, RoomCardList, RoomCardSave
from app.identity.models import Person


def current_card_stay_criteria(observed_at: datetime) -> tuple[ColumnElement[bool], ...]:
    return (
        effective_stay_clause(),
        HousingStay.status.in_(("checked_in", "checkout_pending")),
        HousingStay.actual_check_in_at <= observed_at,
        HousingStay.actual_check_out_at.is_(None),
        Person.deleted_at.is_(None),
    )


def room_card_item(
    stay: HousingStay, person: Person, card: HousingRoomCardRecord | None, *, can_edit: bool
) -> RoomCardItem:
    return RoomCardItem(
        stay_id=stay.id,
        person_id=person.id,
        person_name=person.full_name,
        nickname=person.nickname,
        issued_on=card.issued_on if card else None,
        expires_on=card.expires_on if card else None,
        version=card.version if card else 0,
        can_edit=can_edit,
    )


class HousingRoomCardService:
    def __init__(self, db: Session):
        self.db = db

    def list_room(
        self, room_id: UUID, access: HousingAccessService, observed_at: datetime
    ) -> RoomCardList:
        room = self.db.get(HousingRoom, room_id)
        if room is None or not access.can_read_room(room):
            raise ProblemError(status=404, code="housing_resource_not_found", detail="房间不存在")
        rows = self.db.execute(
            select(HousingStay, Person, HousingRoomCardRecord)
            .join(Person, Person.id == HousingStay.person_id)
            .outerjoin(HousingRoomCardRecord, HousingRoomCardRecord.stay_id == HousingStay.id)
            .where(HousingStay.room_id == room.id, *current_card_stay_criteria(observed_at))
            .order_by(Person.full_name, HousingStay.id)
        ).all()
        return RoomCardList(
            items=[
                room_card_item(stay, person, card, can_edit=access.can_manage_keycards(room))
                for stay, person, card in rows
            ]
        )

    def save(
        self,
        stay_id: UUID,
        input: RoomCardSave,
        *,
        actor_user_id: UUID,
        observed_at: datetime,
        authorize: Callable[[HousingRoom], None],
    ) -> tuple[RoomCardItem, UUID, dict[str, str | int | None]]:
        preliminary = self.db.execute(
            select(HousingStay.person_id, HousingStay.room_id).where(HousingStay.id == stay_id)
        ).one_or_none()
        if preliminary is None:
            raise ProblemError(
                status=404, code="housing_resource_not_found", detail="住宿记录不存在"
            )
        # Same order as the lifecycle service: person, room, all affected active stays,
        # then the target fact. Serializing on the stay also protects first-card insert.
        person = self.db.scalar(
            select(Person)
            .where(Person.id == preliminary.person_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        room = self.db.scalar(
            select(HousingRoom)
            .where(HousingRoom.id == preliminary.room_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        self.db.scalars(
            select(HousingStay)
            .where(
                HousingStay.status.in_(("planned", "checked_in", "checkout_pending")),
                or_(
                    HousingStay.person_id == preliminary.person_id,
                    HousingStay.room_id == preliminary.room_id,
                ),
            )
            .order_by(HousingStay.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        stay = self.db.scalar(
            select(HousingStay)
            .where(HousingStay.id == stay_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if room is None:
            raise ProblemError(status=404, code="housing_resource_not_found", detail="房间不存在")
        authorize(room)
        if (
            stay is None
            or person is None
            or person.deleted_at is not None
            or stay.person_id != preliminary.person_id
            or stay.room_id != room.id
            or stay.status not in {"checked_in", "checkout_pending"}
            or stay.voided_at is not None
            or stay.actual_check_in_at is None
            or stay.actual_check_in_at > observed_at
            or stay.actual_check_out_at is not None
        ):
            raise ProblemError(
                status=409,
                code="housing_room_card_stay_inactive",
                detail="仅可编辑当前有效入住的房卡，请刷新",
            )
        card = self.db.scalar(
            select(HousingRoomCardRecord)
            .where(HousingRoomCardRecord.stay_id == stay.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        version = card.version if card else 0
        if input.expected_version is None or input.expected_version != version:
            raise ProblemError(
                status=409,
                code="housing_room_card_stale_version",
                detail="房卡记录已变化或缺少版本，请刷新后重试",
            )
        metadata: dict[str, str | int | None] = {
            "stay_id": str(stay.id),
            "room_id": str(room.id),
            "version": version + 1,
            "previous_card_issued_on": card.issued_on.isoformat()
            if card and card.issued_on
            else None,
            "previous_card_expires_on": card.expires_on.isoformat()
            if card and card.expires_on
            else None,
            "card_issued_on": input.issued_on.isoformat() if input.issued_on else None,
            "card_expires_on": input.expires_on.isoformat() if input.expires_on else None,
        }
        if card is None:
            card = HousingRoomCardRecord(
                stay_id=stay.id, version=1, updated_by_user_id=actor_user_id
            )
            self.db.add(card)
        else:
            card.version += 1
        card.issued_on, card.expires_on = input.issued_on, input.expires_on
        card.updated_by_user_id = actor_user_id
        self.db.flush()
        return room_card_item(stay, person, card, can_edit=True), room.id, metadata
