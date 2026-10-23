from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO, StringIO
from typing import TYPE_CHECKING
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet._writer import WorksheetWriter
from openpyxl.worksheet.worksheet import Worksheet
from openpyxl.writer.excel import ExcelWriter

from app.food_preferences.reporting import (
    FoodPreferenceRankRow,
    FoodPreferenceReportSnapshot,
)

if TYPE_CHECKING:
    from _typeshed import SizedBuffer

PEOPLE_SHEET_NAME = "人员偏好明细"
CUISINE_SHEET_NAME = "菜系统计"
DISH_SHEET_NAME = "菜品统计"

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_FORBIDDEN_XML_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]")
_FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_PERSON_TYPE_LABELS = {
    "student": "学员",
    "guardian": "监护人",
    "staff": "员工",
    "visitor": "访客",
}
_PERSON_STATUS_LABELS = {
    "active": "有效",
    "draft": "草稿",
    "inactive": "停用",
    "deleted": "已删除",
}

type SpreadsheetCell = str | int | float


@dataclass(frozen=True, slots=True)
class SpreadsheetTable:
    headers: tuple[str, ...]
    rows: tuple[tuple[SpreadsheetCell, ...], ...]


def safe_spreadsheet_text(value: object) -> str:
    text = "" if value is None else str(value)
    text = _FORBIDDEN_XML_CONTROLS.sub("\ufffd", text)
    dangerous = text.startswith(_DANGEROUS_PREFIXES)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return f"'{text}" if dangerous else text


