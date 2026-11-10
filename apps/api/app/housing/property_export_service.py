from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from datetime import datetime
from io import BytesIO, StringIO
from uuid import UUID

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.core.errors import ProblemError
from app.core.privacy import (
    contains_email,
    contains_labeled_sensitive_value,
    contains_phone_or_identity,
)
from app.housing.access import HousingAccessService
from app.housing.models import (
    HousingBuilding,
    HousingFloor,
    HousingRoom,
    HousingRoomIssue,
    HousingStay,
)
from app.housing.occupancy_export_service import _safe_cell, business_date
from app.housing.property_schemas import PropertyExportRequest
from app.identity.models import Person


def public_note(value: str | None) -> str:
    value = value or ""
    if (
        contains_phone_or_identity(value)
        or contains_email(value)
        or contains_labeled_sensitive_value(value)
    ):
        return "[含敏感资料，已隐藏]"
    return value


def render_property_export(
    headers: Sequence[str], rows: Sequence[Sequence[object]], *, format: str, occurred_at: datetime
) -> tuple[bytes, str, str]:
    values = [[_safe_cell(value) for value in row] for row in rows]
    filename = "housing-basic-" + occurred_at.strftime("%Y%m%d")
    if format == "csv":
        stream = StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\r\n")
        writer.writerow(headers)
        writer.writerows(values)
        return stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", filename + ".csv"
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "住宿摘要"
    sheet.append(list(headers))
    for row in values:
        sheet.append(row)
    sheet.freeze_panes = "A2"
    stream_bytes = BytesIO()
    workbook.save(stream_bytes)
    return (
        stream_bytes.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename + ".xlsx",
    )


