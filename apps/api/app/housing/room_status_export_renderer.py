from __future__ import annotations

import csv
from collections.abc import Sequence
from datetime import datetime
from io import BytesIO, StringIO
from typing import Literal
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.housing.occupancy_export_service import _safe_cell, business_date
from app.housing.room_status_export_service import RoomStatusExportRow

_HEADERS = (
    "楼栋",
    "楼层",
    "房号",
    "房型",
    "床位数",
    "当前入住人数",
    "入住状态",
    "核对状态",
    "服务状态",
    "是否可入住",
    "每日保洁",
    "保洁状态",
    "入住人",
    "性别",
    "证件号",
    "手机号",
    "实际入住日期",
    "计划离开日期",
    "人员类型",
    "账号状态",
    "档案来源",
)
_LABELS = {
    "single": "单人间",
    "double": "双人间",
    "triple": "三人间",
    "custom": "自定义",
    "functional": "功能房",
    "occupied": "已入住",
    "unoccupied": "未入住",
    "verified": "已核验",
    "pending_verification": "待核验",
    "available": "可用",
    "problem": "异常",
    "maintenance": "维修中",
    "disabled": "停用",
    "ready": "保洁就绪",
    "pending": "待保洁",
    "male": "男",
    "female": "女",
    "student": "学员",
    "player": "队员",
    "student_player": "学员兼队员",
    "coach": "教练",
    "life_teacher": "生活老师",
    "customer_service": "客服",
    "administration": "行政",
    "visitor": "临时访客",
    "other": "其他工作人员",
    "not_activated": "未激活",
    "active": "已激活",
    "deleted": "已删除",
    "approved_profile": "审核建档",
    "legacy": "原有档案",
}


def _label(value: str | None) -> str:
    return _LABELS.get(value or "", value or "")


def _values(row: RoomStatusExportRow) -> tuple[str, ...]:
    return tuple(
        _safe_cell(value)
        for value in (
            row.building_name,
            row.floor_name,
            row.room_number,
            _label(row.room_type),
            row.capacity,
            row.current_occupants,
            _label(row.occupancy_status),
            _label(row.verification_status),
            _label(row.service_status),
            "是" if row.is_assignable else "否",
            "是" if row.daily_cleaning_enabled else "否",
            _label(row.cleaning_status),
            row.person_name,
            _label(row.sex),
            row.identity_number,
            row.phone,
            business_date(row.actual_check_in_at),
            business_date(row.planned_check_out_at),
            _label(row.person_type),
            "已停用" if row.account_status == "disabled" else _label(row.account_status),
            _label(row.profile_source),
        )
    )


def render_room_status_export(
    rows: Sequence[RoomStatusExportRow],
    *,
    format: Literal["xlsx", "csv"],
    occurred_at: datetime,
) -> tuple[bytes, str, str]:
    filename = "housing-room-status-" + occurred_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime(
        "%Y%m%d"
    )
    if format == "csv":
        stream = StringIO(newline="")
        writer = csv.writer(stream, dialect="excel", lineterminator="\r\n")
        writer.writerow(_HEADERS)
        writer.writerows(_values(row) for row in rows)
        return stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", filename + ".csv"
    workbook = Workbook()
    sheet = workbook.active
    assert isinstance(sheet, Worksheet)
    sheet.title = "当前房态"
    sheet.append(_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append(_values(row))
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:U{len(rows) + 1}"
    for index in range(1, 22):
        sheet.column_dimensions[get_column_letter(index)].width = 20 if index == 15 else 16
    output = BytesIO()
    workbook.save(output)
    return (
        output.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename + ".xlsx",
    )
