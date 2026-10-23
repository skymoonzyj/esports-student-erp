from __future__ import annotations

import csv
from collections.abc import Callable, Sequence
from datetime import date, datetime
from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.housing.reporting_types import (
    GuardianReportRow,
    HousingReportProjectionV1,
    ResidentReportRow,
)

RESIDENT_SHEET_NAME = "入住名单"
GUARDIAN_SHEET_NAME = "未成年监护人"

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_SEX_LABELS = {"male": "男", "female": "女"}
_PERSON_TYPE_LABELS = {
    "student": "学员",
    "player": "队员",
    "student_player": "学员兼队员",
    "coach": "教练",
    "life_teacher": "生活老师",
    "customer_service": "客服",
    "administration": "行政",
    "other": "其他工作人员",
    "visitor": "临时访客",
}
_PURPOSE_LABELS = {
    "permanent": "常住",
    "camp": "随营",
    "temporary": "临时",
}
_STATUS_LABELS = {
    "checked_in": "已入住",
    "checkout_pending": "退房待确认",
    "checked_out": "已退房",
}

type Table = tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]


def safe_spreadsheet_text(value: object) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_DANGEROUS_PREFIXES) else text


def _date(value: date | None) -> str:
    return "" if value is None else value.isoformat()


def _timestamp(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(_SHANGHAI).isoformat(sep=" ", timespec="seconds")


_RESIDENT_COLUMNS: tuple[tuple[str, Callable[[ResidentReportRow], object]], ...] = (
    ("行号", lambda row: row.row_id),
    ("住宿记录ID", lambda row: row.stay_id),
    ("人员ID", lambda row: row.person_id),
    ("姓名", lambda row: row.full_name),
    ("手机号", lambda row: row.phone),
    ("身份证号", lambda row: row.identity_number),
    ("性别", lambda row: _SEX_LABELS[row.sex]),
    ("出生日期", lambda row: _date(row.date_of_birth)),
    ("人员类型", lambda row: _PERSON_TYPE_LABELS[row.person_type]),
    ("楼栋ID", lambda row: row.building_id),
    ("楼栋", lambda row: row.building_name),
    ("楼层ID", lambda row: row.floor_id),
    ("楼层", lambda row: row.floor_name),
    ("房间ID", lambda row: row.room_id),
    ("房号", lambda row: row.room_number),
    ("住宿类型", lambda row: _PURPOSE_LABELS[row.stay_purpose]),
    ("实际入住时间", lambda row: _timestamp(row.actual_check_in_at)),
    ("实际退房时间", lambda row: _timestamp(row.actual_check_out_at)),
    ("住宿状态", lambda row: _STATUS_LABELS[row.stay_status]),
    ("营期ID", lambda row: row.cohort_id),
    ("营期", lambda row: row.cohort_name),
    ("临时来访原因", lambda row: row.visit_reason),
    ("是否未成年", lambda row: "是" if row.is_minor_at_check_in else "否"),
    ("监护人数", lambda row: row.guardian_count),
)

_GUARDIAN_COLUMNS: tuple[tuple[str, Callable[[GuardianReportRow], object]], ...] = (
    ("住户行号", lambda row: row.resident_row_id),
    ("学员ID", lambda row: row.student_id),
    ("学员姓名", lambda row: row.student_name),
    ("监护人ID", lambda row: row.guardian_id),
    ("监护人姓名", lambda row: row.guardian_name),
    ("监护人手机号", lambda row: row.guardian_phone),
    ("监护人身份证号", lambda row: row.guardian_identity_number),
)


def _table[RowT](
    rows: Sequence[RowT],
    columns: Sequence[tuple[str, Callable[[RowT], object]]],
) -> Table:
    headers = tuple(label for label, _ in columns)
    values = tuple(
        tuple(safe_spreadsheet_text(getter(row)) for _, getter in columns)
        for row in rows
    )
    return headers, values


def resident_table(projection: HousingReportProjectionV1) -> Table:
    if any(row.phone_evidence == "approved_profile" for row in projection.residents):
        return _table(
            projection.residents,
            (
                *_RESIDENT_COLUMNS,
                ("手机号核验", lambda row: "已验证" if row.phone_verified else "已审核，未验证"),
            ),
        )
    return _table(projection.residents, _RESIDENT_COLUMNS)


def guardian_table(projection: HousingReportProjectionV1) -> Table:
    return _table(projection.guardians, _GUARDIAN_COLUMNS)


def _write_sheet(sheet: Worksheet, table: Table) -> None:
    headers, rows = table
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for column_index, header in enumerate(headers, start=1):
        values = [header, *(row[column_index - 1] for row in rows)]
        visible_width = max(
            len(part)
            for value in values
            for part in (value.splitlines() or [""])
        )
        sheet.column_dimensions[get_column_letter(column_index)].width = min(
            max(visible_width + 2, 10),
            32,
        )


def render_xlsx(projection: HousingReportProjectionV1) -> bytes:
    workbook = Workbook()
    default_sheet = workbook.active
    assert isinstance(default_sheet, Worksheet), "workbook must create a default worksheet"
    workbook.remove(default_sheet)
    workbook.properties.creator = ""
    workbook.properties.lastModifiedBy = ""
    workbook.properties.title = "住宿实名报送"
    workbook.properties.subject = ""
    workbook.properties.description = ""
    _write_sheet(
        workbook.create_sheet(RESIDENT_SHEET_NAME),
        resident_table(projection),
    )
    _write_sheet(
        workbook.create_sheet(GUARDIAN_SHEET_NAME),
        guardian_table(projection),
    )
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _csv_bytes(table: Table) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output, dialect="excel", lineterminator="\r\n")
    headers, rows = table
    writer.writerow(headers)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def _zip_member(name: str, content: bytes) -> ZipInfo:
    info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    info.file_size = len(content)
    return info


def render_csv_zip(projection: HousingReportProjectionV1) -> bytes:
    members = (
        (f"{RESIDENT_SHEET_NAME}.csv", _csv_bytes(resident_table(projection))),
        (f"{GUARDIAN_SHEET_NAME}.csv", _csv_bytes(guardian_table(projection))),
    )
    output = BytesIO()
    with ZipFile(output, mode="w") as archive:
        for name, content in members:
            archive.writestr(_zip_member(name, content), content)
    return output.getvalue()
