from __future__ import annotations

import csv
import json
import subprocess
import sys
from io import StringIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook

SCRIPT = Path(__file__).with_name("inspect-housing-report.py")
RESIDENT_SHEET = "入住名单"
GUARDIAN_SHEET = "未成年监护人"

RESIDENT_HEADERS = [
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
]
GUARDIAN_HEADERS = [
    "住户行号",
    "学员ID",
    "学员姓名",
    "监护人ID",
    "监护人姓名",
    "监护人手机号",
    "监护人身份证号",
]


def _tables() -> dict[str, list[list[str]]]:
    resident = [
        *RESIDENT_HEADERS,
    ]
    resident_row = [
        "row-001",
        "stay-001",
        "person-001",
        "'=姓名,含逗号\n换行",
        "013800138000",
        "0011010519491231002X",
        "男",
        "2010-01-02",
        "学员",
        "building-001",
        "一号楼",
        "floor-001",
        "二层",
        "room-001",
        "0201",
        "随营",
        "2026-08-01 08:00:00+08:00",
        "",
        "已入住",
        "cohort-001",
        "",
        "",
        "是",
        "1",
    ]
    guardian = [*GUARDIAN_HEADERS]
    guardian_row = [
        "row-001",
        "person-001",
        "'=姓名,含逗号\n换行",
        "guardian-001",
        "'@监护人",
        "013900139000",
        "0011010519491231002X",
    ]
    return {
        RESIDENT_SHEET: [resident, resident_row],
        GUARDIAN_SHEET: [guardian, guardian_row],
    }


def _canonical_tables(tables: dict[str, list[list[str]]]) -> dict[str, list[dict[str, str]]]:
    return {
        name: [dict(zip(rows[0], row)) for row in rows[1:]]
        for name, rows in tables.items()
    }


def _write_xlsx(path: Path, tables: dict[str, list[list[str]]], *, extra_sheet: bool = False) -> None:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, rows in tables.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
    if extra_sheet:
        workbook.create_sheet("额外工作表")
    workbook.save(path)


def _write_csv_zip(path: Path, tables: dict[str, list[list[str]]], *, extra_member: bool = False) -> None:
    with ZipFile(path, mode="w", compression=ZIP_DEFLATED) as archive:
        for name, rows in tables.items():
            output = StringIO(newline="")
            csv.writer(output, lineterminator="\r\n").writerows(rows)
            archive.writestr(f"{name}.csv", output.getvalue().encode("utf-8-sig"))
        if extra_member:
            archive.writestr("额外.csv", b"x\r\n")


def _inspect(format_name: str, path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--format", format_name, str(path)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_cli_emits_identical_canonical_tables_for_xlsx_and_csv(tmp_path: Path) -> None:
    tables = _tables()
    xlsx_path = tmp_path / "report.xlsx"
    csv_path = tmp_path / "report.zip"
    _write_xlsx(xlsx_path, tables)
    _write_csv_zip(csv_path, tables)

    xlsx_result = _inspect("xlsx", xlsx_path)
    csv_result = _inspect("csv", csv_path)

    assert xlsx_result.returncode == 0, xlsx_result.stderr
    assert csv_result.returncode == 0, csv_result.stderr
    assert json.loads(xlsx_result.stdout) == _canonical_tables(tables)
    assert json.loads(csv_result.stdout) == _canonical_tables(tables)
    assert xlsx_result.stdout == csv_result.stdout


def test_cli_rejects_extra_sheets_and_zip_members(tmp_path: Path) -> None:
    tables = _tables()
    xlsx_path = tmp_path / "extra.xlsx"
    csv_path = tmp_path / "extra.zip"
    _write_xlsx(xlsx_path, tables, extra_sheet=True)
    _write_csv_zip(csv_path, tables, extra_member=True)

    for format_name, path in (("xlsx", xlsx_path), ("csv", csv_path)):
        result = _inspect(format_name, path)
        assert result.returncode != 0
        assert result.stderr.startswith("error:")
        assert "Traceback" not in result.stderr


def test_cli_reports_safe_errors_for_bad_format_and_corrupt_files(tmp_path: Path) -> None:
    bad_path = tmp_path / "not-a-report"
    bad_path.write_bytes(b"not a report")

    invalid_format = _inspect("xls", bad_path)
    corrupt_xlsx = _inspect("xlsx", bad_path)
    corrupt_csv = _inspect("csv", bad_path)

    assert invalid_format.returncode != 0
    assert "invalid choice" in invalid_format.stderr
    assert corrupt_xlsx.returncode != 0
    assert corrupt_csv.returncode != 0
    assert corrupt_xlsx.stderr.startswith("error:")
    assert corrupt_csv.stderr.startswith("error:")
    for result in (invalid_format, corrupt_xlsx, corrupt_csv):
        assert "Traceback" not in result.stderr
