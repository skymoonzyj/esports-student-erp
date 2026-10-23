from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO, StringIO
from typing import Literal
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.housing.access import REPORT_PERMISSION, HousingAccessService
from app.housing.inventory_service import HousingInventoryService, HousingOverviewFilters
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom, HousingStay
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.schemas import HousingOccupancyExportRequest
from app.housing.sorting import building_order_expressions
from app.identity.models import Person
from app.identity.normalization import IdentityValidationError, normalize_cn_id, normalize_phone
from app.security.encryption import EncryptedFieldError, EnvelopeCipher

OccupancyExportFormat = Literal["xlsx", "csv"]

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_SEX_LABELS = {"male": "男", "female": "女"}
_HEADERS = ("房号", "入住人", "性别", "身份证号", "手机号", "入住时间", "离开时间")


@dataclass(frozen=True, slots=True)
class OccupancyExportRow:
    room_number: str
    person_name: str
    sex: str
    identity_number: str
    phone: str | None
    check_in_at: datetime
    check_out_at: datetime | None


class HousingOccupancyExportService:
    """Build an export from the same room projection used by the overview."""

    def __init__(
        self,
        db: Session,
        *,
        cipher: EnvelopeCipher,
        access: HousingAccessService,
    ) -> None:
        self.db = db
        self.cipher = cipher
        self.access = access

    def rows(
        self,
        request: HousingOccupancyExportRequest,
        *,
        as_of: datetime,
    ) -> list[OccupancyExportRow]:
        filters = HousingOverviewFilters(
            building_id=request.building_id,
            floor_id=request.floor_id,
            query=request.query,
            room_types=frozenset(request.room_types),
            verification_statuses=frozenset(request.verification_statuses),
            service_statuses=frozenset(request.service_statuses),
            current_gender=request.current_gender,
            occupancy=request.occupancy,
            assignability=request.assignability,
        )
        room_rows = self.db.execute(
            select(HousingRoom, HousingBuilding, HousingFloor)
            .join(HousingBuilding, HousingBuilding.id == HousingRoom.building_id)
            .join(HousingFloor, HousingFloor.id == HousingRoom.floor_id)
            .where(
                self.access.room_permissions_criteria(
                    "housing.read",
                    REPORT_PERMISSION,
                ),
                *self._room_filter_criteria(request),
            )
            .order_by(
                *building_order_expressions(),
                HousingBuilding.name,
                HousingFloor.display_order,
                HousingFloor.number,
                HousingRoom.room_number,
                HousingRoom.id,
            )
        ).all()
        if not room_rows:
            return []

        room_ids = [room.id for room, _, _ in room_rows]
        projections = HousingOccupancyService(self.db).room_projections(room_ids, as_of)
        inventory = HousingInventoryService(self.db)
        selected_rooms: list[HousingRoom] = []
        for room, building, floor in room_rows:
            projection = projections[room.id]
            card = inventory._room_card(room, building, floor, self.access, projection)
            if inventory._matches_filters(card, filters, projection):
                selected_rooms.append(room)
        if not selected_rooms:
            return []

        selected_ids = [room.id for room in selected_rooms]
        stay_rows = list(
            self.db.execute(
                select(HousingStay, Person)
                .join(Person, Person.id == HousingStay.person_id)
                .where(
                    HousingStay.room_id.in_(selected_ids),
                    HousingStay.status.in_(("checked_in", "checkout_pending", "checked_out")),
                    HousingStay.actual_check_in_at.is_not(None),
                )
                .order_by(
                    HousingStay.room_id,
                    HousingStay.actual_check_in_at,
                    HousingStay.id,
                )
            ).all()
        )
        room_order = {room.id: index for index, room in enumerate(selected_rooms)}
        stay_rows.sort(
            key=lambda item: (
                room_order[item[0].room_id],
                item[0].actual_check_in_at or as_of,
                str(item[0].id),
            )
        )
        return [
            OccupancyExportRow(
                room_number=stay.room_name_snapshot,
                person_name=person.full_name,
                sex=person.sex,
                identity_number=decrypt_identity(
                    self.cipher,
                    person.identity_ciphertext,
                    person.date_of_birth,
                ),
                phone=decrypt_phone(self.cipher, person.phone_ciphertext),
                check_in_at=stay.actual_check_in_at,
                check_out_at=stay.actual_check_out_at,
            )
            for stay, person in stay_rows
        ]

    @staticmethod
    def _room_filter_criteria(
        request: HousingOccupancyExportRequest,
    ) -> list[ColumnElement[bool]]:
        criteria: list[ColumnElement[bool]] = []
        if request.building_id is not None:
            criteria.append(HousingRoom.building_id == request.building_id)
        if request.floor_id is not None:
            criteria.append(HousingRoom.floor_id == request.floor_id)
        if request.room_types:
            criteria.append(HousingRoom.room_type.in_(request.room_types))
        if request.verification_statuses:
            criteria.append(HousingRoom.verification_status.in_(request.verification_statuses))
        if request.service_statuses:
            criteria.append(HousingRoom.service_status.in_(request.service_statuses))
        if request.query:
            pattern = f"%{request.query}%"
            criteria.append(
                HousingRoom.room_number.ilike(pattern)
                | HousingBuilding.code.ilike(pattern)
                | HousingBuilding.name.ilike(pattern)
                | HousingFloor.name.ilike(pattern)
            )
        return criteria


