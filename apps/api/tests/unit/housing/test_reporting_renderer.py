from __future__ import annotations

import csv
from datetime import date, datetime
from io import BytesIO, StringIO
from uuid import UUID
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook

from app.housing.reporting_renderer import (
    GUARDIAN_SHEET_NAME,
    RESIDENT_SHEET_NAME,
    render_csv_zip,
    render_xlsx,
    resident_table,
)
from app.housing.reporting_types import (
    GuardianReportRow,
    HousingReportProjectionV1,
    ResidentReportRow,
)


def test_new_reviewed_phone_evidence_is_labeled_without_changing_legacy_tables():
    old = projection()
    assert "手机号核验" not in resident_table(old)[0]
    row = old.residents[0].model_copy(
        update={
            "person_type": "student_player",
            "phone_verified": False,
            "phone_evidence": "approved_profile",
        }
    )
    current = old.model_copy(update={"residents": (row,)})
    headers, values = resident_table(current)
    output = dict(zip(headers, values[0], strict=True))
    assert "学员兼队员" in values[0]
    assert output["手机号核验"] == "已审核，未验证"


def projection(*, guardians: bool = True) -> HousingReportProjectionV1:
    resident = ResidentReportRow(
        row_id="opaque-row-1",
        person_id=UUID("10000000-0000-4000-8000-000000000001"),
        full_name="=危险姓名,含逗号\n换行",
        phone="013800138000",
        identity_number="0011010519491231002X",
        sex="male",
        date_of_birth=date(2010, 1, 2),
        person_type="student",
        building_id=UUID("20000000-0000-4000-8000-000000000001"),
        building_name="1号楼",
        building_order=1,
        floor_id=UUID("30000000-0000-4000-8000-000000000001"),
        floor_name="二层",
        floor_order=2,
        room_id=UUID("40000000-0000-4000-8000-000000000001"),
        room_number="0201",
        stay_id=UUID("50000000-0000-4000-8000-000000000001"),
        stay_purpose="camp",
        actual_check_in_at=datetime.fromisoformat("2026-08-01T00:00:00+08:00"),
        actual_check_out_at=None,
        stay_status="checkout_pending",
        cohort_id=UUID("60000000-0000-4000-8000-000000000001"),
        cohort_name="英雄联盟一期",
        visit_reason=None,
        is_minor_at_check_in=True,
        guardian_count=1 if guardians else 0,
    )
    guardian_rows = (
        (
            GuardianReportRow(
                resident_row_id=resident.row_id,
                student_id=resident.person_id,
                student_name=resident.full_name,
                guardian_id=UUID("70000000-0000-4000-8000-000000000001"),
                guardian_name="@监护人",
                guardian_phone="013900139000",
                guardian_identity_number="0011010519491231002X",
            ),
        )
        if guardians
        else ()
    )
    return HousingReportProjectionV1(
        generated_at=datetime.fromisoformat("2026-08-06T12:00:00+08:00"),
        range_start=datetime.fromisoformat("2026-08-01T00:00:00+08:00"),
        range_end=datetime.fromisoformat("2026-08-22T00:00:00+08:00"),
        residents=(resident,),
        guardians=guardian_rows,
    )


@pytest.mark.parametrize(
    "person_type,label",
    [("team_manager", "战队经理"), ("assistant_coach", "助教")],
)
def test_staff_classification_renders_chinese_labels_in_both_formats(person_type, label):
    payload = projection(guardians=False).model_dump(mode="json")
    payload["residents"][0]["person_type"] = person_type
    current = HousingReportProjectionV1.model_validate(payload)

    xlsx, _ = xlsx_tables(render_xlsx(current))
    csv_zip = csv_tables(render_csv_zip(current))

    assert xlsx == csv_zip
    assert label in xlsx[RESIDENT_SHEET_NAME][1]


def xlsx_tables(content: bytes) -> tuple[dict[str, list[list[str]]], object]:
    workbook = load_workbook(BytesIO(content), data_only=False)
    tables: dict[str, list[list[str]]] = {}
    for sheet_name in workbook.sheetnames:
        sheet = workbook[sheet_name]
        tables[sheet_name] = [
            ["" if cell.value is None else str(cell.value) for cell in row]
            for row in sheet.iter_rows()
        ]
    return tables, workbook


def csv_tables(content: bytes) -> dict[str, list[list[str]]]:
    with ZipFile(BytesIO(content)) as archive:
        assert set(archive.namelist()) == {
            f"{RESIDENT_SHEET_NAME}.csv",
            f"{GUARDIAN_SHEET_NAME}.csv",
        }
        return {
            name.removesuffix(".csv"): list(
                csv.reader(StringIO(archive.read(name).decode("utf-8-sig"), newline=""))
            )
            for name in archive.namelist()
        }


def test_xlsx_and_csv_zip_parse_to_identical_safe_tables() -> None:
    expected_projection = projection()

    xlsx, workbook = xlsx_tables(render_xlsx(expected_projection))
    csv_zip = csv_tables(render_csv_zip(expected_projection))

    assert xlsx == csv_zip
    assert workbook.sheetnames == [RESIDENT_SHEET_NAME, GUARDIAN_SHEET_NAME]
    assert workbook[RESIDENT_SHEET_NAME].freeze_panes == "A2"
    assert workbook[GUARDIAN_SHEET_NAME].freeze_panes == "A2"
    assert workbook[RESIDENT_SHEET_NAME].auto_filter.ref is not None
    assert workbook[GUARDIAN_SHEET_NAME].auto_filter.ref is not None
    resident_name_index = xlsx[RESIDENT_SHEET_NAME][0].index("姓名")
    guardian_name_index = xlsx[GUARDIAN_SHEET_NAME][0].index("监护人姓名")
    assert xlsx[RESIDENT_SHEET_NAME][1][resident_name_index].startswith("'=")
    assert xlsx[GUARDIAN_SHEET_NAME][1][guardian_name_index].startswith("'@")


def test_csv_members_use_utf8_bom_and_rfc4180_newlines() -> None:
    content = render_csv_zip(projection())

    with ZipFile(BytesIO(content)) as archive:
        for name in archive.namelist():
            member = archive.read(name)
            assert member.startswith(b"\xef\xbb\xbf")
            assert b"\r\n" in member


def test_empty_guardian_table_still_has_a_header() -> None:
    xlsx, _ = xlsx_tables(render_xlsx(projection(guardians=False)))
    csv_zip = csv_tables(render_csv_zip(projection(guardians=False)))

    assert len(xlsx[GUARDIAN_SHEET_NAME]) == 1
    assert xlsx == csv_zip


def test_workbook_metadata_does_not_contain_personal_information() -> None:
    _, workbook = xlsx_tables(render_xlsx(projection()))
    metadata = " ".join(
        str(value or "")
        for value in (
            workbook.properties.title,
            workbook.properties.subject,
            workbook.properties.creator,
            workbook.properties.description,
        )
    )

    assert "危险姓名" not in metadata
    assert "013800138000" not in metadata
    assert "0011010519491231002X" not in metadata
