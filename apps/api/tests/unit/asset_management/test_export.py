from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
from types import SimpleNamespace

from openpyxl import load_workbook

from app.asset_management.export_service import render_asset_export


def make_asset() -> SimpleNamespace:
    return SimpleNamespace(
        id="10000000-0000-4000-8000-000000000001",
        asset_code="AST-20260821-ABCD1234",
        name="训练电脑",
        category="训练设备",
        brand="示例",
        model="X1",
        serial_number="SN-001",
        status="assigned",
        location="一号楼 201",
        purchase_date=date(2026, 8, 18),
        warranty_expires_on=date(2028, 8, 18),
        source_purchase_id="30000000-0000-4000-8000-000000000001",
        photo=SimpleNamespace(),
        warranty_channel="online",
        warranty_service_location=None,
        warranty_contact_name=None,
        warranty_contact_phone=None,
        warranty_product_url="https://shop.example.com/item/1",
        warranty_order_number="000012345678901234567890",
    )


def make_assignment() -> SimpleNamespace:
    return SimpleNamespace(
        assigned_at=datetime.fromisoformat("2026-08-20T09:00:00+08:00"),
        expected_return_at=datetime.fromisoformat("2026-08-27T18:00:00+08:00"),
    )


def test_renders_csv_with_photo_indicator() -> None:
    content, mime, filename, format = render_asset_export(
        [(make_asset(), make_assignment(), "张三")],
        format="csv",
        occurred_at=datetime.fromisoformat("2026-08-21T10:00:00+08:00"),
    )
    assert mime == "text/csv; charset=utf-8"
    assert filename.endswith(".csv")
    assert format == "csv"
    text = content.decode("utf-8-sig")
    assert "辨识照片" in text
    assert "已上传" in text
    assert "2026-08-20" in text
    assert "采购日期" in text
    assert "2026-08-18" in text
    assert "保修到期日" in text
    assert "2028-08-18" in text


def test_renders_csv_with_formula_safe_text() -> None:
    asset = make_asset()
    asset.name = '=HYPERLINK("https://example.invalid")'
    content, _, _, _ = render_asset_export(
        [(asset, None, None)],
        format="csv",
        occurred_at=datetime.fromisoformat("2026-08-21T10:00:00+08:00"),
    )
    text = content.decode("utf-8-sig")
    assert "'=HYPERLINK" in text


def test_renders_xlsx_with_headers() -> None:
    content, mime, filename, format = render_asset_export(
        [(make_asset(), None, None)],
        format="xlsx",
        occurred_at=datetime.fromisoformat("2026-08-21T10:00:00+08:00"),
    )
    workbook = load_workbook(BytesIO(content), data_only=True, read_only=True)
    assert workbook.active["A2"].value == "AST-20260821-ABCD1234"
    assert workbook.active["Q2"].value == "线上"
    assert workbook.active["V2"].value == "000012345678901234567890"
    workbook.close()
    assert mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert filename.endswith(".xlsx")
    assert format == "xlsx"
    assert content[:2] == b"PK"
