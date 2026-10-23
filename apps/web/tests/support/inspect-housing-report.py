"""Inspect a downloaded housing report and emit a canonical table object."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Iterable, Sequence
from io import BytesIO, StringIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover - exercised only by a misconfigured E2E environment
    load_workbook = None  # type: ignore[assignment]


RESIDENT_SHEET = "入住名单"
GUARDIAN_SHEET = "未成年监护人"
SHEET_NAMES = (RESIDENT_SHEET, GUARDIAN_SHEET)
CSV_MEMBERS = tuple(f"{name}.csv" for name in SHEET_NAMES)

RESIDENT_HEADERS = (
    "行号",
    "住宿记录ID",
    "人员ID",
    "姓名",
    "手机号",
    "身份证号",
    "性别",
    "出生日期",
    "人员类型",
    "楼栋ID",
    "楼栋",
    "楼层ID",
    "楼层",
    "房间ID",
    "房号",
    "住宿类型",
    "实际入住时间",
    "实际退房时间",
    "住宿状态",
    "营期ID",
    "营期",
    "临时来访原因",
    "是否未成年",
    "监护人数",
)
GUARDIAN_HEADERS = (
    "住户行号",
    "学员ID",
    "学员姓名",
    "监护人ID",
    "监护人姓名",
    "监护人手机号",
    "监护人身份证号",
)
EXPECTED_HEADERS = {
    RESIDENT_SHEET: RESIDENT_HEADERS,
    GUARDIAN_SHEET: GUARDIAN_HEADERS,
}


class ReportInspectionError(Exception):
    """An expected, user-actionable report inspection failure."""


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _table(rows: Iterable[Sequence[object]], sheet_name: str) -> list[dict[str, str]]:
    values = [[_text(value) for value in row] for row in rows]
    expected_headers = EXPECTED_HEADERS[sheet_name]
    if not values or tuple(values[0]) != expected_headers:
        raise ReportInspectionError(f"invalid {sheet_name} table header")
    width = len(expected_headers)
    if any(len(row) != width for row in values):
        raise ReportInspectionError(f"invalid {sheet_name} table shape")
    return [dict(zip(expected_headers, row)) for row in values[1:]]


def _read_xlsx(content: bytes) -> dict[str, list[dict[str, str]]]:
    if load_workbook is None:
        raise ReportInspectionError("XLSX support is unavailable")
    try:
        workbook = load_workbook(
            filename=BytesIO(content),
            read_only=True,
            data_only=True,
        )
    except Exception:  # noqa: BLE001 - normalize openpyxl failures for the CLI boundary
        raise ReportInspectionError("invalid XLSX report workbook") from None
    try:
        sheet_names = tuple(workbook.sheetnames)
        if len(sheet_names) != len(SHEET_NAMES) or set(sheet_names) != set(SHEET_NAMES):
            raise ReportInspectionError("XLSX report must contain exactly the required sheets")
        return {
            sheet_name: _table(
                workbook[sheet_name].iter_rows(values_only=True),
                sheet_name,
            )
            for sheet_name in SHEET_NAMES
        }
    except ReportInspectionError:
        raise
    except Exception:  # noqa: BLE001 - normalize openpyxl failures for the CLI boundary
        raise ReportInspectionError("invalid XLSX report workbook") from None
    finally:
        workbook.close()


def _read_csv_table(content: bytes, member_name: str, sheet_name: str) -> list[dict[str, str]]:
    try:
        decoded = content.decode("utf-8-sig")
        rows = csv.reader(StringIO(decoded, newline=""), strict=True)
        return _table(rows, sheet_name)
    except ReportInspectionError:
        raise
    except Exception:  # noqa: BLE001 - normalize CSV parser failures for the CLI boundary
        raise ReportInspectionError(f"invalid CSV member {member_name}") from None


def _read_csv_zip(content: bytes) -> dict[str, list[dict[str, str]]]:
    try:
        archive = ZipFile(BytesIO(content), mode="r")
    except (BadZipFile, OSError, ValueError):
        raise ReportInspectionError("invalid CSV report archive") from None
    try:
        names = tuple(archive.namelist())
        if len(names) != len(CSV_MEMBERS) or len(set(names)) != len(names) or set(names) != set(CSV_MEMBERS):
            raise ReportInspectionError("CSV report archive must contain exactly the required members")
        tables: dict[str, list[dict[str, str]]] = {}
        for sheet_name, member_name in zip(SHEET_NAMES, CSV_MEMBERS):
            try:
                member = archive.read(member_name)
            except Exception:  # noqa: BLE001 - normalize archive failures for the CLI boundary
                raise ReportInspectionError("invalid CSV report archive") from None
            tables[sheet_name] = _read_csv_table(member, member_name, sheet_name)
        return tables
    except ReportInspectionError:
        raise
    except Exception:  # noqa: BLE001 - normalize archive failures for the CLI boundary
        raise ReportInspectionError("invalid CSV report archive") from None
    finally:
        archive.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect a downloaded housing XLSX or CSV report.",
    )
    parser.add_argument(
        "--format",
        required=True,
        choices=("xlsx", "csv"),
        help="download format",
    )
    parser.add_argument("path", type=Path, help="downloaded report path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        content = args.path.read_bytes()
    except OSError:
        print("error: unable to read report file", file=sys.stderr)
        return 2
    try:
        tables = _read_xlsx(content) if args.format == "xlsx" else _read_csv_zip(content)
    except ReportInspectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(tables, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
