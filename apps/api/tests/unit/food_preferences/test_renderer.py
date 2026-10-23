from __future__ import annotations

import csv
from datetime import UTC, datetime
from io import BytesIO, StringIO
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import load_workbook

from app.food_preferences import renderer as renderer_module
from app.food_preferences.renderer import (
    CUISINE_SHEET_NAME,
    DISH_SHEET_NAME,
    PEOPLE_SHEET_NAME,
    cuisine_statistics_table,
    dish_statistics_table,
    person_detail_table,
    render_csv_zip,
    render_xlsx,
    safe_spreadsheet_text,
)
from app.food_preferences.reporting import (
    FoodPreferencePersonRow,
    FoodPreferenceRankRow,
    FoodPreferenceReportSnapshot,
)


def _snapshot() -> FoodPreferenceReportSnapshot:
    return FoodPreferenceReportSnapshot(
        population="all_people",
        include_inactive=True,
        total_people=2,
        filled_people=1,
        people=(
            FoodPreferencePersonRow(
                person_id=UUID("10000000-0000-4000-8000-000000000001"),
                full_name="=危险姓名,含逗号\n换行",
                person_types=("student", "guardian", "staff", "visitor"),
                status="active",
                cuisines=("+川菜", "-粤菜", "@其他：湘菜"),
                dishes=("\t宫保鸡丁", "\r寿司", "普通菜"),
                updated_at=datetime(2026, 8, 11, 12, 13, 14, tzinfo=UTC),
            ),
            FoodPreferencePersonRow(
                person_id=UUID("10000000-0000-4000-8000-000000000002"),
                full_name="未填写人员",
                person_types=("guardian",),
                status="deleted",
                cuisines=(),
                dishes=(),
                updated_at=None,
            ),
        ),
        cuisines=(
            FoodPreferenceRankRow(label="=川菜", count=1, ratio=0.5),
        ),
        dishes=(
            FoodPreferenceRankRow(label="@宫保鸡丁", count=1, ratio=0.5),
        ),
        access_revision="a" * 64,
    )


@pytest.mark.parametrize("prefix", ("=", "+", "-", "@", "\t", "\r"))
def test_safe_spreadsheet_text_prefixes_every_formula_trigger(prefix: str) -> None:
    stored_prefix = "\n" if prefix == "\r" else prefix
    assert safe_spreadsheet_text(f"{prefix}payload") == f"'{stored_prefix}payload"


def test_safe_spreadsheet_text_replaces_forbidden_xml_controls() -> None:
    assert safe_spreadsheet_text("前\x01后") == "前�后"


def test_tables_have_fixed_columns_typed_counts_and_unfilled_rows() -> None:
    snapshot = _snapshot()

    people = person_detail_table(snapshot)
    cuisines = cuisine_statistics_table(snapshot)
    dishes = dish_statistics_table(snapshot)

    assert people.headers == (
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
    )
    assert people.rows[0] == (
        "'=危险姓名,含逗号\n换行",
        "学员、监护人、员工、访客",
        "有效",
        "已填写",
        "'+川菜",
        "'-粤菜",
        "'@其他：湘菜",
        "'\t宫保鸡丁",
        "'\n寿司",
        "普通菜",
        "2026-08-11 20:13:14+08:00",
    )
    assert people.rows[1][3] == "未填写"
    assert people.rows[1][4:10] == ("", "", "", "", "", "")
    assert cuisines.headers == ("菜系", "选择人数", "占目标人数比例")
    assert cuisines.rows == (("'=川菜", 1, 0.5),)
    assert dishes.headers == ("菜品", "选择人数", "占目标人数比例")
    assert dishes.rows == (("'@宫保鸡丁", 1, 0.5),)


def _xlsx_tables(content: bytes) -> tuple[dict[str, list[list[object]]], object]:
    workbook = load_workbook(BytesIO(content), data_only=False)
    tables: dict[str, list[list[object]]] = {}
    for sheet_name in workbook.sheetnames:
        sheet = workbook[sheet_name]
        tables[sheet_name] = [
            ["" if cell.value is None else cell.value for cell in row]
            for row in sheet.iter_rows()
        ]
    return tables, workbook


def _csv_tables(content: bytes) -> tuple[dict[str, list[list[str]]], list[object]]:
    tables: dict[str, list[list[str]]] = {}
    infos: list[object] = []
    with ZipFile(BytesIO(content)) as archive:
        infos = list(archive.infolist())
        for info in infos:
            tables[info.filename.removesuffix(".csv")] = list(
                csv.reader(
                    StringIO(
                        archive.read(info.filename).decode("utf-8-sig"),
                        newline="",
                    )
                )
            )
    return tables, infos


