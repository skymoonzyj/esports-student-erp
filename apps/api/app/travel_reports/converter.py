from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
from pathlib import PurePath
from typing import cast
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile, is_zipfile

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE, Cell, MergedCell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.datetime import from_excel
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.properties import PageSetupProperties
from openpyxl.worksheet.worksheet import Worksheet

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_ZIP_ENTRIES = 1_000
MAX_BUSINESS_ROWS = 50_000
MAX_SOURCE_COLUMNS = 128
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_REQUIRED_SHEETS = ("账单汇总表", "汇总明细", "国内机票", "国际机票", "火车")
_UNSUPPORTED_SHEETS = (
    "国内酒店",
    "国际及港澳台酒店",
    "用车",
    "保险",
    "非标类",
    "积分",
    "调账",
)
_PERIOD_PATTERN = re.compile(
    r"^\s*(\d{4})[./-](\d{1,2})[./-](\d{1,2})\s*[-~至]\s*"
    r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})\s*$"
)
_MONEY_QUANTUM = Decimal("0.01")
_MAX_ABS_AMOUNT = Decimal(9000000000000)
_DANGEROUS_PREFIXES = ("=", "+", "-", "@")
_SPREADSHEET_NAMESPACE = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_OUTPUT_SHEET_PATH = "xl/worksheets/sheet1.xml"
type ExcelValue = str | Decimal | datetime | date | None

_DOMESTIC_HEADERS = (
    "乘机人",
    "部门",
    "航线",
    "航班",
    "起飞时间",
    "标准票面价",
    "优惠金额(大客户/政采)",
    "行程单票面价",
    "燃油费",
    "机建费",
    "改签费",
    "退票费",
    "代订机票费",
    "实收/实付",
    "审批关联单",
)
_INTERNATIONAL_HEADERS = (
    "乘机人",
    "部门",
    "航线",
    "航班",
    "起飞时间",
    "标准票面价",
    "税费",
    "升舱费",
    "改签费",
    "退票费",
    "服务费",
    "实收/实付",
    "审批关联单",
)
_TRAIN_HEADERS = (
    "乘车人",
    "部门",
    "出发时间",
    "出发站点",
    "到达站点",
    "车次",
    "票面价",
    "改签费",
    "退票费",
    "出票服务费",
    "代打费",
    "实收/实付",
    "审批关联单",
)


class TravelReportWorkbookError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _SheetData:
    headers: dict[str, int]
    rows: tuple[tuple[int, tuple[object, ...]], ...]


@dataclass(frozen=True)
class ConvertedTravelReport:
    original_filename: str
    converted_filename: str
    source_sha256: str
    converted_sha256: str
    source_byte_size: int
    converted_byte_size: int
    converted_content: bytes
    company_name: str
    cost_center: str
    billing_period_start: date
    billing_period_end: date
    domestic_air_count: int
    international_air_count: int
    train_count: int
    source_total_amount: Decimal
    converted_total_amount: Decimal
    warnings: tuple[str, ...]

    @property
    def item_count(self) -> int:
        return self.domestic_air_count + self.international_air_count + self.train_count


def _filename(value: str | None) -> str:
    if not value:
        raise TravelReportWorkbookError("filename", "请选择需要导入的 Excel 文件")
    name = PurePath(value.replace("\\", "/")).name.strip()
    if not name or len(name) > 255 or not name.lower().endswith(".xlsx"):
        raise TravelReportWorkbookError("filename", "仅支持扩展名为 .xlsx 的账单文件")
    return name


