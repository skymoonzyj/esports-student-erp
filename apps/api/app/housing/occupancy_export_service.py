from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO, StringIO
from typing import Literal, cast
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.auth.models import UserAccount
from app.housing.access import REPORT_PERMISSION, HousingAccessService
from app.housing.history_validity import effective_stay_clause
from app.housing.inventory_service import HousingInventoryService, HousingOverviewFilters
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom, HousingStay
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.schemas import HousingOccupancyExportRequest
from app.housing.sorting import building_order_expressions
from app.identity.document_identity import DocumentType, normalize_document_identity
from app.identity.models import Person, StaffProfile, StudentProfile, VisitorProfile
from app.identity.normalization import IdentityValidationError, normalize_cn_id, normalize_phone
from app.personnel_intake.models import AssistedPersonProfile
from app.personnel_intake.readiness import AssistedReadinessPolicy
from app.security.encryption import EncryptedFieldError, EnvelopeCipher, LookupDigest

OccupancyExportFormat = Literal["xlsx", "csv"]

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_SEX_LABELS = {"male": "男", "female": "女"}
_HEADERS = (
    "房号",
    "入住人",
    "性别",
    "身份证号",
    "手机号",
    "入住时间",
    "离开时间",
    "人员类型",
    "账号状态",
    "档案来源",
)
_PERSON_LABELS = {
    "student": "学员",
    "player": "队员",
    "student_player": "学员兼队员",
    "team_manager": "战队经理",
    "coach": "教练",
    "assistant_coach": "助教",
    "life_teacher": "生活老师",
    "customer_service": "客服",
    "administration": "行政",
    "visitor": "临时访客",
    "other": "其他工作人员",
}
_ACCOUNT_LABELS = {
    "not_activated": "未激活",
    "active": "已激活",
    "disabled": "已停用",
    "deleted": "已删除",
}


@dataclass(frozen=True, slots=True)
class OccupancyExportRow:
    room_number: str
    person_name: str
    sex: str | None
    identity_number: str
    phone: str | None
    check_in_at: datetime
    check_out_at: datetime | None
    person_type: str = "other"
    account_status: str = "not_activated"
    profile_source: str = "legacy"
    document_type: str = "cn_id"
    profile_missing: tuple[str, ...] = ()


class HousingOccupancyExportService:
    """Build an export from the same room projection used by the overview."""

    def __init__(
        self,
        db: Session,
        *,
        cipher: EnvelopeCipher,
        lookup: LookupDigest,
        access: HousingAccessService,
    ) -> None:
        self.db = db
        self.cipher = cipher
        self.lookup = lookup
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
            quick_filter=request.quick_filter,
            cleaning_statuses=frozenset(request.cleaning_statuses),
            daily_cleaning_enabled=request.daily_cleaning_enabled,
            cleaning_frequencies=frozenset(request.cleaning_frequencies),
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
            if inventory._matches_filters(card, filters, projection) and inventory._matches_quick_filter(
                card, filters.quick_filter
            ):
                selected_rooms.append(room)
        if not selected_rooms:
            return []

        selected_ids = [room.id for room in selected_rooms]
        stay_rows = list(
            self.db.execute(
                select(
                    HousingStay,
                    Person,
                    StudentProfile.person_id,
                    StaffProfile.staff_type,
                    VisitorProfile.person_id,
                    HousingOccupancyService.assisted_kind_expression(),
                    UserAccount.status,
                    AssistedPersonProfile.person_id,
                )
                .join(Person, Person.id == HousingStay.person_id)
                .outerjoin(StudentProfile, StudentProfile.person_id == Person.id)
                .outerjoin(StaffProfile, StaffProfile.person_id == Person.id)
                .outerjoin(VisitorProfile, VisitorProfile.person_id == Person.id)
                .outerjoin(UserAccount, UserAccount.person_id == Person.id)
                .outerjoin(AssistedPersonProfile, AssistedPersonProfile.person_id == Person.id)
                .where(
                    HousingStay.room_id.in_(selected_ids),
                    effective_stay_clause(),
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
        pending_ids = [person.id for _, person, *_ in stay_rows if person.intake_mode == "pending"]
        readiness = AssistedReadinessPolicy(
            self.db, cipher=self.cipher, lookup=self.lookup
        ).evaluate_many(
            pending_ids,
            reference_date=as_of.astimezone(_SHANGHAI).date(),
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
                    document_type=person.document_type,
                    issuing_country=person.document_issuing_country,
                    sex=person.sex,
                ),
                phone=decrypt_phone(self.cipher, person.phone_ciphertext),
                check_in_at=stay.actual_check_in_at,
                check_out_at=stay.actual_check_out_at,
                person_type=HousingOccupancyService._person_type(
                    student_id, staff_type, visitor_id, kind
                ),
                account_status=status or "not_activated",
                profile_source="approved_profile" if approved_id else "legacy",
                document_type=person.document_type,
                profile_missing=readiness[person.id].activation_missing
                if person.id in readiness
                else (),
            )
            for stay, person, student_id, staff_type, visitor_id, kind, status, approved_id in stay_rows
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


def decrypt_identity(
    cipher: EnvelopeCipher,
    ciphertext: str,
    birth_date: date | None,
    *,
    document_type: str = "cn_id",
    issuing_country: str | None = None,
    sex: str | None = None,
) -> str:
    try:
        number = cipher.decrypt(ciphertext, field_name="identity_number")
        if document_type != "cn_id":
            normalize_document_identity(
                cast(DocumentType, document_type),
                number,
                issuing_country=issuing_country,
                sex=sex,
                date_of_birth=birth_date,
                allow_pending=True,
            )
            # Passport numbers are document facts, not a source of gender or DOB.
            return number
        return normalize_cn_id(
            number,
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
    annotations = ["审核建档" if row.profile_source == "approved_profile" else "原有档案"]
    if row.document_type == "passport":
        # Preserve the legacy 10-column contract and the raw number cell while
        # making the document type unambiguous in its existing annotation cell.
        annotations.append("护照（原号）")
    elif row.document_type == "hk_id":
        annotations.append("香港身份证")
    elif row.document_type == "hk_mo_permit":
        annotations.append("回乡证（港澳居民来往内地通行证）")
    if row.profile_missing:
        labels = {
            "phone": "联系电话",
            "date_of_birth": "出生日期",
            "sex": "性别",
            "document_issuing_country": "签发国家",
            "guardian": "监护资料",
            "guardian_age": "监护年龄待核对",
            "email": "邮箱",
        }
        annotations.append(
            "资料待补："
            + "、".join(labels.get(field, "审批资料待核对") for field in row.profile_missing)
        )
    return (
        _safe_cell(row.room_number),
        _safe_cell(row.person_name),
        _safe_cell(_SEX_LABELS.get(row.sex or "", "待补")),
        _safe_cell(row.identity_number),
        _safe_cell(row.phone),
        _safe_cell(business_date(row.check_in_at)),
        _safe_cell(business_date(row.check_out_at)),
        _safe_cell(_PERSON_LABELS.get(row.person_type, row.person_type)),
        _safe_cell(_ACCOUNT_LABELS.get(row.account_status, row.account_status)),
        _safe_cell("；".join(annotations)),
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
    widths = (14, 18, 10, 24, 16, 14, 14, 18, 14, 16)
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    xlsx_output = BytesIO()
    workbook.save(xlsx_output)
    return (
        xlsx_output.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        f"housing-occupancy-{timestamp}.xlsx",
    )