def test_xlsx_has_three_fixed_safe_sheets_and_metadata() -> None:
    tables, workbook = _xlsx_tables(render_xlsx(_snapshot()))

    assert workbook.sheetnames == [
        PEOPLE_SHEET_NAME,
        CUISINE_SHEET_NAME,
        DISH_SHEET_NAME,
    ]
    assert set(tables) == {PEOPLE_SHEET_NAME, CUISINE_SHEET_NAME, DISH_SHEET_NAME}
    for sheet_name in workbook.sheetnames:
        sheet = workbook[sheet_name]
        assert sheet.freeze_panes == "A2"
        assert sheet.auto_filter.ref is not None
        assert all(cell.font.bold for cell in sheet[1])
    assert all(dimension.width <= 32 for dimension in sheet.column_dimensions.values())
    assert workbook.properties.title == "餐饮偏好统计"
    assert workbook.properties.creator is None
    assert workbook.properties.lastModifiedBy is None
    assert workbook.properties.subject is None
    assert workbook.properties.description is None
    fixed_time = datetime(1980, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    assert workbook.properties.created == fixed_time
    assert workbook.properties.modified == fixed_time
    dangerous_name = workbook[PEOPLE_SHEET_NAME]["A2"]
    assert dangerous_name.data_type == "s"
    assert dangerous_name.value == "'=危险姓名,含逗号\n换行"
    assert workbook[CUISINE_SHEET_NAME]["A2"].data_type == "s"
    assert workbook[CUISINE_SHEET_NAME]["B2"].data_type == "n"
    assert workbook[CUISINE_SHEET_NAME]["C2"].data_type == "n"
    assert workbook[CUISINE_SHEET_NAME]["C2"].number_format == "0.0%"


def test_xlsx_archive_is_deterministic_with_fixed_member_metadata() -> None:
    snapshot = _snapshot()
    first = render_xlsx(snapshot)
    second = render_xlsx(snapshot)

    assert first == second
    with ZipFile(BytesIO(first)) as archive:
        infos = archive.infolist()

    assert len(infos) == 11
    assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in infos)
    assert all(info.external_attr >> 16 == 0o600 for info in infos)
    assert all(info.compress_type == ZIP_DEFLATED for info in infos)


def test_xlsx_render_never_creates_openpyxl_tempfiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_tempfile() -> str:
        raise AssertionError("renderer attempted to create a temporary file")

    monkeypatch.setattr(
        "openpyxl.worksheet._writer.create_temporary_file",
        reject_tempfile,
    )

    assert render_xlsx(_snapshot()).startswith(b"PK")


def test_xlsx_closes_archive_when_openpyxl_save_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_error = RuntimeError("openpyxl save failed")
    archives: list[ZipFile] = []

    def fail_save(writer: object) -> None:
        archives.append(writer._archive)  # type: ignore[attr-defined]
        raise save_error

    monkeypatch.setattr(renderer_module._InMemoryExcelWriter, "save", fail_save)

    with pytest.raises(RuntimeError) as caught:
        render_xlsx(_snapshot())

    assert caught.value is save_error
    assert len(archives) == 1
    archive = archives[0]
    try:
        assert archive.fp is None
    finally:
        archive.close()


def test_csv_zip_is_deterministic_bom_encoded_and_matches_xlsx_rows() -> None:
    snapshot = _snapshot()
    xlsx, _ = _xlsx_tables(render_xlsx(snapshot))
    csv_zip, infos = _csv_tables(render_csv_zip(snapshot))

    assert [info.filename for info in infos] == [
        f"{PEOPLE_SHEET_NAME}.csv",
        f"{CUISINE_SHEET_NAME}.csv",
        f"{DISH_SHEET_NAME}.csv",
    ]
    assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in infos)
    assert all(info.create_system == 3 for info in infos)
    assert all(info.external_attr >> 16 == 0o600 for info in infos)
    assert all(info.compress_type != 0 for info in infos)
    with ZipFile(BytesIO(render_csv_zip(snapshot))) as archive:
        for info in archive.infolist():
            raw_member = archive.read(info.filename)
            parsed_rows = csv_zip[info.filename.removesuffix(".csv")]
            assert raw_member.startswith(b"\xef\xbb\xbf")
            assert raw_member.endswith(b"\r\n")
            assert raw_member.count(b"\r\n") == len(parsed_rows)

    normalized_xlsx = {
        sheet_name: [[str(value) for value in row] for row in rows]
        for sheet_name, rows in xlsx.items()
    }
    assert csv_zip == normalized_xlsx
    assert render_csv_zip(snapshot) == render_csv_zip(snapshot)


def test_csv_zip_sets_unix_origin_without_platform_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_zip_info = renderer_module.ZipInfo

    def non_unix_zip_info(
        filename: str,
        date_time: tuple[int, int, int, int, int, int],
    ) -> object:
        info = real_zip_info(filename, date_time)
        info.create_system = 0
        return info

    monkeypatch.setattr(renderer_module, "ZipInfo", non_unix_zip_info)

    with ZipFile(BytesIO(render_csv_zip(_snapshot()))) as archive:
        assert all(info.create_system == 3 for info in archive.infolist())