def _safe_cell(value: object) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_DANGEROUS_PREFIXES) else text


def business_date(value: datetime | None) -> str:
    """Render business dates without exposing time-of-day information."""

    if value is None:
        return ""
    return value.astimezone(_SHANGHAI).date().isoformat()


def decrypt_identity(cipher: EnvelopeCipher, ciphertext: str, birth_date: date) -> str:
    try:
        return normalize_cn_id(
            cipher.decrypt(ciphertext, field_name="identity_number"),
            expected_birth_date=birth_date,
        ).number
    except (EncryptedFieldError, IdentityValidationError) as exc:
        raise ValueError("housing resident identity data is unavailable") from exc


def decrypt_phone(cipher: EnvelopeCipher, ciphertext: str | None) -> str | None:
    if ciphertext is None:
        return None
    try:
        return normalize_phone(cipher.decrypt(ciphertext, field_name="phone"))
    except (EncryptedFieldError, IdentityValidationError):
        # Phone binding is optional. A malformed legacy value is treated as
        # unavailable rather than allowing an unvalidated value into a file.
        return None


def row_values(row: OccupancyExportRow) -> tuple[str, ...]:
    return (
        _safe_cell(row.room_number),
        _safe_cell(row.person_name),
        _safe_cell(_SEX_LABELS.get(row.sex, row.sex)),
        _safe_cell(row.identity_number),
        _safe_cell(row.phone),
        _safe_cell(business_date(row.check_in_at)),
        _safe_cell(business_date(row.check_out_at)),
    )


def render_occupancy_export(
    rows: list[OccupancyExportRow],
    *,
    format: OccupancyExportFormat,
    occurred_at: datetime,
) -> tuple[bytes, str, str]:
    values = [row_values(row) for row in rows]
    timestamp = occurred_at.astimezone(_SHANGHAI).strftime("%Y%m%d")
    if format == "csv":
        csv_output = StringIO(newline="")
        writer = csv.writer(csv_output, dialect="excel", lineterminator="\r\n")
        writer.writerow(_HEADERS)
        writer.writerows(values)
        return (
            csv_output.getvalue().encode("utf-8-sig"),
            "text/csv; charset=utf-8",
            f"housing-occupancy-{timestamp}.csv",
        )

    workbook = Workbook()
    sheet = workbook.active
    assert isinstance(sheet, Worksheet), "workbook must create a default worksheet"
    sheet.title = "房态入住"
    sheet.append(_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for values_row in values:
        sheet.append(values_row)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(_HEADERS))}{len(values) + 1}"
    widths = (14, 18, 10, 24, 16, 14, 14)
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    xlsx_output = BytesIO()
    workbook.save(xlsx_output)
    return (
        xlsx_output.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        f"housing-occupancy-{timestamp}.xlsx",
    )
