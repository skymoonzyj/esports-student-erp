from __future__ import annotations

import csv
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO, StringIO
from typing import Literal, cast
from xml.etree import ElementTree
from zipfile import ZipFile

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.properties import PageSetupProperties
from openpyxl.worksheet.worksheet import Worksheet

from app.procurement.models import DailyPurchaseRecord
from app.procurement.schemas import ProcurementExportScope

ProcurementExportFormat = Literal["xlsx", "csv"]

_HEADERS = (
    "采购日期",
    "物品名称",
    "采购渠道",
    "商品链接",
    "数量",
    "单价（元）",
    "总价（元）",
    "结算中心",
    "是否使用备用金",
    "是否已报销",
    "结算状态",
    "结算日期",
    "是否已开票",
    "发票金额（元）",
    "发票日期",
    "发票号码",
    "发票文件",
    "备注",
)


def _safe_cell(value: object) -> str:
    text = "" if value is None else str(value)
    if text[:1] in {"=", "+", "-", "@"}:
        return "'" + text
    return text


def _yuan(cents: int) -> str:
    return f"{cents / 100:.2f}"


def _quantity(record: DailyPurchaseRecord) -> str:
    return format(record.quantity.normalize(), "f")


def _row(record: DailyPurchaseRecord) -> tuple[str, ...]:
    invoice = record.invoice_link.invoice if record.invoice_link is not None else None
    return (
        record.purchase_date.isoformat(),
        record.item_name,
        "线上" if record.purchase_channel == "online" else "线下",
        record.product_url or "",
        _quantity(record),
        _yuan(record.unit_price_cents),
        _yuan(record.total_price_cents),
        record.settlement_center.name,
        "是" if record.uses_reserve_fund else "否",
        "是" if record.is_reimbursed else "否",
        "已结算" if record.is_settled else "未结算",
        record.settled_on.isoformat() if record.settled_on else "",
        "是" if invoice is not None else "否",
        _yuan(invoice.amount_cents) if invoice is not None else "",
        invoice.invoice_date.isoformat() if invoice is not None else "",
        invoice.invoice_number or "" if invoice is not None else "",
        invoice.original_filename if invoice is not None else "",
        record.note or "",
    )


def _expense_period(records: list[DailyPurchaseRecord]) -> str:
    if not records:
        return "-"
    first = min(record.purchase_date for record in records)
    last = max(record.purchase_date for record in records)
    if first.year == last.year and first.month == last.month:
        return first.strftime("%Y.%m")
    return f"{first:%Y.%m.%d} - {last:%Y.%m.%d}"


def _settlement_center_summary(records: list[DailyPurchaseRecord]) -> str:
    names = sorted({record.settlement_center.name for record in records})
    if not names:
        return "-"
    if len(names) == 1:
        return names[0]
    return f"{len(names)} 个结算中心"


def _reimbursement_description(record: DailyPurchaseRecord) -> str:
    return _safe_cell(
        f"{record.item_name}（{_quantity(record)} × ¥{_yuan(record.unit_price_cents)}）"
    )


def _reimbursement_note(record: DailyPurchaseRecord) -> str:
    invoice = record.invoice_link.invoice if record.invoice_link is not None else None
    if invoice is None:
        invoice_text = "未上传发票"
    else:
        invoice_text = f"发票：{invoice.invoice_number or invoice.original_filename}"
    parts = [invoice_text]
    if record.note:
        parts.append(record.note)
    return _safe_cell("；".join(parts))


def _cache_reimbursement_totals(content: bytes, totals: dict[str, int]) -> bytes:
    """Keep formulas editable while making their initial totals readable by previews."""
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    ElementTree.register_namespace("", namespace)
    output = BytesIO()
    with ZipFile(BytesIO(content)) as source, ZipFile(output, "w") as destination:
        for member in source.infolist():
            payload = source.read(member.filename)
            if member.filename == "xl/worksheets/sheet1.xml":
                root = ElementTree.fromstring(payload)
                for reference, cents in totals.items():
                    cell = root.find(f".//{{{namespace}}}c[@r='{reference}']")
                    if cell is None or cell.find(f"{{{namespace}}}f") is None:
                        raise RuntimeError("generated reimbursement total formula is missing")
                    value = cell.find(f"{{{namespace}}}v")
                    if value is None:
                        value = ElementTree.SubElement(cell, f"{{{namespace}}}v")
                    value.text = format(Decimal(cents) / 100, ".2f")
                payload = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
            destination.writestr(member, payload)
    return output.getvalue()


