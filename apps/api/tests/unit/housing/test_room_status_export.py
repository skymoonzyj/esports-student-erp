import csv
from datetime import UTC, datetime
from io import BytesIO, StringIO
from uuid import uuid4

import pytest
from openpyxl import load_workbook
from pydantic import ValidationError

from app.housing.room_status_export_renderer import render_room_status_export
from app.housing.room_status_export_schemas import HousingRoomStatusExportRequest
from app.housing.room_status_export_service import RoomStatusExportRow


def test_request_deduplicates_filters_and_retains_explicit_false():
    building_id = uuid4()
    request = HousingRoomStatusExportRequest(
        purpose="房态核对",
        building_ids=[building_id, building_id],
        cleaning_statuses=["ready", "ready"],
        daily_cleaning_enabled=False,
    )
    assert request.building_ids == [building_id]
    assert request.cleaning_statuses == ["ready"]
    assert request.model_dump(exclude_none=True)["daily_cleaning_enabled"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {"purpose": "x"},
        {"purpose": " " * 2},
        {"purpose": "x" * 201},
        {"purpose": "核对", "page": 1},
        {"purpose": "核对", "format": "pdf"},
    ],
)
def test_request_rejects_invalid_audit_context_and_pagination(payload):
    with pytest.raises(ValidationError):
        HousingRoomStatusExportRequest.model_validate(payload)


@pytest.mark.parametrize("format", ["csv", "xlsx"])
def test_renderer_fixed_columns_empty_fields_china_date_and_formula_safety(format):
    now = datetime(2026, 10, 22, 16, 5, tzinfo=UTC)
    row = RoomStatusExportRow(
        building_name="一号楼",
        floor_name="二层",
        room_number="201",
        room_type="double",
        capacity=2,
        current_occupants=1,
        occupancy_status="occupied",
        verification_status="verified",
        service_status="available",
        is_assignable=True,
        daily_cleaning_enabled=False,
        cleaning_status="pending",
        person_name="=公式",
        sex="male",
        identity_number="00123",
        phone="+123",
        actual_check_in_at=now,
        planned_check_out_at=None,
        person_type="coach",
        account_status="active",
        profile_source="approved_profile",
    )
    empty = RoomStatusExportRow(
        building_name="一号楼",
        floor_name="二层",
        room_number="202",
        room_type="single",
        capacity=1,
        current_occupants=0,
        occupancy_status="unoccupied",
        verification_status="pending_verification",
        service_status="maintenance",
        is_assignable=False,
        daily_cleaning_enabled=True,
        cleaning_status="ready",
    )
    content, media_type, filename = render_room_status_export(
        [row, empty],
        format=format,
        occurred_at=now,
    )
    if format == "csv":
        data = list(csv.reader(StringIO(content.decode("utf-8-sig"))))
    else:
        data = list(load_workbook(BytesIO(content), data_only=False).active.values)
    assert list(data[0]) == [
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
    ]
    assert data[1][3] == "双人间"
    assert list(data[1][11:17]) == [
        "待保洁",
        "'=公式",
        "男",
        "00123",
        "'+123",
        "2026-10-23",
    ]
    assert data[1][17] in ("", None)
    assert all(cell in ("", None) for cell in data[2][12:])
    assert data[2][9] == "否"
    assert data[2][11] == "保洁就绪"
    assert filename == f"housing-room-status-20261023.{format}"
    assert "csv" in media_type if format == "csv" else "spreadsheetml" in media_type


def test_snapshot_service_rejects_an_already_started_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.errors import ProblemError
    from app.housing.room_status_export_service import HousingRoomStatusExportService
    from app.security.encryption import EnvelopeCipher, LookupDigest

    with Session(create_engine("sqlite://")) as db:
        db.begin()
        service = HousingRoomStatusExportService(
            db,
            cipher=EnvelopeCipher(b"c" * 32),
            lookup=LookupDigest(b"c" * 32),
            actor_user_id=uuid4(),
        )
        with pytest.raises(ProblemError) as exc:
            service.rows(
                HousingRoomStatusExportRequest(purpose="快照验证"), as_of=datetime.now(UTC)
            )
        assert exc.value.code == "housing_export_snapshot_unavailable"