def _timestamp(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(_SHANGHAI).isoformat(sep=" ", timespec="seconds")


def _three(values: tuple[str, ...]) -> tuple[str, str, str]:
    padded = (*values[:3], "", "", "")
    return tuple(safe_spreadsheet_text(value) for value in padded[:3])  # type: ignore[return-value]


def person_detail_table(snapshot: FoodPreferenceReportSnapshot) -> SpreadsheetTable:
    rows: list[tuple[SpreadsheetCell, ...]] = []
    for person in snapshot.people:
        cuisines = _three(person.cuisines)
        dishes = _three(person.dishes)
        rows.append(
            (
                safe_spreadsheet_text(person.full_name),
                safe_spreadsheet_text(
                    "、".join(_PERSON_TYPE_LABELS[item] for item in person.person_types)
                ),
                _PERSON_STATUS_LABELS[person.status],
                "已填写" if person.cuisines or person.dishes else "未填写",
                *cuisines,
                *dishes,
                safe_spreadsheet_text(_timestamp(person.updated_at)),
            )
        )
    return SpreadsheetTable(
        headers=(
            "姓名",
            "人员类型",
            "人员状态",
            "填写状态",
            "菜系1",
            "菜系2",
            "菜系3",
            "菜品1",
            "菜品2",
            "菜品3",
            "最后更新时间",
        ),
        rows=tuple(rows),
    )


def _statistics_table(
    label_header: str,
    ranks: tuple[FoodPreferenceRankRow, ...],
) -> SpreadsheetTable:
    return SpreadsheetTable(
        headers=(label_header, "选择人数", "占目标人数比例"),
        rows=tuple(
            (safe_spreadsheet_text(row.label), row.count, row.ratio) for row in ranks
        ),
    )


def cuisine_statistics_table(
    snapshot: FoodPreferenceReportSnapshot,
) -> SpreadsheetTable:
    return _statistics_table("菜系", snapshot.cuisines)


def dish_statistics_table(snapshot: FoodPreferenceReportSnapshot) -> SpreadsheetTable:
    return _statistics_table("菜品", snapshot.dishes)


def _write_sheet(sheet: Worksheet, table: SpreadsheetTable) -> None:
    sheet.append(table.headers)
    for row in table.rows:
        sheet.append(row)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = (
        f"A1:{get_column_letter(len(table.headers))}{len(table.rows) + 1}"
    )
    sheet.sheet_view.showGridLines = False
    header_fill = PatternFill(fill_type="solid", fgColor="1F4E78")
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
    for column_index, header in enumerate(table.headers, start=1):
        values = [header, *(row[column_index - 1] for row in table.rows)]
        visible_width = max(
            len(part)
            for value in values
            for part in (str(value).splitlines() or [""])
        )
        sheet.column_dimensions[get_column_letter(column_index)].width = min(
            max(visible_width + 2, 10),
            32,
        )
        if header == "占目标人数比例":
            for row_index in range(2, len(table.rows) + 2):
                sheet.cell(row=row_index, column=column_index).number_format = "0.0%"


class _InMemoryExcelWriter(ExcelWriter):
    """Keep worksheet XML in memory instead of openpyxl temporary files."""

    _archive: ZipFile

    def write_worksheet(self, ws: Worksheet) -> None:
        writer = WorksheetWriter(ws, out=BytesIO())
        writer.write()
        ws._rels = writer._rels  # type: ignore[attr-defined]
        self._archive.writestr(ws.path[1:], writer.read())
        self.manifest.append(ws)


class _DeterministicZipFile(ZipFile):
    """Keep openpyxl's in-memory OOXML members reproducible."""

    def writestr(
        self,
        zinfo_or_arcname: str | ZipInfo,
        data: str | SizedBuffer,
        compress_type: int | None = None,
        compresslevel: int | None = None,
    ) -> None:
        if isinstance(zinfo_or_arcname, ZipInfo):
            info = zinfo_or_arcname
        else:
            info = ZipInfo(zinfo_or_arcname)
            if compress_type is None:
                compress_type = self.compression
            if compresslevel is None:
                compresslevel = self.compresslevel
        info.date_time = _FIXED_ZIP_TIMESTAMP
        info.create_system = 3
        if info.filename.endswith("/"):
            info.external_attr = (0o40775 << 16) | 0x10
        else:
            info.external_attr = 0o600 << 16
        super().writestr(info, data, compress_type, compresslevel)


def _render_workbook(workbook: Workbook) -> bytes:
    output = BytesIO()
    with _DeterministicZipFile(
        output,
        mode="w",
        compression=ZIP_DEFLATED,
        allowZip64=True,
    ) as archive:
        _InMemoryExcelWriter(workbook, archive).save()
    return output.getvalue()


def render_xlsx(snapshot: FoodPreferenceReportSnapshot) -> bytes:
    workbook = Workbook()
    default_sheet = workbook.active
    assert isinstance(default_sheet, Worksheet)
    workbook.remove(default_sheet)
    workbook.properties.creator = ""
    workbook.properties.lastModifiedBy = ""
    workbook.properties.title = "餐饮偏好统计"
    workbook.properties.subject = ""
    workbook.properties.description = ""
    fixed_time = datetime(1980, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    workbook.properties.created = fixed_time
    workbook.properties.modified = fixed_time
    _write_sheet(
        workbook.create_sheet(PEOPLE_SHEET_NAME),
        person_detail_table(snapshot),
    )
    _write_sheet(
        workbook.create_sheet(CUISINE_SHEET_NAME),
        cuisine_statistics_table(snapshot),
    )
    _write_sheet(
        workbook.create_sheet(DISH_SHEET_NAME),
        dish_statistics_table(snapshot),
    )
    return _render_workbook(workbook)


def _csv_bytes(table: SpreadsheetTable) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output, dialect="excel", lineterminator="\r\n")
    writer.writerow(table.headers)
    writer.writerows(table.rows)
    return output.getvalue().encode("utf-8-sig")


def _zip_member(name: str, content: bytes) -> ZipInfo:
    info = ZipInfo(name, date_time=_FIXED_ZIP_TIMESTAMP)
    info.create_system = 3
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    info.file_size = len(content)
    return info


def render_csv_zip(snapshot: FoodPreferenceReportSnapshot) -> bytes:
    members = (
        (f"{PEOPLE_SHEET_NAME}.csv", _csv_bytes(person_detail_table(snapshot))),
        (
            f"{CUISINE_SHEET_NAME}.csv",
            _csv_bytes(cuisine_statistics_table(snapshot)),
        ),
        (f"{DISH_SHEET_NAME}.csv", _csv_bytes(dish_statistics_table(snapshot))),
    )
    output = BytesIO()
    with ZipFile(output, mode="w") as archive:
        for name, content in members:
            archive.writestr(_zip_member(name, content), content)
    return output.getvalue()