def _render_reimbursement_export(
    records: list[DailyPurchaseRecord],
    *,
    applicant_name: str,
    occurred_at: datetime,
) -> tuple[bytes, str, str, str]:
    workbook = Workbook()
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    workbook.calculation.calcMode = "auto"
    sheet = cast(Worksheet, workbook.active)
    sheet.title = "待报销汇总"
    sheet.sheet_view.showGridLines = False

    sheet.merge_cells("A1:I1")
    sheet["A1"] = "日常采购报销单（汇总）"
    sheet.merge_cells("H2:I2")
    sheet.merge_cells("B3:I3")
    sheet.merge_cells("B4:I4")

    sheet["A2"] = "申请人"
    sheet["B2"] = _safe_cell(applicant_name)
    sheet["C2"] = "结算中心"
    sheet["D2"] = _safe_cell(_settlement_center_summary(records))
    sheet["E2"] = "费用所属期"
    sheet["F2"] = _expense_period(records)
    sheet["G2"] = "报销金额"
    sheet["A3"] = "本次报销内容"
    sheet["B3"] = f"日常采购待报销（{len(records)} 项）"
    sheet["A4"] = "报销费用"
    sheet["B4"] = "报销明细"

    headers = (
        "费用分类",
        "费用发生日期",
        "费用类型",
        "费用内容/事由",
        "",
        "金额（元）",
        "结算中心",
        "资金来源",
        "备注/发票",
    )
    for column, value in enumerate(headers, start=1):
        sheet.cell(row=5, column=column, value=value)
    sheet.merge_cells("D5:E5")

    detail_start = 6
    if records:
        detail_end = detail_start + len(records) - 1
        if detail_end > detail_start:
            sheet.merge_cells(
                start_row=detail_start, start_column=1, end_row=detail_end, end_column=1
            )
        sheet.cell(row=detail_start, column=1, value="其他开支")
        for row_index, record in enumerate(records, start=detail_start):
            sheet.merge_cells(
                start_row=row_index,
                start_column=4,
                end_row=row_index,
                end_column=5,
            )
            sheet.cell(row=row_index, column=2, value=record.purchase_date)
            sheet.cell(
                row=row_index,
                column=3,
                value="线上采购" if record.purchase_channel == "online" else "线下采购",
            )
            sheet.cell(row=row_index, column=4, value=_reimbursement_description(record))
            sheet.cell(row=row_index, column=6, value=Decimal(record.total_price_cents) / 100)
            sheet.cell(
                row=row_index,
                column=7,
                value=_safe_cell(record.settlement_center.name),
            )
            sheet.cell(
                row=row_index,
                column=8,
                value="备用金" if record.uses_reserve_fund else "其他资金",
            )
            sheet.cell(row=row_index, column=9, value=_reimbursement_note(record))
    else:
        detail_end = detail_start
        sheet.cell(row=detail_start, column=1, value="其他开支")
        sheet.merge_cells(
            start_row=detail_start, start_column=2, end_row=detail_start, end_column=9
        )
        sheet.cell(row=detail_start, column=2, value="当前范围内没有待报销记录")

    subtotal_row = detail_end + 1
    total_row = subtotal_row + 1
    sheet.merge_cells(start_row=subtotal_row, start_column=2, end_row=subtotal_row, end_column=5)
    sheet.cell(row=subtotal_row, column=2, value="其他开支小计")
    sheet.cell(row=subtotal_row, column=6, value=f"=SUM(F{detail_start}:F{detail_end})")
    sheet.merge_cells(start_row=subtotal_row, start_column=7, end_row=subtotal_row, end_column=9)
    sheet.cell(row=subtotal_row, column=7, value=f"共 {len(records)} 项")
    sheet.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=5)
    sheet.cell(row=total_row, column=1, value="本次报销合计")
    sheet.cell(row=total_row, column=6, value=f"=F{subtotal_row}")
    sheet.merge_cells(start_row=total_row, start_column=7, end_row=total_row, end_column=9)
    sheet.cell(row=total_row, column=7, value="待报销汇总")
    sheet["H2"] = f"=F{total_row}"

    title_fill = PatternFill(fill_type="solid", fgColor="DDEBF7")
    subtotal_fill = PatternFill(fill_type="solid", fgColor="FFF2CC")
    border = Border(
        left=Side(style="thin", color="5B6573"),
        right=Side(style="thin", color="5B6573"),
        top=Side(style="thin", color="5B6573"),
        bottom=Side(style="thin", color="5B6573"),
    )
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="center", wrap_text=True)

    sheet["A1"].font = Font(name="宋体", size=16, bold=True)
    sheet["A1"].alignment = center
    for cell in sheet[2]:
        cell.alignment = center
        cell.font = Font(name="宋体", size=11)
    for label in ("A2", "C2", "E2", "G2"):
        sheet[label].fill = title_fill
        sheet[label].font = Font(name="宋体", size=11, bold=True)
    sheet["A3"].fill = title_fill
    sheet["A3"].font = Font(name="宋体", size=11, bold=True)
    sheet["A3"].alignment = center
    sheet["B3"].alignment = left
    for cell in sheet[4]:
        cell.fill = title_fill
        cell.font = Font(name="宋体", size=13, bold=True)
        cell.alignment = center
    for cell in sheet[5]:
        cell.fill = title_fill
        cell.font = Font(name="宋体", size=10, bold=True)
        cell.alignment = center
    for row in sheet.iter_rows(min_row=detail_start, max_row=detail_end, min_col=1, max_col=9):
        for cell in row:
            cell.font = Font(name="宋体", size=10)
            cell.alignment = center
        row[3].alignment = left
        row[8].alignment = left
    for row_number in (subtotal_row, total_row):
        for cell in sheet[row_number]:
            cell.fill = subtotal_fill if row_number == subtotal_row else title_fill
            cell.font = Font(name="宋体", size=11, bold=True)
            cell.alignment = center
    for row in sheet.iter_rows(min_row=1, max_row=total_row, min_col=1, max_col=9):
        for cell in row:
            cell.border = border

    sheet["H2"].number_format = "#,##0.00"
    for row_number in range(detail_start, detail_end + 1):
        sheet.cell(row=row_number, column=2).number_format = "yyyy-mm-dd"
        sheet.cell(row=row_number, column=6).number_format = "#,##0.00"
    sheet.cell(row=subtotal_row, column=6).number_format = "#,##0.00"
    sheet.cell(row=total_row, column=6).number_format = "#,##0.00"

    widths = (11, 14, 12, 15, 9, 13, 19, 12, 28)
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[chr(64 + index)].width = width
    sheet.row_dimensions[1].height = 28
    sheet.row_dimensions[2].height = 24
    sheet.row_dimensions[3].height = 42
    sheet.row_dimensions[4].height = 24
    sheet.row_dimensions[5].height = 30
    for row_number in range(detail_start, detail_end + 1):
        sheet.row_dimensions[row_number].height = 28

    sheet.freeze_panes = "B6"
    sheet.print_title_rows = "1:5"
    sheet.print_area = f"A1:I{total_row}"
    sheet.sheet_properties.pageSetUpPr = PageSetupProperties(
        fitToPage=True,
        autoPageBreaks=False,
    )
    sheet.page_setup.orientation = "portrait"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.page_margins.left = 0.35
    sheet.page_margins.right = 0.35
    sheet.page_margins.top = 0.5
    sheet.page_margins.bottom = 0.5

    xlsx_output = BytesIO()
    workbook.save(xlsx_output)
    workbook.close()
    total_cents = sum(record.total_price_cents for record in records)
    content = _cache_reimbursement_totals(
        xlsx_output.getvalue(),
        {f"F{subtotal_row}": total_cents, f"F{total_row}": total_cents, "H2": total_cents},
    )
    timestamp = occurred_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return (
        content,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        f"pending-reimbursements-{timestamp}.xlsx",
        "xlsx",
    )


