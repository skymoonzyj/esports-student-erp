import csv
from datetime import UTC, datetime
from io import BytesIO, StringIO

import pytest
from openpyxl import load_workbook

from app.housing.occupancy_export_service import OccupancyExportRow, render_occupancy_export


@pytest.mark.parametrize("format", ["csv", "xlsx"])
def test_accountless_export_labels_and_formula_safety(format):
    row = OccupancyExportRow(
        room_number="201",
        person_name="=恶意公式",
        sex="male",
        identity_number="110105200001020011",
        phone="13800138000",
        check_in_at=datetime(2026, 10, 18, 12, tzinfo=UTC),
        check_out_at=None,
        person_type="student_player",
        account_status="not_activated",
        profile_source="approved_profile",
    )
    content, _, _ = render_occupancy_export([row], format=format, occurred_at=row.check_in_at)
    if format == "csv":
        data = list(csv.reader(StringIO(content.decode("utf-8-sig"))))
    else:
        data = list(load_workbook(BytesIO(content), data_only=False).active.values)
    record = dict(zip(data[0], data[1], strict=True))
    assert record["入住人"] == "'=恶意公式"
    assert record["人员类型"] == "学员兼队员"
    assert record["账号状态"] == "未激活"
    assert record["档案来源"] == "审核建档"