class HousingPropertyExportService:
    def __init__(self, db: Session, actor_id: UUID) -> None:
        self.db, self.actor_id = db, actor_id
        self.access_revision = ""
        # Internal authorization facts only; never written into the export file.
        self.exported_room_scopes: dict[UUID, tuple[UUID, UUID]] = {}

    def _begin_snapshot(self) -> None:
        if self.db.in_transaction():
            raise ProblemError(
                status=503,
                code="housing_export_snapshot_unavailable",
                detail="导出快照不可用，请重试",
            )
        bind = self.db.get_bind()
        connection = (
            self.db.connection()
            if isinstance(bind, Connection) and bind.in_transaction()
            else self.db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        )
        if connection.get_isolation_level().upper() != "REPEATABLE READ":
            self.db.rollback()
            raise ProblemError(
                status=503,
                code="housing_export_snapshot_unavailable",
                detail="导出快照不可用，请重试",
            )

    def _validate_scope(
        self, input: PropertyExportRequest, access: HousingAccessService
    ) -> list[UUID]:
        criteria = access.room_permissions_criteria("housing.read", "housing.exports.basic")
        visible_buildings = set(
            self.db.scalars(select(HousingRoom.building_id).where(criteria).distinct())
        )
        if not visible_buildings or any(
            building_id not in visible_buildings for building_id in input.building_ids
        ):
            raise ProblemError(
                status=403, code="permission_denied", detail="没有所选范围的导出权限"
            )
        return list(input.building_ids or visible_buildings)

    def lock_exported_rooms(
        self,
        room_scopes: Mapping[UUID, tuple[UUID, UUID]],
    ) -> list[HousingRoom]:
        """Lock every emitted identity before refreshing live authorization.

        The permission revision does not include room-to-floor/building facts.
        Hold shared row locks through audit/commit so a scope move cannot race
        between this check and the successful export transaction's commit.
        """
        if not room_scopes:
            return []
        return list(
            self.db.scalars(
                select(HousingRoom)
                .where(HousingRoom.id.in_(room_scopes))
                .order_by(HousingRoom.id)
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            )
        )

    @staticmethod
    def validate_exported_rooms(
        room_scopes: Mapping[UUID, tuple[UUID, UUID]],
        rooms: Sequence[HousingRoom],
        access: HousingAccessService,
    ) -> None:
        """Authorize locked identities using rules refreshed after the lock wait."""
        if len(rooms) != len(room_scopes) or any(
            (room.building_id, room.floor_id) != room_scopes[room.id]
            or not access.can_export_basic(room)
            for room in rooms
        ):
            raise ProblemError(
                status=409,
                code="housing_access_changed",
                detail="导出房间范围已发生变化，请重新导出",
            )

    def rows(
        self, input: PropertyExportRequest, *, as_of: datetime
    ) -> tuple[list[str], list[list[object]]]:
        self._begin_snapshot()
        access = HousingAccessService(self.db, self.actor_id, as_of)
        self.access_revision = access.access_revision
        building_ids = self._validate_scope(input, access)
        scope = access.room_permissions_criteria("housing.read", "housing.exports.basic")
        base = (
            select(HousingRoom, HousingBuilding.name, HousingFloor.name)
            .join(HousingBuilding, HousingBuilding.id == HousingRoom.building_id)
            .join(HousingFloor, HousingFloor.id == HousingRoom.floor_id)
            .where(scope, HousingRoom.building_id.in_(building_ids))
            .order_by(
                HousingBuilding.display_order,
                HousingBuilding.code,
                HousingFloor.number,
                HousingRoom.room_number,
            )
        )
        rows: list[list[object]] = []
        if input.kind == "problems":
            headers = ["楼栋", "楼层", "房号", "房型", "服务状态", "房间备注", "未解决问题说明"]
            labels = {
                "problem": "问题房",
                "maintenance": "维修中",
                "disabled": "停用",
                "single": "单人间",
                "double": "双人间",
                "triple": "三人间",
                "custom": "自定义",
                "functional": "功能房",
            }
            for room, building_name, floor_name in self.db.execute(
                base.where(HousingRoom.service_status.in_(("problem", "maintenance", "disabled")))
            ):
                self.exported_room_scopes[room.id] = (room.building_id, room.floor_id)
                issues = self.db.scalars(
                    select(HousingRoomIssue.description)
                    .where(HousingRoomIssue.room_id == room.id, HousingRoomIssue.status == "open")
                    .order_by(HousingRoomIssue.reported_at, HousingRoomIssue.id)
                )
                rows.append(
                    [
                        building_name,
                        floor_name,
                        room.room_number,
                        labels.get(room.room_type, room.room_type),
                        labels[room.service_status],
                        public_note(room.notes),
                        "；".join(public_note(issue) for issue in issues),
                    ]
                )
            return headers, rows
        from app.housing.room_card_models import HousingRoomCardRecord

        headers = [
            "楼栋",
            "楼层",
            "房号",
            "姓名",
            "昵称",
            "性别",
            "实际入住日期",
            "计划离开日期",
            "房卡开卡日",
            "房卡到期日",
        ]
        stmt = (
            base.add_columns(HousingStay, Person, HousingRoomCardRecord)
            .join(HousingStay, HousingStay.room_id == HousingRoom.id)
            .join(Person, Person.id == HousingStay.person_id)
            .outerjoin(HousingRoomCardRecord, HousingRoomCardRecord.stay_id == HousingStay.id)
            .where(
                HousingStay.status.in_(("checked_in", "checkout_pending")),
                HousingStay.voided_at.is_(None),
                HousingStay.actual_check_in_at.is_not(None),
                HousingStay.actual_check_in_at <= as_of,
                HousingStay.actual_check_out_at.is_(None),
                Person.deleted_at.is_(None),
            )
        )
        for room, building_name, floor_name, stay, person, card in self.db.execute(stmt):
            self.exported_room_scopes[room.id] = (room.building_id, room.floor_id)
            rows.append(
                [
                    building_name,
                    floor_name,
                    room.room_number,
                    person.full_name,
                    person.nickname or "",
                    {"male": "男", "female": "女"}.get(person.sex, "未填写"),
                    business_date(stay.actual_check_in_at),
                    "常住"
                    if stay.duration_mode == "long_term"
                    else business_date(stay.planned_check_out_at),
                    card.issued_on.isoformat() if card and card.issued_on else "",
                    card.expires_on.isoformat() if card and card.expires_on else "",
                ]
            )
        return headers, rows