def render_procurement_export(
    records: list[DailyPurchaseRecord],
    *,
    format: ProcurementExportFormat,
    occurred_at: datetime,
    scope: ProcurementExportScope = "current",
    applicant_name: str = "",
) -> tuple[bytes, str, str, str]:
    if scope == "unreimbursed":
        if format != "xlsx":
            raise ValueError("pending reimbursement exports require xlsx")
        return _render_reimbursement_export(
            records,
            applicant_name=applicant_name,
            occurred_at=occurred_at,
        )

    rows = [_row(record) for record in records]
    timestamp = occurred_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    if format == "csv":
        csv_output = StringIO(newline="")
        writer = csv.writer(csv_output)
        writer.writerow(_HEADERS)
        writer.writerows(tuple(_safe_cell(value) for value in row) for row in rows)
        return (
            csv_output.getvalue().encode("utf-8-sig"),
            "text/csv; charset=utf-8",
            f"daily-purchases-{timestamp}.csv",
            "csv",
        )

    workbook = Workbook()
    sheet = cast(Worksheet, workbook.active)
    sheet.title = "日常采购"
    sheet.append(list(_HEADERS))
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(fill_type="solid", fgColor="1F3A5F")
    for row in rows:
        sheet.append([_safe_cell(value) for value in row])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    widths = (14, 24, 12, 42, 12, 14, 14, 22, 14, 12, 14, 12, 16, 14, 20, 28, 36, 36)
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[chr(64 + index)].width = width
    xlsx_output = BytesIO()
    workbook.save(xlsx_output)
    return (
        xlsx_output.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        f"daily-purchases-{timestamp}.xlsx",
        "xlsx",
    )