def _validate_container(content: bytes) -> None:
    if not content:
        raise TravelReportWorkbookError("empty", "账单文件为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise TravelReportWorkbookError("size", "账单文件不能超过 20 MB")
    if not is_zipfile(BytesIO(content)):
        raise TravelReportWorkbookError("format", "文件不是有效的 XLSX 工作簿")
    try:
        with ZipFile(BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > MAX_ZIP_ENTRIES:
                raise TravelReportWorkbookError("archive", "工作簿内部文件数量异常")
            names: set[str] = set()
            uncompressed = 0
            for member in members:
                normalized = member.filename.replace("\\", "/")
                if normalized.startswith("/") or ".." in normalized.split("/"):
                    raise TravelReportWorkbookError("archive", "工作簿包含不安全的文件路径")
                if normalized in names:
                    raise TravelReportWorkbookError("archive", "工作簿包含重复的内部文件")
                names.add(normalized)
                uncompressed += member.file_size
                if uncompressed > MAX_UNCOMPRESSED_BYTES:
                    raise TravelReportWorkbookError("archive", "工作簿解压后的数据量过大")
                if member.compress_size and member.file_size / member.compress_size > 300:
                    raise TravelReportWorkbookError("archive", "工作簿压缩比例异常")
            if "xl/workbook.xml" not in names or "[Content_Types].xml" not in names:
                raise TravelReportWorkbookError("format", "文件缺少 XLSX 工作簿结构")
            if any(name.lower().endswith("vbaproject.bin") for name in names):
                raise TravelReportWorkbookError("format", "不支持包含宏的工作簿")
    except BadZipFile as exc:
        raise TravelReportWorkbookError("format", "文件不是有效的 XLSX 工作簿") from exc


def _header_text(value: object) -> str:
    return re.sub(r"\s+", "", "" if value is None else str(value).strip())


def _validate_sheet_row_range(worksheet: object, sheet_name: str) -> None:
    """Inspect physical XML rows so blank gaps cannot hide data past our read limit.

    Iterating worksheet values without a bound would synthesize every missing
    row in a sparse sheet. XML iteration stays bounded by the validated archive
    size, and deliberately ignores the workbook's untrusted dimension metadata.
    """
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    row_tag = f"{{{namespace}}}row"
    cell_tag = f"{{{namespace}}}c"
    formula_tag = f"{{{namespace}}}f"
    value_tags = {f"{{{namespace}}}v", f"{{{namespace}}}is"}
    previous_row = 0
    try:
        with worksheet._get_source() as source:  # type: ignore[attr-defined]
            for _, element in ElementTree.iterparse(source, events=("end",)):
                if element.tag != row_tag:
                    continue
                row_number = int(element.get("r", previous_row + 1))
                previous_row = row_number
                if row_number > MAX_BUSINESS_ROWS + 1:
                    has_data = any(
                        part.tag == formula_tag
                        or (part.tag in value_tags and any(text.strip() for text in part.itertext()))
                        for cell in element if cell.tag == cell_tag
                        for part in cell
                    )
                    if has_data:
                        raise TravelReportWorkbookError(
                            "rows",
                            f"工作表“{sheet_name}”第 {row_number} 行超出支持的前 "
                            f"{MAX_BUSINESS_ROWS} 行明细范围，请移除空行或拆分账单后导入",
                        )
                element.clear()
    except (ElementTree.ParseError, ValueError) as exc:
        if isinstance(exc, TravelReportWorkbookError):
            raise
        raise TravelReportWorkbookError("workbook", f"工作表“{sheet_name}”内容无法读取") from exc


def _sheet_data(workbook: object, sheet_name: str) -> _SheetData:
    worksheet = workbook[sheet_name]  # type: ignore[index]
    _validate_sheet_row_range(worksheet, sheet_name)
    worksheet.reset_dimensions()
    iterator = worksheet.iter_rows(
        min_row=1,
        max_row=MAX_BUSINESS_ROWS + 2,
        max_col=MAX_SOURCE_COLUMNS,
        values_only=True,
    )
    try:
        header_row = tuple(next(iterator))
    except StopIteration as exc:
        raise TravelReportWorkbookError("headers", f"工作表“{sheet_name}”为空") from exc
    headers: dict[str, int] = {}
    for index, value in enumerate(header_row):
        normalized = _header_text(value)
        if normalized and normalized not in headers:
            headers[normalized] = index
    rows: list[tuple[int, tuple[object, ...]]] = []
    for excel_row, row in enumerate(iterator, start=2):
        values = tuple(cell for cell in row)
        if excel_row == MAX_BUSINESS_ROWS + 2 and any(
            value not in (None, "") for value in values
        ):
            raise TravelReportWorkbookError(
                "rows", f"工作表“{sheet_name}”超过 {MAX_BUSINESS_ROWS} 条明细"
            )
        if not any(value not in (None, "") for value in values):
            continue
        first = _header_text(values[0] if values else None)
        if not first or first in {"合计", "总计"}:
            continue
        rows.append((excel_row, values))
    return _SheetData(headers=headers, rows=tuple(rows))


def _require_headers(sheet_name: str, data: _SheetData, required: tuple[str, ...]) -> None:
    missing = [header for header in required if _header_text(header) not in data.headers]
    if missing:
        raise TravelReportWorkbookError(
            "headers",
            f"工作表“{sheet_name}”缺少字段：{'、'.join(missing)}",
        )


def _value(data: _SheetData, row: tuple[object, ...], header: str) -> object:
    index = data.headers.get(_header_text(header))
    return row[index] if index is not None and index < len(row) else None


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        text = value.strftime("%Y-%m-%d %H:%M:%S")
    elif isinstance(value, date):
        text = value.isoformat()
    else:
        text = str(value).strip()
    text = ILLEGAL_CHARACTERS_RE.sub("", text)
    if len(text) > 32_767:
        raise TravelReportWorkbookError("cell", "账单中存在超过 Excel 限制的文本")
    if text.startswith(_DANGEROUS_PREFIXES):
        return "'" + text
    return text


def _plain_required(value: object, label: str) -> str:
    text = _cell_text(value)
    if not text:
        raise TravelReportWorkbookError("metadata", f"账单汇总表缺少{label}")
    return text


def _money(value: object, sheet_name: str, excel_row: int, header: str) -> Decimal:
    if value in (None, ""):
        return Decimal("0.00")
    if isinstance(value, bool):
        raise TravelReportWorkbookError(
            "amount", f"{sheet_name}第 {excel_row} 行“{header}”不是有效金额"
        )
    try:
        if isinstance(value, Decimal):
            amount = value
        elif isinstance(value, (int, float)):
            amount = Decimal(str(value))
        else:
            raw = str(value).strip().replace(",", "").replace("￥", "").replace("¥", "")
            amount = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise TravelReportWorkbookError(
            "amount", f"{sheet_name}第 {excel_row} 行“{header}”不是有效金额"
        ) from exc
    if not amount.is_finite() or abs(amount) > _MAX_ABS_AMOUNT:
        raise TravelReportWorkbookError(
            "amount", f"{sheet_name}第 {excel_row} 行“{header}”金额超出允许范围"
        )
    return amount.quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _date_time(value: object) -> ExcelValue:
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, (int, float)):
        try:
            converted = from_excel(value)
            return converted.replace(tzinfo=None) if isinstance(converted, datetime) else converted
        except (TypeError, ValueError, OverflowError):
            return _cell_text(value)
    text = str(value).strip()
    if "\n" in text or "," in text or "，" in text:
        return _cell_text(text)
    try:
        return datetime.fromisoformat(text.replace("/", "-")).replace(tzinfo=None)
    except ValueError:
        pass
    return _cell_text(text)


def _approval(data: _SheetData, row: tuple[object, ...]) -> str:
    return _cell_text(_value(data, row, "出差申请单号") or _value(data, row, "员工备注"))


def _metadata(summary: _SheetData) -> tuple[str, str, date, date, Decimal]:
    values: dict[str, object] = {}
    for _, row in summary.rows:
        if len(row) >= 2:
            label = _header_text(row[0])
            if label:
                values[label] = row[1]
    company = _plain_required(values.get("公司名称"), "公司名称")
    cost_center = _plain_required(values.get("成本中心名称"), "成本中心")
    period_text = _plain_required(values.get("账单周期"), "账单周期")
    match = _PERIOD_PATTERN.fullmatch(period_text.lstrip("'"))
    if not match:
        raise TravelReportWorkbookError("metadata", "账单周期格式无法识别")
    try:
        start = date(*map(int, match.groups()[:3]))
        end = date(*map(int, match.groups()[3:]))
    except ValueError as exc:
        raise TravelReportWorkbookError("metadata", "账单周期日期无效") from exc
    if end < start:
        raise TravelReportWorkbookError("metadata", "账单周期结束日期早于开始日期")
    total = _money(values.get("本期账单消费金额"), "账单汇总表", 1, "本期账单消费金额")
    return company, cost_center, start, end, total


def _domestic_rows(data: _SheetData) -> tuple[tuple[object, ...], ...]:
    required = (
        "乘机人", "费用承担部门", "航线", "航班号", "出发时间",
        "标准票面价", "优惠金额(大客户/政采)", "行程单票面价", "燃油费",
        "机建费", "改签费", "退票费", "代订机票费", "企业应付金额",
    )
    _require_headers("国内机票", data, required)
    result: list[tuple[object, ...]] = []
    for excel_row, row in data.rows:
        result.append((
            _cell_text(_value(data, row, "乘机人")),
            _cell_text(_value(data, row, "费用承担部门")),
            _cell_text(_value(data, row, "航线")),
            _cell_text(_value(data, row, "航班号")),
            _date_time(_value(data, row, "出发时间")),
            *(
                _money(_value(data, row, header), "国内机票", excel_row, header)
                for header in (
                    "标准票面价", "优惠金额(大客户/政采)", "行程单票面价",
                    "燃油费", "机建费", "改签费", "退票费", "代订机票费",
                    "企业应付金额",
                )
            ),
            _approval(data, row),
        ))
    return tuple(result)


def _international_rows(data: _SheetData) -> tuple[tuple[object, ...], ...]:
    required = (
        "费用承担部门", "航线", "航班号", "出发时间", "标准票面价", "税费",
        "升舱费", "改签费", "退票费", "服务费", "企业应付金额",
    )
    _require_headers("国际机票", data, required)
    if "乘机人" not in data.headers and "乘机人中文名" not in data.headers:
        raise TravelReportWorkbookError("headers", "工作表“国际机票”缺少乘机人字段")
    result: list[tuple[object, ...]] = []
    for excel_row, row in data.rows:
        passenger = _value(data, row, "乘机人") or _value(data, row, "乘机人中文名")
        result.append((
            _cell_text(passenger),
            _cell_text(_value(data, row, "费用承担部门")),
            _cell_text(_value(data, row, "航线")),
            _cell_text(_value(data, row, "航班号")),
            _date_time(_value(data, row, "出发时间")),
            *(
                _money(_value(data, row, header), "国际机票", excel_row, header)
                for header in (
                    "标准票面价", "税费", "升舱费", "改签费", "退票费",
                    "服务费", "企业应付金额",
                )
            ),
            _approval(data, row),
        ))
    return tuple(result)


def _train_rows(data: _SheetData) -> tuple[tuple[object, ...], ...]:
    required = (
        "乘客", "费用承担部门", "出发时间", "出发站点", "到达站点", "车次",
        "票面价", "改签费", "退票费", "出票服务费", "代打费", "企业应付金额",
    )
    _require_headers("火车", data, required)
    result: list[tuple[object, ...]] = []
    for excel_row, row in data.rows:
        result.append((
            _cell_text(_value(data, row, "乘客")),
            _cell_text(_value(data, row, "费用承担部门")),
            _date_time(_value(data, row, "出发时间")),
            _cell_text(_value(data, row, "出发站点")),
            _cell_text(_value(data, row, "到达站点")),
            _cell_text(_value(data, row, "车次")),
            *(
                _money(_value(data, row, header), "火车", excel_row, header)
                for header in (
                    "票面价", "改签费", "退票费", "出票服务费", "代打费",
                    "企业应付金额",
                )
            ),
            _approval(data, row),
        ))
    return tuple(result)


_THIN_BORDER = Border(
    left=Side(style="thin", color="FFB7B7B7"),
    right=Side(style="thin", color="FFB7B7B7"),
    top=Side(style="thin", color="FFB7B7B7"),
    bottom=Side(style="thin", color="FFB7B7B7"),
)
_TITLE_FONT = Font(name="Microsoft YaHei", size=12, bold=True, color="FFC00000")
_HEADER_FONT = Font(name="Microsoft YaHei", size=11, bold=True, color="FF1F2937")
_BODY_FONT = Font(name="Microsoft YaHei", size=10, color="FF111827")
_TOTAL_FONT = Font(name="Microsoft YaHei", size=10, bold=True, color="FF111827")
_HEADER_FILL = PatternFill("solid", fgColor="FFF2F4F7")
_TOTAL_FILL = PatternFill("solid", fgColor="FFFFF2CC")
_MONEY_FORMAT = '#,##0.00;[Red]-#,##0.00;-'


def _render_section(
    worksheet: Worksheet,
    *,
    start_row: int,
    start_column: int,
    title: str,
    headers: tuple[str, ...],
    rows: tuple[tuple[object, ...], ...],
    label_span: int,
    money_indexes: tuple[int, ...],
    formula_results: dict[str, Decimal],
) -> int:
    end_column = start_column + len(headers) - 1
    worksheet.merge_cells(
        start_row=start_row,
        start_column=start_column,
        end_row=start_row,
        end_column=end_column,
    )
    title_cell = worksheet.cell(start_row, start_column, title)
    title_cell.font = _TITLE_FONT
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    worksheet.row_dimensions[start_row].height = 24

    header_row = start_row + 1
    for offset, header in enumerate(headers):
        cell = worksheet.cell(header_row, start_column + offset, header)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.border = _THIN_BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    worksheet.row_dimensions[header_row].height = 34

    data_start = start_row + 2
    if rows:
        for row_offset, values in enumerate(rows):
            excel_row = data_start + row_offset
            for column_offset, value in enumerate(values):
                cell = worksheet.cell(
                    excel_row,
                    start_column + column_offset,
                    cast(ExcelValue, value),
                )
                cell.font = _BODY_FONT
                cell.border = _THIN_BORDER
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                if column_offset in money_indexes:
                    cell.number_format = _MONEY_FORMAT
                elif isinstance(value, datetime):
                    cell.number_format = "yyyy-mm-dd hh:mm"
            worksheet.row_dimensions[excel_row].height = 24
        data_end = data_start + len(rows) - 1
    else:
        data_end = data_start
        worksheet.merge_cells(
            start_row=data_start,
            start_column=start_column,
            end_row=data_start,
            end_column=end_column,
        )
        empty_cell = worksheet.cell(data_start, start_column, "暂无数据")
        empty_cell.font = _BODY_FONT
        empty_cell.border = _THIN_BORDER
        empty_cell.alignment = Alignment(horizontal="center", vertical="center")
        for column in range(start_column, end_column + 1):
            worksheet.cell(data_start, column).border = _THIN_BORDER
        worksheet.row_dimensions[data_start].height = 24

    total_row = data_end + 1
    worksheet.merge_cells(
        start_row=total_row,
        start_column=start_column,
        end_row=total_row,
        end_column=start_column + label_span - 1,
    )
    worksheet.cell(total_row, start_column, "合计")
    for column_offset in range(len(headers)):
        total_cell = worksheet.cell(total_row, start_column + column_offset)
        total_cell.font = _TOTAL_FONT
        total_cell.fill = _TOTAL_FILL
        total_cell.border = _THIN_BORDER
        total_cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        if column_offset in money_indexes:
            assert isinstance(total_cell, Cell) and not isinstance(total_cell, MergedCell)
            column_letter = get_column_letter(start_column + column_offset)
            total_cell.value = f"=SUM({column_letter}{data_start}:{column_letter}{data_end})"
            total_cell.number_format = _MONEY_FORMAT
            total = sum(
                (cast(Decimal, row[column_offset]) for row in rows),
                Decimal("0.00"),
            ).quantize(_MONEY_QUANTUM)
            formula_results[f"{column_letter}{total_row}"] = total
    worksheet.row_dimensions[total_row].height = 24
    return total_row


def _cache_formula_results(content: bytes, results: Mapping[str, Decimal]) -> bytes:
    ElementTree.register_namespace("", _SPREADSHEET_NAMESPACE)
    source = BytesIO(content)
    output = BytesIO()
    with ZipFile(source) as archive, ZipFile(output, "w") as rebuilt:
        for member in archive.infolist():
            payload = archive.read(member.filename)
            if member.filename == _OUTPUT_SHEET_PATH:
                root = ElementTree.fromstring(payload)
                for reference, result in results.items():
                    cell = root.find(
                        f".//{{{_SPREADSHEET_NAMESPACE}}}c[@r='{reference}']"
                    )
                    if cell is None or cell.find(
                        f"{{{_SPREADSHEET_NAMESPACE}}}f"
                    ) is None:
                        raise RuntimeError(f"Missing generated formula cell {reference}")
                    value = cell.find(f"{{{_SPREADSHEET_NAMESPACE}}}v")
                    if value is None:
                        value = ElementTree.SubElement(
                            cell,
                            f"{{{_SPREADSHEET_NAMESPACE}}}v",
                        )
                    normalized = Decimal("0.00") if result == 0 else result
                    value.text = format(normalized, "f")
                payload = ElementTree.tostring(
                    root,
                    encoding="utf-8",
                    xml_declaration=True,
                )
            rebuilt.writestr(member, payload)
    return output.getvalue()


def formula_results_are_cached(content: bytes) -> bool:
    try:
        with ZipFile(BytesIO(content)) as archive:
            root = ElementTree.fromstring(archive.read(_OUTPUT_SHEET_PATH))
    except (BadZipFile, KeyError, ElementTree.ParseError):
        return False
    formula_cells = 0
    for cell in root.iter(f"{{{_SPREADSHEET_NAMESPACE}}}c"):
        if cell.find(f"{{{_SPREADSHEET_NAMESPACE}}}f") is None:
            continue
        formula_cells += 1
        value = cell.find(f"{{{_SPREADSHEET_NAMESPACE}}}v")
        if value is None or not (value.text or "").strip():
            return False
    return formula_cells > 0


def _render_workbook(
    *,
    cost_center: str,
    domestic: tuple[tuple[object, ...], ...],
    international: tuple[tuple[object, ...], ...],
    trains: tuple[tuple[object, ...], ...],
) -> bytes:
    workbook = Workbook()
    formula_results: dict[str, Decimal] = {}
    worksheet = cast(Worksheet, workbook.active)
    worksheet.title = "Sheet1"
    worksheet.sheet_view.showGridLines = False
    worksheet.freeze_panes = "A3"
    widths = (
        12.25, 13, 12, 13, 17.875, 11.375, 13, 13, 8.5, 8.5, 9, 9,
        10.75, 11.5, 23.5, 3.5, 13, 13, 19.3, 13, 17.875, 11, 10.25,
        11, 11, 11, 11, 11.5, 23.5,
    )
    for index, width in enumerate(widths, start=1):
        worksheet.column_dimensions[get_column_letter(index)].width = width

    domestic_total_row = _render_section(
        worksheet,
        start_row=1,
        start_column=1,
        title=f"国内机票明细报表 - {cost_center}",
        headers=_DOMESTIC_HEADERS,
        rows=domestic,
        label_span=5,
        money_indexes=tuple(range(5, 14)),
        formula_results=formula_results,
    )
    international_total_row = _render_section(
        worksheet,
        start_row=1,
        start_column=17,
        title=f"国际机票明细报表 - {cost_center}",
        headers=_INTERNATIONAL_HEADERS,
        rows=international,
        label_span=5,
        money_indexes=tuple(range(5, 12)),
        formula_results=formula_results,
    )
    train_start_row = international_total_row + 3
    train_total_row = _render_section(
        worksheet,
        start_row=train_start_row,
        start_column=17,
        title=f"火车明细报表 - {cost_center}",
        headers=_TRAIN_HEADERS,
        rows=trains,
        label_span=6,
        money_indexes=tuple(range(6, 12)),
        formula_results=formula_results,
    )
    final_row = max(domestic_total_row, train_total_row)
    worksheet.print_area = f"A1:AC{final_row}"
    worksheet.page_setup.orientation = "landscape"
    worksheet.page_setup.fitToWidth = 1
    worksheet.page_setup.fitToHeight = 0
    worksheet.sheet_properties.pageSetUpPr = PageSetupProperties(
        fitToPage=True,
        autoPageBreaks=False,
    )
    worksheet.page_margins = PageMargins(left=0.25, right=0.25, top=0.4, bottom=0.4)
    workbook.calculation.calcMode = "auto"
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    workbook.properties.title = f"飞巴商旅账单 - {cost_center}"
    workbook.properties.subject = "ERP 自动转换的机票与火车账单"
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return _cache_formula_results(output.getvalue(), formula_results)


def _output_filename(period_start: date, cost_center: str) -> str:
    safe_center = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", cost_center).strip(" .")
    safe_center = safe_center[:80] or "未命名成本中心"
    return f"飞巴商旅{period_start.year % 100:02d}年{period_start.month}月账单-{safe_center}.xlsx"


def convert_travel_report(
    content: bytes,
    original_filename: str | None,
) -> ConvertedTravelReport:
    name = _filename(original_filename)
    _validate_container(content)
    try:
        workbook = load_workbook(
            BytesIO(content),
            read_only=True,
            data_only=False,
            keep_links=False,
        )
    except Exception as exc:
        raise TravelReportWorkbookError("workbook", "账单工作簿无法读取") from exc
    try:
        missing_sheets = [sheet for sheet in _REQUIRED_SHEETS if sheet not in workbook.sheetnames]
        if missing_sheets:
            raise TravelReportWorkbookError(
                "sheets", f"账单缺少工作表：{'、'.join(missing_sheets)}"
            )
        sheets = {
            sheet: _sheet_data(workbook, sheet)
            for sheet in (*_REQUIRED_SHEETS, *_UNSUPPORTED_SHEETS)
            if sheet in workbook.sheetnames
        }
        company, cost_center, period_start, period_end, source_total = _metadata(
            sheets["账单汇总表"]
        )
        domestic = _domestic_rows(sheets["国内机票"])
        international = _international_rows(sheets["国际机票"])
        trains = _train_rows(sheets["火车"])
        if not domestic and not international and not trains:
            raise TravelReportWorkbookError(
                "empty_business", "账单中没有可转换的国内机票、国际机票或火车明细"
            )
        warnings: list[str] = []
        for sheet_name in _UNSUPPORTED_SHEETS:
            count = len(sheets[sheet_name].rows) if sheet_name in sheets else 0
            if count:
                warnings.append(
                    f"{sheet_name}有 {count} 条明细，当前目标模板尚未定义该区块，已保留在原始文件中。"
                )
        converted_total = sum(
            (cast(Decimal, row[13]) for row in domestic), Decimal("0.00")
        )
        converted_total += sum(
            (cast(Decimal, row[11]) for row in international), Decimal("0.00")
        )
        converted_total += sum(
            (cast(Decimal, row[11]) for row in trains), Decimal("0.00")
        )
        converted_total = converted_total.quantize(_MONEY_QUANTUM)
        difference = (source_total - converted_total).quantize(_MONEY_QUANTUM)
        if difference:
            warnings.append(
                f"账单总额 {source_total:.2f} 元与已转换业务合计 {converted_total:.2f} 元"
                f"相差 {difference:.2f} 元，请核对尚未映射的业务。"
            )
        converted_content = _render_workbook(
            cost_center=cost_center,
            domestic=domestic,
            international=international,
            trains=trains,
        )
        converted_name = _output_filename(period_start, cost_center)
        return ConvertedTravelReport(
            original_filename=name,
            converted_filename=converted_name,
            source_sha256=sha256(content).hexdigest(),
            converted_sha256=sha256(converted_content).hexdigest(),
            source_byte_size=len(content),
            converted_byte_size=len(converted_content),
            converted_content=converted_content,
            company_name=company,
            cost_center=cost_center,
            billing_period_start=period_start,
            billing_period_end=period_end,
            domestic_air_count=len(domestic),
            international_air_count=len(international),
            train_count=len(trains),
            source_total_amount=source_total,
            converted_total_amount=converted_total,
            warnings=tuple(warnings),
        )
    finally:
        workbook.close()
