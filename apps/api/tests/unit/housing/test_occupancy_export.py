import csv
from datetime import UTC, date, datetime
from io import BytesIO, StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from app.housing.occupancy_export_service import (
    HousingOccupancyExportService,
    OccupancyExportRow,
    decrypt_identity,
    render_occupancy_export,
)
from app.housing.schemas import HousingOccupancyExportRequest
from app.security.encryption import EnvelopeCipher, LookupDigest


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


@pytest.mark.parametrize("format", ["csv", "xlsx"])
@pytest.mark.parametrize("number,expected", [("p-Test-001", "p-Test-001"), ("=1+1", "'=1+1")])
def test_pending_passport_export_preserves_document_and_labels_unknown_sex(
    format, number, expected
):
    cipher = EnvelopeCipher(b"e" * 32)
    room = SimpleNamespace(id=uuid4())
    now = datetime(2026, 10, 21, 12, tzinfo=UTC)
    stay = SimpleNamespace(
        room_id=room.id,
        actual_check_in_at=now,
        actual_check_out_at=None,
        id=uuid4(),
        room_name_snapshot="201",
    )
    person = SimpleNamespace(
        id=uuid4(),
        intake_mode="strict",
        full_name="待补护照人员",
        sex=None,
        date_of_birth=None,
        document_type="passport",
        document_issuing_country=None,
        identity_ciphertext=cipher.encrypt(number, field_name="identity_number"),
        phone_ciphertext=None,
    )
    db = Mock()
    db.execute.return_value.all.side_effect = [
        [(room, Mock(), Mock())],
        [(stay, person, None, None, None, "player", None, uuid4())],
    ]
    access = Mock()
    access.room_permissions_criteria.return_value = True
    service = HousingOccupancyExportService(
        db, cipher=cipher, lookup=LookupDigest(b"e" * 32), access=access
    )
    with (
        patch(
            "app.housing.occupancy_export_service.HousingOccupancyService.room_projections",
            return_value={room.id: Mock()},
        ),
        patch(
            "app.housing.occupancy_export_service.HousingInventoryService._room_card",
            return_value=Mock(),
        ),
        patch(
            "app.housing.occupancy_export_service.HousingInventoryService._matches_filters",
            return_value=True,
        ),
    ):
        rows = service.rows(HousingOccupancyExportRequest(purpose="隔离导出验证"), as_of=now)
    content, _, _ = render_occupancy_export(rows, format=format, occurred_at=now)
    if format == "csv":
        data = list(csv.reader(StringIO(content.decode("utf-8-sig"))))
    else:
        data = list(load_workbook(BytesIO(content), data_only=False).active.values)
    record = dict(zip(data[0], data[1], strict=True))
    assert record["身份证号"] == expected
    assert record["性别"] == "待补"
    assert len(record) == 10


def test_cn_export_keeps_normalized_document_and_dob_validation():
    cipher = EnvelopeCipher(b"e" * 32)
    ciphertext = cipher.encrypt("11010519491231002x", field_name="identity_number")
    assert decrypt_identity(cipher, ciphertext, date(1949, 12, 31)) == "11010519491231002X"
    with pytest.raises(ValueError, match="housing resident identity data is unavailable"):
        decrypt_identity(cipher, ciphertext, date(1950, 1, 1))


@pytest.mark.parametrize("number", ["", "invalid\x00passport"])
def test_invalid_passport_export_fails_safely(number):
    cipher = EnvelopeCipher(b"e" * 32)
    with pytest.raises(ValueError, match="housing resident identity data is unavailable"):
        decrypt_identity(
            cipher,
            cipher.encrypt(number, field_name="identity_number"),
            None,
            document_type="passport",
        )
