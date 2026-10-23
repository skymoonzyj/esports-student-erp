from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from io import BytesIO, StringIO
from typing import Literal, cast
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from app.asset_management.models import Asset, AssetAssignment

AssetExportFormat = Literal["xlsx", "csv"]

_HEADERS = (
    "资产代码",
    "资产名称",
    "分类",
    "品牌",
    "型号",
    "序列号",
    "状态",
    "存放位置",
    "采购日期",
    "保修到期日",
    "采购来源记录",
    "当前使用人",
    "领用日期",
    "预计归还日期",
    "辨识照片",
    "照片查看地址",
    "保修方式",
    "售后地点",
    "售后联系人",
    "售后电话",
    "产品链接",
    "订单号",
)
_STATUS_LABELS = {
    "available": "可用",
    "assigned": "使用中",
    "maintenance": "维修中",
    "retired": "已报废",
}
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _business_date(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(_SHANGHAI).date().isoformat()


def _date_only(value: date | None) -> str:
    return value.isoformat() if value is not None else ""


def _safe_cell(value: object) -> str:
    text = "" if value is None else str(value)
    if text[:1] in {"=", "+", "-", "@"}:
        return "'" + text
    return text


def _row_values(
    asset: Asset,
    assignment: AssetAssignment | None,
    person_name: str | None,
) -> tuple[str, ...]:
    has_photo = asset.photo is not None
    return (
        asset.asset_code,
        asset.name,
        asset.category or "",
        asset.brand or "",
        asset.model or "",
        asset.serial_number or "",
        _STATUS_LABELS.get(asset.status, asset.status),
        asset.location or "",
        _date_only(asset.purchase_date),
        _date_only(asset.warranty_expires_on),
        str(asset.source_purchase_id or ""),
        person_name or "未分配",
        _business_date(assignment.assigned_at if assignment else None),
        _business_date(assignment.expected_return_at if assignment else None),
        "已上传" if has_photo else "未上传",
        f"/api/v1/assets/{asset.id}/photo" if has_photo else "",
        {"online": "线上", "offline": "线下"}.get(asset.warranty_channel or "", ""),
        asset.warranty_service_location or "",
        asset.warranty_contact_name or "",
        asset.warranty_contact_phone or "",
        asset.warranty_product_url or "",
        asset.warranty_order_number or "",
    )


def render_asset_export(
    rows: list[tuple[Asset, AssetAssignment | None, str | None]],
    *,
    format: AssetExportFormat,
    occurred_at: datetime,
) -> tuple[bytes, str, str, str]:
    values = [
        _row_values(asset, assignment, person_name) for asset, assignment, person_name in rows
    ]
    timestamp = occurred_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    if format == "csv":
        csv_output = StringIO(newline="")
        writer = csv.writer(csv_output)
        writer.writerow(_HEADERS)
        writer.writerows([tuple(_safe_cell(value) for value in row) for row in values])
        content = csv_output.getvalue().encode("utf-8-sig")
        return content, "text/csv; charset=utf-8", f"assets-{timestamp}.csv", "csv"

    workbook = Workbook()
    sheet = cast(Worksheet, workbook.active)
    sheet.title = "资产台账"
    sheet.append(list(_HEADERS))
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(fill_type="solid", fgColor="1F3A5F")
    for row in values:
        sheet.append([_safe_cell(value) for value in row])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    widths = (
        20,
        24,
        14,
        16,
        18,
        22,
        12,
        22,
        14,
        14,
        38,
        18,
        14,
        16,
        12,
        42,
        12,
        28,
        18,
        22,
        42,
        26,
    )
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[chr(64 + index)].width = width
    xlsx_output = BytesIO()
    workbook.save(xlsx_output)
    return (
        xlsx_output.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        f"assets-{timestamp}.xlsx",
        "xlsx",
    )
