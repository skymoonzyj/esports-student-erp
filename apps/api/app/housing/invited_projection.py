from collections.abc import Collection
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Date, Select, case, exists, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.time import business_date_at
from app.housing.models import HousingStay, HousingTransfer
from app.referrals.errors import InvitedHousingInconsistent
from app.referrals.schemas import InvitedHousingStatus, InvitedHousingStay, InvitedHousingSummary

CURRENT = ("checked_in", "checkout_pending")


def _real_checkout() -> ColumnElement[bool]:
    return ~exists().where(HousingTransfer.from_stay_id == HousingStay.id)


def invited_housing_status_expression(
    person_id: ColumnElement[UUID], *, as_of: datetime
) -> ColumnElement[str]:
    current = (
        select(HousingStay.status)
        .where(HousingStay.person_id == person_id, HousingStay.status.in_(CURRENT))
        .order_by(HousingStay.id)
        .limit(1)
        .correlate_except(HousingStay)
        .scalar_subquery()
    )
    planned_date = (
        select(func.timezone("Asia/Shanghai", HousingStay.planned_check_in_at).cast(Date))
        .where(
            HousingStay.person_id == person_id,
            HousingStay.status == "planned",
        )
        .order_by(HousingStay.planned_check_in_at, HousingStay.id)
        .limit(1)
        .correlate_except(HousingStay)
        .scalar_subquery()
    )
    ended = (
        select(HousingStay.id)
        .where(
            HousingStay.person_id == person_id,
            HousingStay.status == "checked_out",
            _real_checkout(),
        )
        .limit(1)
        .correlate_except(HousingStay)
        .exists()
    )
    return case(
        (current.is_not(None), current),
        (
            planned_date.is_not(None),
            case((planned_date <= business_date_at(as_of), "check_in_pending"), else_="reserved"),
        ),
        (ended, "checked_out"),
        else_="unarranged",
    )


def require_consistent_current_stays(db: Session, authorized_ids: Select[Any]) -> None:
    duplicate = db.scalar(
        select(HousingStay.person_id)
        .where(HousingStay.person_id.in_(authorized_ids), HousingStay.status.in_(CURRENT))
        .group_by(HousingStay.person_id)
        .having(func.count() > 1)
        .limit(1)
    )
    if duplicate is not None:
        raise InvitedHousingInconsistent()


def _columns() -> list[Any]:
    return [
        HousingStay.person_id,
        HousingStay.status,
        HousingStay.building_name_snapshot.label("building_name"),
        HousingStay.floor_name_snapshot.label("floor_name"),
        HousingStay.room_name_snapshot.label("room_name"),
        HousingStay.duration_mode,
        HousingStay.planned_check_in_at,
        HousingStay.planned_check_out_at,
        HousingStay.actual_check_in_at,
        HousingStay.actual_check_out_at,
    ]


def _dto(row: Any) -> InvitedHousingStay:
    return InvitedHousingStay.model_validate(
        {key: row[key] for key in InvitedHousingStay.model_fields}
    )


class InvitedHousingProjectionService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def summaries_for_people(
        self, *, authorized_person_ids: Collection[UUID], as_of: datetime
    ) -> dict[UUID, InvitedHousingSummary]:
        if not authorized_person_ids:
            return {}
        current = {}
        for row in self.db.execute(
            select(*_columns()).where(
                HousingStay.person_id.in_(authorized_person_ids), HousingStay.status.in_(CURRENT)
            )
        ).mappings():
            if row["person_id"] in current:
                raise InvitedHousingInconsistent()
            current[row["person_id"]] = row
        selected = []
        for status, ordering in (
            ("planned", (HousingStay.planned_check_in_at, HousingStay.id)),
            ("checked_out", (HousingStay.actual_check_out_at.desc(), HousingStay.id)),
        ):
            statement = select(
                *_columns(),
                func.row_number()
                .over(partition_by=HousingStay.person_id, order_by=ordering)
                .label("position"),
            ).where(HousingStay.person_id.in_(authorized_person_ids), HousingStay.status == status)
            if status == "checked_out":
                statement = statement.where(_real_checkout())
            ranked = statement.subquery()
            selected.append(
                {
                    row["person_id"]: row
                    for row in self.db.execute(
                        select(ranked).where(ranked.c.position == 1)
                    ).mappings()
                }
            )
        planned, checked_out = selected
        result = {}
        for person_id in authorized_person_ids:
            primary = current.get(person_id)
            next_stay = planned.get(person_id)
            status_value = primary["status"] if primary else "unarranged"
            if primary is None and next_stay is not None:
                primary = next_stay
                status_value = (
                    "check_in_pending"
                    if business_date_at(primary["planned_check_in_at"]) <= business_date_at(as_of)
                    else "reserved"
                )
                next_stay = None
            if primary is None and person_id in checked_out:
                primary = checked_out[person_id]
                status_value = "checked_out"
            result[person_id] = InvitedHousingSummary(
                status=cast(InvitedHousingStatus, status_value),
                stay=_dto(primary) if primary else None,
                next_planned_stay=_dto(next_stay) if next_stay else None,
            )
        return result
